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
idle_mode = os.environ.get("KENKUI_DATABASE_IDLE") == "1"
# Prelaunch runs on demand; periodic recovery/retention is an explicit operating choice.
background_schedules = os.environ.get("KENKUI_BACKGROUND_SCHEDULES", "0")
if background_schedules not in {"0", "1"}:
    raise ValueError("KENKUI_BACKGROUND_SCHEDULES must be 0 or 1")
background_schedules_enabled = background_schedules == "1"
public_web_origin = os.environ.get("KENKUI_PUBLIC_WEB_ORIGIN", "")
public_api_origin = os.environ.get("KENKUI_PUBLIC_API_ORIGIN", "")
if (public_web_origin or public_api_origin) and (public_web_origin, public_api_origin) not in {
    ("https://app.kenkui.fm", "https://api.kenkui.fm"),
    ("https://app.staging.kenkui.fm", "https://api.staging.kenkui.fm"),
}:
    raise ValueError("public origins must be a matching canonical Kenkui HTTPS pair")
app = modal.App(app_name)
proxy_name = os.environ.get("KENKUI_MODAL_PROXY")
proxy = modal.Proxy.from_name(proxy_name, environment_name=deployment) if proxy_name else None


def code_image(base: modal.Image) -> modal.Image:
    return (
        base.apt_install("ffmpeg")
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
                "__pycache__",
                "*.pyc",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
                ".coverage*",
                "htmlcov",
                "dist",
                "build",
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
                "__pycache__",
                "*.pyc",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
                ".coverage*",
                "htmlcov",
                "dist",
                "build",
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
                "KENKUI_DEPLOYMENT": deployment,
                "KENKUI_POCKET_MANIFEST": "/models/runtime/manifest.json",
                "KENKUI_MODAL_APP": app_name,
                "MODAL_ENVIRONMENT": deployment,
                "KENKUI_DATABASE_IDLE": "1" if idle_mode else "0",
                # Container reimports must reconstruct the same conditional dependencies.
                "KENKUI_PUBLIC_WEB_ORIGIN": public_web_origin,
                "KENKUI_PUBLIC_API_ORIGIN": public_api_origin,
                "KENKUI_TEST_EMAIL_NOTIFICATIONS": os.environ.get(
                    "KENKUI_TEST_EMAIL_NOTIFICATIONS", "0"
                ),
                "KENKUI_BACKGROUND_SCHEDULES": background_schedules,
            }
        )
    )


image = code_image(modal.Image.debian_slim(python_version="3.12"))
# Backup tools stay out of the API/render image. The official PostgreSQL image
# includes pg_dump 17 and Debian's system CA bundle for verified Neon TLS.
maintenance_image = (
    code_image(
        modal.Image.from_registry("postgres:17-bookworm", add_python="3.12").apt_install("age")
    )
    if idle_mode
    else image
)
models = modal.Volume.from_name(
    f"{app_name}-models", environment_name=deployment, create_if_missing=True
)
secrets = [
    modal.Secret.from_name(f"{app_name}-worker", environment_name=deployment),
    # Completion mail is a separate secret so worker credentials are never
    # rewritten to add it. An empty SMTP password leaves the deployment silent.
    modal.Secret.from_name(f"{app_name}-mail", environment_name=deployment),
]
if idle_mode:
    # Later secrets override duplicate keys without rewriting worker credentials.
    secrets.append(modal.Secret.from_name(f"{app_name}-database-idle", environment_name=deployment))
if public_web_origin:
    # Public links may be promoted independently of the internal Modal environment.
    secrets.append(
        modal.Secret.from_dict(
            {
                "KENKUI_WEB_ORIGIN": public_web_origin,
                "KENKUI_API_ORIGIN": public_api_origin,
            }
        )
    )

# Full-database read authority and public encryption recipient exist only on daily maintenance.
maintenance_secrets = secrets + (
    [modal.Secret.from_name(f"{app_name}-database-backup", environment_name=deployment)]
    if idle_mode
    else []
)


def configure_model_manifest() -> None:
    """Resolve Modal's volume symlink before the library records asset paths."""
    manifest = Path(os.environ["KENKUI_POCKET_MANIFEST"]).resolve()
    manifest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["KENKUI_POCKET_MANIFEST"] = str(manifest)


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models},
    cpu=8,
    memory=12288,
    timeout=24 * 60 * 60,
    max_containers=2,
    retries=0,
    proxy=proxy,
)
def render_job(dispatch_id: str, token: str) -> None:
    from kenkui_server.compute.hosted import execute_hosted
    from kenkui_server.hosted import object_store, required

    configure_model_manifest()
    execute_hosted(
        required("DATABASE_URL"),
        object_store(),
        dispatch_id,
        token,
        render_workers=8,
    )


@app.function(
    image=image,
    secrets=[modal.Secret.from_name(f"{app_name}-bridge", environment_name=deployment)] + secrets,
    volumes={"/models": models},
    cpu=2,
    memory=4096,
    timeout=240,
    max_containers=2,
    proxy=proxy,
)
@modal.asgi_app(requires_proxy_auth=True)
def api_bridge():
    from kenkui_server.compute.api_bridge import bridge_from_environment

    configure_model_manifest()
    return bridge_from_environment()


@app.function(
    image=image, secrets=secrets, volumes={"/models": models}, timeout=1800, max_containers=1
)
def provision() -> None:
    import kenkui as kk

    from kenkui_server.voice_catalog import VCTK_VOICE_SET, select_hosted_voices

    configure_model_manifest()
    for voice in select_hosted_voices(VCTK_VOICE_SET, kk.list_voices()):
        kk.load_voice(voice.id)
    models.commit()


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Period(minutes=1) if background_schedules_enabled else None,
    scaledown_window=2,
    timeout=240,
    max_containers=1,
    proxy=proxy,
)
def recover_jobs() -> None:
    from kenkui_server.hosted import recover_jobs as recover

    recover()


@app.function(
    image=maintenance_image,
    secrets=maintenance_secrets,
    schedule=modal.Period(hours=24 if idle_mode else 1) if background_schedules_enabled else None,
    timeout=1800 if idle_mode else 300,
    max_containers=1,
    proxy=proxy,
)
def retain_objects() -> None:
    from kenkui_server.hosted import retain_objects as maintain

    maintain()
