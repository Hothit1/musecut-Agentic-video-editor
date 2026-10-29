import copy

import pytest

from backend import captions, facetrack, planner, transcribe

UNITS = [{"id": f"u{i:03d}", "start": i * 5.0, "end": i * 5.0 + 4.5, "text": f"sentence {i} uh words"}
         for i in range(1, 31)]


def spark_ok():
    return {
        "title": "T", "hook_text": "H", "post_copy": "P", "hashtags": ["#ww2", "history"],
        "chapters": [{"start_unit": "u001", "title": "Intro"}],
        "segments": [
            {"start_unit": "u010", "end_unit": "u010", "role": "hook", "reason": "r", "score": 5},
            {"start_unit": "u001", "end_unit": "u004", "role": "main", "reason": "r", "score": 2},
            {"start_unit": "u005", "end_unit": "u008", "role": "main", "reason": "r", "score": 4},
            {"start_unit": "u012", "end_unit": "u015", "role": "main", "reason": "r", "score": 3,
             "b_roll_description": "a map"},
            {"start_unit": "u020", "end_unit": "u021", "role": "closing", "reason": "r", "score": 5},
        ],
    }


def test_spark_check_accepts_valid():
    assert planner.check_spark(spark_ok(), UNITS) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda o: o["segments"][1].update(end_unit="u010"), "overlaps"),
    (lambda o: o["segments"][0].update(role="main"), "hook"),
    (lambda o: o["segments"][2].update(start_unit="u999"), "unknown unit"),
    (lambda o: o["segments"][2].update(start_unit="u008", end_unit="u005"), "after"),
])
def test_spark_check_rejects(mutate, needle):
    o = spark_ok()
    mutate(o)
    errs = planner.check_spark(o, UNITS)
    assert any(needle in e for e in errs), errs


def test_build_plan_valid_and_fits_target():
    plan = planner.build_plan("p1", "prompt", 60, UNITS, spark_ok())
    planner.validate(plan)
    assert plan["hashtags"] == ["ww2", "history"]
    assert planner.kept_duration(plan) <= 63
    kept = [s for s in plan["segments"] if s["keep"]]
    assert kept[0]["role"] == "hook" and any(s["role"] == "closing" for s in kept)
    dropped = [s for s in plan["segments"] if not s["keep"]]
    assert all(s["auto_note"] for s in dropped)
    assert min(dropped, key=lambda s: s["score"])["score"] == 2  # weakest dropped first
    b = next(s for s in plan["segments"] if s["b_roll_description"])
    assert b["shot_type"] == "b_roll" and b["b_roll_source"] == "generated_image"


def test_switch_to_90_readds_segments():
    plan = planner.build_plan("p1", "prompt", 60, UNITS, spark_ok())
    d60 = planner.kept_duration(plan)
    plan["target_duration_sec"] = 90
    planner.fit_duration(plan)
    assert planner.kept_duration(plan) > d60


def test_validate_rejects_bad_plan():
    plan = planner.build_plan("p1", "prompt", 60, UNITS, spark_ok())
    bad = copy.deepcopy(plan)
    bad["segments"][0]["role"] = "intro"
    with pytest.raises(planner.PlanError):
        planner.validate(bad)
    bad = copy.deepcopy(plan)
    bad["thumbnail_selected"] = "thumb_9"
    with pytest.raises(planner.PlanError):
        planner.validate(bad)
    bad = copy.deepcopy(plan)
    bad["target_duration_sec"] = 45
    with pytest.raises(planner.PlanError):
        planner.validate(bad)


def test_crop_keeps_face_inside_and_moves_smoothly():
    W, H = 1280, 654
    cw = facetrack.crop_width(W, H)
    # face jumps from left to right edge, then disappears
    samples = [(i / 2, (100, 100, 260, 300, 0.9)) for i in range(6)]
    samples += [(3 + i / 2, (1000, 100, 1200, 300, 0.9)) for i in range(10)]
    samples += [(8 + i / 2, None) for i in range(4)]
    keys, viol = facetrack.smooth_crop(samples, W, H)
    assert viol == []
    for (t, x), (_, b) in zip(keys, samples):
        assert 0 <= x <= W - cw
        if b:
            assert b[0] >= x and b[2] <= x + cw, (t, x, b)


def test_crop_speed_limited_when_face_allows():
    W, H = 1280, 654
    # wide-ish face drifting slowly: crop should not jump more than the speed cap
    samples = [(i / 2, (500 + 10 * i, 100, 600 + 10 * i, 300, 0.9)) for i in range(20)]
    keys, _ = facetrack.smooth_crop(samples, W, H)
    steps = [abs(b[1] - a[1]) for a, b in zip(keys, keys[1:])]
    assert max(steps) <= facetrack.MAX_SPEED_PX_S / 2 + 1


def test_caption_words_drop_fillers_and_keep_span():
    ws = captions.word_times("Uh the the war, um, was huge", 10.0, 12.0)
    assert [w[0] for w in ws] == ["the", "war,", "was", "huge"]
    assert ws[0][1] == 10.0 and abs(ws[-1][2] - 12.0) < 1e-6


def test_chunker_respects_bounds():
    sil = [(3.0, 3.4), (4.0, 4.5), (20.0, 20.5), (40.0, 41.0)]
    chunks = transcribe.plan_chunks(50.0, sil)
    assert chunks[0][0] == 0 and chunks[-1][1] == 50.0
    for a, b in chunks:
        assert b - a <= transcribe.MAX_UNIT + 1e-6
