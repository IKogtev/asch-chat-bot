Для настройки file-manager если вдруг он не инициализирован с самого начала необходимо выполнить данные команды для инициализации бд и изначальных паролей:

Инициализация изначальной бд
```
 docker compose run --rm file-manager config init
``` 

добавление пользователя и пароля

```
docker compose run --rm file-manager users add admin super_secure_password --perm.admin
```

где admin - имя пользователя
super_secure_password - пароль который будет

Сейчас инициализированы именно такими