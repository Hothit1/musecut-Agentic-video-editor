"""Explainer clips for the demo video, built from a real project's data (no mock visuals).

Usage: python scripts/make_explainers.py <project_id> <out_dir>
Writes e1.mp4 ... e7.mp4 (1920x1080, 30 fps, silent stereo track).
"""
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import facetrack  # noqa: E402

W, H, FPS = 1920, 1080, 30
BG = (14, 15, 19)
PANEL = (23, 25, 32)
LINE = (44, 48, 59)
WHITE, MUTED = (238, 240, 245), (154, 161, 178)
BLUE, YELLOW, GREEN, PURPLE, RED = (79, 140, 255), (255, 212, 0), (60, 207, 142), (201, 167, 255), (255, 80, 90)
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
R = "/System/Library/Fonts/Supplemental/Arial.ttf"
M = "/System/Library/Fonts/Menlo.ttc"


def F(size, path=B):
    return ImageFont.truetype(path, size)


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def canvas(title, step):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((80, 56), f"HOW IT WORKS · {step}", font=F(26), fill=BLUE)
    d.text((80, 96), title, font=F(54), fill=WHITE)
    return im, d


def wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def para(d, xy, text, font, fill, maxw, gap=8):
    x, y = xy
    for ln in wrap(d, text, font, maxw):
        d.text((x, y), ln, font=font, fill=fill)
        y += font.size + gap
    return y


def bullets(d, xy, items, maxw, size=30, fill=WHITE, dot=BLUE):
    x, y = xy
    f = F(size, R)
    for it in items:
        d.ellipse((x, y + size * 0.35, x + 12, y + size * 0.35 + 12), fill=dot)
        y = para(d, (x + 28, y), it, f, fill, maxw - 28) + 12
    return y


def fit(img, box):
    w, h = box
    r = min(w / img.width, h / img.height)
    return img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)


def still(im, dur, out):
    png = out.with_suffix(".png")
    im.save(png)
    ff("-loop", "1", "-t", f"{dur}", "-i", str(png), "-f", "lavfi", "-t", f"{dur}", "-i",
       "anullsrc=r=48000:cl=stereo", "-vf", f"fps={FPS},format=yuv420p", "-shortest", *ENC, str(out))


def grab(video, t):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


# ---------------------------------------------------------------- 1 pipeline
def e1_pipeline(out):
    im, d = canvas("Four Meta models, each doing what it is best at", "1/7")
    rows = [
        ("Muse Voice Transcribe", "muse-voice-transcribe-1.0", PURPLE,
         "Speech → text with turn timestamps. We cut audio at silences into 2–15 s phrases, so every phrase gets its own start/end.",
         "Accurate English ASR, cheap ($0.18 / hour), 256 parallel streams."),
        ("Muse Spark 1.3", "muse-spark-1.3", BLUE,
         "Reads the whole transcript + your prompt and writes the Edit Plan as schema-checked JSON: hook → main → closing, reasons, scores, title, hook, post copy, chapters.",
         "Reasoning model with 1M context and structured JSON output."),
        ("SAM 3.1", "sam-3.1", YELLOW,
         "Video mode: tracks the speaker's face through each segment in one request (prompt \"face\", stable object ids). Boxes drive the 9:16 reframe.",
         "Segment Anything with concepts: no face-model training, works on any video."),
        ("Muse Image", "muse-image-1.0", GREEN,
         "Edits the best face frame into 3 thumbnail styles, and generates illustrative B-roll stills.",
         "Image model that reasons before drawing: keeps identity, spells the title."),
    ]
    y = 200
    for name, mid, col, does, why in rows:
        d.rounded_rectangle((80, y, W - 80, y + 182), radius=18, fill=PANEL, outline=LINE, width=2)
        d.rectangle((80, y, 92, y + 182), fill=col)
        d.text((120, y + 22), name, font=F(36), fill=col)
        d.text((120, y + 68), mid, font=F(22, M), fill=MUTED)
        para(d, (560, y + 22), does, F(27, R), WHITE, W - 700)
        para(d, (560, y + 118), "Why: " + why, F(24, R), MUTED, W - 700)
        y += 200
    still(im, 13, out)


# ---------------------------------------------------------------- 2 transcript
def e2_transcript(pid, out):
    units = json.loads((ROOT / "data" / pid / "work" / "transcript.json").read_text())
    im, d = canvas("Step 1 · Muse Voice Transcribe → timed phrases", "2/7")
    bullets(d, (80, 200), [
        "FFmpeg finds silences; audio is cut into short phrases (2–15 s).",
        "Each phrase is sent to /v1/asr/transcribe in parallel (8 at a time).",
        f"This 15-min lecture → {len(units)} phrases in about a minute, ≈ $0.05.",
        "The model returns turn times, not per-word times — word highlight timing in captions is estimated, and we say so.",
    ], 760, size=28)
    d.rounded_rectangle((900, 190, W - 80, H - 80), radius=18, fill=PANEL, outline=LINE, width=2)
    d.text((930, 210), "transcript.json (real output)", font=F(22), fill=MUTED)
    y = 256
    for u in units[:14]:
        d.text((930, y), f"{u['id']}  {u['start']:6.1f}–{u['end']:6.1f}s", font=F(21, M), fill=PURPLE)
        txt = u["text"] if len(u["text"]) < 58 else u["text"][:56] + "…"
        d.text((1240, y), txt, font=F(21, R), fill=WHITE)
        y += 52
    still(im, 10, out)


# ---------------------------------------------------------------- 3 spark
def e3_spark(pid, out):
    plan = json.loads((ROOT / "data" / pid / "plan.json").read_text())
    s = plan["segments"][0]
    im, d = canvas("Step 2 · Muse Spark 1.3 writes the Edit Plan", "3/7")
    bullets(d, (80, 200), [
        "Input: every timed phrase + your prompt + target length (60 / 90 s).",
        "Spark picks phrase IDs (u012..u015) — it never types timestamps, so it can't invent them. The server maps IDs to exact times.",
        "Story order: a hook for the first 3 s, then main points, then a closing.",
        "Every segment gets a role, a one-sentence reason and a 1–5 score; weak ones are dropped to fit the length.",
        "Output must pass a JSON Schema + overlap/hook checks. If not, errors go back to Spark (max 3 tries), then the job fails clearly — no guessing.",
    ], 820, size=27)
    seg = {k: s[k] for k in ("id", "role", "start_sec", "end_sec", "keep", "score", "reason", "shot_type")}
    seg["unit_ids"] = s["unit_ids"][:3] + (["…"] if len(s["unit_ids"]) > 3 else [])
    js = json.dumps({"title": plan["title"], "hook_text": plan["hook_text"], "segments": [seg]}, indent=2,
                    ensure_ascii=False)
    d.rounded_rectangle((960, 190, W - 80, H - 80), radius=18, fill=PANEL, outline=LINE, width=2)
    d.text((990, 210), "plan.json (real output, excerpt)", font=F(22), fill=MUTED)
    y = 252
    for ln in js.splitlines()[:24]:
        ind = len(ln) - len(ln.lstrip())
        for k, part in enumerate(wrap(d, ln.strip(), F(20, M), W - 1110 - ind * 12) or [""]):
            d.text((990 + (ind + 2 * (k > 0)) * 12, y), part, font=F(20, M),
                   fill=BLUE if '":' in ln else WHITE)
            y += 28
    still(im, 12, out)


# ---------------------------------------------------------------- 4 tracking (animated)
def e4_tracking(pid, out, work):
    d0 = ROOT / "data" / pid
    tracks = json.loads((d0 / "tracks.json").read_text())
    plan = json.loads((d0 / "plan.json").read_text())
    src = d0 / "source.mp4"
    seg = max((s for s in plan["segments"] if tracks[s["id"]]["layout"] == "face"),
              key=lambda s: max((x["box"][0] for x in tracks[s["id"]]["samples"] if x["box"]), default=0)
              - min((x["box"][0] for x in tracks[s["id"]]["samples"] if x["box"]), default=0))
    tr = tracks[seg["id"]]
    dur = min(8.0, seg["end_sec"] - seg["start_sec"])
    fdir = work / "trk"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    ff("-ss", f"{seg['start_sec']:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vf", "fps=15",
       str(fdir / "s%04d.png"))
    frames = sorted(fdir.glob("s*.png"))
    samples = [x for x in tr["samples"] if x["box"]]

    def box_at(t):
        prev = None
        for x in samples:
            if x["t"] > t:
                if prev is None:
                    return x["box"]
                a = (t - prev["t"]) / max(x["t"] - prev["t"], 1e-6)
                return [p + (q - p) * a for p, q in zip(prev["box"], x["box"])]
            prev = x
        return prev["box"] if prev else None

    base, dd = canvas("Step 3 · SAM 3.1 face tracking → smooth 9:16 reframe", "4/7")
    bullets(dd, (80, 190), [
        "SAM 3.1 video mode: each segment goes in as ONE 5 fps clip with the prompt \"face\"; stable object ids follow the speaker (red box) — 8 requests instead of ~180, ~5× cheaper.",
        "Crop centre follows the face with EMA smoothing (α 0.35) and a pan-speed cap (160 px/s) — no jitter, no jumps.",
        "Hard rule: the face box must stay fully inside the crop (yellow). If not, the crop is pushed; if impossible, the segment falls back to full frame.",
        "Speaker tiny (Zoom tile on a slide)? → the segment becomes same-video B-roll instead of a bad crop.",
    ], 1760, size=25)
    for i, fp in enumerate(frames):
        t = i / 15
        fr = Image.open(fp).convert("RGB")
        sw, sh = fr.size
        im = base.copy()
        d = ImageDraw.Draw(im)
        scale = 1180 / sw
        big = fr.resize((1180, int(sh * scale)))
        bd = ImageDraw.Draw(big)
        x = facetrack.crop_x_at(tr, t)
        cw = tr["crop_w"]
        bd.rectangle((0, 0, x * scale, big.height), fill=None)
        shade = Image.new("RGBA", big.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        sd.rectangle((0, 0, x * scale, big.height), fill=(0, 0, 0, 140))
        sd.rectangle(((x + cw) * scale, 0, big.width, big.height), fill=(0, 0, 0, 140))
        big = Image.alpha_composite(big.convert("RGBA"), shade).convert("RGB")
        bd = ImageDraw.Draw(big)
        b = box_at(t)
        if b:
            bd.rectangle([v * scale for v in b], outline=RED, width=4)
            bd.text((b[0] * scale, b[1] * scale - 30), "SAM 3.1: face", font=F(22), fill=RED)
        bd.rectangle((x * scale, 2, (x + cw) * scale, big.height - 2), outline=YELLOW, width=5)
        oy = 470
        im.paste(big, (80, oy))
        crop = fr.crop((int(x), 0, int(x) + cw, sh)).resize((340, 604))
        im.paste(crop, (1500, oy - 70 + 0))
        d.rectangle((1500, oy - 70, 1840, oy + 534), outline=YELLOW, width=4)
        d.text((1500, oy - 112), "9:16 result", font=F(26), fill=YELLOW)
        d.text((80, oy + big.height + 14),
               f"{seg['id']} · t = {t:4.1f}s · crop x = {int(x)} px", font=F(24, M), fill=MUTED)
        im.save(fdir / f"o{i:04d}.png")
    ff("-framerate", "15", "-i", str(fdir / "o%04d.png"), "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
       "anullsrc=r=48000:cl=stereo", "-vf", f"fps={FPS},format=yuv420p", "-shortest", *ENC, str(out))


# ---------------------------------------------------------------- 5 a-roll / b-roll
def e5_rolls(pid, out):
    d0 = ROOT / "data" / pid
    plan = json.loads((d0 / "plan.json").read_text())
    tracks = json.loads((d0 / "tracks.json").read_text())
    src = d0 / "source.mp4"
    im, d = canvas("Step 4 · A-roll and B-roll", "5/7")
    face = next(s for s in plan["segments"] if s["layout"] == "face")
    slide = next((s for s in plan["segments"] if s["b_roll_source"] == "same_video"), None)
    gen = next((s for s in plan["segments"] if s.get("b_roll_image")), None)
    tr = tracks[face["id"]]
    t = face["start_sec"] + 2
    fr = grab(src, t)
    x = facetrack.crop_x_at(tr, 2.0)
    a_img = fr.crop((int(x), 0, int(x) + tr["crop_w"], fr.height)).resize((360, 640))
    panels = [("A-ROLL", "Speaker on camera",
               "Voice + face kept. Reframed to 9:16 by the SAM 3.1 crop path.", a_img, BLUE)]
    if slide:
        s_fr = grab(src, slide["start_sec"] + 2)
        bg = fit(s_fr, (2000, 640)).filter(ImageFilter.GaussianBlur(14))
        cv = Image.new("RGB", (360, 640))
        cv.paste(bg.crop(((bg.width - 360) // 2, 0, (bg.width - 360) // 2 + 360, 640)), (0, 0))
        fg = fit(s_fr, (360, 640))
        cv.paste(fg, (0, (640 - fg.height) // 2))
        panels.append(("B-ROLL · SAME VIDEO", "Slides / screen from the source",
                       "Speaker is only a tiny webcam tile → show the full frame on a blurred fill.", cv, PURPLE))
    if gen:
        g = Image.open(gen["b_roll_image"]).convert("RGB").resize((360, 640))
        panels.append(("B-ROLL · MUSE IMAGE", "Generated illustration",
                       f"Spark proposes the slot + description; Muse Image draws it; slow Ken Burns zoom. "
                       f"Prompt: \"{(gen['b_roll_description'] or '')[:70]}…\"", g, GREEN))
    n = len(panels)
    colw = (W - 160) // n
    for i, (tag, sub, desc, img, col) in enumerate(panels):
        x0 = 80 + i * colw + (colw - 360) // 2
        d.text((80 + i * colw + 20, 190), tag, font=F(30), fill=col)
        d.text((80 + i * colw + 20, 232), sub, font=F(24, R), fill=MUTED)
        im.paste(img, (x0, 280))
        d.rectangle((x0, 280, x0 + 360, 920), outline=col, width=4)
        para(d, (80 + i * colw + 20, 940), desc, F(22, R), WHITE, colw - 40, gap=4)
    still(im, 12, out)


# ---------------------------------------------------------------- 6 thumbnails
def e6_thumbs(pid, out):
    d0 = ROOT / "data" / pid
    plan = json.loads((d0 / "plan.json").read_text())
    im, d = canvas("Step 5 · Thumbnails with Muse Image — you pick 1 of 3", "6/7")
    best = Image.open(d0 / "thumbs" / "best_frame.jpg").convert("RGB")
    bf = fit(best, (520, 300))
    im.paste(bf, (80, 280))
    d.text((80, 230), "Best frame (auto-picked)", font=F(26), fill=MUTED)
    bullets(d, (80, 620), [
        "SAM 3.1 boxes score every sampled frame: big, confident face.",
        "Muse Image edits that frame 3 ways (bold / documentary / clean), keeping the speaker's identity and the exact title.",
        "All 3 are saved; the one you pick is exported as a 1080×1920 PNG.",
    ], 560, size=24)
    d.text((620, 380), "→", font=F(90), fill=BLUE)
    x = 740
    for t in plan["thumbnail_options"]:
        th = Image.open(t["image_path"]).convert("RGB").resize((340, 604))
        im.paste(th, (x, 250))
        sel = t["id"] == plan["thumbnail_selected"]
        d.rectangle((x - 4, 246, x + 344, 858), outline=YELLOW if sel else LINE, width=6 if sel else 2)
        d.text((x, 872), f"{t['style']}{'  (selected)' if sel else ''}", font=F(26),
               fill=YELLOW if sel else MUTED)
        x += 380
    still(im, 10, out)


# ---------------------------------------------------------------- 7 captions + music + render
def e7_finish(pid, out):
    d0 = ROOT / "data" / pid
    im, d = canvas("Step 6 · Captions, music, render — only after Approve", "7/7")
    fr = grab(d0 / "final.mp4", 1.2).resize((360, 640))
    im.paste(fr, (W - 80 - 360, 200))
    d.rectangle((W - 80 - 360, 200, W - 80, 840), outline=BLUE, width=4)
    d.text((W - 80 - 360, 856), "frame from the final short", font=F(22), fill=MUTED)
    bullets(d, (80, 200), [
        "Captions: 3 words at a time, current word highlighted, placed under the chin using the SAM face box. Fillers (uh, um, stutters) removed. Text editable, timing locked.",
        "Hook text from Spark on screen for the first 3 seconds.",
        "Pauses longer than 0.25 s are cut — voice is never sped up.",
        "Music: royalty-free track generated in-repo (CC0). FFmpeg sidechain compression ducks it under the voice automatically; on/off + volume in the editor.",
        "Approve locks the plan (API returns 409 on later edits). Render runs as a background job: queued → processing → done / failed.",
        "Export: MP4 1080×1920, SRT, thumbnail PNG, plan JSON, cost log, post copy.",
    ], 1280, size=27)
    still(im, 13, out)


def main(pid, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    e1_pipeline(out / "e1.mp4")
    e2_transcript(pid, out / "e2.mp4")
    e3_spark(pid, out / "e3.mp4")
    e4_tracking(pid, out / "e4.mp4", out)
    e5_rolls(pid, out / "e5.mp4")
    e6_thumbs(pid, out / "e6.mp4")
    e7_finish(pid, out / "e7.mp4")
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
