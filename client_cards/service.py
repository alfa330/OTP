# -*- coding: utf-8 -*-
"""Сценарии «Карточки клиента»: там, где база встречается с TEZ APP и Telegram.

Порядок работы с соединением везде один, как у «Обращений» (crm/service.py):

    короткий курсор (прочитать) → закрыть → сеть → короткий курсор (записать)

Сеть НИКОГДА не вызывается с открытым курсором: пул соединений общий с колоколом и
табло, а TEZ APP и Telegram думают секундами.
"""

import logging
import threading
from datetime import timedelta

from . import access, files, queries, rules, telegram, transport
from .tez import SOURCE_ACCOUNTS, TezAccounts

log = logging.getLogger(__name__)

# Профиль аккаунта у нас считается свежим столько; старше — переспрашиваем TEZ APP при звонке.
ACCOUNT_FRESH_HOURS = 24
# За один звонок обновляем не больше стольких привязанных аккаунтов: запросы идут подряд.
ACCOUNT_REFRESH_LIMIT = 3
# «Мои незаполненные» — карточки не старше этого: вчерашнюю ещё доделывают, недельную — нет.
PENDING_DAYS = 2
HISTORY_PAGE = 30
REQUESTS_PAGE = 40
JOURNAL_PAGE = 50


class CardError(Exception):
    """Действие выполнить нельзя. message — человеку, code — экрану, status — HTTP."""

    def __init__(self, message, code='CARD_ERROR', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _spawn_thread(work):
    threading.Thread(target=work, name='client-cards', daemon=True).start()


class ClientCardsService:

    def __init__(self, *, cursor_factory, tez=None, gcs=None, now=rules.now_almaty, spawn=_spawn_thread,
                 migrate_chat=None):
        self._cursor = cursor_factory
        self._tez = tez or TezAccounts()
        self._gcs = gcs or {}
        self._now = now
        self._spawn = spawn
        # db.migrate_telegram_chat_id: группа стала супергруппой — перенести всё, что портал о ней помнит.
        self._migrate_chat = migrate_chat

    # ─── Контекст ────────────────────────────────────────────────────────────

    def context(self, user_id):
        with self._cursor() as cursor:
            return queries.load_access_context(cursor, user_id)

    def phone_settings(self, user_id):
        """Блок client_card для /api/operator/sip_settings: открывать ли телефону карточку
        и сколько секунд длится постобработка."""
        with self._cursor() as cursor:
            ctx = queries.load_access_context(cursor, user_id)
            if not ctx or not access.phone_card_enabled(ctx):
                return {'enabled': False}
            settings = queries.get_settings(cursor, access.card_department_id(ctx))
        return {'enabled': True, 'wrapup_sec': settings['wrapup_sec']}

    def admin_url(self):
        return self._tez.admin_url()

    def tez_source(self):
        return self._tez.source()

    # ─── Карточка звонка ─────────────────────────────────────────────────────

    def _require_card(self, ctx):
        department_id = access.card_department_id(ctx)
        if department_id is None or not access.phone_card_enabled(ctx):
            raise CardError('Карточка клиента вам не включена', 'CARD_DISABLED', 403)
        return department_id

    def open_call(self, ctx, *, phone, direction, call_key):
        """Начался звонок — завести (или найти) его карточку и отдать то, что уже привязано
        к номеру у нас. TEZ APP спрашивается отдельным заходом (lookup): карточка не должна
        ждать чужой сервер."""
        department_id = self._require_card(ctx)
        key = rules.phone_key(phone)
        if not key:
            raise CardError('По этому номеру карточка не заводится', 'CARD_BAD_PHONE')
        direction = direction if direction in rules.DIRECTIONS else rules.DIRECTION_IN
        call_key = rules.clean_text(call_key, 160) or None
        now = self._now()
        with self._cursor() as cursor:
            settings = queries.get_settings(cursor, department_id)
            card_id, _created = queries.open_card(
                cursor, department_id=department_id, operator_id=ctx['user_id'], operator_name=ctx['name'],
                phone=key, direction=direction, call_key=call_key, wrapup_sec=settings['wrapup_sec'], now=now)
            return self._bundle(cursor, card_id)

    def _bundle(self, cursor, card_id):
        """Карточка со всем, что нужно её экрану: аккаунты номера, выбранный аккаунт, запросы."""
        card = queries.get_card(cursor, card_id)
        accounts = queries.phone_accounts(cursor, card['phone'])
        account = None
        if card['account_id']:
            account = next((item for item in accounts if item['id'] == card['account_id']), None) \
                or queries.get_account(cursor, card['account_id'])
        requests = queries.card_requests(cursor, [card_id]).get(card_id, [])
        return {'card': card, 'accounts': accounts, 'account': account, 'requests': requests}

    def _card_for(self, cursor, ctx, card_id, *, edit, for_update=False, live=False):
        """Карточка с проверкой права. live — её собираются заполнять: звонок, который не
        состоялся, заполнять нечем."""
        card = queries.get_card(cursor, card_id, for_update=for_update)
        if not card:
            raise CardError('Карточка не найдена', 'CARD_NOT_FOUND', 404)
        allowed = access.can_edit_card(ctx, card) if edit else access.can_view_card(ctx, card)
        if not allowed:
            raise CardError('Эта карточка вам не открыта', 'CARD_FORBIDDEN', 403)
        if live and card['status'] == rules.STATUS_VOID:
            raise CardError('Звонок не состоялся — карточка не заполняется', 'CARD_VOID', 409)
        return card

    def bundle(self, ctx, card_id):
        with self._cursor() as cursor:
            self._card_for(cursor, ctx, card_id, edit=False)
            return self._bundle(cursor, card_id)

    def call_event(self, ctx, card_id, event):
        """Телефон сообщил о звонке: answered — ответили, ended — звонок кончился.

        Время — серверное: часы компьютера оператора бывают впереди и позади на минуты,
        а по этим отметкам считается срок постобработки. Конец звонка без разговора и
        без единой записи в карточке — карточка не состоялась (rules.void_on_end).
        """
        if event not in ('answered', 'ended'):
            raise CardError('Неизвестное событие звонка', 'CARD_BAD_EVENT')
        now = self._now()
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, for_update=True)
            if int(card['operator_id']) != int(ctx['user_id']):
                raise CardError('Звонок ведёт другой оператор', 'CARD_FORBIDDEN', 403)
            fields = {}
            if event == 'answered' and not card['answered_at'] and not card['ended_at']:
                fields['answered_at'] = now
            if event == 'ended' and not card['ended_at']:
                fields['ended_at'] = now
                # Срок постобработки — тот, что действует к концу звонка: руководитель мог
                # поменять его, пока шёл разговор.
                settings = queries.get_settings(cursor, card['department_id'])
                fields['wrapup_sec'] = settings['wrapup_sec'] if settings else card['wrapup_sec']
                if card['status'] == rules.STATUS_OPEN and rules.void_on_end(card):
                    fields['status'] = rules.STATUS_VOID
            if fields:
                queries.update_card(cursor, card_id, fields, now=now)
            return self._bundle(cursor, card_id)

    def lookup(self, ctx, card_id):
        """Спросить TEZ APP о номере карточки и закрепить найденные аккаунты за номером.

        Возвращает карточку и то, что ответил TEZ APP: {'source', 'ok', 'error',
        'driver_hint'}. TEZ APP молчит — карточка остаётся на привязках, которые уже есть.
        """
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=False)
            linked = queries.phone_accounts(cursor, card['phone'])
        found = self._tez.by_phone(card['phone'])
        refreshed = self._refresh_stale(linked, found)
        now = self._now()
        with self._cursor() as cursor:
            # Найденное по номеру — закрепляем за номером; обновлённые профили — только обновляем.
            for account in found['accounts']:
                account_id = queries.upsert_account(cursor, account, by=ctx['user_id'], now=now, fetched=True)
                queries.link_phone(cursor, card['phone'], account_id, origin='tez',
                                   by=ctx['user_id'], by_name=ctx['name'], now=now)
            for account in refreshed:
                queries.upsert_account(cursor, account, by=ctx['user_id'], now=now, fetched=True)
            bundle = self._bundle(cursor, card_id)
            # Город в карточке — снимок: профиль выбранного аккаунта только что обновился.
            account = bundle['account']
            if account and account.get('city') and not bundle['card'].get('city') \
                    and bundle['card']['status'] != rules.STATUS_VOID:
                queries.update_card(cursor, card_id, {'city': account['city']}, now=now)
                bundle['card']['city'] = account['city']
        bundle['tez'] = {'source': found['source'], 'ok': found['ok'], 'error': found['error'],
                         'driver_hint': found['driver_hint']}
        return bundle

    def _refresh_stale(self, linked, found):
        """Профили привязанных аккаунтов, которые TEZ APP давно (или никогда) не отдавал.

        Только при включённом поиске аккаунтов и только те, что не пришли этим же
        запросом по номеру: человек звонит с чужого номера, а аккаунт у него на своём.
        """
        if found['source'] != SOURCE_ACCOUNTS or not found['ok']:
            return []
        fresh_after = self._now() - timedelta(hours=ACCOUNT_FRESH_HOURS)
        seen = {(item['kind'], item['tez_id']) for item in found['accounts']}
        stale = [item for item in linked
                 if (item['kind'], item['tez_id']) not in seen
                 and (item['fetched_at'] is None or item['fetched_at'] < fresh_after)]
        refreshed = []
        for item in stale[:ACCOUNT_REFRESH_LIMIT]:
            reply = self._tez.by_id(item['kind'], item['tez_id'])
            if reply['ok'] and reply['account']:
                refreshed.append(reply['account'])
        return refreshed

    def choose_account(self, ctx, card_id, account_id):
        """Оператор выбрал аккаунт из привязанных к номеру (или снял выбор: account_id None)."""
        now = self._now()
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, for_update=True, live=True)
            if account_id is None:
                # Заполненная карточка без аккаунта и без отметки «Аккаунта нет» выпала бы из
                # отчёта: заполненные = водители + клиенты + без аккаунта.
                if card['status'] == rules.STATUS_DONE:
                    raise CardError('У заполненной карточки должен остаться аккаунт или отметка «Аккаунта нет»',
                                    'CARD_INCOMPLETE')
                queries.update_card(cursor, card_id, {'account_id': None, 'account_kind': None, 'city': None},
                                    now=now)
                return self._bundle(cursor, card_id)
            account = next((item for item in queries.phone_accounts(cursor, card['phone'])
                            if item['id'] == int(account_id)), None)
            if not account:
                raise CardError('Этот аккаунт не привязан к номеру — приложите ссылку на него',
                                'CARD_ACCOUNT_NOT_LINKED')
            queries.update_card(cursor, card_id, {
                'account_id': account['id'], 'account_kind': account['kind'], 'city': account.get('city')},
                now=now)
            self._fit_topics(cursor, card, account['kind'], now)
            return self._bundle(cursor, card_id)

    def _fit_topics(self, cursor, card, kind, now):
        """У карточки теперь аккаунт вида kind — причины из чужой ветки с неё уходят.

        У водителей и клиентов свои причины (rules.AUDIENCES): причина водителя в карточке
        клиента попала бы в отчёт не той ветки. Причины «Третьего лица» остаются — они
        подходят любому аккаунту. Если причин не осталось, сохранённая карточка снова
        «не заполнена» — оператор выбирает причину из ветки нового аккаунта.
        """
        topics = card.get('topics') or []
        audiences = rules.account_audiences(kind)
        if not topics or not audiences:
            return
        kept = rules.topics_of_audiences(topics, queries.category_index(cursor, card['department_id']), audiences)
        if len(kept) == len(topics):
            return
        queries.set_topics(cursor, card['id'], kept)
        if not kept and card['status'] == rules.STATUS_DONE:
            queries.update_card(cursor, card['id'], {'status': rules.STATUS_OPEN, 'completed_at': None}, now=now)

    def mark_no_account(self, ctx, card_id):
        """Оператор отметил: у звонившего нет аккаунта в Тез (решение владельца 12.11.2026).

        Такую карточку можно сохранить без аккаунта — причина всё равно выбирается, а в
        отчёте эти обращения идут отдельной строкой. Выбранный до этого аккаунт снимается:
        «нет» и «вот этот» вместе не бывают. Привязки номера остаются как были — отметка
        говорит об этом звонке, а не о номере.
        """
        now = self._now()
        with self._cursor() as cursor:
            self._card_for(cursor, ctx, card_id, edit=True, for_update=True, live=True)
            queries.update_card(cursor, card_id, {
                'account_id': None, 'account_kind': rules.KIND_NONE, 'city': None}, now=now)
            return self._bundle(cursor, card_id)

    def attach_link(self, ctx, card_id, link):
        """Оператор приложил ссылку на аккаунт из админки Тез.

        Ссылка закрепляется за номером звонка: при следующем звонке с него аккаунт
        подтянется сам (задача #335, п. 2 — человек зарегистрирован на один номер, а
        звонит с другого). TEZ APP сказал, что такого аккаунта нет, — не закрепляем:
        привязка к несуществующему ID пережила бы опечатку навсегда.
        """
        try:
            kind, tez_id = rules.parse_account_link(link)
        except rules.LinkError as error:
            raise CardError(str(error), 'CARD_BAD_LINK')
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, live=True)
        reply = self._tez.by_id(kind, tez_id)
        if reply['ok'] and reply['account'] is None:
            raise CardError('В Тез нет %s с ID %d — проверьте ссылку'
                            % ('водителя' if kind == rules.KIND_DRIVER else 'клиента', tez_id),
                            'CARD_ACCOUNT_NOT_FOUND', 404)
        account = reply['account'] or {'kind': kind, 'tez_id': tez_id}
        now = self._now()
        with self._cursor() as cursor:
            account_id = queries.upsert_account(cursor, account, by=ctx['user_id'], now=now,
                                                fetched=bool(reply['account']))
            queries.link_phone(cursor, card['phone'], account_id, origin='manual',
                               by=ctx['user_id'], by_name=ctx['name'], now=now)
            saved = queries.get_account(cursor, account_id)
            queries.update_card(cursor, card_id, {
                'account_id': account_id, 'account_kind': saved['kind'], 'city': saved.get('city')}, now=now)
            self._fit_topics(cursor, queries.get_card(cursor, card_id, for_update=True), saved['kind'], now)
            bundle = self._bundle(cursor, card_id)
        self._tez.forget(card['phone'])
        return bundle

    def unlink(self, ctx, card_id, link_id):
        """Снять ошибочную привязку аккаунта к номеру карточки."""
        now = self._now()
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, for_update=True)
            link = queries.get_link(cursor, link_id)
            if not link or link['phone'] != card['phone']:
                raise CardError('Привязка не найдена', 'CARD_LINK_NOT_FOUND', 404)
            if not access.can_unlink(ctx, link, card['department_id']):
                raise CardError('Снять привязку может тот, кто её поставил, или руководитель',
                                'CARD_FORBIDDEN', 403)
            queries.unlink_phone(cursor, link_id)
            if card['account_id'] == link['account_id'] and card['status'] != rules.STATUS_DONE:
                queries.update_card(cursor, card_id, {'account_id': None, 'account_kind': None, 'city': None},
                                    now=now)
            bundle = self._bundle(cursor, card_id)
        self._tez.forget(card['phone'])
        return bundle

    def save_card(self, ctx, card_id, data):
        """Сохранить то, что оператор внёс: причины, комментарий, «продолжение обращения»,
        город аккаунта (пока TEZ APP его не отдаёт — см. _set_city)."""
        now = self._now()
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, for_update=True, live=True)
            fields = {}
            if 'comment' in data:
                fields['comment'] = rules.clean_text(data.get('comment'), rules.COMMENT_LIMIT) or None
            if 'parent_id' in data:
                fields['parent_id'] = self._parent_id(cursor, card, data.get('parent_id'))
            if 'city' in data:
                fields.update(self._set_city(cursor, card, data.get('city'), now))
            topics = None
            if 'topics' in data:
                try:
                    topics = rules.normalize_topics(data.get('topics'),
                                                    queries.category_index(cursor, card['department_id']),
                                                    audiences=rules.card_audiences(card))
                except ValueError as error:
                    raise CardError(str(error), 'CARD_BAD_TOPICS')
                if card['status'] == rules.STATUS_DONE and not topics:
                    raise CardError('У заполненной карточки должна остаться причина обращения',
                                    'CARD_INCOMPLETE')
            if fields:
                queries.update_card(cursor, card_id, fields, now=now)
            if topics is not None:
                queries.set_topics(cursor, card_id, topics)
            return self._bundle(cursor, card_id)

    def _parent_id(self, cursor, card, raw):
        """«Продолжение обращения»: прошлое обращение того же отдела. None — снять связь."""
        if raw in (None, '', 0):
            return None
        try:
            parent_id = int(raw)
        except (TypeError, ValueError):
            raise CardError('Обращение для продолжения указано неверно', 'CARD_BAD_PARENT')
        parent = queries.get_card(cursor, parent_id)
        if (not parent or parent['id'] == card['id'] or parent['department_id'] != card['department_id']
                or parent['status'] == rules.STATUS_VOID):
            raise CardError('Обращение для продолжения не найдено', 'CARD_BAD_PARENT', 404)
        return parent_id

    def _set_city(self, cursor, card, raw, now):
        """Город аккаунта, указанный оператором.

        Отчёт по городам (п. 11) строится по городу аккаунта. Пока TEZ APP профиль не
        отдаёт, город называет оператор — один раз на аккаунт: он пишется в сам аккаунт и
        дальше подставляется сам. Город, который отдал TEZ APP, руками не меняется.
        """
        city = rules.clean_text(raw, 120) or None
        if not card['account_id']:
            raise CardError('Сначала выберите аккаунт', 'CARD_INCOMPLETE')
        account = queries.get_account(cursor, card['account_id'])
        if account and account['fetched_at'] is not None and account.get('city'):
            return {'city': account['city']}
        queries.set_account_city(cursor, card['account_id'], city)
        return {'city': city}

    def complete_card(self, ctx, card_id):
        """«Сохранить»: карточка заполнена. Проверка — на сервере: кнопку можно нажать и
        скриптом. Заполненная карточка — повод проверить, не растут ли обращения по причине."""
        now = self._now()
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, for_update=True, live=True)
            problem = rules.completion_error(card)
            if problem:
                raise CardError(problem, 'CARD_INCOMPLETE')
            if card['status'] != rules.STATUS_DONE:
                queries.update_card(cursor, card_id, {'status': rules.STATUS_DONE, 'completed_at': now}, now=now)
            bundle = self._bundle(cursor, card_id)
        department_id = card['department_id']
        self._spawn(lambda: self.check_alerts(department_id))
        return bundle

    def pending(self, ctx):
        """Карточки оператора, которые он ещё не заполнил."""
        since = self._now() - timedelta(days=PENDING_DAYS)
        with self._cursor() as cursor:
            cards = queries.operator_pending(cursor, ctx['user_id'], since=since)
            names = queries.account_names(cursor, [card['account_id'] for card in cards])
        return cards, names

    def history(self, ctx, *, card_id=None, account_id=None, phone=None, search=None, scope='person',
                before_id=None):
        """Обращения человека (scope='person') или всего отдела (scope='all') для вкладки
        «Обращения» карточки. Отдел — тот, в котором оператор работает с карточкой."""
        department_id = None if card_id else self._require_card(ctx)
        with self._cursor() as cursor:
            if card_id:
                card = self._card_for(cursor, ctx, card_id, edit=False)
                department_id = card['department_id']
                account_id = account_id or card['account_id']
                phone = phone or card['phone']
            if scope == 'all':
                account_id, phone = None, None
            elif not account_id and not phone:
                return [], False, {}, {}
            cards, has_more = queries.history(
                cursor, department_id, account_id=account_id, phone=rules.phone_key(phone) if phone else None,
                search=search, exclude_id=card_id, before_id=before_id, limit=HISTORY_PAGE)
            names = queries.account_names(cursor, [card['account_id'] for card in cards])
            requests = queries.card_requests(cursor, [card['id'] for card in cards])
        return cards, has_more, names, requests

    def card_detail(self, ctx, card_id):
        """Обращение целиком: карточка, аккаунт, запросы — открыть из истории или журнала."""
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=False)
            account = queries.get_account(cursor, card['account_id']) if card['account_id'] else None
            requests = queries.card_requests(cursor, [card_id]).get(card_id, [])
            parent = queries.get_card(cursor, card['parent_id']) if card['parent_id'] else None
        return {'card': card, 'account': account, 'requests': requests, 'parent': parent,
                'can_edit': access.can_edit_card(ctx, card)}

    # ─── Запросы в другие отделы ─────────────────────────────────────────────

    def _check_uploads(self, uploads):
        uploads = [item for item in (uploads or []) if item and item.get('data')]
        if len(uploads) > rules.REQUEST_FILES_LIMIT:
            raise CardError('К запросу можно приложить не больше %d файлов' % rules.REQUEST_FILES_LIMIT,
                            'REQUEST_TOO_MANY_FILES')
        for item in uploads:
            if len(item['data']) > rules.REQUEST_FILE_MAX_BYTES:
                raise CardError('Файл «%s» больше 50 МБ — Telegram такой не примет'
                                % files.safe_name(item.get('name')), 'REQUEST_FILE_TOO_LARGE', 413)
        return uploads

    def _store_uploads(self, uploads):
        """Файлы — в хранилище; возвращает описания для строк переписки."""
        stored = []
        try:
            for item in uploads:
                name = files.safe_name(item.get('name'))
                ref = files.store(self._gcs, data=item['data'], filename=name, content_type=item.get('mime'))
                stored.append({'ref': ref, 'name': name, 'mime': item.get('mime'), 'size': len(item['data']),
                               'kind': files.file_kind(name, item.get('mime'))})
        except files.StorageError as error:
            files.drop(self._gcs, [item['ref'] for item in stored])
            raise CardError('%s — запрос не отправлен' % error, 'REQUEST_STORAGE', 503)
        return stored

    def create_request(self, ctx, *, card_id, destination_id, body, uploads=None):
        """Запрос в другой отдел прямо из карточки (задача #335, п. 6–7).

        В группу уходит сообщение с причиной обращения, аккаунтом, номером и текстом
        оператора; файлы — следом реплаями на него. Возвращает (запрос, ошибка доставки):
        Telegram недоступен — запрос сохранён и отправляется повторно кнопкой.
        """
        text = rules.clean_text(body, rules.REQUEST_BODY_LIMIT)
        if len(text) < 3:
            raise CardError('Напишите, что нужно от отдела', 'REQUEST_EMPTY')
        uploads = self._check_uploads(uploads)
        with self._cursor() as cursor:
            card = self._card_for(cursor, ctx, card_id, edit=True, live=True)
            destination = queries.get_destination(cursor, destination_id) if destination_id else None
            if (not destination or destination['department_id'] != card['department_id']
                    or not destination['is_active']):
                raise CardError('Выберите, в какой отдел уходит запрос', 'REQUEST_BAD_DESTINATION')
            if not destination['is_ready']:
                raise CardError('У отдела «%s» не привязана Telegram-группа — запрос отправить некуда'
                                % destination['title'], 'REQUEST_NO_CHAT', 409)
        stored = self._store_uploads(uploads)
        now = self._now()
        topic = card['topics'][0] if card['topics'] else None
        # Причина в запросе — полным путём: «Запрос → Проблема с заказом → Не может завершить заказ».
        topic_title = (rules.topic_path_text(topic['path']) or None) if topic else None
        try:
            with self._cursor() as cursor:
                request_id = queries.create_request(
                    cursor, department_id=card['department_id'], card_id=card['id'], destination=destination,
                    body=text, account_id=card['account_id'], phone=card['phone'], topic_title=topic_title,
                    by=ctx['user_id'], by_name=ctx['name'], now=now)
                # Первая строка переписки — сам запрос: он виден в нити и до доставки, а после
                # неё получает номер сообщения в группе (по нему находится ответ).
                queries.add_message(cursor, request_id=request_id, direction='out', now=now, body=text,
                                    author_user_id=ctx['user_id'], author_name=ctx['name'])
                for item in stored:
                    queries.add_message(cursor, request_id=request_id, direction='out', now=now,
                                        author_user_id=ctx['user_id'], author_name=ctx['name'], file=item)
        except Exception:
            files.drop(self._gcs, [item['ref'] for item in stored])
            raise
        _ok, error = self.deliver_request(request_id)
        return self.request_detail(ctx, request_id), error

    def _send_following(self, chat_id, send):
        """Отправка с переездом группы: (result, error, чат, в который ушло).

        Группа стала супергруппой — её id сменился навсегда. Переносим адресата (и всё,
        что портал помнит об этом чате) на новый id и повторяем туда же.
        """
        result, error = send(chat_id)
        new_chat_id = getattr(error, 'migrate_to', None) if result is None else None
        if not new_chat_id:
            return result, error, chat_id
        log.warning('client_cards: группа %s стала супергруппой %s — адресаты перенесены', chat_id, new_chat_id)
        try:
            with self._cursor() as cursor:
                queries.move_chat(cursor, chat_id, new_chat_id, now=self._now())
            if self._migrate_chat:
                self._migrate_chat(chat_id, new_chat_id)
        except Exception:  # noqa: BLE001 — перенос не должен стоить самого сообщения
            log.exception('client_cards: перенос чата %s → %s не записан', chat_id, new_chat_id)
        result, error = send(new_chat_id)
        return result, error, new_chat_id

    def deliver_request(self, request_id):
        """Отправить запрос в группу: текст, за ним файлы. Возвращает (ok, ошибка).

        Повтор безопасен: что уже ушло (есть номер сообщения в группе), второй раз не
        уходит — «Отправить ещё раз» досылает только недостающее.
        """
        with self._cursor() as cursor:
            request = queries.get_request(cursor, request_id)
            if not request:
                return False, 'Запрос не найден'
            destination = queries.get_destination(cursor, request['destination_id'])
            account = queries.get_account(cursor, request['account_id']) if request['account_id'] else None
            messages = queries.request_messages(cursor, request_id)
        chat_id = request['tg_chat_id'] or (destination or {}).get('chat_id')
        if not chat_id:
            return False, 'У отдела не привязана Telegram-группа'
        root_id = request['tg_message_id'] if request['delivery_status'] == 'sent' else None

        if not root_id:
            from wiki import offices as wiki_offices

            mentions = [{'username': name} for name in
                        wiki_offices.split_telegram_usernames((destination or {}).get('mention_usernames'))]
            text = telegram.build_request_message(
                request_id=request_id, destination_title=request['destination_title'], body=request['body'],
                account=account, phone=request['phone'], topic_title=request['topic_title'],
                author_name=request['created_by_name'], mentions=mentions, admin_url=self._tez.admin_url())
            result, error, chat_id = self._send_following(
                chat_id, lambda target: transport.send_message(target, text))
            now = self._now()
            if result is None:
                with self._cursor() as cursor:
                    queries.set_delivery(cursor, request_id, status='failed', error=str(error), now=now)
                return False, str(error)
            root_id = result.get('message_id')
            root_row = next((item for item in messages if item['direction'] == 'out' and not item['file_ref']),
                            None)
            with self._cursor() as cursor:
                queries.set_delivery(cursor, request_id, status='sent', chat_id=chat_id, message_id=root_id,
                                     error=None, now=now)
                if root_row:
                    queries.set_message_tg(cursor, root_row['id'], chat_id=chat_id, tg_message_id=root_id)

        failed = self._send_files(request_id, chat_id, root_id,
                                  [item for item in messages
                                   if item['direction'] == 'out' and item['file_ref'] and not item['tg_message_id']])
        return (not failed), (failed or None)

    def _send_files(self, request_id, chat_id, reply_to, messages):
        """Файлы переписки — в группу реплаем. Возвращает текст первой ошибки или None.

        Отказ файла запрос не отменяет: текст уже у отдела, а файл остаётся у нас и
        открывается из карточки; досылается повторной отправкой.
        """
        first_error = None
        for item in messages:
            if not files.is_stored(item['file_ref']):
                continue
            data = files.load(self._gcs, item['file_ref'])
            if data is None:
                first_error = first_error or 'Файл «%s» не найден в хранилище' % (item['file_name'] or '')
                continue
            result, error, chat_id = self._send_following(
                chat_id, lambda target, item=item, data=data: transport.send_file(
                    target, name=item['file_name'] or 'file', data=data, mime=item['file_mime'],
                    kind=item['file_kind'], reply_to_message_id=reply_to,
                    caption=telegram.file_caption(request_id)))
            if result is None:
                log.warning('client_cards: файл запроса %s не ушёл: %s', request_id, error)
                first_error = first_error or 'Файл «%s» не ушёл в группу: %s' % (item['file_name'] or '', error)
                continue
            with self._cursor() as cursor:
                queries.set_message_tg(cursor, item['id'], chat_id=chat_id, tg_message_id=result.get('message_id'))
        return first_error

    def _request_for(self, cursor, ctx, request_id, *, act=False):
        request = queries.get_request(cursor, request_id)
        if not request:
            raise CardError('Запрос не найден', 'REQUEST_NOT_FOUND', 404)
        allowed = access.can_act_on_request(ctx, request) if act else access.can_view_request(ctx, request)
        if not allowed:
            raise CardError('Этот запрос вам не открыт', 'REQUEST_FORBIDDEN', 403)
        return request

    def request_detail(self, ctx, request_id, *, seen=True):
        """Запрос с перепиской. Автор, открыв его, гасит своё «непрочитано»."""
        now = self._now()
        with self._cursor() as cursor:
            request = self._request_for(cursor, ctx, request_id)
            if seen and request['created_by'] == ctx['user_id'] and request['author_unread_count']:
                queries.mark_seen(cursor, request_id, ctx['user_id'], now=now)
                request['author_unread_count'] = 0
            messages = queries.request_messages(cursor, request_id)
            account = queries.get_account(cursor, request['account_id']) if request['account_id'] else None
        return {'request': request, 'messages': messages, 'account': account,
                'can_act': access.can_act_on_request(ctx, request)}

    def list_requests(self, ctx, *, department_id=None, mine=False, status=None, search=None, before=None):
        """Запросы раздела: руководителю — все запросы отдела, остальным — свои."""
        departments = access.section_department_ids(ctx)
        if department_id is not None:
            departments = [dep for dep in departments if dep == int(department_id)]
        if not departments:
            return [], False, {}
        managed = set(access.managed_department_ids(ctx))
        only_own = mine or not all(dep in managed for dep in departments)
        with self._cursor() as cursor:
            rows, has_more = queries.list_requests(
                cursor, departments, author_id=ctx['user_id'] if only_own else None, status=status,
                search=search, before=before, limit=REQUESTS_PAGE)
            names = queries.account_names(cursor, [row['account_id'] for row in rows])
        return rows, has_more, names

    def reply(self, ctx, request_id, *, body, uploads=None, reply_to=None):
        """Оператор пишет в открытую нить запроса: текст и файлы уходят в группу реплаем.

        reply_to — строка переписки, на которую отвечают; номер сообщения в группе берётся
        из базы и только в пределах этого запроса — номером с клиента бота можно было бы
        заставить ответить на любое сообщение рабочей группы.
        """
        text = rules.clean_text(body, 3000)
        uploads = self._check_uploads(uploads)
        if not text and not uploads:
            raise CardError('Напишите сообщение или приложите файл', 'REQUEST_EMPTY')
        with self._cursor() as cursor:
            request = self._request_for(cursor, ctx, request_id, act=True)
            if request['status'] in rules.REQUEST_CLOSED:
                raise CardError('Запрос закрыт — верните его в работу, чтобы написать', 'REQUEST_CLOSED', 409)
            if request['delivery_status'] != 'sent' or not request['tg_message_id']:
                raise CardError('Запрос ещё не доставлен в группу — отправьте его ещё раз',
                                'REQUEST_NOT_DELIVERED', 409)
            target = queries.get_message(cursor, request_id, reply_to) if reply_to else None
            if reply_to and not (target and target['tg_message_id']):
                raise CardError('Сообщение, на которое отвечаете, не найдено', 'REQUEST_BAD_REPLY', 404)
        stored = self._store_uploads(uploads)
        chat_id = request['tg_chat_id']
        anchor = (target or {}).get('tg_message_id') or request['tg_message_id']
        now = self._now()
        if text:
            message = telegram.build_reply_message(request_id=request_id, author_name=ctx['name'], body=text)
            result, error, chat_id = self._send_following(
                chat_id, lambda dest: transport.send_message(dest, message, reply_to_message_id=anchor))
            if result is None:
                files.drop(self._gcs, [item['ref'] for item in stored])
                raise CardError('Сообщение не ушло в группу: %s' % error, 'REQUEST_SEND_FAILED', 502)
            with self._cursor() as cursor:
                queries.add_message(cursor, request_id=request_id, direction='out', now=now, body=text,
                                    author_user_id=ctx['user_id'], author_name=ctx['name'],
                                    tg_chat_id=chat_id, tg_message_id=result.get('message_id'),
                                    reply_to_tg_message_id=anchor)
                queries.touch_outbound(cursor, request_id, now=now)
            anchor = result.get('message_id')
        error = None
        if stored:
            with self._cursor() as cursor:
                rows = []
                for item in stored:
                    message_id = queries.add_message(
                        cursor, request_id=request_id, direction='out', now=now,
                        author_user_id=ctx['user_id'], author_name=ctx['name'], file=item)
                    rows.append({'id': message_id, 'file_ref': item['ref'], 'file_name': item['name'],
                                 'file_mime': item['mime'], 'file_kind': item['kind'], 'tg_message_id': None,
                                 'direction': 'out'})
                queries.touch_outbound(cursor, request_id, now=now)
            error = self._send_files(request_id, chat_id, anchor, rows)
        return self.request_detail(ctx, request_id), error

    def set_request_status(self, ctx, request_id, status):
        """Закрыть запрос («Решён», «Отменён») или вернуть в работу.

        Возврат в работу: «Есть ответ», если отдел уже отвечал, иначе «Ждёт ответа». О
        закрытии группе уходит короткая отбивка — иначе там продолжат разбираться.
        """
        if status not in ('resolved', 'cancelled', 'reopen'):
            raise CardError('Неизвестное действие', 'REQUEST_BAD_STATUS')
        now = self._now()
        with self._cursor() as cursor:
            request = self._request_for(cursor, ctx, request_id, act=True)
            target, changed = status, False
            if status == 'reopen':
                target = rules.REQUEST_ANSWERED if request['last_inbound_at'] else rules.REQUEST_OPEN
            # «Вернуть в работу» у запроса, который и так в работе, ничего не меняет.
            if status != 'reopen' or request['status'] in rules.REQUEST_CLOSED:
                changed = queries.set_request_status(cursor, request_id, target, by=ctx['user_id'],
                                                     by_name=ctx['name'], now=now)
            if request['created_by'] == ctx['user_id']:
                queries.mark_seen(cursor, request_id, ctx['user_id'], now=now)
        if changed and target in rules.REQUEST_CLOSED and request['tg_chat_id'] and request['tg_message_id']:
            notice = telegram.build_status_notice(request_id=request_id, status=target, actor_name=ctx['name'])
            self._send_following(request['tg_chat_id'], lambda dest: transport.send_message(
                dest, notice, reply_to_message_id=request['tg_message_id']))
        return self.request_detail(ctx, request_id)

    def retry_delivery(self, ctx, request_id):
        with self._cursor() as cursor:
            self._request_for(cursor, ctx, request_id, act=True)
        _ok, error = self.deliver_request(request_id)
        return self.request_detail(ctx, request_id), error

    def request_file(self, ctx, request_id, message_id):
        """Вложение переписки: {'url'} — ссылка на наш файл, {'data', 'mime', 'name'} — файл из
        Telegram (его прислали в группу, у нас его нет)."""
        with self._cursor() as cursor:
            self._request_for(cursor, ctx, request_id)
            message = queries.get_message(cursor, request_id, message_id)
        if not message or not message['file_ref']:
            raise CardError('Файл не найден', 'REQUEST_FILE_NOT_FOUND', 404)
        if files.is_stored(message['file_ref']):
            url = files.signed_url(self._gcs, message['file_ref'], content_type=message['file_mime'],
                                   filename=message['file_name'])
            if not url:
                raise CardError('Файл сейчас не открыть — хранилище не ответило', 'REQUEST_STORAGE', 503)
            return {'url': url}
        data, error = transport.fetch_file(message['file_ref'])
        if data is None:
            raise CardError('Telegram не отдал файл: %s' % error, 'REQUEST_FILE_UNAVAILABLE', 502)
        return {'data': data, 'mime': message['file_mime'], 'name': message['file_name']}

    def ingest_group_reply(self, *, chat_id, reply_to_message_id, message):
        """Ответ сотрудника в группе → строка переписки запроса и «непрочитано» автору.

        Возвращает {'request_id'} либо None — ответили не на запрос (реплай на сообщение
        другого раздела): не ошибка, просто не наше.
        """
        body = telegram.message_text(message)
        attachment = telegram.extract_attachment(message)
        if not body and not attachment:
            return None
        from_user = message.get('from') if isinstance(message, dict) else getattr(message, 'from_user', None)
        get = (from_user.get if isinstance(from_user, dict)
               else (lambda key, default=None: getattr(from_user, key, default)))
        message_id = (message.get('message_id') if isinstance(message, dict)
                      else getattr(message, 'message_id', None))
        file = None
        if attachment:
            kind = attachment['kind']
            file = {'ref': attachment['file_id'], 'name': attachment.get('name'), 'mime': attachment.get('mime'),
                    'size': attachment.get('size'),
                    'kind': (files.KIND_PHOTO if kind == 'photo'
                             else files.KIND_VIDEO if kind in ('video', 'video_note', 'animation')
                             else files.KIND_DOCUMENT)}
        now = self._now()
        with self._cursor() as cursor:
            request_id = queries.find_request_by_tg_message(cursor, chat_id, reply_to_message_id)
            if not request_id:
                return None
            added = queries.add_message(
                cursor, request_id=request_id, direction='in', now=now, body=body,
                author_name=telegram.sender_name(from_user), tg_chat_id=chat_id, tg_message_id=message_id,
                tg_from_id=get('id') if from_user is not None else None,
                tg_username=get('username') if from_user is not None else None,
                reply_to_tg_message_id=reply_to_message_id, file=file)
            # Повтор апдейта Telegram: ответ уже в нити, второй раз автора не тревожим.
            if added is not None:
                queries.touch_inbound(cursor, request_id, now=now)
        return {'request_id': request_id}

    # ─── Алерты о росте обращений ────────────────────────────────────────────

    def check_alerts(self, department_id):
        """Проверить, не выросли ли обращения по какой-то причине, и написать руководителю.

        Зовётся после каждой заполненной карточки. Решение и запись в журнал — одной
        транзакцией под замком отдела: вторая проверка в ту же секунду увидит уже
        записанный алерт и промолчит. Telegram — после фиксации.
        """
        try:
            now = self._now()
            with self._cursor() as cursor:
                settings = queries.get_settings(cursor, department_id)
                if not settings or not settings['alerts']['enabled']:
                    return []
                queries.lock_alerts(cursor, department_id)
                picked = self._pick_alerts(cursor, department_id, settings['alerts'], now)
                if not picked:
                    return []
                window = settings['alerts']['window_min']
                since = now - timedelta(minutes=window)
                index = queries.category_index(cursor, department_id)
                for node in picked:
                    details = queries.alert_breakdown(cursor, department_id, node['id'], index,
                                                      since=since, until=now)
                    node.update(details)
                    node['alert_id'] = queries.insert_alert(cursor, department_id, node, window_min=window,
                                                            details=details, now=now)
                recipients = queries.alert_recipients(cursor, department_id)
                names = queries.departments(cursor, [department_id])
            text = telegram.build_alert_message(
                department_name=names[0]['name'] if names else None, items=picked, window_min=window)
            delivered = False
            for recipient in recipients:
                result, error, _chat = self._send_following(
                    recipient['chat_id'], lambda dest: transport.send_message(dest, text))
                if result is None:
                    log.warning('client_cards: алерт не дошёл (%s): %s', recipient['label'], error)
                else:
                    delivered = True
            if not recipients:
                log.warning('client_cards: алерту отдела %s некуда уйти — у главы нет Telegram и '
                            'группа не выбрана', department_id)
            if delivered:
                with self._cursor() as cursor:
                    queries.mark_alerts_delivered(cursor, [node['alert_id'] for node in picked])
            return picked
        except Exception:  # noqa: BLE001 — фоновая проверка: сбой не должен ничего ронять
            log.exception('client_cards: проверка алертов отдела %s упала', department_id)
            return []

    def _pick_alerts(self, cursor, department_id, alerts, now):
        window = alerts['window_min']
        since = now - timedelta(minutes=window)
        index = queries.category_index(cursor, department_id)
        counts = queries.alert_counts(cursor, department_id, index, since=since, until=now)
        if not counts:
            return []
        baseline = queries.alert_baseline(
            cursor, department_id, index, until=now, window_min=window, days=rules.ALERT_BASELINE_DAYS)
        last = queries.last_alerts(cursor, department_id,
                                   day_start=now.replace(hour=0, minute=0, second=0, microsecond=0))
        nodes = []
        # Узлы — причины всех уровней: подтема, причина над ней и так до основной. Внутри
        # уровня — от большей волны к меньшей: так они и встанут в сообщении.
        for node_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            path = rules.category_path(index, node_id)
            if not path:
                continue
            titles = rules.category_titles(index, node_id)
            previous = last.get(node_id) or {}
            nodes.append({
                'id': node_id, 'parent_id': index[node_id]['parent_id'], 'ancestors': path[-2::-1],
                'count': count, 'baseline': baseline.get(node_id, 0.0),
                'title': titles[-1], 'parent_title': rules.PATH_SEPARATOR.join(titles[:-1]) or None,
                'audience': index[node_id].get('audience'),
                'last': {'at': previous.get('at'), 'count': previous.get('count')} if previous else None,
                'sent_today': previous.get('sent_today') or 0})
        return rules.pick_alerts(nodes, now=now, settings=alerts)

    def send_test_alert(self, ctx, department_id):
        """Проверочное сообщение по адресам алертов: дошло ли оно — руководитель видит сам."""
        if not access.can_manage(ctx, department_id):
            raise CardError('Настройки раздела вам не открыты', 'CARD_FORBIDDEN', 403)
        with self._cursor() as cursor:
            recipients = queries.alert_recipients(cursor, department_id)
        if not recipients:
            raise CardError('Алерту некуда уйти: у главы отдела не подключён Telegram и группа не выбрана',
                            'ALERT_NO_RECIPIENTS', 409)
        text = ('✅ <b>Проверка алертов «Карточки клиента»</b>\n'
                'Сюда придёт сообщение, когда обращений по какой-то причине станет заметно больше обычного.')
        sent, errors = [], []
        for recipient in recipients:
            result, error, _chat = self._send_following(
                recipient['chat_id'], lambda dest: transport.send_message(dest, text))
            (sent if result is not None else errors).append(
                recipient['label'] if result is not None else '%s: %s' % (recipient['label'], error))
        return {'sent': sent, 'errors': errors}
