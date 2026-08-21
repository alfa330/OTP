#!/usr/bin/env python
"""
CLI к бэклогу/канбану раздела «Задачи» — точка подключения для агента (Claude Code)
и для ручных проверок.

Авторизация: логин/пароль из .env.codex.local (по умолчанию ADMIN_LOGIN/ADMIN_PASSWORD),
транспорт bearer. Cookies после логина сбрасываются — иначе прод отвечает
403 «Invalid request origin».

Примеры:
    python scripts/task_board.py board
    python scripts/task_board.py backlog
    python scripts/task_board.py show 412
    python scripts/task_board.py create "Проверить выгрузку Oktell" --assignee 169 --backlog --estimate 120
    python scripts/task_board.py create "Собрать отчёт" --assignee 169 --assignee 12   # несколько исполнителей
    python scripts/task_board.py create "Разобраться с дублями" --self --from 169
    python scripts/task_board.py log "Починил выгрузку" --report "что сделано" --spent 2h30m
    python scripts/task_board.py deadline 412 --due "2026-08-05 18:00" --remind 1d
    python scripts/task_board.py deadline 412 --in 3d4h --estimate 90
    python scripts/task_board.py deadline 412 --remind off
    python scripts/task_board.py promote 412
    python scripts/task_board.py park 412
    python scripts/task_board.py rank 412 --after 408
    python scripts/task_board.py status 412 in_progress --comment "взял"
    python scripts/task_board.py report 412 "Поднял индексы, переписал выборку" --spent 2h
    python scripts/task_board.py reports 412
    python scripts/task_board.py status 412 completed --report "Что сделано" --spent 3h30m
    python scripts/task_board.py recipients

Флаг --json у любой команды отдаёт сырой ответ API.
"""
import argparse
import io
import json
import mimetypes
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_API_BASE_URL = 'https://otp-2-fos4.onrender.com'

# Куда падают скачанные вложения. Вне репозитория — файлы заказчика в git не место.
DOWNLOAD_ROOT = os.path.join(tempfile.gettempdir(), 'otp_task_files')

# Пределы загрузки на сервере (bot_schedule2.py: TASK_MAX_FILES, TASK_MAX_FILE_SIZE_BYTES).
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

COLUMN_TITLES = [
    ('backlog', 'Бэклог'),
    ('todo', 'К выполнению'),
    ('progress', 'В работе'),
    ('review', 'На проверке'),
    ('done', 'Готово'),
]
STATUS_ACTIONS = ('in_progress', 'completed', 'accepted', 'returned', 'reopened')

# Файлы принимают только эти переходы (bot_schedule2.py: actions_with_files).
STATUS_ACTIONS_WITH_FILES = ('completed', 'returned', 'reopened')

STATUS_LABELS = {
    'assigned': 'поручена',
    'in_progress': 'в работе',
    'completed': 'сдана на проверку',
    'accepted': 'принята',
    'returned': 'возвращена на доработку',
    'reopened': 'переоткрыта',
    'created': 'создана',
}

TAG_LABELS = {'task': 'задача', 'problem': 'проблема', 'suggestion': 'предложение'}

PRIORITY_LABELS = {'normal': 'обычная', 'urgent': 'срочная', 'critical': 'критичная'}

# Виды «ждёт моего действия». Правила продублированы в четырёх местах —
# database.py::get_task_action_needs_summary, src/components/tasks/taskActionNeeds.js,
# notifications/sources.py::tasks и здесь. Меняешь одно — меняй все.
ACTION_KIND_LABELS = {
    'overdue': 'просрочено (я исполнитель)',
    'returned': 'вернули на доработку',
    'info': 'у меня просят информацию',
    'review': 'ждёт моей приёмки',
    'fresh': 'поручили, ещё не начал',
    'accepted': 'мою работу приняли (к сведению)',
}


def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, value = stripped.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


def filename_from_disposition(disposition):
    """Имя файла из Content-Disposition.

    Flask отдаёт сразу два имени: ASCII-огрызок в `filename=` (кириллица из него
    выброшена — «Задачи_2026-08-18.xlsx» приезжает как «_2026-08-18.xlsx») и
    настоящее в `filename*=UTF-8''…`. Читать надо второе.
    """
    text = str(disposition or '')
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", text, re.IGNORECASE)
    if match:
        return requests.utils.unquote(match.group(1).strip())
    match = re.search(r'filename\s*=\s*"?([^";]+)', text, re.IGNORECASE)
    return match.group(1).strip() if match else ''


class TaskBoardClient:
    def __init__(self, base_url=None, login=None, password=None):
        self.base_url = (base_url or os.getenv('OTP_API_BASE_URL') or DEFAULT_API_BASE_URL).rstrip('/')
        self.login = login or os.getenv('ADMIN_LOGIN')
        self.password = password or os.getenv('ADMIN_PASSWORD')
        self.session = requests.Session()
        self.user_id = None
        self.user_name = None

    def authenticate(self):
        if not self.login or not self.password:
            raise SystemExit(
                'Нет логина/пароля. Задайте ADMIN_LOGIN/ADMIN_PASSWORD в .env.codex.local '
                'или передайте --login/--password.'
            )
        response = self.session.post(
            f'{self.base_url}/api/login',
            json={'login': self.login, 'password': self.password, 'auth_transport': 'bearer'},
            timeout=60,
        )
        if response.status_code != 200:
            raise SystemExit(f'Логин не удался: {response.status_code} {response.text[:300]}')
        payload = response.json()
        token = payload.get('access_token')
        user = payload.get('user') or {}
        self.user_id = user.get('id')
        self.user_name = user.get('name')
        if not token or not self.user_id:
            raise SystemExit(f'Логин вернул неожидаемый ответ: {json.dumps(payload)[:300]}')
        # Выставленные логином cookies включают Origin-защиту — работаем только по bearer.
        self.session.cookies.clear()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'X-User-Id': str(self.user_id),
        })
        return self

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, f'{self.base_url}{path}', timeout=120, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(f'{method} {path} → {response.status_code}: {response.text[:500]}')
        if not response.content:
            return {}
        return response.json()

    def _download(self, path, params=None):
        """Бинарный ответ: возвращает (bytes, имя файла из Content-Disposition)."""
        response = self.session.get(f'{self.base_url}{path}', params=params or None, timeout=180)
        if response.status_code >= 400:
            raise SystemExit(f'GET {path} → {response.status_code}: {response.text[:500]}')
        return response.content, filename_from_disposition(response.headers.get('Content-Disposition'))

    def list_tasks(self, **params):
        clean = {key: value for key, value in params.items() if value not in (None, '')}
        return self._request('GET', '/api/tasks', params=clean)

    def get_task(self, task_id):
        """Одна задача точечным запросом.

        Фильтр task_id существует ровно для этого (database.py: «диплинк на задачу,
        которой нет в загруженной выборке»). Тянуть весь список и искать в нём —
        и лишний трафик, и промах, когда список упрётся в limit.
        """
        payload = self.list_tasks(task_id=int(task_id), summary='0')
        tasks = payload.get('tasks') or []
        return tasks[0] if tasks else None

    def recipients(self):
        return self._request('GET', '/api/tasks/recipients')

    def board_people(self):
        return self._request('GET', '/api/tasks/board_people')

    def departments(self):
        return self._request('GET', '/api/tasks/departments')

    def action_required(self):
        return self._request('GET', '/api/tasks/action_required')

    def mark_action_seen(self, task_id, kind=None):
        payload = {'kind': kind} if kind else {}
        return self._request('POST', f'/api/tasks/{int(task_id)}/action_seen', json=payload)

    def set_checklist_item(self, task_id, item_id, is_done, result_note=None):
        payload = {'is_done': bool(is_done)}
        if result_note is not None:
            payload['result_note'] = result_note
        return self._request('PATCH', f'/api/tasks/{int(task_id)}/checklist/{int(item_id)}', json=payload)

    def download_attachment(self, attachment_id):
        return self._download(f'/api/tasks/attachments/{int(attachment_id)}/download')

    def export_tasks(self, **filters):
        clean = {key: value for key, value in filters.items() if value not in (None, '')}
        return self._download('/api/tasks/export', params=clean)

    def list_notes(self):
        return self._request('GET', '/api/tasks/notes')

    def create_note(self, payload):
        return self._request('POST', '/api/tasks/notes', json=payload)

    def update_note(self, note_id, payload):
        return self._request('PATCH', f'/api/tasks/notes/{int(note_id)}', json=payload)

    def delete_note(self, note_id):
        return self._request('DELETE', f'/api/tasks/notes/{int(note_id)}')

    def create_task(self, fields, files=None):
        # Файлы уходят полем `files` (request.files.getlist('files')); остальные поля
        # при этом обязаны ехать той же multipart-формой, а не JSON.
        if files:
            return self._request('POST', '/api/tasks', data=fields, files=files)
        return self._request('POST', '/api/tasks', data=fields)

    def patch_task(self, task_id, payload):
        return self._request('PATCH', f'/api/tasks/{int(task_id)}', json=payload)

    def board_update(self, items):
        return self._request('POST', '/api/tasks/board', json={'items': items})

    def set_status(self, task_id, action, comment='', completion_summary='', spent_minutes=None, files=None):
        payload = {'action': action, 'comment': comment}
        if completion_summary:
            payload['completion_summary'] = completion_summary
        if spent_minutes:
            payload['spent_minutes'] = int(spent_minutes)
        path = f'/api/tasks/{int(task_id)}/status'
        if files:
            # Роут отличает multipart от JSON по Content-Type и только тогда читает файлы.
            form = {key: str(value) for key, value in payload.items()}
            return self._request('POST', path, data=form, files=files)
        return self._request('POST', path, json=payload)

    def list_reports(self, task_id):
        return self._request('GET', f'/api/tasks/{int(task_id)}/reports')

    def add_report(self, task_id, body, spent_minutes=None, kind='progress'):
        payload = {'body': body, 'kind': kind}
        if spent_minutes:
            payload['spent_minutes'] = int(spent_minutes)
        return self._request('POST', f'/api/tasks/{int(task_id)}/reports', json=payload)

    def delete_report(self, report_id):
        return self._request('DELETE', f'/api/tasks/reports/{int(report_id)}')

    def list_messages(self, task_id):
        return self._request('GET', f'/api/tasks/{int(task_id)}/messages')

    def add_message(self, task_id, kind, body, files=None):
        path = f'/api/tasks/{int(task_id)}/messages'
        payload = {'kind': kind, 'body': body}
        if files:
            # Тот же контракт, что у создания задачи: есть файлы — вся форма multipart.
            return self._request('POST', path, data=payload, files=files)
        return self._request('POST', path, json=payload)

    def withdraw_info_request(self, message_id):
        return self._request('POST', f'/api/tasks/messages/{int(message_id)}/withdraw')


# ─────────────── Вычисления над задачами ───────────────

def column_of(task):
    if task.get('is_backlog'):
        return 'backlog'
    status = task.get('status')
    if status in ('in_progress', 'returned'):
        return 'progress'
    if status == 'completed':
        return 'review'
    if status == 'accepted':
        return 'done'
    return 'todo'


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return None


def accepted_at(task):
    """Момент приёмки: по нему сортируется «Готово» — свежепринятое сверху."""
    for item in reversed(task.get('history') or []):
        if item.get('status_code') == 'accepted':
            parsed = parse_iso(item.get('changed_at'))
            if parsed:
                return parsed
    return parse_iso(task.get('completed_at')) or parse_iso(task.get('updated_at'))


def review_authority_id(task):
    """Приёмку закрывает поручитель, а если его нет — постановщик."""
    return (int((task.get('requested_by') or {}).get('id') or 0)
            or int((task.get('creator') or {}).get('id') or 0))


def task_action_need(task, user_id, now=None):
    """Причина, по которой задача ждёт лично этого человека, либо None.

    Правила портированы из src/components/tasks/taskActionNeeds.js — того же
    кода, что рисует панель раздела. Те же правила лежат в SQL дважды:
    database.py::get_task_action_needs_summary и notifications/sources.py::tasks.
    Меняешь правило — меняй во всех четырёх местах.

    Причины взаимоисключающие: у задачи ровно одна, самая срочная.
    """
    person_id = int(user_id or 0)
    if not person_id or not task:
        return None
    now = now or datetime.now()
    status = str(task.get('status') or '').lower()
    is_assignee = is_task_assignee(task, person_id)
    due_at = parse_iso(task.get('due_at'))

    if status == 'completed' and review_authority_id(task) == person_id:
        return 'review'
    # Исполнителю не хватает информации, отвечать мне. Раньше ветки «я
    # исполнитель»: спрашивающий и отвечающий — разные люди, и бэклог тут
    # причину не отменяет.
    # Отсекаем автора вопроса, а не весь состав: постановщик может сам быть
    # одним из исполнителей, и тогда вопрос коллеги от него прятался.
    if (task.get('info_request')
            and status in ('assigned', 'in_progress', 'returned')
            and review_authority_id(task) == person_id
            and int((task.get('info_request') or {}).get('author_id') or 0) != person_id):
        return 'info'
    # Раньше проверок бэклога и живых статусов: принятая задача из работы вышла,
    # но исполнителю о приёмке сказать надо. Кроме случая, когда принимал он сам.
    if status == 'accepted' and is_assignee and review_authority_id(task) != person_id:
        return 'accepted'
    if not is_assignee or task.get('is_backlog'):
        return None
    if status not in ('assigned', 'in_progress', 'returned'):
        return None
    if due_at and due_at < now:
        return 'overdue'
    if status == 'returned':
        return 'returned'
    if status == 'assigned':
        return 'fresh'
    return None


# «Принята» — единственная терминальная причина: задача уже не сдвинется, поэтому
# отметка о просмотре у неё вечная, а просмотренную её из списка убирают совсем.
TERMINAL_ACTION_KINDS = ('accepted',)


def is_action_need_seen(task, kind):
    """Погашено ли уведомление. Серверная отметка — объект {kind, seen_at}.

    Правка задачи сдвигает updated_at и отметку сжигает: причина живая, посмотри
    заново. У терминальной причины отметка не сгорает никогда.
    """
    seen = task.get('action_seen') or {}
    if seen.get('kind') != kind or not seen.get('seen_at'):
        return False
    if kind in TERMINAL_ACTION_KINDS:
        return True
    seen_at = parse_iso(seen.get('seen_at'))
    if not seen_at:
        return False
    updated_at = parse_iso(task.get('updated_at'))
    return updated_at is None or seen_at >= updated_at


def action_needs_by_kind(tasks, user_id, now=None):
    """Задачи, ждущие человека, по причинам; внутри — по дедлайну.

    Возвращает пары (задача, погашено ли). Просмотренную терминальную причину
    выбрасываем — так же поступает панель раздела, иначе список за пару месяцев
    превращается в кладбище закрытых задач.
    """
    now = now or datetime.now()
    buckets = {kind: [] for kind in ACTION_KIND_LABELS}
    for task in tasks or []:
        kind = task_action_need(task, user_id, now)
        if not kind:
            continue
        seen = is_action_need_seen(task, kind)
        if seen and kind in TERMINAL_ACTION_KINDS:
            continue
        buckets[kind].append((task, seen))
    for items in buckets.values():
        items.sort(key=lambda pair: parse_iso(pair[0].get('due_at')) or datetime.max)
    return buckets
DURATION_RE = re.compile(r'(?P<value>\d+)\s*(?P<unit>[dhmдчм]+)', re.IGNORECASE)
UNIT_MINUTES = {'d': 1440, 'д': 1440, 'h': 60, 'ч': 60, 'm': 1, 'м': 1}


def parse_duration_to_minutes(raw):
    """«3d4h», «90m», «2ч30м», «120» → минуты."""
    text = str(raw or '').strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    matched = False
    for match in DURATION_RE.finditer(text):
        unit = match.group('unit')[0]
        if unit not in UNIT_MINUTES:
            continue
        total += int(match.group('value')) * UNIT_MINUTES[unit]
        matched = True
    if not matched:
        raise SystemExit(f'Не понял длительность «{raw}». Примеры: 90, 90m, 4h, 3d4h, 2ч30м')
    return total


DATETIME_PATTERNS = ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%d.%m.%Y %H:%M')
DATE_ONLY_PATTERNS = ('%Y-%m-%d', '%d.%m.%Y')


def parse_due_argument(raw):
    """Абсолютная дата: «2026-08-05 18:00», «2026-08-05» (→ 18:00), ISO. Возвращает ISO-строку."""
    text = str(raw or '').strip()
    if not text:
        return None
    for pattern in DATETIME_PATTERNS:
        try:
            return datetime.strptime(text, pattern).isoformat()
        except ValueError:
            continue
    for pattern in DATE_ONLY_PATTERNS:
        try:
            # Дата без времени — считаем «до конца рабочего дня».
            return datetime.strptime(text, pattern).replace(hour=18).isoformat()
        except ValueError:
            continue
    parsed = parse_iso(text)
    if parsed:
        return parsed.isoformat()
    raise SystemExit(f'Не понял дату «{raw}». Примеры: "2026-08-05 18:00", 2026-08-05, 05.08.2026 14:30')


REMINDER_MAX_MINUTES = 24 * 60


REMINDER_WORD_ALIASES = {
    'день': 24 * 60, 'сутки': 24 * 60, 'day': 24 * 60,
    'час': 60, 'hour': 60,
}


def parse_reminder_argument(raw):
    """«1d», «3h», «за день», «off» → минуты до дедлайна. Максимум сутки."""
    text = str(raw or '').strip().lower()
    if not text or text in ('off', 'no', 'none', 'выкл', '0'):
        return 0
    for word, minutes_alias in REMINDER_WORD_ALIASES.items():
        if word in text and not any(ch.isdigit() for ch in text):
            return minutes_alias
    minutes = parse_duration_to_minutes(text)
    if minutes is None:
        return 0
    if minutes > REMINDER_MAX_MINUTES:
        raise SystemExit(f'Напоминание можно поставить максимум за сутки до дедлайна (получено {minutes} мин)')
    return minutes


def split_minutes_for_form(total_minutes):
    """API принимает дедлайн как days/hours/minutes с ограничениями 23/59."""
    total = max(0, int(total_minutes or 0))
    days, rest = divmod(total, 24 * 60)
    hours, minutes = divmod(rest, 60)
    return {'deadline_days': str(days), 'deadline_hours': str(hours), 'deadline_minutes': str(minutes)}


def format_minutes(minutes):
    total = int(minutes or 0)
    if total <= 0:
        return '—'
    if total < 60:
        return f'{total}м'
    hours, rest = divmod(total, 60)
    if hours < 24:
        return f'{hours}ч{rest}м' if rest else f'{hours}ч'
    days, rest_hours = divmod(hours, 24)
    return f'{days}д{rest_hours}ч' if rest_hours else f'{days}д'


def format_due(task, now=None):
    due = parse_iso(task.get('due_at'))
    if not due:
        return '—'
    now = now or datetime.now()
    label = due.strftime('%d.%m %H:%M')
    if task.get('status') in ('accepted', 'completed'):
        return label
    delta_minutes = int((due - now).total_seconds() // 60)
    if delta_minutes < 0:
        return f'{label} (просрочка {format_minutes(-delta_minutes)})'
    return f'{label} (через {format_minutes(delta_minutes)})'


def task_assignees(task):
    """Все исполнители задачи по порядку.

    Ответ сервера без `assignees` читаем как список из одного человека: CLI
    ходит по прод-API, и старый ответ (или кеш) не должен превращаться в
    «задача без исполнителя».
    """
    people = [item for item in (task.get('assignees') or []) if (item or {}).get('id')]
    if people:
        return people
    single = task.get('assignee') or {}
    return [single] if single.get('id') else []


def task_assignees_label(task, empty='—'):
    """«Айгуль» либо «Айгуль +2»: в строку таблицы три имени не влезают."""
    people = task_assignees(task)
    if not people:
        return empty
    first = people[0].get('name') or empty
    return f'{first} +{len(people) - 1}' if len(people) > 1 else first


def is_task_assignee(task, person_id):
    """Пользователь — один из исполнителей (а не «тот единственный»)."""
    person_id = int(person_id or 0)
    return bool(person_id) and any(
        int(person.get('id') or 0) == person_id for person in task_assignees(task)
    )


def task_line(task, now=None):
    parts = [
        f'#{task["id"]:<5}',
        f'{(task.get("subject") or "")[:56]:<56}',
        f'{task_assignees_label(task)[:20]:<20}',
        f'{task.get("priority", "normal")[:8]:<8}',
        f'оц {format_minutes(task.get("estimate_minutes")):<7}',
        f'срок {format_due(task, now)}',
    ]
    return '  '.join(parts)


# ─────────────── Вложения ───────────────

# Имя файла в базе бывает без расширения: у задачи #160 вложение с ТЗ называется
# буквально «docx». Разбор документа выбирает парсер по расширению, поэтому его
# приходится восстанавливать — сначала из content_type, потом по сигнатуре файла.
CONTENT_TYPE_EXTENSIONS = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/msword': '.doc',
    'application/pdf': '.pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-excel.sheet.macroenabled.12': '.xlsm',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/vnd.ms-powerpoint': '.ppt',
    'text/csv': '.csv',
    'text/plain': '.txt',
    'text/markdown': '.md',
    'application/json': '.json',
    'text/html': '.html',
    'application/zip': '.zip',
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'video/mp4': '.mp4',
}

# Расширения, которые разбирает wiki/importer.py — тот же код, что импортирует
# документы в статьи вики. Своего парсера документов не заводим.
WIKI_IMPORTER_EXTENSIONS = ('.docx', '.doc', '.pdf', '.xlsx', '.xlsm', '.csv', '.txt', '.md')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')
MEDIA_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.mp3', '.ogg', '.wav', '.webm')
PLAIN_EXTENSIONS = ('.txt', '.md', '.json', '.log', '.csv', '.yml', '.yaml', '.sql', '.html', '.htm')


def _sniff_extension(data):
    """Расширение по сигнатуре: docx/xlsx/pptx — все zip, различаем по содержимому."""
    head = bytes(data[:8])
    if head.startswith(b'%PDF'):
        return '.pdf'
    if head.startswith(b'\x89PNG'):
        return '.png'
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head.startswith(b'GIF8'):
        return '.gif'
    if head.startswith(b'\xd0\xcf\x11\xe0'):
        # Старый контейнер OLE2 — .doc/.xls/.ppt; точнее без разбора не скажешь.
        return '.doc'
    if head[:2] == b'PK' and head[2:4] in (b'\x03\x04', b'\x05\x06', b'\x07\x08'):
        # PK\x05\x06 — пустой архив, PK\x07\x08 — разбитый на части.
        import zipfile
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except Exception:
            return '.zip'
        if any(name.startswith('word/') for name in names):
            return '.docx'
        if any(name.startswith('xl/') for name in names):
            return '.xlsx'
        if any(name.startswith('ppt/') for name in names):
            return '.pptx'
        return '.zip'
    return ''


def attachment_extension(file_name, content_type='', data=b''):
    ext = os.path.splitext(str(file_name or ''))[1].lower()
    if ext:
        return ext
    ext = CONTENT_TYPE_EXTENSIONS.get(str(content_type or '').split(';')[0].strip().lower(), '')
    if ext:
        return ext
    return _sniff_extension(data or b'')


def safe_attachment_filename(attachment, data=b''):
    """Имя для диска: с восстановленным расширением и без сюрпризов в путях.

    Расширение приходится дописывать чаще, чем кажется: werkzeug пропускает имя
    через secure_filename, а тот выбрасывает не-ASCII — файл «проверка.xlsx»
    доезжает до базы под именем «xlsx». Для кириллических имён это норма, не край.
    """
    raw = str(attachment.get('file_name') or '') or f'attachment_{attachment.get("id")}'
    raw = os.path.basename(raw.replace('\\', '/'))
    if not os.path.splitext(raw)[1]:
        raw += attachment_extension(raw, attachment.get('content_type'), data)
    safe = re.sub(r'[^\w.\-]+', '_', raw, flags=re.UNICODE).strip('._')
    return f'{attachment.get("id")}_{safe or "attachment"}'


def _pptx_to_text(data):
    """Слайды: wiki/importer.py презентации не знает, а ТЗ нередко приходит ими."""
    import zipfile
    from xml.etree import ElementTree

    text_tag = '{http://schemas.openxmlformats.org/drawingml/2006/main}t'
    archive = zipfile.ZipFile(io.BytesIO(data))
    slides = sorted(
        (name for name in archive.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', name)),
        key=lambda name: int(re.search(r'(\d+)', name).group(1)),
    )
    chunks = []
    for index, name in enumerate(slides, start=1):
        pieces = [
            (node.text or '').strip()
            for node in ElementTree.fromstring(archive.read(name)).iter(text_tag)
        ]
        body = '\n'.join(piece for piece in pieces if piece)
        if body:
            chunks.append('── Слайд %d ──\n%s' % (index, body))
    return '\n\n'.join(chunks)


def _zip_listing(data):
    import zipfile
    archive = zipfile.ZipFile(io.BytesIO(data))
    return '\n'.join(f'{item.file_size:>10}  {item.filename}' for item in archive.infolist())


def _decode_plain(data):
    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            return data.decode(encoding), f'текст ({encoding})'
        except UnicodeDecodeError:
            continue
    return None, 'двоичный файл — текста нет'


def extract_attachment_text(file_name, content_type, data):
    """Текст вложения → (текст | None, пояснение).

    None в тексте значит «читаемого текста здесь нет»: например картинка —
    её агент открывает файлом и смотрит сам, этот CLI за него не смотрит.
    """
    ext = attachment_extension(file_name, content_type, data)

    if ext in IMAGE_EXTENSIONS:
        return None, 'картинка — открой скачанный файл и посмотри на него'
    if ext in MEDIA_EXTENSIONS:
        return None, 'запись экрана или звук — текст отсюда не вынимается'

    if ext == '.pptx':
        try:
            text = _pptx_to_text(data)
            return (text, 'презентация') if text.strip() else (None, 'в презентации нет текста')
        except Exception as error:
            return None, f'презентация не разобралась: {error}'

    if ext in ('.txt', '.md'):
        # Разбираем сами: wiki/importer.py декодирует простой текст как utf-8 с
        # заменой символов, и файл в cp1251 доезжает решёткой вопросительных знаков.
        return _decode_plain(data)

    if ext in WIKI_IMPORTER_EXTENSIONS:
        try:
            # Тот же разбор, что и импорт документа в статью вики: docx через mammoth
            # с сохранением заголовков, pdf через pypdf, xlsx/csv через openpyxl.
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from wiki.importer import convert
            from wiki.sanitize import to_plain_text

            # Картинки документа нам не нужны — важен текст постановки.
            result = convert('file' + ext, data, store_image=lambda blob, content_type='': '')
            text = to_plain_text(result.get('content') or '')
            note = result.get('kind') or ext.lstrip('.')
            if result.get('warnings'):
                note += ' · ' + '; '.join(str(item) for item in result['warnings'])
            if text.strip():
                return text, note
            if ext not in PLAIN_EXTENSIONS:
                return None, f'{note}: текста не нашлось'
        except Exception as error:
            # Старый .doc mammoth не открывает, PDF бывает сканом — это не авария.
            reason = str(error) or type(error).__name__
            if ext not in PLAIN_EXTENSIONS:
                return None, f'разобрать не удалось: {reason}'

    if ext == '.zip':
        try:
            return _zip_listing(data), 'архив — список файлов внутри'
        except Exception as error:
            return None, f'архив не читается: {error}'

    return _decode_plain(data)


def format_bytes(size):
    total = int(size or 0)
    if total < 1024:
        return f'{total} Б'
    if total < 1024 * 1024:
        return f'{total / 1024:.0f} КБ'
    return f'{total / (1024 * 1024):.1f} МБ'


def all_attachments(task):
    """initial + result одним списком: сервер раскладывает их по двум ключам."""
    return list(task.get('attachments') or []) + list(task.get('completion_attachments') or [])


def attachment_line(attachment):
    kind = 'результат' if attachment.get('attachment_kind') == 'result' else 'постановка'
    created = str(attachment.get('created_at') or '')[:16].replace('T', ' ')
    return (f'#{attachment.get("id"):<5} {str(attachment.get("file_name") or "—")[:42]:<42} '
            f'{format_bytes(attachment.get("file_size")):>8}  {kind:<10} {created}')


def download_dir_for(task_id, override=None):
    path = override or os.path.join(DOWNLOAD_ROOT, f'task_{int(task_id)}')
    os.makedirs(path, exist_ok=True)
    return path


def upload_payload(paths):
    """Файлы для multipart. Пределы сервера проверяем заранее — иначе 400 без объяснения."""
    if not paths:
        return None
    if len(paths) > MAX_UPLOAD_FILES:
        raise SystemExit(f'За раз можно приложить не больше {MAX_UPLOAD_FILES} файлов (передано {len(paths)})')
    files = []
    for path in paths:
        if not os.path.isfile(path):
            raise SystemExit(f'Файл не найден: {path}')
        size = os.path.getsize(path)
        if size > MAX_UPLOAD_BYTES:
            raise SystemExit(f'Файл «{os.path.basename(path)}» больше 10 МБ ({format_bytes(size)})')
        content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as handle:
            files.append(('files', (os.path.basename(path), handle.read(), content_type)))
    return files


# ─────────────── Команды ───────────────

def cmd_board(client, args):
    payload = client.list_tasks(only_my='1' if args.mine else None)
    tasks = payload.get('tasks') or []
    if args.assignee:
        # Фильтр по любому из указанных исполнителей: задача общая, и искать её
        # надо по участию, а не по «первому в списке».
        wanted = args.assignee if isinstance(args.assignee, list) else [args.assignee]
        tasks = [t for t in tasks if any(is_task_assignee(t, person_id) for person_id in wanted)]
    if args.json:
        print(json.dumps({'tasks': tasks, 'summary': payload.get('summary')}, ensure_ascii=False, indent=2))
        return

    now = datetime.now()
    buckets = {key: [] for key, _ in COLUMN_TITLES}
    for task in tasks:
        buckets[column_of(task)].append(task)

    print(f'Доска задач · {client.user_name} (id {client.user_id}) · всего {len(tasks)}')
    for key, title in COLUMN_TITLES:
        items = buckets[key]
        if key == 'backlog':
            items.sort(key=lambda t: (t.get('backlog_rank') is None, t.get('backlog_rank') or 0))
        if key == 'done':
            items.sort(key=lambda t: accepted_at(t) or datetime.min, reverse=True)
        print(f'\n── {title} ({len(items)}) ' + '─' * max(0, 60 - len(title)))
        if not items:
            print('   —')
        for task in items:
            print('   ' + task_line(task, now))


def cmd_backlog(client, args):
    payload = client.list_tasks(backlog='only')
    tasks = payload.get('tasks') or []
    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return
    total_estimate = sum(int(t.get('estimate_minutes') or 0) for t in tasks)
    print(f'Бэклог: {len(tasks)} задач, суммарная оценка {format_minutes(total_estimate)}')
    for index, task in enumerate(tasks, start=1):
        rank = task.get('backlog_rank')
        rank_label = f'{rank:g}' if isinstance(rank, (int, float)) else '—'
        print(f'{index:>3}. rank {rank_label:<8} ' + task_line(task))
    missing = [t['id'] for t in tasks if not t.get('estimate_minutes')]
    if missing:
        print(f'\nБез оценки: {", ".join("#" + str(i) for i in missing)}')


def _person_label(person):
    person = person or {}
    name = person.get('name')
    if not name:
        return '—'
    return f'{name} (id {person.get("id")})' if person.get('id') is not None else name


def _recurrence_label(task):
    """Регламент: повторяющаяся задача. В карточке это отдельная сущность, а не флаг."""
    if not task.get('is_regulation') and not task.get('recurrence_type'):
        return None
    parts = []
    kind = task.get('recurrence_type')
    interval = task.get('recurrence_interval')
    if kind:
        unit = {'daily': 'дн', 'weekly': 'нед', 'monthly': 'мес', 'yearly': 'год'}.get(kind, kind)
        parts.append(f'раз в {interval or 1} {unit}')
    if task.get('recurrence_next_at'):
        parts.append(f'следующая {str(task["recurrence_next_at"])[:16].replace("T", " ")}')
    if task.get('regulation_iteration'):
        parts.append(f'итерация {task["regulation_iteration"]}')
    if task.get('regulation_parent_id'):
        parts.append(f'шаблон #{task["regulation_parent_id"]}')
    return ', '.join(parts) or 'да'


def _print_checklist(task, indent='  '):
    items = task.get('checklist') or []
    if not items:
        return
    done = sum(1 for item in items if item.get('is_done'))
    print(f'{indent}чек-лист ({done}/{len(items)}):')
    for item in items:
        mark = 'x' if item.get('is_done') else ' '
        flags = ' · обязательный' if item.get('is_required') else ''
        print(f'{indent}  [{mark}] #{item.get("id")} {item.get("title")}{flags}')
        if item.get('result_note'):
            print(f'{indent}      результат: {item["result_note"]}')
        if item.get('is_done') and (item.get('completed_by_name') or item.get('completed_at')):
            who = item.get('completed_by_name') or '—'
            when = str(item.get('completed_at') or '')[:16].replace('T', ' ')
            print(f'{indent}      отметил {who} {when}')


def _print_reports(task, indent='  '):
    reports = task.get('reports') or []
    if not reports:
        return
    print(f'{indent}отчёты о работе ({len(reports)}):')
    for report in reports:
        kind_label = 'ИТОГ' if report.get('kind') == 'completion' else '····'
        created = str(report.get('created_at') or '')[:16].replace('T', ' ')
        print(f'{indent}  [{kind_label}] #{report.get("id")} {created}  '
              f'{report.get("author_name") or "—"}  ({format_minutes(report.get("spent_minutes"))})')
        for line in str(report.get('body') or '').splitlines():
            print(f'{indent}         {line}')


MESSAGE_KIND_LABELS = {
    'note': 'дополнение',
    'request': 'НЕ ХВАТАЕТ ИНФОРМАЦИИ',
    'answer': 'ответ',
}


def _print_messages(task, indent='  '):
    """Уточнения по постановке. Дополнение ТЗ приходит именно сюда, не в описание."""
    messages = task.get('messages') or []
    if not messages:
        return
    print(f'{indent}уточнения ({len(messages)}):')
    for message in messages:
        kind = message.get('kind') or 'note'
        label = MESSAGE_KIND_LABELS.get(kind, kind)
        created = str(message.get('created_at') or '')[:16].replace('T', ' ')
        open_mark = ' ← ЖДЁТ ОТВЕТА' if kind == 'request' and not message.get('resolved_at') else ''
        print(f'{indent}  #{message.get("id")} {created}  {message.get("author_name") or "—"}  '
              f'[{label}]{open_mark}')
        for line in str(message.get('body') or '').splitlines():
            print(f'{indent}       {line}')
        for attachment in (message.get('attachments') or []):
            print(f'{indent}       файл: {attachment_line(attachment)}')


def _print_history(task, indent='  '):
    history = task.get('history') or []
    if not history:
        return
    print(f'{indent}история ({len(history)}):')
    for item in history:
        code = item.get('status_code') or '—'
        label = STATUS_LABELS.get(code, code)
        when = str(item.get('changed_at') or '')[:16].replace('T', ' ')
        print(f'{indent}  {when}  {code:<12} {label:<24} {item.get("changed_by_name") or "—"}')
        # Комментарий перехода — здесь пишут, почему вернули на доработку.
        for line in str(item.get('comment') or '').splitlines():
            if line.strip():
                print(f'{indent}      ↳ {line}')


def _print_attachments(task, indent='  '):
    """Вложения печатаем всегда, когда они есть, — в них нередко лежит вся постановка."""
    items = all_attachments(task)
    if not items:
        return
    print(f'{indent}ВЛОЖЕНИЯ ({len(items)}):')
    for attachment in items:
        print(f'{indent}  {attachment_line(attachment)}')
    print(f'{indent}  читать: python -X utf8 scripts/task_board.py files {task["id"]} --text')


def cmd_show(client, args):
    task = client.get_task(args.task_id)
    if not task:
        raise SystemExit(f'Задача #{args.task_id} не найдена (или недоступна этому пользователю)')
    if args.json:
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return

    print(f'#{task["id"]} · {task.get("subject")}')
    print(f'  колонка       {dict(COLUMN_TITLES)[column_of(task)]} '
          f'(status={task.get("status")}, is_backlog={task.get("is_backlog")})')
    print(f'  тип           {TAG_LABELS.get(task.get("tag"), task.get("tag") or "—")}')
    people = task_assignees(task)
    if len(people) > 1:
        # Права у исполнителей равные, поэтому просто перечисляем — «основного»
        # среди них нет.
        print(f'  исполнители   {", ".join(_person_label(person) for person in people)}')
    else:
        print(f'  исполнитель   {_person_label(people[0] if people else None)}')
    print(f'  постановщик   {_person_label(task.get("creator"))}')
    print(f'  срочность     {PRIORITY_LABELS.get(task.get("priority"), task.get("priority") or "—")}')
    origin = task.get('requested_by') or {}
    origin_label = origin.get('name') or (
        'своя инициатива'
        if is_task_assignee(task, (task.get('creator') or {}).get('id'))
        else '—'
    )
    print(f'  поручил       {origin_label}')
    print(f'  оценка        {format_minutes(task.get("estimate_minutes"))}')
    print(f'  затрачено     {format_minutes(task.get("spent_minutes"))} (по отчётам)')
    print(f'  план старта   {str(task.get("planned_start_at") or "—")[:16].replace("T", " ")}')
    print(f'  начато        {str(task.get("started_at") or "—")[:16].replace("T", " ")}')
    print(f'  дедлайн       {format_due(task)}')
    if task.get('deadline_duration_minutes'):
        print(f'  срок от выдачи{format_minutes(task.get("deadline_duration_minutes")):>15}')
    remind = task.get('reminder_minutes_before')
    sent = task.get('reminder_sent_at')
    print('  напоминание   ' + (f'за {format_minutes(remind)} до дедлайна'
                                + (f' (отправлено {str(sent)[:16]})' if sent else '') if remind else '—'))
    recurrence = _recurrence_label(task)
    if recurrence:
        print(f'  регламент     {recurrence}')
    print(f'  создана       {str(task.get("created_at") or "—")[:16].replace("T", " ")}')
    print(f'  обновлена     {str(task.get("updated_at") or "—")[:16].replace("T", " ")}')
    if task.get('completed_at') or task.get('completed_by_name'):
        print(f'  закрыта       {str(task.get("completed_at") or "")[:16].replace("T", " ")} '
              f'{task.get("completed_by_name") or ""}'.rstrip())
    print(f'  rank          {task.get("backlog_rank")}')
    # action_seen — это объект {kind, seen_at}, а не флаг: гасится по конкретной причине.
    seen_mark = task.get('action_seen') or {}
    print('  уведомление   ' + (
        f'«{ACTION_KIND_LABELS.get(seen_mark.get("kind"), seen_mark.get("kind"))}» '
        f'просмотрено {str(seen_mark.get("seen_at") or "")[:16].replace("T", " ")}'
        if seen_mark.get('kind') else 'не просмотрено'))

    description = str(task.get('description') or '').strip()
    attachments = all_attachments(task)
    if description:
        print('  описание:')
        for line in description.splitlines():
            print(f'      {line}')
    elif attachments:
        # Ровно случай задачи #160: описание пустое, постановка целиком во вложении.
        print('  описание      пусто — постановка, судя по всему, во ВЛОЖЕНИЯХ (ниже)')
    else:
        print('  описание      —')

    _print_attachments(task)
    _print_messages(task)
    _print_checklist(task)
    _print_reports(task)
    if task.get('completion_summary'):
        print('  итог (последний итоговый отчёт):')
        for line in str(task['completion_summary']).splitlines():
            print(f'      {line}')
    _print_history(task)


def cmd_history(client, args):
    task = client.get_task(args.task_id)
    if not task:
        raise SystemExit(f'Задача #{args.task_id} не найдена')
    history = task.get('history') or []
    if args.json:
        print(json.dumps(history, ensure_ascii=False, indent=2))
        return
    print(f'История #{task["id"]} «{task.get("subject")}»: {len(history)} записей')
    _print_history(task, indent='')


def cmd_files(client, args):
    task = client.get_task(args.task_id)
    if not task:
        raise SystemExit(f'Задача #{args.task_id} не найдена')

    items = all_attachments(task)
    if args.kind == 'initial':
        items = [a for a in items if a.get('attachment_kind') != 'result']
    elif args.kind == 'result':
        items = [a for a in items if a.get('attachment_kind') == 'result']
    if args.only:
        wanted = set(args.only)
        items = [a for a in items if int(a.get('id')) in wanted]

    if not items:
        if args.json:
            print(json.dumps([], ensure_ascii=False))
            return
        print(f'У задачи #{task["id"]} вложений нет' + (' под этот фильтр' if args.only or args.kind else ''))
        return

    # --text и --download оба требуют самих файлов; без них — только список.
    need_bytes = args.text or args.download
    target_dir = download_dir_for(args.task_id, args.dir) if need_bytes else None

    results = []
    for attachment in items:
        row = dict(attachment)
        if need_bytes:
            data, server_name = client.download_attachment(attachment['id'])
            path = os.path.join(target_dir, safe_attachment_filename(attachment, data))
            with open(path, 'wb') as handle:
                handle.write(data)
            row['saved_to'] = path
            row['downloaded_bytes'] = len(data)
            if server_name:
                row['server_file_name'] = server_name
            if args.text:
                text, note = extract_attachment_text(
                    attachment.get('file_name'), attachment.get('content_type'), data
                )
                row['text'] = text
                row['text_note'] = note
        results.append(row)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f'Вложения #{task["id"]} «{task.get("subject")}»: {len(results)}')
    if target_dir:
        print(f'Папка: {target_dir}')
    for row in results:
        print('  ' + attachment_line(row))
        if row.get('saved_to'):
            print(f'      файл: {row["saved_to"]} ({format_bytes(row.get("downloaded_bytes"))})')
        if not args.text:
            continue
        note = row.get('text_note') or ''
        text = row.get('text')
        if not text:
            print(f'      текст: {note}')
            continue
        limit = args.limit if args.limit and args.limit > 0 else None
        body = text if limit is None else text[:limit]
        print(f'      текст ({note}, {len(text)} символов'
              + (f', показаны первые {len(body)}' if limit and len(text) > limit else '') + '):')
        for line in body.splitlines():
            print(f'        {line}')
        if limit and len(text) > limit:
            print(f'        … обрезано; целиком — с --limit 0 или из файла {row.get("saved_to")}')


def cmd_checklist(client, args):
    task = client.get_task(args.task_id)
    if not task:
        raise SystemExit(f'Задача #{args.task_id} не найдена')
    if args.json:
        print(json.dumps(task.get('checklist') or [], ensure_ascii=False, indent=2))
        return
    items = task.get('checklist') or []
    if not items:
        print(f'У задачи #{task["id"]} чек-листа нет')
        return
    print(f'Чек-лист #{task["id"]} «{task.get("subject")}»')
    _print_checklist(task, indent='')


def cmd_check(client, args):
    result = client.set_checklist_item(
        args.task_id, args.item_id,
        is_done=not args.undone,
        result_note=args.note if args.note is not None else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    item = result.get('item') or {}
    mark = 'x' if item.get('is_done') else ' '
    print(f'#{args.task_id} чек-лист [{mark}] #{item.get("id")} {item.get("title")}')
    if item.get('result_note'):
        print(f'  результат: {item["result_note"]}')
    # Отметка чек-листа уходит в Telegram всем участникам задачи — это не тихая правка.
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_inbox(client, args):
    summary = client.action_required()
    breakdown = summary.get('breakdown') or {}
    tasks = client.list_tasks(summary='0').get('tasks') or []
    by_kind = action_needs_by_kind(tasks, client.user_id)

    if args.json:
        print(json.dumps({
            'count': summary.get('count'),
            'breakdown': breakdown,
            'tasks': {
                kind: [{'id': task['id'], 'seen': seen} for task, seen in items]
                for kind, items in by_kind.items()
            },
        }, ensure_ascii=False, indent=2))
        return

    print(f'Ждут действия от {client.user_name} (id {client.user_id}): '
          f'{summary.get("count", 0)} непогашенных')
    for kind, label in ACTION_KIND_LABELS.items():
        server_count = breakdown.get(kind, 0)
        items = by_kind.get(kind) or []
        fresh = [pair for pair in items if not pair[1]]
        print(f'\n── {label} · {len(fresh)} новых из {len(items)} '
              + '─' * max(0, 34 - len(label)))
        if not items:
            print('   —')
        for task, seen in items:
            print(('  ·' if seen else '   ') + task_line(task) + ('  (просмотрено)' if seen else ''))
        if len(fresh) != server_count:
            # Расхождение с бейджем сайдбара значит, что правила разъехались:
            # они продублированы в четырёх местах, см. task_action_need.
            print(f'   ! сервер считает {server_count}, а не {len(fresh)} — правила разъехались')
def cmd_seen(client, args):
    result = client.mark_action_seen(args.task_id, args.kind)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f'#{args.task_id}: уведомление погашено; осталось {result.get("count", 0)}')
    for kind, count in (result.get('breakdown') or {}).items():
        if count:
            print(f'  {ACTION_KIND_LABELS.get(kind, kind)}: {count}')


def cmd_people(client, args):
    if args.departments:
        payload = client.departments()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        departments = payload.get('departments') or []
        print(f'Отделы: {len(departments)} (по умолчанию id {payload.get("default_department_id")})')
        for item in departments:
            print(f'  {str(item.get("id")):>5}  {str(item.get("name") or "")[:40]:<40} задач {item.get("task_count", "—")}')
        return

    if args.all:
        # board_people — все сотрудники с отделами (без уволенных); recipients — только те,
        # кому этот пользователь вправе ставить задачи.
        payload = client.board_people()
        people = payload.get('people') or []
        source = 'сотрудники (переключатель досок)'
    else:
        payload = client.recipients()
        people = payload.get('recipients') or []
        source = 'кому можно ставить задачи'
    if args.json:
        print(json.dumps(people, ensure_ascii=False, indent=2))
        return
    print(f'{source}: {len(people)}')
    for person in people:
        department = person.get('department_name') or person.get('department') or ''
        print(f'  {str(person.get("id")):>5}  {str(person.get("name") or "")[:34]:<34} '
              f'{str(person.get("role") or ""):<14} {department}')


def cmd_export(client, args):
    data, server_name = client.export_tasks(
        mine=args.mine, person_id=args.person, person_scope=args.person_scope,
        department_id=args.department,
    )
    target_dir = args.dir or DOWNLOAD_ROOT
    os.makedirs(target_dir, exist_ok=True)
    name = server_name or f'Задачи_{datetime.now():%Y-%m-%d}.xlsx'
    path = os.path.join(target_dir, re.sub(r'[^\w.\-]+', '_', name, flags=re.UNICODE))
    with open(path, 'wb') as handle:
        handle.write(data)
    if args.json:
        print(json.dumps({'saved_to': path, 'bytes': len(data)}, ensure_ascii=False, indent=2))
        return
    print(f'Выгрузка сохранена: {path} ({format_bytes(len(data))})')


def _note_line(note, now=None):
    mark = 'x' if note.get('is_done') else ' '
    kind = 'дело' if note.get('is_task') else 'заметка'
    due = format_due({'due_at': note.get('due_at'), 'status': 'accepted' if note.get('is_done') else 'assigned'}, now)
    remind = note.get('reminder_minutes_before')
    remind_label = f'напомнить за {format_minutes(remind)}' if remind else ''
    title = note.get('title') or (str(note.get('body') or '').strip().splitlines() or [''])[0]
    return (f'[{mark}] #{str(note.get("id")):<5} {kind:<7} {str(title)[:44]:<44} '
            f'{PRIORITY_LABELS.get(note.get("priority"), ""):<9} срок {due}  {remind_label}').rstrip()


def cmd_notes(client, args):
    notes = client.list_notes().get('notes') or []
    if not args.all:
        notes = [n for n in notes if not n.get('is_done')]
    if args.json:
        print(json.dumps(notes, ensure_ascii=False, indent=2))
        return
    # Заметки личные: сервер отдаёт только свои (owner_id = запросивший).
    print(f'Заметки {client.user_name}: {len(notes)}' + ('' if args.all else ' (открытые; --all — вместе с закрытыми)'))
    now = datetime.now()
    for note in notes:
        print('  ' + _note_line(note, now))
        for line in str(note.get('body') or '').splitlines():
            if line.strip() and line.strip() != (note.get('title') or '').strip():
                print(f'        {line}')


def _note_payload(args):
    payload = {}
    if getattr(args, 'title', None) is not None:
        payload['title'] = args.title
    if getattr(args, 'body', None) is not None:
        payload['body'] = args.body
    if getattr(args, 'priority', None):
        payload['priority'] = args.priority
    if getattr(args, 'due', None):
        payload['due_at'] = parse_due_argument(args.due)
    elif getattr(args, 'in_', None):
        payload['due_at'] = (datetime.now() + timedelta(minutes=parse_duration_to_minutes(args.in_))).isoformat()
    elif getattr(args, 'clear_due', False):
        payload['due_at'] = None
    if getattr(args, 'remind', None) is not None:
        # Тот же предел, что и у задач: больше суток сервер отклонит.
        payload['reminder_minutes_before'] = parse_reminder_argument(args.remind) or None
    if getattr(args, 'todo', False):
        payload['is_task'] = True
    return payload


def cmd_note_add(client, args):
    payload = _note_payload(args)
    payload.setdefault('body', args.body or '')
    if not (payload.get('title') or payload.get('body')):
        raise SystemExit('Заметка без текста: укажите текст или --title')
    result = client.create_note(payload)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print('Заметка создана: ' + _note_line(result.get('note') or {}))


def cmd_note_set(client, args):
    payload = _note_payload(args)
    if args.done:
        payload['is_done'] = True
    if args.undone:
        payload['is_done'] = False
    if not payload:
        raise SystemExit('Нечего менять: --title / --body / --priority / --due / --in / --clear-due / '
                         '--remind / --done / --undone')
    result = client.update_note(args.note_id, payload)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print('Заметка обновлена: ' + _note_line(result.get('note') or {}))


def cmd_note_del(client, args):
    result = client.delete_note(args.note_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f'Заметка #{args.note_id} удалена')


def _build_create_fields(client, args, assignee_ids):
    ids = [int(item) for item in (assignee_ids or []) if item]
    fields = {
        'subject': args.subject,
        'description': getattr(args, 'description', '') or '',
        'tag': getattr(args, 'tag', 'task'),
        'priority': getattr(args, 'priority', 'normal'),
        # Первый исполнитель отдельным полем (это tasks.assigned_to), весь
        # состав — строкой через запятую, как его шлёт форма раздела.
        'assigned_to': str(ids[0]) if ids else '',
        'assignee_ids': ','.join(str(item) for item in ids),
        'is_backlog': '1' if getattr(args, 'backlog', False) else '0',
    }
    if getattr(args, 'estimate', None):
        fields['estimate_minutes'] = str(parse_duration_to_minutes(args.estimate))
    if getattr(args, 'due', None):
        fields['due_at'] = parse_due_argument(args.due)
    elif getattr(args, 'in_', None):
        fields.update(split_minutes_for_form(parse_duration_to_minutes(args.in_)))
    if getattr(args, 'checklist', None):
        fields['checklist_items'] = json.dumps([{'title': item} for item in args.checklist], ensure_ascii=False)
    if getattr(args, 'remind', None) is not None:
        fields['reminder_minutes_before'] = str(parse_reminder_argument(args.remind))
    # Источник задачи: сотрудник, свободный текст либо (по умолчанию) своя инициатива.
    if getattr(args, 'from_id', None):
        fields['requested_by_id'] = str(args.from_id)
    elif getattr(args, 'from_name', None):
        fields['requested_by_name'] = args.from_name
    return fields


def _resolve_assignees(client, args):
    """Состав исполнителей: --assignee можно указать несколько раз, --self добавляет себя.

    Порядок сохраняем в том, в каком их назвали: первый становится
    tasks.assigned_to. Дубликаты сворачиваем — «--self --assignee <свой id>»
    не должен ронять создание.
    """
    ordered = []
    raw = getattr(args, 'assignee', None) or []
    if not isinstance(raw, list):
        raw = [raw]
    if getattr(args, 'self_assign', False):
        raw = [client.user_id] + list(raw)
    for item in raw:
        person_id = int(item)
        if person_id and person_id not in ordered:
            ordered.append(person_id)
    if not ordered:
        raise SystemExit('Укажите исполнителя: --assignee <id> (можно несколько) или --self (себе)')
    return ordered


def _origin_label(args):
    if getattr(args, 'from_id', None):
        return f'поручил id {args.from_id}'
    if getattr(args, 'from_name', None):
        return f'поручил: {args.from_name}'
    return 'своя инициатива'


def cmd_create(client, args):
    assignee_ids = _resolve_assignees(client, args)
    files = upload_payload(getattr(args, 'attach', None))
    result = client.create_task(_build_create_fields(client, args, assignee_ids), files=files)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    where = 'бэклог' if args.backlog else 'доска'
    print(f'Создана задача #{result.get("task_id")} → {where}; {_origin_label(args)}; '
          f'дедлайн {result.get("due_at") or "—"}')
    if files:
        print(f'  приложено файлов: {len(files)}')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_log(client, args):
    """
    Записать уже сделанную работу одной командой: создать задачу на себя,
    провести её через работу и закрыть итоговым отчётом с трудозатратами.
    """
    assignee_ids = [client.user_id]
    fields = _build_create_fields(client, args, assignee_ids)
    fields['is_backlog'] = '0'
    if args.spent and 'estimate_minutes' not in fields:
        # Оценки не было — считаем, что она равна факту, чтобы метрика «факт к оценке» не врала.
        fields['estimate_minutes'] = str(parse_duration_to_minutes(args.spent))

    created = client.create_task(fields, files=upload_payload(getattr(args, 'attach', None)))
    task_id = created.get('task_id')
    if not task_id:
        raise SystemExit(f'Не удалось создать задачу: {json.dumps(created, ensure_ascii=False)[:300]}')

    steps = [('in_progress', {})]
    for text in (args.progress or []):
        steps.append(('__report__', {'body': text}))
    steps.append(('completed', {
        'report': args.report,
        'spent': parse_duration_to_minutes(args.spent) if args.spent else None,
    }))
    if not args.keep_open:
        steps.append(('accepted', {}))

    done = []
    for action, payload in steps:
        if action == '__report__':
            client.add_report(task_id, payload['body'])
            done.append('промежуточный отчёт')
            continue
        if action == 'completed':
            client.set_status(task_id, 'completed',
                              completion_summary=payload['report'],
                              spent_minutes=payload['spent'])
            done.append('сдана с отчётом')
            continue
        client.set_status(task_id, action)
        done.append({'in_progress': 'взята в работу', 'accepted': 'принята'}[action])

    if args.json:
        print(json.dumps({'task_id': task_id, 'steps': done}, ensure_ascii=False, indent=2))
        return
    spent_label = format_minutes(parse_duration_to_minutes(args.spent)) if args.spent else '—'
    print(f'Задача #{task_id} «{args.subject}»: {", ".join(done)}')
    print(f'  затрачено {spent_label}; {_origin_label(args)}; '
          f'{"осталась на проверке" if args.keep_open else "закрыта"}')


def cmd_deadline(client, args):
    payload = {}
    if args.due:
        payload['due_at'] = parse_due_argument(args.due)
    elif args.in_:
        minutes = parse_duration_to_minutes(args.in_)
        payload['due_at'] = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    elif args.clear:
        payload['due_at'] = None
    if args.estimate is not None:
        payload['estimate_minutes'] = parse_duration_to_minutes(args.estimate) if args.estimate else None
    if args.planned_start:
        payload['planned_start_at'] = parse_due_argument(args.planned_start)
    if args.remind is not None:
        payload['reminder_minutes_before'] = parse_reminder_argument(args.remind)
    if not payload:
        raise SystemExit('Нечего менять: укажите --due / --in / --clear / --estimate / --planned-start / --remind')

    result = client.board_update([{'task_id': args.task_id, **payload}])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    item = (result.get('items') or [{}])[0]
    print(
        f'#{item.get("task_id")} «{item.get("subject")}»: '
        f'изменено {", ".join(item.get("changed_fields") or []) or "ничего"}; '
        f'дедлайн {item.get("due_at") or "—"}, оценка {format_minutes(item.get("estimate_minutes"))}'
    )


def _apply_backlog_flag(client, args, is_backlog):
    result = client.board_update([{'task_id': args.task_id, 'is_backlog': is_backlog}])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    item = (result.get('items') or [{}])[0]
    target = 'бэклог' if is_backlog else 'доску (К выполнению)'
    if item.get('updated'):
        print(f'#{item.get("task_id")} «{item.get("subject")}» → {target}')
    else:
        print(f'#{item.get("task_id")} уже там, изменений нет')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_promote(client, args):
    _apply_backlog_flag(client, args, False)


def cmd_park(client, args):
    _apply_backlog_flag(client, args, True)


def cmd_rank(client, args):
    tasks = client.list_tasks(backlog='only').get('tasks') or []
    ranked = [t for t in tasks if isinstance(t.get('backlog_rank'), (int, float))]
    by_id = {int(t['id']): t for t in tasks}
    if args.task_id not in by_id:
        raise SystemExit(f'#{args.task_id} нет в бэклоге')

    if args.top:
        lowest = min((t['backlog_rank'] for t in ranked), default=1)
        new_rank = lowest - 1
    elif args.bottom:
        highest = max((t['backlog_rank'] for t in ranked), default=0)
        new_rank = highest + 1
    elif args.after is not None:
        anchor = by_id.get(args.after)
        if not anchor or not isinstance(anchor.get('backlog_rank'), (int, float)):
            raise SystemExit(f'#{args.after} нет в бэклоге или у него нет rank')
        following = [t['backlog_rank'] for t in ranked if t['backlog_rank'] > anchor['backlog_rank']]
        new_rank = (anchor['backlog_rank'] + min(following)) / 2 if following else anchor['backlog_rank'] + 1
    elif args.value is not None:
        new_rank = args.value
    else:
        raise SystemExit('Укажите --top / --bottom / --after <id> / --value <число>')

    result = client.board_update([{'task_id': args.task_id, 'backlog_rank': new_rank}])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    item = (result.get('items') or [{}])[0]
    print(f'#{item.get("task_id")} «{item.get("subject")}» → rank {item.get("backlog_rank")}')


def cmd_status(client, args):
    report_text = args.report or args.summary or ''
    if args.action == 'completed' and not report_text:
        raise SystemExit('Для сдачи задачи нужен отчёт о проделанной работе: --report "что сделано"')
    attach = getattr(args, 'attach', None)
    if attach and args.action not in STATUS_ACTIONS_WITH_FILES:
        # Роут читает файлы только у этих переходов — иначе они молча пропадут.
        raise SystemExit('Файлы принимают только переходы '
                         + ', '.join(STATUS_ACTIONS_WITH_FILES) + f' (передан {args.action})')
    files = upload_payload(attach)
    result = client.set_status(
        args.task_id,
        args.action,
        comment=args.comment or '',
        completion_summary=report_text,
        spent_minutes=parse_duration_to_minutes(args.spent) if args.spent else None,
        files=files,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f'#{args.task_id} → status={result.get("status") or result.get("task", {}).get("status") or args.action}')
    if files:
        # У completed файлы лягут как «результат», у returned/reopened — как «постановка»
        # (database.py: attachment_kind = 'result' только для completed).
        kind = 'результат' if args.action == 'completed' else 'постановка'
        print(f'  приложено файлов: {len(files)} (вид «{kind}»)')
    if result.get('report_id'):
        print(f'  итоговый отчёт #{result["report_id"]}, затрачено {format_minutes(result.get("spent_minutes"))}')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_report(client, args):
    result = client.add_report(
        args.task_id,
        args.body,
        spent_minutes=parse_duration_to_minutes(args.spent) if args.spent else None,
        kind='completion' if args.final else 'progress',
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    report = result.get('report') or {}
    kind_label = 'итоговый' if report.get('kind') == 'completion' else 'промежуточный'
    print(f'#{args.task_id}: добавлен {kind_label} отчёт #{report.get("id")}, '
          f'затрачено {format_minutes(report.get("spent_minutes"))}')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_clarify(client, args):
    """Уточнение по задаче: дополнение, запрос информации или ответ на запрос."""
    kind = 'request' if args.ask else ('answer' if args.answer else 'note')
    result = client.add_message(args.task_id, kind, args.body, files=upload_payload(args.attach))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    message = result.get('task_message') or {}
    print(f'#{args.task_id}: {result.get("message") or "уточнение добавлено"} '
          f'(#{message.get("id")}, {MESSAGE_KIND_LABELS.get(message.get("kind"), message.get("kind"))})')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_clarifications(client, args):
    payload = client.list_messages(args.task_id)
    messages = payload.get('messages') or []
    if args.json:
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return
    print(f'Уточнения по #{args.task_id}: {len(messages)}')
    _print_messages({'messages': messages}, indent='')


def cmd_unask(client, args):
    result = client.withdraw_info_request(args.message_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f'Запрос #{args.message_id} снят (задача #{result.get("task_id")})')


def cmd_reports(client, args):
    payload = client.list_reports(args.task_id)
    reports = payload.get('reports') or []
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return
    total = payload.get('spent_minutes')
    print(f'Отчёты по #{args.task_id}: {len(reports)}, затрачено всего {format_minutes(total)}')
    for report in reports:
        kind_label = 'ИТОГ' if report.get('kind') == 'completion' else '····'
        created = (report.get('created_at') or '')[:16].replace('T', ' ')
        spent = format_minutes(report.get('spent_minutes'))
        print(f'\n  [{kind_label}] #{report.get("id")}  {created}  {report.get("author_name") or "—"}  ({spent})')
        for line in str(report.get('body') or '').splitlines():
            print(f'         {line}')


def cmd_recipients(client, args):
    payload = client.recipients()
    people = payload.get('recipients') or []
    if args.json:
        print(json.dumps(people, ensure_ascii=False, indent=2))
        return
    print(f'Доступные исполнители: {len(people)}')
    for person in people:
        print(f'  {person.get("id"):>5}  {person.get("name", "")[:40]:<40} {person.get("role", "")}')


# ─────────────── Аргументы ───────────────

def build_parser():
    parser = argparse.ArgumentParser(description='Бэклог и канбан задач OTP через прод-API')
    parser.add_argument('--base-url', help=f'API (по умолчанию {DEFAULT_API_BASE_URL})')
    parser.add_argument('--login', help='логин (по умолчанию ADMIN_LOGIN)')
    parser.add_argument('--password', help='пароль (по умолчанию ADMIN_PASSWORD)')
    parser.add_argument('--json', action='store_true', help='сырой JSON вместо таблицы')
    sub = parser.add_subparsers(dest='command', required=True)

    board = sub.add_parser('board', help='канбан по колонкам')
    board.add_argument('--mine', action='store_true', help='только мои задачи')
    board.add_argument('--assignee', type=int, action='append',
                       help='фильтр по id исполнителя (можно повторять)')
    board.set_defaults(func=cmd_board)

    backlog = sub.add_parser('backlog', help='бэклог в порядке приоритета')
    backlog.set_defaults(func=cmd_backlog)

    show = sub.add_parser('show', help='карточка задачи целиком')
    show.add_argument('task_id', type=int)
    show.set_defaults(func=cmd_show)

    create = sub.add_parser('create', help='создать задачу (по умолчанию сразу на доску)')
    create.add_argument('subject')
    create.add_argument('--assignee', type=int, action='append',
                        help='id исполнителя (см. recipients); повторите флаг, чтобы поручить нескольким')
    create.add_argument('--self', dest='self_assign', action='store_true', help='поставить задачу себе')
    create.add_argument('--description', default='')
    create.add_argument('--tag', default='task', choices=('task', 'problem', 'suggestion'))
    create.add_argument('--priority', default='normal', choices=('normal', 'urgent', 'critical'))
    create.add_argument('--backlog', action='store_true', help='положить в бэклог, не уведомляя исполнителя')
    create.add_argument('--estimate', help='оценка: 90, 4h, 3d4h')
    create.add_argument('--due', help='абсолютный дедлайн: "2026-08-05 18:00"')
    create.add_argument('--in', dest='in_', help='дедлайн через: 3d4h')
    create.add_argument('--checklist', nargs='*', help='пункты чек-листа')
    create.add_argument('--from', dest='from_id', type=int, help='id того, кто поручил задачу')
    create.add_argument('--from-name', dest='from_name', help='кто поручил, если его нет в системе')
    create.add_argument('--remind', help='напомнить в Telegram до дедлайна: 1d, 3h, off (максимум сутки)')
    create.add_argument('--attach', nargs='+', metavar='ФАЙЛ',
                        help='приложить файлы постановки (до 10 штук, каждый до 10 МБ)')
    create.set_defaults(func=cmd_create)

    log = sub.add_parser('log', help='записать уже сделанную работу: создать себе задачу и сразу закрыть отчётом')
    log.add_argument('subject')
    log.add_argument('--report', required=True, help='отчёт о проделанной работе')
    log.add_argument('--spent', help='затрачено времени: 3h, 90m, 1d2h')
    log.add_argument('--description', default='')
    log.add_argument('--tag', default='task', choices=('task', 'problem', 'suggestion'))
    log.add_argument('--priority', default='normal', choices=('normal', 'urgent', 'critical'))
    log.add_argument('--estimate', help='оценка, если она была (иначе берётся равной факту)')
    log.add_argument('--progress', nargs='*', help='промежуточные отчёты по ходу работы')
    log.add_argument('--from', dest='from_id', type=int, help='id того, кто поручил')
    log.add_argument('--from-name', dest='from_name', help='кто поручил, если его нет в системе')
    log.add_argument('--keep-open', action='store_true', help='оставить на проверке, не принимать')
    log.add_argument('--attach', nargs='+', metavar='ФАЙЛ', help='приложить файлы (до 10 штук, до 10 МБ)')
    log.set_defaults(func=cmd_log)

    deadline = sub.add_parser('deadline', help='дедлайн / оценка / плановый старт')
    deadline.add_argument('task_id', type=int)
    deadline.add_argument('--due', help='абсолютный дедлайн')
    deadline.add_argument('--in', dest='in_', help='дедлайн через: 3d4h')
    deadline.add_argument('--clear', action='store_true', help='снять дедлайн')
    deadline.add_argument('--estimate', nargs='?', const='', help='оценка (пусто — сбросить)')
    deadline.add_argument('--planned-start', help='плановый старт для таймлайна')
    deadline.add_argument('--remind', help='напоминание в Telegram: 1d, 3h, off (максимум сутки)')
    deadline.set_defaults(func=cmd_deadline)

    promote = sub.add_parser('promote', help='из бэклога на доску (уведомит исполнителя)')
    promote.add_argument('task_id', type=int)
    promote.set_defaults(func=cmd_promote)

    park = sub.add_parser('park', help='вернуть не начатую задачу в бэклог')
    park.add_argument('task_id', type=int)
    park.set_defaults(func=cmd_park)

    rank = sub.add_parser('rank', help='переставить задачу в бэклоге')
    rank.add_argument('task_id', type=int)
    rank.add_argument('--top', action='store_true')
    rank.add_argument('--bottom', action='store_true')
    rank.add_argument('--after', type=int, help='поставить сразу после этой задачи')
    rank.add_argument('--value', type=float, help='точное значение rank')
    rank.set_defaults(func=cmd_rank)

    status = sub.add_parser('status', help='сменить статус задачи')
    status.add_argument('task_id', type=int)
    status.add_argument('action', choices=STATUS_ACTIONS)
    status.add_argument('--comment', default='')
    status.add_argument('--report', default='', help='отчёт о проделанной работе (обязателен для completed)')
    status.add_argument('--summary', default='', help='алиас --report')
    status.add_argument('--spent', help='затрачено времени: 3h, 90m, 1d2h')
    status.add_argument('--attach', nargs='+', metavar='ФАЙЛ',
                        help='приложить файлы; принимают только ' + ', '.join(STATUS_ACTIONS_WITH_FILES))
    status.set_defaults(func=cmd_status)

    report = sub.add_parser('report', help='добавить отчёт о проделанной работе')
    report.add_argument('task_id', type=int)
    report.add_argument('body', help='что сделано')
    report.add_argument('--spent', help='затрачено времени: 3h, 90m, 1d2h')
    report.add_argument('--final', action='store_true', help='итоговый отчёт (иначе промежуточный)')
    report.set_defaults(func=cmd_report)

    clarify = sub.add_parser('clarify', help='дополнить постановку, запросить информацию или ответить')
    clarify.add_argument('task_id', type=int)
    clarify.add_argument('body', help='текст уточнения')
    clarify.add_argument('--ask', action='store_true',
                         help='запрос информации (может только исполнитель задачи)')
    clarify.add_argument('--answer', action='store_true',
                         help='ответ на открытый запрос — он же его закрывает')
    clarify.add_argument('--attach', nargs='*', metavar='FILE', help='приложить файлы к уточнению')
    clarify.set_defaults(func=cmd_clarify)

    clarifications = sub.add_parser('clarifications', help='лента уточнений по задаче')
    clarifications.add_argument('task_id', type=int)
    clarifications.set_defaults(func=cmd_clarifications)

    unask = sub.add_parser('unask', help='снять свой запрос информации')
    unask.add_argument('message_id', type=int, help='id уточнения-запроса')
    unask.set_defaults(func=cmd_unask)

    reports = sub.add_parser('reports', help='журнал отчётов по задаче')
    reports.add_argument('task_id', type=int)
    reports.set_defaults(func=cmd_reports)

    recipients = sub.add_parser('recipients', help='кому можно ставить задачи')
    recipients.set_defaults(func=cmd_recipients)

    files = sub.add_parser('files', aliases=['attachments'],
                           help='вложения задачи: список, скачать, вынуть текст')
    files.add_argument('task_id', type=int)
    files.add_argument('--download', action='store_true', help='скачать файлы на диск')
    files.add_argument('--text', action='store_true', help='скачать и показать текст (docx/pdf/xlsx/pptx/csv/txt)')
    files.add_argument('--dir', help=f'куда сохранять (по умолчанию {DOWNLOAD_ROOT}/task_<id>)')
    files.add_argument('--only', type=int, nargs='+', metavar='ATT_ID', help='только эти вложения')
    files.add_argument('--kind', choices=('initial', 'result'),
                       help='initial — от постановщика, result — приложенное при сдаче')
    files.add_argument('--limit', type=int, default=6000,
                       help='сколько символов текста печатать (0 — целиком)')
    files.set_defaults(func=cmd_files)

    history = sub.add_parser('history', help='история статусов задачи с комментариями переходов')
    history.add_argument('task_id', type=int)
    history.set_defaults(func=cmd_history)

    checklist = sub.add_parser('checklist', help='чек-лист задачи')
    checklist.add_argument('task_id', type=int)
    checklist.set_defaults(func=cmd_checklist)

    check = sub.add_parser('check', help='отметить пункт чек-листа (уведомит участников в Telegram)')
    check.add_argument('task_id', type=int)
    check.add_argument('item_id', type=int, help='id пункта (см. checklist)')
    check.add_argument('--undone', action='store_true', help='снять отметку')
    check.add_argument('--note', help='результат по пункту (до 2000 символов)')
    check.set_defaults(func=cmd_check)

    inbox = sub.add_parser('inbox', help='что ждёт лично меня: просрочки, возвраты, приёмка')
    inbox.set_defaults(func=cmd_inbox)

    seen = sub.add_parser('seen', help='погасить уведомление по задаче (статус задачи не меняет)')
    seen.add_argument('task_id', type=int)
    seen.add_argument('--kind', choices=tuple(ACTION_KIND_LABELS), help='какую именно причину гасим')
    seen.set_defaults(func=cmd_seen)

    people = sub.add_parser('people', help='люди и отделы раздела')
    people.add_argument('--all', action='store_true', help='все сотрудники, а не только доступные исполнители')
    people.add_argument('--departments', action='store_true', help='отделы вместо людей')
    people.set_defaults(func=cmd_people)

    export = sub.add_parser('export', help='выгрузка задач в Excel (лист на колонку доски)')
    export.add_argument('--dir', help=f'куда сохранить (по умолчанию {DOWNLOAD_ROOT})')
    export.add_argument('--mine', choices=('any', 'assignee', 'creator'),
                        help='охват «мои»: я исполнитель / я постановщик')
    export.add_argument('--person', help='id сотрудника')
    # incoming/outgoing считаются относительно МЕНЯ: incoming — он поставил мне,
    # outgoing — я поставил ему. «Все задачи этого человека» — это any.
    export.add_argument('--person-scope', dest='person_scope',
                        choices=('any', 'incoming', 'outgoing'),
                        help='any — все его задачи; incoming — он мне; outgoing — я ему')
    export.add_argument('--department', help='id отдела (отдел задачи = отдел постановщика)')
    export.set_defaults(func=cmd_export)

    notes = sub.add_parser('notes', help='мои заметки (личные, чужие не видны)')
    notes.add_argument('--all', action='store_true', help='вместе с закрытыми')
    notes.set_defaults(func=cmd_notes)

    note = sub.add_parser('note', help='создать заметку')
    note.add_argument('body', nargs='?', default='', help='текст заметки')
    note.add_argument('--title', help='заголовок (до 160 символов)')
    note.add_argument('--priority', choices=('normal', 'urgent', 'critical'))
    note.add_argument('--due', help='срок: "2026-08-20 18:00"')
    note.add_argument('--in', dest='in_', help='срок через: 3d4h')
    note.add_argument('--remind', help='напомнить в Telegram: 1d, 3h, off (максимум сутки)')
    note.add_argument('--todo', dest='todo', action='store_true', help='это дело с галочкой, а не просто текст')
    note.set_defaults(func=cmd_note_add)

    note_set = sub.add_parser('note-set', help='изменить заметку / отметить сделанной')
    note_set.add_argument('note_id', type=int)
    note_set.add_argument('--title')
    note_set.add_argument('--body')
    note_set.add_argument('--priority', choices=('normal', 'urgent', 'critical'))
    note_set.add_argument('--due', help='срок: "2026-08-20 18:00"')
    note_set.add_argument('--in', dest='in_', help='срок через: 3d4h')
    note_set.add_argument('--clear-due', dest='clear_due', action='store_true', help='снять срок')
    note_set.add_argument('--remind', help='напоминание: 1d, 3h, off (максимум сутки)')
    note_set.add_argument('--todo', dest='todo', action='store_true', help='сделать делом с галочкой')
    note_set.add_argument('--done', action='store_true', help='отметить сделанной')
    note_set.add_argument('--undone', action='store_true', help='снять отметку')
    note_set.set_defaults(func=cmd_note_set)

    note_del = sub.add_parser('note-del', help='удалить заметку')
    note_del.add_argument('note_id', type=int)
    note_del.set_defaults(func=cmd_note_del)


    return parser


def main(argv=None):
    _load_env(os.path.join(ROOT, '.env.codex.local'))
    args = build_parser().parse_args(argv)
    client = TaskBoardClient(args.base_url, args.login, args.password).authenticate()
    args.func(client, args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
