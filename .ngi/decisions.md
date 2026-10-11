# Vera — accepted decisions

Only decisions future agents must not re-decide without new evidence belong here.

## D001 — GitHub main is source of truth
Accepted code lives in `demo23rus/VERA-Bot` `main`. Production files, backups, BotFlow jobs and old chats are evidence, not newer authority.

## D002 — No routine direct production editing
Implementation happens in isolated Router worktrees. Production is changed only by controlled deploy from accepted GitHub `main`.

## D003 — One JOB, one change
No broad audit/refactor inside a focused task.

## D004 — Existing runtime data stays outside Git
`/root/.env_vera`, `vera.db`, `vera_max.db`, Google credentials, user data, runtime state and backup archives are never committed.

## D005 — BotFlow remains legacy/secondary
Existing BotFlow infrastructure is preserved for history/secondary use; it is not a second source of truth and is not the default implementation path after NGI migration.

## D006 — Protected production behavior
Payment/YooKassa, donation tables, user data, channel/autoposting, watchdog, Google Sheets and production messaging are protected unless a JOB explicitly targets them.

## D007 — Economical worker order
Kimi Code is primary. Codex is automatic fallback for quota/execution unavailability. Claude Code is third fallback subject to worker policy. A second coding worker is not launched merely to review a successful JOB.

## D008 — Worker PASS is not owner PASS
Final PASS requires independent diff/scope review, focused checks, fast-forward promotion, controlled deploy when relevant, and health/smoke.

## D009 — Narrow Coordinator preflight is mandatory by default
Implementation workers receive exact files/symbols/contracts/focused tests up front and may not perform broad repository audits without a demonstrated missing dependency.

## D010 — Current Telegram webhook conflict is operational debt, not a reason to rewrite unrelated code
As of 2026-09-28, `vera-bot.service` logs `TelegramConflictError` because webhook and long polling conflict. Resolve as a separate operational JOB; do not mix it into unrelated feature work.


## GPU routing owner decision 2026-10-11
Primary implementation: Hermes/Qwen3.8 through the private GPU bridge. Existing Router supports gpu_local; automatic cloud fallback is disabled. Coordinator reviews exact diff and focused tests. Earlier Kimi/Codex defaults are superseded; product protections and deploy contracts remain. Rollback: restore .ngi/workers.before-gpu.yaml to workers.yaml as a reviewed Git change.


## 2026-10-11 — Shared GPU queue timeout
Owner authorized a shared FIFO inference queue for Hermes and chat workers. gpu_local Router timeout is 5000 seconds to cover bounded queue waits, implementation (3600 s) and independent review (1200 s). Other workers, fallback and production policy are unchanged. Controller QUEUE.md documents limits and rollback; previous gpu_local timeout was 600 s.
