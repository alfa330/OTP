# -*- coding: utf-8 -*-
"""Сторож линий Binotel: раз в минуту сверяет телефон оператора с кабинетом и линией Binotel,
а по вебхуку «API Call Completed» отмечает звонки, которые очередь или группа не отдали
готовому оператору. Всё найденное ложится в журнал binotel_line_watch (journal.py).

Сторож живёт своим потоком: в общий пул бота (4 места) не ходит и запросы людей не держит.
Вебхук только кладёт звонок в очередь разбора — ответ Binotel не ждёт ни базы, ни сети.

Починка (запись presence в кабинет учёткой самого сотрудника) выключена, пока вид расхождения
не назван в heal_kinds: журнал сначала показывает, что сторож узнаёт случай верно.
"""

import collections
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from . import binotel, rules
from .journal import Journal

log = logging.getLogger(__name__)

TICK_SECONDS = 60
# Открытый случай дописываем не каждую минуту: строка одна, а запись — раз в столько секунд.
TOUCH_SECONDS = 300
# Состояние линий годится для разбора звонка столько секунд, старше — читаем заново.
LINES_FRESH_SECONDS = 90
# Звонок из вебхука разбираем с задержкой: событие телефона о том же моменте должно доехать.
CALL_SETTLE_SECONDS = 6
CALLS_QUEUE_MAX = 300
# Оператор «готов» не меньше стольких секунд до плеча — иначе это его только что кончившийся звонок.
READY_MARGIN_SECONDS = 5
# Починка: не чаще раза в столько секунд на случай и не больше стольких попыток.
HEAL_RETRY_SECONDS = 600
HEAL_MAX_TRIES = 3
# Отказ — кабинет не принял учётку сотрудника или её нет вовсе: с теми же данными выйдет то же
# самое, а каждая попытка — это неудачный вход в чужой кабинет. Повтор не раньше чем через
# столько секунд, и перезапуск сервера этот срок не обнуляет (момент отказа лежит в журнале).
HEAL_DENIED_RETRY_SECONDS = 6 * 3600
DENIED_PREFIX = 'отказ: '
# Признак «человек работает» для возврата «Активен»: звонок по линии или событие телефона не
# старше стольких секунд. Телефон, забытый включённым на спящем компьютере, сам шлёт «готов» при
# пробуждении — без этого признака сторож вернул бы в очередь пустое рабочее место.
ALIVE_SECONDS = 900
CLEANUP_EVERY_SECONDS = 24 * 3600
START_LOOKBACK_SECONDS = 30 * 24 * 3600
ERROR_LOG_EVERY_SECONDS = 600
SIP_REGISTERED = ('online', 'inuse')


def _local(ts):
    """Момент в часовом поясе операторов — так его ждёт db.get_operator_live_statuses(as_of=…)."""
    if ZoneInfo is not None:
        try:
            return datetime.fromtimestamp(float(ts), ZoneInfo('Asia/Almaty'))
        except Exception:  # noqa: BLE001 — нет базы поясов: ниже то же смещение числом
            pass
    return datetime.fromtimestamp(float(ts), timezone(timedelta(hours=5)))


def heal_plan(kind, snap):
    """Что сторож поставил бы в кабинет для этого случая: presence или None.

    Запись presence чинит только расхождение «кабинет ≠ телефон», и только когда видно, что
    телефон на этой линии сейчас и есть рабочее место человека:
      * статус остался после выхода — линии в сети нет, телефон молчит: «Неактивен»;
      * кабинет закрыл работающего — линия в сети и по ней недавно шёл звонок либо оператор
        недавно сам менял статус: «Активен»;
      * кабинет «Активен» у закрытого телефона — линия в сети: то, что выбрано в телефоне."""
    target = rules.heal_target(kind, snap.get('phone'))
    if target is None or snap.get('operator_id') is None:
        return None
    sip = snap.get('sip')
    if kind == rules.KIND_CABINET_STALE:
        return target if sip == rules.SIP_OFFLINE else None
    if sip not in SIP_REGISTERED:
        return None
    if kind == rules.KIND_CABINET_CLOSED:
        recent = [a for a in (snap.get('inuse_age'), snap.get('phone_age')) if a is not None]
        return target if recent and min(recent) <= ALIVE_SECONDS else None
    return target


class LineWatchService(object):
    """roster() — состав: [{'id', 'sip_number', 'direction': 'tp' | 'op'}];
    read_lines() — состояние линий Binotel (binotel.read_lines);
    live_statuses(ids, as_of=None) — последние события телефона (db.get_operator_live_statuses);
    account_for(user_id) — учётка кабинета сотрудника для починки (db.get_binotel_account);
    queue_states() — {линия: статус на странице очереди кабинета} для записи в событие звонка;
    heal_kinds — виды расхождений, которые сторож чинит сам (пусто — только журнал)."""

    def __init__(self, db, roster, read_lines, live_statuses=None, account_for=None,
                 queue_states=None, heal_kinds=(), now=None, interval=TICK_SECONDS,
                 presence_factory=None, journal=None):
        self.journal = journal if journal is not None else Journal(db)
        self._roster = roster
        self._read_lines = read_lines
        self._live_statuses = live_statuses if live_statuses is not None else db.get_operator_live_statuses
        self._account_for = account_for
        self._queue_states = queue_states
        self.heal_kinds = frozenset(k for k in (heal_kinds or ()) if k in rules.STATE_KINDS)
        self._now = now or time.time
        self.interval = max(15, int(interval or TICK_SECONDS))
        self._presence_factory = presence_factory or binotel.CabinetPresence
        self._lock = threading.Lock()
        self._open = None                 # {(линия, вид): {'id', 'written', 'tries', 'heal_at'}}
        self._lines = {}
        self._lines_at = 0.0
        self._calls = collections.deque(maxlen=CALLS_QUEUE_MAX)
        self._wake = threading.Event()
        self._ready = False
        self._cleaned_at = 0.0
        self._error_logged = {}

    # ── служебное ───────────────────────────────────────────────────────────
    def _journal_ready(self):
        if self._ready:
            return True
        try:
            self._ready = bool(self.journal.ready())
        except Exception:  # noqa: BLE001 — база недоступна: сторож подождёт следующего шага
            self._complain('journal', 'журнал сторожа линий недоступен')
            return False
        if not self._ready:
            self._complain('journal', 'таблицы журнала сторожа линий нет — сверка не ведётся', exc=False)
        return self._ready

    def _complain(self, key, text, exc=True):
        """Одна и та же беда — в журнал сервера не чаще раза в десять минут."""
        now = self._now()
        if now - self._error_logged.get(key, -ERROR_LOG_EVERY_SECONDS) < ERROR_LOG_EVERY_SECONDS:
            return
        self._error_logged[key] = now
        if exc:
            log.exception('line_watch: %s', text)
        else:
            log.warning('line_watch: %s', text)

    def _people(self):
        out = []
        for person in self._roster() or []:
            line = str(person.get('sip_number') or '').strip()
            if not line or person.get('id') is None:
                continue
            out.append({'id': int(person['id']), 'line': line,
                        'direction': person.get('direction') or None})
        return out

    @staticmethod
    def _age(now, moment):
        return None if not moment else max(0, int(now - moment))

    def _snapshot(self, now, person, live, state):
        phone_age = live.get('seconds') if live else None
        presence_age = self._age(now, state.get('presence_at'))
        ages = [a for a in (phone_age, presence_age) if a is not None]
        # Расхождение началось, когда изменилась та сторона, что менялась последней.
        started = now - min(min(ages), START_LOOKBACK_SECONDS) if ages else now
        return {
            'operator_id': person['id'] if person else None,
            'direction': person['direction'] if person else None,
            'phone': (live or {}).get('status_key'),
            'phone_age': phone_age,
            'presence': state.get('presence'),
            'presence_age': presence_age,
            'sip': state.get('sip') or None,
            'sip_age': self._age(now, state.get('sip_at')),
            'inuse_age': self._age(now, state.get('inuse_at')),
            'started': started,
        }

    def _details(self, kind, snap):
        details = {k: snap[k] for k in ('phone_age', 'presence_age', 'sip_age', 'inuse_age')
                   if snap.get(k) is not None}
        # Что сторож поставил бы в кабинет — пишем и когда починка выключена: по этим строкам
        # видно, верно ли он узнаёт случай, до того как ему дадут право писать.
        plan = heal_plan(kind, snap)
        if plan is not None:
            details['would_set'] = plan
        return details

    # ── сверка состояния ────────────────────────────────────────────────────
    def tick(self):
        """Один шаг сверки. Возвращает найденные расхождения {(линия, вид): снимок} или None."""
        if not self._journal_ready():
            return None
        now = self._now()
        people = self._people()
        lines = self._read_lines() or {}
        with self._lock:
            self._lines, self._lines_at = lines, now
        statuses = (self._live_statuses([p['id'] for p in people]) or {}) if people else {}
        found = {}
        staffed = set()
        for person in people:
            staffed.add(person['line'])
            state = lines.get(person['line'])
            if not state:
                continue
            live = statuses.get(person['id']) or {}
            kind = rules.classify(live.get('status_key'), live.get('seconds'), state.get('presence'),
                                  self._age(now, state.get('presence_at')), state.get('sip'),
                                  self._age(now, state.get('sip_at')))
            if kind:
                found[(person['line'], kind)] = self._snapshot(now, person, live, state)
        # Линия без работающего сотрудника (уволен, не заведён): телефона у неё нет, а статус в
        # кабинете мог остаться открытым.
        for line, state in lines.items():
            if line in staffed:
                continue
            kind = rules.classify(None, None, state.get('presence'),
                                  self._age(now, state.get('presence_at')), state.get('sip'),
                                  self._age(now, state.get('sip_at')))
            if kind:
                found[(line, kind)] = self._snapshot(now, None, {}, state)
        self._sync(found, now)
        self._heal(found, now)
        if now - self._cleaned_at >= CLEANUP_EVERY_SECONDS:
            self._cleaned_at = now
            self.journal.cleanup()
        return found

    def _sync(self, found, now):
        if self._open is None:
            self._open = {}
            for row in self.journal.load_open():
                row_id, line, kind = row[0], row[1], row[2]
                heal_at = float(row[3]) if len(row) > 3 and row[3] else 0.0
                denied = len(row) > 4 and str(row[4] or '').startswith(DENIED_PREFIX)
                self._open[(line, kind)] = {'id': row_id, 'written': 0.0, 'tries': 0, 'heal_at': heal_at,
                                            'denied_at': heal_at if denied else 0.0}
        for key, snap in found.items():
            line, kind = key
            entry = self._open.get(key)
            if entry is None:
                row_id = self.journal.open(kind, line, snap, self._details(kind, snap), snap['started'], now)
                self._open[key] = {'id': row_id, 'written': now, 'tries': 0, 'heal_at': 0.0,
                                   'denied_at': 0.0}
                log.info('line_watch: линия %s — %s (телефон «%s», кабинет «%s», линия %s)',
                         line, kind, snap['phone'] or '—', snap['presence'], snap['sip'] or '?')
            elif now - entry['written'] >= TOUCH_SECONDS:
                self.journal.touch(entry['id'], snap, self._details(kind, snap), now)
                entry['written'] = now
        for key in [k for k in self._open if k not in found]:
            entry = self._open.pop(key)
            self.journal.close(entry['id'], now)
            log.info('line_watch: линия %s — %s кончилось', key[0], key[1])

    # ── починка ─────────────────────────────────────────────────────────────
    def _heal(self, found, now):
        if not self.heal_kinds or self._account_for is None:
            return
        for key, snap in found.items():
            line, kind = key
            if kind not in self.heal_kinds:
                continue
            entry = (self._open or {}).get(key)
            if (not entry or entry['tries'] >= HEAL_MAX_TRIES
                    or (entry['tries'] and now - entry['heal_at'] < HEAL_RETRY_SECONDS)
                    or (entry['denied_at'] and now - entry['denied_at'] < HEAL_DENIED_RETRY_SECONDS)):
                continue
            target = heal_plan(kind, snap)
            if target is None:
                continue
            entry['tries'] += 1
            entry['heal_at'] = now
            result = self.apply_presence(snap['operator_id'], target)
            if result.startswith(DENIED_PREFIX):
                entry['denied_at'] = now
            self.journal.heal(entry['id'], 'set:%s' % target, result, now)
            log.info('line_watch: линия %s — %s, в кабинет ставим «%s»: %s', line, kind, target, result)

    def apply_presence(self, operator_id, presence):
        """Записать presence в кабинет учёткой сотрудника. Возвращает итог словами — для журнала."""
        try:
            account = self._account_for(operator_id) if self._account_for else None
            if not account or not account.get('cabinet_login') or not account.get('cabinet_password'):
                return DENIED_PREFIX + 'учётки кабинета у сотрудника нет'
            cabinet = self._presence_factory(account.get('cabinet_url'), account.get('cabinet_login'),
                                             account.get('cabinet_password'))
            return 'записано' if cabinet.set(presence) else 'уже стояло'
        except binotel.CabinetDenied as exc:
            return DENIED_PREFIX + str(exc)
        except binotel.CabinetError as exc:
            return 'не вышло: %s' % exc
        except Exception as exc:  # noqa: BLE001 — починка не должна ронять сверку
            log.exception('line_watch: запись presence сотруднику %s упала', operator_id)
            return 'не вышло: %s' % type(exc).__name__

    # ── звонки из вебхука ───────────────────────────────────────────────────
    def on_binotel_call(self, fields, matched=False):
        """Наблюдатель вебхука «API Call Completed» (dial_list.service.call_observers).

        Здесь только разбор полей и постановка в очередь: базу и Binotel трогает поток сторожа."""
        if matched:
            return None                      # попытка обзвона — не входящий на линию
        call = rules.call_from_webhook(fields)
        if call is None or not call['incoming']:
            return None
        held = rules.queue_wait(call)
        interesting = (
            (held is not None and held[1] >= rules.QUEUE_HELD_SECONDS)
            or call['disposition'] in rules.DROPPED_DISPOSITIONS
            or any(leg['disposition'] in rules.FAILED_LEG_DISPOSITIONS
                   and leg['waitsec'] >= rules.NO_PICKUP_SECONDS for leg in call['legs'])
        )
        if not interesting:
            return None
        call['queued_at'] = self._now()
        self._calls.append(call)
        self._wake.set()
        return True

    def _fresh_lines(self):
        now = self._now()
        with self._lock:
            if self._lines and now - self._lines_at <= LINES_FRESH_SECONDS:
                return self._lines
        lines = self._read_lines() or {}
        with self._lock:
            self._lines, self._lines_at = lines, now
        return lines

    def _ready_at(self, people, moment, margin):
        """Кто из людей был «готов» в этот момент не меньше margin секунд: {id: секунд в статусе}."""
        if not people:
            return {}
        statuses = self._live_statuses([p['id'] for p in people], as_of=_local(moment)) or {}
        out = {}
        for person in people:
            live = statuses.get(person['id']) or {}
            seconds = live.get('seconds')
            if live.get('status_key') == rules.PHONE_READY and seconds is not None and seconds >= margin:
                out[person['id']] = seconds
        return out

    def drain_calls(self):
        """Разобрать звонки, отстоявшиеся CALL_SETTLE_SECONDS. Возвращает число записанных событий."""
        saved = 0
        while self._calls:
            if self._now() - self._calls[0].get('queued_at', 0) < CALL_SETTLE_SECONDS:
                break
            call = self._calls.popleft()
            try:
                saved += self.handle_call(call)
            except Exception:  # noqa: BLE001 — один звонок не должен остановить разбор остальных
                self._complain('call', 'разбор звонка из вебхука упал')
        return saved

    def handle_call(self, call):
        """События одного звонка: кому из готовых операторов он не достался. Возвращает число записей."""
        if not self._journal_ready():
            return 0
        people = self._people()
        by_line = {p['line']: p for p in people}
        events = []
        holder = {}

        def state_of(line):
            if 'lines' not in holder:
                holder['lines'] = self._fresh_lines()
            return holder['lines'].get(line) or {}

        def event(kind, line, person, start, end, details):
            state = state_of(line) if person else {}
            events.append({
                'kind': kind, 'line': line,
                'operator_id': person['id'] if person else None,
                'direction': person['direction'] if person else 'op',
                'start': start, 'end': end, 'phone': rules.PHONE_READY,
                'presence': state.get('presence'), 'sip': state.get('sip') or None,
                'details': details,
            })

        held = rules.queue_wait(call)
        if held is not None and held[1] >= rules.QUEUE_HELD_SECONDS:
            start, seconds = held
            queue_people = [p for p in people if p['direction'] == 'tp']
            ready = self._ready_at(queue_people, start + seconds, seconds + READY_MARGIN_SECONDS)
            for person in queue_people:
                if person['id'] not in ready:
                    continue
                state = state_of(person['line'])
                # «Активен» в кабинете всё ожидание: presence не менялся после входа звонка в очередь.
                if state.get('presence') != rules.PRESENCE_ACTIVE:
                    continue
                if state.get('presence_at') and state['presence_at'] > start:
                    continue
                event(rules.KIND_QUEUE_HELD, person['line'], person, start, start + seconds, {
                    'wait': seconds, 'call_disposition': call['disposition'],
                    'ready_seconds': ready[person['id']]})

        for line, at, ring, disposition in rules.failed_operator_legs(call, by_line.keys()):
            person = by_line[line]
            if person['id'] in self._ready_at([person], at + 1, READY_MARGIN_SECONDS):
                event(rules.KIND_NO_PICKUP, line, person, at, at + ring, {
                    'ring': ring, 'leg_disposition': disposition,
                    'call_disposition': call['disposition']})

        dropped = rules.group_drop(call, by_line.keys())
        if dropped is not None:
            at, group = dropped
            group_people = [p for p in people if p['direction'] == 'op']
            ready = self._ready_at(group_people, at + 1, READY_MARGIN_SECONDS)
            who = []
            for person in group_people:
                state = state_of(person['line']) if person['id'] in ready else {}
                if state.get('presence') == rules.PRESENCE_ACTIVE:
                    who.append({'line': person['line'], 'operator_id': person['id'],
                                'sip': state.get('sip') or None})
            if who:
                event(rules.KIND_GROUP_DROP, group, None, at, at, {
                    'call_disposition': call['disposition'], 'ready': who})
        if not events:
            return 0

        if self._queue_states is not None and any(e['kind'] == rules.KIND_QUEUE_HELD for e in events):
            # Что в эту минуту показывает сама очередь про оператора, которому она не позвонила:
            # ради этой строки событие и пишется.
            try:
                queue_view = self._queue_states() or {}
            except Exception:  # noqa: BLE001 — без страницы очереди событие всё равно пишем
                queue_view = None
                self._complain('queue', 'страница очереди кабинета не прочиталась')
            if queue_view is not None:
                for item in events:
                    if item['kind'] == rules.KIND_QUEUE_HELD:
                        item['details']['queue_status'] = queue_view.get(item['line'])
        saved = self.journal.add_events(call['general_call_id'], events)
        if saved:
            log.info('line_watch: звонок %s — %s', call['general_call_id'],
                     ', '.join('%s (линия %s)' % (e['kind'], e['line']) for e in events))
        return saved

    # ── поток ───────────────────────────────────────────────────────────────
    def run(self, stop=None):
        """Цикл сторожа: сверка раз в interval секунд, звонки из вебхука — по мере прихода."""
        next_tick = 0.0
        while not (stop is not None and stop.is_set()):
            if self._now() >= next_tick:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — Binotel или база не ответили: ждём следующий шаг
                    self._complain('tick', 'шаг сверки линий не удался')
                next_tick = self._now() + self.interval
            try:
                self.drain_calls()
            except Exception:  # noqa: BLE001
                self._complain('drain', 'разбор звонков из вебхука не удался')
            pause = max(1.0, next_tick - self._now())
            if self._calls:
                pause = min(pause, float(CALL_SETTLE_SECONDS))
            self._wake.wait(pause)
            self._wake.clear()
