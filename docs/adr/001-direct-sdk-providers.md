# ADR-001: LLM-провайдеры вызываются напрямую через SDK за одним Protocol, без CrewAI/LiteLLM

- **Status:** Accepted
- **Date:** 2026-10-11

## Context

Первая версия ходила в модели через CrewAI `LLM` (поверх LiteLLM). По факту использовался
один прямой `llm.call(messages)` — ни агентов, ни инструментов, ни Crew. Зависимость тянет
~150 пакетов (chromadb, instructor, opentelemetry), ломала JSON-режим DeepSeek и не умеет
`output_config`/`effort` новых моделей Anthropic. Требование пользователя: основной ключ —
Anthropic, DeepSeek оставить как опцию.

## Decision

1. Порт `ChatProvider` (`domain/ports.py`): `complete(system, user, *, max_tokens,
   json_schema=None) -> Completion(text, usage)` и `complete_structured(..., schema: type[T])
   -> T`.
2. `infrastructure/llm/anthropic.py` — официальный `anthropic.AsyncAnthropic`:
   `messages.create` для реплик, `messages.parse(output_format=Verdict)` для судьи,
   `output_config.effort` вместо temperature (SDK 1.13 `create` не принимает temperature).
   `stop_reason == "refusal"` → доменное `ProviderRefused`.
3. `infrastructure/llm/deepseek.py` — `httpx.AsyncClient` к OpenAI-совместимому
   `https://api.deepseek.com/chat/completions`; для судьи `response_format=json_object` +
   JSON-схема в промпте, парсинг с извлечением `{...}` из прозы.
4. Строка модели `provider/model` (`anthropic/claude-opus-5-5`, `deepseek/deepseek-chat`,
   `mock/*`); фабрика выбирает реализацию по префиксу. Провайдер — конфигурация.
5. `MockProvider` в `infrastructure/llm/mock.py` — для UI без ключей; `tests/fakes/`
   — `FakeProvider` с очередью ответов и журналом вызовов.

## Alternatives considered

1. **Оставить LiteLLM без CrewAI.** Единый интерфейс для сотни провайдеров; rejected:
   не поддерживает `output_config`/structured outputs Anthropic в актуальном виде, лишний
   слой трансляции ошибок, при двух провайдерах выигрыш нулевой.
2. **`openai` SDK с `base_url` DeepSeek.** Чуть меньше кода, чем httpx; rejected: ещё одна
   крупная зависимость ради одного POST; httpx уже транзитивно есть у anthropic.
3. **Anthropic через OpenAI-совместимый шим.** rejected: теряем structured outputs,
   effort, refusal-семантику; официальный SDK — единственный поддерживаемый путь.

## Consequences

- Минус ~150 транзитивных пакетов, старт за секунды.
- Два провайдера = два файла; третий добавляется файлом + префиксом.
- Серверные fallbacks при refusal (beta) не включены — refusal показывается как ошибка
  разбора (SPEC §4). Записано в Cut scope.

## Revisit when

- Появится третий провайдер со своим протоколом (не OpenAI-совместимый).
- Нужны инструменты (веб-поиск) у аналитиков — тогда сравнить tool runner SDK.
