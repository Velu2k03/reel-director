"""Test the Meta Model API key. Prints status only, never the key.

Usage (run from this directory):
    export META_API_KEY="your key here, local only"
    python test_meta.py            # models-list check only (no generation spend)
    python test_meta.py --chat     # also runs one tiny completion
"""

import sys

from meta_backend import chat, list_models


def main(argv=None):
    argv = sys.argv if argv is None else argv
    try:
        models = list_models()
    except Exception as e:
        print("KEY FAIL: %s" % e, file=sys.stderr)
        raise SystemExit(1)
    print("KEY OK: models endpoint reachable, %d models listed." % len(models))
    if "--chat" in argv:
        # 512 tokens: Spark spends hundreds on hidden reasoning first.
        print("CHAT OK:", chat("Say 'test ok' in two words.", max_tokens=512)[:200])


if __name__ == "__main__":
    main()
