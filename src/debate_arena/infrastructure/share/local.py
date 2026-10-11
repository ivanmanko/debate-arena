"""LocalDirPublisher — snapshots as files in a directory the app serves at ``/shared/``."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Literal

from ...domain.exceptions import PublishError

# Keys come from secrets.token_urlsafe; anything else must not reach the filesystem.
_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")

SHARED_PATH = "/shared"


class LocalDirPublisher:
    backend: Literal["local"] = "local"

    def __init__(self, directory: Path, *, base_url: str) -> None:
        self._dir = directory
        self._base = base_url.rstrip("/")  # PUBLIC_URL; empty → a link from the site root

    async def publish(self, key: str, html: str) -> str:
        path = self._path(key)
        try:
            await asyncio.to_thread(self._write, path, html)
        except OSError as exc:
            raise PublishError(f"{type(exc).__name__}: {exc}") from exc
        return f"{self._base}{SHARED_PATH}/{path.name}"

    async def unpublish(self, key: str) -> None:
        path = self._path(key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as exc:
            raise PublishError(f"{type(exc).__name__}: {exc}") from exc

    def _path(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key):
            raise PublishError(f"invalid snapshot key {key!r}")
        return self._dir / f"{key}.html"

    @staticmethod
    def _write(path: Path, html: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
