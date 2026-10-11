"""Server-sent events over StreamingResponse with heartbeats (SPEC §3.4, ADR-003)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ...domain.events import AnalysisEvent

_END = object()


def format_event(event: AnalysisEvent) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


async def sse_stream(
    events: AsyncIterator[AnalysisEvent], heartbeat_s: float
) -> AsyncIterator[str]:
    """Pump ``events`` through a queue so heartbeats never cancel the source generator."""
    queue: asyncio.Queue[AnalysisEvent | object] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            await queue.put(_END)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is _END:
                break
            yield format_event(item)  # type: ignore[arg-type]
    finally:
        task.cancel()
