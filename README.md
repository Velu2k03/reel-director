# ReelDirector

## What it does

Type a topic in, approve the script, get a finished faceless video out. A local Flask console with a gated flow: you give a topic, the AI writes the script with per-scene visuals and a director review, it stops and waits for your approval, and only after approval it generates images, voice, and the assembled video. Meta parts: Muse Spark script draft and director loop, Muse Wonder full-script revise, Muse Image scene art, SAM 3.1 scene check, and facebook/mms-tts-eng voice run locally. The `openai` package is used only as an HTTP client for Meta's OpenAI-compatible endpoint; no OpenAI service or model is called. A failed Meta call fails the job with its real error; there is no fallback and no placeholder output.

## Who it is for

Product hypothesis, not a validated claim: ReelDirector helps solo educators turn rough lesson notes into narrated short videos, with an AI director that reviews and improves the production. No impact is validated yet.

## Quick start

1. Clone the repo:
   ```bash
   git clone https://github.com/Velu2k03/reel-director.git
   cd reel-director
   ```
2. Set up everything with one command (needs Python 3.12 or newer from https://www.python.org/downloads/; creates `.venv`, installs packages, fetches ffmpeg):
   ```bash
   python3 setup.py
   ```
   (On Windows run `py -3 setup.py` instead. `sh setup.sh` / `setup.bat` pick a Python 3.12+ for you.)
3. Start the console, open http://127.0.0.1:5000, press Sample topic, press 1. Plan scenes, review the script and visuals, press 2. Approve & Produce video, then Download the finished video:
   ```bash
   sh start.sh
   ```
   (On Windows run `start.bat` instead.)

Needs your own Meta Model API key (https://dev.meta.ai); without one the console shows a labelled preview and refuses to produce a video; about $0.01 per image. Paste the key in the page (server memory only, never written to disk) or set `META_API_KEY` in the environment.

## How it works

- Script: Muse Spark drafts the narration, Muse Wonder revises it, Muse Spark describes one visual per scene, then the director loop critiques and revises flagged scenes (at most 2 cycles).
- Approval gate: production runs ONLY from approved narration plus approved visuals; without them it fails with a clear error and never re-drafts.
- Images: one Muse Image illustration per approved visual, composed for 9:16 or 16:9; a failed scene retries once, then fails loudly.
- Scene check: SAM 3.1 segments each scene image; zero matches regenerates once as a close-up, then keeps a warning; unusable concepts skip without regenerating.
- Voice: narration from facebook/mms-tts-eng run locally; a silent scene retries once, then fails loudly.
- Assemble: ffmpeg builds one segment per scene from image plus voice only, with no burned-in text, then concatenates them.
- QC: the finished file is probed for duration, exact dimensions, audio, and audibility; the report names the Meta backends, SAM results, retries, and the revision note.

## Provenance

Pre-existing (not event work): the Oct 14 2026 "Faceless Video Studio Agent" kit (https://github.com/Velu2k03/faceless-video-studio-agent, Pollinations + edge-tts free tier, burned-in captions). Event-built (Oct 26 to Nov 4 window, per the git log and EVENT-BUILD-LOG.md): Meta backends (Spark script, Wonder revise, Image visuals), the gated script-approval flow, the director loop, the SAM scene check, MMS voice, QC, and the console. Nothing else is claimed either way.

Demo: the 3-minute video must OPEN with one line stating pre-existing vs event-built.

## Honest limits

- Localhost only. There is no hosted demo and no deployment in this repo.
- One render at a time. A second request while one is running returns 409.
- Cancellation is cooperative: it stops the job at the next stage or scene boundary; an in-flight step runs to its own timeout first.
- A job that was running when the server restarted cannot resume mid-stage. It is marked `interrupted`; `POST /api/job/<id>/retry` re-renders from the stored approved plan.
- Needs Python 3.12 or newer.
- The first run downloads ffmpeg binaries (static-ffmpeg, ffmpeg AND ffprobe) plus the torch/transformers voice packages and MMS weights from the internet.
- MMS voice (facebook/mms-tts-eng) is CC-BY-NC-4.0: non-commercial use only.
- Muse Wonder is in preview and may change.
- No license file yet, so reuse terms are not set.

## Third-party licenses

| Asset | License | File |
| --- | --- | --- |
| Manrope font | SIL Open Font License | ui/scenes/studio/manrope-LICENSE.txt |
| Material Symbols font | Apache License 2.0 | ui/fonts/LICENSE-material-symbols.txt |
| facebook/mms-tts-eng voice | CC-BY-NC-4.0 (non-commercial) | model weights, downloaded on first voice run |
| meta-sam-parser | SAM License | Python package (see requirements.txt) |
| static-ffmpeg binaries | MIT | Python package (see requirements.txt) |

## Bonus aims (aiming, not won)

No bonus is claimed as won.

- Best Agentic Use-Case: aiming. After approval the agent runs production alone (image retry, voice retry on silence, SAM zero-match regen, QC verdict), every decision in the QC log.
- Best Creative Build: aiming. The Wonder revise carries a visible change note; one consistent style block per job via the pickers.
- Best Build on Meta Open Weights: NOT claimed. Voice uses facebook/mms-tts-eng open weights, which is not Muse Glimmer or Muse Spark Open; no Open Weights eligibility is claimed.
- Most Viral Project: NOT claimed. A post-submission action, not a code feature.
- Community Vote: NOT claimed. A post-submission public-gallery vote, not a code feature.

## Tests

Run every mocked suite (no key, no network; each must exit 0):

```bash
for t in test_gated_flow test_no_captions_style test_phase_a test_phase_b test_phase_c test_phase_d test_finish test_hackathon_bonus test_aspect_and_pasted_key test_meta_only; do .venv/bin/python $t.py; done
```

Live checks (need `META_API_KEY`): `test_meta.py` (key + Spark/Wonder), `test_sam.py [--image path]` (segmentation; generates its image with Muse Image without `--image`), `test_render.py` (full 2-scene render in both formats).
