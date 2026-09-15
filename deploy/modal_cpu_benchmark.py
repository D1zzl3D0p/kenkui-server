"""Benchmark Kenkui synthesis across controlled Modal CPU allocations.

The ``production`` preset reproduces the historical 2-CPU/1-worker baseline,
not the current deployment. Use ``8x8`` for the current CPU/worker policy;
this benchmark still requests 8 GiB rather than the deployed 12 GiB.

Run from the server checkout:

    KENKUI_DEPLOYMENT=staging uv run --extra hosted modal run \
        -m deploy.modal_cpu_benchmark --repeats 1

Use configurations such as ``24x16,24`` to compare 16 and 24 Kenkui workers
on the 24-CPU function.

The benchmark functions use equal CPU request and limit values. This prevents
opportunistic bursting from making both timing and cost comparisons ambiguous.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import modal

from deploy.modal_app import configure_model_manifest, deployment, image, models, secrets

MEMORY_MIB = 8192
CPU_HOUR_COST_USD = 0.04730
MEMORY_GIB_HOUR_COST_USD = 0.00800
CPU_OPTIONS = (1, 2, 4, 8, 16, 24)

app = modal.App(f"kenkui-{deployment}-cpu-benchmark")


def _run_benchmark(source_bytes: bytes, workers: int) -> dict[str, Any]:
    import os
    import subprocess

    import kenkui as kk
    from kenkui._execution import process_pool

    from kenkui_server.hosted import required

    configure_model_manifest()
    # Production deliberately caps this at 16. The benchmark must be able to
    # measure Modal's 24-core option with 24 independent chapters, without
    # changing the production library policy merely to run an experiment.
    process_pool.MAX_RENDER_WORKERS = max(process_pool.MAX_RENDER_WORKERS, workers)
    voice_id = required("KENKUI_VOICE_IDS").split(",")[0].strip()
    voice = next(voice for voice in kk.list_voices() if voice.id == voice_id)
    if voice.state != "loaded":
        raise RuntimeError("Provision the staging worker voice before running the benchmark")

    with TemporaryDirectory(prefix="kenkui-modal-cpu-benchmark-") as directory:
        source = Path(directory) / "benchmark.epub"
        output = Path(directory) / "benchmark.m4b"
        source.write_bytes(source_bytes)
        book = kk.book(source)
        inspection = book.inspect()
        resolved_workers = process_pool.resolve_workers(workers, len(inspection.chapters))

        started = time.monotonic()
        (
            book.metadata(title="Kenkui CPU benchmark", author="Kenkui", cover=None)
            .assign_voice(voice_id)
            .tts()
            .write(output, workers=workers)
        )
        render_seconds = time.monotonic() - started

        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        audio_seconds = float(json.loads(probe.stdout)["format"]["duration"])
        speech_characters = sum(chapter.speech_characters for chapter in inspection.chapters)
        return {
            "audio_seconds": round(audio_seconds, 3),
            "available_logical_cpus": len(os.sched_getaffinity(0)),
            "chapters": len(inspection.chapters),
            "characters_per_second": round(speech_characters / render_seconds, 2),
            "render_seconds": round(render_seconds, 3),
            "real_time_factor": round(render_seconds / audio_seconds, 4),
            "speech_characters": speech_characters,
            "voice": voice_id,
            "workers_requested": workers,
            "workers_resolved": resolved_workers,
        }


_FUNCTION_OPTIONS = {
    "image": image,
    "secrets": secrets,
    "volumes": {"/models": models},
    "memory": MEMORY_MIB,
    "timeout": 24 * 60 * 60,
    "max_containers": 1,
}


@app.function(cpu=(1, 1), **_FUNCTION_OPTIONS)
def benchmark_cpu_1(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


@app.function(cpu=(2, 2), **_FUNCTION_OPTIONS)
def benchmark_cpu_2(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


@app.function(cpu=(4, 4), **_FUNCTION_OPTIONS)
def benchmark_cpu_4(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


@app.function(cpu=(8, 8), **_FUNCTION_OPTIONS)
def benchmark_cpu_8(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


@app.function(cpu=(16, 16), **_FUNCTION_OPTIONS)
def benchmark_cpu_16(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


@app.function(cpu=(24, 24), **_FUNCTION_OPTIONS)
def benchmark_cpu_24(source_bytes: bytes, workers: int) -> dict[str, Any]:
    return _run_benchmark(source_bytes, workers)


FUNCTIONS = {
    1: benchmark_cpu_1,
    2: benchmark_cpu_2,
    4: benchmark_cpu_4,
    8: benchmark_cpu_8,
    16: benchmark_cpu_16,
    24: benchmark_cpu_24,
}


def _fixture(chapters: int, characters_per_chapter: int) -> bytes:
    sentence = (
        "The morning train crossed the valley while rain tapped softly against "
        "the windows, and every passenger watched the mountains emerge from the mist. "
    )
    chapter_documents: list[tuple[str, bytes]] = []
    for number in range(1, chapters + 1):
        heading = f"Benchmark chapter {number}"
        body = (sentence * ((characters_per_chapter // len(sentence)) + 1))[
            :characters_per_chapter
        ]
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            f"<title>{heading}</title></head><body><h1>{heading}</h1><p>{body}</p>"
            "</body></html>"
        ).encode()
        chapter_documents.append((f"OPS/chapter-{number}.xhtml", document))

    manifest = "".join(
        f'<item id="chapter-{number}" href="chapter-{number}.xhtml" '
        'media-type="application/xhtml+xml"/>'
        for number in range(1, chapters + 1)
    )
    spine = "".join(
        f'<itemref idref="chapter-{number}"/>' for number in range(1, chapters + 1)
    )
    package = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="book-id">kenkui-cpu-benchmark</dc:identifier>'
        '<dc:title>Kenkui CPU benchmark</dc:title><dc:language>en</dc:language>'
        f"</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>"
    ).encode()
    container = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b'<rootfiles><rootfile full-path="OPS/book.opf" '
        b'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/book.opf", package)
        for name, document in chapter_documents:
            archive.writestr(name, document)
    return payload.getvalue()


def _requested_resource_cost(cpu: int, seconds: float) -> float:
    hourly_rate = cpu * CPU_HOUR_COST_USD + (MEMORY_MIB / 1024) * MEMORY_GIB_HOUR_COST_USD
    return hourly_rate * seconds / 3600


@app.local_entrypoint()
def main(
    repeats: int = 1,
    chapters: int = 24,
    characters_per_chapter: int = 100,
    configurations: str = "production,1,2,4,8,16,24x16,24",
    source_path: str = "",
) -> None:
    if repeats < 1 or chapters < 1 or characters_per_chapter < 1:
        raise ValueError("repeats, chapters, and characters_per_chapter must be positive")

    if source_path:
        source = Path(source_path).expanduser().resolve()
        source_bytes = source.read_bytes()
        source_label = source.name
    else:
        source_bytes = _fixture(chapters, characters_per_chapter)
        source_label = "synthetic"
    results: list[dict[str, Any]] = []
    requested = [value.strip() for value in configurations.split(",") if value.strip()]
    selected: list[tuple[int, str, int]] = []
    for value in requested:
        if value == "production":
            selected.append((2, "production", 1))
        else:
            cpu_text, separator, workers_text = value.partition("x")
            try:
                cpu = int(cpu_text)
                workers = int(workers_text) if separator else min(cpu, chapters)
            except ValueError as error:
                raise ValueError(f"invalid configuration: {value}") from error
            if cpu not in CPU_OPTIONS or not 1 <= workers <= chapters:
                raise ValueError(f"invalid configuration: {value}")
            selected.append((cpu, "parallel", workers))

    for cpu, policy, workers in selected:
        # Production is measured once at its actual 2-core/1-worker setting.
        # The scaling sweep can give every core an independent chapter when
        # requested, matching Kenkui's unit of parallelism.
        for repeat in range(1, repeats + 1):
            call_started = time.monotonic()
            result = FUNCTIONS[cpu].remote(source_bytes, workers)
            client_seconds = time.monotonic() - call_started
            cost = _requested_resource_cost(cpu, client_seconds)
            results.append(
                {
                    **result,
                    "client_seconds": round(client_seconds, 3),
                    "cpu_physical_cores": cpu,
                    "request_based_cost_usd": round(cost, 6),
                    "request_based_cost_per_audio_hour_usd": round(
                        cost / (result["audio_seconds"] / 3600), 4
                    ),
                    "policy": policy,
                    "repeat": repeat,
                }
            )
            print(json.dumps(results[-1], sort_keys=True), flush=True)

    summary = {
        "source": {
            "bytes": len(source_bytes),
            "label": source_label,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "rates": {
            "cpu_hour_cost_usd": CPU_HOUR_COST_USD,
            "memory_gib_hour_cost_usd": MEMORY_GIB_HOUR_COST_USD,
            "memory_mib": MEMORY_MIB,
        },
        "results": results,
    }
    print(json.dumps(summary, indent=2))
