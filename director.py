"""Phase B bounded director loop (strict Meta-only build).

Replaces the fixed Spark-draft-then-Wonder-revise sequence with a
bounded workflow:

  Brief -> structured scene plan -> critique -> targeted revision
  -> media generation -> assembly -> technical validation.

Only the plan/critique/revision part lives here. Media generation,
assembly, and technical QC stay in render.py; job wiring stays in
app.py. Every function here is deterministic and Meta-key-free, so
the focused tests in test_phase_b.py run fully mocked: the only
model calls in the loop (Muse Spark critique plus per-scene Muse
Spark revision) are injected callables (``spark_critique_fn``,
``revise_scene_fn``), never direct network calls.

Rules enforced here, per the Phase B brief:
  1. Structured scenes carry stable IDs, narration, a visual prompt,
     an intended duration, and a revision status. Model responses
     are validated before use: a scene missing narration, a visual
     prompt, or an ID is rejected with a clear error. Nothing is
     invented to fill a gap and a broken plan never proceeds.
  2. The brief carries topic, audience, and target duration through
     planning, critique, and revision, so critique judges relevance
     to the intended audience, not just grammar.
  3. Critique criteria are concrete and checkable: hook strength,
     repetition between scenes (narration wording and visual ideas),
     estimated narration length against target duration (measured
     voice rate when available, otherwise a stated conservative
     estimate, never an invented exact figure), and whether each
     visual prompt supports its narration.
  4. Targeted revision touches only flagged scenes, with a reason
     per revised scene. Automatic attempts are capped at
     MAX_REVISION_CYCLES critique+revision cycles, then stop. Leftover
     findings are surfaced as unmet criteria (criterion + scene) and
     the run is NOT labelled editorially passed. Technical QC and
     editorial review stay separate downstream in render.qc_report.
  5. No chain-of-thought is produced or exposed anywhere here: the
     log records only decisions (before/after, criterion, status).
  6. SAM concepts come from the visual prompt or the scene subject,
     never from narration stopwords or generic verbs. A bad concept
     is fixed from the visual prompt before any image is
     regenerated, so one bad concept never costs two regens.
"""

import json
from dataclasses import dataclass, field

MAX_REVISION_CYCLES = 2

# Stated conservative speech estimate, words per minute. Used only
# when no measured voice rate is available yet, and always labelled
# as an estimate in findings (never presented as exact).
ESTIMATED_WPM = 130

# Words that carry no subject meaning for relevance / overlap /
# repetition checks. Includes the narration stopwords and generic
# verbs behind the Phase A SAM failure ("in", "carried",
# "thousand"-style filler), so they can never become a concept or a
# relevance signal. Finish fix: adjectives ("brilliant",
# "beautiful", "powerful", "quickly"), abstract nouns ("hook"),
# and mass nouns without a bounded subject ("rain" as weather,
# "water") are listed here too, and the whole meta_sam skip set is
# unioned in below, so derive_sam_concept filters with exactly the
# same rule as meta_sam.concept_for_subject.
STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "flat", "vector", "illustration", "illustrations", "image",
    "photo", "photograph", "picture", "scene", "background",
    "foreground", "shot", "shots", "frame", "frames",
    "giant", "gigantic", "huge", "enormous", "massive",
    "immense", "vast", "colossal", "mammoth", "mighty",
    "mini", "miniature", "solid", "rocky", "fallen",
    "steep", "rugged", "hollow",
    "beautiful", "beauty", "brilliant", "brilliantly",
    "powerful", "powerfully", "power", "powers",
    "quick", "quickly", "slow", "slowly", "fast",
    "strong", "strongly", "weak", "weakly",
    "great", "greatly", "wonderful", "amazing", "good",
    "better", "best", "bad", "worse", "worst",
    "hook", "hooks", "lesson", "lessons", "video", "videos",
    "story", "stories", "topic", "topics", "concept",
    "concepts", "idea", "ideas", "theme", "themes",
    "message", "messages", "moral", "morals", "point",
    "points", "fact", "facts", "truth", "truths", "knowledge",
    "wisdom", "skill", "skills", "habit", "habits",
    "example", "examples",
    "thing", "things", "something", "anything", "everything",
    "stuff", "object", "objects", "item", "items",
    "part", "parts", "side", "sides", "place", "places",
    "shape", "shapes", "size", "sizes", "style", "styles",
    "rain", "rains", "raining", "rainy", "water", "waters",
    "snow", "snows", "snowing", "snowy", "wind", "winds",
    "windy", "weather", "air", "fog", "mist", "storm",
    "storms",
    "in", "on", "of", "to", "for", "with", "by", "from", "at",
    "as", "into", "over", "under", "out", "up", "down",
    "about", "against", "across", "along", "among", "around",
    "behind", "below", "beneath", "beside", "besides",
    "between", "beyond", "during", "near", "past",
    "through", "toward", "towards", "within", "without",
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
    "watch", "watches", "watched", "watching",
    "learn", "learns", "learned", "learning",
    "practice", "practices", "practiced", "practicing",
    "show", "shows", "showed", "shown", "showing",
    "explain", "explains", "explained", "explaining",
    "describe", "describes", "described", "describing",
    "discover", "discovers", "discovered", "discovering",
    "flow", "flows", "flowed", "flowing",
    "form", "forms", "formed", "forming",
    "settle", "settles", "settled", "settling",
    "grow", "grows", "grew", "growing",
    "build", "builds", "built", "building",
    "create", "creates", "created", "creating",
    "leave",
})

# Weak scene-1 openers: a hook that starts this way is flagged.
WEAK_HOOK_OPENERS = (
    "this video", "in this video", "in this lesson", "today we",
    "welcome", "hello", "hi there", "this lesson",
)

MIN_HOOK_WORDS = 8

# Total estimated narration more than this far from target (either
# direction) flags the duration criterion on one scene.
DURATION_TOLERANCE = 0.30

# Shared word-runs of this length between two scenes count as
# repeated narration wording.
REPEAT_NGRAM = 4

# Group language that breaks a solo-learning brief: the model
# kept writing "stay with us" and "together" for solo learners,
# and relevance must keep flagging it.
GROUP_LANGUAGE = (
    "together",
    "stay with us",
    "join us",
    "let us",
    "let's",
    "all of us",
    "as a group",
    "side by side",
)


def is_solo_audience(audience):
    """True when the brief audience learns alone ("solo learners",
    "solo", ...). Group language is then out of place."""
    return "solo" in str(audience or "").lower()


def group_language_in(text):
    """The first group-language phrase found in narration, or ""."""
    low = str(text or "").lower()
    for phrase in GROUP_LANGUAGE:
        if phrase in low:
            return phrase
    return ""


def visual_objects(scene, brief):
    """Distinct visual objects for the repetition-visual check.

    Content words of the visual prompt minus the brief topic
    words: every Jesus scene shares Jesus by definition, so the
    topic word is excluded and only a distinct visual object
    (boat, bread, road, ...) can collide. Returns a set, possibly
    empty when the visual says nothing beyond the topic.
    """
    topic_words = set(_content_words(brief.topic))
    return {w for w in _content_words(scene.get("visual_prompt") or "")
            if w not in topic_words}


class ScenePlanError(ValueError):
    """A model scene plan failed validation: rejected, never filled."""


@dataclass
class Brief:
    """The director brief: topic, audience, and target duration.

    All three travel through planning, critique, and revision so the
    critique judges relevance to the intended audience.
    """
    topic: str
    audience: str = "solo learners"
    target_duration_s: float = 30.0

    def __post_init__(self):
        if not isinstance(self.topic, str) or not self.topic.strip():
            raise ScenePlanError("brief needs a non-empty topic")
        self.topic = self.topic.strip()
        if not isinstance(self.audience, str) or not self.audience.strip():
            raise ScenePlanError("brief needs a non-empty audience")
        self.audience = self.audience.strip()
        try:
            target = float(self.target_duration_s)
        except (TypeError, ValueError):
            raise ScenePlanError(
                "brief target_duration_s must be a positive number, "
                "got %r" % (self.target_duration_s,))
        if target <= 0:
            raise ScenePlanError(
                "brief target_duration_s must be positive, got %r"
                % (self.target_duration_s,))
        self.target_duration_s = target


def make_brief(topic, audience=None, target_duration_s=None,
               scene_count=None):
    """Build a Brief, defaulting audience and target duration.

    Default target is 6s per scene when the scene count is known,
    else 30s. Defaults are stated, never presented as measured.
    """
    if target_duration_s is None and scene_count:
        target_duration_s = float(scene_count) * 6.0
    return Brief(
        topic=topic,
        audience=audience or "solo learners",
        target_duration_s=(target_duration_s
                           if target_duration_s is not None else 30.0),
    )


def _words(text):
    return ["".join(c for c in w.lower() if c.isalpha())
            for w in str(text or "").split()]


def _singular(word):
    # Safe singular, mirroring meta_sam.concept_for_subject, so
    # "river" matches "rivers" in relevance and overlap checks.
    # Proper-name guard (Jesus-run fix): proper names are never
    # singularized, so "jesus" stays whole (never "jesu") and
    # "james"/"moses" stay whole (never "jame"/"mose"). Prefer
    # the original word; strip a final "s" only for common
    # plurals.
    if word == "leaves":
        return "leaf"
    if word in ("jesus", "james", "moses"):
        return word
    if word.endswith("us"):
        return word
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        stem = word[:-1]
        if stem in ("jesu", "jame", "mose"):
            return word
        return stem
    return word


def _content_words(text):
    return [_singular(w) for w in _words(text)
            if w and w not in STOPWORDS]


def validate_scene_plan(raw):
    """Validate a model scene-plan response into structured scenes.

    Each scene must be a dict with a non-empty str ``id``, a
    non-empty str ``narration``, and a non-empty str visual prompt
    (``visual_prompt`` preferred, ``visual`` accepted). Anything else
    raises ScenePlanError naming the scene and the missing field.
    Nothing is invented: no defaults fill a gap, and duplicate IDs
    are rejected. Returns a list of scene dicts with keys id,
    narration, visual_prompt, intended_duration_s (0.0 until a brief
    assigns it), and revision_status ("planned").
    """
    if not isinstance(raw, list) or not raw:
        raise ScenePlanError(
            "scene plan must be a non-empty list, got %r" % (raw,))
    scenes = []
    seen = set()
    for pos, item in enumerate(raw):
        where = "scene at position %d" % pos
        if not isinstance(item, dict):
            raise ScenePlanError(
                "%s is not an object: rejecting response, not "
                "inventing content" % where)
        sid = item.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ScenePlanError(
                "%s is missing its stable ID: rejecting response, "
                "not inventing content" % where)
        sid = sid.strip()
        if sid in seen:
            raise ScenePlanError(
                "duplicate scene ID %r: rejecting response" % sid)
        seen.add(sid)
        narration = item.get("narration")
        if not isinstance(narration, str) or not narration.strip():
            raise ScenePlanError(
                "scene %r is missing narration: rejecting response, "
                "not inventing content" % sid)
        visual = item.get("visual_prompt", item.get("visual"))
        if not isinstance(visual, str) or not visual.strip():
            raise ScenePlanError(
                "scene %r is missing its visual prompt: rejecting "
                "response, not inventing content" % sid)
        scenes.append({
            "id": sid,
            "narration": narration.strip(),
            "visual_prompt": visual.strip(),
            "intended_duration_s": 0.0,
            "revision_status": "planned",
        })
    return scenes


def build_plan(brief, narration_lines, style=None):
    """Build a validated structured plan from draft narration lines.

    Stable IDs scene-1..scene-N; intended duration splits the brief
    target evenly (stated split, re-checked by critique). The visual
    prompt names the scene art; render.py merges the editable style
    block at image time. Raises ScenePlanError on empty lines.
    """
    if not narration_lines or not isinstance(narration_lines, list):
        raise ScenePlanError("no narration lines to plan from")
    lines = [ln.strip() for ln in narration_lines
             if isinstance(ln, str) and ln.strip()]
    if not lines:
        raise ScenePlanError("no narration lines to plan from")
    raw = [{
        "id": "scene-%d" % (i + 1),
        "narration": ln,
        "visual_prompt": "flat vector illustration, " + ln,
    } for i, ln in enumerate(lines)]
    scenes = validate_scene_plan(raw)
    each = brief.target_duration_s / len(scenes)
    for scene in scenes:
        scene["intended_duration_s"] = round(each, 2)
    return scenes


def estimate_seconds(narration, measured_wps=None):
    """Estimated narration seconds + the basis label.

    Uses the measured voice rate when given, else the stated
    conservative ESTIMATED_WPM estimate. Returns (seconds, basis).
    The basis always says which one was used, so a finding never
    presents an estimate as an exact figure.
    """
    words = len(_words(narration))
    if measured_wps:
        rate = float(measured_wps)
        basis = ("measured voice rate %.2f words/s" % rate)
    else:
        rate = ESTIMATED_WPM / 60.0
        basis = ("conservative estimate %d wpm (no measured voice "
                 "yet), not exact" % ESTIMATED_WPM)
    return round(words / rate, 2), basis


def _ngrams(words, n):
    return {" ".join(words[i:i + n])
            for i in range(len(words) - n + 1)}


def critique(brief, scenes, measured_wps=None):
    """Check a structured plan against the concrete criteria.

    Returns (findings, estimates, basis) where findings is a list of
    {scene_id, criterion, detail} with criterion in {hook,
    relevance, repetition-narration, repetition-visual, duration,
    visual-support}, estimates maps scene_id -> estimated seconds,
    and basis is the estimate basis label. Empty findings means the
    plan passes editorially.
    """
    findings = []
    estimates = {}
    basis = None
    for scene in scenes:
        seconds, basis = estimate_seconds(scene["narration"],
                                          measured_wps)
        estimates[scene["id"]] = seconds

    if not scenes:
        return findings, estimates, basis or ""

    # Hook: scene 1 must earn the first seconds.
    first = scenes[0]
    hook_words = _words(first["narration"])
    low = first["narration"].strip().lower()
    if (len(hook_words) < MIN_HOOK_WORDS
            or low.startswith(WEAK_HOOK_OPENERS)):
        findings.append({
            "scene_id": first["id"],
            "criterion": "hook",
            "detail": ("opening narration is %d words (minimum %d) "
                       "or opens weakly; it must hook the intended "
                       "audience %r for topic %r"
                       % (len(hook_words), MIN_HOOK_WORDS,
                          brief.audience, brief.topic)),
        })

    # Relevance: every scene must share a content word with the
    # topic or the audience (judges audience fit, not grammar).
    brief_words = set(_content_words(brief.topic + " "
                                     + brief.audience))
    for scene in scenes:
        if not (set(_content_words(scene["narration"]))
                & brief_words):
            findings.append({
                "scene_id": scene["id"],
                "criterion": "relevance",
                "detail": ("narration shares no content word with "
                           "topic %r or audience %r"
                           % (brief.topic, brief.audience)),
            })

    # Repetition, narration wording: shared 4-grams between scenes.
    grams = {s["id"]: _ngrams(_words(s["narration"]), REPEAT_NGRAM)
             for s in scenes}
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            shared = sorted(grams[scenes[i]["id"]]
                            & grams[scenes[j]["id"]])
            if shared:
                for sid in (scenes[i]["id"], scenes[j]["id"]):
                    findings.append({
                        "scene_id": sid,
                        "criterion": "repetition-narration",
                        "detail": ("repeated narration wording with "
                                   "the other scene: %r"
                                   % shared[0]),
                    })

    # Group language against a solo audience: "stay with us"
    # and "together" do not belong in a solo-learners video.
    # Flagged under relevance, which judges audience fit.
    if is_solo_audience(brief.audience):
        for scene in scenes:
            phrase = group_language_in(scene["narration"])
            if phrase:
                findings.append({
                    "scene_id": scene["id"],
                    "criterion": "relevance",
                    "detail": ("narration uses group language %r for "
                               "a solo audience %r: one learner, "
                               "no group"
                               % (phrase, brief.audience)),
                })

    # Repetition, visual ideas: two visuals sharing a DISTINCT
    # visual object (a content word outside the brief topic).
    # Scenes that share only the topic word never collide: every
    # Jesus scene leads with Jesus by definition, so the topic
    # is excluded and only boat/bread/road-style overlap flags.
    objects = [visual_objects(s, brief) for s in scenes]
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            shared = sorted(objects[i] & objects[j])
            if shared:
                for sid in (scenes[i]["id"], scenes[j]["id"]):
                    findings.append({
                        "scene_id": sid,
                        "criterion": "repetition-visual",
                        "detail": ("repeated visual idea: both visuals "
                                   "share object %r" % shared[0]),
                    })

    # Duration: estimated total against the brief target.
    total = round(sum(estimates.values()), 2)
    target = brief.target_duration_s
    if target > 0 and abs(total - target) / target > DURATION_TOLERANCE:
        if total > target:
            picked = max(scenes, key=lambda s: estimates[s["id"]])
            direction = "over"
        else:
            picked = min(scenes, key=lambda s: estimates[s["id"]])
            direction = "under"
        findings.append({
            "scene_id": picked["id"],
            "criterion": "duration",
            "detail": ("estimated narration total %.1fs %s target "
                       "%.1fs (>%d%% off; %s); this scene is longest/"
                       "shortest at %.1fs"
                       % (total, direction, target,
                          int(DURATION_TOLERANCE * 100), basis,
                          estimates[picked["id"]])),
        })

    # Visual support: each visual prompt must share a content word
    # with its own narration (a nice image is not enough).
    for scene in scenes:
        if not (set(_content_words(scene["visual_prompt"]))
                & set(_content_words(scene["narration"]))):
            findings.append({
                "scene_id": scene["id"],
                "criterion": "visual-support",
                "detail": ("visual prompt shares no content word with "
                           "its narration, so it does not support it"),
            })

    return findings, estimates, basis or ""


# Critique criteria a Muse Spark response may use. A Spark finding
# naming anything else is rejected, never silently accepted.
ALLOWED_CRITERIA = frozenset({
    "hook", "relevance", "repetition-narration", "repetition-visual",
    "duration", "visual-support",
})


class SparkCritiqueError(ValueError):
    """A Muse Spark critique response was rejected: malformed,
    missing, or unparseable. Nothing is invented to replace it."""


def parse_spark_critique_response(raw, scene_ids):
    """Parse and validate a Muse Spark critique response into findings.

    ``raw`` must be a string holding a JSON list (a Markdown fence
    around the JSON is tolerated) of {scene_id, criterion, detail}.
    ``scene_ids`` lists the valid scene IDs. Every item is checked:
    scene_id must name a real scene, criterion must be one of
    ALLOWED_CRITERIA, detail must be a non-empty string. Anything
    else raises SparkCritiqueError naming the problem -- the response
    is rejected, never patched up, and no findings are invented.
    An empty list is a valid "no findings" verdict, not a missing
    response. Each returned finding carries source "spark".
    """
    if not isinstance(raw, str) or not raw.strip():
        raise SparkCritiqueError(
            "Spark critique response is missing or empty: rejecting "
            "the response, not inventing findings")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise SparkCritiqueError(
            "Spark critique response is not parseable JSON: %s; "
            "rejecting the response, not inventing findings"
            % (exc,)) from exc
    if not isinstance(data, list):
        raise SparkCritiqueError(
            "Spark critique response must be a JSON list of findings, "
            "got %s: rejecting the response, not inventing findings"
            % type(data).__name__)
    valid = set(scene_ids)
    findings = []
    for pos, item in enumerate(data):
        where = "Spark finding at position %d" % pos
        if not isinstance(item, dict):
            raise SparkCritiqueError(
                "%s is not an object: rejecting the Spark critique "
                "response, not inventing findings" % where)
        sid = item.get("scene_id")
        if not isinstance(sid, str) or not sid.strip():
            raise SparkCritiqueError(
                "%s is missing its scene_id: rejecting the Spark "
                "critique response" % where)
        sid = sid.strip()
        if sid not in valid:
            raise SparkCritiqueError(
                "%s names unknown scene %r (known: %s): rejecting "
                "the Spark critique response"
                % (where, sid, sorted(valid)))
        criterion = item.get("criterion")
        if criterion not in ALLOWED_CRITERIA:
            raise SparkCritiqueError(
                "%s for scene %r uses criterion %r, outside %s: "
                "rejecting the Spark critique response"
                % (where, sid, criterion, sorted(ALLOWED_CRITERIA)))
        detail = item.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            raise SparkCritiqueError(
                "%s for scene %r (%s) is missing its detail: "
                "rejecting the Spark critique response"
                % (where, sid, criterion))
        findings.append({
            "scene_id": sid,
            "criterion": criterion,
            "detail": detail.strip(),
            "source": "spark",
        })
    return findings


def spark_critique(brief, scenes, spark_critique_fn, measured_wps=None):
    """One Muse Spark critique round, strictly validated.

    Calls ``spark_critique_fn(brief, scenes, measured_wps=...)`` for
    the raw model text, then parses and validates it. A malformed,
    missing, or unparseable response raises SparkCritiqueError; a
    failed model call raises SparkCritiqueError chaining the real
    error. Never invents findings, never falls back silently.
    """
    if spark_critique_fn is None:
        raise ScenePlanError(
            "spark_critique needs spark_critique_fn (Muse Spark in "
            "production, a stub in tests): refusing to invent a "
            "critique with no model call")
    try:
        raw = spark_critique_fn(brief, scenes,
                                measured_wps=measured_wps)
    except SparkCritiqueError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-hard, chained
        raise SparkCritiqueError(
            "Spark critique call failed: %s" % (exc,)) from exc
    return parse_spark_critique_response(
        raw, [s["id"] for s in scenes])


def combined_critique(brief, scenes, spark_critique_fn=None,
                      measured_wps=None):
    """Rule-based floor plus Muse Spark model critique, merged.

    Returns (findings, estimates, basis) like critique(). Rule-based
    findings carry source "rules", Spark findings carry source
    "spark", and both are kept: when Spark agrees with the floor on
    the same scene and criterion, the agreement stays visible as two
    findings instead of being folded away. The revision loop still
    revises each flagged scene once per cycle (it dedups by scene),
    so agreement never costs a double revision.
    ``spark_critique_fn`` is required: with None this raises
    ScenePlanError instead of silently returning a rule-only
    critique while the caller claims Spark ran.
    """
    if spark_critique_fn is None:
        raise ScenePlanError(
            "combined_critique needs spark_critique_fn (Muse Spark in "
            "production, a stub in tests): refusing a silent "
            "rule-only critique where a Spark critique was required")
    findings, estimates, basis = critique(
        brief, scenes, measured_wps=measured_wps)
    spark_findings = spark_critique(
        brief, scenes, spark_critique_fn, measured_wps=measured_wps)
    merged = [dict(f, source="rules") for f in findings]
    merged.extend(spark_findings)
    return merged, estimates, basis


def revise_loop(brief, scenes, critique_fn=None, revise_scene_fn=None,
                max_cycles=MAX_REVISION_CYCLES, measured_wps=None):
    """Run the bounded critique -> targeted revision loop.

    Only scenes named in the critique findings are revised, via
    revise_scene_fn(scene, reasons, brief) -> (new_narration,
    new_visual), which the caller wires to Muse Spark (tests inject
    a stub; there is no network call here). The default critique_fn
    is the deterministic rule-based floor; production passes a
    combined rule-plus-Spark critique so each cycle carries both.
    At most max_cycles critique+revision cycles run, then the loop
    stops: remaining findings become unmet_criteria (each with
    scene_id, criterion, detail, plus source when the finding names
    one) and editorial_pass is False. A clean critique means
    editorial_pass True with no unmet criteria.

    Returns (scenes, log) where log holds brief, cycles_used,
    attempts (per revised scene: cycle, criteria, before/after
    narration and visual prompt, status), unmet_criteria,
    editorial_pass, estimate_basis, spark_critique_findings (the
    Spark-sourced findings from the final critique round), and
    critique_sources (rule/Spark finding counts for that round).
    Attempts are capped: one revision per flagged scene per cycle.
    """
    if critique_fn is None:
        critique_fn = critique
    if revise_scene_fn is None:
        raise ScenePlanError(
            "revise_loop needs revise_scene_fn (Muse Spark in "
            "production, a stub in tests)")
    if max_cycles < 1:
        raise ScenePlanError("max_cycles must be >= 1")
    by_id = {s["id"]: s for s in scenes}
    attempts = []
    cycles_used = 0
    last_findings = []
    last_basis = ""
    for cycle in range(1, max_cycles + 1):
        findings, _estimates, basis = critique_fn(
            brief, scenes, measured_wps=measured_wps)
        last_findings = findings
        last_basis = basis
        if not findings:
            break
        cycles_used = cycle
        flagged = []
        for found in findings:
            if found["scene_id"] not in flagged:
                flagged.append(found["scene_id"])
        for sid in flagged:
            scene = by_id.get(sid)
            if scene is None:
                continue
            reasons = [f for f in findings if f["scene_id"] == sid]
            criteria = sorted({r["criterion"] for r in reasons})
            before_n = scene["narration"]
            before_v = scene["visual_prompt"]
            try:
                new_n, new_v = revise_scene_fn(scene, reasons, brief)
            except Exception as exc:
                attempts.append({
                    "cycle": cycle, "scene_id": sid,
                    "criteria": criteria,
                    "before_narration": before_n,
                    "before_visual": before_v,
                    "after_narration": before_n,
                    "after_visual": before_v,
                    "status": "failed",
                    "error": str(exc)[:300],
                })
                continue
            if (not isinstance(new_n, str) or not new_n.strip()
                    or not isinstance(new_v, str)
                    or not new_v.strip()):
                attempts.append({
                    "cycle": cycle, "scene_id": sid,
                    "criteria": criteria,
                    "before_narration": before_n,
                    "before_visual": before_v,
                    "after_narration": before_n,
                    "after_visual": before_v,
                    "status": "failed",
                    "error": ("revision returned an empty narration "
                              "or visual prompt: kept the original"),
                })
                continue
            narration_changed = new_n.strip() != before_n
            visual_changed = new_v.strip() != before_v
            if not narration_changed and not visual_changed:
                # The model returned the scene unchanged: record the
                # attempt honestly instead of claiming a revision, and
                # leave the scene for the next critique to re-check.
                attempts.append({
                    "cycle": cycle, "scene_id": sid,
                    "criteria": criteria,
                    "before_narration": before_n,
                    "before_visual": before_v,
                    "after_narration": before_n,
                    "after_visual": before_v,
                    "status": "unchanged",
                })
                continue
            # Label what actually changed: narration-revised only
            # when narration changed, visual-revised only when the
            # visual changed, revised when both did. Identical
            # before/after text is never shown as revised.
            if narration_changed and visual_changed:
                status = "revised"
            elif narration_changed:
                status = "narration-revised"
            else:
                status = "visual-revised"
            scene["narration"] = new_n.strip()
            scene["visual_prompt"] = new_v.strip()
            scene["revision_status"] = status
            attempts.append({
                "cycle": cycle, "scene_id": sid,
                "criteria": criteria,
                "before_narration": before_n,
                "before_visual": before_v,
                "after_narration": scene["narration"],
                "after_visual": scene["visual_prompt"],
                "status": status,
            })
    else:
        # Ran every cycle without a clean critique: re-check once so
        # the surfaced criteria describe the final plan, not a stale
        # intermediate one.
        last_findings, _e, last_basis = critique_fn(
            brief, scenes, measured_wps=measured_wps)
    unmet = []
    for f in last_findings:
        entry = {"scene_id": f["scene_id"], "criterion": f["criterion"],
                 "detail": f["detail"]}
        if f.get("source"):
            entry["source"] = f["source"]
        unmet.append(entry)
    for scene in scenes:
        if any(u["scene_id"] == scene["id"] for u in unmet):
            scene["revision_status"] = "unmet"
    spark_findings = [f for f in last_findings
                      if f.get("source") == "spark"]
    log = {
        "brief": {"topic": brief.topic, "audience": brief.audience,
                  "target_duration_s": brief.target_duration_s},
        "cycles_used": cycles_used,
        "max_cycles": max_cycles,
        "attempts": attempts,
        "grouped_attempts": grouped_attempts(attempts),
        "unmet_criteria": unmet,
        "editorial_pass": not unmet,
        "estimate_basis": last_basis,
        "spark_critique_findings": spark_findings,
        "critique_sources": {
            "rules": sum(1 for f in last_findings
                         if f.get("source", "rules") == "rules"),
            "spark": len(spark_findings),
        },
    }
    return scenes, log


# Attempt statuses that count as a revision (narration and/or
# visual actually changed). "unchanged" and "failed" never count.
REVISED_STATUSES = ("revised", "narration-revised", "visual-revised")


def grouped_attempts(attempt_list):
    """Deduplicate revision attempts across cycles, grouped by
    scene and criterion instead of stacked per cycle.

    Returns a list of {scene_id, criterion, cycles, status,
    before_narration, after_narration, before_visual,
    after_visual}: cycles lists every cycle that touched that
    scene+criterion, before_* is the first before, after_* the
    last after, status the latest non-failed status. Sorted by
    scene then criterion, so the log and the UI show one line
    per scene+criterion no matter how many cycles ran.
    """
    grouped = {}
    order = []
    for attempt in attempt_list or []:
        for criterion in attempt.get("criteria", []):
            key = (attempt.get("scene_id"), criterion)
            if key not in grouped:
                grouped[key] = {
                    "scene_id": attempt.get("scene_id"),
                    "criterion": criterion,
                    "cycles": [],
                    "status": attempt.get("status"),
                    "before_narration": attempt.get("before_narration"),
                    "before_visual": attempt.get("before_visual"),
                    "after_narration": attempt.get("after_narration"),
                    "after_visual": attempt.get("after_visual"),
                }
                order.append(key)
            entry = grouped[key]
            if attempt.get("cycle") not in entry["cycles"]:
                entry["cycles"].append(attempt.get("cycle"))
            entry["after_narration"] = attempt.get("after_narration")
            entry["after_visual"] = attempt.get("after_visual")
            if attempt.get("status") != "failed":
                entry["status"] = attempt.get("status")
    return [grouped[key]
            for key in sorted(order, key=lambda k: (str(k[0]), str(k[1])))]


def director_decisions(log, id_to_index=None):
    """Concise QC decision entries for director revisions.

    Each entry names the scene, the cycle, the triggering criteria,
    and the before/after narration and visual prompt. Entries for
    unmet criteria after the cap are appended as surfacing (not
    passing) decisions. id_to_index maps scene ID -> 0-based index.
    """
    id_to_index = id_to_index or {}
    entries = []
    for attempt in log.get("attempts", []):
        idx = id_to_index.get(attempt["scene_id"])
        what = {"revised": "revised",
                "narration-revised": "narration revised",
                "visual-revised": "visual revised"}.get(
                    attempt.get("status"), attempt.get("status"))
        entries.append({
            "decision": (
                "director cycle %d: scene %s %s (%s)"
                % (attempt["cycle"], attempt["scene_id"], what,
                   ", ".join(attempt["criteria"]))),
            "stage": "director",
            "scene": idx,
            "scene_id": attempt["scene_id"],
            "criteria": list(attempt["criteria"]),
            "before_narration": attempt["before_narration"],
            "after_narration": attempt["after_narration"],
            "before_visual": attempt["before_visual"],
            "after_visual": attempt["after_visual"],
            "status": attempt["status"],
            "retried": attempt["cycle"] > 1,
        })
    for unmet in log.get("unmet_criteria", []):
        entries.append({
            "decision": (
                "director surfaced UNMET criterion, not passed: "
                "scene %s still fails %s after %d cycle(s): %s"
                % (unmet["scene_id"], unmet["criterion"],
                   log.get("cycles_used", 0), unmet["detail"][:200])),
            "stage": "director",
            "scene": id_to_index.get(unmet["scene_id"]),
            "scene_id": unmet["scene_id"],
            "criteria": [unmet["criterion"]],
            "status": "unmet",
            "retried": False,
        })
    return entries


try:
    from meta_sam import concept_for_subject as _concept_for_subject
    from meta_sam import _SKIP_WORDS as _SAM_SKIP_WORDS
except ImportError:  # pragma: no cover - meta_sam ships with the repo
    _concept_for_subject = None
    _SAM_SKIP_WORDS = frozenset()

# Same rule in every path: the director-level stopwords plus the
# full meta_sam skip set filter identically, so a word rejected by
# meta_sam.concept_for_subject can never slip back in here.
STOPWORDS = frozenset(set(STOPWORDS) | set(_SAM_SKIP_WORDS))


def derive_sam_concept(scene):
    """Derive the SAM concept from the visual prompt or scene subject.

    Narration is never consulted, so narration stopwords and generic
    verbs ("in", "carried", "thousand"-style filler) can never become
    the concept -- the Phase A failure mode. Adjectives
    ("brilliant", "beautiful", "powerful", "quickly"), abstract
    nouns ("hook"), and mass nouns without a bounded subject
    ("rain" as weather, "water") are rejected the same way -- the
    finish-brief failure mode, where "hook", "brilliant", and "rain"
    concepts found 0 SAM matches on every scene. If no usable
    concept comes from the visual prompt or subject, this raises
    ValueError naming the scene: the caller (render.sam_check)
    records the SAM check as skipped with that reason and keeps the
    image without regenerating. One bad concept therefore never
    costs two regens.

    Returns {concept, visual_subject, source, fixed}: source names
    which field won (visual_prompt, visual, or subject) and fixed is
    True when the primary visual_prompt was absent and a fallback
    field had to supply the subject. Both the visual subject and the
    concept used are returned so the fix stays visible downstream.
    """
    if not isinstance(scene, dict):
        raise ValueError("scene must be a dict, got %r" % (scene,))
    if _concept_for_subject is None:
        raise ValueError("SAM concept backend is unavailable")
    candidates = []
    for key in ("visual_prompt", "visual", "subject"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append((key, value.strip()))
    if not candidates:
        raise ValueError(
            "scene %r has no visual prompt or subject: fix the "
            "concept from the visual prompt first, before "
            "regenerating any image; refusing to derive a concept "
            "from narration stopwords"
            % (scene.get("id", "?"),))
    errors = []
    for pos, (key, subject) in enumerate(candidates):
        try:
            concept = _concept_for_subject(subject)
        except ValueError as exc:
            errors.append("%s: %s" % (key, exc))
            continue
        if (not concept or concept.lower() in STOPWORDS
                or concept.lower() in _SAM_SKIP_WORDS):
            errors.append("%s: derived stopword %r" % (key, concept))
            continue
        return {"concept": concept, "visual_subject": subject,
                "source": key, "fixed": pos > 0}
    raise ValueError(
        "scene %r has no usable visual concept (%s): fix the "
        "concept from the visual prompt first, before regenerating "
        "any image" % (scene.get("id", "?"), "; ".join(errors)))
