"""Пропуск агента: общий токен сборки и личный токен сотрудника.

Личные токены — не украшение: файл, скачанный из раздела, несёт в имени именно
личный токен и шлёт его на каждый запрос. Пока сервер знал только общий, любой
скачанный агент получал 401 и не работал вовсе. Проверяем обе двери.
"""

import hashlib

from oktell_guard import queries
from tests.test_oktell_guard_cursor_rows import TupleCursor


def test_personal_token_lookup_by_hash():
    """В базе хранится только отпечаток: самого токена у нас нет и быть не должно."""
    token = 'g196igjOR17Noukz04DNgmT'
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    cursor = TupleCursor(['id', 'user_id', 'name', 'sip_number'], [(3, 42, 'Карим', '6612')])
    found = queries.user_by_token(cursor, digest)
    assert found['user_id'] == 42
    # Отпечаток ушёл в запрос параметром, а не подстановкой в текст.
    sql, params = cursor.executed[0]
    assert params['token_hash'] == digest
    assert digest not in sql


def test_unknown_token_gives_nothing():
    cursor = TupleCursor(['id', 'user_id', 'name', 'sip_number'], [])
    assert queries.user_by_token(cursor, 'x' * 64) is None


def test_empty_token_is_not_looked_up():
    cursor = TupleCursor(['id'], [])
    assert queries.user_by_token(cursor, '') is None
    assert cursor.executed == []


def test_revoked_tokens_are_excluded_by_query():
    """Отзыв токена должен работать — условие обязано быть в самом запросе."""
    cursor = TupleCursor(['id', 'user_id', 'name', 'sip_number'], [(1, 1, 'Кто-то', '1')])
    queries.user_by_token(cursor, 'a' * 64)
    sql = cursor.executed[0][0]
    assert 'revoked_at IS NULL' in sql


def test_nobody_issues_personal_tokens_any_more():
    """С 1.0.17 человека называет вход по учётке iCORE, а не токен в имени
    файла. Выдачу убрали целиком: оставленная «на всякий случай» ручка завела бы
    второй способ опознания, который однажды разъедется с первым."""
    assert not hasattr(queries, 'issue_token')


def test_the_logged_in_operator_is_taken_by_id_with_the_sip():
    """SIP нужен для сверки «про свой ли номер прислан факт», а в db.get_user
    такой колонки нет — отсюда отдельный запрос."""
    cursor = TupleCursor(['user_id', 'name', 'sip_number'], [(42, 'Кто-то', '6612')])
    found = queries.user_brief(cursor, 42)
    assert found == {'user_id': 42, 'name': 'Кто-то', 'sip_number': '6612'}
    assert queries.user_brief(cursor, None) is None
