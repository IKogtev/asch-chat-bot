# API интеграции НСТ с LifePoint

## 1. Назначение

API предназначено для интеграции LifePoint (ЛП) с сервисом "Насти" для отправки персональных уведомлений пользователям
в поддерживаемые каналы коммуникации.

Основной сценарий:

1. Настя формирует уникальный `global_user_id` для каждого пользователя.
2. НСТ предоставляет LifePoint список пользователей с их
   `global_user_id` для последующего сопоставления пользователей
   на стороне LifePoint по номеру мобильного телефона.
3. После сопоставления LifePoint использует `global_user_id`
   для идентификации пользователя при отправке уведомления.
4. LifePoint отправляет запрос в API Насти, передавая:
   - `global_user_id` — идентификатор пользователя;
   - `channel` — канал отправки; Не обязательно
   - `message` — текст уведомления.
5. Настя по `global_user_id` определяет связанные аккаунты пользователя.
6. Настя отправляет уведомление в указанный канал или во все
   доступные каналы пользователя например если не указан channel.

При отправке уведомления LifePoint не передает в API НАСТИ
номер мобильного телефона и другие
внутренние идентификаторы каналов.

Пример `global_user_id`:

    b95d5201-7d1c-4100-b99b-97ca27222978
    b73c3dbb-26c6-48ee-a565-6c2db92ecfad

---

# 2. Общая архитектура
```
LifePoint
    |
    | global_user_id
    | channel
    | message
    |
    | HTTPS + Authorization
    v
НАСТЯ API
    |
    | поиск пользователя по global_user_id
    v
НАСТЯ
    |
    +---- Telegram
    |
    +---- MAX
    |
    +---- Web-UI
```

LifePoint взаимодействует только с API НСТ.
Прямого взаимодействия LifePoint с каналами не происходит.

---

# 3. Базовый URL

Production:

    https://<NST_HOST>/api/v1

Test:

    https://<NST_TEST_HOST>/api/v1

Точные URL окружений предоставляются отдельно.

---

# 4. Аутентификация

API использует Bearer Token.

Каждый запрос к API должен содержать HTTP-заголовок:

    Authorization: Bearer <API_TOKEN>

Пример:

    Authorization: Bearer eyJhbGciOi...

API-токен является секретным значением и не должен
передаваться в URL или теле запроса.

Для интеграции LifePoint используется отдельный API-токен.

---

# 5. Общие HTTP-заголовки

## Для GET-запросов

Обязательный заголовок:

    Authorization: Bearer <API_TOKEN>

## Для POST-запросов

Обязательные заголовки:

    Authorization: Bearer <API_TOKEN>
    Content-Type: application/json
---

# 6. Доступные методы API

API предоставляет следующие методы:

Метод|	Endpoint|	Назначение
|---|---|---|
GET|	/users|	Получение списка пользователей и их global_user_id
POST|	/notifications|	Отправка уведомления конкретному пользователю

# 7. Получение списка пользователей

## GET /users

Endpoint предназначен для получения списка пользователей,
зарегистрированных в НАСТЕ.

### Request

Метод:

    GET /api/v1/users

Тело запроса отсутствует.

### Response

HTTP 200:

```json
{
  "users": [
    {
      "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
      "first_name": "Иван",
      "last_name": "Иванов",
      "accounts": [
        {
          "platform": "telegram"
        },
        {
          "platform": "max"
        }
      ]
    }
  ]
}
```
### Параметры ответа
|Поле|Тип|Описание|
|---|---|---|
| global_user_id|UUID |	Уникальный идентификатор пользователя НСТ |
|first_name|	string|	Имя пользователя|
|last_name|	string|	Фамилия пользователя|
|accounts	|array|	Доступные каналы пользователя|

Внешнему потребителю не передаются внутренние
platform_user_id

# 8. Отправка персонального уведомления

## POST /notifications

Отправляет сообщение конкретному пользователю,
определенному по global_user_id.
### Request
Метод:

    POST /api/v1/notifications

Обязательные HTTP-заголовки:

    Authorization: Bearer <API_TOKEN>
    Content-Type: application/json
Тело запроса
```json
{
  "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
  "channel": "telegram",
  "message": "У вас новое уведомление"
}
```
### Параметры
|Поле|Тип|Описание| Обязательное |
|---|---|---|---|
| global_user_id|UUID |	Уникальный идентификатор пользователя НСТ |да
|channel|	string|	Канал отправки| нет
|message|	string|	Текст уведомления| да

channel

Допустимые значения:

    telegram — отправить в Telegram;
    max — отправить в MAX;
    all — отправить во все доступные каналы.

# 9. Логика обработки запроса

После получения запроса НАСТЯ выполняет следующие действия:

1. Проверяет авторизацию запроса.
2. Получает global_user_id из тела запроса.
3. Находит пользователя по global_user_id.
4. Определяет связанные аккаунты пользователя.
5. В зависимости от значения channel выбирает канал отправки.
6. Отправляет переданное в поле message сообщение пользователю.
7. Возвращает результат отправки.

Пример

Запрос:
```json
{
  "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
  "channel": "telegram",
  "message": "У вас новое уведомление"
}
```
НАСТЯ находит:
```
global_user_id
      |
      +--- Telegram account
      |
      +--- MAX account
```
Так как:

    channel = telegram

сообщение отправляется только в Telegram.

# 10. Успешный ответ

HTTP 200:
``` json
{
  "status": "ok",
  "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
  "sent_to": [
    "telegram",
  ],
  "results": {
    "telegram": {
      "status": "ok"
    }
  }
}
```
# 11. Формат ошибок
Все ошибки API возвращаются в едином формате:
``` json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Описание ошибки"
  }
}
```
## 11.1 Ошибка авторизации

HTTP 401:
``` json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid authentication credentials"
  }
}
```

## 11.2 Пользователь отсутствует
HTTP 404:
``` json
{
  "error": {
    "code": "USER_NOT_FOUND",
    "message": "User with specified global_user_id was not found"
  }
}
```
## 11.3 Пользователь заблокирован
HTTP 409:
``` json
{
  "error": {
    "code": "USER_BLOCKED",
    "message": "User is blocked"
  }
}
```
## 11.4 Некорректный параметр channel

HTTP 422:
``` json
{
  "error": {
    "code": "INVALID_CHANNEL",
    "message": "channel must be one of: telegram, max, all"
  }
}
```
## 11.5. Некорректное тело запроса

HTTP 422:
``` json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Invalid request parameters"
  }
}
```
## 11.6. Внутренняя ошибка

HTTP 500:
``` json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Internal server error"
  }
}
```

# 12. Примеры запросов
## 12.1. Отправка в Telegram
``` bash
curl -X POST "https://<NST_HOST>/api/v1/notifications" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-raw '{
    "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
    "channel": "telegram",
    "message": "Тестовое уведомление"
  }'
```
## 12.2. Отправка в MAX
``` bash
curl -X POST "https://<NST_HOST>/api/v1/notifications" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-raw '{
    "global_user_id": "57a8ddfe-f225-43e8-8e94-33e4d3708097",
    "channel": "max",
    "message": "Тестовое уведомление"
  }'
```
в одну строчку команда выглядит так:
``` bash
curl -X POST "https://<NST_HOST>/api/v1/notifications" -H "Authorization: Bearer <API_TOKEN>" -H "Content-Type: application/json; charset=utf-8" --data-raw '{"global_user_id":"57a8ddfe-f225-43e8-8e94-33e4d3708097", "channel":"max","message":"Тестовое уведомление"}'
```
## 12.3. Отправка во все доступные каналы
``` bash
curl -X POST "https://<NST_HOST>/api/v1/notifications" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-raw '{
    "global_user_id": "b95d5201-7d1c-4100-b99b-97ca27222978",
    "channel": "all",
    "message": "Тестовое уведомление"
  }'
```
## 12.4 Получение пользователей
``` bash
curl -X GET "https://<NST_HOST>/api/v1/users" \
  -H "Authorization: Bearer <API_TOKEN>" 
```

# 13. Ограничения передаваемых данных

Для отправки уведомления LifePoint передает только:
- `global_user_id`;
- channel;
- message.

Для отправки уведомления не требуется передавать:

- номер мобильного телефона;
- username;
- другие внутренние идентификаторы аккаунтов.

`global_user_id` используется как идентификатор пользователя
в системе НСТ, по которому НСТ самостоятельно определяет
необходимый аккаунт для отправки сообщения.