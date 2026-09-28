# Vera / «С верой» — CURRENT PROJECT STATUS

## SOURCE OF TRUTH
- Canonical repository: `demo23rus/VERA-Bot`.
- During NGI migration, current production code is being reconciled into GitHub `main`.
- After migration, all new JOBs start from `origin/main`.

## PROCESS
- Standard target: discussion → owner «делай» → Coordinator narrow preflight → economical Worker Router → independent GitHub/DC verification → fast-forward promotion → controlled deploy → PASS/BLOCKED.
- BotFlow is retained only as legacy/secondary infrastructure.

## CLOSED / DO NOT REOPEN
- Existing payment/YooKassa flow unless a specific defect is proven.
- Existing channel/autoposting scheduling and anti-duplicate logic unless explicitly targeted.
- Existing watchdog/fail-safe behavior unless explicitly targeted.
- Existing owner cabinet and Google Sheets integration unless explicitly targeted.

## LOCKED CONTENT
- `.env_vera`, databases, credentials, user data and backup archives never enter Git.
- Real user messages/channel posts are not verification tools unless explicitly authorized.

## INFRASTRUCTURE STATUS
- Server/DC device: `gymgenius`.
- Services: `vera-bot.service`, `vera-max.service`; watchdog timer active.
- MAX health endpoint: `http://127.0.0.1:8080/health`.
- Public landing: `https://sveroy.ru/`.
- MAX is healthy.
- Known operational defect: Telegram long polling currently conflicts with an active webhook and logs `TelegramConflictError`.

## ACTIVE
- NGI Autonomous Development Standard v1 migration.
- Economical Worker Router with Coordinator narrow preflight.

## NEXT PRODUCT SLICE
- Resolve Telegram webhook/long-polling conflict as a separate operational JOB.
- Resume product work only through Router after migration verification.

## KNOWN TEST DEBT
- No broad product test suite is required by default; use focused callback/business checks.
- Telegram live callback smoke is blocked until webhook conflict is resolved.

## CURRENT ACCEPTED PRODUCT BASELINES
- Telegram + MAX assistants, owner cabinet, landing, analytics and current production features are accepted baseline unless a new reproducible defect is shown.
- Recent accepted features include «🕯️ Моя духовная неделя» and «⛪ Моё воскресенье».
