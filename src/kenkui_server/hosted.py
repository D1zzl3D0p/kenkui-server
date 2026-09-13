"""Environment-driven hosted composition. Migrations are an explicit release step."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import kenkui as kk
from fastapi import FastAPI

from kenkui_server.app import HostedServices, create_app
from kenkui_server.auth.browser import BrowserAuthBackend, BrowserSessionConfig
from kenkui_server.compute.modal import ModalProcessRunner
from kenkui_server.config import HostedConfig
from kenkui_server.storage.assets import R2AssetStore
from kenkui_server.storage.postgres import PostgresHostedRepository, PostgresIdentityRepository
from kenkui_server.storage.postgres_database import PostgresDatabase


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing configuration: {name}")
    return value


def object_store() -> R2AssetStore:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=required("R2_ENDPOINT"),
        aws_access_key_id=required("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=required("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
    )
    return R2AssetStore(client, bucket=required("R2_BUCKET"), key_salt=required("R2_KEY_SALT"))


def create_hosted_app() -> FastAPI:
    from workos import WorkOSClient

    session_config = BrowserSessionConfig(
        redirect_uri=required("WORKOS_REDIRECT_URI"),
        web_origin=required("KENKUI_WEB_ORIGIN").rstrip("/"),
        cookie_password=required("KENKUI_SESSION_SECRET"),
        invited_emails=frozenset(required("KENKUI_INVITED_EMAILS").split(",")),
    )
    voice_ids = set(required("KENKUI_VOICE_IDS").split(","))
    voices = tuple(voice for voice in kk.list_voices() if voice.id in voice_ids and voice.enabled)
    if {voice.id for voice in voices} != voice_ids:
        raise ValueError("configured voice is missing or disabled")
    database = PostgresDatabase(required("DATABASE_URL"))
    repositories = PostgresHostedRepository(database)
    auth = BrowserAuthBackend(
        WorkOSClient(api_key=required("WORKOS_API_KEY"), client_id=required("WORKOS_CLIENT_ID")),
        PostgresIdentityRepository(database),
        session_config,
    )
    allowance = int(os.environ.get("KENKUI_BETA_CREDITS", "1000"))
    if allowance < 1:
        raise ValueError("beta allowance must be positive")

    def account_for_identity(identity_id: UUID) -> str:
        with database.transaction():
            row = database.execute(
                (
                    "INSERT INTO credit_accounts (id, identity_id) VALUES (%s,%s) ON "
                    "CONFLICT(identity_id) DO UPDATE SET identity_id=EXCLUDED.identity_id "
                    "RETURNING id"
                ),
                (str(uuid4()), str(identity_id)),
            ).fetchone()
            if row is None:
                raise RuntimeError("account creation failed")
            account_id = str(row["id"])
            repositories.billing.grant(
                account_id, allowance, reference=f"beta-allowance:{identity_id}"
            )
            return account_id

    services = HostedServices(
        repositories,
        object_store(),
        voices,
        ModalProcessRunner(
            repositories,
            app_name=os.environ.get("KENKUI_MODAL_APP", "kenkui-beta"),
            max_jobs=int(os.environ.get("KENKUI_MAX_JOBS", "2")),
        ),
        auth,
        account_for_identity,
    )
    config = HostedConfig(
        database_url=required("DATABASE_URL"),
        r2_bucket=required("R2_BUCKET"),
        r2_endpoint=required("R2_ENDPOINT"),
        r2_access_key_id=required("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=required("R2_SECRET_ACCESS_KEY"),
        workos_api_key=required("WORKOS_API_KEY"),
        stripe_webhook_secret="",
    )
    app = create_app(
        hosted_config=config,
        hosted_services=services,
        allowed_origins=[session_config.web_origin],
        model_allowlist=tuple(
            filter(None, os.environ.get("KENKUI_MODEL_ALLOWLIST", "").split(","))
        ),
    )
    app.state.hosted_database = database
    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "serve", "recover"])
    command = parser.parse_args().command
    if command == "migrate":
        database = PostgresDatabase(required("DATABASE_URL"))
        try:
            database.migrate()
        finally:
            database.close()
    elif command == "recover":
        from kenkui_server.jobs.dispatcher import HostedDispatcher

        database = PostgresDatabase(required("DATABASE_URL"))
        try:
            repo = PostgresHostedRepository(database)
            HostedDispatcher(
                repo,
                ModalProcessRunner(
                    repo, app_name=os.environ.get("KENKUI_MODAL_APP", "kenkui-beta")
                ),
            ).recover()
        finally:
            database.close()
    else:
        import uvicorn

        uvicorn.run(create_hosted_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
