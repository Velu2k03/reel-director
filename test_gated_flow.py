"""Gated-flow tests: MOCKED, deterministic, no external services.

Every test here runs without META_API_KEY and without touching the
network: Spark/Wonder/Image/SAM/voice/ffmpeg are stubbed with
unittest.mock. Lines that say MOCKED PASS are this mocked suite;
live proof comes separately (live script-stop run, live approved
end-to-end run, test_meta/test_sam/test_render live).

Covers the gated-flow brief:
  - /api/script runs draft + Wonder revise + Spark visuals +
    director critique/revision, then STOPS in waiting_approval
    with plan, visuals, director decisions, unmet warnings, and
    estimated duration. No images, no voice, no assembly, no
    video file.
  - /api/video is the approval gate: approved lines + approved
    visuals required (clear 400 otherwise, never a silent
    re-draft); production uses them exactly with no text-model
    call after approval.
  - Visuals are distinct Spark descriptions, never narration
    copies (reject + one same-model retry, then fail loud).
  - Duration pre-flight: >30% over an explicit target holds
    production until duration_ack or a fixed target.
  - Jesus-run fixes: proper-name singularizer, topic-excluded
    repetition-visual, narration/visual revised labels, grouped
    (deduped) director findings, solo-audience group-language
    flag + prompt guard, audience preserved as given.
  - Codex P2 stubs: fake image/chat accept aspect_ratio/api_key.
  - Voice preflight: the local MMS voice loads before the first
    paid image call; a voice-load failure raises with zero image
    calls, success is recorded in decisions.

Run: python test_gated_flow.py
"""

import json
import os
import sys
import tempfile
import time
from unittest import mock

import app
import director
import meta_backend
import meta_sam
import render

FAKE_KEY = "gated-flow-fake-key-for-mocked-tests-only"


def _brief(topic="How rivers carve valleys",
           audience="middle-school science students", target=30.0):
    return director.make_brief(topic, audience=audience,
                               target_duration_s=target)


def _script_mocks(lines, visuals=None, critique="[]",
                  echo_revise=False):
    """Stubs for the whole script stage (draft/Wonder/visuals/Spark
    critique+revise). Returns the patchers' namespace dict."""
    visuals = visuals or ["river valley at dawn with a heron landing",
                          "close-up water polishing stones at noon"]
    state = {"visual_calls": []}

    def fake_visual(topic, audience, narration, model=None,
                    api_key=None):
        state["visual_calls"].append(narration)
        idx = len(state["visual_calls"]) - 1
        return "VISUAL: " + visuals[idx % len(visuals)]

    def echo_revise_fn(scene, reasons, brief, api_key=None):
        return (scene["narration"], scene["visual_prompt"])

    return state, (
        mock.patch.object(app, "draft_script",
                          return_value="\n".join(lines)),
        mock.patch.object(app, "revise_with_note",
                          return_value=("\n".join(lines), "mocked note")),
        mock.patch.object(app, "describe_visual",
                          side_effect=fake_visual),
        mock.patch.object(app, "_spark_critique_raw",
                          return_value=critique),
        mock.patch.object(app, "_spark_revise_scene",
                          side_effect=echo_revise_fn if echo_revise
                          else None,
                          return_value=None if echo_revise
                          else (lines[0], visuals[0])),
    )


def _wait_for(client, job_id, deadline_s=20):
    deadline = time.time() + deadline_s
    job = None
    while time.time() < deadline:
        job = client.get("/api/job/" + job_id).get_json()
        if job["status"] in ("done", "error", "cancelled",
                             "approved", "waiting_approval"):
            return job
        time.sleep(0.2)
    return client.get("/api/job/" + job_id).get_json()


def check_script_stage_stops_at_approval():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    lines = ["Rivers cut valleys over thousands of years of flow.",
             "Valley walls keep rocky layers that students can read."]
    visuals = ["wide river valley at dawn with a heron landing",
               "close-up water polishing stones under noon light"]
    state, patches = _script_mocks(lines, visuals,
                                     critique="[]", echo_revise=True)
    tmpv = tempfile.mkdtemp(prefix="gated-script-")
    try:
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                mock.patch.object(app, "VIDEOS_DIR", tmpv), \
                mock.patch.object(render, "run_pipeline",
                                  side_effect=AssertionError(
                                      "media ran in script stage")), \
                mock.patch("meta_image.generate_image",
                           side_effect=AssertionError(
                               "image ran in script stage")):
            resp = client.post("/api/script", json={
                "topic": "How rivers carve valleys",
                "audience": "middle-school science students",
                "scene_count": 2})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            data = resp.get_json()
            assert data["state"] == "waiting_approval", data
            assert data["script"] == lines, data
            assert data["visuals"] == visuals, data
            assert len(data["scenes"]) == 2, data
            assert "director" in data and "attempts" in data["director"], \
                data
            assert "unmet_criteria" in data, data
            assert "editorial_pass" in data, data
            assert data["duration_estimate"]["estimated_total_s"] > 0, \
                data
            assert data["director"].get("grouped_attempts") is not None, \
                data
            job_id = data["job_id"]
            job = client.get("/api/job/" + job_id).get_json()
            assert job["status"] == "waiting_approval", job
            assert job["stages"]["script"] == "done", job
            assert job["stages"]["revise"] == "done", job
            assert job["stages"]["scenes"] == "waiting", job
            assert job["video"] is None and job["qc"] is None, job
            # Stop proof: the job folder holds only job.json --
            # no images, no voice, no video file.
            names = sorted(os.listdir(os.path.join(tmpv, job_id)))
            assert names == ["job.json"], names
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: script stage stops at waiting_approval with "
          "plan, visuals, director log, and duration; no media files")


def check_video_requires_approval():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        resp = client.post("/api/video", json={"topic": "Rivers"})
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert "approved" in resp.get_json()["error"].lower(), \
            resp.get_data(as_text=True)
        resp = client.post("/api/video", json={
            "topic": "Rivers", "script": ["Alpha.", "Beta."]})
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert "visual" in resp.get_json()["error"].lower(), \
            resp.get_data(as_text=True)
        resp = client.post("/api/video", json={
            "topic": "Rivers", "script": ["Alpha.", "Beta."],
            "visuals": ["only one visual"]})
        assert resp.status_code == 400, resp.get_data(as_text=True)
        resp = client.post("/api/video", json={
            "topic": "Rivers",
            "scenes": [{"narration": "Alpha."}]})
        assert resp.status_code == 400, resp.get_data(as_text=True)
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: produce without approved lines+visuals fails "
          "400, never silently re-drafts")


def _fake_pipeline_factory(captured):
    def fake_pipeline(scenes, topic, out_dir, **kwargs):
        captured["scenes"] = [dict(s) for s in scenes]
        captured["topic"] = topic
        for name in ("scenes", "sam", "voice", "assemble", "qc"):
            kwargs.get("on_stage")(name, "done")
        final = os.path.join(out_dir, "final_mocked.mp4")
        with open(final, "wb") as f:
            f.write(b"fake-video-bytes")
        return final, {"passed": True, "mocked": True}
    return fake_pipeline


def check_production_uses_approved_exactly_no_retext():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    lines = ["Rivers cut valleys over thousands of years of flow.",
             "Valley walls keep rocky layers that students can read."]
    visuals = ["wide river valley at dawn with a heron landing",
               "close-up water polishing stones under noon light"]
    captured = {}
    tmpv = tempfile.mkdtemp(prefix="gated-prod-")
    try:
        with mock.patch.object(app, "VIDEOS_DIR", tmpv), \
                mock.patch.object(render, "run_pipeline",
                                  side_effect=_fake_pipeline_factory(
                                      captured)), \
                mock.patch.object(meta_backend, "chat",
                                  side_effect=AssertionError(
                                      "text call after approval")):
            resp = client.post("/api/video", json={
                "topic": "How rivers carve valleys",
                "script": lines, "visuals": visuals,
                "aspect_ratio": "9:16"})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job = _wait_for(client, resp.get_json()["job_id"])
            assert job["status"] == "done", job
            # Approved lines used exactly: rendered narration and
            # visuals match the approval, word for word.
            assert [s["narration"] for s in captured["scenes"]] == \
                lines, captured
            assert [s["visual_prompt"] for s in captured["scenes"]] == \
                visuals, captured
            assert [s["narration"] for s in job["scenes"]] == lines, job
            assert [s["visual_prompt"] for s in job["scenes"]] == \
                visuals, job
            assert job["stages"]["script"] == "done", job
            assert job["stages"]["revise"] == "done", job
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: production uses approved lines+visuals "
          "exactly, zero text-model calls after approval")


def check_visual_narration_copy_rejected():
    brief = _brief()
    narration = "Rivers cut valleys over thousands of years."
    calls = []

    def echo_visual(topic, audience, text, model=None, api_key=None):
        calls.append(text)
        return "VISUAL: " + text

    with mock.patch.object(app, "describe_visual",
                           side_effect=echo_visual):
        try:
            app._spark_visual_for_scene(narration, brief,
                                       api_key=FAKE_KEY)
        except RuntimeError as exc:
            assert "narration as the visual" in str(exc), str(exc)
        else:
            raise AssertionError(
                "narration-as-visual was silently accepted")
        assert len(calls) == 2, calls  # one retry, same model

    distinct = ["wide river valley at dawn with a heron landing"]
    with mock.patch.object(app, "describe_visual",
                           side_effect=[narration,
                                        "VISUAL: " + distinct[0]]):
        got = app._spark_visual_for_scene("Rivers cut valleys.",
                                          brief, api_key=FAKE_KEY)
        assert got == distinct[0], got
    assert app._visual_is_narration_copy(narration, narration) is True
    assert app._visual_is_narration_copy(
        "flat vector illustration, " + narration, narration) is True
    assert app._visual_is_narration_copy(distinct[0], narration) \
        is False
    print("MOCKED PASS: narration-as-visual rejected, retried once "
          "same model, then loud failure; distinct visuals accepted")


def check_duration_preflight_gate():
    # Unit level: >30% over warns with numbers + fix; fits pass.
    warn = app.duration_preflight(34.0, 10.0)
    assert warn and warn["over_target"] is True, warn
    assert warn["estimated_total_s"] == 34.0, warn
    assert warn["target_s"] == 10.0, warn
    assert "fewer scenes" in warn["suggestion"], warn
    assert app.duration_preflight(12.9, 10.0) is None
    assert app.duration_preflight(34.0, None) is None
    # Endpoint level: an explicit 10s target against ~34s of words
    # holds production until duration_ack.
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    lines = ["Rivers cut valleys over thousands of years of steady "
             "flow across the rocky desert plain each spring season.",
             "Valley walls keep rocky layers that young students can "
             "read like pages of an ancient open history book today.",
             "Gravity pulls every loose stone downhill toward the "
             "wide river delta over many centuries of slow change.",
             "Sediment settles softly on the valley floor and builds "
             "rich soil where desert cottonwood trees can grow tall.",
             "The river keeps carving while students watch and learn "
             "how patient water shapes the whole living land around."]
    visuals = ["visual object %d" % i for i in range(5)]
    estimated, _basis = app.estimate_plan_duration(lines)
    assert estimated > 13.0, estimated
    captured = {}
    tmpv = tempfile.mkdtemp(prefix="gated-dur-")
    try:
        with mock.patch.object(app, "VIDEOS_DIR", tmpv), \
                mock.patch.object(render, "run_pipeline",
                                  side_effect=_fake_pipeline_factory(
                                      captured)):
            resp = client.post("/api/video", json={
                "topic": "Rivers", "script": lines,
                "visuals": visuals, "target_duration_s": 10})
            assert resp.status_code == 409, resp.get_data(as_text=True)
            data = resp.get_json()
            assert data["needs_duration_ack"] is True, data
            assert data["estimated_total_s"] == estimated, data
            assert data["target_s"] == 10.0, data
            assert "suggestion" in data, data
            # No job started: nothing renders before approval.
            assert data.get("job_id") is None, data
            resp = client.post("/api/video", json={
                "topic": "Rivers", "script": lines,
                "visuals": visuals, "target_duration_s": 10,
                "duration_ack": True})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job = _wait_for(client, resp.get_json()["job_id"])
            assert job["status"] == "done", job
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: duration pre-flight holds over-target "
          "production until ack, with estimate, target, and fix")


def check_proper_name_singularizer():
    cases = {"jesus": "jesus", "james": "james", "moses": "moses",
             "robot": "robot", "robots": "robot",
             "leaf": "leaf", "leaves": "leaf"}
    for word, want in cases.items():
        assert meta_sam._singular(word) == want, (word,)
        assert director._singular(word) == want, (word,)
    for name, want in (("Jesus", "jesus"), ("James", "james"),
                       ("Moses", "moses")):
        got = meta_sam.concept_for_subject(
            "%s teaches a crowd on a hillside" % name)
        assert got == want, (name, got)
        derived = director.derive_sam_concept(
            {"id": "scene-1",
             "visual_prompt": "%s beside a fishing boat" % name})
        assert derived["concept"] == want, (name, derived)
    # Fail-closed stems: even a hostile input can never return
    # these fragments from the singularizer.
    assert meta_sam._singular("jesus") != "jesu"
    assert meta_sam._singular("james") != "jame"
    assert meta_sam._singular("moses") != "mose"
    print("MOCKED PASS: proper names never singularized (Jesus, "
          "James, Moses); robots->robot, leaves->leaf kept")


def check_repetition_visual_excludes_topic():
    brief = director.make_brief("Jesus", audience="solo learners",
                                target_duration_s=60.0)
    scenes = [
        {"id": "scene-1",
         "narration": "Jesus teaches a crowd on a grassy hillside.",
         "visual_prompt": "Jesus speaking beside a fishing boat on "
                          "the shore at dawn",
         "intended_duration_s": 20.0, "revision_status": "planned"},
        {"id": "scene-2",
         "narration": "Jesus shares bread with his friends at supper.",
         "visual_prompt": "Jesus breaking bread at a wooden table "
                          "by lamplight",
         "intended_duration_s": 20.0, "revision_status": "planned"},
        {"id": "scene-3",
         "narration": "Jesus walks a dusty road toward the village.",
         "visual_prompt": "Jesus walking a dusty road under a wide "
                          "desert sky",
         "intended_duration_s": 20.0, "revision_status": "planned"},
    ]
    findings, _estimates, _basis = director.critique(brief, scenes)
    visual_flags = [f for f in findings
                    if f["criterion"] == "repetition-visual"]
    assert visual_flags == [], findings
    # A genuinely shared object still flags.
    scenes[2]["visual_prompt"] = ("Jesus breaking bread at a wooden "
                                  "table in the morning")
    findings, _e2, _b2 = director.critique(brief, scenes)
    visual_flags = [f for f in findings
                    if f["criterion"] == "repetition-visual"]
    assert {f["scene_id"] for f in visual_flags} == \
        {"scene-2", "scene-3"}, findings
    print("MOCKED PASS: shared topic Jesus never flags "
          "repetition-visual; a shared object still does")


def _flag_once_then_pass():
    state = {"calls": 0}

    def critique(brief_arg, scenes, measured_wps=None):
        if state["calls"] == 0:
            state["calls"] += 1
            return ([{"scene_id": "scene-1", "criterion": "hook",
                     "detail": "stub"}], {}, "stub")
        return [], {}, "stub"

    return critique


def check_revised_labelling():
    brief = _brief()

    plan = director.build_plan(brief, ["Rivers carve deep valleys."])
    out, log = director.revise_loop(
        brief, [dict(s) for s in plan],
        critique_fn=_flag_once_then_pass(),
        revise_scene_fn=lambda scene, reasons, b: (
            "Rivers carve deep valleys for students.",
            scene["visual_prompt"]),
        max_cycles=2)
    assert log["attempts"][0]["status"] == "narration-revised", log
    assert out[0]["revision_status"] == "narration-revised", out

    plan = director.build_plan(brief, ["Rivers carve deep valleys."])
    out, log = director.revise_loop(
        brief, [dict(s) for s in plan],
        critique_fn=_flag_once_then_pass(),
        revise_scene_fn=lambda scene, reasons, b: (
            scene["narration"], "river canyon close-up at dawn"),
        max_cycles=2)
    assert log["attempts"][0]["status"] == "visual-revised", log
    assert out[0]["revision_status"] == "visual-revised", out

    plan = director.build_plan(brief, ["Rivers carve deep valleys."])
    out, log = director.revise_loop(
        brief, [dict(s) for s in plan],
        critique_fn=_flag_once_then_pass(),
        revise_scene_fn=lambda scene, reasons, b: (
            "Rivers carve deep valleys for students.",
            "river canyon close-up at dawn"),
        max_cycles=2)
    assert log["attempts"][0]["status"] == "revised", log
    assert out[0]["revision_status"] == "revised", out

    plan = director.build_plan(brief, ["Rivers carve deep valleys."])
    _out, log = director.revise_loop(
        brief, [dict(s) for s in plan],
        critique_fn=_flag_once_then_pass(),
        revise_scene_fn=lambda scene, reasons, b: (
            scene["narration"], scene["visual_prompt"]),
        max_cycles=1)
    assert log["attempts"][0]["status"] == "unchanged", log
    print("MOCKED PASS: narration-revised / visual-revised / "
          "revised / unchanged labelled by what actually changed")


def check_grouped_findings_deduped():
    attempts = [
        {"cycle": 1, "scene_id": "scene-1", "criteria": ["hook"],
         "before_narration": "A.", "before_visual": "VA",
         "after_narration": "B.", "after_visual": "VB",
         "status": "narration-revised"},
        {"cycle": 2, "scene_id": "scene-1", "criteria": ["hook"],
         "before_narration": "B.", "before_visual": "VB",
         "after_narration": "C.", "after_visual": "VB",
         "status": "narration-revised"},
        {"cycle": 2, "scene_id": "scene-2",
         "criteria": ["relevance"],
         "before_narration": "X.", "before_visual": "VX",
         "after_narration": "Y.", "after_visual": "VY",
         "status": "revised"},
    ]
    grouped = director.grouped_attempts(attempts)
    assert len(grouped) == 2, grouped
    hook = [g for g in grouped if g["criterion"] == "hook"][0]
    assert hook["cycles"] == [1, 2], hook
    assert hook["before_narration"] == "A.", hook
    assert hook["after_narration"] == "C.", hook
    assert hook["status"] == "narration-revised", hook
    print("MOCKED PASS: director findings grouped by scene and "
          "criterion across cycles, not stacked per cycle")


def check_solo_group_language_flagged_and_forbidden():
    brief = director.make_brief("Jesus", audience="solo learners",
                                target_duration_s=60.0)
    scenes = [{"id": "scene-1",
               "narration": "Stay with us as we walk together "
                            "through this lesson about Jesus.",
               "visual_prompt": "Jesus walking a dusty road",
               "intended_duration_s": 20.0,
               "revision_status": "planned"}]
    findings, _e, _b = director.critique(brief, scenes)
    rel = [f for f in findings if f["criterion"] == "relevance"]
    assert len(rel) == 1 and rel[0]["scene_id"] == "scene-1", findings
    assert "together" in rel[0]["detail"], findings
    # A non-solo audience is not flagged for the same line.
    group_brief = director.make_brief(
        "Jesus", audience="a small study group",
        target_duration_s=60.0)
    findings, _e2, _b2 = director.critique(group_brief, scenes)
    assert not [f for f in findings
                if f["criterion"] == "relevance"
                and "group language" in f["detail"]], findings
    # The revision prompt forbids group language for solo
    # audiences and keeps the audience as given.
    seen = []

    def fake_chat(prompt, model=None, max_tokens=512, api_key=None):
        seen.append(prompt)
        return ("NARRATION: Jesus walks a dusty road alone.\n"
                "VISUAL: Jesus walking a dusty road at dawn")

    with mock.patch.object(meta_backend, "chat",
                           side_effect=fake_chat):
        meta_backend.revise_scene(
            "Jesus", "solo learners", 60.0, scenes[0]["narration"],
            scenes[0]["visual_prompt"],
            [{"criterion": "relevance", "detail": "group language"}])
    assert seen, "no prompt sent"
    assert "together" in seen[0] and "solo" in seen[0].lower(), seen
    assert "solo learners" in seen[0], seen
    # The brief audience is never rewritten by planning.
    assert brief.audience == "solo learners", brief
    print("MOCKED PASS: solo group language flagged by relevance, "
          "forbidden in the revision prompt, audience kept as given")


def check_edit_before_produce_is_approved():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    lines = ["Rivers cut valleys over thousands of years of flow.",
             "Valley walls keep rocky layers that students can read."]
    visuals = ["wide river valley at dawn with a heron landing",
               "close-up water polishing stones under noon light"]
    state, patches = _script_mocks(lines, visuals, echo_revise=True)
    captured = {}
    tmpv = tempfile.mkdtemp(prefix="gated-edit-")
    try:
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                mock.patch.object(app, "VIDEOS_DIR", tmpv), \
                mock.patch.object(render, "run_pipeline",
                                  side_effect=_fake_pipeline_factory(
                                      captured)):
            script_id = client.post("/api/script", json={
                "topic": "How rivers carve valleys",
                "scene_count": 2}).get_json()["job_id"]
            # An edit made before pressing Produce is the approved
            # version: clean update, still waiting for approval.
            resp = client.patch(
                "/api/job/%s/scene/0" % script_id,
                json={"narration": "Edited rivers line for students.",
                      "visual_prompt": "edited canyon visual at dusk"})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert resp.get_json()["plan"].startswith("updated"), \
                resp.get_data(as_text=True)
            job = client.get("/api/job/" + script_id).get_json()
            assert job["status"] == "waiting_approval", job
            assert job["script"][0] == \
                "Edited rivers line for students.", job
            resp = client.post("/api/video", json={
                "topic": "How rivers carve valleys",
                "script": job["script"], "visuals": job["visuals"],
                "script_job_id": script_id})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            prod = _wait_for(client, resp.get_json()["job_id"])
            assert prod["status"] == "done", prod
            assert captured["scenes"][0]["narration"] == \
                "Edited rivers line for students.", captured
            assert captured["scenes"][0]["visual_prompt"] == \
                "edited canyon visual at dusk", captured
            script_job = client.get("/api/job/" + script_id).get_json()
            assert script_job["status"] == "approved", script_job
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: pre-produce edits are the approved version; "
          "produce consumes them exactly and marks the plan approved")


def check_codex_p2_stub_signatures():
    import inspect
    # fake_image must accept aspect_ratio (threaded by
    # render.generate_images into meta_image.generate_image).
    seen = {}

    def fake_image(prompt, out_path, api_key, aspect_ratio=None,
                   **kwargs):
        seen["aspect_ratio"] = aspect_ratio
        seen["api_key"] = api_key
        with open(out_path, "wb") as f:
            f.write(b"fake-png-bytes")
        return out_path

    tmp = tempfile.mkdtemp(prefix="gated-p2-img-")
    with mock.patch.object(render.meta_image, "generate_image",
                           side_effect=fake_image):
        render.generate_images(
            [{"narration": "Scene line about photosynthesis."}],
            tmp, FAKE_KEY, aspect_ratio="9:16")
    assert seen["aspect_ratio"] == "9:16", seen
    assert seen["api_key"] == FAKE_KEY, seen

    # fake_chat must accept api_key (threaded by every
    # meta_backend text call).
    chat_seen = {}

    def fake_chat(prompt, model=None, max_tokens=512, api_key=None):
        chat_seen["api_key"] = api_key
        chat_seen["model"] = model
        return "VISUAL: a distinct visual object at dawn"

    with mock.patch.object(meta_backend, "chat",
                           side_effect=fake_chat):
        out = meta_backend.describe_visual(
            "Rivers", "students", "Rivers carve valleys.",
            api_key=FAKE_KEY)
    assert "VISUAL:" in out, out
    assert chat_seen["api_key"] == FAKE_KEY, chat_seen
    assert chat_seen["model"] == meta_backend.SPARK_MODEL, chat_seen
    params = list(inspect.signature(
        meta_backend.describe_visual).parameters)
    assert "api_key" in params, params
    print("MOCKED PASS: Codex P2 stubs accept aspect_ratio/api_key "
          "with no TypeError, keys threaded end to end")


def check_voice_preflight_before_paid_images():
    tmp = tempfile.mkdtemp(prefix="gated-preflight-")
    image_calls = []

    def fake_image(prompt, out_path, api_key, aspect_ratio=None,
                   **kwargs):
        image_calls.append(prompt)
        with open(out_path, "wb") as f:
            f.write(b"fake-png-bytes")
        return out_path

    scene = {"id": "scene-1",
             "narration": "Rivers cut valleys over thousands of years.",
             "visual": "wide river valley at dawn",
             "visual_prompt": "wide river valley at dawn",
             "subject": "river valley"}
    # Failure: a voice-load failure raises before generate_image
    # is ever called.
    with mock.patch.object(render, "_load_voice",
                           side_effect=RuntimeError(
                               "mocked voice load failure")), \
            mock.patch.object(render.meta_image, "generate_image",
                              side_effect=fake_image):
        try:
            render.run_pipeline([scene], "Rivers", tmp,
                                api_key=FAKE_KEY)
        except RuntimeError as e:
            assert "mocked voice load failure" in str(e), e
        else:
            raise AssertionError(
                "voice-load failure did not raise before images")
    assert image_calls == [], image_calls
    # Success: the preflight is recorded as real stage detail in
    # the decisions threaded into QC.
    seen = {}

    def fake_qc(path, sam_results=None, backends=None,
                decisions=None, **kwargs):
        seen["decisions"] = list(decisions or [])
        return {"passed": True}

    with mock.patch.object(render, "_load_voice",
                           return_value=("tok", "model")), \
            mock.patch.object(render, "generate_images",
                              return_value=(["img0.png"], "meta")), \
            mock.patch.object(render, "sam_check",
                              return_value=[{"scene": 0}]), \
            mock.patch.object(render, "synthesize_voice",
                              return_value=["aud0.wav"]), \
            mock.patch.object(render, "media_duration",
                              return_value=1.0), \
            mock.patch.object(render, "assemble",
                              return_value=os.path.join(
                                  tmp, "final.mp4")), \
            mock.patch.object(render, "qc_report",
                              side_effect=fake_qc):
        render.run_pipeline([scene], "Rivers", tmp, api_key=FAKE_KEY)
    preflight = [d for d in seen.get("decisions", [])
                 if "voice preflight ok" in d.get("decision", "")]
    assert len(preflight) == 1, seen.get("decisions")
    assert render.VOICE_MODEL_ID in preflight[0]["decision"], preflight
    print("MOCKED PASS: voice-load failure raises before the first "
          "paid image call (generate_image x0); success records the "
          "voice preflight in decisions")


def main():
    check_script_stage_stops_at_approval()
    check_video_requires_approval()
    check_production_uses_approved_exactly_no_retext()
    check_visual_narration_copy_rejected()
    check_duration_preflight_gate()
    check_proper_name_singularizer()
    check_repetition_visual_excludes_topic()
    check_revised_labelling()
    check_grouped_findings_deduped()
    check_solo_group_language_flagged_and_forbidden()
    check_edit_before_produce_is_approved()
    check_codex_p2_stub_signatures()
    check_voice_preflight_before_paid_images()
    print("MOCKED ALL PASS: 13 gated-flow checks. Live evidence "
          "still required: script-stop run, approved end-to-end "
          "run, test_meta/test_sam/test_render live.")


if __name__ == "__main__":
    sys.exit(main())
