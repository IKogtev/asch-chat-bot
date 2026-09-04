# Задача: добавить API новостей в Web BFF

## Цель

WebUI должен получать опубликованные новости для правой панели через `web-bff`.

```text
WebUI
  │ GET /news
  │ cookie nastya_web
  ▼
web-bff
  │ NewsStore
  ▼
PostgreSQL.news
```

WebUI не должен обращаться напрямую к `kb-manager`, Telegram/MAX-ботам или PostgreSQL.

## Что реализовать

### 1. Методы чтения в `NewsStore`

Файл: `bot/services/database.py`

Добавить:

```python
async def get_published_for_web(
    self,
    global_user_id: str,
    *,
    limit: int,
    offset: int,
) -> list[dict]:
```

Требования к запросу:

- брать только `status = 'sent'`;
- учитывать `target_group` и возвращать только новости, доступные текущему пользователю;
- сортировать по `COALESCE(scheduled_at, created_at) DESC`;
- запрашивать `limit + 1`, чтобы определить `has_more`;
- возвращать поля:
  - `id`;
  - `text`;
  - `files`;
  - `created_at`;
  - `scheduled_at`.

Добавить:

```python
async def get_published_for_web_by_id(
    self,
    global_user_id: str,
    news_id: int,
) -> dict | None:
```

Оба метода должны определять группы пользователя через существующие таблицы:

```text
users.id = global_user_id
  → user_accounts.user_id
  → user_accounts.platform + user_accounts.platform_user_id
  → subscribers.platform + subscribers.user_id
  → subscribers.manager_group / subscribers.coach_group
```

При соединении `user_accounts` с `subscribers` нужно учитывать одновременно:

```sql
subscribers.user_id = user_accounts.platform_user_id
AND subscribers.platform = user_accounts.platform
```

Это исключает ошибочное совпадение одинаковых числовых ID из Telegram и MAX.

Если у глобального пользователя несколько аккаунтов, принадлежность к группам объединяется:

- `manager_group = true`, если флаг установлен хотя бы у одного связанного аккаунта;
- `coach_group = true`, если флаг установлен хотя бы у одного связанного аккаунта.

Правила видимости новостей:

- `target_group IS NULL` или `target_group = 'all'` — доступно всем авторизованным пользователям;
- `target_group = 'manager_group'` — только пользователям с `manager_group = true`;
- `target_group = 'coach_group'` — только пользователям с `coach_group = true`;
- неизвестное значение `target_group` — не показывать;
- `status != 'sent'` — не показывать.

`get_published_for_web_by_id()` должен применять те же правила доступа, что и список. Нельзя позволять получить недоступную групповую новость прямым запросом по ID.

Существующие `get_all()` и `get_by_id()` не менять: они используются административным контуром.

### 2. Создать слой преобразования новостей

Новый файл: `web_bff/news.py`

Добавить DTO:

```python
class NewsListItem(BaseModel):
    id: int
    published_at: datetime
    title: str


class NewsDetail(BaseModel):
    id: int
    published_at: datetime
    title: str
    content: str
    files: list[NewsFile]


class NewsFile(BaseModel):
    name: str
```

Добавить функции:

```python
def to_news_list_item(row: dict) -> NewsListItem:
    ...


def to_news_detail(row: dict) -> NewsDetail:
    ...
```

Правила преобразования:

- `published_at = scheduled_at or created_at`;
- `title` — первая непустая строка новости;
- удалить HTML-теги из `title`;
- схлопнуть повторяющиеся пробелы;
- ограничить заголовок 120 символами;
- `content` — полный исходный текст новости;
- из `files` отдавать только безопасное имя файла;
- внутренние пути файлов не передавать в UI.

`unread` пока не добавлять: персонального состояния прочтения в БД нет.

### 3. Подключить `NewsStore` к BFF

Файл: `web_bff/app.py`

Изменить импорт:

```python
from bot.services.database import AdkApiClient, NewsStore, PostgresChatStore
```

В `lifespan()` после создания пула:

```python
app.state.news_store = NewsStore(pool)
```

Отдельный пул подключений создавать не нужно.

### 4. Реализовать список новостей

Файл: `web_bff/app.py`

```python
@app.get("/news")
async def get_news(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(require_user),
) -> NewsListResponse:
```

При вызове хранилища передать `user_id` как `global_user_id`:

```python
rows = await request.app.state.news_store.get_published_for_web(
    user_id,
    limit=limit,
    offset=offset,
)
```

Ответ:

```json
{
  "items": [
    {
      "id": 12,
      "published_at": "2026-08-27T12:30:00Z",
      "title": "Обновлены условия страхования по программам Fort Knox"
    }
  ],
  "limit": 20,
  "offset": 0,
  "has_more": false
}
```

`require_user` уже обеспечивает:

- проверку cookie;
- проверку существования пользователя;
- запрет для `is_blocked = true`.

### 5. Реализовать получение одной новости

Файл: `web_bff/app.py`

```python
@app.get("/news/{news_id}")
async def get_news_by_id(
    news_id: int,
    request: Request,
    user_id: str = Depends(require_user),
) -> NewsDetail:
```

При вызове хранилища передать `user_id` как `global_user_id`:

```python
row = await request.app.state.news_store.get_published_for_web_by_id(
    user_id,
    news_id,
)
```

Ответ:

```json
{
  "id": 12,
  "published_at": "2026-08-27T12:30:00Z",
  "title": "Обновлены условия страхования",
  "content": "Полный текст новости",
  "files": [
    {
      "name": "Условия страхования.pdf"
    }
  ]
}
```

Если новости нет, она ещё `pending` или недоступна группе текущего пользователя:

```http
404
{"detail":"news_not_found"}
```

Скачивание вложений в эту задачу не входит. Для него позже нужно выпускать защищённые URL через существующий `/files/{token}`.

## Тесты

Новый файл: `tests/unit/web_bff/test_news.py`.

Проверить:

- `/news` без cookie возвращает `401`;
- blocked-пользователь получает `403`;
- возвращаются только `sent`;
- `pending` не возвращаются;
- рассылка `all` доступна любому авторизованному пользователю;
- рассылка `manager_group` доступна только менеджеру;
- рассылка `coach_group` доступна только тренеру;
- группы объединяются по всем аккаунтам Telegram/MAX пользователя;
- при соединении аккаунтов учитывается `platform`;
- групповая новость недоступна по прямому `GET /news/{id}` пользователю без нужной группы;
- неизвестное значение `target_group` не возвращается;
- сортировка идёт от новых к старым;
- `limit` ограничен значением, например `100`;
- `has_more` корректен;
- неизвестный `news_id` возвращает `404`.

## Нужно ли менять другие части «Насти»

Для этой версии:

- `kb-manager` — менять не нужно;
- Telegram/MAX-боты — менять не нужно;
- механизм отправки рассылки — менять не нужно;
- существующие значения `manager_group` и `coach_group` берутся из `subscribers`;
- таблицу `news` — менять не нужно;
- Alembic-миграция — не нужна;
- WebUI обращается только к новым методам BFF.

Единственное изменение вне `web_bff` — два read-only метода в существующем `NewsStore` в `bot/services/database.py`. Они не меняют текущую работу рассылки.

## Что потребуется позже для самостоятельного Web-канала

Отдельной задачей:

- публикация новости в канал `web`;
- независимый от Telegram/MAX статус публикации;
- перенос групп пользователей на глобальный уровень, чтобы назначать группы пользователям без аккаунтов Telegram/MAX;
- таблица прочтений, например `news_reads(user_id, news_id, read_at)`;
- защищённое скачивание вложений;
- при необходимости browser push.

Эти изменения в текущую задачу не входят.
