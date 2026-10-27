# ReelDirector

Type a topic in, approve the script, get a finished faceless video out. A local Flask console with a gated flow: (1) you give a topic, (2) the AI writes the script with per-scene visuals and a director review, (3) it stops and waits for your approval, (4) only after approval it generates images, voice, and the assembled video. No step after approval re-writes the script, and no paid image or voice call happens before approval.

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
3. Start the console, open http://127.0.0.1:5000 in a browser, press Try a sample topic, press 1. Plan scenes, review the script and visuals on the approval screen, press 2. Approve & Produce video, then Download the finished video:
   ```bash
   ./start.sh
   ```
   (On Windows run `start.bat` instead.)

The key can come from either of two options: copy `.env.example` to `.env` and set `META_API_KEY` there (preferred for repeat runs; `.env` is gitignored and only fills variables the real environment does not already set), or paste the key in the page when asked (kept in server memory only, never written to disk). The pasted key drives everything: script draft and revise, Spark visual descriptions, the director critique and scene-revision loop, images, and SAM, not just the health check. Verified live with no environment key set: pasting a key then running script generation returns a live script. Without any key the console shows a labelled preview script only and refuses to produce a video. That is expected, not an error.

## How it works

| Stage | What actually happens |
| --- | --- |
| Script (before approval) | Muse Spark (`muse-spark-1.3`) drafts the narration, Muse Wonder revises it, Muse Spark describes one distinct visual per scene (object, action, setting -- never a copy of the narration; a narration-copy is rejected, retried once same-model, then fails loudly), then the director loop runs: brief (topic, audience, target duration) becomes a validated structured scene plan with stable IDs; a deterministic critique floor checks hook, audience relevance (group language is flagged for solo audiences), repetition (visuals compare distinct objects with the topic word excluded), estimated length vs target, and visual support, and a Muse Spark model critique is validated into the same finding format and merged in (a malformed Spark critique fails the job, never silently rule-only); Muse Spark revises only flagged scenes, at most 2 cycles, then leftover findings surface as unmet criteria and the plan is not labelled editorially passed. The stage returns the plan, visuals, director decisions (grouped by scene and criterion), unmet warnings, and the estimated total duration, then stops in `waiting_approval`. No images, no voice, no assembly run here |
| Approval gate | `POST /api/video` produces ONLY from approved narration lines plus approved visual prompts. Called without them it fails with a clear error and never silently re-drafts. An edit made before pressing Produce is the approved version, used exactly. A pre-flight check compares the approved estimate against an explicit target: more than 30% over holds production until the duration is approved (`duration_ack`) or the target is fixed |
| Images | One image per approved visual prompt from Muse Image (`muse-image-1.0`), composed for the selected format (9:16 vertical default, 16:9 wide); a failed scene retries once, then fails loudly |
| SAM check | SAM 3.1 (`sam-3.1`) segments the main subject of every scene image; the concept is a concrete visual noun only (adjectives, abstract nouns, mass nouns, stopwords, verbs, and generic words are rejected, never narration); no usable concept records the scene as skipped with its reason and keeps the image without regenerating; zero matches for a real checked concept regenerates that scene once as a close-up and re-checks, then keeps a warning and continues |
| Voice | Narration from `facebook/mms-tts-eng`, a Meta open-weight TTS model run locally; a silent scene retries once, then fails loudly |
| Assemble | ffmpeg builds one segment per scene at the selected format's real dimensions (720x1280 or 1280x720) from image plus voice only, with no burned-in text, then concatenates the segments. No SRT file is written and no subtitles filter runs (owner decision Oct 27: the burned-in captions were removed because the output was not good -- large, centered, overlapping the image). There is no caption toggle |
| QC | The finished file is probed for duration, the SELECTED format's dimensions (720x1280 for 9:16, 1280x720 for 16:9, never just any valid size), an audio stream, and audibility, and the report names the Meta backends plus per-scene SAM results, every retry decision, and the revision note. QC has no caption requirement and never fails a job for missing captions |

Script and director review are already done before approval and are marked done, not counted again as production work. Production shows its own step bar -- Images, Scene check, Voice, Assemble, Checks, Final video -- with the current stage name, stage number, per-scene counts (image 2 of 3, voice 1 of 3), and elapsed time, plus distinct Waiting for approval, Producing, Done, Failed, and Cancelled states. During production plan editing locks ("Production in progress, editing locked") and the review-then-produce hint hides; after done the final video shows, not the plan.

Stages in the console report real state. Nothing is marked done until its step finishes, and a failed stage shows its real error. The status pill separates three facts: key presence reads "Key configured" (never "API online"), "Connected" needs a real authenticated check with a timeout, and FFmpeg/FFprobe are probed and reported as found or missing. There are no captions anywhere: no SRT files on disk, nothing burned into the video, nothing in the preview, and nothing in the QC report -- and QC keeps technical validation separate from editorial review (a valid file is never called "high quality" on that basis alone). The storyboard shows one card per actual scene with its real image when available, editable narration, duration, status, provider `muse-image-1.0`, fallback `none (Meta-only, fail-hard)`, and the recorded look (style preset and color theme names); editing narration after a render marks the scene dirty instead of leaving old audio attached, and one scene's image can be regenerated without regenerating everything. Revised scenes show the director's before/after narration and visual prompt with the triggering criterion and revision status; unresolved criteria after the revision cap show on the card and in the job log, never as passed. Visual style and color are option pickers, not typed paragraphs: six style presets (Flat vector keeps the robot-director look as one option, never a required character or colour) and six color swatches, each mapping to a short block in every scene's image prompt, plus an optional collapsed short custom phrase that is never required. The picked style and color flow through planning, image generation, preview metadata, and QC; a changed pick before approval changes the next job, never a job already approved.

## Voice

There is no hosted text-to-speech endpoint on the Meta Model API, so voice cannot be an API call. Narration instead runs the Meta open-weight model `facebook/mms-tts-eng` locally on this machine. `requirements.txt` stays light for the web console while `requirements-voice.txt` holds the heavy voice packages, installed automatically on the first real voice run.

## Who it is for

Product hypothesis, not a validated claim: ReelDirector helps solo educators turn rough lesson notes into narrated short videos, with an AI director that reviews and improves the production. No impact is validated yet.

Note (Oct 27 owner decision): the output is narrated, not captioned. Burned-in captions were removed because the output was not good (large, centered, overlapping the image). The demo runs below predate the removal and still mention their caption checks as historical fact; every run after the removal produces narration with no burned-in text and QC with no caption requirement.

## Demo (submission sequence, event work only)

The demo opens by stating what was built during the event. Event-built (Oct 26 to Nov 4 window): the Meta integration, the console, the render pipeline, and the gated flow. The demo shows only event work. The demo video itself is local runtime output (gitignored): only its local path is described here, never a committed video file.

Follow the sequence in the console at http://127.0.0.1:5000 after `./start.sh`:

1. Brief: topic "How tadpoles become frogs", audience solo learners, target 30s, 3 scenes, Vertical 9:16. Approved 3-scene run `05264f7aa84b` (script job `992167bb8258`, waiting_approval, estimated against the 30s target).
2. Director correction: the director loop critiques each scene and revises only flagged scenes (at most 2 cycles); leftover findings surface as unmet criteria, never as passed. The approval screen shows the script, visuals, director decisions, and duration note; pressing Produce uses the approved lines exactly.
3. Generated scenes: one Muse Image (`muse-image-1.0`) illustration per narration line in the picked look (Watercolor, ocean theme, same style block in every scene prompt), SAM 3.1 checked per scene (zero matches kept as warnings in the QC report, not failures), MMS voice per scene.
4. Finished video: `videos/05264f7aa84b/` (local runtime output, gitignored, not committed) -- 15.4s, 720x1280 h264 with aac voice at -18.5 dB mean, narrated with no burned-in text, QC passed with no caption requirement, downloadable from the preview over HTTP 200.

## Live 16:9 workspace job (Oct 27)

One short live end-to-end job through the redesigned workspace, Wide 16:9, 3 scenes, benign educator brief, real key. Job `009c8ce76531`, topic "How rivers shape valleys", audience elementary students, target 18s, explicit 3-line script. All 7 stages done in 344.4s. Finished video `videos/009c8ce76531/final_How_rivers_shape_valleys.mp4` (local runtime output, gitignored): 13.9s, exactly 1280x720 h264, aac voice at -18.1 dB mean (audible), all 3 scene captions verified clean with only their own line, QC passed, downloadable over HTTP 200 (375,956 bytes, re-probed playable). Still: `shots/workspace_16x9_done_desktop.png` shows the completed job with preview, captions, director decisions, and download link.

SAM note from that job: the director loop revised all 3 visuals, and the checked concepts came out as giant/valley/canyon with 0 matches on every scene (each regenerated once Meta-only, warnings kept, video continued, QC still passed). Scene 0's concept was the size adjective "giant" -- the same adjective-as-concept class as the earlier "brilliant" failure -- so the shared skip rule was extended with size adjectives ("giant valley" now derives "valley") plus a mocked regression test in `test_finish.py`. The valley/canyon zeros were real checked concepts, correctly warnings rather than failures or skips. No second live run was spent re-proving the filter; live re-verification would need another paid generation loop.

## Gated flow, live (Oct 27)

One live script-stop run plus one live approved end-to-end run through the gated console, real key, Vertical 9:16, benign educator briefs.

Script-stop: topic "How desert plants save water", audience solo learners, target 30s, 3 scenes. Job `5358d1d92586` returned `waiting_approval` with 3 narration lines, 3 distinct Spark visuals, the director log (2 cycles, grouped decisions), unmet criteria surfaced, and estimated total 24.5s against the 30s target. Proof of the stop: the job record reads `waiting_approval` with script/revise done and production stages waiting, `video` null, and the job folder holds only `job.json` -- no images, no voice, no video file.

Approved end-to-end: topic "How rivers carve valleys", audience middle-school science students, target 30s, 3 scenes. Script job `59c336681c6b` (estimated 23.1s, no duration warning), approved as production job `54f9df9a3478` via its `script_job_id`. All 6 production steps done in 167.3s with script+revise already done. The approved lines were used exactly (rendered narration and visuals match the approval word for word). Finished video `videos/54f9df9a3478/final_How_rivers_carve_valleys.mp4` (local runtime output, gitignored): 19.2s, exactly 720x1280 h264, aac voice at -17.6 dB mean (audible), all 3 scene captions verified clean with only their own line, QC passed, downloadable over HTTP 200 (557,799 bytes). The script job reads `approved`. Stills: `shots/approval-desktop.png` and `shots/approval-mobile.png` show the waiting-approval screen (script, visuals, director decisions, duration note); `shots/production-desktop.png` and `shots/production-mobile.png` show the production step bar mid-run with per-scene counts and the editing lock, no horizontal scroll on mobile; `shots/done-desktop.png` shows the finished video with all 6 steps done.

## Bonus aims (aiming, not won)

No bonus is claimed as won. Agentic and Creative evidence below comes only from real runs; Open Weights, Most Viral, and Community Vote are not claimed as won or as eligible beyond what is stated here.

1. Best Agentic Use-Case ($5,000) -- aiming. After the script approval gate the agent runs the whole production on its own with no further clicks: image regen on SAM zero matches, voice retry on silence, and QC pass or fail. Every decision lands in the QC decisions log and in the collapsed Director panel, with before/after where a revision happened. The panel lists only decisions that actually happened in the real job record: no invented activity, no hidden chain-of-thought, and a retry is never logged as a first-try success.
2. Best Creative Build ($5,000) -- aiming. The Wonder full-script revise produces a visible change note, not a silent pass (absent when Wonder does not change the script). Muse Image uses one consistent style block across all scenes in a job, editable through the style picker, and the storyboard shows the real generated images, labelled live versus example. No absolute originality is claimed.
3. Best Build on Meta Open Weights ($10,000) -- NOT claimed. The current stack uses hosted Muse Spark through the Meta Model API, not Muse Spark Open run locally, and voice uses facebook/mms-tts-eng open weights, which is not Muse Glimmer or Muse Spark Open. No Open Weights eligibility is claimed. Models will not be switched to chase this bonus without a separate owner decision.
4. Most Viral Project ($20,000) -- NOT claimed in code or docs. It is a post-submission action, not a code feature, and is noted only in the submission checklist outside the repo.
5. Community Vote ($10,000) -- NOT claimed in code or docs. It is a post-submission public-gallery vote, not a code feature.

## Layout

```
app.py            Flask console: gated /api/script (full script stage, stops waiting_approval) and /api/video (approval gate, production only, never re-drafts), per-scene edit/regenerate, serves ui/ and finished videos
director.py       Bounded director loop: brief, plan validation, critique (topic-excluded repetition-visual, solo group-language relevance, proper-name-safe singularizer), capped revision with narration/visual revised labels, grouped findings, SAM concept rule
meta_backend.py   Meta Model API chat: Muse Spark drafts, describes one visual per scene, critiques scenes, and revises flagged scenes (solo audiences get a no-group-language guard, audience kept as given); Muse Wonder revises the full script before the director loop
meta_image.py     Muse Image client only, POST /v1/images/generations, b64 PNG
meta_sam.py       SAM 3.1 client: hosted Responses call, meta-sam-parser decode; proper-name subjects (Jesus/James/Moses) kept whole, never singularized
render.py         Meta images, SAM check, MMS voice, ffmpeg assembly, QC
ui/index.html     Studio console: approval screen, production-only step bar with per-scene counts, edit lock during production, final video after done; self-contained, icons self-hosted in ui/fonts
test_gated_flow.py  Mocked gated-flow checks (script-stop, approval gate, exact-use production, visual rejection, duration gate, proper names, topic-excluded repetition-visual, revised labels, grouped findings, solo group language, pre-produce edits, P2 stubs)
setup.py          One-step setup: Python check, .venv, packages, ffmpeg check
test_render.py    Live 2-scene render in BOTH formats (9:16 720x1280 and 16:9 1280x720): QC, no captions (no SRT, no subtitle stream), SAM results, backend bans
test_no_captions_style.py   Mocked no-caption + style/color-option checks (preset tables, color-in-prompt, same look per scene, pick validation, caption-free QC pass, picker UI)
test_phase_c.py   Mocked Phase C checks (aspect propagation, format-specific QC, workspace states, no captions)
test_phase_d.py   Mocked Phase D checks (scene counts/mapping, critique validation + bounded repair, revision cap, failure reporting, restore, no captions, aspects, 409 guards, cancel/retry, secrets)
test_phase_a.py   Mocked Phase A checks (scene counts, stage failure, no captions, style/color options, health, regen)
test_phase_b.py   Mocked Phase B checks (plan validation, bounded revision, Spark critique/revision wiring, SAM concept rule)
test_meta.py      Live key check: models list, Spark draft, Wonder revise
test_sam.py       SAM decode asserts plus live segmentation check
test_finish.py    Mocked finish-brief checks (adjective/stopword/abstract/mass rejection, concrete acceptance, skip-without-regen, zero-match warning still passes QC, API error fails hard, live adjective regression)
```

Costs in live mode are small: Muse Image is $0.01 per image, so a 5-scene video is about $0.05 in images. Finished videos land in `videos/<job>/` and are gitignored, they are runtime output, not repo content.

## Hackathon facts (verified only)

- Event: Meta Global AI Developer Hackathon, Meta and Cerebral Valley. Build window Oct 26 to Nov 4, 2026. Submission due Nov 4, 9:00 PM ET (Nov 5, 10:00 AM Manila) on the Cerebral Valley submit page.
- Track: Indie Developer, solo builder, public GitHub repo, new work only. Demo only what was built during the event; judges must be able to tell what was created during the event or the project is disqualified. What was built during the event is stated at the top of the demo.
- Theme: Build for Your World -- bring Meta models into a domain you know firsthand, solve a problem you know, free up time and mental space, make something possible that was not before.
- Rubric (Stage 2 and final, 0 to 10 per criterion): Impact 30 percent, Use of Meta AI tooling 25 percent, Technical depth 20 percent, Demo 25 percent.
- What this is: a Flask video pipeline whose output is a finished video -- a video production tool. Style presets, color themes, narration with no burned-in text, and the script approval gate are product choices, not hackathon requirements.
- Submission, four things: CV platform entry, a 3-minute demo video showing only event work (local file, never committed), a public GitHub repo that opens in a logged-out browser, before the deadline, not in the last hour.
- Credits verified for this builder: Meta 150 dollars in Meta Model API credits, key proven live. No other inference credits are claimed.

## Limits, stated plainly

- Narrated video, not captioned. No SRT files are written, nothing is burned into the frames, and QC has no caption requirement. Removed Oct 27 by owner decision because the burned-in output was not good. There is no caption toggle; bringing captions back needs a separate decision.
- Visual style and color are option pickers (six presets, six themes, optional short custom phrase). Unknown style/color ids and paragraph-length custom text are rejected by the server with a 400 naming the valid options.

- Localhost only. There is no hosted demo and no deployment in this repo.
- One render at a time. A second request while one is running returns 409, and scene edits during a run return 409 with a clear error.
- Job state persists to `videos/<job>/job.json` after every stage and scene transition (never the API key). A page refresh restores the active job from the server plus the last job id in the browser; a completed job comes back with its video, QC, and storyboard intact. A waiting-approval plan restores as the approval screen, and `?job=<id>` opens any job directly.
- A job that was running when the server restarted cannot resume mid-stage (its worker thread is gone). It is marked `interrupted`: completed files stay on disk and are listed as preserved, and `POST /api/job/<id>/retry` re-renders from the stored approved narration and visuals with no re-revision. A retry is a full re-run from the beginning, not a mid-stage resume, because a dead stage cannot be picked up halfway.
- Production runs zero text-model calls: no draft, no revise, no critique, no revision after approval. A script-stage plan that is waiting for approval is never picked up by production on its own; only an explicit produce with the approved lines starts media work.
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
