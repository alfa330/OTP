# -*- coding: utf-8 -*-
"""Знак процента в SQL-комментарии ломает запрос молча — этого быть не должно.

29.08.2026, живой чат-аукцион: каждый клик «взять смену» отвечал 500, лоты
стояли нетронутыми до конца прогона. Причина не в правилах аукциона, а в
комментарии внутри SQL:

    -- по корреляции с `s` — новых параметров не добавляется,
    -- а порядок %s в этом запросе трогать опасно.

psycopg2 подставляет параметры обычным `sql % params` — он не разбирает SQL и
комментарии не пропускает. Пятый «плейсхолдер» в тексте пояснения при четырёх
параметрах даёт `IndexError: tuple index out of range` ещё до похода в базу.
Правил аукциона это не касается вовсе, поэтому по симптому («смены не берутся»)
искать можно долго.

Тот же комментарий-заготовка уехал в три места одним коммитом (4476481a):
`claim_shift_auction_test_lot`, `self_schedule_shift_auction_shift` и прогноз в
`_period_operator_availability_tx` (ветка `include_details=False`).

Сторож смотрит ровно то, что попадает в подстановку: первый аргумент
`cursor.execute(sql, params)`. Формы `%(имя)s` он разрешает — там подстановка
идёт по словарю, и лишнее вхождение в комментарии порядок не сдвигает.
"""
import ast
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".claude", "node_modules", "venv", "dist", "__pycache__", "assets", "public"}
NAMED_PLACEHOLDER_RE = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)s")


def _python_files():
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _sql_literal(node):
    """Текст SQL так, как он уйдёт в psycopg2: обычная строка или f-строка.

    В f-строке интересны только литеральные куски — вставленные выражения
    (условия вроде `{direction_filter}`) сами по себе комментариев не несут.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def _executed_sql_literals(tree):
    """(строка, SQL) для каждого `…execute(sql, params)` с параметрами."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("execute", "executemany"):
            continue
        if len(node.args) < 2:
            # Без параметров подстановки нет — процент в тексте безопасен.
            continue
        sql = _sql_literal(node.args[0])
        if sql:
            yield node.lineno, sql


def _percent_in_comment(sql):
    """Строки SQL-комментариев, где остался знак процента."""
    offenders = []
    for line in sql.split("\n"):
        marker = line.find("--")
        if marker < 0:
            continue
        comment = NAMED_PLACEHOLDER_RE.sub("", line[marker:])
        if "%" in comment:
            offenders.append(line.strip())
    return offenders


class SqlCommentPlaceholderTests(unittest.TestCase):
    def test_no_percent_sign_inside_sql_comments(self):
        offenders = []
        for path in _python_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for lineno, sql in _executed_sql_literals(tree):
                for line in _percent_in_comment(sql):
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{lineno} → {line}")
        self.assertEqual(
            offenders,
            [],
            "Знак процента в SQL-комментарии psycopg2 считает плейсхолдером — "
            "запрос падает IndexError'ом ещё до базы:\n" + "\n".join(offenders),
        )

    def test_guard_catches_the_defect_it_was_written_for(self):
        """Сам сторож ловит именно ту строку, что положила чат-аукцион."""
        broken = """
            SELECT s.enabled
            FROM shift_auction_test_access s
            -- а порядок %s в этом запросе трогать опасно.
            WHERE s.id = %s
        """
        self.assertTrue(_percent_in_comment(broken))
        fixed = broken.replace("порядок %s в этом", "порядок параметров в этом")
        self.assertFalse(_percent_in_comment(fixed))

    def test_named_placeholders_in_comments_are_allowed(self):
        """`%(имя)s` подставляется по словарю — лишнее вхождение порядок не рвёт."""
        self.assertFalse(_percent_in_comment("-- раздел уже в периметре (%(sections)s)"))


if __name__ == "__main__":
    unittest.main()
