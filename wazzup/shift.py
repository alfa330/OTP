"""Смена верификатора в разделе «Чаты ОП»: статусы и её закрытие без человека.

Статусов у Wazzup нет, поэтому рабочих часов верификаторов iCORE не видел: при
сменах в графике авто-часы у всей группы стояли нулями. Статус ставится кнопкой
в самом разделе и ложится в общий `operator_status_events` тем же
`append_operator_status_event`, что события iCORE Phone, — учёту часов,
опозданиям, «Графикам работы» и «Моим сменам» второй источник не нужен.

Слова статусов — те же, что шлёт телефон операторам «Основы» отдела продаж
(«Активный», «Перерыв», «Технический перерыв», «Тренинг», выход), поэтому и
читает их учёт часов теми же правилами без единой правки: «готов» — работа,
«перерыв» — перерыв, «тренинг» и «тех причина» оправдывают опоздание, «выключен»
в часы не идёт.

Вкладка браузера — не телефон: закрыв её, человек «выход» не присылает. Без
страховки «Активный» висел бы до следующего утра, съедал начало следующей смены
и прятал опоздание. Поэтому открытый портал отмечается раз в минуту, и если
отметок нет дольше PRESENCE_TIMEOUT_SECONDS, смена закрывается сама — моментом
последней отметки, а не моментом, когда это заметили. Второй предел — статус,
не менявшийся MAX_OPEN_STATUS_HOURS: открытый браузер ещё не значит, что человек
на месте.

Логика перенесена из отложенной вкладки «Чаты Wazzup» (#366), где её уже
разбирали и чинили; словарь там был Chat2Desk, здесь — телефонный.
"""
import re
from datetime import timedelta

# Порядок — порядок меню. «Закончить смену» в список не входит: это не статус
# смены, а её конец, и в меню он стоит отдельно, под чертой.
STATUSES = (
    {'key': 'готов', 'label': 'Активный', 'tone': 'work'},
    {'key': 'перерыв', 'label': 'Перерыв', 'tone': 'break'},
    {'key': 'тренинг', 'label': 'Тренинг', 'tone': 'training'},
    {'key': 'тех причина', 'label': 'Тех. пауза', 'tone': 'tech'},
)
_STATUS_BY_KEY = {item['key']: item for item in STATUSES}
ON_SHIFT_KEYS = frozenset(_STATUS_BY_KEY)
# С него смена начинается: кнопка «Начать смену» ставит именно его.
START_KEY = 'готов'

# Конец смены — тот же ключ, что шлёт телефон при выходе: на табло он «Не в
# сети», в часы не идёт ни у одной модели.
LOGOUT_KEY = 'выключен'
# Состояния «не на смене» других источников, если человек ими пользуется.
_OFF_SHIFT_KEYS = frozenset({LOGOUT_KEY, 'logout', 'offline', 'отключен', 'отключена'})

# Метка событий раздела в operator_status_events.client_event_id. По ней сторож
# отличает смену, открытую здесь, от статусов телефона: закрывать чужую смену
# он не вправе.
CLIENT_EVENT_PREFIX = 'wzws-'
AUTO_LOGOUT_NOTE = 'Портал закрыт'
LONG_SHIFT_NOTE = 'Статус не менялся больше 16 часов'
AUTO_NOTES = frozenset({AUTO_LOGOUT_NOTE, LONG_SHIFT_NOTE})
_CLIENT_EVENT_MAX = 64

HEARTBEAT_SECONDS = 60
# Десять минут, а не две: фоновая вкладка Chrome будит таймеры не чаще раза в
# минуту, а короткий обрыв сети не должен закрывать смену.
PRESENCE_TIMEOUT_SECONDS = 600
# Открытая вкладка доказывает, что открыт браузер, а не что человек на месте:
# компьютер, оставленный на выходные, отмечался бы до понедельника. Смена, в
# которой статус не менялся столько часов, закрывается сама — живая смена
# столько без перерыва не идёт (самая длинная в графике — двенадцать часов).
MAX_OPEN_STATUS_HOURS = 16
# Окно отбора сторожа: отметки старше этого давно разобраны прошлыми проходами.
SWEEP_LOOKBACK_HOURS = 48


def normalize_key(value):
    return ' '.join(str(value or '').strip().lower().split())


def is_on_shift_key(key):
    return normalize_key(key) in ON_SHIFT_KEYS


def is_workspace_event(client_event_id):
    return str(client_event_id or '').startswith(CLIENT_EVENT_PREFIX)


def client_event_id(raw):
    """id события раздела: метка + то, что прислал браузер.

    Браузер присылает свой id, чтобы повтор одного нажатия (обрыв сети до
    ответа) не записал второе событие. Пусто — события без идемпотентности не
    бывает, отвечаем ValueError."""
    tail = re.sub(r'[^0-9A-Za-z_-]', '', str(raw or ''))[:_CLIENT_EVENT_MAX - len(CLIENT_EVENT_PREFIX)]
    if not tail:
        raise ValueError('client_event_id is required')
    return CLIENT_EVENT_PREFIX + tail


def auto_logout_event_id(user_id, logout_at):
    """Один id на один момент закрытия: второй проход сторожа не задвоит выход."""
    return f'{CLIENT_EVENT_PREFIX}auto-{int(user_id)}-{logout_at.strftime("%Y%m%d%H%M%S")}'


def current_status(event, now):
    """Что показать в разделе по последнему событию статуса человека.

    event — {'status_key', 'event_at', 'client_event_id', 'state_note'} или None.
    Смена без событий и смена, закрытая выходом, одинаково «не на смене»; чужой
    незнакомый ключ (статус телефона) показывается как есть и считается сменой —
    прятать его хуже, чем показать непривычное слово."""
    if not event or not event.get('status_key'):
        return {'key': None, 'label': 'Не на смене', 'tone': 'off', 'onShift': False,
                'since': None, 'elapsedSeconds': None, 'auto': False}
    key = normalize_key(event.get('status_key'))
    event_at = event.get('event_at')
    elapsed = None
    if event_at is not None and now >= event_at:
        elapsed = int((now - event_at).total_seconds())
    meta = _STATUS_BY_KEY.get(key)
    if meta:
        label, tone, on_shift = meta['label'], meta['tone'], True
    elif key in _OFF_SHIFT_KEYS:
        label, tone, on_shift = 'Не на смене', 'off', False
    else:
        label, tone, on_shift = key[:1].upper() + key[1:], 'other', True
    return {
        'key': key,
        'label': label,
        'tone': tone,
        'onShift': on_shift,
        'since': event_at.isoformat() if event_at is not None else None,
        'elapsedSeconds': elapsed,
        # Смену закрыл сторож, а не человек: раздел скажет об этом один раз.
        'auto': (not on_shift and is_workspace_event(event.get('client_event_id'))
                 and str(event.get('state_note') or '') in AUTO_NOTES),
    }


def auto_logout(last_seen_at, latest_event, now, timeout_seconds=PRESENCE_TIMEOUT_SECONDS):
    """(момент выхода, причина) или None, если закрывать нечего.

    Закрываем только смену, открытую в разделе: статус телефона или импорта —
    чужая ответственность. Два повода:

    * портал молчит дольше порога — выход ставится моментом, когда его видели
      последний раз (человек ушёл тогда, а не когда это заметили). Тишина
      считается от более позднего из двух: отметки или самого статуса — статус,
      записанный разделом, и есть доказательство, что вкладка была жива; иначе
      смена, начатая после вчерашней отметки, закрылась бы в свою же первую
      секунду, попади проход сторожа между записью статуса и отметкой;
    * статус не менялся MAX_OPEN_STATUS_HOURS — выход ставится ровно на этом
      пределе (см. константу).

    Без отметок вовсе (таблица присутствия не развернулась) закрываем только по
    второму поводу: по тишине судить не о чем."""
    if not latest_event or not is_on_shift_key(latest_event.get('status_key')):
        return None
    if not is_workspace_event(latest_event.get('client_event_id')):
        return None
    event_at = latest_event.get('event_at')
    if event_at is None:
        return None
    cap = event_at + timedelta(hours=MAX_OPEN_STATUS_HOURS)
    if last_seen_at is not None:
        reference = max(last_seen_at, event_at)
        if now - reference >= timedelta(seconds=timeout_seconds):
            return (min(reference, cap), AUTO_LOGOUT_NOTE if reference < cap else LONG_SHIFT_NOTE)
    if now >= cap:
        return (cap, LONG_SHIFT_NOTE)
    return None
