"""Meta Model API backend for ReelDirector (event build, Oct 26-Nov 4 2026).

Auth: Bearer token from the META_API_KEY environment variable.
Base URL: https://api.meta.ai/v1 (Meta Model API).
Text model default: muse-spark-1.3 (Meta's recommended model for agentic work).
The key is never printed, logged, or committed.
"""

import os

import requests

DEFAULT_BASE = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


def _base():
    return os.environ.get("META_API_BASE", DEFAULT_BASE).rstrip("/")


def _model():
    return os.environ.get("META_TEXT_MODEL", DEFAULT_MODEL)


def _headers():
    key = os.environ.get("META_API_KEY", "").strip()
    if not key:
        raise RuntimeError("META_API_KEY is not set in the environment")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def list_models():
    """Cheap key check: lists models without spending generation tokens."""
    r = requests.get(_base() + "/models", headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        if "data" not in data:
            raise RuntimeError("Unexpected models shape: %r" % (data,)[:500])
        models = data["data"]
    else:
        models = data
    if not isinstance(models, (list, tuple)):
        raise RuntimeError("Unexpected models shape: %r" % (data,)[:500])
    return models


def chat(prompt, model=None, max_tokens=512):
    """One chat completion. Returns the assistant text only."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty str")
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be int in 1..4096")
    if model is None:
        model = _model()
    r = requests.post(
        _base() + "/chat/completions",
        headers=_headers(),
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError("Non-JSON chat response: %s" % r.text[:500]) from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Unexpected chat shape: %r" % (data,)[:500]) from e
