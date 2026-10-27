"""SAM 3.1 scene check via the hosted Meta Model API (strict Meta-only).

Documented streaming shape (verified live Oct 27 against the same
scene image the old non-streaming call returned empty for):
  AsyncOpenAI pointed at https://api.meta.ai/v1, model sam-3.1,
  responses.create with stream=True,
  metadata {"mask_encoding": "one_bit"},
  input as a message item:
    [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": <bare concept>},
        {"type": "input_image", "image_url": <data URL with the
            correct mime for the file>}]}]
  parsed with meta-sam-parser parse_responses_stream +
  image_segmentation_format, awaited via final_result.

The old shape (synchronous client, non-stream responses.create,
{"role": "user", "content": [...]} input item, a sentence prompt,
data URL always labelled image/png) completed with an empty output
lane, so it is not used here.

The text prompt must be a bare concept ("robot"), not a sentence.
Callers pass a description, so concept_for_subject reduces it to one
short concept; both the original subject and the concept used are
returned so the reduction stays visible in the QC report.

Rules, per the strict brief:
- If the SAM API call itself errors, raise with the real error so
  the job fails. No substitute, no silent pass.
- A completed response with zero records is a warning, not an
  error: recorded as matches=0 with a warning, never turned into a
  fake match.
"""

import asyncio
import base64
import mimetypes
import os

from openai import AsyncOpenAI

from meta_sam_parser import (
    CompletedOutcome,
    ResponsesStreamLaneError,
    image_segmentation_format,
    parse_responses_stream,
)

MODEL = "sam-3.1"
BASE_URL = "https://api.meta.ai/v1"

# Words that never name the segmentable subject: caption boilerplate
# from the image prompts plus leading articles and vague adjectives.
# Phase B: also narration stopwords and generic verbs. Phase A jobs
# derived concepts from narration words like "in", "carried", and
# "thousand", which found 0 SAM matches on every scene; those words
# must never become a concept again.
_SKIP_WORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "flat", "vector", "illustration", "illustrations", "image",
    "photo", "photograph", "picture", "scene", "background",
    "small", "big", "large", "tiny", "little", "cute", "friendly",
    "main", "primary", "whole", "entire", "full", "central",
    "red", "blue", "green", "yellow", "white", "black", "gray",
    "grey", "light", "dark", "colored", "colourful", "colorful",
    "in", "on", "of", "to", "for", "with", "by", "from", "at",
    "as", "into", "over", "under", "out", "up", "down",
    "and", "or", "but", "not", "no", "so", "very", "just",
    "how", "what", "when", "where", "which", "who", "why",
    "it", "its", "they", "them", "their", "he", "she", "we",
    "you", "your", "yours", "our", "ours", "i", "me", "my",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "shall", "should", "may", "might", "must",
    "make", "makes", "made", "carry", "carries", "carried",
    "take", "takes", "took", "taken", "thousand", "hundred",
    "one", "two", "also", "than", "then", "there", "here",
    "detailed", "closeup", "crosssection", "timelapse", "vshaped",
    "animated", "animation", "diagram", "aerial", "view",
})


def concept_for_subject(subject):
    """Reduce a scene description to one bare SAM concept word.

    Examples: "small robot watching a screen" -> "robot",
    "flat vector illustration, small robot practicing" -> "robot",
    "Alpha robots learn by watching" -> "robot", "robot" -> "robot".
    Raises ValueError when no usable word remains.
    """
    if not isinstance(subject, str):
        raise ValueError("subject must be a non-empty str")
    words = subject.strip().split()
    if not words:
        raise ValueError("subject must be a non-empty str")
    # A leading title-case word followed by more words reads as a
    # proper name ("Alpha robots ..."), not the subject: skip it.
    if len(words) > 1 and words[0][0].isupper() and words[0].isalpha():
        words = words[1:]
    cleaned = []
    for word in words:
        token = "".join(c for c in word.lower() if c.isalpha())
        if token:
            cleaned.append(token)
    if not cleaned:
        raise ValueError("subject has no usable concept word: %r" % (subject,))
    for token in cleaned:
        if token not in _SKIP_WORDS:
            concept = token
            break
    else:
        concept = cleaned[0]
    # Naive singular: "robots" segments better as "robot".
    if (len(concept) > 3 and concept.endswith("s")
            and not concept.endswith("ss")):
        concept = concept[:-1]
    return concept


def _mime_for_path(image_path):
    ext = os.path.splitext(str(image_path))[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    mime, _ = mimetypes.guess_type(str(image_path))
    if mime and mime.startswith("image/"):
        return mime
    raise RuntimeError("unsupported image type for SAM 3.1: %s" % image_path)


def _data_url(image_path):
    with open(image_path, "rb") as f:
        raw = f.read()
    if not raw:
        raise RuntimeError("empty image file: %s" % image_path)
    mime = _mime_for_path(image_path)
    return ("data:%s;base64," % mime) + base64.b64encode(raw).decode("ascii")


def _records_to_boxes(records):
    boxes = []
    masks = 0
    for record in records or []:
        kind = getattr(record, "kind", None)
        if kind == "box":
            boxes.append([record.left, record.top,
                          record.right, record.bottom])
        elif kind == "mask":
            masks += 1
    return boxes, masks


def decode_output(output_text):
    """Decode SAM protocol text with meta-sam-parser.

    Returns {"matches": int, "boxes": [[l, t, r, b], ...],
    "masks": int}. Raises RuntimeError on undecodable output.
    """
    if not output_text or not output_text.strip():
        raise RuntimeError("SAM 3.1 returned empty output text")
    parser = image_segmentation_format().create_parser()
    text = output_text if output_text.endswith("\n") else output_text + "\n"
    for _snapshot in parser.push(text):
        pass
    try:
        finished = parser.finish(CompletedOutcome())
    except Exception as e:
        raise RuntimeError("SAM 3.1 output failed to parse: %s" % e) from e
    boxes, masks = _records_to_boxes(finished.result.records)
    return {"matches": len(boxes), "boxes": boxes, "masks": masks}


async def _segment_async(image_path, subject, concept, api_key, timeout):
    client = AsyncOpenAI(base_url=BASE_URL, api_key=api_key,
                         timeout=timeout)
    try:
        stream = await client.responses.create(
            model=MODEL,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image",
                 "image_url": _data_url(image_path)},
            ]}],
            metadata={"mask_encoding": "one_bit"},
            stream=True,
        )
        parsed = parse_responses_stream(
            stream, image_segmentation_format())
        finished = await parsed.final_result()
    except ResponsesStreamLaneError as e:
        # Completed without a usable output-text lane: no records,
        # so a zero-match warning, never a fake match.
        return {"subject": subject, "concept": concept,
                "matches": 0, "boxes": [], "masks": 0,
                "warning": ("SAM 3.1 returned no output lane for "
                            "concept %r (subject %r): %s; continuing"
                            % (concept, subject, e))}
    except Exception as e:
        raise RuntimeError("SAM 3.1 API call failed: %s" % e) from e
    if not isinstance(finished.outcome, CompletedOutcome):
        raise RuntimeError(
            "SAM 3.1 response did not complete: %s" % (finished.outcome,))
    boxes, masks = _records_to_boxes(finished.records)
    result = {"subject": subject, "concept": concept,
              "matches": len(boxes), "boxes": boxes,
              "masks": masks, "warning": None}
    if result["matches"] == 0:
        result["warning"] = (
            "SAM 3.1 found zero matches for concept %r (subject %r); "
            "continuing" % (concept, subject))
    return result


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def sam_segment(image_path, subject, api_key, timeout=300):
    """Run hosted SAM 3.1 on one scene image. Returns a result dict:

    {"subject": str, "concept": str, "matches": int, "boxes": [...],
     "masks": int, "warning": str|None}

    Raises RuntimeError if the API call itself errors. Zero matches
    is a warning, not an error.
    """
    if not subject or not str(subject).strip():
        raise ValueError("subject must be a non-empty str")
    if not os.path.exists(image_path):
        raise RuntimeError("missing image file: %s" % image_path)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("META_API_KEY is not set in the environment")
    clean_subject = str(subject).strip()
    concept = concept_for_subject(clean_subject)
    return _run_async(_segment_async(
        image_path, clean_subject, concept,
        str(api_key).strip(), timeout))
