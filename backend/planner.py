"""Edit Plan with Muse Spark.

Spark only picks phrase-unit ids (it never writes timestamps); the server maps ids to
start/end from the transcript, validates, then fits the plan to the target duration.
"""
import json
import re

import jsonschema

from . import meta_api
from .config import SCHEMA_PATH

SCHEMA = json.loads(SCHEMA_PATH.read_text())

SPARK_SCHEMA = {
    "type": "object",
    "required": ["title", "hook_text", "post_copy", "hashtags", "chapters", "segments"],
    "properties": {
        "title": {"type": "string"},
        "hook_text": {"type": "string"},
        "post_copy": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "chapters": {"type": "array", "items": {
            "type": "object", "required": ["start_unit", "title"],
            "properties": {"start_unit": {"type": "string"}, "title": {"type": "string"}}}},
        "segments": {"type": "array", "minItems": 3, "items": {
            "type": "object",
            "required": ["start_unit", "end_unit", "role", "reason", "score"],
            "properties": {
                "start_unit": {"type": "string"}, "end_unit": {"type": "string"},
                "role": {"enum": ["hook", "main", "closing"]},
                "reason": {"type": "string"},
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "b_roll_description": {"type": ["string", "null"]}}}},
    },
}

SYSTEM = """You are MuseCut's short-form video editor. You turn a long talking-head transcript
into an edit plan for ONE vertical short (9:16). The transcript is split into phrase units
like [u012 61.2-68.9s] text. You select contiguous unit ranges; you never invent timestamps.

Rules:
- Story order: first segment role "hook" (a strong, self-contained line that grabs attention
  in the first 3 seconds), then "main" segments (problem -> key points), last "closing"
  (conclusion / takeaway / call to action). You MAY reorder segments relative to the source.
- The closing segment must END on a complete concluding sentence (an answer, verdict or
  takeaway). Never end the short on a setup line, a question left open, or a "but..." that
  continues in the next sentence of the source.
- For a 30 s target: hook + 1-2 main + closing, each segment 3-10 s.
- Cut filler, rambling, repetition, and anything off-topic for the user's prompt.
- Each segment is a contiguous range start_unit..end_unit (inclusive), ideally 4-15 s, and
  must read as complete thoughts (don't start or end mid-sentence when avoidable).
- Segments must not overlap. Propose candidates totalling about 1.5x the target duration;
  give every segment a score 1-5 (5 = essential) so the app can trim weaker ones.
- reason: one sentence on why this segment is kept.
- b_roll_description: for at most 2 short main segments where an illustration would help,
  describe a still image (no text, no real faces); otherwise null.
- title: <= 60 chars, punchy. hook_text: <= 8 words, on-screen hook for the first 3 s.
- post_copy: 1-3 sentence caption for the post. hashtags: 4-8 tags without '#'.
- chapters: 4-8 chapters covering the WHOLE source video (start_unit + short title).
Return JSON only."""


CLOSING_ERR = "closing must end on a complete sentence"


class PlanError(ValueError):
    pass


def _fmt_units(units):
    return "\n".join(f"[{u['id']} {u['start']:.1f}-{u['end']:.1f}s] {u['text']}" for u in units)


def check_spark(out, units):
    """Semantic checks on Spark output. Returns list of error strings."""
    idx = {u["id"]: i for i, u in enumerate(units)}
    errs, used = [], set()
    try:
        jsonschema.validate(out, SPARK_SCHEMA)
    except jsonschema.ValidationError as e:
        return [f"schema: {e.message} at {list(e.path)}"]
    for n, s in enumerate(out["segments"]):
        a, b = idx.get(s["start_unit"]), idx.get(s["end_unit"])
        if a is None or b is None:
            errs.append(f"segment {n}: unknown unit id {s['start_unit']}/{s['end_unit']}")
            continue
        if a > b:
            errs.append(f"segment {n}: start_unit after end_unit")
            continue
        rng = set(range(a, b + 1))
        if rng & used:
            errs.append(f"segment {n}: overlaps another segment")
        used |= rng
    if out["segments"] and out["segments"][0]["role"] != "hook":
        errs.append("first segment must have role 'hook'")
    closing = [x for x in out["segments"] if x["role"] == "closing"]
    if closing and closing[-1]["end_unit"] in idx:
        last = units[idx[closing[-1]["end_unit"]]]["text"].rstrip()
        if not re.search(r"[.!?][\"')\]]?$", last):
            errs.append(f"{CLOSING_ERR}: {closing[-1]['end_unit']} ends mid-sentence (\"...{last[-50:]}\"); "
                        f"end the closing on a unit that finishes a sentence")
    for c in out["chapters"]:
        if c["start_unit"] not in idx:
            errs.append(f"chapter '{c['title']}': unknown unit {c['start_unit']}")
    return errs


def ask_spark(pid, units, prompt, target):
    user = (f"User prompt: {prompt}\nTarget duration: {target} seconds\n\n"
            f"Transcript units:\n{_fmt_units(units)}")
    last_errs = []
    for attempt in range(3):
        msg = user
        if last_errs:
            msg += ("\n\nYour previous answer was rejected by the validator:\n- "
                    + "\n- ".join(last_errs[:10]) + "\nFix these and answer again.")
        try:
            out, _ = meta_api.spark_json(pid, "plan", SYSTEM, msg, SPARK_SCHEMA,
                                         f"{len(units)} units, target {target}s, attempt {attempt+1}")
        except ValueError as e:
            last_errs = [str(e)]
            continue
        last_errs = check_spark(out, units)
        if not last_errs:
            return out
        if attempt == 2 and all(e.startswith(CLOSING_ERR) for e in last_errs):
            return out  # soft rule: an imperfect ending must not fail the whole job
    raise PlanError("Spark plan failed validation: " + "; ".join(last_errs[:5]))


def trim_dangling_tail(u, max_frac=0.65):
    """Silence-cut phrases can carry the first words of the next sentence ("...not really. The
    steam engines"). Cut the unit back to its last sentence end, estimating the time from the
    share of characters; only when the dangling part is short. Returns a new unit dict."""
    text = u["text"].rstrip()
    m = None
    for m in re.finditer(r"[.!?](?=\s+\S)", text):
        pass
    if not m:
        return u
    keep, tail = text[:m.end()], text[m.end():].strip()
    if not tail or len(tail) > max_frac * len(text):
        return u
    end = u["start"] + (u["end"] - u["start"]) * len(keep) / len(text) + 0.15
    return {**u, "text": keep, "end": round(min(end, u["end"]), 3)}


def end_on_sentence(us):
    """The short's last words must finish a sentence: walk back to the last unit that contains a
    sentence end and cut there (whatever follows it is dropped)."""
    for k in range(len(us) - 1, -1, -1):
        text = us[k]["text"].rstrip()
        if re.search(r"[.!?][\"')\]]?$", text):
            return us[:k + 1]
        if re.search(r"[.!?](?=\s+\S)", text):
            return us[:k] + [trim_dangling_tail(us[k], max_frac=1.0)]
    return us


def build_plan(pid, prompt, target, units, spark):
    idx = {u["id"]: i for i, u in enumerate(units)}
    segments, captions = [], []
    for n, s in enumerate(spark["segments"], 1):
        us = units[idx[s["start_unit"]]: idx[s["end_unit"]] + 1]
        us = us[:-1] + [trim_dangling_tail(us[-1])]  # never end a segment on a half sentence
        if s["role"] == "closing":
            us = end_on_sentence(us)
        sid = f"seg_{n:02d}"
        desc = (s.get("b_roll_description") or "").strip() or None
        segments.append({
            "id": sid, "role": s["role"], "start_sec": us[0]["start"], "end_sec": us[-1]["end"],
            "keep": True, "reason": s["reason"], "transcript": " ".join(u["text"] for u in us),
            "shot_type": "b_roll" if desc else "a_roll",
            "b_roll_source": "generated_image" if desc else None,
            "b_roll_description": desc, "b_roll_image": None, "score": s["score"],
            "unit_ids": [u["id"] for u in us], "layout": None, "auto_note": None,
        })
        for u in us:
            captions.append({"id": f"cap_{u['id']}", "segment_id": sid, "text": u["text"],
                             "start_sec": u["start"], "end_sec": u["end"]})
    plan = {
        "project_id": pid, "prompt": prompt, "target_duration_sec": target,
        "segments": segments, "captions": captions, "thumbnail_options": [],
        "thumbnail_selected": None,
        "music": {"enabled": True, "track_id": "royalty_free_01", "volume": 0.15},
        "title": spark["title"], "hook_text": spark["hook_text"], "post_copy": spark["post_copy"],
        "hashtags": [h.lstrip("#") for h in spark["hashtags"]],
        "chapters": [{"start_sec": units[idx[c["start_unit"]]]["start"], "title": c["title"]}
                     for c in spark["chapters"]],
        "status": "draft",
    }
    fit_duration(plan)
    return plan


def seg_duration(s, units_by_id=None):
    return s["end_sec"] - s["start_sec"]


def kept_duration(plan):
    return sum(seg_duration(s) for s in plan["segments"] if s["keep"])


def fit_duration(plan):
    """Drop the weakest kept segments until within target (+5%); re-add strong ones if short.
    Hook and closing are protected. Every auto change is noted for the user to review."""
    target = plan["target_duration_sec"]
    segs = plan["segments"]
    for s in segs:
        if s.get("auto_note"):
            s["auto_note"] = None
    while kept_duration(plan) > target * 1.05:
        cands = [s for s in segs if s["keep"] and s["role"] == "main"]
        if not cands:
            break
        weakest = min(cands, key=lambda s: (s["score"], -seg_duration(s)))
        weakest["keep"] = False
        weakest["auto_note"] = f"auto-removed to fit {target}s (score {weakest['score']})"
    for s in sorted([s for s in segs if not s["keep"]], key=lambda s: -s["score"]):
        if kept_duration(plan) + seg_duration(s) <= target * 1.05:
            s["keep"] = True
            s["auto_note"] = f"auto-added to fill {target}s"
    return plan


def validate(plan):
    """JSON-schema + semantic validation. Raises PlanError with a readable message."""
    try:
        jsonschema.validate(plan, SCHEMA)
    except jsonschema.ValidationError as e:
        raise PlanError(f"plan schema: {e.message} at {'/'.join(map(str, e.path))}")
    ids = [s["id"] for s in plan["segments"]]
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate segment ids")
    for s in plan["segments"]:
        if s["end_sec"] <= s["start_sec"]:
            raise PlanError(f"{s['id']}: end_sec must be after start_sec")
        if s["shot_type"] == "b_roll" and not s["b_roll_source"]:
            raise PlanError(f"{s['id']}: b_roll needs b_roll_source")
    if not any(s["keep"] for s in plan["segments"]):
        raise PlanError("at least one segment must be kept")
    opts = {t["id"] for t in plan["thumbnail_options"]}
    if plan["thumbnail_selected"] and plan["thumbnail_selected"] not in opts:
        raise PlanError("thumbnail_selected is not one of thumbnail_options")
    for c in plan["captions"]:
        if c["segment_id"] not in ids:
            raise PlanError(f"caption {c.get('id')} references unknown segment")
    return plan
