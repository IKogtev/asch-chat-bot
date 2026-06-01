# MCP KB Search Server

Сервер поиска по базе знаний с использованием LlamaIndex и HuggingFace embeddings.

## 📋 Содержание

- [Быстрый старт](#быстрый-старт)
- [Endpoints](#endpoints)
- [Конфигурация](#конфигурация)
- [Примеры использования](#примеры-использования)
- [Troubleshooting](#troubleshooting)

## 🚀 Быстрый старт

### Локально (без Docker)

 ``` bash
# Установка зависимостей
pip install -r requirements-server.txt

# Копируем конфиг
cp .env.example .env

# Запуск сервера
python mcp-server-kbsearch_v2.py
 ```

Сервер будет доступен по адресу: `http://localhost:7001`

### Docker

#### Сборка образа (с кешированием)

 ``` bash
# Сборка базового образа (выполнить один раз)
docker build -f Dockerfile.base -t mcp-kbsearch-base:latest .

# Сборка финального образа
docker build -t mcp-kbsearch:latest .
 ```

#### Запуск контейнера

 ``` bash
docker run -p 7001:7001 \
  -v $(pwd)/kb_service:/app/kb_service \
  -v $(pwd)/logs:/app/logs \
  -e MCP_TOKEN=REDACTED_EXAMPLE \
  -e KB_DEFAULT_SOURCE=/path/to/documents \
  mcp-kbsearch:latest
 ```

#### Docker Compose (рекомендуется)

 ``` bash
# Запуск
docker-compose up -d

# Логи
docker-compose logs -f kbsearch-server

# Остановка
docker-compose down
 ```

## 📡 Endpoints

### Статус базы знаний

`GET /kb/status`

**Ответ:**

 ``` json
{
  "success": true,
  "status": {
    "initialized": true,
    "points_count": 42,
    "document_count": 42,
    "last_updated": "2024-01-15T10:30:00",
    "metadata": "metadata"
  }
}
 ```

### Обновить базу знаний

`POST /kb/update`

**Параметры запроса:**

- `source_type`: `local_folder` | `s3` | `default` (по умолчанию: `default`)
- `mode`: `append` | `replace` | `merge` (по умолчанию: `replace`)
- `source_path`: путь для `local_folder`
- `bucket`: имя бакета для S3
- `prefix`: префикс для S3 (по умолчанию: `kb/`)

**Пример:**
POST /kb/update?source_type=local_folder&mode=replace&source_path=/path/to/docs

**Ответ:**

 ``` json
{
  "success": true,
  "message": "База знаний успешно обновлена",
  "status": {
    "initialized": true,
    "document_count": 42,
    "last_updated": "2024-01-15T10:30:00"
  }
}
 ```

### Очистить базу знаний

`POST /kb/clear`

**Ответ:**

 ``` json
{
  "success": true,
  "message": "База знаний успешно очищена",
  "status": {
    "initialized": false,
    "document_count": 0
  }
}
 ```

### MCP Endpoint

`POST /kbsearch/mcp`

Основной endpoint для MCP клиентов. Поддерживает инструменты:
- `kbsearch` - поиск по запросу с параметрами top_k и include_metadata
- `get_kb_info` - информация о KB

**Требует Bearer токен в заголовке Authorization**

### Основные переменные

| Переменная | Описание | Default |
|------------|----------|---------|
| `MCP_TOKEN` | Токен авторизации для MCP | -REDACTED_EXAMPLE |
| `MCP_HOST` | Хост сервера | 0.0.0.0 |
| `MCP_PORT` | Порт сервера | 7001 |
| `MCP_KBSEARCH` | Базовый путь MCP | /kbsearch |
| `EMBEDDING_API_URL` | URL API для эмбеддингов | -https://dsrv1.llm.nstcloud.ru/v1/embeddings |
| `EMBEDDING_API_KEY` | Ключ API для эмбеддингов | default-key |
| `EMBEDDING_MODEL` | Модель эмбеддингов | Qwen/Qwen3-Embedding-0.6B |
| `KB_CHUNK_SIZE` | Размер чанка | 512 |
| `KB_CHUNK_OVERLAP` | Перекрытие чанков | 50 |
| `KB_SIMILARITY_TOP_K` | Макс. результатов | 20 |
| `KB_SIMILARITY_CUTOFF` | Порог релевантности | 0.15 |
| `KB_RRF_K_DOC_SEARCH` | Нормализацонный коэффициент RRF | 60 |
| `KB_CANDIDATE_MULT_DOC_SEARCH` | Название папки с архивом | 120 |
| `DOC_SEARCH_ARCHIVE_SECTION` | Имя папки архива для `must_not` при `search_profile=doc_search` | `5 Архив` |
| `QDRANT_HOST` | хост qdrant | qdrant |
| `QDRANT_PORT` | порт qdrant | 6333 |
| `QDRANT_URL` | путь к qdrant | http://qdrant:6333 |
| `KB_QDRANT_RETRY_INTERVAL` | интервал повторных попыток загрузки KB из Qdrant (сек) | 1 |
| `KB_QDRANT_INIT_TIMEOUT` | макс. время ожидания Qdrant при старте; по истечении процесс завершается для перезапуска контейнера (сек, `0` = без выхода) | 60 |

## 📝 Примеры использования

### Проверка здоровья сервера

 ``` bash
curl http://localhost:7001/kb/status
 ```

### Поиск документов через MCP с токеном

 ``` bash
curl -X POST http://localhost:7001/kbsearch/mcp \
  -H "X-Redacted-Auth: Bearer REDACTED_EXAMPLE" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "kbsearch",
      "arguments": {
        "query": "условия страхования",
        "top_k": 5,
        "include_metadata": true
      }
    }
  }'
 ```

### Получить информацию о KB

 ``` bash
curl -X POST http://localhost:7001/kbsearch/mcp \
  -H "X-Redacted-Auth: Bearer REDACTED_EXAMPLE" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "get_kb_info",
      "arguments": {}
    }
  }'
 ```

### Обновление KB из локальной папки

 ``` bash
curl -X POST "http://localhost:7001/kb/update?source_type=local_folder&mode=replace&source_path=/path/to/documents"
 ```

### Очистка KB

 ``` bash
curl -X POST http://localhost:8000/kb/clear
 ```

### Слияние документов (режим merge)

 ``` bash
curl -X POST "http://localhost:8000/kb/update?source_type=local_folder&mode=merge&source_path=/path/to/new/documents"
 ```

## 🔍 Проверка работоспособности

### 1. Проверить, что контейнер запустился

 ``` bash
docker ps | grep mcp-kbsearch
 ```

### 2. Проверить логи

 ``` bash
docker logs mcp-kbsearch-server
 ```

### 3. Проверить статус KB

 ``` bash
curl http://localhost:7001/kb/status
 ```

### 4. Проверить health check

 ``` bash
curl -v http://localhost:7001/kb/status
 ```

Должен вернуть HTTP 200

## 🐛 Troubleshooting

### Контейнер не стартует

**Проблема:** `Error: Module not found`

**Решение:**

 ``` bash
# Пересобрать образ
docker build --no-cache -t mcp-kbsearch:latest .

# Проверить requirements-server.txt
pip install -r requirements-server.txt
 ```

### KB не инициализируется

**Проблема:** `KB не инициализирована`

**Решение:**

 ``` bash
# Проверить наличие документов
ls -la kb_service/documents/

# Обновить KB вручную
curl -X POST "http://localhost:7001/kb/update?source_type=default"

# Проверить логи
docker logs mcp-kbsearch-server
 ```

### Ошибка аутентификации

**Проблема:** `401 Unauthorized`

**Решение:**

 ``` bash
# Добавить Bearer токен в заголовок
curl -H "X-Redacted-Auth: Bearer REDACTED_EXAMPLE" \
  http://localhost:7001/kb/status
 ```

### Ошибка при инициализации индекса

**Проблема:** `Error loading index from disk`

**Решение:**

 ``` bash
# Очистить индекс и пересоздать
curl -X POST http://localhost:7001/kb/clear

# Обновить KB
curl -X POST "http://localhost:7001/kb/update?source_type=default&mode=replace"
 ```
