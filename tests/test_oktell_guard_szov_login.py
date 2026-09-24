"""Логин Oktell ищется только среди сотрудников СЗоВ — кабинет, без него sip_number.

Решение владельца 24.09.2026: «не перемешивай SIP-номера с другими разделами,
только раздел СЗоВ — чтобы для Oktell подтягивалось только с него». До правки
агент с кабинетным логином оператора СЗоВ приписывался уволенной сотруднице ОП,
у которой в users.sip_number то же число — номер её АТС, а оператор с кабинетом
и пустым sip_number не узнавался вовсе. Люди и логины в тестах выдуманы.
"""

import inspect

from oktell_guard import patrol, queries


class ScriptedCursor:
    """Отдаёт заранее заданные строки и запоминает запрос с параметрами."""

    def __init__(self, rows, columns):
        self._rows = rows
        self.description = [(name,) for name in columns]
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


_RULE_COLUMNS = ('user_id', 'name', 'threshold_s', 'enabled')


def test_the_login_rule_prefers_the_cabinet_and_falls_back_to_sip():
    sql = queries.OKTELL_LOGIN_SQL
    assert sql.index('oka.cabinet_login') < sql.index('u.sip_number')
    assert queries.OKTELL_DEPARTMENT_CODE == 'szov'


def test_personal_rule_looks_only_inside_szov_by_the_login_rule():
    cursor = ScriptedCursor([(9001, 'Оператор А', None, True)], _RULE_COLUMNS)
    found = queries.personal_rule_by_sip(cursor, ' 7901 ')
    assert found['user_id'] == 9001
    sql, params = cursor.calls[0]
    assert 'd.code = %(oktell_department)s' in sql
    assert queries.OKTELL_LOGIN_SQL + ' = %(sip)s' in sql
    assert 'LEFT JOIN oktell_user_accounts oka' in sql
    assert params['sip'] == '7901'
    assert params['oktell_department'] == 'szov'


def test_a_login_held_by_two_people_belongs_to_nobody():
    """Как в серверной сверке: машину и выбросы наугад не приписываем."""
    cursor = ScriptedCursor([(9001, 'Оператор А', None, True), (9002, 'Оператор Б', None, True)],
                            _RULE_COLUMNS)
    assert queries.personal_rule_by_sip(cursor, '7902') is None
    assert 'LIMIT 2' in cursor.calls[0][0]


def test_an_empty_login_does_not_touch_the_database():
    cursor = ScriptedCursor([], _RULE_COLUMNS)
    assert queries.personal_rule_by_sip(cursor, '  ') is None
    assert cursor.calls == []


def test_the_patrol_maps_oktell_history_to_szov_only():
    cursor = ScriptedCursor([('7901', 9001, 180, True)],
                            ('sip_number', 'user_id', 'threshold_s', 'enabled'))
    people, ambiguous = queries.thresholds_by_sip(cursor)
    assert people == {'7901': {'user_id': 9001, 'threshold_s': 180}}
    assert ambiguous == {}
    sql, params = cursor.calls[0]
    assert 'd.code = %(oktell_department)s' in sql
    assert queries.OKTELL_LOGIN_SQL + ' IS NOT NULL' in sql
    assert params['oktell_department'] == 'szov'
    # Отдел больше не параметр ни у сверки, ни у прогона: его «забывали» передать.
    assert list(inspect.signature(queries.thresholds_by_sip).parameters) == ['cursor']
    assert 'department_code' not in inspect.signature(patrol.run_patrol).parameters


def test_the_employee_list_and_the_reporter_show_the_same_login():
    """Вкладка «Сотрудники» и пометка «агент принадлежит…» читают тот же логин."""
    assert queries.OKTELL_LOGIN_SQL in queries._EMPLOYEES_SQL
    assert 'LEFT JOIN oktell_user_accounts oka' in queries._EMPLOYEES_SQL
    cursor = ScriptedCursor([(9003, 'Оператор В', '7903')], ('user_id', 'name', 'sip_number'))
    assert queries.user_brief(cursor, 9003)['sip_number'] == '7903'
    sql, params = cursor.calls[0]
    # Вне СЗоВ логина Oktell нет ни в пометке, ни во вкладке.
    for text in (sql, queries._EMPLOYEES_SQL):
        assert 'CASE WHEN d.code = %(oktell_department)s' in text
        assert queries.OKTELL_LOGIN_SQL in text
    assert params['oktell_department'] == 'szov'
