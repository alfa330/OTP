"""scripts/extract_routes.py: global и поиск имени строкой в переносимом коде.

03.11.2026 разбор перед выкладкой нашёл два следа инструмента в готовых пакетах: флаг слушателя
аукциона, объявленный global, остался в монолите (ast.walk встретил присваивание позже
объявления и счёл имя локальным) — живой поток аукциона падал NameError; отбивка табло ОП и Тез
искала резолверы через globals().get, а в пакете globals() — уже не монолит. Теперь инструмент
переносит global-имя вместе с функцией, а код с поиском имени строкой переносить отказывается."""
import importlib.util
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MONOLITH = textwrap.dedent('''
    import threading
    from flask import Flask, jsonify

    app = Flask(__name__)
    listener_started = False
    listener_lock = threading.Lock()


    def _ensure_listener():
        global listener_started
        if listener_started:
            return
        with listener_lock:
            if not listener_started:
                listener_started = True


    def _late_department():
        resolver = globals().get('_late_resolver')
        return resolver() if resolver else None


    @app.route('/api/demo/stream')
    def demo_stream():
        _ensure_listener()
        return jsonify({'ok': True})


    @app.route('/api/late/check')
    def late_check():
        return jsonify({'department': _late_department()})


    _late_resolver = lambda: 7
''')

SHARED_FLAG = MONOLITH + textwrap.dedent('''

    def bot_status():
        return listener_started
''')


def _load_tool():
    spec = importlib.util.spec_from_file_location('extract_routes_under_test', ROOT / 'scripts' / 'extract_routes.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


class ExtractRoutesToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_tool()

    def _plan(self, source, prefix, package):
        return self.tool.plan(self.tool.Monolith(source), prefix, package)

    def test_free_names_keep_a_global_assigned_in_a_nested_block(self):
        node = self.tool.Monolith(MONOLITH).defs['_ensure_listener']
        self.assertIn('listener_started', self.tool.free_names(node))

    def test_global_name_moves_to_the_package_with_its_function(self):
        plan = self._plan(MONOLITH, '/api/demo', 'demo')
        self.assertIn('listener_started', plan['moved'])
        self.assertIn('_ensure_listener', plan['moved'])
        self.assertEqual(plan['blocked'], [])

    def test_global_on_a_name_the_monolith_keeps_blocks_the_move(self):
        """Флагом пользуется и монолит — он станет аргументом строителя, и global до него не дотянется."""
        plan = self._plan(SHARED_FLAG, '/api/demo', 'demo')
        self.assertIn('listener_started', plan['injected'])
        self.assertTrue(any('global listener_started' in line for line in plan['blocked']), plan['blocked'])

    def test_lookup_by_string_blocks_the_move(self):
        plan = self._plan(MONOLITH, '/api/late', 'late')
        self.assertTrue(any('globals()' in line for line in plan['blocked']), plan['blocked'])


if __name__ == '__main__':
    unittest.main()
