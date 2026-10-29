"""Drive the real UI end-to-end in Chrome and record the screen (Playwright).

Usage: python scripts/record_demo.py samples/citino_15min.mp4 out_dir
Writes out_dir/screen.webm and out_dir/marks.json (event times, used to speed up waits).
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
PROMPT = ("Make a 60s vertical short about why World War II \"turning points\" are a myth, "
          "punchy, hook in first 3 seconds")


def main(video, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    marks, t0 = [], time.time()
    mark = lambda name: marks.append((name, round(time.time() - t0, 2)))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            record_video_dir=str(out), record_video_size={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/app/")
        pg.wait_for_timeout(1500)
        pg.set_input_files("#file", video)
        pg.fill("#prompt", "")
        pg.type("#prompt", PROMPT, delay=12)
        pg.wait_for_timeout(800)
        mark("click_create")
        pg.click("#go")
        pg.wait_for_url("**/plan-editor.html*", timeout=20 * 60 * 1000)
        mark("editor_open")
        pg.wait_for_selector(".seg", timeout=60000)
        pg.wait_for_timeout(2500)
        pg.click(".seg[data-id=seg_01] .play")
        pg.wait_for_timeout(6000)
        pg.click("#stop")
        # edit: caption text, drop a segment, pick a thumbnail, lower music, title
        cap = pg.locator(".seg[data-id=seg_01] textarea").first
        cap.scroll_into_view_if_needed()
        cap.fill("Was there ONE turning point in World War II? No.")
        cap.blur()
        pg.wait_for_timeout(1500)
        kept = pg.locator(".seg:not(.off) .keep")
        if kept.count() > 3:
            box = pg.locator(".seg:not(.off)").nth(2)
            box.scroll_into_view_if_needed()
            box.locator(".keep").click()
            pg.wait_for_timeout(1800)
        pg.mouse.wheel(0, -3000)
        pg.wait_for_timeout(800)
        pg.locator(".thumbs label").nth(1).click()
        pg.wait_for_timeout(1500)
        pg.fill("#musicVol", "0.2")
        pg.dispatch_event("#musicVol", "change")
        pg.wait_for_timeout(1500)
        pg.click("#playAll")
        mark("play_all")
        pg.wait_for_timeout(12000)
        pg.click("#stop")
        mark("approve")
        pg.click("#approve")
        pg.wait_for_selector("#result video", timeout=20 * 60 * 1000)
        mark("render_done")
        pg.locator("#result").scroll_into_view_if_needed()
        pg.wait_for_timeout(1500)
        pg.evaluate("document.querySelector('#result video').play()")
        pg.wait_for_timeout(8000)
        pg.mouse.wheel(0, 600)
        pg.wait_for_timeout(4000)
        mark("end")
        url = pg.url
        vpath = pg.video.path()
        ctx.close()
        b.close()
    Path(vpath).rename(out / "screen.webm")
    (out / "marks.json").write_text(json.dumps({"marks": marks, "editor_url": url}, indent=1))
    print(json.dumps(marks))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
