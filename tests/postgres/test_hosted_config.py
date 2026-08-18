from kenkui_server.config import HostedConfig


def test_hosted_configuration_masks_provider_secrets_in_json() -> None:
    config = HostedConfig(
        database_url="postgresql://db",
        r2_bucket="private",
        r2_endpoint="https://r2.example",
        r2_access_key_id="key",
        r2_secret_access_key="r2-secret",
        workos_api_key="workos-secret",
        stripe_webhook_secret="stripe-secret",
    )

    serialized = config.model_dump_json()

    assert "r2-secret" not in serialized
    assert "workos-secret" not in serialized
    assert "stripe-secret" not in serialized
