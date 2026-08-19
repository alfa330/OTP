"""Сверка выбросов с историей Oktell.

Смысл проверок: программа стоит на компьютере сотрудника и может прислать что
угодно — в том числе выдуманные выбросы на коллегу. В отчёт должно попадать
только то, что подтверждается историей статусов самой АТС.
"""

from datetime import datetime, timedelta

from oktell_guard import verify

MOMENT = datetime(2026, 8, 18, 15, 0, 0)


def history(*pairs):
    """pairs: (сдвиг в секундах от MOMENT, это «Перезвон»?)"""
    rows = []
    for offset, is_recall in pairs:
        rows.append({
            'State': 2 if is_recall else 1,
            'ICode': 2 if is_recall else -1,
            'time_change': (MOMENT + timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return rows


def test_real_violation_is_confirmed():
    """Сидел в «Перезвоне» с 14:56, выброшен в 15:00 — история это подтверждает."""
    rows = history((-240, True))
    status, note = verify.verdict({'happened_at': MOMENT, 'seconds': 185, 'threshold_s': 180}, rows)
    assert status == verify.CONFIRMED, note


def test_invented_violation_on_a_colleague_is_rejected():
    """Классическая подделка: оператор шлёт выброс на коллегу, которая в это
    время спокойно работала. В истории «Перезвона» нет — не засчитываем."""
    rows = history((-300, False))
    status, note = verify.verdict({'happened_at': MOMENT, 'seconds': 200, 'threshold_s': 180}, rows)
    assert status == verify.REJECTED
    assert 'не был' in note


def test_exaggerated_duration_is_rejected():
    """Заявлено 10 минут, по истории — минута. Это не погрешность часов."""
    rows = history((-60, True))
    status, note = verify.verdict({'happened_at': MOMENT, 'seconds': 600, 'threshold_s': 180}, rows)
    assert status == verify.REJECTED


def test_short_sit_below_threshold_is_rejected():
    rows = history((-70, True))
    status, _ = verify.verdict({'happened_at': MOMENT, 'seconds': 70, 'threshold_s': 180}, rows)
    assert status == verify.REJECTED


def test_clock_skew_is_tolerated():
    """Часы машины и АТС расходятся на десятки секунд — это норма и поводом
    отклонить настоящий выброс быть не должно."""
    rows = history((-175, True))
    status, note = verify.verdict({'happened_at': MOMENT, 'seconds': 180, 'threshold_s': 180}, rows)
    assert status == verify.CONFIRMED, note


def test_empty_history_is_rejected():
    status, _ = verify.verdict({'happened_at': MOMENT, 'seconds': 200, 'threshold_s': 180}, [])
    assert status == verify.REJECTED


def test_unparsable_time_stays_pending():
    """Непонятное время — это не подделка и не факт: оставляем на разбор,
    молча засчитывать нельзя."""
    status, _ = verify.verdict({'happened_at': 'вчера', 'seconds': 200, 'threshold_s': 180}, [])
    assert status == verify.PENDING


def test_recall_split_by_short_ready_is_summed():
    """Мигнул на «Готов» и вернулся — правило считает накопленное, и сверка
    обязана считать так же, иначе честный выброс не подтвердится."""
    rows = history((-240, True), (-130, False), (-125, True))
    status, note = verify.verdict({'happened_at': MOMENT, 'seconds': 220, 'threshold_s': 180}, rows)
    assert status == verify.CONFIRMED, note


def test_history_sql_is_readonly_and_scoped():
    sql = verify.build_history_sql('6612', MOMENT, 180)
    assert sql.strip().upper().startswith('SELECT')
    for forbidden in ('INSERT', 'UPDATE', 'DELETE', 'DROP'):
        assert forbidden not in sql.upper()
    assert "'6612'" in sql
    assert '20260818' in sql


def test_history_sql_escapes_quotes():
    sql = verify.build_history_sql("6612' OR 1=1 --", MOMENT, 180)
    assert "6612'' OR 1=1" in sql


def test_utc_timestamp_from_browser_is_converted():
    """Браузер пишет время по Гринвичу (`toISOString`), а история Oktell и наши
    таблицы живут по Алматы. Без перевода сверка искала событие на пять часов
    раньше и отклоняла каждый настоящий выброс."""
    parsed = verify._parse_time('2026-08-19T05:25:00.000Z')
    assert parsed.hour == 10 and parsed.minute == 25


def test_local_timestamp_without_zone_is_left_alone():
    parsed = verify._parse_time('2026-08-19 10:25:00')
    assert parsed.hour == 10


def test_real_violation_confirmed_with_utc_timestamp():
    """Тот самый случай с прода: выброс в 10:25 местного, присланный как 05:25Z."""
    rows = history((-240, True))
    moment_utc = (MOMENT - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    status, note = verify.verdict({'happened_at': moment_utc, 'seconds': 185, 'threshold_s': 180}, rows)
    assert status == verify.CONFIRMED, note
