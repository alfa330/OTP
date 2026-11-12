# -*- coding: utf-8 -*-
"""Правила «Карточки клиента». Чистая логика: ни базы, ни Flask, ни сети.

Здесь то, что должно быть верно при любом запросе и что проверяется без стенда:

* ключ номера — один на звонок, привязку и историю;
* ссылка на аккаунт Тез: вид (водитель / клиент) и ID берутся из адреса админки;
* состояние карточки считается из её отметок времени, а не хранится словом;
* когда алерт о росте обращений уходит руководителю, а когда — нет.
"""

import math
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from common.kz_phone import normalize_kz_phone

ALMATY = ZoneInfo('Asia/Almaty')


def now_almaty():
    """Наивное время Алматы — в таком виде оно лежит во всех таблицах раздела."""
    return datetime.now(ALMATY).replace(tzinfo=None)


def clean_text(value, limit):
    """Текст человека: без управляющих символов, с обрезкой. Переводы строк остаются."""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(value or ''))
    return text.strip()[:limit]


# ─── Номер телефона ──────────────────────────────────────────────────────────

# Короче семи цифр — внутренний номер станции (оператор звонит коллеге): карточку
# по такому звонку не заводим. Длиннее пятнадцати — не номер (E.164).
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15


def phone_key(raw):
    """Ключ номера: только цифры, казахстанский — в виде 77XXXXXXXXX.

    Один и тот же номер приходит по-разному: станция отдаёт «77012345678»,
    оператор набирает «8 701 234 56 78», в Тез он записан «+7701…». Ключ у всех
    трёх один — иначе привязка аккаунта к номеру не нашлась бы при следующем
    звонке. Зарубежный номер остаётся цифрами как есть. None — номера нет:
    внутренний, пустой или мусор.
    """
    digits = re.sub(r'\D', '', str(raw or ''))
    if not digits:
        return None
    kz = normalize_kz_phone(digits)
    if kz:
        return kz
    # Международный префикс «00» и наша «8» вместо «7» перед одиннадцатью цифрами.
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    if not PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
        return None
    return digits


def format_phone(key):
    """Ключ номера для глаз: «+7 701 234-56-78»; не наш формат — «+цифры»."""
    digits = re.sub(r'\D', '', str(key or ''))
    if not digits:
        return ''
    if len(digits) == 11 and digits[0] == '7':
        return '+7 %s %s-%s-%s' % (digits[1:4], digits[4:7], digits[7:9], digits[9:11])
    return '+' + digits


# ─── Аккаунт Тез ─────────────────────────────────────────────────────────────

KIND_DRIVER = 'driver'
KIND_CLIENT = 'client'
KINDS = (KIND_DRIVER, KIND_CLIENT)
KIND_LABELS = {KIND_DRIVER: 'Водитель', KIND_CLIENT: 'Клиент'}

# Раздел админки Тез → вид аккаунта (владелец, 12.11.2026):
#   https://admin.tezapp.org/admin/drivers/12345   — водитель
#   https://admin.tezapp.org/admin/clients/67890  — клиент
_LINK_SECTIONS = {'drivers': KIND_DRIVER, 'clients': KIND_CLIENT}
_KIND_SECTIONS = {kind: section for section, kind in _LINK_SECTIONS.items()}
TEZ_ADMIN_HOST = 'admin.tezapp.org'
DEFAULT_ADMIN_URL = 'https://' + TEZ_ADMIN_HOST
_LINK_PATH = re.compile(r'^/admin/(drivers|clients)/(\d{1,12})(?:[/?#]|$)')
LINK_EXAMPLE = '%s/admin/drivers/12345' % DEFAULT_ADMIN_URL
LINK_MAX_LENGTH = 500


class LinkError(ValueError):
    """Ссылка не ведёт на аккаунт Тез. Текст — для оператора."""


def parse_account_link(text, hosts=(TEZ_ADMIN_HOST,)):
    """Ссылка из админки Тез → (вид, ID). LinkError — это не ссылка на аккаунт.

    Оператор копирует адрес из строки браузера, поэтому принимаем его с хвостами:
    «/edit», параметры, якорь, без «https://». Чужой сайт и чужой раздел админки
    не принимаем: ID без вида аккаунта ничего не значит, а закрепить за номером
    можно только то, что потом откроется.
    """
    raw = str(text or '').strip()
    if not raw:
        raise LinkError('Вставьте ссылку на аккаунт из админки Тез')
    if len(raw) > LINK_MAX_LENGTH or re.search(r'\s', raw):
        raise LinkError('Это не похоже на ссылку. Пример: %s' % LINK_EXAMPLE)
    parts = urlsplit(raw if '://' in raw else 'https://' + raw)
    host = (parts.hostname or '').lower()
    allowed = {str(item).lower() for item in hosts}
    if parts.scheme not in ('http', 'https') or host not in allowed:
        raise LinkError('Нужна ссылка из админки Тез. Пример: %s' % LINK_EXAMPLE)
    match = _LINK_PATH.match(parts.path or '')
    if not match or int(match.group(2)) <= 0:
        raise LinkError('Откройте аккаунт водителя или клиента в админке Тез и скопируйте '
                        'адрес страницы. Пример: %s' % LINK_EXAMPLE)
    return _LINK_SECTIONS[match.group(1)], int(match.group(2))


def account_url(kind, tez_id, base=DEFAULT_ADMIN_URL):
    """Адрес аккаунта в админке Тез — тот же, что оператор вставлял ссылкой."""
    section = _KIND_SECTIONS.get(kind)
    if not section or not tez_id:
        return None
    return '%s/admin/%s/%d' % (str(base or DEFAULT_ADMIN_URL).rstrip('/'), section, int(tez_id))


# ─── Карточка звонка ─────────────────────────────────────────────────────────

DIRECTION_IN = 'in'
DIRECTION_OUT = 'out'
DIRECTIONS = (DIRECTION_IN, DIRECTION_OUT)
DIRECTION_LABELS = {DIRECTION_IN: 'Входящий', DIRECTION_OUT: 'Исходящий'}

# Хранятся три состояния; остальное считается из отметок времени (card_state).
STATUS_OPEN = 'open'    # звонок идёт или карточка ждёт заполнения
STATUS_DONE = 'done'    # заполнена
STATUS_VOID = 'void'    # звонок не состоялся — в отчёты и историю не идёт
STATUSES = (STATUS_OPEN, STATUS_DONE, STATUS_VOID)

STATE_RINGING = 'ringing'   # звонит, ещё не ответили
STATE_TALKING = 'talking'   # разговор
STATE_WRAPUP = 'wrapup'     # постобработка: время на заполнение ещё идёт
STATE_OVERDUE = 'overdue'   # не заполнена за время постобработки
STATE_DONE = 'done'
STATE_VOID = 'void'
STATE_LABELS = {
    STATE_RINGING: 'Звонок', STATE_TALKING: 'Разговор', STATE_WRAPUP: 'Постобработка',
    STATE_OVERDUE: 'Не заполнена', STATE_DONE: 'Заполнена', STATE_VOID: 'Не состоялся',
}

# Телефон мог не донести конец звонка (упал, пропала сеть). Звонок дольше этого
# считаем законченным — карточка не должна вечно числиться «в разговоре».
CALL_MAX_HOURS = 4

COMMENT_LIMIT = 4000
TOPICS_LIMIT = 5


def wrapup_deadline(card):
    """До какого момента идёт постобработка. None — звонок ещё не кончился."""
    ended = card.get('ended_at')
    if not ended:
        return None
    return ended + timedelta(seconds=int(card.get('wrapup_sec') or 0))


def card_state(card, now):
    """Что с карточкой сейчас — по её отметкам времени."""
    status = card.get('status')
    if status == STATUS_VOID:
        return STATE_VOID
    if status == STATUS_DONE:
        return STATE_DONE
    if not card.get('ended_at'):
        started = card.get('created_at')
        if started and now - started > timedelta(hours=CALL_MAX_HOURS):
            return STATE_OVERDUE
        return STATE_TALKING if card.get('answered_at') else STATE_RINGING
    return STATE_WRAPUP if now < wrapup_deadline(card) else STATE_OVERDUE


def is_untouched(card):
    """Оператор ничего не записал: ни причины, ни комментария.

    Выбранный аккаунт записью не считается: его выбирают ещё на гудках (а единственный
    привязанный подставляется сам) — до того, как стало ясно, состоится ли разговор.
    """
    return not (card.get('topics') or str(card.get('comment') or '').strip())


def void_on_end(card):
    """Звонок кончился без разговора, и в карточке пусто — она не состоялась.

    Непринятый входящий и недозвон не должны висеть «не заполненными» и попадать в
    отчёт по причинам. Если оператор успел что-то записать (скажем, «не дозвонился,
    перезвонить»), карточка остаётся — это его запись.
    """
    return not card.get('answered_at') and is_untouched(card)


def completion_error(card):
    """Чего не хватает, чтобы сохранить карточку. None — можно сохранять.

    Порядок тот же, что на экране: сначала аккаунт, потом причина (задача #335,
    п. 2–3: без аккаунта оператор не продолжает, причины выбираются после него).
    """
    if not card.get('account_id'):
        return 'Выберите аккаунт или вставьте ссылку на него'
    if not card.get('topics'):
        return 'Выберите причину обращения'
    return None


def normalize_topics(raw, tree):
    """Выбранные причины → [(category_id, subcategory_id | None)] без повторов.

    tree — {id: {'parent_id', 'is_active', 'has_children'}}: справочник отдела.
    ValueError — причины нет в справочнике, она скрыта, подтема не из этой
    категории или у категории есть подтемы, а выбрана она сама. Скрытую причину
    нельзя выбрать заново, но в старой карточке она остаётся.
    """
    result, seen = [], set()
    for item in raw or ():
        try:
            category_id = int((item or {}).get('category_id'))
            sub_raw = (item or {}).get('subcategory_id')
            subcategory_id = int(sub_raw) if sub_raw not in (None, '') else None
        except (TypeError, ValueError, AttributeError):
            raise ValueError('Причина обращения указана неверно')
        category = tree.get(category_id)
        if not category or category.get('parent_id') is not None or not category.get('is_active'):
            raise ValueError('Такой категории нет в справочнике — обновите карточку')
        if subcategory_id is None:
            if category.get('has_children'):
                raise ValueError('Выберите подтему категории')
        else:
            sub = tree.get(subcategory_id)
            if not sub or sub.get('parent_id') != category_id or not sub.get('is_active'):
                raise ValueError('Такой подтемы нет в справочнике — обновите карточку')
        key = (category_id, subcategory_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    if len(result) > TOPICS_LIMIT:
        raise ValueError('Не больше %d причин в одной карточке' % TOPICS_LIMIT)
    return result


# ─── Запросы в другие отделы ─────────────────────────────────────────────────

REQUEST_OPEN = 'open'            # отправлен, ответа нет
REQUEST_ANSWERED = 'answered'    # есть ответ из отдела
REQUEST_RESOLVED = 'resolved'    # оператор закрыл: вопрос решён
REQUEST_CANCELLED = 'cancelled'  # оператор отменил
REQUEST_STATUSES = (REQUEST_OPEN, REQUEST_ANSWERED, REQUEST_RESOLVED, REQUEST_CANCELLED)
REQUEST_CLOSED = (REQUEST_RESOLVED, REQUEST_CANCELLED)
REQUEST_STATUS_LABELS = {
    REQUEST_OPEN: 'Ждёт ответа', REQUEST_ANSWERED: 'Есть ответ',
    REQUEST_RESOLVED: 'Решён', REQUEST_CANCELLED: 'Отменён',
}

REQUEST_BODY_LIMIT = 3500        # вместе с шапкой сообщение должно влезть в 4096 Telegram
REQUEST_FILES_LIMIT = 6
# Потолок файла — предел Bot API на отправку ботом (50 МБ): больше в группу не уйдёт.
REQUEST_FILE_MAX_BYTES = 50 * 1024 * 1024


# ─── Настройки отдела ────────────────────────────────────────────────────────

WRAPUP_DEFAULT_SEC = 60
WRAPUP_MAX_SEC = 900

ALERT_DEFAULTS = {
    'enabled': True,
    'window_min': 60,       # за какое время считаем обращения
    'min_count': 5,         # меньше стольких обращений — не алерт, как бы ни выросло
    'factor': 2.0,          # во сколько раз больше обычного
    'cooldown_min': 120,    # не повторять по той же причине раньше
    'daily_limit': 4,       # не больше стольких алертов по одной причине за сутки
}
_ALERT_BOUNDS = {
    'window_min': (15, 24 * 60), 'min_count': (2, 500), 'factor': (1.2, 10.0),
    'cooldown_min': (15, 24 * 60), 'daily_limit': (1, 24),
}
# С чем сравниваем «обычно»: то же окно часов за столько прошлых дней.
ALERT_BASELINE_DAYS = 14
# «Рост продолжается» внутри паузы: стало в полтора раза больше и не меньше чем на три,
# и с прошлого сообщения прошло полчаса.
ALERT_GROW_FACTOR = 1.5
ALERT_GROW_MIN_STEP = 3
ALERT_GROW_MIN_GAP_MIN = 30

ALERT_NEW = 'new'           # обращений стало больше обычного
ALERT_GROWING = 'growing'   # после алерта их стало ещё заметно больше
ALERT_STILL = 'still'       # пауза вышла, а обращений по-прежнему больше обычного


def _clamp(value, low, high, default, cast):
    try:
        number = cast(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def normalize_alerts(raw):
    """Настройки алертов в допустимых границах; чего нет или что не число — по умолчанию."""
    raw = raw if isinstance(raw, dict) else {}
    result = {'enabled': bool(raw.get('enabled', ALERT_DEFAULTS['enabled']))}
    for key, (low, high) in _ALERT_BOUNDS.items():
        cast = float if key == 'factor' else int
        result[key] = _clamp(raw.get(key), low, high, ALERT_DEFAULTS[key], cast)
    result['factor'] = round(result['factor'], 2)
    return result


def normalize_wrapup(value):
    """Время постобработки в секундах: 0 — без постобработки, потолок 15 минут."""
    return _clamp(value, 0, WRAPUP_MAX_SEC, WRAPUP_DEFAULT_SEC, int)


# ─── Алерт о росте обращений ─────────────────────────────────────────────────

def alert_threshold(baseline, settings):
    """Сколько обращений за окно — уже алерт.

    Два условия разом: не меньше min_count (пять звонков — рост, два — случайность) и
    не меньше обычного, умноженного на factor. Обычного нет вовсе (новая причина,
    первые дни) — остаётся только min_count.
    """
    return max(int(settings['min_count']), int(math.ceil(float(baseline or 0) * float(settings['factor']))))


def alert_decision(*, count, baseline, last, sent_today, now, settings):
    """Слать ли алерт по причине. None — нет; иначе {'kind', 'threshold'}.

    count       — сколько РАЗНЫХ людей обратилось по причине за окно (один водитель,
                  позвонивший пять раз, — не рост);
    baseline    — сколько обычно: среднее за то же окно часов прошлых дней;
    last        — прошлый алерт по этой причине {'at', 'count'} или None;
    sent_today  — сколько алертов по причине уже ушло за сегодня.

    Без повода не шлём никогда: ниже порога — тишина. Выше порога шлём один раз, а
    дальше молчим всю паузу (cooldown_min), если обращений не стало ещё заметно
    больше. Пауза вышла, а рост держится — напоминаем, но не чаще дневного предела.
    """
    if not settings.get('enabled'):
        return None
    threshold = alert_threshold(baseline, settings)
    if count < threshold:
        return None
    if sent_today >= int(settings['daily_limit']):
        return None
    if not last or not last.get('at'):
        return {'kind': ALERT_NEW, 'threshold': threshold}
    elapsed = now - last['at']
    cooldown = timedelta(minutes=int(settings['cooldown_min']))
    if elapsed >= cooldown:
        # Между алертами прошло не больше двух пауз — это всё та же волна.
        kind = ALERT_STILL if elapsed < cooldown * 2 else ALERT_NEW
        return {'kind': kind, 'threshold': threshold}
    last_count = int(last.get('count') or 0)
    grown = max(last_count + ALERT_GROW_MIN_STEP, int(math.ceil(last_count * ALERT_GROW_FACTOR)))
    if count >= grown and elapsed >= timedelta(minutes=ALERT_GROW_MIN_GAP_MIN):
        return {'kind': ALERT_GROWING, 'threshold': threshold}
    return None


def pick_alerts(nodes, *, now, settings):
    """Какие причины попадут в одно сообщение руководителю.

    nodes — [{'id', 'parent_id', 'count', 'baseline', 'last', 'sent_today', …}] по
    подтемам и по категориям целиком. Волна по одной подтеме поднимает и её
    категорию; два сообщения об одном и том же — шум, поэтому:

      * сработала подтема — категорию в этом же заходе не шлём;
      * по категории шлём, только когда рост размазан по подтемам (ни одна сама
        порога не взяла) и ни по одной её подтеме не идёт пауза.

    Возвращает узлы с добавленными 'kind' и 'threshold', подтемы первыми.
    """
    cooldown = timedelta(minutes=int(settings['cooldown_min']))
    picked, busy_parents = [], set()

    def decide(node):
        return alert_decision(count=node['count'], baseline=node['baseline'], last=node.get('last'),
                              sent_today=node.get('sent_today') or 0, now=now, settings=settings)

    for node in nodes:
        if node.get('parent_id') is None:
            continue
        last = node.get('last')
        if last and last.get('at') and now - last['at'] < cooldown:
            busy_parents.add(node['parent_id'])
        decision = decide(node)
        if decision:
            picked.append({**node, **decision})
            busy_parents.add(node['parent_id'])
    for node in nodes:
        if node.get('parent_id') is not None or node['id'] in busy_parents:
            continue
        decision = decide(node)
        if decision:
            picked.append({**node, **decision})
    return picked
