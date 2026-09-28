# NGI Worker Router verification

This file is intentionally used for safe docs-only migration proof JOBs.

## Primary worker proof
Kimi Code primary proof completed by Router.

## Automatic fallback proof
Codex automatic fallback proof completed by Router.

## Migration PASS evidence
- Primary real Router proof: `JOB_20260928_182431`, Kimi Code, `WORKER_PASS`; branch packaged, pushed and promoted; controlled deploy/health PASS.
- Automatic fallback real Router proof: `JOB_20260928_183409`, Kimi intentionally unavailable; Codex `WORKER_PASS`; branch packaged, pushed and promoted; controlled deploy/health PASS.
- Router tests: 10 passed.
- Worker policy after proof: Kimi primary available, Codex automatic fallback available, Claude date-gated by `workers.yaml`.
- Telegram active webhook was removed without dropping pending updates; `vera-bot.service` restarted; `getWebhookInfo` URL is empty; polling starts without a new `TelegramConflictError` after restart.
- `vera-bot.service` active, `vera-max.service` active, MAX health OK, heartbeats fresh, `sveroy.ru` HTTP 200.
