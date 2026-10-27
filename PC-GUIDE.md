# ReelDirector on your PC: full check guide

Repo: https://github.com/Velu2k03/reel-director (public, main branch).
This guide covers cloning it, verifying it, running the studio UI on
localhost, and testing the Meta key. Nothing here needs a payment.

## 1. What you need first

- Git (https://git-scm.com/downloads, install, then restart the terminal).
- Python 3.10 or newer (https://www.python.org/downloads, tick "Add Python
  to PATH" on Windows during install).
- A Meta Model API key for the live script step. The UI also works without
  one, in demo mode (see step 5). Never paste the key into a chat, a repo
  file, or a screenshot.

## 2. Clone the repo

```
git clone https://github.com/Velu2k03/reel-director.git
cd reel-director
```

## 3. Verify what you got

Check the files and the history. Every commit date must fall inside the
event window, Oct 26 to Nov 4 2026:

```
dir            (Windows)  or  ls -la   (macOS/Linux)
git log --oneline --pretty=format:"%h %ad %s" --date=short
```

You should see README.md, EVENT-BUILD-LOG.md, meta_backend.py,
test_meta.py, app.py, ui/index.html, PC-GUIDE.md, and requirements.txt.
On github.com/Velu2k03/reel-director you can crosscheck the same file
list and commit dates in the browser, logged out.

## 4. Install and run the studio UI

```
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser. You should see the
ReelDirector console: top bar with the event-build tag, a topic box, five
pipeline stage cards (Script, Scenes, Voice, Assemble, QC), a script
panel, a 4-scene storyboard strip, and a job log. Type a topic, press
Generate, and watch the stages advance.

Stop the server with Ctrl+C in the terminal.

## 5. Live mode vs demo mode

- Without a key, the status pill reads "API offline, demo mode" and
  Generate returns a labelled demo script. That is expected, not an error.
- With a key set, the pill reads "API online" and Generate calls Muse
  Spark for a real script. Set the key for one terminal session only:

  Windows (PowerShell):
  ```
  $env:META_API_KEY="paste-your-key-here"
  python app.py
  ```

  macOS/Linux:
  ```
  export META_API_KEY="paste-your-key-here"
  python app.py
  ```

  Closing the terminal clears it. Do not put the key in a file, and do
  not commit any file that contains it (.env is ignored by .gitignore
  as a safety net, but the session-variable method above is preferred).

## 6. Test the key on its own

```
python test_meta.py
python test_meta.py --chat
```

Both print status only, never the key. Expect: 9 models listed, KEY OK,
CHAT OK. If the chat test prints an empty reply, that is the known
reasoning-token behaviour: the test already uses 512 tokens, so rerun
it once before concluding the key is bad.

## 7. Troubleshooting

- "python is not recognized": Python was installed without PATH, or the
  terminal predates the install. Restart the terminal, or reinstall with
  "Add Python to PATH" ticked.
- "pip install fails": try `python -m pip install -r requirements.txt`.
- "Port 5000 in use": another app owns it. Stop that app, or edit the
  port in app.py (last line) and reopen the new address.
- "401 / Unauthorized" from a live Generate: the key is wrong, revoked,
  or pasted with a trailing space. Re-copy it exactly.
- UI loads but Generate never finishes: a render takes a couple of
  minutes (images, voice, ffmpeg). Watch the stage states and the job log;
  a failed stage shows its real error, and no later stage is marked
  done. Also confirm the key has remaining credits on the Meta
  billing page.
- ffmpeg not found: rendering needs ffmpeg on PATH. On Windows:
  `winget install ffmpeg` (or `choco install ffmpeg`), then restart
  the terminal.
- Finished videos live under videos/<job>/ (gitignored). The console
  plays and downloads them; they are runtime output, not repo content.

## 8. What to look at on GitHub

- The repo page shows the file list and the latest commit date.
- The Pull requests tab shows the CodeRabbit review once a review PR
  is open. Reviews there are advisory; nothing merges without a human.
- EVENT-BUILD-LOG.md is the dated record of every event change, so
  anyone can tell what was built during the event window.
