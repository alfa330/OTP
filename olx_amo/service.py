# -*- coding: utf-8 -*-
"""Цикл робота: опросить кабинеты OLX, разобрать новое, записать в amoCRM.

Порядок обработки одного обращения — это раздел 2 ТЗ, переложенный в код:

    новое сообщение в чате
        ├─ номер распознан            → сделка «Новая заявка», тег кабинета,
        │                               ответственный «Администратор», контакт
        │                               с номером в «Раб. тел.»
        ├─ номер писали, но кривой    → та же сделка + пометка «ручная проверка»
        └─ номера нет вовсе           → заготовленный ответ с телефоном кабинета

Три решения, которые стоит понимать до чтения кода
---------------------------------------------------

* **Кабинеты опрашиваются параллельно, но каждый — своей транзакцией.** Девять
  последовательных обходов не уложились бы в SLA, если один кабинет отвечает
  медленно: остальные восемь ждали бы его. При этом писать в базу общей
  транзакцией на все кабинеты нельзя — падение девятого откатило бы работу
  первых восьми, и обращения пришлось бы разбирать заново.

* **Ошибка не теряет обращение.** Любой сбой на пути в amoCRM пишется в журнал
  строкой `error` — и эта же строка и есть очередь на повтор (`retry_failed`).
  Отдельной таблицы очереди нет намеренно: две сущности на одно и то же
  состояние неминуемо разъезжаются.

* **Закладка по чату — наша, а не «прочитано» в OLX.** Отметку о прочтении в
  кабинете снимает любой человек, открывший чат руками, и робот тут же счёл бы
  обращение новым. Поэтому разбор идёт по `last_message_id` в своей таблице, а
  `mark-as-read` в OLX ставится только ради живых людей: непрочитанное в
  кабинете должно гаснуть.
"""

import logging
import os
import threading
from datetime import datetime, timedelta

from . import cabinets, phones, queries
from .amo_writer import AmoWriteError, AmoWriter
from .olx_client import (OlxAuthError, OlxClient, OlxError, OlxRateLimited,
                         message_is_incoming, message_phone, message_time,
                         refresh_tokens)

log = logging.getLogger(__name__)

# Сколько страниц чатов просматриваем за цикл. Сортировка выдачи `/threads` не
# документирована, поэтому «прочитать первую страницу» — недостаточная гарантия:
# старый чат с новым сообщением может лежать вглубь. Четыре страницы по 50 — это
# 200 свежих чатов на кабинет за полминуты, чего с запасом хватает, и всего
# четыре запроса из бюджета.
MAX_THREAD_PAGES = 4
THREADS_PAGE_SIZE = 50

# Сколько сообщений дочитываем в одном чате. Обращения короткие; закладка не
# даёт разбирать одно и то же дважды, а глубже нужного лезть незачем.
MESSAGES_PAGE_SIZE = 30

# Потолок чатов, разбираемых за один цикл на кабинет.
#
# Считаем худший случай. Лимит OLX — 4500 запросов с IP за 5 минут, при
# превышении блокировка на полчаса, из которой нет выхода, кроме ожидания.
# Цикл идёт дважды в минуту, то есть за окно лимита их десять. Один чат — один
# запрос за сообщениями, плюс до четырёх страниц списка на кабинет:
#     10 циклов × 9 кабинетов × (4 + N) < 4500  →  N < 46
# Берём 25 с запасом: остаток бюджета нужен ещё и отправке автоответов, и
# отметкам «прочитано». Всё, что не влезло, разберётся через полминуты
# следующим циклом — обращения не теряются, лишь чуть сдвигается очередь.
MAX_THREADS_PER_CYCLE = 25

# Порог SLA из пункта 6.2 ТЗ — минута от отклика до сделки.
SLA_MS = 60 * 1000

# Насколько старое сообщение робот ещё считает обращением.
#
# Это предохранитель первого запуска. В девяти кабинетах лежат накопленные
# непрочитанные чаты за месяцы; без горизонта первый же опрос завёл бы сделку по
# каждому из них — задним числом, поверх той работы, что маркетолог уже сделал
# руками. Дедупликация не спасёт: она в границах суток, а история по разным дням.
#
# Шесть часов — компромисс: перезапуск приложения или получасовая блокировка
# лимитом ничего не теряют, а вчерашнее в воронку уже не поедет. Значение вынесено
# в окружение: на первом боевом запуске его осмысленно поставить в ноль или
# минуты, посмотреть журнал и только потом отпустить.
HORIZON = timedelta(hours=float(os.getenv('OLX_MESSAGE_HORIZON_HOURS') or 6))

# Смещение Алматы от UTC. Время в журнале хранится этим сдвигом (см. queries.py),
# а amoCRM отдаёт unix-время — единственное место, где они встречаются, это
# проверка повтора `_lead_already_there`.
_ALMATY_OFFSET = timedelta(hours=5)


class CabinetResult(object):
    """Итог опроса одного кабинета. Складывается в olx_poll_runs и в лог."""

    __slots__ = ('code', 'threads_seen', 'messages_seen', 'leads_created',
                 'replies_sent', 'errors', 'error_text', 'skipped')

    def __init__(self, code):
        self.code = code
        self.threads_seen = 0
        self.messages_seen = 0
        self.leads_created = 0
        self.replies_sent = 0
        self.errors = 0
        self.error_text = None
        self.skipped = None

    def as_dict(self):
        return {
            'cabinet': self.code, 'threads': self.threads_seen,
            'messages': self.messages_seen, 'leads': self.leads_created,
            'replies': self.replies_sent, 'errors': self.errors,
            'error': self.error_text, 'skipped': self.skipped,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Токены
# ─────────────────────────────────────────────────────────────────────────────

def ensure_access_token(db, cabinet):
    """Действующий access_token кабинета. None — кабинетом сейчас ходить нечем.

    Обновление и запись идут ВМЕСТЕ и в одной транзакции: OLX ротирует
    refresh_token, старый после обмена перестаёт работать, и не записать новый —
    значит потерять кабинет до нового согласия владельца в браузере.
    """
    with db._get_cursor() as cursor:
        account = queries.get_account(cursor, cabinet.code)
        if not account:
            queries.ensure_accounts(cursor)
            account = queries.get_account(cursor, cabinet.code)

    if not account or not account.get('is_enabled'):
        return None, 'disabled'
    if not cabinet.is_configured():
        with db._get_cursor() as cursor:
            queries.set_account_state(cursor, cabinet.code, 'not_configured')
        return None, 'not_configured'

    token = account.get('access_token')
    expires = account.get('access_token_expires_at')
    if token and expires and expires > datetime.utcnow():
        return token, None

    refresh = account.get('refresh_token')
    if not refresh:
        with db._get_cursor() as cursor:
            queries.set_account_state(
                cursor, cabinet.code, 'needs_auth',
                'кабинет ещё не подтвердил доступ: пройдите согласие владельца')
        return None, 'needs_auth'

    try:
        fresh = refresh_tokens(cabinet.env_client_id, cabinet.env_client_secret, refresh)
    except OlxAuthError as exc:
        with db._get_cursor() as cursor:
            queries.set_account_state(cursor, cabinet.code, 'needs_auth', str(exc))
        log.warning('OLX %s: refresh отвергнут, нужен новый вход владельца: %s',
                    cabinet.code, exc)
        return None, 'needs_auth'
    except OlxError as exc:
        with db._get_cursor() as cursor:
            queries.set_account_state(cursor, cabinet.code, 'error', str(exc))
        return None, 'error'

    with db._get_cursor() as cursor:
        queries.save_tokens(cursor, cabinet.code, fresh['access_token'],
                            fresh['expires_at'], fresh.get('refresh_token'),
                            fresh.get('scope'))
    return fresh['access_token'], None


# ─────────────────────────────────────────────────────────────────────────────
# Разбор одного сообщения
# ─────────────────────────────────────────────────────────────────────────────

def _excerpt(message):
    text = (message or {}).get('text') or ''
    return text.strip()


def extract_phone(message):
    """Найти телефон кандидата в сообщении. Возвращает (номер, сырое, разбор).

    Сначала смотрим недокументированное поле `phone` — в категориях «Работа»
    OLX кладёт номер туда отдельно от текста, и это самый надёжный источник.
    Если его нет или он не под маской, разбираем текст.
    """
    own = cabinets.LINE_PHONES

    direct = message_phone(message)
    if direct:
        normalized = phones.normalize(direct)
        if normalized and normalized not in own:
            return normalized, direct, phones.scan(_excerpt(message), own_lines=own)

    found = phones.scan(_excerpt(message), own_lines=own)
    if found.first:
        # Второй элемент — то, КАК номер был написан: раздел 7 ТЗ требует в
        # журнале обе формы, а две одинаковые колонки разбирать спорный случай
        # не помогают.
        return found.first, found.first_raw, found
    # Номер писали, но он не читается — вернём сырой кусок, он нужен журналу.
    return None, (found.rejected[0] if found.rejected else direct), found


def _handle_message(db, writers, cabinet, thread, message, counters):
    """Обработать одно входящее сообщение. Пишет журнал, счётчики — в `counters`.

    Транзакции здесь КОРОТКИЕ и вокруг сетевых вызовов, а не поверх них. Это
    важнее, чем кажется: у запроса в amoCRM таймаут 60 секунд, кабинетов девять,
    а в пуле сорок соединений. Держи мы курсор всё время записи в CRM — один
    подвисший amoCRM занимал бы четверть пула на минуту, дважды в минуту, и
    вставало бы всё приложение, а не только робот.

    Порядок поэтому такой: прочитать состояние → сходить по сети → записать
    результат. Между чтением и записью возможна гонка (два сообщения одного
    кандидата в разных потоках), и её ловит уникальный индекс дедупликации —
    см. ветку `row is None` ниже.
    """
    thread_id = str(thread.get('id'))
    message_id = str(message.get('id'))
    sent_at = message_time(message)
    excerpt = _excerpt(message)

    phone, raw, found = extract_phone(message)
    tag_name = cabinet.tag_form
    day = sent_at.date() if sent_at else queries.today_almaty()

    # ── ветка 3: номера нет вовсе → заготовленный ответ ───────────────────
    if phone is None and not found.rejected:
        with db._get_cursor() as cursor:
            state = queries.get_thread(cursor, cabinet.code, thread_id) or {}
        if state.get('canned_reply_sent_at'):
            # Автоответ этому человеку уже уходил, и второго робот не шлёт: ТЗ
            # запрещает повторную отправку, а другой автоматический текст
            # раздражает и читается как поломка (решение владельца 02.09.2026).
            #
            # Но и молчать вникуда нельзя — человек задал живой вопрос. Помечаем
            # чат «ждёт ответа человека»: он всплывёт в разделе, и маркетолог
            # ответит сам. Метка ставится один раз на обращение, поэтому строка
            # журнала появляется только на ПЕРВОЕ такое сообщение, а не на
            # каждое следующее «ау?».
            with db._get_cursor() as cursor:
                if queries.mark_awaiting_human(cursor, cabinet.code, thread_id):
                    queries.write_journal(
                        cursor, cabinet.code, 'needs_human', thread_id=thread_id,
                        message_id=message_id, message_at=sent_at,
                        message_excerpt=excerpt, tag=tag_name)
            return
        # Отметку ставим ДО отправки и отдельной транзакцией.
        #
        # Порядок здесь важнее, чем кажется. Отметь мы чат после отправки и в
        # одной транзакции с записью в журнал — любой сбой этой транзакции
        # откатывал бы отметку, и кандидат получал бы то же сообщение снова
        # каждые полминуты. Именно так и вышло 01.09.2026: 172 копии одному
        # человеку.
        #
        # Цена обратного порядка — не отправленный автоответ, если отправка
        # упадёт сразу после отметки. Это осознанный выбор: ТЗ отдельным пунктом
        # запрещает повторную отправку, а не отправленное видно в журнале
        # строкой `error`, и человек может ответить сам. Молчание поправимо,
        # разосланный спам — нет.
        with db._get_cursor() as cursor:
            queries.mark_canned_reply_sent(cursor, cabinet.code, thread_id)

        try:
            writers.olx.send_message(thread_id, cabinet.canned_reply)
        except OlxError as exc:
            counters.errors += 1
            with db._get_cursor() as cursor:
                queries.write_journal(
                    cursor, cabinet.code, 'error', thread_id=thread_id,
                    message_id=message_id, message_at=sent_at, message_excerpt=excerpt,
                    error_text='не удалось отправить заготовленный ответ: %s' % (exc,))
            return

        counters.replies_sent += 1
        with db._get_cursor() as cursor:
            queries.write_journal(
                cursor, cabinet.code, 'canned_reply', thread_id=thread_id,
                message_id=message_id, message_at=sent_at, message_excerpt=excerpt,
                tag=tag_name)
        return

    needs_manual = phone is None
    # Кривой номер в название сделки не поставить: ТЗ отдельным пунктом
    # запрещает сделку с пустым названием, а маска 77XXXXXXXXX обязательна и
    # для названия, и для «Раб. тел.». Поэтому в amoCRM уходит сырой номер
    # только цифрами, а пометка о проверке — примечанием.
    lead_phone = phone or (phones.digits_only(raw) or 'без номера')

    # ── дешёвая проверка дубля до похода в CRM ────────────────────────────
    if phone:
        with db._get_cursor() as cursor:
            already = queries.find_recent_lead(cursor, cabinet.code, phone, day=day)
        if already:
            with db._get_cursor() as cursor:
                queries.write_journal(
                    cursor, cabinet.code, 'duplicate', thread_id=thread_id,
                    message_id=message_id, message_at=sent_at, phone_raw=raw,
                    phone_normalized=phone, tag=tag_name, message_excerpt=excerpt,
                    amo_lead_id=already.get('amo_lead_id'))
            return

    # ── создание сделки (СЕТЬ, вне транзакции) ────────────────────────────
    try:
        lead_id, contact_id = writers.amo.create_lead(
            lead_phone, cabinet.code, needs_manual_review=needs_manual)
    except AmoWriteError as exc:
        counters.errors += 1
        with db._get_cursor() as cursor:
            queries.write_journal(
                cursor, cabinet.code, 'error', thread_id=thread_id,
                message_id=message_id, message_at=sent_at, phone_raw=raw,
                phone_normalized=phone, tag=tag_name, message_excerpt=excerpt,
                error_text=str(exc))
        return

    created_at = queries.now_almaty()
    latency = None
    if sent_at:
        latency = max(0, int((created_at - sent_at).total_seconds() * 1000))

    with db._get_cursor() as cursor:
        row = queries.write_journal(
            cursor, cabinet.code, 'manual_review' if needs_manual else 'lead_created',
            thread_id=thread_id, message_id=message_id, message_at=sent_at,
            lead_created_at=created_at, latency_ms=latency, phone_raw=raw,
            phone_normalized=phone, tag=tag_name, amo_lead_id=lead_id,
            amo_contact_id=contact_id, message_excerpt=excerpt)

        if row is None:
            # Уникальный индекс дедупликации сработал уже ПОСЛЕ создания сделки:
            # два сообщения одного кандидата разъехались по потокам и обогнали
            # проверку выше. Сделка в amoCRM всё же появилась — молчать об этом
            # нельзя, иначе её никто не найдёт. Пишем честный `duplicate` со
            # ссылкой на неё.
            queries.write_journal(
                cursor, cabinet.code, 'duplicate', thread_id=thread_id,
                message_id=message_id, message_at=sent_at, phone_raw=raw,
                tag=tag_name, amo_lead_id=lead_id, message_excerpt=excerpt,
                error_text='гонка дедупликации: сделка создана повторно')
            log.warning('OLX %s: гонка дедупликации, сделка %s создана повторно',
                        cabinet.code, lead_id)
            return

        queries.upsert_thread(cursor, cabinet.code, thread_id,
                              amo_lead_id=lead_id, phone_normalized=phone)

    counters.leads_created += 1
    if latency is not None and latency > SLA_MS:
        log.warning('OLX %s: обращение доехало за %.1f с — вне SLA',
                    cabinet.code, latency / 1000.0)


# ─────────────────────────────────────────────────────────────────────────────
# Опрос кабинета
# ─────────────────────────────────────────────────────────────────────────────

# Клиент amoCRM — СВОЙ на каждый рабочий поток.
#
# `requests.Session` потокобезопасной не объявлена, а кабинеты опрашиваются
# девятью потоками сразу: одна сессия на всех рано или поздно дала бы перепутанные
# ответы или гонку на обновлении заголовка авторизации. Заводить же клиента на
# каждый опрос нельзя — конструктор делает вход по логину и паролю, а это девять
# логинов дважды в минуту.
#
# Отсюда threading.local: поток логинится один раз и переиспользует токен, пока
# тот жив (~40 минут). Девять сессий на процесс — цена, которую видно и которая
# не растёт.
_thread_state = threading.local()


def amo_client_for_thread():
    """Клиент amoCRM текущего потока. Создаётся при первом обращении."""
    client = getattr(_thread_state, 'amo_client', None)
    if client is None:
        import amo_leads
        client = amo_leads.AmoClient()
        _thread_state.amo_client = client
    return client


class _Writers(object):
    """Пара клиентов, которую носит обработчик сообщения: OLX и amoCRM."""

    __slots__ = ('olx', 'amo')

    def __init__(self, olx, amo):
        self.olx = olx
        self.amo = amo


def poll_cabinet(db, cabinet, amo_client=None):
    """Один проход по кабинету. Не бросает наружу — всё складывает в результат.

    `amo_client` не передают в обычной работе: клиент берётся свой на поток (см.
    `amo_client_for_thread`). Параметр оставлен для тестов и разовых прогонов,
    где клиента подменяют заглушкой.
    """
    result = CabinetResult(cabinet.code)

    token, blocked = ensure_access_token(db, cabinet)
    if not token:
        result.skipped = blocked
        # Отметку об опросе ставим ДАЖЕ для пропущенного кабинета: иначе
        # «не настроен» выглядело бы как «робот умер», и сторож простоя
        # звонил бы по девять раз в час о том, что и так известно.
        with db._get_cursor() as cursor:
            queries.mark_polled(cursor, cabinet.code)
        return result

    client = OlxClient(token_provider=lambda: token)
    writers = _Writers(client, AmoWriter(amo_client or amo_client_for_thread()))

    with db._get_cursor() as cursor:
        run_id = queries.start_poll_run(cursor, cabinet.code)

    handled = 0
    truncated = False
    try:
        for page in range(MAX_THREAD_PAGES):
            threads, _, _ = client.threads(offset=page * THREADS_PAGE_SIZE,
                                           limit=THREADS_PAGE_SIZE)
            if not threads:
                break
            result.threads_seen += len(threads)

            # Отсев «ничего не изменилось» — ОТДЕЛЬНО от признака «страница без
            # непрочитанного». Условие остановки ниже смотрит на `unread`, а не
            # на `fresh`: страница, где всё непрочитанное уже разобрано, ещё не
            # значит, что дальше пусто, а сортировка выдачи OLX не документирована.
            unread = [t for t in threads if int(t.get('unread_count') or 0) > 0]
            fresh = _candidates(db, cabinet, threads)
            for index, thread in enumerate(fresh):
                if handled >= MAX_THREADS_PER_CYCLE:
                    # Молчаливого усечения быть не должно: «разобрали всё» и
                    # «разобрали сколько успели» — разные вещи, и вторая обязана
                    # быть видна в логе, иначе накопившаяся очередь выглядит как
                    # затишье. Считаем только непрочитанное на ЭТОЙ странице:
                    # сколько его дальше, мы не знаем — за ним и не ходили.
                    log.warning(
                        'OLX %s: за цикл разобрано %d чатов, ещё минимум %d '
                        'отложены до следующего опроса (потолок бюджета запросов)',
                        cabinet.code, handled, len(fresh) - index)
                    truncated = True
                    break
                _poll_thread(db, writers, cabinet, thread, result)
                handled += 1
            if truncated:
                break

            # Непрочитанного на странице не было ВООБЩЕ — дальше страницы ещё
            # старше, идти вглубь незачем. Это и держит расход запросов на двух
            # процентах бюджета вместо пробоя лимита.
            if not unread:
                break

    except OlxRateLimited as exc:
        result.errors += 1
        result.error_text = 'лимит запросов OLX, пауза %s с' % (exc.retry_after,)
        log.warning('OLX %s: %s', cabinet.code, result.error_text)
    except OlxAuthError as exc:
        result.errors += 1
        result.error_text = str(exc)
        with db._get_cursor() as cursor:
            queries.set_account_state(cursor, cabinet.code, 'needs_auth', str(exc))
    except Exception as exc:                # noqa: BLE001 — цикл не имеет права упасть
        result.errors += 1
        result.error_text = str(exc)
        log.exception('OLX %s: опрос сорвался', cabinet.code)
        with db._get_cursor() as cursor:
            queries.set_account_state(cursor, cabinet.code, 'error', str(exc))

    with db._get_cursor() as cursor:
        queries.finish_poll_run(
            cursor, run_id, threads_seen=result.threads_seen,
            messages_seen=result.messages_seen, leads_created=result.leads_created,
            replies_sent=result.replies_sent, errors=result.errors,
            error_text=result.error_text)
        queries.mark_polled(cursor, cabinet.code,
                            saw_message=bool(result.messages_seen),
                            made_lead=bool(result.leads_created))
        if not result.errors:
            queries.set_account_state(cursor, cabinet.code, 'ok')
    return result


def _poll_thread(db, writers, cabinet, thread, result):
    """Разобрать новое в одном чате.

    Транзакций здесь несколько, и все короткие: между ними идут походы в OLX и
    amoCRM, а держать соединение с базой через сетевой вызов нельзя (см.
    `_handle_message`). Согласованность обеспечивает не длинная транзакция, а
    порядок: сначала записывается результат обращения, и только потом двигается
    закладка чата.
    """
    thread_id = str(thread.get('id'))
    try:
        messages, _, _ = writers.olx.messages(thread_id, limit=MESSAGES_PAGE_SIZE)
    except OlxError as exc:
        result.errors += 1
        log.warning('OLX %s: чат %s не прочитался: %s', cabinet.code, thread_id, exc)
        return

    with db._get_cursor() as cursor:
        state = queries.get_thread(cursor, cabinet.code, thread_id) or {}

    # Человек ответил — снимаем метку ожидания. Признак: наше исходящее позже
    # того момента, когда метка была поставлена. Без этого список «ждут ответа»
    # пришлось бы разгребать руками, а такой список быстро перестают открывать.
    waiting_since = state.get('awaiting_human_since')
    if waiting_since and any(
            m.get('type') == 'sent' and (message_time(m) or datetime.min) > waiting_since
            for m in messages or []):
        with db._get_cursor() as cursor:
            queries.clear_awaiting_human(cursor, cabinet.code, thread_id)

    fresh = _after_bookmark(messages, state.get('last_message_id'),
                            seen_until=state.get('last_message_at'))
    incoming = [m for m in fresh if message_is_incoming(m)]
    result.messages_seen += len(incoming)

    for message in incoming:
        _handle_message(db, writers, cabinet, thread, message, result)

    # Закладку двигаем ПОСЛЕ разбора: упади обработка на середине — чат
    # разберётся заново на следующем опросе, а дубли отсечёт индекс. Обратный
    # порядок терял бы обращения молча, а это ТЗ запрещает прямо.
    newest = _newest(messages)
    with db._get_cursor() as cursor:
        queries.upsert_thread(
            cursor, cabinet.code, thread_id,
            advert_id=_text(thread.get('advert_id')),
            interlocutor_id=_text(thread.get('interlocutor_id')),
            last_message_id=_text((newest or {}).get('id')),
            last_message_at=message_time(newest) if newest else None,
            last_unread_count=int(thread.get('unread_count') or 0),
            last_total_count=int(thread.get('total_count') or 0),
            messages_seen=len(incoming))

    # Гасим непрочитанное в кабинете — ради человека, не ради робота: то, что
    # робот уже отнёс в amoCRM, не должно снова попадаться маркетологу.
    #
    # И только то, что робот ДЕЙСТВИТЕЛЬНО разобрал. Чат, где всё отсеклось
    # горизонтом (накопившаяся история на первом запуске), обязан остаться
    # непрочитанным: в CRM он не поехал, и если погасить его здесь, обращение
    # исчезнет сразу отовсюду — и у робота, и у человека.
    #
    # После записи в базу: упасть здесь безопаснее, чем разобрать заново.
    if not incoming:
        return
    try:
        writers.olx.mark_read(thread_id)
    except OlxError as exc:
        log.debug('OLX %s: не удалось отметить чат %s прочитанным: %s',
                  cabinet.code, thread_id, exc)


def _candidates(db, cabinet, threads):
    """Чаты, которые стоит прочитать на этом круге.

    Непрочитанного мало для отбора, и это выяснилось на проде 02.09.2026: чат,
    который маркетолог открыл в кабинете раньше робота, теряет счётчик
    непрочитанного, и робот к нему уже не подходит — обращение остаётся без
    ответа навсегда. Так потерялись четыре обращения, два из них с телефоном.

    Поэтому кандидатов три вида:

    * есть непрочитанное — обычный случай;
    * чат нам ЗНАКОМ, и число сообщений в нём выросло — значит появилось новое,
      даже если счётчик непрочитанного уже погашен человеком;
    * чат СОВСЕМ новый и создан только что — его мог открыть человек в те
      секунды, что прошли до нашего опроса. Окно берём равным горизонту: всё,
      что старше, робот и так не стал бы разбирать.

    Обратная сторона — отсев. Чат, отсечённый горизонтом, робот намеренно НЕ
    помечает прочитанным (он оставляет его человеку), поэтому тот висит
    непрочитанным вечно. Без проверки «число сообщений не изменилось» такие чаты
    занимали бы весь лимит цикла и вытесняли свежие обращения: старой истории в
    девяти кабинетах больше, чем помещается за полминуты.

    У чатов, заведённых до этой правки, общего числа не записано. Такие
    пропускаем — лишний раз прочитать безвредно, а потерять нет.

    Расход при этом почти не растёт: сообщения дочитываются только у отобранных,
    а список чатов мы и так получаем целиком.
    """
    if not threads:
        return []

    with db._get_cursor() as cursor:
        known = {
            str(row['thread_id']): row
            for row in (queries.threads_state(cursor, cabinet.code,
                                              [t.get('id') for t in threads]) or [])
        }

    edge = queries.now_almaty() - HORIZON
    picked = []
    for thread in threads:
        tid = str(thread.get('id'))
        if int(thread.get('unread_count') or 0) > 0:
            picked.append(thread)
            continue

        state = known.get(tid)
        if state:
            stored = state.get('last_total_count')
            if stored is None or int(stored) != int(thread.get('total_count') or 0):
                picked.append(thread)
            continue

        # Незнакомый чат без непрочитанного: берём только совсем свежий.
        created = _thread_created(thread)
        if created is not None and created > edge:
            picked.append(thread)
    return picked


def _thread_created(thread):
    """Когда чат заведён, в местном времени. OLX и здесь отдаёт UTC без зоны."""
    return message_time({'created_at': (thread or {}).get('created_at')})


def _after_bookmark(messages, bookmark, seen_until=None, now=None):
    """Сообщения, которые ещё не разбирали.

    Три правила, и каждое закрывает свой способ ошибиться.

    1. **Порядок выдачи OLX не документирован**, поэтому на него не полагаемся:
       сортируем сами по времени и по id.

    2. **Закладка по id — основная**, но её может не оказаться в выдаче: чат
       разросся, и страница до неё не достала. Тогда работает закладка по
       ВРЕМЕНИ (`seen_until`) — берём строго то, что новее последнего
       разобранного сообщения. Полагаться в этом случае на «взять всё, что
       видим» нельзя: если OLX отдаёт страницу от старых к новым, робот раз за
       разом разбирал бы одну и ту же древнюю переписку.

    3. **Горизонт.** У чата, который робот видит ВПЕРВЫЕ, закладки нет вовсе, и
       без ограничения он разобрал бы всю историю. На первом же запуске по
       девяти кабинетам это сотни сделок задним числом, и дедупликация тут не
       поможет — она работает в границах суток, а история лежит по разным дням.
       Поэтому у нового чата берутся только сообщения не старше `HORIZON`.
    """
    ordered = sorted(messages or [], key=_order_key)

    if bookmark:
        for index, message in enumerate(ordered):
            if str(message.get('id')) == str(bookmark):
                return ordered[index + 1:]

    # Закладки по id нет или она не нашлась — отсекаем по времени.
    edge = seen_until
    if edge is None:
        edge = (now or queries.now_almaty()) - HORIZON
    return [m for m in ordered
            if (message_time(m) or datetime.min) > edge]


def _order_key(message):
    stamp = message_time(message)
    return (stamp or datetime.min, str(message.get('id') or ''))


def _newest(messages):
    ordered = sorted(messages or [], key=_order_key)
    return ordered[-1] if ordered else None


def _text(value):
    return None if value is None else str(value)


# ─────────────────────────────────────────────────────────────────────────────
# Цикл целиком
# ─────────────────────────────────────────────────────────────────────────────

def is_enabled():
    """Есть ли хоть один кабинет, которым можно ходить, и настроена ли amoCRM."""
    import amo_leads
    return bool(cabinets.configured()) and amo_leads.is_configured()


def poll_all(db, pool=None):
    """Пройти по всем кабинетам. Возвращает список итогов по каждому.

    Кабинеты идут параллельно: девять последовательных обходов не уложились бы
    в SLA, если один отвечает медленно. Пул передаётся снаружи — свой, не общий
    `executor_pool` бота: в нём всего четыре места на всё приложение, и занять
    их девятью опросами значило бы подвесить остальные разделы.

    ВАЖНО: сама эта функция блокируется на `future.result()`, поэтому её нельзя
    запускать В ТОМ ЖЕ пуле, места которого она раздаёт, — координатор занял бы
    место и ждал бы освобождения тех, что уже занял сам. Планировщик поэтому
    раздаёт кабинеты по пулу напрямую (см. `olx_amo_poll_job`), а эта функция
    остаётся для разовых прогонов и тестов.
    """
    targets = [c for c in cabinets.CABINETS if c.is_configured()]
    if not targets:
        log.info('OLX→amo: ни один кабинет не настроен, опрос пропущен')
        return []

    if pool is None:
        return [poll_cabinet(db, cab) for cab in targets]

    futures = [pool.submit(poll_cabinet, db, cab) for cab in targets]
    results = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception:               # noqa: BLE001 — уже залогировано внутри
            log.exception('OLX→amo: опрос кабинета завершился исключением')
    return results


def retry_failed(db, limit=100):
    """Повторить обращения, упавшие по дороге в amoCRM.

    ТЗ: «при ошибке обращение должно повторно ставиться в очередь на отправку, а
    не удаляться». Очередь — это строки журнала с `result='error'`; повтор
    переводит их в нормальный исход и увеличивает счётчик попыток.
    """
    with db._get_cursor() as cursor:
        pending = queries.pending_retries(cursor, limit=limit)
    if not pending:
        return {'retried': 0, 'recovered': 0}

    writer = AmoWriter(amo_client_for_thread())
    recovered = 0
    for row in pending:
        phone = row.get('phone_normalized')
        if not phone:
            # Обращение без распознанного номера повторять нечем: заготовленный
            # ответ уйдёт на следующем опросе по общей логике.
            continue

        # Сбой мог случиться и ПОСЛЕ того, как amoCRM завела сделку: ответ не
        # доехал, а запись есть. Слепой повтор в этом случае создаёт вторую
        # сделку по тому же номеру — ровно то, что ТЗ запрещает отдельным
        # пунктом. Поэтому сначала спрашиваем amoCRM, не появился ли уже
        # контакт с этим номером после нашей попытки.
        existing = _lead_already_there(writer, phone, row.get('created_at'))
        if existing:
            with db._get_cursor() as cursor:
                queries.resolve_retry(cursor, row['id'], 'duplicate',
                                      amo_lead_id=existing, error_text=None)
            log.info('OLX→amo: повтор отменён, сделка %s уже была создана', existing)
            continue

        try:
            lead_id, contact_id = writer.create_lead(phone, row['cabinet_code'])
        except AmoWriteError as exc:
            with db._get_cursor() as cursor:
                queries.resolve_retry(cursor, row['id'], 'error', error_text=str(exc))
            continue
        created_at = queries.now_almaty()
        latency = None
        if row.get('message_at'):
            latency = max(0, int((created_at - row['message_at']).total_seconds() * 1000))
        with db._get_cursor() as cursor:
            queries.resolve_retry(cursor, row['id'], 'lead_created',
                                  amo_lead_id=lead_id, amo_contact_id=contact_id,
                                  lead_created_at=created_at, latency_ms=latency,
                                  error_text=None)
        recovered += 1
    return {'retried': len(pending), 'recovered': recovered}


def _lead_already_there(writer, phone, since):
    """Не создалась ли сделка по этому номеру ещё в неудавшейся попытке.

    Возвращает id сделки, если контакт с таким номером в amoCRM появился НЕ
    РАНЬШЕ нашей попытки. Раньше — значит это чужая, давняя сделка, и она
    поводом отменять повтор не является.

    Проверка вспомогательная и намеренно нестрогая: ручка поиска у amoCRM одна
    (`contacts?query=`), документация обещает объявить её устаревшей, а падение
    поиска не должно мешать повтору. Не смогли проверить — повторяем: лишняя
    сделка хуже потерянной, но обе плохи, и выбор здесь в пользу той, которую
    видно в журнале.
    """
    try:
        contacts = writer.find_contacts_by_phone(phone)
    except Exception:                       # noqa: BLE001 — вспомогательная проверка
        return None

    threshold = None
    if since is not None:
        # `created_at` контакта amoCRM отдаёт unix-временем в UTC, а наше время
        # хранится сдвигом Алматы. Приводим к одному основанию.
        threshold = (since - _ALMATY_OFFSET).timestamp() - 60

    for contact in contacts or []:
        created = contact.get('created_at')
        if threshold is not None and created is not None and float(created) < threshold:
            continue
        leads = ((contact.get('_embedded') or {}).get('leads') or [])
        if leads:
            return leads[0].get('id')
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Уведомления ответственным (раздел 7 ТЗ)
# ─────────────────────────────────────────────────────────────────────────────

# Сколько робот вправе молчать, прежде чем это считается простоем. Значение из
# ТЗ: «максимальное время простоя — не более 15 минут».
IDLE_ALERT_MINUTES = int(os.getenv('OLX_IDLE_ALERT_MINUTES') or 15)

# Сколько кабинет вправе не приносить обращений, прежде чем это подозрительно.
# ТЗ называет это «согласованным интервалом» и числа не задаёт. Двенадцать часов
# — компромисс: ночью в кабинет действительно может никто не написать, а вот
# полсуток тишины при живом опросе означают, что робот «молча перестал видеть
# чаты» — ровно тот случай, которого ТЗ и боится.
SILENCE_ALERT_HOURS = float(os.getenv('OLX_SILENCE_ALERT_HOURS') or 12)


def collect_alerts(db):
    """Что изменилось с прошлой проверки и о чём надо сказать людям.

    Возвращает список сообщений. Пусто — значит ничего не изменилось, и молчать
    правильно: повод, о котором уже сообщили, повторно не шлётся. Восстановление
    сообщается тоже — иначе человек не узнает, что можно выдохнуть.

    Четыре повода ровно те, что перечислены в разделе 7 ТЗ: простой робота,
    потеря авторизации в кабинете, ошибка передачи в amoCRM и подозрительная
    тишина по кабинету.
    """
    with db._get_cursor() as cursor:
        queries.ensure_accounts(cursor)
        rows = queries.health(cursor, idle_minutes=IDLE_ALERT_MINUTES)
        failures = queries.recent_failures(cursor, minutes=IDLE_ALERT_MINUTES)
        known = queries.alert_states(cursor)

    now = queries.now_almaty()
    silence_edge = now - timedelta(hours=SILENCE_ALERT_HOURS)
    changes = []

    for row in rows:
        code = row.get('code')
        cabinet = cabinets.BY_CODE.get(code)
        title = cabinet.title if cabinet else code

        state, detail = _cabinet_alert_state(row, failures.get(code), silence_edge)
        key = 'cabinet:%s' % code
        previous = (known.get(key) or {}).get('state')
        if previous == state:
            continue
        # Первый прогон не должен рассылать «всё хорошо» по девяти кабинетам:
        # это не новость, а девять сообщений на пустом месте.
        if previous is None and state == 'ok':
            with db._get_cursor() as cursor:
                queries.remember_alert(cursor, key, state, detail)
            continue

        changes.append(_alert_text(title, state, detail))
        with db._get_cursor() as cursor:
            queries.remember_alert(cursor, key, state, detail)

    return changes


def _cabinet_alert_state(row, failures, silence_edge):
    """Одно состояние на кабинет — самое серьёзное из совпавших.

    Порядок важен: кабинет без доступа ОДНОВРЕМЕННО и «не опрашивается», и
    «молчит», но сказать человеку надо про причину, а не про следствия.
    """
    if not row.get('is_enabled'):
        return 'disabled', 'кабинет выключен'
    if row.get('state') == 'needs_auth':
        return 'needs_auth', 'потерян доступ, нужен вход владельца кабинета'
    if row.get('state') == 'not_configured':
        return 'not_configured', 'не заданы client_id и секрет приложения'
    if row.get('is_stale'):
        return 'stale', 'не опрашивался дольше %d минут' % IDLE_ALERT_MINUTES
    if failures:
        return 'amo_failing', 'обращения не доезжают в amoCRM: %d за %d минут' % (
            failures, IDLE_ALERT_MINUTES)
    last_message = row.get('last_message_at')
    if last_message is not None and last_message < silence_edge:
        return 'silent', 'ни одного обращения дольше %g часов' % SILENCE_ALERT_HOURS
    return 'ok', None


def _alert_text(title, state, detail):
    """Текст для Telegram. HTML — как во всех остальных отбивках портала."""
    if state == 'ok':
        return ('✅ <b>OLX · %s</b>\n'
                'Восстановилось, робот снова работает.' % title)
    return '⚠️ <b>OLX · %s</b>\n%s' % (title, detail or state)
