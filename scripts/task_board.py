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
import json
import os
import re
import sys
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_API_BASE_URL = 'https://otp-2-fos4.onrender.com'

COLUMN_TITLES = [
    ('backlog', 'Бэклог'),
    ('todo', 'К выполнению'),
    ('progress', 'В работе'),
    ('review', 'На проверке'),
    ('done', 'Готово'),
]
STATUS_ACTIONS = ('in_progress', 'completed', 'accepted', 'returned', 'reopened')


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

    def list_tasks(self, **params):
        clean = {key: value for key, value in params.items() if value not in (None, '')}
        return self._request('GET', '/api/tasks', params=clean)

    def recipients(self):
        return self._request('GET', '/api/tasks/recipients')

    def create_task(self, fields):
        return self._request('POST', '/api/tasks', data=fields)

    def patch_task(self, task_id, payload):
        return self._request('PATCH', f'/api/tasks/{int(task_id)}', json=payload)

    def board_update(self, items):
        return self._request('POST', '/api/tasks/board', json={'items': items})

    def set_status(self, task_id, action, comment='', completion_summary='', spent_minutes=None):
        payload = {'action': action, 'comment': comment}
        if completion_summary:
            payload['completion_summary'] = completion_summary
        if spent_minutes:
            payload['spent_minutes'] = int(spent_minutes)
        return self._request('POST', f'/api/tasks/{int(task_id)}/status', json=payload)

    def list_reports(self, task_id):
        return self._request('GET', f'/api/tasks/{int(task_id)}/reports')

    def add_report(self, task_id, body, spent_minutes=None, kind='progress'):
        payload = {'body': body, 'kind': kind}
        if spent_minutes:
            payload['spent_minutes'] = int(spent_minutes)
        return self._request('POST', f'/api/tasks/{int(task_id)}/reports', json=payload)

    def delete_report(self, report_id):
        return self._request('DELETE', f'/api/tasks/reports/{int(report_id)}')


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


def task_line(task, now=None):
    parts = [
        f'#{task["id"]:<5}',
        f'{(task.get("subject") or "")[:56]:<56}',
        f'{(task.get("assignee") or {}).get("name", "—")[:20]:<20}',
        f'{task.get("priority", "normal")[:8]:<8}',
        f'оц {format_minutes(task.get("estimate_minutes")):<7}',
        f'срок {format_due(task, now)}',
    ]
    return '  '.join(parts)


# ─────────────── Команды ───────────────

def cmd_board(client, args):
    payload = client.list_tasks(only_my='1' if args.mine else None)
    tasks = payload.get('tasks') or []
    if args.assignee:
        tasks = [t for t in tasks if int((t.get('assignee') or {}).get('id') or 0) == args.assignee]
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


def cmd_show(client, args):
    payload = client.list_tasks()
    task = next((t for t in (payload.get('tasks') or []) if int(t['id']) == args.task_id), None)
    if not task:
        raise SystemExit(f'Задача #{args.task_id} не найдена (или недоступна этому пользователю)')
    if args.json:
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return
    print(f'#{task["id"]} · {task.get("subject")}')
    print(f'  колонка       {dict(COLUMN_TITLES)[column_of(task)]} (status={task.get("status")}, is_backlog={task.get("is_backlog")})')
    print(f'  исполнитель   {(task.get("assignee") or {}).get("name", "—")}')
    print(f'  постановщик   {(task.get("creator") or {}).get("name", "—")}')
    print(f'  срочность     {task.get("priority")}')
    origin = task.get('requested_by') or {}
    origin_label = origin.get('name') or (
        'своя инициатива'
        if (task.get('creator') or {}).get('id') == (task.get('assignee') or {}).get('id')
        else '—'
    )
    print(f'  поручил       {origin_label}')
    print(f'  оценка        {format_minutes(task.get("estimate_minutes"))}')
    print(f'  затрачено     {format_minutes(task.get("spent_minutes"))} (по отчётам)')
    print(f'  план старта   {task.get("planned_start_at") or "—"}')
    print(f'  начато        {task.get("started_at") or "—"}')
    print(f'  дедлайн       {format_due(task)}')
    remind = task.get('reminder_minutes_before')
    sent = task.get('reminder_sent_at')
    print(f'  напоминание   ' + (f'за {format_minutes(remind)} до дедлайна'
                                 + (f' (отправлено {sent[:16]})' if sent else '') if remind else '—'))
    print(f'  rank          {task.get("backlog_rank")}')
    if task.get('description'):
        print(f'  описание      {task["description"][:400]}')
    checklist = task.get('checklist') or []
    if checklist:
        print('  чек-лист:')
        for item in checklist:
            mark = 'x' if item.get('is_done') else ' '
            print(f'    [{mark}] {item.get("title")}')
    reports = task.get('reports') or []
    if reports:
        print('  отчёты о работе:')
        for report in reports:
            kind_label = 'ИТОГ' if report.get('kind') == 'completion' else '····'
            created = (report.get('created_at') or '')[:16].replace('T', ' ')
            print(f'    [{kind_label}] {created}  {report.get("author_name") or "—"}  '
                  f'({format_minutes(report.get("spent_minutes"))})')
            for line in str(report.get('body') or '').splitlines():
                print(f'           {line}')
    history = task.get('history') or []
    if history:
        print('  история:')
        for item in history:
            print(f'    {item.get("changed_at")}  {item.get("status_code"):<12} {item.get("changed_by_name") or "—"}')


def _build_create_fields(client, args, assignee_id):
    fields = {
        'subject': args.subject,
        'description': getattr(args, 'description', '') or '',
        'tag': getattr(args, 'tag', 'task'),
        'priority': getattr(args, 'priority', 'normal'),
        'assigned_to': str(assignee_id),
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


def _resolve_assignee(client, args):
    """--self ставит задачу на текущего пользователя, иначе нужен --assignee."""
    if getattr(args, 'self_assign', False):
        return client.user_id
    if getattr(args, 'assignee', None):
        return args.assignee
    raise SystemExit('Укажите исполнителя: --assignee <id> или --self (себе)')


def _origin_label(args):
    if getattr(args, 'from_id', None):
        return f'поручил id {args.from_id}'
    if getattr(args, 'from_name', None):
        return f'поручил: {args.from_name}'
    return 'своя инициатива'


def cmd_create(client, args):
    assignee_id = _resolve_assignee(client, args)
    result = client.create_task(_build_create_fields(client, args, assignee_id))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    where = 'бэклог' if args.backlog else 'доска'
    print(f'Создана задача #{result.get("task_id")} → {where}; {_origin_label(args)}; '
          f'дедлайн {result.get("due_at") or "—"}')
    if result.get('warning'):
        print(f'warning: {result["warning"]}')


def cmd_log(client, args):
    """
    Записать уже сделанную работу одной командой: создать задачу на себя,
    провести её через работу и закрыть итоговым отчётом с трудозатратами.
    """
    assignee_id = client.user_id
    fields = _build_create_fields(client, args, assignee_id)
    fields['assigned_to'] = str(assignee_id)
    fields['is_backlog'] = '0'
    if args.spent and 'estimate_minutes' not in fields:
        # Оценки не было — считаем, что она равна факту, чтобы метрика «факт к оценке» не врала.
        fields['estimate_minutes'] = str(parse_duration_to_minutes(args.spent))

    created = client.create_task(fields)
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
    result = client.set_status(
        args.task_id,
        args.action,
        comment=args.comment or '',
        completion_summary=report_text,
        spent_minutes=parse_duration_to_minutes(args.spent) if args.spent else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f'#{args.task_id} → status={result.get("status") or result.get("task", {}).get("status") or args.action}')
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
    board.add_argument('--assignee', type=int, help='фильтр по id исполнителя')
    board.set_defaults(func=cmd_board)

    backlog = sub.add_parser('backlog', help='бэклог в порядке приоритета')
    backlog.set_defaults(func=cmd_backlog)

    show = sub.add_parser('show', help='карточка задачи целиком')
    show.add_argument('task_id', type=int)
    show.set_defaults(func=cmd_show)

    create = sub.add_parser('create', help='создать задачу (по умолчанию сразу на доску)')
    create.add_argument('subject')
    create.add_argument('--assignee', type=int, help='id исполнителя (см. recipients)')
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
    status.set_defaults(func=cmd_status)

    report = sub.add_parser('report', help='добавить отчёт о проделанной работе')
    report.add_argument('task_id', type=int)
    report.add_argument('body', help='что сделано')
    report.add_argument('--spent', help='затрачено времени: 3h, 90m, 1d2h')
    report.add_argument('--final', action='store_true', help='итоговый отчёт (иначе промежуточный)')
    report.set_defaults(func=cmd_report)

    reports = sub.add_parser('reports', help='журнал отчётов по задаче')
    reports.add_argument('task_id', type=int)
    reports.set_defaults(func=cmd_reports)

    recipients = sub.add_parser('recipients', help='кому можно ставить задачи')
    recipients.set_defaults(func=cmd_recipients)

    return parser


def main(argv=None):
    _load_env(os.path.join(ROOT, '.env.codex.local'))
    args = build_parser().parse_args(argv)
    client = TaskBoardClient(args.base_url, args.login, args.password).authenticate()
    args.func(client, args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
