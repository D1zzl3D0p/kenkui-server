"""The quote, durable intent, and pipeline agree on opt-out announcements."""

import json
from io import BytesIO
from zipfile import ZipFile

import kenkui as kk
import pytest
from fastapi.testclient import TestClient
from test_local_job_api import _epub

from kenkui_server.app import create_app
from kenkui_server.jobs.models import JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings
from kenkui_server.jobs.pipeline import pipeline_from_job
from kenkui_server.storage.repositories import _decode_spec, _encode_spec


def _titled_epub():
    result = BytesIO()
    with ZipFile(BytesIO(_epub())) as original, ZipFile(result, "w") as archive:
        for name in original.namelist():
            data = original.read(name)
            if name == "OPS/one.xhtml":
                data = data.replace(b"<body>", b"<head><title>Chapter 12</title></head><body>")
            archive.writestr(name, data)
    return result.getvalue()


def test_default_quote_preview_and_saved_job_include_title(tmp_path):
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    app = create_app(data_dir=tmp_path, voices=(voice,), fixture_mode=True)
    with TestClient(app) as client:
        asset = client.post(
            "/v1/assets", content=_titled_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        chapter_id = book["chapters"][0]["id"]
        payload = {
            "sourceId": asset["id"],
            "chapters": [chapter_id],
            "casting": {"voiceId": "narrator"},
        }
        quote = client.post("/v1/jobs/preflight", json=payload).json()
        assert quote["addedTitleCharacters"] == len("Chapter 12")
        assert quote["normalizedCharacters"] == len("Hello narrator.") + len("Chapter 12")
        assert quote["chapterAnnouncements"] == [
            {"chapterId": chapter_id, "text": "Chapter 12", "kind": "inserted"}
        ]
        response = client.post("/v1/jobs", json=payload)
        assert response.status_code == 202
        job = app.state.services.repositories.jobs.get(response.json()["id"])
        assert job.spec.tts.speak_chapter_titles is True
        payload["tts"] = {"speakChapterTitles": False}
        off = client.post("/v1/jobs/preflight", json=payload).json()
        assert off["normalizedCharacters"] == len("Hello narrator.")
        assert off["addedTitleCharacters"] == 0
        payload["tts"] = {
            "chapterTitleOverrides": {chapter_id: "The opening"},
            "chapterTitlePauseMs": 300,
        }
        changed = client.post("/v1/jobs/preflight", json=payload).json()
        assert changed["chapterAnnouncements"][0]["text"] == "The opening"
        payload["tts"] = {"chapterTitleOverrides": {chapter_id: None}}
        excluded = client.post("/v1/jobs/preflight", json=payload).json()
        assert excluded["addedTitleCharacters"] == 0
        payload["tts"] = {"chapterTitleOverrides": {"not-selected": "Invalid"}}
        assert client.post("/v1/jobs/preflight", json=payload).status_code == 422


def test_legacy_persisted_jobs_stay_off_and_new_settings_round_trip(tmp_path):
    spec = JobSpec(
        "source",
        ("c1",),
        SingleVoiceCasting("voice"),
        TtsSettings(
            speak_chapter_titles=True,
            chapter_title_pause_ms=400,
            chapter_title_overrides=(("c1", "Chapter one"),),
        ),
        OutputSpec("out.m4b"),
    )
    assert _decode_spec(_encode_spec(spec)) == spec
    raw = json.loads(_encode_spec(spec))
    for field in ("speak_chapter_titles", "chapter_title_pause_ms", "chapter_title_overrides"):
        del raw["tts"][field]
    legacy = _decode_spec(json.dumps(raw))
    assert legacy.tts.speak_chapter_titles is False
    pipeline = pipeline_from_job(legacy, tmp_path / "unused.epub")
    assert "enabled=False" in repr(pipeline.style)


@pytest.mark.parametrize("value", [-1, 60001, True, 1.5])
def test_invalid_title_pause_rejected(value):
    from pydantic import ValidationError

    from kenkui_server.api.schemas import TtsRequest

    with pytest.raises(ValidationError):
        TtsRequest(chapterTitlePauseMs=value)
