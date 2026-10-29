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

from . import meta_api
from .ingest import extract_wav

MIN_UNIT, MAX_UNIT = 2.5, 15.0
PAD = 0.12


def detect_silences(wav, noise_db=-32, min_sil=0.3):
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(wav), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return list(zip(starts, ends))


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
