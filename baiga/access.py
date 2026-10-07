"""Права раздела «Списки Байги». Чистая логика: ни базы, ни Flask.

Кому раздел открыт — решение владельца 05.10.2026:

    супер-админ                     всё
    глава «Маркетинга»              всё («доступ полный»): загрузка, замена и
                                    удаление недели, выгрузка, журнал
    аналитик, поимённо              то же и без QR — список ANALYST_USER_IDS
                                    (06.10.2026: «без qr, полный доступ к разделу»)
    главы ОП и СЗоВ                 поиск и просмотр
    супервайзеры ОП и СЗоВ          поиск и просмотр
    операторы ОП и СЗоВ,
    сотрудники «Маркетинга»         поиск и просмотр — после QR
    остальные                       раздел закрыт

«Остальные» — это и те, кого постановка #356 (п. 2) называла шире: главы прочих
отделов, админ, не возглавляющий отдел, тренер. Владелец перечислил круг сам, и
расширять его «по смыслу» нельзя: в строках ФИО и номер ВУ водителя.

С 07.10.2026 к этому кругу добавляются ВЫДАЧИ из самого раздела — кнопка
«Доступ» (решение владельца: «в самом разделе можно было раздавать доступы…
группам, людям и отделам»). Выдача — строка baiga_access_grants: человек,
группа или отдел и уровень (LEVELS). Она только ДОБАВЛЯЕТ: круг выше не сужает
и уровень, положенный по нему, не понижает. Раздают супер-админ и двое
названных владельцем (ACCESS_MANAGER_USER_IDS) — и только они.

Рядовой сотрудник входит ТОЛЬКО через QR-замок, ключ тот же, что у «Посылок»
(двух разных QR на один экран человек не различит). Кого общий замок портала не
спрашивает и кто не назван выше (стажёр, тренер), тому раздел по кругу закрыт:
открыть его без замка значило бы показать ФИО и номера ВУ без подтверждения.
Вошедшего по выдаче спрашивают так же — см. requires_sensitive_qr.

Аналитик — единственный, кого замок не спрашивает вопреки должности: он назван
поимённо, и раздел ему выдан целиком. Числится он оператором, и общий замок
запер бы от него тот самый раздел, который он ведёт (requires_sensitive_qr).

Главы названы отделом, а не id: назначение главой живёт в
departments.head_user_id и переживает и смену человека, и его вторую учётку.

Должность в контексте — как она записана в карточке (queries.load_access_context),
а не сведённая к оператору: замок отличает кадровика от оператора.
"""

from driver_chats.access import QR_GATED_ROLES as _WIDE_QR_ROLES
from parcels.access import is_department_head, normalize_role
from wiki.access import QR_GATED_ROLES as _COMMON_QR_ROLES
from wiki.access import normalize_role as _exact_role

# Выключатель: пока флаг стоит, раздел открыт только супер-админу — так он
# выкладывался 02.10.2026 и так же закрывается обратно одной строкой. Зеркало во
# фронте — BAIGA_PILOT_SUPER_ADMIN_ONLY в App.jsx, тест сверяет их: переключать
# в обоих местах.
PILOT_SUPER_ADMIN_ONLY = False

# Аналитики поимённо — только id, ФИО в публичный репозиторий не кладём.
# Зеркало — BAIGA_ANALYST_USER_IDS в App.jsx (аналитику нужен пункт меню).
# 540 — сотрудник отдела аналитики, назван владельцем 06.10.2026.
ANALYST_USER_IDS = frozenset({540})

# Кто раздаёт доступ к разделу помимо супер-админа — поимённо, только id
# (решение владельца 07.10.2026: кнопка «Доступ» «будет у суперадминов» и двоих
# названных). Зеркала во фронте нет: кнопку рисует сводка capabilities, а пункт
# меню — флаг baiga_access из профиля.
# 415 — глава «Маркетинга», постановщик раздела.
ACCESS_MANAGER_USER_IDS = frozenset({415})

MANAGE_DEPARTMENT_CODE = 'marketing'     # глава ведёт раздел, сотрудники читают после QR
READ_DEPARTMENT_CODES = ('op', 'szov')   # глава и СВ читают, операторы — после QR

SECTION_DEPARTMENT_CODES = (MANAGE_DEPARTMENT_CODE,) + READ_DEPARTMENT_CODES

_SUPERVISOR_ROLES = ('sv', 'supervisor')

# Уровни доступа — по возрастанию, каждый включает предыдущий:
#   read    искать и смотреть;
#   export  ещё и выгружать выборку в Excel;
#   full    ещё и вести раздел: загрузка, замена и удаление недели, журнал,
#           исходник.
# Зеркало с подписями — LEVELS в src/components/baiga/baigaAccess.js.
LEVEL_READ, LEVEL_EXPORT, LEVEL_FULL = 'read', 'export', 'full'
LEVELS = (LEVEL_READ, LEVEL_EXPORT, LEVEL_FULL)

# Кому выдают: человеку, группе, отделу — ровно то, что назвал владелец.
SUBJECT_USER, SUBJECT_GROUP, SUBJECT_DEPARTMENT = 'user', 'group', 'department'
SUBJECT_TYPES = (SUBJECT_USER, SUBJECT_GROUP, SUBJECT_DEPARTMENT)

# Сколько адресатов принимает одна выдача (зеркало — MAX_SUBJECTS в
# baigaAccess.js): форма отмечает их галочками, и сотня строк одним
# сохранением — уже опечатка, а не выдача.
MAX_GRANT_SUBJECTS = 50

# Кого раздел спрашивает о QR: всех, кому портал вообще выдаёт код (тот же
# состав, что SENSITIVE_QR_GATED_ROLES в bot_schedule2.py). Шире общего замка на
# стажёра: по кругу раздел стажёру закрыт, но выдать его можно и ему — и тогда
# стажёр без подтверждения читал бы то, что оператор рядом открывает кодом.
# Кадровика в наборе нет: код ему портал не выдаёт (решение владельца
# 22.09.2026), и спросить его значило бы запереть выданный раздел навсегда.
QR_ASKED_ROLES = frozenset(_COMMON_QR_ROLES) | frozenset(_WIDE_QR_ROLES)


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _own_code(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def _heads(ctx, codes):
    """Глава хотя бы одного из отделов."""
    return bool(_codes(ctx.get('headed_department_codes')) & set(codes))


def _user_id(ctx):
    try:
        return int(ctx.get('user_id'))
    except (TypeError, ValueError):
        return None


def is_analyst(ctx):
    return _user_id(ctx) in ANALYST_USER_IDS


def is_access_manager(ctx):
    """Раздаёт ли человек доступ к разделу: супер-админ и названные поимённо."""
    if normalize_role(ctx.get('role')) == 'super_admin':
        return True
    return _user_id(ctx) in ACCESS_MANAGER_USER_IDS


def _asked(ctx, roles):
    """Рядовой с должностью из набора. Главу отдела замок не спрашивает —
    подтверждать доступ ему не у кого; должностей админа и супервайзера в
    наборах нет."""
    return not is_department_head(ctx) and _exact_role(ctx.get('role')) in roles


def requires_sensitive_qr(ctx):
    """Нужно ли подтвердить сессию QR-кодом, прежде чем раздел отдаст данные.

    Рядовой — оператор, стажёр, сотрудник бухгалтерии и маркетинга, — по кругу
    он вошёл или по выдаче; кроме аналитика из именного списка: ему раздел
    открыт без замка. Спрашивают это и ручки раздела, и ИИ-помощник
    (baiga/assistant.py) — второй путь к тем же строкам. У помощника перед этим
    стоит ещё и замок самой вики (wiki_route): его это правило не снимает.
    Зеркало — baigaQrRequiredFor в App.jsx.
    """
    if is_analyst(ctx):
        return False
    return _asked(ctx, QR_ASKED_ROLES)


def level_rank(level):
    """Место уровня на шкале LEVELS с единицы; незнакомый уровень — ноль."""
    return LEVELS.index(level) + 1 if level in LEVELS else 0


def strongest(levels):
    """Старший из уровней или None. Незнакомые значения не считаются: строка с
    опечаткой в базе обязана не открывать ничего."""
    return max((level for level in (levels or ()) if level in LEVELS), key=level_rank, default=None)


def _can_read(ctx):
    """Искать и смотреть по кругу раздела — без выгрузки и загрузки."""
    if _heads(ctx, READ_DEPARTMENT_CODES):
        return True
    own = _own_code(ctx)
    if normalize_role(ctx.get('role')) in _SUPERVISOR_ROLES:
        return own in READ_DEPARTMENT_CODES
    # Рядовой — только тот, кого спросит ОБЩИЙ замок портала: одно условие и
    # пускает в раздел, и закрывает данные, разъехаться им негде.
    return _asked(ctx, _COMMON_QR_ROLES) and own in SECTION_DEPARTMENT_CODES


def _circle_level(ctx):
    """Уровень по кругу раздела — без выдач."""
    if (normalize_role(ctx.get('role')) == 'super_admin' or is_analyst(ctx)
            or _heads(ctx, (MANAGE_DEPARTMENT_CODE,))):
        return LEVEL_FULL
    return LEVEL_READ if _can_read(ctx) else None


def level_of(ctx):
    """Уровень человека в разделе: старший из положенного по кругу и выданного
    (ctx['grant_levels'] — уровни выдач, под которые он подпадает). None —
    раздел закрыт.

    Раздающему доступ раздел открыт на чтение и без выдачи: кнопка «Доступ»
    живёт внутри раздела, и человеку, который решает, кому показывать списки,
    незачем быть тем единственным, кто их не видит.
    """
    if PILOT_SUPER_ADMIN_ONLY:
        return LEVEL_FULL if normalize_role(ctx.get('role')) == 'super_admin' else None
    levels = [_circle_level(ctx), strongest(ctx.get('grant_levels'))]
    if is_access_manager(ctx):
        levels.append(LEVEL_READ)
    return strongest(levels)


def circle():
    """Круг раздела строками — для листа «Доступ»: раздающий обязан видеть, кому
    раздел открыт и без выдач, иначе он выдаёт то, что уже выдано, и не видит
    того, чего выдать нельзя.

    Строки — те же ветки, что у _circle_level и _can_read, в том же порядке;
    тест сверяет каждую с настоящим правилом. Названия отделов и имена
    подставляет ручка: здесь только коды и id.
    """
    return (
        {'key': 'super_admin', 'level': LEVEL_FULL, 'qr': False},
        {'key': 'head', 'level': LEVEL_FULL, 'qr': False, 'departments': (MANAGE_DEPARTMENT_CODE,)},
        {'key': 'named', 'level': LEVEL_FULL, 'qr': False, 'user_ids': tuple(sorted(ANALYST_USER_IDS))},
        {'key': 'lead', 'level': LEVEL_READ, 'qr': False, 'departments': READ_DEPARTMENT_CODES},
        {'key': 'staff', 'level': LEVEL_READ, 'qr': True, 'departments': SECTION_DEPARTMENT_CODES},
    )


def can_manage(ctx):
    """Загрузить, заменить и удалить неделю, журнал, исходник — кому раздел
    открыт полностью: супер-админ, глава «Маркетинга», аналитик и те, кому
    полный доступ выдан."""
    return level_of(ctx) == LEVEL_FULL


def can_export(ctx):
    """Выгрузить выборку в Excel — от уровня «выгрузка».

    По кругу раздела это только полный доступ: главам и супервайзерам ОП и
    СЗоВ, операторам и сотрудникам «Маркетинга» владелец дал чтение — файл со
    всеми ФИО и номерами ВУ они не уносят. Выгрузку без ведения раздела даёт
    только выдача.
    """
    return level_rank(level_of(ctx)) >= level_rank(LEVEL_EXPORT)


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является. Зеркало — canAccessBaigaSectionForUser в App.jsx."""
    return level_of(ctx) is not None


def can_manage_access(ctx):
    """Раздавать доступ к разделу. Выключатель закрывает и это: пока раздел
    открыт одному супер-админу, раздавать его некому и незачем."""
    return can_open_section(ctx) and is_access_manager(ctx)


def capabilities(ctx):
    """Сводка для фронта: экран рисует кнопки по ней, а не по роли.

    Кто в раздел не пущен (выключатель, чужой отдел), не получает и прав внутри:
    иначе сводка обещала бы кнопки, на которые каждая ручка ответит 403.
    """
    return {
        'can_open': can_open_section(ctx),
        'can_export': can_export(ctx),
        'can_manage': can_manage(ctx),
        'can_manage_access': can_manage_access(ctx),
    }
