# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: BAIGA_TEST_PORT — порт локального Postgres.

Выдачи раздела «Списки Байги» (кнопка «Доступ», решение владельца 07.10.2026):
человеку, группе, отделу — и правки строк «открыт по умолчанию» (08.10.2026).
tests/test_baiga.py сверяет правило и ручки на
подменённом слое запросов и видит только, что запрос НАПИСАН. Что он ЗНАЧИТ —
под какие выдачи подпадает человек, кто считается в группе и в штате, как
повторная выдача меняет уровень — исполняется здесь, на настоящей схеме раздела
(baiga.schema) и маленьком наборе людей.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск на локальном кластере
(пароль, если он есть, — через PGPASSWORD):

    BAIGA_TEST_PORT=55433 python -m pytest tests/test_baiga_access_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from baiga import access, queries, schema

PORT = os.environ.get('BAIGA_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (BAIGA_TEST_PORT)')

SZOV, OP, MARKETING, TEZ, CLOSED = 1, 2, 3, 4, 5
GROUP, ARCHIVED_GROUP, EMPTY_GROUP = 100, 101, 102
# Тез КЦ: в группе, вне группы, СВ группы, ушедший из группы, придёт завтра,
# уволенный, из архивной группы, в отпуске.
IN_GROUP, NO_GROUP, GROUP_SV, LEFT_GROUP, JOINS_TOMORROW, FIRED, OLD_GROUP, ON_LEAVE = 11, 12, 13, 14, 15, 16, 17, 60
SZOV_OPERATOR, MARKETING_HEAD, TEZ_HEAD, HR = 10, 30, 40, 50
ACTOR = {'user_id': MARKETING_HEAD, 'name': 'Маркетова Мадина'}

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, city text);
    CREATE TABLE groups (id integer PRIMARY KEY, name text NOT NULL, department_id integer,
                         status text NOT NULL DEFAULT 'active');
    CREATE TABLE group_operator_memberships (group_id integer NOT NULL, operator_id integer NOT NULL,
                                             start_date date NOT NULL, end_date date);
    CREATE TABLE group_supervisor_memberships (group_id integer NOT NULL, supervisor_id integer NOT NULL,
                                               start_date date NOT NULL, end_date date);
    INSERT INTO departments (id, code, name, head_user_id, is_active) VALUES
        (1, 'SZOV', 'СЗоВ', NULL, TRUE), (2, 'op', 'Отдел продаж', NULL, TRUE),
        (3, 'marketing', 'Маркетинг', 30, TRUE), (4, 'tez', 'Тез КЦ', 40, TRUE),
        (5, 'old', 'Закрытый отдел', NULL, FALSE);
    INSERT INTO users (id, name, role, status, department_id) VALUES
        (10, 'Сзовов Саян', 'operator', 'working', 1),
        (11, 'Группов Глеб', 'operator', 'working', 4),
        (12, 'Одинов Олжас', 'operator', 'working', 4),
        (13, 'Старшов Санжар', 'sv', 'working', 4),
        (14, 'Ушедшев Улан', 'operator', 'working', 4),
        (15, 'Завтрашнев Заур', 'operator', 'working', 4),
        (16, 'Бывшев Борис', 'operator', 'fired', 4),
        (17, 'Архивов Арман', 'operator', 'working', 4),
        (60, 'Отпусков Олег', 'operator', 'annual_leave', 4),
        (30, 'Маркетова Мадина', 'admin', 'working', 3),
        (40, 'Главнов Гани', 'admin', 'working', 1),
        (50, 'Кадрова Камила', ' HR_Manager ', 'working', NULL);
    INSERT INTO groups (id, name, department_id, status) VALUES
        (100, 'Линия', 4, 'active'), (101, 'Старая линия', 4, 'archived'), (102, 'Пустая', 1, 'active');
    INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) VALUES
        (100, 11, CURRENT_DATE - 30, NULL),
        (100, 14, CURRENT_DATE - 30, CURRENT_DATE - 1),
        (100, 15, CURRENT_DATE + 1, NULL),
        (100, 16, CURRENT_DATE - 30, NULL),
        (100, 60, CURRENT_DATE - 30, CURRENT_DATE),
        (101, 17, CURRENT_DATE - 30, NULL);
    INSERT INTO group_supervisor_memberships (group_id, supervisor_id, start_date, end_date) VALUES
        (100, 13, CURRENT_DATE - 30, NULL);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@pytest.fixture
def db(monkeypatch):
    name = 't_baiga_access_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    # Одной транзакцией, как в Database._init_db: схема раздела ставит свой
    # SAVEPOINT (триграммный индекс), а вне транзакции их не бывает. Сорвалась
    # подготовка — транзакция откатывается, и схемы в кластере не остаётся.
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_baiga_schema(cur)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    # Схема теста — не public: «таблицы выдач и правок круга есть» отвечаем сами.
    monkeypatch.setattr(schema, 'grants_ready', lambda cursor: True)
    monkeypatch.setattr(schema, 'circle_ready', lambda cursor: True)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def grant(db, kind, ident, level='read'):
    with db.cursor() as cur:
        cur.execute("INSERT INTO baiga_access_grants (subject_type, subject_id, level) VALUES (%s, %s, %s)",
                    (kind, ident, level))


def levels(db, user_id):
    with db.cursor() as cur:
        ctx = queries.load_access_context(cur, user_id)
    return sorted(ctx['grant_levels'])


def rows(db, sql):
    with db.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_context_is_the_profile_and_headed_departments(db):
    with db.cursor() as cur:
        operator = queries.load_access_context(cur, str(SZOV_OPERATOR))
        head = queries.load_access_context(cur, TEZ_HEAD)
        hr = queries.load_access_context(cur, HR)
        nobody = queries.load_access_context(cur, 999)
    assert operator == {
        'user_id': SZOV_OPERATOR, 'name': 'Сзовов Саян', 'role': 'operator', 'department_id': SZOV,
        'department_code': 'SZOV', 'city': None, 'headed_department_ids': [], 'headed_department_codes': [],
        'grant_levels': [], 'circle_levels': {},
    }
    # Глава Тез КЦ числится в СЗоВ: свой отдел — один, возглавляемый — другой.
    assert (head['department_id'], head['headed_department_ids'], head['headed_department_codes']) == (
        SZOV, [TEZ], ['tez'])
    # Должность — как в карточке, только без регистра и пробелов.
    assert (hr['role'], hr['department_id'], hr['department_code']) == ('hr_manager', None, None)
    assert nobody is None
    # Без выдач — прежний круг: оператор СЗоВ читает после QR, кадровик закрыт.
    assert access.capabilities(operator)['can_open'] is True
    assert access.requires_sensitive_qr(operator) is True
    assert access.capabilities(hr)['can_open'] is False


def test_grant_to_a_person_reaches_only_that_person(db):
    grant(db, 'user', NO_GROUP, 'export')
    assert levels(db, NO_GROUP) == ['export']
    for other in (IN_GROUP, GROUP_SV, SZOV_OPERATOR, TEZ_HEAD, HR):
        assert levels(db, other) == [], other
    # Номер человека — не номер группы и не номер отдела.
    grant(db, 'group', NO_GROUP, 'full')
    grant(db, 'department', NO_GROUP, 'full')
    assert levels(db, NO_GROUP) == ['export']


def test_grant_to_a_group_reaches_its_current_members(db):
    grant(db, 'group', GROUP, 'read')
    # Оператор и супервайзер группы; членство, кончающееся сегодня, ещё действует.
    for member in (IN_GROUP, GROUP_SV, ON_LEAVE):
        assert levels(db, member) == ['read'], member
    # Вне группы, ушёл вчера, придёт завтра — нет. Отдел тот же, но выдано группе.
    for outsider in (NO_GROUP, LEFT_GROUP, JOINS_TOMORROW, OLD_GROUP, TEZ_HEAD, SZOV_OPERATOR):
        assert levels(db, outsider) == [], outsider


def test_archived_group_opens_nothing(db):
    grant(db, 'group', ARCHIVED_GROUP, 'full')
    assert levels(db, OLD_GROUP) == []
    with db.cursor() as cur:
        cur.execute("UPDATE groups SET status = 'active' WHERE id = %s", (ARCHIVED_GROUP,))
    assert levels(db, OLD_GROUP) == ['full']


def test_grant_to_a_department_reaches_its_people_and_its_head(db):
    grant(db, 'department', TEZ, 'export')
    for person in (IN_GROUP, NO_GROUP, GROUP_SV, LEFT_GROUP, OLD_GROUP, ON_LEAVE):
        assert levels(db, person) == ['export'], person
    # Глава Тез КЦ числится в СЗоВ — в выданный отдел он входит главенством.
    assert levels(db, TEZ_HEAD) == ['export']
    for outsider in (SZOV_OPERATOR, MARKETING_HEAD, HR):
        assert levels(db, outsider) == [], outsider
    # Сняли с отдела главу — выдача отдела до него больше не доходит.
    with db.cursor() as cur:
        cur.execute("UPDATE departments SET head_user_id = NULL WHERE id = %s", (TEZ,))
    assert levels(db, TEZ_HEAD) == []


def test_closed_department_opens_nothing_to_its_people_or_its_head(db):
    """Выдача закрытому отделу ничего не открывает — как архивной группе: лист
    доступа подписывает её «отдел закрыт», и раздел обязан отвечать так же."""
    grant(db, 'department', TEZ, 'full')
    with db.cursor() as cur:
        cur.execute("UPDATE departments SET is_active = FALSE WHERE id = %s", (TEZ,))
    for person in (IN_GROUP, NO_GROUP, GROUP_SV, TEZ_HEAD):
        assert levels(db, person) == [], person
    with db.cursor() as cur:
        ctx = queries.load_access_context(cur, TEZ_HEAD)
        listed = queries.list_grants(cur)
    # И главой закрытого отдела человек больше не считается.
    assert ctx['headed_department_ids'] == []
    assert [(row['subject_id'], row['active']) for row in listed] == [(TEZ, False)]
    with db.cursor() as cur:
        cur.execute("UPDATE departments SET is_active = TRUE WHERE id = %s", (TEZ,))
    assert levels(db, IN_GROUP) == ['full']
    assert levels(db, TEZ_HEAD) == ['full']


def test_every_matching_grant_is_seen_and_the_strongest_wins(db):
    grant(db, 'user', IN_GROUP, 'read')
    grant(db, 'group', GROUP, 'full')
    grant(db, 'department', TEZ, 'export')
    assert levels(db, IN_GROUP) == ['export', 'full', 'read']
    with db.cursor() as cur:
        ctx = queries.load_access_context(cur, IN_GROUP)
    assert access.level_of(ctx) == 'full'
    # Сосед по отделу вне группы — только выдача отделу.
    assert levels(db, NO_GROUP) == ['export']


def test_grants_are_written_in_one_batch_and_repeat_changes_the_level(db):
    with db.cursor() as cur:
        queries.lock_access(cur)
        first = queries.write_grants(cur, [('user', IN_GROUP, 'Группов Глеб'), ('group', GROUP, 'Линия')],
                                     'read', ACTOR)
    assert first == (2, 0)
    with db.cursor() as cur:
        second = queries.write_grants(cur, [('user', IN_GROUP, 'Группов Глеб'), ('department', TEZ, 'Тез КЦ')],
                                      'full', {'user_id': SZOV_OPERATOR, 'name': 'Другой'})
    assert second == (1, 1)
    with db.cursor() as cur:
        third = queries.write_grants(cur, [('user', IN_GROUP, 'Группов Глеб'), ('department', TEZ, 'Тез КЦ')],
                                     'full', ACTOR)
    assert third == (0, 0)
    assert rows(db, "SELECT subject_type, subject_id, level, granted_by, granted_by_name "
                    "FROM baiga_access_grants ORDER BY subject_type, subject_id") == [
        ('department', TEZ, 'full', SZOV_OPERATOR, 'Другой'),
        ('group', GROUP, 'read', MARKETING_HEAD, 'Маркетова Мадина'),
        # Уровень сменил «Другой» — он и записан; третий заход ничего не тронул.
        ('user', IN_GROUP, 'full', SZOV_OPERATOR, 'Другой'),
    ]
    assert rows(db, "SELECT action, subject_type, subject_id, subject_label, level_before, level_after, "
                    "actor_user_id FROM baiga_access_log ORDER BY id") == [
        ('grant', 'user', IN_GROUP, 'Группов Глеб', None, 'read', MARKETING_HEAD),
        ('grant', 'group', GROUP, 'Линия', None, 'read', MARKETING_HEAD),
        ('grant', 'department', TEZ, 'Тез КЦ', None, 'full', SZOV_OPERATOR),
        ('change', 'user', IN_GROUP, 'Группов Глеб', 'read', 'full', SZOV_OPERATOR),
    ]


def test_one_subject_cannot_have_two_grants(db):
    import psycopg2
    grant(db, 'group', GROUP, 'read')
    with pytest.raises(psycopg2.errors.UniqueViolation):
        grant(db, 'group', GROUP, 'full')
    # Человек и группа с одним номером — разные адресаты.
    grant(db, 'user', GROUP, 'full')


def test_level_is_changed_and_grant_is_revoked_with_a_trace(db):
    grant(db, 'group', GROUP, 'read')
    with db.cursor() as cur:
        found = queries.get_grant(cur, rows(db, "SELECT id FROM baiga_access_grants")[0][0], lock=True)
        assert (found['subject_type'], found['subject_id'], found['level'], found['label']) == (
            'group', GROUP, 'read', 'Линия')
        queries.set_grant_level(cur, found, 'export', ACTOR)
    assert levels(db, IN_GROUP) == ['export']
    with db.cursor() as cur:
        found = queries.get_grant(cur, found['id'], lock=True)
        queries.delete_grant(cur, found, ACTOR)
        assert queries.get_grant(cur, found['id']) is None
    assert levels(db, IN_GROUP) == []
    assert rows(db, "SELECT action, subject_label, level_before, level_after, actor_user_id "
                    "FROM baiga_access_log ORDER BY id") == [
        ('change', 'Линия', 'read', 'export', MARKETING_HEAD),
        ('revoke', 'Линия', 'export', None, MARKETING_HEAD),
    ]


def test_sheet_lists_grants_with_labels_people_and_dead_subjects(db):
    for kind, ident in (('user', FIRED), ('group', 999), ('group', ARCHIVED_GROUP), ('department', CLOSED),
                        ('group', GROUP), ('department', TEZ), ('user', IN_GROUP)):
        grant(db, kind, ident)
    with db.cursor() as cur:
        listed = queries.list_grants(cur)
    summary = [(row['subject_type'], row['subject_id'], row['label'], row['detail'], row['role'], row['people'],
                row['active']) for row in listed]
    assert summary == [
        # Отделы, группы, люди; внутри — по названию; удалённый адресат — последним.
        ('department', CLOSED, 'Закрытый отдел', None, None, 0, False),
        # В штате Тез КЦ семеро: уволенный не в счёт, человек в отпуске — в счёт.
        ('department', TEZ, 'Тез КЦ', None, None, 7, True),
        # В группе трое: оператор, супервайзер и человек в отпуске; ушедший,
        # будущий и уволенный — нет.
        ('group', GROUP, 'Линия', 'Тез КЦ', None, 3, True),
        ('group', ARCHIVED_GROUP, 'Старая линия', 'Тез КЦ', None, 1, False),
        ('group', 999, None, None, None, 0, False),
        ('user', FIRED, 'Бывшев Борис', 'Тез КЦ', 'operator', None, False),
        ('user', IN_GROUP, 'Группов Глеб', 'Тез КЦ', 'operator', None, True),
    ]
    assert all(row['granted_at'] is not None for row in listed)


def test_catalog_offers_only_the_living(db):
    with db.cursor() as cur:
        catalog = queries.access_catalog(cur)
    assert [(item['id'], item['name'], item['people']) for item in catalog['department']] == [
        (MARKETING, 'Маркетинг', 1), (OP, 'Отдел продаж', 0), (SZOV, 'СЗоВ', 2), (TEZ, 'Тез КЦ', 7)]
    assert [(item['id'], item['name'], item['detail'], item['people']) for item in catalog['group']] == [
        (GROUP, 'Линия', 'Тез КЦ', 3), (EMPTY_GROUP, 'Пустая', 'СЗоВ', 0)]
    people = {item['id']: item for item in catalog['user']}
    assert FIRED not in people
    assert sorted(people) == sorted([SZOV_OPERATOR, IN_GROUP, NO_GROUP, GROUP_SV, LEFT_GROUP, JOINS_TOMORROW,
                                     OLD_GROUP, ON_LEAVE, MARKETING_HEAD, TEZ_HEAD, HR])
    assert (people[GROUP_SV]['role'], people[GROUP_SV]['detail'], people[GROUP_SV]['people']) == ('sv', 'Тез КЦ', None)
    assert people[HR]['detail'] is None
    assert [item['name'] for item in catalog['user']] == sorted(item['name'] for item in catalog['user'])


def test_only_living_subjects_can_be_granted(db):
    asked = [('user', IN_GROUP), ('user', FIRED), ('user', 999), ('group', GROUP), ('group', ARCHIVED_GROUP),
             ('group', 999), ('department', TEZ), ('department', CLOSED), ('department', 999),
             # Номер живого человека как номер группы и отдела — такого адресата нет.
             ('group', IN_GROUP), ('department', IN_GROUP)]
    with db.cursor() as cur:
        found = queries.find_subjects(cur, asked)
        assert queries.find_subjects(cur, []) == {}
    assert found == {('user', IN_GROUP): 'Группов Глеб', ('group', GROUP): 'Линия', ('department', TEZ): 'Тез КЦ'}


def test_already_granted_levels_are_read_by_kind_and_number(db):
    grant(db, 'user', IN_GROUP, 'read')
    grant(db, 'group', GROUP, 'full')
    with db.cursor() as cur:
        assert queries.granted_levels(cur, []) == {}
        assert queries.granted_levels(cur, [('user', IN_GROUP), ('group', GROUP), ('department', TEZ),
                                            ('group', IN_GROUP)]) == {
            ('user', IN_GROUP): 'read', ('group', GROUP): 'full'}


def context(db, user_id):
    with db.cursor() as cur:
        return queries.load_access_context(cur, user_id)


def test_circle_edit_reaches_the_people_of_the_row(db):
    """Правка строки «открыт по умолчанию» доходит до контекста каждого — тем же
    запросом, что профиль и выдачи, — и меняет ответ правила тем, кто под
    строку подпадает."""
    assert access.capabilities(context(db, SZOV_OPERATOR))['can_open'] is True
    with db.cursor() as cur:
        queries.lock_access(cur)
        assert queries.circle_edits(cur) == {}
        queries.set_circle_level(cur, 'staff:szov', 'read', 'none', ACTOR)
    operator = context(db, SZOV_OPERATOR)
    assert operator['circle_levels'] == {'staff:szov': 'none'}
    assert access.capabilities(operator) == {'can_open': False, 'can_export': False, 'can_manage': False,
                                             'can_manage_access': False}
    # Глава «Маркетинга» видит ту же таблицу правок, но его строка не тронута.
    head = context(db, MARKETING_HEAD)
    assert head['circle_levels'] == {'staff:szov': 'none'}
    assert access.level_of(head) == 'full'
    # Подняли строку до выгрузки — оператор СЗоВ выгружает.
    with db.cursor() as cur:
        queries.set_circle_level(cur, 'staff:szov', 'none', 'export', ACTOR)
    assert access.capabilities(context(db, SZOV_OPERATOR))['can_export'] is True
    # Закрытая строка и выдача складываются: строку закрыли, выданное осталось.
    with db.cursor() as cur:
        queries.set_circle_level(cur, 'staff:szov', 'export', 'none', ACTOR)
    grant(db, 'user', SZOV_OPERATOR, 'read')
    operator = context(db, SZOV_OPERATOR)
    assert (operator['grant_levels'], operator['circle_levels']) == (['read'], {'staff:szov': 'none'})
    assert access.level_of(operator) == 'read'


def test_circle_edit_is_one_row_per_slot_with_the_author_and_a_trace(db):
    other = {'user_id': SZOV_OPERATOR, 'name': 'Другой'}
    with db.cursor() as cur:
        queries.set_circle_level(cur, 'staff:op', 'read', 'none', ACTOR)
        queries.set_circle_level(cur, 'head:szov', 'read', 'full', ACTOR)
    first = rows(db, "SELECT updated_at FROM baiga_access_circle WHERE slot = 'staff:op'")[0][0]
    with db.cursor() as cur:
        queries.set_circle_level(cur, 'staff:op', 'none', 'export', other)
        edits = queries.circle_edits(cur)
    # Повторная правка переписала строку: уровень, автор, время — второй строки нет.
    assert rows(db, "SELECT slot, level, updated_by, updated_by_name FROM baiga_access_circle ORDER BY slot") == [
        ('head:szov', 'full', MARKETING_HEAD, 'Маркетова Мадина'),
        ('staff:op', 'export', SZOV_OPERATOR, 'Другой'),
    ]
    assert {slot: (edit['level'], edit['updated_by_name']) for slot, edit in edits.items()} == {
        'head:szov': ('full', 'Маркетова Мадина'), 'staff:op': ('export', 'Другой')}
    assert edits['staff:op']['updated_at'] >= first
    # Время — настенные часы Алматы, как у остальных таблиц раздела.
    drift = rows(db, "SELECT abs(extract(epoch FROM (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - updated_at)) "
                     "FROM baiga_access_circle WHERE slot = 'staff:op'")[0][0]
    assert drift < 60
    assert rows(db, "SELECT action, subject_type, subject_id, subject_label, level_before, level_after, "
                    "actor_user_id, actor_name FROM baiga_access_log ORDER BY id") == [
        ('circle', 'circle', 0, 'staff:op', 'read', 'none', MARKETING_HEAD, 'Маркетова Мадина'),
        ('circle', 'circle', 0, 'head:szov', 'read', 'full', MARKETING_HEAD, 'Маркетова Мадина'),
        ('circle', 'circle', 0, 'staff:op', 'none', 'export', SZOV_OPERATOR, 'Другой'),
    ]


def test_one_circle_row_cannot_have_two_edits(db):
    import psycopg2
    with db.cursor() as cur:
        cur.execute("INSERT INTO baiga_access_circle (slot, level) VALUES ('staff:op', 'none')")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with db.cursor() as cur:
            cur.execute("INSERT INTO baiga_access_circle (slot, level) VALUES ('staff:op', 'full')")


def test_circle_edit_outlives_its_author(db):
    """Правившего удалили из базы — правка и её след остаются, подписаны его именем."""
    with db.cursor() as cur:
        queries.set_circle_level(cur, 'staff:op', 'read', 'none', ACTOR)
        cur.execute("DELETE FROM users WHERE id = %s", (MARKETING_HEAD,))
    assert rows(db, "SELECT slot, level, updated_by, updated_by_name FROM baiga_access_circle") == [
        ('staff:op', 'none', None, 'Маркетова Мадина')]
    assert rows(db, "SELECT action, subject_label, actor_user_id, actor_name FROM baiga_access_log") == [
        ('circle', 'staff:op', None, 'Маркетова Мадина')]
    assert context(db, SZOV_OPERATOR)['circle_levels'] == {'staff:op': 'none'}


def test_without_the_circle_table_the_context_is_the_default_circle(db, monkeypatch):
    """Таблица правок круга моложе выдач: не легла она — контекст собирается без
    неё (запрос её не упоминает), выдачи работают, круг — по умолчанию."""
    grant(db, 'user', NO_GROUP, 'export')
    with db.cursor() as cur:
        cur.execute("DROP TABLE baiga_access_circle")
    monkeypatch.setattr(schema, 'circle_ready', lambda cursor: False)
    ctx = context(db, NO_GROUP)
    assert (ctx['grant_levels'], ctx['circle_levels']) == (['export'], {})
    assert access.capabilities(context(db, SZOV_OPERATOR))['can_open'] is True
    # И без таблиц выдач — тоже: раздел живёт по кругу, как до кнопки «Доступ».
    with db.cursor() as cur:
        cur.execute("DROP TABLE baiga_access_grants")
        cur.execute("DROP TABLE baiga_access_log")
    monkeypatch.setattr(schema, 'grants_ready', lambda cursor: False)
    ctx = context(db, NO_GROUP)
    assert (ctx['grant_levels'], ctx['circle_levels']) == ([], {})
    assert access.capabilities(context(db, SZOV_OPERATOR))['can_open'] is True


def test_circle_names_ignore_code_case_and_the_fired(db):
    with db.cursor() as cur:
        departments, people = queries.circle_names(cur, ['marketing', 'op', 'szov', 'old', 'нет такого'],
                                                   [SZOV_OPERATOR, FIRED, 999])
        assert queries.circle_names(cur, [], []) == ({}, {})
    # Код в базе записан «SZOV»; закрытый отдел в круг не попадает.
    assert departments == {'marketing': 'Маркетинг', 'op': 'Отдел продаж', 'szov': 'СЗоВ'}
    assert people == {SZOV_OPERATOR: 'Сзовов Саян'}
