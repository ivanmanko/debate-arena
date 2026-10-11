# ADR-004: Раскладка interface → application → domain ← infrastructure; бот остаётся тонким вторым интерфейсом

- **Status:** Accepted
- **Date:** 2026-10-11

## Context

Интерфейсов три: HTTP/Web, Telegram-бот, консольный демо-прогон. Первая версия была
плоской (`bot/ debate/ llm/ storage/`), и `runner.py` знал о Telegram. При добавлении
web-слоя логика раундов не должна зависеть ни от aiogram, ни от FastAPI.

## Decision

```
src/debate_arena/
├── config.py          pydantic-settings; значения из SPEC §7
├── main.py            FastAPI app, lifespan (БД, провайдеры), раздача фронтенда
├── domain/            schemas (Verdict, AnalystSpec, события), analysts, prompts,
│                      transcript, ports (ChatProvider, AnalysisStream), exceptions
├── application/       flow (раунды + судья), service (запуск, реестр, события, персист)
├── infrastructure/    llm/{anthropic,deepseek,mock,factory}; db/{models,session,repo}
└── interface/         api/{routes,schemas}; web/static; bot/{main,handlers,presenter}; cli
```

Правило: `interface → application → domain ← infrastructure`; `domain` и `application`
не импортируют ни `anthropic`, ни `fastapi`, ни `aiogram`, ни SQLAlchemy — только через
`ports`. Бот использует тот же `AnalysisService`, что и web, и только рендерит события.
Имя пакета `debate_arena` сохраняется (переименование — шум без пользы).

## Alternatives considered

1. **Сервисная раскладка `api/services/infrastructure/core`.** rejected: три интерфейса —
   случай B из `references/architecture.md`.
2. **Удалить бота.** Меньше кода; rejected: пользователь не просил, бот — 150 строк поверх
   общего сервиса; записать было бы урезанием scope молча.

## Consequences

- Старые модули `debate/`, `bot/`, `llm/`, `storage/`, `runner.py` удаляются; тесты
  переписываются под новые слои.

## Revisit when

- Появится третий одновременный интерфейс с собственной сессией (например MCP) — проверить,
  что `application` не протекает.
