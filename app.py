"""ReelDirector studio console server (strict Meta-only build).

Serves the console UI at / and these JSON endpoints:
  GET  /api/health  -> {"ok": true, "mode": "live"|"demo"}
  POST /api/key     -> {"ok": true}; one pasted key, in-process only
  GET  /api/sample  -> {"topic": ...} one sample topic to try
  POST /api/script  -> live: {"script": [...], "mode": "live"} via
                       Muse Spark draft + Muse Wonder revise. Without
                       a key: a clearly labelled demo-preview script
                       that MUST never be rendered into a video.
  POST /api/video   -> {"job_id": ...} starts a REAL render job, key
                       required: script (Spark draft, Wonder revise)
                       -> Muse Image scenes -> SAM 3.1 check ->
                       MMS open-weight voice -> ffmpeg assembly -> QC.
                       Without META_API_KEY it fails with a clear
                       error instead of rendering anything.
                       Stages report real progress; nothing is marked
                       done until its step actually finishes.
  GET  /api/job/<id> -> job status, stage states, video URL, QC report.
  GET  /videos/<name> -> the finished MP4 for a completed job.

The key is read from the environment only. It is never written to disk,
never logged, and never committed. Run: python app.py, then open
http://127.0.0.1:5000.

Every backend is Meta: script Spark + Wonder, images Muse Image,
scene check SAM 3.1, voice facebook/mms-tts-eng open weights run
locally. A failed Meta call fails the job with its real error.
"""

import json
import os
import subprocess
import threading
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory

import meta_backend
from meta_backend import (critique_scenes, describe_visual,
                          draft_script, list_models, repair_critique,
                          revise_scene, revise_with_note)
import director
import render
from meta_image import MODEL as IMAGE_MODEL

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(APP_DIR, "ui")
VIDEOS_DIR = os.path.join(APP_DIR, "videos")
os.makedirs(VIDEOS_DIR, exist_ok=True)


def _load_dotenv():
    """Tiny manual .env loader (no new dependency). Fills in only
    variables the real environment does not already set."""
    try:
        with open(os.path.join(APP_DIR, ".env"), encoding="utf-8") as f:
            pass
    except OSError:
        return
    with open(os.path.join(APP_DIR, ".env"), encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'").strip('"').strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# Key pasted in the console via POST /api/key. In-process memory
# only: never logged, never printed, never written to disk.
_MEMORY_KEY = {"key": None}

SAMPLE_TOPIC = ("A tiny robot director who shoots midnight movies "
                "on a desert rooftop")

app = Flask(__name__)

# UI preview ONLY. Never rendered: /api/video refuses to run without
# a key, so these lines can never become a video.
DEMO_PREVIEW_SCRIPT = [
    "Hook: the director does not film first. It plans first.",
    "An agent reads the goal, breaks it into scenes, and checks each one.",
    "Script, visuals, and voice are built as separate passes.",
    "A QC gate reviews every scene before anything is assembled.",
    "The result is a finished video, directed end to end by the agent.",
]
DEMO_PREVIEW_NOTE = ("Preview only: set META_API_KEY and restart for a "
                     "real render. This script is never rendered to video.")

STAGE_KEYS = ["script", "revise", "scenes", "sam", "voice", "assemble", "qc"]
JOBS = {}
JOBS_LOCK = threading.Lock()

JOB_FILENAME = "job.json"

# Scene statuses that are never rewritten by failure/interrupt
# handling: finished or user-flagged work stays visible as-is.
TERMINAL_SCENE = ("done", "error", "dirty")


class _JobCancelled(Exception):
    """Cooperative cancellation: raised at the next stage or scene
    boundary after POST /api/job/<id>/cancel. An in-flight model
    call or ffmpeg step is NOT killed; it runs to its own timeout
    first, which is the stated cancellation limit."""


def _now():
    return time.time()


def _job_elapsed(job, at=None):
    """Seconds since the job started running (live value: pass at=None)."""
    started = job.get("started_at")
    if not started:
        return 0.0
    end = job.get("finished_at") or (at if at is not None else _now())
    return max(0.0, float(end) - float(started))


def _public_job(job):
    """API view of a job: everything except the server-side out_dir,
    plus a live elapsed-seconds counter. Never carries the API key:
    the key lives only in the environment or in-process memory and
    is never written into job state."""
    view = {k: v for k, v in job.items() if k != "out_dir"}
    view["elapsed_s"] = round(_job_elapsed(job), 1)
    return view


def _save_job_by_id(job_id):
    """Persist one job's state plus asset references to
    videos/<job_id>/job.json (atomic tmp+rename). Called after every
    stage/scene transition so a page refresh or a server restart
    finds the latest progress on disk. Never writes the API key."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        snapshot = {k: v for k, v in job.items() if k != "out_dir"}
        out_dir = job.get("out_dir")
    if not out_dir:
        return
    try:
        os.makedirs(out_dir, exist_ok=True)
        tmp = os.path.join(out_dir, JOB_FILENAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=1)
        os.replace(tmp, os.path.join(out_dir, JOB_FILENAME))
    except OSError:
        pass  # persistence is best-effort; the in-memory job is truth


def _disk_files(out_dir):
    """Names of generated files actually on disk (preserved work)."""
    try:
        return sorted(
            name for name in os.listdir(out_dir)
            if os.path.isfile(os.path.join(out_dir, name))
            and name != JOB_FILENAME and not name.endswith(".tmp"))
    except OSError:
        return []


def _restore_jobs(target_dir=None):
    """Rebuild in-memory jobs from videos/*/job.json at startup.

    Completed and failed jobs come back as-is (resumable via retry
    when failed). A job that was queued or running when the server
    died cannot resume mid-stage -- its worker thread is gone -- so
    it is marked "interrupted": completed files stay on disk and are
    listed as preserved, working stages/scenes are marked
    interrupted (finished scenes keep "done"), and the job must be
    re-run from the start with POST /api/job/<id>/retry.
    Returns {"restored": n, "interrupted": m}.
    """
    base = target_dir or VIDEOS_DIR
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {"restored": 0, "interrupted": 0}
    restored, interrupted = 0, 0
    for name in names:
        path = os.path.join(base, name, JOB_FILENAME)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        with JOBS_LOCK:
            if data["id"] in JOBS:
                continue
        out_dir = os.path.join(base, data["id"])
        data["out_dir"] = out_dir
        if data.get("status") in ("queued", "running"):
            was = data["status"]
            data["status"] = "interrupted"
            data["interrupted"] = True
            data["interrupt_reason"] = (
                "server restarted while this job was %s: the worker "
                "thread is gone, so the job cannot resume mid-stage" % was)
            data["preserved"] = _disk_files(out_dir)
            data["must_rerun"] = (
                "re-run this job from the start with "
                "POST /api/job/%s/retry; the files above stay on disk "
                "until the re-run replaces them" % data["id"])
            data["finished_at"] = data.get("finished_at") or _now()
            for key in STAGE_KEYS:
                if data.get("stages", {}).get(key) == "working":
                    data["stages"][key] = "interrupted"
            for rec in data.get("scenes", []):
                if rec.get("status") not in TERMINAL_SCENE + ("interrupted",):
                    if rec.get("status") != "waiting" or was == "running":
                        rec["status"] = "interrupted"
            interrupted += 1
        with JOBS_LOCK:
            JOBS[data["id"]] = data
        restored += 1
        _save_job_by_id(data["id"])
    return {"restored": restored, "interrupted": interrupted}


def _api_key():
    if _MEMORY_KEY["key"]:
        return _MEMORY_KEY["key"]
    return os.environ.get("META_API_KEY", "").strip() or None


def _live_mode():
    return bool(_api_key())


def _safe_error(exc, limit=500):
    """Understandable error text without secrets and without raw
    sensitive provider responses: truncates, and redacts the
    configured key if it ever appears inside the message."""
    text = str(exc) if not isinstance(exc, str) else exc
    key = _api_key()
    if key and key in text:
        text = text.replace(key, "[REDACTED KEY]")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "..."
    return text or "unknown error"


def _media_tool_status(name):
    """Accurate local availability check for ffmpeg/ffprobe: a tools/
    build, then PATH, then the static-ffmpeg download (fetched once
    on first use), then a real --version run. source names where
    the binary came from: tools, path, static-ffmpeg, or missing."""
    path, source = render._tool_with_source(name)
    if source == "missing":
        return {"available": False, "version": None, "source": source,
                "error": "%s not found (automatic download failed): "
                         "install ffmpeg and retry" % name}
    try:
        r = subprocess.run([path, "-version"], capture_output=True,
                           text=True, timeout=15)
    except Exception as e:
        return {"available": False, "version": None, "source": source,
                "error": "%s present but not runnable: %s"
                         % (name, _safe_error(e, 200))}
    if r.returncode != 0:
        return {"available": False, "version": None, "source": source,
                "error": "%s --version failed" % name}
    first = (r.stdout or "").strip().splitlines()
    return {"available": True, "source": source,
            "version": first[0][:120] if first else name}


def _connection_status(timeout=10):
    """One real authenticated check (models list, bounded by timeout).
    Returns {"checked", "ok", "models"|None, "error"|None} with the
    real failure, sanitized, when the check fails."""
    key = _api_key()
    if not key:
        return {"checked": False, "ok": False, "models": None,
                "error": None}
    try:
        models = list_models(api_key=key, timeout=timeout)
        count = len(models) if isinstance(models, (list, tuple)) else None
        return {"checked": True, "ok": True, "models": count,
                "error": None}
    except Exception as e:
        return {"checked": True, "ok": False, "models": None,
                "error": _safe_error(e)}


IMAGE_PROVIDER = IMAGE_MODEL
IMAGE_FALLBACK = "none (Meta-only, fail-hard)"


@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/scenes/<path:name>")
def scenes(name):
    return send_from_directory(os.path.join(UI_DIR, "scenes"), name)


@app.get("/fonts/<path:name>")
def fonts(name):
    return send_from_directory(os.path.join(UI_DIR, "fonts"), name)


@app.get("/videos/<path:name>")
def videos(name):
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return jsonify({"error": "invalid video name"}), 400
    return send_from_directory(VIDEOS_DIR, name)


@app.get("/api/health")
def health():
    """Truthful status, three separate facts, never conflated:

    - key.configured: a key is present ("Key configured"). Key
      presence alone never reads "API online".
    - connection: one real authenticated check with a timeout, and
      its real sanitized failure when it fails.
    - ffmpeg / ffprobe: actually probed, accurately reported.
    mode stays for older clients: live means a key is present, not
    that the connection works.
    """
    key_present = _live_mode()
    connection = _connection_status()
    return jsonify({
        "ok": True,
        "mode": "live" if key_present else "demo",
        "key": {"configured": key_present,
                "label": "Key configured" if key_present else "No key"},
        "connection": connection,
        "ffmpeg": _media_tool_status("ffmpeg"),
        "ffprobe": _media_tool_status("ffprobe"),
    })


@app.get("/api/style")
def style_options():
    """Visual style presets and color themes (options, not text).

    The brief panel renders these as pickers: six style presets
    plus six color themes, each with a short name and a small
    preview description. The flat-vector preset keeps the existing
    robot-director look as one option, never a required character
    or colour. Send the picked ids as style/color with
    POST /api/script or POST /api/video; style_custom is an
    optional short phrase only, never a paragraph."""
    return jsonify({
        "styles": [{"id": sid, "name": preset["name"],
                    "blurb": preset["blurb"]}
                   for sid, preset in render.STYLE_PRESETS.items()],
        "colors": [{"id": cid, "name": theme["name"],
                    "blurb": theme["blurb"],
                    "swatch": theme["swatch"]}
                   for cid, theme in render.COLOR_THEMES.items()],
        "default_style": render.DEFAULT_STYLE,
        "default_color": render.DEFAULT_COLOR,
        "max_custom_phrase": render.MAX_CUSTOM_PHRASE,
        "note": ("Pick style/color ids; send them with "
                 "POST /api/script or POST /api/video."),
    })


def _parse_style_options(body):
    """Validated (style_id, color_id, custom) from a request body.

    style must be a preset id, color a theme id, style_custom a
    short phrase at most MAX_CUSTOM_PHRASE characters. All three
    are optional (None means the default). Returns
    ((style, color, custom), None) or (None, (error_body, status)):
    unknown ids and paragraph-length custom text fail with a clear
    400 naming the valid options, never silently re-typed.
    """
    style = body.get("style")
    color = body.get("color")
    custom = body.get("style_custom")
    if style is not None:
        if not isinstance(style, str) or not style.strip():
            return None, (jsonify(
                {"error": "style must be a style option: %s"
                          % sorted(render.STYLE_PRESETS)}), 400)
        style = style.strip()
        if style not in render.STYLE_PRESETS:
            return None, (jsonify(
                {"error": "unknown style %r: pick one of %s"
                          % (style,
                             sorted(render.STYLE_PRESETS))}), 400)
    if color is not None:
        if not isinstance(color, str) or not color.strip():
            return None, (jsonify(
                {"error": "color must be a color theme: %s"
                          % sorted(render.COLOR_THEMES)}), 400)
        color = color.strip()
        if color not in render.COLOR_THEMES:
            return None, (jsonify(
                {"error": "unknown color theme %r: pick one of %s"
                          % (color,
                             sorted(render.COLOR_THEMES))}), 400)
    if custom is not None:
        if not isinstance(custom, str) or not custom.strip():
            custom = None
        else:
            try:
                render.resolve_custom_phrase(custom)
            except ValueError as exc:
                return None, (jsonify({"error": str(exc)}), 400)
            custom = custom.strip()
    return (style, color, custom), None


def _style_labels(style_id, color_id):
    """Display names for the recorded picks (defaults resolved)."""
    style_name = (render.style_label(style_id)
                  or render.style_label(render.DEFAULT_STYLE))
    color_name = (render.color_label(color_id)
                  or render.color_label(render.DEFAULT_COLOR))
    return style_name, color_name


@app.post("/api/key")
def set_key():
    """Accept one pasted key into in-process memory only."""
    body = request.get_json(silent=True) or {}
    key = body.get("key", "")
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "key is required"}), 400
    _MEMORY_KEY["key"] = key.strip()
    return jsonify({"ok": True})


@app.get("/api/sample")
def sample():
    return jsonify({"topic": SAMPLE_TOPIC})


def _nonempty_lines(text, who):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("%s returned an empty script" % who)
    return lines


def _live_script_lines(topic, api_key=None, scene_count=None,
                       audience=None):
    """Spark draft, then Wonder revise with change note. Both must
    succeed; any failure raises and fails the script stage. Returns
    ([lines], change_note).

    The key (pasted in-memory key or environment, via _api_key)
    is threaded into both model calls: a pasted key with no
    environment key still generates. Without any key the backend
    raises its clear missing-key error, never a silent fallback.
    """
    key = api_key if api_key is not None else _api_key()
    draft = draft_script(topic, api_key=key, scene_count=scene_count)
    _nonempty_lines(draft, "Spark")
    final, note = revise_with_note(draft, topic, api_key=key,
                                   scene_count=scene_count,
                                   audience=audience)
    return _nonempty_lines(final, "Wonder"), note


def _parse_visual_line(raw):
    """Pull the VISUAL: line from a Spark visual description.

    Returns the description or None when the response is unusable;
    the caller retries once, then fails visibly instead of
    inventing a visual.
    """
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("visual:"):
            visual = stripped.split(":", 1)[1].strip()
            if visual:
                return visual
    return None


def _visual_is_narration_copy(visual, narration):
    """True when the visual merely repeats the narration line.

    Compared on normalized text (case/whitespace folded, art
    boilerplate stripped): a visual that IS the narration must be
    rejected and regenerated, never silently used as the visual.
    """
    def _norm(text):
        return " ".join(str(text or "").lower().split())

    core = _norm(visual)
    for prefix in ("flat vector illustration,", "flat vector illustration"):
        if core.startswith(prefix):
            core = core[len(prefix):].strip(" ,")
            break
    return bool(core) and core == _norm(narration)


def _spark_visual_for_scene(narration, brief, api_key=None):
    """One Spark visual description for a narration line.

    Same Muse Spark model only (Meta-only rule): one retry when
    Spark returns no VISUAL line or returns the narration as the
    visual, then the real error propagates so the script stage
    fails hard instead of silently using narration as the visual.
    Returns the visual description.
    """
    key = api_key if api_key is not None else _api_key()
    last = None
    for _ in (1, 2):
        try:
            raw = describe_visual(brief.topic, brief.audience,
                                  narration, api_key=key)
        except Exception as exc:  # noqa: BLE001 - retried once
            last = exc
            continue
        visual = _parse_visual_line(raw)
        if visual is None:
            last = RuntimeError(
                "Spark visual description was unusable (missing "
                "VISUAL: line) for narration %r" % (narration[:80],))
            continue
        if _visual_is_narration_copy(visual, narration):
            last = RuntimeError(
                "Spark returned the narration as the visual "
                "description for %r: rejecting, never silently "
                "using narration as the visual" % (narration[:80],))
            continue
        return visual
    raise last


def estimate_plan_duration(lines):
    """Estimated total narration seconds for approved lines.

    Uses the stated conservative rate (no measured voice yet) via
    director.estimate_seconds. Returns (total_s, basis).
    """
    total = 0.0
    basis = ""
    for line in lines:
        seconds, basis = director.estimate_seconds(line)
        total += seconds
    return round(total, 1), basis


def duration_preflight(estimated_total_s, target_s):
    """Pre-flight duration warning, or None when the estimate fits.

    More than 30 percent over target warns with the estimated
    total, the target, and the suggested fix (fewer scenes or a
    longer target). Never silently renders past the target: the
    caller must hold production until the user approves that
    duration or fixes the target.
    """
    if not target_s or target_s <= 0:
        return None
    if estimated_total_s <= float(target_s) * (
            1.0 + director.DURATION_TOLERANCE):
        return None
    return {
        "over_target": True,
        "estimated_total_s": estimated_total_s,
        "target_s": float(target_s),
        "warning": ("estimated narration total %.1fs is more than "
                    "%d%% over the %.1fs target; the finished video "
                    "will run long" % (
                        estimated_total_s,
                        int(director.DURATION_TOLERANCE * 100),
                        float(target_s))),
        "suggestion": ("approve this duration, use fewer scenes, "
                       "or set a longer target"),
    }


def build_script_plan(topic, audience=None, target_duration_s=None,
                      scene_count=None, api_key=None):
    """Full script stage: draft, Wonder revise, Spark visuals,
    director critique and revision. No images, no voice, no
    assembly: this stops at the approvable plan.

    Returns a payload dict with brief, script lines, scenes (each
    with narration, visual_prompt, revision status, and
    before/after), director (decisions log with grouped attempts),
    unmet_criteria, editorial_pass, revision_note, and
    duration_estimate. Raises on any model failure: a broken plan
    never proceeds to paid media.
    """
    key = api_key if api_key is not None else _api_key()
    count = meta_backend._scene_count_or_default(
        scene_count)
    lines, note = _live_script_lines(topic, api_key=key,
                                     scene_count=count,
                                     audience=audience)
    if len(lines) != count:
        raise RuntimeError(
            "script stage expected %d lines but Wonder returned %d: "
            "rejecting the script instead of planning from a short "
            "script" % (count, len(lines)))
    brief = director.make_brief(
        topic, audience=audience,
        target_duration_s=target_duration_s,
        scene_count=len(lines))
    visuals = [_spark_visual_for_scene(line, brief, api_key=key)
               for line in lines]
    raw = [{"id": "scene-%d" % (i + 1), "narration": line,
            "visual_prompt": visual}
           for i, (line, visual) in enumerate(zip(lines, visuals))]
    scenes = director.validate_scene_plan(raw)
    each = brief.target_duration_s / len(scenes)
    for scene in scenes:
        scene["intended_duration_s"] = round(each, 2)

    def _script_critique(brief_arg, plan_scenes, measured_wps=None):
        return director.combined_critique(
            brief_arg, plan_scenes,
            spark_critique_fn=lambda b, s, measured_wps=None: (
                _spark_critique_raw(b, s, measured_wps=measured_wps,
                                    api_key=key)),
            measured_wps=measured_wps)

    def _script_revise(scene_arg, reasons_arg, brief_arg):
        return _spark_revise_scene(scene_arg, reasons_arg, brief_arg,
                                   api_key=key)

    scenes, director_log = director.revise_loop(
        brief, scenes, critique_fn=_script_critique,
        revise_scene_fn=_script_revise)
    failed = [a for a in director_log.get("attempts", [])
              if a.get("status") == "failed"]
    if failed:
        first = failed[0]
        raise RuntimeError(
            "director Spark revision failed for %s (%s): %s"
            % (first["scene_id"], ", ".join(first["criteria"]),
               first.get("error", "unknown error")))
    estimated_total_s, basis = estimate_plan_duration(
        [s["narration"] for s in scenes])
    return {
        "brief": {"topic": brief.topic,
                  "audience": brief.audience,
                  "target_duration_s": brief.target_duration_s},
        "script": [s["narration"] for s in scenes],
        "visuals": [s["visual_prompt"] for s in scenes],
        "scenes": scenes,
        "director": director_log,
        "unmet_criteria": director_log.get("unmet_criteria", []),
        "editorial_pass": director_log.get("editorial_pass", False),
        "revision_note": note,
        "duration_estimate": {
            "estimated_total_s": estimated_total_s,
            "target_s": brief.target_duration_s,
            "estimate_basis": basis,
            "preflight": duration_preflight(
                estimated_total_s, brief.target_duration_s),
        },
    }


def _script_job_record(job_id, payload, style, aspect_ratio, width,
                       height, target_explicit, color=None,
                       style_custom=None):
    """Waiting-for-approval job record: the approved-to-be plan with
    script and director review done, production waiting, and no
    media on disk yet. The picked style preset and color theme are
    recorded here so a changed selection before approval changes
    the next job."""
    records = build_scene_records(payload["script"], style=style,
                                  color=color,
                                  style_custom=style_custom)
    style_name, color_name = _style_labels(style, color)
    last_attempt = {}
    for attempt in payload["director"].get("attempts", []):
        last_attempt[attempt["scene_id"]] = attempt
    unmet_by_scene = {}
    for unmet in payload.get("unmet_criteria", []):
        unmet_by_scene.setdefault(
            unmet["scene_id"], []).append(unmet["criterion"])
    for rec, scene in zip(records, payload["scenes"]):
        rec["scene_id"] = scene["id"]
        rec["visual_prompt"] = scene["visual_prompt"]
        rec["revision_status"] = scene["revision_status"]
        attempt = last_attempt.get(scene["id"])
        if attempt:
            rec["revision_criteria"] = list(attempt["criteria"])
            rec["before_narration"] = attempt["before_narration"]
            rec["after_narration"] = attempt["after_narration"]
            rec["before_visual"] = attempt["before_visual"]
            rec["after_visual"] = attempt["after_visual"]
        elif scene["id"] in unmet_by_scene:
            rec["revision_criteria"] = unmet_by_scene[scene["id"]]
    now = _now()
    return {
        "id": job_id, "topic": payload["brief"]["topic"],
        "status": "waiting_approval",
        "stages": {"script": "done", "revise": "done",
                   "scenes": "waiting", "sam": "waiting",
                   "voice": "waiting", "assemble": "waiting",
                   "qc": "waiting"},
        "script": list(payload["script"]),
        "visuals": list(payload["visuals"]),
        "mode": "live",
        "scenes": records,
        "video": None, "qc": None, "error": None,
        "revision_note": payload.get("revision_note"),
        "brief": dict(payload["brief"], aspect_ratio=aspect_ratio,
                      width=width, height=height,
                      target_explicit=target_explicit),
        "director": payload["director"],
        "unmet_criteria": list(payload.get("unmet_criteria", [])),
        "editorial_pass": payload.get("editorial_pass", False),
        "duration_estimate": payload["duration_estimate"],
        "style": style,
        "color": color,
        "style_custom": style_custom,
        "style_name": style_name,
        "color_name": color_name,
        "aspect_ratio": aspect_ratio,
        "width": width, "height": height,
        "out_dir": os.path.join(VIDEOS_DIR, job_id),
        "created_at": now, "started_at": now,
        "updated_at": now, "finished_at": None,
        "cancel_requested": False,
        "retries": 0, "last_error": None,
    }


@app.post("/api/script")
def script():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if not _live_mode():
        return jsonify({"script": DEMO_PREVIEW_SCRIPT, "mode": "demo-preview",
                        "note": DEMO_PREVIEW_NOTE})
    audience = body.get("audience")
    if audience is not None:
        if not isinstance(audience, str) or not audience.strip():
            return jsonify(
                {"error": "audience must be a non-empty string"}), 400
        audience = audience.strip()
    target_duration_s = body.get("target_duration_s")
    target_explicit = target_duration_s is not None
    if target_explicit:
        try:
            target_duration_s = float(target_duration_s)
        except (TypeError, ValueError):
            return jsonify(
                {"error": "target_duration_s must be a positive "
                          "number"}), 400
        if target_duration_s <= 0:
            return jsonify(
                {"error": "target_duration_s must be a positive "
                          "number"}), 400
    scene_count = body.get("scene_count", 5)
    try:
        count = meta_backend._scene_count_or_default(
            scene_count)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    parsed, err = _parse_style_options(body)
    if err:
        return err
    style, color, style_custom = parsed
    aspect_ratio = body.get("aspect_ratio", render.DEFAULT_ASPECT)
    if not isinstance(aspect_ratio, str):
        return jsonify(
            {"error": "aspect_ratio must be one of %s"
                      % sorted(render.ASPECTS)}), 400
    aspect_ratio = aspect_ratio.strip() or render.DEFAULT_ASPECT
    try:
        aspect_w, aspect_h = render.dims_for_aspect(aspect_ratio)
    except ValueError:
        return jsonify(
            {"error": "aspect_ratio must be one of %s"
                      % sorted(render.ASPECTS)}), 400
    try:
        payload = build_script_plan(
            topic, audience=audience,
            target_duration_s=target_duration_s,
            scene_count=count, api_key=_api_key())
    except Exception as exc:  # noqa: BLE001 - sanitized, never raw
        return jsonify({"error": _safe_error(exc)}), 502
    job_id = uuid.uuid4().hex[:12]
    record = _script_job_record(
        job_id, payload, style, aspect_ratio, aspect_w, aspect_h,
        target_explicit, color=color, style_custom=style_custom)
    with JOBS_LOCK:
        JOBS[job_id] = record
    _save_job_by_id(job_id)
    return jsonify({
        "job_id": job_id,
        "state": "waiting_approval",
        "mode": "live",
        "message": ("Waiting for your approval: review the script "
                    "and visuals, then produce. No images, no voice, "
                    "no video have been made."),
        "script": payload["script"],
        "visuals": payload["visuals"],
        "scenes": payload["scenes"],
        "style": style,
        "color": color,
        "style_custom": style_custom,
        "brief": record["brief"],
        "director": payload["director"],
        "grouped_decisions": director.grouped_attempts(
            payload["director"].get("attempts", [])),
        "unmet_criteria": payload["unmet_criteria"],
        "editorial_pass": payload["editorial_pass"],
        "revision_note": payload["revision_note"],
        "duration_estimate": payload["duration_estimate"],
    })


def build_scene_records(lines, style=None, color=None,
                        style_custom=None):
    """One storyboard record per narration line, for any scene count.

    Each record names the actual image provider (muse-image-1.0) and
    the fallback (none, Meta-only, fail-hard). Images, durations, and
    SAM notes fill in as the render reaches each scene. Director
    revision fields (scene_id, visual_prompt, revision_status,
    before/after, criteria) fill in when the Phase B director loop
    runs; they stay at their planned defaults otherwise. The picked
    style preset id, color theme id, optional short custom phrase,
    and their display names travel on every record so the image
    prompt for every scene uses the same selected style and color
    and the storyboard shows them, not just CSS.
    """
    style_name, color_name = _style_labels(style, color)
    records = []
    for i, line in enumerate(lines):
        records.append({
            "index": i,
            "scene_id": "scene-%d" % (i + 1),
            "narration": line,
            "visual_prompt": "flat vector illustration, " + line,
            "rendered_narration": None,
            "style": style,
            "color": color,
            "style_custom": style_custom,
            "style_name": style_name,
            "color_name": color_name,
            "image": None,
            "duration": None,
            "status": "waiting",
            "provider": IMAGE_PROVIDER,
            "fallback": IMAGE_FALLBACK,
            "sam": None,
            "dirty": False,
            "dirty_reason": None,
            "revision_status": "planned",
            "revision_criteria": [],
            "before_narration": None,
            "after_narration": None,
            "before_visual": None,
            "after_visual": None,
        })
    return records


def _parse_scene_revision(raw):
    """Pull NARRATION:/VISUAL: lines from a Spark scene revision.

    Returns (narration, visual) or (None, None) when the response is
    unusable; the caller then fails visibly instead of inventing
    content.
    """
    narration, visual = None, None
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("narration:"):
            narration = stripped.split(":", 1)[1].strip()
        elif low.startswith("visual:"):
            visual = stripped.split(":", 1)[1].strip()
    if narration and visual:
        return narration, visual
    return None, None


def _spark_critique_raw(brief, scenes, measured_wps=None, api_key=None):
    """Muse Spark critique calls for the production director loop.

    Returns the raw model text; director.combined_critique parses
    and validates it strictly, so a malformed, missing, or
    unparseable Spark response fails the job instead of passing
    silently as rule-only. At most 3 attempts with the same Muse
    Spark model (Meta-only rule): the first call, then up to two
    strict re-asks quoting the exact validation failure. Each
    attempt is validated here with
    director.parse_spark_critique_response so a re-ask only fires
    on a genuinely rejected shape; the final failure raises the
    real validation error. ``measured_wps`` is accepted to match
    the critique_fn signature and is unused: Spark judges the
    durations the rule-based floor already estimated.
    """
    scene_ids = [s.get("id") for s in scenes]
    key = api_key if api_key is not None else _api_key()
    last = None
    previous = None
    for attempt in (0, 1, 2):
        try:
            if attempt == 0:
                raw = critique_scenes(
                    brief.topic, brief.audience,
                    brief.target_duration_s, scenes, api_key=key)
            else:
                raw = repair_critique(
                    previous_bad=previous, problem=str(last)[:300],
                    topic=brief.topic, audience=brief.audience,
                    target_duration_s=brief.target_duration_s,
                    scene_ids=scene_ids, api_key=key)
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            last = exc
            previous = None
            continue
        previous = raw
        try:
            director.parse_spark_critique_response(raw, scene_ids)
            return raw
        except director.SparkCritiqueError as exc:
            last = exc
    if isinstance(last, director.SparkCritiqueError):
        raise last
    raise director.SparkCritiqueError(
        "Spark critique call failed: %s" % (last,)) from last


def _spark_revise_scene(scene, reasons, brief, api_key=None):
    """Revise ONE flagged scene with Muse Spark (same model only).

    The brief (topic, audience, target duration) and the exact
    criteria that flagged this scene travel in the prompt; nothing
    else is revised. One bounded retry with the same model, then the
    real error propagates so the job fails hard (Meta-only rule).
    Returns (new_narration, new_visual).
    """
    key = api_key if api_key is not None else _api_key()
    last = None
    for _ in (1, 2):
        try:
            raw = revise_scene(
                brief.topic, brief.audience,
                brief.target_duration_s, scene["narration"],
                scene["visual_prompt"], reasons, api_key=key)
        except Exception as exc:  # noqa: BLE001 - retried once, then raised
            last = exc
            continue
        narration, visual = _parse_scene_revision(raw)
        if narration and visual:
            return narration, visual
        last = RuntimeError(
            "Spark scene revision was unusable (missing NARRATION: "
            "or VISUAL: lines)")
    raise last


def _scene_event(job_id, index, event, info):
    """Fold render.py per-scene progress into the job's storyboard."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or index >= len(job.get("scenes", [])):
            return
        rec = job["scenes"][index]
        if event == "image" and info.get("ok"):
            rec["image"] = ("/videos/%s/scene_%d.png" % (job_id, index))
            rec["status"] = "image ready"
        elif event == "image":
            rec["status"] = "error"
        elif event == "sam" and info.get("ok"):
            result = info.get("result") or {}
            rec["sam"] = {"matches": result.get("matches"),
                          "concept": result.get("concept"),
                          "visual_subject": result.get(
                              "visual_subject", result.get("subject")),
                          "warning": result.get("warning")}
            if rec["status"] != "error":
                rec["status"] = "checked"
        elif event == "sam":
            rec["status"] = "error"
        elif event == "voice" and info.get("duration") is not None:
            rec["duration"] = info["duration"]
            rec["rendered_narration"] = rec["narration"]
            if rec["status"] != "error":
                rec["status"] = "voiced"
        elif event == "assembled" and info.get("ok"):
            rec["status"] = "done"
        elif event == "assembled":
            rec["status"] = "error"
    _save_job_by_id(job_id)


def _run_job(job_id, topic, approved, style=None,
               audience=None, target_duration_s=None,
               aspect_ratio=None, director_log=None,
               revision_note=None, color=None, style_custom=None):
    """Production worker: approved plan -> media. No text calls.

    ``approved`` is a list of {narration, visual} dicts from the
    approval gate (/api/video). Script and director review already
    ran in /api/script, so stages script+revise are marked done
    immediately with no work and no model calls -- they are not
    counted again as production work. Production runs Images ->
    Scene check -> Voice -> Assemble -> Checks through
    render.run_pipeline with real per-stage and per-scene
    progress. The approved lines are used exactly: nothing here
    drafts, revises, critiques, or re-revises any text, so the
    plan lines and the rendered lines are identical.
    """
    aspect = str(aspect_ratio or render.DEFAULT_ASPECT).strip()
    width, height = render.dims_for_aspect(aspect)
    job = JOBS[job_id]
    with JOBS_LOCK:
        job["aspect_ratio"] = aspect
        job["width"] = width
        job["height"] = height

    def mark(name, state):
        with JOBS_LOCK:
            if job.get("cancel_requested"):
                raise _JobCancelled()
            job["stages"][name] = state
            job["updated_at"] = _now()
        _save_job_by_id(job_id)

    def scene_event(index, event, info):
        _scene_event(job_id, index, event, info)
        with JOBS_LOCK:
            if job.get("cancel_requested"):
                raise _JobCancelled()

    try:
        if not approved:
            raise RuntimeError(
                "production needs the approved plan: plan scenes "
                "first with POST /api/script, review them, then "
                "produce. Production never re-drafts.")
        # Script + director review are already done (pre-approval):
        # marked done, never re-run here.
        mark("script", "done")
        mark("revise", "done")
        job["script"] = [a["narration"] for a in approved]
        job["visuals"] = [a["visual"] for a in approved]
        job["revision_note"] = revision_note
        job["brief"] = {"topic": topic, "audience": audience,
                        "target_duration_s": target_duration_s,
                        "aspect_ratio": aspect,
                        "width": width, "height": height}
        job["director"] = director_log
        style_name, color_name = _style_labels(style, color)
        job["style"] = style
        job["color"] = color
        job["style_custom"] = style_custom
        job["style_name"] = style_name
        job["color_name"] = color_name
        records = build_scene_records(
            [a["narration"] for a in approved], style=style,
            color=color, style_custom=style_custom)
        if director_log:
            last_attempt = {}
            for attempt in director_log.get("attempts", []):
                last_attempt[attempt["scene_id"]] = attempt
            unmet_by_scene = {}
            for unmet in director_log.get("unmet_criteria", []):
                unmet_by_scene.setdefault(
                    unmet["scene_id"], []).append(unmet["criterion"])
        else:
            last_attempt = {}
            unmet_by_scene = {}
        for pos, (rec, item) in enumerate(zip(records, approved)):
            sid = "scene-%d" % (pos + 1)
            rec["scene_id"] = sid
            rec["visual_prompt"] = item["visual"]
            attempt = last_attempt.get(sid)
            if attempt:
                rec["revision_status"] = (
                    "approved (%s)" % attempt["status"]
                    if attempt["status"] in
                    director.REVISED_STATUSES + ("unchanged",)
                    else attempt["status"])
                rec["revision_criteria"] = list(attempt["criteria"])
                rec["before_narration"] = attempt["before_narration"]
                rec["after_narration"] = attempt["after_narration"]
                rec["before_visual"] = attempt["before_visual"]
                rec["after_visual"] = attempt["after_visual"]
            elif sid in unmet_by_scene:
                rec["revision_status"] = "approved (unmet)"
                rec["revision_criteria"] = unmet_by_scene[sid]
            else:
                rec["revision_status"] = "approved"
        job["scenes"] = records
        scenes = []
        for pos, item in enumerate(approved):
            sid = "scene-%d" % (pos + 1)
            visual = item["visual"]
            try:
                subject = director.derive_sam_concept(
                    {"id": sid,
                     "visual_prompt": visual})["visual_subject"]
            except ValueError:
                # Fix the concept from the visual prompt first: fall
                # back to the visual text itself, which sam_check
                # re-derives (and rejects loudly if truly unusable).
                subject = visual
            scenes.append({"id": sid,
                           "narration": item["narration"],
                           "visual": visual,
                           "visual_prompt": visual,
                           "subject": subject,
                           "style": style,
                           "color": color,
                           "style_custom": style_custom})
        final, report = render.run_pipeline(
            scenes, topic, job["out_dir"], on_stage=mark,
            api_key=_api_key(),
            revision_note=revision_note or None,
            on_scene=scene_event, director_log=director_log,
            aspect_ratio=aspect)
        with JOBS_LOCK:
            job["video"] = "/videos/%s/%s" % (job_id, os.path.basename(final))
            job["qc"] = report
            job["status"] = "done"
            job["finished_at"] = _now()
        _save_job_by_id(job_id)
    except _JobCancelled:
        with JOBS_LOCK:
            states = job["stages"]
            for key in STAGE_KEYS:
                if states.get(key) == "working":
                    states[key] = "cancelled"
            for rec in job.get("scenes", []):
                if rec.get("status") not in TERMINAL_SCENE + ("cancelled",):
                    rec["status"] = "cancelled"
            job["status"] = "cancelled"
            job["finished_at"] = _now()
            job["error"] = ("cancelled by user: stopped at a stage "
                            "boundary; an in-flight step ran to its own "
                            "timeout first")
        _save_job_by_id(job_id)
    except Exception as exc:  # noqa: BLE001 - sanitized, never raw secrets
        with JOBS_LOCK:
            states = job["stages"]
            for key in STAGE_KEYS:
                if states.get(key) == "working":
                    states[key] = "error"
            for rec in job.get("scenes", []):
                if rec.get("status") in ("waiting", "image ready",
                                         "checked", "voiced"):
                    # The render stopped: anything not finished is
                    # failed, never left showing endlessly working.
                    rec["status"] = "error"
            job["status"] = "error"
            job["finished_at"] = _now()
            job["error"] = _safe_error(exc)
        _save_job_by_id(job_id)


def _approved_plan_from_body(body):
    """Approved (narration, visual) pairs from a produce call.

    Accepts either {"scenes": [{narration, visual_prompt}]} or
    {"script": [...], "visuals": [...]} (same length). Returns
    ([{narration, visual}], None) or (None, (error_body, status)):
    without approved lines AND approved visuals production
    refuses with a clear error and never silently re-drafts.
    """
    raw_scenes = body.get("scenes")
    if raw_scenes is not None:
        if not isinstance(raw_scenes, list) or not raw_scenes:
            return None, (jsonify(
                {"error": "scenes must be a non-empty list of "
                          "approved {narration, visual_prompt} "
                          "objects"}), 400)
        pairs = []
        for pos, item in enumerate(raw_scenes):
            if not isinstance(item, dict):
                return None, (jsonify(
                    {"error": "scene at position %d is not an "
                              "object: produce only runs on the "
                              "approved plan" % pos}), 400)
            narration = item.get("narration")
            visual = item.get("visual_prompt", item.get("visual"))
            if (not isinstance(narration, str) or not narration.strip()
                    or not isinstance(visual, str) or not visual.strip()):
                return None, (jsonify(
                    {"error": "scene at position %d needs approved "
                              "narration and an approved visual "
                              "prompt: produce only runs on the "
                              "approved plan" % pos}), 400)
            pairs.append({"narration": narration.strip(),
                          "visual": visual.strip()})
        return pairs, None
    script_lines = body.get("script")
    visuals = body.get("visuals")
    if script_lines is None or visuals is None:
        return None, (jsonify(
            {"error": "approved narration lines and approved visual "
                      "prompts are required: plan scenes first with "
                      "POST /api/script, review them, then produce. "
                      "Production never re-drafts."}), 400)
    if (not isinstance(script_lines, list) or not script_lines
            or not all(isinstance(s, str) and s.strip()
                       for s in script_lines)):
        return None, (jsonify(
            {"error": "script must be a non-empty string list"}), 400)
    if (not isinstance(visuals, list)
            or len(visuals) != len(script_lines)
            or not all(isinstance(v, str) and v.strip()
                       for v in visuals)):
        return None, (jsonify(
            {"error": "visuals must be a non-empty string list the "
                      "same length as script: one approved visual "
                      "prompt per approved line"}), 400)
    script_lines = [s.strip() for s in script_lines]
    if script_lines == DEMO_PREVIEW_SCRIPT:
        return None, (jsonify(
            {"error": "demo-preview scripts are never rendered. "
                      "Plan live scenes first with POST /api/script."}
        ), 400)
    return ([{"narration": n, "visual": v.strip()}
             for n, v in zip(script_lines, visuals)], None)


@app.post("/api/video")
def video():
    """Approval gate: produce ONLY from the approved plan.

    Requires approved narration lines plus approved visual
    prompts (nothing renders from a bare topic). No draft, no
    Wonder revise, no critique, and no Spark revision run here:
    an edit made before pressing Produce is the approved version,
    used exactly. Without approved lines this fails with a clear
    error and never silently re-drafts.
    """
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if not _live_mode():
        return jsonify({"error": "No Meta API key found. "
                                 "Paste your key above."}), 503
    approved, err = _approved_plan_from_body(body)
    if err:
        return err
    parsed, err = _parse_style_options(body)
    if err:
        return err
    style, color, style_custom = parsed
    audience = body.get("audience")
    if audience is not None:
        if not isinstance(audience, str) or not audience.strip():
            return jsonify(
                {"error": "audience must be a non-empty string"}), 400
        audience = audience.strip()
    target_duration_s = body.get("target_duration_s")
    if target_duration_s is not None:
        try:
            target_duration_s = float(target_duration_s)
        except (TypeError, ValueError):
            return jsonify(
                {"error": "target_duration_s must be a positive "
                          "number"}), 400
        if target_duration_s <= 0:
            return jsonify(
                {"error": "target_duration_s must be a positive "
                          "number"}), 400
    aspect_ratio = body.get("aspect_ratio", render.DEFAULT_ASPECT)
    if not isinstance(aspect_ratio, str):
        return jsonify(
            {"error": "aspect_ratio must be one of %s"
                      % sorted(render.ASPECTS)}), 400
    aspect_ratio = aspect_ratio.strip() or render.DEFAULT_ASPECT
    try:
        aspect_w, aspect_h = render.dims_for_aspect(aspect_ratio)
    except ValueError:
        return jsonify(
            {"error": "aspect_ratio must be one of %s"
                      % sorted(render.ASPECTS)}), 400
    # Optional link back to the waiting-approval plan this approval
    # came from: its director log travels into production QC, and
    # the plan is marked approved so it cannot be approved twice.
    script_job_id = body.get("script_job_id")
    linked_log = None
    linked_note = None
    if script_job_id is not None:
        with JOBS_LOCK:
            script_job = JOBS.get(script_job_id)
        if script_job is None:
            return jsonify({"error": "unknown script job: %s"
                                     % (script_job_id,)}), 404
        if script_job.get("status") != "waiting_approval":
            return jsonify({"error": "script job %s is %s, not "
                                     "waiting for approval: plan "
                                     "scenes first, then produce "
                                     "once" % (
                                         script_job_id,
                                         script_job.get("status"))}), 409
        linked_log = script_job.get("director")
        linked_note = script_job.get("revision_note")
        # A changed selection before approval changes the next job:
        # explicit picks win, otherwise the linked plan's picks
        # carry over. A job already approved keeps its own picks.
        if style is None:
            style = script_job.get("style")
        if color is None:
            color = script_job.get("color")
        if style_custom is None:
            style_custom = script_job.get("style_custom")
    # Pre-flight duration check on the APPROVED lines, before any
    # paid media call: an explicit target more than 30 percent
    # below the estimate holds production until the user approves
    # that duration (duration_ack) or fixes the target. Never
    # silently renders past the target.
    gate_target = target_duration_s
    if gate_target is None and script_job_id is not None:
        with JOBS_LOCK:
            script_job = JOBS.get(script_job_id)
            brief = (script_job.get("brief") or {}) if script_job else {}
        if brief.get("target_explicit"):
            gate_target = brief.get("target_duration_s")
    estimated_total_s, _basis = estimate_plan_duration(
        [a["narration"] for a in approved])
    preflight = duration_preflight(estimated_total_s, gate_target)
    if preflight and not body.get("duration_ack"):
        return jsonify({
            "error": ("estimated narration total %.1fs is more than "
                      "%d%% over the %.1fs target: approve this "
                      "duration or fix the target before producing"
                      % (preflight["estimated_total_s"],
                         int(director.DURATION_TOLERANCE * 100),
                         preflight["target_s"])),
            "needs_duration_ack": True,
            "estimated_total_s": preflight["estimated_total_s"],
            "target_s": preflight["target_s"],
            "suggestion": preflight["suggestion"],
        }), 409
    with JOBS_LOCK:
        for job in JOBS.values():
            if job["status"] in ("queued", "running"):
                return jsonify(
                    {"error": "a render is already running"}), 409
        job_id = uuid.uuid4().hex[:12]
        out_dir = os.path.join(VIDEOS_DIR, job_id)
        now = _now()
        if script_job_id is not None:
            linked = JOBS.get(script_job_id)
            if linked is not None and linked.get("status") == \
                    "waiting_approval":
                linked["status"] = "approved"
                linked["finished_at"] = now
                linked["updated_at"] = now
        style_name, color_name = _style_labels(style, color)
        JOBS[job_id] = {
            "id": job_id, "topic": topic, "status": "queued",
            "stages": {k: "waiting" for k in STAGE_KEYS},
            "script": [a["narration"] for a in approved],
            "visuals": [a["visual"] for a in approved],
            "mode": "live",
            "scenes": build_scene_records(
                [a["narration"] for a in approved], style=style,
                color=color, style_custom=style_custom),
            "video": None, "qc": None, "error": None,
            "revision_note": linked_note,
            "brief": {"topic": topic, "audience": audience,
                      "target_duration_s": target_duration_s,
                      "aspect_ratio": aspect_ratio,
                      "width": aspect_w, "height": aspect_h},
            "director": linked_log,
            "script_job_id": script_job_id,
            "duration_ack": bool(body.get("duration_ack")),
            "estimated_total_s": estimated_total_s,
            "style": style,
            "color": color,
            "style_custom": style_custom,
            "style_name": style_name,
            "color_name": color_name,
            "aspect_ratio": aspect_ratio,
            "width": aspect_w, "height": aspect_h,
            "out_dir": out_dir,
            "created_at": now, "started_at": None,
            "updated_at": now, "finished_at": None,
            "cancel_requested": False,
            "retries": 0, "last_error": None,
        }
    with JOBS_LOCK:
        for rec, item in zip(JOBS[job_id]["scenes"], approved):
            rec["visual_prompt"] = item["visual"]
            rec["revision_status"] = "approved"
    if script_job_id is not None:
        _save_job_by_id(script_job_id)
    _save_job_by_id(job_id)
    thread = threading.Thread(target=_run_job,
                              args=(job_id, topic, approved, style,
                                    audience, target_duration_s,
                                    aspect_ratio, linked_log,
                                    linked_note, color,
                                    style_custom),
                              daemon=True)
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["started_at"] = _now()
        JOBS[job_id]["updated_at"] = JOBS[job_id]["started_at"]
    _save_job_by_id(job_id)
    thread.start()
    return jsonify({"job_id": job_id})


@app.get("/api/jobs")
def job_list():
    """Every known job (restored from disk at startup), newest first:
    id, topic, status, format, video, error, elapsed. The page uses
    this after a refresh to find and restore the active job."""
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(),
                      key=lambda j: j.get("created_at") or 0,
                      reverse=True)
        return jsonify({"jobs": [
            {"id": j["id"], "topic": j.get("topic"),
             "status": j.get("status"),
             "aspect_ratio": j.get("aspect_ratio"),
             "video": j.get("video"), "error": j.get("error"),
             "elapsed_s": round(_job_elapsed(j), 1)}
            for j in jobs]})


@app.get("/api/job/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        return jsonify(_public_job(job))


@app.post("/api/job/<job_id>/cancel")
def cancel_job(job_id):
    """Request cancellation of a running job (cooperative).

    Sets a flag the worker checks at every stage and scene
    boundary; the job is then marked cancelled with its completed
    work preserved on disk. Limit, stated plainly: an in-flight
    model call or ffmpeg step is not killed and runs to its own
    timeout first, so cancellation lands at the next boundary."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        if job["status"] not in ("queued", "running"):
            return jsonify({"error": "only a running job can be "
                                     "cancelled: this job is %s"
                                     % job["status"]}), 409
        job["cancel_requested"] = True
    _save_job_by_id(job_id)
    return jsonify({
        "ok": True,
        "note": "cancellation requested: the job stops at the next "
                "stage boundary; an in-flight step finishes first"})


@app.post("/api/job/<job_id>/retry")
def retry_job(job_id):
    """Safe re-run of a failed, interrupted, or cancelled job.

    Only one render runs at a time (a running job elsewhere blocks
    with 409, same as POST /api/video). The retry reuses the stored
    inputs -- topic, latest scene narrations, style, audience,
    target duration, aspect ratio -- resets stages and unfinished
    scenes, keeps the previous error as last_error, and re-renders
    from the start into the same folder: it is a full re-run, not a
    mid-stage resume, because a dead stage cannot be picked up
    halfway. Completed files on disk stay until replaced."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        if job["status"] not in ("error", "interrupted", "cancelled"):
            return jsonify({"error": "only failed, interrupted, or "
                                     "cancelled jobs can be retried: "
                                     "this job is %s" % job["status"]}), 409
        for other in JOBS.values():
            if other["id"] != job_id and other["status"] in ("queued",
                                                             "running"):
                return jsonify(
                    {"error": "a render is already running"}), 409
        brief = job.get("brief") or {}
        # Retry re-renders from the STORED approved plan: narration
        # plus visual prompt per scene, exactly as approved. No
        # draft, no critique, no re-revision runs again.
        approved = []
        for rec in job.get("scenes", []):
            narration = (rec.get("narration") or "").strip()
            visual = (rec.get("visual_prompt") or "").strip()
            if narration and visual:
                approved.append({"narration": narration,
                                 "visual": visual})
        if not approved:
            lines = [s for s in (job.get("script") or []) if s]
            visuals = [v for v in (job.get("visuals") or []) if v]
            if len(lines) == len(visuals):
                approved = [{"narration": n, "visual": v}
                            for n, v in zip(lines, visuals)]
        if not approved:
            return jsonify({"error": "this job has no stored approved "
                                     "plan to retry from"}), 409
        linked_log = job.get("director")
        linked_note = job.get("revision_note")
        topic = job.get("topic", "")
        style = job.get("style")
        color = job.get("color")
        style_custom = job.get("style_custom")
        audience = brief.get("audience")
        target = brief.get("target_duration_s")
        aspect = job.get("aspect_ratio") or render.DEFAULT_ASPECT
        job["last_error"] = job.get("error")
        job["status"] = "running"
        job["stages"] = {k: "waiting" for k in STAGE_KEYS}
        for rec in job.get("scenes", []):
            if rec.get("status") not in ("done",):
                rec["status"] = "waiting"
                rec["image"] = None
                rec["duration"] = None
                rec["sam"] = None
            rec["dirty"] = False
            rec["dirty_reason"] = None
            rec["rendered_narration"] = None
        job["video"] = None
        job["qc"] = None
        job["error"] = None
        job["interrupted"] = False
        job["cancel_requested"] = False
        job["retries"] = int(job.get("retries") or 0) + 1
        job["started_at"] = _now()
        job["updated_at"] = job["started_at"]
        job["finished_at"] = None
    _save_job_by_id(job_id)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, topic, approved, style, audience, target,
              aspect, linked_log, linked_note, color,
              style_custom),
        daemon=True)
    thread.start()
    return jsonify({"job_id": job_id,
                    "note": "full re-run from the stored approved plan "
                            "(narration and visuals reused exactly, no "
                            "re-revision): stages restart from the "
                            "beginning"})


def _job_scene(job_id, index):
    """(job, scene record) or (error body, status)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None, (jsonify({"error": "unknown job"}), 404)
        scenes = job.get("scenes", [])
        if (not isinstance(index, int) or index < 0
                or index >= len(scenes)):
            return None, (jsonify({"error": "unknown scene"}), 404)
        return job, scenes[index]


@app.patch("/api/job/<job_id>/scene/<int:index>")
def edit_scene(job_id, index):
    """Edit one scene's narration, visual, style preset, color theme,
    or short custom phrase.

    Before a render runs (queued job) this updates the plan the render
    will use. After a render (done or error) the scene is marked dirty
    with its reason, so old audio is never silently left attached to
    new narration and an old image is never silently kept for a new
    visual (a visual edit also flags the finished video stale).
    While a render is running, edits are refused (409):
    the in-flight render owns the plan. style must be a preset id,
    color a theme id, style_custom a short phrase: unknown ids and
    paragraph-length custom text fail with a clear 400.
    """
    job, scene_or_resp = _job_scene(job_id, index)
    if job is None:
        return scene_or_resp
    body = request.get_json(silent=True) or {}
    narration = body.get("narration")
    visual = body.get("visual_prompt", body.get("visual"))
    parsed, err = _parse_style_options(body)
    if err:
        return err
    style, color, style_custom = parsed
    if narration is None and visual is None and style is None \
            and color is None and style_custom is None:
        return jsonify({"error": "nothing to update: send narration, "
                                 "visual_prompt, style, color, and/or "
                                 "style_custom"}), 400
    if narration is not None and (
            not isinstance(narration, str) or not narration.strip()):
        return jsonify({"error": "narration must be a non-empty string"}), 400
    if visual is not None and (
            not isinstance(visual, str) or not visual.strip()):
        return jsonify({"error": "visual_prompt must be a non-empty "
                                 "string"}), 400
    with JOBS_LOCK:
        if job["status"] == "running":
            return jsonify({"error": "render is running: wait until it "
                                     "finishes, then edit the scene"}), 409
        look_changed = False
        if style is not None:
            scene_or_resp["style"] = style
            look_changed = True
        if color is not None:
            scene_or_resp["color"] = color
            look_changed = True
        if style_custom is not None:
            scene_or_resp["style_custom"] = style_custom
            look_changed = True
        if look_changed:
            style_name, color_name = _style_labels(
                scene_or_resp.get("style"), scene_or_resp.get("color"))
            scene_or_resp["style_name"] = style_name
            scene_or_resp["color_name"] = color_name
        if job["status"] == "waiting_approval":
            # Pre-approval plan edit: this IS the approved version
            # once Produce is pressed. Clean update, never dirty.
            if narration is not None:
                scene_or_resp["narration"] = narration.strip()
                try:
                    job["script"][index] = narration.strip()
                except (IndexError, TypeError, KeyError):
                    pass
            if visual is not None:
                scene_or_resp["visual_prompt"] = visual.strip()
                try:
                    job["visuals"][index] = visual.strip()
                except (IndexError, TypeError, KeyError):
                    pass
            scene_or_resp["dirty"] = False
            scene_or_resp["dirty_reason"] = None
            resp = jsonify({"scene": scene_or_resp,
                            "plan": "updated (still waiting for approval)"})
        elif job["status"] == "queued":
            # Queued: update the plan before the render starts.
            if narration is not None:
                scene_or_resp["narration"] = narration.strip()
            if visual is not None:
                scene_or_resp["visual_prompt"] = visual.strip()
                try:
                    job["visuals"][index] = visual.strip()
                except (IndexError, TypeError, KeyError):
                    pass
            scene_or_resp["dirty"] = False
            scene_or_resp["dirty_reason"] = None
            resp = jsonify({"scene": scene_or_resp, "plan": "updated"})
        else:
            # Terminal job: mark dirty, never silently stale.
            if narration is not None:
                new_text = narration.strip()
                if (scene_or_resp.get("rendered_narration")
                        and new_text != scene_or_resp["rendered_narration"]):
                    scene_or_resp["dirty"] = True
                    scene_or_resp["dirty_reason"] = (
                        "narration edited after render: audio still "
                        "matches the previous text; regenerate or "
                        "re-render before publishing")
                elif new_text != scene_or_resp["narration"]:
                    # No rendered audio yet (job errored early):
                    # plan update.
                    scene_or_resp["dirty"] = False
                    scene_or_resp["dirty_reason"] = None
                scene_or_resp["narration"] = new_text
                if scene_or_resp.get("status") == "done":
                    scene_or_resp["status"] = "dirty"
            if visual is not None:
                new_visual = visual.strip()
                scene_or_resp["visual_prompt"] = new_visual
                try:
                    job["visuals"][index] = new_visual
                except (IndexError, TypeError, KeyError):
                    pass
                if scene_or_resp.get("image"):
                    # A rendered image exists: it still shows the
                    # previous visual.
                    scene_or_resp["dirty"] = True
                    scene_or_resp["dirty_reason"] = (
                        "visual edited after render: image shows the "
                        "previous visual; regenerate this scene")
                    if scene_or_resp.get("status") == "done":
                        scene_or_resp["status"] = "dirty"
                    if job.get("video"):
                        job["video_stale"] = True
            if look_changed and job.get("video"):
                # The finished video's images used the old look.
                job["video_stale"] = True
            resp = jsonify(
                {"scene": scene_or_resp,
                 "plan": "marked dirty" if scene_or_resp.get("dirty")
                 else "updated"})
    _save_job_by_id(job_id)
    return resp


@app.post("/api/job/<job_id>/scene/<int:index>/regenerate")
def regenerate_scene(job_id, index):
    """Regenerate ONE scene's image (Meta muse-image-1.0 only) plus its
    SAM check, without regenerating anything else. The scene's audio
    is untouched; when the narration no longer matches what was
    rendered, the scene stays dirty with its reason, so stale audio
    is flagged, never hidden. Key required."""
    job, scene_or_resp = _job_scene(job_id, index)
    if job is None:
        return scene_or_resp
    if not _live_mode():
        return jsonify({"error": "No Meta API key found. "
                                 "Paste your key above."}), 503
    with JOBS_LOCK:
        if job["status"] in ("queued", "running"):
            return jsonify({"error": "render is running: wait until it "
                                     "finishes, then regenerate the scene"}), 409
        out_dir = job["out_dir"]
        scene = scene_or_resp
    import meta_image
    import meta_sam
    visual = (scene.get("visual_prompt")
              or "flat vector illustration, " + scene["narration"])
    plan = {"id": scene.get("scene_id", "scene-%d" % (index + 1)),
            "narration": scene["narration"],
            "visual": visual,
            "visual_prompt": visual,
            "style": scene.get("style") or job.get("style"),
            "color": scene.get("color") or job.get("color"),
            "style_custom": (scene.get("style_custom")
                             or job.get("style_custom"))}
    try:
        # Fix the concept from the visual prompt FIRST, before
        # regenerating: a bad concept must never burn a regen.
        derived = director.derive_sam_concept(plan)
        plan["subject"] = derived["visual_subject"]
    except ValueError as exc:
        return jsonify({"error": _safe_error(exc)}), 502
    try:
        path = os.path.join(out_dir, "scene_%d.png" % index)
        aspect = job.get("aspect_ratio") or render.DEFAULT_ASPECT
        meta_image.generate_image(
            render._meta_prompt(plan, aspect_ratio=aspect), path,
            _api_key(), aspect_ratio=aspect)
        if (not os.path.exists(path) or os.path.getsize(path) == 0):
            raise RuntimeError("empty image for scene %d" % index)
        sam_result = meta_sam.sam_segment(
            path, plan["subject"], _api_key())
        sam_result["scene"] = index
        sam_result["subject"] = plan["subject"]
        sam_result["concept"] = derived["concept"]
        sam_result["visual_subject"] = plan["subject"]
    except Exception as exc:
        with JOBS_LOCK:
            scene["status"] = "error"
        _save_job_by_id(job_id)
        return jsonify({"error": _safe_error(exc)}), 502
    with JOBS_LOCK:
        scene["image"] = "/videos/%s/scene_%d.png" % (job_id, index)
        scene["sam"] = {"matches": sam_result.get("matches"),
                        "concept": sam_result.get("concept"),
                        "visual_subject": sam_result.get("visual_subject"),
                        "warning": sam_result.get("warning")}
        stale_audio = (scene.get("rendered_narration") is not None
                       and scene["narration"] != scene["rendered_narration"])
        if stale_audio:
            scene["dirty"] = True
            scene["dirty_reason"] = (
                "image regenerated, but audio still matches "
                "the previous narration; re-render before publishing")
            scene["status"] = "dirty"
        elif scene.get("status") != "done" or scene.get("dirty"):
            scene["status"] = "image ready"
            if not stale_audio:
                scene["dirty"] = False
                scene["dirty_reason"] = None
        job["video_stale"] = True
        resp = jsonify({"scene": scene,
                        "note": "image regenerated for this scene only; "
                                "full video re-render is a later phase"})
    _save_job_by_id(job_id)
    return resp


# Rebuild in-memory jobs from videos/*/job.json at startup, so a
# page refresh or a server restart finds completed work and marks
# jobs that died mid-stage as interrupted (never silently lost).
_RESTORE_SUMMARY = _restore_jobs()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
