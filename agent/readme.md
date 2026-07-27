Информация о langfuse краткая лежит на wiki: 
https://wiki.yandex.ru/homepage/proizvodstvo/issledovanija/langfuse-vozmozhnosti-i-funkcional/

Для запуска тестов локально с помощью langfuse необходимо сделать несколько шагов: 
1. склонировать к себе их репозиторий: 
```
git clone --depth=1 https://github.com/langfuse/langfuse.git
```
2. перейти в папку langfuse    
```
cd langfuse
```
3. замените содержимое в файле в папке langfuse docker-compose.yml на содержимое нашего (если вы уверены что порты у вас не будут конфликтовать, можно оставить оригинальное): langfuse_docker-compose.yml после чего выполнить, у вас запустится рабочая версия 
```    
docker compose up
```

4. после в web морде контейнера на http://localhost:3000, необходимо авторизироваться, создать новую организацию имя не важно, сразу при создании организации начнется создание проекта, название не имеет значение,  после создания появится вот такое окошко: ![alt text](langfuse_files_scripts\create_org.png)
в нем нажимаем create new api key, копируем получившиеся значения прямо из .env к себе в переменные окружения
![alt text](langfuse_files_scripts\example_credetentions.png)
1. 

для экспорта датасетов настроить правильно пути в файле export_dataset.py и можно выгружать вот пример как правильно может выглядеть запуск: 
```
(.venv) C:\Users\Igor\Desktop\project_links\Job NNT\asch-chat-bot>python agent/langfuse_files_scripts/export_dataset.py
```

для импорта датасетов настроить правильно пути в файле import_dataset.py и можно импортировать датасет в langfuse, вот пример как правильно может выглядеть запуск: 
```
(.venv) C:\Users\Igor\Desktop\project_links\Job NNT\asch-chat-bot>python agent/langfuse_files_scripts/import_dataset.py
```

В настройках проекта задаем какая модель у нас будет: 
![alt text](langfuse_files_scripts/setting_llm.png)

задаем имя провайдера модели(просто название для индентификации для себя), подставляем api key, указываем наш api для подключения модели 
```
https://api.llm.nstcloud.ru/v1
```
и указываем имя модели, которую мы будем использовать в качестве оценщика
![alt text](langfuse_files_scripts/setting_llm2.png)

для создания evaluator запускаем файл создания evaluator,  
```
(.venv) C:\Users\Igor\Desktop\project_links\Job NNT\asch-chat-bot>python agent/langfuse_files_scripts/create_evaluator.py 
```
но настройку к сожалению делаем только внутри для автоматических запусков 

