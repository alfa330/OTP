# -*- coding: utf-8 -*-
"""Записи рассылки: WAV, PCM 16 бит, моно — двух частот.

Владелец 15.10.2026: «голос делать качественным, а не 8 кГц — на звонке он станет ещё хуже». Поэтому:
- оригинал — 24 кГц. Это родная частота голоса Soniox: на 48 кГц выше 12 кГц у него пусто (замер
  15.10.2026), больше 24 кГц — только вес. Его слушает руководитель, склеивает его браузер (informerAudio.js);
- телефонная версия — 8 кГц, её играет робот. Звонок на мобильный идёт в G.711, 8 кГц, и больше сеть
  не передаст. Сводит её сервер, один раз и с фильтром (informer/telephone.py).

Сервер формат не правит наугад, а проверяет: Asterisk сыграет «не тот» WAV с хрипом или ускорением,
и выяснилось бы это только по жалобе водителя."""
import struct

PHONE_RATE = 8000
MASTER_RATE = 24000
CHANNELS = 1
BITS = 16
MAX_SECONDS = 5 * 60
# Пять минут звука и заголовок с запасом: телефонная — 4,8 МБ, оригинал — 14,4 МБ.
MAX_BYTES = {PHONE_RATE: 6 * 1024 * 1024, MASTER_RATE: 15 * 1024 * 1024}
MAX_UPLOAD_BYTES = max(MAX_BYTES.values())

_RATE_LABEL = {PHONE_RATE: '8 кГц', MASTER_RATE: '24 кГц'}


def inspect(raw, rates=(PHONE_RATE,)):
    """{'duration_ms', 'data_bytes', 'data_offset', 'sample_rate'} для правильной записи одной из частот
    rates; ValueError — с причиной для человека."""
    if not raw or len(raw) < 44:
        raise ValueError('Запись пустая или повреждена')
    if len(raw) > max(MAX_BYTES[rate] for rate in rates):
        raise ValueError('Запись длиннее пяти минут')
    if raw[0:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Запись должна быть в формате WAV')
    fmt = None
    data_bytes = None
    data_offset = None
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset:offset + 4]
        (size,) = struct.unpack('<I', raw[offset + 4:offset + 8])
        body = offset + 8
        if chunk_id == b'fmt ' and size >= 16:
            fmt = struct.unpack('<HHIIHH', raw[body:body + 16])
        elif chunk_id == b'data':
            data_offset = body
            data_bytes = min(size, len(raw) - body)
            break
        offset = body + size + (size & 1)
    if fmt is None or data_bytes is None:
        raise ValueError('В WAV нет заголовка формата или данных')
    audio_format, channels, sample_rate, _byte_rate, _align, bits = fmt
    if audio_format != 1 or channels != CHANNELS or bits != BITS or sample_rate not in rates:
        allowed = ' или '.join(_RATE_LABEL.get(rate, f'{rate} Гц') for rate in rates)
        raise ValueError(f'Запись должна быть PCM 16 бит, моно, {allowed}')
    samples = data_bytes // (BITS // 8)
    if samples == 0:
        raise ValueError('В записи нет звука')
    if samples > sample_rate * MAX_SECONDS:
        raise ValueError('Запись длиннее пяти минут')
    return {'duration_ms': samples * 1000 // sample_rate, 'data_bytes': samples * (BITS // 8),
            'data_offset': data_offset, 'sample_rate': sample_rate}


def encode(pcm, rate):
    """PCM 16 бит моно (bytes, little-endian) → WAV."""
    fmt = struct.pack('<HHIIHH', 1, CHANNELS, rate, rate * CHANNELS * BITS // 8, CHANNELS * BITS // 8, BITS)
    return (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(pcm)) + pcm)
