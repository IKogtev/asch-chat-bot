## Docker
    
    docker build -f Dockerfile -t mcp-faq-rag:latest .

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