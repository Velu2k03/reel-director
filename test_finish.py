"""Finish-brief Item 1 tests: SAM concept filtering, the real fix (MOCKED).

Every test here is deterministic and mocked: no META_API_KEY, no
network. The SAM API, image generation, and ffmpeg probing are
stubbed with unittest.mock. Lines that say MOCKED PASS are this
suite; live SAM evidence still comes from test_sam.py live and the
live workspace job, never from anything here.

Rule under test (same rule in all three paths: meta_sam.py,
director.derive_sam_concept, render.sam_check):
  - The SAM concept comes only from a segmentable visual subject: a
    concrete noun from the scene visual prompt or subject.
  - Never from narration, never an adjective, verb, abstract noun,
    or generic word. Rejected examples: hook, brilliant, rain as
    weather, beautiful, powerful, quickly, water as a mass without
    a subject. Accepted examples (when the image subject): robot,
    mountain, tree, leaf, riverbank, cloud.
  - No usable concept -> the SAM check is recorded as skipped with
    a clear reason, the image is NOT regenerated, and the job does
    not fail for this.
  - A usable concept with zero matches stays a warning, never a
    QC failure. A SAM API error still fails hard.

Run: python test_finish.py
"""

import os
import sys
import tempfile
from unittest import mock

import director
import meta_sam
import render

REJECT_ADJECTIVES = ("brilliant", "beautiful", "powerful", "quickly")
REJECT_ABSTRACT = ("hook", "hooks")
REJECT_STOPWORDS = ("in", "carried", "thousand")
REJECT_MASS = ("rain", "water")
ACCEPT_CONCRETE = {
    "robot": "robot",
    "mountain": "mountain",
    "tree": "tree",
    "leaf": "leaf",
    "riverbank": "riverbank",
    "cloud": "cloud",
}


def _expect_reject(word):
    for fn_name, fn in (("concept_for_subject",
                         meta_sam.concept_for_subject),
                        ("derive_sam_concept", None)):
        if fn is None:
            try:
                director.derive_sam_concept(
                    {"id": "s1",
                     "visual_prompt":
                         "flat vector illustration, " + word})
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "derive_sam_concept accepted %r" % (word,))
        else:
            try:
                fn(word)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "%s accepted %r" % (fn_name, word))


def check_adjectives_rejected():
    for word in REJECT_ADJECTIVES:
        _expect_reject(word)
    print("MOCKED PASS: adjectives rejected as SAM concepts "
          "(%s)" % (", ".join(REJECT_ADJECTIVES),))


def check_narration_stopwords_rejected():
    for word in REJECT_STOPWORDS:
        _expect_reject(word)
    print("MOCKED PASS: narration stopwords rejected as SAM concepts "
          "(%s)" % (", ".join(REJECT_STOPWORDS),))


def check_abstract_nouns_rejected():
    for word in REJECT_ABSTRACT:
        _expect_reject(word)
    # A bare abstract prompt with only boilerplate must also fail.
    try:
        director.derive_sam_concept(
            {"id": "s1",
             "visual_prompt": "flat vector illustration, hook"})
    except ValueError:
        pass
    else:
        raise AssertionError(
            "derive_sam_concept accepted a hook-only visual prompt")
    print("MOCKED PASS: abstract nouns rejected as SAM concepts "
          "(%s)" % (", ".join(REJECT_ABSTRACT),))


def check_mass_nouns_rejected():
    for word in REJECT_MASS:
        _expect_reject(word)
    print("MOCKED PASS: mass nouns rejected as SAM concepts "
          "(%s)" % (", ".join(REJECT_MASS),))


def check_concrete_subjects_accepted():
    for word, want in ACCEPT_CONCRETE.items():
        got = meta_sam.concept_for_subject(word)
        assert got == want, (word, got, want)
        derived = director.derive_sam_concept(
            {"id": "s1",
             "visual_prompt": "flat vector illustration, small "
                              + word + " in a scene"})
        assert derived["concept"] == want, (word, derived)
    # The concept follows the visual subject even when the
    # narration names something else (river narration, robot art).
    derived = director.derive_sam_concept(
        {"id": "s1",
         "narration": "Rivers flow quickly through the valley.",
         "visual_prompt":
             "flat vector illustration, small robot watching a screen",
         "subject": "small robot watching a screen"})
    assert derived["concept"] == "robot", derived
    print("MOCKED PASS: concrete subjects accepted "
          "(%s), visual beats narration" % (", ".join(ACCEPT_CONCRETE),))


def check_live_adjective_regression():
    # Live 16:9 job 009c8ce76531 derived concept "giant" from the
    # visual "flat vector illustration of a giant valley" (an
    # adjective as concept, 0 SAM matches). The filter must now
    # skip the size adjective and take the landform noun.
    assert meta_sam.concept_for_subject(
        "flat vector illustration of a giant valley") == "valley"
    assert meta_sam.concept_for_subject(
        "flat vector canyon cross-section showing rock layers") \
        == "canyon"
    derived = director.derive_sam_concept(
        {"id": "scene-1",
         "visual_prompt":
             "flat vector illustration of a giant valley"})
    assert derived["concept"] == "valley", derived
    print("MOCKED PASS: live adjective regression fixed "
          "(giant valley -> valley, canyon kept)")


def _scene(visual):
    return {"id": "scene-1", "narration": "Narration line one.",
            "visual": visual, "visual_prompt": visual,
            "subject": visual}


def check_no_concept_skips_without_regen():
    scene = _scene("flat vector illustration, brilliant hook")
    with mock.patch.object(meta_sam, "sam_segment") as seg, \
         mock.patch.object(render.meta_image, "generate_image") as gen:
        results = render.sam_check([scene], ["img0.png"], "fake-key",
                                   decisions=[])
        assert seg.call_count == 0, seg.call_count
        assert gen.call_count == 0, gen.call_count
    assert len(results) == 1, results
    result = results[0]
    assert result.get("skipped") is True, result
    assert result.get("matches") == 0, result
    assert "skipp" in str(result.get("warning", "")).lower(), result
    assert "regen" in str(result.get("warning", "")).lower(), result
    print("MOCKED PASS: no usable concept skips SAM without regen "
          "(sam_segment x0, generate_image x0)")


def check_zero_matches_warns_without_failing_qc():
    scene = _scene("flat vector illustration, small robot")
    first = {"subject": scene["visual_prompt"], "concept": "robot",
             "matches": 0, "boxes": [], "masks": 0,
             "warning": "SAM 3.1 found zero matches for concept "
                        "'robot'; continuing"}
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
    assert len(results) == 1, results
    result = results[0]
    assert result.get("matches") == 0, result
    assert result.get("warning"), result
    assert result.get("skipped") is not True, result
    # Zero matches is a warning, never a QC failure on its own:
    # with a technically valid file and good captions, QC passes.
    tmp = tempfile.mkdtemp(prefix="finish-qc-")
    try:
        final = os.path.join(tmp, "final.mp4")
        with open(final, "wb") as f:
            f.write(b"fake-video-bytes")
        probe = {"format": {"duration": "3.0"},
                 "streams": [{"codec_type": "video", "codec_name": "h264",
                              "width": 1280, "height": 720},
                             {"codec_type": "audio",
                              "codec_name": "aac"}]}
        captions = [{"scene": 0, "srt": "scene_0.srt",
                     "has_own_caption": True, "clean": True}]

        def _fake_run(cmd, timeout=300):
            class _R:
                returncode = 0
                stderr = ("[Parsed_volumedetect] mean_volume: "
                          "-20.0 dB")
                stdout = ""
            return _R()

        with mock.patch.object(render, "_ffprobe",
                               return_value=probe), \
             mock.patch.object(render, "_run",
                               side_effect=_fake_run):
            report = render.qc_report(
                final, sam_results=[result],
                captions=captions, captions_ok=True,
                aspect_ratio="16:9")
        assert report["passed"] is True, report
        assert report["sam"][0]["matches"] == 0, report
        assert report["sam"][0]["warning"], report
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("MOCKED PASS: usable concept with zero matches keeps a "
          "warning and QC still passes (1280x720)")


def check_sam_api_error_fails_hard():
    scene = _scene("flat vector illustration, small robot")
    with mock.patch.object(
            meta_sam, "sam_segment",
            side_effect=RuntimeError(
                "SAM 3.1 API call failed: mocked down")) as seg, \
         mock.patch.object(render.meta_image, "generate_image") as gen:
        try:
            render.sam_check([scene], ["img0.png"], "fake-key",
                             decisions=[])
        except RuntimeError as e:
            assert "mocked down" in str(e), str(e)
        else:
            raise AssertionError("SAM API error did not fail hard")
        assert seg.call_count == 1, seg.call_count
        assert gen.call_count == 0, gen.call_count
    print("MOCKED PASS: SAM API error still fails hard, no regen")


def main():
    check_adjectives_rejected()
    check_narration_stopwords_rejected()
    check_abstract_nouns_rejected()
    check_mass_nouns_rejected()
    check_concrete_subjects_accepted()
    check_live_adjective_regression()
    check_no_concept_skips_without_regen()
    check_zero_matches_warns_without_failing_qc()
    check_sam_api_error_fails_hard()
    print("MOCKED ALL PASS: 9 finish-brief Item 1 checks. Live SAM "
          "evidence still required: test_sam.py live plus the live "
          "16:9 workspace job.")


if __name__ == "__main__":
    sys.exit(main())
