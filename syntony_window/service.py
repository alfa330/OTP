"""Выдача окна Syntony за учётку iCORE — общая для страницы портала и для CRM.

Страница портала просит окно ручкой POST /api/syntony/window (routes.py): оператор там —
из сессии. CRM «Потока» просит его своим ключом через вход в окно CRM
(POST /api/wazzup/embed/launch с window: "syntony", wazzup/embed_auth.py): учётку за
пользователем CRM находит embed_auth (пара или ФИО), сюда приходит уже её id.

Владелец, 20.11.2026: окно, которое CRM получала от iCORE («Чаты ОП» с номером Help Taxi),
«переключить на syntony iframe и передать им». Номер ушёл из Wazzup, и CRM ставит у себя
окно Syntony — прямо в свой <iframe>, без портала iCORE посередине:

    CRM ──POST /api/wazzup/embed/launch {window: "syntony", …}──▶ iCORE ──POST /v1/iframe + ключ──▶ Syntony
    CRM ◀──────────────────────── { url } ─────────────────────── iCORE ◀──────── { url } ────────── Syntony

Оператор для Syntony — та же учётка iCORE (partnerUserId — её id, почта — из списка
доступа): у Syntony это тот же оператор, что открывает «Чаты Syntony» в портале, связь у
него одна.

Границы окна для CRM — те же, что у окна в портале, кроме QR:

* окно — только тем, кто в списке доступа (его ведёт супер-админ); за остальных запрос к
  Syntony не уходит вовсе: первый же запрос записал бы человека у Syntony оператором;
* QR не спрашиваем: сессии портала здесь нет (CRM ходит к нам сервером со своим ключом), а
  окно из CRM открывается без QR — решение владельца 20.10.2026 для окна CRM.

Ссылка одноразовая и живёт 60 секунд: модуль её не хранит, каждый запрос — новая ссылка.
"""

import logging

from . import access, client, queries, rules

logger = logging.getLogger(__name__)

# Срок ссылки по ТЗ Syntony (раздел 8): столько живёт ссылка, а не окно.
LINK_TTL_SECONDS = 60

PARTNER_NO_ACCESS_MESSAGE = ('Окно Syntony этой учётке iCORE не открыто: её нет в списке доступа. '
                             'Обратитесь к администратору iCORE.')


def ask(window, *, user_id, name, email, load_config=client.load_config,
        request_window=client.request_window):
    """Запросить ссылку за человека. None — ключ Syntony на сервере не задан."""
    config = load_config()
    if config is None:
        return None
    body = rules.build_request_body(window, user_id=user_id, name=name, email=email)
    return request_window(config, body)


def remember_link(db, store, user_id, reply, known_state, *, log=None, always=False):
    """Записать, что Syntony ответила о связи, — только если ответ о ней и он новый.

    always=True — и прежний ответ: так отмечается время проверки (сторож переспрашивает
    «не связан» раз в сутки, считая от неё). Окно портала и CRM пишут только перемену — на
    каждое открытие окна запись в базу не нужна.

    log — журнал того, кто выдал окно (ручка портала пишет в свой): по строке видно, откуда
    окно открывали."""
    state = rules.link_state_of(reply.status, reply.body)
    if not state or (state == known_state and not always):
        return
    try:
        with db._get_cursor() as cursor:
            store.set_link_state(cursor, user_id, state)
    except Exception:  # noqa: BLE001
        # Отметка о связи — справка для списка доступа; окно из-за неё не падает.
        (log or logger).exception('syntony_window: не записалось состояние связи пользователя %s', user_id)


def note_reply(what, user_id, reply, *, log=None):
    """Код ошибки в журнал: незнакомый — предупреждением (ТЗ, задача 5). Ни тела, ни ссылки."""
    code = reply.code
    if not code:
        return
    log = log or logger
    if code not in rules.SYNTONY_ERROR_CODES and code not in rules.ICORE_ERROR_CODES:
        log.warning('syntony_window: незнакомый код ошибки Syntony %r (HTTP %s, %s, пользователь %s)',
                    code[:64], reply.status, what, user_id)
    else:
        log.info('syntony_window: %s, пользователь %s — %s (HTTP %s)',
                 what, user_id, code, reply.status)


def partner_window_request(payload):
    """Тело запроса CRM → вход rules.clean_window_request.

    CRM можно проще, чем странице портала: номер водителя — одной строкой (phone) или
    числом, а scope не указывать — с номером это переписка водителя, без него — «Чаты».
    Остальные поля тела (crmUserId, window, что угодно ещё) сюда не попадают: оператора
    решает сервер, не CRM.
    """
    if not isinstance(payload, dict):
        return {}
    phones = payload.get('phones')
    if phones is None and payload.get('phone') is not None:
        phones = [payload.get('phone')]
    if isinstance(phones, (list, tuple)):
        phones = [str(item) if isinstance(item, int) and not isinstance(item, bool) else item
                  for item in phones]
    scope = payload.get('scope')
    if scope is None:
        scope = rules.SCOPE_PHONE if phones else rules.SCOPE_INBOX
    window = {'scope': scope, 'theme': payload.get('theme')}
    if phones is not None:
        window['phones'] = phones
    return window


def open_for_partner(db, user_id, payload, *, load_config=client.load_config,
                     request_window=client.request_window, store=queries):
    """Окно Syntony за учётку iCORE по запросу CRM → client.Reply (статус и тело как у Syntony).

    Порядок тот же, что у ручки портала: список доступа, разбор запроса, Syntony. Запрос к
    Syntony идёт без открытого соединения с базой: курсор закрыт до сети.
    """
    with db._get_cursor() as cursor:
        ctx = store.load_access_context(cursor, user_id)
    if not ctx or not access.can_request_window(ctx):
        return client.Reply(403, rules.error_payload(rules.CODE_NO_ACCESS, PARTNER_NO_ACCESS_MESSAGE))
    window, error = rules.clean_window_request(partner_window_request(payload))
    if error:
        return client.Reply(400, error)
    reply = ask(window, user_id=ctx['user_id'], name=ctx['name'], email=ctx['email'],
                load_config=load_config, request_window=request_window)
    if reply is None:
        reply = client.not_configured_reply()
    else:
        remember_link(db, store, ctx['user_id'], reply, ctx['link_state'])
    note_reply('окно CRM «%s»' % window['scope'], ctx['user_id'], reply)
    return reply
