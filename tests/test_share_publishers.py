"""Snapshot storages behind the SnapshotPublisher port (SPEC §4.7, ADR-006)."""

from __future__ import annotations

import pytest

from debate_arena.config import Settings
from debate_arena.domain.exceptions import PublishError
from debate_arena.infrastructure.share.factory import build_publisher
from debate_arena.infrastructure.share.gcs import GcsPublisher
from debate_arena.infrastructure.share.local import LocalDirPublisher


class TestLocalDir:
    async def test_writes_file_and_returns_site_path(self, tmp_path):
        publisher = LocalDirPublisher(tmp_path / "shared", base_url="")
        url = await publisher.publish("abc", "<!doctype html><p>привет</p>")
        assert url == "/shared/abc.html"
        assert (tmp_path / "shared" / "abc.html").read_text("utf-8").endswith("привет</p>")

    async def test_absolute_link_with_public_url(self, tmp_path):
        publisher = LocalDirPublisher(tmp_path, base_url="https://check.example/")
        assert await publisher.publish("abc", "x") == "https://check.example/shared/abc.html"

    async def test_unpublish_removes_file_and_is_idempotent(self, tmp_path):
        publisher = LocalDirPublisher(tmp_path, base_url="")
        await publisher.publish("abc", "x")
        await publisher.unpublish("abc")
        await publisher.unpublish("abc")
        assert not (tmp_path / "abc.html").exists()

    @pytest.mark.parametrize("key", ["../evil", "a/b", ""])
    async def test_rejects_keys_that_escape_the_directory(self, tmp_path, key):
        with pytest.raises(ValueError):
            await LocalDirPublisher(tmp_path, base_url="").publish(key, "x")


class _FakeBlob:
    def __init__(self, bucket: _FakeBucket, name: str) -> None:
        self.bucket, self.name = bucket, name
        self.cache_control: str | None = None

    def upload_from_string(self, data: str, content_type: str) -> None:
        if self.bucket.fail:
            raise RuntimeError("503 backend error")
        self.bucket.objects[self.name] = (data, content_type, self.cache_control)

    def delete(self) -> None:
        if self.bucket.fail:
            raise RuntimeError("503 backend error")
        if self.name not in self.bucket.objects:
            from google.api_core.exceptions import NotFound

            raise NotFound("no such object")
        del self.bucket.objects[self.name]


class _FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, str, str | None]] = {}
        self.fail = False

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)


class TestGcs:
    async def test_uploads_html_with_cache_header_and_returns_public_link(self):
        bucket = _FakeBucket()
        publisher = GcsPublisher("snaps", cache_max_age_s=60, bucket=bucket)
        url = await publisher.publish("abc", "<p>привет</p>")
        assert url == "https://storage.googleapis.com/snaps/abc.html"
        assert bucket.objects["abc.html"] == (
            "<p>привет</p>",
            "text/html; charset=utf-8",
            "public, max-age=60",
        )

    async def test_unpublish_deletes_and_ignores_missing(self):
        bucket = _FakeBucket()
        publisher = GcsPublisher("snaps", cache_max_age_s=60, bucket=bucket)
        await publisher.publish("abc", "x")
        await publisher.unpublish("abc")
        await publisher.unpublish("abc")
        assert bucket.objects == {}

    async def test_storage_errors_become_publish_error(self):
        bucket = _FakeBucket()
        bucket.fail = True
        publisher = GcsPublisher("snaps", cache_max_age_s=60, bucket=bucket)
        with pytest.raises(PublishError):
            await publisher.publish("abc", "x")
        with pytest.raises(PublishError):
            await publisher.unpublish("abc")


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, mock_llm=True, **overrides)


def test_factory_defaults_to_local_dir(tmp_path):
    publisher = build_publisher(_settings(share_dir=str(tmp_path)))
    assert isinstance(publisher, LocalDirPublisher)
    assert publisher.backend == "local"


def test_settings_report_backend():
    assert _settings().share_backend == "local"
    assert _settings(share_bucket="snaps").share_backend == "gcs"
