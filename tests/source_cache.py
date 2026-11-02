# -*- coding: utf-8 -*-
"""Один разбор монолита на весь набор тестов.

Зачем. Импортировать `bot_schedule2.py` из тестов нельзя: он на старте поднимает
пул к боевой БД и падает на Windows в `time.tzset`. Поэтому тесты достают из него
функции через `ast` — и до этого модуля каждый такой хелпер разбирал файл заново.
Цена разбора: `bot_schedule2.py` (2,4 МБ) — 0,52с, `database.py` (2,5 МБ) — 0,73с,
а вызовов по набору 155. Отсюда 0,5с на средний тест и 18 минут на прогон в CI.

Кэш держится на тексте, а не на пути: подставить `parse` вместо `ast.parse` можно
в любом месте, не трогая то, как хелпер добыл исходник (у 55 файлов это устроено
по-разному). Хэш строки Python считает один раз и запоминает, так что повторный
поиск в кэше ничего не стоит.

ВАЖНО, инвариант. Дерево теперь общее, поэтому **узлы из него менять нельзя** —
правка доедет до всех следующих тестов. Единственная такая правка в наборе —
снятие декораторов (`@app.route`) перед `exec`, и она везде сделана по копии:

    node = copy.deepcopy(function_node(BOT_PATH, "имя_ручки"))
    node.decorator_list = []

`copy.deepcopy` одной функции — микросекунды, в отличие от копии всего дерева
(1,57с, втрое дороже самого разбора). Добавляешь новое место, где узел меняется, —
клонируй его так же.

Мелкие исходники (меньше 100 КБ) не кэшируются вовсе: их разбор дешёвый, а свежее
дерево на каждый вызов безопаснее.
"""

import ast
import copy
import io
import os
import re
import time
import tokenize
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Порог «монолита». Ниже него разбор занимает единицы миллисекунд и кэш не нужен,
# зато каждый вызов получает своё дерево — то есть прежнее поведение.
BIG_SOURCE = 100_000


@lru_cache(maxsize=None)
def _parse_cached(source):
    return ast.parse(source)


def parse(source, *args, **kwargs):
    """Замена `ast.parse`: крупные исходники разбираются один раз за процесс.

    Сигнатура и результат совпадают с `ast.parse`. Если переданы дополнительные
    аргументы (`filename`, `mode`, `feature_version`), кэш не используется —
    их сочетания редки, а тонкости поведения важнее экономии.
    """
    if args or kwargs or not isinstance(source, str) or len(source) < BIG_SOURCE:
        return ast.parse(source, *args, **kwargs)
    return _parse_cached(source)


@lru_cache(maxsize=None)
def read(path):
    """Текст файла с тем же `utf-8-sig`, что используют тесты."""
    return Path(path).read_text(encoding="utf-8-sig")


def tree(path):
    """Разобранный модуль по пути. Дерево общее — смотри инвариант в шапке."""
    return parse(read(path))


def function_node(path, name, class_name=None):
    """Узел функции `name` (при `class_name` — метода класса). Узел общий.

    Менять его нельзя: сначала `copy.deepcopy`. Готовую копию отдаёт
    `function_copy`.
    """
    body = tree(path).body
    if class_name is not None:
        body = next(
            node.body for node in body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    # Ручки, вынесенные из монолита в пакеты (scripts/extract_routes.py), живут вложенными
    # в build_<раздел>_blueprint — ищем по всему модулю, если на верхнем уровне нет.
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    # Спрашивали bot_schedule2.py, а функция уже уехала в пакет раздела: тест про
    # поведение ручки не обязан знать, в каком файле она теперь лежит.
    if class_name is None and Path(path).name == "bot_schedule2.py":
        for extracted in extracted_route_modules():
            for node in ast.walk(tree(extracted)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return node
    raise LookupError(f"функция {name} не найдена ни в {path}, ни в вынесенных routes.py")


@lru_cache(maxsize=None)
def extracted_route_modules():
    """Пакеты, куда scripts/extract_routes.py вынес ручки из bot_schedule2.py."""
    return tuple(
        path for path in sorted(ROOT.glob("*/routes.py"))
        if "scripts/extract_routes.py" in read(path)
    )


# ── bot_schedule2.py после выноса ручек в пакеты (scripts/extract_routes.py) ──────
# Ручки и их помощники живут в <раздел>/routes.py: верхнеуровневые помощники как были,
# ручки — вложенными функциями строителя с @bp.route. Тесты, читавшие монолит как текст
# или ast (их 186), получают склейку: сам монолит плюс каждый вынесенный модуль,
# развёрнутый обратно в вид монолита — функции строителя подняты на уровень модуля,
# @bp.route('/x') снова @app.route('/api/x'). Новым тестам — читать свой <раздел>/routes.py.
BOT_FACADE = ROOT / "bot_schedule2.py"
BOT_FLAT = ROOT / "tests" / ".cache" / "bot_schedule2_flat.py"


def _dedent_code(source, width=4):
    """Снять отступ со строк кода; строки внутри многострочных литералов не трогаем
    (scripts/extract_routes.py их и не сдвигал)."""
    inside = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and token.end[0] > token.start[0]:
                inside.update(range(token.start[0] + 1, token.end[0] + 1))
    except tokenize.TokenError:
        pass
    out = []
    for number, line in enumerate(source.split("\n"), start=1):
        if number not in inside and line.startswith(" " * width):
            line = line[width:]
        out.append(line)
    return "\n".join(out)


def _unnest_routes_module(text):
    """Вынесенный модуль → куски «как в монолите»."""
    module = parse(text)
    lines = _lines(text)
    parts = []
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(getattr(node.value, "value", None), str):
            continue  # докстринг модуля
        if isinstance(node, ast.FunctionDef) and node.name.startswith("build_") and node.name.endswith("_blueprint"):
            # Поздно связанные имена монолита строитель получает словарём monolith_namespace
            # (globals() монолита). В склейке всё — один модуль, и этот словарь — его globals().
            if any(arg.arg == "monolith_namespace" for arg in node.args.kwonlyargs):
                parts.append("monolith_namespace = globals()")
            for child in node.body:
                # Функции и константы раздела, завязанные на аргументы строителя, вложены в
                # него; `bp = Blueprint(...)` и `return bp` — служебные, в склейку не идут.
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                elif isinstance(child, (ast.Assign, ast.AnnAssign)) and "Blueprint(" not in ast.unparse(child.value or child):
                    start = child.lineno
                else:
                    continue
                segment = _dedent_code("".join(lines[start - 1:child.end_lineno]).rstrip("\n"))
                segment = re.sub(r"^@bp\.route\('(/[^']*)'", r"@app.route('/api\1'", segment, count=1, flags=re.M)
                parts.append(segment)
            continue
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        parts.append("".join(lines[start - 1:node.end_lineno]).rstrip("\n"))
    return "\n\n\n".join(parts)


def _compose_monolith_flat_text():
    """Монолит, в который каждый вынесенный модуль вклеен там, где стоит его регистрация
    (scripts/extract_routes.py ставит её на месте первой ручки группы): тесты, режущие текст
    «от этой функции до следующей», видят прежнее соседство."""
    text = read(BOT_FACADE)
    for path in extracted_route_modules():
        package = path.parent.name
        chunk = (f"# ── {path.relative_to(ROOT).as_posix()} (склейка для тестов) ──\n"
                 + _unnest_routes_module(read(path)) + "\n\n\n")
        marker = f"from {package}.routes import build_{package}_blueprint"
        at = text.find(marker)
        if at < 0:
            text = text.rstrip("\n") + "\n\n\n" + chunk
            continue
        line_start = text.rfind("\n", 0, at) + 1
        comment_start = text.rfind("\n", 0, line_start - 1) + 1 if text[line_start - 2:line_start - 1] != "\n" else line_start
        if text[comment_start:line_start].startswith("# Раздел"):
            line_start = comment_start
        text = text[:line_start] + chunk + text[line_start:]
    return text


_BOT_MEMO = {"key": None, "text": None}


def monolith_flat_text():
    """Склейка bot_schedule2.py; пересобирается, только когда менялся монолит или пакеты."""
    paths = [BOT_FACADE, *extracted_route_modules()]
    key = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
    if _BOT_MEMO["key"] != key:
        _BOT_MEMO["text"] = _compose_monolith_flat_text()
        _BOT_MEMO["key"] = key
    return _BOT_MEMO["text"]


def monolith_flat_path():
    """Путь к склейке bot_schedule2.py — туда, где тест писал `ROOT / 'bot_schedule2.py'`."""
    return _write_flat(BOT_FLAT, monolith_flat_text())


def _write_flat(target, text):
    """Записать склейку, если изменилась: через временный файл и переименование, чтобы
    параллельные прогоны (pytest частями) не прочитали её наполовину."""
    try:
        if target.read_text(encoding="utf-8") == text:
            return target
    except FileNotFoundError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(1, 21):
        try:
            os.replace(temp, target)
            break
        except PermissionError:
            # Windows: соседний процесс прогона как раз читает или подменяет файл — подождать;
            # если он уже записал то же самое, своя копия не нужна.
            time.sleep(0.05 * attempt)
            try:
                if target.read_text(encoding="utf-8") == text:
                    temp.unlink(missing_ok=True)
                    break
            except (FileNotFoundError, PermissionError):
                pass
    else:
        os.replace(temp, target)
    read.cache_clear()
    return target


def function_copy(path, name, class_name=None):
    """Отдельная копия узла функции — её уже можно править."""
    return copy.deepcopy(function_node(path, name, class_name=class_name))


# ── database.py после разноса на db/*.py (02.11.2026) ─────────────────────────
# Класс Database теперь собран из миксинов db/<раздел>.py, а модульный уровень живёт в
# db/core.py. Тесты, которые разбирали database.py целиком (класс Database через ast,
# SQL по тексту), получают прежнюю картину из склейки: модульный уровень, один class
# Database со всеми атрибутами и методами, исключения, хвост фасада. Новым тестам —
# читать свой db/<раздел>.py напрямую, склейка существует ради старых.
DB_FACADE = ROOT / "database.py"
DB_PARTS = ROOT / "db"
DB_FLAT = ROOT / "tests" / ".cache" / "database_flat.py"


def _class_node(module, name):
    return next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name)


def _lines(text):
    return text.splitlines(keepends=True)


def _class_chunks(text, class_name):
    """Куски тела класса: имя узла → текст от конца предыдущего узла до конца этого.

    Комментарии над методом едут вместе с ним — так же режет scripts/split_database_mixins.py,
    поэтому из кусков собирается прежний текст."""
    lines = _lines(text)
    node = _class_node(parse(text), class_name)
    chunks = {}
    prev_end = node.lineno
    for child in node.body:
        if isinstance(child, ast.Expr):  # докстринг класса
            prev_end = child.end_lineno
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = child.name
        else:
            name = child.targets[0].id
        chunks[name] = "".join(lines[prev_end:child.end_lineno])
        prev_end = child.end_lineno
    return chunks


def _compose_database_flat_text():
    """Текст database.py «как одним файлом»: см. комментарий над DB_FACADE."""
    from db._legacy_layout import LEGACY_ORDER

    facade_text = read(DB_FACADE)
    core_text = read(DB_PARTS / "core.py")
    facade, core = parse(facade_text), parse(core_text)
    facade_lines, core_lines = _lines(facade_text), _lines(core_text)
    database = _class_node(facade, "Database")

    # Модульный уровень db/core.py без его классов: их тела пойдут в Database и после него.
    core_classes = [node for node in core.body if isinstance(node, ast.ClassDef)]
    skip = set()
    for node in core_classes:
        skip.update(range(node.lineno - 1, node.end_lineno))
    parts = ["".join(line for index, line in enumerate(core_lines) if index not in skip).rstrip("\n"), "\n\n\n"]

    # Куски тела Database отовсюду: фасад (атрибуты, __init__), миксины из db/core.py и db/*.py.
    chunks = _class_chunks(facade_text, "Database")
    core_mixins = {node.name for node in core_classes}
    for base in database.bases:
        name = base.id if isinstance(base, ast.Name) else base.attr
        if name in core_mixins:
            chunks.update(_class_chunks(core_text, name))
            continue
        for path in sorted(DB_PARTS.glob("*.py")):
            if path.name in ("core.py", "__init__.py", "_legacy_layout.py"):
                continue
            text = read(path)
            if f"class {name}:" not in text:
                continue
            chunks.update(_class_chunks(text, name))
            break
        else:
            raise LookupError(f"миксин {name} не найден в db/*.py")
    # В прежнем порядке (db/_legacy_layout.py): тесты режут текст между соседними методами.
    # Чего в списке нет (новые методы) — в конец.
    ordered = [chunks.pop(name) for name in LEGACY_ORDER if name in chunks] + list(chunks.values())
    parts.append("class Database:\n" + "".join(ordered))

    # Исключения из db/core.py (в database.py стояли после класса) и хвост фасада.
    for node in core_classes:
        if node.name not in core_mixins or not node.name.endswith("Mixin"):
            parts.append("\n\n" + "".join(core_lines[node.lineno - 1:node.end_lineno]))
    parts.append("\n\n" + "".join(facade_lines[database.end_lineno:]).lstrip("\n"))
    # Единственная правка тел при разносе: статические помощники низких оценок зовут друг
    # друга через имя класса, и в миксине это ChatRatingsMixin. В склейке класс один —
    # Database, как и было: тесты собирают из неё заглушки и имени миксина не знают.
    return re.sub(r"\bChatRatingsMixin\.(_\w+)\(", r"Database.\1(", "".join(parts))


_FLAT_MEMO = {"key": None, "text": None}


def database_flat_text():
    """Склейка database.py; пересобирается, только когда менялся фасад или db/*.py."""
    paths = [DB_FACADE, *sorted(DB_PARTS.glob("*.py"))]
    key = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
    if _FLAT_MEMO["key"] != key:
        _FLAT_MEMO["text"] = _compose_database_flat_text()
        _FLAT_MEMO["key"] = key
    return _FLAT_MEMO["text"]


def database_flat_path():
    """Путь к склейке database.py (пересобирается, когда исходники изменились).

    Подставляется туда, где тест писал `ROOT / 'database.py'`: читать, разбирать через
    ast и подставлять в compile() как имя файла можно как раньше."""
    text = database_flat_text()
    try:
        if DB_FLAT.read_text(encoding="utf-8") == text:
            return DB_FLAT
    except FileNotFoundError:
        pass
    DB_FLAT.parent.mkdir(parents=True, exist_ok=True)
    # Через временный файл и переименование: параллельные прогоны (pytest частями) пишут
    # одну и ту же склейку, и читатель не должен увидеть её наполовину.
    temp = DB_FLAT.with_name(f"{DB_FLAT.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, DB_FLAT)
    read.cache_clear()
    return DB_FLAT
