# статусы qdrant 
Следующие статусы возможны:

🟢 green: коллекция готова
🟡 yellow: коллекция в процессе оптимизации (загружается)
⚫ grey: коллекция приостановила оптимизацию в ожидании (нужна помощь)
🔴 red: произошла ошибка критическая
# Команды как обращаться к qdrant:
## получить информацию о коллекции: 
curl  -X GET \
  'http://localhost:6333/collections/faq_collection' - FAQ

curl  -X GET \
  'http://localhost:6333/collections/kb_collection' - KB
## список коллекций
curl  -X GET \
  'http://localhost:6333/collections' - LIST ALL COLLECTIONS  

## произвести фильтрацию по запросу
curl -X POST http://localhost:6333/collections/faq_collection/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 2,
    "with_payload": true,
    "with_vector": false,
    "filter": {
      "must": [
        {
          "key": "category",
          "match": { "value": "Инвестиции" }
        }
      ]
    }
  }' - GET POINTS WITH FILTER


curl -X POST http://localhost:6333/collections/faq_collection/points/scroll \
  -H "Content-Type: application/json" \
  -d '{
    "limit": 5,
    "with_payload": true,
    "with_vector": false
  }' - выводим несколько точек без сортировки


  curl -X POST http://localhost:6333/collections/faq_collection/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit":5,"with_vector":false,"with_payload":["category"],"filter":{"must":[{"key":"category","match":{"value":"Инвестиции"}}]}}'


curl -X POST http://localhost:6333/collections/kb_collection/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 2,
    "with_payload": true,
    "with_vector": false,
    "filter": {
      "must": [
        {
          "key": "kb_id",
          "match": { "value": "unit linked" }
        }
      ]
    }
  }' - аналогичное обращение к kb_collection