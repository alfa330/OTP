"""Пространства имён после разноса монолитов — по настоящим модулям, а не по склейкам.

Склейки tests/source_cache.py собирают вынесенный код обратно в один модуль, и в них работает
то, что в настоящих модулях ломается: global на имя, оставшееся в монолите; globals().get по
имени монолита; вызов помощника через класс чужого миксина. Так 03.11.2026 перед выкладкой
нашлись три ошибки — 500 у живого потока аукциона смен, 403 главам отделов в настройке отбивки
табло ОП и Тез, спящий AttributeError в оценках чатов, — и ни один тест на склейках их не видел."""
import ast
import importlib.util
import unittest
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parent.parent
DB_MODULES = sorted((ROOT / 'db').glob('*.py'))
DYNAMIC_CALLS = {'globals', 'vars', 'locals', 'eval', 'exec'}


def _parse(path):
    return ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))


def _where(path, node):
    return f'{path.relative_to(ROOT).as_posix()}:{node.lineno}'


def _module_level_names(tree):
    """Имена, связанные на уровне модуля, в том числе внутри if/try/with/for верхнего уровня."""
    names = set()

    def visit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split('.')[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    names.update(sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name))
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ('body', 'orelse', 'finalbody'):
                    visit(getattr(node, field, None) or [])
                for handler in getattr(node, 'handlers', None) or []:
                    visit(handler.body)

    visit(tree.body)
    return names


def _split_modules():
    """Модули, появившиеся при разносе: пакеты scripts/extract_routes.py и db/*.py."""
    return list(source_cache.extracted_route_modules()) + DB_MODULES


class GlobalStatementTests(unittest.TestCase):
    def test_every_global_name_is_bound_in_the_same_module(self):
        """global в функции пакета (и во вложенной в строитель тоже) указывает на модуль пакета:
        имя, оставшееся в bot_schedule2.py, даёт NameError при первом чтении."""
        problems = []
        for path in _split_modules():
            tree = _parse(path)
            top = _module_level_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Global):
                    problems += [f'{_where(path, node)}: global {name}' for name in node.names if name not in top]
        self.assertEqual(problems, [])


class StringLookupTests(unittest.TestCase):
    def test_split_modules_do_not_look_names_up_by_string(self):
        """globals()/vars() в вынесенном коде смотрят в пакет, а не в монолит. Имена монолита,
        присвоенные ниже регистрации пакета, строитель получает словарём monolith_namespace."""
        problems = []
        for path in _split_modules():
            for node in ast.walk(_parse(path)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in DYNAMIC_CALLS):
                    problems.append(f'{_where(path, node)}: {node.func.id}()')
        self.assertEqual(problems, [])

    def test_names_taken_from_the_monolith_namespace_exist_there(self):
        """monolith_namespace.get('имя') молча вернёт None, если имени в монолите нет."""
        monolith_top = _module_level_names(_parse(ROOT / 'bot_schedule2.py'))
        problems, seen = [], 0
        for path in source_cache.extracted_route_modules():
            for node in ast.walk(_parse(path)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'get' and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'monolith_namespace'):
                    seen += 1
                    name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                    if name not in monolith_top:
                        problems.append(f'{_where(path, node)}: {name!r}')
        self.assertEqual(problems, [])
        if (ROOT / 'szov_wallboard' / 'routes.py').exists():
            self.assertGreaterEqual(seen, 3, 'отбивка табло ОП и Тез ищет резолверы и снимок в монолите')


class MixinQualifiedCallTests(unittest.TestCase):
    def test_class_qualified_helpers_live_in_that_mixin(self):
        """ChatRatingsMixin._x(...) не идёт по MRO Database: _x обязан лежать в самом миксине."""
        classes = {}
        for path in DB_MODULES:
            for node in _parse(path).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                attrs = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        attrs.add(item.name)
                    elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                        for target in (item.targets if isinstance(item, ast.Assign) else [item.target]):
                            if isinstance(target, ast.Name):
                                attrs.add(target.id)
                classes[node.name] = attrs
        problems = []
        for path in DB_MODULES:
            for node in ast.walk(_parse(path)):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id in classes and node.attr not in classes[node.value.id]):
                    problems.append(f'{_where(path, node)}: {node.value.id}.{node.attr}')
        self.assertEqual(problems, [])


class MixinGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            'split_database_mixins_under_test', ROOT / 'scripts' / 'split_database_mixins.py')
        cls.gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gen)

    def _function(self, source):
        return ast.parse(source).body[0]

    def test_low_rating_normalizers_belong_to_chat_ratings(self):
        """«norm» из правила графиков забирал их, и ChatRatingsMixin._normalize_… вёл в никуда."""
        for name in ('_normalize_low_rating_review_status', '_normalize_low_rating_operator_ids',
                     '_normalize_low_rating_phone'):
            node = self._function(f'def {name}(value):\n    return value\n')
            self.assertEqual(self.gen.classify(node)[0], 'chat_ratings', name)
        node = self._function('def auto_fill_norm_hours(self):\n    return None\n')
        self.assertEqual(self.gen.classify(node)[0], 'schedules')

    def test_global_name_stays_free_after_a_nested_assignment(self):
        node = self._function(
            'def ensure():\n'
            '    global flag\n'
            '    if flag:\n'
            '        return\n'
            '    with lock:\n'
            '        if not flag:\n'
            '            flag = True\n')
        self.assertIn('flag', self.gen.free_names(node))


if __name__ == '__main__':
    unittest.main()
