"""Новые сотрудники ОП и СЗоВ — в список окна Syntony сами (владелец, 20.11.2026:
«Сделай так, чтобы автоматом новые добавлялись»).

12.11.2026 список заполнили разом: все, кто тогда работал в ОП и СЗоВ, любая должность
(«Добавь автоматом всех кто есть в ОП и СЗоВ и активен»). Пришедшие позже в список не
попадали, и окно Syntony не открывалось им ни в портале, ни в CRM — так вышло с тремя
операторами «Поток 1», пришедшими 13–16.11. Теперь сторож раз в 15 минут делает то же, что
супер-админ кнопкой «Доступ»:

1. добавляет работающих сотрудников ОП и СЗоВ, которых нет в списке. Кого из списка хоть
   раз убирали — не возвращает: снятие — решение супер-админа. Почта — рабочая из карточки
   (users.email, личная сюда не попадает), если она годится и не занята другой строкой;
   иначе строка без почты;
2. строкам без почты подставляет рабочую почту, появившуюся в карточке позже, — только
   ПУСТЫМ строкам, вписанное руками не трогает: по почте Syntony свяжет оператора с
   сотрудником, без неё связи не будет;
3. записывает у Syntony всех из списка, о чьей связи она ещё не отвечала (ТЗ, задача 6):
   запрос ссылки «Чаты», ссылку не открывает; 409 operator_not_linked — ожидаемый ответ.

След выдач — те же строки grant / email, что у кнопки, с actor_name «Автоматически».

Без ключа Syntony сторож не работает: записать человека у неё нечем, а список ведётся ради
неё. Выключатель — SYNTONY_AUTOGRANT_ENABLED=false. Поток свой, не общий пул бота: запрос к
Syntony держит до 13 с. За прогон — не больше 10 человек на шаг и 60 с разговоров с Syntony,
остальные — в следующий прогон. Отказ подключения целиком (ключ, пауза, лимит) останавливает
прогон; кого Syntony не записала по другой причине, сторож 6 часов не трогает.
"""

import logging
import os
import threading
import time

from . import access, client, queries, rules, service

logger = logging.getLogger(__name__)

DEPARTMENT_CODES = ('op', 'szov')
ACTOR_NAME = 'Автоматически'
ENV_ENABLED = 'SYNTONY_AUTOGRANT_ENABLED'

INTERVAL_SECONDS = 15 * 60
FIRST_RUN_DELAY_SECONDS = 60
BATCH = 10
TALK_BUDGET_SECONDS = 60
PAUSE_SECONDS = 0.7          # Syntony: 60 ссылок в секунду; идём по одному с запасом
RETRY_AFTER_SECONDS = 6 * 3600
UNREGISTERED_SCAN = 200
# Ответы не про человека, а про подключение целиком: идти дальше незачем.
STOP_CODES = frozenset({
    'invalid_key', 'key_revoked', 'key_expired', 'insufficient_scope',
    'connection_inactive', 'embed_disabled', 'rate_limited',
})

_backoff = {}                # id учётки → time.monotonic(), до которого не просить запись
_backoff_lock = threading.Lock()


def is_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_ENABLED, 'true')).strip().lower() not in ('0', 'false', 'off', 'no')


def _grant_new(db, store, limit):
    with db._get_cursor() as cursor:
        people = store.new_staff(cursor, DEPARTMENT_CODES, limit)
    granted = []
    for person in people:
        try:
            with db._get_cursor() as cursor:
                email = person['card_email']
                if email and store.email_owner(cursor, email, person['id']):
                    email = ''      # почта уже у другой строки: одна почта — один сотрудник Syntony
                if store.grant(cursor, person['id'], email, None):
                    store.log(cursor, 'grant', user_id=person['id'], user_name=person['name'],
                              email=email, actor_id=None, actor_name=ACTOR_NAME)
                    granted.append(person['id'])
        except Exception:  # noqa: BLE001
            logger.exception('syntony autogrant: не добавился пользователь %s', person['id'])
    return granted


def _fill_emails(db, store, limit):
    with db._get_cursor() as cursor:
        rows = store.rows_awaiting_card_email(cursor, limit)
    filled = []
    for row in rows:
        try:
            with db._get_cursor() as cursor:
                if store.email_owner(cursor, row['card_email'], row['id']):
                    continue
                if store.fill_empty_email(cursor, row['id'], row['card_email']):
                    store.log(cursor, 'email', user_id=row['id'], user_name=row['name'],
                              email=row['card_email'], actor_id=None, actor_name=ACTOR_NAME)
                    filled.append(row['id'])
        except Exception:  # noqa: BLE001
            logger.exception('syntony autogrant: почта не подставилась пользователю %s', row['id'])
    return filled


def _register(db, store, user_ids, *, load_config, request_window, backoff, budget, pause,
              sleep, clock):
    """Записать людей у Syntony. Курсор закрыт, пока Syntony отвечает."""
    started = clock()
    outcomes, stopped, asked = {}, '', 0
    for user_id in user_ids:
        if clock() - started >= budget:
            break
        with db._get_cursor() as cursor:
            ctx = store.load_access_context(cursor, user_id)
        # Пока сторож шёл, человека могли убрать, уволить или Syntony уже ответила о нём.
        if not ctx or not access.can_request_window(ctx) or ctx['link_state'] != rules.LINK_UNKNOWN:
            continue
        if asked:
            sleep(pause)
        asked += 1
        reply = service.ask(rules.registration_window(), user_id=ctx['user_id'], name=ctx['name'],
                            email=ctx['email'], load_config=load_config, request_window=request_window)
        if reply is None:
            stopped = rules.CODE_NOT_CONFIGURED
            break
        service.remember_link(db, store, ctx['user_id'], reply, ctx['link_state'], log=logger)
        service.note_reply('запись оператора сторожем', ctx['user_id'], reply, log=logger)
        state = rules.link_state_of(reply.status, reply.body)
        key = state or reply.code or 'unknown'
        outcomes[key] = outcomes.get(key, 0) + 1
        if reply.code in STOP_CODES:
            stopped = reply.code
            break
        if not state:
            backoff[ctx['user_id']] = clock() + RETRY_AFTER_SECONDS
    return outcomes, stopped


def run_once(db, *, load_config=client.load_config, request_window=client.request_window,
             store=queries, batch=BATCH, budget=TALK_BUDGET_SECONDS, pause=PAUSE_SECONDS,
             sleep=time.sleep, clock=time.monotonic, backoff=None):
    """Один прогон сторожа → сводка {'granted', 'emails', 'registered', 'stopped'}
    или {'skipped': 'not_configured'}."""
    if load_config() is None:
        return {'skipped': 'not_configured'}
    backoff = _backoff if backoff is None else backoff
    granted = _grant_new(db, store, batch)
    filled = _fill_emails(db, store, batch)
    with db._get_cursor() as cursor:
        candidates = store.unregistered(cursor, UNREGISTERED_SCAN)
    now = clock()
    with _backoff_lock:
        for user_id in [uid for uid, until in backoff.items() if until <= now]:
            del backoff[user_id]
        waiting = [uid for uid in candidates if uid not in backoff][:batch]
    registered, stopped = _register(db, store, waiting, load_config=load_config,
                                    request_window=request_window, backoff=backoff, budget=budget,
                                    pause=pause, sleep=sleep, clock=clock)
    return {'granted': granted, 'emails': filled, 'registered': registered, 'stopped': stopped}


def _describe(summary):
    if summary.get('skipped'):
        return ''
    parts = []
    if summary['granted']:
        parts.append('добавлены в список: %s' % ', '.join(map(str, summary['granted'])))
    if summary['emails']:
        parts.append('почта из карточки: %s' % ', '.join(map(str, summary['emails'])))
    if summary['registered']:
        parts.append('ответы Syntony: %s' % ', '.join('%s×%d' % item for item in sorted(summary['registered'].items())))
    if summary['stopped']:
        parts.append('прогон остановлен: %s' % summary['stopped'])
    return '; '.join(parts)


def _loop(db, interval, first_delay):
    time.sleep(first_delay)
    while True:
        try:
            note = _describe(run_once(db))
            if note:
                logger.info('Окно Syntony, сторож списка — %s', note)
        except Exception:  # noqa: BLE001
            logger.exception('Окно Syntony, сторож списка: прогон упал')
        time.sleep(interval)


_worker = None
_worker_pid = None
_worker_lock = threading.Lock()


def start_worker(db, *, interval=INTERVAL_SECONDS, first_delay=FIRST_RUN_DELAY_SECONDS):
    """Один поток на процесс. Без ключа Syntony или с выключателем — не запускается (ключ и
    выключатель меняются только перезапуском сервиса)."""
    global _worker, _worker_pid
    if not is_enabled() or not client.is_configured():
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_loop, args=(db, interval, first_delay),
                                   name='syntony-autogrant', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
