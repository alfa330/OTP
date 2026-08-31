# -*- coding: utf-8 -*-
"""Правка СУЩЕСТВУЮЩЕЙ статьи: обновление документом и правка по указанию.

Отличие от wiki/ai/authoring.py в одном, но принципиальном: там статья пишется с
нуля, и терять нечего, а здесь на входе уже готовый текст, который люди читали и
на который ссылались. Поэтому весь модуль построен вокруг одного правила:

    МОЛЧА ТЕРЯТЬ НЕЛЬЗЯ НИЧЕГО.

Из него следует всё остальное:

  * таблицы и картинки СТАТЬИ вырезаются маркерами ровно так же, как таблицы
    документа при сборке (см. шапку authoring.py про 1913 объединённых ячеек).
    Модель их не переписывает — она расставляет маркеры, а КЛЕТКИ правит
    точечными указаниями «таблица, строка, колонка: было → стало», которые
    применяет программа (wiki/ai/tablepatch.py). Без этого механизма обновление
    портило данные: замер на проде показал, что модель, не имея доступа к
    клетке, приписывала рядом вторую таблицу, и в статье оказывались сразу
    «14 дней» и «20 дней»;
  * таблицы ДОКУМЕНТА продолжают ту же нумерацию, и модель не знает, какая
    откуда: для неё это просто блоки, которые надо разложить по местам. Так
    обновление таблицы сводится к замене одного маркера на другой;
  * пропавший маркер не стоит статье таблицы — она уезжает в конец с
    предупреждением (restore_tables);
  * ЧТО ИМЕННО ИЗМЕНИЛОСЬ, модель обязана перечислить. Список изменений — не
    украшение: без него редактор получает 7000 знаков нового текста и не может
    проверить его иначе, чем прочитав целиком;
  * ВОПРОСЫ важнее догадок. Если документ противоречит статье (та же акция с
    другой датой) или обрывается на полуслове, модель спрашивает, а не решает
    сама. Вопрос стоит редактору десяти секунд, а тихо подменённая дата в
    регламенте — денег.

Ничего не сохраняется автоматически: результат открывается в редакторе, и
кнопку «Сохранить» нажимает человек.
"""

import re

from bs4 import BeautifulSoup

from .answer import ungrounded_numbers
from . import markup
from . import tablepatch
from .authoring import (MAX_OUTPUT_TOKENS, append_links, canonicalize,
                        images_block, links_block, missing_links,
                        protect_tables, removed_images, restore_tables,
                        structure_warnings, table_hints, truncation_warning)
from ..sanitize import sanitize_html, to_plain_text

_CHANGES_RE = re.compile(r'^[ \t]*ИЗМЕНЕНИЯ[ \t]*:[ \t]*\r?\n?(.*?)(?=^[ \t]*(?:ВОПРОСЫ|СТАТЬЯ)[ \t]*:|\Z)',
                         re.I | re.M | re.S)
_QUESTIONS_RE = re.compile(r'^[ \t]*ВОПРОСЫ[ \t]*:[ \t]*\r?\n?(.*?)(?=^[ \t]*(?:ИЗМЕНЕНИЯ|СТАТЬЯ)[ \t]*:|\Z)',
                           re.I | re.M | re.S)
_BODY_RE = re.compile(r'^[ \t]*СТАТЬЯ[ \t]*:[ \t]*\r?\n?', re.I | re.M)
_BULLET = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s*')

_FORMAT_BLOCK = """
ФОРМАТ ОТВЕТА — ровно четыре части, каждая со своей строки:
ПРАВКИ ТАБЛИЦ:
- точечные правки клеток, по одной на строку (формат ниже); «нет», если не нужны
ИЗМЕНЕНИЯ:
- по одному пункту на каждую правку, коротко и по делу («срок акции 50п-5к: 14 → 20 дней»)
ВОПРОСЫ:
- то, чего не решить без человека; если вопросов нет — напиши «нет»
СТАТЬЯ:
затем ПОЛНЫЙ HTML статьи целиком, а не фрагмент

РАЗМЕТКА — только эти теги: h1 (раздел), h2, h3, p, ul/ol с li, strong,
blockquote, table с thead/tbody/th/td, a href — плюс оформительские блоки,
описанные ниже. Запрещено: style, class, span, произвольный <div>, картинки,
h5 и глубже, обёртка ```html, пояснения вне трёх частей.

ОФОРМЛЕНИЕ, КОТОРОЕ УЖЕ ЕСТЬ В СТАТЬЕ, ПЕРЕНОСИ КАК ЕСТЬ. Блок, который ты
развернул в обычные абзацы, для читателя выглядит как пропавший блок — а
указание его трогать не просило.

ЖЁСТКИЕ ПРАВИЛА
1. Маркеры [[ТАБЛИЦА-N]] и [[КАРТИНКА-N]] переноси ДОСЛОВНО, каждый отдельным
   абзацем. Не пересказывай содержимое таблицы словами и не меняй номер. Данные
   внутри таблицы меняй ТОЛЬКО через блок «ПРАВКИ ТАБЛИЦ» — не приписывай рядом
   вторую таблицу с новыми значениями и не дублируй строки текстом. У маркера
   картинки менять можно ХВОСТ — её размер и выравнивание (см. «КАРТИНКИ И ИХ
   РАЗМЕР» в запросе); номер остаётся прежним.
2. Не удаляй разделы и факты, которых документ не касается. Ты правишь статью, а
   не пишешь её заново.
3. Числа, суммы, сроки, названия и ссылки — дословно из статьи или документа.
   Ничего не додумывай и не округляй.
4. Язык статьи не меняй.
5. В «ИЗМЕНЕНИЯ» не повторяй правки таблиц — они уже перечислены в блоке «ПРАВКИ
   ТАБЛИЦ». Там пиши только про текст: разделы, абзацы, формулировки.
6. Про исчезнувшую строку таблицы спрашивай ТОЛЬКО через «Т1 -СТРОКА N», а не
   ещё и в «ВОПРОСЫ»: один и тот же вопрос дважды заставляет искать между ними
   разницу, которой нет.
""" + tablepatch.PATCH_RULES + markup.MARKUP_GUIDE

UPDATE_PROMPT = """Ты — редактор корпоративной вики таксопарка. Тебе дают ТЕКУЩУЮ статью и НОВЫЙ документ по той же теме. Твоя работа — обновить статью.

ЧТО ЗНАЧИТ ОБНОВИТЬ
Строка за строкой сверить статью с документом: изменившееся — заменить, новое —
добавить на своё место по смыслу, исчезнувшее из документа — НЕ удалять молча, а
вынести вопросом. Всё, чего документ не касается, остаётся как было, дословно.
""" + _FORMAT_BLOCK

EDIT_PROMPT = """Ты — редактор корпоративной вики таксопарка. Тебе дают статью и УКАЗАНИЕ, что в ней поправить. Выполни ровно указание и ничего сверх него.

Указание касается только того, о чём в нём сказано. Остальной текст статьи
переноси дословно, вплоть до формулировок: правка «переписать в деловом тоне»
относится к тону, а не к фактам, а правка «убрать раздел про доставку» — только
к этому разделу.
""" + _FORMAT_BLOCK

FILE_EXTRA = """
НОВЫЙ ДОКУМЕНТ ТЫ ЧИТАЕШЬ САМ, файлом. Маркеров у его таблиц нет — если таблица
документа заменяет таблицу статьи, собери её сам тегом <table> с шапкой <th>, а
маркер устаревшей таблицы статьи убери. Читай страницу как страницу: что стоит в
колонках, то и должно оказаться в колонках.
"""


# Хвост в маркере картинки ([[КАРТИНКА-1 60% справа]]) — это её контролы.
# Без него в шаблоне маркер с контролами не опознавался: в списке изменений
# он утекал наружу как есть, а при сдвиге номеров оставался со старым.
_MARKER_RE = re.compile(r'\[\[(ТАБЛИЦА|КАРТИНКА)-(\d+)([^\]]*)\]\]', re.I)


def humanize(line):
    """Убрать служебные маркеры из текста, который прочитает человек.

    Маркеры — внутренняя механика переноса блоков, и в списке изменений они
    выглядят как утечка кода наружу: «добавлен вводный текст и [[ТАБЛИЦА-4]]».
    Замер на живом прогоне: маркеры попали и в изменения, и в вопрос редактору.
    """
    return _MARKER_RE.sub(
        lambda m: '%s %s' % ('таблица' if m.group(1).upper() == 'ТАБЛИЦА' else 'картинка',
                             m.group(2)),
        str(line or ''))


def dedupe(lines):
    """Убрать повторы по существу, а не по буквам.

    Нужно потому, что источников у списков два: правки клеток формирует
    программа, а модель дополняет их своим пересказом. Замер на проде: об одной
    и той же продлённой акции сообщалось дважды, а про исчезнувший «Розыгрыш
    Hyundai Elantra» задавалось два почти одинаковых вопроса. Дубль в таком
    списке хуже, чем кажется: читающий начинает искать между строками разницу,
    которой нет.

    Сравниваются первые слова И набор чисел — сходство требуется по обоим.
    Порог намеренно строгий: две разные формулировки одного факта тут НЕ
    склеиваются, и это осознанный выбор. Спрятать настоящее изменение хуже, чем
    показать его дважды, а различить «то же самое другими словами» и «другое
    изменение про ту же дату» без понимания смысла нельзя. Косметику здесь
    решает не код, а правило промпта: не пересказывать правки таблиц в списке
    изменений.
    """
    seen, out = [], []
    for line in lines:
        words = re.findall(r'[^\W\d_]{4,}', str(line or '').lower(), re.UNICODE)
        numbers = set(re.findall(r'\d[\d.,]*', str(line or '')))
        key = (frozenset(words[:6]), frozenset(numbers))
        if any(key[0] and key[0] & other[0] and key[1] == other[1] for other in seen):
            continue
        seen.append(key)
        out.append(line)
    return out


def _section(pattern, text):
    match = pattern.search(text or '')
    if not match:
        return []
    lines = []
    for raw in (match.group(1) or '').splitlines():
        line = humanize(_BULLET.sub('', raw).strip())
        if line and line.lower() not in ('нет', 'нет.', '—', '-'):
            lines.append(line)
    return lines[:20]


def parse_reply(text):
    """Ответ модели → (изменения, вопросы, html статьи)."""
    raw = re.sub(r'^```(?:html)?\s*|\s*```$', '', str(text or '').strip(),
                 flags=re.I | re.M).strip()
    changes = _section(_CHANGES_RE, raw)
    questions = _section(_QUESTIONS_RE, raw)
    match = _BODY_RE.search(raw)
    body = raw[match.end():] if match else raw
    if not match:
        # Конверта нет — берём с первого тега, иначе в статью уедет служебный текст.
        # div в списке ОБЯЗАТЕЛЕН: статья может начинаться с вводки или плашки,
        # и без него срез пришёлся бы на <p> ВНУТРИ блока — с потерянным
        # открывающим тегом и висящим </div> в конце.
        first = re.search(r'<(div|h1|h2|h3|p|ul|ol|table|blockquote)\b', body, re.I)
        body = body[first.start():] if first else ''
    return changes, questions, body.strip()


def _prepare(current_html, document_html):
    """Защитить блоки статьи и документа ОДНОЙ сквозной нумерацией.

    Сквозной она сделана нарочно: для модели это просто пронумерованные блоки, и
    «заменить старую таблицу новой» превращается в замену номера. Разные
    пространства имён заставляли бы её удерживать ещё и правило, какое из них
    чьё, — лишний повод ошибиться там, где ошибка дорога.
    """
    body, tables, images = protect_tables(current_html)
    doc_body, doc_tables, doc_images = ('', [], [])
    if document_html:
        doc_body, doc_tables, doc_images = protect_tables(document_html)
        shift, image_shift = len(tables), len(images)
        if shift or image_shift:
            def renumber(match):
                kind, number = match.group(1).upper(), int(match.group(2))
                base = shift if kind == 'ТАБЛИЦА' else image_shift
                # Хвост переносится как есть: номер сдвигается, контролы нет.
                return '[[%s-%d%s]]' % (kind, number + base, match.group(3))
            doc_body = _MARKER_RE.sub(renumber, doc_body)
    return body, doc_body, tables + doc_tables, images + doc_images


def _finish(text, *, current_html, tables, images, sources_text, meta,
            allow_table_patches=True, links=()):
    """Собрать результат правки и проверить его.

    sources_text обязан включать НАЗВАНИЕ статьи: на живом прогоне правка
    сохранила дату «24.07.2026» из заголовка, а проверка чисел искала её только
    в теле и объявила выдумкой. Ложное предупреждение обесценивает все
    остальные — их перестают читать.
    """
    changes, questions, body = parse_reply(text)

    # ПРАВКИ КЛЕТОК применяются здесь, до подстановки таблиц обратно: маркеры в
    # тексте те же, меняется только содержимое блоков, на которые они указывают.
    if allow_table_patches:
        patches = tablepatch.parse(text)
        if patches:
            tables, applied, asked, rejected = tablepatch.apply(tables, patches)
            changes = dedupe(applied + changes)
            questions = dedupe(asked + questions)
            if rejected:
                questions.append('ИИ сослался на несуществующие клетки (%s) — '
                                 'проверьте таблицы вручную'
                                 % '; '.join(rejected[:3]))

    # Считаем ДО подстановки: после неё маркера уже нет, и отличить «убрал по
    # указанию» от «потерял» будет нечем.
    dropped = removed_images(body)
    body = canonicalize(body)
    body, lost = restore_tables(body, tables, images)
    clean = sanitize_html(body)

    # Адрес, который модель не поставила, дописывается разделом в конец. Пустая
    # подпись «по ссылке» без адреса бесполезна.
    lost_links = missing_links(clean, links)
    if lost_links:
        clean = sanitize_html(append_links(clean, lost_links))

    warnings = structure_warnings(
        source_html=current_html, source_text=sources_text,
        result_html=clean, lost_tables=lost)
    cut = truncation_warning(meta)
    if cut:
        warnings.insert(0, cut)

    # Отдельная проверка ИМЕННО для правки: статья не должна похудеть без причины.
    # structure_warnings сравнивает с исходником в целом, а здесь важна утрата
    # относительно ТЕКУЩЕЙ статьи — то есть того, что уже читали люди.
    before = len(to_plain_text(current_html))
    after = len(to_plain_text(clean))
    if before and after < before * 0.75:
        warnings.append('Статья стала заметно короче: было %d знаков, стало %d — '
                        'проверьте, не пропал ли раздел' % (before, after))
    if lost_links:
        warnings.append(
            'ИИ не расставил %d ссылок документа (%s) — они добавлены разделом в '
            'конец, перенесите их по смыслу'
            % (len(lost_links),
               ', '.join((item.get('label') or item['url'])[:40]
                         for item in lost_links[:3])))
    if dropped:
        # Не ошибка, но и не мелочь: из статьи пропала иллюстрация, а файл
        # остался в бакете. Убрать картинку модель может только по прямому
        # указанию, и автор должен видеть, что указание сработало.
        warnings.append('ИИ убрал %s (%s) — так и было задумано?'
                        % ('картинку' if len(dropped) == 1 else 'картинки',
                           ', '.join('№%d' % number for number in dropped)))
    if not changes:
        warnings.append('ИИ не перечислил изменения — сверьте текст сами')

    return {'content': clean, 'changes': changes, 'questions': questions,
            'warnings': warnings, 'meta': meta}


def update_from_document(*, current_title, current_html, document_html='',
                         document_text='', filename='', kind='', generate_fn,
                         blob=None, mime=None, generate_file_fn=None, links=()):
    """Обновить статью новым документом. Возвращает content/changes/questions/warnings."""
    body, doc_body, tables, images = _prepare(
        current_html, '' if blob is not None else document_html)

    header = 'ТЕКУЩАЯ СТАТЬЯ: «%s»\n%s' % (current_title or 'без названия', body)
    hints = table_hints(tables)
    if hints:
        header += ('\n\nБЛОКИ (переносить маркерами дословно):\n' + hints)
    # Содержимое таблиц модель ВИДИТ — иначе ей нечего сверять с документом и
    # нечем указать номер клетки. Переписывать их она по-прежнему не может.
    grid = tablepatch.serialize(tables)
    if grid:
        header += ('\n\nСОДЕРЖИМОЕ ТАБЛИЦ СТАТЬИ (править только через '
                   '«ПРАВКИ ТАБЛИЦ»):\n' + grid)
    pictures = images_block(images)
    if pictures:
        header += '\n\n' + pictures

    # Ссылки нового документа. Для PDF это единственный способ их получить:
    # адрес там в аннотации, а не в тексте (см. importer.pdf_links).
    block = links_block(links)
    if blob is not None:
        if generate_file_fn is None:
            raise ValueError('для файла нужен generate_file_fn')
        text, meta = generate_file_fn(
            UPDATE_PROMPT + FILE_EXTRA,
            '%s\n\nНОВЫЙ ДОКУМЕНТ приложен файлом (%s). Обнови статью по нему.%s'
            % (header, filename or kind or 'документ',
               ('\n\n' + block) if block else ''),
            blob=blob, mime=mime, max_tokens=MAX_OUTPUT_TOKENS)
    else:
        text, meta = generate_fn(
            UPDATE_PROMPT,
            '%s\n\nНОВЫЙ ДОКУМЕНТ (%s):\n%s%s'
            % (header, filename or kind or 'документ', doc_body,
               ('\n\n' + block) if block else ''),
            max_tokens=MAX_OUTPUT_TOKENS)

    # Числа сверяем с ОБОИМИ источниками: правка вправе принести новое число из
    # документа, но не вправе придумать своё.
    sources = '\n'.join(filter(None, [current_title, to_plain_text(current_html),
                                      document_text, to_plain_text(document_html)]))
    return _finish(text, current_html=current_html, tables=tables, images=images,
                   sources_text=sources, meta=meta, links=links)


def edit_by_instruction(*, current_title, current_html, instruction, generate_fn):
    """Правка по указанию редактора («сократи», «добавь раздел про…»)."""
    instruction = ' '.join(str(instruction or '').split())
    if not instruction:
        raise ValueError('нужно указание')

    body, _doc, tables, images = _prepare(current_html, '')
    prompt = 'ТЕКУЩАЯ СТАТЬЯ: «%s»\n%s' % (current_title or 'без названия', body)
    grid = tablepatch.serialize(tables)
    if grid:
        prompt += ('\n\nСОДЕРЖИМОЕ ТАБЛИЦ (править только через «ПРАВКИ ТАБЛИЦ»):\n'
                   + grid)
    pictures = images_block(images)
    if pictures:
        prompt += '\n\n' + pictures
    prompt += '\n\nУКАЗАНИЕ РЕДАКТОРА:\n' + instruction

    text, meta = generate_fn(EDIT_PROMPT, prompt, max_tokens=MAX_OUTPUT_TOKENS)
    result = _finish(text, current_html=current_html, tables=tables, images=images,
                     sources_text='%s\n%s' % (current_title or '',
                                              to_plain_text(current_html)),
                     meta=meta)
    result['instruction'] = instruction
    return result


def invented_numbers(result_html, *sources):
    """Числа результата, которых нет ни в одном источнике. Для тестов и отладки."""
    text = BeautifulSoup(str(result_html or ''), 'html.parser').get_text(' ', strip=True)
    return ungrounded_numbers(text, [{'text': '\n'.join(s or '' for s in sources)}])
