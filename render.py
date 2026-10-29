"""ReelDirector render pipeline (strict Meta-only build).

Topic -> video path, all Meta:
  scenes (narration lines) -> Meta muse-image-1.0 images (one per
  scene) -> SAM 3.1 check per scene -> voice from the Meta
  open-weight model facebook/mms-tts-eng run locally -> ffmpeg
  assembly with no burned-in text -> QC gate.

Strict rules, no exceptions:
  1. NO CAPTIONS (owner decision Oct 27): segments are built from
     image plus voice only. No SRT file is written, no subtitles
     filter runs, and no text is burned into any frame. The
     burned-in captions were removed because the output was not
     good (large, centered, overlapping the image). There is no
     caption toggle: bringing captions back needs a separate owner
     decision and a separate brief.
  2. REAL QC: the finished file is probed (duration, video stream,
     audio stream, audibility) and the run fails loudly if any
     check fails, instead of marking stages done on hope.
  3. FAIL HARD: if any Meta call fails, the job fails with its real
     error. There is no non-Meta backend, no placeholder image, and
     no silent audio anywhere in this module. Without META_API_KEY,
     live rendering is unavailable and generate_images / sam_check
     raise instead of rendering anything.

Voice note: the Meta Model API has no hosted text-to-speech
endpoint; its voice model, muse-voice-transcribe-1.0, is
speech-to-text, the opposite direction, so it cannot narrate.
Narration runs the Meta open-weight TTS model facebook/mms-tts-eng
locally via transformers + torch.
"""

import json
import os
import shutil
import subprocess
import sys

try:
    import meta_image
except ImportError:  # render.py also runs standalone
    meta_image = None

try:
    import meta_sam
except ImportError:
    meta_sam = None

# Real output formats (Phase C). The selected aspect ratio drives
# image prompts, ffmpeg scale, preview metadata, and QC — never
# CSS alone.
ASPECTS = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
}
DEFAULT_ASPECT = "9:16"

# Legacy names kept for older callers: the previous 16:9 default.
WIDTH = 1280
HEIGHT = 720


def dims_for_aspect(aspect_ratio):
    """(width, height) for a supported aspect ratio.

    Raises ValueError naming the supported formats for anything
    else: an unknown format never silently renders as another.
    """
    key = str(aspect_ratio or "").strip()
    try:
        return ASPECTS[key]
    except KeyError:
        raise ValueError(
            "unsupported aspect ratio %r: supported formats are %s"
            % (aspect_ratio, sorted(ASPECTS))) from None

BACKENDS = {
    "script": "muse-spark-1.3 draft + muse-wonder-1.0 revise",
    "image": "muse-image-1.0",
    "sam": "sam-3.1",
    "voice": "facebook/mms-tts-eng (Meta open weights, local)",
}

# Visual style presets (owner decision Oct 27): options, not a
# long text field. Each preset maps to a short style block used
# for every image in a job. The flat-vector preset keeps the
# existing robot-director look as one option, never a required
# character or colour.
STYLE_PRESETS = {
    "flat-vector": {
        "name": "Flat vector",
        "blurb": "Clean flat shapes, the robot-director look",
        "block": ("Flat vector illustration, clean white background, "
                  "one small friendly robot director character with "
                  "a clapperboard, "),
    },
    "watercolor": {
        "name": "Watercolor",
        "blurb": "Soft pigment washes on paper texture",
        "block": ("Watercolor illustration, soft pigment washes "
                  "with visible paper texture, "),
    },
    "paper-cut": {
        "name": "Paper cut",
        "blurb": "Layered cut-paper collage look",
        "block": ("Layered paper cut-out collage, crisp cut edges "
                  "with soft drop shadows, "),
    },
    "3d-clay": {
        "name": "3D clay",
        "blurb": "Soft rounded handmade clay forms",
        "block": ("3D clay render, soft rounded handmade forms "
                  "with a matte finish, "),
    },
    "photo-realistic": {
        "name": "Photo realistic",
        "blurb": "Natural light, true-to-life detail",
        "block": ("Photo-realistic image, natural light with "
                  "true-to-life detail, "),
    },
    "minimal-line": {
        "name": "Minimal line",
        "blurb": "Simple outlines, lots of white space",
        "block": ("Minimal line drawing, single-weight outlines "
                  "with generous white space, "),
    },
}

# Color themes (owner decision Oct 27): swatches, not text. Each
# theme maps to a short color block in the image prompt. The
# selected theme changes the prompt colors and is recorded in the
# job and shown in the storyboard, not just in CSS.
COLOR_THEMES = {
    "facebook-blue": {
        "name": "Facebook blue",
        "blurb": "Blue accents on light gray",
        "block": ("Facebook blue #1877F2 accents, "
                  "light gray #F0F2F5 surfaces, "),
        "swatch": "#1877F2",
    },
    "warm-sunset": {
        "name": "Warm sunset",
        "blurb": "Warm oranges and deep plum",
        "block": ("Warm sunset palette, glowing orange highlights "
                  "with deep plum shadows, "),
        "swatch": "#E86A33",
    },
    "ocean": {
        "name": "Ocean",
        "blurb": "Teal water and deep navy",
        "block": ("Ocean palette, bright teal water tones with "
                  "deep navy shadows, "),
        "swatch": "#0E7C86",
    },
    "forest": {
        "name": "Forest",
        "blurb": "Leaf greens and warm earth",
        "block": ("Forest palette, rich leaf greens with warm "
                  "earth brown tones, "),
        "swatch": "#2E7D32",
    },
    "violet-dusk": {
        "name": "Violet dusk",
        "blurb": "Dusk purples and soft pink",
        "block": ("Violet dusk palette, deep purple tones with "
                  "soft pink highlights, "),
        "swatch": "#7B4DFF",
    },
    "monochrome": {
        "name": "Monochrome",
        "blurb": "Black, white, and gray only",
        "block": ("Monochrome palette, black ink with white and "
                  "gray tones only, "),
        "swatch": "#616161",
    },
}

DEFAULT_STYLE = "flat-vector"
DEFAULT_COLOR = "facebook-blue"

# A short custom phrase may be appended to the selected preset
# (collapsed optional field in the brief panel, never required).
# Anything longer is a paragraph and is rejected, never silently
# truncated: the owner does not want to type.
MAX_CUSTOM_PHRASE = 120

# Legacy default block: the flat-vector preset plus the
# Facebook-blue theme. Kept so older callers and tests that read
# META_STYLE keep working; new code resolves presets by id.
META_STYLE = (STYLE_PRESETS[DEFAULT_STYLE]["block"]
              + COLOR_THEMES[DEFAULT_COLOR]["block"])

VOICE_MODEL_ID = "facebook/mms-tts-eng"

_voice = None  # lazy (tokenizer, model) cache

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def _static_bins():
    """(ffmpeg, ffprobe) from the static-ffmpeg package, downloading
    the platform executables once on first use. Raises the real
    error when the package is missing or the download fails."""
    from static_ffmpeg.run import (
        get_or_fetch_platform_executables_else_raise as fetch)
    return fetch()


def _tool_with_source(name):
    """(path, source) for a media binary: local tools/ dir, PATH,
    then the static-ffmpeg download (fetched once on first use).

    setup.py may place a static build in tools/ (gitignored); render
    and setup share this lookup so both agree on what "installed"
    means. source is "tools", "path", "static-ffmpeg", or
    "missing". A missing binary falls back to the bare PATH name so
    it still raises its natural error.
    """
    suffix = ".exe" if os.name == "nt" else ""
    for cand in (os.path.join(TOOLS_DIR, name + suffix),
                 os.path.join(TOOLS_DIR, "bin", name + suffix)):
        if os.path.isfile(cand):
            return cand, "tools"
    found = shutil.which(name)
    if found:
        return found, "path"
    if name in ("ffmpeg", "ffprobe"):
        try:
            ffmpeg_exe, ffprobe_exe = _static_bins()
        except Exception:
            pass
        else:
            return ((ffmpeg_exe if name == "ffmpeg" else ffprobe_exe),
                    "static-ffmpeg")
    return name, "missing"


def _tool(name):
    """Resolve a media binary: tools/ -> PATH -> static-ffmpeg."""
    path, _source = _tool_with_source(name)
    return path


def _ffmpeg_bin():
    return _tool("ffmpeg")


def _ffprobe_bin():
    return _tool("ffprobe")


def resolve_style_block(style=None, style_custom=None):
    """Style block for one scene: a preset id, or legacy free text.

    A known preset id maps to its short block. Anything else
    non-empty is a legacy full style block, used exactly as given
    (no preset, no color theme merged in). Empty/None means the
    default preset. Returns (block, style_id_or_None): style_id is
    the preset id, or None for legacy text (which has no id).
    """
    raw = (style or "").strip() if isinstance(style, str) else style
    if raw and raw in STYLE_PRESETS:
        return STYLE_PRESETS[raw]["block"], raw
    if raw:
        block = str(raw)
        if not block.endswith(" "):
            block += " "
        return block, None
    return STYLE_PRESETS[DEFAULT_STYLE]["block"], DEFAULT_STYLE


def resolve_color_block(color=None):
    """Color block for one scene: a theme id, default when empty.

    Raises ValueError naming the valid themes for anything else:
    an unknown theme never silently renders as another.
    """
    raw = (color or "").strip() if isinstance(color, str) else color
    if not raw:
        return COLOR_THEMES[DEFAULT_COLOR]["block"], DEFAULT_COLOR
    try:
        return COLOR_THEMES[raw]["block"], raw
    except KeyError:
        raise ValueError(
            "unsupported color theme %r: supported themes are %s"
            % (color, sorted(COLOR_THEMES))) from None


def resolve_custom_phrase(style_custom=None):
    """Validated short custom phrase, or "" when none.

    At most MAX_CUSTOM_PHRASE characters: a paragraph is rejected
    with a clear error, never silently truncated.
    """
    if style_custom is None:
        return ""
    phrase = str(style_custom).strip()
    if not phrase:
        return ""
    if len(phrase) > MAX_CUSTOM_PHRASE:
        raise ValueError(
            "custom style phrase is %d characters (max %d): keep it "
            "a short phrase, not a paragraph"
            % (len(phrase), MAX_CUSTOM_PHRASE))
    if not phrase.endswith(" "):
        phrase += " "
    return phrase


def style_label(style_id):
    """Short display name for a style preset id ("" when unknown)."""
    try:
        return STYLE_PRESETS[style_id]["name"]
    except KeyError:
        return ""


def color_label(color_id):
    """Short display name for a color theme id ("" when unknown)."""
    try:
        return COLOR_THEMES[color_id]["name"]
    except KeyError:
        return ""


def _meta_prompt(scene, aspect_ratio=None):
    """Image prompt for one scene. Style and color come from the
    pickers: scene["style_id"] (or legacy scene["style"] text) and
    scene["color_id"], resolved through the preset tables, so every
    scene in a job shares the same selected style and color. A
    short scene["style_custom"] phrase may be appended. The legacy
    path (free-text scene["style"]) is used exactly as given with
    no theme merged in. The selected aspect ratio adds a real
    composition directive (not CSS): vertical portrait art for
    9:16, wide landscape art for 16:9."""
    visual = scene.get("visual") or scene["narration"]
    core = visual
    prefix = "flat vector illustration, "
    if core.lower().startswith(prefix):
        core = core[len(prefix):]
    style_block, style_id = resolve_style_block(scene.get("style"))
    custom = resolve_custom_phrase(scene.get("style_custom"))
    color_raw = scene.get("color_id", scene.get("color"))
    if style_id is not None or (
            isinstance(color_raw, str) and color_raw.strip()):
        # Preset (or default) path, or an explicitly picked theme:
        # the color theme is part of the prompt.
        color_block, _ = resolve_color_block(color_raw)
    else:
        # Legacy free-text style: used exactly, nothing merged in.
        color_block = ""
    aspect = str(aspect_ratio or scene.get("aspect_ratio")
                 or DEFAULT_ASPECT).strip()
    if aspect not in ASPECTS:
        aspect = DEFAULT_ASPECT
    orientation = ("vertical 9:16 portrait composition, tall framing"
                   if aspect == "9:16"
                   else "wide 16:9 landscape composition, wide framing")
    return (style_block + color_block + custom + "Scene: " + core +
            ", " + orientation +
            ", no text, no words, no letters anywhere in the image")


def _run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ffprobe(path):
    r = _run([_ffprobe_bin(), "-v", "error", "-show_entries",
              "stream=codec_type,codec_name,width,height",
              "-show_entries", "format=duration",
              "-of", "json", path])
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed on %s: %s" % (path, r.stderr[:300]))
    return json.loads(r.stdout)


def media_duration(path):
    return float(_ffprobe(path)["format"]["duration"])


def _require_key(api_key):
    if not api_key or not str(api_key).strip():
        raise RuntimeError(
            "META_API_KEY is not set: live mode is not available. "
            "Set META_API_KEY in the environment; refusing to render "
            "with any non-Meta backend.")


def generate_images(scenes, out_dir, api_key=None, decisions=None,
                      on_scene=None, aspect_ratio=None):
    """One Meta muse-image-1.0 illustration per scene, composed for
    the selected aspect ratio (9:16 vertical default, 16:9 wide).

    Returns (paths, "meta"). On a Muse Image failure the scene is
    retried once with Meta only, recorded in decisions; a second
    failure raises the real error. The job fails, nothing is
    substituted. on_scene(i, "image", {"path": path}) fires per
    finished scene image when given.
    """
    _require_key(api_key)
    if meta_image is None:
        raise RuntimeError("meta_image backend module is unavailable")
    aspect = str(aspect_ratio or DEFAULT_ASPECT).strip()
    dims_for_aspect(aspect)  # fail fast on an unknown format

    def _one(i, scene):
        path = os.path.join(out_dir, "scene_%d.png" % i)
        # Any exception propagates: no fallback, fail the job.
        # Native aspect is requested from the API (720x1280 for
        # 9:16, 1280x720 for 16:9); assemble center-crops anyway so
        # a wide source file still never stretches.
        meta_image.generate_image(
            _meta_prompt(scene, aspect_ratio=aspect), path, api_key,
            aspect_ratio=aspect, decisions=decisions)
        if (not os.path.exists(path) or os.path.getsize(path) == 0):
            raise RuntimeError("empty image for scene %d" % i)
        return path

    paths = []
    for i, scene in enumerate(scenes):
        try:
            paths.append(_one(i, scene))
        except Exception as first:
            try:
                paths.append(_one(i, scene))
            except Exception as second:
                if on_scene is not None:
                    on_scene(i, "image", {"ok": False,
                                          "error": str(second)[:300]})
                raise second
            if decisions is not None:
                decisions.append({
                    "decision": (
                        "scene %d image attempt 1 failed (%s), "
                        "attempt 2 with Muse Image succeeded"
                        % (i, str(first)[:160])),
                    "stage": "scenes", "scene": i, "retried": True,
                })
        if on_scene is not None:
            on_scene(i, "image", {"ok": True, "path": paths[-1]})
    return paths, "meta"


VOICE_REQUIREMENTS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "requirements-voice.txt")


def _install_voice_deps():
    """One-time lazy install of the heavy voice dependencies.

    Installer output streams to stdout so progress stays visible.
    Raises the real error when the install itself fails.
    """
    print("Installing voice software for %s (one-time setup) ..."
          % VOICE_MODEL_ID, flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", VOICE_REQUIREMENTS])
    if r.returncode != 0:
        raise RuntimeError(
            "voice install failed (pip exit %d installing %s); "
            "install it by hand and retry" % (r.returncode, VOICE_MODEL_ID))


def _load_voice():
    """Load the Meta open-weight TTS voice once. Raises the real
    error if the model cannot run on this machine."""
    global _voice
    if _voice is not None:
        return _voice
    try:
        import torch
        from transformers import AutoTokenizer, VitsModel
    except ImportError:
        _install_voice_deps()
        try:
            import torch
            from transformers import AutoTokenizer, VitsModel
        except ImportError as e:
            raise RuntimeError(
                "voice dependencies missing even after installing "
                "requirements-voice.txt (need torch + transformers "
                "for %s): %s" % (VOICE_MODEL_ID, e)) from e
    try:
        torch.set_num_threads(1)
        tokenizer = AutoTokenizer.from_pretrained(VOICE_MODEL_ID)
        model = VitsModel.from_pretrained(VOICE_MODEL_ID)
        model.eval()
    except Exception as e:
        raise RuntimeError(
            "could not load Meta open-weight voice %s on this "
            "machine: %s" % (VOICE_MODEL_ID, e)) from e
    _voice = (tokenizer, model)
    return _voice


def synthesize_voice(scenes, out_dir, decisions=None):
    """Narration per scene from facebook/mms-tts-eng (local).

    Returns [wav paths]. A silence or failure retries the scene
    once with the same model, recorded in decisions; a second
    failure raises the real error. No substitute voice, ever.
    """
    import numpy as np

    tokenizer, model = _load_voice()
    paths = []
    for i, scene in enumerate(scenes):
        text = scene["narration"]
        if not text or not text.strip():
            raise RuntimeError("empty narration for scene %d" % i)

        def _one():
            try:
                import torch

                inputs = tokenizer(text, return_tensors="pt")
                with torch.no_grad():
                    wave = model(**inputs).waveform[0].cpu().numpy()
            except Exception as e:
                raise RuntimeError(
                    "voice synthesis failed for scene %d: %s" % (i, e)) from e
            if wave.size == 0 or float(np.abs(wave).max()) < 1e-4:
                raise RuntimeError(
                    "voice synthesis produced silence for scene %d" % i)
            return wave

        try:
            wave = _one()
        except Exception as first:
            wave = _one()  # retry once, same model; raises for real
            if decisions is not None:
                decisions.append({
                    "decision": (
                        "scene %d voice attempt 1 failed (%s), "
                        "attempt 2 with %s succeeded"
                        % (i, str(first)[:160], VOICE_MODEL_ID)),
                    "stage": "voice", "scene": i, "retried": True,
                })
        path = os.path.join(out_dir, "scene_%d.wav" % i)
        try:
            import soundfile as sf

            sf.write(path, wave, model.config.sampling_rate)
        except Exception as e:
            raise RuntimeError(
                "could not write voice file for scene %d: %s" % (i, e)) from e
        if os.path.getsize(path) == 0:
            raise RuntimeError("empty audio for scene %d" % i)
        paths.append(path)
    return paths


def _visual_subject(scene):
    """The SAM subject for a scene, visual-first (Phase B fix).

    Derives the concept from the visual prompt or the scene subject
    via director.derive_sam_concept, never from narration stopwords
    or generic verbs. Raises ValueError with a clear error when no
    usable visual concept exists: the caller must fix the concept
    from the visual prompt first, before regenerating any image.
    """
    try:
        from director import derive_sam_concept
    except ImportError as e:
        raise RuntimeError(
            "director module unavailable for SAM concept "
            "derivation: %s" % e) from e
    return derive_sam_concept(scene)


def _closeup_prompt(scene, aspect_ratio=None):
    """Revised prompt for a SAM retry: same META_STYLE, plus a
    visual-subject close-up so the subject fills the frame. The
    close-up names the visual subject, never bare narration, so a
    bad concept cannot trigger a second regen for the same scene."""
    try:
        subject = _visual_subject(scene)["visual_subject"]
    except ValueError:
        subject = (scene.get("subject") or scene.get("visual")
                   or scene["narration"])
    return (_meta_prompt(scene, aspect_ratio=aspect_ratio)
            + ", centered single subject, "
            "close-up view of " + subject)


# Same concept rule in this path too: adjectives ("brilliant",
# "beautiful", "powerful", "quickly"), abstract nouns ("hook"),
# mass nouns without a bounded subject ("rain" as weather,
# "water"), narration stopwords, verbs, and generic words can never
# become the SAM concept. Unioned from meta_sam so all three paths
# (meta_sam, director, render) filter identically; checked again on
# the derived concept below as a second gate.
try:
    from meta_sam import _SKIP_WORDS as _SAM_SKIP_WORDS
except ImportError:  # pragma: no cover - meta_sam ships with the repo
    _SAM_SKIP_WORDS = frozenset()


def sam_check(scenes, image_paths, api_key, decisions=None,
              on_scene=None, aspect_ratio=None):
    """SAM 3.1 segmentation check for every scene image.

    Returns a list of per-scene result dicts (matches found, boxes).
    The concept comes from the visual prompt or scene subject via
    _visual_subject, never from narration stopwords, adjectives,
    abstract nouns, or mass nouns: when no usable visual concept
    exists the scene is recorded as skipped (skipped=True) with the
    reason in its warning, the image is kept WITHOUT regenerating,
    and the job continues. Zero matches for a real checked concept
    regenerates that scene image once (Meta only, close-up prompt)
    and re-runs SAM once, recorded in decisions with the visual
    subject and the concept used; still-zero keeps the warning and
    continues, never failing the video alone. A SAM API error raises
    and fails the job.
    """
    _require_key(api_key)
    if meta_sam is None:
        raise RuntimeError("meta_sam backend module is unavailable")
    if meta_image is None:
        raise RuntimeError("meta_image backend module is unavailable")
    if len(scenes) != len(image_paths):
        raise ValueError(
            "scene/image count mismatch: %d scenes, %d images"
            % (len(scenes), len(image_paths)))

    results = []
    for i, (scene, img) in enumerate(zip(scenes, image_paths)):
        try:
            derived = _visual_subject(scene)
        except ValueError as e:
            # No usable visual concept (adjective, abstract or mass
            # noun, stopword, or generic filler only): skip this
            # scene's SAM check with the reason, keep the image, no
            # regen. A bad concept never costs a paid image.
            reason = str(e)[:300]
            skipped = {
                "scene": i,
                "subject": (scene.get("visual_prompt")
                            or scene.get("visual")
                            or scene.get("subject") or ""),
                "concept": "",
                "visual_subject": (scene.get("visual_prompt")
                                   or scene.get("visual")
                                   or scene.get("subject") or ""),
                "matches": 0, "boxes": [], "masks": 0,
                "skipped": True,
                "skip_reason": reason,
                "warning": ("SAM check skipped for scene %d: no "
                            "usable visual concept (%s); image kept "
                            "without regenerating" % (i, reason)),
            }
            if decisions is not None:
                decisions.append({
                    "decision": (
                        "scene %d SAM check skipped: no usable "
                        "visual concept (%s); image kept without "
                        "regenerating, video continues" % (i, reason)),
                    "stage": "sam", "scene": i, "retried": False,
                    "skipped": True,
                })
            results.append(skipped)
            if on_scene is not None:
                on_scene(i, "sam", {"ok": True, "result": skipped,
                                   "skipped": True})
            continue
        # Second gate on the shared rule: even if derivation ever
        # returned a filtered word, skip instead of checking it.
        if derived["concept"].lower() in _SAM_SKIP_WORDS:
            reason = ("derived concept %r is a filtered word"
                      % (derived["concept"],))[:300]
            gated = {
                "scene": i, "subject": derived["visual_subject"],
                "concept": "", "visual_subject":
                    derived["visual_subject"],
                "matches": 0, "boxes": [], "masks": 0,
                "skipped": True, "skip_reason": reason,
                "warning": ("SAM check skipped for scene %d: no "
                            "usable visual concept (%s); image kept "
                            "without regenerating" % (i, reason)),
            }
            if decisions is not None:
                decisions.append({
                    "decision": (
                        "scene %d SAM check skipped: no usable "
                        "visual concept (%s); image kept without "
                        "regenerating, video continues" % (i, reason)),
                    "stage": "sam", "scene": i, "retried": False,
                    "skipped": True,
                })
            results.append(gated)
            if on_scene is not None:
                on_scene(i, "sam", {"ok": True, "result": gated,
                                   "skipped": True})
            continue
        subject = derived["visual_subject"]
        if derived.get("fixed") and decisions is not None:
            decisions.append({
                "decision": (
                    "scene %d SAM concept fixed from the visual "
                    "prompt before checking (visual subject %r, "
                    "concept %r from %s); no image regenerated for "
                    "the bad concept"
                    % (i, subject, derived["concept"],
                       derived["source"])),
                "stage": "sam", "scene": i, "retried": False,
            })
        # API errors propagate: fail the job with the real error.
        try:
            result = meta_sam.sam_segment(img, subject, api_key)
        except Exception as e:
            if on_scene is not None:
                on_scene(i, "sam", {"ok": False, "error": str(e)[:300]})
            raise
        result["scene"] = i
        result["subject"] = subject
        result["concept"] = derived["concept"]
        result["visual_subject"] = subject
        if result.get("matches"):
            results.append(result)
            if on_scene is not None:
                on_scene(i, "sam", {"ok": True, "result": result})
            continue
        regen_kwargs = {"decisions": decisions}
        if aspect_ratio is not None:
            regen_kwargs["aspect_ratio"] = aspect_ratio
        meta_image.generate_image(
            _closeup_prompt(scene, aspect_ratio=aspect_ratio), img,
            api_key, **regen_kwargs)
        second = meta_sam.sam_segment(img, subject, api_key)
        second["scene"] = i
        second["subject"] = subject
        second["concept"] = derived["concept"]
        second["visual_subject"] = subject
        if decisions is not None:
            decisions.append({
                "decision": (
                    "scene %d SAM attempt 1 found 0 matches for "
                    "visual subject %r (concept %r), so the image "
                    "was regenerated once with a close-up prompt "
                    "and SAM re-ran: attempt 2 found %d matches%s"
                    % (
                        i, subject, derived["concept"],
                        second.get("matches", 0),
                        "" if second.get("matches")
                        else ", warning kept, video continues")),
                "stage": "sam", "scene": i, "retried": True,
                "attempt1_matches": 0,
                "attempt2_matches": second.get("matches", 0),
            })
        results.append(second)
        if on_scene is not None:
            on_scene(i, "sam", {"ok": True, "result": second})
    return results


def assemble(scenes, image_paths, audio_paths, topic, out_dir,
             on_scene=None, aspect_ratio=None):
    """One ffmpeg filtergraph for the whole video (image + voice,
    no burned-in text).

    Every scene moves: a slow Ken Burns zoom (in on even scenes,
    out on odd ones, about 15% over the scene) so no frame is a
    frozen still, scenes dissolve into each other with a 0.5s
    crossfade (video xfade + audio acrossfade), and the whole
    video fades in and out over 0.3s / 0.5s. The frame matches
    the selected aspect ratio (9:16 vertical 720x1280 default,
    16:9 1280x720). Source art is center-cropped to the selected
    format and then scaled to the exact output dimensions: a 16:9
    source in a 9:16 job loses its side edges, it never stretches.
    fps is normalised to 30 before zoompan, so the zoom length
    matches the voice duration. No SRT file is written and no
    subtitles filter runs: the output carries image plus voice
    only (owner decision Oct 27, captions removed because the
    burned-in output was not good). A failed ffmpeg render fails
    the job with the real ffmpeg error. on_scene(i, "assembled",
    {"duration": dur}) fires for every scene once the finished
    file exists.
    """
    if not (len(scenes) == len(image_paths) == len(audio_paths)):
        raise ValueError(
            "scene/image/audio count mismatch: %d scenes, %d images, "
            "%d audio files" % (len(scenes), len(image_paths),
                                len(audio_paths)))
    aspect = str(aspect_ratio or DEFAULT_ASPECT).strip()
    width, height = dims_for_aspect(aspect)
    # Caption-era cleanup: stale scene_*.srt files from renders
    # before the Oct 27 removal must not linger beside new
    # caption-free output and cause confusion.
    try:
        for name in os.listdir(out_dir):
            if (name.startswith("scene_") and name.endswith(".srt")
                    and os.path.isfile(os.path.join(out_dir, name))):
                os.remove(os.path.join(out_dir, name))
    except OSError:
        pass
    # Center-crop to the target aspect first (a no-op when the
    # source already matches), then scale exact: never stretch.
    # fps is normalised to 30 before zoompan (a zoompan on an
    # unnormalised stream shortens or stretches the scene).
    crossfade = 0.5
    durs = [media_duration(aud) for aud in audio_paths]
    total = sum(durs) - crossfade * max(0, len(durs) - 1)
    cmd = [_ffmpeg_bin(), "-y"]
    for img, aud in zip(image_paths, audio_paths):
        cmd += ["-framerate", "30", "-loop", "1", "-i", img,
                "-i", aud]
    filters = []
    for i, dur in enumerate(durs):
        vi = 2 * i
        zoom = ("min(zoom+0.001,1.15)" if i % 2 == 0
                else "max(1.15-0.001*on,1.0)")
        filters.append(
            "[%d:v]fps=30,crop=w='min(iw,ih*%d/%d)':h='min(ih,iw*%d/%d)',"
            "scale=%d:%d,zoompan=z='%s':x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':d=1:s=%dx%d:fps=30,"
            "trim=duration=%.3f,setpts=PTS-STARTPTS[v%d]"
            % (vi, width, height, height, width, width, height,
               zoom, width, height, dur, i))
        filters.append(
            "[%d:a]aresample=48000,atrim=duration=%.3f,"
            "asetpts=PTS-STARTPTS[a%d]" % (vi + 1, dur, i))
    cur, off = "v0", durs[0] - crossfade
    for i in range(1, len(durs)):
        nxt = "vx%d" % i
        filters.append("[%s][v%d]xfade=transition=fade:duration=%.3f:"
                       "offset=%.3f[%s]" % (cur, i, crossfade, off, nxt))
        cur, off = nxt, off + durs[i] - crossfade
    acur, aoff = "a0", durs[0] - crossfade
    for i in range(1, len(durs)):
        nxt = "ax%d" % i
        filters.append("[%s][a%d]acrossfade=d=%.3f[%s]"
                       % (acur, i, crossfade, nxt))
        acur, aoff = nxt, aoff + durs[i] - crossfade
    filters.append(
        "[%s]fade=t=in:st=0:d=0.3,fade=t=out:st=%.3f:d=0.5,"
        "format=yuv420p[vout]" % (cur, max(0.0, total - 0.5)))
    filters.append(
        "[%s]afade=t=in:st=0:d=0.3,afade=t=out:st=%.3f:d=0.5[aout]"
        % (acur, max(0.0, total - 0.5)))
    safe = "".join(c if c.isalnum() else "_" for c in topic)[:30]
    final = os.path.join(out_dir, "final_%s.mp4" % safe)
    cmd += ["-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-r", "30", "-c:a", "aac",
            "-movflags", "+faststart", final]
    r = _run(cmd, timeout=600)
    if r.returncode != 0:
        detail = ((r.stderr or "").strip().splitlines() or [""])[-1]
        err = RuntimeError(
            "video rendering failed (ffmpeg exit %d): %s"
            % (r.returncode, detail[:300]))
        if on_scene is not None:
            for i in range(len(durs)):
                on_scene(i, "assembled",
                         {"ok": False, "error": str(err)[:300]})
        raise err
    if not os.path.exists(final) or os.path.getsize(final) == 0:
        err = RuntimeError("empty final video")
        if on_scene is not None:
            for i in range(len(durs)):
                on_scene(i, "assembled",
                         {"ok": False, "error": str(err)[:300]})
        raise err
    if on_scene is not None:
        for i, dur in enumerate(durs):
            on_scene(i, "assembled", {"ok": True, "duration": dur})
    return final


def qc_report(path, sam_results=None, backends=None, decisions=None,
             revision_note=None,
             director_decisions=None, unmet_criteria=None,
             editorial_pass=None, estimate_basis=None,
             aspect_ratio=None, expected_width=None,
             expected_height=None):
    """Real checks on the finished file. Returns a dict with pass/fail.

    Checks duration, the selected format's dimensions, an audio
    stream, audibility, and valid nonempty media, plus scene, image,
    and audio counts via the caller. Captions are gone (owner
    decision Oct 27): QC no longer requires captions and never
    fails a job for missing captions; no SRT files are written and
    none appear in this report.

    sam_results (list of per-scene SAM dicts) is recorded in the
    report; zero SAM matches anywhere is a warning only and never
    fails the video by itself. decisions lists every retry decision
    in plain words (or one explicit "no retry needed" entry);
    director_decisions appends the director loop's per-scene revision
    and surfacing decisions. revision_note records Wonder's change
    note when one exists. report["technical"] holds file-level
    validation; report["editorial"] holds model-review material (SAM
    warnings, revision note, retry decisions, director verdict),
    kept separate on purpose: a technically valid file is never
    labelled "high quality" on that basis alone. unmet_criteria
    (each with scene and criterion) forces editorial_pass False: a
    job with unmet editorial criteria is never labelled as fully
    passed editorially, even when technical QC passes.
    """
    if aspect_ratio is not None:
        exp_w, exp_h = dims_for_aspect(aspect_ratio)
        aspect = str(aspect_ratio).strip()
    elif expected_width is not None and expected_height is not None:
        exp_w, exp_h = int(expected_width), int(expected_height)
        aspect = "%d:%d" % (exp_w, exp_h)
        for key, dims in ASPECTS.items():
            if dims == (exp_w, exp_h):
                aspect = key
    else:
        exp_w, exp_h, aspect = WIDTH, HEIGHT, "16:9"
    report = {"file": os.path.basename(path), "passed": False,
              "aspect_ratio": aspect,
              "expected_dimensions": "%dx%d" % (exp_w, exp_h)}
    report["backends"] = dict(backends) if backends else dict(BACKENDS)
    merged = list(decisions) if decisions else []
    if director_decisions:
        merged.extend(director_decisions)
    if merged:
        report["decisions"] = merged
    else:
        report["decisions"] = [{
            "decision": "no retry needed: every scene passed "
                        "on the first attempt",
            "retried": False,
        }]
    unmet = list(unmet_criteria) if unmet_criteria else []
    if unmet:
        editorial_ok = False
    elif editorial_pass is None:
        editorial_ok = True
    else:
        editorial_ok = bool(editorial_pass)
    if revision_note:
        report["revision_note"] = revision_note
    if director_decisions or unmet or estimate_basis:
        report["director"] = {
            "corrections": len([d for d in (director_decisions or [])
                                if d.get("status") in (
                                    "revised", "narration-revised",
                                    "visual-revised")]),
            "unmet_criteria": unmet,
            "editorial_pass": editorial_ok,
        }
        if estimate_basis:
            report["director"]["estimate_basis"] = estimate_basis
    if sam_results is not None:
        report["sam"] = [
            {"scene": r.get("scene"), "subject": r.get("subject"),
             "concept": r.get("concept"),
             "visual_subject": r.get("visual_subject",
                                    r.get("subject")),
             "matches": r.get("matches"), "boxes": r.get("boxes"),
             "warning": r.get("warning")}
            for r in sam_results
        ]
        warnings = [r.get("warning") for r in sam_results if r.get("warning")]
        if warnings:
            report["sam_warnings"] = warnings
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        report["error"] = "missing or empty output file"
        report["technical"] = {"duration_ok": False,
                               "resolution_ok": False,
                               "audio_ok": False, "audible_ok": False}
        report["editorial"] = {"sam_warnings": report.get("sam_warnings", []),
                               "revision_note": revision_note,
                               "decisions": report.get("decisions", []),
                               "editorial_pass": editorial_ok,
                               "unmet_criteria": unmet}
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
    r = _run([_ffmpeg_bin(), "-v", "info", "-i", path, "-af", "volumedetect",
              "-vn", "-sn", "-dn", "-f", "null", os.devnull])
    mean = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0].strip())
    report["mean_volume_db"] = mean
    dims_ok = bool(video and video[0].get("width") == exp_w
                     and video[0].get("height") == exp_h)
    technical_ok = bool(
        (report.get("duration") or 0) > 0 and video and dims_ok
        and audio and mean is not None and mean > -60)
    if not dims_ok:
        got = ("%dx%d" % (video[0].get("width"),
                          video[0].get("height")) if video else "no video")
        report["dimension_error"] = (
            "output is %s but the selected format %s needs %dx%d: "
            "degraded output, refusing to pass" % (got, aspect,
                                                   exp_w, exp_h))
    report["technical"] = {
        "duration_ok": bool((report.get("duration") or 0) > 0),
        "resolution_ok": dims_ok,
        "audio_ok": bool(audio),
        "audible_ok": bool(mean is not None and mean > -60),
    }
    report["editorial"] = {
        "sam_warnings": report.get("sam_warnings", []),
        "revision_note": revision_note,
        "decisions": report.get("decisions", []),
        "editorial_pass": editorial_ok,
        "unmet_criteria": unmet,
    }
    report["passed"] = bool(technical_ok)
    return report


def run_pipeline(scenes, topic, out_dir, on_stage=None, api_key=None,
                 revision_note=None, on_scene=None, director_log=None,
                 aspect_ratio=None):
    """Full render: voice preflight, then images -> SAM check ->
    voice -> assemble -> QC.

    The preflight loads the local MMS voice before the first paid
    Muse Image call, so a voice failure raises before any image
    spend. Returns (final_path, qc_report). The selected aspect ratio
    (9:16 vertical 720x1280 default, 16:9 1280x720) drives the image
    prompts, the ffmpeg frame, and QC: QC fails when the finished
    dimensions do not match the selected format. Raises on any
    failure; nothing is substituted and no placeholder is ever
    served. Every retry decision is threaded through decisions into
    the QC report. on_scene(i, event, info) fires per-scene
    progress (image, sam, voice with duration, assembled) when
    given. No captions are burned in and QC requires none.
    director_log (from director.revise_loop) adds the director's
    revision/surfacing decisions and the editorial verdict to the
    QC report; technical QC and the editorial verdict stay
    separate.
    """
    aspect = str(aspect_ratio or DEFAULT_ASPECT).strip()
    width, height = dims_for_aspect(aspect)
    os.makedirs(out_dir, exist_ok=True)
    decisions = []
    # Voice preflight: load the local MMS voice BEFORE the first
    # paid Muse Image call, so a voice failure surfaces before any
    # image spend. Recorded in decisions as real stage detail.
    _load_voice()
    decisions.append({
        "decision": ("voice preflight ok: %s loaded before the "
                     "first paid image call" % VOICE_MODEL_ID),
        "stage": "voice", "scene": None, "retried": False,
    })
    director_entries = []
    unmet = []
    editorial_ok = None
    estimate_basis = None
    if director_log is not None:
        try:
            from director import director_decisions as _decisions
            id_to_index = {}
            for i, scene in enumerate(scenes):
                sid = scene.get("id")
                if sid:
                    id_to_index[sid] = i
                id_to_index["scene-%d" % (i + 1)] = i
            director_entries = _decisions(director_log, id_to_index)
        except ImportError:
            director_entries = []
        unmet = list(director_log.get("unmet_criteria", []))
        editorial_ok = bool(director_log.get("editorial_pass",
                                             not unmet)) and not unmet
        estimate_basis = director_log.get("estimate_basis")
        if (not director_entries and not unmet
                and director_log.get("editorial_pass")):
            director_entries = [{
                "decision": (
                    "director critique passed with no revisions "
                    "needed (%d cycle(s) checked)"
                    % director_log.get("cycles_used", 0)),
                "stage": "director", "scene": None,
                "status": "passed", "retried": False,
            }]

    def mark(name, state):
        if on_stage:
            on_stage(name, state)

    def scene_mark(i, event, info):
        if on_scene:
            on_scene(i, event, info)

    mark("scenes", "working")
    image_paths, image_backend = generate_images(
        scenes, out_dir, api_key, decisions, on_scene=on_scene,
        aspect_ratio=aspect)
    mark("scenes", "done")
    mark("sam", "working")
    sam_results = sam_check(scenes, image_paths, api_key, decisions,
                            on_scene=on_scene, aspect_ratio=aspect)
    mark("sam", "done")
    mark("voice", "working")
    audio_paths = synthesize_voice(scenes, out_dir, decisions)
    for i, aud in enumerate(audio_paths):
        try:
            dur = media_duration(aud)
        except Exception:
            dur = None
        scene_mark(i, "voice", {"ok": True, "duration": dur, "path": aud})
    mark("voice", "done")
    mark("assemble", "working")
    final = assemble(scenes, image_paths, audio_paths, topic, out_dir,
                     on_scene=on_scene, aspect_ratio=aspect)
    mark("assemble", "done")
    mark("qc", "working")
    report = qc_report(final, sam_results=sam_results,
                       backends=dict(BACKENDS), decisions=decisions,
                       revision_note=revision_note,
                       director_decisions=director_entries,
                       unmet_criteria=unmet,
                       editorial_pass=editorial_ok,
                       estimate_basis=estimate_basis,
                       aspect_ratio=aspect)
    report["image_backend"] = image_backend
    report["width"] = width
    report["height"] = height
    if not report["passed"]:
        mark("qc", "error")
        raise RuntimeError("QC failed: %r" % (report,))
    mark("qc", "done")
    return final, report
