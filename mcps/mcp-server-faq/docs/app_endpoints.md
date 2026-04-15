на данный момент endpoint есть и которые точно работают: 
/faq/update - для обновления данных из 1 из 2 источников (local_folder(папка на самом контейнере(туда попадают после upload_function)), s3(туда заливать только по s3 как вариант через winscp)) 
/faq/clear - очистка полная индекса
/faq/status - получение статуса индекса
/faq/upload - для загрузки файла по которому можно будет потом произвести индексацию в индекс с помощью /faq/update(в UI обязательно)
/faq/cleanup_uploads - функция чтобы очистить всё что было закинуто с помощь upload
# documents work
/faq/documents/search - для поиска документов по фильтрам с кириллицей правда проблемы бывают
# collection work
/faq/collections/switch - смена индекса коллекции с 1 на другой
/faq/collections/prepare - смены коллекций подгрузка новой коллекции для смены текущей, незаметная сразу подготовка новой. (Правда не тестировал на большом объёме коллекций)

Структура MCP:
```
mcp-server-faq/                     # общая папка faq
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
└── .env                            # Конфигурация


```


# all curl commands FAQ:
CLEAR ACTIVE -> curl -X POST http://localhost:7000/faq/clear 
CLEAR BY COLLECTION NAME -> curl -X POST "http://localhost:7000/faq/clear?collection=faq_collection_v2" 

UPLOAD -> curl -X POST http://localhost:7000/faq/upload -F "file=@C:\Users\Igor\Desktop\project_links\Job NNT\nst-consultant-mcp-servers-pack\mcp-server-faq\data_all\FAQ_all.md" 
UPLOAD -> curl -X POST http://localhost:7000/faq/upload -F "file=@C:\Users\Igor\Desktop\project_links\Job NNT\nst-consultant-mcp-servers-pack\mcp-server-faq\test_data\FAQ.md" 
UPLOAD -> curl -X POST http://localhost:7000/faq/upload -F "file=@C:\Users\Igor\Desktop\project_links\Job NNT\nst-consultant-mcp-servers-pack\mcp-server-faq\test_data\FAQ Unit-Linked 26.xlsx"
UPLOAD -> curl -X POST http://localhost:7000/faq/upload -F "file=@C:\Users\Igor\Desktop\project_links\Job NNT\nst-consultant-mcp-servers-pack\mcp-server-faq\test_data\FAQ_ASZh_ Final.xlsx"

CREATE INDEX LOCAL DIR when using UPLOAD -> curl -X POST "http://localhost:7000/faq/update?source_type=local_folder&source_path=.&mode=replace"

STATUS SERVER -> curl http://localhost:7000/faq/status

CREATE INDEX S3_гeplace -> curl -X POST "http://localhost:7000/faq/update?source_type=s3&s3_bucket=nstdata-cloud-nb-test-data&s3_prefix=temp/igor.konovalov/DATA_FOR_MCP&mode=replace&s3_access_key=REDACTED_EXAMPLE&s3_secret_key=REDACTED_EXAMPLE"


CLEAN ALL LOCAL DIR curl -X POST http://localhost:7000/faq/cleanup_uploads


### Пример запроса работающий для обращения и поиска по документам, фильтрация
curl -X POST http://localhost:7000/faq/documents/search \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @- <<EOF
{
  "category": "Инвестиции",
  "limit": 5
}
EOF


## переключение alias в qdrant прекрасно меняется а вот под index и retrieve требуется map_index который в qdrant не храниться TODO
curl -X POST http://localhost:7000/faq/collections/switch \
  -H "Content-Type: application/json" \
  -d '{"collection":"faq_collection_v2"}'

curl -X POST http://localhost:7000/faq/collections/switch -H "Content-Type: application/json" -d '{"collection":"faq_collection"}'

## удаление коллекции
curl -X POST http://localhost:7000/faq/collections/delete -H "Content-Type: application/json" -d '{"collection":"faq_collection_v2"}'


## полноценный blue green index
curl -X POST http://localhost:7000/faq/collections/prepare \
-H "Content-Type: application/json" \
-d '{
  "version": "3",
  "delete_old": false,
  "source_type": "local_folder",
  "source_path": "."
}'

# all curl commands KB:
CLEAR ACTIVE -> curl -X POST http://localhost:7001/kb/clear 
CLEAR BY COLLECTION NAME -> curl -X POST "http://localhost:7001/kb/clear?collection=kb_collection_v2" 

UPLOAD -> curl -X POST http://localhost:7001/kb/upload -F "file=@C:\Users\Igor\Desktop\project_links\Job NNT\nst-consultant-mcp-servers-pack\mcp-server-faq\data_all\test_kb_meta.md" 


CREATE INDEX LOCAL DIR when using UPLOAD -> curl "http://localhost:7001/kb/update?source_type=local_folder&source_path=.&mode=replace"

STATUS SERVER -> curl http://localhost:7001/kb/status

CREATE INDEX S3_гeplace -> curl "http://localhost:7001/kb/update?source_type=s3&s3_bucket=sandbox-2-k8s-mcp-b1ga7h8ijbukqu3mljmu&s3_prefix=mcp_inputs%2Fkbsearch&mode=replace&s3_access_key=YCAJE0tJOF6DbmqSeM3UWdFw3&s3_secret_key=REDACTED_EXAMPLE"


CLEAN ALL LOCAL DIR curl -X POST http://localhost:7001/kb/cleanup_uploads


### Пример запроса работающий для обращения и поиска по документам, фильтрация
curl -X POST http://localhost:7001/kb/documents/search \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @- <<EOF
{
  "kb_id": "нсж альфавыгода",
  "limit": 5
}
EOF


## переключение alias в qdrant
curl -X POST http://localhost:7001/kb/collections/switch \
  -H "Content-Type: application/json" \
  -d '{"collection":"kb_collection_v2"}'

curl -X POST http://localhost:7001/kb/collections/switch -H "Content-Type: application/json" -d '{"collection":"kb_collection"}'

## удаление коллекции
curl -X POST http://localhost:7001/kb/collections/delete -H "Content-Type: application/json" -d '{"collection":"kb_collection_v2"}'


## полноценный blue green index
curl -X POST http://localhost:7001/kb/collections/prepare \
-H "Content-Type: application/json" \
-d '{
  "version": "2",
  "delete_old": false,
  "source_type": "local_folder",
  "source_path": "."
}'