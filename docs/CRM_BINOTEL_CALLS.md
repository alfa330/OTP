# Звонки iCORE Phone и Binotel в CRM: воронка регионов

Версия 1.0 от 05.11.2026. Адрес API: `https://api.icorehub.kz`. Прежний адрес
`https://otp-2-fos4.onrender.com` пока работает так же.

Задание для разработчика CRM (и его ИИ). Со стороны iCORE всё готово и работает. Ниже —
что сделать в CRM, чтобы менеджеры «Фронт офисов» звонили водителям из воронки регионов
кнопкой в CRM, а CRM сама и сразу видела каждую попытку, исход и разговор.

## Как это устроено

- **Звонит iCORE Phone менеджера.** Кнопка «Позвонить» в CRM открывает ссылку
  `icore-phone:call?number=…`. Телефон на компьютере менеджера набирает номер со своей
  линии Binotel (кабинет удалённого КЦ). Браузер не сообщает, начался ли звонок, — результат
  приходит отдельно (п. 2).
- **Каждый звонок приходит в CRM событием сразу после окончания**, обычно через 1–3 секунды:
  кто звонил, кому, исход (дозвонился, занято, не ответил, не состоялся), сколько ждал ответа,
  сколько говорил, есть ли запись. Это и неудачные попытки, и разговоры, и входящие звонки
  на линию менеджера.
- **Чьи звонки.** Только менеджеров отдела «Фронт офисы», сидящих на линиях удалённого КЦ.
  Звонки, сделанные в режиме «Удалённый КЦ» (вкладка «Обзвон» телефона), и звонки других
  отделов в CRM не приходят.
- **Момента набора нет.** Событие одно — после окончания звонка. Пока звонок идёт, CRM о нём
  не знает (кроме того, что менеджер сам нажал кнопку).
- **Ключ** с доступом «Звонки Binotel» передаётся заголовком `X-Api-Key`. Ключ присылаем
  отдельным сообщением. Хранить только на сервере CRM: в браузер, в журналы и в git он
  попадать не должен.
- **Время** везде в ISO 8601 с поясом Алматы: `2026-11-05T11:00:00+05:00`. Параметры
  `from`, `to`, `updated_since` принимают ISO 8601 с поясом или без (без пояса — Алматы).
- **Номер водителя** — `+7XXXXXXXXXX` (поле `phone`). В том же виде нормализуйте номера
  своей базы: по нему звонок привязывается к лиду.

Проверка ключа:

```http
GET /api/calls/ping
X-Api-Key: <ключ>
```

```json
{"status": "ok", "key": "CRM", "events": ["call.completed"],
 "results": ["answered", "busy", "no_answer", "failed"],
 "limits": {"per_minute": 120, "page_max": 500}}
```

## 1. Кнопка «Позвонить»

Ссылка: `icore-phone:call?number=<номер>`, номер — с `+`, закодированным как `%2B`:

```
icore-phone:call?number=%2B77011234567
```

`prefix` не передавайте: линии Binotel его не понимают. Без `+` линия Binotel номер не
наберёт.

Открывайте ссылку через скрытый iframe — страница CRM никуда не уходит, вопроса «Покинуть
страницу?» нет. Вызывать **прямо в обработчике клика**, до любого `await`: браузер запускает
внешнее приложение только в ответ на действие пользователя.

```js
// Номер из карточки → +7XXXXXXXXXX. null — номер не казахстанский/не российский.
function toE164(raw) {
  let d = String(raw || '').replace(/\D/g, '');
  if (d.length === 11 && (d[0] === '7' || d[0] === '8')) d = d.slice(1);
  return d.length === 10 && d[0] !== '0' ? '+7' + d : null;
}

function callWithIcorePhone(rawPhone) {
  const phone = toE164(rawPhone);
  if (!phone) throw new Error('Неверный номер');
  const frame = document.createElement('iframe');
  frame.style.display = 'none';
  frame.src = 'icore-phone:call?number=' + encodeURIComponent(phone);
  document.body.appendChild(frame);
  setTimeout(() => frame.remove(), 2000);
  return phone;
}

button.addEventListener('click', () => {
  const phone = callWithIcorePhone(lead.phone);           // сначала звонок
  showToast('Звонок передан в iCORE Phone. Если ничего не произошло — проверьте, что телефон открыт.');
  markPendingCall({ leadId: lead.id, phone, at: Date.now() }); // потом всё остальное (п. 2.4)
});
```

- Если у сайта CRM задан Content-Security-Policy с `frame-src`, `child-src` или
  `default-src`, добавьте туда схему: `frame-src 'self' icore-phone:`.
- Не открывайте ссылку в новой вкладке (`target="_blank"`, `window.open`).
- При первом клике браузер спросит «Открыть iCORE Phone?». На `https://` у вопроса есть
  галочка «Всегда разрешать» — после неё клик сразу начинает звонок.

**Что должно быть у менеджера:**
- iCORE Phone установлен установщиком и запущен, вход выполнен. Закрытый телефон ссылка не
  запускает — он покажет «iCORE Phone не запущен».
- **Телефон в обычном режиме, не в «Удалённом КЦ».** Менеджер фронт-офиса переключает режим
  сам: меню статуса (кнопка-пилюля вверху окна) → пункт «Удалённый КЦ». Галочка стоит —
  обзвон удалённого КЦ, ссылки из CRM не принимаются (телефон подскажет снять галочку). Галочки
  нет — обычный телефон, кнопка CRM работает. При смене режима телефон перезапускается сам за
  пару секунд, вход не нужен. Пункт есть в iCORE Phone 3.22.45 и новее (обновление ставится
  само).
- Текущий режим каждого менеджера CRM может узнать заранее — `GET /api/calls/operators`
  (поле `phone_mode`, п. 6) — и предупредить до звонка.
- Телефон откажет и в статусах «Перерыв», «Тренинг», «Технический перерыв», и когда уже идёт
  звонок; причину он покажет менеджеру сам.

## 2. События: подписка

CRM даёт адрес своего приёмника — iCORE шлёт туда каждый звонок.

```http
PUT /api/calls/webhook
X-Api-Key: <ключ>
Content-Type: application/json

{"url": "https://backend.yataxi.kz/api/integrations/icore/calls"}
```

| Поле | Что это |
|---|---|
| `url` | только `https://`, без логина и пароля в адресе, редиректы не выполняются |
| `secret` | необязательно, 16–200 печатных знаков ASCII. Не передан — выпустим сами и вернём **один раз** в ответе (`secret`) |
| `events` | необязательно; сейчас событие одно — `call.completed` |

```json
{"status": "saved", "secret": "…показывается один раз…",
 "subscription": {"url": "https://backend.yataxi.kz/api/integrations/icore/calls",
                  "events": ["call.completed"], "enabled": true, "failures_in_row": 0},
 "signature": {"header": "X-Partner-Signature", "timestamp_header": "X-Partner-Timestamp",
               "scheme": "sha256=hex(hmac_sha256(secret, timestamp + \".\" + body))"}}
```

- `GET /api/calls/webhook` — подписка и счётчики доставок (`ready`, `done`, `failed`).
- `POST /api/calls/webhook/test` — прислать событие `ping` на адрес подписки.
- `GET /api/calls/webhook/deliveries?limit=50` — последние доставки: статус ответа, ошибка,
  число попыток.
- `DELETE /api/calls/webhook` — снять подписку. Повторный `PUT` меняет адрес и секрет.

### 2.1. Как приходит событие

```http
POST <url подписки>
Content-Type: application/json
X-Partner-Event: call.completed
X-Partner-Delivery: 8b0e5c2a-…
Idempotency-Key: 8b0e5c2a-…
X-Partner-Timestamp: 1793858461
X-Partner-Signature: sha256=3f6c…

{"event":"call.completed","id":"8b0e5c2a-…","created_at":"2026-11-05T06:01:01+00:00","data":{"call":{…}}}
```

- **Проверяйте подпись**: HMAC-SHA256 секретом подписки от строки
  `<X-Partner-Timestamp> + "." + <тело запроса байт в байт>`, в hex, с приставкой `sha256=`.
  Отбрасывайте события с меткой времени старше 5 минут. Тело берите сырым — до разбора JSON.
- **Отвечайте 2xx быстро** (не дольше 10 секунд; соединение — 3 секунды): сохранили — 200,
  дальше обрабатывайте у себя. Не-2xx и таймаут повторяются: через 5, 10, 20, 40 секунд и т. д.
  с удвоением до часа, всего до 30 попыток (около 20 часов). Ответ на `429`/`503` с
  `Retry-After` уважается.
- **Одно событие может прийти дважды** (повтор после таймаута). У события свой `id` (он же
  `Idempotency-Key`), у звонка — `data.call.id`. Сохраняйте звонок по `data.call.id`
  (вставка или обновление).
- Тот же звонок приходит повторно и тогда, когда iCORE его уточнил (например, Binotel
  прислал разговор длиннее). Берите последнюю версию по `data.call.updated_at`.

Проверка подписи, Node.js (Express):

```js
const crypto = require('crypto');

app.post('/api/integrations/icore/calls', express.raw({ type: 'application/json' }), (req, res) => {
  const ts = req.get('X-Partner-Timestamp') || '';
  const got = req.get('X-Partner-Signature') || '';
  const want = 'sha256=' + crypto.createHmac('sha256', process.env.ICORE_CALLS_SECRET)
    .update(ts + '.').update(req.body).digest('hex');
  const fresh = Math.abs(Date.now() / 1000 - Number(ts)) < 300;
  if (!fresh || got.length !== want.length
      || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want))) {
    return res.sendStatus(401);
  }
  const event = JSON.parse(req.body.toString('utf8'));
  if (event.event === 'call.completed') saveCall(event.data.call);  // upsert по call.id
  res.sendStatus(200);
});
```

PHP:

```php
$body = file_get_contents('php://input');
$ts = $_SERVER['HTTP_X_PARTNER_TIMESTAMP'] ?? '';
$want = 'sha256=' . hash_hmac('sha256', $ts . '.' . $body, getenv('ICORE_CALLS_SECRET'));
if (abs(time() - (int)$ts) > 300 || !hash_equals($want, $_SERVER['HTTP_X_PARTNER_SIGNATURE'] ?? '')) {
    http_response_code(401); exit;
}
$event = json_decode($body, true);
```

### 2.2. Событие `call.completed`

```json
{
  "event": "call.completed",
  "id": "8b0e5c2a-6f1d-4c3e-9a77-2f7f2a0c1d55",
  "created_at": "2026-11-05T06:01:01+00:00",
  "data": {
    "call": {
      "id": "7890123456",
      "direction": "outgoing",
      "result": "answered",
      "talked": true,
      "disposition": "ANSWER",
      "phone": "+77011234567",
      "phone_raw": "87011234567",
      "line": "915",
      "operator": {"id": 801, "name": "Регионова Алия", "email": "alia@example.kz",
                   "department": "Фронт офисы"},
      "started_at": "2026-11-05T11:00:00+05:00",
      "answered_at": "2026-11-05T11:00:12+05:00",
      "ended_at": "2026-11-05T11:00:59+05:00",
      "wait_sec": 12,
      "talk_sec": 47,
      "has_recording": true,
      "source": "webhook",
      "received_at": "2026-11-05T11:01:01+05:00",
      "updated_at": "2026-11-05T11:01:01+05:00"
    }
  }
}
```

| Поле | Что это |
|---|---|
| `id` | id звонка в Binotel (`generalCallID`), строка. Ключ для сохранения |
| `direction` | `outgoing` — звонил менеджер, `incoming` — позвонили на его линию |
| `result` | `answered` — ответили; `busy` — занято; `no_answer` — не взяли трубку или менеджер сбросил до ответа; `failed` — звонок не состоялся (номер недоступен, ошибка сети) |
| `talked` | был разговор: `answered` и хотя бы секунда разговора |
| `disposition` | исход как его пишет Binotel (`ANSWER`, `BUSY`, `NOANSWER`, `CANCEL`, …) — для журнала |
| `phone` | номер водителя `+7XXXXXXXXXX`; `null`, если номер не такого вида (тогда смотрите `phone_raw`) |
| `phone_raw` | номер цифрами, как его прислал Binotel |
| `line` | внутренний номер линии Binotel, с которой шёл звонок |
| `operator` | менеджер, сидевший на линии в момент звонка: id учётки iCORE, ФИО, почта (= логин менеджера в CRM), отдел |
| `started_at` | начало звонка |
| `answered_at` | когда ответили; `null`, если не ответили |
| `ended_at` | конец звонка |
| `wait_sec` | сколько ждали ответа (гудки), с |
| `talk_sec` | длительность разговора, с |
| `has_recording` | есть ли запись разговора — ссылку брать в п. 5 |
| `source` | `webhook` — пришло от Binotel сразу; `history` — догнано сверкой с историей Binotel (вебхук не дошёл), такое событие может прийти с опозданием до 10 минут |
| `received_at`, `updated_at` | когда iCORE получил звонок и когда последний раз его уточнял |

**Как показывать в воронке:** каждое событие — попытка звонка. Исход попытки — `result`.
Разговор состоялся — `talked: true`, его длительность — `talk_sec`. Для серии недозвонов
считайте попытки с `talked: false`.

### 2.3. Событие `ping`

Приходит только по `POST /api/calls/webhook/test`:
`{"event": "ping", "data": {"note": "Проверка адреса приёмника вебхука iCORE", "events": ["call.completed"]}}`.

### 2.4. Как привязать звонок к лиду

1. Менеджер: `operator.email` = логин менеджера в CRM (так же уже сверяется выгрузка
   `region-call-stats`). Не нашли по почте — по ФИО.
2. Лид: по `phone` среди лидов воронки регионов.
3. Если номер есть в нескольких лидах — берите тот, где этот менеджер нажал «Позвонить» по
   этому номеру не раньше чем за 15 минут до `started_at` (запоминайте нажатие у себя, см.
   `markPendingCall` в п. 1). Нет такого нажатия (менеджер набрал руками, водитель
   перезвонил) — последний открытый лид с этим номером.

## 3. История звонков

Догнать пропущенное (CRM лежала дольше 20 часов, первичная загрузка) и сверка.

```http
GET /api/calls?from=2026-11-05T00:00:00&to=2026-11-06T00:00:00&limit=500&offset=0
X-Api-Key: <ключ>
```

| Параметр | Что это |
|---|---|
| `from`, `to` | начало звонка: `from` включительно, `to` — нет |
| `updated_since` | только звонки, полученные или уточнённые после этого момента — удобно для сверки по расписанию |
| `phone` | номер водителя в любом виде (`87011234567`, `+7 701 123 45 67`) |
| `operator` | id учётки iCORE или почта менеджера |
| `line` | внутренний номер линии |
| `result` | `answered` / `busy` / `no_answer` / `failed` |
| `direction` | `outgoing` / `incoming` |
| `limit`, `offset` | страница: `limit` до 500 (по умолчанию 100) |

```json
{"calls": [{"id": "7890123456", "direction": "outgoing", "result": "answered", "…": "…"}],
 "pagination": {"limit": 500, "offset": 0, "total": 37, "has_more": false}}
```

Звонки — от новых к старым, в том же виде, что `data.call` события. История хранится 90 дней
и начинается с 05.11.2026.

## 4. Один звонок

```http
GET /api/calls/7890123456
```

`{"call": {…}}`; неизвестный звонок — `404 CALLS_API_NOT_FOUND`.

## 5. Запись разговора

```http
GET /api/calls/7890123456/recording
```

```json
{"id": "7890123456", "url": "https://…подписанная ссылка…", "talk_sec": 47}
```

- Ссылка подписанная и живёт около часа. Не сохраняйте её: запрашивайте, когда менеджер
  нажимает «Слушать», и отдавайте браузеру (`<audio src>` или переход).
- `404 CALLS_API_NO_RECORDING` — разговора не было; `409 CALLS_API_RECORDING_NOT_READY` —
  АТС ещё загружает запись (первую минуту после звонка), повторите через `Retry-After`;
  `503 CALLS_API_BUSY` — АТС просит подождать несколько секунд.

## 6. Менеджеры на линиях

```http
GET /api/calls/operators
```

```json
{"operators": [
  {"id": 801, "name": "Регионова Алия", "email": "alia@example.kz", "department": "Фронт офисы",
   "line": "915", "phone_mode": "phone", "seated_at": "2026-11-01T09:00:00+05:00"}
]}
```

`phone_mode`: `phone` — обычный телефон, кнопка CRM работает; `remote_cc` — режим удалённого
КЦ, ссылку телефон не примет. Режим меняется с перезапуска телефона, поэтому значение
может отставать на несколько секунд. Список — кто сидит на линии сейчас.

## 7. Ошибки и пределы

Ошибка — JSON `{"error": "текст", "code": "…"}`.

| Код ответа | `code` | Что значит |
|---|---|---|
| 401 | `CALLS_API_UNAUTHORIZED` | ключ не принят (нет заголовка, неверный или отозван) |
| 403 | `CALLS_API_FORBIDDEN` | ключ настоящий, но звонки ему не открыты |
| 400 | `CALLS_API_BAD_REQUEST` | неверный параметр (текст — какой) |
| 404 | `CALLS_API_NOT_FOUND`, `CALLS_API_NO_SUBSCRIPTION`, `CALLS_API_NO_RECORDING` | нет звонка / подписки / записи |
| 409 | `CALLS_API_RECORDING_NOT_READY`, `CALLS_API_SUBSCRIPTION_DISABLED` | запись ещё не готова / подписка выключена |
| 429 | `CALLS_API_LIMIT` | больше 120 вызовов в минуту на ключ — ждать `Retry-After` |
| 502, 503 | `CALLS_API_UPSTREAM`, `CALLS_API_BUSY`, `CALLS_API_NOT_CONFIGURED` | АТС или сервер временно недоступны — повторить позже |
| 500 | `CALLS_API_FAILED` | внутренняя ошибка — повторить позже |

Каждый вызов ключа iCORE записывает в журнал (что спрошено, ответ, время). Опрашивать
историю по кругу не нужно: события приходят сами, сверку по `updated_since` достаточно
делать раз в 10–15 минут.

## 8. Чек-лист запуска

1. Ключ — в настройках сервера CRM. `GET /api/calls/ping` → `200`.
2. Приёмник событий с проверкой подписи. `PUT /api/calls/webhook` → сохранить `secret`.
3. `POST /api/calls/webhook/test` → в приёмник пришёл `ping`, ответ 200;
   `GET /api/calls/webhook/deliveries` → у доставки `state: done`.
4. Кнопка «Позвонить» в карточке лида воронки регионов (п. 1).
5. Пробный звонок с компьютера менеджера фронт-офиса (телефон в обычном режиме, без галочки
   «Удалённый КЦ»): через несколько секунд после отбоя в приёмник пришёл `call.completed`,
   звонок встал в карточку лида, у разговора открывается запись.
6. Сверка по расписанию: `GET /api/calls?updated_since=<время прошлой сверки минус 5 минут>`.
