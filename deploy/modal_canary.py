"""Run real synthesis and private R2 round trips without creating user jobs.

From the server checkout, with KENKUI_DEPLOYMENT matching the selected environment:
uv run --extra hosted modal run --env staging -m deploy.modal_canary
"""

from pathlib import Path

import modal

from deploy.modal_app import configure_model_manifest, deployment, image, models, secrets

app = modal.App(f"kenkui-{deployment}-canary")


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models},
    cpu=2,
    memory=8192,
    timeout=600,
    max_containers=1,
)
def verify(source_bytes: bytes) -> dict:
    import hashlib
    import json
    import subprocess
    import time
    from tempfile import TemporaryDirectory
    from uuid import uuid4

    import kenkui as kk

    from kenkui_server.hosted import object_store
    from kenkui_server.voice_catalog import VCTK_VOICE_SET, select_hosted_voices

    configure_model_manifest()
    identifier = f"deployment-canary-{uuid4()}"
    store = object_store()
    voice = select_hosted_voices(VCTK_VOICE_SET, kk.list_voices())[0]
    voice_id = voice.id
    if voice.state != "loaded":
        raise RuntimeError("Provision the worker voice before running the canary")
    started = time.monotonic()
    try:
        store.put_source(identifier, source_bytes)
        with store.materialize_source(identifier) as source, TemporaryDirectory() as directory:
            book = kk.book(source)
            chapter_id = book.inspect().chapters[0].id
            output = Path(directory) / "canary.m4b"
            (
                book.select_chapters(chapter_id)
                .metadata(title="Kenkui deployment canary", author="Kenkui", cover=None)
                .assign_voice(voice_id)
                .tts()
                .write(output, workers=1)
            )
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_chapters",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            metadata = json.loads(probe.stdout)
            if float(metadata["format"]["duration"]) <= 0 or len(metadata["chapters"]) != 1:
                raise RuntimeError("Canary audio or chapter validation failed")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
                check=True,
                capture_output=True,
            )
            store.upload_artifact_file(identifier, output)
            payload = output.read_bytes()
            if store.read_artifact(identifier) != payload:
                raise RuntimeError("R2 artifact round trip changed the output")
            return {
                "voice": voice_id,
                "duration_seconds": float(metadata["format"]["duration"]),
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "chapters": len(metadata["chapters"]),
                "decode": "passed",
                "r2_round_trip": "passed",
            }
    finally:
        try:
            store.delete_source(identifier)
        finally:
            store.delete_artifact(identifier)


@app.local_entrypoint()
def main() -> None:
    import json

    fixture = Path(__file__).resolve().parents[2] / "kenkui-studio/tests/fixtures/book.epub"
    print(json.dumps(verify.remote(fixture.read_bytes()), indent=2))
