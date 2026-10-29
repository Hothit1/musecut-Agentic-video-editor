"""Drive the real UI end-to-end in Chrome and record a guided walkthrough (Playwright).

Usage: python scripts/record_demo.py samples/citino_15min.mp4 out_dir
Writes out_dir/screen.webm and out_dir/marks.json (event times, used to speed up waits).
On-screen narration + a spotlight outline are injected into the page while recording.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
PROMPT = ("Make a 60s vertical short about why World War II \"turning points\" are a myth, "
          "punchy, hook in first 3 seconds")

HELPERS = """
window.__say = (t) => {
  let b = document.getElementById('__say');
  if (!b) { b = document.createElement('div'); b.id = '__say';
    b.style.cssText = 'position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:99;' +
      'background:#4f8cffee;color:#fff;font:700 21px -apple-system,sans-serif;padding:10px 22px;' +
      'border-radius:14px;box-shadow:0 8px 30px #0008;max-width:90%;text-align:center';
    document.body.appendChild(b); }
  b.textContent = t; b.style.display = t ? 'block' : 'none';
};
window.__spot = (sel) => {
  document.querySelectorAll('.__spot').forEach(e => { e.classList.remove('__spot'); e.style.outline = ''; e.style.boxShadow = ''; });
  if (!sel) return;
  const e = typeof sel === 'string' ? document.querySelector(sel) : sel;
  if (!e) return;
  e.classList.add('__spot'); e.style.outline = '3px solid #ffd400'; e.style.boxShadow = '0 0 0 9999px #0006';
  e.scrollIntoView({behavior: 'smooth', block: 'center'});
};
"""


def main(video, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    marks, t0 = [], time.time()
    mark = lambda name: marks.append((name, round(time.time() - t0, 2)))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1536, "height": 864}, device_scale_factor=1.25,
                            record_video_dir=str(out), record_video_size={"width": 1920, "height": 1080})
        ctx.add_init_script(HELPERS)
        pg = ctx.new_page()
        pg.on("dialog", lambda d: d.accept())
        say = lambda t: pg.evaluate("t => window.__say(t)", t)
        spot = lambda s: pg.evaluate("s => window.__spot(s)", s)
        w = pg.wait_for_timeout

        # 1 upload
        pg.goto(BASE + "/app/")
        say("Upload a long video (15-min lecture) + say what short you want")
        w(1500)
        pg.set_input_files("#file", video)
        pg.fill("#prompt", "")
        pg.type("#prompt", PROMPT, delay=15)
        w(1200)
        mark("click_create")
        pg.click("#go")
        say("Plan job: Muse Voice Transcribe → Muse Spark 1.3 → SAM 3.1 → Muse Image")
        spot("#steps")
        pg.wait_for_url("**/plan-editor.html*", timeout=20 * 60 * 1000)
        mark("editor_open")
        pg.wait_for_selector(".seg", timeout=60000)
        w(1500)

        # 2 tour of the plan
        say("The Edit Plan is a draft — nothing is rendered until you approve it")
        w(3000)
        spot(".preview .card")
        say("Preview: SAM 3.1 face boxes → smoothed 9:16 crop (yellow box), face always inside")
        pg.click("#playAll")
        w(9000)
        say("Spark wrote the title, on-screen hook, post copy and hashtags — all editable")
        spot(".grid2 .card")
        w(4500)
        pg.fill("#hook", "One battle didn't win WWII")
        pg.press("#hook", "Tab")
        w(2500)
        say("Muse Image: 3 thumbnails from the best face frame — pick one")
        spot(".grid2 .card:nth-child(2)")
        w(2500)
        pg.locator(".thumbs label").nth(1).click()
        w(2000)
        pg.locator(".thumbs label").nth(2).click()
        w(2000)
        say("Spark also chapters the whole source video — click to jump")
        spot("#chapters")
        w(3000)
        pg.locator("#chapters li").nth(2).click()
        w(2500)
        pg.click("#stop")

        segs = pg.locator(".seg")
        n = segs.count()
        for i in range(n):
            el = segs.nth(i)
            sid = el.get_attribute("data-id")
            role = el.locator(".pill").first.inner_text().lower()
            kind = el.locator(".pill").nth(1).inner_text()
            note = el.locator(".note").inner_text() if el.locator(".note").count() else ""
            if i == 0:
                msg = "Each segment: role, timing, score and Spark's reason for keeping it"
            elif "MUSE IMAGE" in kind:
                msg = "B-roll slot: Muse Image still + Ken Burns, voice keeps playing underneath"
            elif "SAME VIDEO" in kind:
                msg = "Speaker is a small webcam tile here → full-frame slide as same-video B-roll"
            elif note:
                msg = f"Dropped automatically to hit 60 s — {note.replace('⚙', '').strip()}"
            else:
                msg = f"{role.title()} segment · {kind.title()}"
            say(msg)
            spot(f".seg[data-id={sid}]")
            w(3800 if i < 4 or note else 2600)
            if i == 0:
                cap = el.locator("textarea").first
                say("Edit caption text — timestamps stay locked")
                cap.fill("Was there ONE turning point in World War II? No.")
                cap.blur()
                w(3000)
                say("Preview one segment")
                el.locator(".play").click()
                w(5000)
                pg.click("#stop")

        say("Switch to 90 s → plan re-adds the strongest dropped segments")
        spot(".durbar")
        pg.select_option("#target", "90")
        w(4500)
        say("Back to 60 s → weakest segments are trimmed again (you review every change)")
        pg.select_option("#target", "60")
        w(4500)
        say("Music: royalty-free track, auto-ducked under the voice")
        spot(".preview .card:nth-child(2)")
        pg.fill("#musicVol", "0.2")
        pg.dispatch_event("#musicVol", "change")
        w(3000)
        spot(None)

        # 3 approve + render
        say("Approve locks the plan — only an approved plan can render")
        spot("#approve")
        w(2500)
        mark("approve")
        pg.click("#approve")
        say("Rendering with FFmpeg: cuts, pause trimming, 9:16 reframe, captions, ducked music")
        pg.wait_for_selector("#result video", timeout=20 * 60 * 1000)
        mark("render_done")
        spot("#result")
        say("Export: MP4 + SRT + thumbnail PNG + plan JSON + cost log + post copy")
        w(3500)
        pg.evaluate("document.querySelector('#result video').play()")
        w(6000)
        say("Every Meta model call is logged with its cost — this whole short cost about $0.55")
        spot("#result table")
        w(6000)
        say("Plan is now locked: further edits are rejected")
        spot("#segs")
        w(3000)
        say("")
        spot(None)
        mark("end")
        vpath = pg.video.path()
        url = pg.url
        ctx.close()
        b.close()
    Path(vpath).rename(out / "screen.webm")
    (out / "marks.json").write_text(json.dumps({"marks": marks, "editor_url": url}, indent=1))
    print(json.dumps(marks))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
