"""Thumbnails (3 options) and generated B-roll stills with Muse Image."""
import subprocess
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import meta_api
from .config import FONT_PATH, OUT_H, OUT_W

STYLES = [
    ("bold", "bold high-contrast YouTube Shorts thumbnail, dramatic rim light, deep shadows"),
    ("documentary", "cinematic documentary poster look, warm archival film grain, muted tones"),
    ("clean", "clean modern creator thumbnail, bright, saturated colors, crisp"),
]


def grab_frame(video, t):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v",
                        "1", "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                       capture_output=True, check=True)
    return p.stdout


def fit_9x16(img):
    """Center-crop/resize any image to OUT_W x OUT_H."""
    w, h = img.size
    tw = int(h * 9 / 16)
    if tw <= w:
        img = img.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h))
    else:
        th = int(w * 16 / 9)
        img = img.crop((0, (h - th) // 2, w, (h - th) // 2 + th))
    return img.resize((OUT_W, OUT_H), Image.LANCZOS)


def _font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default(size)


def fallback_thumbnail(frame_jpeg, title, face_box=None):
    """Pillow composition used only when Muse Image fails (logged as fallback)."""
    import io
    src = Image.open(io.BytesIO(frame_jpeg)).convert("RGB")
    bg = fit_9x16(src).filter(ImageFilter.GaussianBlur(18))
    fg = src.resize((OUT_W, int(src.height * OUT_W / src.width)))
    bg.paste(fg, (0, (OUT_H - fg.height) // 2))
    d = ImageDraw.Draw(bg)
    f = _font(120)
    y = 140
    for line in _wrap(title.upper(), f, OUT_W - 120, d):
        w = d.textlength(line, font=f)
        d.text(((OUT_W - w) / 2, y), line, font=f, fill="white", stroke_width=8, stroke_fill="black")
        y += 135
    return bg


def _wrap(text, font, maxw, d):
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


def make_thumbnails(pid, video, best_t, title, outdir, progress=lambda m: None):
    outdir.mkdir(parents=True, exist_ok=True)
    frame = grab_frame(video, best_t)
    (outdir / "best_frame.jpg").write_bytes(frame)
    progress("generating 3 thumbnails with Muse Image")

    def one(i_style):
        i, (name, style) = i_style
        prompt = (f"Turn this video frame into a vertical 9:16 thumbnail for a short video. Keep the "
                  f"speaker's face and identity exactly as in the photo, sharp, eyes open, centered. "
                  f"Place the large bold title text \"{title}\" BEHIND the speaker (text-behind-subject "
                  f"effect, the person overlaps the letters). Spell the title exactly. Style: {style}.")
        path = outdir / f"thumb_{i}.png"
        try:
            img = meta_api.image_edit(pid, prompt, frame, f"thumbnail_{name}")
            fit_9x16(img).save(path)
            src = "muse_image"
        except Exception:
            fallback_thumbnail(frame, title).save(path)
            src = "fallback_pillow"
        return {"id": f"thumb_{i}", "image_path": str(path), "title_text": title, "style": name,
                "source": src}

    with ThreadPoolExecutor(3) as ex:
        return list(ex.map(one, enumerate(STYLES, 1)))


def make_broll(pid, description, path):
    prompt = (f"Vertical 9:16 illustrative B-roll still for a history short video: {description}. "
              f"Photorealistic archival documentary style, no text, no captions, no watermarks.")
    img = meta_api.image_generate(pid, prompt, "b_roll_image")
    fit_9x16(img).save(path)
    return str(path)
