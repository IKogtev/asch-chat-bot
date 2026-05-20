## Docker
    
    docker build -f Dockerfile -t mcp-faq-rag:latest .

### Qdrant при старте

При `USE_QDRANT=true`, если Qdrant недоступен при запуске, сервер поднимается и в фоне повторяет загрузку индекса:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `FAQ_QDRANT_RETRY_INTERVAL` | `10` | Интервал между попытками (сек) |
| `FAQ_QDRANT_INIT_TIMEOUT` | `600` | Таймаут ожидания; по истечении процесс завершается для перезапуска контейнера (`0` = без выхода) |

Статус: `GET /faq/status` → поле `qdrant_init`.

#### Docker Compose
```bash
# Запуск
docker-compose up -d

# Логи
docker-compose logs -f faq-server

# Остановка
docker-compose down
```

```
mcp-server-faq/                     # общая папка faq
├── docs/                           # Документация
├── faq_service/                    # папка отвечающая за логирование
│   └── logs/                  
├── test-faq/                       # Тест кейсы FAQ
├── utils/                          # папка библиотека утилит
|   ├── preprocessors/              # папка с различными предообработчиками
│   |   ├── document_loader.py      # загрузчик
│   |   └── preprocessors.py        # препроцессор
|   ├── indexer.py                  # индексер модуль отвечающий за действия с индексом
|   ├── logger.py                   # утилита для логов общих
|   ├── storager.py                 # модуль работы с хранилищем docker
|   └── utilities.py                # различные добавочные утилиты
├── docker-compose.yml              # Контейнер compose
├── Dockerfile                      # Контейнер
├── mcp_faq_v2.py                   # Основной сервер
├── requirements.txt                # Зависимости


```