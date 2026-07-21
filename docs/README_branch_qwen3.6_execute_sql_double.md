# Изменения ветки `qwen3.6_execute_sql_double` относительно `main`

Документ описывает отличия ветки от `main` после точки ветвления
[`839d3a4`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/839d3a40d872fc4acaf3cb91a64d30d57f6c47e9)
(`dispatcher prompt fix dohod added`). Сам этот коммит уже влит в `main`;
уникальная работа ветки начинается с [`9999bae`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/9999bae69484d382809ee96c56651ab0036b8362)
(`timings initial`) и далее.

**Коммиты ветки (не в `main`):**

| Коммит | Описание |
|--------|----------|
| [`9999bae`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/9999bae69484d382809ee96c56651ab0036b8362) | timings initial |
| [`4455ebd`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/4455ebda6a5e7ad3650c788d6ab1f774e5f23eaa) | Merge branch `main` into `llm_timing` |
| [`b166a76`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/b166a762f91f5405fd35ab8f8d122b30517fb745) | analyze timing |
| [`937d873`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/937d8737a7811e9d14eb04bccf4b5baa05b74513) | few changes |
| [`5c355db`](https://nstdata.gitlab.yandexcloud.net/ml.sandbox/asch-chat-bot/-/commit/5c355db12c3710aba36f1c27a44fef1e1b95481e) | benchmark timing quality |

---

## 1. Метрики стадий LLM (тайминги и токены)

Добавлен сквозной сбор метрик по стадиям агентной цепочки: wall-time, TTFT,
input/output tokens, число tool calls и model turns.

- Новый модуль [`agent/stage_metrics.py`](../agent/stage_metrics.py) —
  запись в `session.state["_stage_metrics"]`, flattening в payload `_timing`
  (`owasp_ms`, `dispatcher_ttft_ms`, `doc_search_input_tokens`, … + `route`/`intent`).
- [`agent/json_leaf_runner.py`](../agent/json_leaf_runner.py) — при прогоне
  leaf-агента считает TTFT по первому полезному model output, суммирует
  usage_metadata и вызывает `record_stage_metrics`.
- [`agent/rootagent.py`](../agent/rootagent.py) — в финальный `state_delta`
  кладёт `_timing` через `build_timing_payload`, чтобы бот мог прочитать метрики
  из ADK-событий.
- [`bot/services/adk_events.py`](../bot/services/adk_events.py) —
  `extract_timing()` достаёт `_timing` из `stateDelta` финального события.
- [`bot/services/handlers.py`](../bot/services/handlers.py) — stage-тайминги
  попадают в payload `events.response` рядом с `response_time_ms`.
- Юнит-тесты: [`tests/unit/agent/test_stage_metrics.py`](../tests/unit/agent/test_stage_metrics.py),
  [`tests/unit/agent/test_json_leaf_runner.py`](../tests/unit/agent/test_json_leaf_runner.py),
  [`tests/unit/bot/test_adk_events.py`](../tests/unit/bot/test_adk_events.py).

Вспомогательный скрипт разбора прогонов: [`tmp_analyze_models.py`](../tmp_analyze_models.py).

---

## 2. Бенчмарк ADK: качество ответов и тайминги

Добавлен офлайн-прогон вопросов напрямую в ADK (минуя Telegram) с выгрузкой
ответов, таймингов и LLM-оценкой ok/не ok.

- Скрипт [`scripts/adk_ask.py`](../scripts/adk_ask.py) — Excel/текстовый набор
  вопросов, сессии ADK, сбор `answers.jsonl` / `timings.jsonl`, summary по агентам,
  оценка по 10-балльной шкале (как в Jenkins tester).
- Пример вопросов: [`scripts/sample_questions.txt`](../scripts/sample_questions.txt).
- Артефакты прогонов: каталоги [`out/adk_ask*`](../out/)
  (в т.ч. сравнения моделей Qwen3-30B / Qwen3.6-27B).

---

## 3. Product selection: три leaf-агента вместо одного

Один `product_selection_agent` разделён на три специализированных leaf-агента
с отдельными tool-filter, промптами и `output_schema` (плоский контракт под ADK
`set_model_response`).

- Реализация в [`agent/agents/product_selection_agent.py`](../agent/agents/product_selection_agent.py):
  - `create_product_selection_card_kit_agent` / `_filter_` / `_compare_`;
  - схемы `ProductSelection*ResponseSchema`;
  - `coerce_product_selection_schema_payload` — разворот JSON-строк и алиасов
    полей в контракт валидатора;
  - `select_product_selection_agent(intent, …)` — выбор leaf по intent.
- Промпты:
  - [`kb_storage/prompts/product_selection/product_selection_agent_card_kit_prompt.md`](../kb_storage/prompts/product_selection/product_selection_agent_card_kit_prompt.md)
  - [`kb_storage/prompts/product_selection/product_selection_agent_filter_prompt.md`](../kb_storage/prompts/product_selection/product_selection_agent_filter_prompt.md)
  - [`kb_storage/prompts/product_selection/product_selection_agent_compare_prompt.md`](../kb_storage/prompts/product_selection/product_selection_agent_compare_prompt.md)
  - обновлён общий [`product_selection_agent_prompt.md`](../kb_storage/prompts/product_selection/product_selection_agent_prompt.md)
- [`agent/rootagent.py`](../agent/rootagent.py) / [`agent/start_agent.py`](../agent/start_agent.py)
  держат три агента и маршрутизируют через `select_product_selection_agent`.
- Tool-filter сужены по режиму: card/kit и compare — в основном
  `search_column` + `execute_sql`; filter — полный набор аналитики без
  `search_objects`.

---

## 4. Диалог уточнения продукта (needs_clarification → follow-up)

В root-агенте добавлен resume после `needs_clarification`: пользователь выбирает
вариант, цепочка продолжает исходный intent (compare / kit / card).

- Сохранение контекста (`pending_intent`, `clarification_options`,
  `compare_resolved_products`, `original_search_query`) в
  [`agent/rootagent.py`](../agent/rootagent.py).
- `_match_clarification_option` / `_dispatch_clarification_followup` —
  матч по коду или названию и синтез нового dispatcher-результата.
- `_resolved_products_from_resolutions` — восстановление уже resolved
  продуктов для resume compare.
- Тесты: [`tests/unit/agent/test_rootagent.py`](../tests/unit/agent/test_rootagent.py).

---

## 5. Резолвер продуктов: валюта и защита от RecursionError

### 5.1. Фильтр по валюте

При ambiguous-наборе кандидатов запрос сужается, если в тексте явно указана
валюта (`$`, `¥`, «доллар», «юань», …) — символы валюты раньше вырезались
нормализацией и схлопывали варианты в один набор.

- [`agent/product_resolver_service.py`](../agent/product_resolver_service.py) —
  `CURRENCY_HINT_MARKERS`, `_detect_currency_hints`,
  `_filter_candidates_by_currency_hint`.

### 5.2. RecursionError на «архивные бандлы 8965 7698»

Для запросов вроде `архивные бандлы 8965 7698` `extract_product_mentions`
возвращал и всю фразу, и отдельно коды:

`['архивные бандлы 8965 7698', '8965', '7698']`

Дальше `len(mentions) > 1` → `_resolve_product_filter_multi` → первый mention
снова весь запрос → `_resolve_product_filter_safe` → те же mentions → multi
→ `RecursionError`.

Исправление в [`agent/product_resolver_service.py`](../agent/product_resolver_service.py):

- из текстовых частей mentions убираются уже найденные коды →
  `['архивные бандлы', '8965', '7698']`;
- в multi каждый mention резолвится с `allow_multi=False`, чтобы не уходить
  в multi повторно.

Тесты: [`tests/unit/agent/test_product_resolver_service.py`](../tests/unit/agent/test_product_resolver_service.py).

> Примечание: фикс §5.2 на момент написания документа может быть ещё в
> незакоммиченном working tree (не только в `main...HEAD`).

---

## 6. Конфиг LLM: thinking, таймауты, общий GenerateContentConfig

Единая фабрика генерации и параметры LiteLLM под thinking-модели.

- [`agent/config.py`](../agent/config.py):
  - `LITELLM_REQUEST_TIMEOUT`, `LITELLM_NUM_RETRIES`, `LLM_MAX_OUTPUT_TOKENS`;
  - `enable_thinking` / `thinking_token_budget` в `extra_body`;
  - `build_generate_content_config(temperature)` — общий `max_output_tokens` +
    temperature (или ADK сам, если `-1`);
  - дефолт `ROOT_TEMPERATURE` 0.0 → 0.2.
- Агенты переведены на фабрику:
  [`dispatcher_agent.py`](../agent/agents/dispatcher_agent.py),
  [`doc_search_agent.py`](../agent/agents/doc_search_agent.py),
  [`kb_answer_agent.py`](../agent/agents/kb_answer_agent.py),
  [`owasp_agent.py`](../agent/agents/owasp_agent.py),
  product_selection (см. выше).
- В `kb_answer` поле `status` в схеме ослаблено с `Literal["ok"]` на `str`
  (ADK не принимает JSON Schema `const`) —
  [`agent/agents/kb_answer_agent.py`](../agent/agents/kb_answer_agent.py).
- Docker / таймауты бота:
  [`docker-compose.yaml`](../docker-compose.yaml),
  [`bot/services/database.py`](../bot/services/database.py) (`AdkApiClient` timeout),
  [`bot/services/config.py`](../bot/services/config.py),
  [`bot/bot_v6.py`](../bot/bot_v6.py), [`bot/max_bot.py`](../bot/max_bot.py).

---

## 7. Doc search: отказ от snippet в выдаче

Список документов больше не хранит и не показывает snippet — только
ранг, id, имя и путь.

- Валидатор и fallback-промпт: [`agent/agents/doc_search_agent.py`](../agent/agents/doc_search_agent.py).
- Промпт: [`kb_storage/prompts/doc_search/doc_search_agent_prompt.md`](../kb_storage/prompts/doc_search/doc_search_agent_prompt.md).
- Рендер списка: [`utils/doc_search_format.py`](../utils/doc_search_format.py).
- БД-чтение результатов: [`bot/services/database.py`](../bot/services/database.py),
  [`utils/search_results_db.py`](../utils/search_results_db.py).
- Тесты: [`tests/unit/utils/test_doc_search_format.py`](../tests/unit/utils/test_doc_search_format.py).

---

## Краткая карта «что смотреть»

| Тема | Главные файлы |
|------|----------------|
| Тайминги стадий | [`agent/stage_metrics.py`](../agent/stage_metrics.py), [`agent/json_leaf_runner.py`](../agent/json_leaf_runner.py), [`bot/services/adk_events.py`](../bot/services/adk_events.py) |
| Бенчмарк | [`scripts/adk_ask.py`](../scripts/adk_ask.py) |
| Product selection split | [`agent/agents/product_selection_agent.py`](../agent/agents/product_selection_agent.py) |
| Clarification resume | [`agent/rootagent.py`](../agent/rootagent.py) |
| Валюта + RecursionError в mentions | [`agent/product_resolver_service.py`](../agent/product_resolver_service.py) |
| Thinking / timeouts | [`agent/config.py`](../agent/config.py), [`docker-compose.yaml`](../docker-compose.yaml) |
| Без snippet | [`agent/agents/doc_search_agent.py`](../agent/agents/doc_search_agent.py), [`utils/doc_search_format.py`](../utils/doc_search_format.py) |
