## Актуальный стандарт исполнителя NGI — 2026-10-19

**Приоритет для новых задач над историческими настройками ниже.** Только по явной просьбе владельца на конкретную техническую правку: координатор ChatGPT → Desktop Commander (`iMac-Dimka.local`) → Aider + локальная Qwen2.5-Coder 7B → точечные тесты, Playwright для подходящих UI-сценариев → проверка координатором → GitHub по правилам этого репозитория. **Без** Hermes, Kimi, старых gpu_local/NGI Router, Codex/Claude и платных fallback. Записи в `.ngi/workers.yaml` о старом исполнителе не активируют его. Работа только в изолированной ветке/worktree, одна задача — одно изменение; никаких скрытых деплоев, полных аудитов или лишних тестов. Если DC/iMac/GitHub недоступны — BLOCKED, без замены исполнителя. ChatGPT-координатор остаётся облачным. Каноническое описание: [NGI Local Coding Standard](https://github.com/demo23rus/ngi-ai-studio-controller/blob/main/docs/LOCAL_CODING_STANDARD.md).

## Решение владельца 2026-10-18: старый AI-контур отключён

Это решение отменяет прежние требования использовать Hermes, gpu_local, Qwen/Vast, NGI GPU bridge, durable_tasks и обязательный локальный reviewer. Не запускать старую очередь и GPU-роутеры, не создавать новую аренду и не включать платный fallback. Новый исполнитель выбирается владельцем отдельно. Код, Git-история, незавершённые изменения, правила тестирования и защиты production сохраняются.

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


## GPU routing owner decision 2026-10-11
Primary implementation: Hermes/Qwen3.8 through the private GPU bridge. Existing Router supports gpu_local; automatic cloud fallback is disabled. Coordinator reviews exact diff and focused tests. Earlier Kimi/Codex defaults are superseded; product protections and deploy contracts remain. Rollback: restore .ngi/workers.before-gpu.yaml to workers.yaml as a reviewed Git change.


## Owner decision 2026-10-11: Claude Code removed
Claude Code must not be used for implementation, review or fallback. Its executable is disabled on the runners. Earlier manual-provider permissions do not override this decision. Keep GPU-only routing and existing project protections.
