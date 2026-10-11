"""FakePublisher — in-memory SnapshotPublisher with a failure switch (ADR-006 port)."""

from __future__ import annotations

from debate_arena.domain.exceptions import PublishError


class FakePublisher:
    backend = "local"

    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []  # (operation, key)
        self.fail = False

    async def publish(self, key: str, html: str) -> str:
        self.calls.append(("publish", key))
        if self.fail:
            raise PublishError("storage is down")
        self.objects[key] = html
        return f"https://snapshots.test/{key}.html"

    async def unpublish(self, key: str) -> None:
        self.calls.append(("unpublish", key))
        if self.fail:
            raise PublishError("storage is down")
        self.objects.pop(key, None)
