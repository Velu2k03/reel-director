"""No-captions + style/color-option checks: MOCKED, no key, no network.

Owner decision Oct 27: burned-in captions are removed because the
output was not good, and visual style plus color are pickers, not a
long text field to type. Every check here is mocked (ffmpeg, probe,
and model calls stubbed); lines that say MOCKED PASS are this suite.
Live proof comes separately from test_render.py (both formats, real
key) and one approved end-to-end run. Do not substitute a MOCKED
result for a live run.

Run: python test_no_captions_style.py
"""

import os
import sys
import tempfile
from unittest import mock

import app
import render

FAKE_KEY = "no-cap-fake-key-for-mocked-tests-only"

HERE = os.path.dirname(os.path.abspath(__file__))


def check_preset_tables():
    assert len(render.STYLE_PRESETS) >= 6, render.STYLE_PRESETS
    assert len(render.COLOR_THEMES) >= 6, render.COLOR_THEMES
    for sid, preset in render.STYLE_PRESETS.items():
        assert preset["name"] and preset["blurb"] and preset["block"], \
            (sid, preset)
    for cid, theme in render.COLOR_THEMES.items():
        assert theme["name"] and theme["blurb"] and theme["block"], \
            (cid, theme)
        assert theme["swatch"], (cid, theme)
    assert render.DEFAULT_STYLE in render.STYLE_PRESETS
    assert render.DEFAULT_COLOR in render.COLOR_THEMES
    print("MOCKED PASS: style presets (6+) and color themes (6+) each "
          "carry a short name, blurb, and prompt block")


def check_color_changes_prompt():
    base = {"narration": "A lesson about rivers.", "style": "flat-vector"}
    seen = set()
    for cid in render.COLOR_THEMES:
        prompt = render._meta_prompt(dict(base, color=cid))
        block = render.COLOR_THEMES[cid]["block"].strip()
        assert block in prompt, (cid, prompt)
        seen.add(block)
    assert len(seen) == len(render.COLOR_THEMES), seen
    # Same style, different theme: the color text differs.
    blue = render._meta_prompt(dict(base, color="facebook-blue"))
    ocean = render._meta_prompt(dict(base, color="ocean"))
    assert "#1877F2" in blue and "#1877F2" not in ocean, (blue, ocean)
    print("MOCKED PASS: selecting a color theme changes the image "
          "prompt colors")


def check_same_look_every_scene():
    scenes = [{"narration": "Line %d about rivers." % i,
               "style": "paper-cut", "color": "forest"}
              for i in range(3)]
    prompts = [render._meta_prompt(s) for s in scenes]
    head = (render.STYLE_PRESETS["paper-cut"]["block"]
            + render.COLOR_THEMES["forest"]["block"])
    for prompt in prompts:
        assert prompt.startswith(head), prompt
    print("MOCKED PASS: every scene prompt shares the selected style "
          "and color")


def check_changed_selection_changes_next_job():
    scene = {"narration": "A lesson about rivers."}
    first = render._meta_prompt(dict(scene, style="flat-vector",
                                     color="facebook-blue"))
    second = render._meta_prompt(dict(scene, style="watercolor",
                                      color="ocean"))
    assert first != second, (first, second)
    assert "Watercolor" in second and "Ocean" in second, second
    print("MOCKED PASS: a changed style/color pick changes the next "
          "job's prompts")


def check_custom_phrase_short_only():
    scene = {"narration": "A lesson about rivers.", "style": "flat-vector",
             "color": "ocean", "style_custom": "tiny stars"}
    assert "tiny stars" in render._meta_prompt(scene), scene
    try:
        render.resolve_custom_phrase("x" * (render.MAX_CUSTOM_PHRASE + 1))
    except ValueError as e:
        assert "paragraph" in str(e), e
    else:
        raise AssertionError("paragraph-length custom phrase accepted")
    print("MOCKED PASS: custom phrase is short-only, paragraphs "
          "rejected")


def check_style_api_options():
    client = app.app.test_client()
    data = client.get("/api/style").get_json()
    assert len(data["styles"]) >= 6, data
    assert len(data["colors"]) >= 6, data
    assert data["default_style"] in render.STYLE_PRESETS, data
    assert data["default_color"] in render.COLOR_THEMES, data
    for entry in data["styles"]:
        assert entry["id"] and entry["name"] and entry["blurb"], entry
    for entry in data["colors"]:
        assert entry["swatch"], entry
    print("MOCKED PASS: GET /api/style serves style presets and "
          "color themes with defaults")


def check_style_api_validation():
    client = app.app.test_client()
    old_key = app._MEMORY_KEY["key"]
    app._MEMORY_KEY["key"] = FAKE_KEY
    good = {"topic": "Rivers",
            "script": ["Alpha line.", "Beta line."],
            "visuals": ["alpha visual", "beta visual"]}
    # Importing app restores prior jobs from disk: start isolated.
    with app.JOBS_LOCK:
        app.JOBS.clear()
    try:
        with mock.patch.object(app.render, "run_pipeline",
                               side_effect=RuntimeError("mocked down")):
            # Unknown style id fails naming the valid options.
            resp = client.post("/api/video", json=dict(
                good, style="paint-it-like-a-movie"))
            assert resp.status_code == 400, resp.get_data(as_text=True)
            assert "flat-vector" in resp.get_json()["error"], \
                resp.get_json()
            # Unknown color theme fails the same way.
            resp = client.post("/api/video", json=dict(
                good, color="ultraviolet-neon"))
            assert resp.status_code == 400, resp.get_data(as_text=True)
            assert "ocean" in resp.get_json()["error"], resp.get_json()
            # A paragraph as custom text fails, never silently cut.
            resp = client.post("/api/video", json=dict(
                good, style_custom="word " * 100))
            assert resp.status_code == 400, resp.get_data(as_text=True)
            # No job was created by any rejected pick.
            with app.JOBS_LOCK:
                assert not app.JOBS, [j for j in app.JOBS]
            # Valid picks are recorded on the job with labels.
            import time
            resp = client.post("/api/video", json=dict(
                good, style="watercolor", color="ocean"))
            assert resp.status_code == 200, resp.get_data(as_text=True)
            job_id = resp.get_json()["job_id"]
            deadline = time.time() + 15
            while time.time() < deadline:
                job = client.get("/api/job/" + job_id).get_json()
                if job["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
            job = client.get("/api/job/" + job_id).get_json()
            assert job["style"] == "watercolor", job
            assert job["color"] == "ocean", job
            assert job["style_name"] == "Watercolor", job
            assert job["color_name"] == "Ocean", job
            for rec in job["scenes"]:
                assert rec["style"] == "watercolor", rec
                assert rec["color"] == "ocean", rec
                assert rec["style_name"] == "Watercolor", rec
                assert rec["color_name"] == "Ocean", rec
    finally:
        with app.JOBS_LOCK:
            app.JOBS.clear()
        app._MEMORY_KEY["key"] = old_key
    print("MOCKED PASS: unknown style/color and paragraph custom "
          "rejected; valid picks recorded on the job and storyboard")


def _fake_probe_ok(width, height):
    return {"format": {"duration": "4.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264",
                         "width": width, "height": height},
                        {"codec_type": "audio",
                         "codec_name": "aac"}]}


class _FakeVolume:
    returncode = 0
    stderr = ("[Parsed_volumedetect_0] mean_volume: -20.0 dB\n"
             "[Parsed_volumedetect_0] max_volume: -5.0 dB\n")


def check_video_without_captions_passes_qc():
    tmp = tempfile.mkdtemp(prefix="nocap-qc-")
    path = os.path.join(tmp, "final_Test.mp4")
    with open(path, "wb") as f:
        f.write(b"fake-video-bytes")
    with mock.patch.object(render, "_ffprobe",
                           return_value=_fake_probe_ok(720, 1280)), \
            mock.patch.object(render, "_run",
                              return_value=_FakeVolume()):
        report = render.qc_report(path, aspect_ratio="9:16")
    assert report["passed"] is True, report
    assert report["technical"]["duration_ok"] is True, report
    assert report["technical"]["resolution_ok"] is True, report
    assert report["technical"]["audio_ok"] is True, report
    assert report["technical"]["audible_ok"] is True, report
    assert "captions" not in report, report
    assert "caption_error" not in report, report
    print("MOCKED PASS: a video without captions passes QC on "
          "duration, dimensions, audio, and audibility")


def check_ui_has_pickers_no_caption_claims():
    with open(os.path.join(HERE, "ui", "index.html"),
              encoding="utf-8") as f:
        html = f.read()
    assert 'id="styleGrid"' in html, "style picker missing"
    assert 'id="colorGrid"' in html, "color picker missing"
    assert 'id="styleInput"' not in html, "long style textarea remains"
    assert "Captions verified" not in html, "caption claim remains"
    assert "caption layout" not in html.lower(), "caption claim remains"
    assert "safe margins for captions" not in html.lower(), \
        "caption claim remains"
    assert "captions_ok" not in html, "caption check remains"
    print("MOCKED PASS: brief panel shows style and color pickers "
          "with no long textarea and no caption claims")


def main():
    check_preset_tables()
    check_color_changes_prompt()
    check_same_look_every_scene()
    check_changed_selection_changes_next_job()
    check_custom_phrase_short_only()
    check_style_api_options()
    check_style_api_validation()
    check_video_without_captions_passes_qc()
    check_ui_has_pickers_no_caption_claims()
    print("MOCKED ALL PASS: 9 no-caption + style-option checks. Live "
          "evidence still required: test_meta.py, test_sam.py, "
          "test_render.py (both formats), one approved run.")


if __name__ == "__main__":
    sys.exit(main())
