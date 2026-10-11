"""FastAPI application: wiring of settings, database, providers and the service (ADR-004).

Run with ``rational-check`` or ``uvicorn --factory debate_arena.main:create_app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .application.chat import JudgeChatService
from .application.service import AnalysisService, ProviderFactory
from .application.share import ShareService
from .config import Settings, get_settings
from .domain.ports import AnalysisRepository, SnapshotPublisher
from .infrastructure.db.repo import SqlAlchemyAnalysisRepository
from .infrastructure.db.session import Database
from .infrastructure.llm.factory import ProviderRegistry
from .infrastructure.share.factory import build_publisher
from .infrastructure.share.local import SHARED_PATH
from .interface.api.routes import router
from .interface.share.page import render_snapshot

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "interface" / "web" / "static"


def create_app(
    settings: Settings | None = None,
    *,
    repo: AnalysisRepository | None = None,
    provider_factory: ProviderFactory | None = None,
    publisher: SnapshotPublisher | None = None,
) -> FastAPI:
    """Build the app. Tests inject an in-memory repo, a fake provider factory and publisher."""
    settings = settings or get_settings()
    snapshot_publisher = publisher or build_publisher(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database: Database | None = None
        active_repo = repo
        if active_repo is None:
            database = Database(settings.database_url)
            await database.init()
            active_repo = SqlAlchemyAnalysisRepository(database.sessions)
        registry = ProviderRegistry(settings)
        factory = provider_factory or registry.get
        service = AnalysisService(repo=active_repo, provider_factory=factory, settings=settings)
        await service.recover_interrupted()
        app.state.settings = settings
        app.state.service = service
        app.state.chat = JudgeChatService(
            repo=active_repo, provider_factory=factory, settings=settings
        )
        app.state.share = ShareService(
            repo=active_repo,
            publisher=snapshot_publisher,
            render=lambda record: render_snapshot(record, service.analysts),
            token_bytes=settings.share_token_bytes,
        )
        if settings.use_mock:
            logger.warning("No provider key for the selected models (or MOCK_LLM=1) — mock engine")
        logger.info(
            "rational-check %s: analysts=%s judge=%s",
            __version__,
            settings.effective_model_analyst,
            settings.effective_model_judge,
        )
        try:
            yield
        finally:
            await service.shutdown()
            await registry.aclose()
            if database is not None:
                await database.dispose()

    app = FastAPI(title="Rational Check", version=__version__, lifespan=lifespan)
    app.include_router(router)

    if settings.share_backend == "local":
        # Snapshots without GCS (SPEC §4.7); the directory appears on the first publish.
        app.mount(
            SHARED_PATH,
            StaticFiles(directory=settings.share_dir, check_dir=False),
            name="shared",
        )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.web_host, port=settings.web_port)
