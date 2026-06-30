"""Test setup: isolate the DB to a temp SQLite file and force the mock engine.

These env vars must be set *before* debate_arena.config / storage.db are imported,
so this runs at collection time (conftest is imported before the test modules).
"""

from __future__ import annotations

import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"
os.environ["MOCK_LLM"] = "1"
os.environ["BOT_TOKEN"] = "test:token"
# Keep the suite fast — no real provider keys, mock engine handles everything.
