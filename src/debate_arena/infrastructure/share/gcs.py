"""GcsPublisher — snapshots as public objects in a Google Cloud Storage bucket (ADR-006).

The bucket is readable by ``allUsers`` through ``roles/storage.legacyObjectReader``
(``objects.get`` only, no listing). The client is synchronous; calls run in a thread.
Credentials are Application Default Credentials (the Cloud Run service account).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...domain.exceptions import PublishError

PUBLIC_HOST = "https://storage.googleapis.com"


class GcsPublisher:
    backend = "gcs"

    def __init__(self, bucket_name: str, *, cache_max_age_s: int, bucket: Any = None) -> None:
        self._name = bucket_name
        self._cache_control = f"public, max-age={cache_max_age_s}"
        self._bucket = bucket  # injected in tests; created lazily from ADC otherwise

    async def publish(self, key: str, html: str) -> str:
        await self._call(self._upload, f"{key}.html", html)
        return f"{PUBLIC_HOST}/{self._name}/{key}.html"

    async def unpublish(self, key: str) -> None:
        await self._call(self._delete, f"{key}.html")

    async def _call(self, fn: Any, *args: str) -> None:
        try:
            await asyncio.to_thread(fn, *args)
        except Exception as exc:  # noqa: BLE001 — any storage failure becomes a 502
            raise PublishError(f"{type(exc).__name__}: {exc}") from exc

    def _get_bucket(self) -> Any:
        if self._bucket is None:
            from google.cloud import storage

            self._bucket = storage.Client().bucket(self._name)
        return self._bucket

    def _upload(self, name: str, html: str) -> None:
        blob = self._get_bucket().blob(name)
        blob.cache_control = self._cache_control
        blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    def _delete(self, name: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._get_bucket().blob(name).delete()
        except NotFound:
            return
