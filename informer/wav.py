# -*- coding: utf-8 -*-
"""Проверка записи рассылки: WAV, PCM 16 бит, моно, 8 кГц — ровно то, что играет робот.

Склеивает и пересчитывает запись браузер (informerAudio.js). Сервер формат не правит, а
проверяет: Asterisk сыграет «не тот» WAV с хрипом или ускорением, и выяснилось бы это только
по жалобе водителя."""
import struct

SAMPLE_RATE = 8000
CHANNELS = 1
BITS = 16
MAX_BYTES = 6 * 1024 * 1024  # 5 минут 8 кГц/16 бит — 4,8 МБ, с запасом на заголовок


def inspect(raw):
    """{'duration_ms', 'data_bytes'} для правильной записи; ValueError — с причиной для человека."""
    if not raw or len(raw) < 44:
        raise ValueError('Запись пустая или повреждена')
    if len(raw) > MAX_BYTES:
        raise ValueError('Запись длиннее пяти минут')
    if raw[0:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Запись должна быть в формате WAV')
    fmt = None
    data_bytes = None
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset:offset + 4]
        (size,) = struct.unpack('<I', raw[offset + 4:offset + 8])
        body = offset + 8
        if chunk_id == b'fmt ' and size >= 16:
            fmt = struct.unpack('<HHIIHH', raw[body:body + 16])
        elif chunk_id == b'data':
            data_bytes = min(size, len(raw) - body)
            break
        offset = body + size + (size & 1)
    if fmt is None or data_bytes is None:
        raise ValueError('В WAV нет заголовка формата или данных')
    audio_format, channels, sample_rate, _byte_rate, _align, bits = fmt
    if audio_format != 1 or channels != CHANNELS or sample_rate != SAMPLE_RATE or bits != BITS:
        raise ValueError('Запись должна быть PCM 16 бит, моно, 8 кГц')
    samples = data_bytes // (BITS // 8)
    if samples == 0:
        raise ValueError('В записи нет звука')
    return {'duration_ms': samples * 1000 // SAMPLE_RATE, 'data_bytes': data_bytes}
