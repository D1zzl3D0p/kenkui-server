"""Deploy from the server checkout: uv run --extra hosted modal deploy deploy/modal_app.py."""

import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT.parent / "kenkui"
deployment = os.environ.get("KENKUI_DEPLOYMENT", "staging")
if deployment not in {"staging", "production"}:
    raise ValueError("KENKUI_DEPLOYMENT must be staging or production")
app_name = f"kenkui-{deployment}"
app = modal.App(app_name)
proxy_name = os.environ.get("KENKUI_MODAL_PROXY")
proxy = (
    modal.Proxy.from_name(proxy_name, environment_name=deployment) if proxy_name else None
)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("uv==0.12.10")
    .add_local_dir(
        str(CORE),
        "/app/kenkui",
        copy=True,
        ignore=[
            ".git",
            ".venv",
            ".worktrees",
            ".claude",
            "evals",
            "spikes",
            "tests",
            "docs",
            ".env*",
            ".dev.vars*",
            ".serena",
            ".superpowers",
        ],
    )
    .add_local_dir(
        str(ROOT),
        "/app/kenkui-server",
        copy=True,
        ignore=[
            ".git",
            ".venv",
            ".worktrees",
            ".claude",
            "tests",
            "task-8-report.md",
            ".env*",
            ".dev.vars*",
            ".serena",
            ".superpowers",
        ],
    )
    .workdir("/app/kenkui-server")
    .run_commands(
        "uv export --frozen --extra hosted --no-dev --no-hashes "
        "--output-file /tmp/requirements.txt",
        "uv pip install --system -r /tmp/requirements.txt",
    )
    .env(
        {
            "KENKUI_POCKET_MANIFEST": "/models/manifest.json",
            "KENKUI_MODAL_APP": app_name,
            "MODAL_ENVIRONMENT": deployment,
        }
    )
)
models = modal.Volume.from_name(
    f"{app_name}-models", environment_name=deployment, create_if_missing=True
)
secrets = [modal.Secret.from_name(f"{app_name}-worker", environment_name=deployment)]


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models},
    cpu=2,
    memory=8192,
    timeout=7200,
    max_containers=2,
    retries=0,
    proxy=proxy,
)
def render_job(dispatch_id: str, token: str) -> None:
    from kenkui_server.compute.hosted import execute_hosted
    from kenkui_server.hosted import object_store, required

    execute_hosted(required("DATABASE_URL"), object_store(), dispatch_id, token)


@app.function(
    image=image, secrets=secrets, volumes={"/models": models}, timeout=1800, max_containers=1
)
def provision() -> None:
    import kenkui as kk

    from kenkui_server.hosted import required

    for voice_id in required("KENKUI_VOICE_IDS").split(","):
        kk.load_voice(voice_id.strip())
    models.commit()


@app.function(
    image=image, secrets=secrets, schedule=modal.Period(minutes=1), timeout=120, proxy=proxy
)
def recover_jobs() -> None:
    from kenkui_server.compute.modal import ModalProcessRunner
    from kenkui_server.hosted import required
    from kenkui_server.jobs.dispatcher import HostedDispatcher
    from kenkui_server.storage.postgres import PostgresHostedRepository
    from kenkui_server.storage.postgres_database import PostgresDatabase

    database = PostgresDatabase(required("DATABASE_URL"))
    try:
        repositories = PostgresHostedRepository(database)
        HostedDispatcher(
            repositories, ModalProcessRunner(repositories, app_name=app_name)
        ).recover()
    finally:
        database.close()


@app.function(
    image=image, secrets=secrets, schedule=modal.Period(hours=1), timeout=300, proxy=proxy
)
def retain_objects() -> None:
    from kenkui_server.hosted import object_store, required
    from kenkui_server.storage.postgres_database import PostgresDatabase
    from kenkui_server.storage.retention import PostgresRetention

    database = PostgresDatabase(required("DATABASE_URL"))
    try:
        PostgresRetention(database, object_store()).run()
    finally:
        database.close()
