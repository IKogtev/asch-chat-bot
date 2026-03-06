# Qdrant Document Manager KB-manger

## Environment Variables
### Qdrant Configuration
- `QDRANT_HOST`: Qdrant server HOST (default: `qdrant`)
- `QDRANT_PORT`: Qdrant server PORT (default: `6333`)
- `QDRANT_COLLECTION`: Collection name (default: `kb_collection`) отвечает за коллекцию при запуск работы
- `LOG_TO_FILE`: Сохранять логирование в файл default: `false` 

### Embedding API Configuration
- `EMBEDDING_MODEL`: Model name to use (e.g., `"Qwen/Qwen3-Embedding-0.6B"`)
- `CHUNK_SIZE`: chunk size `512`
- `CHUNK_OVERLAP`: chunk overlap `20`

## Docker

```bash
# Build image
docker compose up -d --build
```

## Usage

1. Navigate to http://localhost:5000 если локально иначе на адрес по которому развернули контейнер сервис запускается на 5000 порту 
2. Можно выбрать коллекцию из выпадающего списка все дальнейшие операции будут касаться этой коллекции
3. Delete слева от надписи Active collection кнопка для удаления коллекции (если коллекция является активным alias удалить её не получиться)
4. Add Collection - кнопка справа от выбора коллекции (добавить коллекцию)
5. Switch alias - кнопка за add collection позволяет поменять активный alias между коллекциями одного типа
6. На вкладке Documents отображаются текущие документы и активные Базы знаний, кнопки рядом с ними позволяют совершать действия по удалению и просмотру, delete, view соответственно.
7. Вкладка поиск позволяет производить поиск по свободному запросу семантический
8. вкладка Upload необходима для загрузки документов в qdrant через UI

## API Endpoints

- `GET /api/health` - check Health of collection 
- `GET /api/documents` - получить документы текущей выбранной коллекции в сыром виде
- `POST /api/documents/upload` - загрузка нового документа в qdrant 
- `GET /api/documents/{id}` - получение информации о документе по id и всех его чанков
- `DELETE /api/documents/{id}` - удаление документа по id 
- `POST /api/search` - выполнить поиск по доступным точкам текущей коллекции
- `GET /api/collections/info` - получить информацию о текущей коллекции
- `GET /api/collections` - получить информацию о существующих коллекциях
- `POST /api/collections/switch` - переключатель между коллекциями, решает какая коллекция будет отображаться пользователю 
- `GET /api/collections/active` - получить информацию какие коллекции являются текущими активными alias 
- `GET /api/collections/by-type` - получить информацию о типах существующих коллекций на данный момент два типа faq и kb
- `POST /api/collections/switch-alias` - сменить алиас, поменять алиас между коллекциями одного типа faq или kb
- `POST /api/collections/create` - создать коллекцию новой версии, по типу который выберем faq или kb + номер версии коллекции
- `POST /api/collections/delete` - удалить существующую коллекцию

- `GET /api/knowledge-bases` - получить информацию о существующих базах знаний для выбранной коллекции
- `POST /api/knowledge-bases/delete` - удалить базу знаний
-  `GET /api/documents/download/{document_id}` - скачать файл документа на основе id 
-  `GET /api/filesystem/download?path={path_to_file}` - скачать файл документа на основе пути к нему, временный формат

### Structure:
KB-Manager UI:

```
kb-manager                              # папка UI
├── app/                                # папка приложения
│   ├── logs/                           # логи
│   ├── services/                       # сервисные директории
│   |   └── qdrant_service.py           # обработчик работы с qdrant хранилищем
│   ├── static/                         # веб интерфейс
│   |   ├── app.js                      # совмещение back+front проекта               
│   |   ├── index.html                  # front проекта
│   |   └── style.css                   # стили для проекта
|   ├── utils/                          # папка библиотека утилит
|   |   ├── preprocessors/              # папка с различными предообработчиками
│   |   |   ├── document_loader.py      # загрузчик
│   |   |   └── preprocessors.py        # препроцессор
|   |   ├── logger.py                   # утилита для логов общих
|   |   └── utilities.py                # различные добавочные утилиты
│   ├── main.py                         # back проекта
│   └── models.py                       # модели ожидаемых входных и выходных данных
├── Dockerfile                          # Dockerfile
├── README.md                           # Readme
├── docker-compose.yml                  # Контейнер compose
└── requirements.txt                    # зависимости

```