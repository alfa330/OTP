"""SQL-слой раздела «Вики».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и ничего не знают
про пул и транзакции — их открывает вызывающий, как это делает call_qa.

Почему один запрос вместо нескольких. Пул проекта — MIN_CONN=16 / MAX_CONN=40
(database.py), и эти же слоты делит SSE-фанаут аукциона смен; пул-старвейшн
в проекте уже случался. В оригинальной вики матрица доступа делала по два
запроса НА КАЖДУЮ должность, а просмотр статьи стоил двух отдельных запросов
без транзакции. Здесь контекст доступа собирается одним CTE.
"""

from . import access as wiki_access
from . import schema as wiki_schema
from .access import normalize_role

# Один запрос вместо пяти: профиль, возглавляемые отделы, активные группы
# (и как оператор, и как супервайзер), роли вики, режим доступа.
_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT u.id, u.role, u.department_id, u.direction_id,
           -- Тумблер «раздел Вики выдан отделу». У сотрудника без отдела
           -- (админы, служебные учётки) отдела нет — им раздел не закрываем.
           COALESCE(d.wiki_enabled, TRUE) AS wiki_enabled
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
     WHERE u.id = %(user_id)s
),
headed AS (
    SELECT d.id
      FROM departments d
     WHERE d.head_user_id = %(user_id)s
       AND d.is_active
),
my_groups AS (
    SELECT gom.group_id
      FROM group_operator_memberships gom
      JOIN groups g ON g.id = gom.group_id AND g.status = 'active'
     WHERE gom.operator_id = %(user_id)s
       AND gom.start_date <= CURRENT_DATE
       AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
    UNION
    SELECT gsm.group_id
      FROM group_supervisor_memberships gsm
      JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
     WHERE gsm.supervisor_id = %(user_id)s
       AND gsm.start_date <= CURRENT_DATE
       AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
),
my_wiki_roles AS (
    SELECT r.id, r.code, r.can_read, r.can_create, r.can_edit, r.can_delete,
           r.can_publish, r.can_approve, r.can_manage_users,
           r.can_manage_structure, r.can_manage_access
      FROM wiki_roles r
      JOIN wiki_user_roles ur ON ur.wiki_role_id = r.id
     WHERE ur.user_id = %(user_id)s
),
-- Есть ли у человека хоть одна ДЕЙСТВУЮЩАЯ гостевая выдача. Одним признаком, а
-- не списком: списком его спрашивает /ping ради подписи в шапке, а здесь он
-- нужен как ключ от двери — тумблер отдела «Вики выдана» гостя не касается
-- (wiki/routes.py). Без этого выдача сотруднику отдела, которому вики не
-- выдали, оборачивалась бы 403 на каждом запросе: доступ есть, войти нельзя.
my_guest_access AS (
    SELECT 1
      FROM wiki_guest_access g
     WHERE g.user_id = %(user_id)s
       AND g.revoked_at IS NULL
       AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
     LIMIT 1
)
SELECT
    (SELECT role          FROM me)                                   AS otp_role,
    (SELECT department_id FROM me)                                   AS department_id,
    (SELECT direction_id  FROM me)                                   AS direction_id,
    (SELECT wiki_enabled  FROM me)                                   AS wiki_enabled,
    COALESCE((SELECT array_agg(id)       FROM headed),     '{}')     AS headed_department_ids,
    COALESCE((SELECT array_agg(group_id) FROM my_groups),  '{}')     AS group_ids,
    COALESCE((SELECT json_agg(row_to_json(my_wiki_roles)) FROM my_wiki_roles), '[]') AS wiki_roles,
    COALESCE((SELECT access_mode FROM wiki_user_access_settings
               WHERE user_id = %(user_id)s), 'auto')                 AS access_mode,
    EXISTS (SELECT 1 FROM my_guest_access)                           AS has_guest_access
"""


def load_access_context(cursor, user_id):
    """Всё, что нужно для вычисления прав, одним обращением к базе.

    Возвращает словарь; wiki_roles — список словарей с флагами способностей,
    пригодный для wiki.access.resolve_capabilities.
    """
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': user_id})
    row = cursor.fetchone()
    if not row:
        return None

    (otp_role, department_id, direction_id, wiki_enabled,
     headed, groups, wiki_roles, access_mode, has_guest_access) = row
    return {
        'user_id': int(user_id),
        'otp_role': otp_role,
        'department_id': department_id,
        'direction_id': direction_id,
        'wiki_enabled': wiki_enabled is not False,
        'headed_department_ids': list(headed or []),
        'group_ids': list(groups or []),
        'wiki_roles': list(wiki_roles or []),
        'access_mode': access_mode or 'auto',
        'has_guest_access': bool(has_guest_access),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Периметр доступа
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Совпадение правила с пользователем — ОДНО определение на весь раздел.
#
# Держать одной строкой обязательно: в оригинальной вике условие было
# продублировано в двух вычислителях, они разошлись, и дерево навигации со
# списком статей показывали разное. Здесь его импортирует и wiki/articles.py.
#
# Две оси, а не одна:
#   субъект       — под кого выписано правило (отдел, назначение главой, роль…);
#   min_role_level — ниже какого уровня должности правило не действует.
# Связка нужна, чтобы выразить «отдел И не ниже супервайзера»: правилом на один
# лишь department раздел супервайзера открылся бы операторам, а правилом на
# otp_role='sv' — супервайзерам ЧУЖИХ отделов.
# ─────────────────────────────────────────────────────────────────────────────
SUBJECT_MATCH = """
        (
            (r.subject_type = 'department'      AND r.subject_id   = ANY(%(departments)s))
         OR (r.subject_type = 'department_head' AND r.subject_id   = ANY(%(headed)s))
         OR (r.subject_type = 'direction'       AND r.subject_id   = ANY(%(directions)s))
         OR (r.subject_type = 'group'           AND r.subject_id   = ANY(%(groups)s))
         OR (r.subject_type = 'otp_role'        AND r.subject_role = ANY(%(roles)s))
         OR (r.subject_type = 'wiki_role'       AND r.subject_id   = ANY(%(wiki_roles)s))
         OR (r.subject_type = 'user'            AND r.subject_id   = %(user_id)s)
        )
        AND (r.min_role_level IS NULL OR %(role_level)s >= r.min_role_level)
"""


def from_super_admin(ctx):
    """Супер-админ ли это. Роль, а не способность.

    Способность can_manage_access шире роли: её несёт ещё и роль вики,
    назначенная руками, поэтому «показать всё» на неё вешать нельзя. (Роль OTP
    'admin' её раздавала до 21.08.2026 — теперь руководитель ходит по правилам.)
    """
    return normalize_role(ctx.get('otp_role')) == 'super_admin'


def subject_params(subjects, user_id):
    """Параметры подстановки для SUBJECT_MATCH.

    Пустые списки заменяются заведомо непопадающим значением: `= ANY('{}')`
    в постгресе не ошибка, но и не совпадение, а вот NULL сравнивать нельзя.
    Уровень роли приезжает готовым из collect_subjects — второй раз выводить
    его из строки роли негде и незачем.
    """
    return {
        'user_id': user_id,
        'departments': subjects['department'] or [-1],
        'headed': subjects['department_head'] or [-1],
        'directions': subjects['direction'] or [-1],
        'groups': subjects['group'] or [-1],
        'roles': subjects['otp_role'] or [''],
        'wiki_roles': subjects['wiki_role'] or [-1],
        'role_level': subjects['role_level'],
    }


# Граница пространства — ПОСЛЕДНЕЕ слово о том, что человек видит.
#
# Накладывается поверх любого способа получить раздел: правила, публичность,
# собственный раздел, ручная выдача. Именно поэтому она оформлена отдельным
# фильтром снаружи, а не ещё одним условием в каждой ветке UNION: ветку добавят,
# условие забудут — и граница протечёт ровно там.
#
# Пустой список отделов у пространства = видно всем (обратная совместимость,
# см. wiki/schema.py). Собственный раздел границу НЕ обходит: пространство
# закрыли от отдела целиком, и «но это же его раздел» здесь не аргумент.
#
# ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ — ИМЕННАЯ ГОСТЕВАЯ ВЫДАЧА (решение владельца
# 25.08.2026). До неё исключений не было вовсе, и это было правильно: тогда
# гостевую выдачу нельзя было СДЕЛАТЬ — двери не существовало, механика лежала
# в схеме мёртвой, и «гостевая ссылка границу не обходит» стоило ровно ничего.
#
# Теперь владелец попросил обратного дословно: «чтобы можно было ЛЮБОМУ
# сотруднику из icore предоставить гостевой доступ… отделом ограничен объект».
# Сосед из другого отдела — это и есть весь смысл механики: коллеге по отделу
# раздел и так открыт правилом. Оставь мы границу абсолютной — выдача сохранялась
# бы, в списке значилась «действующей», /ping честно отдавал бы срок, а человек
# видел бы пустой экран. Молчаливый отказ, от которого этот раздел лечили дважды.
#
# Щель узкая по построению, и расширить её нельзя случайно:
#   * только разделы, выданные ЭТОМУ человеку поимённо (g.user_id), — общего
#     послабления отделу или роли здесь нет и быть не может;
#   * только пока выдача жива: revoked_at IS NULL и срок не вышел, потолок
#     срока — 14 дней (schema.MAX_GUEST_DAYS);
#   * только сам выданный раздел и его подразделы, если выдававший это отметил;
#     соседние ветки того же пространства не открываются.
# Всё остальное — публичные разделы чужого пространства, собственные разделы,
# правила — границу по-прежнему не проходит.
_SPACE_GATE_SQL = """
SELECT p.id
  FROM picked p
  JOIN wiki_sections s ON s.id = p.id
 WHERE p.id IN (SELECT id FROM guest_seed UNION SELECT id FROM guest_tree)
    OR NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                    WHERE sd.space_id = s.space_id)
    OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                WHERE sd.space_id = s.space_id
                  AND sd.department_id = ANY(%(departments)s))
"""


# ─────────────────────────────────────────────────────────────────────────────
# ГОСТЕВАЯ ВЫДАЧА НА РАЗДЕЛ
#
# Раскрывается на подразделы, если так решил выдававший
# (wiki_guest_access.include_subsections). Раскрытие нужно потому, что человек,
# открывающий гостю «Регламент СЗоВ», имеет в виду раздел со всем, что в нём
# лежит, — ровно как соседний тумблер grant_subsections у правила.
#
# Определение ОДНО на оба режима, автоматический и ручной. Скопированное в оба
# запроса, оно однажды разойдётся: в исходной вике два вычислителя доступа
# разошлись именно так, и дерево разделов со списком статей показывали разное
# (см. шапку модуля). Границу пространства раскрытие не трогает — она наложена
# снаружи union'а (_SPACE_GATE_SQL) и подразделами не обходится.
#
# Архивные подразделы не подхватываются (status = 'active' в обходе), а сам
# выданный раздел берётся как есть — так же, как было до раскрытия: отзывать
# гостю доступ к разделу, убранному в архив, незачем, он и так пуст.
# ─────────────────────────────────────────────────────────────────────────────
_GUEST_SECTIONS_CTE = """
guest_seed AS (
    SELECT g.section_id AS id, g.include_subsections AS deep
      FROM wiki_guest_access g
     WHERE g.user_id = %(user_id)s
       AND g.section_id IS NOT NULL
       AND g.revoked_at IS NULL
       AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
),
guest_tree AS (
    SELECT id FROM guest_seed WHERE deep
    UNION
    SELECT child.id
      FROM wiki_sections child
      JOIN guest_tree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
),
"""

_GUEST_SECTIONS_PICK = """
UNION
SELECT id FROM guest_seed
UNION
SELECT id FROM guest_tree
"""


# Разделы, доступные на чтение в АВТОМАТИЧЕСКОМ режиме.
#
# «Публичный» раздел с недавних пор не обязательно значит «всем в компании»:
# у него может быть список отделов (wiki_section_public_departments). Пустой
# список сохраняет прежний смысл — виден всем; заполненный сужает до
# перечисленных отделов. Понадобилось, потому что «Общий сотрудник» открывался
# и Тез КЦ, которому вики не предназначена.
#
# Один рекурсивный CTE вместо двух расходившихся вычислителей оригинала
# (getRuleAllowedSectionIds и getUserAllowedSections считали доступ по-разному,
# из-за чего дерево навигации и список статей показывали разное).
#
# Порядок: правила по субъектам → потомки тех правил, где разрешено поддерево →
# публичные разделы → собственные разделы → действующий гостевой доступ.
_AUTO_SECTIONS_SQL = """
WITH RECURSIVE rule_hits AS (
    SELECT r.section_id, bool_or(r.grant_subsections) AS deep
      FROM wiki_section_access_rules r
      JOIN wiki_sections s ON s.id = r.section_id AND s.status = 'active'
     WHERE r.can_read
       AND (
"""  + SUBJECT_MATCH + """
       )
     GROUP BY r.section_id
),
subtree AS (
    SELECT section_id AS id FROM rule_hits WHERE deep
    UNION
    SELECT child.id
      FROM wiki_sections child
      JOIN subtree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
),
"""  + _GUEST_SECTIONS_CTE + """
picked AS (
SELECT id FROM subtree
UNION
SELECT section_id FROM rule_hits
UNION
SELECT s.id FROM wiki_sections s
 WHERE s.status = 'active' AND s.visibility_scope = 'public'
   AND (
        -- Список отделов не заведён — раздел публичен «как раньше», для всех.
        NOT EXISTS (SELECT 1 FROM wiki_section_public_departments d
                     WHERE d.section_id = s.id)
        OR EXISTS (SELECT 1 FROM wiki_section_public_departments d
                    WHERE d.section_id = s.id
                      AND d.department_id = ANY(%(departments)s))
   )
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND owner_user_id = %(user_id)s
"""  + _GUEST_SECTIONS_PICK + """
)
""" + _SPACE_GATE_SQL

# Ручной режим: человек видит ровно то, что ему выдали, плюс публичное.
# Выдача отдела раскрывается во все разделы пространств этого отдела.
_MANUAL_SECTIONS_SQL = """
WITH RECURSIVE seed AS (
    SELECT s.id
      FROM wiki_sections s
      JOIN wiki_user_manual_access m ON m.section_id = s.id
     WHERE m.user_id = %(user_id)s AND s.status = 'active'
    UNION
    SELECT s.id
      FROM wiki_sections s
      JOIN wiki_spaces sp ON sp.id = s.space_id
      JOIN wiki_user_manual_access m ON m.department_id = sp.department_id
     WHERE m.user_id = %(user_id)s AND s.status = 'active'
),
subtree AS (
    SELECT id FROM seed
    UNION
    SELECT child.id
      FROM wiki_sections child
      JOIN subtree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
),
"""  + _GUEST_SECTIONS_CTE + """
picked AS (
SELECT id FROM subtree
UNION
SELECT s.id FROM wiki_sections s
 WHERE s.status = 'active' AND s.visibility_scope = 'public'
   AND (
        -- Список отделов не заведён — раздел публичен «как раньше», для всех.
        NOT EXISTS (SELECT 1 FROM wiki_section_public_departments d
                     WHERE d.section_id = s.id)
        OR EXISTS (SELECT 1 FROM wiki_section_public_departments d
                    WHERE d.section_id = s.id
                      AND d.department_id = ANY(%(departments)s))
   )
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND owner_user_id = %(user_id)s
"""  + _GUEST_SECTIONS_PICK + """
)
""" + _SPACE_GATE_SQL


def allowed_section_ids(cursor, ctx, subjects, *, master_key=True):
    """Идентификаторы разделов, доступных пользователю на чтение.

    Администратор доступов видит все активные разделы — короткое замыкание.
    ВАЖНО: проверка админа стоит ДО раннего выхода по пустому периметру.
    В оригинале порядок был обратный, и администратор вики не мог создать
    статью, пока ему не выдали хотя бы один раздел правилом.

    master_key=False отключает это замыкание и считает ЛИЧНЫЙ периметр — то,
    к чему у человека есть отношение по правилам. Витрины для чтения (список
    статей, поиск, дерево разделов) считают именно его.
    """
    # Супер-админ видит статьи всех отделов — по решению владельца, и в личном
    # периметре тоже. Отдельно от мастер-ключа: can_manage_access несёт ещё и
    # роль вики, назначенная руками, и ей в витринах чужие отделы показывать
    # нельзя (ровно ради этого заведён master_key=False).
    if from_super_admin(ctx) or (master_key and ctx['capabilities'].get('can_manage_access')):
        cursor.execute("SELECT id FROM wiki_sections WHERE status = 'active'")
        return {row[0] for row in cursor.fetchall()}

    params = subject_params(subjects, ctx['user_id'])
    sql = _MANUAL_SECTIONS_SQL if ctx.get('access_mode') == 'manual' else _AUTO_SECTIONS_SQL
    cursor.execute(sql, params)
    return {row[0] for row in cursor.fetchall()}


def sections_of_space(cursor, section_ids, space_id):
    """Из уже посчитанного периметра оставить разделы ОДНОГО пространства.

    Отдельным запросом по готовому множеству, а не условием внутри периметра:
    периметр отвечает на вопрос «что человеку доступно», а это — «что он сейчас
    смотрит». Смешать их значит получить витрину, которая молча теряет доступ
    вместе со сменой выбранного пространства.
    """
    ids = sorted(int(x) for x in (section_ids or ()))
    if not ids:
        return set()
    cursor.execute(
        'SELECT id FROM wiki_sections WHERE id = ANY(%s) AND space_id = %s',
        (ids, int(space_id)),
    )
    return {row[0] for row in cursor.fetchall()}


def articles_of_space(cursor, article_ids, space_id):
    """Из уже посчитанного периметра оставить статьи ОДНОГО пространства.

    Статья принадлежит пространству через свои разделы. Статья БЕЗ разделов не
    принадлежит никакому — и остаётся видимой в любом: так живёт статья-
    классификатор, которую заводит сама схема, и наследие импорта. Прятать их
    по пространствам было бы нечестно: у них нет пространства, чтобы прятаться,
    а границу они не пробивают — без раздела статью видно только тому, кому её
    выдали правилом лично (см. wiki/articles.py: _VISIBLE_ARTICLES_SQL, ветка
    inherited требует раздела из периметра).
    """
    ids = sorted(int(x) for x in (article_ids or ()))
    if not ids:
        return set()
    cursor.execute(
        """
        SELECT a.id
          FROM wiki_articles a
         WHERE a.id = ANY(%(ids)s)
           AND (EXISTS (SELECT 1 FROM wiki_article_sections x
                          JOIN wiki_sections sec ON sec.id = x.section_id
                         WHERE x.article_id = a.id AND sec.space_id = %(space)s)
                OR NOT EXISTS (SELECT 1 FROM wiki_article_sections x
                                WHERE x.article_id = a.id))
        """,
        {'ids': ids, 'space': int(space_id)},
    )
    return {row[0] for row in cursor.fetchall()}


def spaces_for_user(cursor, ctx, include_guest=True):
    """Пространства, которые человеку можно предложить в переключателе.

    Считается ПО ГРАНИЦЕ ПРОСТРАНСТВА, а не по разделам: пустое пространство,
    только что заведённое под нового клиента, обязано появиться в переключателе
    сразу — иначе его создатель не сможет в него зайти, чтобы завести первый
    раздел, и конструктор окажется бесполезен ровно в момент создания.

    Супер-админ видит все активные — он и настраивает границы.

    ГОСТЬ. Пространство, в котором человеку выдали раздел или статью, попадает
    в переключатель, даже если его отдел за границей этого пространства. Иначе
    исключение в _SPACE_GATE_SQL осталось бы половинчатым: разделы посчитаны,
    а прийти к ним некуда — переключатель пуст, и вика открывается пустой.
    Пространство появляется ровно на срок выдачи и исчезает вместе с ней.

    include_guest=False убирает эту прибавку и отвечает на ДРУГОЙ вопрос — не
    «куда человек может прийти», а «какое пространство ему ВЫДАНО». Разница не
    косметическая: по второму списку пускают к справочникам «Парки» и «Офисы»
    (routes_structure._space_scope), а гостя туда не звали. Его пригласили
    прочитать один раздел, и открывать ему заодно телефоны парков — тем более
    на правку, если по должности он редактор, — значит выдать сверх выданного.
    Ровно это и случилось на первом прогоне 25.08.2026: тренер из отдела без
    вики, получив гостевой доступ к одному разделу, завёл парк в справочнике.
    """
    if from_super_admin(ctx) or ctx['capabilities'].get('can_manage_access'):
        cursor.execute("SELECT id FROM wiki_spaces WHERE status = 'active'")
        return [row[0] for row in cursor.fetchall()]

    departments = sorted({d for d in [ctx.get('department_id')]
                          + list(ctx.get('headed_department_ids') or []) if d})
    cursor.execute(
        """
        SELECT sp.id
          FROM wiki_spaces sp
         WHERE sp.status = 'active'
           AND (NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                             WHERE sd.space_id = sp.id)
                OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                            WHERE sd.space_id = sp.id
                              AND sd.department_id = ANY(%(departments)s))
                -- Действующая именная выдача в этом пространстве. Раздел гостя
                -- ищем и напрямую, и через разделы выданной статьи: выдать
                -- можно и то и другое, а пространство у них общее.
                OR (%(with_guest)s AND EXISTS (
                    SELECT 1
                      FROM wiki_guest_access g
                      LEFT JOIN wiki_sections gs ON gs.id = g.section_id
                      LEFT JOIN wiki_article_sections gas ON gas.article_id = g.article_id
                      LEFT JOIN wiki_sections gass ON gass.id = gas.section_id
                     WHERE g.user_id = %(user_id)s
                       AND g.revoked_at IS NULL
                       AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                       AND COALESCE(gs.space_id, gass.space_id) = sp.id)))
         ORDER BY sp.position, sp.id
        """,
        {'departments': departments or [-1], 'user_id': ctx['user_id'],
         'with_guest': bool(include_guest)},
    )
    return [row[0] for row in cursor.fetchall()]


# Правила разделов, действующие на человека, — С УЧЁТОМ «вместе с подразделами».
#
# Тумблер в интерфейсе обещает дословно: «Те же права во всех вложенных
# разделах, включая созданные позже» (WikiSectionAccess.jsx). До 21.08.2026
# вглубь уходило только ЧТЕНИЕ: рекурсия по потомкам жила в _AUTO_SECTIONS_SQL,
# то есть в периметре, а права записи брались отдельным плоским запросом по
# точному section_id. Правка подразделов молча не работала — тот же класс
# отказа, что и в самом инциденте, только с другой стороны.
#
# Поэтому распространение считается ОДИН раз и здесь, а оба потребителя —
# способности и права на конкретном разделе — читают один результат.
_SECTION_RIGHTS_CTE = """
WITH RECURSIVE section_rights_all AS (
    SELECT r.section_id, r.grant_subsections AS deep, 0 AS depth,
           r.can_read, r.can_create, r.can_edit,
           r.can_delete, r.can_publish, r.can_approve
      FROM wiki_section_access_rules r
      JOIN wiki_sections s ON s.id = r.section_id AND s.status = 'active'
     WHERE (""" + SUBJECT_MATCH + """)
     UNION ALL
    SELECT child.id, parent.deep, parent.depth + 1,
           parent.can_read, parent.can_create, parent.can_edit,
           parent.can_delete, parent.can_publish, parent.can_approve
      FROM wiki_sections child
      JOIN section_rights_all parent ON child.parent_section_id = parent.section_id
     -- Ограничитель на случай битого дерева: сервер петель не допускает, но
     -- зациклиться здесь значит подвесить запрос (та же защита стоит в
     -- structure.section_branch_department).
     WHERE parent.deep AND child.status = 'active' AND parent.depth < 50
),
-- Граница пространства — последнее слово о том, что человек видит, и правило
-- её не отменяет (см. _SPACE_GATE_SQL). Стоит она здесь, а не у каждого
-- вызывающего: без неё забытое правило на раздел в закрытом от отдела
-- пространстве вечно поднимало бы способность — вкладки редактора открылись
-- бы, а править было бы нечего. Заодно закрывается adopt/fork
-- (routes_edit._target_section), который спрашивает правило по одному
-- section_id мимо allowed_section_ids.
section_rights AS (
    SELECT sr.section_id, sr.can_read, sr.can_create, sr.can_edit,
           sr.can_delete, sr.can_publish, sr.can_approve
      FROM section_rights_all sr
      JOIN wiki_sections s ON s.id = sr.section_id
     WHERE NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                        WHERE sd.space_id = s.space_id)
        OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                    WHERE sd.space_id = s.space_id
                      AND sd.department_id = ANY(%(departments)s))
)
"""


def section_rules_for_user(cursor, section_ids, subjects, user_id):
    """Правила разделов, действующие на пользователя, — для расчёта прав записи.

    Возвращает {section_id: [правило, ...]}. Одним запросом на все разделы:
    в оригинале матрица доступа делала по два запроса на каждую должность.

    Правило родителя с тумблером «вместе с подразделами» попадает сюда наравне
    с собственным правилом раздела — распространение считает общий
    _SECTION_RIGHTS_CTE, чтобы «вглубь» значило одно и то же и в периметре
    чтения, и в правах записи.
    """
    if not section_ids:
        return {}
    cursor.execute(
        _SECTION_RIGHTS_CTE + """
        SELECT section_id, can_read, can_create, can_edit,
               can_delete, can_publish, can_approve
          FROM section_rights
         WHERE section_id = ANY(%(sections)s)
        """,
        dict(subject_params(subjects, user_id), sections=list(section_ids)),
    )
    keys = ('can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve')
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row[0], []).append(dict(zip(keys, row[1:])))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Способности человека: должность ПЛЮС то, что ему уже выписали правилами
#
# Инцидент 21.08.2026 разобран в шапке access.capabilities_from_grants: право,
# выписанное правилом, гасло молча, потому что способность выводилась из одной
# лишь должности. Здесь — вторая половина починки: собрать выписанное.
#
# Одним запросом на оба вида правил. Разделы берём только активные: правило
# архивного раздела не должно поднимать способность — самого раздела уже нет.
# Правила статей входят сводкой, без разделов, и только mode='grant': запрет
# ничего не разрешает, а способность из него была бы прямым переворотом смысла.
# Границей пространства ветка статей намеренно не накрыта: статья лежит сразу в
# нескольких разделах, джойн вышел бы дороже смысла — способность всё равно
# бесполезна, пока сама статья вне периметра.
# ─────────────────────────────────────────────────────────────────────────────
_GRANTED_RIGHTS_SQL = _SECTION_RIGHTS_CTE + """
SELECT section_id, can_read, can_create, can_edit,
       can_delete, can_publish, can_approve
  FROM section_rights
 UNION ALL
SELECT NULL::int, r.can_read, r.can_create, r.can_edit,
       r.can_delete, r.can_publish, r.can_approve
  FROM wiki_article_access_rules r
 WHERE r.mode = 'grant'
   AND (""" + SUBJECT_MATCH + """)
"""


def granted_rule_rights(cursor, subjects, user_id):
    """Что человеку УЖЕ выписано правилами.

    Возвращает (permissions, publish_sections):
      permissions      — словарь PERMISSION_COLUMNS, «право выписано хоть где»;
      publish_sections — разделы, где выписано именно право публиковать.

    Второе значение нужно витрине: черновик — это то, что ещё предстоит
    выпустить, и не показать его тому, кому выпуск поручили, значит повторить
    ту же ловушку молчаливого отказа этажом выше (wiki/articles.py).
    """
    cursor.execute(_GRANTED_RIGHTS_SQL, subject_params(subjects, user_id))
    keys = ('can_read', 'can_create', 'can_edit', 'can_delete',
            'can_publish', 'can_approve')
    permissions = {name: False for name in keys}
    publish_sections = set()
    for row in cursor.fetchall():
        section_id, flags = row[0], dict(zip(keys, row[1:]))
        for name in keys:
            if flags[name]:
                permissions[name] = True
        if flags['can_publish'] and section_id is not None:
            publish_sections.add(int(section_id))
    return permissions, sorted(publish_sections)


def load_capabilities(cursor, ctx, subjects):
    """Проставить в ctx способности человека. Одна реализация на весь раздел.

    Кладёт три ключа и ничего не возвращает — вызывающему нужен ровно ctx:

      role_capabilities — только от должности и ролей вики. Ими гейтится то,
                          что живёт ВНЕ разделов: справочники «Парки» и
                          «Офисы», черновики и архив во всех витринах сразу.
                          Доступ, выписанный на ОДИН раздел, открывать
                          общекомпанейский справочник не должен;
      capabilities      — итоговые: роль ПЛЮС выписанное правилами. Ими гейтятся
                          роуты и считаются права на конкретном объекте;
      publish_sections  — разделы, где право публиковать пришло из правила.

    Второй запрос на HTTP-запрос — осознанная цена. Правил в разделе десятки, а
    не тысячи, запрос идёт по тем же индексам, что и периметр, и он дешевле
    любого способа узнать то же самое позже: без него каждый гейт считал бы
    выписанные права заново и они бы разошлись — ровно так эта вика уже
    ломалась (см. шапку wiki/articles.py).
    """
    role_capabilities = wiki_access.resolve_capabilities(
        ctx['otp_role'], ctx['wiki_roles'],
        is_department_head=bool(ctx['headed_department_ids']),
    )
    # В ручном режиме правила не действуют вовсе: периметр берётся из
    # wiki_user_manual_access (allowed_section_ids выбирает _MANUAL_SECTIONS_SQL).
    # Поднимать способность правилом, которое его же периметр игнорирует,
    # значит выдать право, которым негде воспользоваться.
    if ctx.get('access_mode') == 'manual':
        granted, publish_sections = {}, []
    else:
        granted, publish_sections = granted_rule_rights(cursor, subjects,
                                                        ctx['user_id'])
    ctx['role_capabilities'] = role_capabilities
    ctx['capabilities'] = wiki_access.merge_capabilities(
        role_capabilities, wiki_access.capabilities_from_grants(granted))
    ctx['publish_sections'] = publish_sections
    return ctx['capabilities']


def log_action(cursor, *, actor_id, action, entity_type=None, entity_id=None,
               target_user_id=None, details=None, ip_address=None, space_id=None):
    """Запись в журнал раздела.

    В оригинале журналов было два (security_audit_logs и access_audit_logs),
    почти одинаковых, и ни один не читался ни API, ни интерфейсом. Здесь один,
    и к нему сразу будет эндпоинт чтения.

    ПРОСТРАНСТВО записи проставляется здесь же, а не вызывающим: log_action
    зовут из сорока с лишним мест, и требовать от каждого назвать пространство
    значит завести правило, которое новое место забудет молча — запись просто
    оказалась бы в журнале всех пространств сразу. Считаем по объекту одной
    формулой (schema.AUDIT_SPACE_SQL) прямо в INSERT: второго рейса в базу это
    не стоит, а расходиться с разбором истории не может.

    space_id аргументом — для тех, кто пространство точно знает, а объекта ещё
    нет (импорт документа заводит статью позже, чем пишет о нём в журнал).
    """
    import json

    cursor.execute(
        """
        INSERT INTO wiki_audit_log (actor_id, action, entity_type, entity_id,
                                    target_user_id, details, ip_address, space_id)
        SELECT %(actor)s, %(action)s, %(etype)s, %(eid)s, %(target)s,
               %(details)s::jsonb, %(ip)s,
               COALESCE(
                   (SELECT s.id FROM wiki_spaces s WHERE s.id = %(space)s::int),
                   (""" + wiki_schema.audit_space_sql(
                       '%(etype)s', '%(eid)s', '%(details)s') + """))
        """,
        {'actor': actor_id, 'action': action, 'etype': entity_type,
         'eid': entity_id, 'target': target_user_id, 'ip': ip_address,
         'space': space_id,
         'details': json.dumps(details or {}, ensure_ascii=False)},
    )


def schema_is_ready(cursor):
    """Созданы ли таблицы раздела. Нужно эндпоинту /api/wiki/ping, чтобы
    отличить «раздел не разворачивался» от «раздел сломан».

    Намеренно не падает ни при каких обстоятельствах: это диагностика, и
    эндпоинт, который сам отдаёт 500, бесполезен ровно тогда, когда он нужен.
    """
    try:
        cursor.execute("SELECT to_regclass('public.wiki_articles') IS NOT NULL")
        row = cursor.fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def counters(cursor):
    """Счётчики для диагностики раздела."""
    cursor.execute(
        """
        SELECT (SELECT count(*) FROM wiki_spaces   WHERE status = 'active'),
               (SELECT count(*) FROM wiki_sections WHERE status = 'active'),
               (SELECT count(*) FROM wiki_articles WHERE status = 'published'),
               (SELECT count(*) FROM wiki_articles),
               (SELECT count(*) FROM wiki_roles),
               (SELECT count(*) FROM wiki_articles WHERE status = 'draft')
        """
    )
    row = cursor.fetchone() or (0, 0, 0, 0, 0, 0)
    spaces, sections, published, articles, roles, drafts = row
    return {
        'spaces': spaces,
        'sections': sections,
        'articles_published': published,
        'articles_total': articles,
        'roles': roles,
        # Черновики — отдельным числом, а не «всего минус опубликованные»:
        # в разности сидит ещё и архив, а на витрине она подписана «Черновиков».
        'articles_draft': drafts,
    }
