"""Test the Meta Model API key. Prints status only, never the key.

Usage:
    export META_API_KEY="your key here, local only"
    python test_meta.py            # models-list check only (no generation spend)
    python test_meta.py --chat     # also runs one tiny completion
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meta_backend import chat, list_models

data = list_models()
models = data.get("data", data) if isinstance(data, dict) else data
print("KEY OK: models endpoint reachable, %d models listed." % len(models))
if "--chat" in sys.argv:
    print("CHAT OK:", chat("Say 'test ok' in two words.", max_tokens=32)[:200])
