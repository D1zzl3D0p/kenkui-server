"""Registry-derived voice discovery for the local server."""

from fastapi import APIRouter, Request

from kenkui_server.api.schemas import VoiceListResponse, VoiceResponse

router = APIRouter(prefix="/v1/voices", tags=["voices"])


@router.get("", response_model=VoiceListResponse)
def list_voices(request: Request) -> VoiceListResponse:
    """Return only enabled entries in the server's explicit local registry."""
    voices = request.app.state.services.voices
    return VoiceListResponse(
        items=[
            VoiceResponse(
                id=voice.id,
                name=voice.name,
                language=voice.language,
                license_id=voice.license_id,
                voice_rights=voice.voice_rights,
            )
            for voice in voices
            if voice.enabled
        ]
    )
