"""Thin clients for Meta Model API (api.meta.ai). Every call writes one cost-log entry."""
import base64
import io
import json
import re
import time

import requests
from openai import OpenAI
from PIL import Image

from . import db
from .config import (API_KEY, ASR_MODEL, BASE_URL, IMAGE_MODEL, PRICES, SAM_MODEL,
                     SPARK_MODEL, SPARK_REASONING)

_client = None


def client():
    global _client
    if _client is None:
        if not API_KEY:
            raise RuntimeError("META_API_KEY is not set (see .env.example)")
        _client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=600, max_retries=3)
    return _client


def _retry(fn, attempts=7):
    """Retry transient failures (rate limit, 5xx, network/DNS) with capped exponential backoff:
    1, 2, 4, 8, 16, 30 s -> rides out ~1 min of network loss before failing the job."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # network / 429 / 5xx
            msg = str(e)
            transient = any(s in msg for s in ("429", "500", "502", "503", "504", "timed out",
                                                 "Connection", "Timeout", "NameResolution",
                                                 "Temporary failure"))
            if i == attempts - 1 or not transient:
                raise
            time.sleep(min(2 ** i, 30))


# ---------- Muse Voice Transcribe ----------
def transcribe_wav(pid, wav_path, label):
    t0 = time.time()

    def call():
        with open(wav_path, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/asr/transcribe",
                headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
                files={
                    "request": (None, json.dumps({"model": ASR_MODEL, "audioEncoding": "WAV",
                                                  "mode": "ENDPOINTING"}), "application/json"),
                    "audio": ("audio.wav", f, "audio/wav"),
                },
                timeout=300,
            )
        if r.status_code >= 400:
            raise RuntimeError(f"ASR HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    try:
        out = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=ASR_MODEL, step="transcribe", input_summary=label,
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    ms = out.get("audioDurationMs", 0)
    db.add_cost(pid, model=ASR_MODEL, step="transcribe", input_summary=label,
                output_summary=out.get("transcript", "")[:120], units=f"{ms/1000:.1f}s audio",
                est_cost_usd=ms / 3_600_000 * PRICES["asr_per_hour"],
                latency_ms=int((time.time() - t0) * 1000))
    return out


# ---------- Muse Spark ----------
def spark_json(pid, step, system, user, schema, input_summary):
    """Ask Spark for JSON matching `schema`. Returns (dict, raw_text). Raises on bad JSON."""
    t0 = time.time()

    def call():
        return client().responses.create(
            model=SPARK_MODEL,
            instructions=system,
            input=user,
            reasoning={"effort": SPARK_REASONING},
            text={"format": {"type": "json_schema", "name": step, "schema": schema, "strict": False}},
        )

    try:
        r = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=SPARK_MODEL, step=step, input_summary=input_summary,
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    u = r.usage
    cost = (u.input_tokens * PRICES["spark_in_per_m"] + u.output_tokens * PRICES["spark_out_per_m"]) / 1e6
    raw = r.output_text or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
        status = "ok"
    except Exception:
        data, status = None, "invalid_json"
    db.add_cost(pid, model=SPARK_MODEL, step=step, input_summary=input_summary,
                output_summary=raw[:200], units=f"in={u.input_tokens} out={u.output_tokens}",
                est_cost_usd=cost, status=status, latency_ms=int((time.time() - t0) * 1000))
    if data is None:
        raise ValueError("Spark returned non-JSON output")
    return data, raw


# ---------- SAM 3.1 ----------
_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")


def sam_boxes(pid, jpeg_bytes, concept, label):
    """Segment one image with a noun-phrase prompt. Returns list of (x1,y1,x2,y2,conf) in image px."""
    t0 = time.time()
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()

    def call():
        return client().responses.create(
            model=SAM_MODEL,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": url}]}],
            metadata={"include_confidence": "true"},
        )

    try:
        r = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=SAM_MODEL, step="facetrack", input_summary=label,
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    boxes = []
    for m in _BOX.finditer(r.output_text or ""):
        x1, y1, x2, y2, w, h = map(int, m.groups()[:6])
        boxes.append((x1, y1, x2, y2, float(m.group(7) or 1.0)))
    db.add_cost(pid, model=SAM_MODEL, step="facetrack", input_summary=label,
                output_summary=f"{len(boxes)} box(es) for '{concept}'", units="1 image",
                est_cost_usd=PRICES["sam_image_each"], latency_ms=int((time.time() - t0) * 1000))
    return boxes


_LINE = re.compile(r"<(\d+)f>(.*)")
_REC = re.compile(r"(?:^|,)(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")


def parse_sam_video(text):
    """SAM 3.1 video output -> {frame_index: [(object_id, (x1,y1,x2,y2), conf, (w,h))]}.
    Frame markers may skip (no objects) and object ids are sparse, so key by both."""
    frames = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        recs = []
        for r in _REC.finditer(m.group(2)):
            oid, x1, y1, x2, y2, w, h = map(int, r.groups()[:7])
            recs.append((oid, (x1, y1, x2, y2), float(r.group(8) or 1.0), (w, h)))
        frames[int(m.group(1))] = recs
    return frames


def sam_video(pid, mp4_bytes, concept, label):
    """Track a concept through a short clip in ONE request (SAM 3.1 video mode)."""
    t0 = time.time()
    url = "data:video/mp4;base64," + base64.b64encode(mp4_bytes).decode()

    def call():
        return client().responses.create(
            model=SAM_MODEL,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_video", "video_url": url}]}],
            metadata={"include_confidence": "true"},
        )

    try:
        r = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=SAM_MODEL, step="facetrack_video", input_summary=label,
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    frames = parse_sam_video(r.output_text or "")
    n = getattr(r.usage, "video_frames_processed", None) or max(len(frames), 1)
    ids = {rec[0] for recs in frames.values() for rec in recs}
    db.add_cost(pid, model=SAM_MODEL, step="facetrack_video", input_summary=label,
                output_summary=f"{len(frames)} frames with '{concept}', {len(ids)} tracked object(s)",
                units=f"{n} video frames", est_cost_usd=max(n, 50) * PRICES["sam_video_frame"],
                latency_ms=int((time.time() - t0) * 1000))
    return frames


# ---------- Muse Image ----------
def _decode_image(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def image_generate(pid, prompt, step, size="1080x1920"):
    t0 = time.time()

    def call():
        return client().images.generate(model=IMAGE_MODEL, prompt=prompt, n=1, size=size)

    try:
        r = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=IMAGE_MODEL, step=step, input_summary=prompt[:200],
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    img = _decode_image(r.data[0].b64_json)
    db.add_cost(pid, model=IMAGE_MODEL, step=step, input_summary=prompt[:200],
                output_summary=f"image {img.size[0]}x{img.size[1]}", units="1 image",
                est_cost_usd=PRICES["image_each"], latency_ms=int((time.time() - t0) * 1000))
    return img


def image_edit(pid, prompt, ref_jpeg, step, size="1080x1920"):
    t0 = time.time()
    url = "data:image/jpeg;base64," + base64.b64encode(ref_jpeg).decode()

    def call():
        r = requests.post(
            f"{BASE_URL}/images/edits",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": IMAGE_MODEL, "prompt": prompt, "images": [{"image_url": url}],
                  "n": 1, "size": size},
            timeout=600,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"images/edits HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    try:
        out = _retry(call)
    except Exception as e:
        db.add_cost(pid, model=IMAGE_MODEL, step=step, input_summary=prompt[:200],
                    output_summary=str(e)[:200], status="failed",
                    latency_ms=int((time.time() - t0) * 1000))
        raise
    img = _decode_image(out["data"][0]["b64_json"])
    db.add_cost(pid, model=IMAGE_MODEL, step=step, input_summary=prompt[:200],
                output_summary=f"edited image {img.size[0]}x{img.size[1]}", units="1 image",
                est_cost_usd=PRICES["image_each"], latency_ms=int((time.time() - t0) * 1000))
    return img
