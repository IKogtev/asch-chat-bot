Для интеграции и безопасности мы теперь делаем интеграцию keycloak для всех наших сервисов, пока что временно используем для локального развертывания:
``` 
docker run -d \
  --name keycloak \
  -p 8088:8080 \
  -e KEYCLOAK_ADMIN=admin \
  -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:latest start-dev
```

Адрес для входа: 
http://localhost:8088/admin/

для получения JWT токена:

```
curl -X POST "http://localhost:8088/realms/kb-manager-test/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=lifepoint" \
  -d "client_secret=<ВАШ_CLIENT_SECRET>"
```
```
curl -X POST "http://localhost:8088/realms/kb-manager-test/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=lifepoint" \
  -d "client_secret=**********"
```

а для развертывания на наших окружениях будем привязываться к keycloak от Окруженний для этого к Паше обращаться или Игорю

```
curl -X GET "http://localhost:5000/api/v1/users?global_user_id=9fce6b58-2c80-41c6-ac72-2919a39ec44b" -H "X-Redacted-Auth: Bearer REMOVED_SECRET"
```

```
curl -X POST "http://localhost:5000/api/v1/notifications" -H "X-Redacted-Auth: Bearer REMOVED_SECRET" -H "Content-Type: application/json; charset=utf-8" --data-raw "{\"global_user_id\":\"57a8ddfe-f225-43e8-8e94-33e4d3708097\",\"channel\":\"max\",\"message\":\"Тестовое уведомление\"}" 
```

```
curl -X POST "http://localhost:5000/api/v1/notifications" -H "X-Redacted-Auth: Bearer <API_TOKEN>" -H "Content-Type: application/json; charset=utf-8" --data-raw "{\"global_user_id\":\"57a8ddfe-f225-43e8-8e94-33e4d3708097\",\"channel\":\"telegram\",\"message\":\"Тестовое уведомление\"}"
```

