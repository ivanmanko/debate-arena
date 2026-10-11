"""Test setup: isolated SQLite file, snapshot dir and forced mock engine, set before settings
load."""

from __future__ import annotations

import os
import tempfile

_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["MOCK_LLM"] = "1"
os.environ["SHARE_DIR"] = tempfile.mkdtemp(prefix="shared-")
os.environ.pop("SHARE_BUCKET", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)
