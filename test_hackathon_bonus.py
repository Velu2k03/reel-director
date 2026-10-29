"""Hackathon bonus-evidence tests: aiming, never won (MOCKED).

Every test here is deterministic and mocked: no META_API_KEY, no
network. Image generation, SAM, voice, and ffmpeg probing are
stubbed with unittest.mock. Lines that say MOCKED PASS are this
suite; live proof still comes from the real runs named in the
README, never from anything here.

Rule under test (the brief's bonus clause):
  - A decision-log entry exists for every regen and retry in a
    mocked run (image retry, voice retry, SAM zero-match regen,
    Muse Image 503 retry), and no entry is invented when the
    first attempt succeeds.
  - A Muse Image content-policy block raises the actionable
    message naming the fix, not the raw HTTP error.
  - A Wonder change note exists when Wonder changes the script
    and is absent ("") when it does not.
  - One consistent style block is used across all scenes in a
    mocked job (identical block in every scene image prompt).
  - README and the Director panel say aiming, not won: the bonus
    section lists Agentic/Creative as aiming, and Open Weights /
    Most Viral / Community Vote as NOT claimed, with no win
    claimed anywhere.

This suite never asserts a bonus is won.

Run: python test_hackathon_bonus.py
"""

import base64
import os
import sys
import tempfile
from unittest import mock

import numpy as np

import app
import meta_backend
import meta_image
import meta_sam
import render

HERE = os.path.dirname(os.path.abspath(__file__))


def _scene(i=0, style="watercolor", color="ocean"):
    return {"id": "scene-%d" % (i + 1),
            "narration": "Narration line %d about rivers." % (i + 1),
            "visual": "flat vector illustration, river valley %d" % (i + 1),
            "visual_prompt":
                "flat vector illustration, river valley %d" % (i + 1),
            "subject": "river valley %d" % (i + 1),
            "style": style,
            "color": color}


def check_image_retry_logged():
    tmp = tempfile.mkdtemp(prefix="bonus-image-")
    calls = {"n": 0}

    def _flaky(prompt, path, key, aspect_ratio=None, decisions=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("mocked image 500")
        with open(path, "wb") as fh:
            fh.write(b"fake-png-bytes")

    decisions = []
    with mock.patch.object(render.meta_image, "generate_image",
                           side_effect=_flaky):
        paths, backend = render.generate_images(
            [_scene()], tmp, "fake-key", decisions=decisions,
            aspect_ratio="9:16")
    assert backend == "meta", backend
    assert len(paths) == 1 and os.path.getsize(paths[0]) > 0, paths
    assert len(decisions) == 1, decisions
    entry = decisions[0]
    assert entry.get("retried") is True, entry
    assert "attempt 1 failed" in entry["decision"], entry
    assert "attempt 2" in entry["decision"], entry
    print("MOCKED PASS: image retry writes one decision-log entry "
          "with the real reason (attempt 1 failed, attempt 2 ok)")


def check_image_first_try_logs_nothing():
    tmp = tempfile.mkdtemp(prefix="bonus-image-clean-")

    def _ok(prompt, path, key, aspect_ratio=None, decisions=None):
        with open(path, "wb") as fh:
            fh.write(b"fake-png-bytes")

    decisions = []
    with mock.patch.object(render.meta_image, "generate_image",
                           side_effect=_ok):
        render.generate_images([_scene()], tmp, "fake-key",
                               decisions=decisions, aspect_ratio="9:16")
    assert decisions == [], decisions
    print("MOCKED PASS: first-try image success invents no "
          "decision-log entry")


def check_voice_retry_logged():
    tmp = tempfile.mkdtemp(prefix="bonus-voice-")
    calls = {"n": 0}

    class _Wave:
        def __init__(self, arr):
            self._arr = arr

        def cpu(self):
            return self

        def numpy(self):
            return self._arr

    class _Out:
        def __init__(self, arr):
            self.waveform = [_Wave(arr)]

    class _FakeModel:
        config = type("Config", (), {"sampling_rate": 16000})()

        def __call__(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Out(np.zeros(160, dtype=np.float32))
            return _Out(np.ones(160, dtype=np.float32) * 0.5)

    def _fake_tokenizer(text, return_tensors=None):
        return {}

    decisions = []
    with mock.patch.object(render, "_load_voice",
                           return_value=(_fake_tokenizer,
                                         _FakeModel())):
        paths = render.synthesize_voice([_scene()], tmp,
                                        decisions=decisions)
    assert len(paths) == 1 and os.path.getsize(paths[0]) > 0, paths
    assert len(decisions) == 1, decisions
    entry = decisions[0]
    assert entry.get("retried") is True, entry
    assert "voice attempt 1 failed" in entry["decision"], entry
    assert "silence" in entry["decision"], entry
    assert render.VOICE_MODEL_ID in entry["decision"], entry
    print("MOCKED PASS: voice retry on silence writes one "
          "decision-log entry with the real reason")


def check_image_503_retried_once_and_logged():
    tmp = tempfile.mkdtemp(prefix="bonus-503-")

    class _Resp503:
        status_code = 503
        text = "Service Unavailable"

    class _Resp200:
        status_code = 200
        text = "ok"

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(
                b"fake-png-bytes").decode()}]}

    attempts = {"n": 0}

    def _flaky_post(url, headers=None, json=None, timeout=None):
        attempts["n"] += 1
        return _Resp503() if attempts["n"] == 1 else _Resp200()

    decisions = []
    with mock.patch.object(meta_image.requests, "post",
                           side_effect=_flaky_post), \
            mock.patch.object(meta_image.time, "sleep") as nap:
        path = os.path.join(tmp, "scene_0.png")
        meta_image.generate_image("a river valley", path, "fake-key",
                                  aspect_ratio="9:16",
                                  decisions=decisions)
    assert attempts["n"] == 2, attempts
    assert nap.call_count == 1, nap
    assert os.path.getsize(path) > 0, path
    assert len(decisions) == 1, decisions
    assert decisions[0].get("retried") is True, decisions
    assert "503" in decisions[0]["decision"], decisions
    # A stubborn 503 still fails hard after the single retry, with
    # the retry recorded.
    decisions2 = []

    def _down_post(url, headers=None, json=None, timeout=None):
        return _Resp503()

    with mock.patch.object(meta_image.requests, "post",
                           side_effect=_down_post), \
            mock.patch.object(meta_image.time, "sleep"):
        try:
            meta_image.generate_image(
                "a river valley", os.path.join(tmp, "x.png"),
                "fake-key", aspect_ratio="9:16", decisions=decisions2)
        except RuntimeError as e:
            assert "Muse Image HTTP 503" in str(e), e
        else:
            raise AssertionError("stubborn 503 did not raise")
    assert len(decisions2) == 1 and decisions2[0].get("retried"), \
        decisions2
    print("MOCKED PASS: Muse Image 503 retries once after backoff "
          "and logs one decision entry; a stubborn 503 fails hard")


def check_image_policy_block_message():
    tmp = tempfile.mkdtemp(prefix="bonus-policy-")

    class _RespPolicy:
        status_code = 400
        text = ('{"error": {"code": "content_policy_violation", '
                '"message": "blocked"}}')

    with mock.patch.object(meta_image.requests, "post",
                           return_value=_RespPolicy()):
        try:
            meta_image.generate_image(
                "a blocked prompt", os.path.join(tmp, "x.png"),
                "fake-key", aspect_ratio="9:16")
        except RuntimeError as e:
            assert str(e) == (
                "Muse Image blocked this prompt (content policy): "
                "edit the visual and regenerate this scene."), e
        else:
            raise AssertionError("policy block did not raise")

    class _Resp400:
        status_code = 400
        text = '{"error": "bad request"}'

    with mock.patch.object(meta_image.requests, "post",
                           return_value=_Resp400()):
        try:
            meta_image.generate_image(
                "a bad prompt", os.path.join(tmp, "y.png"),
                "fake-key", aspect_ratio="9:16")
        except RuntimeError as e:
            assert "Muse Image HTTP 400" in str(e), e
        else:
            raise AssertionError("plain 400 did not raise")
    print("MOCKED PASS: content_policy_violation raises the "
          "actionable blocked-prompt message; other 400s keep the "
          "raw HTTP error")


def check_sam_zero_match_regen_logged():
    scene = _scene()
    first = {"subject": scene["visual_prompt"], "concept": "valley",
             "matches": 0, "boxes": [], "masks": 0,
             "warning": "SAM 3.1 found zero matches for concept "
                        "'valley'; continuing"}
    second = dict(first)
    decisions = []
    with mock.patch.object(meta_sam, "sam_segment",
                           side_effect=[first, second]) as seg, \
         mock.patch.object(render.meta_image, "generate_image",
                           return_value=None) as gen:
        results = render.sam_check([scene], ["img0.png"], "fake-key",
                                   decisions=decisions)
    assert seg.call_count == 2, seg.call_count
    assert gen.call_count == 1, gen.call_count
    assert len(results) == 1 and results[0].get("matches") == 0, results
    regen_notes = [d for d in decisions
                   if d.get("retried") and "regenerated" in d["decision"]]
    assert regen_notes, decisions
    assert "0 matches" in regen_notes[0]["decision"], regen_notes[0]
    print("MOCKED PASS: SAM zero-match regen writes one decision-log "
          "entry with the real reason (gen x1, sam_segment x2)")


def check_wonder_note_present_when_changed():
    revised = ("Rivers carve valleys over ages.\n"
               "Rain feeds every stream.\n"
               "CHANGE-NOTE: tightened the hook and split one long line.")
    with mock.patch.object(meta_backend, "chat",
                           return_value=revised):
        final, note = meta_backend.revise_with_note(
            "draft", "How rivers carve valleys", scene_count=2)
    assert "tightened the hook" in note, (final, note)
    assert "CHANGE-NOTE" not in final, (final, note)
    print("MOCKED PASS: Wonder change note exists when Wonder "
          "changes the script (%r)" % (note,))


def check_wonder_note_absent_when_unchanged():
    same = "Rivers carve valleys over ages.\nRain feeds every stream."
    with mock.patch.object(meta_backend, "chat", return_value=same):
        final, note = meta_backend.revise_with_note(
            "draft", "How rivers carve valleys", scene_count=2)
    assert note == "", (final, note)
    print("MOCKED PASS: Wonder change note is absent when Wonder "
          "does not change the script (no silent pass)")


def check_style_block_identical_across_scenes():
    lines = ["Rivers carve valleys over ages.",
             "Rain feeds every stream.",
             "Valleys widen every century."]
    records = app.build_scene_records(lines, style="watercolor",
                                      color="ocean")
    assert len(records) == 3, records
    prompts = [render._meta_prompt(rec, aspect_ratio="9:16")
               for rec in records]
    want_style, _ = render.resolve_style_block("watercolor")
    want_color, _ = render.resolve_color_block("ocean")
    for prompt in prompts:
        assert want_style in prompt, prompt
        assert want_color in prompt, prompt
    print("MOCKED PASS: one consistent style block across all 3 "
          "scene prompts (Watercolor + ocean, identical blocks)")


def check_readme_bonus_aims_not_won():
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    low = text.lower()
    assert "aiming, not won" in low, "bonus section must say aiming, not won"
    assert "no bonus is claimed as won" in low, \
        "README must disclaim every bonus win"
    for aim in ("best agentic use-case: aiming",
                "best creative build: aiming"):
        assert aim in low, "README must list %r" % (aim,)
    for name in ("best build on meta open weights: not claimed",
                 "most viral project: not claimed",
                 "community vote: not claimed"):
        assert name in low, "README must list %r" % (name,)
    assert "no open weights eligibility is claimed" in low, \
        "Open Weights must be explicitly disclaimed"
    for claim in ("we won", "won the bonus", "prize expected",
                  "expect to win", "testimonial", "streamlit", "chatbot",
                  "baseten", "fireworks"):
        assert claim not in low, "README must not contain %r" % (claim,)
    print("MOCKED PASS: README bonus aims say aiming, never won; "
          "Open Weights/Viral/Community explicitly not claimed")


def check_director_panel_shows_real_decisions():
    with open(os.path.join(HERE, "ui", "index.html"),
              encoding="utf-8") as fh:
        html = fh.read()
    if 'src="/scenes/studio/studio.js"' in html:
        with open(os.path.join(HERE, "ui", "scenes", "studio", "studio.js"),
                  encoding="utf-8") as fh:
            html += "\n" + fh.read()
    for marker in ("Approval gate", "Script revise note",
                   "QC passed", "QC failed", "qc.decisions",
                   "revision_note"):
        assert marker in html, "Director panel missing %r" % (marker,)
    print("MOCKED PASS: Director panel renders approval gate, "
          "revise note, QC verdict, and the real qc.decisions list")


def main():
    check_image_retry_logged()
    check_image_first_try_logs_nothing()
    check_image_503_retried_once_and_logged()
    check_image_policy_block_message()
    check_voice_retry_logged()
    check_sam_zero_match_regen_logged()
    check_wonder_note_present_when_changed()
    check_wonder_note_absent_when_unchanged()
    check_style_block_identical_across_scenes()
    check_readme_bonus_aims_not_won()
    check_director_panel_shows_real_decisions()
    print("MOCKED ALL PASS: 11 hackathon bonus-evidence checks. "
          "No bonus is asserted as won; live evidence still comes "
          "from real runs only.")


if __name__ == "__main__":
    main()
