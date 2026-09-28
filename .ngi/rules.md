# Vera — permanent development rules

## Work unit
- One JOB = one concrete change, one feature slice, one defect, or one infrastructure change.
- JOB text contains only ID, objective, scope, acceptance criteria and task-specific constraints.
- Permanent rules live here, not in repeated worker prompts.

## Source and isolation
- GitHub `main` is source of truth.
- Every implementation starts from current `origin/main`.
- Work happens only in an isolated JOB worktree.
- Production/runtime paths are not ordinary editing workspaces.
- Legacy BotFlow may remain as a secondary adapter, but it must not become a second source of truth.

## Narrow technical preflight
- Coordinator prepares a technical preflight before implementation whenever the integration surface can be identified.
- Preflight names exact relevant files, symbols/functions, contracts, allowed adjacent dependencies and focused tests.
- Worker starts from that preflight and must not do broad repository discovery by default.
- Repo-wide recursive grep, whole-roadmap reading, historical backups, old job folders and unrelated landing/backend inspection are forbidden unless one concrete missing dependency blocks the task.
- If scope must expand, worker stops or documents the exact adjacent dependency required.

## Change discipline
- Reuse existing implementation before introducing new structures.
- No broad audit, broad refactor, opportunistic cleanup, dependency upgrade or unrelated change.
- Do not reopen accepted channel/autoposting, payment/YooKassa, watchdog, Google Sheets, user-data or owner-cabinet behavior unless the JOB explicitly targets it.
- Do not send real user messages or publish channel posts during verification unless the JOB explicitly authorizes a controlled live action.

## Tests
- Focused checks only by default.
- Python syntax/import checks for touched Python entrypoints.
- Nearest callback/business contract and regression checks for touched behavior.
- Landing checks only for landing JOBs.
- Full repository suite is not a default requirement.
- Pre-existing unrelated failures must be demonstrated on base before exclusion.

## Git / promotion / deploy
- Worker changes only the JOB worktree and does not commit/push/deploy.
- Router packages only scoped paths into the JOB branch.
- Direct push to `main` outside controlled promotion is forbidden.
- Promotion is fast-forward only after independent verification.
- Deploy restarts only the affected bot service.
- Watchdog timer/service is not manually restarted in ordinary JOBs.
- Health, heartbeat and fresh journal checks are required after runtime deploy.

## Owner trigger and result
- Owner message «делай» authorizes the normal autonomous flow for an already scoped ordinary JOB.
- Stop safely if scope expands, protected paths are implicated, an external decision is missing or verification fails.
- Owner-facing result is exactly PASS or BLOCKED.

## Economical routing
- Primary worker: Kimi Code.
- Automatic fallback: Codex, then Claude Code when eligible.
- Do not spend a second coding worker after WORKER_PASS; ChatGPT + GitHub/DC perform independent acceptance first.
- One same-worker repair is allowed only after a concrete verified defect.
- Quota/credit/execution failures may trigger fallback; product blockers do not.
- Before fallback, preserve diagnostics and reset the isolated worktree to the recorded clean base.
- Router never purchases credits or paid overage.
