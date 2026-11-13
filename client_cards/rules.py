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

# Аккаунта нет вовсе: звонивший не зарегистрирован в Тез (ошибся номером, спрашивает про
# регистрацию). Оператор отмечает это сам — «Аккаунта нет» (решение владельца 12.11.2026:
# без отметки такие карточки нельзя было сохранить, и они копились «не заполненными»).
# Это вид только самой карточки (client_cards.account_kind при пустом account_id): у
# аккаунтов и привязок такого вида не бывает, поэтому в KINDS его нет.
KIND_NONE = 'none'
NO_ACCOUNT_LABEL = 'Аккаунта нет'

# У водителей, клиентов и третьих лиц — свои причины обращений (владелец, 13.11.2026:
# «разделение по причинам обращений — Клиент/Водитель… собственную ветку для каждой», и
# следом: «в ветку Клиент, Водитель нужно добавить 3 лицо»). Ветка — признак причины
# (client_card_categories.audience); подтема берёт его у своей основной причины.
#
# Третье лицо — звонит не сам водитель и не сам клиент: родственник, очевидец, другая
# служба. Своего аккаунта в Тез у него нет, но звонить он может по чужому, поэтому его
# ветка подходит карточке всегда — и с выбранным аккаунтом, и с отметкой «Аккаунта нет».
#
# С выбранным аккаунтом карточке подходят причины его ветки и ветки «Третье лицо». Без
# аккаунта («Аккаунта нет») кто звонит, называет оператор — причина из любой ветки.
AUDIENCE_THIRD = 'third'
AUDIENCES = (KIND_DRIVER, KIND_CLIENT, AUDIENCE_THIRD)
AUDIENCE_LABELS = {KIND_DRIVER: 'Водитель', KIND_CLIENT: 'Клиент', AUDIENCE_THIRD: 'Третье лицо'}
AUDIENCE_DEFAULT = KIND_DRIVER


def normalize_audience(value):
    """Ветка причин из запроса: 'driver' | 'client' | 'third'. Не прислали — ветка водителей."""
    if value in (None, ''):
        return AUDIENCE_DEFAULT
    if value not in AUDIENCES:
        raise ValueError('Ветка причин указана неверно: нужен водитель, клиент или третье лицо')
    return value


def account_audiences(kind):
    """Ветки причин, подходящие аккаунту вида kind: его собственная и «Третье лицо»."""
    return (kind, AUDIENCE_THIRD) if kind in KINDS else None


def card_audiences(card):
    """Из каких веток карточке подходят причины (см. account_audiences).

    None — аккаунт ещё не выбран или отмечено «Аккаунта нет»: подходит любая ветка.
    """
    return account_audiences(card.get('account_kind')) if card.get('account_id') else None


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


def has_no_account(card):
    """Оператор отметил, что у звонившего нет аккаунта в Тез (см. KIND_NONE)."""
    return not card.get('account_id') and card.get('account_kind') == KIND_NONE


def completion_error(card):
    """Чего не хватает, чтобы сохранить карточку. None — можно сохранять.

    Порядок тот же, что на экране: сначала аккаунт, потом причина (задача #335,
    п. 2–3: без аккаунта оператор не продолжает, причины выбираются после него).
    Аккаунта может не быть вовсе — тогда оператор отмечает это сам («Аккаунта нет»),
    и карточка сохраняется без него; молча, без отметки, — нельзя.
    """
    if not card.get('account_id') and not has_no_account(card):
        return 'Выберите аккаунт, вставьте ссылку на него или отметьте «Аккаунта нет»'
    if not card.get('topics'):
        return 'Выберите причину обращения'
    return None


# ─── Дерево причин ───────────────────────────────────────────────────────────
#
# Причины — дерево (владелец, 13.11.2026): основная причина («Запрос», «Консультация»,
# «Жалоба», «Предложение») → подтема 1-го уровня («Проблема с заказом») → подтема 2-го
# уровня («Не может завершить заказ») → и глубже, если отделу понадобится. Оператор
# выбирает самый глубокий уровень своей ветки.
#
# В карточке причина хранится парой: основная причина (category_id) и выбранная подтема
# ЛЮБОГО уровня (subcategory_id; пусто — выбрана сама основная причина, у которой подтем
# нет). Промежуточные уровни восстанавливаются по справочнику — category_path.
#
# Потолок глубины — чтобы дерево оставалось деревом: в окне карточки шириной с телефон
# шестой уровень отступов уже не читается. Понадобится глубже — меняется это число.
CATEGORY_MAX_DEPTH = 5


def category_path(index, node_id):
    """Путь от основной причины до узла: [id основной, …, id узла].

    index — {id: {'parent_id', …}}. None — узла нет или цепочка родителей оборвана.
    """
    path, seen = [], set()
    current = node_id
    while current is not None:
        node = index.get(current)
        if node is None or current in seen:
            return None
        seen.add(current)
        path.append(current)
        current = node.get('parent_id')
    path.reverse()
    return path


def category_children(index):
    """{id родителя (None — основные причины): [id подтем]} — одним проходом по справочнику."""
    children = {}
    for node_id, node in index.items():
        children.setdefault(node.get('parent_id'), []).append(node_id)
    return children


def category_subtree(index, node_id, children=None):
    """Сам узел и все его подтемы любого уровня — множеством id."""
    children = children if children is not None else category_children(index)
    result, stack = set(), [node_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, ()))
    return result


def category_titles(index, node_id):
    """Названия по пути от основной причины до узла: ['Запрос', 'Проблема с заказом', …]."""
    path = category_path(index, node_id) or [node_id]
    return [str((index.get(item) or {}).get('title') or 'Причина %s' % item) for item in path]


PATH_SEPARATOR = ' → '
# Длина причины в запросе: client_card_requests.topic_title VARCHAR(400).
REQUEST_TOPIC_LIMIT = 400


def topic_path_text(titles, limit=REQUEST_TOPIC_LIMIT):
    """Причина одной строкой по пути: «Запрос → Проблема с заказом → Не может завершить заказ».

    Не влезает в limit — уходят верхние уровни: самое точное название стоит в конце пути.
    """
    parts = [str(title) for title in titles or () if title]
    text = PATH_SEPARATOR.join(parts)
    while len(text) > limit and len(parts) > 1:
        parts = parts[1:]
        text = '…' + PATH_SEPARATOR + PATH_SEPARATOR.join(parts)
    return text[:limit]


def rollup_people(rows, index):
    """[(id выбранной причины, человек)] → {id узла: множество людей} по всем уровням.

    Человек, обратившийся по подтеме, считается и у каждого её предка — по одному разу,
    сколько бы подтем этой ветки он ни назвал. Отсюда и число «по причине целиком»: это
    разные люди на всю ветку, а не сумма её подтем.
    """
    people, paths = {}, {}
    for node_id, person in rows:
        path = paths.get(node_id)
        if path is None:
            path = paths[node_id] = category_path(index, node_id) or [node_id]
        for item in path:
            people.setdefault(item, set()).add(person)
    return people


_AUDIENCE_PICKED = {KIND_DRIVER: 'выбран водитель', KIND_CLIENT: 'выбран клиент'}


def topics_of_audiences(topics, tree, audiences):
    """Причины карточки из веток audiences: [(category_id, subcategory_id | None)].

    Остальные — из чужой ветки: аккаунт карточки сменили с водителя на клиента (или
    наоборот), и прежние причины ему больше не подходят.
    """
    return [(topic['category_id'], topic.get('subcategory_id')) for topic in topics or ()
            if (tree.get(topic['category_id']) or {}).get('audience') in audiences]


def normalize_topics(raw, tree, audiences=None):
    """Выбранные причины → [(category_id, subcategory_id | None)] без повторов.

    tree — {id: {'parent_id', 'is_active', 'has_children', 'audience'}}: справочник отдела.
    category_id — основная причина, subcategory_id — выбранная подтема любого уровня.
    audiences — из каких веток карточке подходят причины (card_audiences); None — из любых.
    ValueError — причины нет в справочнике, она (или уровень над ней) скрыта, она из
    чужой ветки, подтема не из этой ветки или у выбранного узла есть видимые подтемы:
    выбирается самый глубокий уровень. Скрытую причину нельзя выбрать заново, но в
    старой карточке она остаётся.
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
            raise ValueError('Такой причины нет в справочнике — обновите карточку')
        if audiences and category.get('audience') not in audiences:
            raise ValueError('В карточке %s — причина из ветки «%s» сюда не подходит'
                             % (_AUDIENCE_PICKED[audiences[0]], AUDIENCE_LABELS.get(category.get('audience'), '?')))
        if subcategory_id is None:
            if category.get('has_children'):
                raise ValueError('У этой причины есть подтемы — выберите одну из них')
        else:
            branch = category_path(tree, subcategory_id)
            if (not branch or len(branch) < 2 or branch[0] != category_id
                    or not all(tree[node_id].get('is_active') for node_id in branch)):
                raise ValueError('Такой подтемы нет в справочнике — обновите карточку')
            if tree[subcategory_id].get('has_children'):
                raise ValueError('У этой подтемы есть свои подтемы — выберите одну из них')
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

    nodes — [{'id', 'parent_id', 'ancestors', 'count', 'baseline', 'last', 'sent_today', …}]
    по узлам дерева причин всех уровней; у каждого — число людей на всю его ветку.
    ancestors — id предков от родителя до основной причины (нет ключа — берётся один
    parent_id). Волна по одной подтеме поднимает и всё, что над ней; два сообщения об
    одном и том же — шум, поэтому идём от самых глубоких уровней к основным причинам:

      * сработала подтема — уровни над ней в этом же заходе не шлём;
      * по подтеме идёт пауза (о ней уже писали) — уровни над ней тоже молчат;
      * по узлу с подтемами шлём, только когда рост размазан по ним: ни одна сама порога
        не взяла и ни по одной не идёт пауза.

    Возвращает узлы с добавленными 'kind' и 'threshold', самые глубокие первыми.
    """
    cooldown = timedelta(minutes=int(settings['cooldown_min']))
    picked, busy = [], set()

    def ancestors(node):
        chain = node.get('ancestors')
        if chain is None:
            chain = [node['parent_id']] if node.get('parent_id') is not None else []
        return chain

    def decide(node):
        return alert_decision(count=node['count'], baseline=node['baseline'], last=node.get('last'),
                              sent_today=node.get('sent_today') or 0, now=now, settings=settings)

    # Устойчивая сортировка: внутри уровня порядок узлов остаётся каким пришёл.
    for node in sorted(nodes, key=lambda item: -len(ancestors(item))):
        chain = ancestors(node)
        last = node.get('last')
        if chain and last and last.get('at') and now - last['at'] < cooldown:
            busy.update(chain)
        if node['id'] in busy:
            continue
        decision = decide(node)
        if decision:
            picked.append({**node, **decision})
            busy.update(chain)
    return picked
