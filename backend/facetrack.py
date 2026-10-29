"""Face tracking with SAM 3.1 and 9:16 crop path.

Frames are sampled at TRACK_FPS per segment and sent to SAM 3.1 with the concept "face".
The crop centre follows the face with EMA smoothing and a max pan speed. Hard rule: the
face box must sit fully inside the crop; if not, the crop is pushed to contain it, and if
the face is wider than the crop the segment falls back to the full-frame "slide" layout.
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import meta_api
from .config import TRACK_FPS

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
            nx = x + EMA_ALPHA * (target - x)
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


def track_segment(pid, video, seg, W, H):
    frames = sample_frames(video, seg["start_sec"], seg["end_sec"])

    def one(f):
        t, jpg = f
        try:
            return t, meta_api.sam_boxes(pid, jpg, "face", f"{seg['id']} t+{t:.1f}s")
        except Exception:
            return t, None

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, frames))
    samples, prev_cx, faces = [], None, []
    for t, boxes in res:
        b = pick_face(boxes or [], prev_cx)
        if b:
            prev_cx = (b[0] + b[2]) / 2
            faces.append(b)
        samples.append((t, b))
    cw = crop_width(W, H)
    big = [b for b in faces if (b[2] - b[0]) >= FACE_MIN_W_FRAC * W]
    layout = "face" if frames and len(big) >= 0.6 * len(frames) else "slide"
    if layout == "face" and any((b[2] - b[0]) > cw - 2 * MARGIN for b in big):
        layout = "slide"  # face wider than a 9:16 crop: can't crop without cutting it
    keys, viol = smooth_crop([(t, b if b and (b[2] - b[0]) >= FACE_MIN_W_FRAC * W else None)
                              for t, b in samples], W, H) if layout == "face" else ([], [])
    best = None
    for (t, jpg), (_, b) in zip(frames, samples):
        if b and (b[2] - b[0]) >= FACE_MIN_W_FRAC * W:
            score = (b[2] - b[0]) * (b[3] - b[1]) * b[4]
            if best is None or score > best[0]:
                best = (score, seg["start_sec"] + t)
    return {
        "segment_id": seg["id"], "layout": layout, "crop_w": cw, "crop_keys": keys,
        "violations_fixed": viol,
        "samples": [{"t": t, "box": list(b[:4]) if b else None, "conf": b[4] if b else None}
                    for t, b in samples],
        "best_frame_t": best[1] if best else None, "best_frame_score": best[0] if best else 0,
    }


def track_all(pid, video, segments, W, H, progress=lambda m: None):
    out = {}
    for i, s in enumerate(segments, 1):
        progress(f"face tracking {i}/{len(segments)}")
        out[s["id"]] = track_segment(pid, video, s, W, H)
    return out


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
