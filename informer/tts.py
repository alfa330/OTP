# -*- coding: utf-8 -*-
"""Озвучка текста рассылки: Soniox TTS (владелец, 15.10.2026: «генерация голоса через Soniox»).

REST Soniox (POST https://tts-rt.soniox.com/tts, модель tts-rt-v2) отдаёт звук сразу в формате
робота — WAV 8 кГц, моно, PCM 16 бит; проверено 15.10.2026 на казахском и русском. Каждый голос
Soniox говорит на всех 63 языках модели, отдельных «казахских» голосов нет, поэтому список ниже —
не вкус, а замер: голос озвучивал текст акции на обоих языках, распознавание Soniox записывало
услышанное, и в список вошли те, кого оно поняло лучше всех.

Ключ — тот же SONIOX_API_KEY, что у распознавания звонков. Номера водителей сюда не попадают:
только текст сообщения. Цена — $0,70 за час звука: озвучка рассылки стоит доли цента.
"""
import logging
import os
import time

import httpx

from . import wav

log = logging.getLogger(__name__)

TTS_URL = os.getenv("SONIOX_TTS_URL", "https://tts-rt.soniox.com/tts")
MODEL = "tts-rt-v2"
TEXT_MAX = 1500          # ~1,5 минуты речи на часть — дольше автоинформатор не слушают
TIMEOUT_SEC = 90
# Отказ «занято» (429) — повторяем дважды с паузой 2 и 4 с, дальше честный отказ человеку.
BUSY_RETRIES = 2
BUSY_PAUSE_SEC = 2
# Части рассылки называются kz | ru (informer.rules.PARTS_ORDER), а Soniox ждёт ISO-коды.
LANGS = {"kz": "kk", "ru": "ru"}
# Темп речи: Soniox принимает 0,7–1,3. Три понятных шага вместо ползунка.
SPEEDS = {"slow": 0.9, "normal": 1.0, "fast": 1.1}
DEFAULT_SPEED = "normal"

# Голоса по замеру 15.10.2026 (scratchpad soniox_voice_eval.py): озвучка текста акции → распознавание
# Soniox → доля ошибок в словах; лучшие — сверху. label — как голос подписан в интерфейсе.
# Итог 15.10.2026, 14 голосов: у этих четырёх распознавание не ошиблось ни в одном слове на обоих
# языках, кроме записи числа («7» вместо «жеті»/«семь»). Мужские Kirill и Taras — лучшие из мужских:
# Kirill на русском, Taras на казахском.
VOICES = [
    {"id": "Iris", "label": "Айрис", "gender": "female", "about": "женский, мягкий и спокойный"},
    {"id": "Grace", "label": "Грейс", "gender": "female", "about": "женский, ровный, без спешки"},
    {"id": "Taras", "label": "Тарас", "gender": "male", "about": "мужской, сдержанный"},
    {"id": "Kirill", "label": "Кирилл", "gender": "male", "about": "мужской, ровный темп"},
]
DEFAULT_VOICE = VOICES[0]["id"]
_VOICE_IDS = {v["id"] for v in VOICES}


class TTSError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = int(status)


def _clean(text):
    return " ".join(str(text or "").replace("\r", " ").split())


def synthesize(text, lang, voice=None, speed=DEFAULT_SPEED, post=None, sleep=None):
    """Текст → WAV 8 кГц моно (bytes). TTSError — с причиной для человека."""
    key = os.getenv("SONIOX_API_KEY")
    if not key:
        raise TTSError("Озвучка не настроена на сервере: нет ключа Soniox", 503)
    if lang not in LANGS:
        raise TTSError("Язык озвучки — казахский или русский", 400)
    text = _clean(text)
    if len(text) < 2:
        raise TTSError("Напишите текст, который озвучить", 400)
    if len(text) > TEXT_MAX:
        raise TTSError(f"Текст длиннее {TEXT_MAX} знаков — сократите его", 400)
    voice = voice if voice in _VOICE_IDS else DEFAULT_VOICE
    body = {"model": MODEL, "language": LANGS[lang], "voice": voice, "text": text,
            "audio_format": "wav", "sample_rate": wav.SAMPLE_RATE,
            "speed": SPEEDS.get(speed, SPEEDS[DEFAULT_SPEED])}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    status, content = 0, b""
    for attempt in range(BUSY_RETRIES + 1):
        try:
            status, content = (post or _post)(TTS_URL, headers, body)
        except httpx.HTTPError as exc:
            log.warning("informer tts: %s", type(exc).__name__)
            raise TTSError("Soniox не ответил — попробуйте ещё раз", 502)
        # Soniox держит мало одновременных озвучек на ключ (15.10.2026: шесть разом — 429 у пяти).
        if status != 429 or attempt == BUSY_RETRIES:
            break
        (sleep or time.sleep)(BUSY_PAUSE_SEC * (attempt + 1))
    if status == 429:
        raise TTSError("Soniox сейчас занят — попробуйте через минуту", 503)
    if status != 200:
        log.warning("informer tts: HTTP %s, %s", status, content[:200])
        raise TTSError("Soniox не озвучил текст — попробуйте ещё раз", 502)
    try:
        wav.inspect(content)
    except ValueError:
        log.warning("informer tts: ответ не WAV 8 кГц (%d байт)", len(content))
        raise TTSError("Soniox вернул звук не в том формате", 502)
    return content


def _post(url, headers, body):
    with httpx.Client(timeout=TIMEOUT_SEC) as client:
        response = client.post(url, json=body, headers=headers)
        return response.status_code, response.content
