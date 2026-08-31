# -*- coding: utf-8 -*-
"""Оформительские блоки статьи: наставление для модели и ремонт её ответа.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Разметку, которой можно пользоваться, наши промпты
описывали ТРИЖДЫ и разными словами: список тегов в authoring.SYSTEM_PROMPT,
строка через запятую в revise._FORMAT_BLOCK и две «файловые» добавки. Добавить
блоки в одну копию и забыть остальные — самый дешёвый способ получить статью,
которая красиво собирается из документа и разваливается при первой же правке
по указанию. Поэтому наставление здесь одно, и все промпты подключают его
подстановкой.

ПОЧЕМУ РЯДОМ С НАСТАВЛЕНИЕМ ЛЕЖИТ РЕМОНТ. Модель почти всегда пишет блок
правильно и иногда — почти правильно: карточка без сетки, тон, которого нет,
заголовок h2 внутри плашки. Такую разметку нельзя ни оставить как есть (h2
внутри плашки уедет в оглавление статьи и разорвёт его), ни выбросить целиком
(вместе с ней пропадёт написанный текст). Её надо ЧИНИТЬ, и чинить по тем же
правилам, которые модели объявлены, — иначе наставление и поведение системы
расходятся, а разошедшись, перестают проверяться.

ГДЕ ЕЩЁ ЖИВУТ ЭТИ ЖЕ ИМЕНА. Атрибуты блоков перечислены в четырёх местах:
здесь, в серверном санитайзере (wiki/sanitize.py), в читательском DOMPurify
(src/components/wiki/WikiArticle.jsx) и в узле схемы редактора
(src/components/wiki/WikiBlockNode.js). Разойдись любые два — блок сохранится,
а покажется безымянным div'ом: без фона, без колонок, без номеров, и ни одной
ошибки нигде. Паритет сторожит tests/test_wiki_blocks.py.
"""

import re

# Виды блоков-контейнеров и допустимые значения их атрибутов. Значения
# проверяются, а не пропускаются: data-tone="фиолетовый" пережил бы санитайзер,
# не нарисовал бы ничего и остался в теле статьи навсегда — мусором, чьё
# происхождение потом уже не установить.
BLOCK_KINDS = ('lead', 'note', 'cards', 'card')
TONES = ('info', 'ok', 'warn', 'danger', 'tip', 'dark')
COLS = ('1', '2', '3')
LIST_VARIANTS = ('steps', 'chips', 'checks')

# Атрибуты блока, которые доезжают до базы. Ровно этот набор разрешён
# санитайзером; всё прочее у div срезается.
BLOCK_ATTRS = ('data-wiki-block', 'data-tone', 'data-cols', 'data-numbered')
LIST_ATTRS = ('data-variant',)

# Заголовок внутри блока — только h4. Причина не в красоте: оглавление статьи
# собирается по h1/h2/h3 (WikiArticle.jsx), и заголовок плашки уехал бы в
# правую колонку наравне с разделами. Плюс _lift_headings в authoring.py
# поднимает уровни так, чтобы верхний стал h1: статья, у которой единственные
# заголовки — внутри плашек, целиком превратилась бы в набор разделов.
BLOCK_HEADING = 'h4'

# Сколько плашек на статью считается нормой. Не запрет, а порог для
# предупреждения автору: четыре выделенных места читатель ещё различает,
# двенадцать — уже нет, и выделенным перестаёт быть всё.
NOTE_BUDGET = 4

# Чип — короткое значение, а не предложение. Порог в знаках выбран по образцу:
# самое длинное осмысленное значение там — «Абай (Карагандинская обл.)», 26
# знаков. Сорок даёт запас и всё ещё отсекает фразы.
CHIP_LIMIT = 40


MARKUP_GUIDE = """
ОФОРМИТЕЛЬСКИЕ БЛОКИ
Кроме обычных тегов тебе доступны шесть блоков. Это ОФОРМЛЕНИЕ уже имеющегося
текста, а не повод дописать своё: внутри блока стоит ровно то, что стояло бы в
обычном абзаце.

1. Вводка — о чём статья и кому нужна. Одна на статью, самым первым блоком.
<div data-wiki-block="lead"><p>…</p></div>

2. Плашка — одна мысль, которую нельзя пропустить.
<div data-wiki-block="note" data-tone="warn"><h4>Заголовок</h4><p>…</p></div>
Тон: info — уточнение; ok — так правильно; warn — легко ошибиться; danger —
запрет, отказ, потеря денег; tip — как быстрее; dark — разбор случая с числами
(«Например: заказ стоил 11 000 ₸…»). Заголовок необязателен.

3. Шаги — действия строго по порядку.
<ol data-variant="steps"><li>Первое действие</li><li>Второе</li></ol>

4. Карточки — от двух до шести РАВНОЗНАЧНЫХ кусков рядом.
<div data-wiki-block="cards" data-cols="2" data-numbered="true">
<div data-wiki-block="card" data-tone="warn"><h4>Название</h4><p>…</p></div>
<div data-wiki-block="card"><h4>Название</h4><p>…</p></div>
</div>
data-cols — 1, 2 или 3 (по умолчанию 2); data-numbered="true" нумерует
карточки; data-tone у карточки необязателен.

5. Чипы — перечень коротких значений (город, тариф, статус), от пяти штук.
<ul data-variant="chips"><li>Алматы</li><li>Астана</li></ul>

6. Галочки — что входит или что уже сделано.
<ul data-variant="checks"><li>…</li></ul>

КОГДА БЛОК НЕ СТАВЯТ
Связный текст остаётся абзацами. Блок нужен там, где экономит читателю время,
и больше нигде.
Плашек в статье не больше четырёх и никогда две подряд: три плашки подряд не
выделяют ничего.
«Шаги» — только если порядок действий важен. Перечень равнозначного — обычный
<ul>.
«Карточки» — только если кусков от двух и они равнозначны. Один кусок в сетке
это плашка.
«Чипы» — только для значений до трёх слов. Предложение в чип не кладут.
Блок в блок не вкладывают. Единственное исключение — карточка внутри сетки.
Заголовок внутри блока — только <h4>. h1, h2 и h3 внутри блока попадут в
оглавление статьи и разорвут его.
Таблицу и картинку в блок не кладут: и то и другое само по себе блок.
"""


def _attr(tag, name):
    return str(tag.get(name) or '').strip()


def is_block(tag, kind=None):
    """Это оформительский блок? Годится и как предикат для find_parent."""
    if getattr(tag, 'name', None) != 'div':
        return False
    value = _attr(tag, 'data-wiki-block')
    if value not in BLOCK_KINDS:
        return False
    return kind is None or value == kind


def _has_text(tag):
    """Есть ли в узле хоть что-то, кроме пробелов и пустых обёрток."""
    if tag.find(('table', 'img')):
        return True
    return bool((tag.get_text('', strip=True) or '').strip())


def normalize(soup):
    """Починить оформительские блоки в разборе BeautifulSoup. Меняет на месте.

    Порядок шагов не случаен: сначала выправляются значения атрибутов (иначе
    следующий шаг примет чужой тон за наш блок), потом структура (карточка без
    сетки), потом заголовки, и в самом конце выбрасывается пустое — блок мог
    опустеть как раз в ходе ремонта.
    """
    _fix_attrs(soup)
    _fix_lists(soup)
    _fix_cards(soup)
    _fix_nesting(soup)
    _fix_headings(soup)
    _drop_empty(soup)
    return soup


def _fix_attrs(soup):
    """Чужие значения атрибутов — к умолчанию, лишние атрибуты — вон."""
    for tag in soup.find_all('div'):
        kind = _attr(tag, 'data-wiki-block')
        if not kind:
            continue
        if kind not in BLOCK_KINDS:
            # Незнакомый вид блока — это не блок. Разворачиваем, сохранив текст.
            tag.unwrap()
            continue
        attrs = {'data-wiki-block': kind}
        tone = _attr(tag, 'data-tone')
        if tone in TONES and kind in ('note', 'card'):
            attrs['data-tone'] = tone
        if kind == 'cards':
            cols = _attr(tag, 'data-cols')
            attrs['data-cols'] = cols if cols in COLS else '2'
            if _attr(tag, 'data-numbered') == 'true':
                attrs['data-numbered'] = 'true'
        tag.attrs = attrs


def _fix_lists(soup):
    """Вид списка: чужое имя убираем, длинные чипы возвращаем в обычный список."""
    for tag in soup.find_all(('ul', 'ol')):
        variant = _attr(tag, 'data-variant')
        keep = variant in LIST_VARIANTS
        # «Шаги» — только у нумерованного, «чипы» и «галочки» — только у
        # маркированного: у списка не того вида правило CSS просто не сработает,
        # и человек увидит обычный список там, где ждал номера с пунктиром.
        if keep and variant == 'steps' and tag.name != 'ol':
            keep = False
        if keep and variant in ('chips', 'checks') and tag.name != 'ul':
            keep = False
        # Чип — короткое значение. Абзац, положенный в чип, превращается в
        # таблетку на всю ширину строки: хуже, чем обычный пункт списка.
        if keep and variant == 'chips':
            longest = max((len(li.get_text(' ', strip=True) or '')
                           for li in tag.find_all('li', recursive=False)), default=0)
            if longest > CHIP_LIMIT:
                keep = False
        tag.attrs = {'data-variant': variant} if keep else {}


def _fix_cards(soup):
    """Карточка без сетки и сетка без карточек.

    Карточка вне сетки — самая частая осечка модели: она пишет ряд карточек
    подряд, забыв обёртку. Такая карточка не потеряется (это по-прежнему div с
    рамкой), но встанет в один столбец во всю ширину — то есть ровно тем, чем
    карточка не является. Дешевле собрать подряд идущие карточки в сетку, чем
    объяснять это моделью ещё одной строкой промпта.
    """
    # Соседние карточки вне сетки — в одну сетку.
    for card in list(soup.find_all('div')):
        if not is_block(card, 'card') or card.parent is None:
            continue
        if is_block(card.parent, 'cards'):
            continue
        row = [card]
        sibling = card.next_sibling
        while sibling is not None:
            if getattr(sibling, 'name', None) is None:
                # Пробелы между тегами разбором сохраняются — их пропускаем.
                if str(sibling).strip():
                    break
                sibling = sibling.next_sibling
                continue
            if not is_block(sibling, 'card'):
                break
            row.append(sibling)
            sibling = sibling.next_sibling
        grid = soup.new_tag('div')
        grid['data-wiki-block'] = 'cards'
        grid['data-cols'] = '2' if len(row) != 3 else '3'
        card.insert_before(grid)
        for item in row:
            grid.append(item.extract())

    # Не-карточка внутри сетки — наружу, за сетку. Класть её в карточку нельзя:
    # получилась бы карточка, которой автор не писал.
    for grid in list(soup.find_all('div')):
        if not is_block(grid, 'cards'):
            continue
        for child in list(grid.children):
            if getattr(child, 'name', None) is None:
                continue
            if not is_block(child, 'card'):
                grid.insert_after(child.extract())
        if not any(is_block(child, 'card') for child in grid.children
                   if getattr(child, 'name', None)):
            grid.unwrap()


def _fix_nesting(soup):
    """Блок в блоке. Разрешена ровно одна вложенность: карточка в сетке."""
    for tag in list(soup.find_all('div')):
        if not is_block(tag) or tag.parent is None:
            continue
        kind = _attr(tag, 'data-wiki-block')
        parent = tag.find_parent(lambda node: node is not tag and is_block(node))
        if parent is None:
            continue
        if kind == 'card' and _attr(parent, 'data-wiki-block') == 'cards':
            continue
        tag.unwrap()


def _fix_headings(soup):
    """Заголовок внутри блока — только h4.

    Это не косметика. Оглавление статьи собирается по h1/h2/h3, и заголовок
    плашки «Важно» встал бы в него наравне с разделами; а _lift_headings поднял
    бы уровни так, что статья с заголовками только внутри плашек стала бы
    набором разделов из одних плашек.
    """
    for tag in soup.find_all(('h1', 'h2', 'h3', 'h5', 'h6')):
        if tag.find_parent(is_block) is not None:
            tag.name = BLOCK_HEADING


def _drop_empty(soup):
    """Пустой блок — разрыв вёрстки на пустом месте."""
    # Изнутри наружу: опустевшая сетка видна только после того, как из неё
    # выброшены пустые карточки.
    for tag in sorted((t for t in soup.find_all('div') if is_block(t)),
                      key=lambda t: -len(list(t.parents))):
        if not _has_text(tag):
            tag.decompose()
    for tag in soup.find_all(('ul', 'ol')):
        if _attr(tag, 'data-variant') and not _has_text(tag):
            tag.decompose()


def count_blocks(html):
    """Сколько оформления в статье: {'lead': 1, 'note': 3, 'cards': 1, …}.

    Считается по СТРОКЕ, а не по разбору: функцию зовут и до, и после правки, и
    поднимать ради счётчика BeautifulSoup дважды незачем.
    """
    text = str(html or '')
    counts = {}
    for kind in BLOCK_KINDS:
        counts[kind] = len(re.findall(
            r'data-wiki-block\s*=\s*["\']%s["\']' % kind, text))
    for variant in LIST_VARIANTS:
        counts[variant] = len(re.findall(
            r'data-variant\s*=\s*["\']%s["\']' % variant, text))
    return counts


def warnings(*, before_html='', after_html=''):
    """Что стоит сказать автору про оформление результата.

    Два вопроса, на которые сам он ответа не получит: не пропало ли оформление,
    которое в статье уже было (модель обязана его перенести, но не обязана
    помнить об этом), и не переусердствовала ли модель с плашками.
    """
    after = count_blocks(after_html)
    out = []

    if before_html:
        before = count_blocks(before_html)
        lost = {kind: before[kind] - after.get(kind, 0)
                for kind in before if before[kind] > after.get(kind, 0)}
        if lost:
            out.append('ИИ потерял оформление: %s — проверьте, не превратились ли '
                       'блоки в обычный текст'
                       % ', '.join('%s %d' % (_HUMAN.get(k, k), n)
                                   for k, n in sorted(lost.items())))

    if after.get('note', 0) > NOTE_BUDGET:
        out.append('Плашек в статье %d — это больше нормы (%d). Выделено всё, '
                   'значит не выделено ничего: оставьте самые важные'
                   % (after['note'], NOTE_BUDGET))
    return out


_HUMAN = {
    'lead': 'вводок', 'note': 'плашек', 'cards': 'сеток карточек',
    'card': 'карточек', 'steps': 'списков шагов', 'chips': 'списков чипов',
    'checks': 'списков с галочками',
}
