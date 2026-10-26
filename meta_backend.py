"""Meta Model API backend for ReelDirector (event build, Oct 26-Nov 4 2026).

Auth: Bearer token from the META_API_KEY environment variable.
Base URL: https://api.meta.ai/v1 (Meta Model API).
Text model default: muse-spark-1.3 (Meta's recommended model for agentic work).
The key is never printed, logged, or committed.
"""

import os

import requests

BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1")
TEXT_MODEL = os.environ.get("META_TEXT_MODEL", "muse-spark-1.3")


def _headers():
    key = os.environ.get("META_API_KEY", "")
    if not key:
        raise RuntimeError("META_API_KEY is not set in the environment")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def list_models():
    """Cheap key check: lists models without spending generation tokens."""
    r = requests.get(BASE + "/models", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def chat(prompt, model=TEXT_MODEL, max_tokens=512):
    """One chat completion. Returns the assistant text only."""
    r = requests.post(
        BASE + "/chat/completions",
        headers=_headers(),
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
