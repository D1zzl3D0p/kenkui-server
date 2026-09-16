"""Generate an environment-specific Render Blueprint (JSON is valid YAML)."""

import argparse
import ipaddress
import json
import re


def blueprint(environment, image, worker_cidrs=()):
    if environment not in {"staging", "production"}:
        raise ValueError("invalid deployment environment")
    if not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("image must be pinned to a registry sha256 digest")
    cidrs = [str(ipaddress.ip_network(value, strict=True)) for value in worker_cidrs]
    # Ordinary Modal workers use dynamic outbound IPs; TLS and database
    # credentials authenticate them. Fixed-IP restriction is an optional upgrade.
    if not cidrs:
        cidrs = ["0.0.0.0/0"]
    suffix = "" if environment == "production" else ".staging"
    api = f"api{suffix}.kenkui.fm"
    web = f"https://app{suffix}.kenkui.fm"
    name = f"kenkui-{environment}"
    variables = {
        "KENKUI_DEPLOYMENT": environment,
        "MODAL_ENVIRONMENT": environment,
        "KENKUI_MODAL_APP": name,
        "WORKOS_REDIRECT_URI": f"https://{api}/v1/auth/callback",
        "KENKUI_WEB_ORIGIN": web,
        "KENKUI_VOICE_SET": "vctk",
        "KENKUI_MAX_JOBS": "2",
        "KENKUI_MAX_SPEECH_CHARACTERS": "10000000",
        "KENKUI_BETA_CREDITS": "1000",
        "KENKUI_MODEL_ALLOWLIST": "openrouter/deepseek/deepseek-v4-flash",
    }
    env = [{"key": key, "value": value} for key, value in variables.items()]
    env += [
        {
            "key": "DATABASE_URL",
            "fromDatabase": {"name": name + "-db", "property": "connectionString"},
        }
    ]
    env += [{"key": "KENKUI_SESSION_SECRET", "sync": False}]
    env += [
        {"key": key, "sync": False}
        for key in (
            "R2_BUCKET",
            "R2_ENDPOINT",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_KEY_SALT",
            "WORKOS_API_KEY",
            "WORKOS_CLIENT_ID",
            "KENKUI_INVITED_EMAILS",
            "MODAL_TOKEN_ID",
            "MODAL_TOKEN_SECRET",
        )
    ]
    return {
        "services": [
            {
                "type": "web",
                "name": name + "-api",
                "runtime": "image",
                "image": {"url": image},
                "plan": "1c-2g",
                "region": "virginia",
                "numInstances": 1,
                "domains": [api],
                "healthCheckPath": "/v1/health",
                "preDeployCommand": "python -m kenkui_server.hosted migrate",
                "dockerCommand": "python -m kenkui_server.hosted serve",
                "envVars": env,
            }
        ],
        "databases": [
            {
                "name": name + "-db",
                "region": "virginia",
                "plan": "1c-2g",
                "postgresMajorVersion": "17",
                "diskSizeGB": 10,
                "databaseName": "kenkui",
                "user": "kenkui",
                "ipAllowList": [
                    {"source": cidr, "description": "Modal outbound"} for cidr in cidrs
                ],
            }
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=["staging", "production"])
    parser.add_argument("--image", required=True)
    parser.add_argument("--worker-cidr", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(blueprint(args.environment, args.image, args.worker_cidr), indent=2))
