# Подключение MCP

В проекте используются следующие MCP-сервисы:

- `mcp-filesystem` — доступ к файловой системе.
- `mcp-server-kbsearch` — поиск по базе документов через `kb_search`.
- `mcp-server-faq` — поиск по FAQ через `faq_search`.

## Примеры подключения

### KB MCP
```text
Name: Kb
URL: http://localhost:7001/kbsearch/mcp
HTTP Header: Authorization=Bearer <token>
```

### FAQ MCP
```text
Name: FAQ
URL: http://localhost:7000/faq_rag/mcp
HTTP Header: Authorization=Bearer <token>
```

### FAQ MCP (remote example)
```text
Name: FAQ_Blue
URL: https://mcp-server-faq-blue.sandbox-2.wwwnstcloud.ru/faq_rag/mcp
HTTP Header: Authorization=Bearer <token>
```

### Filesystem MCP
```text
Name: filesystem
URL: http://localhost:7002/mcp
```

## Что использует агент

`kb_answer_agent`:
- сначала вызывает `faq_search`;
- при недостаточном результате FAQ вызывает `kb_search`;
- при конфликте данных считает FAQ более приоритетным источником.

`doc_search_agent`:
- использует только `kb_search`.
