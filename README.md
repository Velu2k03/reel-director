# ReelDirector

Agentic director for faceless video: topic in, finished video out.

## Event separation statement (read this first)

This repo is the Meta Global AI Developer Hackathon EVENT build, Oct 26 to Nov 4, 2026.
It is a new repo with its own commit history inside the event window.

Base (NOT event work): the Faceless Video Studio Agent starter kit, built Oct 14, 2026,
at https://github.com/Velu2k03/faceless-video-studio-agent. That kit is Velu's own
prior work. It is the starting point only.

Event work (this repo, and only this counts for the submission):
- Meta Model API backend (Muse Spark for script, Muse Image for visuals), replacing
  the kit's free backends.
- Director critique loop: draft, review, revise, then assemble (the agentic core).
- QC gate before assembly (scene/image/audio count checks, caption check).
- Demo video showing ONLY event work.

The submission demo must show only event work. Nothing from the Oct 14 kit appears
in the demo video except as the declared base.

## Run

Requires Python 3 and a Meta Model API key (never commit it; use a local .env).

```
pip install -r requirements.txt
export META_API_KEY="your key here, local only"
python test_meta.py
```

## Layout

- meta_backend.py: Meta Model API calls (text chat, models list). Image and voice
  modules are planned next, verified against the official docs before use.
- test_meta.py: key test. Prints status only, never the key.
- EVENT-BUILD-LOG.md: dated log of every event change, so judges can tell what was
  built during the event.
