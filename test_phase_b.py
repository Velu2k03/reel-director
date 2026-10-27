"""Phase B focused tests: MOCKED, deterministic, no external services.

Every test here runs without META_API_KEY and without touching the
network: the only model calls in the director loop (Muse Spark
critique plus per-scene Spark revision) are injected stubs,
SAM/Image/ffmpeg runs are stubbed with unittest.mock where touched.
Lines that say MOCKED PASS are this mocked suite; lines that say
LIVE are the live integration scripts (test_meta.py, test_sam.py,
test_render.py) plus one live end-to-end job through the running
app, and are NOT replaced by anything here. Do not substitute a
MOCKED result for a live run.

Correction note (Oct 27): the loop first shipped with a rule-only
critique and a Wonder revision. The brief requires Muse Spark for
critique and revision, so the rule-based critique stays as a
checkable floor, a strictly validated Spark critique merges in on
top of it, and revision is wired to Spark. The check below pins
that wiring: the production critique path must call Spark, a bad
Spark response must be rejected (never silently rule-only), and
production revision must use Spark, never Wonder.

Covers Phase B scope only:
  - structured model-response validation (valid, missing narration,
    missing visual prompt, missing ID, duplicate ID)
  - brief inputs (topic, audience, target duration carried through)
  - bounded critique and revision (revises only flagged scenes,
    stops at the cap, surfaces unmet criteria instead of looping,
    never labelled editorially passed when criteria are unmet)
  - Spark critique + revision wiring (production path calls Spark,
    malformed Spark critique rejected, revision uses Spark)
  - SAM concept derivation (visual subject gives a real concept,
    narration stopwords never do, bad concepts raise before any
    image is regenerated)

Run: python test_phase_b.py
"""

import json
import os
import sys
import tempfile

import director
import render

FAKE_KEY = "phase-b-fake-key-for-mocked-tests-only"


def _brief():
    return director.make_brief(
        "How rivers carve valleys",
        audience="middle-school science students",
        target_duration_s=30.0)


def check_validation_valid():
    raw = [{"id": "scene-1", "narration": "Rivers cut valleys over time.",
            "visual_prompt": "wide river valley landscape"},
           {"id": "scene-2", "narration": "Water carries rock downstream.",
            "visual_prompt": "close-up water over rocks"}]
    scenes = director.validate_scene_plan(raw)
    assert [s["id"] for s in scenes] == ["scene-1", "scene-2"], scenes
    assert all(s["revision_status"] == "planned" for s in scenes)
    # Legacy "visual" key is accepted as the visual prompt.
    legacy = director.validate_scene_plan(
        [{"id": "s1", "narration": "N.", "visual": "V."}])
    assert legacy[0]["visual_prompt"] == "V.", legacy
    print("MOCKED PASS: valid scene plans validate with stable IDs")


def check_validation_missing_fields():
    cases = [
        ([{"id": "scene-1", "visual_prompt": "V."}], "narration"),
        ([{"id": "scene-1", "narration": "N."}], "visual prompt"),
        ([{"narration": "N.", "visual_prompt": "V."}], "ID"),
        ([{"id": "scene-1", "narration": "  ",
           "visual_prompt": "V."}], "narration"),
        ("not-a-list", "non-empty list"),
        ([], "non-empty list"),
        ([{"id": "scene-1", "narration": "N.", "visual_prompt": "V."},
         {"id": "scene-1", "narration": "M.", "visual_prompt": "W."}],
         "duplicate"),
    ]
    for raw, hint in cases:
        try:
            director.validate_scene_plan(raw)
        except director.ScenePlanError as e:
            assert hint.lower() in str(e).lower(), (hint, str(e))
        else:
            raise AssertionError(
                "broken plan accepted, nothing invented check failed: %r"
                % (raw,))
    print("MOCKED PASS: missing narration / visual prompt / ID rejected, "
          "nothing invented, broken plan never proceeds")


def check_brief_carried_through():
    brief = _brief()
    assert brief.topic == "How rivers carve valleys", brief
    assert brief.audience == "middle-school science students", brief
    assert brief.target_duration_s == 30.0, brief
    plan = director.build_plan(
        brief, ["Rivers cut valleys over thousands of years of flow.",
                "Middle-school scientists can trace each rocky layer."])
    assert len(plan) == 2, plan
    assert all(s["intended_duration_s"] == 15.0 for s in plan), plan
    assert plan[0]["id"] == "scene-1", plan
    # The critique sees the same brief: relevance judges audience
    # fit, duration judges the same target.
    findings, _estimates, _basis = director.critique(brief, plan)
    assert isinstance(findings, list), findings
    for bad in ("", "   "):
        try:
            director.make_brief(bad)
        except director.ScenePlanError:
            pass
        else:
            raise AssertionError("empty topic accepted")
    try:
        director.make_brief("T", target_duration_s=-3)
    except director.ScenePlanError:
        pass
    else:
        raise AssertionError("negative target accepted")
    print("MOCKED PASS: brief carries topic, audience, target duration "
          "through planning and critique")


def check_critique_criteria():
    brief = _brief()
    # Hook: weak opener on scene 1 flags hook.
    weak = director.build_plan(
        brief, ["In this video we will look at rivers and valleys today.",
                "Valleys reveal rocky layers that students can read."])
    findings, _estimates, _basis = director.critique(brief, weak)
    assert any(f["criterion"] == "hook" and f["scene_id"] == "scene-1"
               for f in findings), findings
    # Repetition: shared 4-gram wording flags both scenes.
    rep = director.build_plan(
        brief, ["Rivers carve deep valleys through solid rock over time.",
                "Rivers carve deep valleys through solid rock each year."])
    findings, _e2, _b2 = director.critique(brief, rep)
    assert any(f["criterion"] == "repetition-narration"
               for f in findings), findings
    # Duration: estimate basis is stated, never exact; a far target
    # flags duration on one scene only.
    tight = director.make_brief("How rivers carve valleys",
                                audience="middle-school students",
                                target_duration_s=5.0)
    findings, estimates, basis = director.critique(tight, rep)
    assert "estimate" in basis.lower(), basis
    assert "exact" not in basis.lower() or "not exact" in basis.lower(), basis
    dur = [f for f in findings if f["criterion"] == "duration"]
    assert len(dur) == 1, findings
    assert all(v > 0 for v in estimates.values()), estimates
    # Visual support: a visual sharing nothing with its narration.
    odd = [{"id": "scene-1",
            "narration": "Rivers carve valleys for young students.",
            "visual_prompt": "sleepy cat on a sofa cushion",
            "intended_duration_s": 15.0, "revision_status": "planned"}]
    findings, _e3, _b3 = director.critique(brief, odd)
    assert any(f["criterion"] == "visual-support" for f in findings), findings
    # Shared image boilerplate alone is NOT a repeated visual idea.
    same_prefix = director.build_plan(
        brief, ["Rivers carve deep valleys through solid rock over time.",
                "Gravity pulls young students downhill past rocky cliffs."])
    findings, _e5, _b5 = director.critique(brief, same_prefix)
    assert not any(f["criterion"] == "repetition-visual"
                   for f in findings), findings
    # Singular/plural match counts for relevance (river ~ rivers).
    assert not any(f["criterion"] == "relevance"
                   and f["scene_id"] == "scene-1"
                   for f in findings), findings
    # Relevance: a scene sharing no content word with topic/audience.
    off = [{"id": "scene-1",
            "narration": "Rivers carve valleys for young students.",
            "visual_prompt": "river valley landscape for students",
            "intended_duration_s": 15.0, "revision_status": "planned"},
           {"id": "scene-2",
            "narration": "Zebras juggle pineapples at midnight.",
            "visual_prompt": "zebra juggling pineapples at midnight",
            "intended_duration_s": 15.0, "revision_status": "planned"}]
    findings, _e4, _b4 = director.critique(brief, off)
    rel = [f for f in findings if f["criterion"] == "relevance"]
    assert len(rel) == 1 and rel[0]["scene_id"] == "scene-2", findings
    print("MOCKED PASS: critique flags hook, repetition, duration "
          "(stated estimate), visual-support, audience relevance")


def check_revises_only_flagged_scenes():
    brief = _brief()
    plan = director.build_plan(
        brief, ["Rivers carve deep valleys through solid rock over time.",
                "Rivers carve deep valleys through solid rock each year.",
                "Valley walls keep rocky layers that students can read."])
    calls = []

    def fake_critique(brief_arg, scenes, measured_wps=None):
        if fake_critique.calls == 0:
            fake_critique.calls += 1
            return ([{"scene_id": "scene-2",
                      "criterion": "repetition-narration",
                      "detail": "shared wording with scene-1"}],
                    {}, "stub basis")
        return [], {}, "stub basis"
    fake_critique.calls = 0

    def fake_revise(scene, reasons, brief_arg):
        calls.append(scene["id"])
        assert brief_arg.topic == brief.topic, brief_arg
        assert brief_arg.audience == brief.audience, brief_arg
        return ("Valley streams polish stones for young students.",
                "close-up stream polishing stones")

    out, log = director.revise_loop(
        brief, plan, critique_fn=fake_critique,
        revise_scene_fn=fake_revise, max_cycles=2)
    assert calls == ["scene-2"], calls
    # A model reply that changes nothing is recorded as unchanged,
    # never as a revision.
    same, same_log = director.revise_loop(
        brief, director.build_plan(
            brief, ["Rivers carve deep valleys through solid rock."]),
        critique_fn=lambda b, s, measured_wps=None: (
            [{"scene_id": "scene-1", "criterion": "hook",
              "detail": "stub"}], {}, "stub"),
        revise_scene_fn=lambda scene, reasons, b: (
            scene["narration"], scene["visual_prompt"]),
        max_cycles=1)
    assert same_log["attempts"][0]["status"] == "unchanged", same_log
    assert same_log["editorial_pass"] is False, same_log
    assert [d for d in director.director_decisions(same_log, {"scene-1": 0})
            if d.get("status") == "revised"] == [], same_log
    assert out[0]["narration"].startswith("Rivers carve"), out[0]
    assert out[2]["narration"].startswith("Valley walls"), out[2]
    assert out[1]["revision_status"] == "revised", out[1]
    assert log["editorial_pass"] is True, log
    assert log["unmet_criteria"] == [], log
    assert len(log["attempts"]) == 1, log
    attempt = log["attempts"][0]
    assert attempt["before_narration"] != attempt["after_narration"], attempt
    assert attempt["before_visual"] != attempt["after_visual"], attempt
    assert attempt["criteria"] == ["repetition-narration"], attempt
    print("MOCKED PASS: only flagged scenes revised, others untouched, "
          "reason recorded with before/after")


def check_cap_and_unmet_surfaced():
    brief = _brief()
    plan = director.build_plan(
        brief, ["Rivers carve deep valleys through solid rock over time.",
                "Valley walls keep rocky layers that students can read."])
    calls = []

    def always_flags(brief_arg, scenes, measured_wps=None):
        return ([{"scene_id": "scene-1",
                  "criterion": "repetition-narration",
                  "detail": "stubborn shared wording"}],
                {}, "stub basis")

    def fake_revise(scene, reasons, brief_arg):
        calls.append((scene["id"], reasons[0]["criterion"]))
        n = len(calls)
        return ("Rivers still carve valleys for young students, "
                "revision %d." % n,
                "river valley landscape for students, revision %d" % n)

    out, log = director.revise_loop(
        brief, plan, critique_fn=always_flags,
        revise_scene_fn=fake_revise, max_cycles=2)
    # Capped: exactly 2 attempts (one per cycle), then stop, no loop.
    assert len(calls) == 2, calls
    assert log["cycles_used"] == 2, log
    assert len(log["attempts"]) == 2, log
    assert log["editorial_pass"] is False, log
    assert len(log["unmet_criteria"]) == 1, log
    unmet = log["unmet_criteria"][0]
    assert unmet["scene_id"] == "scene-1", unmet
    assert unmet["criterion"] == "repetition-narration", unmet
    assert out[0]["revision_status"] == "unmet", out[0]
    # Director decisions carry before/after, criterion, and status.
    entries = director.director_decisions(
        log, {"scene-1": 0, "scene-2": 1})
    revised = [e for e in entries if e.get("status") == "revised"]
    surfaced = [e for e in entries if e.get("status") == "unmet"]
    assert len(revised) == 2 and len(surfaced) == 1, entries
    assert all(e["before_narration"] and e["after_narration"]
               for e in revised), entries
    # Forced unmet case must NOT be labelled editorially passed,
    # even at the QC layer.
    tmp = tempfile.mkdtemp(prefix="phase-b-qc-")
    missing = os.path.join(tmp, "gone.mp4")
    report = render.qc_report(
        missing, director_decisions=entries,
        unmet_criteria=log["unmet_criteria"], editorial_pass=False)
    assert report["editorial"]["editorial_pass"] is False, report
    assert report["editorial"]["unmet_criteria"] == \
        log["unmet_criteria"], report
    assert any(d.get("stage") == "director"
               for d in report["decisions"]), report
    # And a clean run IS labelled passed.
    clean = render.qc_report(
        missing, director_decisions=[], unmet_criteria=[],
        editorial_pass=True)
    assert clean["editorial"]["editorial_pass"] is True, clean
    print("MOCKED PASS: revision capped at 2 cycles, unmet criterion "
          "surfaced with scene, never labelled editorially passed")


def check_sam_concept_visual_first():
    # Visual subject gives a real concept even when the narration is
    # nothing but stopwords and generic verbs (the Phase A failure).
    scene = {"id": "scene-3",
             "narration": "in the thousand carried on slowly",
             "visual_prompt": "river valley landscape"}
    derived = director.derive_sam_concept(scene)
    assert derived["concept"] == "river", derived
    assert derived["visual_subject"] == "river valley landscape", derived
    assert derived["source"] == "visual_prompt", derived
    assert "carried" not in derived["concept"], derived
    assert "thousand" not in derived["concept"], derived
    # Narration stopwords never become the concept on their own.
    from meta_sam import concept_for_subject
    for filler in ("in", "carried", "thousand"):
        concept = concept_for_subject(
            "flat vector illustration, river valley %s" % filler)
        assert concept not in ("in", "carried", "thousand"), concept
    # Artistic descriptors are not segmentable subjects either: the
    # concept falls through to the visual noun.
    assert concept_for_subject(
        "detailed cross-section diagram of rivers carving valleys "
        "through mountains") == "river"
    assert concept_for_subject(
        "time-lapse animation of a river cutting a valley") == "river"
    # A bad concept raises BEFORE any image is regenerated: the
    # caller fixes the concept from the visual prompt first.
    for bad in ({"id": "scene-9", "narration": "in the thousand"},
                {"id": "scene-9", "narration": "N.", "visual_prompt": "  "},
                {"id": "scene-9", "narration": "N.",
                 "visual_prompt": "in the of to"}):
        try:
            director.derive_sam_concept(bad)
        except ValueError as e:
            assert "visual prompt" in str(e).lower(), e
        else:
            raise AssertionError(
                "bad concept accepted instead of raising: %r" % (bad,))
    print("MOCKED PASS: SAM concept is a visual subject (river), never "
          "narration stopwords; bad concepts raise before any regen")


def check_spark_critique_and_revision_wiring():
    import app as console_app
    import meta_backend
    brief = _brief()
    plan = director.build_plan(
        brief, ["Rivers carve deep valleys through solid rock over time.",
                "Valley walls keep rocky layers that students can read.",
                "Gravity pulls young students downhill past rocky cliffs."])

    # 1. The production critique path calls Spark (injected stub,
    # MOCKED): rule floor plus the validated Spark finding, merged.
    spark_calls = []

    def stub_spark_critique(brief_arg, scenes, measured_wps=None):
        spark_calls.append({"topic": brief_arg.topic,
                            "scenes": [s["id"] for s in scenes]})
        return json.dumps(
            [{"scene_id": "scene-2", "criterion": "relevance",
              "detail": "MOCKED Spark-only finding"}])

    merged, _estimates, _basis = director.combined_critique(
        brief, plan, spark_critique_fn=stub_spark_critique)
    assert spark_calls, "Spark critique was never called"
    spark_only = [f for f in merged if f.get("source") == "spark"]
    assert len(spark_only) == 1, merged
    assert spark_only[0]["scene_id"] == "scene-2", spark_only
    assert spark_only[0]["criterion"] in director.ALLOWED_CRITERIA
    assert spark_only[0]["detail"], spark_only
    assert any(f.get("source") == "rules" for f in merged), merged

    # 2. Malformed, missing, or unparseable Spark responses are
    # rejected with a clear error: never invented, never silently
    # rule-only.
    bad_responses = [
        "", "   ", None, 12345, "this is not json at all",
        '{"scene_id": "scene-1"}',
        json.dumps([{"scene_id": "scene-9", "criterion": "hook",
                     "detail": "unknown scene"}]),
        json.dumps([{"scene_id": "scene-1", "criterion": "vibes",
                     "detail": "outside the allowed criteria"}]),
        json.dumps([{"scene_id": "scene-1", "criterion": "hook"}]),
        json.dumps(["hook"]),
    ]
    for bad in bad_responses:
        try:
            director.parse_spark_critique_response(
                bad, ["scene-1", "scene-2", "scene-3"])
        except director.SparkCritiqueError as e:
            assert "spark" in str(e).lower(), str(e)
        else:
            raise AssertionError(
                "malformed Spark critique accepted: %r" % (bad,))
        try:
            director.combined_critique(
                brief, plan,
                spark_critique_fn=(lambda b, s, measured_wps=None, _b=bad:
                                   _b))
        except director.SparkCritiqueError:
            pass
        else:
            raise AssertionError(
                "combined critique fell back to rule-only for: %r"
                % (bad,))
    # No Spark function at all is also refused, never rule-only.
    try:
        director.combined_critique(brief, plan,
                                   spark_critique_fn=None)
    except director.ScenePlanError:
        pass
    else:
        raise AssertionError("combined critique ran rule-only with "
                             "no Spark function")

    # 3. Production revision uses Spark, never Wonder (MOCKED chat
    # stub records the model on both the critique and revision
    # calls inside the real app.py helpers).
    model_calls = []
    real_chat = meta_backend.chat

    def fake_chat(prompt, model=None, max_tokens=512, api_key=None):
        model_calls.append(model)
        if "JSON list" in prompt:
            return "[]"
        return ("NARRATION: Rivers carve valleys for young students.\n"
                "VISUAL: river valley carving close-up for students")

    meta_backend.chat = fake_chat
    try:
        raw = console_app._spark_critique_raw(brief, plan)
        assert raw == "[]", raw
        new_n, new_v = console_app._spark_revise_scene(
            dict(plan[0]),
            [{"scene_id": "scene-1", "criterion": "hook",
              "detail": "MOCKED weak hook"}],
            brief)
        assert new_n.startswith("Rivers carve valleys"), (new_n,)
        assert new_v, (new_v,)
    finally:
        meta_backend.chat = real_chat
    assert model_calls, "no model call was made"
    assert all(m == meta_backend.SPARK_MODEL for m in model_calls), \
        model_calls
    assert all(m != meta_backend.WONDER_MODEL for m in model_calls), \
        model_calls

    # 4. End to end at the director level with Spark stubs: a
    # stubborn Spark finding flows into the log as Spark evidence
    # and surfaces as unmet with its source after the cap.
    def loop_spark(brief_arg, scenes, measured_wps=None):
        return json.dumps(
            [{"scene_id": "scene-1", "criterion": "hook",
              "detail": "MOCKED stubborn Spark hook finding"}])

    def loop_critique(b, s, measured_wps=None):
        return director.combined_critique(
            b, s, spark_critique_fn=loop_spark,
            measured_wps=measured_wps)

    out, log = director.revise_loop(
        brief, [dict(s) for s in plan], critique_fn=loop_critique,
        revise_scene_fn=lambda scene, reasons, b: (
            "Rivers carve valleys for young students, revised.",
            "river valley carving close-up for students, revised"),
        max_cycles=2)
    assert log["cycles_used"] == 2, log
    assert log["editorial_pass"] is False, log
    assert any(a["scene_id"] == "scene-1" for a in log["attempts"]), log
    sources = [u.get("source") for u in log["unmet_criteria"]]
    assert "spark" in sources, log["unmet_criteria"]
    assert log["spark_critique_findings"], log
    assert log["critique_sources"]["spark"] >= 1, log
    print("MOCKED PASS: production critique calls Spark and merges "
          "validated Spark findings over the rule floor; malformed "
          "Spark critique is rejected, never silently rule-only; "
          "production revision uses Muse Spark, never Wonder")


def main():
    check_validation_valid()
    check_validation_missing_fields()
    check_brief_carried_through()
    check_critique_criteria()
    check_revises_only_flagged_scenes()
    check_cap_and_unmet_surfaced()
    check_spark_critique_and_revision_wiring()
    check_sam_concept_visual_first()
    print("MOCKED ALL PASS: 8 Phase B checks. Live evidence still "
          "required: test_meta.py, test_sam.py, test_render.py plus one "
          "live end-to-end job through the running app.")


if __name__ == "__main__":
    sys.exit(main())
