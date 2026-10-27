"""Процесс «Кодов 2FA»: запрос → решение главы → код в Telegram → сроки.

Один класс на все входы: роуты раздела (портал), нажатия кнопок в Telegram (отдельный пул
раздела) и будильник сроков. Порядок в каждом шаге один и тот же:

    1. транзакция: проверить всё, записать решение и журнал, зафиксировать;
    2. после фиксации — Telegram: сначала код сотруднику, потом отметки у главы и уборка;
    3. итог отправки — второй короткой транзакцией (id сообщения, «код не дошёл»).

Telegram строго после фиксации: иначе откат оставил бы у сотрудника код без записи о
подтверждении — ровно то, что ТЗ запрещает («исключить выдачу кода без одобрения»,
«исключить потерю записей в журнале»). Обратный сбой — решение записано, а Telegram не
ответил — виден: в журнале «код не дошёл», в разделе — причина.

Самих цифр нет ни в базе, ни в журнале, ни в логе: код считается в момент отправки — прямо
перед вызовом Telegram, а не при решении: медленная правка сообщения главы не должна съедать
его 10 секунд запаса (ТЗ п. 5) — и живёт только в сообщении, которое стирается через минуту.
"""

import logging
import time

from . import access, crypto, otpauth, queries, rules, totp

# Второе нажатие «Код не подошёл» в эти секунды — то же нажатие, а не новая просьба: без этого
# два быстрых нажатия присылали два сообщения с цифрами.
RESEND_GUARD_SECONDS = 3

# Telegram не стёр или не удалил сообщение — повторить через столько секунд.
MESSAGE_RETRY_SECONDS = 30


def _iso(moment):
    return rules.iso(moment)


def public_request(request):
    """Запрос для экрана — без telegram_id и служебного."""
    return {
        'id': request['id'],
        'account_id': request['account_id'],
        'account_title': request['account_title'],
        'service': request['service'],
        'requester_id': request['requester_id'],
        'requester_name': request['requester_name'],
        'status': request['status'],
        'status_label': rules.STATUS_LABELS.get(request['status'], request['status']),
        'created_at': _iso(request['created_at']),
        'expires_at': _iso(request['expires_at']),
        'decided_at': _iso(request['decided_at']),
        'decided_by_name': request['decided_by_name'],
        'decided_via': request['decided_via'],
        'resend_until': _iso(request['resend_until']),
        'codes_sent': request['codes_sent'],
        'delivery_error': request['delivery_error'],
    }


class Twofa:
    def __init__(self, *, cursor_factory, tg, clock=time.time, wake=None):
        self._cursor = cursor_factory
        self.tg = tg
        self._clock = clock
        self._wake = wake or (lambda: None)

    # ─── Общее ───────────────────────────────────────────────────────────────

    def now(self):
        return float(self._clock())

    def context(self, user_id, cursor=None):
        if cursor is not None:
            return queries.load_access_context(cursor, user_id, crypto.key_status())
        with self._cursor() as own:
            return queries.load_access_context(own, user_id, crypto.key_status())

    def wake(self):
        try:
            self._wake()
        except Exception:  # noqa: BLE001 — будильник доберётся сам при следующем сроке
            logging.exception('twofa: не разбудил будильник')

    # ─── Запрос кода ─────────────────────────────────────────────────────────

    def request_code(self, ctx, account_id):
        """Сотрудник просит код. outcome: created / pending / forbidden / no_telegram /
        no_approver / not_found."""
        if not access.can_request(ctx, account_id):
            return {'outcome': 'forbidden'}
        if not ctx.get('telegram_id'):
            return {'outcome': 'no_telegram'}
        now = self.now()
        # Сгоревшие запросы, до которых будильник ещё не дошёл, закрываем сразу: иначе
        # сгоревший держал бы место ждущего, и новый запрос не создался бы.
        self.expire_due(now)
        now_dt = rules.almaty(now)
        with self._cursor() as cursor:
            account = queries.get_account(cursor, account_id)
            if not account or not account['is_active']:
                return {'outcome': 'not_found'}
            people = queries.approvers(cursor)
            if not people:
                return {'outcome': 'no_approver'}
            existing = queries.pending_of(cursor, account_id, ctx['user_id'], now_dt)
            if existing:
                return {'outcome': 'pending', 'request': existing}
            request_id = queries.insert_request(
                cursor, account_id=account_id, requester_id=ctx['user_id'], now=now_dt,
                expires_at=rules.almaty(now + rules.REQUEST_TTL_SECONDS))
            if request_id is None:
                return {'outcome': 'pending', 'request': queries.pending_of(cursor, account_id, ctx['user_id'], now_dt)}
            queries.log_event(cursor, 'requested', now=now_dt, request_id=request_id, account_id=account['id'],
                              account_title=account['title'], actor_id=ctx['user_id'], actor_name=ctx['name'])
            request = queries.get_request(cursor, request_id)
        # Уведомление — только включившим тумблер «уведомления о запросах» (по умолчанию он
        # выключен) и только тем, у кого есть Telegram; колокол им покажет запрос сам.
        self._notify_approvers(request, [p for p in people if p['notify'] and p['telegram_id']])
        self.wake()
        return {'outcome': 'created', 'request': request}

    def _notify_approvers(self, request, people):
        if not people:
            return
        text = rules.approval_text(
            requester_name=request['requester_name'], account_title=request['account_title'],
            service=request['service'], created_at=rules.epoch(request['created_at']),
            expires_at=rules.epoch(request['expires_at']))
        sent = []
        for person in people:
            message_id, _error = self.tg.send(person['telegram_id'], text, rules.approval_markup(request['id']))
            if message_id:
                sent.append((person['telegram_id'], message_id))
        if not sent:
            return
        with self._cursor() as cursor:
            for chat_id, message_id in sent:
                queries.insert_message(cursor, request_id=request['id'], kind='approval', chat_id=chat_id,
                                       message_id=message_id, now=rules.almaty(self.now()))
        # Глава мог решить в портале, пока сообщение летело. Статус читаем ПОСЛЕ фиксации
        # вставки, отдельной транзакцией: решение, зафиксированное между вставкой и её
        # фиксацией, своего сообщения ещё не видело, и кнопки у главы остались бы.
        with self._cursor() as cursor:
            current = queries.get_request(cursor, request['id'])
        if current and current['status'] != 'pending':
            self._close_approvals(current)

    # ─── Решение главы ───────────────────────────────────────────────────────

    def decide(self, ctx, request_id, decision, via):
        """Подтвердить или отклонить. Первое нажатие решает; второе — outcome 'already'.

        outcome: approved / rejected / already / expired / cancelled / forbidden / key_missing /
        not_found / key_error.
        """
        if decision not in ('approve', 'reject'):
            raise ValueError(decision)
        if not access.can_approve(ctx):
            # Подтверждающий, у которого раздел закрыт только из-за ключа шифрования, должен
            # узнать настоящую причину, а не «подтверждают глава и супер-админы».
            if (access.is_working(ctx) and (access.is_approver(ctx) or access.is_super_admin(ctx))
                    and not access.key_ready(ctx)):
                return {'outcome': 'key_missing'}
            return {'outcome': 'forbidden'}
        now = self.now()
        now_dt = rules.almaty(now)
        secret = None
        with self._cursor() as cursor:
            request = queries.get_request(cursor, request_id, for_update=True)
            if not request:
                return {'outcome': 'not_found'}
            if request['status'] != 'pending':
                return {'outcome': 'already', 'request': request}
            if request['expires_at'] <= now_dt:
                outcome = {'outcome': 'expired', 'request': request}
            else:
                outcome = None
            if outcome is None:
                requester = queries.load_access_context(cursor, request['requester_id'], ctx.get('key_status'))
                account = queries.get_account(cursor, request['account_id'], with_secret=True)
                if not (requester and account and account['is_active']
                        and access.can_request(requester, request['account_id'])):
                    # Пока запрос ждал, у сотрудника сняли доступ, его уволили или
                    # удалили аккаунт: кода быть не должно, даже если глава нажал «да».
                    self._cancel(cursor, [request['id']], now_dt)
                    outcome = {'outcome': 'cancelled'}
                elif decision == 'approve':
                    try:
                        secret = crypto.decrypt(account['secret_enc'], account['id'])
                    except (crypto.KeyMissing, ValueError):
                        logging.exception('twofa: ключ аккаунта %s не расшифрован', account['id'])
                        return {'outcome': 'key_error', 'request': request}
                    ok = queries.decide(cursor, request_id, status='approved', actor_id=ctx['user_id'], via=via,
                                        now=now_dt, resend_until=rules.almaty(now + rules.RESEND_WINDOW_SECONDS))
                    if not ok:
                        return {'outcome': 'already', 'request': queries.get_request(cursor, request_id)}
                    queries.mark_issued(cursor, request_id, now_dt)
                    queries.log_event(cursor, 'approved', now=now_dt, request_id=request['id'],
                                      account_id=request['account_id'], account_title=request['account_title'],
                                      actor_id=ctx['user_id'], actor_name=ctx['name'],
                                      subject_id=request['requester_id'], subject_name=request['requester_name'],
                                      details={'via': via})
                else:
                    ok = queries.decide(cursor, request_id, status='rejected', actor_id=ctx['user_id'], via=via,
                                        now=now_dt)
                    if not ok:
                        return {'outcome': 'already', 'request': queries.get_request(cursor, request_id)}
                    queries.log_event(cursor, 'rejected', now=now_dt, request_id=request['id'],
                                      account_id=request['account_id'], account_title=request['account_title'],
                                      actor_id=ctx['user_id'], actor_name=ctx['name'],
                                      subject_id=request['requester_id'], subject_name=request['requester_name'],
                                      details={'via': via})
            request = queries.get_request(cursor, request_id)
        if outcome and outcome['outcome'] == 'expired':
            self.expire_due(now)
            return {'outcome': 'expired', 'request': request}
        if outcome and outcome['outcome'] == 'cancelled':
            self._close_approvals(request)
            return {'outcome': 'cancelled', 'request': request}
        delivered = None
        # Сначала код сотруднику: он ждёт его прямо сейчас, а отметка у главы подождёт.
        if request['status'] == 'approved':
            delivered, _issued = self._send_code(request, secret, 'code_sent')
        elif request['status'] == 'rejected' and request['requester_telegram_id']:
            self.tg.send(request['requester_telegram_id'], rules.rejected_text(account_title=request['account_title']))
        self._close_approvals(request)
        self.wake()
        with self._cursor() as cursor:
            request = queries.get_request(cursor, request_id)
        return {'outcome': request['status'], 'request': request, 'delivered': delivered}

    def _close_approvals(self, request):
        """Снять кнопки у главы и отметить решение («после нажатия кнопки исчезают», п. 4)."""
        with self._cursor() as cursor:
            messages = queries.claim_messages(cursor, request['id'], 'approval', to_state='closed',
                                              from_states=('shown',))
        if not messages:
            return
        decided_at = request['decided_at'] or request['expires_at']
        text = rules.approval_closed_text(
            requester_name=request['requester_name'], account_title=request['account_title'],
            status=request['status'], decided_at=rules.epoch(decided_at),
            decided_by=request['decided_by_name'])
        for message in messages:
            self.tg.edit(message['chat_id'], message['message_id'], text)

    def _cancel(self, cursor, request_ids, now_dt):
        """Запросы закрыты без решения: доступ снят или аккаунт удалён. Внутри транзакции."""
        for request_id in request_ids:
            request = queries.get_request(cursor, request_id)
            if not request:
                continue
            cursor.execute("UPDATE twofa_requests SET status = 'cancelled', decided_at = %s "
                           "WHERE id = %s AND status = 'pending'", (now_dt, request_id))
            if cursor.rowcount:
                queries.log_event(cursor, 'cancelled', now=now_dt, request_id=request_id,
                                  account_id=request['account_id'], account_title=request['account_title'],
                                  subject_id=request['requester_id'], subject_name=request['requester_name'])

    def finish_cancelled(self, request_ids):
        """После фиксации: снять кнопки у главы у отменённых запросов."""
        if not request_ids:
            return
        with self._cursor() as cursor:
            requests_ = [queries.get_request(cursor, rid) for rid in request_ids]
        for request in requests_:
            if request:
                self._close_approvals(request)

    # ─── Код ─────────────────────────────────────────────────────────────────

    def _send_code(self, request, secret, event_kind):
        """Код сотруднику в Telegram + сроки «стереть цифры» и «убрать сообщение».

        Код считается здесь, прямо перед вызовом Telegram. Возвращает (дошёл ли, выданный код).
        """
        now = self.now()
        issued = totp.issue(secret, now)
        resend_until = rules.epoch(request['resend_until']) if request['resend_until'] else now
        chat_id = request['requester_telegram_id']
        message_id, error = (None, 'У сотрудника не привязан Telegram')
        if chat_id:
            markup = rules.resend_markup(request['id']) if resend_until > now else None
            message_id, error = self.tg.send(
                chat_id, rules.code_text(account_title=request['account_title'], issued=issued, now=now), markup)
        now_dt = rules.almaty(now)
        with self._cursor() as cursor:
            if message_id:
                visible_until = now + rules.CODE_VISIBLE_SECONDS
                if resend_until > visible_until:
                    hide_at, delete_at = rules.almaty(visible_until), rules.almaty(resend_until)
                else:
                    hide_at, delete_at = None, rules.almaty(visible_until)
                queries.insert_message(cursor, request_id=request['id'], kind='code', chat_id=chat_id,
                                       message_id=message_id, now=now_dt, hide_at=hide_at, delete_at=delete_at)
                queries.mark_delivery(cursor, request['id'], counter=issued['counter'])
                queries.log_event(cursor, event_kind, now=now_dt, request_id=request['id'],
                                  account_id=request['account_id'], account_title=request['account_title'],
                                  subject_id=request['requester_id'], subject_name=request['requester_name'],
                                  details={'valid_until': rules.clock(issued['valid_until'])})
            else:
                queries.mark_delivery(cursor, request['id'], error=error)
                queries.log_event(cursor, 'code_failed', now=now_dt, request_id=request['id'],
                                  account_id=request['account_id'], account_title=request['account_title'],
                                  subject_id=request['requester_id'], subject_name=request['requester_name'],
                                  details={'error': error})
        return bool(message_id), issued

    def resend(self, user_id, request_id):
        """«Код не подошёл»: новый код без главы, 3 минуты после подтверждения.

        Новый код уходит ПЕРВЫМ; старое сообщение удаляется только после того, как новое
        дошло, — иначе сбой отправки оставлял сотрудника и без кода, и без кнопки.

        outcome: resent / same_code / just_sent / window_closed / forbidden / not_found /
        key_error / failed.
        """
        now = self.now()
        now_dt = rules.almaty(now)
        with self._cursor() as cursor:
            request = queries.get_request(cursor, request_id, for_update=True)
            if not request:
                return {'outcome': 'not_found'}
            if int(request['requester_id']) != int(user_id) or request['status'] != 'approved':
                return {'outcome': 'forbidden'}
            if not request['resend_until'] or request['resend_until'] <= now_dt:
                return {'outcome': 'window_closed'}
            last = request['last_issued_at']
            if last is not None and (rules.epoch(now_dt) - rules.epoch(last)) < RESEND_GUARD_SECONDS:
                return {'outcome': 'just_sent'}
            requester = queries.load_access_context(cursor, user_id, crypto.key_status())
            account = queries.get_account(cursor, request['account_id'], with_secret=True)
            # Сняли доступ или удалили аккаунт после подтверждения — повторного кода нет.
            if not (requester and account and account['is_active']
                    and access.can_request(requester, request['account_id'])):
                return {'outcome': 'forbidden'}
            try:
                secret = crypto.decrypt(account['secret_enc'], account['id'])
            except (crypto.KeyMissing, ValueError):
                logging.exception('twofa: ключ аккаунта %s не расшифрован', account['id'])
                return {'outcome': 'key_error'}
            queries.mark_issued(cursor, request_id, now_dt)
            old_ids = queries.code_message_ids(cursor, request_id)
        delivered, issued = self._send_code(request, secret, 'code_resent')
        if not delivered:
            # Новый код не ушёл — отметку «только что выдан» вернуть, иначе следующее нажатие
            # в те же 3 секунды ответило бы «код уже отправлен», а его нет.
            with self._cursor() as cursor:
                cursor.execute("UPDATE twofa_requests SET last_issued_at = %s WHERE id = %s",
                               (last, int(request_id)))
            return {'outcome': 'failed'}
        with self._cursor() as cursor:
            old = queries.claim_message_ids(cursor, old_ids, to_state='deleted')
        for message in old:
            self.tg.delete(message['chat_id'], message['message_id'])
        self.wake()
        same = request['last_counter'] is not None and issued['counter'] == request['last_counter']
        return {'outcome': 'same_code' if same else 'resent',
                'seconds_left': max(0, int(issued['valid_until'] - now)),
                'starts_in': max(0, int(round(issued['valid_from'] - now)))}

    # ─── Сроки ───────────────────────────────────────────────────────────────

    def expire_due(self, now=None):
        """Сгоревшие запросы: отметка у главы, сообщение сотруднику («отправьте новый», п. 3)."""
        now = self.now() if now is None else now
        now_dt = rules.almaty(now)
        with self._cursor() as cursor:
            ids = queries.claim_expired(cursor, now_dt)
            expired = []
            for request_id in ids:
                request = queries.get_request(cursor, request_id)
                if not request:
                    continue
                queries.log_event(cursor, 'expired', now=now_dt, request_id=request_id,
                                  account_id=request['account_id'], account_title=request['account_title'],
                                  subject_id=request['requester_id'], subject_name=request['requester_name'])
                expired.append(request)
        for request in expired:
            self._close_approvals(request)
            if request['requester_telegram_id']:
                self.tg.send(request['requester_telegram_id'],
                             rules.expired_text(account_title=request['account_title']))
        return expired

    def hide_due(self, now):
        """Стереть цифры через минуту. Telegram не ответил — повторить (queries.retry_hide)."""
        with self._cursor() as cursor:
            rows = queries.claim_due_hide(cursor, rules.almaty(now))
        retry_at = rules.almaty(now + MESSAGE_RETRY_SECONDS)
        for row in rows:
            if row['state'] == 'hidden':
                resend_until = rules.epoch(row['resend_until'])
                done = self.tg.edit(row['chat_id'], row['message_id'],
                                    rules.code_hidden_text(account_title=row['account_title'],
                                                           resend_until=resend_until),
                                    rules.resend_markup(row['request_id']))
                if not done:
                    with self._cursor() as cursor:
                        queries.retry_hide(cursor, row['id'], retry_at)
            elif not self.tg.delete(row['chat_id'], row['message_id']):
                with self._cursor() as cursor:
                    queries.retry_delete(cursor, row['id'], retry_at)
        return rows

    def delete_due(self, now):
        with self._cursor() as cursor:
            rows = queries.claim_due_delete(cursor, rules.almaty(now))
        retry_at = rules.almaty(now + MESSAGE_RETRY_SECONDS)
        for row in rows:
            if not self.tg.delete(row['chat_id'], row['message_id']):
                with self._cursor() as cursor:
                    queries.retry_delete(cursor, row['id'], retry_at)
        return rows

    def run_due(self):
        """Шаг будильника: всё просроченное — сейчас; вернуть секунды эпохи следующего срока."""
        now = self.now()
        self.expire_due(now)
        self.hide_due(now)
        self.delete_due(now)
        with self._cursor() as cursor:
            moment = queries.next_due(cursor)
        return rules.epoch(moment) if moment else None

    # ─── Нажатия кнопок в Telegram ───────────────────────────────────────────

    _TOASTS = {
        'approved': 'Подтверждено — код отправлен',
        'rejected': 'Отклонено',
        'expired': 'Запрос уже сгорел: прошло 10 минут',
        'cancelled': 'Запрос отменён: у сотрудника нет доступа к аккаунту',
        'forbidden': 'Подтверждают глава маркетинга и супер-админы',
        'key_missing': 'Ключ шифрования раздела не задан — подтвердить нельзя',
        'not_found': 'Запрос не найден',
        'key_error': 'Ключ аккаунта не расшифровывается — код не выдан',
        'no_user': 'Ваш Telegram не привязан к учётке портала',
    }

    @staticmethod
    def delivery_reason(error):
        """Почему код не дошёл — словами для главы, а не ответом Bot API."""
        text = str(error or '').lower()
        if 'blocked' in text:
            return 'сотрудник остановил бота портала'
        if 'chat not found' in text or 'not found' in text:
            return 'сотрудник ни разу не открывал бота портала'
        if 'не привязан' in text:
            return 'у сотрудника не привязан Telegram'
        if 'не ответил' in text:
            return 'Telegram не ответил — код мог дойти, уточните у сотрудника'
        return 'Telegram не принял сообщение'

    def handle_callback(self, telegram_user_id, data):
        """twofa:ok:<id> / twofa:no:<id> — глава; twofa:new:<id> — сотрудник.

        Возвращает (текст всплывашки, показать ли окном). Права проверяются по учётке, к
        которой привязан Telegram нажавшего, а не по тому, кому ушло сообщение: переслать
        сообщение с кнопкой можно, а подтвердить чужим аккаунтом — нельзя.
        """
        try:
            _prefix, action, raw_id = str(data or '').split(':', 2)
            request_id = int(raw_id)
        except ValueError:
            return 'Неверная кнопка', True
        with self._cursor() as cursor:
            user_id = queries.user_id_by_telegram(cursor, telegram_user_id)
            ctx = queries.load_access_context(cursor, user_id, crypto.key_status()) if user_id else None
        if not ctx:
            return self._TOASTS['no_user'], True
        if action in ('ok', 'no'):
            result = self.decide(ctx, request_id, 'approve' if action == 'ok' else 'reject', 'telegram')
            outcome = result['outcome']
            if outcome == 'already':
                request = result['request']
                when = rules.clock(rules.epoch(request['decided_at']), seconds=False) if request['decided_at'] else ''
                return 'Уже решено: %s %s' % (rules.STATUS_LABELS.get(request['status'], '').lower(), when), False
            if outcome == 'approved' and result.get('delivered') is False:
                return 'Подтверждено, но код не дошёл: %s' % self.delivery_reason(
                    result['request'].get('delivery_error')), True
            return self._TOASTS.get(outcome, 'Готово'), outcome in ('forbidden', 'key_missing', 'key_error', 'cancelled')
        if action == 'new':
            result = self.resend(ctx['user_id'], request_id)
            outcome = result['outcome']
            if outcome == 'resent':
                return 'Новый код отправлен', False
            if outcome == 'just_sent':
                return 'Код уже отправлен — посмотрите сообщение ниже', False
            if outcome == 'same_code':
                # Выдан следующий код (до смены было меньше 10 с): строгий сервис примет его
                # только с начала его шага — так и сказать, а не «действует ещё 30 с».
                if result.get('starts_in'):
                    return 'Код тот же — он начнёт действовать через %d с. Введите его после этого' % (
                        result['starts_in']), True
                return 'Код тот же — он действует ещё %d с. Проверьте, что ввели его без ошибок' % (
                    result['seconds_left']), True
            if outcome == 'window_closed':
                return 'Прошло 3 минуты — запросите код заново в разделе «Коды 2FA»', True
            if outcome == 'failed':
                return 'Код не отправился — нажмите ещё раз', True
            if outcome == 'key_error':
                return self._TOASTS['key_error'], True
            return 'Кнопка не ваша или доступ к аккаунту снят', True
        return 'Неверная кнопка', True

    # ─── Тумблер уведомлений ─────────────────────────────────────────────────

    def set_notify(self, ctx, enabled):
        """Включить или выключить себе уведомления о запросах кодов (колокол и Telegram)."""
        with self._cursor() as cursor:
            queries.set_notify(cursor, ctx['user_id'], bool(enabled), rules.almaty(self.now()))
            return queries.notify_enabled(cursor, ctx['user_id'])

    # ─── Аккаунты и доступы ──────────────────────────────────────────────────

    def duplicates(self, cursor, secrets):
        """{индекс: название живого аккаунта с тем же ключом} — для предпросмотра и сохранения.

        Ключи живых аккаунтов расшифровываются здесь же, в памяти: их не больше пары десятков
        (ТЗ: 50), а хранить рядом с шифротекстом что-то, что выдаёт совпадение ключей, не стоит.
        Повтор внутри одного списка (тот же аккаунт дважды в экспорте) — тоже дубль.
        """
        known = {}
        for account_id, title, secret_enc in queries.active_secrets(cursor):
            try:
                known.setdefault(crypto.decrypt(secret_enc, account_id), title)
            except (crypto.KeyMissing, ValueError):
                logging.warning('twofa: ключ аккаунта %s не расшифрован при сверке дублей', account_id)
        found, seen = {}, {}
        for index, secret in enumerate(secrets):
            if not secret:
                continue
            if secret in known:
                found[index] = known[secret]
            elif secret in seen:
                found[index] = seen[secret]
            else:
                seen[secret] = None
        return found

    def add_accounts(self, ctx, entries):
        """Сохранить аккаунты: [{'title', 'service', 'secret': bytes}]. Ключи шифруются здесь,
        каждый — со своим id (crypto: AAD). outcome: ok (created) / duplicate (index, title)."""
        now_dt = rules.almaty(self.now())
        created = []
        with self._cursor() as cursor:
            clashes = self.duplicates(cursor, [entry['secret'] for entry in entries])
            if clashes:
                index = min(clashes)
                return {'outcome': 'duplicate', 'index': index,
                        'title': clashes[index] or entries[index]['title']}
            for entry in entries:
                account_id = queries.next_account_id(cursor)
                queries.insert_account(
                    cursor, account_id=account_id, title=entry['title'], service=entry['service'],
                    secret_enc=crypto.encrypt(entry['secret'], account_id), actor_id=ctx['user_id'], now=now_dt)
                queries.log_event(cursor, 'account_added', now=now_dt, account_id=account_id,
                                  account_title=entry['title'], actor_id=ctx['user_id'], actor_name=ctx['name'],
                                  details={'service': entry['service']} if entry['service'] else None)
                created.append(account_id)
        return {'outcome': 'ok', 'created': created}

    def rename_account(self, ctx, account_id, title, service):
        now_dt = rules.almaty(self.now())
        with self._cursor() as cursor:
            account = queries.get_account(cursor, account_id, for_update=True)
            if not account or not account['is_active']:
                return False
            if not queries.rename_account(cursor, account_id, title=title, service=service, now=now_dt):
                return False
            if (account['title'], account['service']) != (title, service):
                queries.log_event(cursor, 'account_renamed', now=now_dt, account_id=account_id, account_title=title,
                                  actor_id=ctx['user_id'], actor_name=ctx['name'],
                                  details={'from': account['title'], 'to': title})
        return True

    def remove_account(self, ctx, account_id):
        now_dt = rules.almaty(self.now())
        with self._cursor() as cursor:
            account = queries.get_account(cursor, account_id, for_update=True)
            if not account or not account['is_active']:
                return False
            cursor.execute("SELECT id FROM twofa_requests WHERE account_id = %s AND status = 'pending'",
                           (int(account_id),))
            pending = [int(row[0]) for row in cursor.fetchall()]
            self._cancel(cursor, pending, now_dt)
            queries.remove_account(cursor, account_id, actor_id=ctx['user_id'], now=now_dt)
            queries.log_event(cursor, 'account_removed', now=now_dt, account_id=account_id,
                              account_title=account['title'], actor_id=ctx['user_id'], actor_name=ctx['name'])
        self.finish_cancelled(pending)
        return True

    def set_person_grants(self, ctx, user_id, account_ids):
        """Набор аккаунтов человека целиком. Снятое закрывает и его ждущие запросы к ним."""
        now_dt = rules.almaty(self.now())
        with self._cursor() as cursor:
            target = queries.person(cursor, user_id)
            if not target:
                return {'outcome': 'not_found'}
            live = {a['id']: a['title'] for a in queries.list_accounts(cursor)}
            wanted = [int(x) for x in account_ids]
            if any(x not in live for x in wanted):
                return {'outcome': 'unknown_account'}
            if wanted and not target['working']:
                return {'outcome': 'not_working'}
            added, removed = queries.set_user_grants(cursor, user_id, wanted, actor_id=ctx['user_id'], now=now_dt)
            for account_id in added:
                queries.log_event(cursor, 'access_granted', now=now_dt, account_id=account_id,
                                  account_title=live.get(account_id), actor_id=ctx['user_id'], actor_name=ctx['name'],
                                  subject_id=target['id'], subject_name=target['name'])
            for account_id in removed:
                queries.log_event(cursor, 'access_revoked', now=now_dt, account_id=account_id,
                                  account_title=live.get(account_id), actor_id=ctx['user_id'], actor_name=ctx['name'],
                                  subject_id=target['id'], subject_name=target['name'])
            pending = []
            if removed:
                cursor.execute("SELECT id FROM twofa_requests WHERE requester_id = %s AND status = 'pending' "
                               "AND account_id = ANY(%s)", (int(user_id), removed))
                pending = [int(row[0]) for row in cursor.fetchall()]
                self._cancel(cursor, pending, now_dt)
        self.finish_cancelled(pending)
        return {'outcome': 'ok', 'added': added, 'removed': removed}

    @staticmethod
    def parse(text):
        """Разбор QR или ключа — для предпросмотра и сохранения одними правилами."""
        return otpauth.parse(text)
