# План реализации агента "Подбор продуктов"

Дата подготовки: 04.05.2026

## 1. Цель

Добавить в существующую цепочку агентов новый специализированный агент `product_selection_agent`, который отвечает за подбор, сравнение и объяснение продуктов на основе продуктового классификатора, каталога данных и семантического слоя через `dbhub` MCP.

Итоговая цепочка должна стать:

1. `owasp_agent`
2. `dispatcher_agent`
3. один из downstream-агентов:
   - `doc_search_orchestrator`
   - `kb_answer_agent`
   - `product_selection_agent`

## 2. Уточненная постановка

Нужно не расширять `kb_answer_agent`, а добавить отдельного агента для продуктовых сценариев. Причина: подбор продуктов требует SQL-доступа к табличным данным, работы с каталогом данных, объяснимых фильтров и ранжирования. Это отличается от RAG-ответов по FAQ/KB и от поиска документов.

Минимально достаточный MVP:

- маршрутизировать продуктовые запросы в новый route;
- создать leaf-агента с `dbhub` MCP tools;
- валидировать JSON-контракт агента в `root_agent`;
- возвращать пользователю финальный текст через существующую схему `_root_final_text`;
- не менять внешний контракт Telegram-бота.

## 3. Предположения

- Продуктовый классификатор уже загружен в PostgreSQL, доступный сервису `dbhub`. Это таблица products, описание таблицы есть в каталоге данных. Исходные таблицы здесь: kb_storage\tables. загрузка выполняется скриптом mcps\kb-manager\app\scripts\load_tables.py
- В `mcps/dbhub/dbhub_nstya_config.toml` уже описаны необходимые tools: `execute_sql`, `search_objects`, `search_table`, `search_column`, `search_analytic`, `search_semantic_template`.
- Точный физический table name классификатора агент должен узнавать через `search_table`, а не получать из промпта как жестко заданный факт.
- На первом этапе агент работает только с табличным классификатором. Если источника презентаций, описаний и акций нет в `dbhub`, по UC-01 агент честно сообщает, что дополнительные материалы требуют отдельного источника.
- Генерация файлов для UC-09 не входит в первый инкремент. В MVP агент возвращает Markdown/текст в сообщении. Потом мы добавим файлы про продукты, и занесем их в таблицу явно, чтобы именно они составляли "комплект материалов для продукта".

## 4. Критерий успеха

Из пользовательских запросов вида:

- "Расскажи про Fort Knox 6 месяцев"
- "Какие продукты без риска потери капитала?"
- "Сравни Защищенный капитал и Fort Knox"
- "Что посоветовать клиенту, который хочет сохранить сбережения?"
- "Почему Unit Linked не основной вариант для сохранения сбережений?"

диспетчер выбирает `route="product_selection"`, новый агент получает данные через `dbhub`, формирует короткий проверяемый ответ, а `root_agent` возвращает этот текст пользователю. Существующие маршруты `doc_search`, `kb_answer`, `smalltalk`, `show_more`, `show_all`, `file_download` не ломаются.

## 5. Архитектурное решение

### 5.1. Новый route и intent

Добавить route:

```json
"product_selection"
```

Добавить intents:

```json
"product_filter"
"product_compare"
"product_recommendation"
"product_explanation"
"product_alternatives"
```

Правило маршрутизации:

- если пользователь просит свойства, подбор, сравнение, альтернативы, объяснение выбора или список продуктов по параметрам, использовать `route="product_selection"`;
- если пользователь явно просит файл, презентацию, документ, регламент, памятку или скачать материал, сохранять `route="doc_search"`;
- если пользователь спрашивает общий вопрос по знаниям, условиям, правилам или процессам, не связанным с табличным подбором продуктов, сохранять `route="kb_answer"`;
- `smalltalk` остается внутри `kb_answer`.

### 5.2. Новый агент

Создать модуль:

```text
agent/agents/product_selection_agent.py
```

Ответственность:

- анализировать продуктовый intent и сущности из state;
- искать нужную таблицу через каталог данных;
- читать бизнес-описания колонок;
- читать допустимые значения аналитик;
- при необходимости читать семантические шаблоны;
- выполнять только read-only SQL через `execute_sql`;
- формировать ответ, привязанный к строкам и полям классификатора;
- не отвечать по памяти, если данных нет.

Не должен делать:

- OWASP-проверку;
- маршрутизацию;
- поиск документов через `kb_search`;
- финальную orchestration logic;
- комплаенс-решение за менеджера.

### 5.3. Контракт `product_selection_agent`

Базовый JSON:

```json
{
  "status": "ok",
  "mode": "product_card",
  "message": "Краткий ответ пользователю",
  "used_tables": ["products"],
}
```

Допустимые `mode`:

"product_filter"
"product_compare"
"product_recommendation"
"product_explanation"
"product_alternatives"
- `no_data`

Валидация:

- `status` только `ok`;
- `mode` из списка выше;
- `message` обязателен и не пустой;
- `source` только `dbhub` или `none`;
- для `no_data` допустим `source="none"`;
- `used_tables` нормализуются в массивы строк;

### 5.4. State

`root_agent` должен передавать в state:

- общий `user_query`;
- `product_selection_intent`;
- `product_selection_search_query`;
- `product_selection_result_json`;
- `_product_selection_result_parsed`.

также передавать профиль пользователя по аналогии с `kb_answer_agent`

### 5.5. Интеграция с dbhub MCP

Добавить в `agent/config.py` настройки:

- `DBHUB_MCP_URL`, значение по умолчанию: `http://dbhub:8080/mcp`;
- `DBHUB_MCP_TOKEN`, по умолчанию `MCP_TOKEN`;
- `DBHUB_MCP_TIMEOUT_SEC`, по умолчанию `MCP_TIMEOUT_SEC`.

В `product_selection_agent` подключить `McpToolset` по аналогии с `kb_answer_agent` и `doc_search_agent`, но с tool filter:

```python
[
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]
```

Перед реализацией нужно проверить фактический endpoint `bytebase/dbhub:latest` в текущем compose. Если `/mcp` не подходит, зафиксировать рабочий путь в `.env` через `DBHUB_MCP_URL`.

### 5.6. Работа с каталогом данных и семантическим слоем

Промпт агента должен закрепить цикл работы, аналогичный `C:\GitHub\dbhub_impl\products_upload\Ты — аналитический ReAct-ассистент.md`:

0. вызвать `search_semantic_template` для понимания терминологии и привычных шаблонов ответов
1. Вызвать `search_table` и определить релевантную таблицу.
2. Вызвать `search_column` для выбранной таблицы.
3. Для категориальных фильтров вызвать `search_analytic`.
5. Если каталога недостаточно, использовать `search_objects` для проверки структуры.
6. Сформировать минимальный SQL.
7. Выполнить `execute_sql`.
8. Интерпретировать результат, отделяя данные таблицы от вывода агента.

Запреты в промпте:

- не придумывать имена таблиц, колонок и значения;
- не использовать `SELECT *` для финальных пользовательских ответов, кроме диагностического ограниченного просмотра структуры;
- не утверждать актуальность "на сегодня", если нет поля статуса или версии;
- не раскрывать внутренние поля во внешнем клиентском тексте без явного разрешения в данных;
- не давать инвестиционную рекомендацию как финальное решение, только помощь менеджеру.

## 6. План работ

### Этап 1. Проверка исходных данных и dbhub

1. Проверить, что таблица `products` и таблицы каталога данных загружены в PostgreSQL через `mcps/kb-manager/app/scripts/load_tables.py`.
2. Проверить, что `mcps/dbhub/dbhub_nstya_config.toml` видит источник `nstya_data` и содержит tools `execute_sql`, `search_objects`, `search_table`, `search_column`, `search_analytic`, `search_semantic_template`.
3. Проверить рабочий HTTP endpoint `dbhub` из контейнера `adk-agent`. Если `/mcp` не подходит, зафиксировать фактический путь в `DBHUB_MCP_URL`.
4. Проверить вручную минимальный сценарий tools: `search_table` -> `search_column(products)` -> `search_analytic` для одного категориального поля -> `execute_sql` по `products`.

### Этап 2. Контракты маршрутизации и нового агента

1. Добавить в `validate_dispatcher_result` route `product_selection`.
2. Добавить допустимые product intents:
   - `product_filter`
   - `product_compare`
   - `product_recommendation`
   - `product_explanation`
   - `product_alternatives`
3. Зафиксировать семантическое правило: product intents допустимы только с `route="product_selection"`.
4. Для product intents требовать непустой `search_query`.
5. Создать `validate_product_selection_result` в `agent/agents/product_selection_agent.py`.
6. В валидаторе нового агента проверять поля из текущего MVP-контракта: `status`, `mode`, `message`, `source`, `used_tables`.
7. Нормализовать `used_tables` в массив строк.
8. Добавить unit-тесты на валидаторы диспетчера и нового агента.

### Этап 3. Диспетчер

1. Обновить `kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md`.
2. Добавить `product_selection` в список допустимых route.
3. Добавить product intents из раздела 5.1.
4. Обновить приоритет маршрутизации:
   - сначала `file_download`, `show_more`, `show_all`;
   - затем явный `doc_search`;
   - затем `product_selection`;
   - затем `smalltalk`;
   - затем `kb_answer`.
5. Добавить примеры продуктовых запросов:
   - список/фильтр продуктов по параметрам -> `product_filter`;
   - сравнение продуктов или семейств -> `product_compare`;
   - подбор под цель клиента -> `product_recommendation`;
   - объяснение, почему продукт подходит или не подходит -> `product_explanation`;
   - поиск альтернатив -> `product_alternatives`.
6. Отдельно закрепить, что запросы на файлы, презентации, документы и скачивание материалов по продукту остаются в `doc_search`.

### Этап 4. Новый агент

1. Создать `agent/agents/product_selection_agent.py`.
2. Реализовать `create_product_selection_agent(model)`.
3. Подключить `dbhub` MCP tools через `McpToolset` и настройки `DBHUB_MCP_URL`, `DBHUB_MCP_TOKEN`, `DBHUB_MCP_TIMEOUT_SEC`.
4. Ограничить tools списком из раздела 5.5.
5. Добавить fallback prompt.
6. Создать основной prompt:

```text
kb_storage/prompts/product_selection/product_selection_agent_prompt.md
```

7. В prompt закрепить обязательный порядок работы из раздела 5.6:
   - сначала `search_semantic_template`;
   - затем `search_table`;
   - затем `search_column`;
   - затем при необходимости `search_analytic`;
   - затем при необходимости `search_objects`;
   - затем минимальный read-only SQL через `execute_sql`.
8. Запретить ответы по памяти, выдумывание колонок/значений и утверждение актуальности "на сегодня" без поля статуса или версии.
9. Убедиться, что `load_prompt("product_selection_agent_prompt.md", ...)` корректно попадает в папку `product_selection`.

### Этап 5. Root orchestration

1. Добавить `product_selection_agent` в `RootAgent`.
2. Добавить его в `sub_agents`.
3. Очищать `_product_selection_result_parsed` перед запуском цепочки.
4. После диспетчера обрабатывать `dispatch["route"] == "product_selection"`.
5. Передавать в state:
   - общий `user_query`;
   - `product_selection_intent`;
   - `product_selection_search_query`.
6. Передавать профиль пользователя по аналогии с `kb_answer_agent`.
7. Запускать нового leaf-агента через `_run_json_leaf_agent`.
8. Класть `format_text_answer(product_selection["message"])` в `_root_final_text`.

### Этап 6. Сборка цепочки

1. В `agent/start_agent.py` создать `product_selection_agent = create_product_selection_agent(model)`.
2. Передать его в `RootAgent`.
3. Обновить тест `test_start_agent.py`, включая stubs.
4. Обновить тесты `test_rootagent.py` на новый route.

### Этап 7. Конфигурация окружения

1. Добавить настройки `DBHUB_MCP_URL`, `DBHUB_MCP_TOKEN`, `DBHUB_MCP_TIMEOUT_SEC`.
2. Проверить `docker-compose.yaml`: сервис `dbhub` уже есть и монтирует `mcps/dbhub/dbhub_nstya_config.toml`.
3. Если endpoint или доступность `dbhub` нестабильны, добавить healthcheck.
4. Для Kubernetes отдельно синхронизировать env-переменные, service discovery и NetworkPolicy, если агент будет ходить в `dbhub` в кластере.

### Этап 8. Тестирование

Unit:

- валидатор dispatcher с `product_selection`;
- валидатор `product_selection_agent`;
- `RootAgent` вызывает новый агент при `route="product_selection"`;
- `RootAgent` не вызывает `kb_answer_agent` и `doc_search_orchestrator` для продуктового route;
- `start_agent` собирает цепочку с новым агентом.

Интеграционные проверки:

- `dbhub` доступен из контейнера `adk-agent`;
- `search_table` возвращает таблицу классификатора;
- `search_column` возвращает бизнес-описания колонок;
- `search_semantic_template` возвращает шаблоны семантического слоя;
- `execute_sql` работает read-only;
- агент корректно отвечает на сценарии из MVP: фильтр, сравнение, подбор, объяснение, альтернативы.

Регрессионные сценарии:

- "покажи документы по Fort Knox" -> `doc_search`;
- "скачай 1" -> `doc_search` / `file_download`;
- "что ты умеешь" -> `kb_answer` / `smalltalk`;
- "что такое НСЖ" -> `kb_answer`;
- "сравни Fort Knox и Защищенный капитал" -> `product_selection`.

