"""FFmpeg render of an APPROVED plan: cut + pause shortening, 9:16 reframe, B-roll,
burned-in captions, background music with sidechain ducking, MP4 + SRT export."""
import json
import shutil
import subprocess

from . import captions as cap
from . import facetrack, thumbnail
from .config import MUSIC_DIR, OUT_FPS, OUT_H, OUT_W

MAX_PAUSE = 0.25   # pauses longer than this between phrases are cut down (no speed-up)
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-r", str(OUT_FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


class RenderError(RuntimeError):
    pass


def ff(args):
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RenderError(p.stderr[-1500:])


def pieces_for(seg, plan):
    """Speech spans of a segment (from its caption phrases), merging small gaps."""
    caps = sorted([c for c in plan["captions"] if c["segment_id"] == seg["id"]],
                  key=lambda c: c["start_sec"])
    spans = []
    for c in caps:
        a, b = max(c["start_sec"], seg["start_sec"]), min(c["end_sec"], seg["end_sec"])
        if b - a < 0.2:
            continue
        if spans and a - spans[-1][1] <= MAX_PAUSE:
            spans[-1][1] = max(spans[-1][1], b)
            spans[-1][2].append(c)
        else:
            spans.append([a, b, [c]])
    return spans or [[seg["start_sec"], seg["end_sec"], caps]]


def caption_y(seg, track, H):
    layout = effective_layout(seg, track)
    if seg["shot_type"] == "b_roll" and seg["b_roll_source"] == "generated_image":
        return 1450
    if layout == "face":
        bottoms = [s["box"][3] for s in track["samples"] if s["box"]]
        fb = sorted(bottoms)[len(bottoms) // 2] / H * OUT_H if bottoms else 1200
        return int(min(max(fb + 60, 1150), 1600))
    fg_h = OUT_W * 9 / 16
    return int(OUT_H / 2 + fg_h / 2 + 60)


def effective_layout(seg, track):
    if seg["shot_type"] == "b_roll" and seg["b_roll_source"] == "same_video":
        return "slide"
    return (track or {}).get("layout") or "slide"


def render_piece(src, seg, track, a, b, out, W, H, seg_offset, seg_total, log):
    dur = b - a
    gen = seg["shot_type"] == "b_roll" and seg["b_roll_source"] == "generated_image"
    if gen and seg.get("b_roll_image"):
        # Ken Burns over the Muse Image still; audio stays the speaker's voice
        frames = max(1, int(round(dur * OUT_FPS)))
        z0 = 1.0 + 0.12 * seg_offset / max(seg_total, 0.1)
        z1 = 1.0 + 0.12 * (seg_offset + dur) / max(seg_total, 0.1)
        vf = (f"scale={OUT_W*2}:{OUT_H*2},zoompan=z='{z0:.4f}+({z1 - z0:.5f})*on/{frames}':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={OUT_W}x{OUT_H}:fps={OUT_FPS}")
        ff(["-loop", "1", "-framerate", str(OUT_FPS), "-i", seg["b_roll_image"],
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-filter_complex", f"[0:v]{vf},trim=duration={dur:.3f},setsar=1[v]",
            "-map", "[v]", "-map", "1:a", "-t", f"{dur:.3f}", *ENC, str(out)])
        return
    layout = effective_layout(seg, track)
    if layout == "face":
        cw = track["crop_w"]
        cmds = []
        t = 0.0
        while t <= dur + 0.05:
            x = facetrack.crop_x_at(track, (a - seg["start_sec"]) + t)
            cmds.append(f"{t:.2f} crop@c x {int(round(x))};")
            t += 0.1
        cmd_file = out.with_suffix(".cmd")
        cmd_file.write_text("\n".join(cmds))
        x0 = int(round(facetrack.crop_x_at(track, a - seg["start_sec"])))
        vf = (f"sendcmd=f='{cmd_file}',crop@c=w={cw}:h={H}:x={x0}:y=0,"
              f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1")
        ff(["-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vf", vf, *ENC, str(out)])
    else:
        if seg["shot_type"] == "a_roll" and track and track.get("layout") == "slide":
            log.append(f"{seg['id']}: speaker too small/large for a safe 9:16 face crop -> "
                       f"full-frame fit with blurred background")
        fc = (f"[0:v]split[a][b];[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
              f"crop={OUT_W}:{OUT_H},gblur=sigma=30,eq=brightness=-0.08[bg];"
              f"[b]scale={OUT_W}:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        ff(["-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a", *ENC, str(out)])


def render(pid, plan, src, probe, tracks, outdir, progress=lambda m: None):
    if plan["status"] != "approved":
        raise RenderError("refusing to render a plan that is not approved")
    W, H = probe["width"], probe["height"]
    work = outdir / "render_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    log, clips, cap_pieces, t_out = [], [], [], 0.0
    kept = [s for s in plan["segments"] if s["keep"]]
    for si, seg in enumerate(kept, 1):
        progress(f"rendering segment {si}/{len(kept)}")
        track = tracks.get(seg["id"])
        if seg["shot_type"] == "b_roll" and seg["b_roll_source"] == "generated_image" \
                and not seg.get("b_roll_image"):
            try:
                seg["b_roll_image"] = thumbnail.make_broll(pid, seg["b_roll_description"],
                                                           outdir / f"broll_{seg['id']}.png")
            except Exception as e:
                log.append(f"{seg['id']}: Muse Image B-roll failed ({e}); using A-roll")
                seg = {**seg, "shot_type": "a_roll", "b_roll_source": None}
        if track and track.get("violations_fixed"):
            log.append(f"{seg['id']}: crop pushed to keep face inside at t={track['violations_fixed']}")
        spans = pieces_for(seg, plan)
        seg_total = sum(b - a for a, b, _ in spans)
        off = 0.0
        cy = caption_y(seg, track, H)
        for pi, (a, b, caps) in enumerate(spans):
            out = work / f"c{si:02d}_{pi:02d}.mp4"
            render_piece(src, seg, track, a, b, out, W, H, off, seg_total, log)
            clips.append(out)
            cap_pieces.append({"src_start": a, "src_end": b, "out_start": t_out, "captions": caps,
                               "cap_y": cy})
            off += b - a
            t_out += b - a
    listf = work / "list.txt"
    listf.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = work / "joined.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy", str(joined)])
    total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", str(joined)], capture_output=True,
                                 text=True).stdout.strip())

    progress("burning captions + mixing music")
    words = cap.timeline_words(cap_pieces)
    srt = outdir / "captions.srt"
    cap.write_srt(words, srt)
    pattern = cap.render_caption_frames(words, total, work / "caps", plan.get("hook_text"))
    final = outdir / "final.mp4"
    music = plan["music"]
    track_file = MUSIC_DIR / f"{music['track_id']}.mp3"
    inputs = ["-i", str(joined), "-framerate", str(cap.CAP_FPS), "-i", pattern]
    vchain = "[1:v]fps=%d,format=rgba[cap];[0:v][cap]overlay=0:0:format=auto:shortest=1[v]" % OUT_FPS
    if music["enabled"] and track_file.exists():
        inputs += ["-stream_loop", "-1", "-i", str(track_file)]
        vol = music["volume"] * 2.0
        achain = (f"[2:a]atrim=0:{total:.3f},volume={vol:.3f},afade=t=in:d=1,"
                  f"afade=t=out:st={max(total - 2, 0):.3f}:d=2[m];[0:a]asplit[v1][v2];"
                  f"[m][v2]sidechaincompress=threshold=0.015:ratio=12:attack=15:release=400[md];"
                  f"[v1][md]amix=inputs=2:normalize=0:duration=first[a]")
    else:
        achain = "[0:a]anull[a]"
    ff([*inputs, "-filter_complex", f"{vchain};{achain}", "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.3f}", *ENC, "-movflags", "+faststart", str(final)])
    (outdir / "render_log.json").write_text(json.dumps({"duration": total, "notes": log}, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return {"mp4": str(final), "srt": str(srt), "duration": total, "notes": log}
