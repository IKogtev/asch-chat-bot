# Переменные окружения web-bff в Kubernetes

Значения для overlay вроде `yc-sandbox-2-chatbot-dev`. На test1/prod те же внутрикластерные URL и пути; отличаются CORS и публичные hostname.


PVC комплектов (не env): `kb-shared-rwx` → `mountPath: /app/kb_storage/manager/kb`, `subPath: manager/kb`, `readOnly: true`.

`WEB_BFF_COOKIE_DOMAIN` в коде ещё нет. Для двух hostname (UI и BFF) его нужно будет добавить (`.sandbox-2.wwwnstcloud.ru` / `.nstcloud.ru`).

## web-bff

| Название переменной | Значение | Комментарий |
|---|---|---|
| `DATABASE_URL` | `postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@$(POSTGRES_URL):$(POSTGRES_PORT)/$(POSTGRES_DB)` | Собрать как у chatbot из `chatbot-postgres-secrets` / `chatbot-postgres-config`. |
| `ADK_API_BASE` | `http://adk-agent:8000` | Внутрикластерный Service агента. |
| `ADK_APP_NAME` | `agent` | Как у бота. |
| `ADK_TIMEOUT_SEC` | `180` | Как `chatbot-config`. |
| `KB_MANAGER_URL` | `http://kb-manager:5000` | Внутрикластерный Service. |
| `KB_MANAGER_TOKEN` | из secret kb-manager | Тот же токен, что у kb-manager; скачивание файлов по id. |
| `BOT_TELEGRAM_API` | `http://chatbot:8001` | Не `http://bot:8001` из compose. OTP в Telegram. |
| `BOT_MAX_API` | `http://chatbot-max:8002` | Не `http://bot-max:8002`. OTP в MAX. |
| `OTP_INTERNAL_SECRET` | общий секрет (не в git) | Один ключ с chatbot и chatbot-max. |
| `WEB_BFF_OTP_SECRET` | случайная строка (secret) | Хеш OTP в БД, не дефолт `dev-otp-secret-change-me`. |
| `WEB_BFF_FILE_SECRET` | случайная строка (secret) | Подпись `/files/...`. |
| `WEB_BFF_HOST` | `0.0.0.0` | Слушать все интерфейсы пода. |
| `WEB_BFF_PORT` | `8010` | Порт контейнера / Service / probe. |
| `WEB_BFF_OTP_STUB` | `false` | В кластере реальная отправка в мессенджеры. |
| `WEB_BFF_ALLOW_DEV_AUTH` | `false` | Не оставлять `X-User-Id` в prod/test. |
| `WEB_BFF_COOKIE_SECURE` | `true` | HTTPS. |
| `WEB_BFF_COOKIE_NAME` | `nastya_web` | Можно не задавать, это дефолт. |
| `WEB_BFF_CORS_ORIGINS` | например, `https://web-ui-chatbot-dev.sandbox-2.wwwnstcloud.ru` | Origin **UI**. Несколько — через запятую без пробелов. |
| `WEB_BFF_SESSION_TTL_SEC` | `604800` | 7 дней; можно опустить (дефолт). |
| `WEB_BFF_OTP_TTL_SEC` | `600` | время жизни кода для авторизации Можно опустить. |
| `WEB_BFF_OTP_MAX_ATTEMPTS` | `5` | Можно опустить. |
| `WEB_BFF_OTP_MAX_REQUESTS` | `5` | Можно опустить. |
| `WEB_BFF_OTP_REQUEST_WINDOW_SEC` | `600` | Можно опустить. |
| `WEB_BFF_SUGGESTIONS` | `false`  | Блок подсказок «Карточка/Комплект» в `blocks`. пока отключить|
| `PRODUCT_KITS_ROOT` | `/app/kb_storage/manager/kb/1 Продукты` | Только путь (пробел + кириллица). Как в `chatbot-config`. Нужен mount PVC. |
| `ARCHIVE_KITS_ROOT` | `/app/kb_storage/manager/kb/6 Архив` | То же для архивных комплектов. |
| `DOWNLOADS_DIR` | `/app/data/upload/web_files` | Локальный кэш kb-manager, не PVC. `emptyDir`. |
| `SHOW_LIST_SIZE` | `5` | Пагинация документов (`bot.services.config`). |
| `SHOW_BY_PAGE` | `False` | Как у бота. |
| `WEB_BFF_OTP_STUB_PHONE` | не задавать | Только локальный stub. |
| `WEB_BFF_OTP_STUB_CODE` | не задавать | Только локальный stub. |

## chatbot и chatbot-max

| Название переменной | Значение | Комментарий |
|---|---|---|
| `OTP_INTERNAL_SECRET` | тот же, что у BFF | Иначе `/internal/otp` не примет запрос. Сейчас в overlay ботов нет. |

## web-ui

| Название переменной | Значение | Комментарий |
|---|---|---|
| публичный URL BFF | `https://web-bff-chatbot-dev.sandbox-2.wwwnstcloud.ru` | Имя зависит от фронта (`NEXT_PUBLIC_*` или runtime). test1: `https://web-bff-chatbot-test1.sandbox-2.wwwnstcloud.ru`; prod: `https://web-bff-chatbot-prod.nstcloud.ru`. |
