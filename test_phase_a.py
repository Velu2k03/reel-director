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
  - no captions: assembly writes no SRT and burns no subtitles,
    QC passes without any caption evidence (owner decision Oct 27:
    burned-in captions removed because the output was not good)
  - style and color options (presets, not typed paragraphs)
  - truthful health (Key configured vs authenticated connection,
    ffmpeg/ffprobe source incl. the static-ffmpeg fallback)
  - per-scene regenerate touches one scene only
  - visual edits stored (dirty + video-stale after render, clean
    before; regen consumes the new visual and clears dirty)

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

HERE = os.path.dirname(os.path.abspath(__file__))


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
    # Gated contract: production runs on approved lines+visuals, so
    # the failure here is a production-stage (image) failure, with
    # script+revise already done pre-approval.
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        with mock.patch.object(render, "_load_voice",
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
            deadline = time.time() + 15
            job = None
            while time.time() < deadline:
                job = client.get("/api/job/" + job_id).get_json()
                if job["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
            assert job["status"] == "error", job
            assert job["stages"]["script"] == "done", job["stages"]
            assert job["stages"]["revise"] == "done", job["stages"]
            assert job["stages"]["scenes"] == "error", job["stages"]
            left_working = [k for k, v in job["stages"].items()
                            if v == "working"]
            assert not left_working, job["stages"]
            assert "mocked Image down" in job["error"], job
            assert FAKE_KEY not in job["error"], "key leaked into error"
            for rec in job["scenes"]:
                assert rec["status"] != "working", rec
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.clear()
    print("MOCKED PASS: failed stage marked error, nothing left working, "
          "no key in error")


def check_assemble_failure_fails_hard():
    tmp = tempfile.mkdtemp(prefix="phase-a-assemble-")
    scenes = [{"narration": "Scene one narration here."},
              {"narration": "Scene two narration here."}]
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
        stderr = "mocked ffmpeg failure"

    with mock.patch.object(render, "media_duration", return_value=2.0), \
            mock.patch.object(render, "_run", return_value=FakeResult()):
        try:
            render.assemble(scenes, imgs, auds, "Topic", tmp)
        except RuntimeError as e:
            assert "segment rendering failed for scene" in str(e), e
        else:
            raise AssertionError(
                "assemble silently passed a failed ffmpeg segment")
    print("MOCKED PASS: broken segment fails the job, no silent "
          "fallback")


def check_no_captions_in_assemble():
    tmp = tempfile.mkdtemp(prefix="phase-a-nocap-")
    scenes = [{"narration": "Only scene one words."},
              {"narration": "Only scene two words."}]
    imgs, auds = [], []
    for i in range(2):
        img = os.path.join(tmp, "scene_%d.png" % i)
        aud = os.path.join(tmp, "scene_%d.wav" % i)
        open(img, "wb").write(b"fake-image-bytes")
        open(aud, "wb").write(b"fake-audio-bytes")
        imgs.append(img)
        auds.append(aud)

    seen = []

    class FakeOk:
        returncode = 0
        stderr = ""

        def check_returncode(self):
            return 0

    def fake_run(cmd, timeout=300):
        seen.append(list(cmd))
        out = cmd[-1]
        if cmd[1:3] == ["-y", "-f"]:
            with open(out, "wb") as f:
                f.write(b"fake-final-bytes")
        elif out != os.devnull:
            with open(out, "wb") as f:
                f.write(b"fake-media-bytes")
        return FakeOk()

    with mock.patch.object(render, "media_duration", return_value=2.0), \
            mock.patch.object(render, "_run", side_effect=fake_run):
        render.assemble(scenes, imgs, auds, "Topic", tmp)
    # No SRT file on disk, no subtitles filter anywhere.
    assert [n for n in os.listdir(tmp)
            if n.endswith(".srt")] == [], os.listdir(tmp)
    assert not hasattr(render, "verify_captions"), \
        "verify_captions still present"
    assert not hasattr(render, "caption_style_for_aspect"), \
        "caption_style_for_aspect still present"
    seg_cmds = [c for c in seen if "seg_" in c[-1]]
    assert seg_cmds, seen
    for cmd in seg_cmds:
        vf = cmd[cmd.index("-vf") + 1]
        assert "subtitles" not in vf, (cmd, vf)
        assert "force_style" not in vf, (cmd, vf)
    print("MOCKED PASS: assembly writes no SRT and burns no subtitles")


def check_no_caption_qc_gate():
    tmp = tempfile.mkdtemp(prefix="phase-a-qc-")
    missing = os.path.join(tmp, "gone.mp4")
    # A missing file still fails, but never for captions.
    report = render.qc_report(missing)
    assert report["passed"] is False, report
    assert "caption_error" not in report, report
    assert "captions_ok" not in report.get("technical", {}), report
    assert "captions" not in report, report
    assert "editorial" in report and "technical" in report, report
    print("MOCKED PASS: QC has no caption requirement and no "
          "caption failure mode")


def check_style_options():
    assert len(render.STYLE_PRESETS) >= 6, render.STYLE_PRESETS
    assert len(render.COLOR_THEMES) >= 6, render.COLOR_THEMES
    scene = {"narration": "A lesson about rivers."}
    default_prompt = render._meta_prompt(scene)
    assert default_prompt.startswith(render.META_STYLE), default_prompt
    # Every preset maps to a short block used for every scene.
    heads = set()
    for sid in render.STYLE_PRESETS:
        p = render._meta_prompt({"narration": "A lesson about rivers.",
                                 "style": sid})
        assert render.STYLE_PRESETS[sid]["block"].strip() in p, (sid, p)
        heads.add(p)
    assert len(heads) == len(render.STYLE_PRESETS), heads
    # Flat vector keeps the robot-director look as one option.
    flat = render._meta_prompt({"narration": "A lesson about rivers.",
                                "style": "flat-vector"})
    assert "robot" in flat, flat
    water = render._meta_prompt({"narration": "A lesson about rivers.",
                                 "style": "watercolor"})
    assert "robot" not in water, water
    # Legacy free text still renders exactly, with no forced robot.
    custom = "Watercolor wash, warm paper background, "
    styled = render._meta_prompt({"narration": "A lesson about rivers.",
                                  "style": custom})
    assert custom.strip() in styled, styled
    assert "robot" not in styled, styled
    assert "#1877F2" not in styled, styled
    print("MOCKED PASS: style presets as options, default kept, "
          "no forced robot")


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
            assert data[tool].get("source") in (
                "tools", "path", "static-ffmpeg", "missing"), data
            if data[tool]["available"]:
                assert data[tool].get("version"), data
            else:
                assert data[tool].get("error"), data
    finally:
        app._MEMORY_KEY["key"] = old_key
        if old_env is not None:
            os.environ["META_API_KEY"] = old_env
    print("MOCKED PASS: health separates Key configured from an "
          "authenticated check, reports ffmpeg/ffprobe, leaks no key")


def check_health_static_ffmpeg_fallback():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    old_env = os.environ.get("META_API_KEY")
    try:
        app._MEMORY_KEY["key"] = None
        if "META_API_KEY" in os.environ:
            del os.environ["META_API_KEY"]

        class _Done:
            returncode = 0
            stdout = "ffmpeg version static-fake\nbuilt for tests"
            stderr = ""

        # tools/ and PATH miss: health uses the static-ffmpeg
        # download and says so. No network here: the fetch is
        # stubbed.
        with mock.patch.object(render, "TOOLS_DIR",
                               "/nonexistent-tools-dir"), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch.object(render, "_static_bins",
                                  return_value=("/fake/ffmpeg",
                                                "/fake/ffprobe")), \
                mock.patch("subprocess.run",
                           return_value=_Done()):
            data = client.get("/api/health").get_json()
        assert data["ffmpeg"]["available"] is True, data["ffmpeg"]
        assert data["ffmpeg"]["source"] == "static-ffmpeg", \
            data["ffmpeg"]
        assert "static-fake" in data["ffmpeg"]["version"], \
            data["ffmpeg"]
        assert data["ffprobe"]["available"] is True, data["ffprobe"]
        assert data["ffprobe"]["source"] == "static-ffmpeg", \
            data["ffprobe"]
        # Download failing too: missing is reported honestly,
        # never papered over.
        with mock.patch.object(render, "TOOLS_DIR",
                               "/nonexistent-tools-dir"), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch.object(render, "_static_bins",
                                  side_effect=RuntimeError(
                                      "mocked offline")):
            data = client.get("/api/health").get_json()
        assert data["ffmpeg"]["available"] is False, data["ffmpeg"]
        assert data["ffmpeg"]["source"] == "missing", data["ffmpeg"]
        assert "not found" in data["ffmpeg"]["error"], data["ffmpeg"]
        assert data["ffprobe"]["available"] is False, data["ffprobe"]
        assert data["ffprobe"]["source"] == "missing", data["ffprobe"]
    finally:
        app._MEMORY_KEY["key"] = old_key
        if old_env is not None:
            os.environ["META_API_KEY"] = old_env
    print("MOCKED PASS: health falls back to the static-ffmpeg "
          "download when tools/ and PATH miss, and reports missing "
          "honestly when the download fails")


def check_status_pill_preview_only():
    with open(os.path.join(HERE, "ui", "scenes", "studio", "studio.js"),
              encoding="utf-8") as f:
        js = f.read()
    assert "Preview only (no key)" in js, \
        "status pill must read 'Preview only (no key)' without a key"
    assert "Demo mode" not in js, "status pill must not say 'Demo mode'"
    print("MOCKED PASS: status pill reads 'Preview only (no key)', "
          "never 'Demo mode'")


def check_visual_edit_after_render():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    tmp = tempfile.mkdtemp(prefix="phase-a-visual-")
    job_id = "visualmock1"
    scenes = app.build_scene_records(["First scene words."])
    scenes[0].update({
        "visual_prompt": "old river visual",
        "rendered_narration": "First scene words.",
        "image": "/videos/%s/scene_0.png" % job_id,
        "status": "done",
    })
    with app.JOBS_LOCK:
        app.JOBS[job_id] = {"id": job_id, "topic": "t",
                            "status": "done", "stages": {},
                            "scenes": scenes,
                            "video": "/videos/%s/final.mp4" % job_id,
                            "visuals": ["old river visual"],
                            "qc": None, "error": None,
                            "style": None, "out_dir": tmp}
    try:
        # Terminal job: a visual edit stores the new visual, marks
        # the scene dirty with its reason, and flags video stale.
        resp = client.patch("/api/job/%s/scene/0" % job_id,
                            json={"visual_prompt":
                                  "new mountain visual"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        scene = resp.get_json()["scene"]
        assert scene["visual_prompt"] == "new mountain visual", scene
        assert scene["dirty"] is True, scene
        assert scene["dirty_reason"] == (
            "visual edited after render: image shows the previous "
            "visual; regenerate this scene"), scene
        assert scene["status"] == "dirty", scene
        job = client.get("/api/job/" + job_id).get_json()
        assert job["visuals"][0] == "new mountain visual", job
        assert job["video_stale"] is True, job
        # Regenerating consumes the new visual and clears the dirty
        # flag; the finished video stays flagged stale.
        def fake_image(prompt, path, key, aspect_ratio=None,
                       **kwargs):
            assert "new mountain visual" in prompt, prompt
            with open(path, "wb") as f:
                f.write(b"mocked-scene-image")

        with mock.patch("meta_image.generate_image",
                        side_effect=fake_image), \
                mock.patch("meta_sam.sam_segment",
                           return_value={"subject": "s",
                                         "concept": "mountain",
                                         "matches": 1,
                                         "boxes": [[1, 2, 3, 4]],
                                         "masks": 0,
                                         "warning": None}):
            resp = client.post("/api/job/%s/scene/0/regenerate"
                               % job_id)
            assert resp.status_code == 200, \
                resp.get_data(as_text=True)
        scene = resp.get_json()["scene"]
        assert scene["dirty"] is False, scene
        assert scene["dirty_reason"] is None, scene
        job = client.get("/api/job/" + job_id).get_json()
        assert job["video_stale"] is True, job
        # Queued job: a visual edit updates the plan, never dirty.
        with app.JOBS_LOCK:
            app.JOBS[job_id]["status"] = "queued"
            app.JOBS[job_id]["video"] = None
            app.JOBS[job_id].pop("video_stale", None)
            app.JOBS[job_id]["scenes"][0].update({
                "image": None, "status": "waiting", "dirty": False,
                "dirty_reason": None, "rendered_narration": None})
        resp = client.patch("/api/job/%s/scene/0" % job_id,
                            json={"visual": "queued visual update"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        scene = resp.get_json()["scene"]
        assert scene["visual_prompt"] == "queued visual update", scene
        assert scene["dirty"] is False, scene
        job = client.get("/api/job/" + job_id).get_json()
        assert job["visuals"][0] == "queued visual update", job
        assert job.get("video_stale") is not True, job
        # Errored-early job (no rendered image): plan update, clean.
        with app.JOBS_LOCK:
            app.JOBS[job_id]["status"] = "error"
        resp = client.patch("/api/job/%s/scene/0" % job_id,
                            json={"visual_prompt": "early visual fix"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        scene = resp.get_json()["scene"]
        assert scene["visual_prompt"] == "early visual fix", scene
        assert scene["dirty"] is False, scene
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None)
    print("MOCKED PASS: visual edits store the new visual; after "
          "render the scene is dirty with its reason and the video "
          "stays stale until re-rendered")


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

    def fake_image(prompt, path, key, aspect_ratio=None, **kwargs):
        calls.append((prompt, path, aspect_ratio))
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
        assert "audio still" in scene["dirty_reason"], scene
        assert "caption" not in scene["dirty_reason"].lower(), scene
    finally:
        app._MEMORY_KEY["key"] = old_key
        with app.JOBS_LOCK:
            app.JOBS.pop(job_id, None)
    print("MOCKED PASS: regenerate touches one scene only; post-render "
          "narration edit marks dirty, never silently stale")


def main():
    check_scene_counts_and_mapping()
    check_failed_stage_reporting()
    check_assemble_failure_fails_hard()
    check_no_captions_in_assemble()
    check_no_caption_qc_gate()
    check_style_options()
    check_health_truthful()
    check_health_static_ffmpeg_fallback()
    check_status_pill_preview_only()
    check_visual_edit_after_render()
    check_regenerate_single_scene()
    print("MOCKED ALL PASS: 11 Phase A checks. Live evidence still "
          "required: test_meta.py, test_sam.py, test_render.py.")


if __name__ == "__main__":
    sys.exit(main())
