"""Authenticated, bounded internal operations for the Neon API."""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import anyio
import kenkui as kk
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from kenkui_server.api.assets import get_cover, inspect_book
from kenkui_server.api.covers import MAX_COVER_BYTES, image_type, replace_cover
from kenkui_server.api.jobs import _preflight
from kenkui_server.api.schemas import (
    AssetResponse,
    BookResponse,
    CastingRequest,
    JobRequest,
    OutputRequest,
    PreflightResponse,
    TtsRequest,
)
from kenkui_server.config import DEFAULT_CHARACTER_MODEL
from kenkui_server.jobs.models import Asset
from kenkui_server.storage.source_uploads import SourceUploadConflict

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Result = TypeVar("Result")
MAX_REQUEST_BYTES = 4 * ((MAX_COVER_BYTES + 2) // 3) + 65536


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OperationRequest(WireModel):
    environment: Literal["staging", "production"]


class SourceRequest(OperationRequest):
    source_id: Identifier = Field(alias="sourceId")
    owner_id: Identifier = Field(alias="ownerId")


class StrictCasting(CastingRequest):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)
    voice_id: ShortText | None = Field(None, alias="voiceId")
    narrator_voice_id: ShortText | None = Field(None, alias="narratorVoiceId")
    unknown_voice_id: ShortText | None = Field(None, alias="unknownVoiceId")
    model_id: ShortText | None = Field(None, alias="modelId")
    method: ShortText = "gendered"
    cast: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=1000)


class StrictTts(TtsRequest):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


class StrictOutput(OutputRequest):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


class StrictJob(JobRequest):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)
    source_id: Identifier = Field(alias="sourceId")
    chapters: list[ShortText] = Field(min_length=1, max_length=10000)
    casting: StrictCasting
    tts: StrictTts = Field(default_factory=StrictTts)
    output: StrictOutput = Field(default_factory=StrictOutput)


class PreflightRequest(OperationRequest):
    owner_id: Identifier = Field(alias="ownerId")
    job: StrictJob


class CoverRequest(SourceRequest):
    cover_base64: str = Field(
        alias="coverBase64", min_length=1, max_length=4 * ((MAX_COVER_BYTES + 2) // 3)
    )
    media_type: Literal["image/png", "image/jpeg"] = Field(alias="mediaType")


class DispatchRequest(OperationRequest):
    # Only committed dispatches authorize work; no caller-supplied user identity.
    dispatch_id: Identifier = Field(alias="dispatchId")


class CoverResponse(WireModel):
    cover_base64: Annotated[str, Field(max_length=11184812)] = Field(alias="coverBase64")
    media_type: Literal["image/png", "image/jpeg"] = Field(alias="mediaType")


class DispatchResponse(WireModel):
    accepted: bool


@dataclass(frozen=True)
class BridgeServices:
    repositories: Any
    assets: Any
    voices: tuple[kk.Voice, ...]
    runner: Any
    close: Callable[[], None]


def _source(services: Any, source_id: str, owner_id: str) -> Asset:
    try:
        asset: Asset = services.repositories.assets.get(source_id)
        owner = services.repositories.assets.owner_id(source_id)
    except KeyError as error:
        raise HTTPException(404, "asset not found") from error
    if str(owner) != owner_id:
        raise HTTPException(403, "resource is not owned by this user")
    if asset.format != "epub":
        raise HTTPException(415, "expected application/epub+zip")
    return asset


def _replace_cover(services: Any, payload: CoverRequest, max_upload_bytes: int) -> AssetResponse:
    source = _source(services, payload.source_id, payload.owner_id)
    try:
        image = base64.b64decode(payload.cover_base64, validate=True)
        if len(image) > MAX_COVER_BYTES:
            raise HTTPException(413, "Cover exceeds 8 MB.")
        if image_type(image) != payload.media_type:
            raise ValueError("cover media type mismatch")
    except (ValueError, binascii.Error) as error:
        raise HTTPException(422, "The cover or EPUB is invalid.") from error
    # Version plus immutable source/image hashes make retry bytes deterministic.
    identifier = uuid5(
        NAMESPACE_URL,
        f"kenkui-cover-v1:{payload.environment}:{payload.owner_id}:{source.id}:"
        f"{source.sha256}:{hashlib.sha256(image).hexdigest()}",
    )
    try:
        new_id = services.repositories.source_uploads.cover_attempt(
            str(identifier), payload.owner_id
        )
    except SourceUploadConflict as error:
        raise HTTPException(409, "cover retry conflicts with stored asset") from error
    identifier = UUID(new_id)
    try:
        existing = services.repositories.assets.get(new_id)
    except KeyError:
        existing = None
    if existing is not None:
        _source(services, new_id, payload.owner_id)
        return AssetResponse(id=existing.id, format=existing.format, sha256=existing.sha256)
    try:
        with services.assets.materialize_source(source.id) as path:
            updated = replace_cover(path.read_bytes(), image, identifier=identifier)
    except (
        ValueError,
        BadZipFile,
        ParseError,
        DefusedXmlException,
        StopIteration,
        KeyError,
    ) as error:
        raise HTTPException(422, "The cover or EPUB is invalid.") from error
    if len(updated) > max_upload_bytes:
        raise HTTPException(413, "The book with its new cover exceeds the upload limit.")
    # Reserve durable cleanup tracking before PUT. Same-byte concurrent retries
    # share one intent; completed asset rows retain tracking after finalization.
    asset = Asset(new_id, new_id, hashlib.sha256(updated).hexdigest(), "epub")
    try:
        existing = services.repositories.source_uploads.begin(asset, payload.owner_id)
        if existing is not None:
            return AssetResponse(id=existing.id, format=existing.format, sha256=existing.sha256)
        services.assets.put_source(new_id, updated)
        asset = services.repositories.source_uploads.complete(new_id, payload.owner_id)
    except SourceUploadConflict as error:
        raise HTTPException(409, "cover retry conflicts with stored asset") from error
    return AssetResponse(id=asset.id, format=asset.format, sha256=asset.sha256)


def create_bridge_app(
    *,
    environment: Literal["staging", "production"],
    secret: str,
    services_factory: Callable[[], Any],
    max_upload_bytes: int = 50 * 1024 * 1024,
    max_speech_characters: int = 10000000,
    request_timeout_seconds: float = 120,
    max_operations: int = 2,
    model_allowlist: tuple[str, ...] = (DEFAULT_CHARACTER_MODEL,),
) -> FastAPI:
    """Modal proxy auth is mandatory too; this secret protects the inner app."""
    if environment not in {"staging", "production"} or not secret:
        raise ValueError("bridge environment and secret are required")
    if not 0 < request_timeout_seconds <= 120:
        raise ValueError("bridge deadline must be within 120 seconds")
    if max_operations < 1:
        raise ValueError("bridge operation capacity must be positive")
    services: Any = None
    operations: anyio.abc.TaskGroup | None = None
    capacity: anyio.Semaphore | None = None
    accepting = False

    async def bounded(operation: Callable[[], Result]) -> Result:
        if not accepting or operations is None or capacity is None:
            raise HTTPException(503, "bridge is shutting down")
        try:
            capacity.acquire_nowait()
        except anyio.WouldBlock as error:
            raise HTTPException(429, "bridge operation capacity exhausted") from error
        finished = anyio.Event()
        result: list[Result] = []
        failure: list[Exception] = []
        slot = capacity

        async def execute() -> None:
            # The lifespan owns this work, independently of request deadlines
            # and disconnects. Retain both admission and the thread limiter
            # until real completion, including during graceful shutdown.
            with anyio.CancelScope(shield=True):
                try:
                    result.append(await anyio.to_thread.run_sync(operation))
                except Exception as error:
                    failure.append(error)
                finally:
                    slot.release()
                    finished.set()

        try:
            operations.start_soon(execute)
        except BaseException:
            slot.release()
            raise
        try:
            with anyio.fail_after(request_timeout_seconds):
                await finished.wait()
        except TimeoutError as error:
            # This bounds the HTTP wait only. The tracked operation keeps its
            # slot and dependencies until it actually finishes; retries may
            # receive 429. Do not assume Modal kills post-response threads.
            raise HTTPException(504, "bridge operation deadline exceeded") from error
        if failure:
            raise failure[0]
        return result[0]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal operations, capacity, accepting
        try:
            async with anyio.create_task_group() as tasks:
                operations = tasks
                capacity = anyio.Semaphore(max_operations)
                accepting = True
                try:
                    yield
                finally:
                    accepting = False
            # Task-group exit drains shielded operations before dependencies
            # close. A hung provider call can delay graceful shutdown.
        finally:
            operations = None
            capacity = None
            if services is not None and callable(getattr(services, "close", None)):
                services.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hosted_auth = None  # Ownership checked before invoking legacy helpers.
    app.state.model_allowlist = model_allowlist
    app.state.max_upload_bytes = max_upload_bytes
    app.state.max_speech_characters = max_speech_characters

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("X-Kenkui-Bridge-Secret", "")
        if not secrets.compare_digest(supplied.encode(), secret.encode()):
            return JSONResponse({"detail": "unauthenticated"}, status_code=401)
        # Authenticate before parsing/DB access; bound actual streamed bytes.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": "request too large"}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
        return await call_next(request)

    def dependencies(payload: OperationRequest) -> Any:
        nonlocal services
        if payload.environment != environment:
            raise HTTPException(403, "environment mismatch")
        # Event-loop initialization has no await; requests share one lazy pool.
        if services is None:
            services = services_factory()
            app.state.services = services
        return services

    @app.post("/inspect", response_model=BookResponse)
    async def inspect(payload: SourceRequest, request: Request) -> BookResponse:
        selected = dependencies(payload)

        def calculate() -> BookResponse:
            _source(selected, payload.source_id, payload.owner_id)
            return anyio.run(inspect_book, payload.source_id, request)

        return await bounded(calculate)

    @app.post("/cover", response_model=CoverResponse)
    async def read_source_cover(payload: SourceRequest, request: Request) -> CoverResponse:
        selected = dependencies(payload)

        def calculate() -> CoverResponse:
            _source(selected, payload.source_id, payload.owner_id)
            response = anyio.run(get_cover, payload.source_id, request)
            # Existing read_cover enforces MAX_COVER_BYTES before decompressing.
            media_type = response.media_type
            if media_type not in {"image/png", "image/jpeg"}:
                raise HTTPException(422, "The source cover cannot be read.")
            return CoverResponse(
                coverBase64=base64.b64encode(bytes(response.body)).decode("ascii"),
                mediaType="image/png" if media_type == "image/png" else "image/jpeg",
            )

        return await bounded(calculate)

    @app.post("/preflight", response_model=PreflightResponse)
    async def preflight(payload: PreflightRequest, request: Request) -> PreflightResponse:
        selected = dependencies(payload)

        def calculate() -> PreflightResponse:
            _source(selected, payload.job.source_id, payload.owner_id)
            spec, characters = _preflight(request, payload.job)
            return PreflightResponse(source_id=spec.source_id, normalized_characters=characters)

        return await bounded(calculate)

    @app.post("/replace_cover", response_model=AssetResponse)
    async def cover(payload: CoverRequest) -> AssetResponse:
        selected = dependencies(payload)
        return await bounded(lambda: _replace_cover(selected, payload, max_upload_bytes))

    @app.post("/dispatch", response_model=DispatchResponse)
    async def dispatch(payload: DispatchRequest) -> DispatchResponse:
        selected = dependencies(payload)

        def submit() -> DispatchResponse:
            try:
                committed = selected.repositories.dispatches.get(payload.dispatch_id)
                job = selected.repositories.jobs.get(committed.job_id)
                owner = selected.repositories.jobs.owner_id(job.id)
                _source(selected, job.spec.source_id, str(owner))
            except KeyError as error:
                raise HTTPException(404, "dispatch not found") from error
            selected.runner.start(committed.id)
            # Acceptance does not promise a new worker: Python leases own retries.
            return DispatchResponse(accepted=True)

        return await bounded(submit)

    return app


def bridge_from_environment() -> FastAPI:
    """Compose one isolated environment lazily; never migrate on a request."""
    import os

    from kenkui_server.compute.modal import ModalProcessRunner
    from kenkui_server.hosted import object_store, required
    from kenkui_server.storage.postgres import PostgresHostedRepository
    from kenkui_server.storage.postgres_database import PostgresDatabase
    from kenkui_server.voice_catalog import VCTK_VOICE_SET, select_hosted_voices

    environment = required("KENKUI_DEPLOYMENT")
    if environment not in {"staging", "production"}:
        raise ValueError("invalid bridge environment")

    def services() -> BridgeServices:
        database = PostgresDatabase(
            required("DATABASE_URL"),
            idle=os.environ.get("KENKUI_DATABASE_IDLE") == "1",
            connect_timeout=15,
        )
        try:
            repositories = PostgresHostedRepository(database)
            return BridgeServices(
                repositories,
                object_store(),
                select_hosted_voices(
                    os.environ.get("KENKUI_VOICE_SET", VCTK_VOICE_SET), kk.list_voices()
                ),
                ModalProcessRunner(
                    repositories,
                    app_name=f"kenkui-{environment}",
                    max_jobs=int(os.environ.get("KENKUI_MAX_JOBS", "2")),
                ),
                database.close,
            )
        except BaseException:
            database.close()
            raise

    return create_bridge_app(
        environment=environment,  # type: ignore[arg-type]
        secret=required("KENKUI_BRIDGE_SECRET"),
        services_factory=services,
        max_speech_characters=int(os.environ.get("KENKUI_MAX_SPEECH_CHARACTERS", "10000000")),
        model_allowlist=tuple(
            filter(
                None,
                (
                    os.environ.get("KENKUI_MODEL_ALLOWLIST", "").strip() or DEFAULT_CHARACTER_MODEL
                ).split(","),
            )
        ),
    )
