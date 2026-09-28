# -*- coding: utf-8 -*-
"""Робот «пропущенные входящие → amoCRM» (задача #291): один цикл разбора.

Джоба в монолите зовёт `run_cycle` раз в пятнадцать секунд на своём пуле из одного потока
(`POOL`). Общий `executor_pool` бота — четыре места на всё приложение, а запрос в amoCRM
может висеть минуту; занять там место значило бы подвешивать чужие разделы.

Что делает цикл
---------------
1. Берёт непринятые входящие, по которым решения ещё нет (с момента включения робота и не
   старше HORIZON_HOURS).
2. Для каждого спрашивает `cdr.missed.decide`: ждать, клиент дозвонился, перезвонил и снова
   не дозвонился (решение переносится на последний звонок) — или передавать.
3. Передача: если у клиента уже есть наша сделка на перезвон, которую ещё не взяли в работу
   (этап «Новая заявка»), новой не заводим — к той дописываем примечание о повторном
   звонке. Иначе заводим сделку с тегом «Пропущенный входящий» и примечанием о звонке.
4. Повторяет упавшие передачи с растущей паузой; зависшие — сначала проверяет по amoCRM.

Порядок записи — «отметка до действия»: строка `sending` в журнале появляется ДО запроса
в amoCRM. Так перезапуск посреди запроса оставляет след, по которому повтор найдёт уже
заведённую сделку, а не заведёт вторую.
"""

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from cdr import lines as lines_mod, missed, missed_config, missed_queries as mq, queries
from cdr.missed_amo import LeadReadError

log = logging.getLogger(__name__)

# Один поток: циклы идут строго по очереди, и два цикла не разберут один звонок дважды.
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='cdr-missed-amo')

# Звонки старше этого робот не разбирает. После простоя (перезапуск, выкладка) он
# догоняет пропущенное за последние часы, но не заводит перезвоны на вчерашний вечер.
HORIZON_HOURS = 3

# Наша сделка по номеру, заведённая не раньше стольких суток назад и ещё стоящая в
# «Новой заявке», — повод не заводить вторую.
REPEAT_LOOKBACK_DAYS = 3

# Попыток передачи, после которых строка становится `failed` и видна в разделе.
MAX_ATTEMPTS = 5

# Сделок за цикл. Цикл раз в пятнадцать секунд, непринятых ~50 в сутки; потолок нужен на
# случай, когда после простоя накопилось, — чтобы один цикл не держал поток минутами.
MAX_TRANSFERS_PER_CYCLE = 10

_ALMATY = timezone(timedelta(hours=5))
_writer_state = {'writer': None}
_writer_lock = threading.Lock()


def is_enabled():
    from amocrm import leads as amo_leads
    return missed_config.enabled() and amo_leads.is_configured()


def _writer():
    """Писатель amoCRM на жизнь процесса: конструктор клиента логинится, и делать это
    каждые пятнадцать секунд незачем. Поток у пула один, но замок дешевле догадок."""
    with _writer_lock:
        if _writer_state['writer'] is None:
            from amocrm import leads as amo_leads
            from cdr.missed_amo import MissedCallWriter
            _writer_state['writer'] = MissedCallWriter(amo_leads.AmoClient())
        return _writer_state['writer']


def _local(value):
    """Отметка из базы (datetime с поясом или ISO-строка) → наивное время Алматы."""
    if value is None or value == '':
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(_ALMATY).replace(tzinfo=None)


def _queue_calls_of(calls, phone):
    """Звонки номера из снимка очередей: строки времени → datetime для decide."""
    if calls is None:
        return None
    out = []
    for call in calls:
        if not isinstance(call, dict) or call.get('phone') != phone:
            continue
        out.append({
            'linkedid': call.get('linkedid'),
            'queued_at': missed._moment(call.get('queued_at')),
            'answered_at': missed._moment(call.get('answered_at')),
            'ended': bool(call.get('ended')),
        })
    return out


def run_cycle(db, now=None, writer_factory=None):
    """Один проход. Возвращает счётчик исходов — джоба пишет его в лог, только когда он
    не пустой (цикл будит себя 5760 раз в сутки)."""
    now = now or queries.now_almaty()
    writer_factory = writer_factory or _writer
    summary = Counter()

    with db._get_cursor() as cursor:
        since = mq.ensure_state(cursor, now)
        edge = max(since, now - timedelta(hours=HORIZON_HOURS))
        todo = mq.candidates(cursor, edge)
        bridge = queries.agent_state(cursor)
        queue_calls, queue_at = queries.load_queue_calls(cursor)
        retries = mq.due_retries(cursor)
        by_phone = (mq.phone_touches(cursor, {c['phone'] for c in todo}, edge - timedelta(hours=1))
                    if todo else {})

    fresh_until = _local(bridge.get('live_at'))
    queue_until = _local(queue_at)
    context = _Context(db, writer_factory, now, summary)

    # Повторы — первыми: строка «в полёте» держит новые сделки того же клиента (см.
    # transfer), и чем раньше она разобрана, тем раньше они пойдут.
    for row in retries:
        try:
            context.retry(row)
        except Exception as exc:  # noqa: BLE001 — одна строка не должна ронять остальные
            summary['errors'] += 1
            context.error = str(exc)
            log.exception('Пропущенные→amo: повтор %s не удался', row.get('linkedid'))

    for touch in todo:
        decision = missed.decide(
            touch, by_phone.get(touch['phone'], ()), now, fresh_until,
            _queue_calls_of(queue_calls, touch['phone']), queue_until)
        if decision.kind == missed.WAIT:
            continue
        try:
            context.apply(touch, decision)
        except Exception as exc:  # noqa: BLE001 — один звонок не должен ронять остальные
            summary['errors'] += 1
            context.error = str(exc)
            log.exception('Пропущенные→amo: звонок %s не разобран', touch.get('linkedid'))

    if summary or context.error:
        with db._get_cursor() as cursor:
            mq.mark_run(cursor, error=context.error)
    return summary


# Что говорит проверка «у клиента уже есть наша сделка на перезвон».
_NO_OWN = 'none'
_DEFER = 'defer'

REPEAT_REASON = ('У клиента уже есть сделка на перезвон, её ещё не взяли в работу — '
                 'дописано примечание')
FAILED_TEXT = 'передать не удалось — перезвоните клиенту вручную'


class _Context(object):
    """Всё, что нужно одному циклу: база, писатель (заводится при первой передаче) и счётчики."""

    def __init__(self, db, writer_factory, now, summary):
        self.db = db
        self._factory = writer_factory
        self._writer = None
        self._writer_failed = False
        self.now = now
        self.summary = summary
        self.error = None

    def writer(self):
        if self._writer is None and not self._writer_failed:
            try:
                self._writer = self._factory()
            except Exception as exc:  # noqa: BLE001 — amoCRM недоступна: звонки подождут
                self._writer_failed = True
                self.error = 'amoCRM недоступна: %s' % exc
                log.warning('Пропущенные→amo: вход в amoCRM не удался: %s', exc)
        return self._writer

    def _since_repeat(self):
        return self.now - timedelta(days=REPEAT_LOOKBACK_DAYS)

    # ── решения ──────────────────────────────────────────────────────────────

    def apply(self, touch, decision):
        if decision.kind == missed.ANSWERED:
            with self.db._get_cursor() as cursor:
                if mq.insert_decision(cursor, touch, mq.ANSWERED, decision.reason,
                                      decision.ended_at):
                    mq.resolve_chain(cursor, touch['phone'], touch['started_at'],
                                     answered_reason=decision.reason)
                    self.summary['answered'] += 1
            return
        if decision.kind == missed.CHAINED:
            with self.db._get_cursor() as cursor:
                if mq.insert_decision(cursor, touch, mq.CHAINED, decision.reason,
                                      decision.ended_at, next_linkedid=decision.next_linkedid):
                    self.summary['chained'] += 1
            return
        if decision.kind == missed.TRANSFER:
            if self.summary['created'] + self.summary['repeat'] >= MAX_TRANSFERS_PER_CYCLE:
                return
            if self.writer() is None:
                # amoCRM не пускает. Звонок всё равно ложится в журнал ошибкой без попытки:
                # иначе при долгом сбое он молча выпал бы за горизонт, и перезвонить ему
                # руками никто бы не узнал. Повтор заберёт его, как только вход заработает.
                with self.db._get_cursor() as cursor:
                    if mq.insert_decision(cursor, touch, mq.ERROR, decision.reason,
                                          decision.ended_at, error=self.error):
                        self.summary['errors'] += 1
                return
            self.transfer(touch, decision)

    def _own_open_lead(self, phone, linkedid, started):
        """Наша сделка по номеру, ещё стоящая в «Новой заявке» (её id), _NO_OWN — такой нет,
        _DEFER — сейчас не узнать, и решать нельзя.

        «Не узнать» — это и amoCRM, не ответившая про сделку, и наша же строка по номеру,
        ещё не разобранная повтором: по ней сделка могла появиться, а ответ — потеряться."""
        writer = self.writer()
        with self.db._get_cursor() as cursor:
            in_flight = mq.pending_rows(cursor, phone, self._since_repeat(), linkedid, started)
            own = mq.latest_own_lead(cursor, phone, self._since_repeat())
        if in_flight:
            return _DEFER
        if not own:
            return _NO_OWN
        try:
            state = writer.lead_state(own[0])
        except LeadReadError as exc:
            log.info('Пропущенные→amo: решение по %s отложено: %s', linkedid, exc)
            return _DEFER
        if state == (missed_config.PIPELINE_ID, missed_config.STATUS_ID):
            return own[0]
        return _NO_OWN

    def transfer(self, touch, decision):
        writer = self.writer()
        linkedid, phone = str(touch['linkedid']), str(touch['phone'])
        park = lines_mod.park_of_queue(touch.get('queue'))

        own = self._own_open_lead(phone, linkedid, touch['started_at'])
        if own == _DEFER:
            self.summary['deferred'] += 1
            return
        if own != _NO_OWN:
            with self.db._get_cursor() as cursor:
                inserted = mq.insert_decision(cursor, touch, mq.REPEAT, REPEAT_REASON,
                                              decision.ended_at, amo_lead_id=own)
                if inserted:
                    mq.resolve_chain(cursor, phone, touch['started_at'], lead_id=own)
            if inserted:
                writer.add_note(own, missed.note_text(touch, park, repeat=True))
                self.summary['repeat'] += 1
            return

        with self.db._get_cursor() as cursor:
            claimed = mq.insert_decision(cursor, touch, mq.SENDING, decision.reason,
                                         decision.ended_at)
        if not claimed:
            return
        self._create(touch, park)

    def _create(self, touch, park):
        writer = self.writer()
        linkedid, phone = str(touch['linkedid']), str(touch['phone'])
        try:
            contact_id = writer.find_contact_id(phone)
            lead_id, contact_id = writer.create_missed_lead(phone, contact_id=contact_id)
        except Exception as exc:  # noqa: BLE001 — AmoWriteError, вход, сеть: всё в журнал
            with self.db._get_cursor() as cursor:
                mq.mark_error(cursor, linkedid, phone, exc)
            self.summary['errors'] += 1
            self.error = str(exc)
            log.warning('Пропущенные→amo: сделка по %s не заведена: %s', linkedid, exc)
            return
        with self.db._get_cursor() as cursor:
            mq.mark_created(cursor, linkedid, phone, lead_id, contact_id)
            mq.resolve_chain(cursor, phone, touch['started_at'], lead_id=lead_id)
        writer.add_note(lead_id, missed.note_text(touch, park))
        self.summary['created'] += 1

    # ── повторы ──────────────────────────────────────────────────────────────

    def _fail(self, linkedid, phone):
        with self.db._get_cursor() as cursor:
            mq.mark_error(cursor, linkedid, phone, FAILED_TEXT, final=True)
        self.summary['failed'] += 1

    def retry(self, row):
        """Строка `error` или зависшая `sending`: сначала выяснить, не заведена ли уже сделка,
        потом — нет ли у клиента нашей сделки по другому звонку, и только потом повторять."""
        linkedid, phone = str(row['linkedid']), str(row['phone'])
        started = missed._moment(row.get('started_at'))
        too_old = started is None or started < self.now - timedelta(hours=HORIZON_HOURS)
        exhausted = int(row.get('attempts') or 0) >= MAX_ATTEMPTS
        park = lines_mod.park_of_queue(row.get('queue'))
        writer = self.writer()

        # Запрос по строке уходил — он мог дойти до amoCRM, а ответ потеряться. Ищем
        # заведённую сделку ДО того, как объявить строку проваленной или повторять:
        # иначе клиенту перезвонили бы дважды, по сделке и «вручную». Не смогли спросить —
        # вслепую не повторяем; строку за горизонтом закрываем, чтобы не висела вечно.
        sent = int(row.get('sent_epoch') or 0)
        if sent:
            if writer is None:
                if too_old:
                    self._fail(linkedid, phone)
                return
            try:
                existing = writer.recent_own_lead(phone, sent - 120)
            except Exception as exc:  # noqa: BLE001
                log.warning('Пропущенные→amo: проверка перед повтором %s не удалась: %s',
                            linkedid, exc)
                if too_old:
                    self._fail(linkedid, phone)
                return
            if existing:
                with self.db._get_cursor() as cursor:
                    mq.mark_created(cursor, linkedid, phone, existing)
                    if started is not None:
                        mq.resolve_chain(cursor, phone, started, lead_id=existing)
                writer.add_note(existing, missed.note_text(row, park))
                self.summary['recovered'] += 1
                return

        if exhausted or too_old:
            self._fail(linkedid, phone)
            return
        if writer is None:
            return

        # Пока строка ждала, клиенту могла появиться наша сделка по другому звонку.
        own = self._own_open_lead(phone, linkedid, row['started_at'])
        if own == _DEFER:
            self.summary['deferred'] += 1
            return
        if own != _NO_OWN:
            with self.db._get_cursor() as cursor:
                mq.mark_repeat(cursor, linkedid, phone, own, REPEAT_REASON)
                if started is not None:
                    mq.resolve_chain(cursor, phone, started, lead_id=own)
            writer.add_note(own, missed.note_text(row, park, repeat=True))
            self.summary['repeat'] += 1
            return

        with self.db._get_cursor() as cursor:
            attempt = mq.reclaim(cursor, linkedid, phone, row.get('status'), row.get('attempts'))
        if attempt is None:
            return
        self._create(row, park)
