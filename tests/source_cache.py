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
    return next(
        node for node in body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def function_copy(path, name, class_name=None):
    """Отдельная копия узла функции — её уже можно править."""
    return copy.deepcopy(function_node(path, name, class_name=class_name))
