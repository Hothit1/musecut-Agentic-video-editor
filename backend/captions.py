"""Captions: word timing estimate, SRT export, and a transparent PNG caption track.

Muse Voice Transcribe gives phrase-level times; word times inside a phrase are estimated
proportionally to word length. Editing caption text keeps the phrase's start/end; the new
words are spread over the same span.
"""
import re

from PIL import Image, ImageDraw, ImageFont

from .config import FONT_PATH, OUT_H, OUT_W

FILLERS = {"uh", "um", "uhm", "erm", "er", "ah", "hmm"}
GROUP = 3
CAP_FPS = 10


def clean_words(text):
    words = []
    for w in text.split():
        core = re.sub(r"[^\w']", "", w).lower()
        if core in FILLERS or not core:
            continue
        if words and re.sub(r"[^\w']", "", words[-1]).lower() == core and len(core) > 1:
            continue  # stutter "the the"
        words.append(w)
    return words


def word_times(text, start, end):
    words = clean_words(text)
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    total, t, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append((w, t, t + d))
        t += d
    return out


def timeline_words(pieces):
    """pieces: [{src_start, src_end, out_start, captions:[{text,start_sec,end_sec}], cap_y}]
    -> [(word, t0, t1, cap_y)] on the output timeline."""
    out = []
    for p in pieces:
        for c in p["captions"]:
            for w, a, b in word_times(c["text"], c["start_sec"], c["end_sec"]):
                a2, b2 = max(a, p["src_start"]), min(b, p["src_end"])
                if b2 - a2 < 0.04:
                    continue
                off = p["out_start"] - p["src_start"]
                out.append((w, a2 + off, b2 + off, p["cap_y"]))
    return out


def groups(words):
    gs, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == GROUP or w[0].endswith((".", "?", "!", ",")):
            gs.append(cur)
            cur = []
    if cur:
        gs.append(cur)
    return gs


def _ts(t):
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def write_srt(words, path):
    lines = []
    for i, g in enumerate(groups(words), 1):
        lines += [str(i), f"{_ts(g[0][1])} --> {_ts(g[-1][2])}", " ".join(w[0] for w in g), ""]
    path.write_text("\n".join(lines))


def render_caption_frames(words, duration, outdir, hook_text=None):
    """Write CAP_FPS PNG frames (transparent) for the whole output; returns ffmpeg input pattern."""
    outdir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT_PATH, 92)
    hook_font = ImageFont.truetype(FONT_PATH, 104)
    gs = groups(words)
    blank = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    n = int(duration * CAP_FPS) + 1
    cache = {}
    gi = 0
    for f in range(n):
        t = f / CAP_FPS
        while gi < len(gs) and gs[gi][-1][2] < t:
            gi += 1
        g = gs[gi] if gi < len(gs) and gs[gi][0][1] <= t <= gs[gi][-1][2] + 0.15 else None
        active = None
        if g:
            for k, w in enumerate(g):
                if w[1] <= t < w[2]:
                    active = k
            if active is None:
                active = len(g) - 1 if t >= g[-1][1] else 0
        show_hook = hook_text and t < 3.0
        key = (id(g) if g else None, active, bool(show_hook))
        if key not in cache:
            img = blank.copy()
            d = ImageDraw.Draw(img)
            if show_hook:
                _draw_centered(d, hook_text.upper(), hook_font, 170, fill=(255, 255, 255),
                               box=(0, 0, 0, 170))
            if g:
                y = g[0][3]
                texts = [w[0].upper() for w in g]
                widths = [d.textlength(x + " ", font=font) for x in texts]
                x = (OUT_W - sum(widths)) / 2
                for k, (tx, wd) in enumerate(zip(texts, widths)):
                    col = (255, 221, 0) if k == active else (255, 255, 255)
                    d.text((x, y), tx, font=font, fill=col, stroke_width=7, stroke_fill="black")
                    x += wd
            path = outdir / f"u{len(cache):05d}.png"
            img.save(path)
            cache[key] = path
        link = outdir / f"f{f:06d}.png"
        if link.exists():
            link.unlink()
        link.symlink_to(cache[key].name)
    return str(outdir / "f%06d.png")


def _draw_centered(d, text, font, y, fill, box=None):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= OUT_W - 120:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=font)
        yy = y + i * 118
        if box:
            d.rounded_rectangle(((OUT_W - w) / 2 - 30, yy - 12, (OUT_W + w) / 2 + 30, yy + 116),
                                radius=24, fill=box)
        d.text(((OUT_W - w) / 2, yy), ln, font=font, fill=fill, stroke_width=6, stroke_fill="black")
