"""Phase D focused tests: MOCKED, deterministic, no external services.

Every test here runs without META_API_KEY and without touching the
network: Muse Spark/Wonder/Image, SAM, voice, and ffmpeg runs are
stubbed with unittest.mock. Lines that say MOCKED PASS are this
mocked suite; lines that say LIVE are the live integration runs
(test_render.py in both formats, plus live end-to-end jobs through
the workspace) and are NOT replaced by anything here. Do not
substitute a MOCKED result for a live run.

Covers Phase D scope only:
  - dynamic scene counts and asset mapping (2, 4, 6 scenes)
  - structured model-response validation (exact keys, no pass/reason
    shape, bounded same-model repair retries)
  - bounded critique and revision (revision cap, unmet surfaced)
  - failed-stage reporting (failed, never endlessly working)
  - job restoration (refresh restores progress; restart marks
    interrupted jobs with preserved work and a re-run path)
  - no captions: missing output still fails QC, with no caption
    failure mode anywhere (owner decision Oct 27)
  - aspect-ratio propagation (format drives dims and QC)
  - duplicate protection (second render rejected, edits during a
    run rejected)
  - cancellation (cooperative cancel marks cancelled; done jobs
    refuse cancel; failed jobs retry from the stored plan)
  - secrets safety (key redacted from errors, never on disk, never
    in frontend code)

Run: python test_phase_d.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

import app
import director
import render

FAKE_KEY = "phase-d-fake-key-for-mocked-tests-only"

GOOD_CRITIQUE = json.dumps([
    {"scene_id": "scene-1", "criterion": "hook",
     "detail": "MOCKED hook finding"},
])

# The shape a drifting model returns instead of the required keys
# (observed live): per-criterion pass/reason objects with no scene.
WRONG_SHAPE = json.dumps([
    {"criterion": "hook", "pass": True, "reason": "MOCKED looks fine"},
])


class _Brief:
    def __init__(self, topic="T", audience="A", target=30.0):
        self.topic = topic
        self.audience = audience
        self.target_duration_s = target


def _scenes(*ids):
    return [{"id": sid, "narration": "Narration for %s." % sid,
             "visual_prompt": "flat vector %s" % sid} for sid in ids]


def check_scene_counts_and_mapping():
    for n in (2, 4, 6):
        lines = ["Lesson line %d about rivers." % i for i in range(n)]
        records = app.build_scene_records(lines)
        assert len(records) == n, (n, len(records))
        for i, rec in enumerate(records):
            assert rec["index"] == i, rec
            assert rec["narration"] == lines[i], rec
            assert rec["provider"] == "muse-image-1.0", rec
            assert rec["status"] == "waiting" and not rec["dirty"], rec
    job_id = "mockeddjob123"
    lines = ["Alpha.", "Beta.", "Gamma.", "Delta."]
    with app.JOBS_LOCK:
        app.JOBS[job_id] = {"id": job_id, "topic": "t",
                            "status": "running", "stages": {},
                            "scenes": app.build_scene_records(lines),
                            "out_dir": "/tmp/nope-phase-d"}
    try:
        for i in range(4):
            app._scene_event(job_id, i, "image", {"ok": True})
            app._scene_event(job_id, i, "sam",
                             {"ok": True,
                              "result": {"matches": 1,
                                         "concept": "river",
                                         "visual_subject": "river"}})
            app._scene_event(job_id, i, "voice",
                             {"ok": True, "duration": 2.0 + i})
            app._scene_event(job_id, i, "assembled", {"ok": True})
        scenes = app.JOBS[job_id]["scenes"]
        for i, rec in enumerate(scenes):
            assert rec["image"] == "/videos/%s/scene_%d.png" % (job_id, i), rec
            assert rec["duration"] == 2.0 + i, rec
            assert rec["sam"]["matches"] == 1, rec
            assert rec["status"] == "done", rec
    finally:
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None)
        shutil.rmtree("/tmp/nope-phase-d", ignore_errors=True)
    print("MOCKED PASS: scene counts (2/4/6) and per-scene asset mapping")


def check_structured_response_validation():
    # Exact-shape responses validate; wrong shapes are rejected,
    # never patched up.
    findings = director.parse_spark_critique_response(
        GOOD_CRITIQUE, ["scene-1"])
    assert findings[0]["criterion"] == "hook", findings
    assert findings[0]["source"] == "spark", findings
    assert director.parse_spark_critique_response("[]", ["scene-1"]) == []
    for bad in (WRONG_SHAPE, "", "   ", None, "not json",
                json.dumps([{"scene_id": "scene-9",
                             "criterion": "hook", "detail": "x"}]),
                json.dumps([{"scene_id": "scene-1",
                             "criterion": "vibes", "detail": "x"}])):
        try:
            director.parse_spark_critique_response(bad, ["scene-1"])
        except director.SparkCritiqueError:
            pass
        else:
            raise AssertionError(
                "malformed Spark critique accepted: %r" % (bad,))
    # The production path retries the SAME model with a strict
    # re-ask: first call good means one call, no repair.
    brief = _Brief()
    with mock.patch.object(app, "critique_scenes",
                           return_value=GOOD_CRITIQUE) as first, \
         mock.patch.object(app, "repair_critique") as repair:
        raw = app._spark_critique_raw(brief, _scenes("scene-1"))
        assert json.loads(raw)[0]["scene_id"] == "scene-1", raw
        assert first.call_count == 1, first.call_count
        assert repair.call_count == 0, repair.call_count
    # Wrong shape, then fixed by the re-ask: two calls total.
    with mock.patch.object(app, "critique_scenes",
                           return_value=WRONG_SHAPE) as first, \
         mock.patch.object(app, "repair_critique",
                           return_value=GOOD_CRITIQUE) as repair:
        raw = app._spark_critique_raw(brief, _scenes("scene-1"))
        assert json.loads(raw)[0]["detail"] == "MOCKED hook finding", raw
        assert first.call_count == 1, first.call_count
        assert repair.call_count == 1, repair.call_count
    # Always wrong: exactly 3 attempts (1 + 2 re-asks), then the real
    # validation error, never a silent rule-only pass.
    with mock.patch.object(app, "critique_scenes",
                           return_value=WRONG_SHAPE) as first, \
         mock.patch.object(app, "repair_critique",
                           return_value=WRONG_SHAPE) as repair:
        try:
            app._spark_critique_raw(brief, _scenes("scene-1"))
        except director.SparkCritiqueError as e:
            assert "scene_id" in str(e), str(e)
        else:
            raise AssertionError("always-bad critique silently passed")
        assert first.call_count == 1, first.call_count
        assert repair.call_count == 2, repair.call_count
    print("MOCKED PASS: structured critique validation with bounded "
          "same-model repair (1 good, 1+1 repaired, 1+2 then fail)")


def check_bounded_revision():
    brief = director.make_brief("Rivers", scene_count=2)
    plan = director.build_plan(brief, ["Water flows downhill.",
                                       "Valleys form over time."])

    def always_flags(brief_arg, scenes, measured_wps=None):
        return ([{"scene_id": "scene-1", "criterion": "hook",
                  "detail": "MOCKED weak hook", "source": "rules"}],
                {}, "mocked-estimate")

    revisions = []

    def fake_revise(scene, reasons, brief_arg):
        revisions.append(scene["id"])
        return ("Revised " + scene["narration"], "revised visual")

    _plan, log = director.revise_loop(
        brief, plan, critique_fn=always_flags,
        revise_scene_fn=fake_revise)
    assert log["cycles_used"] <= director.MAX_REVISION_CYCLES, log
    assert log["editorial_pass"] is False, log
    assert log["unmet_criteria"], log
    print("MOCKED PASS: revision bounded at %d cycles, unmet criteria "
          "surfaced, editorial not passed" % log["cycles_used"])


def _wait_for(client, job_id, deadline_s=20):
    deadline = time.time() + deadline_s
    job = None
    while time.time() < deadline:
        job = client.get("/api/job/" + job_id).get_json()
        if job["status"] in ("done", "error", "cancelled",
                             "interrupted"):
            return job
        time.sleep(0.2)
    return client.get("/api/job/" + job_id).get_json()


def check_failed_stage_reporting():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    # Hermetic: the console writes into a scratch videos dir, never
    # the real one, so mocked jobs cannot pollute live restores.
    tmpv = tempfile.mkdtemp(prefix="phase-d-videos-")
    job_id = None
    try:
        with mock.patch.object(app, "VIDEOS_DIR", tmpv), \
             mock.patch.object(render, "_load_voice",
                               return_value=("tok", "model")), \
             mock.patch.object(render, "generate_images",
                               side_effect=RuntimeError(
                                   "mocked Image down")):
            resp = client.post("/api/video", json={
                "topic": "Anything",
                "script": ["Alpha line.", "Beta line."],
                "visuals": ["alpha visual", "beta visual"]})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job_id = resp.get_json()["job_id"]
            job = _wait_for(client, job_id)
            assert job["status"] == "error", job
            assert job["stages"]["script"] == "done", job["stages"]
            assert job["stages"]["revise"] == "done", job["stages"]
            assert job["stages"]["scenes"] == "error", job["stages"]
            assert not [k for k, v in job["stages"].items()
                        if v == "working"], job["stages"]
            assert "mocked Image down" in job["error"], job
            assert FAKE_KEY not in job["error"], "key leaked into error"
            assert job["finished_at"], job
            assert isinstance(job["elapsed_s"], (int, float)), job
            for rec in job["scenes"]:
                assert rec["status"] != "working", rec
            # The failure persisted to disk for refresh/restart.
            path = os.path.join(tmpv, job_id, "job.json")
            assert os.path.exists(path), path
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["status"] == "error", saved
            assert saved["error"] == job["error"], saved
            assert FAKE_KEY not in json.dumps(saved), "key on disk"
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None) if job_id else None
        shutil.rmtree(tmpv, ignore_errors=True)
    print("MOCKED PASS: failed stage marked error with timestamps, "
          "elapsed, and a matching job.json on disk, no key leaked")


def _write_snapshot(base, job_id, snapshot, files=()):
    out_dir = os.path.join(base, job_id)
    os.makedirs(out_dir, exist_ok=True)
    for name in files:
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(b"fake-bytes")
    with open(os.path.join(out_dir, "job.json"), "w",
              encoding="utf-8") as f:
        json.dump(snapshot, f)
    return out_dir


def check_job_restoration():
    base = tempfile.mkdtemp(prefix="phase-d-restore-")
    with app.JOBS_LOCK:
        app.JOBS.clear()
    try:
        done_id, live_id = "donerestore1", "liverestore2"
        _write_snapshot(base, done_id, {
            "id": done_id, "topic": "Done topic", "status": "done",
            "stages": {k: "done" for k in app.STAGE_KEYS},
            "script": ["Done line one.", "Done line two."],
            "scenes": [
                {"index": 0, "scene_id": "scene-1",
                 "narration": "Done line one.", "status": "done",
                 "image": "/videos/%s/scene_0.png" % done_id,
                 "duration": 3.0},
                {"index": 1, "scene_id": "scene-2",
                 "narration": "Done line two.", "status": "done",
                 "image": "/videos/%s/scene_1.png" % done_id,
                 "duration": 3.0}],
            "video": "/videos/%s/final_Done.mp4" % done_id,
            "qc": {"passed": True}, "error": None,
            "aspect_ratio": "9:16", "width": 720, "height": 1280,
            "created_at": 1.0, "started_at": 1.0,
            "finished_at": 2.0},
            files=("scene_0.png", "scene_1.png", "final_Done.mp4"))
        _write_snapshot(base, live_id, {
            "id": live_id, "topic": "Live topic", "status": "running",
            "stages": {"script": "done", "revise": "done",
                       "scenes": "working", "sam": "waiting",
                       "voice": "waiting", "assemble": "waiting",
                       "qc": "waiting"},
            "script": [], "scenes": [
                {"index": 0, "scene_id": "scene-1",
                 "narration": "Live line one.", "status": "done",
                 "image": "/videos/%s/scene_0.png" % live_id},
                {"index": 1, "scene_id": "scene-2",
                 "narration": "Live line two.", "status": "waiting",
                 "image": None}],
            "video": None, "qc": None, "error": None,
            "aspect_ratio": "9:16", "width": 720, "height": 1280,
            "created_at": 1.0, "started_at": 1.0},
            files=("scene_0.png",))
        summary = app._restore_jobs(base)
        assert summary == {"restored": 2, "interrupted": 1}, summary
        # Completed job: restored intact, progress and assets kept.
        with app.JOBS_LOCK:
            done = app.JOBS[done_id]
            live = app.JOBS[live_id]
        assert done["status"] == "done", done
        assert done["video"] == "/videos/%s/final_Done.mp4" % done_id, done
        assert done["scenes"][0]["image"].endswith("scene_0.png"), done
        # Interrupted job: clearly marked, work preserved on disk,
        # finished scenes keep done, unfinished are interrupted.
        assert live["status"] == "interrupted", live
        assert live["interrupted"] is True, live
        assert live["interrupt_reason"], live
        assert "scene_0.png" in live["preserved"], live
        assert "job.json" not in live["preserved"], live
        assert live["must_rerun"] and live_id in live["must_rerun"], live
        assert live["stages"]["scenes"] == "interrupted", live
        assert live["stages"]["script"] == "done", live["stages"]
        assert live["scenes"][0]["status"] == "done", live["scenes"]
        assert live["scenes"][1]["status"] == "interrupted", live["scenes"]
        # A page refresh sees the same state through the API.
        client = app.app.test_client()
        fetched = client.get("/api/job/" + live_id).get_json()
        assert fetched["status"] == "interrupted", fetched
        assert fetched["preserved"] == live["preserved"], fetched
        listed = client.get("/api/jobs").get_json()["jobs"]
        assert {j["id"] for j in listed} >= {done_id, live_id}, listed
        # Retry of the interrupted job is accepted (it re-renders
        # from the stored approved plan with no re-revision; here
        # the render fails fast on the mocked pipeline error).
        # The interrupted snapshot has no visuals stored, so give
        # it approved visuals first, like a real approval would.
        with app.JOBS_LOCK:
            for rec in app.JOBS[live_id]["scenes"]:
                if not rec.get("visual_prompt"):
                    rec["visual_prompt"] = "mocked visual"
        old_key = app._MEMORY_KEY["key"]
        app._MEMORY_KEY["key"] = FAKE_KEY
        try:
            with mock.patch.object(render, "run_pipeline",
                                   side_effect=RuntimeError(
                                       "mocked render down again")):
                resp = client.post("/api/job/%s/retry" % live_id)
                assert resp.status_code == 200, \
                    resp.get_data(as_text=True)
                job = _wait_for(client, live_id)
                assert job["status"] == "error", job
                assert job["retries"] == 1, job
                assert "mocked render down again" in job["error"], job
                assert job.get("interrupted") is False, job
        finally:
            app._MEMORY_KEY["key"] = old_key
        # Retry of a done job is refused, not run in parallel.
        resp = client.post("/api/job/%s/retry" % done_id)
        assert resp.status_code == 409, resp.get_data(as_text=True)
    finally:
        with app.JOBS_LOCK:
            app.JOBS.clear()
        shutil.rmtree(base, ignore_errors=True)
    print("MOCKED PASS: refresh restores done jobs; restart marks "
          "running jobs interrupted with preserved files; retry re-runs")


def check_no_caption_failure_mode():
    tmp = tempfile.mkdtemp(prefix="phase-d-nocap-")
    try:
        # No finished file at all: QC still fails, but never for
        # captions (there is no caption requirement anymore).
        report = render.qc_report(
            os.path.join(tmp, "final_x.mp4"), aspect_ratio="9:16")
        assert report["passed"] is False, report
        assert "caption_error" not in report, report
        assert "captions" not in report, report
        assert "captions_ok" not in report.get("technical", {}), report
        assert not hasattr(render, "verify_captions"), \
            "verify_captions still present"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("MOCKED PASS: missing output still fails QC, with no "
          "caption failure mode anywhere")


def check_aspect_propagation():
    assert render.dims_for_aspect("9:16") == (720, 1280)
    assert render.dims_for_aspect("16:9") == (1280, 720)
    assert render.DEFAULT_ASPECT == "9:16"
    for bad in ("1:1", "portrait", "", None):
        try:
            render.dims_for_aspect(bad)
        except ValueError as e:
            assert "9:16" in str(e) and "16:9" in str(e), e
        else:
            raise AssertionError(
                "unknown aspect %r silently accepted" % (bad,))
    vertical = render._meta_prompt({"narration": "Rivers."},
                                   aspect_ratio="9:16")
    wide = render._meta_prompt({"narration": "Rivers."},
                               aspect_ratio="16:9")
    assert "portrait" in vertical and "landscape" in wide, (vertical, wide)
    print("MOCKED PASS: aspect ratio drives dims, prompts, and QC; "
          "unknown formats rejected")


def check_duplicate_protection():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    running_id = "mockedrunning9"
    with app.JOBS_LOCK:
        app.JOBS[running_id] = {
            "id": running_id, "topic": "Running", "status": "running",
            "stages": {k: "waiting" for k in app.STAGE_KEYS},
            "script": ["Line one.", "Line two."], "mode": "live",
            "scenes": app.build_scene_records(["Line one.",
                                               "Line two."]),
            "video": None, "qc": None, "error": None,
            "revision_note": None, "brief": {}, "director": None,
            "style": None, "aspect_ratio": "9:16",
            "out_dir": "/tmp/nope-phase-d-dup"}
    try:
        resp = client.post("/api/video", json={
            "topic": "Second", "script": ["A.", "B."],
            "visuals": ["visual A", "visual B"]})
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert "already running" in resp.get_json()["error"], \
            resp.get_data(as_text=True)
        resp = client.patch("/api/job/%s/scene/0" % running_id,
                            json={"narration": "Changed mid-run."})
        assert resp.status_code == 409, resp.get_data(as_text=True)
        resp = client.post("/api/job/%s/scene/0/regenerate" % running_id)
        assert resp.status_code == 409, resp.get_data(as_text=True)
        resp = client.post("/api/job/%s/retry" % running_id)
        assert resp.status_code == 409, resp.get_data(as_text=True)
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(running_id, None)
        shutil.rmtree("/tmp/nope-phase-d-dup", ignore_errors=True)
    print("MOCKED PASS: second render, mid-run edit, mid-run "
          "regenerate, and retry-while-running all rejected with 409")


def check_cancel_and_retry():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    tmpv = tempfile.mkdtemp(prefix="phase-d-videos-cancel-")
    job_id = None
    gate = threading.Event()
    try:
        with mock.patch.object(app, "VIDEOS_DIR", tmpv), \
             mock.patch.object(render, "run_pipeline") as pipeline:

            def _wait_then_stage(scenes, topic, out_dir, **kwargs):
                assert gate.wait(20), "test gate never released"
                kwargs.get("on_stage")("scenes", "done")

            pipeline.side_effect = _wait_then_stage
            resp = client.post("/api/video", json={
                "topic": "Cancel me", "script": ["Alpha.", "Beta."],
                "visuals": ["alpha visual", "beta visual"],
                "aspect_ratio": "9:16"})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job_id = resp.get_json()["job_id"]
            # Cancel on a running job is accepted (real endpoint,
            # worker is parked inside the mocked pipeline)...
            resp = client.post("/api/job/%s/cancel" % job_id)
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert "boundary" in resp.get_json()["note"], \
                resp.get_data(as_text=True)
            gate.set()
            job = _wait_for(client, job_id)
            assert job["status"] == "cancelled", job
            assert job["finished_at"], job
            assert isinstance(job["elapsed_s"], (int, float)), job
            assert not [k for k, v in job["stages"].items()
                        if v == "working"], job["stages"]
            for rec in job["scenes"]:
                assert rec["status"] != "working", rec
            # ...and the cancelled job retries from the stored plan.
            pipeline.side_effect = RuntimeError("mocked render down")
            resp = client.post("/api/job/%s/retry" % job_id)
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job = _wait_for(client, job_id)
            assert job["status"] == "error", job
            assert job["retries"] == 1, job
            assert "mocked render down" in job["error"], job
            # Cancel on a terminal job is refused.
            resp = client.post("/api/job/%s/cancel" % job_id)
            assert resp.status_code == 409, resp.get_data(as_text=True)
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None) if job_id else None
        shutil.rmtree(tmpv, ignore_errors=True)
    print("MOCKED PASS: cooperative cancel marks the job cancelled "
          "at a boundary; retry re-runs from the stored plan")


def check_secrets_safety():
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        redacted = app._safe_error(Exception("leaked " + FAKE_KEY))
    finally:
        app._MEMORY_KEY["key"] = old_key
    assert FAKE_KEY not in redacted, redacted
    assert "[REDACTED KEY]" in redacted, redacted
    long_err = app._safe_error("z" * 600)
    assert len(long_err) <= 503, len(long_err)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, ".gitignore"), encoding="utf-8") as f:
        ignored = f.read()
    for entry in (".env", "videos/", "*.mp4", "test-render-out-9x16/",
                  "test-render-out-16x9/"):
        assert entry in ignored, (entry, ignored)
    # A local .env is supported configuration; it must stay outside Git.
    tracked_env = subprocess.run(
        ["git", "ls-files", "--", ".env"], cwd=here,
        capture_output=True, text=True, check=True).stdout.strip()
    assert not tracked_env, \
        "the local .env file must never be tracked by Git"
    with open(os.path.join(here, "ui", "index.html"),
              encoding="utf-8") as f:
        frontend = f.read()
    if 'src="/scenes/studio/studio.js"' in frontend:
        with open(os.path.join(here, "ui", "scenes", "studio", "studio.js"),
                  encoding="utf-8") as f:
            frontend += "\n" + f.read()
    assert "META_API_KEY" not in frontend, \
        "frontend code references the key variable"
    assert "localStorage.setItem" in frontend, \
        "persistence hook missing from the page"
    print("MOCKED PASS: errors redact the key, .env/videos/runtime "
          "outputs ignored, no key in frontend code")


def check_waiting_plan_persists_across_restore():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    tmpv = tempfile.mkdtemp(prefix="phase-d-waiting-")
    lines = ["Rivers cut valleys over thousands of years of flow.",
             "Valley walls keep rocky layers that students can read."]
    visuals = ["wide river valley at dawn with a heron landing",
               "close-up water polishing stones under noon light"]
    calls = {"n": 0}

    def fake_visual(topic, audience, narration, model=None,
                    api_key=None):
        out = visuals[calls["n"] % len(visuals)]
        calls["n"] += 1
        return "VISUAL: " + out

    def echo_revise_fn(scene, reasons, brief, api_key=None):
        return (scene["narration"], scene["visual_prompt"])

    try:
        with mock.patch.object(app, "VIDEOS_DIR", tmpv), \
             mock.patch.object(app, "draft_script",
                               return_value="\n".join(lines)), \
             mock.patch.object(app, "revise_with_note",
                               return_value=("\n".join(lines),
                                             "mocked note")), \
             mock.patch.object(app, "describe_visual",
                               side_effect=fake_visual), \
             mock.patch.object(app, "_spark_critique_raw",
                               return_value="[]"), \
             mock.patch.object(app, "_spark_revise_scene",
                               side_effect=echo_revise_fn):
            resp = client.post("/api/script", json={
                "topic": "How rivers carve valleys",
                "audience": "middle-school science students",
                "scene_count": 2})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            data = resp.get_json()
            assert data["state"] == "waiting_approval", data
            job_id = data["job_id"]
            resp = client.patch("/api/job/%s/scene/0" % job_id,
                                json={"narration":
                                      "Edited opening line here."})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            with app.JOBS_LOCK:
                app.JOBS.clear()
            summary = app._restore_jobs()
            assert summary["restored"] == 1, summary
            job = client.get("/api/job/" + job_id).get_json()
            assert job["status"] == "waiting_approval", job
            assert job["scenes"][0]["narration"] == \
                "Edited opening line here.", job
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
        shutil.rmtree(tmpv, ignore_errors=True)
    print("MOCKED PASS: waiting_approval plan with a pre-approval "
          "edit restores from disk after a simulated restart")


def main():
    check_scene_counts_and_mapping()
    check_structured_response_validation()
    check_bounded_revision()
    check_failed_stage_reporting()
    check_job_restoration()
    check_waiting_plan_persists_across_restore()
    check_no_caption_failure_mode()
    check_aspect_propagation()
    check_duplicate_protection()
    check_cancel_and_retry()
    check_secrets_safety()
    print("MOCKED ALL PASS: 11 Phase D checks. Live evidence still "
          "required: test_meta.py, test_sam.py, test_render.py, plus "
          "the live Step 0 workspace job.")


if __name__ == "__main__":
    sys.exit(main())
