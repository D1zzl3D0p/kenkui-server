"""Local job admission, snapshots, event streams, cancellation, and artifacts."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

import kenkui as kk
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from kenkui_server.api.assets import _authorize_asset
from kenkui_server.api.schemas import (
    CastingRequest,
    EventResponse,
    JobListResponse,
    JobRequest,
    JobResponse,
    PreflightResponse,
    ProgressResponse,
)
from kenkui_server.jobs.models import (
    CharacterCasting,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.jobs.pipeline import pipeline_from_job

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _response(job: Job, failure: dict[str, str] | None = None) -> JobResponse:
    return JobResponse(
        failure=failure,
        source_id=job.spec.source_id,
        source_cover=job.spec.output.source_cover,
        title=job.spec.output.title,
        author=job.spec.output.author,
        narrator_voice_id=(
            job.spec.casting.voice_id
            if isinstance(job.spec.casting, SingleVoiceCasting)
            else job.spec.casting.narrator_voice_id
        ),
        casting_mode="single" if isinstance(job.spec.casting, SingleVoiceCasting) else "characters",
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


def _casting(
    request: CastingRequest, allowed_models: tuple[str, ...]
) -> SingleVoiceCasting | CharacterCasting:
    """Choose a casting shape from the request, refusing an unlisted model.

    Naming a model means character casting: attribution cannot run without
    one. The allowlist is checked here rather than at execution so a job is
    never admitted that the worker would have to abandon.
    """
    if request.model_id is None:
        if request.voice_id is None:
            raise HTTPException(status_code=422, detail="a voice is required")
        return SingleVoiceCasting(request.voice_id)
    if request.model_id not in allowed_models:
        raise HTTPException(status_code=422, detail="model_not_allowed")
    narrator = request.narrator_voice_id or request.voice_id
    if narrator is None:
        raise HTTPException(status_code=422, detail="a narrator voice is required")
    return CharacterCasting(
        narrator_voice_id=narrator,
        unknown_voice_id=request.unknown_voice_id or "",
        cast=tuple(request.cast.items()),
        method=request.method,
        model_id=request.model_id,
    )


def _spec(request: JobRequest, allowed_models: tuple[str, ...] = ()) -> JobSpec:
    if request.output.format != "m4b":
        raise HTTPException(status_code=422, detail="only m4b output is supported")
    try:
        return JobSpec(
            source_id=request.source_id,
            chapters=tuple(request.chapters),
            casting=_casting(request.casting, allowed_models),
            tts=TtsSettings(
                chapter_pauses=request.tts.chapter_pauses,
                prepare_numbers=request.tts.prepare_numbers,
                pronunciation_corrections=request.tts.pronunciation_corrections,
                stutter_handling=request.tts.stutter_handling,
                chapter_pause_ms=request.tts.chapter_pause_ms,
                heading_before_pause_ms=request.tts.heading_before_pause_ms,
                heading_after_pause_ms=request.tts.heading_after_pause_ms,
                paragraph_pause_ms=request.tts.paragraph_pause_ms,
                line_pause_ms=request.tts.line_pause_ms,
                scene_pause_ms=request.tts.scene_pause_ms,
            ),
            output=OutputSpec(
                "artifact.m4b",
                request.output.title,
                request.output.author,
                request.output.source_cover,
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _preflight(request: Request, payload: JobRequest) -> tuple[JobSpec, int]:
    services = request.app.state.services
    spec = _spec(payload, request.app.state.model_allowlist)
    try:
        services.repositories.assets.get(spec.source_id)
        _authorize_asset(request, spec.source_id)
        inspection = services.repositories.inspections.get(spec.source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="asset inspection not found") from error
    known = {chapter.id for chapter in inspection.chapters}
    if not set(spec.chapters).issubset(known):
        raise HTTPException(status_code=422, detail="unknown chapter id")
    casting = spec.casting
    voice_ids = (
        (casting.voice_id,)
        if isinstance(casting, SingleVoiceCasting)
        else (casting.narrator_voice_id, casting.unknown_voice_id, *(v for _, v in casting.cast))
    )
    available = {voice.id for voice in services.voices if voice.enabled}
    if not set(voice_ids).issubset(available):
        raise HTTPException(status_code=422, detail="voice is unavailable")
    try:
        with services.assets.materialize_source(spec.source_id) as source:
            pipeline = pipeline_from_job(spec, source)
            inspected = pipeline.inspect()
    except kk.KenkuiError as error:
        raise HTTPException(status_code=422, detail=error.code.value) from error
    characters = sum(chapter.speech_characters or 0 for chapter in inspected.chapters)
    if characters <= 0:
        raise HTTPException(422, "empty_speech")
    if characters > request.app.state.max_speech_characters:
        raise HTTPException(422, "job_size_limit")
    return spec, characters


@router.post("/preflight", response_model=PreflightResponse)
def preflight(payload: JobRequest, request: Request) -> PreflightResponse:
    """Validate executable local intent without creating a Job or reservation."""
    spec, characters = _preflight(request, payload)
    services = request.app.state.hosted_services
    if services is None:
        return PreflightResponse(source_id=spec.source_id, normalized_characters=characters)
    from kenkui_server.billing.pricing import credits_for_characters

    identity = request.state.hosted_identity
    account = services.repositories.billing.account(
        services.account_id_for_identity(identity.user_id)
    )
    from kenkui_server.jobs.models import CharacterCasting

    estimated = credits_for_characters(
        characters, multivoice=isinstance(spec.casting, CharacterCasting)
    )
    return PreflightResponse(
        source_id=spec.source_id,
        normalized_characters=characters,
        estimated_credits=estimated,
        available_credits=account.available_credits,
        valid=account.available_credits >= estimated,
    )


@router.post("", response_model=JobResponse, response_model_exclude_none=True, status_code=202)
def create_job(
    payload: JobRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> JobResponse:
    """Admit local work or hosted credit-backed work before starting its runner."""
    spec, characters = _preflight(request, payload)
    hosted_services = request.app.state.hosted_services
    if hosted_services is None:
        try:
            job = request.app.state.services.dispatcher.submit(
                spec, idempotency_key=idempotency_key
            )
        except ValueError as error:
            if str(error) == "idempotency_conflict":
                raise HTTPException(status_code=409, detail="idempotency_conflict") from error
            raise
    else:
        identity = getattr(request.state, "hosted_identity", None)
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            job = request.app.state.services.dispatcher.submit(
                spec,
                owner_id=identity.user_id,
                account_id=hosted_services.account_id_for_identity(identity.user_id),
                normalized_speech_characters=characters,
                idempotency_key=idempotency_key,
            )
        except ValueError as error:
            if str(error) in {
                "empty_speech",
                "insufficient_credits",
                "source_expired",
                "active_job_limit",
                "idempotency_conflict",
            }:
                raise HTTPException(status_code=409, detail=str(error)) from error
            raise
    return _response(job)


@router.get("", response_model=JobListResponse, response_model_exclude_none=True)
def list_jobs(request: Request) -> JobListResponse:
    """Return local jobs, or only the caller's owned jobs in hosted mode."""
    jobs = request.app.state.services.repositories.jobs.list()
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


@router.get("/{job_id}", response_model=JobResponse, response_model_exclude_none=True)
def get_job(job_id: str, request: Request) -> JobResponse:
    """Return the authoritative durable snapshot used for SSE reconnect recovery."""
    try:
        job = request.app.state.services.repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    _authorize_job(request, job_id)
    return _response(job, request.app.state.services.repositories.failure_for_job(job_id))


@router.post("/{job_id}/cancel", response_model=JobResponse, response_model_exclude_none=True)
def cancel_job(job_id: str, request: Request) -> JobResponse:
    """Durably request cancellation once; repeated calls return the same snapshot."""
    repositories = request.app.state.services.repositories
    try:
        repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    _authorize_job(request, job_id)
    return _response(repositories.request_cancellation(job_id))


@router.get(
    "/{job_id}/events",
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-event-schema": {"$ref": "#/components/schemas/EventResponse"},
                }
            }
        }
    },
)
async def stream_events(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Stream append-only events; callers re-fetch the snapshot after reconnecting."""
    repositories = request.app.state.services.repositories
    try:
        repositories.jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    _authorize_job(request, job_id)
    try:
        seen = max(int(last_event_id or "0"), 0)
    except ValueError:
        seen = 0

    async def events() -> AsyncIterator[str]:
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
    services = request.app.state.services
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
    filename = (
        re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]', "_", job.spec.output.title or job_id)[:180].strip(" .")
        or job_id
    )
    filename += ".m4b"
    try:
        if request.app.state.hosted_services is None:
            return FileResponse(artifacts[0].path, media_type="audio/mp4", filename=filename)
        return RedirectResponse(
            services.assets.artifact_url(artifacts[0].path, filename=filename),
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="artifact not found") from error
