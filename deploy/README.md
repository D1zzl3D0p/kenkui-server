# Deployment and local development

The hosted API runs TypeScript on Neon Functions, with Neon Auth, Cloudflare
ingress/static web, R2 storage and Python Modal workers. Public production reuses
the former staging Neon branch and internal worker/storage names.

- [Production configuration](neon/prelaunch-production.md)
- [On-demand operations and recovery tradeoffs](on-demand-prelaunch.md)
- [Verified release packaging](neon/README.md)
- [Same-auth standalone fallback](neon/rollback.md)
- [Encrypted backup custody and maintenance](neon/maintenance.md)
- [Payments](payments.md)

The Render Blueprint generator and Python image release workflow are retired.
Historical instructions remain under [the archive](../docs/archive/render/README.md).

## Python local server

The local/self-hosted Python server remains supported. Provision voices explicitly;
rendering does not download models. Build Studio first with `npm run build`.

```sh
uv run python -c 'import kenkui as kk; kk.load_voice("eponine")'
uv run kenkui-server --web-build-path ../kenkui-studio/dist --max-jobs 2 --render-workers 1
```

Keep the selected sibling core revision explicit: hosted still uses `97cba6b`;
newer core features require capability reconciliation.

## Verification

Use `python scripts/verify-workspace.py --tier fast` during development.
Integration requires the disposable PostgreSQL target and restore tools listed
in the [README](../README.md). `--tier integration` checks Python, hosted
TypeScript, Studio, builds and browser journeys. The Neon workflow additionally
checks release archives, ingress and TLS artifacts. Actual provider login and
provisioned synthesis remain separate acceptance.
