# Перенос Google ADK sessions в PostgreSQL

## Цель

Перевести хранилище сессий Google ADK с текущего локального/in-memory режима на отдельную базу PostgreSQL в промышленном кластере.

После переноса `adk-agent` и `adk-web` должны использовать один общий session backend. Сессии, состояние и события ADK должны сохраняться после рестарта pod'ов.

## Что нужно подготовить

### 1. Отдельная база данных

Создать отдельную базу для ADK-сессий:

```text
adk_sessions
```

Не использовать основную базу приложения. ADK самостоятельно создает и использует свои таблицы, поэтому лучше изолировать lifecycle, backup, очистку и диагностику.

### 2. Отдельный пользователь

Создать отдельного пользователя, например:

```text
adk_sessions_user
```

Минимальные права:

- подключение к базе `adk_sessions`;
- создание объектов в схеме `public`;
- чтение и запись таблиц ADK;
- удаление таблиц/схемы при запуске подготовительного reset-скрипта, если он используется.

Для подготовительного скрипта создания БД пользователь также должен иметь возможность подключаться к служебной базе `postgres` и выполнять `CREATE DATABASE`.

### 3. Write endpoint PostgreSQL-кластера

ADK-сессии требуют запись. DSN должен указывать на стабильную write-точку PostgreSQL-кластера:

```text
postgresql+asyncpg://adk_sessions_user:<password>@<postgres-write-endpoint>:5432/adk_sessions
```

Нельзя указывать:

- read-replica endpoint;
- hostname конкретного pod'а/instance;
- балансировщик, который может отправить write-запрос на replica.

### 4. Два PostgreSQL-кластера

Если в production используются два PostgreSQL-кластера, для ADK должен быть один active-primary write endpoint.

Допустимый режим:

```text
active primary -> ADK writes
standby/DR cluster -> replication/backup/failover
```

Недопустимый режим:

```text
ADK writes -> cluster A primary
ADK writes -> cluster B primary
```

Active/active запись в два независимых primary для ADK-сессий использовать нельзя: состояние `sessions`, `events` и `state` может разойтись.

## Подготовка БД

В репозитории добавлен скрипт:

```text
python -m scripts.adk_session_db
```

Он берет DSN из переменной:

```text
ADK_SESSION_SERVICE_URI
```

Режимы:

```sh
python -m scripts.adk_session_db
```

Создает базу, если ее нет.

```sh
python -m scripts.adk_session_db --reset-existing
```

Создает базу, если ее нет. Если база уже существует, очищает схему `public` через `DROP SCHEMA public CASCADE` и затем создает ее заново.

Этот reset удаляет существующие ADK-сессии. Это ожидаемое поведение для текущего переноса: сохранение сессий между версиями не требуется.

## Переключение ADK

Оба процесса должны получить один и тот же DSN:

```text
ADK_SESSION_SERVICE_URI=postgresql+asyncpg://adk_sessions_user:<password>@<postgres-write-endpoint>:5432/adk_sessions
```

Запуск ADK должен использовать параметр:

```sh
--session_service_uri "$ADK_SESSION_SERVICE_URI"
```

Это относится и к `adk-agent`, и к `adk-web`.

## Проверки после переключения

Проверить:

1. ADK создает таблицы в базе `adk_sessions`.
2. Новый диалог создает session/events в PostgreSQL.
3. После рестарта ADK можно продолжить ту же сессию.
4. `adk-agent` и `adk-web` видят одну и ту же историю.
5. После failover write endpoint снова принимает подключения.

Во время failover активный ADK run может завершиться ошибкой. Это допустимо, если следующий запрос после восстановления writer endpoint работает корректно.

## Что не входит в этот перенос

Не требуется переносить старые SQLite/in-memory сессии.

Не требуется настраивать retention в рамках этой задачи. Но для эксплуатации нужно отдельно определить правила очистки старых ADK events, мониторинг размера таблиц и backup/restore.
