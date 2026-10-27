"""Codex follow-up tests: MOCKED, deterministic, no external services.

P1 (true aspect art, no stretching):
  - 9:16 generate_images requests vertical art (size 720x1280 or
    aspect 9:16 threaded into meta_image.generate_image)
  - 16:9 generate_images requests wide art (1280x720 / 16:9)
  - unknown aspect rejected (ValueError), never silently rendered
  - assemble center-crops before scaling (vf has crop + exact scale),
    never a bare stretching scale; QC dims unchanged

P2 (pasted in-memory key works for generation):
  - pasted key with no environment key succeeds for script generation
  - critique + revise-scene paths also use the pasted key
  - no key at all fails with a clear error, never a silent fallback

Lines that say MOCKED PASS are this mocked suite; lines that say
LIVE are the live integration scripts and are NOT replaced by
anything here. Do not substitute a MOCKED result for a live run.

Run: python test_codex_followup.py
"""

import os
import sys
import tempfile
from unittest import mock

import app as console_app
import meta_backend
import meta_image
import render

FAKE_KEY = "codex-followup-fake-key-for-mocked-tests-only"


def _scenes(n=2):
    return [{"narration": "Scene line %d about photosynthesis." % i}
            for i in range(n)]


def _capture_image_calls(aspect):
    """Run generate_images with a stubbed image backend; return calls."""
    tmp = tempfile.mkdtemp(prefix="codex-img-")
    calls = []

    def fake_generate(prompt, out_path, api_key, **kwargs):
        calls.append({"prompt": prompt, "api_key": api_key,
                      "kwargs": dict(kwargs)})
        with open(out_path, "wb") as f:
            f.write(b"fake-png-bytes")
        return out_path

    with mock.patch.object(render.meta_image, "generate_image",
                           side_effect=fake_generate):
        paths, backend = render.generate_images(
            _scenes(), tmp, FAKE_KEY, aspect_ratio=aspect)
    assert backend == "meta", backend
    assert len(paths) == 2, paths
    return calls


def check_vertical_requests_vertical():
    calls = _capture_image_calls("9:16")
    for call in calls:
        size = call["kwargs"].get("size", "")
        aspect = call["kwargs"].get("aspect_ratio", "")
        vertical = (size == "720x1280") or (aspect == "9:16")
        assert vertical, (
            "9:16 job did not request vertical art: %r" % (call["kwargs"],))
    print("MOCKED PASS: 9:16 requests vertical art (720x1280 or 9:16)")


def check_wide_requests_wide():
    calls = _capture_image_calls("16:9")
    for call in calls:
        size = call["kwargs"].get("size", "")
        aspect = call["kwargs"].get("aspect_ratio", "")
        wide = (size == "1280x720") or (aspect == "16:9")
        assert wide, (
            "16:9 job did not request wide art: %r" % (call["kwargs"],))
    print("MOCKED PASS: 16:9 requests wide art (1280x720 or 16:9)")


def check_unknown_aspect_rejected():
    tmp = tempfile.mkdtemp(prefix="codex-img-bad-")
    with mock.patch.object(render.meta_image, "generate_image") as gen:
        try:
            render.generate_images(_scenes(), tmp, FAKE_KEY,
                                   aspect_ratio="1:1")
        except ValueError:
            pass
        else:
            raise AssertionError("unknown aspect silently accepted")
        assert gen.call_count == 0, gen.call_count
    print("MOCKED PASS: unknown aspect rejected, no image requested")


class _FakeRun:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, timeout=300):
        self.cmds.append(cmd)
        seg = cmd[-1]
        with open(seg, "wb") as f:
            f.write(b"fake-seg")
        concat = [c for c in cmd if c == "concat"]
        assert not concat  # segment cmds only; concat handled below
        return mock.Mock(returncode=0, stdout="", stderr="")

    def concat_call(self, cmd, timeout=300):
        self.cmds.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"fake-final")
        result = mock.Mock()
        result.check_returncode = lambda: 0
        return result


def _assemble_vfs(aspect):
    from test_phase_c import _fake_media, _two_scenes
    tmp = tempfile.mkdtemp(prefix="codex-assemble-")
    scenes = _two_scenes()
    imgs, auds = _fake_media(tmp, scenes)
    fake = _FakeRun()
    real_run = render._run

    def dispatch(cmd, timeout=300):
        if cmd[1:3] == ["-y", "-f"]:
            return fake.concat_call(cmd, timeout)
        return fake(cmd, timeout)

    with mock.patch.object(render, "media_duration", return_value=2.0), \
            mock.patch.object(render, "_run", side_effect=dispatch):
        render.assemble(scenes, imgs, auds, "Topic", tmp,
                        aspect_ratio=aspect)
    seg_cmds = [c for c in fake.cmds if "seg_" in c[-1]]
    assert len(seg_cmds) == 2, fake.cmds
    return [c[c.index("-vf") + 1] for c in seg_cmds]


def check_assemble_crops_never_stretches():
    for aspect, dims in (("9:16", "720:1280"), ("16:9", "1280:720")):
        for vf in _assemble_vfs(aspect):
            assert "scale=%s" % dims in vf, (aspect, vf)
            assert "crop=" in vf, (
                "segment stretches instead of center-cropping: %r" % (vf,))
            assert "force_style" in vf, (aspect, vf)
    print("MOCKED PASS: assemble center-crops then scales "
          "(no stretch), exact dims per format")


def check_qc_dims_still_gated():
    from test_phase_c import _qc_with_dims
    for aspect, (w, h) in (("9:16", (720, 1280)), ("16:9", (1280, 720))):
        report = _qc_with_dims(w, h, aspect)
        assert report["expected_dimensions"] == "%dx%d" % (w, h), report
        assert report["aspect_ratio"] == aspect, report
    print("MOCKED PASS: QC still enforces exact output dimensions")


def _with_pasted_key_only(fn):
    """Run fn with only the in-memory pasted key (no environment key)."""
    old_env = os.environ.pop("META_API_KEY", None)
    old_mem = console_app._MEMORY_KEY["key"]
    console_app._MEMORY_KEY["key"] = FAKE_KEY
    try:
        return fn()
    finally:
        if old_env is not None:
            os.environ["META_API_KEY"] = old_env
        console_app._MEMORY_KEY["key"] = old_mem


def _fake_chat_factory(seen):
    def fake_chat(prompt, model=None, max_tokens=512, api_key=None):
        seen.append(api_key)
        headers = meta_backend._headers(api_key)
        assert FAKE_KEY in headers["Authorization"], headers
        return "Alpha line.\nBeta line.\nGamma line.\nDelta line.\nEpsilon line."
    return fake_chat


def check_pasted_key_script_generation():
    seen = []
    with mock.patch.object(meta_backend, "chat",
                           side_effect=_fake_chat_factory(seen)):
        lines, note = _with_pasted_key_only(
            lambda: console_app._live_script_lines("Photosynthesis"))
    assert len(lines) == 5, lines
    assert seen and all(k == FAKE_KEY for k in seen), seen
    print("MOCKED PASS: pasted in-memory key drives script generation "
          "with no environment key")


def check_pasted_key_critique_and_revise():
    import director
    seen = []

    def fake_chat(prompt, model=None, max_tokens=512, api_key=None):
        seen.append(api_key)
        meta_backend._headers(api_key)  # must not raise: key present
        if "NARRATION:" in prompt or "revising ONE scene" in prompt:
            return "NARRATION: Revised line.\nVISUAL: revised visual"
        return "[]"

    with mock.patch.object(meta_backend, "chat", side_effect=fake_chat):
        def run():
            brief = director.make_brief("Photosynthesis")
            scenes = [{"id": "scene-1", "narration": "Alpha line.",
                       "visual_prompt": "valley river"}]
            raw = console_app._spark_critique_raw(brief, scenes)
            assert raw == "[]", raw
            new_n, new_v = console_app._spark_revise_scene(
                {"narration": "Alpha line.",
                 "visual_prompt": "valley river"},
                [{"criterion": "hook", "detail": "weak hook"}], brief)
            assert new_n and new_v, (new_n, new_v)
            return True
        _with_pasted_key_only(run)
    assert seen and all(k == FAKE_KEY for k in seen), seen
    print("MOCKED PASS: pasted in-memory key drives critique + "
          "scene-revision paths")


def check_no_key_fails_clearly():
    old_env = os.environ.pop("META_API_KEY", None)
    old_mem = console_app._MEMORY_KEY["key"]
    console_app._MEMORY_KEY["key"] = None
    try:
        try:
            console_app._live_script_lines("Photosynthesis")
        except RuntimeError as e:
            assert "META_API_KEY" in str(e) or "key" in str(e).lower(), e
        else:
            raise AssertionError(
                "script generation without any key did not fail")
    finally:
        if old_env is not None:
            os.environ["META_API_KEY"] = old_env
        console_app._MEMORY_KEY["key"] = old_mem
    print("MOCKED PASS: no key fails with a clear error, never silently")


def main():
    check_vertical_requests_vertical()
    check_wide_requests_wide()
    check_unknown_aspect_rejected()
    check_assemble_crops_never_stretches()
    check_qc_dims_still_gated()
    check_pasted_key_script_generation()
    check_pasted_key_critique_and_revise()
    check_no_key_fails_clearly()
    print("MOCKED PASS: all Codex follow-up checks")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
