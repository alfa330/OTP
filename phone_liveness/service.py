# -*- coding: utf-8 -*-
"""Обход пульса телефона: раз в две минуты закрывает статусы молчащих телефонов и продолжает
статус тех, кто вернулся. Свой поток: в общий пул бота (4 места) не ходит.

Событие пишется штатным db.append_operator_status_event — с тем же замком оператора, пересборкой
отрезков и пересчётом часов, что и у события самого телефона. Закрытие — «выключен» с пометкой
«нет связи с телефоном», возврат — прежний статус с пометкой «связь восстановлена»: в ленте
статусов видно, что это сделал сервер, а не оператор.

Возврат ставится через секунду после закрытия, то есть закрытие отменяется: телефон, вернувшийся
без нового события статуса, всё это время работал, пропадала только связь. Выключенный телефон
при запуске сам присылает стартовый статус, и его закрытие остаётся как есть.
"""
import logging
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from . import rules, store

log = logging.getLogger(__name__)

SWEEP_SECONDS = 120
# db.rebuild_operator_status_days берёт не больше двух недель за вызов.
REBUILD_CHUNK_DAYS = 14
ERROR_LOG_EVERY_SECONDS = 1800


def almaty_now():
    """Наивное «сейчас» по Алмате — в этих часах лежат и события статусов, и активность сессий."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
        except Exception:  # noqa: BLE001 — нет базы поясов: ниже то же смещение числом
            pass
    return datetime.utcnow() + timedelta(hours=5)


class PhoneLivenessService(object):
    """db — база (нужны _get_cursor и append_operator_status_event);
    department_codes — коды отделов, за чьими телефонами следим ('*' — все);
    rebuild_days(operator_id, с, по) — пересобрать дни после давнего закрытия (необязателен)."""

    def __init__(self, db, department_codes, append_event=None, rebuild_days=None, now=None,
                 monotonic=None, interval=SWEEP_SECONDS, silence_seconds=rules.SILENCE_SECONDS):
        self.db = db
        self.department_codes = [str(code).strip().lower() for code in (department_codes or [])
                                 if str(code).strip()]
        self._append = append_event or db.append_operator_status_event
        self._rebuild = rebuild_days or getattr(db, 'rebuild_operator_status_days', None)
        self._now = now or almaty_now
        self._monotonic = monotonic or time.monotonic
        self._started = self._monotonic()
        self.interval = max(30, int(interval or SWEEP_SECONDS))
        self.silence_seconds = max(600, int(silence_seconds or rules.SILENCE_SECONDS))
        self._error_logged = {}

    def _complain(self, key, text, exc=False):
        """Одна и та же беда — в журнал сервера не чаще раза в полчаса."""
        mark = self._monotonic()
        if mark - self._error_logged.get(key, -ERROR_LOG_EVERY_SECONDS) < ERROR_LOG_EVERY_SECONDS:
            return
        self._error_logged[key] = mark
        if exc:
            log.exception('phone_liveness: %s', text)
        else:
            log.warning('phone_liveness: %s', text)

    def sweep(self):
        """Один обход. Возвращает {'closed': n, 'restored': n, 'held': причина или None}."""
        if not self.department_codes:
            return {'closed': 0, 'restored': 0, 'held': None}
        now = self._now()
        with self.db._get_cursor() as cursor:
            rows = store.load_rows(cursor, self.department_codes, now)
            freshest = store.freshest_pulse(cursor, now)
        decision = rules.plan(rows, now, freshest, self._monotonic() - self._started, self.silence_seconds)
        if decision['held'] == 'pulse_stale':
            self._complain('pulse_stale', 'активность сессий телефонов не обновляется — '
                           'молчащие телефоны не закрываем (ждут: %d)' % decision['candidates'])
        elif decision['held'] == 'mass_silence':
            self._complain('mass_silence', 'молчит слишком много телефонов разом (%d) — похоже на сбой, '
                           'статусы не закрываем' % decision['candidates'])
        closed = sum(1 for row in decision['close'] if self._close(row, now))
        restored = sum(1 for row in decision['restore'] if self._restore(row))
        return {'closed': closed, 'restored': restored, 'held': decision['held']}

    def _close(self, row, now):
        moment = rules.close_moment(row, now)
        try:
            result = self._append(operator_id=row['operator_id'], event_at=moment,
                                  status_key=rules.OFF_KEY, state_note=rules.CLOSE_NOTE,
                                  client_event_id=rules.close_event_id(row)) or {}
        except Exception:  # noqa: BLE001 — один оператор не должен остановить обход
            self._complain('close', 'статус оператора %s закрыть не удалось' % row['operator_id'], exc=True)
            return False
        if result.get('duplicate') or result.get('noop'):
            return False
        log.info('phone_liveness: оператор %s — «%s» закрыт моментом %s: телефон молчит с %s',
                 row['operator_id'], row['key'], moment.strftime('%d.%m %H:%M'),
                 rules.last_sign(row).strftime('%d.%m %H:%M'))
        self._rebuild_after(row['operator_id'], moment)
        return True

    def _restore(self, row):
        try:
            with self.db._get_cursor() as cursor:
                key = store.previous_status(cursor, row)
            if not key:
                return False
            moment = rules.restore_moment(row)
            result = self._append(operator_id=row['operator_id'], event_at=moment, status_key=key,
                                  state_note=rules.RESTORE_NOTE,
                                  client_event_id=rules.restore_event_id(row)) or {}
        except Exception:  # noqa: BLE001
            self._complain('restore', 'статус оператора %s продолжить не удалось' % row['operator_id'],
                           exc=True)
            return False
        if result.get('duplicate') or result.get('noop'):
            return False
        log.info('phone_liveness: оператор %s — телефон снова на связи с %s и статус не менял: '
                 '«%s» продолжен с момента закрытия %s',
                 row['operator_id'], row['pulse'].strftime('%d.%m %H:%M'), key, moment.strftime('%d.%m %H:%M'))
        self._rebuild_after(row['operator_id'], moment)
        return True

    def _rebuild_after(self, operator_id, moment, until=None):
        """Событие само пересобирает свой день и соседние; если оно легло в прошлое дальше, дни
        после них остались с прежней картиной — пересобираем их до сегодняшнего (или до until).
        Пересборка идёт кусками по две недели: дольше держать замок оператора нельзя."""
        first, last = moment.date() + timedelta(days=2), (until or self._now()).date()
        if self._rebuild is None or first > last:
            return
        try:
            while first <= last:
                chunk_end = min(last, first + timedelta(days=REBUILD_CHUNK_DAYS - 1))
                self._rebuild(operator_id, first, chunk_end)
                first = chunk_end + timedelta(days=1)
        except Exception:  # noqa: BLE001
            self._complain('rebuild', 'дни после события у оператора %s не пересобраны' % operator_id, exc=True)

    def close_hang(self, operator_id, around):
        """Закрыть уже случившееся зависание — то, что обход не застал (телефон успели запустить
        снова, или отдел подключили к обходу позже). around — любой момент внутри зависшего
        отрезка, наивное время Алматы.

        Закрывает только настоящую тишину: между последним событием телефона до этого момента и
        следующим после должно пройти не меньше срока молчания, и события в этом промежутке
        отсутствуют по построению. Момент закрытия — тот же, что у обхода без пульса: последнее
        событие + 10 минут.

        Срезается всё до следующего события. Если телефон потом запустили в тот же статус, сервер
        этот запуск мог не сохранить (повтор статуса), и следующим событием окажется первое
        действие оператора после запуска — минуты между запуском и этим действием уйдут вместе
        с зависанием. Для зависаний на десятки часов это погрешность, но смотреть ленту перед
        закрытием — дело того, кто закрывает.
        Ошибки условий — ValueError с текстом для человека."""
        operator_id = int(operator_id)
        with self.db._get_cursor() as cursor:
            last, following = store.hang_window(cursor, operator_id, around)
        if last is None:
            raise ValueError('у сотрудника нет событий телефона до этого момента')
        if last['key'] == rules.OFF_KEY:
            raise ValueError('в этот момент статус уже «выключен» — закрывать нечего')
        now = self._now()
        end = following['event_at'] if following else now
        silent = (end - last['event_at']).total_seconds()
        if silent < self.silence_seconds + rules.CLOSE_LAG_SECONDS:
            raise ValueError('между событиями телефона %d мин — это не зависание' % (silent // 60))
        row = {'operator_id': operator_id, 'event_id': last['event_id'], 'key': last['key'], 'note': None,
               'event_at': last['event_at'], 'pulse': None, 'seen': None}
        moment = rules.close_moment(row, end - timedelta(seconds=1))
        result = self._append(operator_id=operator_id, event_at=moment, status_key=rules.OFF_KEY,
                              state_note=rules.CLOSE_NOTE, client_event_id=rules.close_event_id(row)) or {}
        if result.get('duplicate') or result.get('noop'):
            return {'closed': False, 'reason': 'это зависание уже закрыто'}
        self._rebuild_after(operator_id, moment, until=end + timedelta(days=1))
        log.info('phone_liveness: оператор %s — зависший «%s» закрыт вручную моментом %s (висел до %s)',
                 operator_id, last['key'], moment.strftime('%d.%m %H:%M'), end.strftime('%d.%m %H:%M'))
        return {'closed': True, 'status_key': last['key'], 'closed_at': moment.isoformat(sep=' '),
                'hang_until': end.isoformat(sep=' '),
                'hours_removed': round((end - moment).total_seconds() / 3600.0, 1)}

    def run(self, stop=None):
        """Цикл обхода. stop — threading.Event для остановки (в проде поток живёт с процессом)."""
        while not (stop is not None and stop.is_set()):
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 — база не ответила: ждём следующий обход
                self._complain('sweep', 'обход не удался', exc=True)
            if stop is not None:
                stop.wait(self.interval)
            else:
                time.sleep(self.interval)
