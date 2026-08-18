"""Дерево «Коммерческого отдела» и права на него.

Структура, которую собирает этот модуль:

    Коммерческий отдел (пространство)
    ├── Коммерческий директор     видит только уровень 50
    ├── Руководитель группы       видит глава отдела (ОП или ОТП)
    ├── Супервайзер               свой отдел, не ниже уровня 30
    ├── Оператор                  узел-родитель, вглубь НЕ раздаёт
    │   ├── ОП                    отдел «Отдел продаж»
    │   └── ОТП                   отдел «СЗоВ»
    └── Общий сотрудник           публичный, виден всем без правил

Почему «Оператор» раздаёт права НЕ вглубь. Правило с grant_subsections=TRUE
открывает всё поддерево, и тогда оператор продаж вместе со своей веткой получил
бы ветку ОТП. Поэтому на самом «Операторе» правило узкое (только сам раздел,
чтобы ветка не висела в дереве обрубком), а отделы гейтятся каждый своим.

Почему «видит своё и всё, что ниже» получается само. Уровень в правиле — это
порог «не ниже», а не «ровно»: раздел супервайзера требует 30, операторские
ветки не требуют ничего, поэтому супервайзер (30) проходит в оба, а глава
отдела (40) — ещё и туда, куда пускают по department. Отдельных правил
«и всё, что ниже» выписывать не нужно.

Генератор идемпотентен: повторный прогон ничего не дублирует и не сбрасывает
правки, сделанные руками (кроме тех полей, которыми управляет сам). Ничего не
удаляет — снятая ветка уходит в архив.
"""

from .access import ROLE_LEVELS

# Код пространства. По нему генератор находит своё дерево при повторных
# прогонах — имя пространства владелец волен переименовать.
SPACE_CODE = 'commercial'
SPACE_NAME = 'Коммерческий отдел'

# Разделы верхнего уровня. slug уникален в пределах пространства.
SECTION_DIRECTOR = ('commercial-director', 'Коммерческий директор')
SECTION_HEAD = ('group-head', 'Руководитель группы')
SECTION_SUPERVISOR = ('supervisor', 'Супервайзер')
SECTION_OPERATOR = ('operator', 'Оператор')
SECTION_COMMON = ('common-employee', 'Общий сотрудник')

# Ветки отделов внутри «Оператора»: код отдела в OTP → как называется в вики.
# Код, а не id: id отдела на другой базе (или после пересоздания) другой, а
# departments.code стабилен и уже используется как ключ в departmentViews.js.
OPERATOR_BRANCHES = (
    ('op', 'ОП'),
    ('szov', 'ОТП'),
)

_READ_ONLY = {'can_read': True}


def _department_ids(cursor, codes):
    """id отделов по кодам. Отсутствующий код молча пропускаем.

    Молча — потому что генератор обязан отработать и на базе, где отдела ещё
    нет: он создаст всё остальное, а ветку добавит следующий прогон.
    """
    cursor.execute(
        "SELECT code, id FROM departments WHERE code = ANY(%s) AND is_active",
        (list(codes),),
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def _ensure_space(cursor, created_by):
    cursor.execute("SELECT id FROM wiki_spaces WHERE code = %s", (SPACE_CODE,))
    row = cursor.fetchone()
    if row:
        # status трогаем: пространство могли увести в архив, а генератор его
        # возвращает — это и есть «пересобрать структуру».
        cursor.execute(
            "UPDATE wiki_spaces SET status = 'active', updated_at = "
            "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = %s",
            (row[0],),
        )
        return row[0], False

    cursor.execute(
        """
        INSERT INTO wiki_spaces (code, name, description, icon, status, position, created_by)
        VALUES (%s, %s, %s, 'fa-briefcase', 'active',
                COALESCE((SELECT max(position) + 1 FROM wiki_spaces), 0), %s)
        RETURNING id
        """,
        (SPACE_CODE, SPACE_NAME, 'Знания коммерческого отдела: ОП и ОТП', created_by),
    )
    return cursor.fetchone()[0], True


def _ensure_section(cursor, *, space_id, slug, name, parent_id=None,
                    department_id=None, section_kind='common',
                    visibility_scope='restricted', created_by=None):
    """Создаёт раздел или возвращает существующий по (пространство, slug).

    Ключ — slug, а не имя: имя владелец переименовывает из интерфейса, и поиск
    по имени на втором прогоне создал бы дубль. Ровно так уже случалось при
    переносе контента (см. cleanup_wiki_dupes.py).
    """
    cursor.execute(
        "SELECT id FROM wiki_sections WHERE space_id = %s AND slug = %s",
        (space_id, slug),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            """
            UPDATE wiki_sections
               SET parent_section_id = %(parent)s,
                   department_id     = %(dept)s,
                   section_kind      = %(kind)s,
                   visibility_scope  = %(scope)s,
                   status            = 'active',
                   updated_at        = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = %(id)s
            """,
            {'id': row[0], 'parent': parent_id, 'dept': department_id,
             'kind': section_kind, 'scope': visibility_scope},
        )
        return row[0], False

    cursor.execute(
        """
        INSERT INTO wiki_sections (space_id, parent_section_id, name, slug,
                                   department_id, section_kind, visibility_scope,
                                   position, created_by)
        VALUES (%(space)s, %(parent)s, %(name)s, %(slug)s, %(dept)s, %(kind)s,
                %(scope)s,
                COALESCE((SELECT max(position) + 1 FROM wiki_sections
                           WHERE space_id = %(space)s), 0),
                %(by)s)
        RETURNING id
        """,
        {'space': space_id, 'parent': parent_id, 'name': name, 'slug': slug,
         'dept': department_id, 'kind': section_kind, 'scope': visibility_scope,
         'by': created_by},
    )
    return cursor.fetchone()[0], True


def _ensure_rule(cursor, *, section_id, subject_type, subject_id=None,
                 subject_role=None, permissions=None, grant_subsections=True,
                 min_role_level=None, created_by=None):
    """Правило доступа. ON CONFLICT обязателен — второй прогон иначе падает.

    Уникальность в базе — по (раздел, тип субъекта, id, роль), и генератор
    гоняется повторно при каждом изменении структуры.
    """
    from .structure import upsert_section_rule
    return upsert_section_rule(
        cursor,
        section_id=section_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_role=subject_role,
        permissions=dict(_READ_ONLY, **(permissions or {})),
        grant_subsections=grant_subsections,
        min_role_level=min_role_level,
        created_by=created_by,
    )


def ensure_commercial_structure(cursor, *, created_by=None):
    """Собирает дерево и правила. Идемпотентно. Возвращает отчёт о сделанном."""
    report = {'space': None, 'sections_created': [], 'sections_kept': [],
              'rules': 0, 'skipped_departments': []}

    space_id, space_created = _ensure_space(cursor, created_by)
    report['space'] = {'id': space_id, 'created': space_created}

    departments = _department_ids(cursor, [code for code, _ in OPERATOR_BRANCHES])

    def section(slug_name, **kwargs):
        slug, name = slug_name
        section_id, created = _ensure_section(
            cursor, space_id=space_id, slug=slug, name=name,
            created_by=created_by, **kwargs)
        (report['sections_created'] if created else report['sections_kept']).append(name)
        return section_id

    def rule(**kwargs):
        _ensure_rule(cursor, created_by=created_by, **kwargs)
        report['rules'] += 1

    # ── Коммерческий директор: только по уровню, отдел роли не играет ────────
    director_id = section(SECTION_DIRECTOR)
    rule(section_id=director_id, subject_type='otp_role',
         subject_role='super_admin', min_role_level=ROLE_LEVELS['super_admin'],
         permissions={'can_create': True, 'can_edit': True, 'can_delete': True,
                      'can_publish': True, 'can_approve': True})

    # ── Руководитель группы: адресуемся НАЗНАЧЕНИЮ, а не человеку ────────────
    # Правило переезжает вместе со сменой главы; на проде глава «Отдела продаж»
    # уже менялся (июнь 2026), и персональное правило пришлось бы переставлять.
    head_id = section(SECTION_HEAD)
    for code, _title in OPERATOR_BRANCHES:
        department_id = departments.get(code)
        if not department_id:
            report['skipped_departments'].append(code)
            continue
        rule(section_id=head_id, subject_type='department_head',
             subject_id=department_id,
             permissions={'can_create': True, 'can_edit': True, 'can_delete': True,
                          'can_publish': True, 'can_approve': True})

    # ── Супервайзер: свой отдел И не ниже уровня СВ ──────────────────────────
    supervisor_id = section(SECTION_SUPERVISOR)
    for code, _title in OPERATOR_BRANCHES:
        department_id = departments.get(code)
        if not department_id:
            continue
        rule(section_id=supervisor_id, subject_type='department',
             subject_id=department_id, min_role_level=ROLE_LEVELS['sv'],
             permissions={'can_create': True, 'can_edit': True})

    # ── Оператор: узел-родитель. Правило узкое — вглубь НЕ раздаёт ───────────
    operator_id = section(SECTION_OPERATOR)
    for code, title in OPERATOR_BRANCHES:
        department_id = departments.get(code)
        if not department_id:
            continue
        # grant_subsections=False — иначе ветка соседнего отдела открылась бы
        # вместе с родителем, и всё разделение потеряло бы смысл.
        rule(section_id=operator_id, subject_type='department',
             subject_id=department_id, grant_subsections=False)

        branch_id = section((code, title), parent_id=operator_id,
                            department_id=department_id, section_kind='department')
        rule(section_id=branch_id, subject_type='department',
             subject_id=department_id)
        # Супервайзер своего отдела правит операторские статьи, оператор — нет.
        rule(section_id=branch_id, subject_type='department',
             subject_id=department_id, min_role_level=ROLE_LEVELS['sv'],
             permissions={'can_create': True, 'can_edit': True})

    # ── Общий сотрудник: публичный, виден всем без единого правила ───────────
    # visibility_scope='public' — это и есть «отображается у всех»: периметр
    # добавляет такие разделы отдельным UNION, минуя правила.
    section(SECTION_COMMON, visibility_scope='public')

    return report
