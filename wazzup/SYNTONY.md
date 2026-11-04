# Пересылка вебхуков: Global, «Департамент», iTaxi, Salam Taxi и ещё 11 номеров → Syntony, Help Taxi → CRM «Потока»

Приёмник iCORE остаётся `/api/wazzup/webhook/<token>`. После сохранения сообщений
и статусов он записывает исходные события пересылаемых каналов в отдельную очередь
PostgreSQL и отвечает Wazzup. Внешний POST выполняется фоновым работником;
HTTP-запрос к получателю не держит транзакцию БД и не задерживает ответ приёмника.

Маршрутов шестнадцать (`wazzup/syntony.py: ROUTES`), у каждого свои каналы, свой адрес,
ключ и выключатель. Очередь и работник общие, колонка `route` очереди называет
получателя. Всё, что ниже сказано про Syntony, верно и для остальных маршрутов.

| Маршрут | Каналы | Настройки |
|---|---|---|
| `syntony` | Global (два канала ниже) | `SYNTONY_WEBHOOK_*` |
| `syntony_department` | `33bc674a-727f-4e2d-b350-da1832ecfdd5` — +77008541777 «Департамент» | `SYNTONY_DEPARTMENT_WEBHOOK_*` |
| `syntony_itaxi` | `771c4257-4f8f-45fb-8c6c-08b61ea7e7f5` — +77470943333 iTaxi | `SYNTONY_ITAXI_WEBHOOK_*` |
| `syntony_salam` | `34da6a88-2b9c-4c5a-be8d-072e89bfd440` — +77001551199 Salam Taxi | `SYNTONY_SALAM_WEBHOOK_*` |
| `syntony_global_1230` | `b88e8d3a-f3f4-4854-bbc8-076a0924d118` — Global, номер …1230 | `SYNTONY_GLOBAL_1230_WEBHOOK_*` |
| `syntony_itaxi_0880` | `ee3372c2-f15f-4ace-8176-dba1a3838bb3` — iTaxi, номер …0880 | `SYNTONY_ITAXI_0880_WEBHOOK_*` |
| `syntony_jana_1372` | `24ea28f0-ba8a-4be7-a9b9-6253ff3d39ef` — Jana Taxi, номер …1372 | `SYNTONY_JANA_1372_WEBHOOK_*` |
| `syntony_qazaq_9671` | `7fede3da-b56a-4af4-918f-092145066b61` — Qazaq, номер …9671 | `SYNTONY_QAZAQ_9671_WEBHOOK_*` |
| `syntony_amanat_2629` | `9f6271c6-5ef7-4687-a196-de82a4c2dc8f` — Аманат, номер …2629 | `SYNTONY_AMANAT_2629_WEBHOOK_*` |
| `syntony_department_7692` | `e28a8bf4-49e4-46c5-a915-23123439a4fc` — «Департамент», номер …7692 | `SYNTONY_DEPARTMENT_7692_WEBHOOK_*` |
| `syntony_dostoyny_6032` | `e24b4922-14f9-4fa3-8730-e5d804869f7e` — Достойный, номер …6032 | `SYNTONY_DOSTOYNY_6032_WEBHOOK_*` |
| `syntony_nol_4569` | `56701573-92a4-4121-af25-9be630900778` — Ноль, номер …4569 | `SYNTONY_NOL_4569_WEBHOOK_*` |
| `syntony_tenge_9716` | `c0055888-ab4a-4bd1-ab54-44fa8342202b` — Тенге, номер …9716 | `SYNTONY_TENGE_9716_WEBHOOK_*` |
| `syntony_registration_1223` | `c4e9f91a-5a3c-4110-8319-345ce0229650` — «Центр регистрации», номер …1223 | `SYNTONY_REGISTRATION_1223_WEBHOOK_*` |
| `syntony_chestny_0602` | `ec4209b1-c0f6-4cdd-9037-9ee18c34206d` — Честный, номер …0602 | `SYNTONY_CHESTNY_0602_WEBHOOK_*` |
| `potok_crm` | `6983db13-ae8e-4b27-b09d-6c10ae4f54cf` — +77070253322 «Help Taxi» | `POTOK_CRM_WEBHOOK_*` |

**«Департамент» → Syntony (30.10.2026).** Владелец: «отключить от чатов оп как и
глобал и пересылать все вебхуки на syntony прямо как глобал». В Syntony у канала своё
подключение («ДепТакси»), и различает она их по ключу: с ключом Global его сообщения
падали в подключение Global. Поэтому маршрут свой — тот же адрес Syntony, свой ключ и
выключатель. В «Чатах ОП» канал, как Global, только для чтения
(`pilot.py: EXCLUDED_CHANNELS`): ни отправки, ни заметок, ни карточки водителя;
счётчик «ждут ответа» его не ведёт, накопленное до исключения снято при старте схемы
(`unread.py`). Пересылка — с выкладки, прошлое не досылается.

**iTaxi и Salam Taxi → Syntony (04.11.2026).** Тем же порядком, что «Департамент»: у
каждого номера своё подключение в Syntony со своим ключом и свой маршрут; адрес — тот
же. Salam Taxi — таксопарк, не «доставка». В «Чатах ОП» оба номера только для чтения,
счётчик «ждут ответа» их не ведёт, накопленное снимается при старте схемы.

**Ещё 11 номеров → Syntony (04.11.2026).** Второй список Syntony, владелец: «все эти … тоже».
Тот же порядок: у каждого номера своё подключение со своим ключом и свой маршрут
(`syntony.py: SYNTONY_NUMBERS`), в «Чатах ОП» — только чтение. У Global, iTaxi и
«Департамента» теперь по два номера: ключ нового номера — только для него, прежний
маршрут и ключ не меняются, поэтому в имени нового маршрута последние цифры номера.
Полных номеров в коде и документации нет: страж `test_no_personal_data_in_repo` не
пропускает больше десяти разных номеров в файле; номер — по channelId в Wazzup.

**Help Taxi → CRM «Потока» (20.10.2026).** До 15.10.2026 номер жил в аккаунте
Wazzup «Поток», чей вебхук смотрит в CRM группы. С 15.10 номер в аккаунте `op`,
вебхук которого приходит в iCORE, и CRM этих событий больше не получала.
Пересылаются входящие, исходящие (в том числе ответы из «Чатов ОП»), правки,
удаления и статусы канала — в исходной форме вебхука Wazzup. Событий
`createContact`/`createDeal` нет: у аккаунта `op` эта подписка выключена, контакт
и сделку CRM заводит сама по первому сообщению номера.

**Автор ответа из «Чатов ОП» (20.10.2026).** Отправку ключом аккаунта Wazzup подписывает
как «Admin» без id. Перед отправкой в CRM маршрут подставляет в эхо наших отправок
`authorId`/`authorName` пользователя CRM — как делал Wazzup, когда «Поток» писал из его окна
в CRM, — и `icoreAuthor: {userId, name}` (учётка iCORE). Пару «учётка iCORE ↔ пользователь
CRM» сообщает сама CRM окну чатов (`wazzup/crm_identity.py`, таблица
`wazzup_crm_identities`); без пары `authorName` — имя сотрудника в iCORE. Эхо, пришедшее
раньше ответа Wazzup на отправку, ждёт автора до 20 секунд (повтор через 2 с, `last_error =
awaiting_author`). Эхо отправки из самого Wazzup (с `authorId`) и входящие не меняются.

## Границы пересылки

Только аккаунт `op` и следующие channelId:

- `99df6893-fb6b-4e1d-a78e-9e6e6b37abb2` — Global Taxi.
- `a4bccb5e-5d41-483d-b7c1-a1079685577d` — Global (старый канал).
- `33bc674a-727f-4e2d-b350-da1832ecfdd5` — «Департамент», +77008541777, WABA (с 30.10.2026).
- `771c4257-4f8f-45fb-8c6c-08b61ea7e7f5` — iTaxi, +77470943333, WABA (с 04.11.2026).
- `34da6a88-2b9c-4c5a-be8d-072e89bfd440` — Salam Taxi, +77001551199, WABA (с 04.11.2026).
- 11 каналов `SYNTONY_NUMBERS` из таблицы выше — номера WhatsApp, не WABA (с 04.11.2026).

Входящие, исходящие (`isEcho: true`), правки и удаления в `messages`, все
`statuses` (включая `edited` и `error`) и `channelsUpdates` проходят без
изменения полей объектов. Если весь пакет состоит из событий Global, сохраняются
исходные байты JSON. В смешанном пакете отбираются события Global; обёртка
сериализуется снова. Никаких дополнительных полей в JSON не добавляется.

Канал статуса без `channelId` определяется по `messageId` в том же пакете или
в `wazzup_messages` аккаунта `op`. Неизвестные статусы ждут появления сообщения
до 72 часов. После определения другого канала они удаляются из очереди без
отправки. События без определяемого канала (`createContact`, `createDeal`,
модерация шаблонов) не пересылаются. Аккаунт «Поток» не участвует.

Пересылается новый поток вебхуков с момента включения. История из БД не
восстанавливается и не отправляется. Подписки Wazzup интеграция не меняет.
Проверка 07.10.2026: включены только `messagesAndStatuses`; Global Taxi активен,
старый Global заблокирован. Служебный `{ "test": true }` подтверждается iCORE
без постановки в очередь.

## Настройка

Секреты задаются только на сервере; в Git и браузер не попадают:

```text
SYNTONY_WEBHOOK_ENABLED=true
SYNTONY_WEBHOOK_API_URL=<HTTPS endpoint Syntony>
SYNTONY_WEBHOOK_API_KEY=<ключ Syntony>
SYNTONY_WEBHOOK_AUTH_HEADER=Authorization
SYNTONY_WEBHOOK_AUTH_PREFIX=Bearer

SYNTONY_DEPARTMENT_WEBHOOK_ENABLED=true
SYNTONY_DEPARTMENT_WEBHOOK_API_URL=<тот же HTTPS endpoint Syntony>
SYNTONY_DEPARTMENT_WEBHOOK_API_KEY=<ключ подключения «ДепТакси» в Syntony>

SYNTONY_ITAXI_WEBHOOK_ENABLED=true
SYNTONY_ITAXI_WEBHOOK_API_URL=<тот же HTTPS endpoint Syntony>
SYNTONY_ITAXI_WEBHOOK_API_KEY=<ключ подключения iTaxi в Syntony>

SYNTONY_SALAM_WEBHOOK_ENABLED=true
SYNTONY_SALAM_WEBHOOK_API_URL=<тот же HTTPS endpoint Syntony>
SYNTONY_SALAM_WEBHOOK_API_KEY=<ключ подключения Salam Taxi в Syntony>

# Так же каждый маршрут SYNTONY_NUMBERS — своим префиксом из таблицы, например:
SYNTONY_JANA_1372_WEBHOOK_ENABLED=true
SYNTONY_JANA_1372_WEBHOOK_API_URL=<тот же HTTPS endpoint Syntony>
SYNTONY_JANA_1372_WEBHOOK_API_KEY=<ключ этого номера в Syntony>

POTOK_CRM_WEBHOOK_ENABLED=true
POTOK_CRM_WEBHOOK_API_URL=<HTTPS endpoint CRM «Потока»>
POTOK_CRM_WEBHOOK_API_KEY=<ключ, который проверяет CRM>
POTOK_CRM_WEBHOOK_AUTH_HEADER=Authorization
POTOK_CRM_WEBHOOK_AUTH_PREFIX=Bearer
```

Без `..._ENABLED=true` и полного набора маршрут молчит: события его каналов в
очередь не встают. Пересылка начинается с момента включения, прошлое не досылается.

В запросе: `POST`, `Content-Type: application/json`,
`Authorization: Bearer <ключ Syntony>` и стабильный `Idempotency-Key`.
Ключ Wazzup, входной секрет вебхука и заголовки исходного запроса не передаются.
Редиректы запрещены. Таймаут соединения — 3 секунды, чтения ответа — 10 секунд.
Ответ считается принятым при HTTP 2xx; содержимое ответа не читается.

Тест авторизации 07.10.2026: корректный Bearer-ключ — HTTP 200 и
`status: ignored_wazzup_service` для служебного JSON; неверный ключ — HTTP 401.
Тест не содержал телефона, текста сообщения или реальных вложений.

## Доставка и наблюдение

Схема `wazzup_syntony_outbox` создаётся при штатном старте приложения вместе
с Wazzup-схемой. Доставка использует lease на 90 секунд и `SKIP LOCKED`.
Повторный webhook подавляется по отпечатку исходного JSON; delivery fingerprint
стабилен при сетевых повторах. Ошибка записи очереди возвращает Wazzup HTTP 500,
чтобы исходный отправитель повторил запрос.

Сетевые ошибки и не-2xx повторяются с задержкой 5 секунд, затем удвоением до
1 часа; учитывается `Retry-After` при 429/503. Максимум 256 попыток, затем
`failed` для ручной проверки. После принятия JSON очищается из очереди.
Доставка имеет семантику «как минимум один раз»: при таймауте после принятия
или остановке процесса возможен дубль. Получателю следует учитывать
`Idempotency-Key` и идентификаторы сообщений Wazzup.

Технические записи `done`/`ignored` хранятся 7 дней, отпечатки входных пакетов
и оставшиеся записи — максимум 45 дней. Очистка запускается раз в час порциями.
Секреты, тексты сообщений, URL и тела ответов не пишутся в журналы работника.

Сводка без содержимого сообщений:

```sql
SELECT route, phase, count(*), min(created_at), max(completed_at)
FROM wazzup_syntony_outbox GROUP BY route, phase;

SELECT route, phase, last_error, count(*)
FROM wazzup_syntony_outbox
WHERE phase IN ('ready', 'resolve', 'failed')
GROUP BY route, phase, last_error;

SELECT route, count(*) AS delivered, max(delivered_at) AS last_delivery
FROM wazzup_syntony_outbox WHERE delivered_at IS NOT NULL GROUP BY route;
```

`receipt` — только отпечаток входного пакета, не отдельная HTTP-доставка.
`resolve` — ожидание канала для статуса. `done` может означать завершение такого
разрешения; только непустой `delivered_at` подтверждает успешный внешний POST.

Для паузы установить `SYNTONY_WEBHOOK_ENABLED=false` и перезапустить сервис:
HTTP-пересылка и приём новых событий в очередь остановятся. Уже накопленная
очередь сохранится и продолжит работу после включения. Вебхуки Wazzup и чаты
iCORE продолжат обрабатываться обычным приёмником.
