"""Cut the voiced demo down to the submission limit (<= 3:00) without new TTS.

Reuses the existing voice-over clips (demo_dir/vo/gNN.mp3, from add_voiceover.py) and the
assembled parts (demo_dir/parts). Each line gets a visual window from its part, re-timed to
the line's length, so picture and voice stay in sync. The final short plays with its own audio.

Usage: python scripts/make_demo_3min.py demo_out_v7 [out.mp4]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W, H, FPS = 1920, 1080, 30
TEMPO = 1.05        # voice slightly faster, still natural
GAP = 0.3           # breath after each line
SHORT_SEC = 25.0    # how much of the final short to play
LIMIT = 180.0

# (voice clip index, part, window start, window end) — window is in part-local seconds
PLAN = [
    (0, "p0.mp4", 0, 5),
    (1, "explain/e1.mp4", 0, 13),
    (2, "explain/e2.mp4", 0, 10),
    (3, "explain/e3.mp4", 0, 12),
    (4, "explain/e4.mp4", 0, 8),
    (5, "explain/e5.mp4", 0, 12),
    (6, "explain/e6.mp4", 0, 10),
    (7, "explain/e7.mp4", 0, 13),
    (8, "p_demo.mp4", 0, 3),
    (9, "p1.mp4", 0, None),
    (10, "p2.mp4", 0, None),
    (11, "p3.mp4", 0, 4.5),
    (12, "p3.mp4", 4.5, 13.5),
    (13, "p3.mp4", 13.5, 21),
    (14, "p3.mp4", 21, 34),
    (16, "p3.mp4", 34, 50),
    (17, "p3.mp4", 50, 66),
    (19, "p3.mp4", 79, None),
    (20, "p4.mp4", 0, None),
    (21, "p5.mp4", 0, 12),
    (23, "p6.mp4", 0, None),
    ("short", "p7.mp4", 0, SHORT_SEC),
    (24, "p9.mp4", 0, None),
    (25, "p8.mp4", 0, None),
]
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True, check=True).stdout)


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def main(demo_dir, out=None):
    dd = Path(demo_dir)
    work = dd / "cut3"
    work.mkdir(exist_ok=True)
    clips, t, short_range = [], 0.0, None
    for n, (vo, part, a, b) in enumerate(PLAN):
        src = dd / "parts" / part
        b = dur(src) if b is None else b
        clip = work / f"k{n:02d}.mp4"
        if vo == "short":
            d = b - a
            ff("-ss", f"{a}", "-t", f"{d}", "-i", str(src), "-vf", f"fps={FPS},fade=t=out:st={d - 0.6}:d=0.6",
               "-af", f"afade=t=out:st={d - 1.2}:d=1.2", *ENC, str(clip))
            short_range = (t, t + d)
        else:
            mp3 = dd / "vo" / f"g{vo:02d}.mp3"
            d = dur(mp3) / TEMPO + GAP
            speed = (b - a) / d  # >1 speeds the picture up, <1 holds stills longer
            ff("-ss", f"{a}", "-t", f"{b - a}", "-i", str(src), "-i", str(mp3), "-filter_complex",
               f"[0:v]setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS},scale={W}:{H},setsar=1,"
               f"tpad=stop_mode=clone:stop_duration=1[v];"
               f"[1:a]atempo={TEMPO},aresample=48000,aformat=channel_layouts=stereo,apad[a]",
               "-map", "[v]", "-map", "[a]", "-t", f"{d:.3f}", *ENC, str(clip))
            if speed > 3:
                print(f"note: line {vo} picture sped up {speed:.1f}x")
        clips.append(clip)
        t += dur(clip)
    total = t
    print(f"total {total:.1f}s")
    if total > LIMIT:
        raise SystemExit(f"too long: {total:.1f}s > {LIMIT}s")

    ins, fc = [], ""
    for i, c in enumerate(clips):
        ins += ["-i", str(c)]
        fc += (f"[{i}:v]scale={W}:{H},setsar=1,fps={FPS},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v{i}];"
               f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}];")
    k = len(clips)
    fc += "".join(f"[v{i}][a{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=1[v][main];"
    # quiet CC0 music bed under the voice, muted while the short plays its own mix
    s0, s1 = short_range
    fc += (f"[{k}:a]atrim=0:{total:.3f},aresample=48000,aformat=channel_layouts=stereo,"
           f"volume='if(between(t,{s0 - 0.4:.2f},{s1 + 0.4:.2f}),0,0.22)':eval=frame,"
           f"afade=t=out:st={total - 2:.2f}:d=2[bed];[main]asplit[m1][m2];"
           f"[bed][m2]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
           f"[m1]volume=1.0[m1v];[m1v][duck]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
    ins += ["-stream_loop", "-1", "-i", str(ROOT / "assets/music/royalty_free_01.mp3")]
    out = Path(out) if out else dd / "musecut_demo_3min.mp4"
    ff(*ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *ENC, "-movflags", "+faststart",
       "-t", f"{total:.3f}", str(out))
    print(out, f"{dur(out):.1f}s")


if __name__ == "__main__":
    main(*sys.argv[1:])
