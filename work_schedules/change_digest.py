# -*- coding: utf-8 -*-
"""Сводка изменений графика работы за сутки — «Уведомления об изменениях».

Постановка (владелец): в разделе «Графики работы» в меню «3 точки» —
переключатель, по которому в Telegram приходит сводка за день. Главам отделов —
только по своему отделу. И отдельным требованием: «нельзя, чтобы это выглядело
как спам».

Здесь только чистая логика: границы суток, правило первичного внесения,
восстановление дня «было / стало» и вёрстка. Кому и когда отправлять — в
bot_schedule2, данные — в database.py. Модуль не знает ни про Telegram, ни про
базу, поэтому весь он проверяется тестами без сети и без подключения.

Ключевые решения
────────────────

**Формат — одна таблица «Сотрудник · Группа · Было · Стало · Кто изменил».**
Макет владельца от 17.09.2026 (без столбца «Причина»: причину правки портал не
хранит; «СВ» заменён на «Кто изменил» — в журнале записан тот, кто правил, а не
супервайзер группы). Аналитические блоки прежней версии («кто сколько менял»,
«дни графика») сняты вместе с ним.

**Строка — сотрудник + день графика, итог за сутки.** Руководитель, трижды
двигавший одну смену, дал бы три строки подряд, из которых важна только
разница между первой и последней. Поэтому «Было» — день до первой правки за
сутки, «Стало» — после последней, в «Кто изменил» — все авторы по порядку.
Если к вечеру день вернули как был, строки нет: изменения по итогу не
случилось.

**«Было» и «Стало» — весь день, а не изменённые строки журнала.** Журнал пишет
только отличия: сняли одну смену из двух — в журнале одна строка 'removed', и
«Стало: нет смены» было бы неправдой (на бою в сентябре 72 дня из 1903 — с
двумя-тремя сменами). Полное состояние восстанавливается откатом: текущий
график минус все правки, внесённые после. На боевом журнале 24.08–17.09.2026
откат сошёлся на 5631 операции из 5635; где не сходится (правка мимо журнала),
строка честно показывает только изменённые смены.

**Первичное внесение графика — не изменение.** Постановка владельца
(17.09.2026). Журнал пишет дифф дня, поэтому заполнение пустого дня ложилось в
сводку наравне с правками — на боевых данных 24.08–17.09.2026 это 3549
«правок» из 5635. Внесением считается операция, которая заполнила день, где до
неё не было ни смены, ни выходного, и сделана способом, которым график вносят:
вручную, загрузкой файла или публикацией аукциона. Обмены, доборы, выдача с
аукциона и заявки операторов правят уже внесённый график, даже когда смена
ложится на пустой день: добор почти всегда именно так и выглядит. Пустоту дня
до операции журнал хранит колонкой `day_was_empty`: по одним действиям её не
определить — вторая смена в заполненный день пишется тем же 'added' (на бою
40 таких правок). Для строк, записанных до колонки, решают действия.
Исключение — перенос: одна операция сняла смену с одного дня и поставила на
другой, пустой. Второй день — половина переноса, а не внесение.

**Сутки — закрытые, вчерашние.** Сводка уходит утром про вчера. «С начала
сегодняшнего дня» означало бы, что вечерние правки не попадут никуда, а
граница суток зависела бы от минуты запуска.

**Таблица — rich-сообщением.** Сводка уходит методом `sendRichMessage`
(Bot API 10.1) в разметке HTML. HTML, а не Markdown: фамилия с «|» разломала
бы Markdown-таблицу, а экранирование HTML у бота уже есть. Текст
(`build_digest`) остаётся запасным: если Telegram rich-сообщение не примет,
сводка уйдёт им, а не пропадёт.

**Чего сводка не покрывает.** Пересчёт перерывов и снятие статус-периода
историю не пишут сознательно (см. `database.py`), поэтому нигде не сказано
«все изменения графика» — только «изменения», как их видит журнал.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone


# ── Источники правок ────────────────────────────────────────────────────────

# Способ, которым правка попала в график, — в скобках после автора. Коды — те
# же, что в database.WORK_SHIFT_CHANGE_SOURCES; подписи повторяют формулировки
# экрана истории (src/components/schedule/shiftHistoryFormat.js), кроме
# статуса: «Кастек Гаухар (статусом)» не читается.
SOURCE_LABELS = {
    'supervisor': 'вручную',
    'import': 'загрузка из файла',
    'auction': 'публикация аукциона',
    'auction_admin': 'выдача с аукциона',
    'status_period': 'период статуса',
    'shift_request': 'заявка оператора',
    'swap': 'обмен сменами',
    'auction_topup': 'добор с аукциона',
    'auction_topup_cancel': 'отмена добора',
    'system': 'системой',
}

# Ручная правка — способ по умолчанию, в «Кто изменил» он не подписывается:
# «(вручную)» у каждой второй строки — шум.
MANUAL_SOURCE = 'supervisor'

# Способы, которыми график ВНОСЯТ. Заполнение пустого дня одним из них —
# первичное внесение, в сводку оно не идёт (см. докстроку модуля). Незнакомый
# код сюда не попадает: спрятать правку молча хуже, чем показать лишнюю.
ENTRY_SOURCES = ('supervisor', 'import', 'auction')

# Действия, которые возможны на пустом дне. Для строк журнала без колонки
# day_was_empty операция из одних таких действий считается заполнением.
FILL_ACTIONS = ('added', 'day_off_set')

# Вид смены в скобках после времени. Обычная смена не подписывается. Подписи —
# как в планировщике (PLANNER_SHIFT_TYPE_*_LABEL в src/App.jsx).
SHIFT_TYPE_REGULAR = 'regular'
SHIFT_TYPE_LABELS = {
    'office_practice': 'практика в офисе',
    'phone_shift': 'смена на телефонах',
}


# ── Расписание ──────────────────────────────────────────────────────────────

# Когда уходит сводка. Час и минута названы здесь, а не только в расписании,
# потому что окно настроек обещает получателю ровно это время: разойдясь,
# подпись и джоба сделали бы из обещания неправду. Тест сверяет их с
# CronTrigger в bot_schedule2.
SEND_HOUR = 9
SEND_MINUTE = 45

REPORT_HINT = (
    'Каждое утро в 09:45 — за прошедшие сутки. '
    'В день без изменений сводка не приходит.'
)


# ── Границы суток ───────────────────────────────────────────────────────────

def day_window(day):
    """Полуинтервал [00:00 дня, 00:00 следующего) naive-датами Алматы.

    `changed_at` — TIMESTAMP без часового пояса, куда пишется уже алматинское
    время (DEFAULT `CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'`), а сессия
    Postgres на Render живёт в UTC. Поэтому границы считаются здесь, в Python,
    и уходят в запрос параметрами: `changed_at::date = CURRENT_DATE` сдвинул бы
    сутки на пять часов. Интервал полуоткрытый — BETWEEN захватил бы ровно
    полночь следующих суток и продублировал бы правку в двух сводках.
    """
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def previous_day(now_dt):
    """Отчётные сутки для запуска в момент `now_dt` — всегда вчерашние."""
    today = now_dt.date() if hasattr(now_dt, 'date') else now_dt
    return today - timedelta(days=1)


def almaty_now():
    """Текущее время Алматы — сдвигом, а не ZoneInfo, как в cdr/queries.py.

    У Казахстана с 01.03.2024 одна зона UTC+5 без перевода часов. Откат на
    `datetime.now()` при недоступном tzdata отдал бы время контейнера, то есть UTC,
    и дата в заголовке сводки разошлась бы с часами получателя.
    """
    return datetime.now(timezone(timedelta(hours=5)))


# ── Форматирование ──────────────────────────────────────────────────────────

def plural_ru(count, one, few, many):
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def changes_word(count):
    return plural_ru(count, 'изменение', 'изменения', 'изменений')


def people_word(count):
    """Форма после предлога «у»: «у 21 сотрудника», «у 3 сотрудников»."""
    return plural_ru(count, 'сотрудника', 'сотрудников', 'сотрудников')


def format_date(day):
    """«16.09.2026» — дата в заголовке, как в макете."""
    return '%02d.%02d.%d' % (day.day, day.month, day.year)


def format_day_short(day):
    """«18.09» — день графика в ячейке: год там всегда очевиден."""
    return '%02d.%02d' % (day.day, day.month)


def _time_label(value):
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%H:%M')
    return str(value)[:5]


def _shift_type(value):
    return str(value or '').strip().lower() or SHIFT_TYPE_REGULAR


def _shift_item(start, end, shift_type):
    return (_time_label(start), _time_label(end), _shift_type(shift_type))


def shift_label(item):
    """«09:00–18:00» или «09:00–18:00 (практика в офисе)»."""
    start, end, shift_type = item
    text = '%s–%s' % (start, end)
    if shift_type != SHIFT_TYPE_REGULAR:
        text += ' (%s)' % SHIFT_TYPE_LABELS.get(shift_type, shift_type)
    return text


def day_state_label(day, shifts, day_off):
    """Ячейка «Было» / «Стало»: «18.09, 09:00–18:00», «18.09 — выходной»,
    «18.09 — нет смены». Смены — по времени начала."""
    parts = [shift_label(item) for item in sorted(shifts)]
    if parts:
        # Смена и выходной в одном дне схемой не запрещены — показываем оба.
        if day_off:
            parts.append('выходной')
        return '%s, %s' % (format_day_short(day), ', '.join(parts))
    if day_off:
        return '%s — выходной' % format_day_short(day)
    return '%s — нет смены' % format_day_short(day)


def empty_day_text(first_entries=None):
    """Строка для суток без изменений — её видит только разовая отправка по кнопке.

    Если за сутки график только вносили, «никто не менял» звучало бы как сбой
    сводки у человека, который вчера загрузил файл. Поэтому внесение названо.
    """
    days = {(entry.get('operator_id'), entry.get('shift_date')) for entry in first_entries or []}
    if not days:
        return 'За эти сутки график никто не менял.'
    people = len({operator_id for operator_id, _ in days})
    return ('Изменений не было. Первичное внесение графика — %d %s у %d %s — '
            'в сводку не входит.' % (
                len(days), plural_ru(len(days), 'день', 'дня', 'дней'),
                people, people_word(people),
            ))


# ── Первичное внесение ──────────────────────────────────────────────────────

def _change_key(entry):
    """Правка — сотрудник + день графика + операция (автор, changed_at).

    `changed_at` не передаётся в INSERT и приходит из DEFAULT — это начало
    транзакции, у всех строк одной операции совпадает до микросекунды. Автор в
    ключе — чтобы два человека в одну микросекунду не слиплись в одну правку.
    """
    actor_id = entry.get('actor_id')
    return (entry.get('operator_id'), entry.get('shift_date'),
            actor_id if actor_id is not None else -1, entry.get('changed_at'))


def _is_fill(rows):
    """Строки ОДНОЙ правки заполнили пустой день способом внесения графика.

    Все три условия обязательны. Действия проверяются и при известном
    day_was_empty: если одна транзакция дважды снимала дифф одного дня
    (сначала сняли смену, потом поставили выходной), у второй пачки флаг
    честный True, но правка в целом — не внесение.
    """
    if any(str(row.get('source') or '').strip() not in ENTRY_SOURCES for row in rows):
        return False
    if any(row.get('action') not in FILL_ACTIONS for row in rows):
        return False
    return not any(row.get('day_was_empty') is False for row in rows)


def split_first_entries(entries):
    """(правки, первичное внесение) — записи журнала, разведённые по правкам.

    Порядок записей внутри каждой половины сохраняется. Правка уходит в одну
    половину целиком: разрывать её строки значило бы посчитать одно действие
    человека и там, и там.
    """
    groups = {}
    for entry in entries or []:
        groups.setdefault(_change_key(entry), []).append(entry)
    fill_keys = {key for key, rows in groups.items() if _is_fill(rows)}

    # Перенос: одна операция по сотруднику тронула ровно два дня — один
    # правкой, другой заполнением. Заполненный день — вторая половина
    # переноса, и без неё в сводке осталась бы только снятая смена.
    days_by_operation = {}
    for operator_id, shift_date, actor_id, changed_at in groups:
        days_by_operation.setdefault((operator_id, actor_id, changed_at), []).append(shift_date)
    for (operator_id, actor_id, changed_at), days in days_by_operation.items():
        if len(days) != 2:
            continue
        keys = [(operator_id, day, actor_id, changed_at) for day in days]
        if sum(1 for key in keys if key in fill_keys) == 1:
            fill_keys.difference_update(keys)

    changes = []
    first = []
    for entry in entries or []:
        (first if _change_key(entry) in fill_keys else changes).append(entry)
    return changes, first


# ── Было / стало ────────────────────────────────────────────────────────────

def _entry_order(entry):
    # Само время, а не его строка: у времени без микросекунд str() короче, и
    # строковое сравнение разошлось бы с хронологией.
    return (entry.get('changed_at') or datetime.min, entry.get('id') or 0)


def _undo(state, entry):
    """Откатить одну строку журнала на состоянии дня. False — не сошлось:
    в дне нет смены, которую строка называет добавленной."""
    shifts = state['shifts']
    action = entry.get('action')
    new_item = _shift_item(entry.get('start'), entry.get('end'), entry.get('shift_type'))
    # Для 'changed' вид «до» пишется всегда; подстраховка на случай его пустоты.
    prev_type = entry.get('prev_shift_type') or entry.get('shift_type')
    prev_item = _shift_item(entry.get('prev_start'), entry.get('prev_end'), prev_type)
    consistent = True
    if action in ('added', 'changed'):
        if shifts[new_item] > 0:
            shifts[new_item] -= 1
        else:
            consistent = False
    if action in ('removed', 'changed'):
        shifts[prev_item] += 1
    if action == 'day_off_set':
        consistent = consistent and state['day_off']
        state['day_off'] = False
    if action == 'day_off_cleared':
        consistent = consistent and not state['day_off']
        state['day_off'] = True
    return consistent


def _frozen(state):
    shifts = []
    for item, count in state['shifts'].items():
        shifts.extend([item] * max(0, count))
    return tuple(sorted(shifts)), bool(state['day_off'])


def reconstruct_days(entries, current_states, later_entries=None):
    """Состояние дня до и после каждой правки — откатом журнала от текущего графика.

    entries — строки окна сводки, later_entries — строки тех же дней,
    внесённые после окна, current_states — {(operator_id, shift_date):
    {'shifts': [(start, end, shift_type)], 'day_off': bool}} на момент запроса.
    Возвращает {ключ правки: (до, после, сошлось ли)}, где состояние —
    (кортеж смен, выходной).

    Откат идёт от новых правок к старым: «после» правки — состояние перед её
    откатом, «до» — после. Не сошлось однажды — всё, что раньше в этом дне,
    помечено несошедшимся: оно выведено из уже неверного состояния.
    """
    by_day = {}
    for entry in list(entries or []) + list(later_entries or []):
        by_day.setdefault((entry.get('operator_id'), entry.get('shift_date')), []).append(entry)

    result = {}
    for day_key, day_entries in by_day.items():
        current = (current_states or {}).get(day_key) or {}
        state = {
            'shifts': Counter(_shift_item(*item) for item in current.get('shifts') or []),
            'day_off': bool(current.get('day_off')),
        }
        operations = {}
        for entry in sorted(day_entries, key=_entry_order):
            operations.setdefault(_change_key(entry), []).append(entry)
        consistent = True
        for key in reversed(list(operations)):
            after = _frozen(state)
            for entry in sorted(operations[key], key=_entry_order, reverse=True):
                consistent = _undo(state, entry) and consistent
            result[key] = (_frozen(state), after, consistent)
    return result


def _journal_parts(rows, side):
    """Запасная ячейка, когда откат не сошёлся: только смены из строк журнала."""
    shifts = []
    day_off = False
    for row in rows:
        action = row.get('action')
        if side == 'before':
            if action in ('removed', 'changed'):
                shifts.append(_shift_item(row.get('prev_start'), row.get('prev_end'),
                                          row.get('prev_shift_type') or row.get('shift_type')))
            day_off = day_off or action == 'day_off_cleared'
        else:
            if action in ('added', 'changed'):
                shifts.append(_shift_item(row.get('start'), row.get('end'), row.get('shift_type')))
            day_off = day_off or action == 'day_off_set'
    return tuple(sorted(shifts)), day_off


def _journal_parts_label(day, shifts, day_off):
    """Как day_state_label, но пустая сторона — просто дата: что ещё было в
    дне, при несошедшемся откате неизвестно, и «нет смены» было бы догадкой."""
    if not shifts and not day_off:
        return format_day_short(day)
    return day_state_label(day, shifts, day_off)


def _actors_label(operations):
    """«Кастек Гаухар (загрузка из файла), Сабыр Азана» — авторы по порядку.

    Ручная правка не подписывается, остальные способы — в скобках: по ним
    видно, что смену взял сам оператор с аукциона или отдал в обмен.
    """
    order = []
    ways = {}
    for rows in operations:
        first = rows[0]
        name = str(first.get('actor_name') or '').strip() or 'Без автора'
        if name not in ways:
            order.append(name)
            ways[name] = []
        source = str(first.get('source') or 'system').strip() or 'system'
        label = SOURCE_LABELS.get(source, source)
        if label not in ways[name]:
            ways[name].append(label)
    parts = []
    for name in order:
        labels = ways[name]
        if labels == [SOURCE_LABELS[MANUAL_SOURCE]]:
            parts.append(name)
        else:
            parts.append('%s (%s)' % (name, ', '.join(labels)))
    return ', '.join(parts)


def build_rows(entries, current_states=None, later_entries=None):
    """(строки таблицы, отсечённое первичное внесение).

    Строка — сотрудник + день графика: «Было» до первой правки за сутки,
    «Стало» после последней. Первичное внесение отсекается только В НАЧАЛЕ
    дня: если день сперва заполнили, а потом подвинули, «Было» — заполненный
    день, а не пустой. Строки, где к концу суток день вернули как был,
    выпадают.
    """
    changes, first_entries = split_first_entries(entries)
    fill_keys = {_change_key(entry) for entry in first_entries}
    states = reconstruct_days(entries, current_states, later_entries)

    operations_by_day = {}
    for entry in sorted(entries or [], key=_entry_order):
        day_key = (entry.get('operator_id'), entry.get('shift_date'))
        operations = operations_by_day.setdefault(day_key, {})
        operations.setdefault(_change_key(entry), []).append(entry)

    rows = []
    dropped_fills = []
    for (operator_id, shift_date), operations in operations_by_day.items():
        keys = list(operations)
        leading = 0
        while leading < len(keys) and keys[leading] in fill_keys:
            leading += 1
        for key in keys[:leading]:
            dropped_fills.extend(operations[key])
        kept = keys[leading:]
        if not kept:
            continue

        before, _, first_ok = states.get(kept[0], (None, None, False))
        _, after, last_ok = states.get(kept[-1], (None, None, False))
        label = day_state_label
        if not (first_ok and last_ok):
            before = _journal_parts(operations[kept[0]], 'before')
            after = _journal_parts(operations[kept[-1]], 'after')
            label = _journal_parts_label
        if before == after:
            continue

        sample = operations[kept[0]][0]
        rows.append({
            'operator_id': operator_id,
            'operator_name': str(sample.get('operator_name') or '').strip() or 'Без имени',
            'group_name': str(sample.get('group_name') or '').strip(),
            'shift_date': shift_date,
            'before': label(shift_date, *before),
            'after': label(shift_date, *after),
            'actors': _actors_label(operations[key] for key in kept),
        })

    # Внутри группы — по сотруднику и дню: так супервайзер читает свою группу
    # подряд, а у одного человека дни идут по порядку. Без группы — в конце.
    rows.sort(key=lambda row: (not row['group_name'], row['group_name'].lower(),
                               row['operator_name'].lower(), row['shift_date']))
    return rows, dropped_fills


# ── Rich-сообщение ──────────────────────────────────────────────────────────

# Потолки sendRichMessage: 32 768 символов и 500 блоков, где блок — в том числе
# каждая строка таблицы. Держим запас на заголовок и строку-хвост «… и ещё N».
RICH_TEXT_LIMIT = 32768
RICH_BLOCK_LIMIT = 500
RICH_TEXT_BUDGET = 30000
RICH_ROWS_LIMIT = 450

RICH_COLUMNS = ('Сотрудник', 'Группа', 'Было: дата и время', 'Стало: дата и время', 'Кто изменил')


def _plain(value):
    return str(value if value is not None else '')


def _title(day):
    return '🔔 Изменения графика — %s' % format_date(day)


def build_digest_rich(day, rows, escape=None, first_entries=None):
    """Сводка rich-сообщением (поле `html` у sendRichMessage): одна таблица.

    Теги идут вплотную, без переводов строк: как rich-разметка обходится с ними
    между блоками, документация не говорит, а пустой абзац был бы шумом.
    """
    esc = escape or _plain
    parts = ['<h3>%s</h3>' % esc(_title(day))]
    if not rows:
        # Сюда доходит только разовая отправка по кнопке: регулярная рассылка
        # за пустые сутки молчит и до вёрстки не добирается.
        parts.append('<p>%s</p>' % esc(empty_day_text(first_entries)))
        return ''.join(parts)

    header = '<tr>%s</tr>' % ''.join('<th>%s</th>' % esc(title) for title in RICH_COLUMNS)
    parts.extend(['<table>', header])
    used = sum(len(part) for part in parts) + len('</table>')
    shown = 0
    tail_reserve = 120
    for row in rows:
        cells = (row['operator_name'], row['group_name'] or '—', row['before'], row['after'],
                 row['actors'])
        markup = '<tr>%s</tr>' % ''.join('<td>%s</td>' % esc(value) for value in cells)
        if shown >= RICH_ROWS_LIMIT or used + len(markup) + tail_reserve > RICH_TEXT_BUDGET:
            break
        parts.append(markup)
        used += len(markup)
        shown += 1
    rest = len(rows) - shown
    if rest:
        parts.append('<tr><td colspan="%d"><i>… и ещё %d %s — полностью они в «Истории» '
                     'раздела «Графики работы»</i></td></tr>'
                     % (len(RICH_COLUMNS), rest, changes_word(rest)))
    parts.append('</table>')
    return ''.join(parts)


# ── Запасной текст ──────────────────────────────────────────────────────────

# Потолок обычного сообщения Telegram — 4096 символов, и сообщение сверх него
# не обрезается, а не уходит совсем. Запас — на разметку и хвост «… и ещё N».
TELEGRAM_TEXT_BUDGET = 3600


def build_digest(day, rows, escape=None, first_entries=None):
    """Та же сводка обычным сообщением (parse_mode=HTML) — если rich не примут.

    escape — экранирование подставляемых значений; приходит аргументом, чтобы
    модуль не тянул за собой bot_schedule2. Экранировать обязательно: один «<»
    в фамилии роняет ВСЁ сообщение, а не одну строку.
    """
    esc = escape or _plain
    lines = ['<b>%s</b>' % esc(_title(day)), '']
    if not rows:
        lines.append(esc(empty_day_text(first_entries)))
        return '\n'.join(lines)

    used = sum(len(line) + 1 for line in lines)
    shown = 0
    for row in rows:
        head = '• <b>%s</b>' % esc(row['operator_name'])
        if row['group_name']:
            head += ' · %s' % esc(row['group_name'])
        block = '%s\n   %s → %s\n   %s' % (head, esc(row['before']), esc(row['after']),
                                          esc(row['actors']))
        if used + len(block) + 1 + 80 > TELEGRAM_TEXT_BUDGET:
            break
        lines.append(block)
        used += len(block) + 1
        shown += 1
    rest = len(rows) - shown
    if rest:
        lines.append('… и ещё %d %s — полностью они в «Истории» раздела «Графики работы»'
                     % (rest, changes_word(rest)))
    return '\n'.join(lines)
