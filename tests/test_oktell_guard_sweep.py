# -*- coding: utf-8 -*-
"""Серверная сверка «кто пересидел, а выброса нет».

Правило живёт в окне на машине человека, и мимо него есть законные пути: свой
браузер, закрытое окно, снятая программа, слепое правило (04.09.2026 — оператор
просидел 334 с при пороге 180, и ничего не произошло). Сверка смотрит не на
агентов, а на результат: история АТС против нашего журнала.
"""

from datetime import datetime, timedelta

import pytest

from oktell_guard import sweep


def row(login, state, icode, time_change, enumerator):
    return {'login': login, 'state': state, 'icode': icode,
            'time_change': time_change, 'enumerator': enumerator}


RECALL = (2, 2)
READY = (1, -1)
BUSY = (5, -1)
TRAINING = (2, 3)


# --------------------------------------------------------------------------- #
# Разбор истории в отрезки
# --------------------------------------------------------------------------- #

def test_segment_lasts_until_next_state_of_the_same_person():
    rows = [
        row('6684', *RECALL, '2026-09-04 15:26:14', 1),
        row('6684', *READY, '2026-09-04 15:31:48', 2),
    ]
    segments = sweep.recall_segments(rows)
    assert len(segments) == 1
    assert segments[0]['login'] == '6684'
    assert segments[0]['seconds'] == 334, 'ровно тот случай, из-за которого всё затевалось'


def test_open_segment_is_not_reported():
    """Человек сидит прямо сейчас — отрезок не закрыт. Отдавать его нельзя:
    каждый прогон записывал бы одно и то же сидение заново, всё длиннее."""
    rows = [row('6684', *RECALL, '2026-09-04 15:26:14', 1)]
    assert sweep.recall_segments(rows) == []


def test_other_break_reasons_are_not_recall():
    rows = [
        row('6684', *TRAINING, '2026-09-04 15:19:13', 1),
        row('6684', *READY, '2026-09-04 15:26:12', 2),
    ]
    assert sweep.recall_segments(rows) == []


def test_people_do_not_close_each_other_segments():
    """Строки идут вперемешку по всем операторам. Если закрывать отрезок
    следующей строкой ВООБЩЕ, а не следующей строкой того же человека,
    длительности превращаются в мусор."""
    rows = [
        row('6684', *RECALL, '2026-09-04 15:00:00', 1),
        row('6612', *READY, '2026-09-04 15:00:30', 2),
        row('6612', *BUSY, '2026-09-04 15:01:00', 3),
        row('6684', *READY, '2026-09-04 15:05:00', 4),
    ]
    segments = sweep.recall_segments(rows)
    assert len(segments) == 1
    assert segments[0]['seconds'] == 300


def test_repeated_sittings_are_separate_segments():
    rows = [
        row('6684', *RECALL, '2026-09-04 10:00:00', 1),
        row('6684', *READY, '2026-09-04 10:04:00', 2),
        row('6684', *RECALL, '2026-09-04 11:00:00', 3),
        row('6684', *READY, '2026-09-04 11:10:00', 4),
    ]
    segments = sweep.recall_segments(rows)
    assert [s['seconds'] for s in segments] == [240, 600]


def test_rows_out_of_order_are_sorted_by_enumerator():
    rows = [
        row('6684', *READY, '2026-09-04 15:31:48', 2),
        row('6684', *RECALL, '2026-09-04 15:26:14', 1),
    ]
    assert sweep.recall_segments(rows)[0]['seconds'] == 334


def test_broken_rows_are_skipped_not_fatal():
    rows = [
        {'login': '', 'state': 2, 'icode': 2, 'time_change': '2026-09-04 15:00:00', 'enumerator': 1},
        row('6684', *RECALL, 'не время', 2),
        row('6684', *RECALL, '2026-09-04 15:26:14', 3),
        row('6684', *READY, '2026-09-04 15:31:48', 4),
        'вообще не строка',
    ]
    segments = sweep.recall_segments(rows)
    assert len(segments) == 1


# --------------------------------------------------------------------------- #
# Порог и периметр
# --------------------------------------------------------------------------- #

def _segment(login='6684', start='2026-09-04 15:26:14', seconds=334):
    begin = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    return {'login': login, 'start': begin,
            'end': begin + timedelta(seconds=seconds), 'seconds': seconds}


def test_overdue_uses_personal_threshold():
    segments = [_segment(seconds=200)]
    assert sweep.overdue(segments, lambda login: 180)
    assert not sweep.overdue(segments, lambda login: 300)


def test_person_outside_the_rule_is_not_our_business():
    """None от threshold_for означает «правило его не касается»: чужой отдел,
    выключен лично, нет SIP-номера. Такие отрезки в отчёт попадать не должны."""
    assert sweep.overdue([_segment()], lambda login: None) == []
    assert sweep.overdue([_segment()], lambda login: 0) == []


def test_exactly_at_threshold_counts():
    assert sweep.overdue([_segment(seconds=180)], lambda login: 180)


# --------------------------------------------------------------------------- #
# Сопоставление с тем, что уже записала программа
# --------------------------------------------------------------------------- #

def test_violation_from_the_agent_closes_the_segment():
    """Программа сработала и записала выброс — сверка обязана промолчать,
    иначе один и тот же выброс попадёт в отчёт дважды."""
    segment = _segment()
    known = [{'sip_number': '6684', 'happened_at': datetime(2026, 9, 4, 15, 29, 14)}]
    assert sweep.already_known(segment, known)


def test_violation_of_another_person_does_not_close_the_segment():
    segment = _segment()
    known = [{'sip_number': '6612', 'happened_at': datetime(2026, 9, 4, 15, 29, 14)}]
    assert not sweep.already_known(segment, known)


def test_violation_far_in_time_does_not_close_the_segment():
    segment = _segment()
    known = [{'sip_number': '6684', 'happened_at': datetime(2026, 9, 4, 12, 0, 0)}]
    assert not sweep.already_known(segment, known)


def test_clock_skew_is_tolerated():
    """Часы машины оператора и АТС расходятся, а агент ещё и округляет.
    Полторы минуты допуска — те же, что в verify.py."""
    segment = _segment()
    just_before = segment['start'] - timedelta(seconds=60)
    assert sweep.already_known(segment, [{'sip_number': '6684', 'happened_at': just_before}])


def test_empty_journal_leaves_the_segment_uncovered():
    assert not sweep.already_known(_segment(), [])


# --------------------------------------------------------------------------- #
# Идемпотентность
# --------------------------------------------------------------------------- #

def test_client_key_is_stable_for_the_same_sitting():
    """Ключ строится по НАЧАЛУ отрезка: конец между прогонами может уехать на
    секунду, и по нему в отчёте появился бы дубль."""
    first = _segment(seconds=334)
    second = _segment(seconds=336)
    assert sweep.client_key(first) == sweep.client_key(second)


def test_client_key_differs_between_people_and_sittings():
    assert sweep.client_key(_segment()) != sweep.client_key(_segment(login='6612'))
    assert sweep.client_key(_segment()) != sweep.client_key(_segment(start='2026-09-04 16:00:00'))


# --------------------------------------------------------------------------- #
# Запрос к Oktell
# --------------------------------------------------------------------------- #

def test_history_sql_is_read_only_and_paged():
    sql = sweep.build_history_sql(datetime(2026, 9, 4), datetime(2026, 9, 5), cursor_enum=500)
    assert sql.lstrip().upper().startswith('SELECT')
    for forbidden in ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'EXEC'):
        assert forbidden not in sql.upper()
    assert 'Enumerator > 500' in sql
    assert 'ORDER BY h.Enumerator' in sql


def test_history_sql_covers_the_asked_period():
    sql = sweep.build_history_sql(datetime(2026, 9, 4, 8, 0), datetime(2026, 9, 4, 20, 0))
    assert "'20260904 08:00:00'" in sql
    assert "'20260904 20:00:00'" in sql


def test_note_explains_itself_to_a_human():
    segment = dict(_segment(), threshold_s=180)
    text = sweep.note(segment)
    assert '334' in text and '180' in text
    assert 'Перезвон' in text


@pytest.mark.parametrize('bad', [None, [], 'строка'])
def test_no_rows_no_crash(bad):
    assert sweep.recall_segments(bad) == []
