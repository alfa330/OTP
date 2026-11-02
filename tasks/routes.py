"""Раздел «tasks»: ручки /api/tasks — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_tasks_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from common import log_secrets
from database import db
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
import collections
import html
import json
import logging
import os
import requests
import threading
import time
import uuid


TASK_ALLOWED_TAGS = {'task', 'problem', 'suggestion', 'production'}

TASK_ALLOWED_ACTIONS = {'in_progress', 'completed', 'accepted', 'returned', 'reopened'}

TASK_ALLOWED_PRIORITIES = {'normal', 'urgent', 'critical'}

TASK_ALLOWED_RECURRENCE_TYPES = {'daily', 'weekly', 'monthly'}

TASK_MAX_FILES = 10

TASK_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

TASK_ATTACHMENTS_UPLOAD_FOLDER = (os.getenv('TASK_ATTACHMENTS_UPLOAD_FOLDER') or 'TaskAttachments/').strip()

TASKS_MAX_PAGE_SIZE = int(os.getenv('TASKS_MAX_PAGE_SIZE', '200'))

TASK_TAG_LABELS = {
    'task': 'Задача',
    'problem': 'Проблема',
    'suggestion': 'Предложение',
    'production': 'Продакшн'
}

def _parse_task_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

def _parse_task_int(value, field_name, default=0, minimum=0, maximum=None):
    raw = str(value if value is not None else '').strip()
    if raw == '':
        return int(default)
    try:
        parsed = int(raw)
    except Exception:
        raise ValueError(f"{field_name} must be an integer")
    if parsed < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return parsed

def _parse_task_deadline_minutes(source):
    days = _parse_task_int(source.get('deadline_days'), 'deadline_days', default=0, minimum=0, maximum=3650)
    hours = _parse_task_int(source.get('deadline_hours'), 'deadline_hours', default=0, minimum=0, maximum=23)
    minutes = _parse_task_int(source.get('deadline_minutes'), 'deadline_minutes', default=0, minimum=0, maximum=59)
    total = days * 24 * 60 + hours * 60 + minutes
    return total if total > 0 else None

def _parse_task_estimate_minutes(source):
    """Плановая длительность задачи: либо estimate_minutes, либо часы+минуты из формы."""
    direct = source.get('estimate_minutes')
    if direct is not None and str(direct).strip() != '':
        total = _parse_task_int(direct, 'estimate_minutes', default=0, minimum=0, maximum=3650 * 24 * 60)
        return total or None
    hours = _parse_task_int(source.get('estimate_hours'), 'estimate_hours', default=0, minimum=0, maximum=3650 * 24)
    minutes = _parse_task_int(source.get('estimate_extra_minutes'), 'estimate_extra_minutes', default=0, minimum=0, maximum=59)
    total = hours * 60 + minutes
    return total or None

def _parse_task_spent_minutes(source):
    """Фактические трудозатраты: либо spent_minutes, либо часы+минуты из формы."""
    direct = source.get('spent_minutes')
    if direct is not None and str(direct).strip() != '':
        total = _parse_task_int(direct, 'spent_minutes', default=0, minimum=0, maximum=3650 * 24 * 60)
        return total or None
    hours = _parse_task_int(source.get('spent_hours'), 'spent_hours', default=0, minimum=0, maximum=3650 * 24)
    minutes = _parse_task_int(source.get('spent_extra_minutes'), 'spent_extra_minutes', default=0, minimum=0, maximum=59)
    total = hours * 60 + minutes
    return total or None

def _parse_task_checklist_items(raw_value):
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        raw_items = raw_value
    else:
        raw_text = str(raw_value or '').strip()
        if not raw_text:
            return []
        try:
            parsed = json.loads(raw_text)
            raw_items = parsed if isinstance(parsed, list) else []
        except Exception:
            raw_items = [line.strip() for line in raw_text.splitlines()]

    items = []
    seen = set()
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            title = str(raw_item.get('title') or raw_item.get('text') or '').strip()
            is_required = bool(raw_item.get('is_required', True))
        else:
            title = str(raw_item or '').strip()
            is_required = True
        if not title:
            continue
        title = title[:255]
        dedup_key = title.casefold()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        items.append({"title": title, "is_required": is_required})
        if len(items) >= 50:
            break
    return items

def _collect_task_notification_recipients(task_ctx, actor_user_id):
    recipients = []
    seen_chat_ids = set()

    creator_chat_id = task_ctx.get('creator_telegram_id')
    creator_id = task_ctx.get('created_by')
    if creator_chat_id and (creator_id is None or int(creator_id) != int(actor_user_id)):
        chat_key = str(creator_chat_id)
        if chat_key not in seen_chat_ids:
            seen_chat_ids.add(chat_key)
            recipients.append({
                "kind": "creator",
                "chat_id": creator_chat_id,
                "name": task_ctx.get('creator_name') or 'Постановщик'
            })

    # По строке на каждого исполнителя. Автора действия пропускаем — сообщать
    # человеку о его же клике незачем. Если состав почему-то не приехал,
    # откатываемся на скалярного исполнителя: молчать хуже, чем повторить.
    assignee_people = task_ctx.get('assignees')
    if not assignee_people:
        assignee_people = [{
            "id": task_ctx.get('assigned_to'),
            "telegram_id": task_ctx.get('assignee_telegram_id'),
            "name": task_ctx.get('assignee_name'),
        }]
    for person in assignee_people:
        assignee_chat_id = person.get('telegram_id')
        assignee_id = person.get('id')
        if not assignee_chat_id:
            continue
        if assignee_id is not None and int(assignee_id) == int(actor_user_id):
            continue
        chat_key = str(assignee_chat_id)
        if chat_key in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_key)
        recipients.append({
            "kind": "assignee",
            "chat_id": assignee_chat_id,
            "name": person.get('name') or 'Исполнитель'
        })

    # Поручитель принимает итог, поэтому обновления по задаче должны доходить и до него.
    requester_chat_id = task_ctx.get('requester_telegram_id')
    requester_id = task_ctx.get('requested_by')
    if requester_chat_id and (requester_id is None or int(requester_id) != int(actor_user_id)):
        chat_key = str(requester_chat_id)
        if chat_key not in seen_chat_ids:
            seen_chat_ids.add(chat_key)
            recipients.append({
                "kind": "requester",
                "chat_id": requester_chat_id,
                "name": task_ctx.get('requester_name') or 'Поручитель'
            })

    return recipients

TASK_MESSAGE_SAVED_LABELS = {
    'note': 'Задача дополнена',
    'request': 'Запрос отправлен постановщику',
    'answer': 'Ответ отправлен исполнителю',
}

TASK_MESSAGE_NOTIFICATION_HEADERS = {
    'note': '📌 Постановщик дополнил задачу',
    'request': '❓ Исполнителю не хватает информации',
    'answer': '💬 Ответ на запрос информации',
}

def _collect_task_assignee_recipients(task_ctx, actor_user_id):
    recipients = _collect_task_notification_recipients(task_ctx, actor_user_id)
    return [item for item in recipients if (item.get('kind') or '').strip() == 'assignee']

def _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths):
    if not gcs_bucket or not uploaded_blob_paths:
        return
    for uploaded_path in uploaded_blob_paths:
        try:
            gcs_bucket.blob(uploaded_path).delete()
        except Exception:
            pass

TASK_EXPORT_COLUMNS = {
    'id':          ('№', 7),
    'subject':     ('Тема', 52),
    'tag':         ('Тип', 14),
    'priority':    ('Срочность', 13),
    'assignee':    ('Исполнители', 34),
    'requester':   ('Поручил', 26),
    'created':     ('Создана', 17),
    'planned':     ('Плановый старт', 17),
    'due':         ('Дедлайн', 17),
    'estimate':    ('Оценка, ч', 11),
    'started':     ('Начата', 17),
    'spent':       ('Факт, ч', 10),
    'completed':   ('Завершена', 17),
    'summary':     ('Итог', 60),
    'brief':       ('Бриф', 48),
    'description': ('Описание', 60),
}

TASK_EXPORT_DATE_KEYS = frozenset({'created', 'planned', 'due', 'started', 'completed'})

TASK_EXPORT_HOUR_KEYS = frozenset({'estimate', 'spent'})

TASK_EXPORT_FIELD_BY_DATE_KEY = {
    'created': 'created_at',
    'planned': 'planned_start_at',
    'due': 'due_at',
    'started': 'started_at',
    'completed': 'completed_at',
}

TASK_EXPORT_CLOSED_STATUSES = frozenset({'completed', 'accepted'})

def _task_export_column_of(task):
    """В какой лист ложится задача. Повторяет BOARD_COLUMN_QUERY во фронте."""
    if task.get('is_backlog'):
        return 'backlog'
    status = (task.get('status') or '').strip().lower()
    if status in ('in_progress', 'returned'):
        return 'progress'
    if status == 'completed':
        return 'review'
    if status == 'accepted':
        return 'done'
    return 'todo'

def _task_export_hours(minutes):
    """Минуты -> часы: по колонке в Excel считают суммы, «3ч 30м» так не сложить."""
    if minutes is None:
        return None
    return round(int(minutes) / 60, 2)

TASK_REPORT_ERRORS = {
    'TASK_NOT_FOUND': ("Task not found", 404),
    'REPORT_NOT_FOUND': ("Отчёт не найден", 404),
    'REPORT_BODY_REQUIRED': ("Опишите проделанную работу", 400),
    'INVALID_REPORT_KIND': ("Invalid report kind", 400),
    'INVALID_SPENT_MINUTES': ("Invalid spent_minutes", 400),
    'NOTHING_TO_UPDATE': ("No fields to update", 400),
    'TASK_FORBIDDEN': ("You do not have access to this task", 403),
    'ONLY_TASK_PARTICIPANT': ("Отчёт пишут исполнитель, постановщик или админ", 403),
    'ONLY_REPORT_AUTHOR': ("Редактировать отчёт может только его автор", 403),
    # Лента уточнений по задаче
    'MESSAGE_NOT_FOUND': ("Уточнение не найдено", 404),
    'MESSAGE_BODY_REQUIRED': ("Напишите текст или приложите файл", 400),
    'INVALID_MESSAGE_KIND': ("Неизвестный вид уточнения", 400),
    'ONLY_TASK_OWNER_ADDS': ("Дополнить задачу может постановщик, поручитель или админ", 403),
    'ONLY_TASK_OWNER_ANSWERS': ("Ответить может постановщик, поручитель или админ", 403),
    'ONLY_ASSIGNEE_ASKS': ("Запросить информацию может только исполнитель", 403),
    'ONLY_REQUEST_AUTHOR': ("Снять запрос может только тот, кто его отправил", 403),
    'REQUEST_ALREADY_OPEN': ("Запрос уже отправлен — дождитесь ответа", 409),
    'NO_OPEN_REQUEST': ("По задаче нет открытого запроса информации", 409),
    'NOT_A_REQUEST': ("Это не запрос информации", 400),
    'NO_ONE_TO_ASK': ("Задача целиком ваша — спросить не у кого", 400),
    'TASK_CLOSED_FOR_REQUEST': ("Работа уже сдана — запрашивать информацию поздно", 409),
}

def _task_report_error_response(error):
    message, code = TASK_REPORT_ERRORS.get(str(error), (str(error), 400 if isinstance(error, ValueError) else 403))
    return jsonify({"error": message}), code


def build_tasks_blueprint(
    *,
    ALLOWED_ORIGINS,
    API_TOKEN,
    AVATAR_SIGNED_URL_CACHE,
    AVATAR_SIGNED_URL_CACHE_LOCK,
    AVATAR_SIGNED_URL_CACHE_MAX_ITEMS,
    AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS,
    AVATAR_SIGNED_URL_TTL_SECONDS,
    TASK_PRIORITY_LABELS,
    TASK_TELEGRAM_TIMEOUT_SECONDS,
    TASK_WEB_APP_BASE_URL,
    TELEGRAM_MAX_CAPTION_CHARS,
    TELEGRAM_MAX_MESSAGE_CHARS,
    _TASK_EXPORT_BASE,
    _build_avatar_signed_url,
    _build_current_task_deep_link,
    _build_task_deep_link,
    _build_task_notification_reply_markup,
    _department_scope_id_for_requester,
    _escape_telegram_html,
    _format_task_due_for_notification,
    _format_task_spent_for_notification,
    _get_telegram_error_text,
    _is_allowed_origin,
    _normalize_origin,
    _normalize_user_role,
    _send_telegram_text_message,
    _task_route_guard,
    _telegram_exception_text,
    _truncate_for_telegram,
    get_gcs_client,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('tasks', __name__, url_prefix='/api')

    def _parse_task_assignee_ids(raw_value):
        """Состав исполнителей из тела запроса.

    Понимает и массив из JSON (PATCH), и строку «12,15,7» из multipart-формы
    (POST принимает только form-data), и одиночный id — так же, как это делает
    Database._normalize_task_assignees, чтобы правила «дубликаты свернуть,
    порядок сохранить, потолок не превысить» жили в одном месте.

    Порядок значим: первый становится tasks.assigned_to.
    """
        if raw_value is None:
            raise ValueError("Выберите хотя бы одного исполнителя")
        try:
            return db._normalize_task_assignees(raw_value)
        except ValueError as normalize_error:
            code = str(normalize_error)
            if code == 'TOO_MANY_ASSIGNEES':
                raise ValueError(f"Исполнителей у задачи не больше {db.TASK_MAX_ASSIGNEES}")
            if code == 'ASSIGNEE_REQUIRED':
                raise ValueError("Выберите хотя бы одного исполнителя")
            raise ValueError("assigned_to must be an integer")

    def _task_assignees_outside_scope(assignee_ids, requester_id, requester_role):
        """Кому из состава поручать нельзя. Пустой список — всё в порядке.

    Список разрешённых спрашиваем ОДИН раз на весь состав: раньше эта сверка
    стояла отдельно в создании и в правке, дословно повторённая, и разъезжалась
    при любой правке одной из двух копий.
    """
        allowed_recipients = db.get_task_recipients(
            requester_id,
            requester_role,
            scope_department_id=getattr(g, 'task_scope_department_id', None)
        )
        allowed_ids = {int(item['id']) for item in allowed_recipients if item.get('id') is not None}
        return [person_id for person_id in (assignee_ids or []) if int(person_id) not in allowed_ids]

    def _task_assignee_chat_ids(assignee_ids):
        """Telegram-чаты исполнителей одним запросом (порядок — как в составе)."""
        ids = [int(item) for item in (assignee_ids or []) if item]
        if not ids:
            return []
        with db._get_cursor() as cursor:
            cursor.execute(
                "SELECT id, telegram_id FROM users"
                " WHERE id = ANY(%s) AND telegram_id IS NOT NULL",
                (ids,)
            )
            chat_by_id = {int(row[0]): row[1] for row in cursor.fetchall()}
        return [chat_by_id[person_id] for person_id in ids if person_id in chat_by_id]

    def _build_task_subject_notification_html(task_ctx, task_link=None):
        subject_safe = _escape_telegram_html(
            task_ctx.get('subject') or f"Задача #{task_ctx.get('id')}",
            220
        )
        if not task_link:
            return subject_safe
        return (
            f'<a href="{_escape_telegram_html(task_link, 500)}">'
            f'{subject_safe}'
            '</a>'
        )

    def _fetch_task_notification_context(task_id):
        with db._get_cursor() as cursor:
            cursor.execute("""
            SELECT
                t.id, t.subject, t.tag, t.priority, t.due_at, t.created_by,
                creator.telegram_id, creator.name,
                t.assigned_to, assignee.telegram_id, assignee.name,
                t.requested_by_id, requester.telegram_id,
                COALESCE(requester.name, t.requested_by_name),
                t.production_brief
            FROM tasks t
            LEFT JOIN users creator ON creator.id = t.created_by
            LEFT JOIN users assignee ON assignee.id = t.assigned_to
            LEFT JOIN users requester ON requester.id = t.requested_by_id
            WHERE t.id = %s
            LIMIT 1
        """, (int(task_id),))
            row = cursor.fetchone()
            # Весь состав исполнителей: задачу могли поручить нескольким, и знать о
            # ней должен каждый. Скалярные assignee_* ниже оставлены для мест, где
            # человек ровно один (текст «Исполнитель: …» в сообщении).
            assignee_rows = []
            if row:
                cursor.execute("""
                SELECT u.id, u.telegram_id, u.name
                  FROM task_assignees ta
                  JOIN users u ON u.id = ta.user_id
                 WHERE ta.task_id = %s
                 ORDER BY ta.position, ta.user_id
            """, (int(task_id),))
                assignee_rows = cursor.fetchall()
        if not row:
            return None
        return {
            "id": row[0],
            "subject": row[1],
            "tag": row[2],
            "priority": row[3],
            "due_at": row[4].isoformat() if hasattr(row[4], 'isoformat') else row[4],
            "created_by": row[5],
            "creator_telegram_id": row[6],
            "creator_name": row[7],
            "assigned_to": row[8],
            "assignee_telegram_id": row[9],
            "assignee_name": row[10],
            "assignees": [
                {"id": item[0], "telegram_id": item[1], "name": item[2]}
                for item in assignee_rows
            ],
            # Поручитель принимает итог — он должен получать уведомления по задаче.
            "requested_by": row[11],
            "requester_telegram_id": row[12],
            "requester_name": row[13],
            # Бриф продакшна (#354): из него — строка «Что:» в сообщениях.
            "production_brief": row[14]
        }

    def _build_task_status_notification_html(
        action,
        task_ctx,
        actor_name,
        recipient_kind,
        comment=None,
        completion_summary=None,
        completion_files_count=0,
        task_link=None,
        spent_minutes=None
    ):
        action_norm = (action or '').strip().lower()
        tag_label = TASK_TAG_LABELS.get((task_ctx.get('tag') or 'task').strip().lower(), 'Задача')
        priority_label = TASK_PRIORITY_LABELS.get((task_ctx.get('priority') or 'normal').strip().lower(), 'Обычная')
        due_label = _format_task_due_for_notification(task_ctx.get('due_at'))
        subject_safe = _build_task_subject_notification_html(task_ctx, task_link)
        tag_safe = _escape_telegram_html(tag_label, 60)
        priority_safe = _escape_telegram_html(priority_label, 60)
        actor_safe = _escape_telegram_html(actor_name or 'Сотрудник', 80)

        header_map = {
            'in_progress': '📥 Задача принята в работу',
            'completed': '✅ Задача отмечена выполненной',
            'accepted': '✅ Результат принят',
            'returned': '↩️ Задача возвращена на доработку',
            'reopened': '🔄 Задача возобновлена'
        }
        status_map = {
            'in_progress': 'В работе',
            'completed': 'Ожидает проверки',
            'accepted': 'Принята',
            'returned': 'Возвращена',
            'reopened': 'Снова в работе'
        }

        lines = [
            f"<b>{header_map.get(action_norm, '🔔 Обновление статуса задачи')}</b>",
            "",
            f"<b>Тип:</b> {tag_safe}",
            f"<b>Срочность:</b> {priority_safe}",
            f"<b>Тема:</b> {subject_safe}",
            f"<b>Статус:</b> {status_map.get(action_norm, 'Обновлён')}",
            f"<b>Кто изменил:</b> {actor_safe}"
        ]
        if due_label:
            lines.insert(4, f"<b>Дедлайн:</b> {_escape_telegram_html(due_label, 80)}")

        if comment:
            lines.extend([
                "",
                "<b>Комментарий:</b>",
                _escape_telegram_html(comment, 800)
            ])

        if action_norm == 'completed':
            lines.append(f"<b>Файлов:</b> {int(completion_files_count or 0)}")
            spent_label = _format_task_spent_for_notification(spent_minutes)
            if spent_label:
                lines.append(f"<b>Затрачено:</b> {_escape_telegram_html(spent_label, 40)}")
            lines.extend([
                "",
                "<b>Отчёт о проделанной работе:</b>",
                _escape_telegram_html(completion_summary or "Отчёт не заполнен.", 2500)
            ])
            # Итог принимает поручитель — говорим ему об этом прямо, а не «просто уведомляем».
            if (recipient_kind or '').strip() == 'requester':
                lines.extend([
                    "",
                    "<b>Задача ждёт вашей приёмки:</b> проверьте итог и примите его или верните на доработку."
                ])

        message = "\n".join(lines)
        if len(message) > TELEGRAM_MAX_MESSAGE_CHARS:
            message = message[:TELEGRAM_MAX_MESSAGE_CHARS]
        return message

    def _task_production_item_line(production_brief):
        """«Что:» в Telegram у продакшна (задача #354): тип без наименования ничего
    не говорит исполнителю, а весь бриф — в карточке задачи."""
        import task_production
        item = task_production.item_label(production_brief)
        return f"<b>Что:</b> {_escape_telegram_html(item, 120)}" if item else None

    def _build_task_event_notification_html(event, task_ctx, actor_name, changed_fields=None, task_link=None):
        event_norm = (event or '').strip().lower()
        tag_label = TASK_TAG_LABELS.get((task_ctx.get('tag') or 'task').strip().lower(), 'Задача')
        priority_label = TASK_PRIORITY_LABELS.get((task_ctx.get('priority') or 'normal').strip().lower(), 'Обычная')
        due_label = _format_task_due_for_notification(task_ctx.get('due_at'))
        subject_safe = _build_task_subject_notification_html(task_ctx, task_link)
        tag_safe = _escape_telegram_html(tag_label, 60)
        priority_safe = _escape_telegram_html(priority_label, 60)
        actor_safe = _escape_telegram_html(actor_name or 'Сотрудник', 80)

        header_map = {
            'edited': '✏️ Задача обновлена',
            'deleted': '🗑️ Задача удалена',
            'promoted': '🆕 Задача из бэклога в работу',
            'planned': '🗓️ Сроки задачи обновлены'
        }

        lines = [
            f"<b>{header_map.get(event_norm, '🔔 Обновление задачи')}</b>",
            "",
            f"<b>Тип:</b> {tag_safe}",
            f"<b>Срочность:</b> {priority_safe}",
            f"<b>Тема:</b> {subject_safe}",
            f"<b>Кто изменил:</b> {actor_safe}"
        ]
        if due_label:
            lines.insert(4, f"<b>Дедлайн:</b> {_escape_telegram_html(due_label, 80)}")
        # Из бэклога задача впервые доходит до исполнителя — как и при постановке,
        # ему сразу видно, что именно сделать.
        production_line = _task_production_item_line(task_ctx.get('production_brief')) if event_norm == 'promoted' else None
        if production_line:
            lines.insert(3, production_line)

        if event_norm in ('edited', 'planned'):
            labels = {
                'subject': 'тема',
                'description': 'описание',
                'tag': 'тип',
                'priority': 'срочность',
                'deadline': 'дедлайн',
                'recurrence': 'регламент',
                'checklist': 'чек-лист',
                'assigned_to': 'состав исполнителей',
                'estimate': 'оценка',
                'planned_start': 'плановый старт',
                'is_backlog': 'бэклог',
                'backlog_rank': 'приоритет в бэклоге',
                'requested_by': 'кто поручил',
                'reminder': 'напоминание',
                'production_brief': 'бриф'
            }
            changed_items = []
            for item in (changed_fields or []):
                key = str(item or '').strip()
                if not key:
                    continue
                changed_items.append(labels.get(key, key))
            if changed_items:
                lines.append(f"<b>Изменено:</b> {_escape_telegram_html(', '.join(changed_items), 120)}")

        if event_norm == 'promoted':
            lines.append("")
            lines.append("<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>")

        message = "\n".join(lines)
        if len(message) > TELEGRAM_MAX_MESSAGE_CHARS:
            message = message[:TELEGRAM_MAX_MESSAGE_CHARS]
        return message

    def _build_task_report_notification_html(task_ctx, actor_name, report, task_link=None):
        is_final = (report or {}).get('kind') == 'completion'
        header = '📄 Итоговый отчёт по задаче' if is_final else '📝 Отчёт о проделанной работе'
        lines = [
            f"<b>{header}</b>",
            "",
            f"<b>Тема:</b> {_build_task_subject_notification_html(task_ctx, task_link)}",
            f"<b>Автор:</b> {_escape_telegram_html(actor_name or 'Сотрудник', 80)}"
        ]
        spent_label = _format_task_spent_for_notification((report or {}).get('spent_minutes'))
        if spent_label:
            lines.append(f"<b>Затрачено:</b> {_escape_telegram_html(spent_label, 40)}")
        body = str((report or {}).get('body') or '').strip()
        if body:
            lines.append("")
            lines.append(_escape_telegram_html(body, 1500))
        message = "\n".join(lines)
        if len(message) > TELEGRAM_MAX_MESSAGE_CHARS:
            message = message[:TELEGRAM_MAX_MESSAGE_CHARS]
        return message

    def _build_task_message_notification_html(task_ctx, actor_name, message, task_link=None):
        """Уточнение по задаче: дополнение постановки, запрос информации либо ответ."""
        kind = str((message or {}).get('kind') or 'note').strip().lower()
        header = TASK_MESSAGE_NOTIFICATION_HEADERS.get(kind, '📌 Уточнение по задаче')
        lines = [
            f"<b>{header}</b>",
            "",
            f"<b>Тема:</b> {_build_task_subject_notification_html(task_ctx, task_link)}",
            f"<b>Автор:</b> {_escape_telegram_html(actor_name or 'Сотрудник', 80)}"
        ]
        attachments = (message or {}).get('attachments') or []
        if attachments:
            lines.append(f"<b>Файлов:</b> {len(attachments)}")
        body = str((message or {}).get('body') or '').strip()
        if body:
            lines.append("")
            lines.append(_escape_telegram_html(body, 1500))
        if kind == 'request':
            lines.append("")
            lines.append("Ответьте в карточке задачи — до ответа работа стоит.")
        message_html = "\n".join(lines)
        if len(message_html) > TELEGRAM_MAX_MESSAGE_CHARS:
            message_html = message_html[:TELEGRAM_MAX_MESSAGE_CHARS]
        return message_html

    def _build_task_checklist_notification_html(task_ctx, checklist_item, actor_name, is_done, task_link=None):
        tag_label = TASK_TAG_LABELS.get((task_ctx.get('tag') or 'task').strip().lower(), 'Задача')
        priority_label = TASK_PRIORITY_LABELS.get((task_ctx.get('priority') or 'normal').strip().lower(), 'Обычная')
        due_label = _format_task_due_for_notification(task_ctx.get('due_at'))
        subject_safe = _build_task_subject_notification_html(task_ctx, task_link)
        status_label = 'выполнен' if is_done else 'снова открыт'
        lines = [
            "<b>☑️ Обновление чек-листа</b>",
            "",
            f"<b>Тип:</b> {_escape_telegram_html(tag_label, 60)}",
            f"<b>Срочность:</b> {_escape_telegram_html(priority_label, 60)}",
            f"<b>Тема:</b> {subject_safe}",
            f"<b>Пункт:</b> {_escape_telegram_html(checklist_item.get('title') or 'Пункт чек-листа', 180)}",
            f"<b>Статус:</b> {_escape_telegram_html(status_label, 40)}",
            f"<b>Кто изменил:</b> {_escape_telegram_html(actor_name or 'Сотрудник', 80)}"
        ]
        if due_label:
            lines.insert(4, f"<b>Дедлайн:</b> {_escape_telegram_html(due_label, 80)}")
        message = "\n".join(lines)
        if len(message) > TELEGRAM_MAX_MESSAGE_CHARS:
            message = message[:TELEGRAM_MAX_MESSAGE_CHARS]
        return message

    def _send_task_completion_attachments_to_telegram(chat_id, task_subject, attachments, task_link=None):
        warnings = []
        if not chat_id or not attachments:
            return warnings

        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendDocument"
        gcs_client = None

        for idx, attachment in enumerate(attachments, start=1):
            try:
                storage_type = str(attachment.get('storage_type') or 'gcs').strip().lower() or 'gcs'
                file_name = (attachment.get('file_name') or f'attachment_{idx}').strip() or f'attachment_{idx}'
                content_type = (attachment.get('content_type') or '').strip() or 'application/octet-stream'

                if storage_type == 'gcs':
                    bucket_name = (attachment.get('gcs_bucket') or '').strip()
                    blob_path = (attachment.get('gcs_blob_path') or '').strip()
                    if not bucket_name or not blob_path:
                        warnings.append(f"Не удалось отправить файл '{file_name}': отсутствуют метаданные хранилища")
                        continue
                    if gcs_client is None:
                        gcs_client = get_gcs_client()
                    blob = gcs_client.bucket(bucket_name).blob(blob_path)
                    if not blob.exists():
                        warnings.append(f"Не удалось отправить файл '{file_name}': файл не найден в хранилище")
                        continue
                    file_bytes = blob.download_as_bytes()
                else:
                    file_data = attachment.get('file_data')
                    if isinstance(file_data, memoryview):
                        file_bytes = file_data.tobytes()
                    elif isinstance(file_data, bytearray):
                        file_bytes = bytes(file_data)
                    elif isinstance(file_data, bytes):
                        file_bytes = file_data
                    else:
                        warnings.append(f"Не удалось отправить файл '{file_name}': неподдерживаемый формат данных")
                        continue

                files = {
                    'document': (file_name, file_bytes, content_type)
                }
                data = {
                    'chat_id': int(chat_id)
                }
                if content_type.lower().startswith('image/webp'):
                    # Фото задач теперь лежат в WebP (task_photos), а .webp,
                    # присланный документом, Telegram распознаёт и показывает
                    # СТИКЕРОМ — без подписи и без кнопки «сохранить файл».
                    data['disable_content_type_detection'] = 'true'
                if idx == 1:
                    task_subject_html = _escape_telegram_html(task_subject or 'Без названия', 180)
                    if task_link:
                        task_subject_html = (
                            f'<a href="{_escape_telegram_html(task_link, 500)}">'
                            f'{task_subject_html}'
                            '</a>'
                        )
                    caption = (
                        "<b>📎 Файлы по задаче</b>\n"
                        f"{task_subject_html}"
                    )
                    if len(caption) > TELEGRAM_MAX_CAPTION_CHARS:
                        caption = caption[:TELEGRAM_MAX_CAPTION_CHARS]
                    if caption:
                        data['caption'] = caption
                        data['parse_mode'] = 'HTML'

                response = requests.post(
                    telegram_url,
                    files=files,
                    data=data,
                    timeout=TASK_TELEGRAM_TIMEOUT_SECONDS
                )
                if response.status_code != 200:
                    warnings.append(f"Не удалось отправить файл '{file_name}': {_get_telegram_error_text(response)}")
            except Exception as attachment_error:
                warnings.append(f"Не удалось отправить файл '{attachment.get('file_name') or idx}': {_telegram_exception_text(attachment_error)}")

        return warnings

    def _delete_task_attachment_blobs_from_gcs(attachments):
        warnings = []
        if not attachments:
            return warnings

        try:
            gcs_client = get_gcs_client()
        except Exception as storage_error:
            return [f"Failed to initialize GCS client: {storage_error}"]

        for attachment in attachments:
            storage_type = (attachment.get('storage_type') or 'gcs').strip().lower() or 'gcs'
            if storage_type != 'gcs':
                continue

            bucket_name = (attachment.get('gcs_bucket') or '').strip()
            blob_path = (attachment.get('gcs_blob_path') or '').strip()
            file_name = (attachment.get('file_name') or 'attachment').strip() or 'attachment'
            if not bucket_name or not blob_path:
                warnings.append(f"Missing storage metadata for '{file_name}'")
                continue

            try:
                gcs_client.bucket(bucket_name).blob(blob_path).delete()
            except Exception as delete_error:
                warnings.append(f"Failed to delete '{file_name}' from GCS: {delete_error}")

            # Миниатюра фотографии — служебный файл: человеку о ней знать незачем,
            # поэтому её осечка в предупреждения не попадает, только в журнал.
            thumb_path = (attachment.get('thumb_blob_path') or '').strip()
            if thumb_path:
                try:
                    gcs_client.bucket(bucket_name).blob(thumb_path).delete()
                except Exception:
                    logging.warning("Failed to delete task photo thumbnail %s", thumb_path, exc_info=True)

        return warnings

    def _upload_task_attachments_to_gcs(files, stage='initial'):
        file_items = [item for item in (files or []) if item and item.filename]
        if len(file_items) > TASK_MAX_FILES:
            raise ValueError(f"Too many files. Max allowed: {TASK_MAX_FILES}")
        if not file_items:
            return [], [], None

        bucket_name = (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS') or '').strip()
        if not bucket_name:
            raise RuntimeError("GOOGLE_CLOUD_STORAGE_BUCKET_TASKS is not configured")

        upload_folder = TASK_ATTACHMENTS_UPLOAD_FOLDER.strip('/')
        upload_prefix = f"{upload_folder}/" if upload_folder else ""
        gcs_client = get_gcs_client()
        gcs_bucket = gcs_client.bucket(bucket_name)

        attachments = []
        uploaded_blob_paths = []

        for idx, file_storage in enumerate(file_items):
            file_data = file_storage.read() or b''
            if len(file_data) > TASK_MAX_FILE_SIZE_BYTES:
                raise ValueError(f"File '{file_storage.filename}' exceeds 10 MB limit")

            safe_name = secure_filename(file_storage.filename) or f'attachment_{idx + 1}'
            content_type = (file_storage.mimetype or '').strip() or 'application/octet-stream'

            # Картинка уходит в бакет WebP'ом, рядом — миниатюра для плитки в
            # карточке (task_photos). Всё, что не перевелось, ложится как есть:
            # отказать во вложении из-за кодека хуже, чем сохранить исходник.
            photo = None
            try:
                import task_photos
                photo = task_photos.prepare(file_data, filename=file_storage.filename,
                                            content_type=content_type)
            except Exception:
                logging.exception("Task photo conversion failed, storing the file as is")
            if photo:
                file_data = photo['data']
                content_type = photo['content_type']
                safe_name = photo['file_name']

            blob_path = (
                f"{upload_prefix}tasks/{stage}/"
                f"{datetime.utcnow().strftime('%Y/%m/%d')}/"
                f"{uuid.uuid4().hex}_{safe_name}"
            )

            blob = gcs_bucket.blob(blob_path)
            blob.upload_from_string(file_data, content_type=content_type)
            uploaded_blob_paths.append(blob_path)

            # Миниатюра не обязательна: без неё плитка покажет полный кадр, поэтому
            # её осечка не отменяет загрузку вложения.
            thumb_path = None
            if photo and photo.get('thumb'):
                candidate = task_photos.thumb_blob_path(blob_path)
                try:
                    gcs_bucket.blob(candidate).upload_from_string(photo['thumb'], content_type='image/webp')
                    uploaded_blob_paths.append(candidate)
                    thumb_path = candidate
                except Exception:
                    logging.warning("Task photo thumbnail upload failed", exc_info=True)

            attachments.append({
                "file_name": safe_name,
                "content_type": content_type,
                "file_size": len(file_data),
                "storage_type": "gcs",
                "gcs_bucket": bucket_name,
                "gcs_blob_path": blob_path,
                "thumb_blob_path": thumb_path
            })

        return attachments, uploaded_blob_paths, gcs_bucket

    TASK_EXPORT_SHEETS = (
        ('backlog',  'Бэклог',
         _TASK_EXPORT_BASE + ('due', 'estimate', 'brief', 'description')),
        ('todo',     'К выполнению',
         _TASK_EXPORT_BASE + ('planned', 'due', 'estimate', 'brief', 'description')),
        ('progress', 'В работе',
         _TASK_EXPORT_BASE + ('planned', 'due', 'estimate', 'started', 'spent', 'brief', 'description')),
        ('review',   'На проверке',
         _TASK_EXPORT_BASE + ('due', 'estimate', 'started', 'spent', 'completed', 'summary', 'brief', 'description')),
        ('done',     'Готово',
         _TASK_EXPORT_BASE + ('due', 'estimate', 'started', 'spent', 'completed', 'summary', 'brief', 'description')),
    )

    def _task_export_value(key, task):
        import task_production
        if key == 'id':
            return task.get('id')
        if key == 'subject':
            return task.get('subject') or ''
        if key == 'tag':
            return TASK_TAG_LABELS.get((task.get('tag') or 'task').strip().lower(), 'Задача')
        if key == 'priority':
            return TASK_PRIORITY_LABELS.get((task.get('priority') or 'normal').strip().lower(), 'Обычная')
        if key == 'assignee':
            return task.get('assignee_name') or ''
        if key == 'requester':
            return task.get('requested_by_name') or ''
        if key == 'estimate':
            return _task_export_hours(task.get('estimate_minutes'))
        if key == 'spent':
            return _task_export_hours(task.get('spent_minutes'))
        if key == 'summary':
            return task.get('completion_summary') or ''
        if key == 'brief':
            # Бриф продакшна строками «Поле: значение». У остальных задач клетка
            # пустая, и лист без единого продакшна колонку не покажет вовсе.
            return task_production.describe_text(task.get('production_brief'))
        if key == 'description':
            return task.get('description') or ''
        return task.get(TASK_EXPORT_FIELD_BY_DATE_KEY[key])

    def _task_export_fill_sheet(sheet, columns, tasks, now):
        values = {key: [_task_export_value(key, task) for task in tasks] for key in columns}
        # Колонку, пустую во ВСЕХ строках листа, не выводим вовсе: заголовок без
        # единого значения — шум, из-за которого лист приходится листать вбок.
        if tasks:
            columns = [
                key for key in columns
                if any(value not in (None, '') for value in values[key])
            ]

        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(fill_type='solid', fgColor='1F2937')
        overdue_font = Font(color='B91C1C')
        for col, key in enumerate(columns, start=1):
            title, width = TASK_EXPORT_COLUMNS[key]
            cell = sheet.cell(row=1, column=col, value=title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            sheet.column_dimensions[get_column_letter(col)].width = width

        for idx, task in enumerate(tasks, start=2):
            # Красим только просроченный дедлайн: остальные состояния нейтральны, и
            # цвет в них был бы шумом, а не подсказкой.
            due_at = task.get('due_at')
            is_overdue = bool(
                due_at
                and due_at < now
                and (task.get('status') or '') not in TASK_EXPORT_CLOSED_STATUSES
            )
            for col, key in enumerate(columns, start=1):
                cell = sheet.cell(row=idx, column=col, value=values[key][idx - 2])
                if key in TASK_EXPORT_DATE_KEYS:
                    cell.number_format = 'DD.MM.YYYY HH:MM'
                    if key == 'due' and is_overdue:
                        cell.font = overdue_font
                elif key in TASK_EXPORT_HOUR_KEYS:
                    cell.number_format = '0.##'

        # Шапка закреплена и с фильтром — по сотням строк иначе не поработать.
        sheet.freeze_panes = 'A2'
        last_column = get_column_letter(len(columns))
        sheet.auto_filter.ref = 'A1:%s%d' % (last_column, max(len(tasks) + 1, 2))

    def _notify_task_message(task_id, requester, requester_id, message):
        """Уточнение уходит всем участникам задачи, кроме автора."""
        warnings = []
        try:
            task_ctx = _fetch_task_notification_context(task_id)
            if not task_ctx:
                return warnings
            actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
            task_link = _build_current_task_deep_link(task_ctx.get('id'))
            message_html = _build_task_message_notification_html(
                task_ctx=task_ctx,
                actor_name=actor_name,
                message=message,
                task_link=task_link
            )
            for recipient in _collect_task_notification_recipients(task_ctx, requester_id):
                response = _send_telegram_text_message(
                    recipient.get('chat_id'),
                    message_html,
                    parse_mode='HTML',
                    reply_markup=_build_task_notification_reply_markup(task_link)
                )
                if response.status_code != 200:
                    warnings.append(
                        f"Не удалось отправить уведомление ({recipient.get('name') or 'получатель'}): "
                        f"{_get_telegram_error_text(response)}"
                    )
        except Exception as notify_error:
            warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")
        return warnings

    def _delete_production_material_blobs(blobs):
        """Объекты материалов удалённой задачи. Осечка — в журнал, не человеку.

    Незаконченные загрузки сначала отменяются: иначе браузер догрузил бы файл
    уже после удаления задачи, и объект остался бы в бакете без хозяина.
    """
        if not blobs:
            return
        from task_production import storage as production_storage

        for blob in blobs:
            if blob.get('upload_session'):
                production_storage.cancel_session(blob['upload_session'], origin=blob.get('upload_origin'))
        try:
            client = get_gcs_client()
        except Exception:
            logging.exception("Продакшн: нет клиента хранилища для уборки материалов")
            return
        for blob in blobs:
            bucket_name, blob_path = blob.get('bucket'), blob.get('blob_path')
            if not bucket_name or not blob_path:
                continue
            try:
                client.bucket(bucket_name).blob(blob_path).delete()
            except Exception as delete_error:
                if delete_error.__class__.__name__ != 'NotFound':
                    logging.warning("Продакшн: не удалось удалить %s: %s", blob_path, delete_error)

    @bp.route('/tasks/recipients', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_task_recipients():
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])
            recipients = db.get_task_recipients(
                requester_id,
                requester_role,
                scope_department_id=getattr(g, 'task_scope_department_id', None)
            )
            return jsonify({
                "status": "success",
                "recipients": recipients
            }), 200
        except Exception as e:
            logging.error(f"Error in get_task_recipients: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/tasks/board_people', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_task_board_people():
        """Список сотрудников для переключателя досок: без уволенных, с отделами."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])
            people = db.get_task_board_people(requester_id, requester_role)
            for person in people:
                person["avatar_url"] = _build_avatar_signed_url(
                    person.pop("avatar_bucket", None),
                    person.pop("avatar_blob_path", None)
                )
            return jsonify({"status": "success", "people": people}), 200
        except Exception as e:
            logging.error(f"Error in get_task_board_people: {e}")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/departments', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_task_departments():
        """Отделы для переключателя раздела + отдел, выбранный по умолчанию.

    Принадлежность задачи отделу выводится из членства того, кто её поставил.
    Значение по умолчанию считает сервер: у главы отдела это возглавляемый
    отдел, а не строка в его профиле, — на клиенте это не вычислить.
    """
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])
            departments = db.get_task_departments(requester_id, requester_role)

            # У личного охвата отдела по умолчанию нет. Отдел задачи — это отдел
            # того, кто её ПОСТАВИЛ, поэтому подставить рядовому сотруднику его
            # собственный отдел значит спрятать от него всё, что поручили извне:
            # переключатель молча срезал бы задачу, пришедшую от админа из другого
            # отдела. Ему нужны все свои задачи, а не задачи своего отдела.
            default_department_id = (
                None if getattr(g, 'task_scope_is_personal', False)
                else _department_scope_id_for_requester(requester_id)
            )
            # Отдел без единой видимой задачи по умолчанию не выбираем: раздел
            # открылся бы пустым, и это читалось бы как «задачи пропали».
            if default_department_id is not None and not any(
                item.get('id') == default_department_id for item in departments
            ):
                default_department_id = None

            return jsonify({
                "status": "success",
                "departments": departments,
                "default_department_id": default_department_id
            }), 200
        except Exception as e:
            logging.error(f"Error in get_task_departments: {e}")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/action_required', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_task_action_required_count():
        """Бейдж сайдбара: сколько задач ждут действия лично от пользователя."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            summary = db.get_task_action_needs_summary(requester_id)
            return jsonify({
                "status": "success",
                "count": summary.get("count", 0),
                "breakdown": summary.get("breakdown", {})
            }), 200
        except Exception as e:
            logging.error(f"Error in get_task_action_required_count: {e}")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/action_seen', methods=['POST', 'OPTIONS'])
    @require_api_key
    def mark_task_action_seen(task_id):
        """Уведомление просмотрено: гасим его в счётчике, задачу не трогаем."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            payload = request.get_json(silent=True) or {}
            try:
                summary = db.mark_task_action_seen(requester_id, task_id, payload.get('kind'))
            except ValueError as e:
                error_code = str(e)
                if error_code == 'INVALID_TASK_ACTION_KIND':
                    return jsonify({"error": "Invalid action kind"}), 400
                if error_code == 'TASK_NOT_FOUND':
                    return jsonify({"error": "Task not found"}), 404
                raise

            return jsonify({
                "status": "success",
                "count": summary.get("count", 0),
                "breakdown": summary.get("breakdown", {})
            }), 200
        except Exception as e:
            logging.error(f"Error in mark_task_action_seen: {e}")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/notes', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def handle_task_notes():
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if request.method == 'GET':
                notes = db.get_task_notes(requester_id)
                return jsonify({
                    "status": "success",
                    "notes": notes
                }), 200

            data = request.get_json() or {}
            title = str(data.get('title') or '').strip()
            body = str(data.get('body') or data.get('text') or '')
            priority = (str(data.get('priority') or 'normal').strip().lower() or 'normal')
            due_at = data.get('due_at') if 'due_at' in data else None
            is_task = _parse_task_bool(data.get('is_task'))
            is_done = _parse_task_bool(data.get('is_done')) if is_task else False

            if priority not in TASK_ALLOWED_PRIORITIES:
                return jsonify({"error": "Invalid priority"}), 400

            try:
                note = db.create_task_note(
                    owner_id=requester_id,
                    title=title,
                    body=body,
                    priority=priority,
                    due_at=due_at,
                    is_task=is_task,
                    is_done=is_done,
                    reminder_minutes_before=data.get('reminder_minutes_before')
                )
            except ValueError as create_error:
                code = str(create_error)
                if code == 'INVALID_PRIORITY':
                    return jsonify({"error": "Invalid priority"}), 400
                if code == 'INVALID_DUE_AT':
                    return jsonify({"error": "Invalid due_at"}), 400
                if code == 'INVALID_REMINDER':
                    return jsonify({"error": "Напоминание можно поставить максимум за сутки до дедлайна"}), 400
                return jsonify({"error": code}), 400

            return jsonify({
                "status": "success",
                "message": "Note saved successfully",
                "note": note
            }), 201

        except Exception as e:
            logging.error(f"Error in handle_task_notes: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/notes/<int:note_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def handle_single_task_note(note_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if request.method == 'PATCH':
                data = request.get_json() or {}
                has_title = 'title' in data
                has_body = 'body' in data or 'text' in data
                has_priority = 'priority' in data
                has_due_at = 'due_at' in data
                has_is_task = 'is_task' in data
                has_is_done = 'is_done' in data
                has_reminder = 'reminder_minutes_before' in data

                if not any([has_title, has_body, has_priority, has_due_at, has_is_task, has_is_done, has_reminder]):
                    return jsonify({"error": "No fields to update"}), 400

                priority = (str(data.get('priority') or '').strip().lower() or 'normal') if has_priority else None
                if has_priority and priority not in TASK_ALLOWED_PRIORITIES:
                    return jsonify({"error": "Invalid priority"}), 400

                edit_kwargs = {
                    "note_id": note_id,
                    "owner_id": requester_id
                }
                if has_title:
                    edit_kwargs["title"] = data.get('title')
                if has_body:
                    edit_kwargs["body"] = data.get('body') if 'body' in data else data.get('text')
                if has_priority:
                    edit_kwargs["priority"] = priority
                if has_due_at:
                    edit_kwargs["due_at"] = data.get('due_at')
                if has_is_task:
                    edit_kwargs["is_task"] = _parse_task_bool(data.get('is_task'))
                if has_is_done:
                    edit_kwargs["is_done"] = _parse_task_bool(data.get('is_done'))
                if has_reminder:
                    edit_kwargs["reminder_minutes_before"] = data.get('reminder_minutes_before')

                try:
                    note = db.update_task_note(**edit_kwargs)
                except ValueError as update_error:
                    code = str(update_error)
                    if code == 'NOTE_NOT_FOUND':
                        return jsonify({"error": "Note not found"}), 404
                    if code == 'NOTHING_TO_UPDATE':
                        return jsonify({"error": "No fields to update"}), 400
                    if code == 'INVALID_PRIORITY':
                        return jsonify({"error": "Invalid priority"}), 400
                    if code == 'INVALID_DUE_AT':
                        return jsonify({"error": "Invalid due_at"}), 400
                    if code == 'INVALID_REMINDER':
                        return jsonify({"error": "Напоминание можно поставить максимум за сутки до дедлайна"}), 400
                    return jsonify({"error": code}), 400
                except PermissionError:
                    return jsonify({"error": "You do not have access to this note"}), 403

                return jsonify({
                    "status": "success",
                    "message": "Note updated successfully",
                    "note": note
                }), 200

            try:
                result = db.delete_task_note(note_id=note_id, owner_id=requester_id)
            except ValueError as delete_error:
                code = str(delete_error)
                if code == 'NOTE_NOT_FOUND':
                    return jsonify({"error": "Note not found"}), 404
                return jsonify({"error": code}), 400

            return jsonify({
                "status": "success",
                "message": "Note deleted successfully",
                **result
            }), 200

        except Exception as e:
            logging.error(f"Error in handle_single_task_note: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/export', methods=['GET', 'OPTIONS'])
    @require_api_key
    def export_tasks_excel():
        """Выгрузка задач в Excel: колонка доски = отдельный лист.

    Охват тот же, что у доски: сервер отбирает задачи теми же условиями, что и
    список раздела, поэтому в файл не попадёт ничего, чего человек не видит.
    """
        requester_id, requester, guard_response, guard_status = _task_route_guard()
        if guard_response is not None:
            return guard_response, guard_status

        requester_role = getattr(g, 'effective_task_role', requester[3])
        try:
            tasks = db.get_tasks_for_export(
                requester_id=requester_id,
                requester_role=requester_role,
                mine=(request.args.get('mine') or '').strip() or None,
                person_id=request.args.get('person_id'),
                person_scope=(request.args.get('person_scope') or '').strip() or None,
                department_id=(request.args.get('department_id') or '').strip() or None,
            )
        except ValueError:
            return jsonify({"error": "Invalid export filter"}), 400
        except Exception:
            logging.exception('tasks: выгрузка не удалась')
            return jsonify({"error": "Internal server error"}), 500

        by_column = collections.defaultdict(list)
        for task in tasks:
            by_column[_task_export_column_of(task)].append(task)

        now = datetime.now()
        workbook = Workbook()
        workbook.remove(workbook.active)
        for column_id, sheet_title, columns in TASK_EXPORT_SHEETS:
            # Пустую колонку листом не заводим: шапка без строк читается как «файл не
            # сформировался», а не как «здесь пусто».
            if not by_column.get(column_id):
                continue
            _task_export_fill_sheet(
                workbook.create_sheet(sheet_title), columns, by_column[column_id], now
            )
        if not workbook.sheetnames:
            _task_export_fill_sheet(
                workbook.create_sheet('Задачи'), TASK_EXPORT_SHEETS[1][2], [], now
            )

        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return send_file(
            stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Задачи_%s.xlsx' % now.strftime('%Y-%m-%d')
        )

    @bp.route('/tasks', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def handle_tasks():
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])

            if request.method == 'GET':
                try:
                    created_regulation_task_ids = db.materialize_due_regulation_tasks()
                    for created_task_id in created_regulation_task_ids[:20]:
                        task_ctx = _fetch_task_notification_context(created_task_id)
                        if not task_ctx:
                            continue
                        # Регламент мог быть поручен нескольким — тогда новая
                        # итерация приходит каждому. Раньше здесь брался скаляр, и
                        # соисполнители о ночной задаче в Telegram не узнавали.
                        assignee_chat_ids = [
                            person.get('telegram_id')
                            for person in (task_ctx.get('assignees') or [])
                            if person.get('telegram_id')
                        ] or ([task_ctx.get('assignee_telegram_id')]
                              if task_ctx.get('assignee_telegram_id') else [])
                        if not assignee_chat_ids:
                            continue
                        task_link = _build_current_task_deep_link(created_task_id)
                        tag_label = TASK_TAG_LABELS.get((task_ctx.get('tag') or 'task').strip().lower(), 'Задача')
                        priority_label = TASK_PRIORITY_LABELS.get((task_ctx.get('priority') or 'normal').strip().lower(), 'Обычная')
                        due_label = _format_task_due_for_notification(task_ctx.get('due_at'))
                        task_subject_html = _build_task_subject_notification_html(task_ctx, task_link)
                        lines = [
                            "<b>🔁 Новая регламентная задача</b>",
                            "",
                            f"<b>Тип:</b> {_escape_telegram_html(tag_label, 60)}",
                            f"<b>Срочность:</b> {_escape_telegram_html(priority_label, 60)}",
                            f"<b>Тема:</b> {task_subject_html}",
                            "",
                            "<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>"
                        ]
                        if due_label:
                            lines.insert(4, f"<b>Дедлайн:</b> {_escape_telegram_html(due_label, 80)}")
                        production_line = _task_production_item_line(task_ctx.get('production_brief'))
                        if production_line:
                            lines.insert(3, production_line)
                        for assignee_chat_id in assignee_chat_ids:
                            _send_telegram_text_message(
                                assignee_chat_id,
                                "\n".join(lines),
                                parse_mode='HTML',
                                reply_markup=_build_task_notification_reply_markup(task_link)
                            )
                except Exception as materialize_error:
                    logging.warning("Failed to materialize due regulation tasks: %s", materialize_error)

                query_text = (request.args.get('q') or request.args.get('search') or '').strip()
                status_filter = (request.args.get('status') or '').strip().lower() or None
                tag_filter = (request.args.get('tag') or '').strip().lower() or None
                priority_filter = (request.args.get('priority') or '').strip().lower() or None
                only_my_raw = (request.args.get('only_my') or '').strip().lower()
                only_my = only_my_raw in {'1', 'true', 'yes', 'y', 'on'}
                person_scope = (request.args.get('person_scope') or '').strip().lower() or None
                backlog_filter = (request.args.get('backlog') or '').strip().lower() or None
                mine_filter = (request.args.get('mine') or '').strip().lower() or None
                task_id_filter = (request.args.get('task_id') or '').strip() or None
                sort_order = (request.args.get('sort') or '').strip().lower() or None
                # Отдел задачи = отдел её постановщика; 'none' — постановщики без отдела.
                department_filter = (request.args.get('department_id') or '').strip().lower() or None
                # summary=0 — не считать сводку: колонки доски просят её один раз на загрузку.
                include_summary = (request.args.get('summary') or '').strip().lower() not in {'0', 'false', 'no'}
                person_id_raw = request.args.get('person_id')
                person_id = None
                if person_id_raw is not None and str(person_id_raw).strip() != '':
                    try:
                        person_id = int(person_id_raw)
                    except Exception:
                        return jsonify({"error": "Invalid person_id param"}), 400

                raw_limit = request.args.get('limit')
                raw_offset = request.args.get('offset')
                limit = None
                offset = 0

                if raw_limit is not None and str(raw_limit).strip() != '':
                    try:
                        limit = int(raw_limit)
                    except Exception:
                        return jsonify({"error": "Invalid limit param"}), 400
                    if limit < 1 or limit > TASKS_MAX_PAGE_SIZE:
                        return jsonify({"error": f"limit must be in range 1..{TASKS_MAX_PAGE_SIZE}"}), 400

                if raw_offset is not None and str(raw_offset).strip() != '':
                    try:
                        offset = int(raw_offset)
                    except Exception:
                        return jsonify({"error": "Invalid offset param"}), 400
                    if offset < 0:
                        return jsonify({"error": "offset must be >= 0"}), 400

                try:
                    payload = db.get_tasks_for_requester(
                        requester_id=requester_id,
                        requester_role=requester_role,
                        search=query_text,
                        status=status_filter,
                        tag=tag_filter,
                        priority=priority_filter,
                        limit=limit,
                        offset=offset,
                        only_my=only_my,
                        person_id=person_id,
                        person_scope=person_scope,
                        backlog=backlog_filter,
                        mine=mine_filter,
                        task_id=task_id_filter,
                        sort=sort_order,
                        department_id=department_filter,
                        include_summary=include_summary
                    )
                except ValueError as e:
                    error_code = str(e)
                    if error_code == 'INVALID_TASK_STATUS_FILTER':
                        return jsonify({"error": "Invalid status filter"}), 400
                    if error_code == 'INVALID_TASK_TAG_FILTER':
                        return jsonify({"error": "Invalid tag filter"}), 400
                    if error_code == 'INVALID_TASK_PRIORITY_FILTER':
                        return jsonify({"error": "Invalid priority filter"}), 400
                    if error_code == 'INVALID_TASK_LIMIT':
                        return jsonify({"error": "Invalid limit value"}), 400
                    if error_code == 'INVALID_TASK_OFFSET':
                        return jsonify({"error": "Invalid offset value"}), 400
                    if error_code == 'INVALID_TASK_PERSON_ID_FILTER':
                        return jsonify({"error": "Invalid person_id filter"}), 400
                    if error_code == 'INVALID_TASK_PERSON_SCOPE_FILTER':
                        return jsonify({"error": "Invalid person_scope filter"}), 400
                    if error_code == 'INVALID_TASK_BACKLOG_FILTER':
                        return jsonify({"error": "Invalid backlog filter (use only|exclude)"}), 400
                    if error_code == 'INVALID_TASK_MINE_FILTER':
                        return jsonify({"error": "Invalid mine filter (use any|assignee|creator)"}), 400
                    if error_code == 'INVALID_TASK_ID_FILTER':
                        return jsonify({"error": "Invalid task_id filter"}), 400
                    if error_code == 'INVALID_TASK_SORT':
                        return jsonify({"error": "Invalid sort (use freshness|importance)"}), 400
                    if error_code == 'INVALID_TASK_DEPARTMENT_FILTER':
                        return jsonify({"error": "Invalid department_id filter"}), 400
                    raise

                tasks = payload.get("tasks") or []
                for task in tasks:
                    # Первый исполнитель и весь состав: у карточки на доске лицо одно,
                    # в списке — стопка. Подписанные ссылки кешируются по (бакет, путь),
                    # поэтому десять исполнителей на страницу не стоят десяти подписей.
                    assignees = task.get("assignees")
                    if isinstance(assignees, list):
                        for person in assignees:
                            if not isinstance(person, dict):
                                continue
                            person["avatar_url"] = _build_avatar_signed_url(
                                person.get("avatar_bucket"),
                                person.get("avatar_blob_path")
                            )
                            person.pop("avatar_bucket", None)
                            person.pop("avatar_blob_path", None)

                    assignee = task.get("assignee")
                    if isinstance(assignee, dict):
                        assignee["avatar_url"] = _build_avatar_signed_url(
                            assignee.get("avatar_bucket"),
                            assignee.get("avatar_blob_path")
                        )
                        assignee.pop("avatar_bucket", None)
                        assignee.pop("avatar_blob_path", None)

                    creator = task.get("creator")
                    if isinstance(creator, dict):
                        creator["avatar_url"] = _build_avatar_signed_url(
                            creator.get("avatar_bucket"),
                            creator.get("avatar_blob_path")
                        )
                        creator.pop("avatar_bucket", None)
                        creator.pop("avatar_blob_path", None)

                total_filtered = int(payload.get("total_filtered", len(tasks)) or 0)
                # Без сводки общего числа мы не считали — отдаём отфильтрованное, а не длину страницы.
                total_all = int(payload.get("total_all") or 0) or total_filtered
                returned = len(tasks)
                # Потолок строк действует и без limit, поэтому «есть ещё» считаем всегда.
                has_more = (offset + returned) < total_filtered

                return jsonify({
                    "status": "success",
                    "tasks": tasks,
                    "summary": payload.get("summary") or {},
                    "totals": {
                        "all": total_all,
                        "filtered": total_filtered
                    },
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "returned": returned,
                        "has_more": has_more
                    },
                    "query": query_text,
                    "filters": {
                        "status": status_filter,
                        "tag": tag_filter,
                        "priority": priority_filter,
                        "only_my": only_my,
                        "person_id": person_id,
                        "person_scope": person_scope,
                        "backlog": backlog_filter,
                        "mine": mine_filter,
                        "sort": sort_order,
                        "department_id": department_filter
                    }
                }), 200

            subject = (request.form.get('subject') or '').strip()
            description = (request.form.get('description') or '').strip()
            tag = (request.form.get('tag') or 'task').strip().lower() or 'task'
            priority = (request.form.get('priority') or 'normal').strip().lower() or 'normal'
            recurrence_type = (request.form.get('recurrence_type') or '').strip().lower() or None
            try:
                recurrence_interval = _parse_task_int(request.form.get('recurrence_interval'), 'recurrence_interval', default=1, minimum=1, maximum=365)
                is_regulation = _parse_task_bool(request.form.get('is_regulation')) or bool(recurrence_type)
                deadline_minutes = _parse_task_deadline_minutes(request.form)
                checklist_items = _parse_task_checklist_items(request.form.get('checklist_items'))
                is_backlog = _parse_task_bool(request.form.get('is_backlog'))
                estimate_minutes = _parse_task_estimate_minutes(request.form)
            except ValueError as parse_error:
                return jsonify({"error": str(parse_error)}), 400
            due_at_raw = (request.form.get('due_at') or '').strip() or None
            planned_start_raw = (request.form.get('planned_start_at') or '').strip() or None
            requested_by_id_raw = (request.form.get('requested_by_id') or '').strip() or None
            requested_by_name_raw = (request.form.get('requested_by_name') or '').strip() or None
            reminder_raw = (request.form.get('reminder_minutes_before') or '').strip() or None
            # Бриф продакшна — JSON-строкой: форма multipart, вложенный объект в
            # ней иначе не передать. Разбирает и проверяет его task_production.
            import task_production
            production_brief_raw = (request.form.get('production_brief') or '').strip() or None
            assigned_to_raw = request.form.get('assigned_to')
            # Состав приезжает строкой «12,15,7»: multipart-поле одно, а порядок в
            # нём значим — первый становится tasks.assigned_to.
            assignee_ids_raw = (request.form.get('assignee_ids') or '').strip()

            if not subject:
                return jsonify({"error": "subject is required"}), 400
            if tag not in TASK_ALLOWED_TAGS:
                return jsonify({"error": "Invalid tag"}), 400
            if priority not in TASK_ALLOWED_PRIORITIES:
                return jsonify({"error": "Invalid priority"}), 400
            if recurrence_type and recurrence_type not in TASK_ALLOWED_RECURRENCE_TYPES:
                return jsonify({"error": "Invalid recurrence_type"}), 400
            if not assigned_to_raw and not assignee_ids_raw:
                return jsonify({"error": "assigned_to is required"}), 400

            try:
                assignee_ids = _parse_task_assignee_ids(assignee_ids_raw or assigned_to_raw)
            except ValueError as assignee_error:
                return jsonify({"error": str(assignee_error)}), 400
            assigned_to = assignee_ids[0]

            forbidden = _task_assignees_outside_scope(assignee_ids, requester_id, requester_role)
            if forbidden:
                return jsonify({"error": "You cannot assign a task to this user"}), 403

            files = request.files.getlist('files')
            try:
                attachments, uploaded_blob_paths, gcs_bucket = _upload_task_attachments_to_gcs(files, stage='initial')
            except ValueError as upload_error:
                return jsonify({"error": str(upload_error)}), 400
            except RuntimeError as upload_error:
                return jsonify({"error": str(upload_error)}), 500
            except Exception as upload_error:
                logging.error(f"Task attachment upload failed: {upload_error}")
                return jsonify({"error": "Failed to upload task attachments"}), 500

            try:
                created = db.create_task(
                    assignee_ids=assignee_ids,
                    subject=subject,
                    description=description,
                    tag=tag,
                    assigned_to=assigned_to,
                    created_by=requester_id,
                    attachments=attachments,
                    priority=priority,
                    deadline_minutes=deadline_minutes,
                    is_regulation=is_regulation,
                    recurrence_type=recurrence_type,
                    recurrence_interval=recurrence_interval,
                    checklist_items=checklist_items,
                    is_backlog=is_backlog,
                    estimate_minutes=estimate_minutes,
                    planned_start_at=planned_start_raw,
                    due_at=due_at_raw,
                    requested_by_id=requested_by_id_raw,
                    requested_by_name=requested_by_name_raw,
                    reminder_minutes_before=reminder_raw,
                    production_brief=production_brief_raw
                )
            except ValueError as create_error:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                code = str(create_error)
                production_error = task_production.error_message(code)
                if production_error:
                    return jsonify({"error": production_error, "code": code}), 400
                error_map = {
                    "SUBJECT_REQUIRED": "subject is required",
                    "INVALID_TAG": "Invalid tag",
                    "INVALID_PRIORITY": "Invalid priority",
                    "INVALID_DEADLINE": "Invalid deadline",
                    "INVALID_RECURRENCE_TYPE": "Invalid recurrence_type",
                    "INVALID_RECURRENCE_INTERVAL": "Invalid recurrence_interval",
                    "INVALID_CHECKLIST": "Invalid checklist",
                    "INVALID_ESTIMATE": "Invalid estimate_minutes",
                    "INVALID_PLANNED_START": "Invalid planned_start_at",
                    "INVALID_REQUESTED_BY": "Invalid requested_by_id",
                    "INVALID_REMINDER": "Напоминание можно поставить максимум за сутки до дедлайна",
                    "INVALID_ASSIGNED_TO": "Invalid assigned_to value",
                    "ASSIGNEE_REQUIRED": "Выберите хотя бы одного исполнителя",
                    "TOO_MANY_ASSIGNEES": f"Исполнителей у задачи не больше {db.TASK_MAX_ASSIGNEES}"
                }
                return jsonify({"error": error_map.get(code, code)}), 400
            except Exception:
                # DB write failed after upload -> cleanup uploaded blobs
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                raise

            telegram_warning = None
            try:
                # Один запрос на всех исполнителей вместо N вызовов get_user.
                assignee_chat_ids = _task_assignee_chat_ids(created.get('assignee_ids') or assignee_ids)
                # Карточка в бэклоге ещё не обязательство исполнителя — уведомляем только при выносе на доску.
                if assignee_chat_ids and not is_backlog:
                    task_link = _build_current_task_deep_link(created.get("id"))
                    tag_label = TASK_TAG_LABELS.get(tag, 'Задача')
                    priority_label = TASK_PRIORITY_LABELS.get(priority, 'Обычная')
                    due_label = _format_task_due_for_notification(created.get('due_at'))
                    requester_name = requester[2] if requester else 'Система'
                    task_subject_html = _escape_telegram_html(subject, 220)
                    if task_link:
                        task_subject_html = (
                            f'<a href="{_escape_telegram_html(task_link, 500)}">'
                            f'{task_subject_html}'
                            '</a>'
                        )
                    message_lines = [
                        "<b>🆕 Новая задача</b>",
                        "",
                        f"<b>Тип:</b> {_escape_telegram_html(tag_label, 60)}",
                        f"<b>Срочность:</b> {_escape_telegram_html(priority_label, 60)}",
                        f"<b>Тема:</b> {task_subject_html}",
                        f"<b>От:</b> {_escape_telegram_html(requester_name, 80)}",
                        "",
                        "<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>"
                    ]
                    if due_label:
                        message_lines.insert(4, f"<b>Дедлайн:</b> {_escape_telegram_html(due_label, 80)}")
                    production_line = _task_production_item_line(created.get('production_brief'))
                    if production_line:
                        message_lines.insert(3, production_line)
                    message = "\n".join(message_lines)
                    reply_markup = _build_task_notification_reply_markup(task_link)
                    failures = []
                    for assignee_chat_id in assignee_chat_ids:
                        tg_response = _send_telegram_text_message(
                            assignee_chat_id,
                            message,
                            parse_mode='HTML',
                            reply_markup=reply_markup
                        )
                        if tg_response.status_code != 200:
                            error_detail = _get_telegram_error_text(tg_response)
                            failures.append(error_detail)
                            logging.error(f"Task Telegram API error: {error_detail}")
                    if failures:
                        telegram_warning = (
                            "Task created, but Telegram notification failed: "
                            + " | ".join(failures[:3])
                        )
            except Exception as notify_error:
                telegram_warning = f"Task created, but Telegram notification failed: {str(notify_error)}"
                logging.error(f"Task Telegram notification error: {_telegram_exception_text(notify_error)}")

            response_payload = {
                "status": "success",
                "message": "Задача добавлена в бэклог" if is_backlog else "Task created successfully",
                "task_id": created.get("id"),
                "is_backlog": bool(is_backlog),
                "due_at": created.get("due_at")
            }
            if telegram_warning:
                response_payload["warning"] = telegram_warning

            return jsonify(response_payload), 201

        except Exception as e:
            logging.error(f"Error in handle_tasks: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def handle_single_task(task_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])
            requester_role_norm = _normalize_user_role(requester_role)

            if request.method == 'PATCH':
                import task_production
                data = request.get_json() or {}
                has_subject = 'subject' in data
                has_description = 'description' in data
                has_tag = 'tag' in data
                has_assigned_to = 'assigned_to' in data or 'assignee_ids' in data
                has_priority = 'priority' in data
                has_deadline = any(key in data for key in ('deadline_days', 'deadline_hours', 'deadline_minutes'))
                has_due_at = 'due_at' in data
                has_is_regulation = 'is_regulation' in data
                has_recurrence_type = 'recurrence_type' in data
                has_recurrence_interval = 'recurrence_interval' in data
                has_checklist = 'checklist_items' in data
                has_estimate = any(key in data for key in ('estimate_minutes', 'estimate_hours', 'estimate_extra_minutes'))
                has_planned_start = 'planned_start_at' in data
                has_origin = 'requested_by_id' in data or 'requested_by_name' in data
                has_reminder = 'reminder_minutes_before' in data
                has_production_brief = 'production_brief' in data

                if not any([
                    has_subject, has_description, has_tag, has_assigned_to,
                    has_priority, has_deadline, has_due_at, has_is_regulation,
                    has_recurrence_type, has_recurrence_interval, has_checklist,
                    has_estimate, has_planned_start, has_origin, has_reminder,
                    has_production_brief
                ]):
                    return jsonify({"error": "No fields to update"}), 400

                subject = data.get('subject') if has_subject else None
                description = data.get('description') if has_description else None
                tag = data.get('tag') if has_tag else None
                priority = (str(data.get('priority') or '').strip().lower() or 'normal') if has_priority else None
                assigned_to = None

                if has_priority and priority not in TASK_ALLOWED_PRIORITIES:
                    return jsonify({"error": "Invalid priority"}), 400

                try:
                    deadline_minutes = _parse_task_deadline_minutes(data) if has_deadline else None
                    recurrence_type = (str(data.get('recurrence_type') or '').strip().lower() or None) if has_recurrence_type else None
                    recurrence_interval = (
                        _parse_task_int(data.get('recurrence_interval'), 'recurrence_interval', default=1, minimum=1, maximum=365)
                        if has_recurrence_interval else None
                    )
                    is_regulation = _parse_task_bool(data.get('is_regulation')) if has_is_regulation else None
                    checklist_items = _parse_task_checklist_items(data.get('checklist_items')) if has_checklist else None
                    estimate_minutes = _parse_task_estimate_minutes(data) if has_estimate else None
                except ValueError as parse_error:
                    return jsonify({"error": str(parse_error)}), 400

                if has_recurrence_type and recurrence_type and recurrence_type not in TASK_ALLOWED_RECURRENCE_TYPES:
                    return jsonify({"error": "Invalid recurrence_type"}), 400

                assignee_ids = None
                if has_assigned_to:
                    raw_assignees = data.get('assignee_ids') if 'assignee_ids' in data else data.get('assigned_to')
                    try:
                        assignee_ids = _parse_task_assignee_ids(raw_assignees)
                    except ValueError as assignee_error:
                        return jsonify({"error": str(assignee_error)}), 400
                    assigned_to = assignee_ids[0]

                    forbidden = _task_assignees_outside_scope(assignee_ids, requester_id, requester_role)
                    if forbidden:
                        return jsonify({"error": "You cannot assign a task to this user"}), 403

                try:
                    edit_kwargs = {
                        "task_id": task_id,
                        "requester_id": requester_id,
                        "requester_role": requester_role,
                        "subject": subject if has_subject else None,
                        "description": description if has_description else None,
                        "tag": tag if has_tag else None,
                        "assigned_to": assigned_to if has_assigned_to else None
                    }
                    if has_assigned_to:
                        edit_kwargs["assignee_ids"] = assignee_ids
                    if has_priority:
                        edit_kwargs["priority"] = priority
                    if has_deadline:
                        edit_kwargs["deadline_minutes"] = deadline_minutes
                    if has_is_regulation:
                        edit_kwargs["is_regulation"] = is_regulation
                    if has_recurrence_type:
                        edit_kwargs["recurrence_type"] = recurrence_type
                    if has_recurrence_interval:
                        edit_kwargs["recurrence_interval"] = recurrence_interval
                    if has_checklist:
                        edit_kwargs["checklist_items"] = checklist_items
                    if has_due_at:
                        edit_kwargs["due_at"] = data.get('due_at')
                    if has_estimate:
                        edit_kwargs["estimate_minutes"] = estimate_minutes
                    if has_planned_start:
                        edit_kwargs["planned_start_at"] = data.get('planned_start_at')
                    if has_origin:
                        edit_kwargs["requested_by_id"] = data.get('requested_by_id')
                        edit_kwargs["requested_by_name"] = data.get('requested_by_name')
                    if has_reminder:
                        edit_kwargs["reminder_minutes_before"] = data.get('reminder_minutes_before')
                    if has_production_brief:
                        edit_kwargs["production_brief"] = data.get('production_brief')

                    result = db.edit_task(**edit_kwargs)
                except ValueError as value_error:
                    code = str(value_error)
                    production_error = task_production.error_message(code)
                    if production_error:
                        return jsonify({"error": production_error, "code": code}), 400
                    if code == 'TASK_NOT_FOUND':
                        return jsonify({"error": "Task not found"}), 404
                    if code == 'NOTHING_TO_UPDATE':
                        return jsonify({"error": "No fields to update"}), 400
                    if code == 'SUBJECT_REQUIRED':
                        return jsonify({"error": "subject is required"}), 400
                    if code == 'INVALID_TAG':
                        return jsonify({"error": "Invalid tag"}), 400
                    if code == 'INVALID_PRIORITY':
                        return jsonify({"error": "Invalid priority"}), 400
                    if code == 'INVALID_DEADLINE':
                        return jsonify({"error": "Invalid deadline"}), 400
                    if code == 'INVALID_RECURRENCE_TYPE':
                        return jsonify({"error": "Invalid recurrence_type"}), 400
                    if code == 'INVALID_RECURRENCE_INTERVAL':
                        return jsonify({"error": "Invalid recurrence_interval"}), 400
                    if code == 'INVALID_CHECKLIST':
                        return jsonify({"error": "Invalid checklist"}), 400
                    if code == 'INVALID_ASSIGNED_TO':
                        return jsonify({"error": "Invalid assigned_to value"}), 400
                    if code == 'ASSIGNEE_REQUIRED':
                        return jsonify({"error": "Выберите хотя бы одного исполнителя"}), 400
                    if code == 'TOO_MANY_ASSIGNEES':
                        return jsonify({
                            "error": f"Исполнителей у задачи не больше {db.TASK_MAX_ASSIGNEES}"
                        }), 400
                    if code == 'INVALID_ESTIMATE':
                        return jsonify({"error": "Invalid estimate_minutes"}), 400
                    if code == 'INVALID_PLANNED_START':
                        return jsonify({"error": "Invalid planned_start_at"}), 400
                    if code == 'INVALID_REQUESTED_BY':
                        return jsonify({"error": "Invalid requested_by_id"}), 400
                    if code == 'INVALID_REMINDER':
                        return jsonify({"error": "Напоминание можно поставить максимум за сутки до дедлайна"}), 400
                    return jsonify({"error": code}), 400
                except PermissionError as permission_error:
                    code = str(permission_error)
                    if code == 'ONLY_CREATOR_CAN_EDIT':
                        return jsonify({"error": "Only task creator can edit this task"}), 403
                    if code == 'TASK_FORBIDDEN':
                        return jsonify({"error": "You do not have access to this task"}), 403
                    return jsonify({"error": code}), 403

                telegram_warnings = []
                try:
                    if result.get('updated'):
                        task_ctx = _fetch_task_notification_context(task_id)
                        if task_ctx:
                            actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
                            task_link = _build_current_task_deep_link(task_ctx.get('id'))
                            reply_markup = _build_task_notification_reply_markup(task_link)
                            recipients = _collect_task_assignee_recipients(task_ctx, requester_id)
                            for recipient in recipients:
                                message_html = _build_task_event_notification_html(
                                    event='edited',
                                    task_ctx=task_ctx,
                                    actor_name=actor_name,
                                    changed_fields=result.get('changed_fields') or [],
                                    task_link=task_link
                                )
                                response = _send_telegram_text_message(
                                    recipient.get('chat_id'),
                                    message_html,
                                    parse_mode='HTML',
                                    reply_markup=reply_markup
                                )
                                if response.status_code != 200:
                                    recipient_name = recipient.get('name') or 'исполнитель'
                                    telegram_warnings.append(
                                        f"Не удалось отправить уведомление ({recipient_name}): {_get_telegram_error_text(response)}"
                                    )
                except Exception as notify_error:
                    telegram_warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

                response_payload = {
                    "status": "success",
                    "message": "Task updated successfully" if result.get('updated') else "No changes applied",
                    "task": result
                }
                if telegram_warnings:
                    response_payload["warning"] = _truncate_for_telegram(" | ".join(telegram_warnings), 1000)
                return jsonify(response_payload), 200

            # DELETE
            if requester_role_norm != 'super_admin':
                return jsonify({"error": "Only super_admin can delete tasks"}), 403

            task_ctx_before_delete = _fetch_task_notification_context(task_id)

            try:
                delete_result = db.delete_task(
                    task_id=task_id,
                    requester_id=requester_id,
                    requester_role=requester_role
                )
            except ValueError as value_error:
                code = str(value_error)
                if code == 'TASK_NOT_FOUND':
                    return jsonify({"error": "Task not found"}), 404
                return jsonify({"error": code}), 400
            except PermissionError as permission_error:
                code = str(permission_error)
                if code == 'ONLY_SUPER_ADMIN':
                    return jsonify({"error": "Only super_admin can delete tasks"}), 403
                if code == 'TASK_FORBIDDEN':
                    return jsonify({"error": "You do not have access to this task"}), 403
                return jsonify({"error": code}), 403

            warnings = []
            warnings.extend(_delete_task_attachment_blobs_from_gcs(delete_result.get('attachments') or []))
            _delete_production_material_blobs(delete_result.get('production_blobs') or [])

            try:
                if task_ctx_before_delete:
                    actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
                    recipients = _collect_task_assignee_recipients(task_ctx_before_delete, requester_id)
                    for recipient in recipients:
                        message_html = _build_task_event_notification_html(
                            event='deleted',
                            task_ctx=task_ctx_before_delete,
                            actor_name=actor_name,
                            changed_fields=[]
                        )
                        response = _send_telegram_text_message(
                            recipient.get('chat_id'),
                            message_html,
                            parse_mode='HTML'
                        )
                        if response.status_code != 200:
                            recipient_name = recipient.get('name') or 'исполнитель'
                            warnings.append(
                                f"Не удалось отправить уведомление ({recipient_name}): {_get_telegram_error_text(response)}"
                            )
            except Exception as notify_error:
                warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

            response_payload = {
                "status": "success",
                "message": "Task deleted successfully",
                "task_id": task_id
            }
            if warnings:
                response_payload["warning"] = _truncate_for_telegram(" | ".join(warnings), 1000)
            return jsonify(response_payload), 200

        except Exception as e:
            logging.error(f"Error in handle_single_task: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/board', methods=['POST', 'OPTIONS'])
    @require_api_key
    def handle_tasks_board():
        """
    Планирующие правки с доски: перенос бэклог ↔ доска, порядок приоритезации,
    оценка длительности и сроки. Батч — чтобы drag&drop переставлял соседей одним запросом.

    Тело: {"items": [{"task_id": 12, "is_backlog": false, "backlog_rank": 2.5,
                      "estimate_minutes": 120, "planned_start_at": "...", "due_at": "..."}]}
    Допускается и одиночный объект без обёртки items.
    """
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])
            data = request.get_json(silent=True) or {}
            raw_items = data.get('items')
            if raw_items is None:
                raw_items = [data] if data.get('task_id') is not None else []
            if not isinstance(raw_items, list) or not raw_items:
                return jsonify({"error": "items is required"}), 400
            if len(raw_items) > 200:
                return jsonify({"error": "Too many items (max 200)"}), 400

            error_map = {
                'TASK_NOT_FOUND': ("Task not found", 404),
                'NOTHING_TO_UPDATE': ("No fields to update", 400),
                'INVALID_ESTIMATE': ("Invalid estimate_minutes", 400),
                'INVALID_PLANNED_START': ("Invalid planned_start_at", 400),
                'INVALID_DEADLINE': ("Invalid due_at", 400),
                'INVALID_BACKLOG_RANK': ("Invalid backlog_rank", 400),
                'INVALID_REMINDER': ("Напоминание можно поставить максимум за сутки до дедлайна", 400),
                'BACKLOG_ONLY_FOR_ASSIGNED': ("Только не начатую задачу можно вернуть в бэклог", 409),
                'TASK_FORBIDDEN': ("You do not have access to this task", 403),
                'ONLY_CREATOR_CAN_PLAN': ("Сроки задачи меняет постановщик или админ", 403)
            }

            results = []
            promoted = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    return jsonify({"error": "Each item must be an object"}), 400
                try:
                    item_task_id = int(raw_item.get('task_id'))
                except Exception:
                    return jsonify({"error": "task_id must be an integer"}), 400

                kwargs = {
                    "task_id": item_task_id,
                    "requester_id": requester_id,
                    "requester_role": requester_role
                }
                if 'is_backlog' in raw_item:
                    kwargs["is_backlog"] = _parse_task_bool(raw_item.get('is_backlog'))
                if 'backlog_rank' in raw_item:
                    kwargs["backlog_rank"] = raw_item.get('backlog_rank')
                if any(key in raw_item for key in ('estimate_minutes', 'estimate_hours', 'estimate_extra_minutes')):
                    try:
                        kwargs["estimate_minutes"] = _parse_task_estimate_minutes(raw_item)
                    except ValueError as parse_error:
                        return jsonify({"error": str(parse_error)}), 400
                if 'planned_start_at' in raw_item:
                    kwargs["planned_start_at"] = raw_item.get('planned_start_at')
                if 'due_at' in raw_item:
                    kwargs["due_at"] = raw_item.get('due_at')
                if 'reminder_minutes_before' in raw_item:
                    kwargs["reminder_minutes_before"] = raw_item.get('reminder_minutes_before')

                try:
                    result = db.update_task_board_state(**kwargs)
                except ValueError as value_error:
                    message, code = error_map.get(str(value_error), (str(value_error), 400))
                    return jsonify({"error": message, "task_id": item_task_id}), code
                except PermissionError as permission_error:
                    message, code = error_map.get(str(permission_error), (str(permission_error), 403))
                    return jsonify({"error": message, "task_id": item_task_id}), code

                results.append(result)
                if result.get('updated') and result.get('was_backlog') and not result.get('is_backlog'):
                    promoted.append(result)

            warnings = []
            actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
            for result in promoted:
                try:
                    task_ctx = _fetch_task_notification_context(result.get('task_id'))
                    if not task_ctx:
                        continue
                    task_link = _build_current_task_deep_link(task_ctx.get('id'))
                    reply_markup = _build_task_notification_reply_markup(task_link)
                    for recipient in _collect_task_assignee_recipients(task_ctx, requester_id):
                        response = _send_telegram_text_message(
                            recipient.get('chat_id'),
                            _build_task_event_notification_html(
                                event='promoted',
                                task_ctx=task_ctx,
                                actor_name=actor_name,
                                task_link=task_link
                            ),
                            parse_mode='HTML',
                            reply_markup=reply_markup
                        )
                        if response.status_code != 200:
                            recipient_name = recipient.get('name') or 'исполнитель'
                            warnings.append(
                                f"Не удалось отправить уведомление ({recipient_name}): {_get_telegram_error_text(response)}"
                            )
                except Exception as notify_error:
                    warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

            response_payload = {
                "status": "success",
                "message": "Доска обновлена" if any(item.get('updated') for item in results) else "Изменений нет",
                "items": results,
                "updated": sum(1 for item in results if item.get('updated'))
            }
            if warnings:
                response_payload["warning"] = _truncate_for_telegram(" | ".join(warnings), 1000)
            return jsonify(response_payload), 200

        except Exception as e:
            logging.error(f"Error in handle_tasks_board: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/reports', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def handle_task_reports(task_id):
        """Журнал отчётов о проделанной работе по задаче."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])

            if request.method == 'GET':
                try:
                    reports = db.get_task_reports(task_id, requester_id, requester_role)
                except (ValueError, PermissionError) as error:
                    return _task_report_error_response(error)
                return jsonify({
                    "status": "success",
                    "reports": reports,
                    "spent_minutes": sum(int(item.get('spent_minutes') or 0) for item in reports) or None
                }), 200

            data = request.get_json(silent=True) or request.form or {}
            try:
                spent_minutes = _parse_task_spent_minutes(data)
            except ValueError as parse_error:
                return jsonify({"error": str(parse_error)}), 400

            try:
                report = db.create_task_report(
                    task_id=task_id,
                    requester_id=requester_id,
                    requester_role=requester_role,
                    body=data.get('body') or data.get('report') or '',
                    spent_minutes=spent_minutes,
                    kind=data.get('kind') or 'progress'
                )
            except (ValueError, PermissionError) as error:
                return _task_report_error_response(error)

            warnings = []
            try:
                task_ctx = _fetch_task_notification_context(task_id)
                if task_ctx:
                    actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
                    task_link = _build_current_task_deep_link(task_ctx.get('id'))
                    for recipient in _collect_task_notification_recipients(task_ctx, requester_id):
                        response = _send_telegram_text_message(
                            recipient.get('chat_id'),
                            _build_task_report_notification_html(
                                task_ctx=task_ctx,
                                actor_name=actor_name,
                                report=report,
                                task_link=task_link
                            ),
                            parse_mode='HTML',
                            reply_markup=_build_task_notification_reply_markup(task_link)
                        )
                        if response.status_code != 200:
                            warnings.append(
                                f"Не удалось отправить уведомление ({recipient.get('name') or 'получатель'}): "
                                f"{_get_telegram_error_text(response)}"
                            )
            except Exception as notify_error:
                warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

            payload = {"status": "success", "message": "Отчёт добавлен", "report": report}
            if warnings:
                payload["warning"] = _truncate_for_telegram(" | ".join(warnings), 1000)
            return jsonify(payload), 201

        except Exception as e:
            logging.error(f"Error in handle_task_reports: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/reports/<int:report_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def handle_task_report_item(report_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])

            if request.method == 'PATCH':
                data = request.get_json(silent=True) or {}
                kwargs = {
                    "report_id": report_id,
                    "requester_id": requester_id,
                    "requester_role": requester_role
                }
                if 'body' in data:
                    kwargs["body"] = data.get('body')
                if any(key in data for key in ('spent_minutes', 'spent_hours', 'spent_extra_minutes')):
                    try:
                        kwargs["spent_minutes"] = _parse_task_spent_minutes(data)
                    except ValueError as parse_error:
                        return jsonify({"error": str(parse_error)}), 400
                try:
                    report = db.update_task_report(**kwargs)
                except (ValueError, PermissionError) as error:
                    return _task_report_error_response(error)
                return jsonify({"status": "success", "message": "Отчёт обновлён", "report": report}), 200

            try:
                result = db.delete_task_report(report_id, requester_id, requester_role)
            except (ValueError, PermissionError) as error:
                return _task_report_error_response(error)
            return jsonify({"status": "success", "message": "Отчёт удалён", **result}), 200

        except Exception as e:
            logging.error(f"Error in handle_task_report_item: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/messages', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def handle_task_messages(task_id):
        """Лента уточнений по задаче: дополнения постановки и запросы информации."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            requester_role = getattr(g, 'effective_task_role', requester[3])

            if request.method == 'GET':
                try:
                    messages = db.get_task_messages(task_id, requester_id, requester_role)
                except (ValueError, PermissionError) as error:
                    return _task_report_error_response(error)
                return jsonify({"status": "success", "messages": messages}), 200

            content_type = (request.content_type or '').lower()
            if 'multipart/form-data' in content_type:
                source = request.form
                uploaded_files = request.files.getlist('files')
            else:
                source = request.get_json(silent=True) or {}
                uploaded_files = []

            kind = str(source.get('kind') or 'note').strip().lower()
            body = str(source.get('body') or '').strip()

            attachments = []
            uploaded_blob_paths = []
            gcs_bucket = None
            if uploaded_files:
                try:
                    attachments, uploaded_blob_paths, gcs_bucket = _upload_task_attachments_to_gcs(
                        uploaded_files, stage='clarification'
                    )
                except ValueError as upload_error:
                    return jsonify({"error": str(upload_error)}), 400
                except RuntimeError as upload_error:
                    return jsonify({"error": str(upload_error)}), 500
                except Exception as upload_error:
                    logging.error(f"Task clarification upload failed: {upload_error}")
                    return jsonify({"error": "Failed to upload attachments"}), 500

            try:
                message = db.create_task_message(
                    task_id=task_id,
                    requester_id=requester_id,
                    requester_role=requester_role,
                    kind=kind,
                    body=body,
                    attachments=attachments
                )
            except (ValueError, PermissionError) as error:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                return _task_report_error_response(error)
            except Exception:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                raise

            warnings = _notify_task_message(task_id, requester, requester_id, message)
            payload = {
                "status": "success",
                "message": TASK_MESSAGE_SAVED_LABELS.get(message.get('kind'), 'Уточнение добавлено'),
                "task_message": message
            }
            if warnings:
                payload["warning"] = _truncate_for_telegram(" | ".join(warnings), 1000)
            return jsonify(payload), 201

        except Exception as e:
            logging.error(f"Error in handle_task_messages: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/messages/<int:message_id>/withdraw', methods=['POST', 'OPTIONS'])
    @require_api_key
    def withdraw_task_info_request(message_id):
        """Снять свой запрос информации — разобрался сам, постановщика не держим."""
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            try:
                result = db.withdraw_task_info_request(
                    message_id,
                    requester_id,
                    getattr(g, 'effective_task_role', requester[3])
                )
            except (ValueError, PermissionError) as error:
                return _task_report_error_response(error)

            return jsonify({"status": "success", "message": "Запрос снят", **result}), 200

        except Exception as e:
            logging.error(f"Error in withdraw_task_info_request: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/status', methods=['POST', 'OPTIONS'])
    @require_api_key
    def update_task_status(task_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            content_type = (request.content_type or '').lower()
            is_multipart = 'multipart/form-data' in content_type

            if is_multipart:
                source = request.form
                uploaded_files = request.files.getlist('files')
            else:
                source = request.get_json() or {}
                uploaded_files = []

            action = str(source.get('action') or '').strip().lower()
            comment = str(source.get('comment') or '').strip()
            # `report` — понятный алиас: при сдаче задачи это отчёт о проделанной работе.
            completion_summary = str(source.get('completion_summary') or source.get('report') or '').strip()

            if action not in TASK_ALLOWED_ACTIONS:
                return jsonify({"error": "Invalid action"}), 400

            try:
                spent_minutes = _parse_task_spent_minutes(source)
            except ValueError as parse_error:
                return jsonify({"error": str(parse_error)}), 400

            action_attachments = []
            uploaded_blob_paths = []
            gcs_bucket = None

            actions_with_files = {'completed', 'returned', 'reopened'}
            if action in actions_with_files:
                try:
                    stage = 'result' if action == 'completed' else action
                    action_attachments, uploaded_blob_paths, gcs_bucket = _upload_task_attachments_to_gcs(
                        uploaded_files,
                        stage=stage
                    )
                except ValueError as upload_error:
                    return jsonify({"error": str(upload_error)}), 400
                except RuntimeError as upload_error:
                    return jsonify({"error": str(upload_error)}), 500
                except Exception as upload_error:
                    logging.error(f"Task attachment upload failed ({action}): {upload_error}")
                    return jsonify({"error": "Failed to upload attachments"}), 500
            elif uploaded_files:
                return jsonify({"error": "Files can be attached only when completing, returning, or reopening a task"}), 400

            try:
                result = db.update_task_status(
                    task_id=task_id,
                    requester_id=requester_id,
                    requester_role=getattr(g, 'effective_task_role', requester[3]),
                    action=action,
                    comment=comment,
                    completion_summary=completion_summary if action == 'completed' else None,
                    completion_attachments=action_attachments,
                    spent_minutes=spent_minutes if action == 'completed' else None
                )
            except ValueError as value_error:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                code = str(value_error)
                if code == 'TASK_NOT_FOUND':
                    return jsonify({"error": "Task not found"}), 404
                if code in ('INVALID_ACTION', 'INVALID_TRANSITION'):
                    return jsonify({"error": "Invalid task status transition"}), 400
                if code == 'CHECKLIST_INCOMPLETE':
                    return jsonify({"error": "Сначала завершите обязательные пункты чек-листа"}), 400
                if code == 'INVALID_SPENT_MINUTES':
                    return jsonify({"error": "Invalid spent_minutes"}), 400
                return jsonify({"error": code}), 400
            except PermissionError as permission_error:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                code = str(permission_error)
                if code in ('TASK_FORBIDDEN', 'ONLY_ASSIGNEE', 'ONLY_REVIEWER'):
                    return jsonify({"error": "You do not have permission for this action"}), 403
                return jsonify({"error": code}), 403
            except Exception:
                _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
                raise

            telegram_warnings = []
            try:
                should_notify_participants = bool(result.get('history_id'))
                if should_notify_participants:
                    task_ctx = _fetch_task_notification_context(task_id)
                    if task_ctx:
                        actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
                        task_subject = (task_ctx.get('subject') or f"Задача #{task_id}").strip()
                        task_link = _build_current_task_deep_link(task_ctx.get('id'))
                        reply_markup = _build_task_notification_reply_markup(task_link)
                        recipients = _collect_task_notification_recipients(task_ctx, requester_id)

                        for recipient in recipients:
                            message_html = _build_task_status_notification_html(
                                action=action,
                                task_ctx=task_ctx,
                                actor_name=actor_name,
                                recipient_kind=recipient.get('kind') or 'participant',
                                comment=comment,
                                completion_summary=completion_summary,
                                completion_files_count=len(action_attachments),
                                task_link=task_link,
                                spent_minutes=spent_minutes
                            )
                            response = _send_telegram_text_message(
                                recipient.get('chat_id'),
                                message_html,
                                parse_mode='HTML',
                                reply_markup=reply_markup
                            )
                            if response.status_code != 200:
                                recipient_name = recipient.get('name') or recipient.get('kind') or 'получатель'
                                telegram_warnings.append(
                                    f"Не удалось отправить уведомление ({recipient_name}): {_get_telegram_error_text(response)}"
                                )
                                continue

                            if action == 'completed':
                                telegram_warnings.extend(
                                    _send_task_completion_attachments_to_telegram(
                                        recipient.get('chat_id'),
                                        task_subject,
                                        action_attachments,
                                        task_link=task_link
                                    )
                                )
            except Exception as notify_error:
                telegram_warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

            action_messages = {
                'in_progress': 'Task moved to in progress',
                'completed': 'Task marked as completed',
                'accepted': 'Task accepted',
                'returned': 'Task returned for rework',
                'reopened': 'Task reopened'
            }
            response_payload = {
                "status": "success",
                "message": action_messages.get(action, 'Task status updated'),
                "task": result
            }
            if telegram_warnings:
                response_payload["warning"] = _truncate_for_telegram(" | ".join(telegram_warnings), 1000)
            return jsonify(response_payload), 200

        except Exception as e:
            logging.error(f"Error in update_task_status: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/checklist/<int:item_id>', methods=['PATCH', 'OPTIONS'])
    @require_api_key
    def update_task_checklist_item(task_id, item_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            data = request.get_json() or {}
            if 'is_done' not in data:
                return jsonify({"error": "is_done is required"}), 400
            is_done = _parse_task_bool(data.get('is_done'))

            update_note = 'result_note' in data
            result_note = data.get('result_note') if update_note else None
            if result_note is not None and not isinstance(result_note, str):
                result_note = str(result_note)
            if isinstance(result_note, str) and len(result_note) > 2000:
                result_note = result_note[:2000]

            try:
                item = db.update_task_checklist_item(
                    task_id=task_id,
                    checklist_item_id=item_id,
                    requester_id=requester_id,
                    requester_role=getattr(g, 'effective_task_role', requester[3]),
                    is_done=is_done,
                    result_note=result_note,
                    update_note=update_note
                )
            except ValueError as value_error:
                code = str(value_error)
                if code == 'CHECKLIST_ITEM_NOT_FOUND':
                    return jsonify({"error": "Checklist item not found"}), 404
                return jsonify({"error": code}), 400
            except PermissionError as permission_error:
                code = str(permission_error)
                if code == 'TASK_FORBIDDEN':
                    return jsonify({"error": "You do not have access to this task"}), 403
                return jsonify({"error": code}), 403

            telegram_warnings = []
            try:
                task_ctx = _fetch_task_notification_context(task_id)
                if task_ctx:
                    actor_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
                    task_link = _build_current_task_deep_link(task_id)
                    reply_markup = _build_task_notification_reply_markup(task_link)
                    recipients = _collect_task_notification_recipients(task_ctx, requester_id)
                    for recipient in recipients:
                        message_html = _build_task_checklist_notification_html(
                            task_ctx=task_ctx,
                            checklist_item=item,
                            actor_name=actor_name,
                            is_done=is_done,
                            task_link=task_link
                        )
                        response = _send_telegram_text_message(
                            recipient.get('chat_id'),
                            message_html,
                            parse_mode='HTML',
                            reply_markup=reply_markup
                        )
                        if response.status_code != 200:
                            recipient_name = recipient.get('name') or 'участник'
                            telegram_warnings.append(
                                f"Не удалось отправить уведомление ({recipient_name}): {_get_telegram_error_text(response)}"
                            )
            except Exception as notify_error:
                telegram_warnings.append(f"Ошибка отправки Telegram-уведомления: {_telegram_exception_text(notify_error)}")

            response_payload = {
                "status": "success",
                "message": "Checklist item updated",
                "item": item
            }
            if telegram_warnings:
                response_payload["warning"] = _truncate_for_telegram(" | ".join(telegram_warnings), 1000)
            return jsonify(response_payload), 200

        except Exception as e:
            logging.error(f"Error in update_task_checklist_item: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tasks/attachments/<int:attachment_id>/download', methods=['GET', 'OPTIONS'])
    @require_api_key
    def download_task_attachment(attachment_id):
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            try:
                attachment = db.get_task_attachment_for_requester(
                    attachment_id=attachment_id,
                    requester_id=requester_id,
                    requester_role=getattr(g, 'effective_task_role', requester[3])
                )
            except PermissionError:
                return jsonify({"error": "You do not have access to this attachment"}), 403

            if not attachment:
                return jsonify({"error": "Attachment not found"}), 404

            if attachment.get('storage_type') == 'gcs':
                bucket_name = (attachment.get('gcs_bucket') or '').strip()
                blob_path = (attachment.get('gcs_blob_path') or '').strip()
                if not bucket_name or not blob_path:
                    return jsonify({"error": "Attachment storage metadata is invalid"}), 500
                try:
                    gcs_client = get_gcs_client()
                    bucket = gcs_client.bucket(bucket_name)
                    blob = bucket.blob(blob_path)
                    if not blob.exists():
                        return jsonify({"error": "Attachment object not found in storage"}), 404
                    file_bytes = blob.download_as_bytes()
                except Exception as storage_error:
                    logging.error(f"Error downloading GCS task attachment: {storage_error}")
                    return jsonify({"error": "Failed to download attachment from storage"}), 500

                return send_file(
                    BytesIO(file_bytes),
                    as_attachment=True,
                    download_name=attachment['file_name'],
                    mimetype=attachment.get('content_type') or 'application/octet-stream'
                )

            return send_file(
                BytesIO(attachment['file_data']),
                as_attachment=True,
                download_name=attachment['file_name'],
                mimetype=attachment.get('content_type') or 'application/octet-stream'
            )
        except Exception as e:
            logging.error(f"Error in download_task_attachment: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/tasks/<int:task_id>/photos', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_task_photo_previews(task_id):
        """Адреса картинок задачи для плиток карточки — одним запросом на карточку.

    Подписанные ссылки, а не байты через API: <img> на другом домене не шлёт
    заголовок авторизации, а прокси означал бы по запросу на каждую плитку.
    Подробности — в шапке task_photos.
    """
        try:
            requester_id, requester, guard_response, guard_status = _task_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            import task_photos
            try:
                rows = db.list_task_photo_attachments_for_requester(
                    task_id=task_id,
                    requester_id=requester_id,
                    requester_role=getattr(g, 'effective_task_role', requester[3]),
                    content_types=task_photos.PREVIEW_TYPES,
                )
            except PermissionError:
                return jsonify({"error": "You do not have access to this task"}), 403
            if rows is None:
                return jsonify({"error": "Task not found"}), 404

            return jsonify({"photos": task_photos.sign_previews(get_gcs_client, rows)}), 200
        except Exception as e:
            logging.error(f"Error in get_task_photo_previews: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    return bp
