"""Local job admission, snapshots, event streams, cancellation, and artifacts."""

from __future__ import annotations

import asyncio

import kenkui as kk
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from kenkui_server.api.schemas import EventResponse, JobRequest, JobResponse, PreflightResponse, ProgressResponse
from kenkui_server.jobs.models import Job, JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings
from kenkui_server.jobs.transitions import CancelRequested, transition
from kenkui_server.storage.repositories import StaleWriteError

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status.value,
        progress=ProgressResponse(
            stage=job.progress.stage, completed=job.progress.completed, total=job.progress.total
        ),
    )


def _spec(request: JobRequest) -> JobSpec:
    if request.output.format != "m4b":
        raise HTTPException(status_code=422, detail="only m4b output is supported")
    try:
        return JobSpec(
            source_id=request.source_id,
            chapters=tuple(request.chapters),
            casting=SingleVoiceCasting(request.casting.voice_id),
            tts=TtsSettings(request.tts.normalize_text),
            output=OutputSpec("artifact.m4b"),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _preflight(request: Request, payload: JobRequest) -> tuple[JobSpec, int]:
    services = request.app.state.local_services
    spec = _spec(payload)
    try:
        asset = services.repositories.assets.get(spec.source_id)
        inspection = services.repositories.inspections.get(spec.source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="asset inspection not found") from error
    known = {chapter.id for chapter in inspection.chapters}
    if not set(spec.chapters).issubset(known):
        raise HTTPException(status_code=422, detail="unknown chapter id")
    voice = next((voice for voice in services.voices if voice.id == spec.casting.voice_id), None)
    if voice is None or not voice.enabled:
        raise HTTPException(status_code=422, detail="voice is unavailable")
    try:
        pipeline = kk.book(asset.path).select_chapters(*spec.chapters)
        if spec.tts.normalize_text:
            pipeline = pipeline.normalize_text()
        inspected = pipeline.inspect()
    except kk.KenkuiError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return spec, sum(chapter.speech_characters or 0 for chapter in inspected.chapters)


@router.post("/preflight", response_model=PreflightResponse)
def preflight(payload: JobRequest, request: Request) -> PreflightResponse:
    """Validate executable local intent without creating a Job or reservation."""
    spec, characters = _preflight(request, payload)
    return PreflightResponse(source_id=spec.source_id, normalized_characters=characters)


@router.post("", response_model=JobResponse, status_code=202)
def create_job(
    payload: JobRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> JobResponse:
    """Admit a locally unmetered job and commit its dispatch before starting work."""
    spec, _ = _preflight(request, payload)
    job = request.app.state.local_services.dispatcher.submit(spec, idempotency_key=idempotency_key)
    return _response(job)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request) -> JobResponse:
    """Return the authoritative durable snapshot used for SSE reconnect recovery."""
    try:
        return _response(request.app.state.local_services.repositories.jobs.get(job_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request) -> JobResponse:
    """Durably request cancellation once; repeated calls return the same snapshot."""
    repositories = request.app.state.local_services.repositories
    while True:
        try:
            current = repositories.jobs.get(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        if current.status.is_terminal:
            return _response(current)
        cancelled = transition(current, CancelRequested())
        try:
            repositories.update_job_and_append_event(
                cancelled, expected_version=current.version, event_type="cancelled"
            )
        except StaleWriteError:
            continue
        return _response(cancelled)


@router.get("/{job_id}/events")
async def stream_events(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Stream append-only events; callers re-fetch the snapshot after reconnecting."""
    repositories = request.app.state.local_services.repositories
    try:
        repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    try:
        seen = max(int(last_event_id or "0"), 0)
    except ValueError:
        seen = 0

    async def events() -> object:
        nonlocal seen
        while True:
            for event in repositories.events.list_for_job(job_id):
                if event.sequence <= seen:
                    continue
                seen = event.sequence
                payload = EventResponse(
                    sequence=event.sequence,
                    type=event.event_type,
                    progress=ProgressResponse(
                        stage=event.progress.stage,
                        completed=event.progress.completed,
                        total=event.progress.total,
                    ),
                ).model_dump_json(by_alias=True)
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
            snapshot = repositories.jobs.get(job_id)
            if snapshot.status.is_terminal:
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/{job_id}/artifact")
def get_artifact(job_id: str, request: Request) -> Response:
    """Authorize artifact retrieval from an owned completed local job only."""
    services = request.app.state.local_services
    try:
        job = services.repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    if job.status.value != "succeeded":
        raise HTTPException(status_code=409, detail="artifact is not available")
    artifacts = services.repositories.artifacts.list_for_job(job_id)
    if len(artifacts) != 1:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        return Response(services.assets.read_artifact(job_id), media_type="audio/mp4")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="artifact not found") from error
