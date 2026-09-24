# -*- coding: utf-8 -*-
"""Живой хвост сегодняшних суток: касания на портале отстают от станции на секунды.

Зачем, если сутки и так приезжают
---------------------------------
Задание «сутки» портал выдаёт целиком: страница CDR за день — 2–5 тысяч строк, мегабайт
на проводе, и гонять его раз в двадцать секунд значило бы качать со станции полторы
тысячи мегабайт в сутки ради того, что за эти секунды не поменялось. Табло же живёт
секундами: «принято/потеряно за сегодня» на стене устаревает вместе с очередью.

Как устроено
------------
Мост держит строки сегодняшних суток в памяти. Раз в `LIVE_INTERVAL_SECONDS` он спрашивает
у станции ТОЛЬКО окно последних двух часов (сотни строк, десятки килобайт), вливает их в
память, склеивает касания заново тем же `cdr.touches` и досылает порталу лишь те, что
появились или изменились. Раз в десять минут — полный проход по дню: страховка от строки,
которую станция дописала задним числом позже двух часов (длинный разговор через перевод).

Откуда берутся точные ожидание и ответ
--------------------------------------
Кроме CDR хвост читает журнал очередей станции (`cdr_bridge/pbxdb.py`, таблица `queuelog`)
за то же окно и дописывает касаниям вход в очередь, момент ответа, ожидание, разговор и
сторону отбоя. До этого табло выводило их из длины автоинформатора и событий телефонов
iCORE, и аудит 17.09.2026 намерил из-за этого 3–5 п.п. лишнего SL. Журнал недоступен —
касания едут как раньше, без этих полей: точность важна, но не важнее данных.

Что он НИКОГДА не делает
------------------------
Не трогает у станции ничего, кроме `/freepbx/cdr` — того же пути, что читает суточное
задание, — и чтения журнала очередей одним запросом по индексу.
Не повторяет запрос после таймаута: станция низкоконкурентная, повтор только добавит ей
работы. Не роняет мост: любая ошибка здесь — строка в журнале и следующая попытка через
интервал, а после трёх подряд — пауза подольше. Не шлёт порталу то, что не изменилось, —
кроме пульса: раз в HEARTBEAT_SECONDS уходит пустое приращение, по которому портал видит,
что хвост жив. Табло ОП судит о свежести по live_at, и ночью, когда за час два звонка,
без пульса оно объявляло мост умершим при живом опросе станции каждые двадцать секунд.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta

from cdr import queue_facts as queue_facts_mod, touches as touches_mod
from cdr_bridge.station import StationError

log = logging.getLogger('cdr_bridge.live')

# Окно приращения. Строка CDR появляется в момент завершения плеча, а её calldate — это
# начало; двух часов хватает даже разговору с переводом и долгим ожиданием, а полный
# проход раз в десять минут подбирает всё, что старше.
OVERLAP_MINUTES = 120
FULL_REFRESH_EVERY = 30          # циклов; при интервале 20 с — раз в десять минут
BACKOFF_AFTER_FAILURES = 3       # подряд неудач, после которых пауза растёт
BACKOFF_SECONDS = 300

# Пульс при тишине. Портал ставит live_at только по присланному приращению, а табло ОП по
# его возрасту пишет «данные устарели» (порог на экране — две минуты, в отбивке — десять).
# Пустое приращение раз в минуту — двести байт, зато возраст на табло честный.
HEARTBEAT_SECONDS = 60

# Касание сравнивается по этим полям: прочее (URL записи) либо выводится из них, либо
# меняется вместе с ними.
_FINGERPRINT_FIELDS = ('started_at', 'answered_at', 'ext', 'call_type', 'result',
                       'talk_seconds', 'dial_seconds', 'queue', 'legs', 'recording_url',
                       # Точные поля журнала очередей: ответ приходит позже входа, и
                       # касание обязано доехать до портала второй раз, когда он известен.
                       'queued_at', 'wait_seconds', 'talk_measured_seconds', 'hangup_side',
                       # Номер линии: у заявки автообзвона он выводится из префикса
                       # набора, а префикс учится по всему дню — номер может появиться
                       # на следующем цикле, и касание обязано доехать ещё раз.
                       'line_number')

TOUCH_FIELDS = ('linkedid', 'phone', 'started_at', 'answered_at', 'ext', 'call_type', 'result',
                'talk_seconds', 'dial_seconds', 'queue', 'recording_url', 'legs',
                'queued_at', 'wait_seconds', 'talk_measured_seconds', 'hangup_side',
                'line_number')


def _row_key(row):
    """Строка CDR — плечо звонка. Одного uniqueid мало: станция отдаёт по строке на каждое
    направление того же канала, поэтому в ключ входит всё, что их различает."""
    return tuple(str(row.get(name) or '') for name in
                 ('uniqueid', 'linkedid', 'calldate', 'src', 'dst', 'dstchannel', 'disposition'))


def _parse_calldate(value):
    text = str(value or '')[:19].replace('T', ' ')
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _fmt(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S')


def _fingerprint(touch):
    return json.dumps([touch.get(name) for name in _FINGERPRINT_FIELDS],
                      ensure_ascii=False, sort_keys=True, default=str)


def _wire(touch):
    return {name: touch.get(name) for name in TOUCH_FIELDS}


class LiveTail:
    def __init__(self, post, station, interval_seconds, today=None, pbxdb=None):
        """post(path, payload) — отправка на портал (подписанная, как у моста);
        station — тот же Station, что читает сутки; pbxdb — журнал очередей станции
        (`cdr_bridge/pbxdb.py`), может отсутствовать; today — для тестов."""
        self._post = post
        self._station = station
        self._pbxdb = pbxdb
        self.interval = max(5, int(interval_seconds))
        self._today = today or (lambda: date.today())
        self.day = None
        self.rows = {}           # ключ плеча → строка CDR
        self.facts = {}          # callid → точные факты очереди (журнал станции)
        self.sent = {}           # (linkedid, phone) → отпечаток отправленного касания
        self.cycles = 0
        self.failures = 0
        self.next_due = 0.0
        self.last_push_at = None

    # ── расписание ───────────────────────────────────────────────────────────

    def seconds_until_due(self, now=None):
        now = time.time() if now is None else now
        return max(0.0, self.next_due - now)

    def maybe_step(self, now=None):
        """Шаг, если пора. Наружу не бросает никогда — мост важнее хвоста."""
        now = time.time() if now is None else now
        if now < self.next_due:
            return False
        try:
            self.step(now)
            self.failures = 0
            self.next_due = now + self.interval
        except StationError as exc:
            self._fail(now, 'станция: %s (%s)' % (exc, exc.code))
        except Exception as exc:  # noqa: BLE001
            self._fail(now, str(exc))
        return True

    def _fail(self, now, reason):
        self.failures += 1
        pause = BACKOFF_SECONDS if self.failures >= BACKOFF_AFTER_FAILURES else self.interval
        log.warning('Живой хвост: %s — следующая попытка через %d с', reason, pause)
        self.next_due = now + pause

    # ── работа ───────────────────────────────────────────────────────────────

    def _reset(self, day):
        self.day = day
        self.rows = {}
        self.facts = {}
        self.sent = {}
        self.cycles = 0

    def window(self):
        """(начало, конец, полный ли проход) временем. Хвост до 01:00 завтра — как у
        суточного задания: звонок, начатый в 23:59, собирается целиком."""
        day = self.day
        end = datetime.combine(day + timedelta(days=1), datetime.min.time()) + timedelta(hours=1)
        full = not self.rows or self.cycles % FULL_REFRESH_EVERY == 0
        start = datetime.combine(day, datetime.min.time())
        if not full:
            latest = max((m for m in (_parse_calldate(r.get('calldate')) for r in self.rows.values())
                          if m is not None), default=None)
            if latest is not None:
                start = max(start, latest - timedelta(minutes=OVERLAP_MINUTES))
        return start, end, full

    def _queue_facts(self, start, end):
        """Точные факты очередей за окно или None, если журнал сейчас недоступен.

        None и пустой словарь — разные вещи: пустой означает «в окне не было ни одного
        звонка в очередь», а None — «спросить не вышло». Перепутать их значит на полном
        проходе стереть накопленное и разослать порталу касания без входа в очередь."""
        if self._pbxdb is None or not getattr(self._pbxdb, 'enabled', False):
            return None
        try:
            return self._pbxdb.facts(start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning('Живой хвост: журнал очередей не прочитался: %s', exc)
            return None

    def step(self, now=None):
        now = time.time() if now is None else now
        today = self._today()
        if self.day != today:
            self._reset(today)
        start, end, full = self.window()
        from_dt, to_dt = _fmt(start), _fmt(end)
        fresh = list(self._station.iter_cdr(from_dt, to_dt))
        if full:
            self.rows = {}
        for row in fresh:
            if isinstance(row, dict):
                self.rows[_row_key(row)] = row
        self.cycles += 1
        # Журнал очередей — за то же окно, что и CDR: полный проход перечитывает день
        # целиком, приращение накапливается поверх (см. queue_facts.merge).
        facts = self._queue_facts(start, end)
        if facts is not None:
            self.facts = facts if full else queue_facts_mod.merge(self.facts, facts)

        built = queue_facts_mod.attach(touches_mod.build_touches(list(self.rows.values())),
                                       self.facts)
        day_text = self.day.isoformat()
        current = {}
        changed = []
        for touch in built:
            if str(touch.get('started_at') or '')[:10] != day_text:
                continue
            key = (touch['linkedid'], touch['phone'])
            fingerprint = _fingerprint(touch)
            current[key] = fingerprint
            if self.sent.get(key) != fingerprint:
                changed.append(_wire(touch))
        removed = [{'linkedid': k[0], 'phone': k[1]} for k in self.sent if k not in current]
        if not changed and not removed:
            if self.last_push_at is not None and now - self.last_push_at < HEARTBEAT_SECONDS:
                log.debug('Живой хвост: без изменений (%d строк, %d касаний)', len(fresh), len(current))
                return
            # Тишина дольше минуты — пульс: те же поля, пустые списки, флаг для журнала портала.
            self._post('live', {
                'day': day_text,
                'touches': [],
                'removed': [],
                'rows_seen': len(self.rows),
                'full_refresh': full,
                'heartbeat': True,
            })
            self.last_push_at = now
            log.debug('Живой хвост: пульс без изменений (%d строк, %d касаний)', len(fresh), len(current))
            return
        self._post('live', {
            'day': day_text,
            'touches': changed,
            'removed': removed,
            'rows_seen': len(self.rows),
            'full_refresh': full,
        })
        # Отправлено — значит принято: портал отвечает 200 только после записи.
        self.sent = current
        self.last_push_at = now
        log.info('Живой хвост %s: строк в окне %d, касаний за день %d, дослано %d, убрано %d%s',
                 day_text, len(fresh), len(current), len(changed), len(removed),
                 ' (полный проход)' if full else '')
