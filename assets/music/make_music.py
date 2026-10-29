"""Generate MuseCut's royalty-free background track procedurally (no samples, no AI).

Output: royalty_free_01.mp3 — a calm 90 s ambient pad (Am-F-C-G) with a soft pulse.
Released under CC0 by the MuseCut authors. Run: python assets/music/make_music.py
"""
import subprocess
import wave
from pathlib import Path

import numpy as np

SR, DUR, BPM = 44100, 96, 84
HERE = Path(__file__).parent
CHORDS = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G (MIDI)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def main():
    t = np.arange(int(SR * DUR)) / SR
    out = np.zeros((len(t), 2))
    bar = 4 * 60 / BPM
    for i in range(int(DUR / bar) + 1):
        chord = CHORDS[i % 4]
        s, e = int(i * bar * SR), min(len(t), int((i + 1) * bar * SR + 0.5 * SR))
        if s >= len(t):
            break
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 0.8) * np.exp(-tt / (bar * 1.6))
        for k, m in enumerate(chord + [chord[0] - 12]):
            f = hz(m)
            tone = (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)
                    + 0.1 * np.sin(6 * np.pi * f * tt))
            pan = 0.35 + 0.1 * k
            out[s:e, 0] += tone * env * (1 - pan) * 0.12
            out[s:e, 1] += tone * env * pan * 0.12
    beat = 60 / BPM
    pulse = np.zeros(len(t))
    for b in np.arange(0, DUR, beat):
        s = int(b * SR)
        n = min(int(0.25 * SR), len(t) - s)
        tt = np.arange(n) / SR
        pulse[s:s + n] += np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 18) * 0.35
    out += pulse[:, None]
    fade = np.minimum(1, np.minimum(t / 2.0, (DUR - t) / 3.0))
    out *= fade[:, None]
    out /= np.max(np.abs(out)) * 1.25
    pcm = (out * 32767).astype(np.int16)
    wav = HERE / "royalty_free_01.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-b:a", "160k",
                    str(HERE / "royalty_free_01.mp3")], check=True)
    wav.unlink()


if __name__ == "__main__":
    main()
