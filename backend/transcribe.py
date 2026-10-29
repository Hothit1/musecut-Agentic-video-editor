"""Transcript with phrase-level timestamps using Muse Voice Transcribe.

Muse Voice Transcribe returns turn-level startMs/endMs (no per-word times). To get
fine cut points we split the audio at detected silences into 2-15 s phrases and
transcribe each phrase; each phrase becomes one "unit" the planner can pick.
Word times inside a unit are estimated in captions.py (proportional to word length).
"""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import meta_api
from .ingest import extract_wav

MIN_UNIT, MAX_UNIT = 2.5, 15.0
PAD = 0.12


def pauses_from_db(db, hop, rel_db=6.0, min_pause=0.25):
    """Pauses = runs of frames quieter than (15th-percentile level + rel_db), capped below the
    median so speech is never counted. Adapts to a music bed or room noise, unlike a fixed
    -32 dB gate. db: per-frame level in dBFS, hop: frame length in seconds."""
    thr = min(np.percentile(db, 15) + rel_db, np.median(db) - 3.0)
    low = db < thr
    out, i, n = [], 0, len(db)
    while i < n:
        if low[i]:
            j = i
            while j < n and low[j]:
                j += 1
            if (j - i) * hop >= min_pause:
                out.append((round(i * hop, 3), round(j * hop, 3)))
            i = j
        else:
            i += 1
    return out


def detect_silences(wav, hop=0.05):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win = int(16000 * hop)
    n = len(x) // win
    if n == 0:
        return []
    rms = np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(1) + 1e-12)
    return pauses_from_db(20 * np.log10(rms), hop)


def plan_chunks(duration, silences):
    """Cut points at silence midpoints so each chunk is MIN_UNIT..MAX_UNIT long."""
    mids = [(s + e) / 2 for s, e in silences]
    cuts, last = [0.0], 0.0
    for m in mids:
        if m - last >= MIN_UNIT:
            # force intermediate hard cuts if the speaker never paused
            while m - last > MAX_UNIT:
                last += MAX_UNIT
                cuts.append(last)
            if m - last >= MIN_UNIT:
                cuts.append(m)
                last = m
    while duration - last > MAX_UNIT:
        last += MAX_UNIT
        cuts.append(last)
    cuts.append(duration)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > 0.4]


_GLUED_TAIL = re.compile(r"\b(\w*?(\w{2,}))([.,!?])\2\3")


def clean_asr_text(text):
    """Fix an ASR artifact where a word's tail is repeated glued to it ("industrialize.ize.")."""
    return _GLUED_TAIL.sub(r"\1\3", text)


def transcribe(pid, video_path, duration, workdir, progress=lambda m: None):
    workdir.mkdir(parents=True, exist_ok=True)
    full = extract_wav(video_path, workdir / "full.wav")
    chunks = plan_chunks(duration, detect_silences(full))
    progress(f"transcribing {len(chunks)} phrases")

    def one(i_ab):
        i, (a, b) = i_ab
        wav = extract_wav(full, workdir / f"c{i:04d}.wav", a, b - a)
        out = meta_api.transcribe_wav(pid, wav, f"phrase {i} [{a:.1f}-{b:.1f}s]")
        turns = [t for t in out.get("turns", []) if t.get("transcript", "").strip()]
        text = " ".join(t["transcript"].strip() for t in turns) or out.get("transcript", "").strip()
        text = clean_asr_text(text)
        if not text:
            return None
        if turns:
            s = a + min(t["startMs"] for t in turns) / 1000 - PAD
            e = a + max(t["endMs"] for t in turns) / 1000 + PAD
        else:
            s, e = a, b
        return {"start": round(max(a, s), 3), "end": round(min(b, max(e, s + 0.5)), 3), "text": text}

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, enumerate(chunks)))
    units = []
    for r in res:
        if r:
            r["id"] = f"u{len(units) + 1:03d}"
            units.append(r)
    (workdir / "transcript.json").write_text(json.dumps(units, indent=1))
    return units
