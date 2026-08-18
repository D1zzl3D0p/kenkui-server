"""Local job admission, snapshots, event streams, cancellation, and artifacts."""

from __future__ import annotations

import asyncio

import kenkui as kk
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from kenkui_server.api.schemas import (
    EventResponse,
    JobListResponse,
    JobRequest,
    JobResponse,
    PreflightResponse,
    ProgressResponse,
)
from kenkui_server.jobs.models import Job, JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status.value,
        progress=ProgressResponse(
            stage=job.progress.stage, completed=job.progress.completed, total=job.progress.total
        ),
    )


def _authorize_job(request: Request, job_id: str) -> None:
    """Enforce hosted ownership while leaving local deployments unmetered and private."""
    hosted_auth = getattr(request.app.state, "hosted_auth", None)
    if hosted_auth is None:
        return
    identity = getattr(request.state, "hosted_identity", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not hosted_auth.backend.authorize(identity.user_id, hosted_auth.job_owner_resolver(job_id)):
        raise HTTPException(status_code=403, detail="resource is not owned by this user")


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


@router.get("", response_model=JobListResponse)
def list_jobs(request: Request) -> JobListResponse:
    """Return local jobs, or only the caller's owned jobs in hosted mode."""
    jobs = request.app.state.local_services.repositories.jobs.list()
    hosted_auth = getattr(request.app.state, "hosted_auth", None)
    identity = getattr(request.state, "hosted_identity", None)
    if hosted_auth is not None and identity is not None:
        jobs = tuple(
            job
            for job in jobs
            if hosted_auth.backend.authorize(
                identity.user_id, hosted_auth.job_owner_resolver(job.id)
            )
        )
    return JobListResponse(items=[_response(job) for job in jobs])


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request) -> JobResponse:
    """Return the authoritative durable snapshot used for SSE reconnect recovery."""
    try:
        job = request.app.state.local_services.repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    _authorize_job(request, job_id)
    return _response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request) -> JobResponse:
    """Durably request cancellation once; repeated calls return the same snapshot."""
    repositories = request.app.state.local_services.repositories
    try:
        repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    _authorize_job(request, job_id)
    return _response(repositories.request_cancellation(job_id))


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
    _authorize_job(request, job_id)
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
    _authorize_job(request, job_id)
    if job.status.value != "succeeded":
        raise HTTPException(status_code=409, detail="artifact is not available")
    artifacts = services.repositories.artifacts.list_for_job(job_id)
    if len(artifacts) != 1:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        return Response(services.assets.read_artifact(job_id), media_type="audio/mp4")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="artifact not found") from error
