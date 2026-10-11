"""Pick the snapshot storage from settings: GCS when ``SHARE_BUCKET`` is set, else a dir."""

from __future__ import annotations

from pathlib import Path

from ...config import Settings
from ...domain.ports import SnapshotPublisher
from .gcs import GcsPublisher
from .local import LocalDirPublisher


def build_publisher(settings: Settings) -> SnapshotPublisher:
    if settings.share_backend == "gcs":
        return GcsPublisher(settings.share_bucket, cache_max_age_s=settings.share_cache_max_age_s)
    return LocalDirPublisher(Path(settings.share_dir), base_url=settings.public_url)
