"""Клиент официального REST API Binotel 4.0 — общий для всех отделов на этой АТС.

Компаний Binotel у нас несколько (у каждого отдела на Binotel своя), и у каждой
свой ключ API: имя компании → префикс переменных окружения (COMPANY_ENV_PREFIX).
В отличие от `tez/status_sync.py` (который скрейпит панель my.binotel.kz ради
истории статусов), этот модуль работает с официальным REST API Binotel 4.0
(developers.binotel.ua, «Integration with CRM») по ключу/секрету:

  - база: https://api.binotel.com/api/4.0/
  - метод: POST {base}/<group>/<method>.json, тело JSON с {key, secret, ...params}
  - звонки сотрудника за период:
        stats/list-of-calls-by-internal-number-for-period
        параметры: internalNumber, startTime, stopTime (unix), максимум 7 дней;
  - все звонки компании за сутки:
        stats/list-of-calls-for-period
        параметры: startTime, stopTime (unix), окно строго в один день;
  - ссылка на запись разговора (живёт ~15 минут):
        stats/call-record   (параметр callID = generalCallID);
  - ответ: {status:'success', callDetails:{ "<generalCallID>": {...} }}.

Сопоставление звонка с нашим оператором — ПО ИМЕНИ (employeeData.name из ответа),
а не по internalNumber: один и тот же sip со временем закрепляют за разными
операторами (см. ветку матчинга в bot_schedule2._binotel_random_call).

ENV (окружение сервера — Render; локально .env.codex.local), на каждую компанию:
    <PREFIX>_API_KEY=<ключ>
    <PREFIX>_API_SECRET=<секрет>
    <PREFIX>_API_URL=https://api.binotel.com/api/4.0   # опционально
    <PREFIX>_TZ=Asia/Almaty                            # опционально
где PREFIX — из COMPANY_ENV_PREFIX (TEZ_BINOTEL, REMOTE_CC_BINOTEL) либо
<ИМЯ_КОМПАНИИ_ЗАГЛАВНЫМИ>_BINOTEL для любой новой.

Частота запросов. Binotel отвергает слишком частые запросы (status='error', code=106,
«Requests are too frequent. You can do this request after N sec.»). Клиент сам
расставляет запросы во времени — см. блок «частота запросов» ниже. Вызывающему
достаточно знать параметр max_wait у методов клиента:
    max_wait=None — ждать своей очереди сколько нужно и повторять после отказа (как было);
    max_wait=0    — не ждать вовсе: нельзя отправить прямо сейчас — сразу RateLimited
                    (фоновые опросы: обойдутся тем, что уже знают);
    max_wait=N    — ждать не дольше N секунд (оператор или телефон ждёт ответа).

Проверка вручную (без БД), чтобы сверить имена полей с реальным ответом:
    python -m binotel.client --internal 907 --start 2026-06-01 --stop 2026-06-03
"""

import argparse
import http.cookiejar
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


log = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Almaty"
DEFAULT_API_URL = "https://api.binotel.com/api/4.0"
# Таймаут HTTP — отдельно на соединение и на ответ (13.10.2026). Ответа click-to-call ждём до
# 40 с: Binotel отвечает, когда линия оператора ответила или отказала (отказ, код 150, приходит
# после ~30 с звонков на линию). А соединение с api.binotel.com — доли секунды, и когда сеть до
# Binotel пропадала, запрос висел все 40 с: 13.10.2026 в 19:04 двое операторов ждали по 41 с и
# получили «обратитесь к руководителю». Не соединились за HTTP_CONNECT_TIMEOUT — запрос до
# Binotel не дошёл, и click-to-call его повторяет (ORIGINATE_CONNECT_RETRIES).
HTTP_CONNECT_TIMEOUT = 5
HTTP_READ_TIMEOUT = 40
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
# Повторы click-to-call (13.10.2026). Повторять можно только запрос, который заведомо не дошёл
# до Binotel или который Binotel заведомо не выполнил, — иначе водителю уйдёт второй звонок:
#   * не соединились (таймаут соединения, отказ в соединении, имя не разрешилось) — запрос не
#     отправлен: ещё ORIGINATE_CONNECT_RETRIES раз;
#   * код 105 «Something went wrong», пришедший за ORIGINATE_105_FAST_SEC: все шесть таких отказов
#     за три недели, сверенные со списком звонков Binotel, звонка не дали, а ручной повтор
#     оператора через 4–10 с проходил: ещё ORIGINATE_105_RETRIES раз.
# Паузы перед повторами — ORIGINATE_RETRY_PAUSES. После таймаута ответа (запрос ушёл, ответа нет)
# и после любого другого ответа Binotel — без повтора.
ORIGINATE_CONNECT_RETRIES = 2
ORIGINATE_105_RETRIES = 2
ORIGINATE_105_FAST_SEC = 10.0
ORIGINATE_RETRY_PAUSES = (2.0, 3.0)
_ERROR_CODE_RE = re.compile(r"\bcode=(\d{1,6})\b")
# Binotel: list-of-calls-by-internal-number-for-period ограничен окном в 7 дней.
MAX_WINDOW_DAYS = 7
# list-of-calls-for-period отдаёт ВСЕ звонки компании, но окно строго в одни
# сутки (два дня -> «Wrong data»), а limit/offset/callType/internalNumber метод
# молча игнорирует. Обрезки нет: проверено на самом нагруженном дне базы
# (08.07.2026, 1166 звонков) — выдача за день в точности равна сумме двух своих
# половин, ни один звонок не потерялся и не задвоился.
MAX_PERIOD_WINDOW_DAYS = 1
# history-by-external-number принимает массив номеров, НО режет ответ по общему
# числу ЗВОНКОВ (~500): при превышении часть номеров молча выпадает из ответа с
# нулём звонков (проверено 2026-07-22: 95 номеров -> 504 звонка, выпало 45; 30
# номеров -> 344 звонка, все на месте). Поэтому шлём маленькими пачками и, если
# ответ подошёл к лимиту, делим пачку пополам — иначе теряются звонки, а с ними
# успешки. Одиночный номер не делим (у лида столько звонков не бывает).
MAX_EXTERNAL_NUMBERS_PER_REQUEST = 25
# Порог, при достижении которого ответ мог быть обрезан -> дробим пачку.
EXTERNAL_NUMBERS_TRUNCATION_GUARD = 400
# callType в ответе Binotel: 0 = входящий, 1 = исходящий.
CALL_TYPE_INCOMING = 0
CALL_TYPE_OUTGOING = 1
# disposition, у которых по документации существует запись разговора.
RECORDED_DISPOSITIONS = {"ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS"}
# recordingStatus из ответа Binotel, при котором запись реально доступна.
RECORDED_STATUSES = {"uploaded"}
# ------------------------------------------------------------ частота запросов
# Binotel лимитирует частоту запросов: на лишний отвечает status='error', code=106,
# «Requests are too frequent. You can do this request after N sec.». Как клиент
# расставляет запросы:
#
#   * Состояние — НА КЛЮЧ API, то есть на компанию Binotel, а не на процесс: Тез КЦ и
#     удалённый КЦ друг друга не ждут. Клиент при этом создают заново почти на каждый
#     вызов (from_config), поэтому состояние живёт в модуле, а не в объекте клиента.
#   * Замок состояния берётся только на арифметику («когда мне слать»). Ни сон, ни сам
#     HTTP-запрос под ним не идут. Раньше замок был один на процесс и держался весь
#     запрос, а click-to-call отвечает ~10 с: всё это время стояли запросы всех
#     компаний. 12.10.2026 так обрывались живые разговоры — ответ на вопрос телефона
#     «класть ли трубку на гудках» не успевал за его 5 секунд.
#   * Между отправками по одному ключу — зазор MIN_REQUEST_INTERVAL, и такой же — после
#     каждого полученного ответа (при общем замке зазор считался от конца запроса; этот
#     проверенный темп сохранён). Запрос, на который ответа ещё нет, никого не держит.
#   * Подсказка «after N sec» становится ПАУЗОЙ, общей для всех потоков: пока она не
#     истекла, запросы под неё не уходят вовсе. Раньше спал только поток, получивший
#     отказ, а остальные продолжали стучаться и получали тот же отказ.
#   * «Нагружаемые» методы (выборки статистики) идут строго по одному на ключ: у
#     Binotel на них отдельный бюджет, около 5 запросов в минуту.
#
# На что именно Binotel ставит лимит лёгкого метода, достоверно не известно: то ли
# на метод целиком («не чаще раза в N секунд на компанию»), то ли на повтор одного и
# того же запроса («этот звонок спрашивали только что»). Текст «You can do THIS
# request after N sec» читается обоими способами, сайт с документацией из нашей сети
# не открывается, а ошибка в любую сторону стоит дорого: пауза на весь метод при
# лимите «на запрос» держала бы чужие звонки из-за одного, пауза «на запрос» при
# лимите на метод — это прежний перебор отказов. Поэтому клиент выясняет это сам:
#
#   * отвергнутый запрос ставит на паузу запросы С ТЕМИ ЖЕ ДАННЫМИ (тот же звонок, та
#     же линия) — это верно при любом устройстве лимита;
#   * если отвергнут запрос, какого мы последние _SAME_REQUEST_MEMORY секунд не
#     отправляли, повтором это быть не может: значит, лимит у метода общий. Тогда и
#     эта пауза, и следующие (_WIDE_LIMIT_TTL секунд) действуют на весь метод;
#   * вывод снимается, как только два запроса метода приняты подряд: при общем лимите
#     так не бывает, значит «новый» запрос до нас спрашивал кто-то другой (прежний
#     процесс перед перезапуском).
# Что клиент выяснил, он пишет в журнал — это и есть ответ на вопрос об устройстве
# лимита.
#
# Приложение поднимается одним процессом (waitress), так что состояние в памяти
# процесса и есть общее на всех.
#
# Зазор оставлен в 1 с. По журналу 12.10.2026 Binotel отвергал stats/call-details и
# при нём (подсказки 1–4 с) — его окно шире секунды. Каково оно на самом деле, по
# одним подсказкам не понять, поэтому под Binotel частоту подстраивает общая пауза, а
# зазор — только нижняя граница. Для подбора при каждом отказе в журнал пишется,
# сколько прошло от прошлого принятого запроса того же метода: это число плюс
# подсказка и есть окно Binotel.
MIN_REQUEST_INTERVAL = 1.0   # минимальный зазор между отправками по одному ключу, сек
RATE_LIMIT_MAX_RETRIES = 3   # сколько раз отправить запрос, пока Binotel отвечает «too frequent»
RATE_LIMIT_MAX_WAIT = 12     # потолок паузы по подсказке сервера, сек
RATE_LIMIT_MARGIN = 0.4      # запас к подсказке: N в «after N sec» — целые секунды
# Точечные методы stats/* (один звонок по id): отвечают быстро и в бюджет
# «нагружаемых» не входят. На них живёт обзвон — исход звонка и запись разговора.
LIGHT_STATS_ENDPOINTS = frozenset({"stats/call-details", "stats/call-record"})
_HEAVY_SCOPE = "stats:heavy"
# Сколько секунд запрос с теми же данными считается «тем же». С запасом к окну
# Binotel: подсказки не превышали 4 с, значит повтор он отвергает секунд пять.
_SAME_REQUEST_MEMORY = 20.0
# Сколько помним, что лимит метода общий. Не навсегда: вывод делается по одному
# отказу, а «нового» для нас запроса мог коснуться чужой опрос того же звонка (другой
# процесс с этим ключом) — тогда через этот срок клиент проверит заново.
_WIDE_LIMIT_TTL = 300.0
# Раньше срока вывод снимают два принятых подряд запроса метода: между их отправками
# прошло не больше стольких зазоров MIN_REQUEST_INTERVAL (с запасом на время ответа).
# При общем лимите с окном в секунды такого не бывает.
_BACK_TO_BACK = 2.0
# По каким параметрам запрос «тот же самый». Прочие параметры — настройки запроса:
# callerIdForEmployee, например, у всего отдела один, и отказ одному оператору не
# должен ставить на паузу звонки остальных.
_IDENTITY_PARAMS = ("generalCallID", "internalNumber", "externalNumber")
_MAX_REQUEST_TOKENS = 64     # больше значений из одного запроса не запоминаем
_MAX_TRACKED = 512           # потолок записей в памяти одного ключа; старые выметаются


class RateLimited(RuntimeError):
    """Запрос упёрся в лимит частоты Binotel.

    Либо не отправлен вовсе (ждать своей очереди пришлось бы дольше разрешённого
    max_wait), либо отправлен и отвергнут («Requests are too frequent», код 106), а
    повторять уже нельзя. Наследник RuntimeError: прежние `except RuntimeError` и
    `except Exception` у вызывающих работают как раньше.

    retry_after — через сколько секунд есть смысл повторить (оценка клиента);
    endpoint    — метод API."""

    def __init__(self, message="", retry_after=0.0, endpoint=""):
        super().__init__(message or "Binotel: запросы слишком часто (too frequent)")
        self.retry_after = max(0.0, float(retry_after or 0.0))
        self.endpoint = str(endpoint or "")


def connect_failed(exc):
    """Запрос не дошёл до Binotel: не соединились — таймаут соединения, отказ в соединении, имя
    не разрешилось. Повторять такой запрос безопасно. Обрыв уже открытого соединения
    («Connection aborted», RemoteDisconnected) и таймаут ответа сюда не входят: запрос мог уйти."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        reason = getattr(exc.args[0], "reason", None) if exc.args else None
        return isinstance(reason, urllib3.exceptions.NewConnectionError)
    return False


def error_code(exc):
    """Код отказа Binotel из текста ошибки клиента («… code=105: …») или None."""
    m = _ERROR_CODE_RE.search(str(exc or ""))
    return int(m.group(1)) if m else None


def _endpoint_name(endpoint):
    return str(endpoint or "").strip().strip("/").lower()


def is_heavy_endpoint(endpoint):
    """«Нагружаемый» ли метод Binotel.

    Тяжёлыми считаются все stats/* кроме LIGHT_STATS_ENDPOINTS: list-of-calls-for-period,
    list-of-calls-by-internal-number-for-period, history-by-external-number,
    outgoing-/incoming-calls-for-period, list-of-calls-per-day, …-since и любая новая
    выборка. Незнакомый метод статистики безопаснее пустить по одному, чем выбить
    бюджет соседям по ключу. calls/*, settings/* и прочие группы — лёгкие."""
    name = _endpoint_name(endpoint)
    return name.startswith("stats/") and name not in LIGHT_STATS_ENDPOINTS


def _limit_scope(endpoint):
    """Чью паузу соблюдает метод.

    У «нагружаемых» бюджет общий на ключ — и пауза одна на все выборки. У остальных
    пауза не шире своего метода: отказ в call-details не должен задерживать
    click-to-call по тому же ключу. Окажись лимит у Binotel общим на ключ, соседний
    метод узнает об этом одним отвергнутым запросом — это дешевле, чем держать
    звонок оператора из-за чужого опроса."""
    return _HEAVY_SCOPE if is_heavy_endpoint(endpoint) else _endpoint_name(endpoint)


def _request_tokens(params):
    """О чём этот запрос — звонок, линия, номер: значения _IDENTITY_PARAMS, каждое отдельно.

    Отдельно, а не весь запрос целиком: список из двух звонков и одиночный запрос по
    одному из них — для Binotel, возможно, «тот же звонок». Хранятся не сами значения
    (среди них номера телефонов), а их хеши; в журнал они не попадают. У запроса без
    таких параметров отличить его от других нечем — пауза у него на весь метод."""
    found = []
    for name in _IDENTITY_PARAMS:
        value = (params or {}).get(name)
        values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        for item in values:
            if item is not None and str(item).strip() and len(found) < _MAX_REQUEST_TOKENS:
                found.append(hash((name, str(item).strip())))
    return tuple(dict.fromkeys(found))


class _KeyRate:
    """Состояние частоты запросов одного ключа API (одной компании Binotel)."""

    __slots__ = ("lock", "next_slot_at", "quiet_until", "cooldowns", "seen",
                 "wide_until", "accepted_at", "heavy_lock")

    def __init__(self):
        # Короткий замок: под ним только арифметика. Ни сна, ни HTTP.
        self.lock = threading.Lock()
        # Времена — time.monotonic().
        self.next_slot_at = 0.0    # первый свободный слот отправки (зазор от прошлой ОТПРАВКИ)
        self.quiet_until = 0.0     # зазор от прошлого полученного ОТВЕТА
        # До какого момента Binotel просил не слать. Ключ — область лимита
        # (_limit_scope: весь метод или все выборки) либо (область, значение запроса).
        self.cooldowns = {}
        self.seen = {}             # (область, значение запроса) -> когда такое уходило
        self.wide_until = {}       # область -> до какого момента паузы ставим на весь метод
        self.accepted_at = {}      # область -> когда ушёл последний ПРИНЯТЫЙ запрос (для журнала)
        # «Нагружаемые» запросы — строго по одному: замок держится весь вызов.
        self.heavy_lock = threading.Lock()


_RATE_STATES = {}
_RATE_STATES_LOCK = threading.Lock()


def _rate_state(api_key):
    """Состояние частоты для ключа API — одно на процесс, сколько бы клиентов ни создали."""
    key = str(api_key or "")
    with _RATE_STATES_LOCK:
        state = _RATE_STATES.get(key)
        if state is None:
            state = _RATE_STATES[key] = _KeyRate()
        return state


def _reset_rate_state():
    """Забыть состояние частоты всех ключей. Только для тестов."""
    with _RATE_STATES_LOCK:
        _RATE_STATES.clear()


def _too_frequent_wait_seconds(payload):
    """Если Binotel ответил «слишком часто» — вернёт рекомендованную паузу (сек), иначе None.

    Признак — код 106 либо сам текст: в поле code Binotel отдаёт и число, и строку,
    а текст ошибки может поменяться."""
    text = f"{payload.get('message') or ''} {payload.get('error') or ''}".lower()
    by_code = str(payload.get('code') or '').strip() == '106'
    if not by_code and 'frequent' not in text and 'часто' not in text:
        return None
    m = re.search(r'after\s+(\d+)\s*sec', text)
    return int(m.group(1)) if m else 10


def _not_sent(endpoint, retry_after, reason=""):
    """RateLimited для запроса, который не ушёл: ждать пришлось бы дольше разрешённого.

    Слова «too frequent» и «часто» в тексте нужны: по ним вызывающие отличают лимит
    частоты от отказа АТС (обзвон отвечает на такое 429, а не 502)."""
    retry_after = max(0.0, float(retry_after))
    return RateLimited(
        f"Binotel {endpoint}: запросы слишком часто (too frequent), запрос не отправлен"
        f"{f' — {reason}' if reason else ''}; повторить можно через {retry_after:.1f} с",
        retry_after=retry_after, endpoint=endpoint)


def _paused_until(state, scope, tokens):
    """До какого момента Binotel просил не слать такой запрос. Вызывать под state.lock."""
    until = state.cooldowns.get(scope, 0.0)
    for token in tokens:
        until = max(until, state.cooldowns.get((scope, token), 0.0))
    return until


def _remember_request(state, scope, tokens, sent_at):
    """Отметить, что запрос уходит. Вызывать под state.lock.

    Возвращает, «новый» ли он: ни одно из его значений не уходило в этот метод
    последние _SAME_REQUEST_MEMORY секунд."""
    fresh = bool(tokens)
    for token in tokens:
        key = (scope, token)
        last = state.seen.get(key)
        if last is not None and sent_at - last <= _SAME_REQUEST_MEMORY:
            fresh = False
        state.seen[key] = sent_at
    if len(state.seen) > _MAX_TRACKED:
        edge = sent_at - _SAME_REQUEST_MEMORY
        state.seen = {key: at for key, at in state.seen.items() if at > edge}
    return fresh


def _note_too_frequent(state, scope, tokens, fresh, hint, sent_at):
    """Binotel ответил «too frequent» — запомнить паузу для всех потоков этого ключа.

    Пауза всегда ложится на запросы с теми же данными (tokens), а на весь метод —
    когда отвергнут «новый» запрос (fresh: повтором он быть не мог, значит лимит
    общий) и ещё _WIDE_LIMIT_TTL секунд после такого случая. У выборок статистики и
    у запросов без параметров данных нет — у них пауза всегда общая.

    Возвращает (сколько осталось ждать этому запросу,
                сколько прошло от последнего ПРИНЯТОГО запроса метода до отвергнутого | None,
                легла ли пауза на весь метод,
                впервые ли выяснилось, что лимит общий).
    Второе число вместе с подсказкой показывает настоящее окно лимита Binotel — по
    нему подбирают MIN_REQUEST_INTERVAL.

    Подсказку режем потолком RATE_LIMIT_MAX_WAIT: одна строка ответа не должна
    останавливать, например, звонки отдела на час. Просит Binotel и правда больше —
    следующий запрос после паузы принесёт новую подсказку, один на все потоки, а не
    по одному с каждого."""
    pause = min(max(0, hint), RATE_LIMIT_MAX_WAIT) + RATE_LIMIT_MARGIN
    with state.lock:
        now = time.monotonic()
        until = now + pause
        learned = False
        if tokens and fresh:
            learned = state.wide_until.get(scope, 0.0) <= now
            state.wide_until[scope] = now + _WIDE_LIMIT_TTL
        whole = not tokens or state.wide_until.get(scope, 0.0) > now
        keys = [(scope, token) for token in tokens]
        if whole:
            keys.append(scope)
        for key in keys:
            state.cooldowns[key] = max(state.cooldowns.get(key, 0.0), until)
        if len(state.cooldowns) > _MAX_TRACKED:
            state.cooldowns = {key: at for key, at in state.cooldowns.items() if at > now}
        accepted_at = state.accepted_at.get(scope)
        since = max(0.0, sent_at - accepted_at) if accepted_at is not None else None
        return _paused_until(state, scope, tokens) - now, since, whole, learned


def _note_accepted(state, scope, sent_at):
    """Запрос принят лимитом Binotel (ответ не «too frequent»).

    Запоминает, когда он ушёл, и заодно проверяет вывод «лимит у метода общий». Два
    принятых подряд запроса метода (с нашим обычным зазором) при общем лимите с окном
    в секунды невозможны: значит, вывод был ложным — «новый» для нас запрос кто-то
    спрашивал помимо нас (прежний процесс до перезапуска, чужой скрипт с тем же
    ключом). Тогда вывод снимается; возвращает True, если снят."""
    with state.lock:
        previous = state.accepted_at.get(scope)
        if previous is None or sent_at > previous:
            state.accepted_at[scope] = sent_at
        # abs: ответы приходят не в порядке отправки (медленный запрос обгоняют быстрые).
        if (previous is not None and scope in state.wide_until
                and 0 < abs(sent_at - previous) <= _BACK_TO_BACK * MIN_REQUEST_INTERVAL):
            del state.wide_until[scope]
            return True
    return False


def _acquire_heavy(state, endpoint, deadline):
    """Встать в очередь «нагружаемых» запросов ключа (ждать не дольше deadline)."""
    if deadline is None:
        state.heavy_lock.acquire()
        return
    remaining = deadline - time.monotonic()
    got = (state.heavy_lock.acquire(timeout=remaining) if remaining > 0
           else state.heavy_lock.acquire(blocking=False))
    if not got:
        raise _not_sent(endpoint, MIN_REQUEST_INTERVAL, "идёт другая выборка статистики по этому ключу")


def _await_slot(state, scope, tokens, endpoint, deadline, pause_left):
    """Дождаться своей очереди на отправку.

    Возвращает (остаток бюджета пауз, момент отправки, «новый» ли запрос — см.
    _remember_request).

    Отправлять можно, когда прошли три срока:
      * зазор MIN_REQUEST_INTERVAL от прошлой отправки по этому ключу;
      * такой же зазор от последнего ПОЛУЧЕННОГО ответа. Так было при общем замке
        (зазор считался от конца запроса), и на этом темпе выверены и длинные обходы,
        и обычный опрос. Запросы «в полёте» при этом никого не держат: пока ответа
        нет, зазору не от чего считаться;
      * пауза, которую Binotel запросил для такого запроса или для всего метода.

    Слот занимает тот, кто первым дождался, — заранее его не бронируют. Бронь «на
    потом» держала бы очередь из-за одного запроса, который сам ещё стоит на паузе.

    Замок состояния берётся только на арифметику, спим всегда вне его. Проснувшись,
    всё пересчитываем: слот мог занять сосед, а паузу — продлить чужой отказ 106.

    deadline — момент, дольше которого ждать нельзя. None — режим «как раньше»:
    очередь ждём сколько нужно, а на паузы Binotel тратим не больше pause_left секунд."""
    woke_at = 0.0
    while True:
        with state.lock:
            # Своему сну верим: time.sleep меньше заказанного не спит, а часы monotonic
            # бывают грубее сна (Windows) — иначе на каждый запрос уходил бы лишний круг.
            now = max(time.monotonic(), woke_at)
            paused_until = _paused_until(state, scope, tokens)
            ready_at = max(paused_until, state.next_slot_at, state.quiet_until)
            if ready_at <= now:
                state.next_slot_at = now + MIN_REQUEST_INTERVAL
                return pause_left, now, _remember_request(state, scope, tokens, now)
            wait = ready_at - now
            if deadline is not None:
                # Не успеть — говорим сразу, а не после сна «на удачу».
                if wait > max(0.0, deadline - now):
                    raise _not_sent(endpoint, wait)
            else:
                pause = max(0.0, paused_until - now)
                if pause > pause_left:
                    raise _not_sent(endpoint, wait, "Binotel продлевает паузу")
                pause_left -= pause
        time.sleep(wait)
        woke_at = ready_at


def _parse_env_file(path):
    """Простой парсер .env (KEY=VALUE); кавычки снимаем."""
    data = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return data


# Компаний в Binotel у нас две, и у каждой СВОЙ ключ API: ключ выдаётся на
# аккаунт компании и линии другой компании не видит. Имя компании → префикс
# переменных окружения (<PREFIX>_API_KEY / _API_SECRET / _API_URL / _TZ).
COMPANY_TEZ = "tez"                # отдел Тез КЦ (историческая, по умолчанию)
COMPANY_REMOTE_CC = "remote_cc"    # удалённый колл-центр (обзвон водителей из iCORE Phone)
COMPANY_ENV_PREFIX = {
    COMPANY_TEZ: "TEZ_BINOTEL",
    COMPANY_REMOTE_CC: "REMOTE_CC_BINOTEL",
}


def env_prefix_for(company):
    """Имя компании → префикс переменных окружения с ключом API.

    Известные компании — из таблицы; любая новая — <ИМЯ>_BINOTEL заглавными,
    чтобы новый отдел на Binotel не требовал правки кода: достаточно завести
    переменные на сервере."""
    name = str(company or COMPANY_TEZ).strip()
    if name in COMPANY_ENV_PREFIX:
        return COMPANY_ENV_PREFIX[name]
    if name.upper() == name and name.endswith("_BINOTEL"):
        return name  # передали сразу префикс
    return f"{name.upper()}_BINOTEL"


def get_config(env_file=".env.codex.local", company=COMPANY_TEZ):
    """Конфиг из окружения; чего нет — добираем из .env.codex.local.

    `company` — имя из COMPANY_ENV_PREFIX либо сразу префикс переменных."""
    file_env = _parse_env_file(env_file)
    prefix = env_prefix_for(company)

    def pick(name, default=None):
        return os.getenv(name) or file_env.get(name) or default

    base_url = (pick(f"{prefix}_API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")
    return {
        "company": company,
        "env_prefix": prefix,
        "base_url": base_url,
        "api_key": pick(f"{prefix}_API_KEY"),
        "api_secret": pick(f"{prefix}_API_SECRET"),
        "tz": pick(f"{prefix}_TZ", DEFAULT_TZ),
    }


def api_ready(config=None):
    cfg = config or get_config()
    return bool(cfg.get("api_key")) and bool(cfg.get("api_secret"))


def _tzinfo(tz_name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name or DEFAULT_TZ)
        except Exception:
            pass
    from datetime import timezone
    return timezone(timedelta(hours=5))  # Asia/Almaty без перехода на летнее время


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def normalize_call_end_party(value, call_type=-1):
    """Map Binotel's whoHungUp value to the journal's provider-neutral enum."""
    raw = "" if value is None else str(value).strip()
    if not raw:
        return "unknown"

    compact = re.sub(r"[\s_\-./]+", "", raw.casefold())
    operator_values = {
        "employee", "operator", "agent", "manager", "staff", "internal",
        "internalnumber", "extension", "user",
        "сотрудник", "оператор", "менеджер", "працівник",
    }
    client_values = {
        "client", "customer", "external", "externalnumber", "abonent",
        "subscriber", "otherparty",
        "клиент", "абонент", "клієнт",
    }
    system_values = {
        "system", "pbx", "ivr", "api", "timeout", "network",
        "система", "атс",
    }
    if compact in operator_values:
        return "operator"
    if compact in client_values:
        return "client"
    if compact in system_values:
        return "system"

    direction = _to_int(call_type, default=-1)
    if compact in {"caller", "calling", "originator"}:
        if direction == CALL_TYPE_INCOMING:
            return "client"
        if direction == CALL_TYPE_OUTGOING:
            return "operator"
    if compact in {"callee", "called", "recipient", "answerer"}:
        if direction == CALL_TYPE_INCOMING:
            return "operator"
        if direction == CALL_TYPE_OUTGOING:
            return "client"
    return "unknown"


class BinotelApiClient:
    """Минимальный клиент Binotel API 4.0 (key/secret)."""

    def __init__(self, api_key, api_secret, base_url=DEFAULT_API_URL, tz=DEFAULT_TZ, timeout=HTTP_TIMEOUT):
        if not api_key or not api_secret:
            raise ValueError("Ключ и секрет Binotel API не заданы (<PREFIX>_API_KEY/<PREFIX>_API_SECRET)")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")
        self.tz = tz or DEFAULT_TZ
        self.timeout = timeout
        self.session = requests.Session()
        # Одну сессию теперь делят потоки: запросы идут одновременно (раньше общий замок
        # пропускал их по одному). Пул соединений к этому готов, а банка cookies — нет:
        # её читают и дополняют без общего замка. API cookies не нужны (ключ и секрет —
        # в теле каждого запроса), поэтому не принимаем их вовсе.
        self.session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))

    @classmethod
    def from_config(cls, config=None):
        cfg = config or get_config()
        return cls(cfg.get("api_key"), cfg.get("api_secret"), cfg.get("base_url"), cfg.get("tz"))

    def _post(self, endpoint, params, max_wait=None, timeout=None):
        """POST {base}/{endpoint}.json с key+secret. Возвращает распарсенный JSON.

        Очередь к Binotel — у каждого ключа API своя (см. блок «частота запросов» в
        начале модуля): запрос ждёт слот отправки и паузу, которую Binotel запросил
        отказом «too frequent», но сам HTTP-запрос идёт уже вне замков. «Нагружаемые»
        методы вдобавок идут по одному на ключ.

        max_wait — сколько секунд вызов согласен ЖДАТЬ своей очереди, считая от начала
        вызова (зазор, пауза Binotel, чужая выборка статистики):
          None — как раньше: очередь ждём сколько нужно, после отказа «too frequent»
                 повторяем — всего до RATE_LIMIT_MAX_RETRIES отправок, а на паузы уходит
                 суммарно не больше ~37 с (на это рассчитаны вызывающие: журнал табло
                 Тез поэтому и качается своим потоком);
          0    — не ждём вовсе: нельзя отправить прямо сейчас или Binotel ответил
                 «too frequent» — сразу RateLimited. Для фоновых опросов;
          N    — ждём не дольше N секунд, иначе RateLimited (причём сразу, а не через
                 N секунд, если уже видно, что не успеть). Для живых действий.
        Сам HTTP-запрос в это время не входит — его ограничивает timeout.

        timeout — таймаут HTTP этого вызова, как у requests (секунды или пара «соединение,
        чтение»); None — таймаут клиента (HTTP_TIMEOUT). Общему опросу живых звонков обзвона
        нужен свой, короткий: опрос у компании один, и зависший на 40 с запрос держал бы финалы
        всех её операторов (ревью 13.10.2026).

        Отказ лимита — RateLimited (наследник RuntimeError), прочие ошибки — RuntimeError."""
        url = f"{self.base_url}/{endpoint}.json"
        body = dict(params or {})
        body["key"] = self.api_key
        body["secret"] = self.api_secret

        state = _rate_state(self.api_key)
        heavy = is_heavy_endpoint(endpoint)
        scope = _limit_scope(endpoint)
        # У выборок пауза всегда общая — различать запросы между собой незачем.
        tokens = () if heavy else _request_tokens(params)
        deadline = None if max_wait is None else time.monotonic() + max(0.0, float(max_wait))

        if heavy:
            _acquire_heavy(state, endpoint, deadline)
        try:
            return self._post_in_turn(endpoint, url, body, state, scope, tokens, heavy, deadline, timeout)
        finally:
            if heavy:
                # Замок тяжёлых держим весь вызов вместе с повторами: всё, чего ждёт
                # тяжёлый запрос (пауза Binotel, зазор, слот), одинаково касается и
                # остальных тяжёлых этого ключа — отпускать его на время сна незачем.
                state.heavy_lock.release()

    def _post_in_turn(self, endpoint, url, body, state, scope, tokens, heavy, deadline, timeout=None):
        """Тело _post: дождаться очереди, отправить, при «too frequent» — пауза и повтор.
        timeout — таймаут HTTP каждой отправки (None — таймаут клиента)."""
        if timeout is None:
            timeout = self.timeout
        attempts = max(1, int(RATE_LIMIT_MAX_RETRIES))
        # Режим «как раньше» (deadline None): сколько секунд можно потратить на паузы.
        pause_left = attempts * (RATE_LIMIT_MAX_WAIT + RATE_LIMIT_MARGIN)
        attempt = 0
        while True:
            attempt += 1
            pause_left, sent_at, fresh = _await_slot(
                state, scope, tokens, endpoint, deadline, pause_left)
            try:
                # Вне замка состояния: медленный ответ (click-to-call — ~10 с) никого не держит.
                resp = self.session.post(url, json=body, timeout=timeout)
            finally:
                # Ответ получен (или запрос оборвался) — от этого момента считается зазор
                # до следующей отправки, см. _await_slot.
                with state.lock:
                    state.quiet_until = max(state.quiet_until,
                                            time.monotonic() + MIN_REQUEST_INTERVAL)

            if resp.status_code != 200:
                raise RuntimeError(f"Binotel HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                payload = resp.json()
            except ValueError:
                raise RuntimeError(f"Binotel: не JSON в ответе {endpoint}: {resp.text[:300]}")
            status = str(payload.get("status", "")).lower()
            ok = not status or status in ("success", "ok", "1", "true")
            hint = None if ok else _too_frequent_wait_seconds(payload)
            # Лимит частоты запрос прошёл — даже если дальше отказ по существу (не та
            # линия, неверные данные).
            if hint is None and _note_accepted(state, scope, sent_at):
                log.info("Binotel %s: два запроса подряд приняты — общего лимита у метода нет, "
                         "паузы снова только для запросов с теми же данными", endpoint)
            if ok:
                return payload

            # Код ошибки нужен в тексте: у click-to-call, например, 104 означает
            # «дозвон по API не включён на линии», и это вопрос к поддержке
            # Binotel, а не к нашему коду.
            code = payload.get('code')
            message = (
                f"Binotel {endpoint} status={payload.get('status')!r}"
                f"{f' code={code}' if code not in (None, '') else ''}: "
                f"{payload.get('message') or payload.get('error') or ''}"[:300]
            )
            if hint is None:
                raise RuntimeError(message)

            pause, since, whole, learned = _note_too_frequent(state, scope, tokens, fresh, hint, sent_at)
            can_wait = pause <= (pause_left if deadline is None
                                 else max(0.0, deadline - time.monotonic()))
            retry = attempt < attempts and can_wait
            if heavy:
                paused = "всех выборок статистики ключа"
            else:
                paused = "всех запросов этого метода" if whole else "запросов с теми же данными"
            # Данные запроса (id звонка, номера) в журнал не пишем — только метод.
            log.info("Binotel %s: слишком часто (подсказка %s с%s) — пауза %.1f с для %s, "
                     "попытка %d из %d, %s",
                     endpoint, hint,
                     "" if since is None else ", от прошлого принятого запроса %.1f с" % since,
                     pause, paused, attempt, attempts, "повторим" if retry else "отказ")
            if learned:
                log.info("Binotel %s: отвергнут запрос, какого мы не отправляли, — лимит у метода "
                         "общий. Ближайшие %d мин паузы действуют на весь метод",
                         endpoint, int(_WIDE_LIMIT_TTL // 60))
            if not retry:
                raise RateLimited(message, retry_after=pause, endpoint=endpoint)

    def _request(self, endpoint, params, max_wait=None, timeout=None):
        """self._post для методов клиента: max_wait и timeout уходят дальше, только когда заданы.

        У подклассов и тестовых подделок _post бывает прежней сигнатуры
        (endpoint, params) — вызов без max_wait и timeout их не ломает."""
        extra = {}
        if max_wait is not None:
            extra["max_wait"] = max_wait
        if timeout is not None:
            extra["timeout"] = timeout
        return self._post(endpoint, params, **extra)

    @staticmethod
    def _normalize_call(raw):
        """callDetails-элемент Binotel -> нормализованный dict.

        Имена полей защищены фолбэками (в разных инсталляциях встречаются
        варианты написания), т.к. официальную схему сверяем через CLI.
        """
        if not isinstance(raw, dict):
            return None
        gid = raw.get("generalCallID") or raw.get("generalCallId") or raw.get("callID") or raw.get("id")
        if gid is None:
            return None
        call_type = _to_int(raw.get("callType"), default=-1)
        billsec = _to_int(raw.get("billsec"), default=0)
        waitsec = _to_int(raw.get("waitsec"), default=0)
        start_time = _to_int(raw.get("startTime") or raw.get("startTimeUTC") or raw.get("start_time"), default=0)
        who_hung_up = raw.get("whoHungUp") if "whoHungUp" in raw else raw.get("who_hung_up")
        # employeeData несёт имя/почту сотрудника, обслужившего звонок. Один и тот же
        # sip (internalNumber) со временем закрепляют за разными операторами, поэтому
        # сопоставлять звонок с нашим оператором нужно ПО ИМЕНИ, а не по номеру.
        emp = raw.get("employeeData")
        emp_name = ""
        emp_email = ""
        if isinstance(emp, dict):
            emp_name = str(emp.get("name") or emp.get("fullName") or "").strip()
            emp_email = str(emp.get("email") or "").strip()
        # Линия компании, НА которую позвонил клиент (`pbxNumberData.number`) — это не номер
        # клиента, а наш собственный городской: по нему табло Тез отделяет поток ТП от ОП.
        # Очередь в API не видна вовсе, и линия — единственный признак направления.
        pbx = raw.get("pbxNumberData")
        line_number = str((pbx or {}).get("number") or "").strip() if isinstance(pbx, dict) else ""
        return {
            "general_call_id": str(gid),
            "call_type": call_type,
            "line_number": line_number,
            "billsec": billsec,
            "waitsec": waitsec,
            "start_time": start_time,
            "internal_number": str(raw.get("internalNumber") or raw.get("internalNumbers") or "").strip(),
            "external_number": str(raw.get("externalNumber") or raw.get("clientNumber") or "").strip(),
            "disposition": str(raw.get("disposition") or "").strip().upper(),
            "employee_name": emp_name,
            "employee_email": emp_email,
            "recording_status": str(raw.get("recordingStatus") or "").strip().lower(),
            "call_end_party": normalize_call_end_party(who_hung_up, call_type),
            "call_end_raw": "" if who_hung_up is None else str(who_hung_up).strip(),
        }

    @staticmethod
    def _extract_call_details(payload):
        details = payload.get("callDetails")
        if isinstance(details, dict):
            return list(details.values())
        if isinstance(details, list):
            return details
        return []

    # max_wait у методов ниже — см. _post. У методов из нескольких запросов (окна,
    # пачки) он действует на каждый запрос к Binotel отдельно.

    def list_calls_by_internal_number(self, internal_number, start_ts, stop_ts, max_wait=None):
        """Все звонки сотрудника за период. Период бьём на окна <=7 дней."""
        internal_number = str(internal_number).strip()
        if not internal_number:
            return []
        start_ts = int(start_ts)
        stop_ts = int(stop_ts)
        if stop_ts < start_ts:
            start_ts, stop_ts = stop_ts, start_ts
        window = MAX_WINDOW_DAYS * 86400
        out = []
        seen = set()
        cur = start_ts
        while cur <= stop_ts:
            chunk_stop = min(cur + window - 1, stop_ts)
            payload = self._request(
                "stats/list-of-calls-by-internal-number-for-period",
                {"internalNumber": internal_number, "startTime": cur, "stopTime": chunk_stop},
                max_wait,
            )
            for raw in self._extract_call_details(payload):
                call = self._normalize_call(raw)
                if not call or call["general_call_id"] in seen:
                    continue
                seen.add(call["general_call_id"])
                out.append(call)
            cur = chunk_stop + 1
        return out

    def list_calls_by_external_numbers(self, phones, max_wait=None):
        """Вся история звонков по номерам КЛИЕНТОВ (stats/history-by-external-number).

        В отличие от list_calls_by_internal_number период не задаётся вообще —
        метод отдаёт всю историю по каждому номеру. Именно поэтому для успешек
        ОП не нужно зеркалить весь трафик компании: список интересующих номеров
        известен заранее (это база лидов), и один запрос закрывает сразу пачку.

        Проверено на живом API: 99 номеров -> 677 звонков; 300 номеров метод
        принимает, 1000 отвергает («Request is too large»). Шлём по
        MAX_EXTERNAL_NUMBERS_PER_REQUEST с запасом.
        """
        numbers = [str(p).strip() for p in (phones or []) if str(p or '').strip()]
        if not numbers:
            return []
        out = []
        seen = set()

        def _fetch(chunk):
            """Тянет историю по пачке номеров; если ответ подошёл к лимиту выдачи
            (значит часть номеров могла выпасть) — делит пачку пополам и повторяет,
            пока не станет надёжно. Одиночный номер уже не делим."""
            if not chunk:
                return
            payload = self._request("stats/history-by-external-number", {"externalNumbers": chunk}, max_wait)
            details = self._extract_call_details(payload)
            if len(details) >= EXTERNAL_NUMBERS_TRUNCATION_GUARD and len(chunk) > 1:
                mid = len(chunk) // 2
                _fetch(chunk[:mid])
                _fetch(chunk[mid:])
                return
            for raw in details:
                call = self._normalize_call(raw)
                if not call or call["general_call_id"] in seen:
                    continue
                seen.add(call["general_call_id"])
                out.append(call)

        for start in range(0, len(numbers), MAX_EXTERNAL_NUMBERS_PER_REQUEST):
            _fetch(numbers[start:start + MAX_EXTERNAL_NUMBERS_PER_REQUEST])
        return out

    def list_calls_for_day(self, day, max_wait=None):
        """Все звонки компании за календарный день (Asia/Almaty), одним запросом.

        В отличие от history-by-external-number список номеров тут не нужен:
        метод отдаёт весь трафик дня, а мы уже локально решаем, какие номера нам
        интересны. Для «сколько лидов реально обзвонили» это единственный дешёвый
        путь — историю по 7 000 номеров пришлось бы тянуть сотнями запросов, а
        день закрывается одним (500–1200 звонков, обрезки нет).

        day — date либо строка 'YYYY-MM-DD'.
        """
        key = day.isoformat() if hasattr(day, 'isoformat') else str(day).strip()
        start_ts, stop_ts = _day_bounds_unix(key, key, self.tz)
        payload = self._request(
            "stats/list-of-calls-for-period",
            {"startTime": start_ts, "stopTime": stop_ts},
            max_wait,
        )
        out = []
        seen = set()
        for raw in self._extract_call_details(payload):
            call = self._normalize_call(raw)
            if not call or call["general_call_id"] in seen:
                continue
            seen.add(call["general_call_id"])
            out.append(call)
        return out

    def get_call_record_url(self, general_call_id, max_wait=None):
        """Ссылка на запись разговора (подписанный S3-URL, живёт ~1 час) или None."""
        payload = self._request("stats/call-record", {"generalCallID": str(general_call_id)}, max_wait)
        url = payload.get("url") or payload.get("recordUrl") or payload.get("link")
        if not url and isinstance(payload.get("callDetails"), dict):
            first = next(iter(payload["callDetails"].values()), {})
            if isinstance(first, dict):
                url = first.get("url") or first.get("recordUrl")
        return url or None

    # ------------------------------------------------------------ звонки (calls/*)
    # Методы группы calls подтверждены официальным архивом примеров Binotel API
    # 4.0 (binotel-api-4.0.0-samples: samples-api-rest-calls.php).

    def originate_internal_to_external(self, internal_number, external_number, max_wait=None, **extra):
        """Двусторонний звонок: АТС звонит на внутреннюю линию сотрудника, а после
        ответа — на внешний номер. Возвращает generalCallID (str).

        Это click-to-call для обзвона из iCORE Phone: номер водителя в телефон не
        передаётся вовсе, оператор получает от АТС входящее плечо. `extra` уходит
        в запрос как есть (например callerIdForEmployee, playbackWaiting,
        callTimeToExt, limitCallTime — их поддержка проверяется живым звонком,
        в официальных примерах этих параметров нет).

        Метод синхронный: Binotel отвечает, когда линия сотрудника ответила или
        отказала, — это секунд десять. Очередь остальных запросов он при этом не
        держит. max_wait — см. _post (в запрос к Binotel он не попадает).

        Запрос, не дошедший до Binotel (connect_failed), и быстрый отказ с кодом 105 метод
        повторяет сам (ORIGINATE_*); исключение последней попытки уходит вызывающему как есть."""
        params = {
            "internalNumber": str(internal_number or "").strip(),
            "externalNumber": str(external_number or "").strip(),
        }
        if not params["internalNumber"] or not params["externalNumber"]:
            raise ValueError("internal_number и external_number обязательны")
        for key, value in extra.items():
            if value is not None:
                params[key] = value
        connect_left, busy_left, retries = ORIGINATE_CONNECT_RETRIES, ORIGINATE_105_RETRIES, 0
        while True:
            started = time.monotonic()
            try:
                payload = self._request("calls/internal-number-to-external-number", params, max_wait)
                break
            except RateLimited:
                raise
            except Exception as exc:
                if connect_failed(exc) and connect_left > 0:
                    connect_left -= 1
                    why = "нет соединения с Binotel (%s)" % type(exc).__name__
                elif (error_code(exc) == 105 and busy_left > 0
                      and time.monotonic() - started <= ORIGINATE_105_FAST_SEC):
                    busy_left -= 1
                    why = "Binotel ответил кодом 105"
                else:
                    raise
            pause = ORIGINATE_RETRY_PAUSES[min(retries, len(ORIGINATE_RETRY_PAUSES) - 1)]
            retries += 1
            # Номера в журнал не пишем: ни линии, ни водителя.
            log.warning("Binotel click-to-call: %s — повтор %d через %.0f с", why, retries, pause)
            time.sleep(pause)
        gid = payload.get("generalCallID") or payload.get("generalCallId")
        if gid in (None, ""):
            raise RuntimeError(f"Binotel: звонок принят, но generalCallID не вернулся: {str(payload)[:300]}")
        return str(gid)

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        """{generalCallID: нормализованный звонок} по одному или списку id.

        Пока разговор идёт, Binotel отдаёт disposition 'ONLINE'; пока звонок ещё
        не начался или уже не найден — элемент пуст и в ответ не попадает.
        Официальный пример опрашивает этот метод раз в 10 секунд, а частые опросы
        Binotel отвергает («too frequent»): фоновому опросу стоит передавать
        max_wait=0 и при RateLimited обходиться прежними данными, а несколько
        звонков спрашивать одним запросом — списком id. timeout — таймаут HTTP этого
        вызова (см. _post): опросу живых звонков — короткий."""
        if isinstance(general_call_ids, (list, tuple, set)):
            ids = [str(x).strip() for x in general_call_ids if str(x or "").strip()]
        else:
            ids = [str(general_call_ids or "").strip()] if str(general_call_ids or "").strip() else []
        if not ids:
            return {}
        payload = self._request("stats/call-details", {"generalCallID": ids if len(ids) > 1 else ids[0]}, max_wait,
                                timeout)
        out = {}
        for raw in self._extract_call_details(payload):
            call = self._normalize_call(raw)
            if call:
                out[call["general_call_id"]] = call
        return out

    def hangup_call(self, general_call_id, max_wait=None):
        """Завершить звонок по generalCallID (calls/hangup-call)."""
        return self._request("calls/hangup-call", {"generalCallID": str(general_call_id).strip()}, max_wait)

    def format_dt(self, unix_ts):
        """unix -> 'dd.mm.YYYY HH:MM:SS' в TZ панели (совместимо с _parse_datetime_raw)."""
        try:
            return datetime.fromtimestamp(int(unix_ts), _tzinfo(self.tz)).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            return ""


def _day_bounds_unix(date_from, date_to, tz_name=DEFAULT_TZ):
    """['YYYY-MM-DD', 'YYYY-MM-DD'] -> (start_unix, stop_unix), включительно по дню."""
    tz = _tzinfo(tz_name)
    start = datetime.strptime(str(date_from).strip(), "%Y-%m-%d")
    end = datetime.strptime(str(date_to).strip(), "%Y-%m-%d")
    if end < start:
        start, end = end, start
    start_dt = start.replace(hour=0, minute=0, second=0, tzinfo=tz)
    end_dt = end.replace(hour=23, minute=59, second=59, tzinfo=tz)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _main():
    parser = argparse.ArgumentParser(description="Проверка Binotel API 4.0 (звонки по internalNumber)")
    parser.add_argument("--internal", help="internalNumber (sip_number оператора)")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--stop", help="YYYY-MM-DD")
    parser.add_argument("--record", help="generalCallID: получить ссылку на запись")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = get_config()
    if not api_ready(cfg):
        raise SystemExit("TEZ_BINOTEL_API_KEY/SECRET не заданы (env или .env.codex.local)")
    client = BinotelApiClient.from_config(cfg)

    if args.record:
        print("record url:", client.get_call_record_url(args.record))
        return

    if not (args.internal and args.start and args.stop):
        raise SystemExit("Нужны --internal, --start, --stop (или --record <generalCallID>)")
    start_ts, stop_ts = _day_bounds_unix(args.start, args.stop, cfg["tz"])
    calls = client.list_calls_by_internal_number(args.internal, start_ts, stop_ts)
    logging.info("Получено звонков: %d", len(calls))
    for c in calls[:20]:
        c = dict(c)
        c["dt"] = client.format_dt(c["start_time"])
        print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    _main()
