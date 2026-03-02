#  UI Endpoints to get information
/ - UI main
/api/health - проверить жив ли qdrant 
/api/documents - получить документы текущей выбранной коллекции в сыром виде
/api/collections/info - получить информацию о текущей коллекции
/api/documents/{document_id} - получение информации о документе по id и всех его чанков
/api/knowledge-bases - получить информацию о существующих базах знаний для выбранной коллекции
/api/collections - получить информацию о существующих коллекциях
/api/collections/active - получить информацию какие коллекции являются текущими активными alias 
/api/collections/by-type - получить информацию о типах существующих коллекций на данный момент два типа faq и kb
# UI endpoints to post information
/api/collections/switch-alias - сменить алиас, поменять алиас между коллекциями одного типа faq или kb
/api/collections/create - создать коллекцию новой версии, по типу который выберем faq или kb + номер версии коллекции
/api/collections/delete - удалить существующую коллекцию
/api/collections/switch -  переключатель между коллекциями, решает какая коллекция будет отображаться пользователю
/api/knowledge-bases/delete - удалить базу знаний
/api/search - выполнить поиск по доступным точкам текущей коллекции
/api/documents/{document_id} - удаление документа по id 
/api/documents/upload - загрузка документов в qdrant 