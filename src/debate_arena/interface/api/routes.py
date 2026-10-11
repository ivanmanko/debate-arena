"""Routes — validation, routing, HTTP. All logic lives in AnalysisService."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ... import __version__
from ...application.chat import JudgeChatService
from ...application.service import AnalysisService
from ...application.share import ShareService
from ...config import Settings
from ...domain.exceptions import (
    AnalysisNotFound,
    AnalysisRunning,
    ChatUnavailable,
    ProviderError,
    ProviderNotConfigured,
    ProviderRefused,
    PublishError,
    ShareNotFound,
    ShareUnavailable,
)
from .schemas import (
    AnalysisCreated,
    AnalysisDetail,
    AnalysisRequest,
    AnalysisSummaryOut,
    AnalystOut,
    ChatExchangeOut,
    ChatRequestIn,
    ConfigOut,
    HealthOut,
    ShareOut,
)
from .sse import sse_stream

router = APIRouter()
logger = logging.getLogger(__name__)


def get_service(request: Request) -> AnalysisService:
    return request.app.state.service


def get_chat(request: Request) -> JudgeChatService:
    return request.app.state.chat


def get_share(request: Request) -> ShareService:
    return request.app.state.share


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/api/analyses", status_code=status.HTTP_202_ACCEPTED, response_model=AnalysisCreated)
async def create_analysis(
    body: AnalysisRequest, service: AnalysisService = Depends(get_service)
) -> AnalysisCreated:
    try:
        started = await service.start_analysis(
            text=body.text,
            question=body.question,
            rounds=body.rounds,
            model=body.model,
            mode=body.mode,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ProviderNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AnalysisCreated(id=started.id, legend=started.legend)


@router.get("/api/analyses", response_model=list[AnalysisSummaryOut])
async def list_analyses(
    limit: int | None = Query(default=None, ge=1),
    service: AnalysisService = Depends(get_service),
) -> list[AnalysisSummaryOut]:
    return await service.list(limit)


@router.get("/api/analyses/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(
    analysis_id: int, service: AnalysisService = Depends(get_service)
) -> AnalysisDetail:
    try:
        record = await service.get(analysis_id)
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    return AnalysisDetail(
        **record.model_dump(exclude={"turns", "verdict", "chat", "share"}),
        turns=record.turns,
        verdict=record.verdict,
        chat=record.chat,
        share=ShareOut(url=record.share.url, created_at=record.share.created_at)
        if record.share
        else None,
        analysts=[AnalystOut(name=a.name, emoji=a.emoji, focus=a.focus) for a in service.analysts],
    )


@router.get("/api/analyses/{analysis_id}/events")
async def analysis_events(
    analysis_id: int,
    service: AnalysisService = Depends(get_service),
    settings: Settings = Depends(get_settings_dep),
) -> StreamingResponse:
    if not service.is_running(analysis_id):
        try:
            await service.get(analysis_id)
        except AnalysisNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    return StreamingResponse(
        sse_stream(service.subscribe(analysis_id), settings.sse_heartbeat_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/analyses/{analysis_id}/chat", response_model=ChatExchangeOut)
async def ask_judge(
    analysis_id: int, body: ChatRequestIn, chat: JudgeChatService = Depends(get_chat)
) -> ChatExchangeOut:
    try:
        exchange = await chat.ask(analysis_id, body.text, body.legend)
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    except ChatUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ProviderRefused as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Модель отказалась отвечать на это сообщение"
        ) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ProviderNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return ChatExchangeOut(question=exchange.question, reply=exchange.reply)


def _storage_unavailable(exc: PublishError) -> HTTPException:
    """Bucket names, paths and storage error bodies stay in the server log."""
    logger.warning("snapshot storage failed: %s", exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail="snapshot storage is unavailable")


@router.post("/api/analyses/{analysis_id}/share", response_model=ShareOut)
async def publish_analysis(analysis_id: int, share: ShareService = Depends(get_share)) -> ShareOut:
    try:
        record = await share.publish(analysis_id)
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    except ShareUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PublishError as exc:
        raise _storage_unavailable(exc) from exc
    return ShareOut(url=record.url, created_at=record.created_at)


@router.delete("/api/analyses/{analysis_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_analysis(analysis_id: int, share: ShareService = Depends(get_share)) -> Response:
    try:
        await share.withdraw(analysis_id)
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    except ShareNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis is not published") from exc
    except PublishError as exc:
        raise _storage_unavailable(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_analysis(
    analysis_id: int,
    service: AnalysisService = Depends(get_service),
    chat: JudgeChatService = Depends(get_chat),
    share: ShareService = Depends(get_share),
) -> Response:
    if chat.is_busy(analysis_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="the judge is still answering")
    try:
        # A public page must not outlive its analysis: withdraw first, keep it on failure.
        await share.delete_analysis(analysis_id, lambda: service.delete(analysis_id))
    except AnalysisRunning as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="analysis is still running") from exc
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    except PublishError as exc:
        raise _storage_unavailable(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/config", response_model=ConfigOut)
async def get_config(
    service: AnalysisService = Depends(get_service),
    share: ShareService = Depends(get_share),
    settings: Settings = Depends(get_settings_dep),
) -> ConfigOut:
    return ConfigOut(
        models=settings.available_models,
        default_model=settings.effective_model_analyst,
        default_rounds=settings.default_rounds,
        max_rounds=settings.max_rounds,
        max_text_chars=settings.max_text_chars,
        max_question_chars=settings.max_question_chars,
        mock=settings.use_mock,
        analysts=[AnalystOut(name=a.name, emoji=a.emoji, focus=a.focus) for a in service.analysts],
        chat_max_chars=settings.judge_chat_max_chars,
        chat_max_messages=settings.judge_chat_max_messages,
        share_backend=share.backend,
    )


@router.get("/health", response_model=HealthOut)
async def health(settings: Settings = Depends(get_settings_dep)) -> HealthOut:
    return HealthOut(status="ok", version=__version__, mock=settings.use_mock)
