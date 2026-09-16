"""Private local asset upload and public inspection routes."""

from __future__ import annotations

import hashlib
from uuid import uuid4
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import anyio
import kenkui as kk
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request, Response

from kenkui_server.api.covers import MAX_COVER_BYTES, read_cover, replace_cover
from kenkui_server.api.schemas import AssetResponse, BookResponse, ChapterResponse
from kenkui_server.jobs.models import Asset, InspectedChapter, Inspection

router = APIRouter(prefix="/v1/assets", tags=["assets"])


def _authorize_asset(request: Request, asset_id: str) -> None:
    """Enforce hosted ownership for private source assets."""
    hosted_auth = getattr(request.app.state, "hosted_auth", None)
    if hosted_auth is None:
        return
    identity = getattr(request.state, "hosted_identity", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if hosted_auth.asset_owner_resolver is None or not hosted_auth.backend.authorize(
        identity.user_id, hosted_auth.asset_owner_resolver(asset_id)
    ):
        raise HTTPException(status_code=403, detail="resource is not owned by this user")


@router.post("", response_model=AssetResponse, status_code=201)
async def upload_asset(request: Request) -> AssetResponse:
    """Accept one EPUB body and retain it behind a server-assigned private path."""
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/epub+zip":
        raise HTTPException(status_code=415, detail="expected application/epub+zip")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > request.app.state.max_upload_bytes:
            raise HTTPException(status_code=413, detail="source exceeds upload limit")
        payload.extend(chunk)
    if not payload:
        raise HTTPException(status_code=422, detail="empty asset")
    asset_id = str(uuid4())
    services = request.app.state.services
    path = services.assets.put_source(asset_id, bytes(payload))
    digest = hashlib.sha256(payload).hexdigest()
    asset = Asset(asset_id, str(path) if path is not None else asset_id, digest, "epub")
    hosted_auth = getattr(request.app.state, "hosted_auth", None)
    if hosted_auth is None:
        services.repositories.assets.put(asset)
    else:
        identity = getattr(request.state, "hosted_identity", None)
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication required")
        services.repositories.assets.put_for_owner(asset, identity.user_id)
    return AssetResponse(id=asset.id, format=asset.format, sha256=asset.sha256)


@router.get("/{asset_id}/book", response_model=BookResponse)
async def inspect_book(asset_id: str, request: Request) -> BookResponse:
    """Inspect an owned local EPUB through Kenkui's public Pipeline API."""
    services = request.app.state.services
    try:
        asset = services.repositories.assets.get(asset_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="asset not found") from error
    _authorize_asset(request, asset_id)
    try:

        def inspect_source() -> kk.BookInspection:
            with services.assets.materialize_source(asset_id) as source:
                return kk.book(source).inspect()

        inspected = await anyio.to_thread.run_sync(inspect_source)
    except kk.KenkuiError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    inspection = Inspection(
        source_id=asset.id,
        title=inspected.metadata.title or "",
        author=inspected.metadata.author or "",
        chapters=tuple(
            InspectedChapter(chapter.id, chapter.title) for chapter in inspected.chapters
        ),
    )
    try:
        services.repositories.inspections.put(inspection)
    except Exception:
        # Inspection is immutable for an uploaded source; return its authoritative snapshot.
        inspection = services.repositories.inspections.get(asset.id)
    speech_counts = {chapter.id: chapter.speech_characters for chapter in inspected.chapters}
    return BookResponse(
        source_id=inspection.source_id,
        title=inspection.title,
        author=inspection.author,
        chapters=[
            ChapterResponse(
                id=chapter.id,
                title=chapter.title,
                speech_characters=speech_counts.get(chapter.id),
            )
            for chapter in inspection.chapters
        ],
    )


@router.get("/{asset_id}/cover")
async def get_cover(asset_id: str, request: Request) -> Response:
    _authorize_asset(request, asset_id)
    services = request.app.state.services
    try:
        services.repositories.assets.get(asset_id)

        def load() -> tuple[bytes, str] | None:
            with services.assets.materialize_source(asset_id) as source:
                return read_cover(source.read_bytes())

        result = await anyio.to_thread.run_sync(load)
    except KeyError as error:
        raise HTTPException(404, "Source or cover not found.") from error
    except (ValueError, BadZipFile, ParseError, DefusedXmlException, StopIteration) as error:
        raise HTTPException(422, "The source cover cannot be read.") from error
    if result is None:
        raise HTTPException(404, "This book has no supported cover.")
    payload, media_type = result
    return Response(
        payload,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{asset_id}/cover", response_model=AssetResponse, status_code=201)
async def upload_cover(asset_id: str, request: Request) -> AssetResponse:
    """Create a new owned EPUB; never mutate a source used by another draft/job."""
    _authorize_asset(request, asset_id)
    services = request.app.state.services
    try:
        services.repositories.assets.get(asset_id)
    except KeyError as error:
        raise HTTPException(404, "Source not found.") from error
    if request.headers.get("content-type", "").split(";")[0] not in {"image/png", "image/jpeg"}:
        raise HTTPException(415, "Choose a PNG or JPEG cover.")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_COVER_BYTES:
            raise HTTPException(413, "Cover exceeds 8 MB.")
        payload.extend(chunk)
    try:

        def transform() -> bytes:
            with services.assets.materialize_source(asset_id) as source:
                return replace_cover(source.read_bytes(), bytes(payload))

        updated = await anyio.to_thread.run_sync(transform)
    except (
        ValueError,
        BadZipFile,
        ParseError,
        DefusedXmlException,
        StopIteration,
        KeyError,
    ) as error:
        raise HTTPException(422, "The cover or EPUB is invalid.") from error
    if len(updated) > request.app.state.max_upload_bytes:
        raise HTTPException(413, "The book with its new cover exceeds the upload limit.")
    new_id = str(uuid4())
    path = services.assets.put_source(new_id, updated)
    asset = Asset(
        new_id,
        str(path) if path is not None else new_id,
        hashlib.sha256(updated).hexdigest(),
        "epub",
    )
    if getattr(request.app.state, "hosted_auth", None) is None:
        services.repositories.assets.put(asset)
    else:
        services.repositories.assets.put_for_owner(asset, request.state.hosted_identity.user_id)
    return AssetResponse(id=asset.id, format=asset.format, sha256=asset.sha256)
