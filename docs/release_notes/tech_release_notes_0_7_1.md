# Технические release notes 0.7.1

Документ для DevOps-обновления с 0.6.9 на 0.7.1.

Важно: целевая 0.6.9 в текущем контуре считается основанной на 0.6.6, поэтому при обновлении нужно учитывать изменения, накопленные после 0.6.6.

## Что меняется при обновлении

1. Версия платформы обновляется до `0.7.1`; для `docker-compose` значение `PLATFORM_VERSION=0.7.1` задано у `bot`, `bot-max` и `kb-manager`.
2. Добавлен новый агент `product_selection_agent` для подбора, фильтрации, сравнения и объяснения продуктов.
3. В dispatcher добавлен маршрут `product_selection`; продуктовые запросы теперь могут уходить в нового агента.
4. Добавлен новый MCP-сервис `dbhub` для read-only доступа агента к табличным данным PostgreSQL.
5. Добавлена новая база данных `nstya_data` для продуктовых таблиц и data catalog.
6. База `nstya_data` создается на той же СУБД PostgreSQL, где находится основная база приложения `aszh-bot`; отдельный PostgreSQL-инстанс не нужен.
7. Добавлен конфиг `mcps/dbhub/dbhub_nstya_config.toml`; его нужно примонтировать в контейнер `dbhub`.
8. `dbhub` должен быть доступен агенту по MCP endpoint `http://dbhub:8080/mcp` или по аналогичному service DNS в Kubernetes.
9. Для агента и ADK Web нужны переменные `DBHUB_MCP_URL`, `DBHUB_MCP_TOKEN`, `DBHUB_MCP_TIMEOUT_SEC`.
10. Для `kb-manager` нужны переменные `NSTYA_DATA_URL`, `NSTYA_DATA_SOURCE_DIR`, `PRODUCT_KITS_ROOT`.
11. Для ботов нужны `PRODUCT_KITS_ROOT`, `PRODUCT_KITS_MAX_FILES`, `PRODUCT_KITS_MAX_FILE_SIZE_MB` при отклонении от дефолтов.
12. Добавлен скрипт `load_tables.ps1`, который запускает загрузчик таблиц внутри контейнера `kb-manager`.
13. Загрузчик таблиц при старте проверяет наличие целевой базы из `NSTYA_DATA_URL`; если базы нет, подключается к служебной базе `postgres` на том же host/port/user и выполняет `CREATE DATABASE`.
14. Загрузчик пересоздает пользовательские таблицы в схеме `public` базы `nstya_data`; это штатная полная перезагрузка продуктовых таблиц.
15. Обрабатываются только Excel-файлы с суффиксом `_active.xlsx`; суффикс удаляется при формировании имени таблицы.
16. Обязателен файл `business layer_active.xlsx`; из него создаются таблицы `dc_entities`, `dc_columns`, `dc_analytics`, `dc_semantic_templates`.
17. В обычных Excel-таблицах поддержана вторая строка-комментарий: если первая ячейка первой строки данных содержит `#`, строка пропускается.
18. При загрузке таблицы `products` добавляются поля `folder_kit` и `folder_kit_status`; они рассчитываются по папкам комплектов продуктов внутри `PRODUCT_KITS_ROOT`.
19. Боты умеют отправлять комплект документов продукта по структурному действию агента `send_product_kit`.
20. Для комплектов продуктов бот отдает только файлы непосредственно из найденной папки, без обхода вложенных каталогов; служебные и скрытые файлы пропускаются.
21. Добавлена обертка для MCP toolset с пересозданием сессии при типовых ошибках закрытого или протухшего MCP-соединения.
22. Обновлен поиск по базе знаний и FAQ: добавлен общий hybrid/RRF-поиск, retry-параметры подключения к Qdrant и отдельные настройки коллекций.
23. Для `mcp-server-kbsearch` добавлены параметры `KB_RRF_K_DOC_SEARCH`, `KB_CANDIDATE_MULT_DOC_SEARCH`, `DOC_SEARCH_ARCHIVE_SECTION`, `KB_QDRANT_RETRY_INTERVAL`, `KB_QDRANT_INIT_TIMEOUT`.
24. Для `mcp-server-faq` добавлены параметры `FAQ_QDRANT_RETRY_INTERVAL`, `FAQ_QDRANT_INIT_TIMEOUT`.
25. Коллекции документов в compose зафиксированы явно: `kb_collection`, `knowledge_base_collection`, `faq_collection`.
26. Добавлены настройки сжатия контекста агента: `AGENT_CONTEXT_COMPACTION_INTERVAL`, `AGENT_CONTEXT_COMPACTION_OVERLAP_SIZE`, `AGENT_CONTEXT_TOKEN_THRESHOLD`, `AGENT_CONTEXT_EVENT_RETENTION_SIZE`.
27. Добавлены prompt-файлы для `product_selection`; при развертывании нужно сохранить монтирование `kb_storage/prompts`.
28. Добавлена таблица `events` через Alembic-миграцию; таблица больше не создается автоматически в runtime.
29. Добавлены изменения глобальных пользователей, блокировки/разблокировки, MAX-бота и экспорта диалогов из 0.6.6-hf/0.6.9-линии; при обновлении с 0.6.9 нужно сверить, какие миграции уже были применены в конкретной базе.

## Новые и измененные сервисы

### `dbhub`

Новый сервис:

- образ: `bytebase/dbhub:latest`;
- порт внутри сети: `8080`;
- транспорт: `http`;
- конфиг: `/config/dbhub_nstya_config.toml`;
- источник данных: PostgreSQL DSN на базу `nstya_data`.

В `docker-compose` конфиг монтируется так:

```yaml
./mcps/dbhub/dbhub_nstya_config.toml:/config/dbhub_nstya_config.toml:ro
```

Для Kubernetes нужно добавить эквивалентный Deployment/Service/ConfigMap или другой штатный способ запуска `dbhub` и открыть доступ к нему из `adk-agent` и `adk-web`.

### `kb-manager`

`kb-manager` теперь используется не только для UI/индексации, но и для загрузки продуктовых таблиц.

Обязательные env:

```text
NSTYA_DATA_URL=postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data
NSTYA_DATA_SOURCE_DIR=/app/data/kb_documents/tables
PRODUCT_KITS_ROOT=/app/data/kb_documents/kb/1 Продукты
```

`NSTYA_DATA_URL` должен указывать на ту же PostgreSQL СУБД, что и `DATABASE_URL`, но на отдельную базу `nstya_data`.

### `bot` и `bot-max`

Для отправки комплектов продуктов контейнерам ботов нужен read-only mount с продуктовой папкой и переменная:

```text
PRODUCT_KITS_ROOT=/app/kb_storage/kb/1 Продукты
```

В `docker-compose` добавлено монтирование:

```yaml
./kb_storage/kb:/app/kb_storage/kb:ro
```

## Данные для `load_tables.ps1`

Перед запуском нужно подготовить каталог `NSTYA_DATA_SOURCE_DIR`.

Минимальный набор:

1. `business layer_active.xlsx` с листами:
   - `business entities`;
   - `columns`;
   - `analytics`;
   - `semantic_templates`.
2. Продуктовые таблицы с именами вида `<table_name>_active.xlsx`.
3. Для таблицы продуктов желательно имя `products_active.xlsx`, чтобы загрузчик создал таблицу `products` и рассчитал `folder_kit`.
4. Папки комплектов продуктов должны лежать внутри `PRODUCT_KITS_ROOT`.

Имена таблиц формируются из имени файла без `_active`; если в книге несколько листов, имя таблицы дополняется именем листа.

## Логика запуска `load_tables.ps1`

Скрипт находится в корне репозитория:

```powershell
.\load_tables.ps1
```

Что делает скрипт:

1. Переходит в корень проекта.
2. Проверяет, что сервис `kb-manager` уже запущен через `docker compose`.
3. Выполняет внутри контейнера:

```powershell
docker compose exec -T kb-manager python -m app.scripts.load_tables
```

4. Возвращает ошибку, если контейнер не запущен или загрузчик завершился с ненулевым кодом.

Что делает Python-загрузчик:

1. Берет каталог Excel-файлов из `NSTYA_DATA_SOURCE_DIR`.
2. Берет DSN целевой базы из `NSTYA_DATA_URL`.
3. Извлекает имя базы из DSN, например `nstya_data`.
4. Подключается к служебной базе `postgres` на том же host/port/user, что указаны в `NSTYA_DATA_URL`.
5. Если базы `nstya_data` нет, создает ее командой `CREATE DATABASE`.
6. Подключается к `nstya_data`.
7. Удаляет все пользовательские таблицы из схемы `public`.
8. Загружает активные Excel-файлы и data catalog.
9. Создает индексы для таблиц data catalog.
10. Валидирует согласованность data catalog и выводит предупреждения.

Требование к правам: пользователь из `NSTYA_DATA_URL` должен иметь право подключаться к служебной базе `postgres`, создавать базу данных и создавать/удалять таблицы в `nstya_data`.

## Минимальный порядок обновления с 0.6.9 до 0.7.1

1. Сделать backup основной базы `aszh-bot`, volume PostgreSQL, Qdrant и файлового хранилища `kb_storage`.
2. Обновить образы и конфигурацию сервисов до версии 0.7.1.
3. Убедиться, что `PLATFORM_VERSION` обновлен до `0.7.1` во всех целевых configmap/env.
4. Добавить или проверить сервис `dbhub` и его конфиг `dbhub_nstya_config.toml`.
5. Добавить env для `DBHUB_MCP_*` в `adk-agent` и `adk-web`.
6. Добавить env `NSTYA_DATA_URL`, `NSTYA_DATA_SOURCE_DIR`, `PRODUCT_KITS_ROOT` в `kb-manager`.
7. Добавить `PRODUCT_KITS_ROOT` и mount продуктовых файлов в `bot` и `bot-max`.
8. Запустить PostgreSQL.
9. Применить Alembic-миграции основной базы, включая миграцию для `events` и миграции глобальных пользователей, если они еще не применены в текущей 0.6.9 базе.
10. Запустить `kb-manager`.
11. Подготовить Excel-файлы в `NSTYA_DATA_SOURCE_DIR`.
12. Запустить загрузку таблиц:

```powershell
.\load_tables.ps1
```

13. Проверить, что в PostgreSQL появилась база `nstya_data` и таблицы `products`, `dc_entities`, `dc_columns`, `dc_analytics`, `dc_semantic_templates`.
14. Запустить `dbhub` и проверить доступность MCP endpoint `/mcp`.
15. Перезапустить `adk-agent` и `adk-web`, чтобы они подхватили prompt-файлы и MCP-настройки.
16. Перезапустить `bot` и `bot-max`.
17. Проверить сценарии: обычный вопрос, поиск по KB, FAQ, продуктовый подбор, отправка комплекта документов продукта.

## Особые замечания

1. `load_tables.ps1` рассчитан на `docker compose`. В Kubernetes нужно выполнить тот же модуль внутри pod `kb-manager`, например через `kubectl exec`, с теми же env и примонтированными данными.
2. `load_tables.py` полностью пересоздает таблицы в `nstya_data.public`; не запускайте его, если в этой базе есть ручные таблицы, которые нужно сохранить.
3. Если `dbhub` стартует раньше загрузки таблиц, он может быть доступен, но продуктовые запросы будут неполными до успешного запуска `load_tables`.
4. Если `PRODUCT_KITS_ROOT` пустой или не содержит папки продуктов, загрузчик все равно загрузит таблицу `products`, но `folder_kit` будет заполнен статусом `не найдена` или диагностикой.
5. При изменении структуры папок комплектов продуктов нужно повторно запускать `load_tables`, чтобы пересчитать `folder_kit`.
6. В Kubernetes-манифестах текущей ветки могут оставаться старые значения `PLATFORM_VERSION` в configmap; перед релизом их нужно обновить до `0.7.1`.
