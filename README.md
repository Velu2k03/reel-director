# ReelDirector

Type a topic in, get a finished faceless video out. A local Flask console that writes a script, generates one image per scene, checks every scene with SAM, adds voice and captions, assembles it with ffmpeg, and only serves the video after a QC check passes.

Meta parts: Muse Spark (script draft, plus the director loop: scene critique and per-scene revision), Muse Wonder (full-script revise of the draft before the director loop), Muse Image `muse-image-1.0` (scene art), SAM 3.1 (scene check), `facebook/mms-tts-eng` Meta open weights run locally (voice), and Muse Code (which wrote the strict Meta-only rewrite). Non-Meta local tooling: Flask (console server), FFmpeg/FFprobe (assembly, probing), and the usual Python packages. No edge-tts, gTTS, or Pollinations anywhere. A failed Meta call fails the job with its real error. There is no non-Meta generation backend, no fallback, and no placeholder output anywhere.

## Quick start

1. Clone the repo:
   ```bash
   git clone https://github.com/Velu2k03/reel-director.git
   cd reel-director
   ```
2. Set up everything with one command (checks Python, creates `.venv`, installs packages, checks ffmpeg):
   ```bash
   python setup.py
   ```
3. Start the console, open http://127.0.0.1:5000 in a browser, press Try a sample topic, press 1. Plan scenes, review the lines, press 2. Produce video, then Download the finished video:
   ```bash
   ./start.sh
   ```
   (On Windows run `start.bat` instead.)

The key can come from either of two options: copy `.env.example` to `.env` and set `META_API_KEY` there (preferred for repeat runs; `.env` is gitignored and only fills variables the real environment does not already set), or paste the key in the page when asked (kept in server memory only, never written to disk). The pasted key drives everything: script draft and revise, the director critique and scene-revision loop, images, and SAM, not just the health check. Verified live with no environment key set: pasting a key then running script generation returns a live 5-line script. Without any key the console shows a labelled preview script only and refuses to produce a video. That is expected, not an error.

## How it works

| Stage | What actually happens |
| --- | --- |
| Script | Muse Spark (`muse-spark-1.3`) writes the first narration draft |
| Revise (director loop) | Brief (topic, audience, target duration) becomes a validated structured scene plan with stable IDs; a deterministic critique floor checks hook, audience relevance, repetition, estimated length vs target, and visual support, and a Muse Spark (`muse-spark-1.3`) model critique is validated into the same finding format and merged in (a malformed Spark critique fails the job, never silently rule-only); Muse Spark revises only flagged scenes, at most 2 cycles, then leftover findings surface as unmet criteria and the job is not labelled editorially passed |
| Scenes | One image per narration line from Muse Image (`muse-image-1.0`), composed for the selected format (9:16 vertical default, 16:9 wide); a failed scene retries once, then fails loudly |
| SAM check | SAM 3.1 (`sam-3.1`) segments the main subject of every scene image; the concept is a concrete visual noun only (adjectives, abstract nouns, mass nouns, stopwords, verbs, and generic words are rejected, never narration); no usable concept records the scene as skipped with its reason and keeps the image without regenerating; zero matches for a real checked concept regenerates that scene once as a close-up and re-checks, then keeps a warning and continues |
| Voice | Narration from `facebook/mms-tts-eng`, a Meta open-weight TTS model run locally; a silent scene retries once, then fails loudly |
| Assemble | ffmpeg builds one segment per scene at the selected format's real dimensions (720x1280 or 1280x720), each with only its own caption in format-specific size and safe margins, then concatenates the segments |
| QC | The finished file is probed for duration, the SELECTED format's dimensions (720x1280 for 9:16, 1280x720 for 16:9, never just any valid size), an audio stream, and audibility, and the report names the Meta backends plus per-scene SAM results, every retry decision, and the revision note |

Stages in the console report real state. Nothing is marked done until its step finishes, and a failed stage shows its real error. The status pill separates three facts: key presence reads "Key configured" (never "API online"), "Connected" needs a real authenticated check with a timeout, and FFmpeg/FFprobe are probed and reported as found or missing. Captions are required: a scene whose subtitles cannot render fails the job instead of passing silently, every scene's caption evidence lands in the QC report, and QC keeps technical validation separate from editorial review (a valid file is never called "high quality" on that basis alone). The storyboard shows one card per actual scene with its real image when available, editable narration, duration, status, provider `muse-image-1.0`, and fallback `none (Meta-only, fail-hard)`; editing narration after a render marks the scene dirty instead of leaving old audio attached, and one scene's image can be regenerated without regenerating everything. Revised scenes show the director's before/after narration and visual prompt with the triggering criterion and revision status; unresolved criteria after the revision cap show on the card and in the job log, never as passed. Visual style is an editable field that defaults to the flat-vector robot-director look; no character or colour is required.

## Voice

There is no hosted text-to-speech endpoint on the Meta Model API, so voice cannot be an API call. Narration instead runs the Meta open-weight model `facebook/mms-tts-eng` locally on this machine. `requirements.txt` stays light for the web console while `requirements-voice.txt` holds the heavy voice packages, installed automatically on the first real voice run.

## Who it is for

Product hypothesis, not a validated claim: ReelDirector helps solo educators turn rough lesson notes into captioned short videos, with an AI director that reviews and improves the production. No impact is validated yet.

## Demo (real run, Oct 27)

One live 9:16 job through the workspace, benign educator brief, real key. Follow it in the console at http://127.0.0.1:5000 after `./start.sh`:

1. Brief: topic "How rain makes rivers: a short water-cycle lesson", audience middle-school science students, target 30s, 3 scenes, Vertical 9:16. Job `f3269fe6fbdb`.
2. A real director correction: the first attempt's director loop revised scene 2 (submitted "Rain gathers into streams, and streams join into rivers." became the rendered "When rain falls on hills and mountains, tiny droplets gather into trickling streams that flow downhill, merging with other streams..."). A separate live job the same day showed the same loop revising scene 2 for duration/relevance and scene 1 for repetition-visual, with a Muse Spark repetition-narration finding on scene 3 and unmet criteria surfaced, never passed.
3. Generated scenes: one Muse Image (`muse-image-1.0`) illustration per narration line, SAM 3.1 checked (zero matches kept as warnings in the QC report, not failures), MMS voice per scene (4.1s, 18.6s, 3.5s), captions verified per scene with safe margins.
4. Finished video: `videos/f3269fe6fbdb/final_How_rain_makes_rivers__a_short.mp4` (local runtime output, gitignored, not committed) -- 26.4s, 720x1280 h264, aac voice at -17.1 dB mean, captions verified, QC passed, downloadable from the preview. The same job also proved recovery live: the server was killed mid-run, the job came back `interrupted` with its 3 scene images listed as preserved, and `retry` re-rendered it to done.
5. Stills: `shots/workspace_desktop.png` and `shots/workspace_mobile.png` (local, gitignored) show the restored completed job -- video preview, director decisions, storyboard cards, no horizontal scroll on mobile.

## Live 16:9 workspace job (Oct 27)

One short live end-to-end job through the redesigned workspace, Wide 16:9, 3 scenes, benign educator brief, real key. Job `009c8ce76531`, topic "How rivers shape valleys", audience elementary students, target 18s, explicit 3-line script. All 7 stages done in 344.4s. Finished video `videos/009c8ce76531/final_How_rivers_shape_valleys.mp4` (local runtime output, gitignored): 13.9s, exactly 1280x720 h264, aac voice at -18.1 dB mean (audible), all 3 scene captions verified clean with only their own line, QC passed, downloadable over HTTP 200 (375,956 bytes, re-probed playable). Still: `shots/workspace_16x9_done_desktop.png` shows the completed job with preview, captions, director decisions, and download link.

SAM note from that job: the director loop revised all 3 visuals, and the checked concepts came out as giant/valley/canyon with 0 matches on every scene (each regenerated once Meta-only, warnings kept, video continued, QC still passed). Scene 0's concept was the size adjective "giant" -- the same adjective-as-concept class as the earlier "brilliant" failure -- so the shared skip rule was extended with size adjectives ("giant valley" now derives "valley") plus a mocked regression test in `test_finish.py`. The valley/canyon zeros were real checked concepts, correctly warnings rather than failures or skips. No second live run was spent re-proving the filter; live re-verification would need another paid generation loop.

## Bonus aims

Aiming, not won. Agentic: the run retries failed scenes and weak SAM matches on its own, and every retry lands in the QC decisions log for usefulness. Creative: an original robot-director house style drawn by Muse Image and tightened by Muse Spark from hook to takeaway.

## Layout

```
app.py            Flask console, job endpoints, per-scene edit/regenerate, serves ui/ and finished videos
director.py       Phase B bounded director loop: brief, plan validation, critique, capped revision, SAM concept rule
meta_backend.py   Meta Model API chat: Muse Spark drafts, critiques scenes, and revises flagged scenes; Muse Wonder revises the full script before the director loop
meta_image.py     Muse Image client only, POST /v1/images/generations, b64 PNG
meta_sam.py       SAM 3.1 client: hosted Responses call, meta-sam-parser decode
render.py         Meta images, SAM check, MMS voice, ffmpeg assembly, QC
ui/index.html     Studio console, self-contained, icons self-hosted in ui/fonts
setup.py          One-step setup: Python check, .venv, packages, ffmpeg check
test_render.py    Live 2-scene render in BOTH formats (9:16 720x1280 and 16:9 1280x720): QC, captions, SAM results, backend bans
test_phase_c.py   Mocked Phase C checks (aspect propagation, format-specific QC, workspace states)
test_phase_d.py   Mocked Phase D checks (scene counts/mapping, critique validation + bounded repair, revision cap, failure reporting, restore, captions, aspects, 409 guards, cancel/retry, secrets)
test_phase_a.py   Mocked Phase A checks (scene counts, stage failure, captions, style, health, regen)
test_phase_b.py   Mocked Phase B checks (plan validation, bounded revision, Spark critique/revision wiring, SAM concept rule)
test_meta.py      Live key check: models list, Spark draft, Wonder revise
test_sam.py       SAM decode asserts plus live segmentation check
test_finish.py    Mocked finish-brief checks (adjective/stopword/abstract/mass rejection, concrete acceptance, skip-without-regen, zero-match warning still passes QC, API error fails hard, live adjective regression)
```

Costs in live mode are small: Muse Image is $0.01 per image, so a 5-scene video is about $0.05 in images. Finished videos land in `videos/<job>/` and are gitignored, they are runtime output, not repo content.

## Limits, stated plainly

- Localhost only. There is no hosted demo and no deployment in this repo.
- One render at a time. A second request while one is running returns 409, and scene edits during a run return 409 with a clear error.
- Job state persists to `videos/<job>/job.json` after every stage and scene transition (never the API key). A page refresh restores the active job from the server plus the last job id in the browser; a completed job comes back with its video, QC, and storyboard intact.
- A job that was running when the server restarted cannot resume mid-stage (its worker thread is gone). It is marked `interrupted`: completed files stay on disk and are listed as preserved, and `POST /api/job/<id>/retry` re-renders from the stored plan. A retry is a full re-run from the beginning, not a mid-stage resume, because a dead stage cannot be picked up halfway.
- Cancellation is cooperative: `POST /api/job/<id>/cancel` (or the Cancel button) stops the job at the next stage or scene boundary with completed work preserved. Exact limit: an in-flight model call or ffmpeg step is not killed and runs to its own timeout first, so cancellation lands at the next boundary, never mid-step. Verified Oct 27 by clicking Cancel in a real Chromium browser against the running app (job `0b8a74a6fd6c` marked `cancelled` at the revise boundary after 119.7s, `job.json` with 3 scene records preserved on disk; desktop and mobile screenshots in `shots/cancel_desktop.png` and `shots/cancel_mobile.png`, no horizontal scroll on mobile).
- Every job reports elapsed seconds and per-scene progress in the console and the API. The director loop is bounded (2 critique+revision cycles max), then stops and surfaces unmet criteria.
- There is no hosted Meta text-to-speech endpoint. Voice is the Meta open-weight model `facebook/mms-tts-eng` run locally, never a hosted call and never a non-Meta service.
- SAM 3.1 checks every scene image. A SAM API error fails the job. Zero matches for a checked concept is a warning in the QC report, not a failure on its own. A scene with no usable visual concept (only adjectives, abstract or mass nouns, stopwords, or filler) is recorded as skipped with its reason and keeps its image without regenerating.
- If any Meta call fails (Spark, Wonder, Image, SAM), the job fails with the real error. Nothing is substituted and no placeholder is served.
- The strict Meta-only rewrite of this repo was done by Muse Code, powered by Meta Muse Spark.
- No license file yet, so reuse terms are not set. Do not assume a license from this README.

## If something fails

- Setup stops on ffmpeg with an install command. Run that command, then run setup again.
- The console shows a preview instead of a video. Paste a key and try again.
- A run fails halfway. Open Details in the console for the real reason, then try again.
- Voice is slow the first time. The voice software installs once, then later runs reuse it.
