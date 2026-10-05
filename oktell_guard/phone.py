"""iCORE Phone в разделе «Ограничитель Перезвона»: статусы ОП по группам и автоофлайн.

Чистая логика — ни базы, ни Flask (как access.py): модуль импортируют и раздел,
и /api/operator/sip_settings в монолите, и тесты напрямую.

ТЗ от 05.10.2026 — статусы оператора отдела продаж зависят от его группы:
- «Основа» (op_osnova): основной статус «Активный» — входящие и исходящие.
  Отдельного «Исхода» у Основы нет.
- «ЯР» (op_yandex_reg) и «Поток» (op_potok): основной статус «Исход» — только
  исходящие. Больше N минут без инициации звонка — телефон сам ставит «Офлайн»
  («выброс»), вернуться оператор может только сам, выбрав «Исход».
- «Поток» при работе через систему автодозвона: отдельный рабочий статус
  «Автодозвон».
- Всем группам вручную: «Перерыв», «Технический перерыв», «Тренинг».
«Соединение» и «Разговор» — фазы звонка поверх выбранного статуса, их ставит
телефон сам, в меню их нет.

Состав статусов живёт ЗДЕСЬ, на сервере: телефон рисует меню из присланного
списка, поэтому поменять набор статусов группы можно без выпуска телефона.
Свойства самих статусов (DND, исходящие, presence FOP2, ключ для часов) — в
телефоне (ICoreStatus.h): это техника, а не продуктовое решение.
"""

PHONE_DEPARTMENT_CODE = 'op'

# Группа ОП по модели расчёта: модель берётся у группы, в которой оператор
# состоит на дату, а без действующего членства — у его направления (та же
# лестница, что у Database._load_operator_calculation_models_tx).
STATUS_GROUP_BY_MODEL = {
    'op_osnova': 'osnova',
    'op_yandex_reg': 'yar',
    'op_potok': 'potok',
}
STATUS_GROUP_LABELS = {'osnova': 'Основа', 'yar': 'ЯР', 'potok': 'Поток'}
# Статусы по группам получают только те, кто сидит на телефоне. СВ, тренер или
# админ с SIP-номером остаются на прежнем наборе.
STATUS_GROUP_ROLES = frozenset({'operator', 'trainee'})

# Коды статусов в контракте с телефоном (ICoreStatus.h, ICoreStatusFromCode).
# Незнакомый код телефон пропускает, поэтому новый код можно добавить сюда раньше,
# чем его поддержат все телефоны парка.
STATUS_CODES = ('active', 'outbound', 'autodial', 'break', 'tech_break', 'training', 'offline')

# Порядок = порядок в меню телефона: рабочие статусы сверху, ручные паузы — в
# порядке ТЗ. «Офлайн» вручную не выбирается: в него телефон переводит сам.
# «Автодозвон» телефон показывает, только если у оператора есть номер автодозвона.
STATUS_PROFILES = {
    'osnova': {'statuses': ('active', 'break', 'tech_break', 'training'), 'start': 'active'},
    'yar': {'statuses': ('outbound', 'break', 'tech_break', 'training'), 'start': 'outbound'},
    'potok': {'statuses': ('outbound', 'autodial', 'break', 'tech_break', 'training'),
              'start': 'outbound'},
}

# Автоофлайн считается только в «Исходе». У «Основы» «Исхода» нет, поэтому
# выбирать её в правиле бессмысленно — в списке групп правила её нет.
IDLE_OFFLINE_GROUPS = ('yar', 'potok')

IDLE_THRESHOLD_MIN_S = 60
IDLE_THRESHOLD_MAX_S = 3600
IDLE_THRESHOLD_DEFAULT_S = 300
WARN_BEFORE_DEFAULT_S = 60
WARN_BEFORE_MAX_S = 600

DEFAULT_PHONE_SETTINGS = {
    'enabled': True,
    'threshold_s': IDLE_THRESHOLD_DEFAULT_S,
    'warn_before_s': WARN_BEFORE_DEFAULT_S,
    'groups': list(IDLE_OFFLINE_GROUPS),
}
# Поля правила, которые можно менять из интерфейса (whitelist, как SETTINGS_FIELDS).
PHONE_SETTINGS_FIELDS = ('enabled', 'threshold_s', 'warn_before_s', 'groups')

# «Выброс» в офлайн телефон отправляет обычным событием статуса — тем же путём и
# той же очередью на диске, что и все часы: статус «офлайн» с пометкой «авто».
# Отдельная ручка дала бы второй канал доставки со своими потерями, а здесь
# один факт — одно событие: смена статуса и есть выброс.
KICK_STATUS_KEY = 'офлайн'
KICK_STATE_NOTE = 'авто'
KICK_CLIENT_KEY_PREFIX = 'phone|'


def normalize_key(value) -> str:
    """Тот же вид ключа, что у Database._normalize_import_status_key."""
    return ' '.join(str(value or '').strip().lower().split())


def status_group_for(role, group_model, direction_model):
    """Группа статусов ('osnova' | 'yar' | 'potok') или None — прежний набор.

    Действующее на дату членство в группе решает всегда, даже если это группа
    не ОП (тогда None): направление — только когда членства нет вовсе.
    """
    if normalize_key(role) not in STATUS_GROUP_ROLES:
        return None
    model = normalize_key(group_model) or normalize_key(direction_model)
    return STATUS_GROUP_BY_MODEL.get(model)


def clamp_idle_threshold(value, default=IDLE_THRESHOLD_DEFAULT_S) -> int:
    """Порог простоя в секундах: минута — минимум, час — максимум."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(IDLE_THRESHOLD_MIN_S, min(IDLE_THRESHOLD_MAX_S, number))


def clamp_warn_before(value, threshold_s, default=WARN_BEFORE_DEFAULT_S) -> int:
    """За сколько секунд до выброса предупреждать. Предупреждение не может
    начаться раньше, чем через полминуты простоя — иначе оно висело бы всегда."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    upper = min(WARN_BEFORE_MAX_S, max(0, int(threshold_s) - 30))
    return max(0, min(upper, number))


def normalize_groups(value, default=None):
    """Группы правила: только из IDLE_OFFLINE_GROUPS, без повторов, в их порядке.

    None — «не задано» (берём default); пустой список — осознанное «ни одной».
    """
    if value is None:
        return list(IDLE_OFFLINE_GROUPS if default is None else default)
    if isinstance(value, str):
        # JSONB обычно приходит из драйвера уже списком, но строкой '["yar"]'
        # он тоже бывает (другой драйвер, ручной SELECT ::text) — скобки и
        # кавычки снимаем, иначе такая строка молча давала бы «ни одной группы».
        value = [part.strip().strip('"\'') for part in
                 value.strip().strip('[]').replace(';', ',').split(',')]
    try:
        wanted = {normalize_key(item) for item in value}
    except TypeError:
        return list(IDLE_OFFLINE_GROUPS if default is None else default)
    return [group for group in IDLE_OFFLINE_GROUPS if group in wanted]


# Строки, которые _as_bool понимает как «да» и «нет». Вынесены, чтобы проверка
# запроса (validate_phone_settings_changes) и разбор не разошлись.
_TRUE_TEXTS = ('1', 'true', 'yes', 'on', 'да')
_FALSE_TEXTS = ('0', 'false', 'no', 'off', 'нет', '')


def _as_bool(value, default):
    """bool('false') — True, поэтому строки разбираем явно: правило правят и из
    формы, и руками через API, и «false» строкой не должно включать выбросы."""
    if value is None:
        return default
    if isinstance(value, str):
        text = normalize_key(value)
        if text in _TRUE_TEXTS:
            return True
        if text in _FALSE_TEXTS:
            return False
        return default
    return bool(value)


class PhoneSettingsError(ValueError):
    """Запрос на правку правила с непригодным значением. Отдельный класс, чтобы
    ручка отвечала 400 только на это, а не на любой ValueError из глубины."""


def _valid_seconds(value) -> bool:
    # bool — тоже int: True молча стал бы «1 секундой», а после подрезки —
    # минутным порогом. Дробь и строка с числом годятся, «5 мин» и NaN — нет.
    if isinstance(value, bool):
        return False
    try:
        int(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def validate_phone_settings_changes(changes) -> dict:
    """Проверить присланную правку правила; вернуть только годные к наложению поля.

    Зачем: нормализаторы ниже на всё непонятное отвечают ЗНАЧЕНИЕМ ПО УМОЛЧАНИЮ,
    а не текущим. Для чтения строки из базы это верно, для правки — нет:
    {"enabled": "выкл"} при выключенном правиле молча включал выбросы с порогом
    300 с обеим группам, и через десять минут телефоны начинали выбрасывать
    операторов по правилу, которого никто не задавал. Поэтому непригодное
    значение — отказ (PhoneSettingsError), а не «как-нибудь сохраним».

    null и поля вне PHONE_SETTINGS_FIELDS пропускаются: null = «не трогать».
    """
    if changes is None:
        return {}
    if not isinstance(changes, dict):
        raise PhoneSettingsError('Ожидался объект с полями правила')
    checked = {}
    for name in PHONE_SETTINGS_FIELDS:
        value = changes.get(name)
        if value is None:
            continue
        if name == 'enabled':
            # Пустая строка для _as_bool — «нет», но из формы это «не заполнено»:
            # выключать правило всему отделу по пустому полю нельзя.
            good = isinstance(value, bool) or (
                isinstance(value, str) and normalize_key(value) != ''
                and normalize_key(value) in _TRUE_TEXTS + _FALSE_TEXTS)
            if not good:
                raise PhoneSettingsError('Поле «Правило включено» должно быть «да» или «нет»')
        elif name == 'threshold_s':
            if not _valid_seconds(value):
                raise PhoneSettingsError('Порог простоя должен быть целым числом секунд')
        elif name == 'warn_before_s':
            if not _valid_seconds(value):
                raise PhoneSettingsError('Время предупреждения должно быть целым числом секунд')
        elif name == 'groups':
            # Строку не берём, хотя normalize_groups её разбирает (так приходит
            # JSONB из базы): в запросе 'yar' вместо ['yar'] — ошибка клиента.
            if (not isinstance(value, (list, tuple))
                    or not all(isinstance(item, str) for item in value)):
                raise PhoneSettingsError('Группы правила должны быть списком кодов групп')
            # Незнакомый код normalize_groups молча выбросил бы: опечатка 'yarr'
            # сняла бы группу с правила, а сохранение выглядело бы успешным.
            unknown = [item for item in value if normalize_key(item) not in IDLE_OFFLINE_GROUPS]
            if unknown:
                raise PhoneSettingsError(
                    'Неизвестная группа правила: ' + ', '.join(str(item) for item in unknown))
        checked[name] = value
    return checked


def normalize_phone_settings(raw) -> dict:
    """Правило автоофлайна в каноническом виде (недостающее — по умолчанию)."""
    raw = raw or {}
    threshold = clamp_idle_threshold(raw.get('threshold_s'))
    return {
        'enabled': _as_bool(raw.get('enabled'), DEFAULT_PHONE_SETTINGS['enabled']),
        'threshold_s': threshold,
        'warn_before_s': clamp_warn_before(raw.get('warn_before_s'), threshold),
        'groups': normalize_groups(raw.get('groups')),
    }


def is_kick_event(status_key, state_note) -> bool:
    """Событие статуса — это автоматический выброс в «Офлайн»?"""
    return (normalize_key(status_key) == KICK_STATUS_KEY
            and normalize_key(state_note) == KICK_STATE_NOTE)


def kick_client_key(client_event_id, user_id=None, event_at=None) -> str:
    """Ключ идемпотентности выброса. Обычно это GUID события телефона: повторная
    доставка того же события не даст второго выброса. Без GUID — оператор и момент."""
    client_event_id = str(client_event_id or '').strip()
    if client_event_id:
        return (KICK_CLIENT_KEY_PREFIX + client_event_id)[:128]
    moment = event_at.isoformat() if hasattr(event_at, 'isoformat') else str(event_at or '')
    return f"{KICK_CLIENT_KEY_PREFIX}{user_id}|{moment}"[:128]


def status_profile_payload(group, settings=None, report_connecting=True):
    """Блок `status_profile` для /api/operator/sip_settings или None (прежний набор).

    report_connecting — слать ли в отчёт фазу «Соединение». На пилюле она есть
    всегда; выключатель нужен серверу: каждое событие статуса пересобирает
    сегменты оператора за трое суток, а «Соединение» — это два лишних события на
    каждый неотвеченный набор. Если нагрузка от исходящих групп окажется велика,
    фазу убирают из отчёта, не выпуская телефон.
    """
    profile = STATUS_PROFILES.get(group)
    if not profile:
        return None
    rule = normalize_phone_settings(settings)
    idle_enabled = (rule['enabled'] and group in rule['groups']
                    and 'outbound' in profile['statuses'])
    return {
        'group': group,
        'group_label': STATUS_GROUP_LABELS.get(group, group),
        'statuses': list(profile['statuses']),
        'start': profile['start'],
        'report_connecting': bool(report_connecting),
        'idle_offline': {
            'enabled': bool(idle_enabled),
            'threshold_s': rule['threshold_s'],
            'warn_before_s': rule['warn_before_s'],
        },
    }
