# Worker dependencies and WorkOS

November 4, 2026 continuation of the ecosystem cleanup. Render resources are
retained at the owner's request. No provider deployments or credential changes
are part of this change. Read-only GitHub inventory found the backup workflow
absent and the availability workflow still active; neither was changed here.

## Dependency boundary

`worker` owns boto3, Modal and psycopg (binary/pool). `hosted` composes that extra
and adds WorkOS, preserving existing Python hosted and account-recovery support.
The local server's base requirements are unchanged. The TypeScript hosted API
continues to own Neon Auth.

The Modal image builder now exports `--extra worker` for both ordinary and
maintenance images. Operator examples for deployment, canaries and benchmarks
select the same extra. The full compatibility test commands intentionally keep
`hosted`, because they exercise the supported legacy composition too.

The newer published staging-isolation commit `b77bc47` was merged before this
change, preserving its test app, routing and secret-map isolation. No live app was
updated. Frozen hosted core remains `97cba6b` (library version 10.1.1).

## Verification

The new image-builder regression executes the actual frozen requirements export
chosen by each image. Both ordinary/idle cases failed with WorkOS present before
the change, then passed with all S3/Modal/PostgreSQL requirements retained. The
hosted export still includes WorkOS. Registration/schedule/isolation checks pass.

A fresh, isolated worker-only virtual environment installs successfully. It imports
the render, bridge, recovery/maintenance entry points with WorkOS, PyJWT,
cryptography, cffi and pycparser absent. Those five packages are excluded from
the worker export; they remain available through `hosted`. No locked package
versions or sources were upgraded. Export/install are also checked with uv0.12.10,
the version pinned in Modal images.

This proves local dependency selection and imports, not a new Linux Modal image
or live synthesis deployment. The existing encrypted-backup tools and separate
maintenance image remain required.

Fresh full integration result: 444 Python tests passed, one provisioned-voice
test skipped; hosted TypeScript 251 passed with 12 optional paired-locale skips;
Studio 173 passed and five browser journeys passed. Mypy, Ruff, OpenAPI and
generated API checks, hosted build and both Studio builds pass. The modified
Python files pass formatting checks. Two existing canary/benchmark format issues
were normalized while updating their install examples.
