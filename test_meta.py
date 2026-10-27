"""Test the Meta Model API key: Spark draft model + Wonder revise model.

Usage (run from this directory, key required):
    export META_API_KEY="your key here, local only"
    python test_meta.py            # models-list check + tiny Spark call
    python test_meta.py --chat     # also runs one tiny Wonder call

Prints status only, never the key. Without META_API_KEY live mode is
not available, so this test fails with a clear error instead of
pretending anything passed.
"""

import os
import sys

from meta_backend import (SPARK_MODEL, WONDER_MODEL, chat, draft_script,
                          list_models, revise_script)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if not os.environ.get("META_API_KEY", "").strip():
        print("KEY FAIL: META_API_KEY is not set in the environment. "
              "Live mode is not available.", file=sys.stderr)
        raise SystemExit(1)
    try:
        models = list_models()
    except Exception as e:
        print("KEY FAIL: %s" % e, file=sys.stderr)
        raise SystemExit(1)
    print("KEY OK: models endpoint reachable, %d models listed." % len(models))
    try:
        # Tiny Spark call (generous budget: reasoning models spend
        # hidden tokens before answering).
        draft = draft_script("Why agents plan before acting",
                             model=SPARK_MODEL)
        first = [ln for ln in draft.splitlines() if ln.strip()][0]
        print("SPARK OK: %s" % first[:200])
    except Exception as e:
        print("SPARK FAIL: %s" % e, file=sys.stderr)
        raise SystemExit(1)
    if "--chat" in argv:
        try:
            final = revise_script(draft, "Why agents plan before acting",
                                  model=WONDER_MODEL)
            first = [ln for ln in final.splitlines() if ln.strip()][0]
            print("WONDER OK: %s" % first[:200])
        except Exception as e:
            print("WONDER FAIL: %s" % e, file=sys.stderr)
            raise SystemExit(1)
    else:
        try:
            tiny = chat("Say 'test ok' in two words.", model=WONDER_MODEL,
                        max_tokens=2048)
            print("WONDER OK: %s" % tiny[:200])
        except Exception as e:
            print("WONDER FAIL: %s" % e, file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
