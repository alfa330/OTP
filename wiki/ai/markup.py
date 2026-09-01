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
BLOCK_KINDS = ('lead', 'note', 'cards', 'card', 'stats', 'stat', 'gallery')
TONES = ('info', 'ok', 'warn', 'danger', 'tip', 'dark', 'neutral')
COLS = ('1', '2', '3')
LIST_VARIANTS = ('steps', 'chips', 'checks', 'crosses')

# Сетка и её ячейка. Пар две, устроены они одинаково, и весь ремонт
# разметки (ячейка без сетки, чужак внутри сетки, допустимая вложенность)
# работает по этой таблице — иначе второй вид сетки потребовал бы копии
# трёх функций, которые обязаны меняться вместе.
GRIDS = (('cards', 'card'), ('stats', 'stat'))
_GRID_KINDS = tuple(grid for grid, _item in GRIDS)

# Кому можно задать тон. Сетка своего тона не имеет: цвет несёт ячейка, а
# крашеная сетка была бы прямоугольником под прямоугольниками.
_TONED = ('note', 'card', 'stat')

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
Кроме обычных тегов тебе доступны восемь блоков. Это ОФОРМЛЕНИЕ уже имеющегося
текста, а не повод дописать своё: внутри блока стоит ровно то, что стояло бы в
обычном абзаце.

1. Вводка — о чём статья и кому нужна. Одна на статью, самым первым блоком.
<div data-wiki-block="lead"><p>…</p></div>

2. Плашка — одна мысль, которую нельзя пропустить.
<div data-wiki-block="note" data-tone="warn"><h4>Заголовок</h4><p>…</p></div>
Тон: info — уточнение; ok — так правильно; warn — легко ошибиться; danger —
запрет, отказ, потеря денег; tip — как быстрее; neutral — справочно, без
окраски; dark — разбор случая с числами («Например: заказ стоил 11 000 ₸…»).
Заголовок необязателен.

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

7. Крестики — чего делать нельзя. Пара к галочкам: рядом они читаются как
«вот так да, вот так нет».
<ul data-variant="crosses"><li>…</li></ul>

8. Показатели — крупные числа: срок, сумма, доля, порог. От двух до четырёх.
<div data-wiki-block="stats" data-cols="3">
<div data-wiki-block="stat"><h4>10 минут</h4><p>бесплатное ожидание</p></div>
<div data-wiki-block="stat"><h4>4,75</h4><p>минимальный рейтинг</p></div>
</div>
В <h4> — само значение с единицей, в <p> — подпись к нему. Показатель это
ЧИСЛО, а не предложение: «10 минут» да, «ожидание бесплатное» нет.
Показателю можно задать data-tone — он красит само число: ok для выгодного
порога, danger для запретного значения. Без нужды не задавай.

9. Галерея — несколько кадров ОДНОГО действия: читатель листает их на месте,
стрелками или пальцем, а не прокручивает страницу мимо четырёх скриншотов.
<div data-wiki-block="gallery">
<img src="…" alt="Откройте раздел «Межгород»">
<img src="…" alt="Выберите заказ">
</div>
Подпись каждого кадра живёт в alt — витрина показывает её под кадром. Ширину и
выравнивание внутри галереи не задавай: их держит сама галерея. От двух кадров;
один кадр — обычная картинка.

КОГДА БЛОК НЕ СТАВЯТ
Связный текст остаётся абзацами. Блок нужен там, где экономит читателю время,
и больше нигде.
Плашек в статье не больше четырёх и никогда две подряд: три плашки подряд не
выделяют ничего.
«Шаги» — только если порядок действий важен. Перечень равнозначного — обычный
<ul>.
«Карточки» — только если кусков от двух и они равнозначны. Один кусок в сетке
это плашка.
«Показатели» — только там, где числа сравнивают взглядом. Одно число внутри
предложения остаётся в предложении.
«Чипы» — только для значений до трёх слов. Предложение в чип не кладут.
Блок в блок не вкладывают. Исключения ровно два: карточка внутри сетки
карточек и показатель внутри сетки показателей.
Заголовок внутри блока — только <h4>. h1, h2 и h3 внутри блока попадут в
оглавление статьи и разорвут его.
Таблицу в блок не кладут: она сама по себе блок. Картинку — тоже, и
единственное исключение здесь галерея: она для того и заведена.
Галерею не собирают из разных мест инструкции. Два кадра рядом — это один шаг,
показанный с двух экранов; кадры двух РАЗНЫХ действий листать нельзя, читатель
не увидит второй.
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
        if tone in TONES and kind in _TONED:
            attrs['data-tone'] = tone
        if kind in _GRID_KINDS:
            cols = _attr(tag, 'data-cols')
            if cols in COLS:
                attrs['data-cols'] = cols
            elif kind == 'cards':
                # У карточек умолчание пишется явно: их две колонки заданы
                # правилом .wiki-prose [data-wiki-block='cards'], и явный
                # атрибут нужен панели редактора, чтобы подсветить активную
                # кнопку. У показателей умолчание — три, и его достаточно
                # оставить в CSS: лишний атрибут только спорил бы с ним.
                attrs['data-cols'] = '2'
        # Нумерация — только у карточек. Пронумерованные показатели читались бы
        # списком шагов, хотя это величины, а не порядок.
        if kind == 'cards' and _attr(tag, 'data-numbered') == 'true':
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
    """Ячейка без сетки и сетка без ячеек — для обеих сеток сразу.

    Ячейка вне сетки — самая частая осечка модели: она пишет ряд карточек
    подряд, забыв обёртку. Такая карточка не потеряется (это по-прежнему div с
    рамкой), но встанет в один столбец во всю ширину — то есть ровно тем, чем
    карточка не является. Дешевле собрать подряд идущие ячейки в сетку, чем
    объяснять это моделью ещё одной строкой промпта.
    """
    for grid_kind, item_kind in GRIDS:
        _fix_one_grid(soup, grid_kind, item_kind)


def _fix_one_grid(soup, grid_kind, item_kind):
    # Соседние ячейки вне сетки — в одну сетку.
    for card in list(soup.find_all('div')):
        if not is_block(card, item_kind) or card.parent is None:
            continue
        if is_block(card.parent, grid_kind):
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
            if not is_block(sibling, item_kind):
                break
            row.append(sibling)
            sibling = sibling.next_sibling
        grid = soup.new_tag('div')
        grid['data-wiki-block'] = grid_kind
        # Тройка встаёт в три колонки, всё остальное — в две: ряд из четырёх
        # ячеек читается как два ряда по две, а из пяти — как 3+2.
        grid['data-cols'] = '3' if len(row) % 3 == 0 else '2'
        card.insert_before(grid)
        for item in row:
            grid.append(item.extract())

    # Чужак внутри сетки — наружу, за сетку. Класть его в ячейку нельзя:
    # получилась бы карточка, которой автор не писал.
    for grid in list(soup.find_all('div')):
        if not is_block(grid, grid_kind):
            continue
        for child in list(grid.children):
            if getattr(child, 'name', None) is None:
                continue
            if not is_block(child, item_kind):
                grid.insert_after(child.extract())
        if not any(is_block(child, item_kind) for child in grid.children
                   if getattr(child, 'name', None)):
            grid.unwrap()


def _fix_nesting(soup):
    """Блок в блоке. Разрешены ровно две вложенности — обе из GRIDS."""
    for tag in list(soup.find_all('div')):
        if not is_block(tag) or tag.parent is None:
            continue
        kind = _attr(tag, 'data-wiki-block')
        parent = tag.find_parent(lambda node: node is not tag and is_block(node))
        if parent is None:
            continue
        if (_attr(parent, 'data-wiki-block'), kind) in GRIDS:
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
    'card': 'карточек', 'stats': 'сеток показателей', 'stat': 'показателей',
    'steps': 'списков шагов', 'chips': 'списков чипов',
    'checks': 'списков с галочками', 'crosses': 'списков с крестиками',
}


# ─────────────────────────────────────────────────────────────────────────────
# КАРТИНКИ: РАЗМЕР И ВЫРАВНИВАНИЕ
#
# Второй набор «оформления», которым модель теперь распоряжается. Живёт здесь,
# а не в authoring.py, по той же причине, что и наставление о блоках: словарь
# оформления у статьи должен быть ОДИН, иначе он расходится по копиям.
#
# ЧТО БЫЛО ДО. Картинка вырезалась из статьи вместе с адресом и возвращалась
# как <img src alt> — БЕЗ data-width и data-align. То есть автор ставил
# скриншот на 60 % и прижимал вправо, просил ИИ «сократи», и картинка молча
# возвращалась во всю ширину слева. Проверено прогоном на паре
# protect_tables/restore_tables: из тега выживали ровно src и alt.
#
# ЕДИНИЦА — ПРОЦЕНТ от ширины колонки, диапазон 10–100. Это не наше решение,
# а формат узла редактора (src/components/wiki/imageSize.js): статью читают и
# с телефона, где колонка втрое уже, а у раздела вдобавок свой масштаб (zoom
# на .wiki-scope), от которого пиксельные величины уезжают.
#
# СТИЛЬ СОБИРАЕТСЯ ЗДЕСЬ ЗАНОВО, а не переносится из тега. Свойства пишутся
# по одному: сокращённое margin серверный санитайзер выбрасывает целиком — он
# сверяет ИМЯ свойства с белым списком, а там только margin-left и
# margin-right. Формула обязана совпадать с styleFor из imageSize.js, и за
# этим следит отдельный тест: разойдись они — размер, выставленный ИИ, и
# размер, выставленный человеком, дали бы разную вёрстку.
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_MIN = 10
IMAGE_MAX = 100
IMAGE_ALIGNS = ('left', 'center', 'right')

# Как выравнивание называется по-русски — и в маркере от модели, и в подсказке
# для человека. Порядок важен: ищем вхождением, и «по центру» обязано
# проверяться раньше «центр», иначе длинная форма никогда не сработает целиком.
_ALIGN_WORDS = (
    ('по центру', 'center'), ('center', 'center'), ('центру', 'center'),
    ('центр', 'center'), ('посередине', 'center'),
    ('слева', 'left'), ('влево', 'left'), ('left', 'left'),
    ('справа', 'right'), ('вправо', 'right'), ('right', 'right'),
)

ALIGN_RU = {'left': 'слева', 'center': 'по центру', 'right': 'справа'}

# Слова, которыми модель просит УБРАТЬ картинку. Отдельная команда нужна,
# потому что просто выбросить маркер нельзя: потерянная картинка возвращается
# в конец статьи отдельным разделом — правило «данные документа молча не
# теряем» старше этой фичи и остаётся.
# Только однозначные повеления. «Не нужна» и «без неё» отсюда убраны нарочно:
# хвост маркера — свободный текст, и такая формулировка может оказаться в нём
# как пояснение, а ценой ошибки будет ИСЧЕЗНУВШАЯ из статьи картинка.
_REMOVE_WORDS = ('убрать', 'убери', 'удалить', 'удали', 'убрана', 'удалена')

_IMAGE_SIZE_RE = re.compile(r'(\d{1,3})\s*%')
_STYLE_WIDTH_RE = re.compile(r'(?:^|;)\s*width\s*:\s*([\d.]+)\s*%', re.I)


def clamp_image_size(value):
    """Ширина в процентах или None.

    None — это «размер не задан», и оно НЕ равно 100 %. Подмени пустоту сотней,
    и каждый мелкий значок растянулся бы на всю колонку, превратившись в мыло.
    Та же оговорка стоит в clampSize из imageSize.js.
    """
    try:
        number = float(str(value).strip().rstrip('%'))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(min(IMAGE_MAX, max(IMAGE_MIN, round(number))))


def image_align(value):
    return value if value in IMAGE_ALIGNS else None


def parse_image_controls(tail):
    """Хвост маркера «[[КАРТИНКА-1 60% справа]]» → (ширина, выравнивание, убрать)."""
    text = ' '.join(str(tail or '').lower().split())
    if not text:
        return None, None, False
    remove = any(word in text for word in _REMOVE_WORDS)
    found = _IMAGE_SIZE_RE.search(text)
    size = clamp_image_size(found.group(1)) if found else None
    align = None
    for word, value in _ALIGN_WORDS:
        if word in text:
            align = value
            break
    return size, align, remove


def image_style(size=None, align=None):
    """Инлайновый стиль картинки — двойник styleFor из imageSize.js."""
    parts = []
    width = clamp_image_size(size)
    if width:
        parts.append('width: %d%%' % width)
    if align == 'left':
        parts.extend(['margin-left: 0', 'margin-right: auto'])
    if align == 'center':
        parts.extend(['margin-left: auto', 'margin-right: auto'])
    if align == 'right':
        parts.extend(['margin-left: auto', 'margin-right: 0'])
    return '; '.join(parts)


def _quote(value):
    """Кавычки внутри значения атрибута сломали бы тег — убираем, как и раньше."""
    return str(value or '').strip().replace('"', '')


def image_tag(src, alt='', size=None, align=None):
    """Собрать <img> с контролами. Размер живёт И в data-width, И в style.

    Дублирование намеренное и взято у узла редактора: по data-width он
    восстанавливает состояние при следующем открытии статьи, а style рисует
    картинку читателю. Оставь одно — и либо размер не виден, либо не
    редактируется.
    """
    size = clamp_image_size(size)
    align = image_align(align)
    out = ['<img src="%s"' % _quote(src)]
    alt = _quote(alt)
    if alt:
        out.append(' alt="%s"' % alt)
    if size:
        out.append(' data-width="%d"' % size)
    if align:
        out.append(' data-align="%s"' % align)
    style = image_style(size, align)
    if style:
        out.append(' style="%s"' % style)
    out.append('>')
    return ''.join(out)


def read_image_controls(tag):
    """Ширина и выравнивание уже стоящей картинки (узел bs4).

    Ширина читается и из data-width, и из процента в style — ровно как в
    sizeFromElement: атрибут может выпасть из белого списка санитайзера, и
    тогда размер уцелеет в стиле, а не пропадёт молча.
    """
    size = clamp_image_size(tag.get('data-width'))
    if not size:
        found = _STYLE_WIDTH_RE.search(str(tag.get('style') or ''))
        size = clamp_image_size(found.group(1)) if found else None
    return size, image_align(str(tag.get('data-align') or '').strip() or None)


def read_image(html):
    """Разобрать сохранённый тег картинки → (src, alt, ширина, выравнивание)."""
    from bs4 import BeautifulSoup
    tag = BeautifulSoup(str(html or ''), 'html.parser').find('img')
    if tag is None:
        return '', '', None, None
    size, align = read_image_controls(tag)
    return (str(tag.get('src') or ''), str(tag.get('alt') or ''), size, align)


def retag_image(html, size=None, align=None):
    """Пересобрать картинку с новыми контролами.

    Пустое значение означает «оставить как было»: маркер без хвоста не должен
    сбрасывать размер, который выставил человек.
    """
    src, alt, current_size, current_align = read_image(html)
    if not src:
        return str(html or '')
    return image_tag(src, alt,
                     clamp_image_size(size) or current_size,
                     image_align(align) or current_align)


IMAGE_GUIDE = """
КАРТИНКИ И ИХ РАЗМЕР
Картинки вырезаны и заменены маркерами [[КАРТИНКА-1]]. Маркер — это сама
картинка: перенеси его дословно, отдельным абзацем, туда, где он нужен по
смыслу. Номера не меняй и не выдумывай.

У картинки два контрола, и ты вправе ими пользоваться. Пиши их в самом
маркере, после номера:
[[КАРТИНКА-1 60%]]          ширина в процентах от колонки, от 10 до 100
[[КАРТИНКА-1 по центру]]    выравнивание: слева, по центру, справа
[[КАРТИНКА-1 45% справа]]   и то и другое
[[КАРТИНКА-1]]              оставить как есть

КАК ВЫБИРАТЬ РАЗМЕР
Снимок экрана целиком — 100 % (и это же значение по умолчанию).
Часть экрана, кнопка, значок, QR-код — 30–50 % и по центру: растянутый мелкий
элемент превращается в мыло.
Вертикальный снимок телефона — не шире 45 %, иначе он занимает экран целиком
и текст статьи приходится искать под ним.
Две картинки, которые читатель сравнивает между собой, — одинаковой ширины.
Если у картинки размер УЖЕ задан, не трогай его без причины: его выставил
человек, и [[КАРТИНКА-1]] без хвоста его сохраняет.

УБРАТЬ КАРТИНКУ можно только по прямому указанию и только так:
[[КАРТИНКА-1 убрать]]. Просто выбросить маркер нельзя — картинка вернётся в
конец статьи отдельным разделом.
"""
