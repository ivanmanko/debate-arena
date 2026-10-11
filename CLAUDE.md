# CLAUDE.md — правила агента в этом репозитории

Rational Check: веб-инструмент (и Telegram-бот), где три рациональных LLM-аналитика
разбирают присланный текст по Манифесту здравого смысла, а судья выносит структурированный
вердикт.

## Project Overview

Python 3.12, FastAPI + vanilla JS фронтенд, SQLAlchemy (SQLite локально / Postgres в
Docker), aiogram для бота. LLM — Anthropic (основной) и DeepSeek через один `Protocol`;
без ключей работает mock-режим. Личный инструмент, один процесс, деплой через
`docker compose`.

## Source of truth

- **`SPEC.md` определяет поведение.** Код реализует спеку, тесты её проверяют. Изменил
  поведение — обнови спеку в том же коммите. Константа, влияющая на наблюдаемое поведение
  и не объявленная в SPEC §7, — баг.
- **Pydantic-схемы — единственный источник контракта:** `domain/schemas.py` (`Verdict`,
  `AnalystSpec`) и `interface/api/schemas.py` (DTO). Не выдумывать, не переименовывать,
  не дублировать поля.
- **Архитектурные решения — в `docs/adr/`.** От принятого ADR не отступать молча: назвать
  конфликт, предложить ревизию.
- Манифест здравого смысла (7 столпов) — в `domain/prompts.py`; первоисточник — скилл
  `common-sense`.

## Commands

### Development (local)
```bash
uv sync
uv run rational-check                 # web: http://127.0.0.1:8000 (mock без ключей)
uv run rational-check-bot             # Telegram-бот (нужен BOT_TOKEN)
uv run python -m debate_arena.interface.cli "текст для разбора"
uv run pytest                         # unit + контракт API, без сети/LLM
uv run ruff check . && uv run ruff format --check .
```

### Docker / deploy
```bash
docker compose up -d --build          # web + postgres; bot — профиль `bot`
```

## Architecture

### Layer structure
```
src/debate_arena/
├── config.py          pydantic-settings (значения SPEC §7)
├── main.py            FastAPI app, lifespan, раздача фронтенда
├── domain/            schemas, analysts, prompts, transcript, ports, exceptions
├── application/       flow (раунды + судья), service (запуск, события, персист)
├── infrastructure/    llm/ (anthropic, deepseek, mock, factory); db/ (models, session, repo)
└── interface/         api/, web/static/, bot/, cli.py
```
Правило зависимостей: `interface → application → domain ← infrastructure`. `domain` и
`application` не импортируют anthropic/fastapi/aiogram/sqlalchemy — только `domain/ports`.

### Key design decisions
- Провайдеры напрямую через SDK за `ChatProvider`, строка модели `provider/model` — ADR-001.
- Три аналитика с общим Манифестом и разными линзами, слепой первый раунд, судья со
  structured output — ADR-002.
- FastAPI + SSE + одностраничный фронтенд без сборки, события в памяти, факты в БД — ADR-003.
- Раскладка слоёв, бот как тонкий второй интерфейс — ADR-004.
- Fail-fast: без ключа выбранного провайдера и без `MOCK_LLM` приложение не стартует с
  реальной моделью — уходит в mock и явно сообщает об этом в `/api/config`.

### Required environment variables (.env)
| Переменная | Назначение | Обязательна |
|---|---|---|
| `ANTHROPIC_API_KEY` | ключ Anthropic (модели `anthropic/*`) | да, если выбран anthropic |
| `DEEPSEEK_API_KEY` | ключ DeepSeek (модели `deepseek/*`) | да, если выбран deepseek |
| `MODEL_ANALYST`, `MODEL_JUDGE` | строки `provider/model` | нет (дефолты в SPEC §7) |
| `BOT_TOKEN` | Telegram-бот | только для бота |
| `DATABASE_URL` | SQLAlchemy async URL | нет (SQLite) |
| `MOCK_LLM` | принудительный mock | нет |

Все переменные документированы в `.env.example`.

## Engineering discipline

1. **Тест раньше реализации** для детерминированной логики (парсеры, фильтры, пороги, ветки
   контракта). Отдельный коммит `test(scope): … (failing)`, следом `feat(scope): …`.
2. **Красное не коммитится.** `uv run pytest && uv run ruff check .` перед каждым коммитом.
3. **Внешние системы в unit-тестах не участвуют.** Сеть, LLM, БД — только через фейки из
   `tests/fakes/`. Реальные — в `evals/` или тестах с маркером `integration`.
4. **`python-architect` после каждой значимой фичи**, правки отдельным коммитом
   `refactor(scope): review fixes`.
5. **`AI_NOTES.md` ведётся с первого дня:** что принято, что отвергнуто и почему, где агент
   остановился и спросил.
6. **Урезанный объём фиксируется** в README «Cut scope», недостигнутые цели — в SPEC как
   недостигнутые. Числа только измеренные.

## Git

- Базовая ветка: `main`. Задачи в `feat/<slug>` и `fix/<slug>` от свежей базы.
  Мердж: прямой fast-forward (соло-проект).
- План из 2–6 логических коммитов показывается, разрешения не ждёт. Коммиты — автоматически;
  единственный гейт — отчёт перед мерджем/деплоем.
- Формат: `<type>(<scope>): <описание>`; types `feat fix refactor test docs chore perf ci eval data`.
  Язык коммитов: английский.
- **Без атрибуции AI** — ни `Co-Authored-By`, ни «Generated with» в коммитах и PR.
- `CLAUDE.md` коммитится.

## Scope

Не добавлять фичи сверх SPEC (соблазны: auth, потоковые токены, веб-поиск, редактор
аналитиков). Урезанное записывается в README «Cut scope», а не реализуется молча.
Секреты только в окружении; `.env` в gitignore.
