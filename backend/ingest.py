"""FFmpeg probe + audio extraction."""
import json
import subprocess

from .config import MAX_DURATION_SEC


class IngestError(ValueError):
    pass


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-800:]}")
    return p.stdout


def probe(path):
    out = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                          str(path)]))
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise IngestError("No video stream found")
    if a is None:
        raise IngestError("Video has no audio track")
    dur = float(out["format"]["duration"])
    if dur > MAX_DURATION_SEC:
        raise IngestError(f"Video is {dur/60:.1f} min; demo limit is {MAX_DURATION_SEC//60} min")
    num, den = v.get("r_frame_rate", "25/1").split("/")
    return {"duration": dur, "width": int(v["width"]), "height": int(v["height"]),
            "fps": float(num) / float(den or 1), "has_audio": True}


def extract_wav(src, dst, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    run(cmd)
    return dst
