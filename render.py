"""ReelDirector render pipeline (event build, Oct 26-Nov 4 2026).

Implements the topic -> video path inside the event repo:
  scenes (narration lines) -> Pollinations images -> voice
  (edge-tts, gTTS fallback) -> ffmpeg assembly -> QC.

Two deliberate design rules in this module:
  1. CAPTION FIX: every segment carries ONLY its own caption, with
     zero-based timestamps. Burning a full subtitle file with
     absolute timestamps into every segment would make every scene
     show scene 1's caption, so that is avoided here by construction.
  2. REAL QC: the finished file is probed (duration, video stream,
     audio stream, audibility) and the run fails loudly if any
     check fails, instead of marking stages done on hope.

Non-Meta components are disclosed, not hidden: voice comes from
edge-tts/gTTS, because the Meta Model API has no hosted
text-to-speech endpoint. Its voice model transcribes speech to
text, the opposite direction. Images
are Meta muse-image-1.0 in live mode (key present), with Pollinations
as the fallback when Meta fails and as the backend in demo mode
(no key). The script step (Meta muse-spark-1.3) lives in app.py.
"""

import asyncio
import json
import os
import subprocess
import urllib.parse

import requests

try:
    import meta_image
except ImportError:  # noqa: F401 - render.py also runs standalone
    meta_image = None

WIDTH = 1280
HEIGHT = 720

META_STYLE = ("Flat vector illustration, clean white background, "
              "Facebook theme: Facebook blue #1877F2 accents, "
              "light gray #F0F2F5 surfaces, one small friendly robot "
              "director character with a clapperboard, ")


def _meta_prompt(scene):
    visual = scene.get("visual") or scene["narration"]
    core = visual
    prefix = "flat vector illustration, "
    if core.lower().startswith(prefix):
        core = core[len(prefix):]
    return (META_STYLE + "Scene: " + core +
            ", no text, no words, no letters anywhere in the image")


def _pollinations_image(prompt, out_path):
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(prompt)
           + "?width=%d&height=%d&nologo=true" % (WIDTH, HEIGHT))
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    if os.path.getsize(out_path) == 0:
        raise RuntimeError("empty image for %s" % out_path)


def _run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ffprobe(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries",
              "stream=codec_type,codec_name,width,height",
              "-show_entries", "format=duration",
              "-of", "json", path])
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed on %s: %s" % (path, r.stderr[:300]))
    return json.loads(r.stdout)


def media_duration(path):
    return float(_ffprobe(path)["format"]["duration"])


def generate_images(scenes, out_dir, api_key=None):
    """One illustration per scene. Meta muse-image-1.0 first when a key
    is present, Pollinations fallback per scene on any Meta failure,
    Pollinations for every scene in demo mode (no key).
    Returns (paths, backend) where backend is meta, pollinations, or
    mixed."""
    paths = []
    backends = set()
    for i, scene in enumerate(scenes):
        path = os.path.join(out_dir, "scene_%d.png" % i)
        used_meta = False
        if api_key and meta_image is not None:
            try:
                meta_image.generate_image(_meta_prompt(scene), path, api_key)
                used_meta = True
            except Exception:
                used_meta = False
        if not used_meta:
            prompt = scene.get("visual") or (
                "flat vector illustration, " + scene["narration"])
            _pollinations_image(prompt, path)
        backends.add("meta" if used_meta else "pollinations")
        paths.append(path)
    backend = backends.pop() if len(backends) == 1 else "mixed"
    return paths, backend


def _edge_tts(text, out_path):
    import edge_tts

    async def _save():
        comm = edge_tts.Communicate(text, "en-US-AriaNeural")
        await comm.save(out_path)

    asyncio.run(_save())


def synthesize_voice(scenes, out_dir):
    """Narration per scene. edge-tts first, gTTS fallback. Returns [paths]."""
    paths = []
    for i, scene in enumerate(scenes):
        path = os.path.join(out_dir, "scene_%d.mp3" % i)
        try:
            _edge_tts(scene["narration"], path)
        except Exception:
            from gtts import gTTS

            gTTS(scene["narration"], lang="en").save(path)
        if os.path.getsize(path) == 0:
            raise RuntimeError("empty audio for scene %d" % i)
        paths.append(path)
    return paths


def _stamp(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, sec = divmod(rem, 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return "%02d:%02d:%02d,%03d" % (h, m, sec, ms)


def _escape_filter_path(path):
    return (path.replace("\\", "\\\\").replace(":", "\\:")
                .replace("'", "\\'").replace("[", "\\[").replace("]", "\\]"))


def _segment_srt(narration, duration, path):
    """One caption, zero-based. This is the caption-bug fix: a segment
    must never see another segment's timestamps."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("1\n%s --> %s\n%s\n" % (
            _stamp(0), _stamp(duration), narration))


def assemble(scenes, image_paths, audio_paths, topic, out_dir):
    """Per-scene segments (image + voice + own caption), then concat."""
    if not (len(scenes) == len(image_paths) == len(audio_paths)):
        raise ValueError(
            "scene/image/audio count mismatch: %d scenes, %d images, "
            "%d audio files" % (len(scenes), len(image_paths),
                                len(audio_paths)))
    parts = []
    for i, (scene, img, aud) in enumerate(
            zip(scenes, image_paths, audio_paths)):
        dur = media_duration(aud)
        srt = os.path.join(out_dir, "scene_%d.srt" % i)
        _segment_srt(scene["narration"], dur, srt)
        seg = os.path.join(out_dir, "seg_%d.mp4" % i)
        srt_esc = _escape_filter_path(os.path.abspath(srt))
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", aud,
               "-vf", "scale=%d:%d,subtitles='%s'" % (WIDTH, HEIGHT, srt_esc),
               "-c:v", "libx264", "-tune", "stillimage",
               "-c:a", "aac", "-t", str(dur), seg]
        r = _run(cmd)
        if r.returncode != 0:
            # Captions failed (missing fonts etc.): retry without them.
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", aud,
                   "-vf", "scale=%d:%d" % (WIDTH, HEIGHT),
                   "-c:v", "libx264", "-tune", "stillimage",
                   "-c:a", "aac", "-t", str(dur), seg]
            _run(cmd, timeout=300).check_returncode()
        parts.append(seg)
    lst = os.path.join(out_dir, "parts.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write("file '%s'\n" % os.path.abspath(p))
    safe = "".join(c if c.isalnum() else "_" for c in topic)[:30]
    final = os.path.join(out_dir, "final_%s.mp4" % safe)
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
          "-i", lst, "-c", "copy", final], timeout=300).check_returncode()
    return final


def qc_report(path):
    """Real checks on the finished file. Returns a dict with pass/fail."""
    report = {"file": os.path.basename(path), "passed": False}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        report["error"] = "missing or empty output file"
        return report
    probe = _ffprobe(path)
    report["duration"] = float(probe["format"]["duration"])
    streams = probe.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    report["video"] = "%sx%s %s" % (
        video[0].get("width"), video[0].get("height"),
        video[0].get("codec_name")) if video else None
    report["audio"] = audio[0].get("codec_name") if audio else None
    r = _run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect",
              "-vn", "-sn", "-dn", "-f", "null", os.devnull])
    mean = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0].strip())
    report["mean_volume_db"] = mean
    report["passed"] = bool(
        report["duration"] > 0 and video
        and video[0].get("width") == WIDTH
        and video[0].get("height") == HEIGHT
        and audio and mean is not None and mean > -60)
    return report


def run_pipeline(scenes, topic, out_dir, on_stage=None, api_key=None):
    """Full render. on_stage(name, state) reports real progress.
    Returns (final_path, qc_report). Raises on any failure."""
    os.makedirs(out_dir, exist_ok=True)

    def mark(name, state):
        if on_stage:
            on_stage(name, state)

    mark("scenes", "working")
    image_paths, image_backend = generate_images(scenes, out_dir, api_key)
    mark("scenes", "done")
    mark("voice", "working")
    audio_paths = synthesize_voice(scenes, out_dir)
    mark("voice", "done")
    mark("assemble", "working")
    final = assemble(scenes, image_paths, audio_paths, topic, out_dir)
    mark("assemble", "done")
    mark("qc", "working")
    report = qc_report(final)
    report["image_backend"] = image_backend
    if not report["passed"]:
        mark("qc", "error")
        raise RuntimeError("QC failed: %r" % (report,))
    mark("qc", "done")
    return final, report
