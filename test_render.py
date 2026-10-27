"""Integration test for render.py (needs network: Pollinations, gTTS).

Renders a 2-scene video from fixed lines and asserts:
  - QC passes (duration > 0, 1280x720, audio present, audible)
  - each scene's segment subtitle file carries ONLY its own caption
Run: python test_render.py [out_dir]
"""

import os
import subprocess
import sys

import render

SCENES = [
    {"narration": "Alpha robots learn by watching.",
     "visual": "flat vector illustration, small robot watching a screen"},
    {"narration": "Beta robots practice until they improve.",
     "visual": "flat vector illustration, small robot practicing with blocks"},
]


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "test-render-out"
    final, qc = render.run_pipeline(SCENES, "Caption fix test", out_dir)
    assert qc["passed"], "QC failed: %r" % (qc,)
    assert qc["video"] == "1280x720 h264", qc
    assert qc["audio"], qc
    # Caption fix proof: each segment SRT must hold only its own line.
    for i, scene in enumerate(SCENES):
        srt_path = os.path.join(out_dir, "scene_%d.srt" % i)
        with open(srt_path, encoding="utf-8") as f:
            srt = f.read()
        assert scene["narration"] in srt, "scene %d caption missing" % i
        for j, other in enumerate(SCENES):
            if j != i:
                assert other["narration"] not in srt, (
                    "scene %d SRT leaked scene %d caption" % (i, j))
    print("PASS: %s (%.1fs, %s, audio %s, mean %.1f dB)" % (
        final, qc["duration"], qc["video"], qc["audio"],
        qc["mean_volume_db"]))


if __name__ == "__main__":
    main()
