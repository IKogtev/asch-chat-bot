# Проблема с периодическим отсутствием вызовов MCP tools

## Краткое описание

В production иногда наблюдается ситуация, когда агент должен вызвать MCP-инструмент (`faq_search`, `kb_search` или другие tools), но фактически не вызывает его. В результате модель может:

- ответить по памяти;
- вернуть `source="faq_search"` или `source="kb_search"` без реального tool call;
- вернуть `source="none"` для содержательного `kb_answer`-запроса, что затем отсекается валидатором;
- отдать пользователю общий текст ошибки валидации.

Проблема связана не с бизнес-логикой поиска, а с нестабильностью MCP-сессий в связке Google ADK `McpToolset` + `StreamableHTTPConnectionParams`.

## Наблюдения из production

На момент проверки namespace: `chatbot-prod`.

Состояние pod'ов:

- `adk-agent` работал около 5 часов;
- `mcp-server-faq` и `mcp-server-kbsearch` были пересозданы примерно 81 минуту назад;
- после пересоздания MCP pod'ов в логах `adk-agent` появились ошибки stale MCP session.

Характерные строки из логов `adk-agent`:

```text
streamable_http.py:298 - GET stream disconnected, reconnecting in 1000ms...
mcp_toolset.py:309 - Exception during MCP session execution:
Failed to get tools from MCP server: Connection closed
mcp.shared.exceptions.McpError: Connection closed
mcp_session_manager.py:465 - Cleaning up session (disconnected or different loop)
session_context.py:204 - Error on session runner task:
Attempted to exit cancel scope in a different task than it was entered in
session_context.py:151 - Failed to close MCP session:
Attempted to exit cancel scope in a different task than it was entered in
```

В проверенном фрагменте логов было найдено 18 записей, связанных с `Connection closed`, reconnect или ошибкой `cancel scope`.

## Пользовательский симптом

Один и тот же запрос `Как посмотреть видео про Альфа кидс` был обработан по-разному.

Первый запуск:

- `dispatcher_agent` выбрал `route="kb_answer"` и `intent="kb_answer"`;
- `kb_answer_agent` вернул `source="faq_search"`;
- в логах ADK было `Generated 4 events in agent run`;
- на стороне `mcp-server-faq` не было строки `FAQ поиск` на этот момент;
- ответ был неточным и выглядел как сгенерированный по памяти.

Второй запуск через короткое время:

- маршрут остался тем же;
- в логах ADK было `Generated 6 events in agent run`;
- на стороне `mcp-server-faq` появилась строка `FAQ поиск`;
- ответ содержал реальные ссылки из FAQ.

Это указывает, что в первом случае модель заявила `source="faq_search"`, но tool фактически не был вызван.

## Вероятная причина

`McpToolset` держит MCP-сессию поверх streamable HTTP. Если MCP pod перезапускается, соединение рвётся или SSE GET stream отключается, `adk-agent` может продолжать работать со stale session.

При следующем обращении к tools происходит ошибка:

- `list_tools` падает с `Connection closed`;
- ADK пытается очистить сессию;
- cleanup завершается ошибкой `Attempted to exit cancel scope in a different task than it was entered in`;
- для одного или нескольких последующих запусков агент может остаться без доступных tools.

Когда tools недоступны, LLM всё равно может сформировать JSON-ответ по контракту и указать `source="faq_search"` или `source="kb_search"`, хотя реального MCP-вызова не было.

## Почему текущая валидация не ловит все случаи

Сейчас валидатор ловит часть проблем. Например, для `intent="kb_answer"` ответ с `mode="text_answer"` и `source="none"` считается ошибочным.

Но валидатор не проверяет факт реального tool call. Поэтому ответ вида:

```json
{
  "status": "ok",
  "mode": "text_answer",
  "message": "Ответ по памяти",
  "source": "faq_search"
}
```

может пройти валидацию, даже если `faq_search` в этом запуске не вызывался.

## План исправления

### 1. Быстрая операционная мера

При redeploy или restart MCP-сервисов нужно также перезапускать `adk-agent`.

Это сбрасывает stale MCP-сессии и уменьшает вероятность состояния, когда `adk-agent` держит старые streamable HTTP handles после пересоздания MCP pod'ов.

Рекомендуемое правило:

```text
Если пересозданы mcp-server-faq или mcp-server-kbsearch, пересоздать adk-agent.
```

### 2. Обновить зависимости ADK/MCP SDK

Проверить текущие версии:

```bash
kubectl exec -n chatbot-prod deploy/adk-agent -- pip show google-adk mcp
```

После проверки обновить `google-adk` и `mcp` до версий, где исправлены проблемы streamable HTTP session cleanup и anyio cancel scope.

Перед обновлением нужно прогнать smoke-тесты:

- `kb_answer` с `faq_search`;
- `kb_answer` с fallback в `kb_search`;
- `doc_search` через `kb_search`;
- повторный запрос после restart `mcp-server-faq`;
- повторный запрос после restart `mcp-server-kbsearch`.

### 3. Добавить проверку факта tool call

Нужно ввести runtime-инвариант:

```text
Если intent требует MCP tool, финальный ответ не может считаться валидным,
пока в текущем запуске не был зафиксирован соответствующий tool call/tool response.
```

Для `kb_answer_agent`:

- при `intent="smalltalk"` tools не обязательны;
- при `intent="kb_answer"` должен быть вызван минимум `faq_search`;
- если итоговый `source="kb_search"` или `source="faq_search+kb_search"`, должен быть зафиксирован вызов `kb_search`;
- если итоговый `source="faq_search"`, должен быть зафиксирован вызов `faq_search`.

Для `doc_search_agent`:

- при содержательном поиске документов должен быть зафиксирован вызов `kb_search`.

Если tool call отсутствует, нужно не отдавать ответ пользователю как достоверный, а возвращать контролируемую ошибку или запускать повтор.

### 4. Добавить retry/reconnect вокруг MCP toolset

Нужно обработать ошибки:

- `McpError: Connection closed`;
- ошибки `Failed to get tools from MCP server`;
- ошибки stream disconnect;
- cleanup errors вида `Attempted to exit cancel scope...`.

При таких ошибках нужно:

1. удалить или пересоздать текущую MCP-сессию;
2. заново создать `McpToolset`;
3. повторить получение tools;
4. ограничить число повторов, например 1-2 попытки на turn.

Важно: retry должен быть на уровне MCP tool discovery/tool call, а не на уровне всего пользовательского запроса без контроля, иначе можно получить дублирование side effects. Для текущих search tools это безопаснее, так как они read-only.

### 5. Улучшить observability

Добавить в логи:

- список tools, доступных агенту перед запуском leaf-agent;
- количество найденных tools после `list_tools`;
- имя каждого реально вызванного tool;
- mapping `invocation_id -> tool_calls`;
- отдельный warning, если `source` в JSON указывает на MCP, но tool call в этом turn отсутствует.

Минимальный полезный лог:

```text
invocation_id=<id> agent=kb_answer_agent available_tools=[faq_search,kb_search]
invocation_id=<id> tool_call=faq_search status=ok
invocation_id=<id> result_source=faq_search required_tool_seen=true
```

### 6. Добавить health/liveness проверку MCP-пути

Обычная readiness MCP pod'а не гарантирует, что `adk-agent` имеет живую MCP-сессию.

Нужна проверка со стороны `adk-agent`, которая выполняет хотя бы:

- `list_tools` для `faq_search`;
- `list_tools` для `kb_search`;
- опционально lightweight test call с безопасным запросом.

Если проверка стабильно не проходит, pod `adk-agent` должен быть перезапущен liveness probe или внешним monitor'ом.

### 7. Проверить маршрутизацию отдельно

Не все случаи отсутствия MCP-вызова являются ошибкой MCP.

Например, если `dispatcher_agent` классифицирует запрос как `intent="smalltalk"`, `kb_answer_agent` по правилам не должен вызывать `faq_search` или `kb_search`.

Поэтому диагностика должна разделять:

- tools не вызваны, потому что intent `smalltalk` — нормальное поведение;
- tools не вызваны, хотя intent `kb_answer` или `doc_search` — ошибка;
- tools были недоступны из-за `Connection closed` — инфраструктурная ошибка MCP session;
- tools были доступны, но модель не вызвала обязательный tool — ошибка prompt/contract enforcement.

## Рекомендуемый порядок внедрения

1. Ввести операционное правило: restart `adk-agent` после restart MCP pod'ов.
2. Добавить логирование фактических tool calls и проверки `source -> required_tool_seen`.
3. Добавить runtime-валидацию: если `source` указывает на MCP, но tool call отсутствует, ответ не пропускать.
4. Добавить reconnect/retry для `McpToolset` при `Connection closed`.
5. Обновить версии `google-adk` и `mcp`, проверить совместимость.
6. Добавить liveness/health probe MCP-пути из `adk-agent`.

## Реализованный фикс: `/mcp-healthz` + livenessProbe

Пункт 6 из списка выше реализован.

### Что сделано

- Появился кастомный entrypoint `app_server.py` в корне репозитория.
  Он использует `google.adk.cli.fast_api.get_fast_api_app(agents_dir="/app")`,
  то есть запускает тот же ADK API server, что и `adk api_server .`,
  но дополнительно регистрирует два маршрута:
  - `GET /healthz` — дешёвый process-level probe (поднята ли FastAPI);
  - `GET /mcp-healthz` — обход всех live `McpToolset` в дереве
    `root_agent.sub_agents` и вызов `await toolset.get_tools(...)`
    с таймаутом `MCP_HEALTHZ_TIMEOUT_SEC` (по умолчанию 8 секунд).
    Если хотя бы один toolset не отдал список tools, возвращается `503`.

- `Dockerfile.agent` теперь стартует через `uvicorn app_server:app`
  вместо `adk api_server . ...`.
- В `deployment/kubernetes/base/adk-agent.yaml` обновлены `command`/`args`
  под новый entrypoint.
- В overlays `yc-app-chatbot-prod` и `yc-sandbox-2-chatbot-test1`
  добавлен patch `patch-probes-adk-agent.yaml`:
  - `startupProbe` — TCP на 8000, окно ≈10 минут на старт;
  - `readinessProbe` — `/healthz`, гейтит траффик до готовности процесса;
  - `livenessProbe` — `/mcp-healthz`, период 60 с, таймаут 15 с,
    `failureThreshold: 3`. При устойчивом отказе live MCP toolsets
    kubelet перезапускает pod, что чистит stale streamable_http сессии
    и leaks `cancel scope`.

### Что это даёт

- Не нужен внешний watchdog: kubelet сам перезапускает `adk-agent`,
  когда `McpToolset.get_tools` начинает падать.
- Сигнал — реальное состояние in-process MCP сессий, а не reachability
  MCP сервиса, поэтому ловятся именно те случаи, что были в логах
  (`Connection closed`, `Attempted to exit cancel scope...`).
- Бот не получает «висящий» агент: на время `livenessProbe` failure
  readiness тоже исключает pod из service, и трафик не идёт в сломанный
  процесс.

### Поведение, которое стоит мониторить

- Слишком частые рестарты `adk-agent` (counter `kube_pod_container_status_restarts_total`)
  будут означать, что MCP сервер действительно живёт нестабильно
  и нужно чинить уже его, а не агента.
- Если `livenessProbe` будет ложно срабатывать из-за длительной
  деградации MCP, можно увеличить `failureThreshold` или
  `MCP_HEALTHZ_TIMEOUT_SEC`. Но это маскирует проблему, а не решает.

## Критерии успешного исправления

Проблема считается исправленной, если:

- после restart `mcp-server-faq` следующий `kb_answer` с FAQ-запросом либо успешно вызывает `faq_search`, либо возвращает контролируемую ошибку без ответа по памяти;
- после restart `mcp-server-kbsearch` следующий `doc_search` или fallback в `kb_search` либо успешно вызывает `kb_search`, либо возвращает контролируемую ошибку;
- в логах нет повторяющихся `Connection closed` без последующего успешного reconnect;
- невозможно получить финальный ответ с `source="faq_search"` без реального `faq_search` tool call в том же invocation;
- невозможно получить финальный ответ с `source="kb_search"` без реального `kb_search` tool call в том же invocation.
