"""Перенос группы ручек Flask из bot_schedule2.py в пакет раздела — механикой, не руками.

    python scripts/extract_routes.py --prefix /api/events --package events --report
    python scripts/extract_routes.py --prefix /api/events --package events --write

Что делает:
  * находит все @app.route('<prefix>/…') в монолите и считает замыкание модульных имён,
    которые эти ручки используют (транзитивно, через помощники и константы);
  * имя «переносимо», если его замыкание не упирается в якоря монолита (app, bot, db,
    планировщик…): такие помощники и константы уезжают в <package>/routes.py байт в байт;
    если их зовут и из других мест монолита, монолит импортирует их обратно на прежней строке;
  * непереносимое (db, require_api_key, _get_authenticated_requester, …) становится
    именованными аргументами строителя build_<package>_blueprint — так устроены все
    33 пакета репозитория (см. wiki/routes.py);
  * ручки становятся вложенными функциями строителя с @bp.route('<путь без /api>'):
    тела не меняются, только отступ кода; строки многострочных литералов не трогаются,
    чтобы SQL и тексты сообщений остались теми же байтами;
  * из монолита ручки и переносимые помощники удаляются, на месте первой ручки встаёт
    регистрация блюпринта.
После --write: ruff F821 по обоим файлам, тесты, читавшие эти функции из bot_schedule2.py,
перенацелить на <package>/routes.py (список печатает --report).
"""
import argparse
import ast
import builtins
import io
import os
import re
import sys
import tokenize
from collections import OrderedDict

BUILTINS = set(dir(builtins))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONOLITH = os.path.join(ROOT, 'bot_schedule2.py')
# Имена, замыкание которых делает помощник непереносимым: живут только в процессе монолита.
ANCHORS = {'app', 'bot', 'dp', 'db', 'scheduler', 'storage', 'executor_pool', 'crm_pool',
           'auth_maintenance_executor', 'group_late_pool', 'fleet_edm_pool', 'olx_amo_pool',
           'yandex_pro_pool', 'op_funnel_pool', 'ai_qa_sample_pool', 'ai_qa_digest_pool',
           'binotel_panel_pool', 'resource_forecast_pool', '_SZOV_BOT_LOOP'}
FLASK_NAMES = {'request', 'jsonify', 'g', 'Response', 'abort', 'send_file', 'make_response',
               'stream_with_context', 'redirect', 'url_for', 'current_app', 'Blueprint'}
STAMP = 'вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026'


def read_text(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def write_text(path, text, newline):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text.replace('\n', newline))


def first_line(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def free_names(node):
    loads, bound = set(), set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            (loads if isinstance(sub.ctx, ast.Load) else bound).add(sub.id)
        elif isinstance(sub, ast.arg):
            bound.add(sub.arg)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            bound.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                bound.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            bound.add(sub.name)
        elif isinstance(sub, ast.Global):
            loads.update(sub.names)
            bound.difference_update(sub.names)
    return loads - bound


class Monolith:
    def __init__(self, text):
        self.text = text
        self.lines = text.split('\n')
        self.tree = ast.parse(text)
        self.defs = OrderedDict()      # имя -> узел (функции, классы, константы)
        self.imports = OrderedDict()   # имя -> строка импорта, которой оно связано
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs.setdefault(node.name, node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.defs.setdefault(target.id, node)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.defs.setdefault(node.target.id, node)
        # Импорты монолита — и те, что стоят внутри try/if на верхнем уровне (PIL, необязательные пакеты).
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.setdefault(alias.asname or alias.name.split('.')[0], ast.unparse(ast.Import(names=[alias])))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    self.imports.setdefault(alias.asname or alias.name,
                                            ast.unparse(ast.ImportFrom(module=node.module, names=[alias], level=0)))
        self._free = {}
        self._movable = {}
        # Свободные имена каждого узла верхнего уровня — один раз: users_of зовётся часто.
        self._node_free = []
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                self._node_free.append((node, free_names(node)))
            else:
                self._node_free.append((node, {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}))

    def free(self, name):
        if name not in self._free:
            self._free[name] = free_names(self.defs[name]) if name in self.defs else set()
        return self._free[name]

    def is_function(self, name):
        return isinstance(self.defs.get(name), (ast.FunctionDef, ast.AsyncFunctionDef))

    def movable(self, name, trail=()):
        """Переносимо ли имя: его замыкание в монолите не упирается в якоря."""
        if name in self._movable:
            return self._movable[name]
        if name in ANCHORS:
            return False
        if name not in self.defs:
            return True  # импорт или встроенное
        if name in trail:
            return True  # рекурсия — решат другие ветви
        result = all(self.movable(dep, trail + (name,)) for dep in self.free(name)
                     if dep in self.defs or dep in ANCHORS)
        self._movable[name] = result
        return result

    def routes(self, prefix):
        found = []
        for node in self.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == 'route' and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == 'app' and dec.args
                        and isinstance(dec.args[0], ast.Constant)):
                    path = dec.args[0].value
                    if path == prefix or path.startswith(prefix.rstrip('/') + '/'):
                        found.append((node, dec, path))
        return found

    def users_of(self, name, exclude):
        """Узлы верхнего уровня (кроме exclude), использующие имя."""
        result = []
        for node, names in self._node_free:
            key = getattr(node, 'name', None)
            if key in exclude or node is self.defs.get(name):
                continue
            if name in names:
                result.append(node)
        return result

    def segment(self, node):
        """Текст узла с декораторами, без комментариев над ним."""
        return '\n'.join(self.lines[first_line(node) - 1:node.end_lineno])


def indent_code(source, pad='    '):
    """Отступ только строкам кода: строки внутри многострочных литералов не трогаем."""
    inside = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                inside.update(range(tok.start[0] + 1, tok.end[0] + 1))
    except tokenize.TokenError:
        pass
    out = []
    for number, line in enumerate(source.split('\n'), start=1):
        out.append(line if (number in inside or not line.strip()) else pad + line)
    return '\n'.join(out)


def plan(mono, prefix, package):
    routes = mono.routes(prefix)
    if not routes:
        raise SystemExit(f'ручек с префиксом {prefix} не найдено')
    route_names = {node.name for node, _, _ in routes}

    # Замыкание: все модульные имена, которых касаются ручки, транзитивно. `app` из
    # декоратора @app.route не в счёт — декоратор переписывается на bp.route.
    def route_free(node):
        names = free_names(node)
        if 'app' in names and not any(
                isinstance(sub, ast.Name) and sub.id == 'app'
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[]))):
            names.discard('app')
        return names

    # Своё — то, что никто вне группы не использует. Переносимое своё уезжает на уровень модуля;
    # своё, но завязанное на якоря (db, require_api_key…), становится вложенной функцией
    # строителя — рядом с ручками, с теми же аргументами. Общие помощники (CORS, роли, GCS)
    # остаются в монолите и приходят аргументами — им потом свой дом в common/.
    needed = OrderedDict()

    def absorb(names):
        """Добавить имена в замыкание; переносимое раскрывается сразу (его зависимости тоже едут)."""
        queue = list(names)
        while queue:
            name = queue.pop(0)
            if name in needed or name in route_names:
                continue
            needed[name] = True
            if name in mono.defs and mono.movable(name):
                queue.extend(n for n in sorted(mono.free(name)) if n not in needed)

    absorb(n for node, _, _ in routes for n in sorted(route_free(node)))

    def own_names():
        """Своё = никто вне группы и уезжающих не использует; до неподвижной точки."""
        private = {name for name in needed
                   if name in mono.defs and name not in ANCHORS
                   and (mono.movable(name) or mono.is_function(name))}
        changed = True
        while changed:
            changed = False
            for name in sorted(private):
                if mono.users_of(name, route_names | private):
                    private.discard(name)
                    changed = True
        return private

    # Вложенные помощники (свои, но на якорях) раскрываются после того, как стали своими:
    # их зависимости тоже должны попасть в замыкание. Повторяем, пока замыкание растёт.
    while True:
        private = own_names()
        extra = [dep for name in sorted(private) if not mono.movable(name)
                 for dep in sorted(mono.free(name)) if dep not in needed and dep not in route_names]
        if not extra:
            break
        absorb(extra)

    moved, nested, injected, imports, flask, unknown = [], [], [], [], [], []
    for name in needed:
        if name in mono.defs:
            if name in private and mono.movable(name):
                moved.append(name)
            elif name in private:
                nested.append(name)
            else:
                injected.append(name)
        elif name in FLASK_NAMES:
            pass
    # На уровень модуля можно только то, что не трогает аргументы строителя и вложенных:
    # иначе имя не найдётся. Такие помощники тоже вкладываются в строитель.
    changed = True
    while changed:
        changed = False
        for name in list(moved):
            if mono.free(name) & (set(injected) | set(nested)):
                moved.remove(name)
                nested.append(name)
                changed = True
    for name in needed:
        if name in mono.defs:
            continue
        if name in FLASK_NAMES:
            flask.append(name)
        elif name in mono.imports:
            imports.append(mono.imports[name])
        elif name in BUILTINS:
            continue
        else:
            unknown.append(name)
    # Переносимые — в порядке объявления в монолите (константы зависят друг от друга).
    moved.sort(key=lambda n: first_line(mono.defs[n]))
    nested.sort(key=lambda n: first_line(mono.defs[n]))
    reimport = []
    return {
        'routes': routes, 'moved': moved, 'nested': nested, 'injected': sorted(injected),
        'flask': sorted(set(flask)), 'imports': sorted(set(imports)), 'unknown': sorted(unknown),
        'reimport': reimport,
    }


def render_module(mono, prefix, package, p):
    newline = '\n'
    lines = ['"""Раздел «' + package + '»: ручки ' + prefix + ' — ' + STAMP + '.',
             '',
             'Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).',
             'Зависимости от процесса монолита приходят аргументами build_' + package + '_blueprint,',
             'как у остальных пакетов репозитория."""']
    if p['flask']:
        lines.append('from flask import ' + ', '.join(['Blueprint'] + [n for n in p['flask'] if n != 'Blueprint']))
    else:
        lines.append('from flask import Blueprint')
    lines += p['imports']
    lines.append('')
    for name in p['moved']:
        lines += ['', mono.segment(mono.defs[name])]
    if not p['injected']:
        signature = [f'def build_{package}_blueprint():']
    elif len(', '.join(p['injected'])) < 70:
        signature = [f'def build_{package}_blueprint(*, {", ".join(p["injected"])}):']
    else:
        signature = [f'def build_{package}_blueprint(', '    *,'] + [f'    {name},' for name in p['injected']] + ['):']
    lines += ['', ''] + signature + [
        '    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""',
        f"    bp = Blueprint('{package}', __name__, url_prefix='/api')"]
    for name in p['nested']:
        lines += ['', indent_code(mono.segment(mono.defs[name]))]
    for node, dec, path in p['routes']:
        segment = mono.segment(node)
        rule = path[len('/api'):] or '/'
        dec_src = mono.lines[dec.lineno - 1]
        new_dec = dec_src.replace('@app.route(', '@bp.route(', 1).replace(repr(path), repr(rule), 1)
        if new_dec == dec_src:
            new_dec = dec_src.replace('@app.route(', '@bp.route(', 1).replace(path, rule, 1)
        segment = segment.replace(dec_src, new_dec, 1)
        lines += ['', indent_code(segment)]
    lines += ['', '    return bp', '']
    return newline.join(lines)


def register_block(package, p):
    args = ''.join(f'        {name}={name},\n' for name in p['injected'])
    return (f'# Раздел «{package}»: ручки вынесены в {package}/routes.py ({STAMP.split(",")[0].split(" скриптом")[0]}).\n'
            f'from {package}.routes import build_{package}_blueprint  # noqa: E402\n'
            f'app.register_blueprint(build_{package}_blueprint(\n{args}))\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--prefix', required=True, help='например /api/events')
    parser.add_argument('--package', required=True, help='каталог пакета, например events')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--report', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args()

    raw = read_text(MONOLITH)
    newline = '\r\n' if '\r\n' in raw else '\n'
    mono = Monolith(raw.replace('\r\n', '\n'))
    p = plan(mono, args.prefix, args.package)

    print(f'ручек: {len(p["routes"])}')
    for node, _, path in p['routes']:
        print(f'  {path:50s} {node.name}  ({node.end_lineno - first_line(node) + 1} строк)')
    print('переносимые помощники и константы:', p['moved'] or '—')
    print('вложенные в строитель (свои, но на db/require_*):', p['nested'] or '—')
    print('аргументы строителя (общее с монолитом):', p['injected'] or '—')
    print('flask:', p['flask'], '| импорты:', len(p['imports']))
    if p['unknown']:
        print('НЕИЗВЕСТНЫЕ имена:', p['unknown'])
    tests = []
    names = [n.name for n, _, _ in p['routes']] + p['moved']
    for entry in sorted(os.listdir(os.path.join(ROOT, 'tests'))):
        if entry.endswith('.py'):
            text = read_text(os.path.join(ROOT, 'tests', entry))
            if 'bot_schedule2' in text and any(re.search(rf'\b{re.escape(n)}\b', text) for n in names):
                tests.append(entry)
    print(f'тесты, читающие эти имена из bot_schedule2: {len(tests)}', tests[:12])
    if args.report:
        return 1 if p['unknown'] else 0
    if p['unknown']:
        raise SystemExit('есть неизвестные имена — перенос остановлен')

    target = os.path.join(ROOT, args.package, 'routes.py')
    if os.path.exists(target):
        raise SystemExit(f'{target} уже существует: дописывание в готовый пакет пока вручную')
    write_text(target, render_module(mono, args.prefix, args.package, p), newline)
    init_path = os.path.join(ROOT, args.package, '__init__.py')
    if not os.path.exists(init_path):
        write_text(init_path, f'"""Раздел «{args.package}» ({STAMP})."""\n', newline)

    # Монолит: убрать перенесённое, вставить регистрацию и обратные импорты.
    lines = mono.lines[:]
    removals = []
    for node, _, _ in p['routes']:
        removals.append((first_line(node), node.end_lineno, None))
    for name in p['moved'] + p['nested']:
        node = mono.defs[name]
        back = f'from {args.package}.routes import {name}  # noqa: E402 — {STAMP.split(",")[0]}' if name in p['reimport'] else None
        removals.append((first_line(node), node.end_lineno, back))
    # Регистрация выполняется при импорте, поэтому встаёт после последнего определения из
    # аргументов строителя (они могут стоять ниже самих ручек), но не раньше первой ручки.
    anchor = min(first_line(node) for node, _, _ in p['routes'])
    for name in p['injected']:
        node = mono.defs.get(name)
        if node is not None and node.end_lineno + 1 > anchor:
            anchor = node.end_lineno + 1
    removals.append((anchor, anchor - 1, register_block(args.package, p).rstrip('\n')))
    removals.sort()
    output, cursor = [], 1
    for start, end, back in removals:
        output += lines[cursor - 1:start - 1]
        if back:
            output += back.split('\n')
        cursor = max(cursor, end + 1)
    output += lines[cursor - 1:]
    write_text(MONOLITH, '\n'.join(output), newline)
    print(f'записано: {args.package}/routes.py; из bot_schedule2.py убрано {len(removals)} узлов')
    return 0


if __name__ == '__main__':
    sys.exit(main())
