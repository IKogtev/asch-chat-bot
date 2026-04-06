# Описание реализации цепочки агентов
## состав агентов
OWASP_agent — только проверка безопасности запроса
dispatcher_agent — только классификация запроса и выбор ветки
doc_search_agent — только поиск/выдача списка документов
KB_answer_agent — только ответ по базе знаний
## Промпты
промпты храним в папке kb_storage\prompts
именуем так:
<название агента>-prompt.md
станые копии сохраняем с именами
<название агента>-prompt-old<дата замены, например 260331>.md
## LLM
все аренты работают с общей моделью build_common_model
## Инструменты
разным агентам доступны различные инструменты:
OWASP_agent — без MCP
Dispatcher_agent — без MCP
doc_search_agent — kb_search, на коллекции "база документов"
KB_answer_agent — kb_search, на коллекции "база знаний"

## Контракты агентов
### OWASP_agent
{
  "status": "ok",
  "route": "continue",
  "reason": "safe"
}

{
  "status": "blocked",
  "route": "reject",
  "reason": "prompt_injection",
  "user_message": "Запрос отклонён по соображениям безопасности."
}

### dispatcher_agent
{
  "status": "ok",
  "route": "doc_search",
  "reason": "user asks to find/download documents about Fort Knox",
  "search_query": "продукт Fort Knox"
}

{
  "status": "ok",
  "route": "KB_answer",
  "reason": "user asks informational question",
  "search_query": "Что такое накопительное страхование жизни"
}
## Типы вызовов
doc_search - поиск документов
kb_answer - поиск ответа на вопрос
smalltalk - простой разговор, идет так же в kb_answer
blocked - заблокировано безопасностью OWASP