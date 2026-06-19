# Release notes: глоссарий терминов в цепочке агентов

**Дата:** 2026-06-17  
**Область:** `agent/glossary.py`, `agent/rootagent.py`, промпты агентов, загрузка таблиц через kb-manager.

---

## Кратко

1. **Глоссарий** хранится в PostgreSQL (таблица `glossary`), загружается из Excel `glossary_active.xlsx` через kb-manager.
2. **`GlossaryLookup`** в начале каждого turn находит термины в сообщении пользователя и, по маршруту, **расширяет поисковые запросы в коде** (не только через LLM).
3. В state попадают **два параллельных канала**:
   - `from_glossary` — справочник `[term, definition, category]` для промптов;
   - расширенные строки запроса — `search_query`, `product_selection_search_query`, `doc_search_query`.
4. Категории **`продукт`**, **`сокращение`**, **`термин`** задают разное поведение: замена, дополнение или только контекст для агента.

---

## 1. Источник данных

| Компонент | Путь / имя |
|-----------|------------|
| Excel (источник) | `kb_storage/manager/glossary/glossary_active.xlsx` (в Docker: `GLOSSARY_SOURCE_DIR`, по умолчанию `/app/data/kb_documents/manager/glossary`) |
| Загрузчик | `mcps/kb-manager/app/scripts/load_tables.py` → `TablesLoaderService._load_glossary` |
| Таблица БД | `glossary` в PostgreSQL (`NSTYA_DATA_URL`) |

### Колонки Excel → PostgreSQL

| Поле в БД | Кандидаты заголовков в Excel |
|-----------|------------------------------|
| `term` | `term`, `сокращение` |
| `definition` | `definition`, `определение` |
| `aliases_normalized` | `aliases`, `синонимы` (разделители `;` или `,`) |
| `category` | `category`, `категория` |
| `term_normalized` | вычисляется при загрузке |

После правок в Excel нужно выполнить загрузку таблиц в kb-manager («Загрузить таблицы») и дождаться обновления кэша агента (см. §6).

---

## 2. Категории и правила подстановки

Логика реализована в `build_glossary_expanded_query()` (`agent/glossary.py`).

| Категория | Подстановка в поисковый запрос | Пример |
|-----------|--------------------------------|--------|
| **`продукт`** | **Замена** совпавшего фрагмента на `definition` | `ФК` → `Fort Knox`; `Форт Нокс` → `Fort Knox` |
| **`сокращение`** | **Дополнение**: после совпадения добавляется `definition` (сам термин сохраняется) | `НСЖ` → `НСЖ накопительное страхование жизни` |
| **`термин`** | В запрос **не подставляется**; только в `from_glossary` для интерпретации LLM | `фокус` остаётся в тексте, агент использует definition «материалы в фокусе АСЖ» |

### Общие правила поиска совпадений

- Совпадение по **границам слов** (не внутри других слов: `кафнедра` ≠ `ФН`).
- Нормализация: нижний регистр, `ё` → `е`, схлопнутые пробелы.
- При расширении запроса учитываются **синонимы** из `aliases_normalized`.
- Более длинные термины обрабатываются первыми; перекрывающиеся span'ы не дублируются.
- Для `сокращение`: если расшифровка уже стоит сразу после термина, повторно не добавляется.

---

## 3. Класс `GlossaryLookup`

**Файл:** `agent/glossary.py`  
**Подключение:** `RootAgent` создаёт экземпляр при старте (`agent/rootagent.py`).

| Метод | Назначение |
|-------|------------|
| `find(text)` | Найти все категории терминов в тексте → `[[term, definition, category], ...]` |
| `expand_search_query(text)` | Расширить строку по правилам `продукт` / `сокращение` |
| `build_doc_search_query(text)` | Алиас на `expand_search_query` (тот же алгоритм) |

Данные читаются из PostgreSQL с **in-memory кэшем**:

| Переменная окружения | По умолчанию | Смысл |
|---------------------|--------------|-------|
| `NSTYA_DATA_URL` | `postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data` | DSN для загрузки глоссария |
| `GLOSSARY_CACHE_TTL_SEC` | `300` | TTL кэша записей в секундах |

При ошибке подключения к БД `find()` возвращает `[]`, `expand_search_query()` — исходный текст без изменений.

---

## 4. Поток в `RootAgent`

```mermaid
flowchart TD
    A[user_message] --> B[glossary_lookup.find]
    B --> C["from_glossary в state<br/>справочник для промптов"]

    A --> D[dispatcher]
    C --> D
    D --> E["dispatcher.search_query<br/>(нормализация маршрута)"]

    A --> F[build_doc_search_query]
    F --> G["doc_search_query в state → kb_search"]

    E --> H[expand_search_query]
    H --> I["search_query в state → kb_answer"]
    H --> J["product_selection_search_query в state"]

    C --> K[dispatcher / kb_answer / product_selection / doc_search]
    I --> K
    J --> K
    G --> L[doc_search_agent → kb_search]
```

### Порядок выполнения

1. **OWASP-проверка** (если не заблокировано).
2. **`from_glossary`** — всегда, до dispatcher:
   ```python
   ctx.session.state["from_glossary"] = await self.glossary_lookup.find(user_text)
   ```
3. **Dispatcher** — получает `from_glossary` в промпте; формирует `route`, `intent`, `search_query`.
4. **По маршруту** — расширение запроса в коде:

| Маршрут | База для расширения | Поле state | Метод |
|---------|---------------------|------------|-------|
| `doc_search` | `user_message` | `doc_search_query` | `build_doc_search_query(user_message)` |
| `kb_answer` | `dispatcher.search_query` или `user_message` | `search_query` | `expand_search_query(...)` |
| `product_selection` | `dispatcher.search_query` или `user_message` | `product_selection_search_query` | `expand_search_query(...)` |

**Важно:** для `doc_search` расширение делается из **исходного сообщения**, а не из `search_query` dispatcher'а. Для `kb_answer` и `product_selection` — из нормализованного `search_query` dispatcher'а (если пуст — fallback на `user_message`).

---

## 5. Роль агентов и промптов

Глоссарий не дублирует подстановки в LLM: код уже записал расширенные query-поля. Агентам передаётся явное правило **не переписывать** query для `продукт` / `сокращение`.

| Агент | Файл промпта | Query для инструментов | `from_glossary` |
|-------|--------------|------------------------|-----------------|
| dispatcher | `kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md` | формирует `search_query` (без expand в коде) | справочник при выборе route/intent |
| kb_answer | `kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md` | `search_query` (уже расширен) | `термин` → интерпретация `user_query` |
| product_selection | `kb_storage/prompts/product_selection/product_selection_agent_prompt.md` | `product_selection_search_query` (уже расширен) | `термин` → фильтры и формулировки |
| doc_search | `kb_storage/prompts/doc_search/doc_search_agent_prompt.md` | `doc_search_query` (уже расширен) | `термин` → релевантность; не подставлять в query |

Fallback-инструкции в коде агентов (`agent/agents/*_agent.py`) дублируют те же правила по категориям на случай недоступности файла промпта.

---

## 6. Примеры

### Продукт (замена)

| Ввод пользователя | `doc_search_query` / `search_query` |
|-------------------|-----------------------------------|
| `дай документы по ФК` | `дай документы по Fort Knox` |
| `презентеры по Форт Нокс` | `презентеры по Fort Knox` |

### Сокращение (дополнение)

| Ввод | Расширенный запрос |
|------|-------------------|
| `что такое НСЖ?` | `что такое НСЖ накопительное страхование жизни?` |
| `документы по фокусу и ГСС` | `документы по фокусу и ГСС Гарантированная страховая сумма` |

(`фокус` — категория `термин`, в query не меняется; `ГСС` — `сокращение`.)

### Термин (только `from_glossary`)

| Ввод | Query | `from_glossary` |
|------|-------|-----------------|
| `материалы по фокусу` | без изменений | `[["Фокус", "материалы в фокусе АСЖ", "термин"]]` |

---

## 7. Тесты

| Файл | Что покрывает |
|------|----------------|
| `tests/unit/agent/test_glossary.py` | категории, алиасы, границы слов, пустой глоссарий |
| `tests/unit/agent/test_rootagent.py` | `from_glossary` до dispatcher, `expand_search_query` для kb_answer/product_selection, `doc_search_query` |

Запуск:

```bash
pytest tests/unit/agent/test_glossary.py tests/unit/agent/test_rootagent.py -q
```

---

## 8. Эксплуатация

1. Отредактировать `glossary_active.xlsx` (колонки term, definition, aliases, category).
2. В kb-manager выполнить загрузку таблиц (`load_tables.py` или UI «Загрузить таблицы»).
3. Перезапустить ADK-agent **или** дождаться истечения `GLOSSARY_CACHE_TTL_SEC` (по умолчанию 5 минут).

Без таблицы `glossary` в БД агент работает как раньше: пустой `from_glossary`, запросы без расширения.

---

## 9. Связанные файлы

| Назначение | Путь |
|------------|------|
| Логика глоссария | `agent/glossary.py` |
| Оркестрация | `agent/rootagent.py` |
| Загрузка Excel → БД | `mcps/kb-manager/app/services/tables_loader_service.py` |
| Скрипт загрузки | `mcps/kb-manager/app/scripts/load_tables.py` |
| Промпты | `kb_storage/prompts/{dispatcher,kb_answer,product_selection,doc_search}/*_agent_prompt.md` |

---

## 10. Отличие от первой итерации

Ранее использовалась функция `apply_glossary_to_text()`: все найденные термины **заменялись** на `definition` без учёта категории, вызов шёл через уже собранный `from_glossary`.

Сейчас:

- подстановка централизована в `GlossaryLookup.expand_search_query` / `build_doc_search_query`;
- **`продукт`** и **`сокращение`** обрабатываются по-разному;
- **`термин`** не попадает в поисковый query, только в `from_glossary`;
- расширенные строки явно пишутся в отдельные поля state, а не только в справочник для LLM.
