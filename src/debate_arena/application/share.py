"""ShareService — publishing a finished analysis as a public snapshot (SPEC §4.7, ADR-006).

The page is rendered by the interface layer and passed in as ``render``; the service only
decides when publishing is allowed and keeps the storage and the database in step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Literal

from ..domain.exceptions import AnalysisNotFound, ShareNotFound, ShareUnavailable
from ..domain.ports import AnalysisRepository, SnapshotPublisher
from ..domain.records import AnalysisRecord, ShareRecord

logger = logging.getLogger(__name__)

SnapshotRenderer = Callable[[AnalysisRecord], str]


class ShareService:
    """Use cases behind ``POST`` / ``DELETE /api/analyses/{id}/share``."""

    def __init__(
        self,
        *,
        repo: AnalysisRepository,
        publisher: SnapshotPublisher,
        render: SnapshotRenderer,
        token_bytes: int,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._render = render
        self._token_bytes = token_bytes
        # One process (SPEC §1): a per-analysis lock serialises publish, withdraw and delete,
        # so a page can neither be uploaded twice nor outlive its analysis.
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def backend(self) -> Literal["gcs", "local"]:
        return self._publisher.backend

    async def publish(self, analysis_id: int) -> ShareRecord:
        """Publish once; a published analysis returns its existing link (the verdict is final)."""
        async with self._lock(analysis_id):
            return await self._publish(analysis_id)

    async def withdraw(self, analysis_id: int) -> None:
        """Remove the page, then the record; on a storage error the record stays."""
        async with self._lock(analysis_id):
            await self._withdraw(analysis_id)

    async def delete_analysis(
        self, analysis_id: int, delete: Callable[[], Awaitable[object]]
    ) -> None:
        """Withdraw the page if any, then run ``delete`` — atomically w.r.t. publishing."""
        async with self._lock(analysis_id):
            with contextlib.suppress(AnalysisNotFound, ShareNotFound):
                await self._withdraw(analysis_id)
            await delete()
        self._locks.pop(analysis_id, None)

    def _lock(self, analysis_id: int) -> asyncio.Lock:
        return self._locks.setdefault(analysis_id, asyncio.Lock())

    async def _publish(self, analysis_id: int) -> ShareRecord:
        record = await self._get(analysis_id)
        if record.share is not None:
            return record.share
        if record.status != "finished" or record.verdict is None:
            raise ShareUnavailable("only a finished analysis with a verdict can be published")
        key = secrets.token_urlsafe(self._token_bytes)
        url = await self._publisher.publish(key, self._render(record))
        try:
            share = await self._repo.save_share(analysis_id, key, url)
        except BaseException:
            # A page nobody can withdraw must not outlive a failed save. A cancellation while
            # the upload thread runs can still leave an orphan object (accepted, AI_NOTES).
            await self._unpublish_quietly(key)
            raise
        logger.info("share.published analysis=%s backend=%s", analysis_id, self.backend)
        return share

    async def _withdraw(self, analysis_id: int) -> None:
        record = await self._get(analysis_id)
        if record.share is None:
            raise ShareNotFound(str(analysis_id))
        await self._publisher.unpublish(record.share.key)
        await self._repo.delete_share(analysis_id)
        logger.info("share.withdrawn analysis=%s", analysis_id)

    async def _get(self, analysis_id: int) -> AnalysisRecord:
        record = await self._repo.get(analysis_id)
        if record is None:
            raise AnalysisNotFound(str(analysis_id))
        return record

    async def _unpublish_quietly(self, key: str) -> None:
        try:
            await self._publisher.unpublish(key)
        except Exception:  # noqa: BLE001 — the original error is the one to report
            logger.exception("could not remove orphaned snapshot %s", key)
