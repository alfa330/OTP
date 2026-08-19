"""Чистая логика прав раздела «Вики».

Модуль намеренно НЕ импортирует ни database, ни flask, ни bot_schedule2:
импорт database.py открывает пул к боевой БД, из-за чего тесты проекта вынуждены
парсить его через ast. Здесь только функции над данными — их можно импортировать
в тестах напрямую и гонять без базы.

Всё, что требует SQL (собрать группы пользователя, вытащить правила), живёт в
методах Database и передаётся сюда готовыми структурами.

── Порядок вычисления прав на статью ────────────────────────────────────────
1. Собрать субъекты пользователя: отдел + возглавляемые отделы, направление,
   активные группы (как оператор и как супервайзер), роль + все роли ниже по
   иерархии, роли вики, сам пользователь.
2. Если у статьи visibility_mode='restricted' — база берётся ТОЛЬКО из правил
   статьи; правила разделов игнорируются. Иначе — ИЛИ по правилам всех разделов
   статьи, а правила статьи применяются поверх как оверрайд.
3. Правило mode='deny' сильнее любого grant. Без этого нельзя выразить «скрыть
   от одного человека внутри разрешённого отдела».
4. Способность can_manage_access обходит deny — иначе администратор способен
   заблокировать сам себя без возможности восстановить. Но strict_mode обходит
   только super_admin, и такой обход обязан попасть в журнал.
"""

from .schema import CAPABILITY_COLUMNS, PERMISSION_COLUMNS

# Копия ROLE_HIERARCHY из bot_schedule2.py:1501. Дублируется сознательно —
# импортировать bot_schedule2 отсюда нельзя (он поднимает Flask и бота).
# ВНИМАНИЕ: роли 'supervisor' в иерархии OTP нет (её уровень 0), хотя в CHECK
# на users.role она присутствует. Поэтому правило на 'operator' НЕ
# распространяется на 'supervisor' автоматически — см. expand_otp_roles.
ROLE_LEVELS = {
    'operator': 10,
    'trainee': 10,
    'trainer': 20,
    'sv': 30,
    'admin': 40,
    'super_admin': 50,
}

def role_level_of(role):
    """Уровень должности по шкале ROLE_LEVELS. Незнакомая роль — 0.

    Ноль, а не None: уровень участвует в сравнении `уровень >= min_role_level`,
    и незнакомая роль обязана не проходить ни одно ограничение по уровню.
    """
    return ROLE_LEVELS.get(normalize_role(role), 0)


NO_PERMISSIONS = {name: False for name in PERMISSION_COLUMNS}
NO_CAPABILITIES = {name: False for name in CAPABILITY_COLUMNS}


# В базе исторически встречаются оба написания. _normalize_user_role
# (bot_schedule2.py:1492) приводит их к канону — повторяем то же самое, иначе
# носитель роли 'superadmin' не подпал бы ни под одно правило.
_ROLE_ALIASES = {
    'superadmin': 'super_admin',
    'super admin': 'super_admin',
}


def normalize_role(role):
    value = str(role or '').strip().lower()
    return _ROLE_ALIASES.get(value, value)


# ─────────────────────────────────────────────────────────────────────────────
# Способности (глобальные, не привязаны к разделу)
# ─────────────────────────────────────────────────────────────────────────────

def capabilities_from_otp_role(role, is_department_head=False):
    """Способности по умолчанию, когда у человека нет ни одной роли вики.

    Заменяет getLegacyCapabilities оригинала, где ролевая модель была второй,
    параллельной, и расходилась с основной.

    is_department_head — глава отдела. В OTP это НЕ роль, а поле
    headed_department_id в профиле, и isAdminLikeRole специально вычитает таких
    из глобальных админов. Повторяем это правило: глава отдела управляет
    структурой, но только в своём отделе, и не получает can_manage_users.
    """
    role = normalize_role(role)
    caps = dict(NO_CAPABILITIES)

    if not role:
        return caps

    # Читать может любой аутентифицированный сотрудник; что именно он увидит,
    # решают правила разделов и статей, а не эта способность.
    caps['can_read'] = True

    if role == 'super_admin' or (role == 'admin' and not is_department_head):
        for name in CAPABILITY_COLUMNS:
            caps[name] = True
        return caps

    if role == 'admin' and is_department_head:
        caps.update(can_create=True, can_edit=True, can_delete=True,
                    can_publish=True, can_approve=True, can_manage_structure=True)
        return caps

    # Супервайзер ведёт содержимое своего направления целиком, включая выпуск:
    # решение владельца 19.08.2026. До него способность публиковать была только
    # у админов, и выданное в ПРАВИЛЕ РАЗДЕЛА can_publish молча гасилось ниже
    # (право записи требует и правила, и способности) — владелец выдал право в
    # интерфейсе, правило сохранилось, а портал продолжал отвечать «нет права
    # публиковать в этом разделе». Способность — это «вправе ли в принципе»,
    # а где именно, по-прежнему решают правила разделов: там, где can_publish
    # не выписан (например «Оператор» без соответствующего правила), публикации
    # не будет.
    if role in ('sv', 'supervisor'):
        caps.update(can_create=True, can_edit=True,
                    can_publish=True, can_approve=True)
        return caps

    # Тренер остаётся без публикации намеренно: он ведёт обучение, а не выпуск
    # регламентов, и в «Ивентах» право публикации ему тоже не дано.
    if role == 'trainer':
        caps.update(can_create=True, can_edit=True)
        return caps

    # operator, trainee и всё неизвестное — только чтение.
    return caps


def has_write_capability(capabilities):
    """Есть ли у человека хоть одна способность СВЕРХ чтения.

    Этим гейтятся справочники «Парки» и «Офисы» (решение владельца 19.08.2026):
    их правит всякий, кто вообще что-то делает с содержимым, а не только
    носитель can_manage_structure. Раньше супервайзер и тренер могли завести и
    отредактировать статью, но не могли поправить телефон парка — хотя это тот
    же справочный контент, и держать его в актуальном виде некому, кроме них.

    Считается по фактическим способностям, а не по списку ролей: способности
    могут прийти и от роли вики, назначенной руками, и такой человек тоже
    должен попадать под правило.
    """
    return any(capabilities.get(name) for name in CAPABILITY_COLUMNS
               if name != 'can_read')


def merge_capabilities(*sources):
    """ИЛИ по способностям. Deny-правил на уровне способностей нет и не будет:
    в оригинале их тоже нет, а вводить второй механизм запрета — путь к тому,
    что две проверки разойдутся."""
    result = dict(NO_CAPABILITIES)
    for source in sources:
        if not source:
            continue
        for name in CAPABILITY_COLUMNS:
            if source.get(name):
                result[name] = True
    return result


def resolve_capabilities(otp_role, wiki_role_rows, is_department_head=False):
    """Итоговые способности: роли вики, а при их отсутствии — роль OTP.

    wiki_role_rows — список словарей из wiki_roles (уже отфильтрованных по
    пользователю). Пустой список означает «ролей вики не назначено».
    """
    if wiki_role_rows:
        merged = merge_capabilities(*wiki_role_rows)
        # Роль вики никогда не отнимает право читать раздел целиком:
        # запрет выражается правилами, а не отсутствием способности.
        merged['can_read'] = True
        return merged
    return capabilities_from_otp_role(otp_role, is_department_head)


# ─────────────────────────────────────────────────────────────────────────────
# Субъекты правил
# ─────────────────────────────────────────────────────────────────────────────

def expand_otp_roles(role):
    """Роли, под которые подпадает человек: своя плюс все НИЖЕ по иерархии.

    Правило на 'operator' должно действовать и для sv, и для admin — иначе
    руководитель не увидит того, что видит его подчинённый. Это точная замена
    рекурсии по positions.parent_position_id из оригинала.

    'supervisor' обрабатывается отдельно: в ROLE_HIERARCHY его нет (уровень 0),
    поэтому по уровню он не подпадает ни подо что. Считаем его равным 'sv' —
    иначе носители этой роли не увидят вообще ни одного ролевого правила.
    """
    role = normalize_role(role)
    if not role:
        return []

    effective = 'sv' if role == 'supervisor' else role
    level = ROLE_LEVELS.get(effective, 0)

    roles = {role}
    if level:
        roles.update(name for name, value in ROLE_LEVELS.items() if value <= level)
    if role == 'supervisor':
        roles.add('supervisor')
    return sorted(roles)


def collect_subjects(*, user_id, otp_role, department_id=None, headed_department_ids=(),
                     direction_id=None, group_ids=(), wiki_role_ids=()):
    """Все пары (subject_type, ключ), под которые подпадает пользователь.

    Возвращает словарь, готовый для подстановки в SQL-запрос правил:
        {'department': [...], 'department_head': [...], 'direction': [...],
         'group': [...], 'otp_role': [...], 'wiki_role': [...], 'user': [...],
         'role_level': int}
    """
    departments = set()
    if department_id:
        departments.add(int(department_id))
    for value in headed_department_ids or ():
        if value:
            departments.add(int(value))

    headed = sorted({int(value) for value in (headed_department_ids or ()) if value})

    return {
        'department': sorted(departments),
        # Возглавляемые отделы попадают в ОБА ключа намеренно: по 'department'
        # глава получает всё, что открыто его отделу (то самое «видит всё, что
        # ниже себя»), а по 'department_head' — то, что адресовано именно главе.
        'department_head': headed,
        'direction': [int(direction_id)] if direction_id else [],
        'group': sorted({int(value) for value in (group_ids or ()) if value}),
        'otp_role': expand_otp_roles(otp_role),
        'wiki_role': sorted({int(value) for value in (wiki_role_ids or ()) if value}),
        'user': [int(user_id)] if user_id else [],
        'role_level': role_level_of(otp_role),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Слияние правил
# ─────────────────────────────────────────────────────────────────────────────

def _merge_grants(rules):
    """ИЛИ по правам всех разрешающих правил."""
    result = dict(NO_PERMISSIONS)
    for rule in rules:
        for name in PERMISSION_COLUMNS:
            if rule.get(name):
                result[name] = True
    return result


def _collect_denials(rules):
    """Набор прав, которые хотя бы одно deny-правило запрещает."""
    denied = set()
    for rule in rules:
        if rule.get('mode') != 'deny':
            continue
        for name in PERMISSION_COLUMNS:
            # В deny-правиле отмеченное право означает «запретить именно его».
            if rule.get(name):
                denied.add(name)
    return denied


def resolve_article_permissions(*, capabilities, visibility_mode='inherit',
                                strict_mode=False, section_rules=(), article_rules=(),
                                otp_role=None, is_section_owner=False,
                                is_article_owner=False, guest_allows_read=False):
    """Эффективные права на конкретную статью.

    section_rules  — правила разделов, которым принадлежит статья (только grant).
    article_rules  — правила самой статьи, каждое с полем mode ('grant'|'deny').

    Возвращает (permissions, reason), где reason — короткая строка, объясняющая
    решение. Она нужна эндпоинту /api/wiki/access/effective: когда уровней прав
    четыре, администратору необходим ответ на вопрос «почему он это видит».
    """
    role = normalize_role(otp_role)
    is_super_admin = role == 'super_admin'
    is_wiki_admin = bool(capabilities.get('can_manage_access'))

    grants = [r for r in article_rules if r.get('mode') != 'deny']
    denials = _collect_denials(article_rules)

    if visibility_mode == 'restricted':
        permissions = _merge_grants(grants)
        reason = 'правила статьи (режим «только по списку»)'
    else:
        permissions = _merge_grants(list(section_rules) + grants)
        reason = 'правила разделов статьи'
        if grants:
            reason = 'правила разделов + правила статьи'

    # Владелец раздела и автор статьи всегда могут её читать и править:
    # иначе человек способен потерять доступ к тому, что сам ведёт.
    if is_section_owner or is_article_owner:
        permissions['can_read'] = True
        permissions['can_edit'] = True
        reason = 'владелец раздела' if is_section_owner else 'автор статьи'

    if guest_allows_read and not permissions['can_read']:
        permissions['can_read'] = True
        reason = 'гостевой доступ'

    # Запреты сильнее разрешений.
    if denials:
        for name in denials:
            permissions[name] = False
        reason = 'запрещено правилом статьи'

    # Право записи требует и глобальной способности, и разрешения на объекте.
    for name in ('can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve'):
        if permissions[name] and not capabilities.get(name):
            permissions[name] = False

    # Обход администратора. strict_mode оставляет эту дверь только super_admin.
    bypassed = False
    if strict_mode:
        if is_super_admin:
            permissions = {name: True for name in PERMISSION_COLUMNS}
            reason = 'обход super_admin (статья в строгом режиме)'
            bypassed = True
    elif is_wiki_admin:
        permissions = dict(permissions)
        permissions['can_read'] = True
        for name in PERMISSION_COLUMNS:
            if capabilities.get(name):
                permissions[name] = True
        if denials:
            reason = 'администратор вики (перекрывает запрет)'
            bypassed = True
        else:
            reason = 'администратор вики'

    permissions['_reason'] = reason
    permissions['_bypassed_restriction'] = bypassed
    return permissions


def permissions_only(permissions):
    """Отбрасывает служебные поля _reason/_bypassed_restriction."""
    return {name: bool(permissions.get(name)) for name in PERMISSION_COLUMNS}


# ─────────────────────────────────────────────────────────────────────────────
# Кто кому может выдавать доступ («зернистость»)
# ─────────────────────────────────────────────────────────────────────────────
#
# Решение владельца 18.08.2026: право раздавать доступ само ограничено
# должностью раздающего.
#
#     Коммерческий директор  → ЛЮБОЙ сотрудник, включая других директоров
#     Руководитель группы    → СВ, тренер, оператор
#     Супервайзер            → оператор
#     Тренер, оператор       → не раздают вовсе, только читают
#
# Таблица, а не арифметика «на ступень ниже»: у супервайзера ступенька
# ПЕРЕПРЫГИВАЕТ тренера. Это не описка — так сформулировал владелец: СВ
# раздаёт операторам, тренера не трогает. Вывести это формулой нельзя, а
# формула, которая «почти совпадает», разошлась бы с решением молча.
#
# ДИРЕКТОР ВЫДАЁТ И СВОЕМУ УРОВНЮ — «может добавить любого сотрудника»
# (дословное требование владельца). Правило «никто не выдаёт своему уровню»
# действует ниже по лестнице, но наверху лишено смысла: над директором никого
# нет, эскалировать некуда, а супер-админ и так видит все разделы
# (queries.from_super_admin). Потолок 40 отрезал от списка пятерых
# супер-админов, и владелец не смог выписать правило на коллегу.
#
# Значение — максимальный уровень, которому носитель роли вправе открыть
# раздел. Роли, которой в таблице нет, выдавать нельзя вообще.
GRANT_CEILING = {
    'super_admin': 50,
    'admin': 30,
    'sv': 10,             # только оператор; тренер (20) пропущен намеренно
    'supervisor': 10,
}

# Правило без порога открывает раздел всем от оператора и выше, поэтому его
# «вес» равен уровню оператора. Отдельная константа, чтобы NULL не превращался
# в ноль где-нибудь по дороге: ноль пропустил бы любую проверку.
UNBOUNDED_RULE_LEVEL = ROLE_LEVELS['operator']


def grant_ceiling(otp_role, *, is_wiki_admin=False):
    """До какого уровня должности человек вправе открывать разделы.

    None — не вправе вовсе. is_wiki_admin (способность can_manage_access,
    выданная РОЛЬЮ ВИКИ, а не должностью) поднимает потолок до максимума:
    администратора вики назначают руками именно для этого.
    """
    if is_wiki_admin:
        return ROLE_LEVELS['super_admin']
    return GRANT_CEILING.get(normalize_role(otp_role))


def rule_grant_level(min_role_level):
    """Уровень, которому правило фактически открывает раздел."""
    return UNBOUNDED_RULE_LEVEL if min_role_level is None else int(min_role_level)


def may_grant_with_ceiling(ceiling, min_role_level, target_role=None):
    """Проходит ли правило под уже посчитанный потолок.

    Потолок приходит готовым, а не выводится из роли второй раз: роут считает
    его один раз на запрос (там же решается вопрос про роль вики), и повторный
    вывод — это второй источник истины, который однажды разойдётся с первым.

    target_role — роль КОНКРЕТНОГО адресата (правило на человека). Без неё
    супервайзер выписал бы правило subject_type='user' на самого себя и выдал
    бы себе полный доступ к любому разделу: порог там NULL, и одна лишь
    проверка порога такое пропускает.
    """
    if ceiling is None:
        return False
    if rule_grant_level(min_role_level) > ceiling:
        return False
    if target_role is not None and role_level_of(target_role) > ceiling:
        return False
    return True


def may_grant_rule(otp_role, min_role_level, *, is_wiki_admin=False,
                   target_role=None):
    """Вправе ли носитель роли выписать такое правило."""
    return may_grant_with_ceiling(
        grant_ceiling(otp_role, is_wiki_admin=is_wiki_admin),
        min_role_level, target_role)
