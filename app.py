"""ReelDirector studio console server (event build, Oct 26-Nov 4 2026).

Serves the Wonder-generated console UI at / and these JSON endpoints:
  GET  /api/health  -> {"ok": true, "mode": "live"|"demo"}
  POST /api/script  -> {"script": [...]} via Muse Spark when META_API_KEY
                       is set, otherwise a clearly labelled demo script.
  POST /api/video   -> {"job_id": ...} starts a REAL render job:
                       script (Spark live, demo script in demo mode)
                       -> Pollinations images -> voice (edge-tts,
                       gTTS fallback) -> ffmpeg assembly -> QC.
                       Stages report real progress; nothing is marked
                       done until its step actually finishes.
  GET  /api/job/<id> -> job status, stage states, video URL, QC report.
  GET  /videos/<name> -> the finished MP4 for a completed job.

The key is read from the environment only. It is never written to disk,
never logged, and never committed. Run: python app.py, then open
http://127.0.0.1:5000.

Non-Meta components are disclosed in the UI footer and the README:
images come from Pollinations and voice from edge-tts/gTTS, because
Meta offers no batch text-to-speech API. The script step is Meta.
"""

import os
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory

from meta_backend import chat
import render

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(APP_DIR, "ui")
VIDEOS_DIR = os.path.join(APP_DIR, "videos")
os.makedirs(VIDEOS_DIR, exist_ok=True)

app = Flask(__name__)

DEMO_SCRIPT = [
    "Hook: the director does not film first. It plans first.",
    "An agent reads the goal, breaks it into scenes, and checks each one.",
    "Script, visuals, voice, and captions are built as separate passes.",
    "A QC gate reviews every scene before anything is assembled.",
    "The result is a finished video, directed end to end by the agent.",
]

STAGE_KEYS = ["script", "scenes", "voice", "assemble", "qc"]
JOBS = {}
JOBS_LOCK = threading.Lock()


def _live_mode():
    return bool(os.environ.get("META_API_KEY", "").strip())


def _api_key():
    return os.environ.get("META_API_KEY", "").strip() or None


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
    return jsonify({"ok": True, "mode": "live" if _live_mode() else "demo"})


def _script_lines(topic):
    if not _live_mode():
        return DEMO_SCRIPT, "demo"
    lines = chat(
        "Write a 5-line faceless video script outline for this topic. "
        "One clear sentence per line, no numbering, no preamble. "
        "Topic: " + topic,
        max_tokens=2048,
    )
    return [ln.strip() for ln in lines.splitlines() if ln.strip()], "live"


@app.post("/api/script")
def script():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    lines, mode = _script_lines(topic)
    return jsonify({"script": lines, "mode": mode})


def _run_job(job_id, topic, script_lines=None):
    job = JOBS[job_id]

    def mark(name, state):
        with JOBS_LOCK:
            job["stages"][name] = state

    try:
        mark("script", "working")
        if script_lines:
            lines, mode = script_lines, "live" if _live_mode() else "demo"
        else:
            lines, mode = _script_lines(topic)
        job["mode"] = mode
        job["script"] = lines
        mark("script", "done")
        scenes = [{"narration": ln,
                   "visual": "flat vector illustration, " + ln}
                  for ln in lines]
        final, report = render.run_pipeline(
            scenes, topic, job["out_dir"], on_stage=mark,
            api_key=_api_key())
        with JOBS_LOCK:
            job["video"] = "/videos/%s/%s" % (job_id, os.path.basename(final))
            job["qc"] = report
            job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(exc)


@app.post("/api/video")
def video():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    script_lines = body.get("script")
    if script_lines is not None:
        if (not isinstance(script_lines, list)
                or not script_lines
                or not all(isinstance(s, str) and s.strip()
                           for s in script_lines)):
            return jsonify({"error": "script must be a non-empty string list"}), 400
        script_lines = [s.strip() for s in script_lines]
    with JOBS_LOCK:
        for job in JOBS.values():
            if job["status"] in ("queued", "running"):
                return jsonify(
                    {"error": "a render is already running"}), 409
        job_id = uuid.uuid4().hex[:12]
        out_dir = os.path.join(VIDEOS_DIR, job_id)
        JOBS[job_id] = {
            "id": job_id, "topic": topic, "status": "queued",
            "stages": {k: "waiting" for k in STAGE_KEYS},
            "script": [], "mode": "live" if _live_mode() else "demo",
            "video": None, "qc": None, "error": None,
            "out_dir": out_dir,
        }
    thread = threading.Thread(target=_run_job,
                              args=(job_id, topic, script_lines),
                              daemon=True)
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
    thread.start()
    return jsonify({"job_id": job_id})


@app.get("/api/job/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        return jsonify({k: v for k, v in job.items()
                        if k != "out_dir"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
