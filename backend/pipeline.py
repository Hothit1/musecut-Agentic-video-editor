"""Background jobs: plan (transcribe -> Spark -> SAM -> Muse Image) and render."""
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import db, facetrack, planner, render, thumbnail, transcribe


def paths(pid):
    d = db.project_dir(pid)
    return {"dir": d, "plan": d / "plan.json", "tracks": d / "tracks.json",
            "transcript": d / "work" / "transcript.json", "thumbs": d / "thumbs"}


def load_plan(pid):
    p = paths(pid)["plan"]
    return json.loads(p.read_text()) if p.exists() else None


def save_plan(plan):
    planner.validate(plan)
    p = paths(plan["project_id"])["plan"]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=1))
    tmp.replace(p)
    return plan


def load_tracks(pid):
    p = paths(pid)["tracks"]
    return json.loads(p.read_text()) if p.exists() else {}


def _run(jid, fn):
    def wrapper():
        db.update_job(jid, status="processing")
        try:
            fn(lambda m: db.update_job(jid, progress=m))
            db.update_job(jid, status="done", progress="done")
        except Exception as e:
            traceback.print_exc()
            db.update_job(jid, status="failed", error=f"{type(e).__name__}: {e}"[:1000])
    threading.Thread(target=wrapper, daemon=True).start()


def start_plan(pid):
    jid = db.create_job(pid, "plan")
    _run(jid, lambda progress: run_plan(pid, progress))
    return jid


def run_plan(pid, progress):
    proj = db.get_project(pid)
    P = paths(pid)
    video, probe = proj["video_path"], proj["probe"]
    W, H = probe["width"], probe["height"]
    if P["transcript"].exists():
        units = json.loads(P["transcript"].read_text())
    else:
        progress("transcribing with Muse Voice Transcribe")
        units = transcribe.transcribe(pid, video, probe["duration"], P["dir"] / "work", progress)
    if not units:
        raise planner.PlanError("transcript is empty - no speech detected")

    progress("planning with Muse Spark")
    spark = planner.ask_spark(pid, units, proj["prompt"], proj["target_duration_sec"])
    plan = planner.build_plan(pid, proj["prompt"], proj["target_duration_sec"], units, spark)

    tracks = facetrack.track_all(pid, video, plan["segments"], W, H, progress)
    P["tracks"].write_text(json.dumps(tracks))
    for s in plan["segments"]:
        s["layout"] = tracks[s["id"]]["layout"]
        if s["layout"] == "slide" and s["shot_type"] == "a_roll":
            # speaker is a small webcam tile -> show the shared screen as same-video B-roll
            s["shot_type"], s["b_roll_source"] = "b_roll", "same_video"
            s["b_roll_description"] = "Slide / screen from the source video (speaker tile small)"

    best = max(tracks.values(), key=lambda t: t["best_frame_score"])
    best_t = best["best_frame_t"] or plan["segments"][0]["start_sec"] + 1
    gen = [s for s in plan["segments"] if s["b_roll_source"] == "generated_image"]
    progress("Muse Image: thumbnails + B-roll stills")
    with ThreadPoolExecutor(4) as ex:
        fut_thumbs = ex.submit(thumbnail.make_thumbnails, pid, video, best_t, plan["title"],
                               P["thumbs"])
        futs = {s["id"]: ex.submit(thumbnail.make_broll, pid, s["b_roll_description"],
                                   P["dir"] / f"broll_{s['id']}.png") for s in gen}
        plan["thumbnail_options"] = fut_thumbs.result()
        for s in gen:
            try:
                s["b_roll_image"] = futs[s["id"]].result()
            except Exception as e:
                s["auto_note"] = f"B-roll image failed ({str(e)[:80]}); will retry at render"
    plan["thumbnail_selected"] = plan["thumbnail_options"][0]["id"]
    save_plan(plan)
    db.set_project_status(pid, "planned")


def start_render(pid):
    jid = db.create_job(pid, "render")
    _run(jid, lambda progress: run_render(pid, progress))
    return jid


def run_render(pid, progress):
    proj = db.get_project(pid)
    plan = load_plan(pid)
    res = render.render(pid, plan, proj["video_path"], proj["probe"], load_tracks(pid),
                        paths(pid)["dir"], progress)
    # persist b-roll images that were generated during render
    save_plan(plan)
    (paths(pid)["dir"] / "render_result.json").write_text(json.dumps(res, indent=1))
    db.set_project_status(pid, "rendered")
