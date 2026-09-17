"""Exercise durable recovery in isolated SQL schemas using real synthesis and R2.

An operator creates the disposable schema and lease fixtures first. This app
never admits user jobs or modifies their billing. Run with --fixture pointing to
that private JSON descriptor; --report selects the non-secret evidence output.
"""

from pathlib import Path

import modal

from deploy.modal_app import configure_model_manifest, deployment, image, models, secrets

app = modal.App(f"kenkui-{deployment}-checkpoint-canary")


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models},
    cpu=2,
    memory=8192,
    timeout=600,
    max_containers=1,
    retries=0,
)
def phase(schema: str, job_id: str, token: str, epub: bytes, stop_after: int) -> dict:
    import hashlib
    import json
    import subprocess
    from dataclasses import replace
    from tempfile import TemporaryDirectory

    import kenkui as kk
    from kenkui import _resolution
    from kenkui._characters import store as casting
    from kenkui._execution import coordinator
    from kenkui.checkpoints import checkpointing

    from kenkui_server.hosted import object_store, required
    from kenkui_server.storage.checkpoints import HostedCheckpointStore
    from kenkui_server.storage.postgres_database import PostgresDatabase

    if not schema.startswith("checkpoint_canary_"):
        raise ValueError("canary schema required")
    configure_model_manifest()
    db = PostgresDatabase(required("DATABASE_URL"), schema=schema)
    objects = object_store()
    checkpoint_store = HostedCheckpointStore(db, objects, job_id, (job_id, token))
    original_bindings = _resolution._execution_bindings
    original_render = coordinator.render_spawned
    submitted_chapters = set()
    submitted_segments = 0

    def no_local_cache(*args, **kwargs):
        return replace(original_bindings(*args, **kwargs), cache_store=None)

    def record(tasks, *args, **kwargs):
        nonlocal submitted_segments
        submitted_segments += len(tasks)
        submitted_chapters.update(task.chapter_id for task in tasks)
        return original_render(tasks, *args, **kwargs)

    class Interrupted(BaseException):
        pass

    def on_event(event):
        if (
            isinstance(event, kk.StageProgress)
            and event.stage == "render"
            and stop_after
            and event.completed == stop_after
        ):
            raise Interrupted()

    _resolution._execution_bindings = no_local_cache
    coordinator.render_spawned = record
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.epub", root / "output.m4b"
            source.write_bytes(epub)
            with checkpointing(checkpoint_store):
                existing = casting.read_response("canary-response")
                if not stop_after and existing != {"speaker": "narrator"}:
                    raise RuntimeError("attribution checkpoint was not restored")
                casting.write_response("canary-response", "fixture", {"speaker": "narrator"})
                pipeline = (
                    kk.epub(source)
                    .metadata(title="Checkpoint canary", cover=None)
                    .assign_voice("eponine")
                    .pauses(chapter_ms=150)
                    .tts()
                )
                try:
                    pipeline.write(output, workers=1, on_event=on_event)
                except Interrupted:
                    if not stop_after:
                        raise
                    result = {"interrupted_after_chapter": stop_after}
                else:
                    if stop_after:
                        raise RuntimeError("canary interruption did not happen")
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
                    if len(metadata["chapters"]) != 2:
                        raise RuntimeError("canary chapter count mismatch")
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
                        check=True,
                        capture_output=True,
                    )
                    identifier = "checkpoint-canary-" + job_id
                    objects.upload_artifact_file(identifier, output)
                    payload = output.read_bytes()
                    if objects.read_artifact(identifier) != payload:
                        raise RuntimeError("artifact round-trip mismatch")
                    result = {
                        "duration_seconds": float(metadata["format"]["duration"]),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                        "decode": "passed",
                        "attribution_restored": True,
                    }
            result.update(
                {
                    "synthesized_chapters": len(submitted_chapters),
                    "submitted_segments": submitted_segments,
                    "ready_chapters": db.execute(
                        "SELECT count(*) AS n FROM job_checkpoints WHERE job_id=%s AND ready "
                        "AND checkpoint_key<>'casting-v1'",
                        (job_id,),
                    ).fetchone()["n"],
                }
            )
            return result
    finally:
        _resolution._execution_bindings = original_bindings
        coordinator.render_spawned = original_render
        db.close()


@app.function(image=image, secrets=secrets, timeout=120)
def cleanup(schema: str, job_ids: list[str]) -> dict:
    from kenkui_server.hosted import object_store, required
    from kenkui_server.storage.postgres_database import PostgresDatabase

    if not schema.startswith("checkpoint_canary_"):
        raise ValueError("canary schema required")
    db = PostgresDatabase(required("DATABASE_URL"), schema=schema)
    objects = object_store()
    try:
        rows = db.execute("SELECT id FROM job_checkpoints").fetchall()
        for row in rows:
            objects.delete_checkpoint(row["id"])
        for job_id in job_ids:
            objects.delete_artifact("checkpoint-canary-" + job_id)
        return {"checkpoint_objects_deleted": len(rows), "artifact_objects_deleted": len(job_ids)}
    finally:
        db.close()


def fixture_epub() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles>'
            '<rootfile full-path="book.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles>'
            '</container>',
        )
        archive.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="id">checkpoint-canary</dc:identifier>'
            '<dc:title>Checkpoint canary</dc:title>'
            '<dc:language>en</dc:language>'
            '</metadata>'
            '<manifest>'
            '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest>'
            '<spine>'
            '<itemref idref="one"/>'
            '<itemref idref="two"/>'
            '</spine>'
            '</package>',
        )
        for name, text in (
            ("one", "The first chapter is safely saved."),
            ("two", "The second chapter completes the book."),
        ):
            archive.writestr(
                name + ".xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
                + text
                + "</p></body></html>",
            )
    return buffer.getvalue()


@app.local_entrypoint()
def main(fixture: str, report: str) -> None:
    import json

    config = json.loads(Path(fixture).read_text())
    source = fixture_epub()
    results = []
    try:
        for stop_after, job in enumerate(config["jobs"], start=1):
            arguments = (config["schema"], job["job_id"], job["token"], source)
            first = phase.remote(*arguments, stop_after)
            if first["ready_chapters"] != stop_after:
                raise RuntimeError("interrupted attempt did not commit expected chapters")
            resumed = phase.remote(*arguments, 0)
            if resumed["synthesized_chapters"] != 2 - stop_after:
                raise RuntimeError("replacement synthesized an already completed chapter")
            repeated = phase.remote(*arguments, 0)
            if repeated["synthesized_chapters"] or repeated["sha256"] != resumed["sha256"]:
                raise RuntimeError("fully checkpointed recovery changed the result")
            results.append(
                {"stop_after": stop_after, "first": first, "resumed": resumed, "repeated": repeated}
            )
    finally:
        cleaned = cleanup.remote(config["schema"], [j["job_id"] for j in config["jobs"]])
    evidence = {"environment": deployment, "checks": results, "cleanup": cleaned}
    Path(report).write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
