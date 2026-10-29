"""Add an English voice-over (macOS `say`) and a quiet CC0 music bed to the assembled demo.

Usage: python scripts/add_voiceover.py demo_out_v7
Reads demo_dir/parts/list.txt for part order/durations; the final short (p7) keeps its own audio.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE, RATE = "Samantha", 182

# (part, offset seconds inside the part, line)
LINES = [
    ("p0.mp4", 0.3, "MuseCut, an agentic video editor built on Meta's models."),
    ("explain/e1.mp4", 0.3, "A long video goes in. Four Meta models produce an editable plan, and nothing renders until you approve it."),
    ("explain/e2.mp4", 0.3, "Muse Voice Transcribe turns the speech into short, timed sentences, split at natural pauses."),
    ("explain/e3.mp4", 0.3, "Muse Spark reads the transcript and returns schema-checked JSON. It picks sentence IDs, so it can never invent timestamps."),
    ("explain/e4.mp4", 0.3, "SAM 3 point 1 tracks the speaker's face in video mode, and the crop always keeps it in frame."),
    ("explain/e5.mp4", 0.3, "A-roll is the speaker. B-roll comes from the source itself, or from Muse Image, with a slow Ken Burns move."),
    ("explain/e6.mp4", 0.3, "Muse Image edits the best real frame into three thumbnail styles."),
    ("explain/e7.mp4", 0.3, "Captions highlight each word, and royalty-free music ducks under the voice."),
    ("p_demo.mp4", 0.2, "Now the live app, on one real run."),
    ("p1.mp4", 0.3, "Upload a fifteen minute creator video, describe the short you want, and give your manual edit estimate."),
    ("p2.mp4", 0.3, "About two minutes of real processing, sped up here."),
    ("p3.mp4", 0.5, "The edit plan is a draft."),
    ("p3.mp4", 4.5, "The preview shows SAM's face boxes and the smoothed nine by sixteen crop."),
    ("p3.mp4", 13.5, "Spark wrote the title, hook, post copy and hashtags. All editable."),
    ("p3.mp4", 21.0, "Pick one of three Muse Image thumbnails."),
    ("p3.mp4", 28.0, "Spark also chapters the whole video."),
    ("p3.mp4", 34.0, "Each segment shows its role, timing, score, and why Spark kept it. Caption text can be fixed, while timestamps stay locked."),
    ("p3.mp4", 50.0, "Segments that don't fit sixty seconds are dropped automatically, weakest first."),
    ("p3.mp4", 66.0, "Switch to ninety seconds and the strongest dropped segments come back. Switch back, and they're trimmed again."),
    ("p3.mp4", 79.0, "Then approve. That locks the plan."),
    ("p4.mp4", 0.2, "FFmpeg renders the cuts, reframe, captions and music."),
    ("p5.mp4", 0.5, "The export: video, subtitles, thumbnail, plan JSON, post copy, and a cost log for every model call."),
    ("p5.mp4", 12.0, "And the time saved: measured machine time plus review, against the creator's own estimate."),
    ("p6.mp4", 0.2, "Here's the result."),
    ("p9.mp4", 0.4, "About three and a half minutes with MuseCut, versus two hours by hand."),
    ("p8.mp4", 0.4, "About twenty cents per short, every call logged. MuseCut."),
]


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True).stdout)


def main(demo_dir):
    dd = Path(demo_dir)
    parts = [l.split("'")[1] for l in (dd / "parts/list.txt").read_text().splitlines() if l.strip()]
    start, t = {}, 0.0
    for p in parts:
        start[p] = (t, dur(dd / "parts" / p))
        t += start[p][1]
    total = t
    vo = dd / "vo"
    vo.mkdir(exist_ok=True)
    ins, fc, labels = [], [], []
    for i, (part, off, text) in enumerate(LINES):
        f = vo / f"l{i:02d}.aiff"
        subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(f), text], check=True)
        at = start[part][0] + off
        nxt = [start[p][0] + o for p, o, _ in LINES[i + 1:i + 2]] or [total]
        room = nxt[0] - at - 0.2
        d = dur(f)
        if d > room:
            print(f"WARN line {i} {d:.1f}s > room {room:.1f}s: {text[:40]}")
        ins += ["-i", str(f)]
        fc.append(f"[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={int(at * 1000)}:all=1[v{i}]")
        labels.append(f"[v{i}]")
    n = len(LINES)
    s7, d7 = start["p7.mp4"]
    music = ROOT / "assets/music/royalty_free_01.mp3"
    ins += ["-stream_loop", "-1", "-i", str(music)]
    # quiet bed everywhere except the final short (which has its own mix)
    fc.append(f"[{n + 1}:a]atrim=0:{total:.3f},aresample=48000,aformat=channel_layouts=stereo,"
              f"volume='if(between(t,{s7 - 0.5:.2f},{s7 + d7 + 0.5:.2f}),0,0.25)':eval=frame[bed]")
    fc.append(f"[0:a]aresample=48000[orig]")
    fc.append("".join(labels) + f"amix=inputs={n}:normalize=0,volume=6,asplit[vo][vosc]")
    fc.append("[bed][vosc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
    fc.append("[orig][duck][vo]amix=inputs=3:normalize=0,alimiter=limit=0.95[a]")
    out = dd / "musecut_demo_vo.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dd / "musecut_demo.mp4"), *ins,
                    "-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)],
                   check=True)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
