"""Small test for the SAM 3.1 client (meta_sam.py).

Always runs (no key needed):
  - decode_output on a real SAM 3 protocol line via meta-sam-parser:
    box records and coordinates must come out intact.
  - empty output must raise instead of passing silently.
  - concept_for_subject reduces descriptions to a bare concept.

Live check (needs META_API_KEY): segments a real scene image through
the hosted sam-3.1 model over the streaming path (AsyncOpenAI,
stream=True, one_bit metadata, message-type input) and requires at
least 1 match with a real box. Without a key the live part is
reported and skipped; the decode assertions above still run.
Run: python test_sam.py [image_path] [concept]
Defaults: ui/scenes/scene1.jpg, concept "robot".
"""

import os
import sys

from meta_sam_parser import CompletedOutcome  # noqa: F401 (protocol check)

from meta_sam import concept_for_subject, decode_output

SAMPLE_LINE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>\n"
)


def main():
    decoded = decode_output(SAMPLE_LINE)
    assert decoded["matches"] == 2, decoded
    # meta-sam-parser reports half-open boxes: right/bottom are x2+1/y2+1.
    assert decoded["boxes"][0] == [211, 228, 271, 255], decoded
    assert decoded["boxes"][1] == [155, 228, 203, 255], decoded
    print("DECODE PASS: %d matches, boxes %r"
          % (decoded["matches"], decoded["boxes"]))
    try:
        decode_output("   \n")
    except RuntimeError:
        print("EMPTY PASS: empty SAM output raises")
    else:
        raise AssertionError("empty SAM output did not raise")

    assert concept_for_subject("small robot watching a screen") == "robot"
    assert concept_for_subject(
        "flat vector illustration, small robot practicing") == "robot"
    assert concept_for_subject("robot") == "robot"
    print("CONCEPT PASS: descriptions reduce to a bare concept")

    here = os.path.dirname(os.path.abspath(__file__))
    default_image = os.path.join(here, "ui", "scenes", "scene1.jpg")
    image = sys.argv[1] if len(sys.argv) > 1 else default_image
    concept = sys.argv[2] if len(sys.argv) > 2 else "robot"
    key = os.environ.get("META_API_KEY", "").strip()
    if not key:
        print("LIVE SKIP: needs META_API_KEY; "
              "decode assertions above passed.")
        return
    from meta_sam import sam_segment

    result = sam_segment(image, concept, key)
    assert isinstance(result["matches"], int), result
    assert isinstance(result["boxes"], list), result
    assert result.get("concept") == concept, result
    assert result["matches"] >= 1, (
        "live SAM returned zero matches: %r" % (result,))
    assert all(len(b) == 4 for b in result["boxes"]), result
    print("LIVE PASS: matches=%d boxes=%r concept=%r warning=%r"
          % (result["matches"], result["boxes"],
             result["concept"], result["warning"]))


if __name__ == "__main__":
    main()
