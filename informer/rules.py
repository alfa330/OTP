# -*- coding: utf-8 -*-
"""Правила автоинформатора без базы: исходы звонка, повторы, окно времени, части записи."""
from datetime import datetime, time, timedelta, timezone

ALMATY = timezone(timedelta(hours=5))

# Темп — сколько тянут линии робота, без потолка закона: решение владельца 16.10.2026 («сколько тянут
# линии, без предела»), принятое со знанием закона РК «О связи» (ст. 2 подп. 22-2 и ст. 21 п. 3-1, с
# 20.08.2026): больше десяти автовызовов с записью в минуту оператор вправе не пропускать без договора.
# Темп рассылки — её собственное поле; общий предел — свободные места на линиях робота.
# Линия Binotel держит два звонка одновременно (15.10.2026: из пяти разом прошли два, трём —
# 480 сразу). Значение по умолчанию для диспетчера; сам диспетчер присылает ёмкость линий.
CALLS_PER_LINE = 2
# Сколько звонков в минуту вытянут линии: место на линии звонок держит на время вызова (до ответа в
# среднем ~12 с — замер 15.10.2026: 7–16 с), записи и отбоя (~2 с). Это оценка для поля формы, а не
# обещание: недозвон держит место дольше (до 35 с), отказ сети — меньше. Двойник — informerView.js.
EXPECTED_RING_SECONDS = 12
CALL_OVERHEAD_SECONDS = 2
DEFAULT_AUDIO_SECONDS = 25          # записи ещё нет — обычная длина: две части по ~12 с
MAX_PER_MINUTE = 600                # верх поля — проверка ввода, а не темп: 45 линий по 2 звонка — меньше
DEFAULT_WINDOW = (time(10, 0), time(20, 0))
DEFAULT_MAX_ATTEMPTS = 2
MAX_ATTEMPTS_LIMIT = 5
DEFAULT_RETRY_MINUTES = 60
# Отказ «линия занята» — не попытка: повтор через полминуты, счётчик попыток не растёт.
LINE_BUSY_RETRY_SECONDS = 30
# Звонок, по которому диспетчер не прислал итог за «длину записи + это время», — потерян
# (перезапуск шлюза, обрыв связи): водитель возвращается в очередь как после неудачной попытки.
# Запас покрывает вызов (до 35 с), доставку итога и повторы диспетчера.
STALE_CALL_SECONDS = 300
# Самая длинная запись: пять минут — это уже не оповещение, а лекция.
MAX_AUDIO_MS = 5 * 60 * 1000

RESULT_ANSWERED = 'answered'        # водитель снял трубку (сколько слушал — listened_ms)
RESULT_NO_ANSWER = 'no_answer'      # звонило, не ответил
RESULT_BUSY = 'busy'                # занято
RESULT_UNAVAILABLE = 'unavailable'  # выключен, вне сети, номер не существует
RESULT_REJECTED = 'rejected'        # сбросил вызов
RESULT_LINE_BUSY = 'line_busy'      # Binotel не дал линию (480 сразу, без вызова)
RESULT_FAILED = 'failed'            # робот или АТС не смогли позвонить
RESULT_LOST = 'lost'                # итог не пришёл (перезапуск, обрыв)
RESULTS = (RESULT_ANSWERED, RESULT_NO_ANSWER, RESULT_BUSY, RESULT_UNAVAILABLE, RESULT_REJECTED,
           RESULT_LINE_BUSY, RESULT_FAILED, RESULT_LOST)
RESULT_LABELS = {
    RESULT_ANSWERED: 'Дозвонились',
    RESULT_NO_ANSWER: 'Не ответил',
    RESULT_BUSY: 'Занято',
    RESULT_UNAVAILABLE: 'Недоступен',
    RESULT_REJECTED: 'Сбросил',
    RESULT_LINE_BUSY: 'Линия занята',
    RESULT_FAILED: 'Ошибка АТС',
    RESULT_LOST: 'Итог не пришёл',
    '': 'Звоним',
}

HANGUP_DRIVER = 'driver'   # водитель положил трубку, не дослушав
HANGUP_ROBOT = 'robot'     # запись кончилась — робот положил сам
HANGUP_NETWORK = 'network'
HANGUP_SIDES = (HANGUP_DRIVER, HANGUP_ROBOT, HANGUP_NETWORK, '')

STATE_PENDING = 'pending'
STATE_CALLING = 'calling'
STATE_DONE = 'done'          # дозвонились
STATE_GIVEN_UP = 'given_up'  # попытки кончились
STATE_STOPPED = 'stopped'    # рассылку завершили раньше, чем дошла очередь

CAMPAIGN_DRAFT = 'draft'
CAMPAIGN_ACTIVE = 'active'
CAMPAIGN_PAUSED = 'paused'
CAMPAIGN_DONE = 'done'
CAMPAIGN_ACTIONS = {
    # действие: (из каких статусов, в какой)
    'start': ({CAMPAIGN_DRAFT, CAMPAIGN_PAUSED}, CAMPAIGN_ACTIVE),
    'pause': ({CAMPAIGN_ACTIVE}, CAMPAIGN_PAUSED),
    'finish': ({CAMPAIGN_DRAFT, CAMPAIGN_ACTIVE, CAMPAIGN_PAUSED}, CAMPAIGN_DONE),
}

LANG_LABELS = {'kz': 'казахская', 'ru': 'русская'}
# Порядок частей записи — решение владельца 15.10.2026: сначала казахская, потом русская.
PARTS_ORDER = ('kz', 'ru')

STAGE_NONE = 'none'   # не дозвонились
STAGE_FULL = 'full'   # дослушал до конца


def clean_parts(parts, duration_ms):
    """Части записи [{lang, start_ms, end_ms}] → проверенный список в порядке PARTS_ORDER.

    Части нужны аналитике («на какой части положил трубку»), поэтому границы обязаны лежать
    внутри записи и идти по порядку, а языки — ровно те, что в PARTS_ORDER. ValueError — нет."""
    if not isinstance(parts, (list, tuple)) or len(parts) != len(PARTS_ORDER):
        raise ValueError('Ожидались две части записи: казахская и русская')
    out = []
    previous_end = 0
    for expected_lang, part in zip(PARTS_ORDER, parts):
        if not isinstance(part, dict) or part.get('lang') != expected_lang:
            raise ValueError('Порядок частей записи: сначала казахская, потом русская')
        try:
            start_ms = int(part.get('start_ms'))
            end_ms = int(part.get('end_ms'))
        except (TypeError, ValueError):
            raise ValueError('Границы частей записи — целые миллисекунды')
        if not (previous_end <= start_ms < end_ms <= int(duration_ms)):
            raise ValueError('Границы частей записи выходят за запись или перекрываются')
        out.append({'lang': expected_lang, 'start_ms': start_ms, 'end_ms': end_ms})
        previous_end = end_ms
    return out


def listen_stage(listened_ms, parts, played_to_end=False):
    """На какой части водитель положил трубку: язык части | STAGE_FULL | STAGE_NONE.

    listened_ms — от ответа до отбоя; запись играет с момента ответа, поэтому сравнивается с
    границами напрямую. Дослушал до конца — когда запись доиграла (played_to_end) или он был на
    линии дольше конца последней части: тишина в хвосте записи уже ничего не добавляет."""
    if listened_ms is None:
        return STAGE_NONE
    listened = max(0, int(listened_ms))
    if played_to_end or not parts or listened >= int(parts[-1]['end_ms']):
        return STAGE_FULL
    for part in parts:
        if listened < int(part['end_ms']):
            return part['lang']
    return STAGE_FULL


def stage_label(stage):
    if stage == STAGE_FULL:
        return 'дослушал до конца'
    if stage == STAGE_NONE:
        return '—'
    return f"положил на части «{LANG_LABELS.get(stage, stage)}»"


def after_result(result, attempts, max_attempts, retry_after_minutes, now):
    """(состояние водителя, когда звонить снова | None, попыток после учёта) по итогу звонка.

    «Линия занята» попыткой не считается: Binotel водителю не звонил вовсе."""
    attempts = max(0, int(attempts))
    if result == RESULT_ANSWERED:
        return STATE_DONE, None, attempts
    if result == RESULT_LINE_BUSY:
        return STATE_PENDING, now + timedelta(seconds=LINE_BUSY_RETRY_SECONDS), max(0, attempts - 1)
    if attempts >= int(max_attempts):
        return STATE_GIVEN_UP, None, attempts
    return STATE_PENDING, now + timedelta(minutes=int(retry_after_minutes)), attempts


def in_window(now, window_start, window_end):
    """Входит ли момент now (aware) в окно рассылки по Алматы [начало, конец).

    Окно через полночь (22:00–02:00) поддержано, хотя для звонков водителям оно странное."""
    local = now.astimezone(ALMATY).time()
    if window_start <= window_end:
        return window_start <= local < window_end
    return local >= window_start or local < window_end


def parse_hhmm(value, default):
    """'HH:MM' → time; пусто — default; мусор — ValueError."""
    text = str(value or '').strip()
    if not text:
        return default
    try:
        hours, minutes = text.split(':', 1)
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise ValueError('Время окна — в виде ЧЧ:ММ')


def clamp_int(value, default, low, high, name):
    """Целое из формы в пределах [low, high]; пусто — default; мусор — ValueError."""
    if value is None or str(value).strip() == '':
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f'{name}: нужно целое число')
    if not low <= number <= high:
        raise ValueError(f'{name}: от {low} до {high}')
    return number


def line_capacity(lines):
    """Сколько звонков робот держит одновременно: сумма ёмкости линий {'939': {'capacity': 2, ...}}."""
    total = 0
    for info in (lines or {}).values():
        try:
            total += max(0, int((info or {}).get('capacity') or 0))
        except (TypeError, ValueError, AttributeError):
            continue
    return total


def line_rate(channels, audio_ms=None):
    """Звонков в минуту, которые вытянут channels одновременных звонков при записи audio_ms (оценка)."""
    channels = max(0, int(channels or 0)) or CALLS_PER_LINE   # линий ещё не видно — как одна линия
    audio_s = audio_ms / 1000 if audio_ms and audio_ms > 0 else DEFAULT_AUDIO_SECONDS
    per_call = EXPECTED_RING_SECONDS + audio_s + CALL_OVERHEAD_SECONDS
    return max(1, min(MAX_PER_MINUTE, int(channels * 60 // per_call)))


def now_utc():
    return datetime.now(timezone.utc)
