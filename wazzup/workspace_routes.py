"""HTTP рабочего места верификатора в «Чатах ОП». Правила — wazzup/access.py и wazzup/shift.py.

    GET  /api/wazzup/workspace            режим раздела, закрыт ли он, статусы и текущий статус
    POST /api/wazzup/workspace/qr         верификатор: обычный QR портала для экрана замка
    POST /api/wazzup/workspace/scan       прежний код чатов (OTPW:): админ/глава открывает, СВ шлёт код главе
    POST /api/wazzup/workspace/code       подтверждающий: выслать код ещё раз
    POST /api/wazzup/workspace/approve    подтверждающий: код из Telegram -> доступ открыт
    POST /api/wazzup/workspace/status     верификатор: поставить статус смены
    POST /api/wazzup/workspace/heartbeat  верификатор: портал открыт — продлить смену

С 09.10.2026 экран замка показывает ОБЫЧНЫЙ QR портала (решение владельца): его
подтверждают в «QR доступ» одним сканом — супервайзер и глава отдела сотрудника,
админ без отдела, супер-админ (`_sensitive_access_approval_error`), — и такая
сессия открывает чаты (access.load_operator_state). Скан, код и подтверждение ниже —
прежний путь для кодов OTPW:, показанных до этого: подтверждает тот же круг, а
супервайзер дополнительно вводит код, который уходит главе отдела в Telegram.

Зависимости приходят аргументами фабрики — проверки прав и авторизация живут в
bot_schedule2, и обратный импорт был бы циклом (как у op_wallboard). SQL — в
workspace_store.py.
"""
import html
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from . import access
from . import shift
from .workspace_store import WorkspaceStore

log = logging.getLogger(__name__)

_ALMATY = ZoneInfo('Asia/Almaty')
# Сколько сканов одного сотрудника один подтверждающий может начать за час.
# Каждый новый скан — это новый код в Telegram главе отдела и новые попытки
# ввода; потолок не даёт перебирать код, обновляя QR.
SCANS_PER_HOUR = 6

QR_MESSAGES = {
    'expired': 'Срок действия кода истёк — попросите обновить QR',
}
QR_FALLBACK_MESSAGE = 'Это не QR-код доступа к чатам'


def almaty_now():
    """Naive-локаль Алматы: в ней лежат event_at статусов и смены графика."""
    return datetime.now(_ALMATY).replace(tzinfo=None)


def utc_now():
    return datetime.now(timezone.utc)


def build_code_message(code, operator_name, approver_name, approver_role_label, ttl_seconds):
    """Сообщение главе отдела. Коротко: код, кому открывают и кто запросил —
    по этим трём строкам он и решает, называть ли код."""
    def esc(value, limit=120):
        return html.escape(str(value or '—')[:limit], quote=False)

    approver = esc(approver_name)
    if approver_role_label:
        approver += ', ' + esc(str(approver_role_label).lower(), 40)
    minutes = max(1, int(ttl_seconds) // 60)
    return '\n'.join([
        '<b>Код доступа к «Чатам ОП»</b>',
        '',
        f'<code>{esc(code, 12)}</code>',
        '',
        f'<b>Кому:</b> {esc(operator_name)}',
        f'<b>Открывает:</b> {approver}',
        '',
        f'Действует {minutes} мин. Не ждали этот запрос — никому не называйте код.',
    ])


def build_wazzup_workspace_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                     chat_access, current_session_id, approver_context,
                                     approval_perimeter_error, send_telegram, secret,
                                     sales_department_id, portal_qr, role_label=lambda role: '',
                                     avatar_url=lambda user: None, store=None, now=almaty_now,
                                     clock=utc_now, new_code=access.new_code,
                                     heartbeat_seconds=shift.HEARTBEAT_SECONDS,
                                     presence_timeout_seconds=shift.PRESENCE_TIMEOUT_SECONDS,
                                     code_ttl_seconds=access.CODE_TTL_SECONDS):
    """chat_access() -> {'user_id', 'mode', 'locked', 'can_process'} для человека
    запроса (bot_schedule2._wazzup_chat_access): одно правило на раздел, ленту,
    отправку и статусы.

    approver_context(approver_id) -> (context, error): вправе ли человек вообще
    подтверждать доступ (админ, супервайзер или глава отдела) и его отделы.
    approval_perimeter_error(**kwargs) — `_sensitive_access_approval_error`:
    периметр подтверждения тот же, что у обычного QR, своей копии правила нет.
    portal_qr(session_id, user_id) -> (строка для QR, срок) — обычный код портала
    (`_sensitive_qr_payload`): своего сборщика здесь нет, чтобы экран чатов не
    показал код, который сканер «QR доступ» не примет за свой."""
    bp = Blueprint('wazzup_workspace', __name__, url_prefix='/api/wazzup/workspace')
    store = store or WorkspaceStore(db)

    def fail(message, status, **extra):
        return jsonify({"error": message, **extra}), status

    # ── смена: статус и присутствие ─────────────────────────────────────────
    def write_auto_logout(user_id, decision):
        logout_at, note = decision
        store.append_status(user_id, logout_at, shift.LOGOUT_KEY, note,
                            shift.auto_logout_event_id(user_id, logout_at))
        log.info("Чаты ОП: смена %s закрыта сама на %s (%s)", user_id, logout_at, note)
        return logout_at

    def last_seen_safe(user_id):
        # Без таблицы отметок (схема раздела не развернулась) раздел обязан
        # работать: статусы пишутся, отключается только закрытие по тишине.
        try:
            return store.last_seen(user_id)
        except Exception:
            log.exception("Чаты ОП: отметки портала недоступны")
            return None

    def close_if_stale(user_id, moment):
        """Закрыть смену, если портал молчал дольше порога или статус не менялся
        MAX_OPEN_STATUS_HOURS. Возвращает момент выхода.

        Зовётся и сторожем, и КАЖДОЙ отметкой до её записи: иначе вкладка,
        проснувшаяся через двадцать минут, успела бы продлить смену раньше
        сторожа, и эти двадцать минут засчитались бы работой."""
        decision = shift.auto_logout(last_seen_safe(user_id), store.latest_status(user_id, moment),
                                     moment, presence_timeout_seconds)
        return write_auto_logout(user_id, decision) if decision else None

    def current_for(user_id, moment):
        return shift.current_status(store.latest_status(user_id, moment), moment)

    def touch(user_id, moment):
        try:
            store.touch(user_id, moment)
        except Exception:
            log.exception("Чаты ОП: отметка портала %s не записалась", user_id)

    def sweep():
        """Проход сторожа: закрыть смены, чей портал замолчал. Для планировщика."""
        moment = now()
        rows = store.stale_presences(moment - timedelta(seconds=presence_timeout_seconds),
                                     moment - timedelta(hours=shift.SWEEP_LOOKBACK_HOURS))
        closed = 0
        for row in rows:
            decision = shift.auto_logout(row['last_seen_at'], row['latest'], moment,
                                         presence_timeout_seconds)
            if not decision:
                continue
            try:
                write_auto_logout(row['user_id'], decision)
                closed += 1
            except Exception:
                log.exception("Чаты ОП: не удалось закрыть смену %s", row['user_id'])
        if closed:
            log.info("Чаты ОП: сторож закрыл смен — %s", closed)
        return closed

    def shift_state(user_id):
        moment = now()
        close_if_stale(user_id, moment)
        return {'statuses': list(shift.STATUSES), 'startKey': shift.START_KEY,
                'logoutKey': shift.LOGOUT_KEY, 'current': current_for(user_id, moment),
                'heartbeatSeconds': heartbeat_seconds}

    def operator_guard(*, allow_locked=False):
        """(access, error). Статусы и отметки — только верификатору с открытым доступом."""
        state = chat_access()
        if state.get('mode') != access.MODE_OPERATOR:
            return None, fail('Раздел в этом режиме открыт верификаторам', 403)
        if state.get('locked') and not allow_locked:
            return None, fail('Доступ к чатам не подтверждён', 403, code='CHAT_ACCESS_LOCKED')
        return state, None

    # ── подтверждение: скан и код ───────────────────────────────────────────
    def resolve_target(context, session_id, operator_id):
        """Кому открывается доступ и вправе ли это сделать зовущий (context —
        ответ approver_context).

        ОДНА функция на скан, повторную отправку кода и само подтверждение: имя
        сотрудника чужого отдела не должно показаться тому, кто открыть ему
        доступ не вправе, а проверка при вводе кода обязана совпадать с той,
        что прошёл скан."""
        operator = db.get_user(id=operator_id)
        state = store.operator_state(operator_id, session_id, now().date(), sales_department_id)
        if not operator or not state['verifier']:
            return None, ('Этот код не открывает чаты: сотрудник не верификатор отдела продаж', 400)
        if not store.session_is_live(session_id, operator_id):
            return None, ('Сессия сотрудника уже завершена — пусть войдёт заново и покажет новый код', 410)
        department_id = db.get_user_department_id(operator_id)
        perimeter_error = approval_perimeter_error(
            approver_role=context['approver'][3],
            approver_id=context['approver'][0],
            approver_department_id=context.get('department_id'),
            approver_headed_department_ids=context.get('headed_department_ids') or [],
            operator_department_id=department_id,
            operator_supervisor_id=operator[6] if len(operator) > 6 else None,
        )
        if perimeter_error:
            return None, perimeter_error
        return {'approver': context['approver'], 'operator': operator, 'session_id': session_id,
                'without_code': access.approves_without_code(
                    context['approver'][3], context.get('headed_department_ids'), department_id),
                'already_granted': state['unlocked'],
                'recipient': store.code_recipient(operator_id)}, None

    def recipient_error(recipient):
        if not recipient:
            return fail('У отдела сотрудника не назначен глава — код отправить некому', 409)
        if not recipient.get('telegram_id'):
            return fail(f"У главы отдела ({recipient['name']}) не привязан Telegram — "
                        "код отправить некуда", 409)
        return None

    def send_code(target, challenge_id, code):
        """Отправить код главе отдела. False — не ушло."""
        approver = target['approver']
        text = build_code_message(code, target['operator'][2], approver[2],
                                  role_label(approver[3]), code_ttl_seconds)
        try:
            response = send_telegram(target['recipient']['telegram_id'], text)
        except Exception:
            log.exception("Чаты ОП: код скана %s не отправлен", challenge_id)
            return False
        if getattr(response, 'status_code', None) != 200:
            log.error("Чаты ОП: Telegram не принял код скана %s (HTTP %s)", challenge_id,
                      getattr(response, 'status_code', '?'))
            return False
        return True

    def card(target, **extra):
        operator = target['operator']
        recipient = target.get('recipient') or {}
        return jsonify({
            "status": "success",
            "scope": "wazzup_chats",
            "operator_id": operator[0],
            "operator_name": operator[2],
            "operator_login": operator[7] if len(operator) > 7 else None,
            "operator_direction": operator[4] if len(operator) > 4 else None,
            "operator_department": recipient.get('department_name'),
            "avatar_url": avatar_url(operator),
            "already_granted": bool(target.get('already_granted')),
            **extra,
        })

    def challenge_payload(challenge_id, recipient, expires_at, last_sent_at, sent_count, attempts):
        moment = clock()
        resend_in = int((last_sent_at + timedelta(seconds=access.CODE_RESEND_SECONDS)
                         - moment).total_seconds())
        return {
            "challengeId": str(challenge_id),
            "codeSentTo": (recipient or {}).get('name'),
            "codeExpiresInSeconds": max(0, int((expires_at - moment).total_seconds())),
            "resendInSeconds": max(0, resend_in),
            "canResend": int(sent_count) < access.CODE_MAX_SENDS,
            "attemptsLeft": max(0, access.CODE_MAX_ATTEMPTS - int(attempts)),
        }

    def challenge_id_of(body):
        try:
            return uuid.UUID(str(body.get('challengeId') or ''))
        except (ValueError, AttributeError, TypeError):
            return None

    # ── роуты ───────────────────────────────────────────────────────────────
    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def workspace_state():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        state = chat_access()
        if not state.get('mode'):
            return fail('forbidden', 403)
        try:
            operator = state['mode'] == access.MODE_OPERATOR
            locked = bool(state.get('locked'))
            return jsonify({
                "status": "success",
                "mode": state['mode'],
                "locked": locked,
                "canProcess": bool(state.get('can_process')),
                "shift": shift_state(state['user_id']) if operator and not locked else None,
            }), 200
        except Exception:
            log.exception("Чаты ОП: не удалось собрать состояние раздела")
            return fail('Не удалось загрузить раздел', 500)

    @bp.route('/qr', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_qr():
        """Код для экрана замка — обычный QR портала, тот же, что у «Вики».

        Именно эта ручка, а не /api/sensitive-access/qr/request из портала: её
        зовут и вкладки, открытые до перехода на обычный QR, — они рисуют то,
        что вернул сервер, и начинают показывать обычный код без перезагрузки."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        state, error = operator_guard(allow_locked=True)
        if error:
            return error
        session_id = current_session_id()
        if not session_id:
            return fail('Сессия не найдена — войдите в портал заново', 401)
        try:
            if not store.session_is_live(session_id, state['user_id']):
                return fail('Сессия завершена — войдите в портал заново', 401)
            payload, expires_at = portal_qr(session_id, state['user_id'])
            return jsonify({
                "status": "success",
                "qr_payload": payload,
                "token_expires_at": expires_at.isoformat().replace('+00:00', 'Z'),
                "granted": not state.get('locked'),
            }), 200
        except Exception:
            log.exception("Чаты ОП: код для сканера не собрался")
            return fail('Не удалось сформировать QR', 500)

    @bp.route('/scan', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_scan():
        """Скан кода сотрудника. В отличие от предпросмотра обычного QR, меняет
        состояние: админ/глава сразу открывает доступ, СВ запрашивает код."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        approver_id = chat_access().get('user_id')
        if not approver_id:
            return fail('Unauthorized', 401)
        body = request.get_json(silent=True) or {}
        try:
            # Право подтверждать — раньше разбора кода: постороннему незачем знать
            # даже то, настоящий ли код ему попался.
            context, context_error = approver_context(approver_id)
            if context_error:
                return fail(context_error[0], context_error[1])
            try:
                claims = access.decode_qr_token(secret, body.get('token'), now=clock())
            except access.QrError as token_error:
                log.info("Чаты ОП: код сканера отклонён (%s)", token_error.reason)
                return fail(QR_MESSAGES.get(token_error.reason, QR_FALLBACK_MESSAGE), 400)
            target, error = resolve_target(context, claims['session_id'], claims['user_id'])
            if error:
                return fail(error[0], error[1])
            if target['already_granted']:
                return card(target), 200
            if target['without_code']:
                if not store.grant_without_code(claims['session_id'], claims['user_id'], approver_id):
                    return fail('Сессия сотрудника уже завершена — пусть войдёт заново и покажет новый код', 410)
                log.info("Чаты ОП: доступ сотруднику %s открыл %s без Telegram-кода",
                         claims['user_id'], approver_id)
                return card(target, already_granted=True, granted_now=True), 200
            recipient = target['recipient']
            no_recipient = recipient_error(recipient)
            if no_recipient:
                return no_recipient
            operator_id, moment = target['operator'][0], clock()
            # Повторный скан того же кода тем же человеком код заново не шлёт.
            active = store.active_challenge(claims['session_id'], approver_id, operator_id,
                                            moment, access.CODE_MAX_ATTEMPTS)
            if active:
                return card(target, **challenge_payload(
                    active['id'], recipient, active['expires_at'], active['last_sent_at'],
                    active['sent_count'], active['attempts'])), 200
            if store.recent_challenges(approver_id, operator_id) >= SCANS_PER_HOUR:
                return fail('Слишком много запросов кода по этому сотруднику — попробуйте позже', 429)
            challenge_id, code = uuid.uuid4(), new_code()
            expires_at = moment + timedelta(seconds=code_ttl_seconds)
            # Строка записана ДО отправки: код, который уже в Telegram, обязан
            # находить своё ожидание. Не ушёл — ожидание снимаем, чтобы повторный
            # скан начал заново, а не упёрся в «код уже отправлен».
            outcome, active = store.create_challenge(challenge_id, claims['session_id'], operator_id, approver_id,
                                   recipient['id'], access.hash_code(secret, challenge_id, code),
                                   expires_at, moment, SCANS_PER_HOUR, access.CODE_MAX_ATTEMPTS)
            if outcome == 'limited':
                return fail('Слишком много запросов кода по этому сотруднику — попробуйте позже', 429)
            if outcome == 'active':
                return card(target, **challenge_payload(
                    active['id'], recipient, active['expires_at'], active['last_sent_at'],
                    active['sent_count'], active['attempts'])), 200
            if not send_code(target, challenge_id, code):
                store.drop_challenge(challenge_id)
                return fail('Не удалось отправить код в Telegram главе отдела — попробуйте ещё раз', 502)
            return card(target, **challenge_payload(challenge_id, recipient, expires_at,
                                                    moment, 1, 0)), 200
        except Exception:
            log.exception("Чаты ОП: скан кода не обработан")
            return fail('Не удалось обработать код', 500)

    @bp.route('/code', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_code():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        approver_id = chat_access().get('user_id')
        if not approver_id:
            return fail('Unauthorized', 401)
        challenge_id = challenge_id_of(request.get_json(silent=True) or {})
        if challenge_id is None:
            return fail('Некорректный запрос', 400)
        try:
            moment = clock()
            challenge = store.challenge(challenge_id, approver_id)
            if not challenge or challenge['consumed_at'] is not None:
                return fail('Запрос кода уже закрыт — отсканируйте QR заново', 410)
            if int(challenge['sent_count']) >= access.CODE_MAX_SENDS:
                return fail('Код уже отправлялся несколько раз — отсканируйте QR заново', 429)
            wait = int((challenge['last_sent_at'] + timedelta(seconds=access.CODE_RESEND_SECONDS)
                        - moment).total_seconds())
            if wait > 0:
                return fail(f'Повторная отправка — через {wait} с', 429, resendInSeconds=wait)
            # QR к этому моменту мог истечь, а ожидание ещё живо — сверяем по нему.
            context, error = approver_context(approver_id)
            if not error:
                target, error = resolve_target(context, challenge['session_id'], challenge['user_id'])
            if error:
                return fail(error[0], error[1])
            recipient = target['recipient']
            no_recipient = recipient_error(recipient)
            if no_recipient:
                return no_recipient
            code = new_code()
            expires_at = moment + timedelta(seconds=code_ttl_seconds)
            sent_count = store.renew_code(challenge_id, approver_id,
                                          access.hash_code(secret, challenge_id, code), moment,
                                          expires_at, recipient['id'], access.CODE_MAX_SENDS,
                                          access.CODE_RESEND_SECONDS)
            if sent_count is None:
                return fail('Код уже отправлен или запрос закрыт — обновите карточку сканирования', 429)
            if not send_code(target, challenge_id, code):
                return fail('Не удалось отправить код в Telegram главе отдела — попробуйте ещё раз', 502)
            return jsonify({"status": "success",
                            **challenge_payload(challenge_id, recipient, expires_at, moment,
                                                sent_count, 0)}), 200
        except Exception:
            log.exception("Чаты ОП: код не отправлен повторно")
            return fail('Не удалось отправить код', 500)

    @bp.route('/approve', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_approve():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        approver_id = chat_access().get('user_id')
        if not approver_id:
            return fail('Unauthorized', 401)
        body = request.get_json(silent=True) or {}
        challenge_id = challenge_id_of(body)
        code = access.normalize_code(body.get('code'))
        if challenge_id is None:
            return fail('Некорректный запрос', 400)
        if code is None:
            return fail(f'Введите {access.CODE_DIGITS} цифр кода из Telegram', 400)
        try:
            challenge = store.challenge(challenge_id, approver_id)
            if not challenge or challenge['consumed_at'] is not None:
                return fail('Запрос кода уже закрыт — отсканируйте QR заново', 410)
            # Периметр и сессию сверяем ДО кода: отказ по существу не должен ни
            # тратить попытку, ни подсказывать, верен ли код.
            context, error = approver_context(approver_id)
            if not error:
                target, error = resolve_target(context, challenge['session_id'], challenge['user_id'])
            if error:
                return fail(error[0], error[1])
            outcome, attempts_left, _row = store.redeem(
                challenge_id, approver_id, clock(),
                lambda row: access.code_matches(secret, challenge_id, code, row['code_hash']),
                access.CODE_MAX_ATTEMPTS)
            if outcome == 'gone':
                return fail('Запрос кода уже закрыт — отсканируйте QR заново', 410)
            if outcome == 'expired':
                return fail('Код истёк — отправьте новый', 410, code='CODE_EXPIRED')
            if outcome == 'attempts':
                return fail('Попытки закончились — отправьте новый код', 429,
                            code='CODE_ATTEMPTS', attemptsLeft=0)
            if outcome == 'wrong':
                return fail('Неверный код' if attempts_left
                            else 'Неверный код. Попытки закончились — отправьте новый',
                            400, code='CODE_WRONG', attemptsLeft=attempts_left)
            log.info("Чаты ОП: доступ сотруднику %s открыл %s (код ушёл %s)",
                     challenge['user_id'], approver_id, challenge['recipient_id'])
            return jsonify({"status": "success", "scope": "wazzup_chats",
                            "operator_id": target['operator'][0],
                            "operator_name": target['operator'][2]}), 200
        except Exception:
            log.exception("Чаты ОП: доступ не открыт")
            return fail('Не удалось открыть доступ', 500)

    @bp.route('/status', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_status():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        state, error = operator_guard()
        if error:
            return error
        user_id = state['user_id']
        body = request.get_json(silent=True) or {}
        key = shift.normalize_key(body.get('status_key'))
        if key != shift.LOGOUT_KEY and not shift.is_on_shift_key(key):
            return fail('Неизвестный статус', 400)
        try:
            event_id = shift.client_event_id(body.get('client_event_id'))
        except ValueError as value_error:
            return fail(str(value_error), 400)
        try:
            moment = now()
            # Сначала закрыть замолчавшую смену: новый статус после долгой паузы —
            # это новая смена, а не продолжение той, что шла, пока портал был закрыт.
            close_if_stale(user_id, moment)
            # Отметка ДО статуса: проход сторожа между двумя записями увидел бы
            # вчерашнюю отметку рядом со свежим «Активным» и закрыл бы смену в её
            # первую же секунду.
            touch(user_id, moment)
            result = store.append_status(user_id, moment, key, None, event_id)
            return jsonify({"status": "success", "result": result,
                            "current": current_for(user_id, moment)}), 200
        except ValueError as value_error:
            return fail(str(value_error), 400)
        except Exception:
            log.exception("Чаты ОП: статус %s не записался (%s)", key, user_id)
            return fail('Статус не сохранился, попробуйте ещё раз', 500)

    @bp.route('/heartbeat', methods=['POST', 'OPTIONS'])
    @require_api_key
    def workspace_heartbeat():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        state, error = operator_guard(allow_locked=True)
        if error:
            return error
        if state.get('locked'):
            # Доступ не подтверждён (или сессию закрыли) — отмечать нечего;
            # отвечаем честно, чтобы портал перестал слать отметки.
            return jsonify({"status": "success", "locked": True, "current": None}), 200
        try:
            user_id = state['user_id']
            moment = now()
            close_if_stale(user_id, moment)
            current = current_for(user_id, moment)
            # Отметка нужна только идущей смене: сторож закрывает лишь её.
            if current['onShift']:
                touch(user_id, moment)
            return jsonify({"status": "success", "locked": False, "current": current}), 200
        except Exception:
            log.exception("Чаты ОП: отметка портала не записалась")
            return fail('Нет связи с сервером', 500)

    bp.sweep = sweep
    bp.store = store
    return bp
