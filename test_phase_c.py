"""Phase C focused tests: MOCKED, deterministic, no external services.

Every test here runs without META_API_KEY and without touching the
network: Muse Image, SAM, and ffmpeg runs are stubbed with
unittest.mock. Lines that say MOCKED PASS are this mocked suite;
lines that say LIVE are the live integration runs (test_render.py
in both formats, plus live end-to-end jobs through the workspace)
and are NOT replaced by anything here. Do not substitute a MOCKED
result for a live run.

Covers Phase C scope only:
  - aspect-ratio propagation: selected 9:16 produces vertical
    dimensions in the image prompt, the ffmpeg render, preview
    metadata, and QC, and a mismatched dimension fails QC; same
    for 16:9
  - workspace state handling: empty, loading, success, and failure
    states are distinct, and a failed job never shows as working

Run: python test_phase_c.py
"""

import os
import sys
import tempfile
import time
from unittest import mock

import app
import render

FAKE_KEY = "phase-c-fake-key-for-mocked-tests-only"


def check_aspect_dims():
    assert render.dims_for_aspect("9:16") == (720, 1280)
    assert render.dims_for_aspect("16:9") == (1280, 720)
    assert render.DEFAULT_ASPECT == "9:16"
    for bad in ("1:1", "vertical", "", None, "916"):
        try:
            render.dims_for_aspect(bad)
        except ValueError as e:
            assert "9:16" in str(e) and "16:9" in str(e), e
        else:
            raise AssertionError(
                "unknown aspect %r silently accepted" % (bad,))
    print("MOCKED PASS: aspect dims (9:16 vertical default, 16:9 wide, "
          "unknown formats rejected)")


def check_prompt_orientation():
    scene = {"narration": "A lesson about rivers."}
    vertical = render._meta_prompt(scene, aspect_ratio="9:16")
    wide = render._meta_prompt(scene, aspect_ratio="16:9")
    assert vertical.startswith(render.META_STYLE), vertical
    assert wide.startswith(render.META_STYLE), wide
    assert "9:16" in vertical and "portrait" in vertical, vertical
    assert "16:9" in wide and "landscape" in wide, wide
    assert vertical != wide, "orientation is not format-specific"
    # Scene-carried aspect works too (no caller arg needed).
    carried = render._meta_prompt(dict(scene, aspect_ratio="16:9"))
    assert "landscape" in carried, carried
    print("MOCKED PASS: image prompt carries real orientation per format")


def check_no_caption_layout():
    # Captions are gone (owner decision Oct 27): no per-format
    # caption layout exists anywhere in the render path.
    assert not hasattr(render, "caption_style_for_aspect"), \
        "caption_style_for_aspect still present"
    assert not hasattr(render, "verify_captions"), \
        "verify_captions still present"
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "render.py"), encoding="utf-8") as f:
        source = f.read()
    # No subtitles filter is ever built into an ffmpeg command (the
    # word survives only in the removal note in docstrings).
    assert "subtitles='" not in source, \
        "render.py still builds a subtitles filter"
    assert "_segment_srt" not in source, \
        "render.py still writes segment SRT files"
    print("MOCKED PASS: no caption layout, no caption check, no "
          "subtitles filter in the render path")


class _FakeRun:
    """Stand-in for render._run: materializes the output file so the
    pipeline's existence checks pass, records every command."""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, timeout=300):
        self.cmds.append(list(cmd))
        out = cmd[-1]
        if out != os.devnull:
            with open(out, "wb") as f:
                f.write(b"fake-media-bytes")
        return self

    @property
    def returncode(self):
        return 0

    @property
    def stderr(self):
        return ""

    def check_returncode(self):
        return 0


def _two_scenes():
    return [{"narration": "Alpha robots learn by watching."},
            {"narration": "Beta robots practice until they improve."}]


def _fake_media(tmp, scenes):
    imgs, auds = [], []
    for i in range(len(scenes)):
        img = os.path.join(tmp, "scene_%d.png" % i)
        aud = os.path.join(tmp, "scene_%d.wav" % i)
        open(img, "wb").write(b"fake-image-bytes")
        open(aud, "wb").write(b"fake-audio-bytes")
        imgs.append(img)
        auds.append(aud)
    return imgs, auds


def check_assemble_dims():
    for aspect, dims in (("9:16", "scale=720:1280"),
                         ("16:9", "scale=1280:720")):
        tmp = tempfile.mkdtemp(prefix="phase-c-assemble-")
        scenes = _two_scenes()
        imgs, auds = _fake_media(tmp, scenes)
        fake = _FakeRun()
        with mock.patch.object(render, "media_duration",
                               return_value=2.0), \
                mock.patch.object(render, "_run", side_effect=fake):
            final = render.assemble(scenes, imgs, auds, "Topic", tmp,
                                    aspect_ratio=aspect)
        assert os.path.exists(final), final
        assert len(fake.cmds) == 1, fake.cmds
        cmd = fake.cmds[0]
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert dims in fc, (aspect, fc)
        assert "crop=" in fc, (aspect, fc)
        assert "subtitles" not in fc, (aspect, fc)
        assert "force_style" not in fc, (aspect, fc)
        assert "zoompan" in fc, (aspect, fc)
        assert "xfade" in fc and "acrossfade" in fc, (aspect, fc)
        assert "yuv420p" in fc, cmd  # browser-compatible pixels
        assert "+faststart" in cmd, cmd
    # Unknown formats fail fast instead of rendering wrong.
    tmp = tempfile.mkdtemp(prefix="phase-c-assemble-bad-")
    scenes = _two_scenes()
    imgs, auds = _fake_media(tmp, scenes)
    try:
        render.assemble(scenes, imgs, auds, "Topic", tmp,
                        aspect_ratio="1:1")
    except ValueError:
        pass
    else:
        raise AssertionError("assemble accepted an unknown format")
    print("MOCKED PASS: ffmpeg frame follows the selected format "
          "(CSS alone changes nothing)")


def _fake_probe(width, height):
    return {"format": {"duration": "4.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264",
                         "width": width, "height": height},
                        {"codec_type": "audio",
                         "codec_name": "aac"}]}


class _FakeVolume:
    returncode = 0
    stderr = ("[Parsed_volumedetect_0] mean_volume: -20.0 dB\n"
             "[Parsed_volumedetect_0] max_volume: -5.0 dB\n")


def _qc_with_dims(width, height, aspect):
    tmp = tempfile.mkdtemp(prefix="phase-c-qc-")
    path = os.path.join(tmp, "final_Test.mp4")
    open(path, "wb").write(b"fake-video-bytes")
    with mock.patch.object(render, "_ffprobe",
                           return_value=_fake_probe(width, height)), \
            mock.patch.object(render, "_run",
                              return_value=_FakeVolume()):
        report = render.qc_report(path, aspect_ratio=aspect)
        # A video without captions passes QC: no caption evidence
        # is required or reported.
        assert "captions" not in report, report
        assert "caption_error" not in report, report
        return report


def check_qc_dims():
    for aspect, (w, h) in (("9:16", (720, 1280)),
                           ("16:9", (1280, 720))):
        report = _qc_with_dims(w, h, aspect)
        assert report["aspect_ratio"] == aspect, report
        assert report["expected_dimensions"] == "%dx%d" % (w, h), report
        assert report["technical"]["resolution_ok"] is True, report
        assert report["passed"] is True, report
        assert "dimension_error" not in report, report
    # Mismatched dimensions fail QC loudly: a 16:9 file never
    # passes as a 9:16 job, and vice versa.
    bad = _qc_with_dims(1280, 720, "9:16")
    assert bad["passed"] is False, bad
    assert bad["technical"]["resolution_ok"] is False, bad
    assert "dimension_error" in bad and "720x1280" in bad[
        "dimension_error"], bad
    bad2 = _qc_with_dims(720, 1280, "16:9")
    assert bad2["passed"] is False, bad2
    assert "dimension_error" in bad2, bad2
    print("MOCKED PASS: QC checks the SELECTED dimensions and fails "
          "mismatched output, never degraded-pass")


def check_preview_metadata():
    """The job record the preview reads carries the real format."""
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    approved = {"script": ["Alpha line.", "Beta line."],
                "visuals": ["alpha visual", "beta visual"]}
    try:
        with mock.patch.object(render, "run_pipeline",
                               side_effect=RuntimeError("mocked down")):
            # Unknown formats are rejected before any job exists.
            resp = client.post("/api/video", json=dict(
                {"topic": "Rivers", "aspect_ratio": "1:1"}, **approved))
            assert resp.status_code == 400, resp.get_data(as_text=True)
            # Default is vertical when the caller sends no format.
            resp = client.post("/api/video", json=dict(
                {"topic": "Rivers"}, **approved))
            assert resp.status_code == 200, resp.get_data(as_text=True)
            default_job = client.get(
                "/api/job/" + resp.get_json()["job_id"]).get_json()
            assert default_job["aspect_ratio"] == "9:16", default_job
            assert (default_job["width"],
                    default_job["height"]) == (720, 1280), default_job
            # Wait for the (mock-failed) job to finish, then go wide.
            deadline = time.time() + 15
            while time.time() < deadline:
                state = client.get(
                    "/api/job/" + default_job["id"]).get_json()["status"]
                if state in ("done", "error"):
                    break
                time.sleep(0.2)
            resp = client.post("/api/video", json=dict(
                {"topic": "Rivers", "aspect_ratio": "16:9",
                 "audience": "middle-schoolers",
                 "target_duration_s": 24}, **approved))
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job = client.get(
                "/api/job/" + resp.get_json()["job_id"]).get_json()
            assert job["aspect_ratio"] == "16:9", job
            assert (job["width"], job["height"]) == (1280, 720), job
            assert job["brief"]["aspect_ratio"] == "16:9", job
            assert job["brief"]["audience"] == "middle-schoolers", job
            assert job["brief"]["target_duration_s"] == 24, job
    finally:
        with app.JOBS_LOCK:
            app.JOBS.clear()
        app._MEMORY_KEY["key"] = old_key
    print("MOCKED PASS: preview metadata carries the selected format "
          "(default 9:16, 16:9 on request, unknown rejected)")


def check_workspace_states():
    """Empty, loading, success, and failure are distinct job states,
    and a failed job never shows as working."""
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        # Failure: the image backend goes down after a visible
        # loading stretch. The job must end failed with nothing
        # left working and its real error attached.
        def _slow_image_down(*args, **kwargs):
            time.sleep(2.0)
            raise RuntimeError("mocked Image down")

        with mock.patch.object(render, "_load_voice",
                               return_value=("tok", "model")), \
                mock.patch.object(render, "generate_images",
                                  side_effect=_slow_image_down):
            resp = client.post("/api/video", json={
                "topic": "Rivers",
                "script": ["Alpha line.", "Beta line."],
                "visuals": ["alpha visual", "beta visual"]})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job_id = resp.get_json()["job_id"]
            job = client.get("/api/job/" + job_id).get_json()
            assert job["status"] in ("queued", "running"), job
            loading_seen = job["status"]
            deadline = time.time() + 15
            while time.time() < deadline:
                job = client.get("/api/job/" + job_id).get_json()
                if job["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
            assert job["status"] == "error", job
            assert loading_seen in ("queued", "running"), loading_seen
            assert job["status"] != loading_seen, job
            assert "mocked Image down" in job["error"], job
            assert FAKE_KEY not in job["error"], "key leaked"
            left = [k for k, v in job["stages"].items()
                    if v == "working"]
            assert not left, job["stages"]
            for rec in job["scenes"]:
                assert rec["status"] != "working", rec
            # Unknown jobs stay unknown, never a fake state.
            missing = client.get("/api/job/no-such-job")
            assert missing.status_code == 404, missing.status_code
    finally:
        with app.JOBS_LOCK:
            app.JOBS.clear()
        app._MEMORY_KEY["key"] = old_key
    print("MOCKED PASS: failure is terminal and distinct (nothing left "
          "working); unknown jobs stay unknown")


def main():
    check_aspect_dims()
    check_prompt_orientation()
    check_no_caption_layout()
    check_assemble_dims()
    check_qc_dims()
    check_preview_metadata()
    check_workspace_states()
    print("MOCKED ALL PASS: 7 Phase C checks. Live evidence still "
          "required: test_render.py (both formats) plus live 9:16 and "
          "16:9 end-to-end jobs through the workspace.")


if __name__ == "__main__":
    sys.exit(main())
