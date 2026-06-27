# Актуализация на 2026-04-15

Для текущей реализации нужно считать актуальными следующие правила:

- `kb_answer_agent` использует два MCP-инструмента: `faq_search` и `kb_search`.
- Для содержательного запроса агент сначала вызывает `faq_search`.
- Если FAQ дал точный или достаточно уверенный ответ, `kb_search` не вызывается.
- Если FAQ дал слабый, частичный или неполный результат, агент вызывает `kb_search` как fallback или дополнение.
- При конфликте данных приоритет у `faq_search`.
- Для `smalltalk` ни `faq_search`, ни `kb_search` не вызываются.
- В state для `kb_answer_agent` передаются `search_query`, `faq_collection`, `kb_answer_collection`, `intent` и пользовательский профиль.
- Контракт `kb_answer_agent` включает поле `source` со значениями `faq_search | kb_search | faq_search+kb_search | none`.

# Актуализация на 2026-06-21 (plan 001: human corporate dialogue)

- **Ack:** bot generic (`ACK_ENABLED`) или route-aware interim event из `root_agent` (`ADK_ROUTE_ACK_ENABLED`). Ack не пишется в dialog store; event log `event_type=ack`.
- **Turn guard:** `turn_id` + `ACTIVE_TURNS` в bot — stale final drop при быстром втором сообщении.
- **Dialogue manager:** `agent/dialogue/manager.py` — CTA whitelist, smalltalk limit (3 → redirect), clarification override (`needs_clarification`).
- **Voice agent:** опционально (`VOICE_AGENT_ENABLED=false` по умолчанию); `fact_guard.validate_voice` — fallback на draft при новых anchors.
- **Persistent state:** `dialog_phase`, `dialog_topic`, `smalltalk_turns`, `last_route`, `last_cta`, `pending_clarification` — не очищаются в `_clear_state_keys`.

---
# Актуальное описание реализации цепочки агентов

## 1. Назначение

Документ фиксирует фактическую архитектуру реализованной цепочки агентов в Google ADK.

Цель реализации:
- централизованно оркестрировать обработку пользовательского запроса;
- разделить ответственность между специализированными агентами;
- валидировать JSON-результат каждого промежуточного этапа;
- изолировать безопасность, маршрутизацию, поиск документов и ответы по базе знаний;
- обеспечить расширяемую схему для Telegram-бота и ADK runtime.

---

## 2. Состав цепочки

В цепочке участвуют:

- `owasp_agent`
- `dispatcher_agent`
- `doc_search_agent`
- `kb_answer_agent`
- `root_agent` — кодовый оркестратор над всей цепочкой

Создание цепочки выполняется через `build_agent_chain()`, который собирает общий model instance и передаёт его во все leaf-агенты. Конечная внешняя точка входа — `root_agent`.

---

## 3. Роли агентов

### `owasp_agent`
Назначение:
- проверка пользовательского запроса на небезопасный ввод;
- возврат строго структурированного JSON-результата.

Не делает:
- поиск по базе знаний;
- поиск документов;
- маршрутизацию;
- формирование содержательного ответа пользователю.

---

### `dispatcher_agent`
Назначение:
- классификация пользовательского запроса;
- выбор единственного маршрута обработки;
- формирование нормализованного `search_query` для следующего этапа.

Не делает:
- вызовы MCP tools;
- поиск документов;
- поиск ответа в базе знаний;
- финальный ответ пользователю.

---

### `doc_search_agent`
Назначение:
- поиск документов через MCP tool `kb_search`;
- возврат JSON-контракта с режимом документного ответа и пользовательским сообщением.

Особенности текущей реализации:
- работает только как агент поиска документов;
- использует коллекцию из `ctx.session.state["doc_search_collection"]`;
- получает поисковый запрос через `search_query`.

Не делает:
- проверку безопасности;
- маршрутизацию;
- ответ по знаниям как основной режим;
- финальную оркестрацию.

---

### `kb_answer_agent`
Назначение:
- ответ по базе знаний через MCP tool `kb_search`;
- обработка smalltalk в рамках того же агента;
- возврат JSON-контракта с текстовым ответом.

Особенности текущей реализации:
- при `intent=kb_answer` обязан искать ответ через `kb_search`;
- при `intent=smalltalk` не вызывает `kb_search` и отвечает как разговорный ассистент;
- использует коллекцию из `ctx.session.state["kb_answer_collection"]`.

Не делает:
- проверку безопасности;
- маршрутизацию;
- финальную оркестрацию.

---

### `root_agent`
Назначение:
- единственная внешняя точка входа в ADK-приложение;
- кодовая оркестрация цепочки;
- передача state между агентами;
- валидация промежуточных JSON-контрактов;
- преобразование результата выбранной ветки в финальный текстовый ответ.

Именно `root_agent`, а не промпты leaf-агентов, управляет порядком вызова этапов и fallback-поведением.

---

## 4. Порядок выполнения цепочки

Порядок вызова фиксированный:

1. `owasp_agent`
2. `dispatcher_agent`
3. один из:
   - `doc_search_agent`
   - `kb_answer_agent`

### Правила выполнения

- если `owasp_agent` возвращает `status=blocked`, цепочка останавливается;
- если `owasp_agent` возвращает `status=ok`, управление передаётся в `dispatcher_agent`;
- `dispatcher_agent` обязан выбрать ровно один `route`;
- параллельный запуск `doc_search_agent` и `kb_answer_agent` не используется;
- финальный ответ формируется только `root_agent`.

---

## 5. Передача состояния

В текущей реализации `root_agent` использует `ctx.session.state` как рабочее хранилище промежуточных значений.

### Основные ключи state

Общие:
- `user_query` — исходный текст пользователя

Промежуточные raw-output:
- `owasp_result_json`
- `dispatcher_result_json`
- `doc_search_result_json`
- `kb_answer_result_json`

Промежуточные parsed-output:
- `_owasp_result_parsed`
- `_dispatcher_result_parsed`
- `_doc_search_result_parsed`
- `_kb_answer_result_parsed`

Маршрутизация:
- `search_query`
- `intent`
- `doc_search_collection`
- `kb_answer_collection`

Финальный ответ:
- `_root_final_text`

Дополнительно в ветке `kb_answer` профиль пользователя из `ctx.user.state` распаковывается в `ctx.session.state`, чтобы промпт агента мог ссылаться на пользовательские атрибуты напрямую.

---

## 6. Модель

Все leaf-агенты используют общую модель, создаваемую фабрикой:

```python
build_common_model()
```

Фабрика строит `LiteLlm` на основании переменных окружения:
- `LLM_API_MODEL`
- `LLM_API_KEY`
- `LLM_API_URL`

Это исключает дублирование конфигурации модели в каждом агенте.

---

## 7. Промпты

Промпты загружаются через helper `load_prompt(...)`.

### Текущая схема размещения
Путь формируется как:

```text
PROMPTS_DIR / <agent_name> / <filename>
```

Пример:
```text
<AGENT_PROMPTS_DIR>/dispatcher/dispatcher_agent_prompt.md
<AGENT_PROMPTS_DIR>/doc_search/doc_search_agent_prompt.md
<AGENT_PROMPTS_DIR>/kb_answer/kb_answer_agent_prompt.md
<AGENT_PROMPTS_DIR>/owasp/owasp_agent_prompt.md
```

### Особенности
- если файл не найден, используется встроенный fallback prompt;
- для каждого агента запускается polling watcher;
- при изменении prompt-файла instruction агента обновляется без перезапуска процесса.

### Требования к промптам
Каждый промпт должен:
- описывать только роль своего агента;
- не дублировать orchestration logic;
- требовать возврат JSON без markdown;
- не смешивать свою роль с поведением соседних агентов.

---

## 8. Tools и MCP

### 8.1. Общий принцип

Набор tools задаётся отдельно для каждого агента.

### `owasp_agent`
Tools:
- отсутствуют

### `dispatcher_agent`
Tools:
- отсутствуют

### `doc_search_agent`
Tools:
- `kb_search` через `McpToolset`

Источник подключения:
- `KBSEARCH_MCP_URL`
- optional Bearer token через `MCP_TOKEN`
- timeout через `MCP_TIMEOUT_SEC`

### `kb_answer_agent`
Tools:
- `kb_search` через `McpToolset`

Источник подключения:
- `KBSEARCH_MCP_URL`
- optional Bearer token через `MCP_TOKEN`
- timeout через `MCP_TIMEOUT_SEC`

---

### 8.2. Правила вызова `kb_search`

Текущая реализация предполагает:

Для `doc_search_agent`:
- использовать `query={search_query}`;
- использовать `collection={doc_search_collection}`;
- передавать `include_metadata=true`.

Для `kb_answer_agent`:
- при `intent=kb_answer` использовать `query={search_query}`;
- использовать `collection={kb_answer_collection}`;
- передавать `include_metadata=true`;
- при `intent=smalltalk` tool не вызывать.

---

## 9. Коллекции

Имена коллекций задаются через конфигурацию:

- `ACTIVE_DOCUMENTS_COLLECTION`  
  значение по умолчанию: `kb`

- `KB_DOCUMENTS_COLLECTION`  
  значение по умолчанию: `knowledge_base`

На уровне `root_agent`:
- `doc_search_agent` работает с `doc_collection`;
- `kb_answer_agent` работает с `kb_collection`.

---

## 10. Route и intent

В реализации разделяются:

### `route`
Технический маршрут выполнения.

Допустимые значения:
- `doc_search`
- `kb_answer`

### `intent`
Смысловой тип пользовательского запроса.

Допустимые значения:
- `doc_search`
- `kb_answer`
- `smalltalk`

### Правила
- `dispatcher_agent` всегда возвращает `route`;
- `dispatcher_agent` всегда возвращает `intent`;
- `smalltalk` не является отдельной веткой выполнения;
- `smalltalk` маршрутизируется в `kb_answer`;
- ветка `reject` используется только на этапе `owasp_agent`.

---

## 11. Контракты агентов

### 11.1. Контракт `owasp_agent`

#### Safe
```json
{
  "status": "ok",
  "route": "continue",
  "reason": "safe"
}
```

#### Blocked
```json
{
  "status": "blocked",
  "route": "reject",
  "reason": "prompt_injection",
  "user_message": "Запрос отклонён по соображениям безопасности."
}
```

#### Правила
- `status` обязателен;
- `route` обязателен;
- `reason` допускается пустым, но логически должен быть задан;
- `user_message` обязателен при `status=blocked`.

---

### 11.2. Контракт `dispatcher_agent`

#### Поиск документов
```json
{
  "status": "ok",
  "route": "doc_search",
  "intent": "doc_search",
  "reason": "user asks to find documents",
  "search_query": "продукт Fort Knox"
}
```

#### Ответ по базе знаний
```json
{
  "status": "ok",
  "route": "kb_answer",
  "intent": "kb_answer",
  "reason": "user asks an informational question",
  "search_query": "что такое накопительное страхование жизни"
}
```

#### Smalltalk
```json
{
  "status": "ok",
  "route": "kb_answer",
  "intent": "smalltalk",
  "reason": "user greeting or general small talk",
  "search_query": ""
}
```

#### Правила
- `status` должен быть `ok`;
- `route` должен быть одним из `doc_search|kb_answer`;
- `intent` должен быть одним из `doc_search|kb_answer|smalltalk`;
- `reason` обязателен;
- `search_query` обязателен для `doc_search` и `kb_answer`;
- для `smalltalk` допускается пустой `search_query`.

---

### 11.3. Контракт `doc_search_agent`

Текущая реализация использует упрощённый контракт без массива `results`.

```json
{
  "status": "ok",
  "mode": "document_list",
  "message": "**Найденные файлы:**\n1. **Имя файла** — краткий комментарий."
}
```

#### Допустимые mode
- `document_list`
- `no_data`
- `info`
- `app_command`

#### Правила
- `status` должен быть `ok`;
- `mode` обязателен;
- `message` обязателен;
- структурированный массив найденных документов в текущем контракте не используется;
- ответственность за формат списка документов находится внутри `message`.

---

### 11.4. Контракт `kb_answer_agent`

```json
{
  "status": "ok",
  "mode": "text_answer",
  "message": "Краткий ответ пользователю"
}
```

#### Допустимые mode
- `text_answer`
- `no_data`

#### Правила
- `status` должен быть `ok`;
- `mode` обязателен;
- `message` обязателен.

---

## 12. Финальный результат наружу

В текущей реализации наружу из `root_agent` возвращается не JSON-контракт, а финальный текстовый `Event` ADK.

То есть внешний потребитель получает уже готовый пользовательский текст:
- сообщение блокировки;
- текстовый ответ по базе знаний;
- текст сообщения со списком найденных документов.

### Следствие
Telegram-бот на текущем этапе ориентируется на финальный текст ответа, а не на унифицированный JSON-контракт вида `mode/message/results`.

Это важно, потому что исходный проектный документ предполагал единый JSON-контракт для бота, но фактическая реализация сейчас работает иначе.

---

## 13. Валидация

После выполнения каждого leaf-агента `root_agent`:
1. забирает raw-output из `ctx.session.state[output_key]`;
2. извлекает JSON через `extract_json(...)`;
3. валидирует результат функцией соответствующего валидатора;
4. сохраняет parsed-объект обратно в `ctx.session.state`.

Используются валидаторы:
- `validate_owasp_result(...)`
- `validate_dispatcher_result(...)`
- `validate_doc_search_result(...)`
- `validate_kb_answer_result(...)`

---

## 14. Ошибки и fallback-поведение

### Ошибки этапа
Если агент вернул:
- невалидный JSON;
- JSON без обязательных полей;
- недопустимое значение `status`, `route`, `intent` или `mode`;

то это считается ошибкой этапа и выбрасывается exception в `root_agent`.

### Ошибки на уровне оркестратора
Любое необработанное исключение в `root_agent` приводит к возврату безопасного сообщения:

```text
Произошла ошибка при обработке запроса. Попробуйте позже.
```

Если включён `DEBUG_EXCEPTIONS=true`, наружу может быть возвращено debug-сообщение с типом и текстом ошибки.

### Блокировка безопасности
Если `owasp_agent` возвращает `status=blocked`, `root_agent` сразу завершает цепочку и отдаёт `user_message` пользователю.

---

## 15. Логирование

Логирование реализовано на нескольких уровнях:
- `agent_chain`
- `root_agent`
- отдельные логгеры leaf-агентов
- логгер helper/watcher-компонентов

В логах фиксируются:
- входной пользовательский текст;
- raw JSON-ответы leaf-агентов;
- parsed JSON после валидации;
- выбранный `route` и `intent`;
- используемый поисковый запрос;
- ошибки исполнения и stack trace.

---

## 16. Ограничения текущей реализации

На текущем этапе нужно учитывать следующие ограничения:

1. `doc_search_agent` не возвращает структурированный `results[]`; список документов инкапсулирован в `message`.
2. финальный наружный контракт для Telegram-бота не унифицирован как JSON.
3. `root_agent` нормализует выход leaf-веток до простого текстового ответа.
4. поведение hot-reload промптов основано на polling watcher и работает на уровне изменения `agent.instruction`.
5. smalltalk не вынесен в отдельного агента, а обрабатывается внутри `kb_answer_agent`.

---

## 17. Итог

Текущая реализация соответствует базовой идее цепочки специализированных агентов:
- безопасность вынесена отдельно;
- маршрутизация вынесена отдельно;
- поиск документов и ответы по базе знаний разделены;
- оркестрация выполняется кодом;
- промежуточные результаты валидируются.

При этом фактическая реализация упростила внешний контракт:
- внутри цепочки используется JSON между агентами;
- наружу возвращается финальный текст от `root_agent`.

Именно это состояние архитектуры следует считать актуальным для текущего release notes.
