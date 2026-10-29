"""Meta image generation backend (strict Meta-only build).

Calls muse-image-1.0 through the Meta Model API:
  POST https://api.meta.ai/v1/images/generations
  {model, prompt, n, size, output_format, response_format}

This is the ONLY image backend. There is no fallback: any API
failure raises and the caller must fail the job with the real
error instead of substituting another backend or a placeholder.

The key is passed in by the caller (app.py reads it from the
environment or the pasted in-memory key). It is never logged, never
written to disk, and never committed. Size requests native aspect
from the API: "720x1280" for 9:16, "1280x720" for 16:9. The ffmpeg
assembly additionally center-crops every scene image to the
selected format before scaling to the exact output dimensions, so
even a 16:9 source file never stretches into a vertical frame.

Cost: $0.01 per image (Meta list price). A 5-scene video costs about
$0.05, inside the $150 hackathon credits.
"""

import base64
import time

import requests

API_URL = "https://api.meta.ai/v1/images/generations"
MODEL = "muse-image-1.0"

# One short wait before the single 503 retry (the API was briefly
# unavailable, not the prompt).
RETRY_BACKOFF_S = 2

# Native sizes requested from the Muse Image API, one per format.
# No other size is ever requested: these are the only two sizes
# this project claims the API accepts.
SIZES = {
    "16:9": "1280x720",
    "9:16": "720x1280",
}


def size_for_aspect(aspect_ratio):
    """API size string for a supported aspect ratio.

    Raises ValueError for anything else: an unknown format never
    silently renders as another.
    """
    key = str(aspect_ratio or "").strip()
    try:
        return SIZES[key]
    except KeyError:
        raise ValueError(
            "unsupported aspect ratio %r: supported formats are %s"
            % (aspect_ratio, sorted(SIZES))) from None


def generate_image(prompt, out_path, api_key, timeout=180, size=None,
                   aspect_ratio=None, decisions=None):
    """Generate one PNG image with Muse Image. Returns out_path.
    Raises RuntimeError on any API failure (no fallback).

    The requested size is native aspect: pass aspect_ratio="9:16"
    for a "720x1280" request or "16:9" for "1280x720" (an explicit
    size wins when both are given; the default with neither is
    "1280x720"). Only the two sizes in SIZES are ever sent.

    HTTP 503 (briefly unavailable) retries once after a short
    backoff, recorded in decisions when given. HTTP 400 with a
    content_policy_violation code raises the actionable
    blocked-prompt message instead of the raw HTTP error.
    """
    if size is None:
        if aspect_ratio is None:
            size = SIZES["16:9"]
        else:
            size = size_for_aspect(aspect_ratio)
    elif size not in SIZES.values():
        raise ValueError(
            "unsupported Muse Image size %r: supported sizes are %s"
            % (size, sorted(SIZES.values())))

    def _post():
        return requests.post(
            API_URL,
            headers={"Authorization": "Bearer " + api_key,
                     "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "output_format": "png",
                "response_format": "b64_json",
            },
            timeout=timeout,
        )

    resp = _post()
    if resp.status_code == 503:
        first = (resp.text or "")[:160]
        time.sleep(RETRY_BACKOFF_S)
        resp = _post()
        if decisions is not None:
            decisions.append({
                "decision": ("Muse Image HTTP 503 (%s), retried "
                             "once after a short backoff" % first),
                "stage": "scenes", "scene": None, "retried": True,
            })
    if resp.status_code != 200:
        body = resp.text or ""
        if resp.status_code == 400 \
                and "content_policy_violation" in body:
            raise RuntimeError(
                "Muse Image blocked this prompt (content policy): "
                "edit the visual and regenerate this scene.")
        raise RuntimeError(
            "Muse Image HTTP %d: %s" % (resp.status_code, body[:300]))
    data = resp.json()
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(
            "Muse Image returned no image: %s" % (str(data)[:300]))
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(items[0]["b64_json"]))
    return out_path
