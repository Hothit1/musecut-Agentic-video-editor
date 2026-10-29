"""Assemble the hackathon demo video (1920x1080) from a real UI recording + the rendered short.

Usage: python scripts/make_demo_video.py demo_out <project_id>
Waiting periods (plan job, render job) are sped up and labelled as such.
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_R = "/System/Library/Fonts/Supplemental/Arial.ttf"
W, H, FPS = 1920, 1080, 30
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def f(size, bold=True):
    return ImageFont.truetype(FONT if bold else FONT_R, size)


def card(path, lines, bg=(14, 15, 19)):
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    y = sum(-s // 2 - 14 for _, s, _ in lines) + H // 2
    for text, size, col in lines:
        font = f(size, size >= 40)
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=col)
        y += size + 28
    im.save(path)


def banner(path, text):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = f(34)
    w = d.textlength(text, font=font)
    d.rounded_rectangle(((W - w) / 2 - 28, H - 96, (W + w) / 2 + 28, H - 34), radius=16,
                        fill=(79, 140, 255, 235))
    d.text(((W - w) / 2, H - 88), text, font=font, fill="white")
    im.save(path)


def still(png, dur, out):
    ff("-loop", "1", "-t", f"{dur}", "-i", str(png), "-f", "lavfi", "-t", f"{dur}", "-i",
       "anullsrc=r=48000:cl=stereo", "-vf", f"fps={FPS},format=yuv420p", "-shortest", *ENC, str(out))


def screen_part(src, a, b, speed, label_png, out):
    vf = (f"[0:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{speed},fps={FPS},"
          f"scale=1728:1080,pad={W}:{H}:96:0:color=0x0e0f13[s]")
    if label_png:
        vf += f";[s][1:v]overlay=0:0[v]"
        ins = ["-i", str(src), "-loop", "1", "-i", str(label_png)]
    else:
        vf += ";[s]null[v]"
        ins = ["-i", str(src)]
    dur = (b - a) / speed
    ff(*ins, "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
       "-filter_complex", vf, "-map", "[v]", "-map", f"{2 if label_png else 1}:a",
       "-t", f"{dur:.3f}", *ENC, str(out))


def main(demo_dir, pid):
    dd = Path(demo_dir)
    work = dd / "parts"
    work.mkdir(exist_ok=True)
    m = dict((k, v) for k, v in json.loads((dd / "marks.json").read_text())["marks"])
    exp = json.loads(subprocess.run(["curl", "-s", f"http://127.0.0.1:8765/api/projects/{pid}/export"],
                                    capture_output=True, text=True).stdout)
    src = dd / "screen.webm"
    parts = []

    card(work / "c1.png", [("MuseCut", 120, (255, 255, 255)),
                           ("Long video  →  editable Edit Plan  →  approved 9:16 short", 44, (79, 140, 255)),
                           ("Muse Voice Transcribe · Muse Spark 1.3 · SAM 3.1 · Muse Image", 34, (200, 167, 255)),
                           ("Input: 15-min public-domain lecture (Robert Citino, archive.org, PD Mark 1.0)",
                            26, (154, 161, 178))])
    still(work / "c1.png", 5, work / "p0.mp4"); parts.append("p0.mp4")

    banner(work / "b1.png", "1 · Upload a 15-min video + prompt")
    screen_part(src, 0, m["click_create"] + 1.5, 1, work / "b1.png", work / "p1.mp4"); parts.append("p1.mp4")
    wait = m["editor_open"] - m["click_create"]
    banner(work / "b2.png", f"2 · Plan job: transcribe → Spark plan → SAM 3.1 face track → Muse Image "
                            f"({wait/60:.1f} min real time, sped up)")
    screen_part(src, m["click_create"] + 1.5, m["editor_open"], max(1, wait / 9), work / "b2.png",
                work / "p2.mp4"); parts.append("p2.mp4")
    banner(work / "b3.png", "3 · Edit the plan: preview 9:16 crop, edit captions, drop segments, pick thumbnail")
    screen_part(src, m["editor_open"], m["approve"] + 1, 1, work / "b3.png", work / "p3.mp4"); parts.append("p3.mp4")
    rw = m["render_done"] - m["approve"] - 1
    banner(work / "b4.png", f"4 · Approve locks the plan → FFmpeg render ({rw:.0f} s real time, sped up)")
    screen_part(src, m["approve"] + 1, m["render_done"], max(1, rw / 6), work / "b4.png",
                work / "p4.mp4"); parts.append("p4.mp4")
    banner(work / "b5.png", "5 · Export: MP4 + SRT + thumbnail + plan JSON + cost log + post copy")
    screen_part(src, m["render_done"], m["end"], 1, work / "b5.png", work / "p5.mp4"); parts.append("p5.mp4")

    card(work / "c2.png", [("The result (with sound)", 64, (255, 255, 255)),
                           ("9:16 · face always inside the crop · word-highlight captions · ducked music",
                            32, (154, 161, 178))])
    still(work / "c2.png", 3, work / "p6.mp4"); parts.append("p6.mp4")
    short = ROOT / "data" / pid / "final.mp4"
    ff("-i", str(short), "-filter_complex",
       f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
       f"gblur=sigma=40,eq=brightness=-0.25[bg];[b]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,fps={FPS}[v]",
       "-map", "[v]", "-map", "0:a", *ENC, str(work / "p7.mp4")); parts.append("p7.mp4")

    cs = exp["cost_summary"]
    lines = [("Every model call is logged", 56, (255, 255, 255))]
    for model, v in cs["by_model"].items():
        lines.append((f"{model}:  {v['calls']} calls · ${v['usd']:.3f}", 34, (200, 167, 255)))
    lines += [(f"Total for this short: ${cs['total_usd']:.2f}", 44, (60, 207, 142)),
              ("Plan JSON is schema-validated · render only runs after Approve", 30, (154, 161, 178)),
              ("Roadmap v2: dubbing, lip-sync, multiple shorts per video", 30, (154, 161, 178))]
    card(work / "c3.png", lines)
    still(work / "c3.png", 7, work / "p8.mp4"); parts.append("p8.mp4")

    (work / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    out = dd / "musecut_demo.mp4"
    ins, fc = [], ""
    for i, part in enumerate(parts):
        ins += ["-i", str(work / part)]
        fc += f"[{i}:v]scale=1920:1080,setsar=1,fps=30,settb=AVTB,setpts=PTS-STARTPTS[v{i}];[{i}:a]asetpts=PTS-STARTPTS[a{i}];"
    fc += "".join(f"[v{i}][a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[v][a]"
    ff(*ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *ENC, "-movflags", "+faststart",
       str(out))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
