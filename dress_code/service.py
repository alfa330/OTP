# -*- coding: utf-8 -*-
"""Логика раздела «Дресс-код»: кто снимает, приём фото, вывод ИИ, решения, сводка, чистка.

Порядок приёма фото важен и держится намеренно: короткая транзакция проверяет право
снять, ИИ и бакет работают БЕЗ открытого соединения (внешний вызов в транзакции —
это блокировка, которую держат секунды; так однажды уже лежал прод), потом вторая
короткая транзакция пишет строку. Гонку двух «Отправить» решает уникальный индекс
(одно первое фото в день), а залитый файл проигравшего удаляется.
"""

import logging
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from . import (
    AI_OK, AI_PENDING, AI_RETRY_LIMIT, AI_UNCLEAR, AI_VIOLATION,
    CAMERA_DEFAULTS, CAMERA_LIMITS,
    DECISION_CONFIRMED, DECISION_RETAKE, DECISIONS,
    QUALITY_ATTEMPTS, QUALITY_HINTS, QUALITY_OK, REPEAT_WINDOW_DAYS, RETENTION_DAYS,
)
from . import ai as dc_ai
from . import photos
from . import rules as dc_rules

log = logging.getLogger(__name__)

ALMATY = ZoneInfo('Asia/Almaty')
COMMENT_MAX = 1000
LIST_LIMIT = 500
# Сегодняшняя смена считается «без фото», когда с её начала прошло столько минут.
MISSING_GRACE_MINUTES = 30
# Фоном ИИ повторяет только свежие фото: давнее всё равно разбирают руками.
PENDING_MAX_AGE = timedelta(days=2)
SOURCES = ('camera', 'system')
EMPLOYEE_HISTORY_DAYS = 14


class DressCodeError(Exception):
    def __init__(self, message, status=400, code='DRESS_CODE_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def now_almaty():
    return datetime.now(ALMATY).replace(tzinfo=None)


def today_almaty():
    return now_almaty().date()


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


# ── Настройка ───────────────────────────────────────────────────────────────

def clean_camera(raw):
    """Правила автопроверки кадра: известные ключи, числа в пределах CAMERA_LIMITS; всё
    остальное — значение по умолчанию. Камера сотрудника получает только такой набор."""
    out = dict(CAMERA_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, (low, high) in CAMERA_LIMITS.items():
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if value != value or value in (float('inf'), float('-inf')):
            continue
        value = max(low, min(high, value))
        out[key] = int(round(value)) if key == 'hold_ms' else round(value, 3)
    return out


def load_settings(cursor):
    # camera — через to_jsonb строки: пока колонки нет (схема не докатилась), это NULL,
    # и камера работает на значениях по умолчанию, а не падает весь раздел.
    cursor.execute("""
        SELECT s.department_ids, s.roles, s.updated_at, to_jsonb(s) -> 'camera'
          FROM dress_code_settings s WHERE s.id = 1
    """)
    row = cursor.fetchone()
    cursor.execute("""
        SELECT r.id, r.body, r.created_at, u.name
          FROM dress_code_rules r
          LEFT JOIN users u ON u.id = r.created_by
         ORDER BY r.id DESC
         LIMIT 1
    """)
    rule = cursor.fetchone()
    return {
        'department_ids': sorted(int(x) for x in (row[0] if row else None) or []),
        'roles': [str(x) for x in (row[1] if row else None) or []],
        'updated_at': _iso(row[2]) if row else None,
        'camera': clean_camera(row[3] if row else None),
        'rules': {
            'version': int(rule[0]) if rule else None,
            'body': rule[1] if rule else dc_rules.DEFAULT_RULES,
            'created_at': _iso(rule[2]) if rule else None,
            'created_by': rule[3] if rule else None,
        },
    }


def save_settings(cursor, actor_id, *, rules_body=None, department_ids=None, roles=None, camera=None):
    """Новая версия правил — только если текст правда изменился; отделы и роли — целиком;
    правила камеры (стенд суперадмина) — целиком, в пределах CAMERA_LIMITS."""
    current = load_settings(cursor)
    if camera is not None:
        cursor.execute("""
            UPDATE dress_code_settings
               SET camera = %s, updated_by = %s,
                   updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = 1
        """, (Json(clean_camera(camera)), actor_id))
    if rules_body is not None:
        body = dc_rules.clean_body(rules_body)
        if body != (current['rules']['body'] or '').strip():
            cursor.execute(
                "INSERT INTO dress_code_rules (body, created_by) VALUES (%s, %s)", (body, actor_id))
    if department_ids is not None or roles is not None:
        ids = current['department_ids'] if department_ids is None else sorted(
            {int(x) for x in department_ids if str(x).strip().lstrip('-').isdigit()})
        clean_roles = current['roles'] if roles is None else sorted(
            {str(r).strip() for r in roles if str(r).strip() in _ALLOWED_ROLES})
        if ids:
            cursor.execute("SELECT id FROM departments WHERE id = ANY(%s)", (ids,))
            known = {int(r[0]) for r in cursor.fetchall()}
            ids = [x for x in ids if x in known]
        cursor.execute("""
            UPDATE dress_code_settings
               SET department_ids = %s, roles = %s, updated_by = %s,
                   updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = 1
        """, (ids, clean_roles, actor_id))
    return load_settings(cursor)


_ALLOWED_ROLES = ('operator', 'trainee', 'sv', 'trainer')


def department_choices(cursor):
    cursor.execute("SELECT id, name, code FROM departments ORDER BY name")
    return [{'id': int(r[0]), 'name': r[1], 'code': r[2]} for r in cursor.fetchall()]


# ── Кто снимает ─────────────────────────────────────────────────────────────

def user_row(cursor, user_id):
    cursor.execute("""
        SELECT id, name, role, status, department_id, city, telegram_id
          FROM users WHERE id = %s
    """, (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'name': row[1], 'role': row[2], 'status': row[3],
            'department_id': int(row[4]) if row[4] is not None else None, 'city': row[5],
            'telegram_id': row[6]}


def must_photograph(user, settings):
    """Работающий сотрудник выбранного отдела с выбранной ролью (ТЗ п. 1)."""
    return bool(
        user and user.get('status') == 'working'
        and user.get('department_id') in set(settings.get('department_ids') or [])
        and user.get('role') in set(settings.get('roles') or []))


# ── Строка фото ─────────────────────────────────────────────────────────────

_CHECK_SELECT = """
    SELECT c.id, c.user_id, u.name, c.department_id, d.name, c.city, c.day, c.taken_at,
           c.retake_of, c.source, c.attempts, c.photo_bucket, c.photo_path, c.rules_version,
           c.ai_status, c.ai_quality, c.ai_visible, c.ai_reasons, c.ai_remarks, c.ai_note,
           c.analyzed_at, c.decision, c.decision_comment, du.name, c.decided_at,
           c.is_repeat, c.acknowledged_at,
           (SELECT ch.id FROM dress_code_checks ch WHERE ch.retake_of = c.id) AS retake_id
      FROM dress_code_checks c
      JOIN users u ON u.id = c.user_id
      LEFT JOIN departments d ON d.id = c.department_id
      LEFT JOIN users du ON du.id = c.decided_by
"""


def _check_from_row(row):
    return {
        'id': int(row[0]), 'user_id': int(row[1]), 'employee': row[2],
        'department_id': row[3], 'department': row[4], 'city': row[5],
        'day': _iso(row[6]), 'taken_at': _iso(row[7]), 'retake_of': row[8], 'source': row[9],
        'attempts': row[10], 'photo_bucket': row[11], 'photo_path': row[12], 'rules_version': row[13],
        'ai_status': row[14], 'ai_quality': row[15], 'ai_visible': row[16] or [],
        'ai_reasons': row[17] or [], 'ai_remarks': row[18] or [], 'ai_note': row[19],
        'analyzed_at': _iso(row[20]), 'decision': row[21], 'decision_comment': row[22],
        'decided_by': row[23], 'decided_at': _iso(row[24]), 'is_repeat': bool(row[25]),
        'acknowledged_at': _iso(row[26]), 'retake_id': row[27],
    }


def fetch_check(cursor, check_id, department_ids=None, for_update=False):
    sql = _CHECK_SELECT + " WHERE c.id = %s"
    params = [int(check_id)]
    if department_ids is not None:
        sql += " AND c.department_id = ANY(%s)"
        params.append(list(department_ids))
    if for_update:
        sql += " FOR UPDATE OF c"
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return _check_from_row(row) if row else None


def with_photo_url(gcs, check):
    """Наружу — без пути в бакете, с подписанным адресом."""
    out = {k: v for k, v in check.items() if k not in ('photo_bucket', 'photo_path')}
    out['photo_url'] = photos.signed_url(gcs, check.get('photo_bucket'), check.get('photo_path'))
    return out


def needs_review(check):
    return check.get('decision') is None and check.get('ai_status') in (AI_VIOLATION, AI_UNCLEAR)


# ── Сотрудник ───────────────────────────────────────────────────────────────

def employee_status(check):
    """Что видит сотрудник. До решения руководителя — нейтрально (ТЗ п. 2)."""
    decision = check.get('decision')
    if decision:
        return decision
    if check.get('ai_status') == AI_OK:
        return 'ok'
    return 'review'


def employee_view(gcs, check):
    status = employee_status(check)
    reasons = []
    if status == DECISION_CONFIRMED:
        reasons = [item.get('sign') for item in check.get('ai_reasons') or [] if item.get('sign')]
    return {
        'id': check['id'], 'day': check['day'], 'taken_at': check['taken_at'],
        'status': status, 'reasons': reasons,
        'comment': check.get('decision_comment') if status in (DECISION_CONFIRMED, DECISION_RETAKE) else None,
        'is_repeat': check.get('is_repeat') if status == DECISION_CONFIRMED else False,
        'acknowledged_at': check.get('acknowledged_at'), 'retake_of': check.get('retake_of'),
        'retake_id': check.get('retake_id'),
        'photo_url': photos.signed_url(gcs, check.get('photo_bucket'), check.get('photo_path')),
    }


def _open_request(checks, today):
    """Сегодняшнее фото, которое просят переснять (или нарушение, которое исправляют),
    и на которое повторного ещё нет."""
    for check in checks:
        if (check['day'] == today.isoformat() and check.get('decision') in (DECISION_CONFIRMED, DECISION_RETAKE)
                and not check.get('retake_id')):
            return check
    return None


def my_state(cursor, gcs, user_id):
    settings = load_settings(cursor)
    user = user_row(cursor, user_id)
    required = must_photograph(user, settings)
    today = today_almaty()
    cursor.execute(_CHECK_SELECT + """
         WHERE c.user_id = %s AND c.day >= %s
         ORDER BY c.taken_at DESC
    """, (int(user_id), today - timedelta(days=EMPLOYEE_HISTORY_DAYS)))
    checks = [_check_from_row(r) for r in cursor.fetchall()]
    primary_today = next((c for c in checks if c['day'] == today.isoformat() and not c['retake_of']), None)
    request = _open_request(checks, today)
    return {
        'required': required,
        'can_capture': bool(required and (primary_today is None or request is not None)),
        'retake_for': request['id'] if (primary_today and request) else None,
        'attempts': QUALITY_ATTEMPTS,
        'camera': settings['camera'],
        'today': [employee_view(gcs, c) for c in checks if c['day'] == today.isoformat()],
        'history': [employee_view(gcs, c) for c in checks if c['day'] != today.isoformat()],
    }


def _target(cursor, user_id, today, retake_of):
    """День фото и фото, к которому оно повторное. DressCodeError — снимать нельзя."""
    if retake_of:
        parent = fetch_check(cursor, retake_of)
        if (not parent or parent['user_id'] != int(user_id) or parent['day'] != today.isoformat()
                or parent.get('decision') not in (DECISION_CONFIRMED, DECISION_RETAKE)):
            raise DressCodeError('Повторное фото к этому снимку не запрошено', 409, 'DRESS_CODE_NO_RETAKE')
        if parent.get('retake_id'):
            raise DressCodeError('Повторное фото уже отправлено', 409, 'DRESS_CODE_ALREADY_SENT')
        return parent
    cursor.execute("""
        SELECT 1 FROM dress_code_checks WHERE user_id = %s AND day = %s AND retake_of IS NULL
    """, (int(user_id), today))
    if cursor.fetchone():
        raise DressCodeError('Фото на сегодня уже отправлено', 409, 'DRESS_CODE_ALREADY_SENT')
    return None


def _ai_columns(result, ai_error):
    """Колонки вывода ИИ: результат или «ожидает анализа» с ошибкой."""
    if result is None:
        return {'ai_status': AI_PENDING, 'ai_quality': None, 'ai_visible': [], 'ai_reasons': [],
                'ai_remarks': [], 'ai_note': None, 'ai_model': None, 'ai_attempts': 1,
                'ai_error': (ai_error or 'ИИ недоступен')[:300], 'analyzed_at': None}
    return {'ai_status': result['verdict'], 'ai_quality': result['quality'],
            'ai_visible': result['visible'], 'ai_reasons': result['violations'],
            'ai_remarks': result['remarks'], 'ai_note': result.get('note') or None,
            'ai_model': result.get('model'), 'ai_attempts': 1, 'ai_error': None,
            'analyzed_at': now_almaty()}


def submit(db, gcs, user_id, raw_bytes, *, attempt=1, source='camera', retake_of=None, analyze=None):
    """Фото сотрудника. {'retake': True, 'hint'} — плохой кадр, переснять (ничего не
    сохранено); иначе {'retake': False, 'check'} — фото принято."""
    analyze = analyze or dc_ai.analyze
    try:
        attempt = max(1, min(int(attempt or 1), QUALITY_ATTEMPTS))
    except (TypeError, ValueError):
        attempt = 1
    source = source if source in SOURCES else 'camera'
    today = today_almaty()
    with db._get_cursor() as cursor:
        settings = load_settings(cursor)
        user = user_row(cursor, user_id)
        if not must_photograph(user, settings):
            raise DressCodeError('Фото для дресс-кода от вас не требуется', 403, 'DRESS_CODE_NOT_REQUIRED')
        parent = _target(cursor, user_id, today, int(retake_of) if retake_of else None)

    prepared = photos.prepare(raw_bytes)
    result, ai_error = None, None
    try:
        result = analyze(prepared['data'], settings['rules']['body'])
    except dc_ai.DressCodeAIError as exc:
        ai_error = str(exc)
    except Exception as exc:   # сбой ИИ не блокирует фото (ТЗ п. 2)
        log.exception('Дресс-код: ИИ упал на фото пользователя %s', user_id)
        ai_error = type(exc).__name__
    if result and result['quality'] != QUALITY_OK and attempt < QUALITY_ATTEMPTS:
        return {'retake': True, 'attempt': attempt, 'quality': result['quality'],
                'hint': QUALITY_HINTS.get(result['quality']) or 'Переснимите фото'}

    path = photos.blob_path(user_id, today)
    bucket = photos.upload(gcs, prepared, path)
    columns = _ai_columns(result, ai_error)
    try:
        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO dress_code_checks
                    (user_id, department_id, city, day, retake_of, source, attempts,
                     photo_bucket, photo_path, photo_width, photo_height, rules_version,
                     ai_status, ai_quality, ai_visible, ai_reasons, ai_remarks, ai_note,
                     ai_model, ai_attempts, ai_error, analyzed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                int(user_id), user.get('department_id'), user.get('city'),
                parent['day'] if parent else today, parent['id'] if parent else None, source, attempt,
                bucket, path, prepared['width'], prepared['height'], settings['rules']['version'],
                columns['ai_status'], columns['ai_quality'], Json(columns['ai_visible']),
                Json(columns['ai_reasons']), Json(columns['ai_remarks']), columns['ai_note'],
                columns['ai_model'], columns['ai_attempts'], columns['ai_error'], columns['analyzed_at'],
            ))
            check_id = cursor.fetchone()[0]
            check = fetch_check(cursor, check_id)
    except Exception as exc:
        photos.drop(gcs, [(bucket, path)])
        if getattr(exc, 'pgcode', None) == '23505':
            raise DressCodeError('Фото на сегодня уже отправлено', 409, 'DRESS_CODE_ALREADY_SENT')
        raise
    return {'retake': False, 'check': check}


def acknowledge(cursor, check_id, user_id):
    cursor.execute("""
        UPDATE dress_code_checks
           SET acknowledged_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s AND user_id = %s AND decision = 'confirmed' AND acknowledged_at IS NULL
        RETURNING id
    """, (int(check_id), int(user_id)))
    if not cursor.fetchone():
        raise DressCodeError('Отметить «Ознакомлен» можно только у подтверждённого нарушения', 409,
                             'DRESS_CODE_NOTHING_TO_ACK')


# ── ИИ фоном ────────────────────────────────────────────────────────────────

def analyze_pending(db, gcs, *, limit=3, analyze=None):
    """Повторная проверка фото, на которые ИИ не ответил (ТЗ п. 9). Возвращает
    фото, которым теперь нужна проверка руководителя (для уведомления)."""
    analyze = analyze or dc_ai.analyze
    with db._get_cursor() as cursor:
        if not _ready(cursor):
            return []
        settings = load_settings(cursor)
        cursor.execute("""
            SELECT id, photo_bucket, photo_path, ai_attempts
              FROM dress_code_checks
             WHERE ai_status = 'pending' AND taken_at >= %s
             ORDER BY taken_at
             LIMIT %s
        """, (now_almaty() - PENDING_MAX_AGE, int(limit)))
        pending = cursor.fetchall()
    became_review = []
    for check_id, bucket, path, attempts in pending:
        result, error = None, None
        try:
            data = photos.read(gcs, bucket, path)
            result = analyze(data, settings['rules']['body'])
        except dc_ai.DressCodeAIError as exc:
            error = str(exc)
        except Exception as exc:
            log.exception('Дресс-код: повторный анализ фото %s упал', check_id)
            error = type(exc).__name__
        with db._get_cursor() as cursor:
            if result is not None:
                columns = _ai_columns(result, None)
                cursor.execute("""
                    UPDATE dress_code_checks
                       SET ai_status = %s, ai_quality = %s, ai_visible = %s, ai_reasons = %s,
                           ai_remarks = %s, ai_note = %s, ai_model = %s, ai_attempts = ai_attempts + 1,
                           ai_error = NULL, analyzed_at = %s, rules_version = %s
                     WHERE id = %s AND ai_status = 'pending'
                """, (columns['ai_status'], columns['ai_quality'], Json(columns['ai_visible']),
                      Json(columns['ai_reasons']), Json(columns['ai_remarks']), columns['ai_note'],
                      columns['ai_model'], columns['analyzed_at'], settings['rules']['version'], check_id))
            elif int(attempts or 0) + 1 >= AI_RETRY_LIMIT:
                # Долгий сбой — на ручную проверку (ТЗ п. 9).
                cursor.execute("""
                    UPDATE dress_code_checks
                       SET ai_status = 'unclear', ai_attempts = ai_attempts + 1, ai_error = %s,
                           ai_note = 'ИИ недоступен — нужна ручная проверка'
                     WHERE id = %s AND ai_status = 'pending'
                """, ((error or '')[:300], check_id))
            else:
                cursor.execute("""
                    UPDATE dress_code_checks SET ai_attempts = ai_attempts + 1, ai_error = %s
                     WHERE id = %s AND ai_status = 'pending'
                """, ((error or '')[:300], check_id))
            check = fetch_check(cursor, check_id)
        if check and needs_review(check):
            became_review.append(check)
    return became_review


def mark_reviewers_notified(cursor, check_id):
    cursor.execute("""
        UPDATE dress_code_checks
           SET reviewers_notified_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s AND reviewers_notified_at IS NULL
        RETURNING id
    """, (int(check_id),))
    return bool(cursor.fetchone())


def _ready(cursor):
    from .schema import schema_is_ready
    return schema_is_ready(cursor)


# ── Руководитель ────────────────────────────────────────────────────────────

def _filters_sql(*, date_from, date_to, department_ids, q, city):
    where = ["c.day BETWEEN %s AND %s"]
    params = [date_from, date_to]
    if department_ids is not None:
        where.append("c.department_id = ANY(%s)")
        params.append(list(department_ids))
    if q:
        where.append("u.name ILIKE %s")
        params.append('%' + q.replace('%', r'\%').replace('_', r'\_') + '%')
    if city:
        # Точное совпадение: город выбирают из списка, который отдаёт сам сервер.
        where.append("c.city = %s")
        params.append(city)
    return where, params


def list_checks(cursor, gcs, *, date_from, date_to, department_ids=None, ai_status=None,
                decision=None, q=None, city=None, limit=LIST_LIMIT):
    where, params = _filters_sql(date_from=date_from, date_to=date_to, department_ids=department_ids,
                                 q=q, city=city)
    summary = _summary(cursor, where, params)
    if ai_status:
        where.append("c.ai_status = %s")
        params.append(ai_status)
    if decision == 'waiting':
        where.append("c.decision IS NULL AND c.ai_status IN ('violation', 'unclear')")
    elif decision == 'none':
        where.append("c.decision IS NULL")
    elif decision in DECISIONS:
        where.append("c.decision = %s")
        params.append(decision)
    elif decision == 'repeat':
        where.append("c.is_repeat")
    cursor.execute(_CHECK_SELECT + " WHERE " + " AND ".join(where)
                   + " ORDER BY c.day DESC, c.taken_at DESC LIMIT %s", params + [int(limit)])
    rows = [with_photo_url(gcs, _check_from_row(r)) for r in cursor.fetchall()]
    return {'rows': rows, 'summary': summary}


def _summary(cursor, where, params):
    """Показатели периода (ТЗ п. 7). Знаменатель у каждой доли называет экран."""
    cursor.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE c.ai_status IN ('ok', 'violation', 'unclear')),
               COUNT(*) FILTER (WHERE c.ai_status = 'ok'),
               COUNT(*) FILTER (WHERE c.ai_status = 'violation'),
               COUNT(*) FILTER (WHERE c.ai_status = 'unclear'),
               COUNT(*) FILTER (WHERE c.ai_status = 'pending'),
               COUNT(*) FILTER (WHERE c.decision IS NULL AND c.ai_status IN ('violation', 'unclear')),
               COUNT(*) FILTER (WHERE c.decision = 'confirmed'),
               COUNT(*) FILTER (WHERE c.decision = 'no_violation'),
               COUNT(*) FILTER (WHERE c.decision = 'retake'),
               COUNT(*) FILTER (WHERE c.is_repeat AND c.decision = 'confirmed')
          FROM dress_code_checks c
          JOIN users u ON u.id = c.user_id
         WHERE """ + " AND ".join(where), params)
    row = cursor.fetchone() or [0] * 11
    keys = ('photos', 'analyzed', 'ai_ok', 'ai_violation', 'ai_unclear', 'ai_pending',
            'waiting', 'confirmed', 'no_violation', 'retake', 'repeat')
    return {key: int(value or 0) for key, value in zip(keys, row)}


def missing_photos(cursor, settings, *, date_from, date_to, department_ids=None, q=None, city=None, now=None):
    """Кто по графику работал, но фото дня не прислал. Сегодняшняя смена — только
    когда с её начала прошло MISSING_GRACE_MINUTES (до этого человек ещё в пути)."""
    dept = list(settings.get('department_ids') or [])
    roles = list(settings.get('roles') or [])
    if not dept or not roles:
        return {'rows': [], 'shifts': 0}
    if department_ids is not None:
        dept = [x for x in dept if x in set(department_ids)]
        if not dept:
            return {'rows': [], 'shifts': 0}
    params = [date_from, date_to, dept, roles]
    extra = ""
    if q:
        extra += " AND u.name ILIKE %s"
        params.append('%' + q.replace('%', r'\%').replace('_', r'\_') + '%')
    if city:
        extra += " AND u.city = %s"
        params.append(city)
    cursor.execute("""
        SELECT ws.shift_date, u.id, u.name, d.name, u.city, MIN(ws.start_time),
               EXISTS (SELECT 1 FROM dress_code_checks c
                        WHERE c.user_id = u.id AND c.day = ws.shift_date AND c.retake_of IS NULL)
          FROM work_shifts ws
          JOIN users u ON u.id = ws.operator_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE ws.shift_date BETWEEN %s AND %s
           AND u.department_id = ANY(%s) AND u.role = ANY(%s) AND u.status = 'working'
    """ + extra + """
         GROUP BY ws.shift_date, u.id, u.name, d.name, u.city
         ORDER BY ws.shift_date DESC, u.name
    """, params)
    now = now or now_almaty()
    rows, shifts = [], 0
    for day, user_id, name, department, user_city, start, has_photo in cursor.fetchall():
        if day == now.date() and isinstance(start, dt_time):
            due = datetime.combine(day, start) + timedelta(minutes=MISSING_GRACE_MINUTES)
            if due > now:
                continue      # смена ещё не началась — не считаем ни в знаменателе, ни в пропусках
        shifts += 1
        if has_photo:
            continue
        rows.append({'day': _iso(day), 'user_id': int(user_id), 'employee': name,
                     'department': department, 'city': user_city,
                     'shift_start': start.strftime('%H:%M') if isinstance(start, dt_time) else None})
    return {'rows': rows, 'shifts': shifts}


def check_detail(cursor, gcs, check_id, department_ids=None):
    check = fetch_check(cursor, check_id, department_ids)
    if not check:
        return None
    cursor.execute("""
        SELECT decision, comment, actor_name, created_at
          FROM dress_code_decisions WHERE check_id = %s ORDER BY created_at, id
    """, (int(check_id),))
    journal = [{'decision': r[0], 'comment': r[1], 'actor': r[2], 'at': _iso(r[3])} for r in cursor.fetchall()]
    return {**with_photo_url(gcs, check), 'journal': journal}


def _clean_comment(text):
    return ' '.join(str(text or '').split())[:COMMENT_MAX] or None


def decide(cursor, check_id, *, department_ids, actor_id, actor_name, decision, comment=None):
    """Решение руководителя. Возвращает {'check', 'events'}: events — что разослать после
    фиксации (сотруднику — подтверждённое нарушение и просьба переснять, руководителю —
    повторное нарушение за 10 дней)."""
    if decision not in DECISIONS:
        raise DressCodeError('Неизвестное решение', 400, 'DRESS_CODE_BAD_DECISION')
    check = fetch_check(cursor, check_id, department_ids, for_update=True)
    if not check:
        raise DressCodeError('Фото не найдено', 404, 'DRESS_CODE_NOT_FOUND')
    note = _clean_comment(comment)
    if decision == DECISION_CONFIRMED and not check.get('ai_reasons') and not note:
        raise DressCodeError('Напишите в комментарии, в чём нарушение: ИИ его не назвал', 400,
                             'DRESS_CODE_COMMENT_REQUIRED')
    if decision == DECISION_RETAKE and check.get('retake_id'):
        raise DressCodeError('Повторное фото уже прислано', 409, 'DRESS_CODE_ALREADY_SENT')
    previous = check.get('decision')
    cursor.execute("""
        UPDATE dress_code_checks
           SET decision = %s, decision_comment = %s, decided_by = %s,
               decided_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               acknowledged_at = CASE WHEN %s = 'confirmed' AND decision = 'confirmed'
                                      THEN acknowledged_at ELSE NULL END
         WHERE id = %s
    """, (decision, note, actor_id, decision, check['id']))
    cursor.execute("""
        INSERT INTO dress_code_decisions (check_id, decision, comment, actor_id, actor_name)
        VALUES (%s, %s, %s, %s, %s)
    """, (check['id'], decision, note, actor_id, (actor_name or '')[:255] or None))

    repeat_of = []
    if decision == DECISION_CONFIRMED:
        day = datetime.strptime(check['day'], '%Y-%m-%d').date()
        cursor.execute("""
            SELECT id, day FROM dress_code_checks
             WHERE user_id = %s AND decision = 'confirmed' AND id <> %s
               AND day >= %s AND day < %s
             ORDER BY day
        """, (check['user_id'], check['id'], day - timedelta(days=REPEAT_WINDOW_DAYS), day))
        repeat_of = [{'id': r[0], 'day': _iso(r[1])} for r in cursor.fetchall()]
        cursor.execute("UPDATE dress_code_checks SET is_repeat = %s WHERE id = %s",
                       (bool(repeat_of), check['id']))
        # Решение задним числом: подтверждённые позже в пределах окна стали повторными.
        cursor.execute("""
            UPDATE dress_code_checks SET is_repeat = TRUE
             WHERE user_id = %s AND decision = 'confirmed' AND id <> %s
               AND day > %s AND day <= %s
        """, (check['user_id'], check['id'], day, day + timedelta(days=REPEAT_WINDOW_DAYS)))
    else:
        cursor.execute("UPDATE dress_code_checks SET is_repeat = FALSE WHERE id = %s", (check['id'],))
        if previous == DECISION_CONFIRMED:
            # Нарушение отменили: более поздние нарушения могли считаться повторными из-за него.
            recount_repeats(cursor, [check['user_id']])

    updated = fetch_check(cursor, check['id'])
    events = []
    if decision != previous and decision in (DECISION_CONFIRMED, DECISION_RETAKE):
        events.append({'kind': 'employee_' + decision, 'check': updated})
    if decision == DECISION_CONFIRMED and decision != previous and repeat_of:
        events.append({'kind': 'repeat', 'check': updated, 'previous': repeat_of})
    return {'check': updated, 'events': events}


def recount_repeats(cursor, user_ids):
    """«Повторное нарушение» у подтверждённых фото людей — заново по тому же правилу, что в
    decide: есть другое подтверждённое нарушение за REPEAT_WINDOW_DAYS до дня фото."""
    ids = sorted({int(x) for x in user_ids or []})
    if not ids:
        return
    cursor.execute("""
        UPDATE dress_code_checks c
           SET is_repeat = EXISTS (
                SELECT 1 FROM dress_code_checks p
                 WHERE p.user_id = c.user_id AND p.decision = 'confirmed' AND p.id <> c.id
                   AND p.day >= c.day - %s AND p.day < c.day)
         WHERE c.user_id = ANY(%s) AND c.decision = 'confirmed'
    """, (REPEAT_WINDOW_DAYS, ids))


# ── Стенд суперадмина ───────────────────────────────────────────────────────

def stand_analyze(raw_bytes, rules_text, *, analyze=None):
    """Пробный кадр со стенда: та же подготовка и тот же ИИ, что у фото сотрудника, но
    БЕЗ записи — ни строки в базе, ни файла в бакете. `employee` — что увидел бы сотрудник:
    retake — просьба переснять с подсказкой, ok — «Соответствует», review — «проверит
    руководитель»."""
    analyze = analyze or dc_ai.analyze
    prepared = photos.prepare(raw_bytes)
    try:
        result = analyze(prepared['data'], rules_text)
    except dc_ai.DressCodeAIError as exc:
        raise DressCodeError('ИИ сейчас недоступен: %s' % exc, 503, 'DRESS_CODE_AI_DOWN')
    quality = result['quality']
    if quality != QUALITY_OK:
        employee = 'retake'
    elif result['verdict'] == AI_OK:
        employee = 'ok'
    else:
        employee = 'review'
    return {
        'width': prepared['width'], 'height': prepared['height'],
        'quality': quality, 'hint': QUALITY_HINTS.get(quality),
        'verdict': result['verdict'], 'visible': result['visible'], 'violations': result['violations'],
        'remarks': result['remarks'], 'note': result.get('note') or None, 'model': result.get('model'),
        'employee': employee,
    }


def delete_check(db, gcs, check_id):
    """Удалить фото совсем (суперадмин убирает пробные снимки): само фото, присланные к нему
    повторные и журнал решений (каскадом). Файлы в бакете — после фиксации: строка, оставшаяся
    без файла, хуже файла, оставшегося без строки. Возвращает номера удалённых фото."""
    with db._get_cursor() as cursor:
        cursor.execute("""
            WITH RECURSIVE tree AS (
                SELECT id FROM dress_code_checks WHERE id = %s
                UNION ALL
                SELECT c.id FROM dress_code_checks c JOIN tree t ON c.retake_of = t.id
            )
            DELETE FROM dress_code_checks WHERE id IN (SELECT id FROM tree)
            RETURNING id, user_id, photo_bucket, photo_path
        """, (int(check_id),))
        rows = cursor.fetchall()
        if not rows:
            raise DressCodeError('Фото не найдено', 404, 'DRESS_CODE_NOT_FOUND')
        recount_repeats(cursor, [r[1] for r in rows])
    photos.drop(gcs, [(r[2], r[3]) for r in rows])
    return sorted(int(r[0]) for r in rows)


# ── Чистка ──────────────────────────────────────────────────────────────────

def cleanup(db, gcs, keep_days=RETENTION_DAYS):
    """История — месяц (ТЗ п. 8): строки и фото старше уходят, журнал решений — каскадом."""
    edge = today_almaty() - timedelta(days=int(keep_days))
    with db._get_cursor() as cursor:
        if not _ready(cursor):
            return 0
        cursor.execute("""
            DELETE FROM dress_code_checks WHERE day < %s
            RETURNING photo_bucket, photo_path
        """, (edge,))
        refs = [(r[0], r[1]) for r in cursor.fetchall()]
    photos.drop(gcs, refs)
    return len(refs)
