# -*- coding: utf-8 -*-
"""Разметка ответа модели: конверт, санитайзер, ремонт блоков, ссылки на разговоры.

ПОРЯДОК ШАГОВ — ЗАЩИТА, А НЕ ВКУС. Ответ модели — чужой HTML: в нём может
оказаться что угодно, от <script> до выдуманного атрибута. Поэтому сначала он
проходит санитайзер с узким белым списком (ни style, ни class, ни ссылок — в
сводке им делать нечего), потом ремонт блоков ТЕМ ЖЕ wiki.ai.markup.normalize,
что чинит статьи вики (карточка без сетки, чужой тон, заголовок h2 внутри
плашки), и только ПОСЛЕ этого метки [[#12]] становятся кнопками разговоров.
Кнопку собирает код: атрибут data-qa-ref санитайзер модели не пропускает, так
что подсунуть ссылку на чужой разговор модель не может — номер, которого нет
среди разговоров дня, просто исчезает.

ПОЧЕМУ ПОДПИСЬ КНОПКИ ЗАВИСИТ ОТ ТЕКСТА ВОКРУГ. Модель почти всегда называет
сотрудника и тут же ставит метку: «Иванова Айгерим [[#3]] не назвала тариф».
Кнопка «Иванова А. · 14:09» повторила бы фамилию дважды подряд — тот самый
шум, за который владелец бракует экраны. Если фамилия уже стоит в строке перед
меткой, кнопка подписывается одним временем.
"""
from __future__ import annotations

import html as html_lib
import math
import re

from wiki.ai import markup as wiki_markup

try:
    import nh3
except ImportError:  # pragma: no cover — окружение без зависимости
    nh3 = None

ALLOWED_TAGS = {
    "p", "br", "hr", "h3", "h4", "strong", "b", "em", "i", "u", "s", "mark", "code",
    "ul", "ol", "li", "blockquote", "table", "thead", "tbody", "tr", "th", "td",
    "div", "span",
    # h1/h2/h5/h6 пропускаются, чтобы их не потерять вместе с текстом: ниже они
    # переименовываются в разрешённые уровни, а не выбрасываются.
    "h1", "h2", "h5", "h6",
}
ALLOWED_ATTRIBUTES = {
    "div": set(wiki_markup.BLOCK_ATTRS),
    "ul": set(wiki_markup.LIST_ATTRS),
    "ol": set(wiki_markup.LIST_ATTRS),
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

REF_RE = re.compile(r"\[\[\s*#\s*(\d{1,4})\s*\]\]")
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*")
_HEADLINE_RE = re.compile(r"^\s*\**\s*КРАТКО\s*\**\s*:\s*(.+?)\s*$", re.M | re.I)
_BODY_RE = re.compile(r"^\s*\**\s*СВОДКА\s*\**\s*:\s*", re.M | re.I)
_BLOCK_TAGS = ("p", "li", "td", "th", "h3", "h4", "blockquote", "div")
_BULLET_RE = re.compile(r"^([-•*]|\d+[.)])\s+")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
_MD_HEADING_RE = re.compile(r"^#{1,4}\s+")
HEADLINE_LIMIT = 120


def parse_envelope(text: str) -> tuple[str, str]:
    """Ответ «КРАТКО: … / СВОДКА: <html>» → (строка-заголовок, html).

    Модель иногда забывает конверт или заворачивает HTML в ```html — ни то ни
    другое не повод терять сводку: без конверта весь ответ считается телом, а
    заголовок потом берётся из вводки."""
    raw = _FENCE_RE.sub("", str(text or "")).strip()
    headline = ""
    match = _HEADLINE_RE.search(raw)
    if match:
        headline = match.group(1)
    body_match = _BODY_RE.search(raw)
    if body_match:
        body = raw[body_match.end():]
    elif match:
        body = raw[match.end():]
    else:
        body = raw
    return clean_headline(headline), body.strip()


def clean_headline(text: str) -> str:
    text = REF_RE.sub("", str(text or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = " ".join(text.split()).strip(" «»\"'*")
    text = text.rstrip(".")
    if len(text) > HEADLINE_LIMIT:
        cut = text[:HEADLINE_LIMIT].rsplit(" ", 1)[0]
        text = cut.rstrip(",;:—- ") + "…"
    return text


def _plain_to_html(text: str) -> str:
    """Модель ответила текстом без тегов (так изредка делает резервное звено):
    абзацы по пустой строке, строки с «- » — список, **жирный** — <strong>."""
    blocks = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        if all(_BULLET_RE.match(line) for line in lines):
            ordered = all(_NUMBERED_RE.match(line) for line in lines)
            items = "".join("<li>" + _inline(_BULLET_RE.sub("", line)) + "</li>" for line in lines)
            tag = "ol" if ordered else "ul"
            blocks.append(f"<{tag}>{items}</{tag}>")
        elif len(lines) == 1 and _MD_HEADING_RE.match(lines[0]):
            blocks.append("<h3>" + _inline(_MD_HEADING_RE.sub("", lines[0])) + "</h3>")
        else:
            blocks.append("<p>" + "<br>".join(_inline(line) for line in lines) + "</p>")
    return "".join(blocks)


def _inline(text: str) -> str:
    escaped = html_lib.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def clean_html(raw: str) -> str:
    """Чужой HTML модели → безопасный HTML с исправленными блоками вики."""
    if nh3 is None:
        raise RuntimeError("Не установлен nh3 — показывать HTML модели без санитизации нельзя")
    text = _FENCE_RE.sub("", str(raw or "")).strip()
    if not text:
        return ""
    if not re.search(r"<(p|div|ul|ol|h\d|table|blockquote|li)\b", text, re.I):
        text = _plain_to_html(text)
    safe = nh3.clean(text, tags=ALLOWED_TAGS,
                     attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
                     url_schemes=set(), strip_comments=True)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(safe, "html.parser")
    # Уровни заголовков: разделы сводки — h3, внутри блоков — h4 (так же, как в
    # статьях: заголовок выше h4 внутри блока normalize опускает сам).
    for tag in soup.find_all(("h1", "h2")):
        tag.name = "h3"
    for tag in soup.find_all(("h5", "h6")):
        tag.name = "h4"
    wiki_markup.normalize(soup)
    for tag in soup.find_all("p"):
        if not tag.get_text(strip=True) and not tag.find("br"):
            tag.decompose()
    return str(soup).strip()


# ── ссылки на разговоры ───────────────────────────────────────────────────────

def short_name(name: str) -> str:
    """«Карабек Ержан» → «Карабек Е.»: в кнопке полное ФИО не помещается."""
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[1][:1]}."


def ref_entry(talk) -> dict:
    """Что нужно кнопке разговора: куда вести и как подписать."""
    return {"kind": talk["kind"], "id": talk["id"], "family": talk["family"],
            "operator": talk.get("operator") or "—", "time": talk.get("time") or "",
            "ai_score": talk.get("ai_score"), "direction": talk.get("direction") or ""}


def _ref_title(entry) -> str:
    # Те же слова, что в списках раздела (subjects.js: subjectTitle): у Chat2Desk
    # единица — заявка, а не чат.
    kind = ("Заявка" if entry.get("kind") == "c2d_snapshot"
            else "Чат" if entry.get("family") == "chats" else "Звонок")
    parts = [f"{kind} #{entry['id']}" if entry.get("id") is not None else kind,
             entry.get("operator") or "—"]
    if entry.get("time"):
        parts.append(entry["time"])
    if entry.get("ai_score") is not None:
        parts.append(f"балл ИИ {round(entry['ai_score'])}")
    return " · ".join(parts)


_SEPARATOR_RE = re.compile(r"^[\s,;и]*$")
# Окончания, которые в косвенных падежах меняются: «Иванова» → «Ивановой»,
# «Толстая» → «Толстой». Отрезав их, получаем основу, с которой начинается
# фамилия в любом падеже («Карабек» → «Карабеком» — и без отрезания).
_SURNAME_ENDINGS = ("ая", "яя", "ий", "ый", "ой", "а", "я")


def _surname_stem(surname: str) -> str:
    word = surname.lower()
    for ending in _SURNAME_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[:-len(ending)]
    return word


# Падежные окончания после основы (_surname_stem): «Иванов|ой», «Карабек|ом»,
# «Толст|ой», «Айгерим» — без окончания.
_CASE_ENDINGS = ("ого|его|ому|ему|ыми|ими|ами|ями|ой|ей|ою|ею|ом|ем|ым|им|ую|юю|ая|яя|ий|ый"
                 "|ах|ях|ам|ям|а|я|у|ю|е|ы|и|о")
_NAME_RE_CACHE: dict = {}


def _name_re(word: str):
    """Слово имени в тексте в любом падеже: основа С ЗАГЛАВНОЙ буквы и падежное
    окончание до конца слова. Заглавная — потому что «тенге», «пакет» и «нам» не
    фамилии Тен, Пак и Нам; конец слова — потому что «Канал» не «Кан»."""
    stem = _surname_stem(word or "")
    if len(stem) < 2:
        return None
    if stem not in _NAME_RE_CACHE:
        cap = stem[:1].upper() + stem[1:]
        _NAME_RE_CACHE[stem] = re.compile(rf"(?<!\w){re.escape(cap)}(?:{_CASE_ENDINGS})?(?!\w)")
    return _NAME_RE_CACHE[stem]


def _named_people(text: str, people) -> set:
    """Кто из сотрудников ссылок назван в тексте. Фамилию, которую делят двое
    (однофамильцы; «Иванов» и «Иванова» — одна основа), засчитываем только
    вместе с именем: иначе «Иванов Арман [[#1]], то же — [[#2]]» сняло бы имя с
    кнопки звонка Ивановой, и он читался бы как второй звонок Иванова."""
    found = set()
    for person in people:
        parts = person.split()
        surname_re = _name_re(parts[0]) if parts else None
        if surname_re is None or not surname_re.search(text or ""):
            continue
        stem = _surname_stem(parts[0])
        shared = any(other != person and other.split() and _surname_stem(other.split()[0]) == stem
                     for other in people)
        if shared:
            first_re = _name_re(parts[1]) if len(parts) > 1 else None
            if first_re is None or not first_re.search(text or ""):
                continue
        found.add(person)
    return found


# Сколько текста после метки смотреть: «[[#3]] (Серик Даурен)», «[[#3]], и
# Иванова тоже…» — фамилия сразу за кнопкой повторила бы её подпись.
_AFTER_WINDOW = 60


def _ref_label(entry, before: str, *, after: str = "", people=(),
               same_as_previous: bool = False) -> str:
    """Подпись кнопки: «Фамилия И. · время» или одно время, если рядом назван
    именно этот сотрудник — в строке перед меткой или сразу за ней, — или кнопка
    продолжает перечень того же сотрудника («[Иванова А. · 10:12], [10:40]»).
    Назван рядом кто-то ещё — фамилия остаётся: время без неё читалось бы как
    разговор того, кого назвали."""
    operator = str(entry.get("operator") or "")
    people = set(people) | ({operator} if operator and operator != "—" else set())
    named_before = _named_people(before, people)
    named_after = _named_people(after, people)
    named = bool(operator) and (named_before == {operator}
                                or (not named_before and named_after == {operator}))
    # У заявки Chat2Desk времени нет (только день) — тогда номер, как в списке
    # («Заявка #3741»): одинаковые «[чат], [чат]» рядом не различить.
    stamp = entry.get("time") or (f"#{entry['id']}" if entry.get("id") is not None else "")
    if named or same_as_previous:
        return stamp or ("чат" if entry.get("family") == "chats" else "звонок")
    return f"{short_name(entry.get('operator'))} · {stamp}" if stamp else short_name(entry.get("operator"))


def _preceding_text(node, upto: int) -> str:
    """Текст строки перед меткой: от начала ближайшего блока до места метки.

    Внутри карточки или плашки к строке добавляется их заголовок: в карточке
    «Иванова Айгерим» метка в тексте ниже — это её же разговор, и фамилию в
    кнопке повторять незачем."""
    from bs4 import NavigableString
    heading = ""
    holder = node.find_parent(lambda tag: wiki_markup.is_block(tag, "card")
                              or wiki_markup.is_block(tag, "note"))
    if holder is not None and holder.find("h4") is not None:
        heading = holder.find("h4").get_text(" ", strip=True)
    block = node.find_parent(_BLOCK_TAGS)
    # В таблице фамилия обычно в первой колонке той же строки, а метка — в
    # последней: «до метки» — это вся строка, а не одна ячейка.
    if block is not None and block.name in ("td", "th") and block.find_parent("tr") is not None:
        block = block.find_parent("tr")
    acc = []
    if block is not None:
        for piece in block.descendants:
            if piece is node:
                break
            if isinstance(piece, NavigableString):
                acc.append(str(piece))
    # Только звено после предыдущей метки этой же строки: «Иванова [[#1]] и
    # Петров [[#2]]» — для второй кнопки рядом назван лишь Петров.
    acc.append(str(node)[:upto].rsplit("]]", 1)[-1])
    # Куски — через пробел: ячейки и теги, склеенные встык («Айгеримне»), теряли
    # границу слова, и имя рядом не находилось.
    return f"{heading} {' '.join(acc)[-90:]}"


def _following_text(node, start: int) -> str:
    """Текст сразу за меткой — до конца ближайшего блока, но не дальше
    _AFTER_WINDOW знаков и не дальше следующей метки. Через границы тегов:
    модель любит «[[#3]] (<strong>Серик Даурен</strong>)»."""
    from bs4 import NavigableString
    acc = [str(node)[start:]]
    block = node.find_parent(_BLOCK_TAGS)
    if block is not None:
        after = False
        for piece in block.descendants:
            if piece is node:
                after = True
                continue
            if after and isinstance(piece, NavigableString):
                acc.append(str(piece))
                if sum(len(part) for part in acc) >= _AFTER_WINDOW:
                    break
    return " ".join(acc)[:_AFTER_WINDOW].split("[[", 1)[0]


def link_refs(html: str, refs: dict) -> tuple[str, list[str]]:
    """Метки [[#n]] → кнопки разговоров. Возвращает (html, использованные ключи).

    refs — {номер: ref_entry}. Неизвестный номер выбрасывается вместе с меткой:
    модель обязана ссылаться только на разговоры из данных, а «битая» кнопка
    хуже, чем её отсутствие."""
    if not html or "[[" not in html:
        return html or "", []
    from bs4 import BeautifulSoup, NavigableString
    soup = BeautifulSoup(html, "html.parser")
    used: list[str] = []
    people = {e.get("operator") for e in refs.values()
              if e.get("operator") and e.get("operator") != "—"}
    for node in list(soup.find_all(string=REF_RE)):
        if not isinstance(node, NavigableString) or node.parent is None:
            continue
        text = str(node)
        pieces = []
        last = 0
        dropped = False
        previous_operator = None
        for match in REF_RE.finditer(text):
            gap = text[last:match.start()]
            pieces.append(gap)
            if not _SEPARATOR_RE.match(gap):
                previous_operator = None
            last = match.end()
            entry = refs.get(int(match.group(1)))
            if not entry:
                dropped = True
                continue
            key = f"{entry['kind']}:{entry['id']}"
            span = soup.new_tag("span")
            span["data-qa-ref"] = key
            span["data-qa-kind"] = "chat" if entry.get("family") == "chats" else "call"
            span["title"] = _ref_title(entry)
            # За меткой — до следующей метки: там может стоять чужая фамилия.
            span.string = _ref_label(entry, _preceding_text(node, match.start()),
                                     after=_following_text(node, match.end()), people=people,
                                     same_as_previous=previous_operator == entry.get("operator"))
            previous_operator = entry.get("operator")
            pieces.append(span)
            used.append(key)
        pieces.append(text[last:])
        if dropped:
            # Выброшенная метка оставляет пробел перед запятой или точкой и
            # пустые скобки. Чистим только этот текстовый узел: весь HTML
            # регуляркой трогать нельзя — она задела бы и чужой текст.
            merged = []
            for piece in pieces:
                if isinstance(piece, str) and merged and isinstance(merged[-1], str):
                    merged[-1] += piece
                else:
                    merged.append(piece)
            pieces = [re.sub(r"[ \t]{2,}", " ",
                             re.sub(r"\(\s*\)", "", re.sub(r"[ \t]+([,.;:)])", r"\1", piece)))
                      if isinstance(piece, str) else piece for piece in merged]
        replacement = []
        for piece in pieces:
            if isinstance(piece, str):
                if piece:
                    replacement.append(NavigableString(piece))
            else:
                replacement.append(piece)
        if replacement:
            node.replace_with(*replacement)
        else:
            node.extract()
    return str(soup), used


def render(raw: str, refs: dict) -> tuple[str, list[str]]:
    """Ответ модели → (готовый HTML, ключи упомянутых разговоров)."""
    return link_refs(clean_html(raw), refs)


# Подписи чисел шапки раздела (DigestView: SectionStats) так, как модель их
# пересказывает: «7 — критических за день», «91 — средний балл», «5 из 30 —
# проверено людьми». Подпись сверяется ЦЕЛИКОМ: корень где угодно ловил и доли по
# критериям, ради которых показатели и ставят («3 из 7 — не проверили статус
# заказа» при трёх проверенных людьми исчезало бы молча). Порядок — от узкого к
# широкому: «средний балл человека» раньше «средний балл».
_HEADER_STAT_LABELS = (
    ("human_avg", r"средн\w* (?:балл|оценка) (?:человека|людей|проверяющ\w*|супервайзер\w*)"),
    ("usual_ai_avg", r"(?:обычн\w* (?:средн\w* )?балл(?: ии)?|обычно|норма"
                     r"|средн\w* балл (?:прошлой|за прошлую) недел\w*)"),
    ("below_60", r"(?:(?:разговор\w*|звонк\w*|чат\w*|оцен\w*) )?(?:ниже|меньше|до) 60(?: балл\w*)?"),
    ("critical_pct", r"(?:критическ\w*(?: нарушени\w*| ошиб\w*)?"
                     r"|(?:разговор\w*|звонк\w*|чат\w*) с критическ\w*(?: нарушени\w*| ошиб\w*)?)"
                     r"(?: за день)?"),
    ("critical", r"(?:критическ\w*(?: нарушени\w*| ошиб\w*)?"
                 r"|(?:разговор\w*|звонк\w*|чат\w*) с критическ\w*(?: нарушени\w*| ошиб\w*)?)"
                 r"(?: за день)?"),
    ("ai_avg", r"(?:средн\w* балл|средняя оценка)(?: ии)?(?: за день| дня| направления| раздела"
               r"| отдела)?(?: «[^»]*»)?"),
    ("reviewed", r"(?:проверено|проверен\w*)(?: людьми| человеком| супервайзер\w*)?"),
    ("evaluated", r"(?:всего )?(?:оценено|оцененн\w*|оценок)(?: (?:разговор\w*|звонк\w*|чат\w*"
                  r"|заяв\w*|диалог\w*))?(?: за день)?"),
    ("operators", r"(?:всего )?сотрудник\w*(?: на линии| за день| в смене| в работе| работал\w*)?"),
)
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(%?)")
_OF_RE = re.compile(r"\bиз\s+(\d+)")


def _half_up(value) -> int:
    """Округление как у экрана (Math.round): 72.5 → 73. Встроенный round
    Питона банковский (72.5 → 72), и число в промпте расходилось бы с шапкой."""
    return int(math.floor(float(value) + 0.5))


def _label_text(label: str) -> str:
    text = label.lower().replace("ё", "е")
    text = re.sub(r"[^\w%«» ]+", " ", text)
    return " ".join(text.split())


def _header_numbers(stats: dict, usual: dict | None) -> dict:
    header = {key: stats.get(key) for key in ("critical", "evaluated", "reviewed",
                                               "below_60", "operators")}
    for key in ("ai_avg", "human_avg"):
        header[key] = _half_up(stats[key]) if stats.get(key) is not None else None
    header["usual_ai_avg"] = (_half_up(usual["ai_avg"])
                              if (usual or {}).get("ai_avg") is not None else None)
    evaluated = stats.get("evaluated") or 0
    header["critical_pct"] = (_half_up(100 * (stats.get("critical") or 0) / evaluated)
                              if evaluated else None)
    return header


def drop_header_stats(html: str, stats: dict | None, usual: dict | None = None) -> str:
    """Выбросить показатели, которые повторяют цифры шапки раздела.

    Над текстом экран сам показывает «оценено» с числом сотрудников, «средний
    балл ИИ» с «обычно», «критических» с «ниже 60» и «проверено людьми» со
    средним баллом человека — точные, от сервера. Модели это сказано, и обычно
    она слушается, но не всегда: на живом дне 02.10.2026 два раздела из восьми
    всё равно поставили «91 — средний балл» и «7 критических за день». Дубль на
    экране — брак (требование владельца), поэтому страховка детерминированная:
    число совпало с шапкой И подпись про то же — показатель вон. Иное число с
    той же подписью («5 из 30 критических по достоверности») остаётся: это уже
    не повтор."""
    if not html or "data-wiki-block" not in html or not stats:
        return html or ""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    header = _header_numbers(stats, usual)
    changed = False
    for stat in list(soup.find_all("div", attrs={"data-wiki-block": "stat"})):
        title_text = stat.find("h4").get_text(" ", strip=True) if stat.find("h4") else ""
        label = _label_text(stat.find("p").get_text(" ", strip=True) if stat.find("p") else "")
        match = _LEADING_NUMBER_RE.match(title_text)
        if not match:
            continue
        # «N из M» в шапке бывает одно — «проверено R из оценено E»: другое M —
        # это уже доля по критерию.
        of = _OF_RE.search(title_text)
        if of and int(of.group(1)) != (stats.get("evaluated") or 0):
            continue
        value = _half_up(match.group(1).replace(",", "."))
        percent = bool(match.group(2))
        for key, pattern in _HEADER_STAT_LABELS:
            if (key == "critical_pct") != percent:
                continue
            if (header.get(key) is not None and value == int(header[key])
                    and re.fullmatch(pattern, label)):
                stat.decompose()
                changed = True
                break
    if not changed:
        return html
    # Сетка, в которой не осталось показателей, — пустая рамка: вон и её.
    for grid in list(soup.find_all("div", attrs={"data-wiki-block": "stats"})):
        if not grid.find("div", attrs={"data-wiki-block": "stat"}):
            grid.decompose()
    return str(soup)


def to_text(html: str, numbers: dict | None = None) -> str:
    """Готовая сводка → текст для промпта (обзор отдела и чат).

    Кнопки разговоров снова становятся метками [[#n]] — по нумерации ТЕКУЩЕГО
    чтения дня (numbers: «kind:id» → n): сводку составляли по одной нумерации,
    а чат читает день заново, и номера могли сдвинуться."""
    if not html:
        return ""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for span in soup.find_all("span", attrs={"data-qa-ref": True}):
        number = (numbers or {}).get(span.get("data-qa-ref"))
        span.replace_with(f"[[#{number}]]" if number else span.get_text())
    for tag in soup.find_all(("p", "li", "h3", "h4", "tr", "blockquote", "div")):
        tag.insert_after("\n")
    for tag in soup.find_all("li"):
        tag.insert_before("— ")
    text = soup.get_text()
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def headline_from(html: str) -> str:
    """Заголовок из вводки, если модель забыла строку «КРАТКО»."""
    if not html:
        return ""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    lead = soup.find("div", attrs={"data-wiki-block": "lead"}) or soup.find("p")
    if lead is None:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", lead.get_text(" ", strip=True))[0]
    return clean_headline(sentence)
