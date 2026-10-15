# -*- coding: utf-8 -*-
"""Телефонная версия записи: оригинал 24 кГц → 8 кГц для робота, один раз и с честным фильтром.

Звонок на мобильный идёт в G.711: 8 кГц, полоса до 4 кГц, выше сеть ничего не передаст. Портит звук
не сама граница, а то, как к ней приходят. Прореживание без фильтра складывает всё, что выше 4 кГц
(шипящие «с», «ш», «ц»), обратно в полосу разговора, и в трубке это хрип и «песок». Здесь:
- полоса 80–3900 Гц, фильтр с линейной фазой (окно Кайзера, 1201 отсчёт): выше 3950 Гц подавление
  не меньше 80 дБ, так что наложения частот нет, а речь не «плывёт» по фазе;
- ниже 80 Гц — гул и толчки микрофона: в трубке их не слышно, а запас громкости они съедают;
- после фильтра пик не выше −1 dBFS, чтобы кодек не срезал верхушки.
Задержка фильтра вычтена: границы частей (где кончается казахская, где начинается русская) остаются
там же с точностью до отсчёта, а на них стоит аналитика «где положил трубку».

Считается блоками через FFT: память идёт на блок, а не на всю пятиминутную запись."""
import numpy as np

from . import wav

FACTOR = wav.MASTER_RATE // wav.PHONE_RATE        # 3: 24 кГц → 8 кГц ровно
LOW_HZ = 80
HIGH_HZ = 3900
TAPS = 1201                                        # переход ~100 Гц на 24 кГц при подавлении 80 дБ
KAISER_BETA = 7.857                                # 80 дБ: 0,1102 × (80 − 8,7)
PEAK_LIMIT_DB = -1.0
BLOCK = FACTOR * 16384                             # кратно FACTOR: стык блоков не сбивает шаг прореживания

_kernel = None


def kernel():
    """Полосовой фильтр 80–3900 Гц для 24 кГц; усиление на 1 кГц — ровно 1."""
    global _kernel
    if _kernel is None:
        n = np.arange(TAPS) - (TAPS - 1) / 2

        def lowpass(cutoff):
            return 2 * cutoff / wav.MASTER_RATE * np.sinc(2 * cutoff / wav.MASTER_RATE * n)

        h = (lowpass(HIGH_HZ) - lowpass(LOW_HZ)) * np.kaiser(TAPS, KAISER_BETA)
        w = 2 * np.pi * 1000 / wav.MASTER_RATE
        h /= abs(np.sum(h * np.exp(-1j * w * np.arange(TAPS))))
        _kernel = h
    return _kernel


def _window(x, begin, length):
    """x[begin:begin + length] в float64, за краями — нули."""
    out = np.zeros(length)
    lo, hi = max(begin, 0), min(begin + length, len(x))
    if hi > lo:
        out[lo - begin:hi - begin] = x[lo:hi]
    return out


def band_limit(x, h=None, factor=FACTOR, block=BLOCK):
    """Фильтр и прореживание без сдвига во времени: z[i] = (x ∗ h)[delay + factor·i], i < ⌈len(x)/factor⌉.
    То же, что np.convolve(x, h)[delay::factor], только блоками (overlap-save)."""
    h = kernel() if h is None else np.asarray(h, dtype=np.float64)
    m = len(h)
    delay = (m - 1) // 2
    n_out = -(-len(x) // factor)
    size = 1 << (block + m - 2).bit_length()       # FFT не короче block + m − 1: без кругового наложения
    spectrum = np.fft.rfft(h, size)
    out = np.empty(n_out)
    produced = 0
    start = delay                                  # первый нужный отсчёт свёртки
    while produced < n_out:
        # Отсчёт свёртки k зависит от x[k − m + 1 .. k]: блоку [start, start + block) нужен такой кусок x.
        segment = _window(x, start - (m - 1), block + m - 1)
        full = np.fft.irfft(np.fft.rfft(segment, size) * spectrum, size)
        picked = full[m - 1:m - 1 + block:factor][:n_out - produced]
        out[produced:produced + len(picked)] = picked
        produced += len(picked)
        start += block
    return out


def from_master(raw):
    """Оригинал (WAV 24 кГц) → телефонная версия (WAV 8 кГц, моно, PCM 16 бит). ValueError — не оригинал."""
    info = wav.inspect(raw, rates=(wav.MASTER_RATE,))
    pcm = np.frombuffer(raw, dtype='<i2', count=info['data_bytes'] // 2, offset=info['data_offset'])
    phone = band_limit(pcm) / 32768.0
    peak = float(np.max(np.abs(phone))) if len(phone) else 0.0
    limit = 10 ** (PEAK_LIMIT_DB / 20)
    if peak > limit:
        phone *= limit / peak
    samples = np.clip(np.rint(phone * 32768.0), -32768, 32767).astype('<i2')
    return wav.encode(samples.tobytes(), wav.PHONE_RATE)
