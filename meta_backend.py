"""Meta Model API backend for ReelDirector (strict Meta-only build).

Auth: Bearer [REDACTED] from the META_API_KEY environment variable.
Base URL: https://api.meta.ai/v1 (Meta Model API, OpenAI-compatible
Chat Completions shape, verified against Meta's developer docs:
Bearer MODEL_API_KEY, POST /v1/chat/completions with
{model, messages, max_tokens}).

Text models:
  SPARK_MODEL  = muse-spark-1.3  (first script draft, plus the
                 Phase B director loop: scene critique and
                 per-scene revision of flagged scenes)
  WONDER_MODEL = muse-wonder-1.0 (full-script revise of the draft
                 before the director loop)

Both steps must succeed for a live render; there is no non-Meta
fallback. Token budgets are generous on purpose: Spark and Wonder
are reasoning models and spend hidden reasoning tokens first.

The key is read from the environment only. It is never printed,
logged, written to disk, or committed.
"""

import os

import requests

DEFAULT_BASE = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
WONDER_MODEL = "muse-wonder-1.0"
# Kept for backwards compatibility; new code should name a model.
DEFAULT_MODEL = SPARK_MODEL

# Reasoning models spend many tokens before answering; the API-side
# cap must not starve them.
MAX_TOKENS_LIMIT = 32768
SCRIPT_DRAFT_TOKENS = 8192
SCRIPT_REVISE_TOKENS = 8192


def _base():
    return os.environ.get("META_API_BASE", DEFAULT_BASE).rstrip("/")


def _model():
    return os.environ.get("META_TEXT_MODEL", DEFAULT_MODEL)


def _headers(api_key=None):
    key = (api_key if api_key is not None
           else os.environ.get("META_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("META_API_KEY is not set in the environment")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def list_models(api_key=None, timeout=30):
    """Cheap authenticated key check: lists models without spending
    generation tokens. api_key overrides the environment (the console
    accepts a pasted in-memory key); timeout bounds the check so a
    hung connection reports its real failure instead of hanging."""
    r = requests.get(_base() + "/models", headers=_headers(api_key),
                     timeout=timeout)
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
    """One chat completion. Returns the assistant text only.

    Raises on any API failure: the caller must fail the job, never
    substitute another backend.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty str")
    if (not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= MAX_TOKENS_LIMIT):
        raise ValueError(
            "max_tokens must be int in 1..%d" % MAX_TOKENS_LIMIT)
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
        timeout=300,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError("Non-JSON chat response: %s" % r.text[:500]) from e
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Unexpected chat shape: %r" % (data,)[:500]) from e
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            "Empty chat content from the model (no usable text returned): "
            "failing loudly instead of rendering from nothing")
    return content


def draft_script(topic, model=None):
    """First script draft via Muse Spark. Returns raw assistant text."""
    return chat(
        "Write a 5-line faceless video script outline for this topic. "
        "One clear sentence per line, no numbering, no preamble. "
        "Topic: " + topic,
        model=model or SPARK_MODEL,
        max_tokens=SCRIPT_DRAFT_TOKENS,
    )


REVISE_BRIEF = (
    "Line 1 must be a stronger hook that earns the first three "
    "seconds. Lines 2-4 build one beat each in order. Line 5 is a "
    "clean closing takeaway that restates the point."
)


def revise_script(draft, topic, model=None):
    """Muse Wonder critiques the Spark draft and returns the final
    5-line script. Returns raw assistant text."""
    return chat(
        "You are revising a faceless-video script draft for this topic: "
        + topic + "\n\nDraft:\n" + draft + "\n\nCritique the draft "
        "(hook strength, one idea per line, speakability), then output "
        "the final script as exactly 5 lines, one clear sentence per "
        "line, no numbering, no preamble, no critique in the output. "
        + REVISE_BRIEF + " Final script only.",
        model=model or WONDER_MODEL,
        max_tokens=SCRIPT_REVISE_TOKENS,
    )


SPARK_CRITIQUE_CRITERIA = (
    "hook, relevance, repetition-narration, repetition-visual, "
    "duration, visual-support"
)


def critique_scenes(topic, audience, target_duration_s, scenes,
                    model=None):
    """Muse Spark critiques structured scenes. Returns raw text.

    The prompt demands exactly one JSON list of
    {scene_id, criterion, detail} objects and nothing else, with
    criterion limited to SPARK_CRITIQUE_CRITERIA. The caller
    (director.parse_spark_critique_response) validates the text
    strictly: a malformed, missing, or unparseable response is
    rejected, never patched up. ``scenes`` is a list of dicts with
    id, narration, and visual_prompt.
    """
    listing = "\n".join(
        "Scene %s | narration: %r | visual prompt: %r"
        % (s.get("id"), s.get("narration"), s.get("visual_prompt"))
        for s in scenes)
    return chat(
        "You are critiquing a faceless lesson video plan against its "
        "brief. Brief: topic %r, audience %r, target total duration "
        "%.0f seconds.\n\nPlan:\n%s\n\nJudge ONLY these criteria: %s. "
        "Reply with exactly one JSON list and nothing else. Every "
        "object MUST have exactly these three keys: \"scene_id\" "
        "(one of the Scene ids above), \"criterion\" (one of the "
        "listed criteria), and \"detail\" (one sentence saying what "
        "fails). Do NOT use keys like \"pass\" or \"reason\"; do NOT "
        "emit one object per criterion. Example of one finding: "
        "[{\"scene_id\": \"scene-1\", \"criterion\": \"hook\", "
        "\"detail\": \"the opening line states the topic instead of "
        "hooking the viewer\"}]. One object per real problem, an "
        "empty list [] when every scene is fine, no commentary "
        "outside the JSON."
        % (topic, audience, float(target_duration_s), listing,
           SPARK_CRITIQUE_CRITERIA),
        model=model or SPARK_MODEL,
        max_tokens=SCRIPT_REVISE_TOKENS,
    )


def repair_critique(previous_bad, problem, topic, audience,
                    target_duration_s, scene_ids, model=None):
    """One strict re-ask after a rejected Spark critique.

    Same Muse Spark model only (Meta-only rule): quotes the exact
    validation ``problem`` with the previous bad reply so the model
    can correct its shape. Still returns raw text for the caller to
    validate again; a repeated failure raises and fails the job.
    """
    return chat(
        "Your last critique reply was rejected (%s). Previous reply: "
        "%r. Brief: topic %r, audience %r, target total duration "
        "%.0f seconds. Valid scene ids: %s. Valid criteria: %s. "
        "Reply again with exactly one JSON list and nothing else, "
        "every object having exactly the keys \"scene_id\", "
        "\"criterion\", and \"detail\", or [] when nothing fails."
        % (problem, str(previous_bad)[:800], topic, audience,
           float(target_duration_s), list(scene_ids),
           SPARK_CRITIQUE_CRITERIA),
        model=model or SPARK_MODEL,
        max_tokens=SCRIPT_REVISE_TOKENS,
    )


def revise_scene(topic, audience, target_duration_s, narration,
                 visual_prompt, reasons, model=None):
    """Muse Spark revises ONE flagged scene. Returns raw text.

    The prompt carries the brief plus the exact flagged criteria for
    this scene and demands exactly two lines: 'NARRATION: ...' then
    'VISUAL: ...'. The caller parses those lines and fails visibly
    when they are missing. ``reasons`` is a list of finding dicts
    with criterion and detail.
    """
    criteria = "; ".join("%s: %s" % (r.get("criterion"),
                                     r.get("detail", "")[:200])
                         for r in reasons)
    return chat(
        "You are the director revising ONE scene of a faceless "
        "lesson video. Brief: topic %r, audience %r, target total "
        "duration %.0f seconds. Current narration: %r. Current "
        "visual prompt: %r. Fix ONLY these flagged issues: %s. "
        "Reply with exactly two lines and nothing else: "
        "'NARRATION: <revised narration, one sentence>' then "
        "'VISUAL: <revised visual prompt, one short phrase>'. "
        "No preamble, no explanation."
        % (topic, audience, float(target_duration_s), narration,
           visual_prompt, criteria),
        model=model or SPARK_MODEL,
        max_tokens=SCRIPT_REVISE_TOKENS,
    )


def revise_with_note(draft, topic, model=None):
    """Wonder revise that also returns a one-sentence change note.

    Asks Wonder for the final 5-line script followed by a trailing
    "CHANGE-NOTE:" line naming its main edit. Returns
    (final_text, change_note); the note parses leniently and falls
    back to "" when Wonder omits the line.
    """
    raw = chat(
        "You are revising a faceless-video script draft for this topic: "
        + topic + "\n\nDraft:\n" + draft + "\n\nCritique the draft "
        "(hook strength, one idea per line, speakability), then output "
        "the final script as exactly 5 lines, one clear sentence per "
        "line, no numbering, no preamble, no critique in the output. "
        + REVISE_BRIEF + " After the 5 lines, add one last line of "
        "the form \"CHANGE-NOTE: <one sentence saying what you "
        "changed>\".",
        model=model or WONDER_MODEL,
        max_tokens=SCRIPT_REVISE_TOKENS,
    )
    note = ""
    kept = []
    for line in raw.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("change-note:") or low.startswith("change note:"):
            note = stripped.split(":", 1)[1].strip()
        elif stripped:
            kept.append(stripped)
    return "\n".join(kept), note
