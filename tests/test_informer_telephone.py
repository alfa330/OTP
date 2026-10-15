# -*- coding: utf-8 -*-
"""Телефонная версия записи автоинформатора (informer/telephone.py): оригинал 24 кГц → 8 кГц для робота.

Владелец, 15.10.2026: «голос делать качественным, а не 8 кГц — на звонке он станет ещё хуже». Звонок идёт
в 8 кГц, поэтому защищаем то, от чего зависит звук в трубке: речь 150–3800 Гц проходит без изменений,
всё выше 4 кГц не складывается обратно в полосу разговора (шипящие без «песка»), гул ниже 50 Гц убран,
пик не выше −1 dBFS, и ни одна отметка частей не съезжает во времени."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from informer import telephone, wav  # noqa: E402

RATE = wav.MASTER_RATE


def master(signal):
    pcm = np.clip(np.rint(np.asarray(signal) * 32768), -32768, 32767).astype('<i2')
    return wav.encode(pcm.tobytes(), RATE)


def phone_samples(raw):
    info = wav.inspect(raw)
    assert info['sample_rate'] == wav.PHONE_RATE
    return np.frombuffer(raw, dtype='<i2', count=info['data_bytes'] // 2, offset=info['data_offset']) / 32768


def gain_db(freq, seconds=2.0, amp=0.5):
    t = np.arange(int(RATE * seconds)) / RATE
    tone = amp * np.sin(2 * np.pi * freq * t)
    out = phone_samples(telephone.from_master(master(tone)))[1600:-1600]   # без краёв: там включение тона
    rms_in = amp / np.sqrt(2)
    return 20 * np.log10(np.sqrt(np.mean(out ** 2)) / rms_in + 1e-15)


def test_blocks_give_exactly_the_direct_convolution():
    h = telephone.kernel()
    x = np.random.default_rng(7).standard_normal(100003)
    direct = np.convolve(x, h)[(len(h) - 1) // 2::telephone.FACTOR][:-(-len(x) // telephone.FACTOR)]
    blocks = telephone.band_limit(x, block=telephone.FACTOR * 1024)                      # 33 блока со стыками
    assert len(blocks) == len(direct) == 33335
    assert np.max(np.abs(blocks - direct)) < 1e-9


@pytest.mark.parametrize('freq', [150, 300, 1000, 2500, 3400, 3800])
def test_speech_band_passes_unchanged(freq):
    assert abs(gain_db(freq)) < 0.05


@pytest.mark.parametrize('freq', [4100, 5000, 6000, 8000, 11000])
def test_nothing_above_4khz_folds_back_into_speech(freq):
    # Без фильтра 6 кГц легли бы в трубку тоном 2 кГц той же громкости.
    assert gain_db(freq) < -80


@pytest.mark.parametrize('freq', [0, 20])
def test_rumble_and_dc_removed(freq):
    if freq == 0:
        out = phone_samples(telephone.from_master(master(np.full(2 * RATE, 0.2))))[1600:-1600]
        assert np.max(np.abs(out)) < 1e-3
    else:
        assert gain_db(freq) < -60


def test_timing_kept_to_the_sample_and_length_not_shorter():
    click = np.zeros(2 * RATE)
    click[RATE] = 0.9                                         # ровно 1,000 с
    out = phone_samples(telephone.from_master(master(click)))
    assert int(np.argmax(np.abs(out))) == wav.PHONE_RATE      # ровно 1,000 с и в телефонной
    for n in (612000, 612001, 612002):
        raw = master(0.3 * np.sin(2 * np.pi * 440 * np.arange(n) / RATE))
        source_ms = wav.inspect(raw, rates=(RATE,))['duration_ms']
        phone_ms = wav.inspect(telephone.from_master(raw))['duration_ms']
        assert 0 <= phone_ms - source_ms <= 1                 # части оригинала помещаются в телефонную


def test_peak_never_above_minus_one_dbfs():
    t = np.arange(2 * RATE) / RATE
    square = 0.95 * np.sign(np.sin(2 * np.pi * 1000 * t))   # после фильтра у меандра выбросы выше входа
    out = phone_samples(telephone.from_master(master(square)))
    assert 20 * np.log10(np.max(np.abs(out))) <= telephone.PEAK_LIMIT_DB + 0.01


def test_only_master_accepted():
    with pytest.raises(ValueError):
        telephone.from_master(wav.encode(b'\x00\x00' * 8000, wav.PHONE_RATE))
