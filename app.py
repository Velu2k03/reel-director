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
import shutil
import subprocess
import threading
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory

from meta_backend import (critique_scenes, draft_script, list_models,
                          repair_critique, revise_scene, revise_with_note)
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
    "Script, visuals, voice, and captions are built as separate passes.",
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
    build, then PATH, then a real --version run."""
    path = render._tool(name)
    resolved = path if os.path.isabs(path) else shutil.which(name)
    if not resolved:
        return {"available": False, "version": None,
                "error": "%s not found: install ffmpeg and retry" % name}
    try:
        r = subprocess.run([resolved, "-version"], capture_output=True,
                           text=True, timeout=15)
    except Exception as e:
        return {"available": False, "version": None,
                "error": "%s present but not runnable: %s"
                         % (name, _safe_error(e, 200))}
    if r.returncode != 0:
        return {"available": False, "version": None,
                "error": "%s --version failed" % name}
    first = (r.stdout or "").strip().splitlines()
    return {"available": True,
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
def style_default():
    """The default visual style block (editable per render). The
    default keeps the existing flat-vector robot-director look; it is
    one option, never a required character or colour."""
    return jsonify({"default": render.META_STYLE,
                    "note": "Editable: send style with POST /api/video."})


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


def _live_script_lines(topic):
    """Spark draft, then Wonder revise with change note. Both must
    succeed; any failure raises and fails the job. Returns
    ([5 lines], change_note)."""
    draft = draft_script(topic)
    _nonempty_lines(draft, "Spark")
    final, note = revise_with_note(draft, topic)
    return _nonempty_lines(final, "Wonder"), note


@app.post("/api/script")
def script():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if not _live_mode():
        return jsonify({"script": DEMO_PREVIEW_SCRIPT, "mode": "demo-preview",
                        "note": DEMO_PREVIEW_NOTE})
    lines, note = _live_script_lines(topic)
    return jsonify({"script": lines, "mode": "live", "revision_note": note})


def build_scene_records(lines, style=None):
    """One storyboard record per narration line, for any scene count.

    Each record names the actual image provider (muse-image-1.0) and
    the fallback (none, Meta-only, fail-hard). Images, durations, and
    SAM notes fill in as the render reaches each scene. Director
    revision fields (scene_id, visual_prompt, revision_status,
    before/after, criteria) fill in when the Phase B director loop
    runs; they stay at their planned defaults otherwise.
    """
    records = []
    for i, line in enumerate(lines):
        records.append({
            "index": i,
            "scene_id": "scene-%d" % (i + 1),
            "narration": line,
            "visual_prompt": "flat vector illustration, " + line,
            "rendered_narration": None,
            "style": style,
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


def _spark_critique_raw(brief, scenes, measured_wps=None):
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
    last = None
    previous = None
    for attempt in (0, 1, 2):
        try:
            if attempt == 0:
                raw = critique_scenes(
                    brief.topic, brief.audience,
                    brief.target_duration_s, scenes)
            else:
                raw = repair_critique(
                    previous_bad=previous, problem=str(last)[:300],
                    topic=brief.topic, audience=brief.audience,
                    target_duration_s=brief.target_duration_s,
                    scene_ids=scene_ids)
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


def _spark_revise_scene(scene, reasons, brief):
    """Revise ONE flagged scene with Muse Spark (same model only).

    The brief (topic, audience, target duration) and the exact
    criteria that flagged this scene travel in the prompt; nothing
    else is revised. One bounded retry with the same model, then the
    real error propagates so the job fails hard (Meta-only rule).
    Returns (new_narration, new_visual).
    """
    last = None
    for _ in (1, 2):
        try:
            raw = revise_scene(
                brief.topic, brief.audience,
                brief.target_duration_s, scene["narration"],
                scene["visual_prompt"], reasons)
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


def _run_job(job_id, topic, script_lines=None, style=None,
               audience=None, target_duration_s=None,
               aspect_ratio=None):
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
        if script_lines:
            # Draft + revise already ran live inside /api/script.
            lines = script_lines
            note = ""
            mark("script", "done")
            mark("revise", "working")
        else:
            mark("script", "working")
            draft = draft_script(topic)
            _nonempty_lines(draft, "Spark")
            mark("script", "done")
            mark("revise", "working")
            final_text, note = revise_with_note(draft, topic)
            lines = _nonempty_lines(final_text, "Wonder")
        # Phase B bounded director loop, inside the revise stage:
        # structured plan -> rule-based critique floor plus Muse
        # Spark model critique -> targeted Spark revision of flagged
        # scenes only (max 2 cycles), then media generation. A Spark
        # critique or revision failure here fails the job with the
        # real error (Meta-only fail-hard); a malformed Spark
        # critique is rejected, never silently treated as rule-only.
        # Unmet criteria after the cap are recorded and surfaced,
        # never silently passed.
        brief = director.make_brief(
            topic, audience=audience,
            target_duration_s=target_duration_s,
            scene_count=len(lines))
        plan = director.build_plan(brief, lines)

        def _production_critique(brief_arg, scenes,
                                 measured_wps=None):
            return director.combined_critique(
                brief_arg, scenes,
                spark_critique_fn=_spark_critique_raw,
                measured_wps=measured_wps)

        plan, director_log = director.revise_loop(
            brief, plan, critique_fn=_production_critique,
            revise_scene_fn=_spark_revise_scene)
        failed = [a for a in director_log.get("attempts", [])
                  if a.get("status") == "failed"]
        if failed:
            first = failed[0]
            raise RuntimeError(
                "director Spark revision failed for %s (%s): %s"
                % (first["scene_id"],
                   ", ".join(first["criteria"]),
                   first.get("error", "unknown error")))
        mark("revise", "done")
        job["script"] = [s["narration"] for s in plan]
        job["revision_note"] = note
        job["brief"] = {"topic": brief.topic,
                        "audience": brief.audience,
                        "target_duration_s": brief.target_duration_s,
                        "aspect_ratio": aspect,
                        "width": width, "height": height}
        job["director"] = director_log
        records = build_scene_records(
            [s["narration"] for s in plan], style=style)
        last_attempt = {}
        for attempt in director_log.get("attempts", []):
            last_attempt[attempt["scene_id"]] = attempt
        unmet_by_scene = {}
        for unmet in director_log.get("unmet_criteria", []):
            unmet_by_scene.setdefault(
                unmet["scene_id"], []).append(unmet["criterion"])
        for rec, scene in zip(records, plan):
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
        job["scenes"] = records
        scenes = []
        for scene in plan:
            visual = scene["visual_prompt"]
            try:
                subject = director.derive_sam_concept(
                    {"id": scene["id"],
                     "visual_prompt": visual})["visual_subject"]
            except ValueError:
                # Fix the concept from the visual prompt first: fall
                # back to the visual text itself, which sam_check
                # re-derives (and rejects loudly if truly unusable).
                subject = visual
            scenes.append({"id": scene["id"],
                           "narration": scene["narration"],
                           "visual": visual,
                           "visual_prompt": visual,
                           "subject": subject,
                           "style": style})
        final, report = render.run_pipeline(
            scenes, topic, job["out_dir"], on_stage=mark,
            api_key=_api_key(), revision_note=note or None,
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


@app.post("/api/video")
def video():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if not _live_mode():
        return jsonify({"error": "No Meta API key found. "
                                 "Paste your key above."}), 503
    script_lines = body.get("script")
    if script_lines is not None:
        if (not isinstance(script_lines, list)
                or not script_lines
                or not all(isinstance(s, str) and s.strip()
                           for s in script_lines)):
            return jsonify({"error": "script must be a non-empty string list"}), 400
        if [s.strip() for s in script_lines] == DEMO_PREVIEW_SCRIPT:
            return jsonify({"error": "demo-preview scripts are never "
                                     "rendered. Set META_API_KEY for live "
                                     "script first."}), 400
        script_lines = [s.strip() for s in script_lines]
    style = body.get("style")
    if style is not None:
        if not isinstance(style, str) or not style.strip():
            return jsonify(
                {"error": "style must be a non-empty string"}), 400
        style = style.strip()
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
    with JOBS_LOCK:
        for job in JOBS.values():
            if job["status"] in ("queued", "running"):
                return jsonify(
                    {"error": "a render is already running"}), 409
        job_id = uuid.uuid4().hex[:12]
        out_dir = os.path.join(VIDEOS_DIR, job_id)
        now = _now()
        JOBS[job_id] = {
            "id": job_id, "topic": topic, "status": "queued",
            "stages": {k: "waiting" for k in STAGE_KEYS},
            "script": [], "mode": "live",
            "scenes": build_scene_records(script_lines or [], style=style),
            "video": None, "qc": None, "error": None,
            "revision_note": None,
            "brief": {"topic": topic, "audience": audience,
                      "target_duration_s": target_duration_s,
                      "aspect_ratio": aspect_ratio,
                      "width": aspect_w, "height": aspect_h},
            "director": None,
            "style": style,
            "aspect_ratio": aspect_ratio,
            "width": aspect_w, "height": aspect_h,
            "out_dir": out_dir,
            "created_at": now, "started_at": None,
            "updated_at": now, "finished_at": None,
            "cancel_requested": False,
            "retries": 0, "last_error": None,
        }
    thread = threading.Thread(target=_run_job,
                              args=(job_id, topic, script_lines, style,
                                    audience, target_duration_s,
                                    aspect_ratio),
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
        lines = list(job.get("script") or [])
        if not lines:
            lines = [s.get("narration", "") for s in job.get("scenes", [])
                     if s.get("narration")]
        script_lines = [s for s in lines if s] or None
        topic = job.get("topic", "")
        style = job.get("style")
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
        args=(job_id, topic, script_lines, style, audience, target,
              aspect),
        daemon=True)
    thread.start()
    return jsonify({"job_id": job_id,
                    "note": "full re-run from the stored plan: stages "
                            "restart from the beginning"})


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
    """Edit one scene's narration (and style).

    Before a render runs (queued job) this updates the plan the render
    will use. After a render (done or error) the scene is marked dirty
    with its reason, so old audio and captions are never silently left
    attached to new narration. While a render is running, edits are
    refused (409): the in-flight render owns the plan.
    """
    job, scene_or_resp = _job_scene(job_id, index)
    if job is None:
        return scene_or_resp
    body = request.get_json(silent=True) or {}
    narration = body.get("narration")
    style = body.get("style")
    if narration is None and style is None:
        return jsonify({"error": "nothing to update: send narration "
                                 "and/or style"}), 400
    if narration is not None and (
            not isinstance(narration, str) or not narration.strip()):
        return jsonify({"error": "narration must be a non-empty string"}), 400
    if style is not None and (
            not isinstance(style, str) or not style.strip()):
        return jsonify({"error": "style must be a non-empty string"}), 400
    with JOBS_LOCK:
        if job["status"] == "running":
            return jsonify({"error": "render is running: wait until it "
                                     "finishes, then edit the scene"}), 409
        if job["status"] == "queued":
            # Queued: update the plan before the render starts.
            if narration is not None:
                scene_or_resp["narration"] = narration.strip()
            if style is not None:
                scene_or_resp["style"] = style.strip()
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
                        "narration edited after render: audio and "
                        "captions still match the previous text; "
                        "regenerate or re-render before publishing")
                elif new_text != scene_or_resp["narration"]:
                    # No rendered audio yet (job errored early):
                    # plan update.
                    scene_or_resp["dirty"] = False
                    scene_or_resp["dirty_reason"] = None
                scene_or_resp["narration"] = new_text
                if scene_or_resp.get("status") == "done":
                    scene_or_resp["status"] = "dirty"
            if style is not None:
                scene_or_resp["style"] = style.strip()
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
    and captions are untouched; when the narration no longer matches
    what was rendered, the scene stays dirty with its reason, so stale
    audio is flagged, never hidden. Key required."""
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
            "style": scene.get("style") or job.get("style")}
    try:
        # Fix the concept from the visual prompt FIRST, before
        # regenerating: a bad concept must never burn a regen.
        derived = director.derive_sam_concept(plan)
        plan["subject"] = derived["visual_subject"]
    except ValueError as exc:
        return jsonify({"error": _safe_error(exc)}), 502
    try:
        path = os.path.join(out_dir, "scene_%d.png" % index)
        meta_image.generate_image(render._meta_prompt(plan), path,
                                  _api_key())
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
                "image regenerated, but audio and captions still match "
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
