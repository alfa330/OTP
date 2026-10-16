# -*- coding: utf-8 -*-
"""Автоинформатор без базы: части записи, где положил трубку, повторы, окно, WAV, список водителей."""
import io
import os
import struct
import sys
from datetime import datetime, time, timedelta, timezone

import pytest
from openpyxl import Workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from informer import recipients, rules, wav  # noqa: E402

PARTS = [{'lang': 'kz', 'start_ms': 500, 'end_ms': 13050}, {'lang': 'ru', 'start_ms': 13650, 'end_ms': 25220}]
NOW = datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc)


def test_parts_order_is_kazakh_first():
    assert rules.clean_parts(PARTS, 25520) == PARTS
    with pytest.raises(ValueError):
        rules.clean_parts(list(reversed(PARTS)), 25520)          # решение владельца: казахская первой
    with pytest.raises(ValueError):
        rules.clean_parts([PARTS[0], {**PARTS[1], 'end_ms': 30000}], 25520)   # за пределами записи
    with pytest.raises(ValueError):
        rules.clean_parts([PARTS[0], {**PARTS[1], 'start_ms': 13000}], 25520)  # части перекрываются


@pytest.mark.parametrize('listened, played, stage', [
    (None, False, rules.STAGE_NONE),
    (0, False, 'kz'),
    (13049, False, 'kz'),
    (13050, False, 'ru'),
    (25219, False, 'ru'),
    (25220, False, rules.STAGE_FULL),     # хвост тишины уже ничего не добавляет
    (3000, True, rules.STAGE_FULL),       # запись доиграла — значит дослушал
])
def test_listen_stage(listened, played, stage):
    assert rules.listen_stage(listened, PARTS, played) == stage


def test_after_result():
    assert rules.after_result(rules.RESULT_ANSWERED, 1, 2, 60, NOW) == (rules.STATE_DONE, None, 1)
    state, at, attempts = rules.after_result(rules.RESULT_LINE_BUSY, 1, 1, 60, NOW)
    assert (state, attempts) == (rules.STATE_PENDING, 0)         # попытку не сжигаем
    assert at == NOW + timedelta(seconds=rules.LINE_BUSY_RETRY_SECONDS)
    state, at, attempts = rules.after_result(rules.RESULT_NO_ANSWER, 1, 2, 45, NOW)
    assert (state, at, attempts) == (rules.STATE_PENDING, NOW + timedelta(minutes=45), 1)
    assert rules.after_result(rules.RESULT_BUSY, 2, 2, 45, NOW)[0] == rules.STATE_GIVEN_UP
    # «Сеть не пропустила» — водителю не звонили: попытка цела даже на последней, повтор через 5 минут.
    assert rules.after_result(rules.RESULT_NO_LINK, 2, 2, 45, NOW) == (
        rules.STATE_PENDING, NOW + timedelta(seconds=rules.NO_LINK_RETRY_SECONDS), 1)
    assert rules.RESULT_NO_LINK in rules.RESULTS and rules.RESULT_NO_LINK in rules.NOT_ATTEMPTS
    assert rules.RESULT_LABELS[rules.RESULT_NO_LINK] == 'Сеть не пропустила'


def test_window_by_almaty_time():
    noon_almaty = datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc)
    assert rules.in_window(noon_almaty, time(10), time(20))
    assert not rules.in_window(noon_almaty, time(13), time(20))
    assert not rules.in_window(datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc), time(10), time(20))  # 20:00
    assert rules.in_window(datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc), time(22), time(2))       # 23:00


def _wav(rate=8000, channels=1, bits=16, seconds=1.0):
    data = b'\x00' * int(rate * seconds) * channels * (bits // 8)
    fmt = struct.pack('<HHIIHH', 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    return (b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)


def test_wav_inspect():
    assert wav.inspect(_wav(seconds=25.52))['duration_ms'] == 25520
    both = (wav.MASTER_RATE, wav.PHONE_RATE)
    master = wav.inspect(_wav(rate=24000, seconds=25.52), rates=both)
    assert master['duration_ms'] == 25520 and master['sample_rate'] == 24000
    assert master['data_offset'] == 44 and master['data_bytes'] == 612480 * 2
    assert wav.inspect(_wav(seconds=2), rates=both)['sample_rate'] == 8000
    # По умолчанию — только телефонная: оригинал роботу не годится, он сыграл бы его в три раза медленнее.
    for bad in (_wav(rate=24000), _wav(rate=16000), _wav(channels=2), _wav(bits=8), b'', b'ID3' + b'\x00' * 100):
        with pytest.raises(ValueError):
            wav.inspect(bad)
    with pytest.raises(ValueError, match='24 кГц или 8 кГц'):
        wav.inspect(_wav(rate=48000), rates=both)
    with pytest.raises(ValueError, match='пяти минут'):
        wav.inspect(_wav(rate=24000, seconds=301), rates=both)


def test_line_rate_is_what_robot_fits_with_fifteen_at_once():
    """Владелец, 16.10.2026: в поле темпа сразу максимум — сколько тянет робот; с того же дня — предел звонков
    разом («жёстких залпов делать не надо вообще, по 3 максимум», вечером — «на 10», потом «ещё до 15»)."""
    nine = {str(n): {'capacity': 2, 'active': 0} for n in (902, 907, 919, 923, 925, 928, 939, 942, 944)}
    assert rules.line_capacity(nine) == 18
    assert rules.line_capacity({'939': {'capacity': 'x'}, '940': None, '941': {'capacity': 2}}) == 2
    assert rules.MAX_ACTIVE_CALLS == 15
    # Звонок держит место: ~12 с до ответа + запись + ~2 с на отбой; мест — не больше пятнадцати.
    assert rules.line_rate(18, 25520) == 22                 # 15 × 60 / 39,52 с
    assert rules.line_rate(18, 8120) == 40                  # запись короче — звонков больше: 900 / 22,12 с
    assert rules.line_rate(18) == 22                        # записи ещё нет — обычные 25 с
    assert rules.line_rate(0, 25520) == 3                   # линий не видно — как одна линия (2 звонка)
    assert rules.line_rate(2, 300000) == 1                  # даже на пятиминутной записи не ноль
    assert rules.line_rate(10000, 1000) == 60               # и тысяча линий — пятнадцать звонков разом


def test_wav_encode_round_trip():
    pcm = struct.pack('<4h', 0, 32767, -32768, 100)
    raw = wav.encode(pcm, wav.PHONE_RATE)
    info = wav.inspect(raw)
    assert raw[info['data_offset']:info['data_offset'] + info['data_bytes']] == pcm


def test_recipients_csv_with_header_and_duplicates():
    raw = 'ФИО;Телефон\nИванов;+7 707 612 90 56\nПетров;87076129056\nСидоров;12345\n;7 778 503 59 61\n'.encode()
    parsed = recipients.parse(raw, '.csv')
    assert parsed['rows'] == [(2, 'Иванов', '77076129056'), (5, '', '77785035961')]
    assert parsed['duplicates'] == 1 and parsed['invalid'] == 1


def test_recipients_phone_only_column_without_header():
    parsed = recipients.parse('+77076770474\n87760034405\n'.encode(), '.csv')
    assert [r[2] for r in parsed['rows']] == ['77076770474', '77760034405']
    assert all(r[1] == '' for r in parsed['rows'])               # имени нет — не выдумываем


def test_recipients_xlsx_numbers_and_name_column():
    wb = Workbook()
    ws = wb.active
    ws.append(['Водитель', 'Номер'])
    ws.append(['Айгерим', 77473524248])                           # Excel хранит номер числом
    ws.append(['Ерлан', 87076129056.0])
    stream = io.BytesIO()
    wb.save(stream)
    parsed = recipients.parse(stream.getvalue(), '.xlsx')
    assert parsed['rows'] == [(2, 'Айгерим', '77473524248'), (3, 'Ерлан', '77076129056')]


def test_recipients_rejects_files_without_numbers():
    with pytest.raises(ValueError):
        recipients.parse('Иванов\nПетров\n'.encode(), '.csv')
    with pytest.raises(ValueError):
        recipients.parse(b'x', '.txt')
