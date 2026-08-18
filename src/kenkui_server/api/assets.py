"""Private local asset upload and public inspection routes."""

from __future__ import annotations

import hashlib
from uuid import uuid4

import anyio
import kenkui as kk
from fastapi import APIRouter, HTTPException, Request, Response

from kenkui_server.api.schemas import AssetResponse, BookResponse, ChapterResponse
from kenkui_server.jobs.models import Asset, InspectedChapter, Inspection

router = APIRouter(prefix="/v1/assets", tags=["assets"])


@router.post("", response_model=AssetResponse, status_code=201)
async def upload_asset(request: Request) -> AssetResponse:
    """Accept one EPUB body and retain it behind a server-assigned private path."""
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/epub+zip":
        raise HTTPException(status_code=415, detail="expected application/epub+zip")
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=422, detail="empty asset")
    asset_id = str(uuid4())
    services = request.app.state.local_services
    path = services.assets.put_source(asset_id, payload)
    digest = hashlib.sha256(payload).hexdigest()
    asset = Asset(asset_id, str(path), digest, "epub")
    services.repositories.assets.put(asset)
    return AssetResponse(id=asset.id, format=asset.format, sha256=asset.sha256)


@router.get("/{asset_id}/book", response_model=BookResponse)
async def inspect_book(asset_id: str, request: Request) -> BookResponse:
    """Inspect an owned local EPUB through Kenkui's public Pipeline API."""
    services = request.app.state.local_services
    try:
        asset = services.repositories.assets.get(asset_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="asset not found") from error
    try:
        inspected = await anyio.to_thread.run_sync(lambda: kk.book(asset.path).inspect())
    except kk.KenkuiError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    inspection = Inspection(
        source_id=asset.id,
        title=inspected.metadata.title or "",
        author=inspected.metadata.author or "",
        chapters=tuple(InspectedChapter(chapter.id, chapter.title) for chapter in inspected.chapters),
    )
    try:
        services.repositories.inspections.put(inspection)
    except Exception:
        # Inspection is immutable for an uploaded source; return its authoritative snapshot.
        inspection = services.repositories.inspections.get(asset.id)
    return BookResponse(
        source_id=inspection.source_id,
        title=inspection.title,
        author=inspection.author,
        chapters=[
            ChapterResponse(id=chapter.id, title=chapter.title, speech_characters=None)
            for chapter in inspection.chapters
        ],
    )
