# -*- coding: utf-8 -*-
"""Сводка изменений графика работы за сутки — «Уведомления об изменениях».

Постановка (владелец): в разделе «Графики работы» в меню «3 точки» —
переключатель, по которому в Telegram приходит сводка за день: кто сколько раз
менял график, кому сколько раз меняли и по каким дням были изменения. Главам
отделов — только по своему отделу. И отдельным требованием: «нельзя, чтобы это
выглядело как спам».

Здесь только чистая логика: границы суток, группировка записей и текст
сообщения. Кому и когда отправлять — в bot_schedule2, данные — в database.py.
Модуль не знает ни про Telegram, ни про базу, поэтому весь он проверяется
тестами без сети и без подключения.

Ключевые решения
────────────────

**Единица счёта — «правка», а не строка журнала.** `work_shift_changes` пишет
строку на каждое отличие дня: подвинули смену и сняли выходной — две строки об
одном действии. Правкой считается тройка «сотрудник + день графика +
операция», где операция — это `changed_at`: он не передаётся в INSERT и
приходит из DEFAULT `CURRENT_TIMESTAMP`, то есть равен времени НАЧАЛА
транзакции и у всей пачки строк одной операции совпадает до микросекунды.
На боевых данных (5704 строки, 24.08–15.09.2026) строк на 5 % больше, чем
правок, у обменов — на 20 %.

**Ключ операции — пара (автор, changed_at), а не один `changed_at`.** Два
человека теоретически попадают в одну микросекунду, и тогда их правки слиплись
бы в одну.

**Массовые операции не считаются наравне с ручными.** Одна загрузка из файла —
это 369 правок у 54 сотрудников (реальный случай 11.09.2026), одна публикация
аукциона — 143. Если не разделять, шапка сводки будет «Изменений: 585», а
человека, который весь день правил смены руками, в списке не станет видно.
Поэтому у каждого автора рядом с числом правок стоит СПОСОБ и число заходов:
«369 правок · загрузка из файла (1 раз)» против «147 правок · вручную
(18 заходов)».

**Операторы, меняющие график сами, не попадают в список имён.** Обмены сменами
и доборы с аукциона пишутся от имени самого оператора: за сутки это полтора
десятка человек, каждый со своей единственной правкой. В списке «кто менял»
они вытеснили бы руководителей, ради которых сводка и собирается. Поэтому они
уходят одной строкой-счётчиком. Принцип тот же, что у экрана истории, где для
механики ФИО не показывается (`IMPERSONAL_SOURCES` в
`src/components/schedule/shiftHistoryFormat.js`), но наборы не совпадают и не
должны: экран описывает ОДНУ ячейку, и там публикация аукциона безлична, а
обмен подписан; сводка отвечает руководителю «кто менял график», и там
публикацию сделал конкретный администратор, а обмены — полтора десятка
операторов, чьи имена в сводке — шум.

**Первичное внесение графика — не изменение.** Постановка владельца
(17.09.2026): сводка про изменения, и если первое внесение графика тоже
считается, его убрать. Журнал пишет дифф дня, поэтому заполнение пустого дня
ложилось в сводку наравне с правками — на боевых данных 24.08–17.09.2026 это
3549 «правок» из 5635. Внесением считается операция, которая заполнила день,
где до неё не было ни смены, ни выходного, и сделана способом, которым график
вносят: вручную, загрузкой файла или публикацией аукциона. Обмены, доборы,
выдача с аукциона и заявки операторов правят уже внесённый график, даже когда
смена ложится на пустой день: добор почти всегда именно так и выглядит, и
без этого исключения из сводки пропали бы доборы и половина каждого обмена.
Пустоту дня до операции журнал хранит отдельной колонкой (`day_was_empty`):
по одним действиям её не определить — вторая смена в день, где смена уже
стоит, пишется тем же 'added' (на бою таких правок 40). Для строк, записанных
до колонки, решают действия: одни 'added' / 'day_off_set' — внесение.

**Мало правок — никаких разбивок.** При пяти и менее правках три блока («кто
менял», «кому меняли», «дни») пересказали бы друг друга тремя строками каждый.
Тогда печатается просто перечень: что, кому, на какой день и кто сделал.

**Таблицы — rich-сообщением.** Сводка уходит методом `sendRichMessage`
(Bot API 10.1) в разметке HTML: числа по авторам, сотрудникам и дням стоят
столбцами, а не строками через тире. HTML, а не Markdown: фамилия с «|»
разломала бы Markdown-таблицу, а экранирование HTML у бота уже есть. Прежний
текст (`build_digest`) остаётся запасным: если Telegram rich-сообщение не
примет, сводка уйдёт им, а не пропадёт.

**Сутки — закрытые, вчерашние.** Сводка уходит утром про вчера. «С начала
сегодняшнего дня» означало бы, что вечерние правки не попадут никуда, а
граница суток зависела бы от минуты запуска.

**Две оси дат разведены в тексте намеренно.** `changed_at` — когда правку
внесли (это период сводки), `shift_date` — какой день графика она затронула
(это блок «дни»). Они расходятся: на бою правки задним числом доходят до −80
дней, вперёд — до +31. Заголовок «Изменения, внесённые 14 сентября» и строка
«Дни графика, которых коснулись правки» подписаны так, чтобы даты из октября
под сентябрьским заголовком не читались как ошибка данных.

**Чего сводка не покрывает.** Пересчёт перерывов и снятие статус-периода
историю не пишут сознательно (см. `database.py`), поэтому нигде не сказано
«все изменения графика» — только «изменения», как их видит журнал.
"""

from datetime import datetime, timedelta, timezone


# ── Источники правок ────────────────────────────────────────────────────────

# Способ, которым правка попала в график. Коды — те же, что в
# database.WORK_SHIFT_CHANGE_SOURCES; подписи повторяют формулировки экрана
# истории (src/components/schedule/shiftHistoryFormat.js), чтобы сводка и
# журнал в интерфейсе говорили об одном и том же одинаково.
SOURCE_LABELS = {
    'supervisor': 'вручную',
    'import': 'загрузка из файла',
    'auction': 'публикация аукциона',
    'auction_admin': 'выдача с аукциона',
    'status_period': 'статусом',
    'shift_request': 'заявка оператора',
    'swap': 'обмен сменами',
    'auction_topup': 'добор с аукциона',
    'auction_topup_cancel': 'отмена добора',
    'system': 'системой',
}

# Правки, которые оператор делает сам себе. Имён не печатаем — см. докстроку.
# Всё остальное (в том числе незнакомый код) считается решением руководителя и
# попадает в «Кто менял» с именем: пропустить правку молча хуже, чем назвать
# её общим словом.
SELF_SERVICE_SOURCES = ('swap', 'auction_topup', 'auction_topup_cancel')

# Способы, которыми график ВНОСЯТ. Заполнение пустого дня одним из них —
# первичное внесение, в сводку оно не идёт (см. докстроку модуля). Незнакомый
# код сюда не попадает: спрятать правку молча хуже, чем показать лишнюю.
ENTRY_SOURCES = ('supervisor', 'import', 'auction')

# Действия, которые возможны на пустом дне. Для строк журнала без колонки
# day_was_empty операция из одних таких действий считается заполнением.
FILL_ACTIONS = ('added', 'day_off_set')

# Роль автора одним словом перед фамилией. Тот же словарь, что на экране
# истории (ROLE_PREFIXES), только в родительном падеже строки «кто менял».
ROLE_LABELS = {
    'super_admin': 'администратор',
    'admin': 'администратор',
    'sv': 'супервайзер',
    'trainer': 'тренер',
    'operator': 'оператор',
    'trainee': 'стажёр',
}

ACTION_LABELS = {
    'added': 'смена добавлена',
    'removed': 'смена удалена',
    'changed': 'смена изменена',
    'day_off_set': 'проставлен выходной',
    'day_off_cleared': 'выходной снят',
}

# Пара «действие·источник» там, где источник меняет смысл фразы. Список — копия
# ACTION_SOURCE_LABELS с экрана истории, дополненная заявками операторов:
# в JS-словаре источника 'shift_request' нет вовсе.
ACTION_SOURCE_LABELS = {
    'added·auction': 'взята с аукциона',
    'added·auction_topup': 'добор с аукциона',
    'added·auction_admin': 'выдана с аукциона',
    'added·swap': 'получена при обмене',
    'added·import': 'загружена из файла',
    'added·shift_request': 'добавлена по заявке',
    'removed·auction': 'снята публикацией аукциона',
    'removed·auction_topup_cancel': 'добор отменён',
    'removed·auction_admin': 'снята с аукциона',
    'removed·swap': 'отдана при обмене',
    'removed·import': 'убрана загрузкой из файла',
    'removed·status_period': 'снята статусом',
    'removed·shift_request': 'снята по заявке',
    'changed·auction': 'пересобрана публикацией аукциона',
    'changed·auction_topup': 'расширена добором',
    'changed·auction_topup_cancel': 'урезана отменой добора',
    'changed·swap': 'пересобрана обменом',
    'changed·import': 'заменена загрузкой из файла',
    'changed·shift_request': 'изменена по заявке',
    'day_off_set·auction': 'выходной с аукциона',
    'day_off_set·import': 'выходной из файла',
    'day_off_set·swap': 'выходной после обмена',
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
    и «Сформировано» в сводке разошлось бы с часами получателя на пять часов.
    """
    return datetime.now(timezone(timedelta(hours=5)))


# ── Форматирование ──────────────────────────────────────────────────────────

MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def plural_ru(count, one, few, many):
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def changes_word(count):
    return plural_ru(count, 'правка', 'правки', 'правок')


def people_word(count):
    """Форма после предлога «у»: «у 21 сотрудника», «у 3 сотрудников»."""
    return plural_ru(count, 'сотрудника', 'сотрудников', 'сотрудников')


def people_counted(count):
    """Счётная форма: «73 сотрудника», «5 сотрудников», «1 сотрудник»."""
    return plural_ru(count, 'сотрудник', 'сотрудника', 'сотрудников')


def format_day(day):
    return '%d %s' % (day.day, MONTHS_GENITIVE[day.month - 1])


def format_day_full(day):
    return '%d %s %d' % (day.day, MONTHS_GENITIVE[day.month - 1], day.year)


def format_day_range(start, end):
    """«14–20 сентября», «29 сентября — 3 октября», «14 сентября»."""
    if start == end:
        return format_day(start)
    if start.month == end.month and start.year == end.year:
        return '%d–%d %s' % (start.day, end.day, MONTHS_GENITIVE[end.month - 1])
    return '%s — %s' % (format_day(start), format_day(end))


def _time_label(value):
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%H:%M')
    return str(value)[:5]


def shift_times_label(entry):
    """«09:00—18:00» или «09:00—18:00 → 10:00—19:00» для изменённой смены."""
    nxt = ''
    if entry.get('start') is not None and entry.get('end') is not None:
        nxt = '%s—%s' % (_time_label(entry.get('start')), _time_label(entry.get('end')))
    prev = ''
    if entry.get('prev_start') is not None and entry.get('prev_end') is not None:
        prev = '%s—%s' % (_time_label(entry.get('prev_start')), _time_label(entry.get('prev_end')))
    if prev and nxt and prev != nxt:
        return '%s → %s' % (prev, nxt)
    return nxt or prev


def entry_action_label(entry):
    key = '%s·%s' % (entry.get('action') or '', entry.get('source') or '')
    return (ACTION_SOURCE_LABELS.get(key)
            or ACTION_LABELS.get(entry.get('action') or '')
            or str(entry.get('action') or '—'))


def actor_label(name, role):
    """«Кастек Гаухар, супервайзер». Роль нужна, чтобы в списке не гадать,
    правил это руководитель отдела или сам сотрудник."""
    name = str(name or '').strip() or 'Без автора'
    role_label = ROLE_LABELS.get(str(role or '').strip().lower())
    return '%s, %s' % (name, role_label) if role_label else name


def format_day_short(day):
    """«14.09» — в списке дней месяц повторялся бы по десять раз подряд."""
    return '%02d.%02d' % (day.day, day.month)


WEEKDAYS_SHORT = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')


def format_day_weekday(day):
    """«14.09, пн» — в таблице дней по дню недели видно, что перетряхнули выходные."""
    return '%s, %s' % (format_day_short(day), WEEKDAYS_SHORT[day.weekday()])


# ── Первичное внесение ──────────────────────────────────────────────────────

def _change_key(entry):
    """Правка — сотрудник + день графика + операция (автор, changed_at)."""
    actor_id = entry.get('actor_id')
    return (entry.get('operator_id'), entry.get('shift_date'),
            actor_id if actor_id is not None else -1, entry.get('changed_at'))


def _is_first_entry(rows):
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
    first_keys = {key for key, rows in groups.items() if _is_first_entry(rows)}

    changes = []
    first = []
    for entry in entries or []:
        (first if _change_key(entry) in first_keys else changes).append(entry)
    return changes, first


def empty_day_text(first_entries=None):
    """Строка для суток без правок — её видит только разовая отправка по кнопке.

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


# ── Группировка ─────────────────────────────────────────────────────────────

def summarize(entries):
    """Свести записи журнала к числам сводки.

    entries — записи за отчётные сутки: словари с ключами operator_id,
    operator_name, shift_date, action, source, actor_id, actor_name,
    actor_role, changed_at (плюс времена смены для мелкой сводки). Первичное
    внесение отсекается ДО этой функции (split_first_entries): доставке нужно
    знать, остались ли правки, раньше, чем собирается текст.

    Правка — тройка «сотрудник + день графика + операция», операция — пара
    «автор + changed_at» (см. докстроку модуля).
    """
    total = set()
    actors = {}
    operators = {}
    days = {}
    self_service = {}

    for entry in entries or []:
        operator_id = entry.get('operator_id')
        shift_date = entry.get('shift_date')
        changed_at = entry.get('changed_at')
        source = str(entry.get('source') or 'system').strip() or 'system'
        # actor_id допускает NULL по схеме (ON DELETE SET NULL), и тогда пары
        # «автор + время» не выйдет — подставляем заведомо невозможный id.
        actor_id = entry.get('actor_id')
        operation = (actor_id if actor_id is not None else -1, changed_at)
        change = (operator_id, shift_date, operation)

        total.add(change)

        day_bucket = days.setdefault(shift_date, set())
        day_bucket.add((operator_id, operation))

        operator_bucket = operators.setdefault(operator_id, {
            'name': entry.get('operator_name') or '',
            'changes': set(),
        })
        operator_bucket['changes'].add((shift_date, operation))

        if source in SELF_SERVICE_SOURCES:
            bucket = self_service.setdefault(source, {'changes': set(), 'people': set()})
            bucket['changes'].add(change)
            bucket['people'].add(operator_id)
            continue

        actor_bucket = actors.setdefault(operation[0], {
            'name': entry.get('actor_name') or '',
            'role': entry.get('actor_role') or '',
            'changes': set(),
            'operators': set(),
            'sources': {},
        })
        # Имя автора хранится копией в каждой строке: если человека потом
        # переименовали, свежая строка принесёт новое написание — берём его.
        if entry.get('actor_name'):
            actor_bucket['name'] = entry.get('actor_name')
        if entry.get('actor_role'):
            actor_bucket['role'] = entry.get('actor_role')
        actor_bucket['changes'].add(change)
        actor_bucket['operators'].add(operator_id)
        source_bucket = actor_bucket['sources'].setdefault(source, {
            'changes': set(), 'operations': set(),
        })
        source_bucket['changes'].add(change)
        source_bucket['operations'].add(operation)

    actor_rows = []
    for actor_id, bucket in actors.items():
        ways = sorted(
            (
                {
                    'source': source,
                    'changes': len(data['changes']),
                    'operations': len(data['operations']),
                }
                for source, data in bucket['sources'].items()
            ),
            key=lambda item: (-item['changes'], item['source']),
        )
        actor_rows.append({
            'actor_id': actor_id,
            'name': bucket['name'],
            'role': bucket['role'],
            'changes': len(bucket['changes']),
            'operators': len(bucket['operators']),
            'ways': ways,
        })
    actor_rows.sort(key=lambda item: (-item['changes'], item['name']))

    operator_rows = [
        {'operator_id': operator_id, 'name': bucket['name'], 'changes': len(bucket['changes'])}
        for operator_id, bucket in operators.items()
    ]
    operator_rows.sort(key=lambda item: (-item['changes'], item['name']))

    self_rows = sorted(
        (
            {'source': source, 'changes': len(data['changes']), 'people': len(data['people'])}
            for source, data in self_service.items()
        ),
        key=lambda item: (-item['changes'], item['source']),
    )

    return {
        'total': len(total),
        'actors': actor_rows,
        'operators': operator_rows,
        'days': {day: len(bucket) for day, bucket in days.items()},
        'self_service': self_rows,
        'self_total': sum(row['changes'] for row in self_rows),
    }


# ── Текст сообщения ─────────────────────────────────────────────────────────

# Потолок сообщения Telegram — 4096 символов, и сообщение сверх него просто не
# уходит. Держим запас на разметку и на хвост «и ещё N».
TELEGRAM_TEXT_BUDGET = 3600

# Сколько строк печатать в разбивках. Боевые максимумы за сутки: 24 автора,
# 92 затронутых сотрудника, 63 разных дня графика — без потолка сводка в
# сообщение не влезает.
ACTORS_LIMIT = 10
OPERATORS_LIMIT = 12
DAY_GROUPS_LIMIT = 12

# До этого числа правок разбивки не печатаются — вместо них перечень самих
# правок: три блока по одной строке пересказали бы друг друга.
DETAILED_MAX_CHANGES = 5


def _plain(value):
    return str(value if value is not None else '')


def _text_length(lines):
    return sum(len(line) + 1 for line in lines)


def _fits(lines, block):
    return _text_length(lines) + _text_length(block) <= TELEGRAM_TEXT_BUDGET


def _head_counts(stats):
    head = 'Правок: <b>%d</b> · сотрудников: <b>%d</b>' % (
        stats['total'], len(stats['operators']),
    )
    # Третье число ставим, только если оно не повторяет соседнее: одинаковые
    # числа подряд читаются как ошибка, а не как сводка.
    if stats['actors'] and len(stats['actors']) != len(stats['operators']):
        head += ' · менявших: <b>%d</b>' % len(stats['actors'])
    return head


def build_digest(day, entries, scope_label, generated_label=None, escape=None,
                 first_entries=None):
    """Текст сводки за сутки (parse_mode=HTML) — запасной к build_digest_rich.

    escape — экранирование подставляемых значений; приходит аргументом, чтобы
    модуль не тянул за собой bot_schedule2. Экранировать обязательно: один «<»
    в фамилии роняет ВСЁ сообщение, а не одну строку.
    first_entries — отсечённое первичное внесение; нужно только, чтобы назвать
    его в сообщении за сутки без правок.
    """
    esc = escape or _plain
    stats = summarize(entries)
    total = stats['total']

    lines = ['🗓 <b>Изменения в графике за %s</b>' % esc(format_day_full(day))]
    lines.append('Область: %s' % esc(scope_label or 'Все отделы'))
    if not total:
        # Сюда доходит только разовая отправка по кнопке: регулярная рассылка
        # за пустые сутки молчит и до текста не добирается.
        lines.append('')
        lines.append(empty_day_text(first_entries))
        return '\n'.join(lines)

    lines.append(_head_counts(stats))

    if total <= DETAILED_MAX_CHANGES:
        lines.append('')
        rows = _detailed_lines(entries, esc)
        shown = 0
        # Бюджет и здесь: правок не больше пяти, но в каждой бывает по
        # несколько действий и два длинных ФИО, а сообщение сверх потолка
        # Telegram не обрезается, а не уходит совсем.
        for row in rows:
            if not _fits(lines, [row, '… и ещё 5 правок']):
                break
            lines.append(row)
            shown += 1
        if shown < len(rows):
            rest = len(rows) - shown
            lines.append('… и ещё %d %s' % (rest, changes_word(rest)))
        if generated_label:
            lines.append('')
            lines.append('<i>Сформировано %s</i>' % esc(generated_label))
        return '\n'.join(lines)

    for block in (
        _actors_block(stats, esc),
        _operators_block(stats, esc),
        _days_block(stats, esc),
        _self_service_block(stats, esc),
    ):
        if block and _fits(lines, block):
            lines.append('')
            lines.extend(block)

    if generated_label:
        lines.append('')
        lines.append('<i>Сформировано %s</i>' % esc(generated_label))
    return '\n'.join(lines)


def _detailed_lines(entries, esc):
    """Перечень правок, когда их единицы: что, кому, на какой день и кто сделал.

    Строка на ПРАВКУ — ту же единицу, что в шапке, — а не на строку журнала.
    Поставили выходной на день со сменой: журнал пишет «смена удалена» и
    «проставлен выходной» двумя строками, а человек сделал одно действие.
    И наоборот, разбитая смена (09:00—13:00 и 18:00—22:00) — две строки с
    одинаковым действием, и ни одну из них терять нельзя.
    """
    rows = []
    for group in _changes_in_order(entries):
        first = group[0]
        shift_date = first.get('shift_date')
        row = '• <b>%s</b> · %s — %s' % (
            esc(first.get('operator_name') or 'Без имени'),
            esc(format_day(shift_date)) if shift_date else '—',
            esc(_change_actions_label(group)),
        )
        if str(first.get('source') or '') not in SELF_SERVICE_SOURCES:
            row += '\n   %s' % esc(actor_label(first.get('actor_name'), first.get('actor_role')))
        rows.append(row)
    return rows


def _changes_in_order(entries):
    """Строки журнала, собранные в правки, по времени внесения."""
    changes = {}
    for entry in entries or []:
        changes.setdefault(_change_key(entry), []).append(entry)
    return sorted(changes.values(),
                  key=lambda group: (_plain(group[0].get('changed_at')),
                                     _plain(group[0].get('operator_name'))))


def _change_actions_label(group):
    """«смена удалена 09:00—18:00, проставлен выходной» — все действия одной правки.

    Сначала действия со временем смены, потом выходные: так пара читается
    как одно событие.
    """
    actions = []
    for item in sorted(group, key=lambda row: (0 if shift_times_label(row) else 1,
                                               _plain(row.get('start') or row.get('prev_start')),
                                               _plain(row.get('action')))):
        label = entry_action_label(item)
        times = shift_times_label(item)
        actions.append('%s %s' % (label, times) if times else label)
    return ', '.join(actions)


def _ways_label(ways, esc):
    """«вручную (18 заходов)» — способ и сколько раз к нему прибегали.

    Число заходов отделяет одну загрузку файла от полутора сотен ручных правок:
    без него и то и другое выглядит как «369 правок», и человек, правивший
    смены весь день, теряется за тем, кто один раз нажал «Импорт».
    """
    parts = []
    for way in ways[:2]:
        label = SOURCE_LABELS.get(way['source'], way['source'])
        operations = way['operations']
        parts.append('%s (%d %s)' % (esc(label), operations,
                                     plural_ru(operations, 'заход', 'захода', 'заходов')))
    if len(ways) > 2:
        rest = len(ways) - 2
        parts.append('и ещё %d %s' % (rest, plural_ru(rest, 'способ', 'способа', 'способов')))
    return ', '.join(parts)


def _actors_block(stats, esc):
    rows = stats['actors']
    if not rows:
        return []
    block = ['<b>Кто менял</b>']
    for row in rows[:ACTORS_LIMIT]:
        line = '👤 %s — <b>%d</b> %s' % (
            esc(actor_label(row['name'], row['role'])), row['changes'],
            changes_word(row['changes']),
        )
        if row['operators'] > 1:
            line += ' у %d %s' % (row['operators'], people_word(row['operators']))
        ways = _ways_label(row['ways'], esc)
        if ways:
            line += '\n   %s' % ways
        block.append(line)
    tail = rows[ACTORS_LIMIT:]
    if tail:
        block.append('… и ещё %d — %d %s' % (
            len(tail), sum(item['changes'] for item in tail),
            changes_word(sum(item['changes'] for item in tail)),
        ))
    return block


def _operators_block(stats, esc):
    rows = stats['operators']
    if not rows:
        return []
    block = ['<b>Кому меняли</b>']
    for row in rows[:OPERATORS_LIMIT]:
        block.append('• %s — %d' % (esc(row['name'] or 'Без имени'), row['changes']))
    tail = rows[OPERATORS_LIMIT:]
    if tail:
        block.append('… и ещё %d %s — %d %s' % (
            len(tail), people_counted(len(tail)),
            sum(item['changes'] for item in tail),
            changes_word(sum(item['changes'] for item in tail)),
        ))
    return block


def _days_block(stats, esc):
    """Дни ГРАФИКА, которых коснулись правки, — не день, за который сводка.

    Показываем по дню, а не диапазоном: диапазон «4–20 сентября — 573» короче,
    но прячет ровно то, ради чего блок и нужен — в какой день графика пришлась
    основная перетряска. За сутки разных дней бывает до 63 (боевой максимум),
    поэтому берём самые нагруженные, а печатаем их по порядку дат.
    """
    if not stats['days']:
        return []

    shown, tail_days, tail_changes = _days_shown(stats)
    line = ' · '.join('%s — %d' % (esc(format_day_short(day)), count)
                      for day, count in shown)
    if tail_days:
        line += ' · … и ещё %d %s — %d %s' % (
            tail_days, plural_ru(tail_days, 'день', 'дня', 'дней'),
            tail_changes, changes_word(tail_changes),
        )
    return ['<b>Дни графика, которых коснулись правки</b>', line]


def _days_shown(stats):
    """(показанные дни по порядку дат, сколько дней в хвосте, сколько в нём правок)."""
    days = stats['days']
    ordered = sorted(days.items())
    if len(ordered) <= DAY_GROUPS_LIMIT:
        return ordered, 0, 0
    keep = set(sorted(days, key=lambda day: -days[day])[:DAY_GROUPS_LIMIT])
    shown = [(day, count) for day, count in ordered if day in keep]
    tail_changes = sum(count for day, count in ordered if day not in keep)
    return shown, len(ordered) - len(shown), tail_changes


def _self_service_block(stats, esc):
    rows = stats['self_service']
    if not rows:
        return []
    # Одной строкой и без имён: см. докстроку модуля.
    parts = []
    for row in rows:
        parts.append('%s — %d (%d %s)' % (
            esc(SOURCE_LABELS.get(row['source'], row['source'])),
            row['changes'], row['people'], people_counted(row['people']),
        ))
    return ['<b>Операторы сами</b>', '🔄 ' + ' · '.join(parts)]


# ── Rich-сообщение: таблицы ─────────────────────────────────────────────────

# Потолки sendRichMessage: 32 768 символов и 500 блоков, где блок — в том числе
# каждая строка таблицы. Пределы ACTORS/OPERATORS/DAY_GROUPS держат сводку
# далеко под ними даже на именах предельной длины (VARCHAR 255), это сторожит
# тест — отдельный бюджет, как у текста, здесь не нужен.
RICH_TEXT_LIMIT = 32768
RICH_BLOCK_LIMIT = 500

# Рамки нужны, чтобы столбцы читались на телефоне. Полос нет: чередующаяся
# заливка на таблице в десяток строк — цвет без смысла.
RICH_TABLE_OPEN = '<table bordered compact>'


def _rich_cell(value, header=False, numeric=False, colspan=None):
    tag = 'th' if header else 'td'
    attrs = ''
    if colspan:
        attrs += ' colspan="%d"' % colspan
    if numeric:
        attrs += ' align="right"'
    return '<%s%s>%s</%s>' % (tag, attrs, value, tag)


def _rich_table(columns, rows, tail=None):
    """columns — [(заголовок, числовой ли столбец)], rows — уже экранированные
    ячейки. tail — строка «… и ещё N» во всю ширину таблицы."""
    numeric = [flag for _, flag in columns]
    parts = [RICH_TABLE_OPEN, '<tr>']
    parts.extend(_rich_cell(title, header=True, numeric=flag) for title, flag in columns)
    parts.append('</tr>')
    for row in rows:
        parts.append('<tr>')
        parts.extend(_rich_cell(value, numeric=flag) for value, flag in zip(row, numeric))
        parts.append('</tr>')
    if tail:
        parts.append('<tr>%s</tr>' % _rich_cell('<i>%s</i>' % tail, colspan=len(columns)))
    parts.append('</table>')
    return ''.join(parts)


def build_digest_rich(day, entries, scope_label, generated_label=None, escape=None,
                      first_entries=None):
    """Сводка за сутки rich-сообщением (поле `html` у sendRichMessage).

    Состав и пороги те же, что у build_digest, и числа считает тот же
    summarize: таблицы и запасной текст не могут разойтись. Блоки идут
    вплотную, без переводов строк между тегами: как rich-разметка обходится с
    ними между блоками, документация не говорит, а пустой абзац между
    таблицами был бы шумом.
    """
    esc = escape or _plain
    stats = summarize(entries)

    parts = ['<h3>🗓 Изменения в графике за %s</h3>' % esc(format_day_full(day))]
    scope = 'Область: %s' % esc(scope_label or 'Все отделы')
    if not stats['total']:
        # Как и у текста: сюда доходит только разовая отправка по кнопке.
        parts.append('<p>%s<br>%s</p>' % (scope, esc(empty_day_text(first_entries))))
        return ''.join(parts)

    parts.append('<p>%s<br>%s</p>' % (scope, _head_counts(stats)))
    if stats['total'] <= DETAILED_MAX_CHANGES:
        parts.append(_rich_detailed_table(entries, esc))
    else:
        for title, table in (
            ('Кто менял', _rich_actors_table(stats, esc)),
            ('Кому меняли', _rich_operators_table(stats, esc)),
            ('Дни графика, которых коснулись правки', _rich_days_table(stats, esc)),
            ('Операторы сами', _rich_self_service_table(stats, esc)),
        ):
            if table:
                parts.append('<h4>%s</h4>%s' % (title, table))
    if generated_label:
        parts.append('<footer>Сформировано %s</footer>' % esc(generated_label))
    return ''.join(parts)


def _rich_detailed_table(entries, esc):
    """Правок единицы — строка на правку, как в перечне у текста."""
    rows = []
    for group in _changes_in_order(entries):
        first = group[0]
        shift_date = first.get('shift_date')
        # Обмены и доборы — без имени автора, как везде в сводке.
        author = '—'
        if str(first.get('source') or '') not in SELF_SERVICE_SOURCES:
            author = esc(actor_label(first.get('actor_name'), first.get('actor_role')))
        rows.append([
            esc(first.get('operator_name') or 'Без имени'),
            esc(format_day_weekday(shift_date)) if shift_date else '—',
            esc(_change_actions_label(group)),
            author,
        ])
    return _rich_table(
        [('Сотрудник', False), ('День графика', False), ('Что сделано', False), ('Автор', False)],
        rows,
    )


def _rich_actors_table(stats, esc):
    rows = stats['actors']
    if not rows:
        return ''
    body = [
        [esc(actor_label(row['name'], row['role'])), str(row['changes']),
         str(row['operators']), _ways_label(row['ways'], esc) or '—']
        for row in rows[:ACTORS_LIMIT]
    ]
    tail = None
    rest = rows[ACTORS_LIMIT:]
    if rest:
        changes = sum(item['changes'] for item in rest)
        tail = '… и ещё %d — %d %s' % (len(rest), changes, changes_word(changes))
    return _rich_table(
        [('Автор', False), ('Правок', True), ('Сотрудников', True), ('Способ', False)],
        body, tail,
    )


def _rich_operators_table(stats, esc):
    rows = stats['operators']
    if not rows:
        return ''
    body = [[esc(row['name'] or 'Без имени'), str(row['changes'])]
            for row in rows[:OPERATORS_LIMIT]]
    tail = None
    rest = rows[OPERATORS_LIMIT:]
    if rest:
        changes = sum(item['changes'] for item in rest)
        tail = '… и ещё %d %s — %d %s' % (len(rest), people_counted(len(rest)),
                                           changes, changes_word(changes))
    return _rich_table([('Сотрудник', False), ('Правок', True)], body, tail)


def _rich_days_table(stats, esc):
    if not stats['days']:
        return ''
    shown, tail_days, tail_changes = _days_shown(stats)
    body = [[esc(format_day_weekday(day)), str(count)] for day, count in shown]
    tail = None
    if tail_days:
        tail = '… и ещё %d %s — %d %s' % (
            tail_days, plural_ru(tail_days, 'день', 'дня', 'дней'),
            tail_changes, changes_word(tail_changes),
        )
    return _rich_table([('День графика', False), ('Правок', True)], body, tail)


def _rich_self_service_table(stats, esc):
    rows = stats['self_service']
    if not rows:
        return ''
    # Без имён: см. докстроку модуля.
    body = [[esc(SOURCE_LABELS.get(row['source'], row['source'])),
             str(row['changes']), str(row['people'])]
            for row in rows]
    return _rich_table([('Способ', False), ('Правок', True), ('Сотрудников', True)], body)
