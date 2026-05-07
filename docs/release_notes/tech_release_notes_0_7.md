# Технические release notes 0.7

Изменения по сравнению с релизом 0.6.6:

- Добавили нового агента `product_selection_agent` для сценариев подбора, фильтрации, сравнения, рекомендаций и объяснения продуктов.
- Добавили новый MCP `dbhub` для read-only доступа агента к PostgreSQL через инструменты `search_table`, `search_column`, `search_analytic`, `search_semantic_template`, `search_objects` и `execute_sql`.
- Добавили новую базу данных `nstya_data` в PostgreSQL для табличных продуктовых данных и data catalog.
- Добавили конфигурацию `mcps/dbhub/dbhub_nstya_config.toml`; при разворачивании нужно примонтировать ее в контейнер `dbhub`.
- Добавили сервис `dbhub` в `docker-compose.yaml`; он зависит от `postgres` и публикует MCP endpoint `http://dbhub:8080/mcp`.
- Добавили переменные окружения для подключения агента к `dbhub`: `DBHUB_MCP_URL`, `DBHUB_MCP_TOKEN`, `DBHUB_MCP_TIMEOUT_SEC`.
- Добавили загрузчик таблиц в `nstya_data`: скрипт `load_tables.ps1` запускает `python -m app.scripts.load_tables` внутри контейнера `kb-manager`.
- Добавили переменные окружения `kb-manager` для загрузчика таблиц: `NSTYA_DATA_URL` и `NSTYA_DATA_SOURCE_DIR`.
- Добавили правило загрузки Excel-файлов: обрабатываются только файлы с суффиксом `_active.xlsx`; суффикс удаляется при формировании имени таблицы.
- Добавили обязательный файл data catalog `business layer_active.xlsx`; из него создаются таблицы `dc_entities`, `dc_columns`, `dc_analytics`, `dc_semantic_templates`.
- Добавили поддержку второй строки-комментария в Excel-файлах: если первая ячейка строки содержит `#`, строка пропускается при загрузке.
- Добавили Alembic-миграцию `b2c3d4e5f6a7_add_events_table.py` для таблицы `events`; перед запуском новой версии нужно применить миграции.
- Добавили ORM-модель `LoggedEvent`; создание таблицы `events` больше не выполняется автоматически в runtime через `EventLogger`.
- Добавили настройки сжатия контекста агента: `AGENT_CONTEXT_COMPACTION_INTERVAL`, `AGENT_CONTEXT_COMPACTION_OVERLAP_SIZE`, `AGENT_CONTEXT_TOKEN_THRESHOLD`, `AGENT_CONTEXT_EVENT_RETENTION_SIZE`.
- Добавили prompt-файл `kb_storage/prompts/product_selection/product_selection_agent_prompt.md`; при разворачивании нужно сохранить монтирование `kb_storage/prompts` в контейнер агента.
- Обновили dispatcher: появился маршрут `product_selection`, который направляет продуктовые запросы в нового агента.
- Обновили экспорт диалогов в `kb-manager`: выгрузка теперь связывает сообщения, ответы и скачанные файлы по `turn_id`, а время ответа выводится в секундах.
- Добавили вспомогательный скрипт `tests/create_session_adk_web.ps1` для создания сессии через ADK Web API.
- Добавили и обновили unit-тесты для нового агента, маршрутизации, сжатия контекста и загрузчика таблиц.

Минимальный порядок действий при обновлении:

- Обновить образы и конфигурацию сервисов до версии 0.7.
- Проверить, что в окружении заданы `DBHUB_MCP_URL`, `DBHUB_MCP_TOKEN`, `DBHUB_MCP_TIMEOUT_SEC`, `NSTYA_DATA_URL`, `NSTYA_DATA_SOURCE_DIR`.
- Запустить PostgreSQL и применить Alembic-миграции, включая создание таблицы `events`.
- Запустить сервис `dbhub` и проверить доступность MCP endpoint `/mcp`.
- Подготовить Excel-файлы в каталоге `NSTYA_DATA_SOURCE_DIR`: продуктовые таблицы должны иметь суффикс `_active.xlsx`, data catalog должен называться `business layer_active.xlsx`.
- Загрузить таблицы в новую базу командой `.\load_tables.ps1`.
- Перезапустить `adk-agent` и `adk-web`, чтобы агент подхватил новый prompt и MCP-настройки.
