"""Meta-only hardening tests: MOCKED, deterministic, no network.

Covers the Meta-only brief:
  - chat() accepts only muse-spark-1.3 and muse-wonder-1.0
    (ValueError for anything else); the base URL is hard-coded,
    so META_API_BASE / META_TEXT_MODEL overrides change nothing.
  - No banned non-Meta backend string (whisper, piper, edge-tts,
    gTTS, Pollinations, pyttsx, espeak, elevenlabs, replicate)
    appears in any tracked .py/.txt/.js/.html file except test
    files (which assert absence) and README.md.

Run: python test_meta_only.py
"""

import os
import re
import subprocess
import sys
from unittest import mock

import meta_backend

HERE = os.path.dirname(os.path.abspath(__file__))

BANNED_RE = re.compile(
    r"whisper|piper|edge[-_]?tts|gtts|pollinations|pyttsx|espeak"
    r"|elevenlabs|replicate",
    re.IGNORECASE,
)


def check_chat_model_allowlist():
    # Anything outside the two Meta text models is rejected
    # before any HTTP call.
    for bad in ("gpt-4o", "claude-opus-4", "whisper-1",
                "muse-spark-1.3 ", " SPARK", ""):
        try:
            meta_backend.chat("Hello.", model=bad, api_key="fake-key")
        except ValueError as e:
            assert "Meta-only" in str(e), e
        else:
            raise AssertionError("chat accepted model %r" % (bad,))
    # The two allowed models pass validation (HTTP stubbed).
    seen = []

    class _Resp:
        status_code = 200
        text = "ok"

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Hi."}}]}

    def _post(url, headers=None, json=None, timeout=None):
        seen.append((url, json["model"]))
        return _Resp()

    want_url = "https://api.meta.ai/v1/chat/completions"
    with mock.patch.object(meta_backend.requests, "post",
                           side_effect=_post):
        for good in (meta_backend.SPARK_MODEL,
                     meta_backend.WONDER_MODEL):
            out = meta_backend.chat("Hello.", model=good,
                                    api_key="fake-key")
            assert out == "Hi.", out
        out = meta_backend.chat("Hello.", api_key="fake-key")
        assert out == "Hi.", out
    assert seen[0] == (want_url, meta_backend.SPARK_MODEL), seen
    assert seen[1] == (want_url, meta_backend.WONDER_MODEL), seen
    assert seen[2] == (want_url, meta_backend.DEFAULT_MODEL), seen
    # Env overrides are gone: even when set, they change nothing.
    with mock.patch.dict(os.environ,
                         {"META_API_BASE": "https://evil.example/v1",
                          "META_TEXT_MODEL": "gpt-4o"}):
        assert meta_backend._base() == "https://api.meta.ai/v1"
        with mock.patch.object(meta_backend.requests, "post",
                               side_effect=_post):
            meta_backend.chat("Hello.", api_key="fake-key")
    assert seen[-1] == (want_url, meta_backend.SPARK_MODEL), seen
    print("MOCKED PASS: chat() accepts only the two Meta text "
          "models; base URL is hard-coded")


def _tracked_source_files():
    r = subprocess.run(["git", "ls-files"], capture_output=True,
                       text=True, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(
            "git ls-files failed: %s" % ((r.stderr or "")[:200],))
    files = []
    for line in r.stdout.splitlines():
        name = line.strip()
        if not name.endswith((".py", ".txt", ".js", ".html")):
            continue
        base = os.path.basename(name)
        if base.startswith("test_") or base == "README.md":
            continue
        files.append(name)
    return files


def check_no_banned_backends():
    files = _tracked_source_files()
    assert files, "no tracked source files found"
    hits = []
    for name in files:
        path = os.path.join(HERE, name)
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if BANNED_RE.search(line):
                    hits.append("%s:%d:%s"
                                % (name, i, line.strip()[:120]))
    assert not hits, \
        "banned non-Meta backend strings found:\n" + "\n".join(hits)
    print("MOCKED PASS: zero banned backend strings in %d tracked "
          "source files (test files + README.md excluded)"
          % len(files))


def main():
    check_chat_model_allowlist()
    check_no_banned_backends()
    print("MOCKED ALL PASS: 2 Meta-only checks.")


if __name__ == "__main__":
    sys.exit(main())
