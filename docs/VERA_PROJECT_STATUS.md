# Vera / «С верой» — CURRENT PROJECT STATUS

## SOURCE OF TRUTH
- Canonical repository: `demo23rus/VERA-Bot`.
- GitHub `main` is canonical now; production runtime files are symlinked to `/root/vera`.
- All new JOBs start from `origin/main`.

## PROCESS
- Standard target: discussion → owner «делай» → Coordinator narrow preflight → economical Worker Router → independent GitHub/DC verification → fast-forward promotion → controlled deploy → PASS/BLOCKED.
- NGI Autonomous Development Standard v1 is the active production development process.
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
- Telegram webhook/long-polling conflict resolved on 2026-09-28; polling is active without a new `TelegramConflictError` after restart.

## ACTIVE
- Normal product development through the economical Worker Router with Coordinator narrow preflight.

## NEXT PRODUCT SLICE
- Continue normal product work through Router.

## KNOWN TEST DEBT
- No broad product test suite is required by default; use focused callback/business checks.
- Telegram live callback smoke is available now that the webhook conflict is resolved.

## CURRENT ACCEPTED PRODUCT BASELINES
- Telegram + MAX assistants, owner cabinet, landing, analytics and current production features are accepted baseline unless a new reproducible defect is shown.
- Recent accepted features include «🕯️ Моя духовная неделя» and «⛪ Моё воскресенье».
- «⛪ Моё воскресенье» Telegram/MAX contract is focused-test accepted and DONE/CLOSED; no manual live acceptance remains as NEXT.
