"""Strict Meta-only render test: 2 scenes, live backends only.

Needs META_API_KEY. Renders 2 scenes and asserts:
  - QC passes (duration > 0, 1280x720, audio present, audible)
  - NO burned-in captions: no SRT file is written for any scene and
    the finished file carries no subtitle stream (owner decision
    Oct 27: captions removed because the burned-in output was not
    good)
  - the QC report holds real SAM results (matches + boxes per scene)
  - every recorded backend is Meta (image muse-image-1.0, sam-3.1,
    voice facebook/mms-tts-eng)
  - no banned non-Meta backend string remains in render.py, app.py,
    meta_backend.py, meta_image.py, meta_sam.py, or requirements.txt
    (this file is the only allowed exception: it asserts absence)

Without META_API_KEY live mode is not available, so this test fails
with a clear error instead of rendering anything fake.
Run: python test_render.py [out_dir]
"""

import os
import sys

BANNED = ("pollinations", "edge-tts", "edge_tts", "gtts",
          "whisper", "piper")
BAN_FILES = ("render.py", "app.py", "meta_backend.py", "meta_image.py",
             "meta_sam.py", "requirements.txt")

SCENES = [
    {"narration": "Alpha robots learn by watching.",
     "visual": "flat vector illustration, small robot watching a screen",
     "subject": "small robot watching a screen"},
    {"narration": "Beta robots practice until they improve.",
     "visual": "flat vector illustration, small robot practicing with blocks",
     "subject": "small robot practicing with blocks"},
]


def check_bans():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in BAN_FILES:
        with open(os.path.join(here, name), encoding="utf-8") as f:
            text = f.read()
        for banned in BANNED:
            assert banned not in text.lower(), (
                "%s still references banned backend %r" % (name, banned))
    print("BANS PASS: no banned backend string in %s" % (", ".join(BAN_FILES),))


def check_style():
    """Every image prompt shares one META_STYLE path (_meta_prompt),
    so the style block, robot-director character, and colours are
    identical per scene. Needs no key."""
    import inspect

    import render

    assert render.META_STYLE and "robot" in render.META_STYLE, render.META_STYLE
    src = inspect.getsource(render.generate_images)
    assert "_meta_prompt" in src, "generate_images bypasses _meta_prompt"
    try:
        regen_src = inspect.getsource(render._closeup_prompt)
    except AttributeError:
        regen_src = ""
    assert "_meta_prompt" in regen_src, "SAM retry bypasses _meta_prompt"
    prompts = [render._meta_prompt(s) for s in SCENES]
    for p in prompts:
        assert p.startswith(render.META_STYLE), p
        assert "#1877F2" in p and "robot" in p, p
    head = prompts[0][:len(render.META_STYLE)]
    assert all(p.startswith(head) for p in prompts), prompts
    print("STYLE PASS: all image prompts share one META_STYLE path")


def run_format(render, api_key, aspect, out_dir, expect_video):
    final, qc = render.run_pipeline(SCENES, "Caption fix test", out_dir,
                                    api_key=api_key,
                                    aspect_ratio=aspect)
    assert qc["passed"], "QC failed: %r" % (qc,)
    assert qc["aspect_ratio"] == aspect, qc
    assert qc["expected_dimensions"] == expect_video.split(" ")[0], qc
    assert qc["video"] == expect_video, qc
    assert qc["audio"], qc
    backends = qc.get("backends") or {}
    assert qc.get("image_backend") == "meta", qc
    assert backends.get("image") == "muse-image-1.0", qc
    assert backends.get("sam") == "sam-3.1", qc
    assert "mms-tts" in backends.get("voice", ""), qc
    assert "spark" in backends.get("script", "").lower(), qc
    assert "wonder" in backends.get("script", "").lower(), qc
    decisions = qc.get("decisions")
    assert isinstance(decisions, list) and decisions, qc
    for entry in decisions:
        assert isinstance(entry, dict) and entry.get("decision"), qc
    sam = qc.get("sam")
    assert isinstance(sam, list) and len(sam) == len(SCENES), qc
    for i, entry in enumerate(sam):
        assert entry.get("scene") == i, qc
        assert isinstance(entry.get("matches"), int), qc
        assert isinstance(entry.get("boxes"), list), qc
        assert isinstance(entry.get("concept"), str) and entry["concept"].strip(), qc
        assert isinstance(entry.get("subject"), str) and entry["subject"].strip(), qc
    # No-caption proof: no SRT file on disk for any scene, and no
    # subtitle stream in the finished file.
    leftovers = [n for n in os.listdir(out_dir)
                 if n.endswith(".srt")]
    assert leftovers == [], "SRT files written: %r" % (leftovers,)
    probe = render._ffprobe(final)
    kinds = [s.get("codec_type") for s in probe.get("streams", [])]
    assert "subtitle" not in kinds, "subtitle stream burned in: %r" % (kinds,)
    assert "captions" not in qc, qc
    assert "caption_error" not in qc, qc
    total_matches = sum(e["matches"] for e in sam)
    print("PASS: %s (%.1fs, %s, audio %s, mean %.1f dB, "
          "SAM matches %d over %d scenes)" % (
              final, qc["duration"], qc["video"], qc["audio"],
              qc["mean_volume_db"], total_matches, len(sam)))
    return final, qc


def main():
    check_bans()
    check_style()
    api_key = os.environ.get("META_API_KEY", "").strip()
    if not api_key:
        print("LIVE FAIL: META_API_KEY is not set in the environment. "
              "Live mode is not available; refusing a fake render.",
              file=sys.stderr)
        raise SystemExit(1)
    import render

    base = sys.argv[1] if len(sys.argv) > 1 else "test-render-out"
    for aspect, expect in (("9:16", "720x1280 h264"),
                          ("16:9", "1280x720 h264")):
        out_dir = "%s-%s" % (base, aspect.replace(":", "x"))
        run_format(render, api_key, aspect, out_dir, expect)
    print("LIVE ALL PASS: 2-scene render in 9:16 and 16:9, QC dims match.")


if __name__ == "__main__":
    main()
