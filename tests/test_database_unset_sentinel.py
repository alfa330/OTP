# -*- coding: utf-8 -*-
"""Часовой «поле не пришло» должен быть ровно один — модульный.

Как ломалось (задача #258, 400 «Invalid deadline» при добавлении исполнителя).
21.08.2026 рядом с `update_training` в тело класса `Database` положили второй
`_UNSET = object()`. Значения по умолчанию в сигнатурах методов вычисляются в
области КЛАССА и связались с ним, а сравнения `x is not _UNSET` внутри тел
методов ищут имя по правилам функций — локальная, объемлющая, МОДУЛЬНАЯ — и
область класса при этом пропускается. В результате у всех методов ниже
`has_<поле>` было истинно всегда: `edit_task` считала, что `due_at` прислали,
и отправляла сам объект-часовой в `datetime.fromisoformat` → `INVALID_DEADLINE`.
Ломалось любое сохранение из модалки правки задачи, а не только дедлайн.

Ошибка тихая: код читается правильно, тесты на отдельных функциях зелёные,
падает только сборка целиком. Поэтому проверяем инвариант, а не симптом.
"""

import ast
import unittest
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")


def _database_class():
    module = source_cache.parse(SOURCE)
    return next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _class_level_names(class_node):
    """Имена, присвоенные прямо в теле класса."""
    names = {}
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node.lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = node.lineno
    return names


def _default_names(function_node):
    """Имена, которыми метод пользуется как значением по умолчанию."""
    defaults = list(function_node.args.defaults) + [d for d in function_node.args.kw_defaults if d]
    return {d.id for d in defaults if isinstance(d, ast.Name)}


class DatabaseUnsetSentinelTest(unittest.TestCase):
    def test_class_body_does_not_shadow_default_values(self):
        """Атрибут класса не должен перекрывать имя, стоящее в дефолтах методов."""
        class_node = _database_class()
        class_names = _class_level_names(class_node)

        shadowed = []
        for node in class_node.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for name in sorted(_default_names(node) & set(class_names)):
                shadowed.append(f"{node.name} (строка {node.lineno}) ← {name} "
                                f"из тела класса (строка {class_names[name]})")

        self.assertEqual(shadowed, [], "\n".join([
            "Значение по умолчанию связано с атрибутом класса, а сравнение в теле метода —",
            "с модулем: «поле не пришло» перестаёт отличаться от «поле пришло».",
            "Часовому место в модуле, а не в теле класса.",
            *shadowed,
        ]))

    def test_sentinel_lives_in_module_only(self):
        """`_UNSET` объявлен один раз и на уровне модуля."""
        module = source_cache.parse(SOURCE)
        module_level = [
            node.lineno for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_UNSET" for t in node.targets)
        ]
        self.assertEqual(len(module_level), 1, "модульный _UNSET должен быть ровно один")
        self.assertNotIn("_UNSET", _class_level_names(_database_class()),
                         "в теле класса Database не должно быть своего _UNSET")

    def test_no_qualified_sentinel_left(self):
        """`Database._UNSET` больше не существует — обращения к нему упадут AttributeError."""
        self.assertNotIn("Database._UNSET", SOURCE)

    def test_edit_task_can_leave_deadline_untouched(self):
        """Модель поломки: с двумя часовыми `has_due_at` истинно даже без аргумента."""
        namespace = {}
        exec(  # noqa: S102 — воспроизводим ровно ту конструкцию, что дала дефект
            "_UNSET = object()\n"
            "class Broken:\n"
            "    _UNSET = object()\n"
            "    def edit(self, due_at=_UNSET):\n"
            "        return due_at is not _UNSET\n"
            "class Fixed:\n"
            "    def edit(self, due_at=_UNSET):\n"
            "        return due_at is not _UNSET\n",
            namespace,
        )
        self.assertTrue(namespace["Broken"]().edit(), "проверка сама себя не проверяет")
        self.assertFalse(namespace["Fixed"]().edit())


if __name__ == "__main__":
    unittest.main()
