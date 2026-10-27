"""Phase A focused tests: MOCKED, deterministic, no external services.

Every test here runs without META_API_KEY and without touching the
network: Meta backends (Spark, Wonder, Image, SAM) and ffmpeg runs are
stubbed with unittest.mock. Lines that say MOCKED PASS are this
mocked suite; lines that say LIVE are the live integration scripts
(test_meta.py, test_sam.py, test_render.py) and are NOT replaced by
anything here. Do not substitute a MOCKED result for a live run.

Covers Phase A scope only:
  - dynamic scene counts and asset mapping (1, 5, 7 scenes)
  - failed-stage reporting (failed, never endlessly working; no leaks)
  - caption failure handling (fail-hard, QC must not fully pass)
  - editable visual style (default kept, no forced robot)
  - truthful health (Key configured vs authenticated connection)
  - per-scene regenerate touches one scene only

Run: python test_phase_a.py
"""

import json
import os
import sys
import tempfile
import time
from unittest import mock

import app
import render

FAKE_KEY = "phase-a-fake-key-for-mocked-tests-only"


def check_scene_counts_and_mapping():
    for n in (1, 5, 7):
        lines = ["Lesson line %d about photosynthesis." % i for i in range(n)]
        records = app.build_scene_records(lines)
        assert len(records) == n, (n, len(records))
        for i, rec in enumerate(records):
            assert rec["index"] == i, rec
            assert rec["narration"] == lines[i], rec
            assert rec["provider"] == "muse-image-1.0", rec
            assert "none" in rec["fallback"].lower(), rec
            assert "meta" in rec["fallback"].lower(), rec
            assert rec["image"] is None and rec["duration"] is None, rec
            assert rec["status"] == "waiting" and not rec["dirty"], rec
    # Asset mapping: per-scene image events land on the right card.
    job_id = "mockedjob123"
    lines = ["Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon."]
    with app.JOBS_LOCK:
        app.JOBS[job_id] = {"id": job_id, "topic": "t", "status": "running",
                            "stages": {}, "scenes": app.build_scene_records(lines),
                            "out_dir": "/tmp/nope"}
    try:
        for i in range(5):
            app._scene_event(job_id, i, "image", {"ok": True, "path": "p"})
            app._scene_event(job_id, i, "voice", {"ok": True, "duration": 1.0 + i,
                                                 "path": "a"})
        scenes = app.JOBS[job_id]["scenes"]
        for i, rec in enumerate(scenes):
            assert rec["image"] == "/videos/%s/scene_%d.png" % (job_id, i), rec
            assert rec["duration"] == 1.0 + i, rec
            assert rec["rendered_narration"] == lines[i], rec
    finally:
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None)
    print("MOCKED PASS: scene counts (1/5/7) and per-scene asset mapping")


def check_failed_stage_reporting():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        with mock.patch.object(app, "draft_script",
                               side_effect=RuntimeError("mocked Spark down")):
            resp = client.post("/api/video", json={"topic": "Anything"})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job_id = resp.get_json()["job_id"]
            deadline = time.time() + 15
            job = None
            while time.time() < deadline:
                job = client.get("/api/job/" + job_id).get_json()
                if job["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
            assert job["status"] == "error", job
            assert job["stages"]["script"] == "error", job["stages"]
            left_working = [k for k, v in job["stages"].items()
                            if v == "working"]
            assert not left_working, job["stages"]
            assert "mocked Spark down" in job["error"], job
            assert FAKE_KEY not in job["error"], "key leaked into error"
            for rec in job["scenes"]:
                assert rec["status"] != "working", rec
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: failed stage marked error, nothing left working, "
          "no key in error")


def check_caption_failure_fails_hard():
    tmp = tempfile.mkdtemp(prefix="phase-a-captions-")
    scenes = [{"narration": "Scene one caption here."},
              {"narration": "Scene two caption here."}]
    imgs = []
    auds = []
    for i in range(2):
        img = os.path.join(tmp, "scene_%d.png" % i)
        aud = os.path.join(tmp, "scene_%d.wav" % i)
        open(img, "wb").write(b"fake-image-bytes")
        open(aud, "wb").write(b"fake-audio-bytes")
        imgs.append(img)
        auds.append(aud)

    class FakeResult:
        returncode = 1
        stderr = "Unable to load subtitles: mocked font failure"

    with mock.patch.object(render, "media_duration", return_value=2.0), \
            mock.patch.object(render, "_run", return_value=FakeResult()):
        try:
            render.assemble(scenes, imgs, auds, "Topic", tmp)
        except RuntimeError as e:
            assert "caption rendering failed for scene" in str(e), e
        else:
            raise AssertionError(
                "assemble silently passed a failed subtitle burn")
    print("MOCKED PASS: broken subtitle path fails the job, no silent "
          "fallback without captions")


def check_caption_qc_gate():
    tmp = tempfile.mkdtemp(prefix="phase-a-qc-")
    missing = os.path.join(tmp, "gone.mp4")
    # Degraded captions must not pass as a full success.
    report = render.qc_report(missing, captions=[{"scene": 0, "clean": False}],
                              captions_ok=False)
    assert report["passed"] is False, report
    assert "caption_error" in report, report
    assert report["technical"]["captions_ok"] is False, report
    assert "editorial" in report and "technical" in report, report
    # Missing caption evidence also fails closed.
    report2 = render.qc_report(missing)
    assert report2["passed"] is False, report2
    assert report2["captions_ok"] is False, report2
    print("MOCKED PASS: degraded/missing captions fail QC, technical vs "
          "editorial kept separate")


def check_verify_captions():
    tmp = tempfile.mkdtemp(prefix="phase-a-srt-")
    scenes = [{"narration": "Only scene one words."},
              {"narration": "Only scene two words."}]
    with open(os.path.join(tmp, "scene_0.srt"), "w") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nOnly scene one words.\n")
    with open(os.path.join(tmp, "scene_1.srt"), "w") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nOnly scene two words.\n"
                "Only scene one words.\n")
    records, ok = render.verify_captions(scenes, tmp)
    assert ok is False, records
    assert records[0]["clean"] is True, records
    assert records[1]["clean"] is False, records
    # Missing file fails, never silently passes.
    os.remove(os.path.join(tmp, "scene_1.srt"))
    records, ok = render.verify_captions(scenes, tmp)
    assert ok is False and records[1]["clean"] is False, records
    print("MOCKED PASS: per-scene caption tracking (clean / leaked / "
          "missing)")


def check_style_editable():
    scene = {"narration": "A lesson about rivers."}
    default_prompt = render._meta_prompt(scene)
    assert default_prompt.startswith(render.META_STYLE), default_prompt
    custom = "Watercolor wash, warm paper background, "
    styled = render._meta_prompt({"narration": "A lesson about rivers.",
                                  "style": custom})
    assert custom.strip() in styled, styled
    assert "robot" not in styled, styled
    assert "#1877F2" not in styled, styled
    print("MOCKED PASS: style editable, default kept, no forced robot")


def check_health_truthful():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    old_env = os.environ.get("META_API_KEY")
    try:
        # No key anywhere: demo, connection unchecked, never API online.
        app._MEMORY_KEY["key"] = None
        if "META_API_KEY" in os.environ:
            del os.environ["META_API_KEY"]
        data = client.get("/api/health").get_json()
        assert data["key"]["configured"] is False, data
        assert data["key"]["label"] == "No key", data
        assert data["connection"]["checked"] is False, data
        assert data["mode"] == "demo", data
        # Key present but connection failing: "Key configured" + real
        # failure, never "API online".
        app._MEMORY_KEY["key"] = FAKE_KEY
        with mock.patch.object(app, "list_models",
                               side_effect=RuntimeError("mocked 401 denied")):
            data = client.get("/api/health").get_json()
        assert data["key"]["configured"] is True, data
        assert data["key"]["label"] == "Key configured", data
        assert data["connection"]["checked"] is True, data
        assert data["connection"]["ok"] is False, data
        assert "mocked 401" in data["connection"]["error"], data
        assert FAKE_KEY not in json.dumps(data), "key leaked in health"
        # Authenticated check passing: 9 fake models.
        with mock.patch.object(app, "list_models",
                               return_value=[{"id": "m%d" % i}
                                             for i in range(9)]):
            data = client.get("/api/health").get_json()
        assert data["connection"]["ok"] is True, data
        assert data["connection"]["models"] == 9, data
        # ffmpeg/ffprobe honestly reported with the fields present.
        for tool in ("ffmpeg", "ffprobe"):
            assert "available" in data[tool], data
    finally:
        app._MEMORY_KEY["key"] = old_key
        if old_env is not None:
            os.environ["META_API_KEY"] = old_env
    print("MOCKED PASS: health separates Key configured from an "
          "authenticated check, reports ffmpeg/ffprobe, leaks no key")


def check_regenerate_single_scene():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    tmp = tempfile.mkdtemp(prefix="phase-a-regen-")
    job_id = "regenmock1"
    lines = ["First scene words.", "Second scene words.", "Third scene words."]
    with app.JOBS_LOCK:
        app.JOBS[job_id] = {"id": job_id, "topic": "t", "status": "done",
                            "stages": {}, "scenes": app.build_scene_records(lines),
                            "video": None, "qc": None, "error": None,
                            "style": None, "out_dir": tmp}
    calls = []

    def fake_image(prompt, path, key):
        calls.append((prompt, path))
        with open(path, "wb") as f:
            f.write(b"mocked-scene-image")

    try:
        with mock.patch("meta_image.generate_image",
                        side_effect=fake_image), \
                mock.patch("meta_sam.sam_segment",
                           return_value={"subject": "s", "concept": "scene",
                                         "matches": 1, "boxes": [[1, 2, 3, 4]],
                                         "masks": 0, "warning": None}):
            resp = client.post("/api/job/%s/scene/1/regenerate" % job_id)
            assert resp.status_code == 200, resp.get_data(as_text=True)
        assert len(calls) == 1, calls
        assert calls[0][1] == os.path.join(tmp, "scene_1.png"), calls
        job = client.get("/api/job/" + job_id).get_json()
        assert job["scenes"][1]["image"] == "/videos/%s/scene_1.png" % job_id
        assert job["scenes"][1]["sam"]["matches"] == 1
        assert job["scenes"][0]["image"] is None, job["scenes"][0]
        assert job["scenes"][2]["image"] is None, job["scenes"][2]
        # Narration edit after render marks dirty with its reason.
        with app.JOBS_LOCK:
            app.JOBS[job_id]["scenes"][1]["rendered_narration"] = \
                "Second scene words."
            app.JOBS[job_id]["scenes"][1]["status"] = "done"
        resp = client.patch("/api/job/%s/scene/1" % job_id,
                            json={"narration": "Completely new words here."})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        scene = resp.get_json()["scene"]
        assert scene["dirty"] is True and scene["dirty_reason"], scene
        assert "audio and captions" in scene["dirty_reason"], scene
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None)
    print("MOCKED PASS: regenerate touches one scene only; post-render "
          "narration edit marks dirty, never silently stale")


def main():
    check_scene_counts_and_mapping()
    check_failed_stage_reporting()
    check_caption_failure_fails_hard()
    check_caption_qc_gate()
    check_verify_captions()
    check_style_editable()
    check_health_truthful()
    check_regenerate_single_scene()
    print("MOCKED ALL PASS: 8 Phase A checks. Live evidence still "
          "required: test_meta.py, test_sam.py, test_render.py.")


if __name__ == "__main__":
    sys.exit(main())
