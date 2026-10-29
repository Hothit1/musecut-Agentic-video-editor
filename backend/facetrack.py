"""Face tracking with SAM 3.1 and 9:16 crop path.

Primary path: SAM 3.1 *video mode*. Each segment is sent as one low-res 5 fps proxy clip with
the concept "face"; SAM returns a box per frame with stable object ids, so the speaker is
followed by id (no client-side box matching) at ~1/5 the cost of per-image calls.
Fallback: per-image calls at TRACK_FPS if the video request fails.
The crop centre follows the face with EMA smoothing and a max pan speed. Hard rule: the
face box must sit fully inside the crop; if not, the crop is pushed to contain it, and if
the face is wider than the crop the segment falls back to the full-frame "slide" layout.
"""
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import meta_api
from .config import TRACK_FPS, TRACK_VIDEO_FPS

PROXY_W = 640

FACE_MIN_W_FRAC = 0.10   # face narrower than this => speaker is a thumbnail (slide/screen share)
EMA_ALPHA = 0.35
MAX_SPEED_PX_S = 160.0
MARGIN = 8


def crop_width(w, h):
    return min(w, int(round(h * 9 / 16 / 2)) * 2)


def sample_frames(video, start, end, fps=TRACK_FPS):
    """Return [(t_rel, jpeg_bytes)] for frames at start + k/fps."""
    times, t = [], 0.0
    while start + t < end - 0.05:
        times.append(t)
        t += 1.0 / fps
    out = []
    for tr in times:
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start + tr:.3f}", "-i", str(video),
                            "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True)
        if p.returncode == 0 and p.stdout:
            out.append((tr, p.stdout))
    return out


def pick_face(boxes, prev_cx=None):
    """Largest confident box (or nearest to previous centre when several)."""
    boxes = [b for b in boxes if b[4] >= 0.4]
    if not boxes:
        return None
    if prev_cx is not None and len(boxes) > 1:
        return min(boxes, key=lambda b: abs((b[0] + b[2]) / 2 - prev_cx) - (b[2] - b[0]))
    return max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]) * b[4])


def smooth_crop(samples, W, H, fps=TRACK_FPS):
    """samples: [(t, box|None)] -> (keyframes [(t, x)], violations [t]) ; x = crop left edge."""
    cw = crop_width(W, H)
    max_step = MAX_SPEED_PX_S / fps
    alpha = 1 - (1 - EMA_ALPHA) ** (TRACK_FPS / fps)  # same smoothing time-constant at any fps
    keys, viol = [], []
    x = None
    last_box = None
    for t, box in samples:
        box = box or last_box
        if box is not None:
            last_box = box
            target = (box[0] + box[2]) / 2 - cw / 2
        else:
            target = (W - cw) / 2
        target = min(max(target, 0), W - cw)
        if x is None:
            x = target
        else:
            nx = x + alpha * (target - x)
            x = x + max(-max_step, min(max_step, nx - x))
        if box is not None:
            # hard rule: face fully inside crop (with margin) -> push crop, never cut the face
            if box[0] - MARGIN < x:
                x = box[0] - MARGIN
            if box[2] + MARGIN > x + cw:
                x = box[2] + MARGIN - cw
            x = min(max(x, 0), W - cw)
            if box[0] < x or box[2] > x + cw:
                viol.append(round(t, 2))
        keys.append((round(t, 3), int(round(x))))
    return keys, viol


def proxy_clip(video, start, end, fps=TRACK_VIDEO_FPS):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                        "-i", str(video), "-an", "-vf", f"fps={fps},scale={PROXY_W}:-2",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                        "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", "-"],
                       capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("proxy clip failed: " + p.stderr.decode()[-300:])
    return p.stdout


def speaker_track(frames, W, H):
    """Pick the speaker's object id (largest confident area over time) and return
    {frame_index: (x1,y1,x2,y2,conf)} in SOURCE pixels."""
    score = {}
    for recs in frames.values():
        for oid, (x1, y1, x2, y2), c, _ in recs:
            score[oid] = score.get(oid, 0) + (x2 - x1) * (y2 - y1) * c
    if not score:
        return {}
    sid = max(score, key=score.get)
    out = {}
    for k, recs in frames.items():
        for oid, (x1, y1, x2, y2), c, (pw, ph) in recs:
            if oid == sid:
                sx, sy = W / pw, H / ph
                out[k] = (x1 * sx, y1 * sy, x2 * sx, y2 * sy, c)
    return out


def samples_video(pid, video, seg, W, H):
    clip = proxy_clip(video, seg["start_sec"], seg["end_sec"])
    frames = meta_api.sam_video(pid, clip, "face", f"{seg['id']} video {seg['end_sec'] - seg['start_sec']:.1f}s")
    track = speaker_track(frames, W, H)
    n = max(1, math.ceil((seg["end_sec"] - seg["start_sec"]) * TRACK_VIDEO_FPS))
    return [(k / TRACK_VIDEO_FPS, track.get(k)) for k in range(n)], TRACK_VIDEO_FPS, "sam3.1_video"


def samples_images(pid, video, seg):
    frames = sample_frames(video, seg["start_sec"], seg["end_sec"])

    def one(f):
        t, jpg = f
        try:
            return t, meta_api.sam_boxes(pid, jpg, "face", f"{seg['id']} t+{t:.1f}s")
        except Exception:
            return t, None

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, frames))
    samples, prev_cx = [], None
    for t, boxes in res:
        b = pick_face(boxes or [], prev_cx)
        if b:
            prev_cx = (b[0] + b[2]) / 2
        samples.append((t, b))
    return samples, TRACK_FPS, "sam3.1_images"


def track_segment(pid, video, seg, W, H):
    try:
        samples, fps, mode = samples_video(pid, video, seg, W, H)
    except Exception:
        samples, fps, mode = samples_images(pid, video, seg)
    samples = [(t, b if b and b[4] >= 0.4 else None) for t, b in samples]
    faces = [b for _, b in samples if b]
    cw = crop_width(W, H)
    big = [b for b in faces if (b[2] - b[0]) >= FACE_MIN_W_FRAC * W]
    layout = "face" if samples and len(big) >= 0.6 * len(samples) else "slide"
    if layout == "face" and any((b[2] - b[0]) > cw - 2 * MARGIN for b in big):
        layout = "slide"  # face wider than a 9:16 crop: can't crop without cutting it
    keys, viol = smooth_crop([(t, b if b and (b[2] - b[0]) >= FACE_MIN_W_FRAC * W else None)
                              for t, b in samples], W, H, fps) if layout == "face" else ([], [])
    best = None
    for t, b in samples:
        if b and (b[2] - b[0]) >= FACE_MIN_W_FRAC * W:
            score = (b[2] - b[0]) * (b[3] - b[1]) * b[4]
            if best is None or score > best[0]:
                best = (score, seg["start_sec"] + t)
    return {
        "segment_id": seg["id"], "layout": layout, "crop_w": cw, "crop_keys": keys,
        "violations_fixed": viol, "mode": mode, "fps": fps,
        "samples": [{"t": round(t, 3), "box": [round(v, 1) for v in b[:4]] if b else None,
                     "conf": b[4] if b else None} for t, b in samples],
        "best_frame_t": best[1] if best else None, "best_frame_score": best[0] if best else 0,
    }


def track_all(pid, video, segments, W, H, progress=lambda m: None):
    progress(f"face tracking {len(segments)} segments (SAM 3.1 video mode)")
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda s: track_segment(pid, video, s, W, H), segments))
    return {r["segment_id"]: r for r in res}


def crop_x_at(track, t):
    """Linear interpolation of crop x at segment-relative time t."""
    keys = track["crop_keys"]
    if not keys:
        return None
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return x0 + (x1 - x0) * (t - t0) / max(t1 - t0, 1e-6)
    return keys[-1][1]
