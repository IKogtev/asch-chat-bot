# Тестирование google-adk-agent

Скрипт читает тест-кейсы из Excel, отправляет вопросы в ADK-агента через HTTP API, при наличии ключа к LLM — автоматически оценивает ответы и сохраняет отчет (и при возможности — график).

## Установка python-зависимостей

Из каталога `tester`:

```bash
cd tester
pip install -r requirements.txt
```

## `.env`: переменные окружения

Переменные читаются из окружения и из файла **`tester/.env`** (если он есть — он подгружается поверх общего `.env`).

### Обязательные

| Переменная | Описание |
|------------|----------|
| **`ADK_API_BASE`** | URL сервиса ADK. Пример: `https://adk-agent-chatbot-test1.sandbox-2.wwwnstcloud.ru` или внутри Kubernetes кластера `http://adk-agent:8000`. По этому адресу выполняется проверка доступности (health); если агент недоступен, выполнение также прервется. |
| `TC_TASK_FILE_NAME` | Имя файла Excel с тест-кейсами. Пример: `NSTya base test v1 260410.xlsx` |

### Не обязательны для старта, но нужны для автооценки ответов

| Переменная | Описание |
|------------|----------|
| `LLM_API_KEY` | Ключ к OpenAI-совместимому API "LLM-оценщика". Если пусто, скрипт все равно запустится и задаст вопросы, но блок автооценки будет пропущен (в отчете будут нули и пометка, что оценка не выполнялась). |
| `LLM_API_URL` | URL для API LLM сервера (по умолчанию задан в коде `https://dsrv1.llm.nstcloud.ru/v1`). |
| `LLM_API_MODEL` | Имя модели (по умолчанию задано в коде `Qwen/Qwen3-30B-A3B`). |

### Часто полезные

| Переменная | Описание |
|------------|----------|
| `ADK_APP_NAME` | Имя приложения в ADK (по умолчанию `agent`). |
| `ADK_TIMEOUT_SEC` | Таймаут HTTP-запросов к ADK в секундах (по умолчанию `180`). |
| `ASK_QUESTIONS` | `1` / `true` / `yes` — задавать вопросы агенту; иначе можно работать только с уже сохраненным parquet ответов. |
| `ADK_TEST_USER_ID`, `ADK_TEST_SESSION_ID` | Идентификаторы пользователя и сессии (есть значения по умолчанию). |
| `ADK_TEST_FIRST_NAME` и др. | Поля профиля для `stateDelta` (имя, фамилия, регион и т.д.). |

## Запуск

Из каталога **`tester`** (чтобы пути к Excel и `.env` совпадали с ожиданиями скрипта):

```bash
cd tester
python adk_agent_tester_v1.py
```

С параметрами:

```bash
python adk_agent_tester_v1.py --excel "C:\path\to\cases.xlsx" --out "C:\path\to\reports"
python adk_agent_tester_v1.py --user-id myuser --session-id mysession
python adk_agent_tester_v1.py --fake-first-name "Иван"
```

- **`--excel`** — путь к файлу с тест-кейсами (по умолчанию: `tester/` + имя из `TC_TASK_FILE_NAME` или встроенное в коде имя по умолчанию). Файл должен существовать.
- **`--out`** — каталог для отчетов (по умолчанию каталог `tester`).
- Остальные флаги см. в `python adk_agent_tester_v1.py --help`.

---

# Примеры запросов в adk-agent

## Kubernetes

- Установить переменные окружения
```sh
ADK_AGENT_URL="http://adk-agent.chatbot-test1:8000"
ADK_AGENT_APP="agent"
USER="jenkins-smoke"
SESSION="smoke-$(date +%s)"
FIRST_NAME="Jenkins"
FULL_NAME="Jenkins"
```

- Инициализировать сессию
```sh
curl -sS -X POST "${ADK_AGENT_URL}/apps/${ADK_AGENT_APP}/users/${USER}/sessions/${SESSION}" \
  -H "Content-Type: application/json" \
  -d '{}'
```

- Пример smalltalk
```sh
curl -sS -X POST "${ADK_AGENT_URL}/run" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "app_name": "${ADK_AGENT_APP}",
  "user_id": "${USER}",
  "session_id": "${SESSION}",
  "stateDelta": {
    "first_name":"${FIRST_NAME}",
    "full_name":"${FULL_NAME}"
  },
  "new_message": {
    "role": "user",
    "parts": [{"text": "Привет! Что ты умеешь?"}]
  }
}
EOF
```

- Пример Fort Knox
```sh
curl -sS -X POST "${ADK_AGENT_URL}/run" \
    -H "Content-Type: application/json" \
    -d "{
        \"app_name\": \"${ADK_AGENT_APP}\",
        \"user_id\": \"${USER}\",
        \"session_id\": \"${SESSION}\",
        \"stateDelta\": {
            \"first_name\": \"${FIRST_NAME}\",
            \"full_name\": \"${FULL_NAME}\"
        },
        \"new_message\": {
            \"role\": \"user\",
            \"parts\": [{\"text\": \"В каких документах рассказывают про Fort Knox\"}]
        }
    }"
```
