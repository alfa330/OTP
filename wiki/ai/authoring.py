# -*- coding: utf-8 -*-
"""Сборка статьи вики из загруженного документа.

ГЛАВНОЕ РЕШЕНИЕ: таблицы через модель НЕ ПРОХОДЯТ. Там, где документ уже даёт
готовую сетку (DOCX через mammoth, XLSX/CSV через openpyxl), таблица вырезается
из текста, на её место встаёт маркер, и модель работает только с прозой. Готовый
HTML таблицы возвращается на место маркера дословно.

Почему так, а не «попросить модель аккуратно перенести таблицу». В корпусе вики
63 таблицы, в них 1618 ячеек, а colspan и rowspan встречаются по 1913 раза —
объединённые ячейки здесь норма, а не исключение. Перенос такой сетки языковой
моделью это игра, где выигрыш «получилось как было», а проигрыш — молча съеденная
строка с ценой или сдвинутая на одну колонку комиссия. Проверить такое глазами
нельзя: чтобы заметить пропавшую строку, надо знать, что она была. Поэтому сетка
переносится программой, а модель занимается тем, что действительно умеет —
заголовками, порядком разделов и связным текстом.

Обратная сторона: PDF и скан. Там сетки нет ВООБЩЕ — pypdf выдаёт из таблицы
поток слов, а из скана и вовсе ничего (importer на таком отказывает прямым
текстом: «в PDF нет текстового слоя»). Разложить слова обратно по колонкам из
текста нельзя, поэтому такой файл целиком уходит в модель, которая читает
страницу с разметкой (providers.generate_document). Это единственный путь, и
именно поэтому за таблицами из PDF следит отдельная проверка целостности, а
результат обязательно смотрит человек: статья создаётся ЧЕРНОВИКОМ в редакторе,
а не записывается в базу сама.

КАНОН СТАТЬИ выведен из корпуса (36 статей прода, замер 11.08.2026), а не придуман:
  * заголовки в теле — h1 (144 раза, первый заголовок статьи в 20 из 36), внутри
    h2 (88) и h3 (35). h4 встречается 260 раз, но 130 из них в одной статье, и
    ЧАНКЕР ИНДЕКСА границей его не считает — то есть раздел под h4 для поиска
    помощника не существует. Поэтому h4 и глубже сводятся к h3;
  * summary есть у 31 статьи из 36 — значит краткое описание часть канона, а не
    необязательное поле;
  * медианная длина текста 2871 знак: статья это несколько разделов, а не
    простыня;
  * оформление (font-size, цвета, произвольные class) у модели вырезается
    начисто. Ровно в этом смысл «единого вида»: 2756 инлайновых style в корпусе
    появились из вставок из Word, и добавлять к ним ещё и машинные не нужно.
"""

import re

from bs4 import BeautifulSoup

from .answer import ungrounded_numbers
from ..sanitize import sanitize_html, to_plain_text

# Маркер таблицы. Квадратные скобки и русское слово выбраны нарочно: такой токен
# модель переносит дословно, а если всё же переврёт — регулярка ниже узнаёт его
# и по искажённому виду (лишние пробелы, потерянные скобки).
TABLE_TOKEN = '[[ТАБЛИЦА-%d]]'
_TABLE_TOKEN_RE = re.compile(r'\[{1,2}\s*ТАБЛИЦА[\s-]*(\d+)\s*\]{1,2}', re.I)

# Теги, которые модели разрешено принести в теле статьи. Список узкий намеренно:
# из него нельзя собрать ни оформительский мусор, ни вложенные контейнеры.
_ALLOWED = {
    'p', 'h1', 'h2', 'h3', 'strong', 'b', 'em', 'i', 'u', 's', 'mark', 'code',
    'pre', 'blockquote', 'ul', 'ol', 'li', 'a', 'br', 'hr',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'caption',
}
# Заголовки глубже третьего уровня: см. про h4 в шапке.
_DEMOTE = {'h4': 'h3', 'h5': 'h3', 'h6': 'h3'}
# Атрибуты, выживающие у модельной разметки. Ни style, ни class.
_KEEP_ATTRS = {'a': ('href', 'title'), 'th': ('colspan', 'rowspan'),
               'td': ('colspan', 'rowspan')}

MAX_OUTPUT_TOKENS = 8000        # статья длиннее ответа в чате; медиана 2871 знак

SYSTEM_PROMPT = """Ты — редактор корпоративной вики таксопарка. Ты превращаешь загруженный документ в статью вики.

ЧТО ТЫ ДЕЛАЕШЬ
Приводишь содержимое документа к виду статьи: даёшь разделам заголовки, убираешь
служебный мусор документа, выстраиваешь порядок изложения. Ты НЕ пересказываешь и
НЕ сокращаешь по смыслу: все факты, числа, суммы, сроки, названия, телефоны и
адреса обязаны остаться. Ничего не добавляй от себя.

ФОРМАТ ОТВЕТА — ровно три части, каждая с новой строки:
НАЗВАНИЕ: короткое название статьи (до 90 знаков, без кавычек и без слова «документ»)
КРАТКО: одно предложение о том, что внутри и кому пригодится (до 200 знаков)
СТАТЬЯ:
затем HTML тела статьи

РАЗМЕТКА ТЕЛА — только эти теги:
<h1> раздел, <h2> подраздел, <h3> глубже
<p> абзац, <ul>/<ol> с <li> список
<strong> важное, <blockquote> предупреждение
<table><thead><tr><th>…</th></tr></thead><tbody><tr><td>…</td></tr></tbody></table>
<a href="…"> ссылка

ЗАПРЕЩЕНО: style, class, font, div, span, картинки, h4 и глубже, теги <html> и
<body>, обёртка ```html, любые пояснения до или после ответа.

ЖЁСТКИЕ ПРАВИЛА
1. Название статьи в тело не дублируй: первым в теле идёт содержание, а не
   заголовок с тем же текстом.
2. Маркеры вида [[ТАБЛИЦА-1]] — это вырезанные таблицы документа. Перенеси
   каждый маркер в ответ ДОСЛОВНО, отдельным абзацем, в подходящем по смыслу
   месте, и добавь перед ним заголовок или строку-подводку. Не пересказывай
   содержимое таблицы словами, не выдумывай его и не меняй номер маркера.
3. Ни одного числа, которого нет в документе. Не округляй, не пересчитывай, не
   переводи валюту.
4. Язык статьи — язык документа.
5. Пустых абзацев, повторов заголовков и фраз вроде «в этом документе
   описывается» быть не должно.
"""

# Отдельная инструкция для файла, который модель читает сама (PDF, скан, фото).
FILE_PROMPT_EXTRA = """
ЭТО ФАЙЛ, КОТОРЫЙ ТЫ ЧИТАЕШЬ САМ. Маркеров таблиц в нём нет — таблицы ты обязана
собрать сама и оформить тегом <table> с шапкой <th>. Читай страницу как страницу:
что стоит в колонках — то и должно оказаться в колонках. Если ячейка объединена,
используй colspan или rowspan. Если текст на странице нечитаем, не угадывай —
пропусти и не выдумывай значение.
"""


def _envelope(text):
    """Разбор ответа модели: (название, кратко, html тела)."""
    raw = str(text or '').strip()
    # Обёртку ```html модели ставят даже под запретом — снимаем молча.
    raw = re.sub(r'^```(?:html)?\s*|\s*```$', '', raw, flags=re.I | re.M).strip()

    title = summary = ''
    match = re.search(r'^\s*НАЗВАНИЕ\s*:\s*(.+)$', raw, re.I | re.M)
    if match:
        title = match.group(1).strip().strip('«»"\'')
    match = re.search(r'^\s*КРАТКО\s*:\s*(.+)$', raw, re.I | re.M)
    if match:
        summary = match.group(1).strip()

    body = raw
    match = re.search(r'^[ \t]*СТАТЬЯ[ \t]*:[ \t]*\r?\n?', raw, re.I | re.M)
    if match:
        body = raw[match.end():]
    else:
        # Конверта нет — берём всё, что похоже на HTML, начиная с первого тега.
        first = re.search(r'<(h1|h2|h3|p|ul|ol|table|blockquote)\b', body, re.I)
        if first:
            body = body[first.start():]
    return title[:200], summary[:400], body.strip()


def protect_tables(html):
    """Вырезает таблицы, ставит маркеры. Возвращает (html, [таблицы]).

    Таблица уходит из поля зрения модели целиком — вместе с объединёнными
    ячейками, которые она почти наверняка потеряла бы (см. шапку).
    """
    soup = BeautifulSoup(str(html or ''), 'html.parser')
    tables = []
    for index, table in enumerate(soup.find_all('table'), start=1):
        tables.append(normalize_table(table))
        marker = soup.new_tag('p')
        marker.string = TABLE_TOKEN % index
        table.replace_with(marker)
    return str(soup), tables


def _table_caption(table_html, limit=120):
    """Шапка таблицы одной строкой — чтобы модель понимала, о чём маркер."""
    soup = BeautifulSoup(str(table_html or ''), 'html.parser')
    row = soup.find('tr')
    if not row:
        return ''
    cells = [' '.join((cell.get_text(' ', strip=True) or '').split())
             for cell in row.find_all(('th', 'td'))]
    return ' | '.join(c for c in cells if c)[:limit]


def table_hints(tables):
    """Подсказка к маркерам: номер, размер и шапка каждой таблицы."""
    lines = []
    for index, table in enumerate(tables, start=1):
        soup = BeautifulSoup(str(table or ''), 'html.parser')
        rows = len(soup.find_all('tr'))
        caption = _table_caption(table)
        lines.append('%s — строк: %d; колонки: %s'
                     % (TABLE_TOKEN % index, rows, caption or 'без шапки'))
    return '\n'.join(lines)


def normalize_table(table):
    """Единый вид таблицы: структура остаётся, оформление уходит.

    colspan и rowspan сохраняются — это смысл, а не оформление. А вот style,
    class, width и colwidth убираются: из-за них таблицы в корпусе выглядят
    каждая по-своему, хотя данные в них однотипные.
    """
    if isinstance(table, str):
        table = BeautifulSoup(table, 'html.parser').find('table')
        if table is None:
            return ''
    for tag in table.find_all(('colgroup', 'col')):
        tag.decompose()
    for cell in table.find_all(('td', 'th')):
        # mammoth оборачивает каждую клетку Word в <p> — проверено на реальном
        # файле. Одинокий абзац внутри клетки это оформление конвертера, а не
        # структура документа, и в единый вид он не входит.
        children = [child for child in cell.find_all(recursive=False)]
        if len(children) == 1 and children[0].name == 'p':
            children[0].unwrap()
    for tag in table.find_all(True):
        keep = _KEEP_ATTRS.get(tag.name, ())
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in keep}
    table.attrs = {}
    _ensure_header(table)
    return str(table)


def _ensure_header(table):
    """Первая строка становится шапкой, если таблица её потеряла.

    Проверено на корпусе: у 63 таблиц 295 th на 1618 td — шапка есть почти
    везде, и таблица без неё читается как набор чисел без подписей.
    """
    if table.find('th'):
        return
    first = table.find('tr')
    if not first:
        return
    cells = first.find_all('td')
    if not cells or len(cells) < 2:
        return
    texts = [cell.get_text(' ', strip=True) for cell in cells]
    # Шапка — это короткие непустые подписи, а не данные. Строка из чисел
    # шапкой не считается: в выгрузках Excel первая строка бывает данными.
    if any(not text or len(text) > 60 for text in texts):
        return
    if sum(1 for text in texts if re.fullmatch(r'[\d\s.,%-]+', text)) > len(texts) / 2:
        return
    for cell in cells:
        cell.name = 'th'


_EMPTY_TEXT = re.compile(r'^[\s ]*$')


def canonicalize(html):
    """Привести тело статьи к канону: теги, уровни заголовков, чистка мусора."""
    soup = BeautifulSoup(str(html or ''), 'html.parser')

    for tag in soup.find_all(('script', 'style', 'html', 'head', 'body', 'meta',
                              'link', 'img', 'figure', 'figcaption', 'details',
                              'summary', 'iframe')):
        if tag.name in ('html', 'body'):
            tag.unwrap()
        else:
            tag.decompose()

    for tag in soup.find_all(True):
        if tag.name in _DEMOTE:
            tag.name = _DEMOTE[tag.name]
        if tag.name not in _ALLOWED:
            tag.unwrap()
            continue
        keep = _KEEP_ATTRS.get(tag.name, ())
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in keep}

    # Пустые абзацы и заголовки. Их приносит и Word, и модель, а в статье они
    # выглядят разрывом вёрстки.
    for tag in soup.find_all(('p', 'h1', 'h2', 'h3', 'li', 'blockquote')):
        if not tag.find(('table', 'br')) and _EMPTY_TEXT.match(tag.get_text('', strip=False) or ''):
            tag.decompose()

    for table in soup.find_all('table'):
        normalize_table(table)

    _lift_headings(soup)
    return str(soup).strip()


def _lift_headings(soup):
    """Поднять заголовки так, чтобы верхний уровень был h1.

    Без этого «единый вид» не получается: замер на трёх настоящих файлах показал,
    что из Word модель принесла разделы h2, а из Excel и PDF — h1, хотя канон
    один. Уровень заголовка это не смысл, а оформление, и выравнивать его должна
    программа, а не удача.

    Сдвиг сохраняет ВЛОЖЕННОСТЬ: h2/h3 превращаются в h1/h2, то есть иерархия
    документа остаётся, меняется только точка отсчёта.
    """
    levels = [int(tag.name[1]) for tag in soup.find_all(('h1', 'h2', 'h3'))]
    if not levels:
        return
    shift = min(levels) - 1
    if shift <= 0:
        return
    for tag in soup.find_all(('h1', 'h2', 'h3')):
        tag.name = 'h%d' % (int(tag.name[1]) - shift)


def drop_leading_title(html, title):
    """Убрать первый заголовок, если он повторяет название статьи.

    Название — отдельное поле, и в теле он даёт две одинаковые строки подряд.
    """
    if not title:
        return html
    soup = BeautifulSoup(str(html or ''), 'html.parser')
    for tag in soup.find_all(True, recursive=False):
        if tag.name not in ('h1', 'h2', 'h3'):
            break
        if _squash(tag.get_text(' ', strip=True)) == _squash(title):
            tag.decompose()
        break
    return str(soup).strip()


def _squash(text):
    return ' '.join(str(text or '').split()).lower()


def restore_tables(html, tables):
    """Вернуть таблицы на места маркеров. Потерянные — в конец, с предупреждением.

    Возвращает (html, [номера потерянных]). Таблица не теряется НИ ПРИ КАКИХ
    обстоятельствах: если модель проглотила маркер, таблица уезжает в конец
    статьи под отдельный заголовок. Тихо выбросить данные документа нельзя.
    """
    text = str(html or '')
    used = set()

    def substitute(match):
        index = int(match.group(1))
        if 1 <= index <= len(tables):
            used.add(index)
            return tables[index - 1]
        return ''            # номер, которого не было — маркер просто убираем

    text = _TABLE_TOKEN_RE.sub(substitute, text)
    # Маркер мог уехать внутрь абзаца — тогда после подстановки остаётся <p>
    # с таблицей внутри. Для браузера это ошибка вложенности, разворачиваем.
    soup = BeautifulSoup(text, 'html.parser')
    for table in soup.find_all('table'):
        parent = table.parent
        # blockquote и li здесь не для красоты: чанкер индекса обходит таблицу
        # только через контейнеры (div/section/details/body), а внутри «листового»
        # элемента до неё не доходит — и таблица уезжает в индекс потоком значений
        # «Алматы 5% Астана 7%» вместо «Город: Алматы; Комиссия: 5%».
        if parent is not None and parent.name in ('p', 'li', 'strong', 'em',
                                                  'blockquote'):
            parent.insert_before(table.extract())
            if _EMPTY_TEXT.match(parent.get_text('', strip=False) or ''):
                parent.decompose()

    lost = [i for i in range(1, len(tables) + 1) if i not in used]
    if lost:
        tail = ['<h1>Таблицы из документа</h1>']
        for index in lost:
            tail.append(tables[index - 1])
        soup.append(BeautifulSoup(''.join(tail), 'html.parser'))
    return str(soup).strip(), lost


_TRUNCATED = ('max_tokens', 'maxtokens', 'length')


def truncation_warning(meta):
    """Модель не дописала статью. Проверяется, потому что молча не видно.

    У Gemini это finishReason='MAX_TOKENS', у OpenAI-совместимых — 'length'.
    Ответ при этом приходит с HTTP 200 и выглядит нормальным: обрыв заметен
    только по тому, что последний раздел кончается на полуслове. На длинном
    документе это самый вероятный дефект, и он должен быть НАЗВАН.
    """
    finish = str((meta or {}).get('finish') or '').strip().lower()
    if finish in _TRUNCATED:
        return ('ИИ не дописал статью до конца — документ длиннее, чем модель '
                'может выдать за раз. Проверьте конец текста и допишите вручную')
    return None


def structure_warnings(*, source_html, source_text, result_html, lost_tables):
    """Что модель могла испортить. Список для человека, а не для лога.

    Предупреждения, а не отказ: статья открывается в редакторе, и решение за
    редактором. Отказ здесь был бы хуже — он оставил бы человека вообще без
    заготовки, тогда как испорченную строку он поправит за секунду.
    """
    warnings = []

    if lost_tables:
        warnings.append(
            'Модель не расставила %d из таблиц документа (%s) — они добавлены в '
            'конец статьи, перенесите их по смыслу'
            % (len(lost_tables), ', '.join('№%d' % i for i in lost_tables)))

    source_tables = len(BeautifulSoup(str(source_html or ''), 'html.parser')
                        .find_all('table'))
    result_soup = BeautifulSoup(str(result_html or ''), 'html.parser')
    result_tables = len(result_soup.find_all('table'))
    if source_tables and result_tables < source_tables:
        warnings.append('Таблиц в документе %d, в статье %d — проверьте, что '
                        'ничего не потеряно' % (source_tables, result_tables))

    if source_text:
        invented = ungrounded_numbers(result_soup.get_text(' ', strip=True),
                                      [{'text': source_text}])
        if invented:
            warnings.append('Числа, которых нет в документе: %s — проверьте их'
                            % ', '.join(invented[:8]))

        # Сокращение — тоже дефект: статья обязана сохранить содержание, а не
        # пересказать его. Порог мягкий: разметка документа выкидывает служебные
        # строки, поэтому падение до 60 % считаем нормой.
        result_len = len(result_soup.get_text(' ', strip=True))
        if result_len < len(source_text) * 0.6:
            warnings.append('Текста в статье заметно меньше, чем в документе '
                            '(%d знаков против %d) — проверьте, всё ли перенесено'
                            % (result_len, len(source_text)))
    if not result_soup.find(('h1', 'h2', 'h3')):
        warnings.append('В статье нет ни одного заголовка — разделите её на разделы')
    return warnings


def build_user_prompt(*, filename, kind, body_html, tables):
    """Запрос к модели по разобранному документу."""
    parts = ['ФАЙЛ: %s (%s)' % (filename or 'без имени', kind or 'документ')]
    hints = table_hints(tables)
    if hints:
        parts.append('ТАБЛИЦЫ, ВЫРЕЗАННЫЕ ИЗ ДОКУМЕНТА (переносить маркерами '
                     'дословно, содержимое не пересказывать):\n' + hints)
    parts.append('СОДЕРЖИМОЕ ДОКУМЕНТА:\n' + str(body_html or ''))
    return '\n\n'.join(parts)


def compose(*, filename, kind, source_html='', source_text='', generate_fn,
            blob=None, mime=None, generate_file_fn=None):
    """Документ -> черновик статьи.

    Две ветки, и различие между ними принципиальное:
      * blob задан — файл читает сама модель (PDF, скан, фото), таблицы собирает
        она же, и за ними следит проверка целостности;
      * иначе на входе уже разобранный HTML, таблицы из него вырезаются и
        возвращаются на место программой.

    Возвращает {title, summary, content, warnings, meta, tables}.
    """
    if blob is not None:
        if generate_file_fn is None:
            raise ValueError('для файла нужен generate_file_fn')
        text, meta = generate_file_fn(
            SYSTEM_PROMPT + FILE_PROMPT_EXTRA,
            'ФАЙЛ: %s (%s). Собери из него статью вики по правилам выше.'
            % (filename or 'без имени', kind or 'документ'),
            blob=blob, mime=mime, max_tokens=MAX_OUTPUT_TOKENS)
        tables = []
        protected = ''
    else:
        protected, tables = protect_tables(source_html)
        text, meta = generate_fn(
            SYSTEM_PROMPT,
            build_user_prompt(filename=filename, kind=kind, body_html=protected,
                              tables=tables),
            max_tokens=MAX_OUTPUT_TOKENS)

    title, summary, body = _envelope(text)
    body = canonicalize(body)
    body = drop_leading_title(body, title)
    body, lost = restore_tables(body, tables)
    clean = sanitize_html(body)

    warnings = structure_warnings(
        source_html=source_html or protected, source_text=source_text,
        result_html=clean, lost_tables=lost)
    cut = truncation_warning(meta)
    if cut:
        warnings.insert(0, cut)

    return {
        'title': title,
        'summary': summary or to_plain_text(clean, limit=280),
        'content': clean,
        'warnings': warnings,
        'tables': len(tables),
        'meta': meta,
    }
