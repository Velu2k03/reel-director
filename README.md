# ReelDirector

Type a topic in, get a finished faceless video out. A local Flask console that writes a script, generates one image per scene, adds voice and captions, assembles it with ffmpeg, and only serves the video after a QC check passes.

## Quick start

Requirements: Python 3.10 or newer, ffmpeg on PATH.

```bash
git clone https://github.com/Velu2k03/reel-director.git
cd reel-director
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000, type a topic, press Generate. A render takes a couple of minutes. The finished MP4 plays in the console and can be downloaded from there.

No key is needed to try it. Without a key it runs in demo mode with a fixed demo script. With a key it calls Muse Spark for a real script and Muse Image for the scene images.

## How it works

| Stage | What actually happens |
| --- | --- |
| Script | Muse Spark writes one narration line per scene in live mode, or the console uses a labelled demo script in demo mode |
| Scenes | One image per narration line. Muse Image (`muse-image-1.0`) first in live mode, Pollinations as a per-scene fallback |
| Voice | `edge-tts` first, `gTTS` as fallback. The Meta Model API has no hosted text-to-speech endpoint to call with this key, its voice model transcribes speech to text, so voice stays outside Meta |
| Assemble | ffmpeg builds one segment per scene, each with only its own caption, then concatenates the segments |
| QC | The finished file is probed for duration, 1280x720 video, an audio stream, and audibility. A failed check fails the job instead of serving a broken video |

Stages in the console report real state. Nothing is marked done until its step finishes, and a failed stage shows its error.

## Live mode vs demo mode

Set the key for one terminal session only. Do not put it in a file that gets committed, and do not paste it into a screenshot or chat.

```bash
export META_API_KEY="your-key-here"
python app.py
```

On Windows PowerShell: `$env:META_API_KEY="your-key-here"` before `python app.py`.

Without the key, the status reads demo mode and Generate returns the demo script. That is expected, not an error.

## Layout

```
app.py            Flask console, job endpoints, serves ui/ and finished videos
meta_backend.py   Meta Model API chat and models list (Muse Spark)
meta_image.py     Muse Image client, POST /v1/images/generations, b64 PNG
render.py         Images, voice, ffmpeg assembly, per-scene captions, QC
ui/index.html     Studio console, self-contained, icons self-hosted in ui/fonts
ui/scenes/        Storyboard art shown in the console
test_meta.py      Key check only, prints status, never prints the key
test_render.py    End to end render test, checks QC and per-scene captions
PC-GUIDE.md       Full setup, run, and troubleshooting guide
```

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `META_API_KEY` | unset, demo mode | Required for live script and live images |
| `META_API_BASE` | `https://api.meta.ai/v1` | Override only if Meta changes the endpoint |
| `META_TEXT_MODEL` | `muse-spark-1.3` | Script model |

Costs in live mode are small: Muse Image is $0.01 per image, so a 5-scene video is about $0.05 in images. Finished videos land in `videos/<job>/` and are gitignored, they are runtime output, not repo content.

## Tests

```bash
python test_meta.py            # models list check, no generation spend
python test_meta.py --chat     # also runs one tiny completion
python test_render.py          # renders 2 scenes, checks QC and captions
```

`test_render.py` needs network. It fails if any scene segment carries another scene's caption. That check guards the per-scene caption rule: a segment must only ever carry its own caption.

## Limits, stated plainly

- Localhost only. There is no hosted demo and no deployment in this repo.
- One render at a time. A second request while one is running returns 409.
- Voice is edge-tts/gTTS, not a Meta Model API call, because that API has no hosted text-to-speech endpoint. Its voice model, `muse-voice-transcribe-1.0`, transcribes speech to text, the opposite direction.
- If Muse Image fails for a scene, that scene falls back to Pollinations. The QC line in the console says which backend was used: `meta`, `pollinations`, or `mixed`.
- No license file yet, so reuse terms are not set. Do not assume a license from this README.
