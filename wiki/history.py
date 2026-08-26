# -*- coding: utf-8 -*-
"""История версий статьи: сборка редакций из снимков и сравнение двух редакций.

Модуль намеренно ЧИСТЫЙ — SQL здесь нет, на вход приходят уже прочитанные
строки. Так сделано потому, что сложность тут не в запросе, а в смысле таблицы
`wiki_article_versions`, и смысл этот надо было закрыть тестами, а не глазами.

Главное про таблицу: снимок делается ПЕРЕД правкой — `update_article`
(wiki/edit.py) зовёт `snapshot_version` первой строкой. Значит одна строка
версии описывает сразу два разных момента:

    title/summary/status/content  — как статья выглядела ДО этой правки;
    editor_id/created_at/comment  — кто и когда эту правку делал.

Отсюда два следствия, и каждое ломает наивную выдачу «покажем строки списком»:

1. **Строку нельзя показать как есть.** «Версия №7, изменил Иванов» читалась бы
   как «вот что написал Иванов», хотя в строке лежит текст ПРЕДЫДУЩЕГО автора.
   Состояние из строки N создала правка из строки N−1 — по этой паре и
   собирается авторство. Первая строка — исключение: её пишет создание статьи
   уже ПОСЛЕ вставки, там автор и текст свои.

2. **Строк больше, чем редакций.** Снимок пишется на любой PATCH, даже когда
   правка не тронула ни одно поле статьи: перенос в другой раздел и смена тегов
   до UPDATE статьи вообще не доходят (см. ранний выход в `update_article`), а
   строка версии уже создана. Проверено по проду 25.08.2026: у статьи №618 —
   десять строк и пять разных текстов, а снимок «до первой правки» совпадает с
   созданием у ВСЕХ 304 статей, где строк хотя бы две. Поэтому одинаковые
   подряд состояния сливаются в одну редакцию, а «молчаливые» сохранения
   остаются при ней числом и списком — не выброшены, но и не выданы за правку
   текста.

Текущего текста статьи в таблице версий нет вовсе — он в `wiki_articles`.
Совпадение последней строки с телом статьи в проде скорее исключение (49 из
305), так что «текущая редакция» собирается отдельно и авторство ей берётся из
самой статьи (`updated_by`/`updated_at`): архивирование меняет статью вообще без
снимка (`delete_article`), и доверять здесь последней строке нельзя.

Номеров редакций наружу НЕТ намеренно. `version_number` считает сохранения, а не
редакции, и после слияния шёл бы дырами (1, 4, 9, 10); собственная же сквозная
нумерация разошлась бы с «редакция №N» из ознакомлений, где ключом служит ровно
`max(version_number)`. Два разных числа под одним словом на соседних экранах —
худшее из трёх. Редакции опознаются датой и автором, как в любом почтовом
клиенте.
"""

import datetime
import difflib
import re
from html import unescape

# Разбор HTML в строки для сравнения. Санитайзер уже отработал (в базе лежит
# очищенное тело), но скрипты и стили выбрасываем всё равно: сравнение читают
# люди, и мусор в нём — такой же брак, как в статье.
_SCRIPT = re.compile(r'<(script|style)\b.*?</\1\s*>', re.I | re.S)
_BASE64_SRC = re.compile(r'src\s*=\s*"data:[^"]*"', re.I)
_IMAGE = re.compile(r'<img\b[^>]*>', re.I)
# Ячейка таблицы — не отдельная строка сравнения, а часть строки: разбей мы
# таблицу по ячейкам, правка одного числа показалась бы как «строка исчезла и
# появилась другая» одиннадцать раз подряд (у «Всех акций» столько колонок).
_CELL_END = re.compile(r'</(td|th)\s*>', re.I)
# Строка таблицы разбирается ЦЕЛИКОМ и по ячейкам сразу: тело ячейки редактор
# заворачивает в абзац (`<td><p>…</p></td>` — так у 20 статей прода), а «</p>»
# ниже даёт перевод строки. Из-за этого строка на одиннадцать колонок ехала в
# сравнение одиннадцатью отдельными строками — по значению в каждой, без
# намёка на то, что это одна запись таблицы.
_TABLE = re.compile(r'<table\b.*?</table\s*>', re.I | re.S)
_ROW = re.compile(r'<tr\b[^>]*>(.*?)</tr\s*>', re.I | re.S)
_CELL = re.compile(r'<(td|th)\b[^>]*>(.*?)</\1\s*>', re.I | re.S)
# Метка строки таблицы в потоке текста: её надо пронести сквозь снятие тегов и
# раскрытие сущностей, поэтому она из символов, которых в тексте не бывает.
_MARK = '\x00{}\x00'
_MARK_RE = re.compile(r'\x00(\d+)\x00')
_BLOCK_END = re.compile(
    r'</(p|div|li|h[1-6]|tr|table|thead|tbody|ul|ol|blockquote|pre|figure|'
    r'figcaption|details|summary|section|article)\s*>|<br\s*/?>', re.I)
_TAG = re.compile(r'<[^>]+>')
# \xa0 попадает сюда из &nbsp; уже после раскрытия сущностей.
_SPACES = re.compile(r'[ \t\r\f\v ]+')
# Токен для пословного сравнения: слово вместе с идущими за ним пробелами —
# так склейка обратно даёт исходную строку без домысливания пробелов.
_TOKEN = re.compile(r'\S+\s*')

# Потолки. Первый защищает базу и браузер от статьи вроде «Все акции» (90 КБ
# тела), второй — ответ сервера: показать три тысячи изменённых строк всё равно
# нельзя, а отправить их по сети можно, и это будут мегабайты.
MAX_BLOCKS = 8000
MAX_ROWS = 800


def _plain(fragment):
    """Кусок HTML → одна строка текста. Внутренние абзацы и <br> — пробелы."""
    text = _BLOCK_END.sub(' ', fragment or '')
    text = _TAG.sub('', text)
    # Сущности раскрываем ПОСЛЕ снятия тегов: сделай мы наоборот, записанный в
    # тексте «&lt;p&gt;» превратился бы в настоящий тег и был бы съеден.
    text = unescape(text).replace('\n', ' ')
    return _SPACES.sub(' ', text).strip()


def _table_rows(table_html, sink):
    """Разбирает одну таблицу в строки-записи и возвращает текст с метками.

    Шапка таблицы (строка целиком из `<th>`) запоминается и раздаётся остальным
    строкам как ИМЕНА КОЛОНОК. Без них сравнение говорит «было „Активная“, стало
    „Завершена“» — про какую графу речь, человек угадывает по соседям; с ними
    строка читается сразу: «Актуальность: Активная → Завершена».
    """
    columns = None

    def take(match):
        cells = [(tag.lower(), _plain(body))
                 for tag, body in _CELL.findall(match.group(1))]
        # Пустая строка таблицы («| | |») смысла не несёт, а в сравнении стоила
        # бы места наравне с настоящим абзацем.
        if not cells or not any(value for _, value in cells):
            return '\n'
        values = [value for _, value in cells]
        head = all(tag == 'th' for tag, _ in cells)
        nonlocal columns
        if head and columns is None:
            columns = values
        sink.append({
            'text': ' | '.join(values),
            'cells': values,
            # Шапке имена колонок не нужны: она сама ими и является.
            'columns': None if head else columns,
            'head': head,
        })
        return '\n' + _MARK.format(len(sink) - 1) + '\n'

    return _ROW.sub(take, table_html)


def html_to_lines(html):
    """Тело статьи → список строк для сравнения, со структурой строк таблиц.

    Строка здесь — абзац, пункт списка, заголовок или ЗАПИСЬ ТАБЛИЦЫ. У записи
    таблицы, кроме текста, есть разбивка по ячейкам и имена колонок: экран рисует
    её таблицей, а не полосой текста с палками-разделителями.

    Вложенные таблицы разбираются как одна: `<tr>` берётся нежадно, и строка
    внешней таблицы обрывается на первом `</tr>` внутренней. В проде таких нет
    (проверено 26.08.2026 по всем 159 статьям с таблицами), а редактор их и не
    даёт создать.
    """
    if not html:
        return []
    text = _SCRIPT.sub(' ', str(html))
    # Картинка в base64 занимает в проде 81 % объёма тела (см. to_plain_text) и
    # в сравнении не значит ничего: её адрес меняется при каждой перезаливке.
    text = _BASE64_SRC.sub('', text)
    text = _IMAGE.sub('\n[изображение]\n', text)
    rows = []
    text = _TABLE.sub(lambda m: _table_rows(m.group(0), rows), text)
    # Ячейки, до которых разбор таблиц не дошёл (обрывок разметки без <table>),
    # склеиваем по-старому: палка лучше, чем потерянная граница ячеек.
    text = _CELL_END.sub(' | ', text)
    text = _BLOCK_END.sub('\n', text)
    text = _TAG.sub('', text)
    text = unescape(text)
    lines = []
    for raw in text.split('\n'):
        mark = _MARK_RE.fullmatch(raw.strip())
        if mark:
            lines.append(rows[int(mark.group(1))])
        else:
            # Хвостовая палка остаётся от последней ячейки: «</td>» даёт
            # разделитель, а разделять после неё уже нечего.
            line = _SPACES.sub(' ', raw).strip().strip('|').strip()
            if not line or not line.strip('| '):
                continue
            lines.append({'text': line, 'cells': None, 'columns': None, 'head': False})
        if len(lines) >= MAX_BLOCKS:
            break
    return lines


def html_to_blocks(html):
    """Те же строки, только текстом. Оставлено ради сравнения и тестов."""
    return [line['text'] for line in html_to_lines(html)]


def _tokens(line):
    return _TOKEN.findall(line or '')


def _inline_parts(before, after):
    """Пословная разметка пары строк: что убрали и что дописали.

    Без неё правка одного слова в длинном абзаце показывается как «строка
    целиком удалена, строка целиком добавлена», и глазами их надо сличать
    самому — ровно та работа, ради которой сравнение и открывают.
    """
    left, right = _tokens(before), _tokens(after)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    before_parts, after_parts = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if i2 > i1:
            before_parts.append({'op': 'same' if tag == 'equal' else 'cut',
                                 'text': ''.join(left[i1:i2])})
        if j2 > j1:
            after_parts.append({'op': 'same' if tag == 'equal' else 'add',
                                'text': ''.join(right[j1:j2])})
    return before_parts, after_parts


# Насколько две строки должны быть похожи, чтобы показывать их парой с пословной
# разметкой. Ниже порога это не «правка строки», а «одну убрали, другую
# написали», и пословное сравнение нарисовало бы случайные совпадения предлогов.
_PAIR_RATIO = 0.4


def _pairable(before, after):
    return difflib.SequenceMatcher(None, before, after).ratio() >= _PAIR_RATIO


def _plain_row(op, line):
    """Строка вывода как есть: текстом, а у записи таблицы — ещё и ячейками."""
    row = {'op': op, 'text': line['text']}
    if line['cells']:
        row['cells'] = line['cells']
        row['columns'] = line['columns']
        row['head'] = line['head']
    return row


def _column_name(columns, index):
    if columns and index < len(columns):
        return columns[index] or None
    return None


def _table_change(left, right):
    """Правка записи таблицы: что изменилось в КАЖДОЙ графе.

    Возвращает None, когда ячейки не сходятся по числу (colspan, объединённые
    строки — таких статей в проде 30). Тогда запись сравнивается как обычный
    текст: показать «графа 7» там, где граф на самом деле разное количество,
    хуже, чем не называть их вовсе.
    """
    if not left['cells'] or not right['cells']:
        return None
    if len(left['cells']) != len(right['cells']):
        return None
    columns = right['columns'] or left['columns']
    cells = []
    for index, (was, now) in enumerate(zip(left['cells'], right['cells'])):
        cell = {'name': _column_name(columns, index),
                'before': was, 'after': now, 'changed': was != now}
        if cell['changed'] and was and now:
            cell['before_parts'], cell['after_parts'] = _inline_parts(was, now)
        cells.append(cell)
    return cells


def diff_blocks(before_html, after_html, context=3, max_rows=MAX_ROWS):
    """Построчное сравнение двух тел статьи.

    Возвращает плоский список строк вывода (op: same | gap | del | ins | change)
    — единой лентой, а не двумя колонками: на телефоне две колонки текста
    нечитаемы, а лента одинаково работает всюду.

    Неизменённые куски сворачиваются, оставляя `context` строк вокруг правки:
    статья на восемьсот абзацев, где поправили один, иначе выдаёт восемьсот
    строк «без изменений», среди которых правку надо искать.

    Запись таблицы остаётся ОДНОЙ строкой вывода и несёт свои ячейки: у правки
    внутри такой записи в `cells` лежит разбор по графам, и экран показывает
    только изменившиеся — «Актуальность: Активная → Завершена» вместо одиннадцати
    значений подряд, из которых десять те же самые.
    """
    before = html_to_lines(before_html)
    after = html_to_lines(after_html)
    before_text = [line['text'] for line in before]
    after_text = [line['text'] for line in after]
    matcher = difflib.SequenceMatcher(None, before_text, after_text, autojunk=False)
    opcodes = matcher.get_opcodes()

    rows, added, removed, truncated = [], 0, 0, False

    def put(row):
        nonlocal truncated
        if len(rows) >= max_rows:
            truncated = True
            return
        rows.append(row)

    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == 'equal':
            lines = before[i1:i2]
            # У первого куска нужен только хвост (что идёт ПЕРЕД правкой), у
            # последнего — только голова. Иначе сравнение начинается с трёх
            # строк вступления, к правке отношения не имеющих.
            head = 0 if index == 0 else min(context, len(lines))
            tail = 0 if index == len(opcodes) - 1 else min(context, len(lines) - head)
            for line in lines[:head]:
                put(_plain_row('same', line))
            skipped = len(lines) - head - tail
            if skipped > 0:
                put({'op': 'gap', 'skipped': skipped})
            if tail:
                for line in lines[len(lines) - tail:]:
                    put(_plain_row('same', line))
            continue

        removed += i2 - i1
        added += j2 - j1

        if tag == 'delete':
            for line in before[i1:i2]:
                put(_plain_row('del', line))
            continue
        if tag == 'insert':
            for line in after[j1:j2]:
                put(_plain_row('ins', line))
            continue

        # replace: строки сопоставляем по порядку, пока они похожи.
        paired = min(i2 - i1, j2 - j1)
        for shift in range(paired):
            left, right = before[i1 + shift], after[j1 + shift]
            if _pairable(left['text'], right['text']):
                cells = _table_change(left, right)
                row = {'op': 'change', 'before': left['text'], 'after': right['text']}
                if cells:
                    row['cells'] = cells
                    row['columns'] = right['columns'] or left['columns']
                    row['head'] = right['head']
                else:
                    row['before_parts'], row['after_parts'] = _inline_parts(
                        left['text'], right['text'])
                put(row)
            else:
                put(_plain_row('del', left))
                put(_plain_row('ins', right))
        for line in before[i1 + paired:i2]:
            put(_plain_row('del', line))
        for line in after[j1 + paired:j2]:
            put(_plain_row('ins', line))

    return {'rows': rows, 'added': added, 'removed': removed,
            'truncated': truncated,
            'blocks_before': len(before), 'blocks_after': len(after)}


def _field(before, after, key):
    """Изменение одного текстового поля. None — поле не трогали."""
    was, now = before.get(key), after.get(key)
    if (was or '') == (now or ''):
        return None
    return {'before': was, 'after': now}


def diff_states(before, after, context=3, max_rows=MAX_ROWS):
    """Полное сравнение двух редакций: заголовок, аннотация, статус и текст."""
    body = diff_blocks(before.get('content'), after.get('content'),
                       context=context, max_rows=max_rows)
    fields = [_field(before, after, key) for key in ('title', 'summary', 'status')]
    body_changed = (before.get('content') or '') != (after.get('content') or '')
    return {
        'title': fields[0],
        'summary': fields[1],
        'status': fields[2],
        'body': body,
        # Правка тронула разметку, а не слова: обёрнутый в цитату абзац, снятое
        # выделение, переставленная колонка. Сравнение текста тут честно пусто,
        # и без отдельного признака экран сказал бы «различий нет» там, где в
        # списке редакций стоит «Текст». Расхождение между двумя своими же
        # экранами читается как поломка, поэтому случай назван словами.
        'markup_only': body_changed and not body['added'] and not body['removed'],
        # «Различий нет» решает сервер: у браузера исходных тел нет, и проверить
        # ему нечего, кроме пустого списка строк, — а он пуст и когда статья не
        # менялась, и когда вывод целиком свернулся в пропуск.
        'identical': not body_changed and not any(fields),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Сборка списка редакций
# ─────────────────────────────────────────────────────────────────────────────

_STATE_KEYS = ('title', 'summary', 'status', 'content_hash')


def _state(row):
    return tuple((row.get(key) or '') for key in _STATE_KEYS)


def _iso(value):
    """Время — строкой ISO без зоны, и это не мелочь.

    В базе оно уже местное (Asia/Almaty, см. schema._NOW), а Flask сериализует
    datetime как «… GMT» — браузер прибавляет к местному ещё пять часов. На
    журнале вики этот баг уже случался и чинился ровно так же
    (structure.list_audit), второй раз наступать незачем.
    """
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _who(row):
    return {'editor_id': row.get('editor_id'),
            'editor_name': row.get('editor_name'),
            'created_at': _iso(row.get('created_at')),
            'comment': row.get('change_comment') or None}


# Насколько отметки снимка и статьи могут разойтись, чтобы считать их одной
# правкой. В проде разницы нет вовсе: снимок и UPDATE идут одной транзакцией и
# берут одно и то же CURRENT_TIMESTAMP (проверено на всех 305 статьях). Но
# равенство «секунда в секунду» — слишком тонкая опора: на стенде, где каждый
# запрос сам себе транзакция, отметки расходятся на микросекунды, и пометка
# «Откат» вместе с комментарием правки молча пропадала. Архивирование, ради
# которого проверка и заведена, отстоит от последней правки на минуты и дни.
_SAME_EDIT_WINDOW = datetime.timedelta(seconds=1)


def _same_edit(last, current):
    """Описывает ли последняя строка версий ту же правку, что и статья."""
    if not last:
        return False
    snapshot, updated = last.get('created_at'), current.get('updated_at')
    if snapshot is None or updated is None:
        return False
    if snapshot == updated:
        return True
    try:
        return abs(updated - snapshot) <= _SAME_EDIT_WINDOW
    except TypeError:
        return False


def _changed_fields(older, newer):
    """Что отличает редакцию от предыдущей. Порядок — от важного к мелкому."""
    fields = []
    if older.get('content_hash') != newer.get('content_hash'):
        fields.append('content')
    if (older.get('title') or '') != (newer.get('title') or ''):
        fields.append('title')
    if (older.get('summary') or '') != (newer.get('summary') or ''):
        fields.append('summary')
    if (older.get('status') or '') != (newer.get('status') or ''):
        fields.append('status')
    return fields


def build_history(rows, current=None):
    """Редакции статьи, новые сверху.

    rows — строки `wiki_article_versions` по возрастанию version_number, БЕЗ
    тела (только `content_hash`): у самой большой статьи прода тело весит 90 КБ,
    и десять таких в память ради списка тянуть незачем.

    current — текущее состояние статьи (то же плюс updated_by/updated_at).
    """
    rows = list(rows or [])
    entries = []

    for index, row in enumerate(rows):
        # Состояние из строки создала ПРЕДЫДУЩАЯ правка; у создания статьи
        # строка своя собственная.
        author = rows[index - 1] if index else row
        state = _state(row)

        if entries and entries[-1]['_state'] == state:
            entry = entries[-1]
            entry['version_ids'].append(row['id'])
            # Снимок «до первой правки» повторяет создание статьи: автор у него
            # тот же самый, и «ещё одно сохранение» здесь было бы выдумкой.
            if author['id'] != entry['_author_row_id']:
                entry['extra_saves'].append(_who(author))
            continue

        entry = {
            'key': 'v%s' % row['id'],
            'version_id': row['id'],
            'version_ids': [row['id']],
            'title': row.get('title'),
            'summary': row.get('summary'),
            'status': row.get('status'),
            'content_hash': row.get('content_hash'),
            'content_len': row.get('content_len'),
            'is_current': False,
            'extra_saves': [],
            'restored_from_version_id': author.get('restored_from_version_id'),
            '_state': state,
            '_author_row_id': author['id'],
        }
        entry.update(_who(author))
        entries.append(entry)

    if current:
        state = _state(current)
        if entries and entries[-1]['_state'] == state:
            # Текст статьи совпал с последним снимком — значит последняя правка
            # ничего в нём не изменила, и отдельной редакции здесь нет. Но
            # сохранение было, и человек был: у «Всех акций» это перенос статьи
            # 18.08.2026 с комментарием «Убран дубль», который иначе пропал бы
            # из истории вовсе.
            entries[-1]['is_current'] = True
            if rows and rows[-1]['id'] != entries[-1]['_author_row_id']:
                entries[-1]['extra_saves'].append(_who(rows[-1]))
        else:
            last = rows[-1] if rows else None
            same_edit = _same_edit(last, current)
            entry = {
                'key': 'current',
                'version_id': None,
                'version_ids': [],
                'title': current.get('title'),
                'summary': current.get('summary'),
                'status': current.get('status'),
                'content_hash': current.get('content_hash'),
                'content_len': current.get('content_len'),
                'is_current': True,
                'extra_saves': [],
                'restored_from_version_id': (
                    last.get('restored_from_version_id') if same_edit else None),
                '_state': state,
                '_author_row_id': None,
            }
            # Кто и когда — из САМОЙ статьи, а не из последнего снимка. Разница
            # видна на архивировании: delete_article двигает updated_at, но
            # снимка не пишет, и метка последней строки версий описывала бы
            # прошлую правку. В обычном же случае оба источника совпадают
            # секунда в секунду — проверено на всех 305 статьях прода.
            entry.update({
                'editor_id': current.get('updated_by'),
                'editor_name': current.get('updated_by_name'),
                'created_at': _iso(current.get('updated_at')),
                # Комментарий к правке живёт только в снимке, и брать его можно
                # лишь тогда, когда снимок относится к ЭТОЙ правке.
                'comment': (_who(last)['comment'] if same_edit else None),
            })
            entries.append(entry)

    for index, entry in enumerate(entries):
        entry['changed'] = (_changed_fields(entries[index - 1], entry)
                            if index else [])
        entry['is_first'] = index == 0
        entry['saves'] = 1 + len(entry['extra_saves'])
        entry.pop('_state', None)
        entry.pop('_author_row_id', None)

    entries.reverse()
    return entries
