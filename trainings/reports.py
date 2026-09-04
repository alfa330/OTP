# -*- coding: utf-8 -*-
"""Автоматическая отчётность по проведённым тренингам (задача #261).

Раз в день, раз в неделю и раз в месяц подписанный получатель получает в свой
Telegram сводку: по каким темам проводили занятия, кто проводил, сколько было
участников и кто именно. Постановка задачи (Omarova Aru): «дату тренинга, тему
тренинга, кто проводил, количество участников, список сотрудников с указанием
ФИО каждого участника».

Здесь только чистая логика: границы периода, выборка занятий, текст сводки и
книга Excel. Рассылку (кому и когда) гоняет планировщик в bot_schedule2 —
модуль ничего не знает ни про Telegram, ни про APScheduler, поэтому весь его
объём проверяется тестами без базы и без сети.

Ключевые решения
────────────────

**Что считать одним тренингом.** В `trainings` строка на КАЖДОГО участника:
провели тему десяти человекам — десять строк. Занятие собирается обратно по
ключу «дата + время начала + время конца + тема + кто записал». Именно так его
и заводят: форма раздела принимает список сотрудников и пишет им один интервал.
Группировать по одной дате без времени нельзя — два разных занятия одного
тренера по одной теме в один день слиплись бы в одно, и «количество участников»
перестало бы отвечать на вопрос «сколько людей было в аудитории».

**Кто проводил — это `trainings.created_by`.** Отдельного поля «тренер» у
занятия нет, и заводить его в рамках отчёта нельзя: 1898 исторических строк
остались бы без него. Тренинг записывает тот, кто его провёл, — это и есть
единственный имеющийся ответ, и в разделе показывается он же
(`created_by_name` в `/api/trainings`).

**Пустой день не рассылается, пустая неделя и месяц — рассылаются.**
Ежедневная сводка «за 03.09 тренингов не проводили» ушла бы 250 раз в год ни о
чём — это ровно тот информационный шум, которого в портале быть не должно.
А вот «за неделю не провели ни одного тренинга» — это уже факт, за которым
следят: неделя без обучения видна, только если о ней сказать.

**Область видимости — та же, что у остальных Telegram-отчётов портала**
(`get_admins_with_feedback_telegram_reports_enabled`,
`get_rate_change_report_recipients`): админ получает все отделы, глава отдела —
свои. Отдел занятия берётся по отделу УЧАСТНИКА (`users.department_id`) — тем
же выражением, которым раздел фильтрует `/api/trainings` для главы отдела,
иначе отчёт и экран показывали бы разное.

**Группа участника — на дату занятия, а не текущая.** Тот же LATERAL, что в
`/api/trainings`: 120 из 1898 строк принадлежат людям без открытого членства
(в основном уволенным), и по текущей группе они все свалились бы в «Без
группы», хотя на дату тренинга группа у них была.
"""

import calendar
from datetime import date as dt_date, timedelta
from io import BytesIO


# ── Периодичность ───────────────────────────────────────────────────────────

PERIODS = ('daily', 'weekly', 'monthly')

PERIOD_LABELS = {
    'daily': 'Ежедневно',
    'weekly': 'Еженедельно',
    'monthly': 'Ежемесячно',
}

# Как называется сам отчёт в заголовке письма.
PERIOD_TITLES = {
    'daily': 'Тренинги за день',
    'weekly': 'Тренинги за неделю',
    'monthly': 'Тренинги за месяц',
}

# Когда приходит — подпись для интерфейса. Держим рядом с расписанием джобов,
# чтобы обещание в окне настроек и CronTrigger не разъехались.
PERIOD_HINTS = {
    'daily': 'Каждое утро в 09:30 — за вчера. В день без занятий не приходит',
    'weekly': 'В понедельник в 09:35 — за прошлую неделю',
    'monthly': '1-го числа в 09:40 — за прошлый месяц',
}

# Пустой период: рассылать сводку «занятий не было» или промолчать.
# Ежедневная — молчит (иначе 250 писем в год ни о чём), недельная и месячная
# говорят: отсутствие обучения за неделю это тоже результат.
PERIOD_SENDS_WHEN_EMPTY = {
    'daily': False,
    'weekly': True,
    'monthly': True,
}

MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)

MONTHS_NOMINATIVE = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


def normalize_period(value):
    """Периодичность из запроса. None, если такой нет."""
    norm = str(value or '').strip().lower()
    aliases = {
        'day': 'daily', 'день': 'daily', 'ежедневно': 'daily',
        'week': 'weekly', 'неделя': 'weekly', 'еженедельно': 'weekly',
        'month': 'monthly', 'месяц': 'monthly', 'ежемесячно': 'monthly',
    }
    norm = aliases.get(norm, norm)
    return norm if norm in PERIODS else None


def period_bounds(period, today):
    """Границы отчётного периода и его человеческое название.

    Возвращает (date_from, date_to, label). Обе границы включительные —
    `training_date` это DATE, и BETWEEN по нему однозначен.

    Отчёт всегда про ЗАКРЫТЫЙ период: сводка «за день» приходит утром про
    вчера, «за неделю» в понедельник про прошлую неделю (пн–вс), «за месяц»
    1-го числа про прошлый месяц. Считать «с начала текущего» нельзя: тот же
    день попал бы в две рассылки, и суммы за месяц не сошлись бы с суммой
    недель.
    """
    if period == 'daily':
        day = today - timedelta(days=1)
        return day, day, format_day(day)

    if period == 'weekly':
        # Прошлая календарная неделя пн–вс. `weekday()` — 0 для понедельника,
        # поэтому начало текущей недели это today - weekday, а прошлой — ещё
        # минус семь дней. Запуск в понедельник даёт ровно предыдущую неделю,
        # запуск в любой другой день — тоже (джоба может отработать позже из-за
        # misfire_grace_time, и период от этого меняться не должен).
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        end = start + timedelta(days=6)
        return start, end, format_range(start, end)

    if period == 'monthly':
        first_of_this = today.replace(day=1)
        end = first_of_this - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, '%s %s' % (MONTHS_NOMINATIVE[start.month - 1], start.year)

    raise ValueError('Неизвестная периодичность: %r' % (period,))


def format_day(day):
    return '%d %s %d' % (day.day, MONTHS_GENITIVE[day.month - 1], day.year)


def format_range(start, end):
    """«28 августа — 3 сентября 2026», без повтора месяца и года."""
    if start == end:
        return format_day(start)
    if start.year != end.year:
        return '%s — %s' % (format_day(start), format_day(end))
    if start.month == end.month:
        return '%d—%d %s %d' % (start.day, end.day, MONTHS_GENITIVE[end.month - 1], end.year)
    return '%d %s — %d %s %d' % (
        start.day, MONTHS_GENITIVE[start.month - 1],
        end.day, MONTHS_GENITIVE[end.month - 1], end.year,
    )


def month_period_bounds(month):
    """Границы календарного месяца 'YYYY-MM' — для разовой отправки из окна
    настроек: «прислать за такой-то месяц»."""
    year_raw, _, month_raw = str(month or '').partition('-')
    year = int(year_raw)
    month_num = int(month_raw)
    if not 1 <= month_num <= 12:
        raise ValueError('Неверный месяц: %r' % (month,))
    start = dt_date(year, month_num, 1)
    end = dt_date(year, month_num, calendar.monthrange(year, month_num)[1])
    return start, end, '%s %s' % (MONTHS_NOMINATIVE[month_num - 1], year)


# ── Выборка занятий ─────────────────────────────────────────────────────────

_SESSIONS_SQL = """
    SELECT
        t.training_date,
        t.start_time,
        t.end_time,
        t.reason,
        t.topic_id,
        tp.title                AS topic_title,
        tp.kind                 AS topic_kind,
        t.count_in_hours,
        t.comment,
        t.created_by,
        COALESCE(cb.name, 'System') AS trainer_name,
        u.id                    AS operator_id,
        u.name                  AS operator_name,
        u.status                AS operator_status,
        u.department_id,
        d.name                  AS department_name,
        grp.name                AS group_name
      FROM trainings t
      JOIN users u        ON u.id = t.operator_id
      LEFT JOIN users cb  ON cb.id = t.created_by
      LEFT JOIN training_topics tp ON tp.id = t.topic_id
      LEFT JOIN departments d ON d.id = u.department_id
      -- Группа НА ДАТУ ЗАНЯТИЯ и ровно одна: у части людей два членства
      -- накрывают один день, и обычный JOIN раздвоил бы участника, завысив
      -- «количество участников».
      LEFT JOIN LATERAL (
          SELECT g.name
            FROM group_operator_memberships gom
            JOIN groups g ON g.id = gom.group_id
           WHERE gom.operator_id = t.operator_id
             AND gom.start_date <= t.training_date
             AND (gom.end_date IS NULL OR gom.end_date >= t.training_date)
           ORDER BY gom.start_date DESC, gom.id DESC
           LIMIT 1
      ) grp ON TRUE
     WHERE t.training_date >= %s
       AND t.training_date <= %s
       {dept_clause}
     ORDER BY t.training_date, t.start_time, t.end_time,
              COALESCE(tp.title, t.reason), t.created_by, u.name
"""


def fetch_sessions(cursor, date_from, date_to, department_ids=None):
    """Занятия периода, собранные из построчных записей `trainings`.

    department_ids: None — без границы (админ), иначе множество отделов главы.
    Пустое множество означает «отделов нет» и честно даёт пустой отчёт, а не
    молча весь портал.

    Возвращает список занятий, у каждого — список участников. Группировка
    делается здесь, а не в SQL с ARRAY_AGG: из одного плоского прохода
    собираются и лист занятий, и лист участников, и сводки, а объём смешной —
    за самый плотный месяц 328 строк.
    """
    if department_ids is None:
        dept_clause = ''
        params = [date_from, date_to]
    else:
        ids = sorted({int(value) for value in department_ids})
        if not ids:
            return []
        dept_clause = 'AND u.department_id = ANY(%s)'
        params = [date_from, date_to, ids]

    cursor.execute(_SESSIONS_SQL.format(dept_clause=dept_clause), params)

    sessions = []
    index = {}
    for row in cursor.fetchall():
        (training_date, start_time, end_time, reason, topic_id, topic_title,
         topic_kind, count_in_hours, comment, created_by, trainer_name,
         operator_id, operator_name, operator_status, department_id,
         department_name, group_name) = row

        key = (training_date, start_time, end_time, topic_id, reason, created_by)
        session = index.get(key)
        if session is None:
            session = {
                'date': training_date,
                'start_time': start_time,
                'end_time': end_time,
                'topic_id': topic_id,
                # Название темы: у корпоративной берём живое из справочника, у
                # базовой причины — сам reason. Переименование корпоративной
                # темы `reason` у прошлых записей не переписывает (так решено в
                # разделе), поэтому в отчёте показываем текущее название темы,
                # а не слепок на момент занятия.
                'title': topic_title or reason,
                'reason': reason,
                'is_corporate': topic_id is not None,
                'topic_kind': topic_kind,
                'count_in_hours': bool(count_in_hours),
                'comment': comment or '',
                'trainer_id': created_by,
                'trainer_name': trainer_name or 'System',
                'participants': [],
            }
            index[key] = session
            sessions.append(session)

        session['participants'].append({
            'id': operator_id,
            'name': operator_name or ('#%s' % operator_id),
            'status': operator_status,
            'department_id': department_id,
            'department_name': department_name or '',
            'group_name': group_name or '',
        })

    return sessions


# ── Сводка ──────────────────────────────────────────────────────────────────

def summarize(sessions):
    """Числа шапки отчёта и разбивки по темам и по проводившим."""
    people = set()
    by_topic = {}
    by_trainer = {}
    minutes_total = 0

    for session in sessions:
        head_count = len(session['participants'])
        for person in session['participants']:
            people.add(person['id'])

        minutes_total += session_minutes(session) * head_count

        topic = by_topic.setdefault(session['title'], {
            'title': session['title'],
            'is_corporate': session['is_corporate'],
            'sessions': 0,
            'participants': 0,
            'people': set(),
        })
        topic['sessions'] += 1
        topic['participants'] += head_count
        topic['people'].update(person['id'] for person in session['participants'])

        trainer = by_trainer.setdefault(session['trainer_name'], {
            'name': session['trainer_name'],
            'sessions': 0,
            'participants': 0,
        })
        trainer['sessions'] += 1
        trainer['participants'] += head_count

    topics = sorted(
        ({**item, 'people': len(item['people'])} for item in by_topic.values()),
        key=lambda item: (-item['participants'], item['title']),
    )
    trainers = sorted(
        by_trainer.values(),
        key=lambda item: (-item['participants'], item['name']),
    )

    return {
        'sessions': len(sessions),
        # «Участников» — суммарная посещаемость (один человек на трёх занятиях
        # это три участия), «сотрудников» — сколько разных людей охвачено.
        # Одно число вместо двух отвечало бы то на один вопрос, то на другой.
        'participations': sum(len(item['participants']) for item in sessions),
        'people': len(people),
        'minutes': minutes_total,
        'topics': topics,
        'trainers': trainers,
    }


def session_minutes(session):
    """Длительность занятия в минутах. 0, если времени нет или он «через
    полночь» — отрицательную длительность в отчёт пускать нельзя."""
    start = session.get('start_time')
    end = session.get('end_time')
    if start is None or end is None:
        return 0
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    return max(0, end_minutes - start_minutes)


def format_duration(minutes):
    total = int(minutes or 0)
    if total <= 0:
        return '0 мин'
    hours, mins = divmod(total, 60)
    if hours and mins:
        return '%d ч %02d мин' % (hours, mins)
    if hours:
        return '%d ч' % hours
    return '%d мин' % mins


def format_time_range(session):
    start = session.get('start_time')
    end = session.get('end_time')
    if start is None or end is None:
        return ''
    return '%02d:%02d–%02d:%02d' % (start.hour, start.minute, end.hour, end.minute)


def plural_ru(count, one, few, many):
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


# ── Текст сообщения ─────────────────────────────────────────────────────────

# Потолок одного сообщения Telegram — 4096 символов. Держим запас на служебный
# хвост («полный список — в файле») и на разметку.
TELEGRAM_TEXT_BUDGET = 3600

# Сколько ФИО показывать в самом сообщении. Дальше — «и ещё N», полный список
# всегда лежит в приложенной книге.
NAMES_INLINE_LIMIT = 12

# Сколько занятий расписывать поимённо. За месяц их под сотню — сообщение
# превратилось бы в простыню, а разбивка по темам отвечает на тот же вопрос
# короче.
SESSIONS_DETAILED_LIMIT = {'daily': 40, 'weekly': 25, 'monthly': 0}

# До этого числа занятий разбивки «по темам» и «кто проводил» не печатаются:
# поимённый список ниже уже рассказал всё то же самое, и повтор одного и того
# же в двух местах одного экрана — ровно тот информационный шум, которого в
# портале быть не должно. Порог маленький осознанно: с десятком занятий
# разбивка это уже сводка, которую читают первой, а не эхо списка.
SUMMARY_SKIP_MAX_SESSIONS = 5


def build_digest(period, period_label, sessions, scope_label, generated_label,
                 escape=None, attached=False):
    """Текст сообщения в Telegram (parse_mode=HTML).

    escape — функция экранирования (`_escape_telegram_html` монолита). Приходит
    аргументом, а не импортом: обратный импорт из bot_schedule2 был бы циклом,
    как и у остальных пакетов портала. Значение по умолчанию есть, но оно
    минимальное — в проде обязательно передавать монолитный хелпер.

    attached=True — к сообщению приложена книга Excel; тогда усечённые списки
    честно ссылаются на файл, а не обрываются молча.
    """
    esc = escape or _fallback_escape
    summary = summarize(sessions)

    title = PERIOD_TITLES.get(period, 'Тренинги')
    lines = [
        '🎓 <b>%s</b>' % esc(title),
        'Период: %s' % esc(period_label),
        'Область: %s' % esc(scope_label),
        'Сформирован: %s' % esc(generated_label),
    ]

    if not sessions:
        lines.append('')
        lines.append('За период занятий не проводили.')
        return '\n'.join(lines)

    detail_limit = SESSIONS_DETAILED_LIMIT.get(period, 0)
    # Все занятия попадут в сообщение поимённо — значит разбивки повторили бы
    # их своими словами. Бюджет символов при таком числе занятий не при чём:
    # пять занятий по десятку ФИО это полтысячи символов из четырёх тысяч.
    detailed_covers_everything = 0 < len(sessions) <= min(detail_limit, SUMMARY_SKIP_MAX_SESSIONS)

    lines.append('')
    head = 'Занятий: <b>%d</b> · участий: <b>%d</b>' % (
        summary['sessions'], summary['participations'])
    # «Сотрудников» — только когда оно отличается от участий. На дневной сводке
    # из двух занятий по одному человеку три одинаковых числа в строке
    # выглядели бы как ошибка, а не как сводка.
    if summary['people'] != summary['participations']:
        head += ' · сотрудников: <b>%d</b>' % summary['people']
    lines.append(head)
    if summary['minutes'] > 0:
        lines.append('Суммарно в часах: %s' % esc(format_duration(summary['minutes'])))

    if not detailed_covers_everything:
        # Разбивка по темам отвечает на «по каким темам проводили» и не растёт
        # от числа участников.
        lines.append('')
        lines.append('<b>По темам</b>')
        for topic in summary['topics'][:15]:
            mark = '🏷' if topic['is_corporate'] else '•'
            lines.append('%s %s — %d %s, %d %s' % (
                mark, esc(topic['title']),
                topic['sessions'], plural_ru(topic['sessions'], 'занятие', 'занятия', 'занятий'),
                topic['participants'], plural_ru(topic['participants'], 'участник', 'участника', 'участников'),
            ))
        hidden_topics = len(summary['topics']) - 15
        if hidden_topics > 0:
            lines.append('… и ещё %d %s' % (
                hidden_topics, plural_ru(hidden_topics, 'тема', 'темы', 'тем')))

        lines.append('')
        lines.append('<b>Кто проводил</b>')
        for trainer in summary['trainers'][:10]:
            lines.append('👤 %s — %d %s, %d %s' % (
                esc(trainer['name']),
                trainer['sessions'], plural_ru(trainer['sessions'], 'занятие', 'занятия', 'занятий'),
                trainer['participants'], plural_ru(trainer['participants'], 'участник', 'участника', 'участников'),
            ))
        hidden_trainers = len(summary['trainers']) - 10
        if hidden_trainers > 0:
            lines.append('… и ещё %d' % hidden_trainers)

    # Поимённая часть. Постановка требует «список сотрудников с указанием ФИО
    # каждого участника» — в сообщении он влезает у дневной и недельной сводки,
    # у месячной живёт только в книге.
    if detail_limit > 0:
        lines.append('')
        lines.append('<b>Занятия</b>')
        shown = 0
        for session in sessions:
            if shown >= detail_limit:
                break
            block = _session_block(session, esc)
            # Считаем бюджет ДО добавления: обрезанный посередине список ФИО
            # хуже честного «полный список — в файле».
            if _text_length(lines) + _text_length(block) > TELEGRAM_TEXT_BUDGET:
                break
            lines.extend(block)
            shown += 1
        if shown < len(sessions):
            rest = len(sessions) - shown
            lines.append('… и ещё %d %s%s' % (
                rest, plural_ru(rest, 'занятие', 'занятия', 'занятий'),
                ' — полный список в файле' if attached else '',
            ))
    elif attached:
        lines.append('')
        lines.append('Полный список занятий и ФИО участников — в приложенном файле.')

    return '\n'.join(lines)


def _session_block(session, esc):
    head_count = len(session['participants'])
    time_range = format_time_range(session)
    header = '📅 %s%s · %s' % (
        session['date'].strftime('%d.%m'),
        ' %s' % time_range if time_range else '',
        esc(session['title']),
    )
    block = [
        '',
        header,
        'Провёл: %s · участников: %d' % (esc(session['trainer_name']), head_count),
    ]
    names = [person['name'] for person in session['participants']]
    inline = names[:NAMES_INLINE_LIMIT]
    tail = len(names) - len(inline)
    listed = ', '.join(esc(name) for name in inline)
    if tail > 0:
        listed += ' и ещё %d' % tail
    block.append(listed)
    return block


def _text_length(lines):
    return sum(len(line) + 1 for line in lines)


def _fallback_escape(value):
    return (str(value or '')
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


# ── Книга Excel ─────────────────────────────────────────────────────────────

def report_filename(period, date_from, date_to):
    """Имя файла: по периоду видно, за что отчёт, без открытия книги."""
    if period == 'daily':
        return 'Trainings_day_%s.xlsx' % date_from.strftime('%Y%m%d')
    if period == 'monthly':
        return 'Trainings_month_%s.xlsx' % date_from.strftime('%Y%m')
    return 'Trainings_week_%s_%s.xlsx' % (
        date_from.strftime('%Y%m%d'), date_to.strftime('%Y%m%d'))


def build_xlsx(xlsxwriter, period, period_label, sessions, scope_label, generated_label):
    """Книга из трёх листов: «Занятия», «Участники», «Сводка».

    xlsxwriter приходит аргументом по той же причине, что и `escape`: пакет не
    тянет зависимости монолита сам, а тесты собирают книгу настоящим
    xlsxwriter'ом без запуска приложения.

    Два листа с занятиями и участниками — не дублирование, а два разных
    вопроса. «Занятия» отвечают ровно на постановку (дата, тема, кто проводил,
    сколько участников, их ФИО одной ячейкой). «Участники» — это плоская
    таблица по одной строке на человека: только по ней можно отфильтровать
    «кто из отдела X был на тренингах» или свести сводную.
    """
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    workbook.set_properties({
        'title': '%s — %s' % (PERIOD_TITLES.get(period, 'Тренинги'), period_label),
        'subject': 'Проведённые тренинги',
        'author': 'OTP',
        'company': 'OTP',
        'comments': 'Сформирован %s. Область: %s' % (generated_label, scope_label),
    })

    fmt = {
        'title': workbook.add_format({
            'bold': True, 'font_size': 15, 'font_color': '#FFFFFF',
            'bg_color': '#172554', 'valign': 'vcenter'}),
        'subtitle': workbook.add_format({
            'font_size': 10, 'font_color': '#CBD5E1',
            'bg_color': '#172554', 'valign': 'vcenter'}),
        'header': workbook.add_format({
            'bold': True, 'bg_color': '#2563EB', 'font_color': '#FFFFFF', 'border': 1,
            'align': 'center', 'valign': 'vcenter', 'text_wrap': True}),
        'cell': workbook.add_format({'border': 1, 'valign': 'top'}),
        'wrap': workbook.add_format({'border': 1, 'valign': 'top', 'text_wrap': True}),
        'center': workbook.add_format({'border': 1, 'align': 'center', 'valign': 'top'}),
        'num': workbook.add_format({
            'border': 1, 'align': 'center', 'valign': 'top', 'num_format': '0'}),
        'date': workbook.add_format({
            'border': 1, 'align': 'center', 'valign': 'top', 'num_format': 'dd.mm.yyyy'}),
        'section': workbook.add_format({
            'bold': True, 'font_size': 11, 'bg_color': '#F1F5F9', 'border': 1}),
        'kv_label': workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#F8FAFC'}),
        'kv_value': workbook.add_format({'border': 1}),
        'note': workbook.add_format({'font_size': 10, 'font_color': '#64748B'}),
    }

    summary = summarize(sessions)
    _sheet_sessions(workbook, fmt, period, period_label, scope_label, generated_label, sessions)
    _sheet_participants(workbook, fmt, sessions)
    _sheet_summary(workbook, fmt, period_label, scope_label, summary)

    workbook.close()
    output.seek(0)
    return output.getvalue()


def _write_banner(sheet, fmt, last_col, title, period_label, scope_label, generated_label):
    sheet.merge_range(0, 0, 0, last_col, title, fmt['title'])
    sheet.merge_range(1, 0, 1, last_col,
                      'Период: %s   ·   Область: %s   ·   Сформирован: %s'
                      % (period_label, scope_label, generated_label),
                      fmt['subtitle'])
    sheet.set_row(0, 26)
    sheet.set_row(1, 18)


def _sheet_sessions(workbook, fmt, period, period_label, scope_label, generated_label, sessions):
    sheet = workbook.add_worksheet('Занятия')
    headers = ['№', 'Дата', 'Время', 'Тема', 'Вид темы', 'Кто проводил',
               'Отдел', 'Участников', 'ФИО участников']
    _write_banner(sheet, fmt, len(headers) - 1,
                  PERIOD_TITLES.get(period, 'Тренинги'),
                  period_label, scope_label, generated_label)

    header_row = 3
    for col, header in enumerate(headers):
        sheet.write(header_row, col, header, fmt['header'])

    row = header_row + 1
    for index, session in enumerate(sessions, start=1):
        people = session['participants']
        departments = sorted({person['department_name'] for person in people if person['department_name']})
        sheet.write_number(row, 0, index, fmt['num'])
        sheet.write_datetime(row, 1, session['date'], fmt['date'])
        sheet.write(row, 2, format_time_range(session), fmt['center'])
        sheet.write(row, 3, session['title'], fmt['wrap'])
        sheet.write(row, 4, 'Корпоративная' if session['is_corporate'] else 'Базовая', fmt['center'])
        sheet.write(row, 5, session['trainer_name'], fmt['cell'])
        sheet.write(row, 6, ', '.join(departments), fmt['cell'])
        sheet.write_number(row, 7, len(people), fmt['num'])
        sheet.write(row, 8, ', '.join(person['name'] for person in people), fmt['wrap'])
        row += 1

    if not sessions:
        sheet.merge_range(row, 0, row, len(headers) - 1,
                          'За период занятий не проводили', fmt['cell'])
        row += 1

    # Ширины сняты по факту на боевых данных: «Пахриддинов Динмухамад
    # Тажиханулы» и «СЗоВ — Служба заботы о водителях» в 26 и 22 не влезали,
    # а обрезанное имя в отчёте читается как другое имя.
    for col, width in enumerate([5, 12, 13, 38, 15, 36, 36, 14, 70]):
        sheet.set_column(col, col, width)
    sheet.freeze_panes(header_row + 1, 0)
    if sessions:
        sheet.autofilter(header_row, 0, row - 1, len(headers) - 1)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.repeat_rows(header_row)


def _sheet_participants(workbook, fmt, sessions):
    """По одной строке на участника — для фильтров и сводных таблиц."""
    sheet = workbook.add_worksheet('Участники')
    headers = ['№', 'Дата', 'Время', 'Тема', 'Кто проводил',
               'ФИО участника', 'Отдел', 'Группа', 'Статус']
    for col, header in enumerate(headers):
        sheet.write(0, col, header, fmt['header'])

    row = 1
    index = 0
    for session in sessions:
        for person in session['participants']:
            index += 1
            sheet.write_number(row, 0, index, fmt['num'])
            sheet.write_datetime(row, 1, session['date'], fmt['date'])
            sheet.write(row, 2, format_time_range(session), fmt['center'])
            sheet.write(row, 3, session['title'], fmt['wrap'])
            sheet.write(row, 4, session['trainer_name'], fmt['cell'])
            sheet.write(row, 5, person['name'], fmt['cell'])
            sheet.write(row, 6, person['department_name'], fmt['cell'])
            sheet.write(row, 7, person['group_name'], fmt['cell'])
            sheet.write(row, 8, _status_label(person['status']), fmt['center'])
            row += 1

    if index == 0:
        sheet.merge_range(row, 0, row, len(headers) - 1,
                          'За период занятий не проводили', fmt['cell'])
        row += 1

    for col, width in enumerate([5, 12, 13, 34, 36, 30, 36, 22, 16]):
        sheet.set_column(col, col, width)
    sheet.freeze_panes(1, 0)
    if index:
        sheet.autofilter(0, 0, row - 1, len(headers) - 1)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.repeat_rows(0)


def _sheet_summary(workbook, fmt, period_label, scope_label, summary):
    sheet = workbook.add_worksheet('Сводка')
    state = {'row': 0}

    def kv(label, value):
        sheet.write(state['row'], 0, label, fmt['kv_label'])
        sheet.merge_range(state['row'], 1, state['row'], 3, value, fmt['kv_value'])
        state['row'] += 1

    sheet.merge_range(0, 0, 0, 3, 'Сводка за %s' % period_label, fmt['section'])
    state['row'] = 1
    kv('Область', scope_label)
    kv('Занятий', summary['sessions'])
    kv('Участий (посещений)', summary['participations'])
    kv('Разных сотрудников', summary['people'])
    kv('Суммарно в часах', format_duration(summary['minutes']))
    state['row'] += 1

    sheet.merge_range(state['row'], 0, state['row'], 3, 'По темам', fmt['section'])
    state['row'] += 1
    for col, header in enumerate(['Тема', 'Вид', 'Занятий', 'Участий']):
        sheet.write(state['row'], col, header, fmt['header'])
    state['row'] += 1
    for topic in summary['topics']:
        sheet.write(state['row'], 0, topic['title'], fmt['wrap'])
        sheet.write(state['row'], 1,
                    'Корпоративная' if topic['is_corporate'] else 'Базовая', fmt['center'])
        sheet.write_number(state['row'], 2, topic['sessions'], fmt['num'])
        sheet.write_number(state['row'], 3, topic['participants'], fmt['num'])
        state['row'] += 1
    state['row'] += 1

    sheet.merge_range(state['row'], 0, state['row'], 3, 'Кто проводил', fmt['section'])
    state['row'] += 1
    # Имя занимает A:B — у темы во втором столбце стоит её вид, а у тренера
    # такого признака нет, и пустая колонка оставляла в таблице дырку.
    sheet.merge_range(state['row'], 0, state['row'], 1, 'Сотрудник', fmt['header'])
    sheet.write(state['row'], 2, 'Занятий', fmt['header'])
    sheet.write(state['row'], 3, 'Участий', fmt['header'])
    state['row'] += 1
    for trainer in summary['trainers']:
        sheet.merge_range(state['row'], 0, state['row'], 1, trainer['name'], fmt['cell'])
        sheet.write_number(state['row'], 2, trainer['sessions'], fmt['num'])
        sheet.write_number(state['row'], 3, trainer['participants'], fmt['num'])
        state['row'] += 1

    state['row'] += 1
    sheet.merge_range(state['row'], 0, state['row'], 3,
                      '«Участий» — суммарная посещаемость: один сотрудник на трёх '
                      'занятиях даёт три участия.', fmt['note'])

    for col, width in enumerate([40, 16, 12, 12]):
        sheet.set_column(col, col, width)


def _status_label(status):
    return {
        'working': 'Работает',
        'fired': 'Уволен',
        'bs': 'Без сохранения',
    }.get(str(status or '').strip().lower(), str(status or ''))
