# Изменения ветки `qwen3.6_for_merge` относительно `main`

Ветка вынесена из `qwen3.6_execute_sql_double`. Содержит только:

1. метрики стадий LLM (`_timing`);
2. resume после `needs_clarification`;
3. фильтр кандидатов по валюте в product resolver;
4. отказ от `snippet` в doc search.

Не входят (откаты к состоянию как на `main` / `839d3a4`): split product_selection на 3 leaf-агента, фикс RecursionError в mentions, конфиг thinking/timeouts/`build_generate_content_config`.

Backup исходной точки: `refs/backup/pre-split-qwen36-for-merge`.

---

## 1. Метрики стадий LLM (тайминги и токены)

Сквозной сбор метрик по стадиям: wall-time, TTFT, input/output tokens,
tool calls, model turns.

- [`agent/stage_metrics.py`](../agent/stage_metrics.py) — `_stage_metrics` → плоский `_timing`
- [`agent/json_leaf_runner.py`](../agent/json_leaf_runner.py) — TTFT / usage / `record_stage_metrics`
- [`agent/rootagent.py`](../agent/rootagent.py) — `_timing` в финальный `state_delta`
- [`bot/services/adk_events.py`](../bot/services/adk_events.py) — `extract_timing()`
- [`bot/services/handlers.py`](../bot/services/handlers.py) — тайминги в `events.response`
- Тесты: [`tests/unit/agent/test_stage_metrics.py`](../tests/unit/agent/test_stage_metrics.py),
  [`tests/unit/agent/test_json_leaf_runner.py`](../tests/unit/agent/test_json_leaf_runner.py),
  [`tests/unit/bot/test_adk_events.py`](../tests/unit/bot/test_adk_events.py)

---

## 2. Диалог уточнения продукта (needs_clarification → follow-up)

После `needs_clarification` пользователь выбирает option — цепочка продолжает
исходный intent (compare / kit / card).

- [`agent/rootagent.py`](../agent/rootagent.py) —
  `_match_clarification_option`, `_dispatch_clarification_followup`,
  `_resolved_products_from_resolutions`, сохранение `pending_intent` / options
- Тесты: [`tests/unit/agent/test_rootagent.py`](../tests/unit/agent/test_rootagent.py)

---

## 3. Резолвер продуктов: фильтр по валюте

При ambiguous-наборе кандидатов запрос сужается, если явно указана валюта
(`$`, `¥`, «доллар», «юань», …).

- [`agent/product_resolver_service.py`](../agent/product_resolver_service.py) —
  `CURRENCY_HINT_MARKERS`, `_detect_currency_hints`,
  `_filter_candidates_by_currency_hint`
- Тесты: [`tests/unit/agent/test_product_resolver_service.py`](../tests/unit/agent/test_product_resolver_service.py)

---

## 4. Doc search: отказ от snippet в выдаче

Список документов без snippet — только ранг, id, имя и путь.

- [`agent/agents/doc_search_agent.py`](../agent/agents/doc_search_agent.py),
  [`agent/agents/doc_search_orchestrator.py`](../agent/agents/doc_search_orchestrator.py)
- Промпт: [`kb_storage/prompts/doc_search/doc_search_agent_prompt.md`](../kb_storage/prompts/doc_search/doc_search_agent_prompt.md)
- [`utils/doc_search_format.py`](../utils/doc_search_format.py),
  [`utils/search_results_db.py`](../utils/search_results_db.py),
  [`bot/services/database.py`](../bot/services/database.py)
- Тесты: [`tests/unit/utils/test_doc_search_format.py`](../tests/unit/utils/test_doc_search_format.py),
  [`tests/unit/bot/test_utils.py`](../tests/unit/bot/test_utils.py)

---

## Краткая карта

| Тема | Главные файлы |
|------|----------------|
| Тайминги стадий | [`agent/stage_metrics.py`](../agent/stage_metrics.py), [`agent/json_leaf_runner.py`](../agent/json_leaf_runner.py), [`bot/services/adk_events.py`](../bot/services/adk_events.py) |
| Clarification resume | [`agent/rootagent.py`](../agent/rootagent.py) |
| Валютный disambiguation | [`agent/product_resolver_service.py`](../agent/product_resolver_service.py) |
| Без snippet | [`agent/agents/doc_search_agent.py`](../agent/agents/doc_search_agent.py), [`utils/doc_search_format.py`](../utils/doc_search_format.py) |
