# С верой / Vera — agent instructions

Before changing code, read:
1. `.ngi/project.yaml`
2. `.ngi/rules.md`
3. `.ngi/decisions.md`
4. `.ngi/workers.yaml`
5. `docs/VERA_PROJECT_STATUS.md`
6. only the relevant section of `VERA_ROADMAP.md`

Rules:
- One JOB = one concrete change or one proven defect.
- Start from current `origin/main` in an isolated branch/worktree.
- GitHub `main` is the only accepted code source of truth.
- No routine direct production editing.
- No broad audit/refactor/full-suite run unless the JOB explicitly proves it is necessary.
- Start from the Coordinator technical preflight: exact files, symbols, contracts and focused tests.
- Do not scan unrelated modules, historical backups, old BotFlow jobs or landing assets unless the JOB requires them.
- Preserve existing architecture and protected areas.
- Never commit secrets, `.env`, credentials, databases, user data or backup archives.
- Implementation workers do not merge `main`, deploy production or restart services.
- Final owner-facing status is only PASS or BLOCKED after independent verification and controlled deploy/health.
