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
# Одновременно робот ведёт не больше стольких звонков — решение владельца 16.10.2026 после первого боевого
# запуска: 46 в минуту с общего номера кабинета за 4 минуты уронили исходящие всем операторам (оператор связи
# ~33 с держит вызов и отвечает «занято»), а каждый новый залп продлевал блок: «жёстких залпов делать не надо
# вообще, по 3 максимум»; вечером того же дня, когда сеть снова пропускала, поднял: «давай на 10 увеличим» — 10
# разом, затем «ещё до 15» и «до 20» — 20 разом при темпе 20 в минуту, набор по одному раз в 3 с. Диспетчер держит тот же предел сам
# (INFORMER_MAX_ACTIVE); двойник — informerView.js.
MAX_ACTIVE_CALLS = 20
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
# Сеть не пропустила вызов: до Binotel не дошёл ни один ответ за 32 с (16.10.2026: офисная сеть отрезала VM
# от Binotel после залпа вызовов) или Binotel 32 с не достучался до оператора связи (в тот же день после ~4
# минут темпа 46/мин) — водителю не звонили, хотя записалось бы «не ответил»/«занято»/«сбросил».
RESULT_NO_LINK = 'no_link'
RESULTS = (RESULT_ANSWERED, RESULT_NO_ANSWER, RESULT_BUSY, RESULT_UNAVAILABLE, RESULT_REJECTED,
           RESULT_LINE_BUSY, RESULT_NO_LINK, RESULT_FAILED, RESULT_LOST)
# Водителю не звонили вовсе — попыткой не считается, в доли итогов не входит. «Сбой АТС» — тоже: это 502/503 от
# Binotel или отказ самого робота, до водителя вызов не дошёл (19.10.2026: 71 такой звонок рассылки 9, 11 водителей
# из-за них записаны в «не дозвонились»). Повтор — через обычный интервал, но не больше max_attempts сбоев
# подряд по одному водителю, иначе «битый» номер крутился бы вечно.
NOT_ATTEMPTS = (RESULT_LINE_BUSY, RESULT_NO_LINK, RESULT_FAILED)
NO_LINK_RETRY_SECONDS = 300         # связь пропала — повтор не раньше, чем через 5 минут
# Прогрев темпа (владелец, 17.10.2026, после того как 20 в минуту с утра за 15 минут снова уронили номер:
# «каждое автоинформирование начинать через прогрев — сначала 3, 7, 10, 15, 20»). Ступени — звонков в минуту,
# по WARMUP_STEP_MINUTES каждая, от запуска рассылки и заново после каждой паузы робота из-за отказов сети;
# темп самой рассылки — потолок. После последней ступени прогрев кончается.
WARMUP_STAGES = (3, 7, 10, 15, 20, 25)
WARMUP_STEP_MINUTES = 5
# Простой дольше этого (новое окно дня, долгая пауза, робот молчал) — прогрев заново: 18.10.2026 рассылка,
# оставшаяся «идёт» с вечера, в 10:00 стартовала сразу на 18 в минуту, и через 10 минут номер снова заблокировали.
WARMUP_IDLE_MINUTES = 30
# Остывание после отказов сети (владелец, 18.10.2026: «сделай так, чтобы мне не приходилось писать каждый раз;
# даже если упирался в сбои, сам начинал правильно»). Оператор связи отбивает номер на десятки минут, а пробные
# звонки каждые 2–15 минут только продлевали блок (16–18.10). Поэтому: среди последних COOLDOWN_TRIP_WINDOW
# итогов COOLDOWN_TRIP_NO_LINK и больше «сеть не пропустила», и последний — тоже → рассылка молчит
# COOLDOWN_MINUTES (растут при повторе), потом продолжает сама с прогревом с первой ступени.
COOLDOWN_TRIP_WINDOW = 5
COOLDOWN_TRIP_NO_LINK = 3
COOLDOWN_MINUTES = (45, 60, 90)


def cooldown_minutes(count):
    """Сколько молчать после count-го остывания подряд (1 — первое)."""
    return COOLDOWN_MINUTES[min(max(int(count or 1), 1), len(COOLDOWN_MINUTES)) - 1]


def network_tripped(results):
    """results — итоги последних звонков, новые первыми. Сеть отбивает: последний — «сеть не пропустила»,
    и таких среди окна не меньше COOLDOWN_TRIP_NO_LINK."""
    window = [r for r in list(results)[:COOLDOWN_TRIP_WINDOW]]
    return bool(window) and window[0] == RESULT_NO_LINK and window.count(RESULT_NO_LINK) >= COOLDOWN_TRIP_NO_LINK
RESULT_LABELS = {
    RESULT_ANSWERED: 'Дозвонились',
    RESULT_NO_ANSWER: 'Не ответил',
    RESULT_BUSY: 'Занято',
    RESULT_UNAVAILABLE: 'Недоступен',
    RESULT_REJECTED: 'Сбросил',
    RESULT_LINE_BUSY: 'Линия занята',
    RESULT_NO_LINK: 'Сеть не пропустила',
    RESULT_FAILED: 'Сбой АТС',
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


def after_result(result, attempts, max_attempts, retry_after_minutes, now, failed_before=0):
    """(состояние водителя, когда звонить снова | None, попыток после учёта) по итогу звонка.

    «Линия занята», «Сеть не пропустила» и «Сбой АТС» попыткой не считаются: водителю не звонили вовсе.
    failed_before — сколько «сбоев АТС» у водителя уже было: после max_attempts сбоев сдаёмся."""
    attempts = max(0, int(attempts))
    if result == RESULT_ANSWERED:
        return STATE_DONE, None, attempts
    if result == RESULT_LINE_BUSY:
        return STATE_PENDING, now + timedelta(seconds=LINE_BUSY_RETRY_SECONDS), max(0, attempts - 1)
    if result == RESULT_NO_LINK:
        return STATE_PENDING, now + timedelta(seconds=NO_LINK_RETRY_SECONDS), max(0, attempts - 1)
    if result == RESULT_FAILED:
        if int(failed_before or 0) + 1 >= int(max_attempts):
            return STATE_GIVEN_UP, None, max(0, attempts - 1)
        return STATE_PENDING, now + timedelta(minutes=int(retry_after_minutes)), max(0, attempts - 1)
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
    """Звонков в минуту, которые вытянут channels одновременных звонков при записи audio_ms (оценка),
    но не больше MAX_ACTIVE_CALLS разом."""
    channels = max(0, int(channels or 0)) or CALLS_PER_LINE   # линий ещё не видно — как одна линия
    channels = min(channels, MAX_ACTIVE_CALLS)
    audio_s = audio_ms / 1000 if audio_ms and audio_ms > 0 else DEFAULT_AUDIO_SECONDS
    per_call = EXPECTED_RING_SECONDS + audio_s + CALL_OVERHEAD_SECONDS
    return max(1, min(MAX_PER_MINUTE, int(channels * 60 // per_call)))


def warmup_rate(minutes_since):
    """Разрешённый темп в минуту через minutes_since минут после старта или паузы; None — прогрев кончился
    (или не начинался: minutes_since is None у рассылок, запущенных до прогрева)."""
    if minutes_since is None:
        return None
    step = int(max(0.0, float(minutes_since)) // WARMUP_STEP_MINUTES)
    if step >= len(WARMUP_STAGES):
        return None
    return WARMUP_STAGES[step]


def now_utc():
    return datetime.now(timezone.utc)
