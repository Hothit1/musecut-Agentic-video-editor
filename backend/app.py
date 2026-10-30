"""MuseCut API (FastAPI). Run: uvicorn backend.app:app --port 8000"""
import json
import shutil

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, pipeline, planner
from .config import DATA_DIR, ROOT
from .ingest import IngestError, probe

app = FastAPI(title="MuseCut — Agentic Video Editor")
db.init()
app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")
app.mount("/app", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")


@app.get("/")
def index():
    return RedirectResponse("/app/")


def _project(pid):
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _url(path):
    return "/files/" + str(path).split(str(DATA_DIR) + "/", 1)[-1]


@app.post("/api/projects")
def create_project(video: UploadFile = File(...), prompt: str = Form(...),
                   target_duration_sec: int = Form(60),
                   manual_edit_minutes: int | None = Form(None)):
    if target_duration_sec not in (30, 60, 90):
        raise HTTPException(422, "target_duration_sec must be 30, 60 or 90")
    if not prompt.strip():
        raise HTTPException(422, "prompt is required")
    import uuid
    pid = "p_" + uuid.uuid4().hex[:10]
    d = db.project_dir(pid)
    dst = d / ("source" + (("." + video.filename.rsplit(".", 1)[-1]) if "." in (video.filename or "") else ".mp4"))
    with dst.open("wb") as f:
        shutil.copyfileobj(video.file, f)
    try:
        info = probe(dst)
    except IngestError as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, str(e))
    except RuntimeError:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, "could not read video file")
    if manual_edit_minutes is not None and not 1 <= manual_edit_minutes <= 24 * 60:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, "manual_edit_minutes must be between 1 and 1440")
    db.create_project(prompt.strip(), target_duration_sec, dst, info, pid=pid,
                      manual_minutes=manual_edit_minutes)
    return {"project_id": pid, "probe": info}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _project(pid)
    p["video_url"] = _url(p["video_path"])
    p.pop("video_path")
    return p


@app.post("/api/projects/{pid}/plan")
def make_plan(pid: str):
    _project(pid)
    job = db.latest_job(pid, "plan")
    if job and job["status"] in ("queued", "processing"):
        return {"job_id": job["id"], "status": job["status"]}
    plan = pipeline.load_plan(pid)
    if plan and plan["status"] == "approved":
        raise HTTPException(409, "plan already approved")
    return {"job_id": pipeline.start_plan(pid), "status": "queued"}


@app.get("/api/projects/{pid}/plan")
def get_plan(pid: str):
    _project(pid)
    job = db.latest_job(pid, "plan")
    plan = pipeline.load_plan(pid)
    if not plan:
        return {"plan": None, "job": job}
    tracks = pipeline.load_tracks(pid)
    for t in plan["thumbnail_options"]:
        t["url"] = _url(t["image_path"])
    for s in plan["segments"]:
        s["b_roll_url"] = _url(s["b_roll_image"]) if s.get("b_roll_image") else None
    return {"plan_id": f"{pid}:plan", "plan": plan, "job": job,
            "kept_duration_sec": round(planner.kept_duration(plan), 1),
            "tracks": {k: {"layout": v["layout"], "crop_w": v["crop_w"], "crop_keys": v["crop_keys"]}
                       for k, v in tracks.items()}}


EDITABLE_SEG = {"keep", "role", "shot_type", "b_roll_source", "b_roll_description"}


@app.patch("/api/projects/{pid}/plan")
def patch_plan(pid: str, body: dict):
    _project(pid)
    plan = pipeline.load_plan(pid)
    if not plan:
        raise HTTPException(404, "no plan yet")
    if plan["status"] == "approved":
        raise HTTPException(409, "plan is approved and locked")
    new = json.loads(json.dumps(plan))
    if "segments" in body:
        by_id = {s["id"]: s for s in new["segments"]}
        order = [s.get("id") for s in body["segments"]]
        if sorted(order) != sorted(by_id):
            raise HTTPException(422, "segments must contain every existing segment id exactly once")
        segs = []
        for upd in body["segments"]:
            s = by_id[upd["id"]]
            for k, v in upd.items():
                if k in EDITABLE_SEG:
                    if k == "b_roll_description" and v != s.get("b_roll_description"):
                        s["b_roll_image"] = None  # regenerate at render
                    s[k] = v
            if s["shot_type"] == "a_roll":
                s["b_roll_source"], s["b_roll_description"], s["b_roll_image"] = None, None, None
            if "keep" in upd:
                s["auto_note"] = None
            segs.append(s)
        new["segments"] = segs
    if "captions" in body:  # text only: timestamps are never edited in v1
        caps = {c["id"]: c for c in new["captions"]}
        for upd in body["captions"]:
            if upd.get("id") not in caps:
                raise HTTPException(422, f"unknown caption {upd.get('id')}")
            caps[upd["id"]]["text"] = str(upd["text"])
    for k in ("title", "hook_text", "post_copy", "hashtags", "thumbnail_selected"):
        if k in body:
            new[k] = body[k]
    if "music" in body:
        new["music"].update({k: body["music"][k] for k in ("enabled", "volume") if k in body["music"]})
    if "target_duration_sec" in body and body["target_duration_sec"] != new["target_duration_sec"]:
        new["target_duration_sec"] = body["target_duration_sec"]
        planner.fit_duration(new)
    try:
        pipeline.save_plan(new)
    except planner.PlanError as e:
        raise HTTPException(422, str(e))
    return get_plan(pid)


@app.post("/api/projects/{pid}/approve")
def approve(pid: str):
    _project(pid)
    plan = pipeline.load_plan(pid)
    if not plan:
        raise HTTPException(404, "no plan yet")
    if plan["status"] != "approved":
        pipeline.stamp(pid, "approved")
        plan["status"] = "approved"
        try:
            pipeline.save_plan(plan)
        except planner.PlanError as e:
            raise HTTPException(422, str(e))
    job = db.latest_job(pid, "render")
    if job and job["status"] in ("queued", "processing", "done"):
        return {"status": "approved", "render_job": job}
    return {"status": "approved", "render_job": {"id": pipeline.start_render(pid), "status": "queued"}}


@app.get("/api/projects/{pid}/render")
def render_status(pid: str):
    _project(pid)
    job = db.latest_job(pid, "render")
    if not job:
        return {"status": "not_started"}
    return {"status": job["status"], "progress": job["progress"], "error": job["error"]}


@app.get("/api/projects/{pid}/export")
def export(pid: str):
    proj = _project(pid)
    d = pipeline.paths(pid)["dir"]
    res_f = d / "render_result.json"
    if not res_f.exists():
        raise HTTPException(409, "render not finished")
    res = json.loads(res_f.read_text())
    plan = pipeline.load_plan(pid)
    (d / "plan_export.json").write_text(json.dumps(plan, indent=1))
    costs = db.cost_log(pid)
    (d / "cost_log.json").write_text(json.dumps(
        {"calls": costs, "time_saved": pipeline.time_report(pid, proj.get("manual_minutes"))}, indent=1))
    post = f"{plan['title']}\n\n{plan['post_copy']}\n\n" + " ".join("#" + h for h in plan["hashtags"])
    (d / "post_copy.txt").write_text(post)
    thumb = next(t for t in plan["thumbnail_options"] if t["id"] == plan["thumbnail_selected"])
    by_model = {}
    for c in costs:
        m = by_model.setdefault(c["model"], {"calls": 0, "usd": 0.0, "failed": 0})
        m["calls"] += 1
        m["usd"] += c["est_cost_usd"] or 0
        m["failed"] += c["status"] not in ("ok",)
    return {
        "mp4": _url(res["mp4"]), "srt": _url(res["srt"]), "thumbnail": _url(thumb["image_path"]),
        "thumbnail_options": [_url(t["image_path"]) for t in plan["thumbnail_options"]],
        "plan_json": _url(d / "plan_export.json"), "cost_log": _url(d / "cost_log.json"),
        "post_copy": post, "duration_sec": res["duration"], "render_notes": res["notes"],
        "cost_summary": {"total_usd": round(sum(v["usd"] for v in by_model.values()), 4),
                         "by_model": by_model},
        "chapters": plan.get("chapters", []),
        "time_saved": pipeline.time_report(pid, proj.get("manual_minutes")),
    }


@app.get("/api/projects/{pid}/costs")
def costs(pid: str):
    _project(pid)
    return db.cost_log(pid)
