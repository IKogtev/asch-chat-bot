# Как подключить mcp из контейнера к ChatBox и получить к ним доступ: 
У нас есть 2 mcp:
mcp-filesystem - для управления файловой системой, сейчас запущен с доступом на чтение, без возможности редактировать файлы.
mcp-server-kbsearch - для поиска по документам семантического, с возможностью использовать фильтры

Для того чтобы добавить mcp внутри chatbox заходим в Settings: 
![alt text](images/instruction1.png)
Дальше выбираем MCP, листаем до Custom MCP Servers нажимаем => Add Server => Add Custom Server
![alt text](images/instruction2.png)
где прописываем имя сервера 
Тип выбираем всегда Remote(http/sse)
прописываем соответствующий URL 
и если требуется то прописываем HTTP Header
Все что прописывать берем из настроек конфигурации для разных mcp

После того как прописали обязательно нажимаем кнопку test, и если у нас успешно виден mcp мы увидим перечисления инструментов доступных: 
 ![alt text](images/instruction3.png)

## возможные настройки конфигурации для разных mcp: 

### mcp-kb: 
```
Name: Kb
URL: http://localhost:7001/kbsearch/mcp
HTTP Header: Authorization=Bearer REDACTED_EXAMPLE-here
```
### mcp-filesystem:
```
Name: filesystem
URL: http://localhost:7002/mcp
```
### mcp-faq-blue:
```
Name: FAQ_Blue
URL: https://mcp-server-faq-blue.sandbox-2.wwwnstcloud.ru/faq_rag/mcp
HTTP Header: Authorization=Bearer REDACTED_EXAMPLE-here
```