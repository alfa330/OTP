# Чаты OLX: ручка iCORE для партнёра

Версия 1 от 29.10.2026. Адрес API: `https://otp-2-fos4.onrender.com`.

iCORE ведёт чаты девяти кабинетов OLX (отклики кандидатов на объявления). Через
эту ручку партнёр получает то же, что видит наш сотрудник: кабинеты, список чатов,
переписку, журнал откликов, может ответить кандидату от имени кабинета и
подписаться на события.

## Как это устроено

- **У OLX нет вебхуков.** Новое в чатах узнаёт наш робот, опрашивая кабинеты раз
  в 30 секунд. Поэтому события партнёру шлёт iCORE, а не OLX, и задержка события
  относительно сообщения кандидата — до 30 секунд.
- **Ключ** выдаёт iCORE с областью «Чаты OLX»; передаётся заголовком
  `X-Api-Key`. Показывается один раз; хранить на сервере.
- **Время** везде в ISO 8601 с поясом (`2026-10-29T14:05:11+05:00`). Параметры
  `since`, `from`, `to` принимают ISO 8601 с поясом или без (без пояса — Алматы).
- **Один вызов — одна строка журнала** у нас: кто, когда, что спросил.
- Чтение переписки и отправка ответа ходят в OLX живьём и расходуют общий с
  роботом лимит запросов OLX. Не опрашивайте переписку по кругу — для «что
  нового» есть события.

## Кабинеты

```http
GET /api/olx/cabinets
X-Api-Key: <ключ>
```

```json
{"cabinets": [
  {"code": "global", "title": "Global", "olx_id": "161323828", "line_phone": "77008581230",
   "enabled": true, "state": "ok", "last_poll_at": "2026-10-29T14:05:02+05:00",
   "last_message_at": "2026-10-29T13:48:10+05:00", "last_lead_at": "2026-10-29T13:48:14+05:00",
   "stale": false}
]}
```

`code` — то, что передаётся в остальных ручках. `stale: true` — кабинет не
опрашивался больше 15 минут (робот стоит или кабинет отключился).

## Список чатов

```http
GET /api/olx/threads?cabinet=global&awaiting=1&since=2026-10-29T00:00:00%2B05:00&limit=50&offset=0
```

| Параметр | Что это |
|---|---|
| `cabinet` | код кабинета; без него — все девять |
| `awaiting` | `1` — только чаты, где кандидат ждёт ответа человека |
| `since` | только чаты, изменившиеся после этого момента (новое сообщение, сделка, метка ожидания) |
| `limit`, `offset` | страница, `limit` до 200, по умолчанию 50 |

```json
{"threads": [
  {"cabinet": "global", "cabinet_title": "Global", "thread_id": "4195536101",
   "advert_id": "1234567", "advert_title": "Водитель такси", "interlocutor_id": "88123",
   "interlocutor_name": "Айдар", "last_message_id": "9911", "last_message_at": "2026-10-29T13:48:10+05:00",
   "messages_seen": 3, "awaiting_human_since": null, "robot_replied_at": null,
   "phone": "77011234567", "amo_lead_id": 123456, "first_seen_at": "2026-10-28T09:10:00+05:00",
   "updated_at": "2026-10-29T13:48:14+05:00", "url": "https://www.olx.kz/mojolx/wiadomosci/#!thread=4195536101"}
 ],
 "pagination": {"limit": 50, "offset": 0, "total": 1, "has_more": false}}
```

Это чаты, которые робот видел с момента запуска; историю старше его горизонта в
списке искать не нужно — она есть в самом OLX.

## Переписка

```http
GET /api/olx/threads/global/4195536101
```

```json
{"cabinet": {"code": "global", "title": "Global", "olx_id": "161323828", "line_phone": "77008581230"},
 "thread": {"id": "4195536101", "advert_id": "1234567", "advert_title": "Водитель такси",
            "interlocutor_id": "88123", "interlocutor_name": "Айдар",
            "awaiting_human_since": null, "phone": "77011234567", "amo_lead_id": 123456},
 "messages": [
   {"id": "9910", "direction": "in", "text": "Здравствуйте, хочу работать, 8 701 123 45 67",
    "at": "2026-10-29T13:48:10+05:00", "author": null, "attachments": [], "cvs": [], "failed": false, "error": null},
   {"id": "9911", "direction": "out", "text": "Здравствуйте! …", "at": "2026-10-29T13:48:40+05:00",
    "author": "Робот", "attachments": [], "cvs": [], "failed": false, "error": null}
 ],
 "events": [
   {"id": "event-77", "event": "lead_created", "text": "Создана сделка", "at": "2026-10-29T13:48:14+05:00",
    "amo_lead_id": 123456, "phone": "77011234567", "error": null, "latency_ms": 4100}
 ],
 "url": "https://www.olx.kz/mojolx/wiadomosci/#!thread=4195536101"}
```

`direction: in` — кандидат, `out` — кабинет. `author` у исходящих: `Робот`
(автоответ), имя сотрудника, `Партнёр: <название ключа>` (отправлено через эту
ручку) или `Из кабинета OLX` (написано прямо в кабинете). `failed: true` — наш
ответ не доехал, `error` говорит почему. `events` — отметки робота в той же ленте.

## Ответ кандидату

```http
POST /api/olx/threads/global/4195536101/messages
Content-Type: application/json

{"text": "Добрый день! Приходите в офис на Жамбыла, 172В с 9 до 19."}
```

```json
{"status": "sent", "cabinet": "global", "thread_id": "4195536101",
 "text": "Добрый день! …", "author": "Партнёр: CRM «Потока»", "outbound_id": 5120}
```

Сообщение уходит от имени кабинета; в переписке оно подписано названием ключа.
Один и тот же текст в один чат за 15 секунд второй раз не отправляется (`409`).
После ответа робот больше не шлёт в этот чат автоответ и снимает метку «ждёт
человека».

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `OLX_API_BAD_REQUEST` | пустой или слишком длинный текст (`reason`: `empty`, `too_long`) |
| 409 | `OLX_API_DUPLICATE` | такое сообщение в этот чат уже отправляется |
| 429 | `OLX_API_RATE_LIMITED` | OLX ограничил частоту; ждать `Retry-After` |
| 503 | `OLX_API_CABINET_UNAVAILABLE` | кабинет отключён или требует входа владельца (`reason`) |
| 502 | `OLX_API_UPSTREAM` | OLX ответил ошибкой |

## Журнал откликов

```http
GET /api/olx/journal?cabinet=global&from=2026-10-01&to=2026-11-01&result=lead_created&limit=100&offset=0
```

Строка на каждое обращение, разобранное роботом: `result` —
`lead_created` (сделка в amoCRM), `manual_review` (сделка, номер требует
проверки), `duplicate` (повтор за сутки), `canned_reply` (автоответ без номера),
`needs_human` (ждёт ответа человека), `human_reply` (ответил сотрудник или
партнёр), `error`. В строке: кабинет, чат, время сообщения, номер до и после
нормализации, сделка и контакт amoCRM, задержка, отрывок текста.

## События: подписка

```http
PUT /api/olx/webhook
Content-Type: application/json

{"url": "https://crm.example.kz/icore/olx", "secret": "<16–200 знаков ASCII>",
 "events": ["message.received", "message.sent"]}
```

- `url` — только `https://`, без логина и пароля в адресе.
- `secret` — ваш секрет для подписи; можно не присылать, тогда его выпустит
  iCORE и вернёт **один раз** в ответе (`secret`).
- `events` — какие события слать; пусто или нет поля — все.

Ответ: `{"status": "saved", "subscription": {...}, "signature": {...}}`.
`GET /api/olx/webhook` показывает подписку и счётчики доставок
(`deliveries: {ready, done, failed}`), `DELETE /api/olx/webhook` снимает её,
`GET /api/olx/webhook/deliveries?limit=50` — последние доставки с исходами,
`POST /api/olx/webhook/test` — прислать событие `ping` для проверки адреса.

### Как приходит событие

```http
POST <ваш url>
Content-Type: application/json
X-Partner-Event: message.received
X-Partner-Delivery: 7e4c0d6c-0d8c-4a9b-9b1d-2f0b2c6b7c11
Idempotency-Key: 7e4c0d6c-0d8c-4a9b-9b1d-2f0b2c6b7c11
X-Partner-Timestamp: 1761735911
X-Partner-Signature: sha256=…

{"event":"message.received","id":"7e4c0d6c-…","created_at":"2026-10-29T09:05:11.312+00:00","data":{…}}
```

Подпись: `sha256=hex(HMAC-SHA256(secret, timestamp + "." + body))`, где `body` —
байты тела запроса как есть, `timestamp` — значение `X-Partner-Timestamp`.

```python
import hmac, hashlib
def verify(secret, timestamp, body: bytes, signature):
    expected = 'sha256=' + hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Отвечайте `2xx` сразу, обработку делайте после ответа. Не-`2xx` и таймаут
(3 с на соединение, 10 с на ответ) повторяются с задержкой 5 секунд, удвоением
до часа, до 30 попыток; `Retry-After` при `429`/`503` уважается. Доставка «как
минимум один раз»: повтор возможен, опирайтесь на `id` события. Порядок между
событиями не гарантируется — у каждого есть время.

### Событие `message.received`

```json
{"cabinet": {"code": "global", "title": "Global", "olx_id": "161323828", "line_phone": "77008581230"},
 "thread": {"id": "4195536101", "advert_id": "1234567", "advert_title": "Водитель такси",
            "interlocutor_id": "88123", "interlocutor_name": "Айдар",
            "awaiting_human_since": null, "phone": null, "amo_lead_id": null},
 "message": {"id": "9910", "text": "Здравствуйте, хочу работать, 8 701 123 45 67",
             "at": "2026-10-29T13:48:10+05:00", "attachments": [], "cvs": [],
             "phone": "77011234567", "phone_raw": "8 701 123 45 67"},
 "robot": {"outcome": "lead_created"}}
```

`robot.outcome` — что робот уже сделал с этим сообщением (значения те же, что
`result` в журнале): по `lead_created` и `manual_review` сделка в amoCRM уже есть,
по `canned_reply` кандидату ушёл автоответ, `needs_human` — кандидат ждёт живого
ответа, `duplicate` — повтор за сутки. `thread.*` отражает состояние чата на
момент опроса, то есть **до** разбора этого сообщения.

### Событие `message.sent`

```json
{"cabinet": {"code": "global", "title": "Global", "olx_id": "161323828", "line_phone": "77008581230"},
 "thread": {"id": "4195536101", "advert_id": null, "advert_title": null, "interlocutor_id": null,
            "interlocutor_name": null, "awaiting_human_since": null, "phone": null, "amo_lead_id": null},
 "message": {"text": "Здравствуйте! …", "at": "2026-10-29T13:48:40+05:00", "kind": "canned",
             "source": "robot", "author": "Робот"}}
```

`kind`: `canned` — автоответ робота, `portal` — ответ человека или партнёра;
`source`: `robot`, `portal` (сотрудник из раздела), `partner` (через эту ручку,
`author` — название ключа).

### Событие `ping`

Приходит по `POST /api/olx/webhook/test`; `data.cabinets` — список кабинетов.

## Ошибки

Тело: `{"error": "…", "code": "…"}`.

| HTTP | `code` | Что значит |
|---|---|---|
| 400 | `OLX_API_BAD_REQUEST` | неверные параметры или тело; текст в `error` |
| 401 | `OLX_API_UNAUTHORIZED` | ключа нет, он неверный или отозван |
| 403 | `OLX_API_FORBIDDEN` | ключу не открыта область «Чаты OLX» |
| 404 | `OLX_API_UNKNOWN_CABINET` | такого кабинета нет — см. `/api/olx/cabinets` |
| 404 | `OLX_API_NO_SUBSCRIPTION` | подписки ещё нет |
| 429 | `OLX_API_LIMIT` | больше `OLX_API_PER_MINUTE` (120) вызовов за минуту; ждать `Retry-After` |
| 500 | `OLX_API_FAILED` | внутренняя ошибка; повторить позже |
| 503 | `OLX_API_NOT_CONFIGURED` | ручка на сервере не развёрнута |

## Пределы

- 120 вызовов в минуту на ключ; список чатов — до 200 строк за страницу, журнал
  — до 500; тело запроса — до 64 КБ.
- Длину сообщения кандидату ограничивает OLX; при превышении — `400` с `reason: too_long`.

## Что мы храним

Журнал вызовов ключа: время, IP, ручка, исход, сводка (чат, длина ответа, адрес
подписки) и для ответов — сам текст. Доставки событий: исходы и ошибки, тело
события до 7 дней после доставки, недоставленное — 30 дней. Секрет подписки
хранится открыто: им подписывается каждая доставка.
