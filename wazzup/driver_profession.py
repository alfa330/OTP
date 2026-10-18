"""Профессия водителя для карточки водителя «Чатов ОП» — из кабинета Яндекс Fleet.

Просьба владельца 18.10.2026: «чтобы в карточках водителей отображалось, кто
водитель по профессии: мото курьер, авто курьер и т.д.». В CRM yataxi профессии
нет (в схеме driver-info её нет ни в каком виде, проверено 18.10.2026), а кабинет
хранит её у исполнителя:
  * карточка исполнителя POST /api/fleet/router/v1/cards/driver/details →
    driver.driver_profile.profession_id («taxi/driver», «cargo/courier/on-car»,
    «cargo/courier/on-motorcycle», «cargo/courier/on-foot», …). Это та же ручка,
    которой «Провайдер ЭДО» читает карточки (fleet_edm/client.py), и то же поле,
    по которому сам кабинет решает, как показать профиль исполнителя;
  * подписи по-русски — справочник профессий парка GET …/existing-professions
    ([{id, name}]), по нему «Рассылки» строят фильтр.

Учётка. Сперва общая сессия «Провайдера ЭДО»: ею карточки исполнителей читаются
каждый день. Не приняли её, у неё нет прав на этот парк или кабинет отказал ей
иначе (4xx) — служебный аккаунт «Рассылок», если он подключён. Обе сессии здесь
только читаются.

Цена. Профессия с подписью живёт в базе неделю (кэш общий для всех процессов):
повторное открытие карточки — один SELECT, а кабинет спрашивается один раз на
водителя — при первом открытии его карточки, одним запросом (медиана ручек
кабинета 0,35 с). Справочник подписей нужен, только когда такой профессии нет ни
в одном уже сохранённом справочнике, — то есть почти никогда. Запрос не дольше
REQUEST_TIMEOUT; разовый 5xx или 429 повторяется сразу один раз (так кабинет и
отвечает — поштучно), таймаут — нет. Кабинет не ответил — в карточке просто нет
профессии (или прежняя, если была), а сам кабинет какое-то время не спрашивается
(FAILURE_PAUSE, у непринятой сессии SESSION_PAUSE): один сбой не превращается в
задержку каждой следующей карточки.

Лог. Каждый отказ — с тем, какой это был запрос, какой учёткой и что ответил
кабинет (ID в тексте обрезаны), каждый ответ — одной строкой INFO. 18.10.2026
первая же проба на проде упала с голым «FleetError», и по логу было не понять,
что случилось.
"""
import hashlib
import json
import logging
import re
import threading
import time

from driver_mailings import queries as mailing_session
from driver_mailings.client import PATH_PROFESSIONS, MailingsClient
from fleet_edm import queries as edm_session
from fleet_edm.client import PATH_CARD, FleetAccessDenied, FleetError, FleetSessionExpired

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 3
CONCURRENCY = 2
FOUND_TTL = 7 * 86400
# Водителя нет в этом парке или подписи профессии не нашлось — спросим раньше.
MISSING_TTL = 6 * 3600
NAMES_TTL = 7 * 86400              # справочник одного парка — не чаще раза в неделю
RETRY_DELAY = 0.3                  # перед повтором разового 5xx или 429
FAILURE_PAUSE = 60                 # сеть, 429, 5xx: кабинет сейчас не в духе
SESSION_PAUSE = 600                # сессию не приняли — новые куки приносит человек
REFUSED_PAUSE = 600                # иной отказ 4xx этой учётке на этот парк
DENIED_PAUSE = 3600                # нет прав на парк — сами они не появятся

_HEX32 = re.compile(r'^[0-9a-f]{32}$')
_IDS = re.compile(r'[0-9a-f]{32}')
# Так транспорт (fleet_edm.client) называет разовый 5xx и 429 после попыток.
_TRANSIENT = re.compile(r'попыток: (?:HTTP 5\d\d|429\b)')
_CLIENT_ERROR = re.compile(r'^Fleet вернул 4\d\d\b')

# Подписи, если справочника кабинета нет под рукой, — слова самого кабинета
# (раздел тренажёров «Fleet» сверял их с живым кабинетом).
FALLBACK_NAMES = {
    'taxi/driver': 'Водитель такси',
    'cargo/courier/on-foot': 'Пеший курьер',
    'cargo/courier/on-car': 'Курьер на автомобиле',
    'cargo/courier/on-motorcycle': 'Курьер на мотоцикле',
    'cargo/courier/on-truck': 'Водитель на грузовом авто',
}


class CabinetClient(MailingsClient):
    """Транспорт кабинета с общим пределом запросов в полёте (MailingsClient),
    но без пауз внутри запроса: карточку ждёт человек, и сбой должен отвечать
    сразу, а не через нарастающие повторы. Ширина не растёт выше CONCURRENCY."""

    LOG_LABEL = 'Карточка водителя'

    def __init__(self, cookies, user_agent=None):
        super().__init__(cookies, user_agent, concurrency=CONCURRENCY, max_delay=0, timeout=REQUEST_TIMEOUT)
        self._concurrency_ceiling = CONCURRENCY


_clients = {}      # учётка → (отпечаток кук, клиент): один клиент на сессию на процесс
_blocks = {}       # (учётка | '*', парк | None) → до какого time.monotonic() не спрашивать
_lock = threading.Lock()


def _fingerprint(session):
    raw = json.dumps([session.get('cookies'), session.get('user_agent') or ''], sort_keys=True,
                     ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def cabinet_clients(db):
    """[(учётка, клиент)] по порядку: общая сессия ЭДО, затем своя «Рассылок».
    Клиент живёт, пока не сменились куки; новые куки снимают прежние отказы."""
    with db._get_cursor() as cursor:
        rows = (('ЭДО', edm_session.load_session(cursor)), ('Рассылки', mailing_session.load_session(cursor)))
    clients = []
    with _lock:
        for name, row in rows:
            if not row or not row.get('cookies'):
                continue
            mark = _fingerprint(row)
            held = _clients.get(name)
            if held is None or held[0] != mark:
                held = _clients[name] = (mark, CabinetClient(row['cookies'], row.get('user_agent')))
                for key in [key for key in _blocks if key[0] == name]:
                    del _blocks[key]
            clients.append((name, held[1]))
    return clients


def _blocked(name, park_id, moment):
    with _lock:
        return any(_blocks.get(key, 0.0) > moment for key in (('*', None), (name, None), (name, park_id)))


def _block(key, seconds, moment, reason):
    with _lock:
        _blocks[key] = moment + seconds
    log.warning('Карточка водителя: кабинет Fleet не спрашиваем %d с — %s', seconds, reason)


def _kind(error):
    """Чей отказ: 'session', 'denied' (403 — на этот парк) и 'refused' (иной 4xx на этот
    парк) — учётки, можно спросить другую; 'down' — самого кабинета (сеть, 5xx, 429)."""
    text = str(error)
    if isinstance(error, FleetSessionExpired):
        return 'session'
    if isinstance(error, FleetAccessDenied) or text.startswith('Fleet вернул 403'):
        return 'denied'
    if _CLIENT_ERROR.match(text):
        return 'refused'
    return 'down'


def _note_failure(error, name, park_id, moment, what):
    """Закрыть на время учётку, её парк или весь кабинет — по тому, чей это отказ."""
    kind = _kind(error)
    key, seconds = {'session': ((name, None), SESSION_PAUSE), 'denied': ((name, park_id), DENIED_PAUSE),
                    'refused': ((name, park_id), REFUSED_PAUSE)}.get(kind, (('*', None), FAILURE_PAUSE))
    answer = _IDS.sub(lambda match: match.group(0)[:8] + '…', str(error))[:200]
    _block(key, seconds, moment, '%s, учётка «%s»: %s: %s' % (what, name, type(error).__name__, answer))


def _call(client, method, path, park_id, body=None):
    """Запрос в кабинет; разовый 5xx или 429 — ещё раз через RETRY_DELAY. Сеть не
    повторяем: второй таймаут человек ждал бы ещё REQUEST_TIMEOUT."""
    try:
        return client._request(method, path, park_id=park_id, body=body, attempts=1)
    except FleetError as error:
        if not _TRANSIENT.search(str(error)):
            raise
    time.sleep(RETRY_DELAY)
    return client._request(method, path, park_id=park_id, body=body, attempts=1)


def _fetch_profession(client, park_id, driver_id):
    """(found, profession_id); 404 — водителя в этом парке нет."""
    try:
        payload = _call(client, 'POST', PATH_CARD, park_id, body={'driver_id': driver_id})
    except FleetError as error:
        if str(error).startswith('Fleet вернул 404'):
            return False, None
        raise
    profile = (((payload or {}).get('driver') or {}).get('driver_profile')) or {}
    return True, str(profile.get('profession_id') or '').strip() or None


def _fetch_names(client, park_id):
    payload = _call(client, 'GET', PATH_PROFESSIONS, park_id)
    items = payload if isinstance(payload, list) else ((payload or {}).get('professions') or [])
    names = {}
    for item in items:
        if isinstance(item, dict):
            code, name = str(item.get('id') or '').strip(), ' '.join(str(item.get('name') or '').split())
            if code and name:
                names[code] = name
    return names


def _label(db, client, name, park_id, profession_id, moment):
    """(подпись, справочник не ответил): подпись из сохранённого справочника любого
    парка, где она есть; нет нигде — справочник этого парка (не чаще раза в
    NAMES_TTL), иначе запасная."""
    with db._get_cursor() as cursor:
        cursor.execute('SELECT names ->> %s FROM wazzup_fleet_professions WHERE names ? %s '
                       'ORDER BY fetched_at DESC LIMIT 1', (profession_id, profession_id))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0], False
        cursor.execute('SELECT 1 FROM wazzup_fleet_professions WHERE park_id = %s '
                       'AND fetched_at > now() - make_interval(secs => %s)', (park_id, NAMES_TTL))
        asked = cursor.fetchone() is not None
    if not asked and not _blocked(name, park_id, moment):
        try:
            names = _fetch_names(client, park_id)
        except Exception as error:  # noqa: BLE001 — без справочника есть запасные подписи
            _note_failure(error, name, park_id, moment, 'справочник профессий')
            return FALLBACK_NAMES.get(profession_id), True
        from psycopg2.extras import Json
        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO wazzup_fleet_professions(park_id, names, fetched_at) VALUES (%s, %s, now())
                ON CONFLICT (park_id) DO UPDATE SET names = EXCLUDED.names, fetched_at = EXCLUDED.fetched_at
            """, (park_id, Json(names)))
        if names.get(profession_id):
            return names[profession_id], False
    return FALLBACK_NAMES.get(profession_id), False


def _ask(db, clients, park_id, driver_id, moment):
    """(found, profession_id, подпись) от первой учётки, что ответила; None — кабинет не ответил."""
    if _blocked('*', None, moment):
        return None
    for name, client in clients():
        if _blocked(name, park_id, moment):
            continue
        try:
            found, profession_id = _fetch_profession(client, park_id, driver_id)
        except Exception as error:  # noqa: BLE001 — отказ учётки: спросим другую; сбой кабинета: пауза всем
            _note_failure(error, name, park_id, moment, 'карточка исполнителя')
            continue
        if not profession_id:
            log.info('Карточка водителя: кабинет ответил (учётка «%s») — %s', name,
                     'профессии в карточке нет' if found else 'в этом парке водителя нет')
            return found, None, None
        label, failed = _label(db, client, name, park_id, profession_id, moment)
        log.info('Карточка водителя: кабинет ответил (учётка «%s») — %s, подпись: %s', name, profession_id,
                 label or 'не нашлась')
        if label is None and failed:
            return None          # справочник не ответил — спросим при следующем открытии, а не через 6 часов
        return found, profession_id, label
    return None


def _shown(found, profession_id, label):
    return {'id': profession_id, 'name': label} if found and profession_id and label else None


def lookup(db, driver_id, park_id, clients=None, now=time.monotonic):
    """Профессия водителя {'id', 'name'} или None. park_id — парк водителя по CRM
    (park.yandex_id). clients() — [(учётка, клиент)], по умолчанию сессии из базы.
    Не бросает: без профессии карточка водителя работает как прежде."""
    driver_id, park_id = (str(value or '').strip().lower() for value in (driver_id, park_id))
    if not (_HEX32.match(driver_id) and _HEX32.match(park_id)):
        return None
    try:
        with db._get_cursor() as cursor:
            cursor.execute('SELECT park_id, found, profession_id, profession_name, '
                           'EXTRACT(EPOCH FROM now() - fetched_at) FROM wazzup_driver_professions '
                           'WHERE driver_id = %s', (driver_id,))
            row = cursor.fetchone()
        known = _shown(*row[1:4]) if row and row[0] == park_id else None
        if row and row[0] == park_id:
            settled = row[1] and (row[2] is None or row[3])
            if float(row[4]) < (FOUND_TTL if settled else MISSING_TTL):
                return known
        answer = _ask(db, clients or (lambda: cabinet_clients(db)), park_id, driver_id, now())
        if answer is None:
            return known
        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO wazzup_driver_professions(driver_id, park_id, found, profession_id, profession_name,
                                                      fetched_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (driver_id) DO UPDATE SET park_id = EXCLUDED.park_id, found = EXCLUDED.found,
                    profession_id = EXCLUDED.profession_id, profession_name = EXCLUDED.profession_name,
                    fetched_at = EXCLUDED.fetched_at
            """, (driver_id, park_id) + answer)
        return _shown(*answer)
    except Exception:  # noqa: BLE001 — профессия не стоит падения карточки
        log.exception('Карточка водителя: профессия из кабинета не собралась')
        return None
