<!-- NGI_QUIET_DEVELOPMENT_2026_10_22 -->
## NGI Quiet Development — обязательный режим для новых задач (22.10.2026)

**Приоритет над устаревшими инструкциями по исполнителям и моделям ниже.** [Единый стандарт NGI](https://github.com/demo23rus/ngi-ai-studio-controller/blob/main/docs/NGI_QUIET_DEVELOPMENT.md). Особые проектные требования безопасности, приватности, релиза и прямые решения владельца остаются обязательными.

- **Один чат — один проект; одна задача — одно изменение.** Не смешивать репозитории, не переименовывать чаты. Не запускать дополнительный аудит, перенастройку ИИ, новые интеграции или обходные работы без запроса.
- **Тихая работа:** не засорять чат рассказами о каждой команде, промежуточными логами и повторяющимися статусами. При длительной работе — только короткое существенное обновление или явный блокер; итог — «PASS/BLOCKED: изменение, проверки, commit/PR, что осталось».
- **GitHub — источник истины.** Читать только актуальные инструкции/статус и релевантный код. Изменять код в изолированной ветке/worktree; выполнять необходимые **целевые** тесты. Для авторизации, платежей, данных и безопасности сохранять усиленные проверки и независимый review, если требует проект.
- **Без самовольного production/deploy**, действий с реальными данными, оплат, рассылок, рекламы, переключения OAuth, опасных Git-команд и платных API/fallback. Просить владельца только о необходимых авторизациях, оплатах и важных необратимых решениях.
- **Не гонять модели часами.** Использовать существующий выбранный для проекта исполнитель. Qwen3 (простое) / GLM-4.7-Flash (сложное) — текущие варианты NGI Local Coder, но явный выбор владельца для отдельного проекта сохраняется. Qwen2.5-7B, Hermes, gpu_local и исторические Router-инструкции не активировать. При повторном провале — BLOCKED с причиной, не бесконечные попытки.
- **Никаких фиктивных PASS или обещаний фоновой работы.** Подробные отчёты держать в файлах/PR, обновлять существующий handoff только по необходимости.

<!-- /NGI_QUIET_DEVELOPMENT_2026_10_22 -->

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
