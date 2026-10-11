# SPEC — Rational Check (Проверка здравого смысла)

> Behavior specification, written before implementation. If observed behavior deviates
> from this spec, either the code or this document has a bug. There is no hidden
> hardcoded behavior.

## 1. Purpose and scope

Пользователь присылает текст (пост, статью, аргумент, тезис). Три **рациональных
аналитика** — LLM-агенты, не привязанные ни к какой философской школе, апологеты
рационального мышления по Манифесту здравого смысла — независимо разбирают текст, затем
перекрёстно проверяют друг друга: критикуют слабые места и **явно принимают** рациональные
доводы коллег. **Судья** синтезирует структурированный вердикт: что самое рациональное,
какие утверждения доказаны / вероятны / не подлежат проверке, какие софизмы найдены, что
опровергло бы вывод и что из этого следует на практике.

In scope:
- Web UI (одна страница): ввод текста, живой ход разбора, карточка вердикта, история.
- HTTP API + SSE для того же.
- Провайдеры LLM: Anthropic (по умолчанию) и DeepSeek; mock-режим без ключей.
- Telegram-бот как второй тонкий интерфейс (`/check <текст>`).

Out of scope (cut consciously):
- Аутентификация, мультипользовательность, квоты. Инструмент личный / для одного чата.
- Потоковая выдача токенов внутри реплики (SSE отдаёт реплики целиком).
- Веб-поиск и проверка фактов по внешним источникам: аналитики работают только с текстом
  и своими знаниями, и обязаны это явно помечать.
- Редактирование аналитиков через UI/БД: аналитики описаны в коде (ADR-002).
- Лидерборд персон из первой версии — удалён вместе с персонами-философами.

## 2. Data

- `Analysis` — один разбор: `id`, `source_text`, `question` (что именно проверить,
  опционально), `rounds`, `model_analyst`, `model_judge`, `status`
  (`running|finished|failed`), `error`, `created_at`, `finished_at`, `duration_ms`,
  `title` (первые 80 символов текста без переносов).
- `Turn` — реплика аналитика: `analysis_id`, `round_no`, `analyst`, `emoji`, `text`,
  `created_at`.
- `VerdictRecord` — вердикт судьи: `analysis_id`, `payload` (JSON ровно по Pydantic-схеме
  `Verdict`, §3.3), `created_at`.
- Аналитики (`AnalystSpec`) — в коде, `domain/analysts.py`.

## 3. API contract

Pydantic-схемы в `debate_arena/interface/api/schemas.py` (DTO) и
`debate_arena/domain/schemas.py` (`Verdict`, `AnalystSpec`) — единственный источник
контракта. Префикс `/api`.

### 3.1 `POST /api/analyses` → 202

Запрос `AnalysisRequest`: `text` (1..MAX_TEXT_CHARS символов после strip), `question`
(опционально, ≤ 500), `rounds` (1..MAX_ROUNDS, по умолчанию DEFAULT_ROUNDS), `model`
(опционально, один из `GET /api/config.models`). Ответ `{"id": int}`. Ошибки: 422 —
валидация; 503 — LLM-провайдер не сконфигурирован (не бывает в mock-режиме).

### 3.2 `GET /api/analyses/{id}` → 200 `AnalysisDetail`

`id, title, source_text, question, rounds, status, error, created_at, finished_at,
duration_ms, analysts[], turns[], verdict|null`. 404 если нет.

### 3.3 `Verdict` (структурированный вывод судьи)

| Поле | Тип | Смысл |
|---|---|---|
| `core_question` | str | де-фрейминг: нейтральная формулировка предмета |
| `conclusion` | str | самое рациональное заключение по тексту |
| `rationality_score` | int 0..100 | насколько рационален сам исходный текст |
| `confidence` | int 0..100 | уверенность судьи в заключении |
| `established` | list[str] | что доказано строго |
| `probable` | list[str] | рациональная рабочая гипотеза (Оккам) |
| `uncertain` | list[str] | честные белые пятна |
| `claims` | list[ClaimAssessment] | ключевые утверждения текста и их статус |
| `fallacies` | list[Fallacy] | найденные уловки (имя, цитата, объяснение) |
| `steelman` | str | сильнейшая версия позиции текста |
| `falsification` | str | что должно появиться, чтобы вердикт изменился |
| `so_what` | str | практическое следствие |
| `scores` | list[AnalystScore] | оценка каждого аналитика (rigor/evidence/charity/responsiveness 1..10, note) |
| `most_rational` | str | имя аналитика, чья линия была самой рациональной |

`ClaimAssessment.status ∈ {supported, plausible, unsupported, unfalsifiable, false}`.

**Invariants:** `scores` содержит ровно по одной записи на каждого аналитика;
`most_rational` ∈ имена аналитиков; все числа в диапазонах; `conclusion` непуст.
Невалидный вердикт от модели → одна повторная попытка, затем `status=failed`.

### 3.4 `GET /api/analyses/{id}/events` → `text/event-stream`

Сначала replay всех уже случившихся событий, затем live. События (поле `event`, данные —
JSON): `started {analysts, rounds}`, `round {round_no, kind}`, `typing {analyst}`,
`turn {round_no, analyst, emoji, text}`, `verdict {…Verdict}`, `error {message}`,
`done {status}`. После `done` соединение закрывается. Heartbeat-комментарий каждые
SSE_HEARTBEAT_S секунд. Для завершённого разбора (после рестарта сервера) события
восстанавливаются из БД: `started`, `turn`×N, `verdict`, `done`.

### 3.5 Прочее

- `GET /api/analyses?limit=N` → `AnalysisSummary[]` (новые первыми, `limit` ≤ HISTORY_LIMIT).
- `DELETE /api/analyses/{id}` → 204 (запущенный удалить нельзя → 409).
- `GET /api/config` → `{models: [{id, label, provider}], default_model, default_rounds,
  max_rounds, max_text_chars, mock: bool, analysts: [{name, emoji, focus}]}`.
- `GET /health` → `{status, version, mock}`.
- `GET /` → `index.html` фронтенда.

## 4. Pipeline (normative behavior)

1. **Раунд 1 — Независимый разбор.** Каждый аналитик получает только текст (и
   `question`), без реплик коллег, чтобы исключить якорение. Все три вызова идут
   **параллельно**. Реплика структурирована по протоколу Манифеста: ядро тезиса → факты
   vs интерпретации → бремя доказывания / Оккам / уловки → сильнейшая версия → вывод.
2. **Раунды 2..N — Перекрёстная проверка.** Последовательно; каждый видит весь транскрипт.
   Обязательные блоки реплики: «Принимаю:» (рациональные доводы коллег, которые
   принимаются, с указанием чьи), «Оспариваю:» (ошибки коллег с объяснением),
   «Уточняю:» (собственная скорректированная позиция). Согласие под весом доводов —
   норма, не слабость; несогласие ради несогласия запрещено.
3. **Судья.** Получает текст, вопрос и весь транскрипт, возвращает `Verdict` строго по
   схеме (structured output у Anthropic; JSON-mode + схема в промпте у DeepSeek).
4. Каждая реплика и вердикт сохраняются в БД сразу, события публикуются подписчикам SSE.
5. Любая ошибка модели → `status=failed`, событие `error`, разбор остаётся в истории с
   сохранёнными репликами.

Таблица отказов:

| Причина | Поведение |
|---|---|
| Провайдер вернул `stop_reason=refusal` | `error: "Модель отказалась разбирать этот текст"`, failed |
| Сетевая/5xx ошибка после ретраев SDK | `error` с классом ошибки, failed |
| Вердикт не прошёл схему дважды | `error: "Судья не дал валидный вердикт"`, failed |
| Текст пуст / длиннее лимита | 422 до запуска |

## 5. Constraints handling

- Язык реплик и вердикта — `LANGUAGE` (по умолчанию русский), подставляется в промпты.
- Аналитики не выдумывают источники: отсутствие данных формулируется как «не могу
  проверить», это требование Манифеста (п. 3 и 6).
- Один разбор = `3 × rounds + 1` вызовов LLM. Параллелизм ограничен семафором
  LLM_CONCURRENCY.

## 6. Edge cases (normative)

| # | Input | Required behavior |
|---|---|---|
| 1 | Пустой текст / только пробелы | 422 |
| 2 | Текст > MAX_TEXT_CHARS | 422 с сообщением о лимите |
| 3 | `rounds=1` | Только независимый разбор + судья, событие `round` одно |
| 4 | Подписка на SSE после завершения | Replay из памяти или БД, затем `done` |
| 5 | Рестарт сервера во время разбора | Разбор остаётся `running` в БД → при старте помечается `failed` с `error="interrupted"` |
| 6 | Неизвестный `model` в запросе (нет в `GET /api/config.models`) | 422 |
| 7 | Нет ключей и `MOCK_LLM` не задан | Приложение стартует в mock-режиме и явно пишет это в `/api/config.mock` и в UI |
| 8 | Текст без утверждений (вопрос, список покупок) | Судья обязан вернуть вердикт с `claims=[]` и объяснением в `conclusion` |

## 7. Declared heuristics and assumptions

Значения зеркалятся в `config.py`; менять здесь и там в одном коммите.

| Константа | Значение | Обоснование |
|---|---|---|
| `DEFAULT_ROUNDS` | 2 | 1 независимый + 1 перекрёстный: минимум, при котором видна реакция друг на друга |
| `MAX_ROUNDS` | 3 | 10 вызовов LLM на разбор — потолок по времени ожидания в UI |
| `MAX_TEXT_CHARS` | 20 000 | ~5k токенов текста × 3 аналитика × раунды: укладывается в контекст и бюджет |
| `MODEL_ANALYST` | `anthropic/claude-opus-5-5` | Ключ Anthropic — основной у пользователя; Opus 5.5 — текущая модель линейки |
| `MODEL_JUDGE` | `anthropic/claude-opus-5-5` | Судья должен быть не слабее аналитиков |
| `ANALYST_EFFORT` / `JUDGE_EFFORT` | `medium` / `high` | Opus 5.5: thinking всегда включён, effort — единственный регулятор; судье — больше |
| `TURN_MAX_TOKENS` | 6 000 | На Opus 5.5 thinking входит в лимит; реплика ≤ ~1 500 токенов + запас |
| `JUDGE_MAX_TOKENS` | 16 000 | Структурированный вердикт + thinking |
| `DEEPSEEK_TEMPERATURE` | 0.3 | Аналитика, не креатив; Anthropic temperature не принимает (SDK 1.13) |
| `LLM_CONCURRENCY` | 3 | Параллельный раунд 1 — ровно три вызова |
| `DEEPSEEK_MAX_RETRIES` | 2 | Зеркалит дефолт ретраев Anthropic SDK; httpx сам не ретраит (408/409/429/5xx, сетевые) |
| `DEEPSEEK_TIMEOUT_S` | 600 | Reasoner отвечает минутами |
| `RUNS_KEPT_IN_MEMORY` | 50 | Завершённые разборы в памяти для быстрого replay SSE; дальше — из БД |
| `SSE_HEARTBEAT_S` | 15 | Прокси обрывают тихие соединения на 30–60 с |
| `HISTORY_LIMIT` | 50 | Боковая панель истории |
| `TITLE_CHARS` | 80 | Заголовок в истории |
| `LANGUAGE` | русский | язык реплик |

## 8. Observability

Логи через `logging` (структура `%(asctime)s %(levelname)s %(name)s: %(message)s`). На
каждый вызов LLM — строка `llm.call provider=<p> model=<m> role=<analyst|judge>
analysis=<id> round=<n> ms=<t> in=<tokens> out=<tokens>`; на завершение разбора —
`analysis.done id=<id> status=<s> ms=<t> calls=<n>`. Токены — как отдал провайдер
(`usage`), без оценок. `/health` отдаёт версию из `pyproject`.

## 9. Non-functional targets

| Цель | Замер |
|---|---|
| Разбор на 2 раунда с Opus 5.5 укладывается в 3 минуты | ✗ не измерено — нужен живой ключ (2026-10-11) |
| Unit-тесты без сети < 10 с | ✓ 107 тестов, 3.6 с (`uv run pytest`, 2026-10-11) |
| Разбор в mock-режиме, 1 раунд | 0.8 с (CLI, 2026-10-11) |

## 10. Acceptance criteria / Definition of Done

- `uv run pytest` зелёный: контракт API (§3), пайплайн на фейковом провайдере (§4),
  edge cases §6 №1–4, 6–7.
- Ручная проверка в mock-режиме: текст → три реплики × 2 раунда → вердикт в UI; история;
  перезагрузка страницы восстанавливает разбор.
- Done, когда с живым ключом Anthropic разбор проходит до вердикта (ручной прогон,
  результат записан в README «Measurements»).
