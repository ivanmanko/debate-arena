"""Routes — validation, routing, HTTP. All logic lives in AnalysisService."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ... import __version__
from ...application.service import AnalysisService
from ...config import Settings
from ...domain.exceptions import AnalysisNotFound, AnalysisRunning, ProviderNotConfigured
from .schemas import (
    AnalysisCreated,
    AnalysisDetail,
    AnalysisRequest,
    AnalysisSummaryOut,
    AnalystOut,
    ConfigOut,
    HealthOut,
)
from .sse import sse_stream

router = APIRouter()


def get_service(request: Request) -> AnalysisService:
    return request.app.state.service


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/api/analyses", status_code=status.HTTP_202_ACCEPTED, response_model=AnalysisCreated)
async def create_analysis(
    body: AnalysisRequest, service: AnalysisService = Depends(get_service)
) -> AnalysisCreated:
    try:
        analysis_id = await service.start(
            text=body.text, question=body.question, rounds=body.rounds, model=body.model
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ProviderNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AnalysisCreated(id=analysis_id)


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
        **record.model_dump(exclude={"turns", "verdict"}),
        turns=record.turns,
        verdict=record.verdict,
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


@router.delete("/api/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_analysis(
    analysis_id: int, service: AnalysisService = Depends(get_service)
) -> Response:
    try:
        await service.delete(analysis_id)
    except AnalysisRunning as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="analysis is still running") from exc
    except AnalysisNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="analysis not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/config", response_model=ConfigOut)
async def get_config(
    service: AnalysisService = Depends(get_service),
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
    )


@router.get("/health", response_model=HealthOut)
async def health(settings: Settings = Depends(get_settings_dep)) -> HealthOut:
    return HealthOut(status="ok", version=__version__, mock=settings.use_mock)
