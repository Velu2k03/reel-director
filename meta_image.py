"""Meta image generation backend (event build, Oct 2026).

Calls muse-image-1.0 through the Meta Model API:
  POST https://api.meta.ai/v1/images/generations
  {model, prompt, n, size, output_format, response_format}

The key is passed in by the caller (app.py reads it from the
environment). It is never logged, never written to disk, and never
committed. Size sets aspect ratio only, not exact pixels: a 1280x720
request returns a 16:9 image (observed 2048x1152), which the ffmpeg
assembly scales to 1280x720 without distortion.

Cost: $0.01 per image (Meta list price). A 5-scene video costs about
$0.05, inside the $150 hackathon credits.
"""

import base64

import requests

API_URL = "https://api.meta.ai/v1/images/generations"
MODEL = "muse-image-1.0"


def generate_image(prompt, out_path, api_key, timeout=180):
    """Generate one PNG image with Muse Image. Returns out_path.
    Raises RuntimeError on any API failure (caller falls back)."""
    resp = requests.post(
        API_URL,
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "prompt": prompt,
            "n": 1,
            "size": "1280x720",
            "output_format": "png",
            "response_format": "b64_json",
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "Muse Image HTTP %d: %s" % (resp.status_code, resp.text[:300]))
    data = resp.json()
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(
            "Muse Image returned no image: %s" % (str(data)[:300]))
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(items[0]["b64_json"]))
    return out_path
