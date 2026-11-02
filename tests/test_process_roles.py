"""Роли процесса (deploy/yandex): api, realtime, worker — проверки по тексту, без импорта монолита.

Главное, что здесь охраняется: SSE-потоки живут в контейнере realtime, и Caddy обязан знать
каждый их путь. Новая ручка с text/event-stream без строки в матчере @realtime тихо осядет
в api и начнёт держать его нити — ровно то, от чего разнос делался."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONOLITH = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')  # роли и бюджет нитей живут в самом монолите
# Лимиты стримов: кто-то задан в монолите и передан строителю пакета, кто-то — внутри самого пакета.
BACKEND = {'bot_schedule2.py': MONOLITH,
           **{path.relative_to(ROOT).as_posix(): path.read_text(encoding='utf-8') for path in ROOT.glob('*/routes.py')}}
CADDYFILE = (ROOT / 'deploy' / 'yandex' / 'Caddyfile').read_text(encoding='utf-8')
COMPOSE = (ROOT / 'deploy' / 'yandex' / 'docker-compose.yml').read_text(encoding='utf-8')

# Путь каждого долгоживущего потока → файл, где он отдаётся.
STREAM_PATHS = {
    '/api/notifications/stream': 'notifications/routes.py',
    '/api/wazzup/pilot/stream': 'wazzup/pilot.py',
    '/api/op_wallboard/live': 'op_wallboard/routes.py',
    '/api/pbx/stream': 'pbx/routes.py',
    '/api/pbx/agent/stream': 'pbx/routes.py',
    '/api/shift_auction/test_events': 'bot_schedule2.py',
}
# Стрим на секунды (озвучка тренажёра), остаётся в api — см. Caddyfile.
SHORT_STREAM_FILES = {'voice_trainer/routes.py'}
SKIP_DIRS = {'.git', '.claude', 'node_modules', 'outputs', 'tests', 'scripts', '__pycache__',
             'asr_test', 'microsip_dnd_shield', 'oktell_recall_guard', 'cdr_bridge', 'dist'}


def _files_with_event_stream():
    found = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith('.py'):
                continue
            path = Path(dirpath) / name
            if 'text/event-stream' in path.read_text(encoding='utf-8', errors='ignore'):
                found.add(path.relative_to(ROOT).as_posix())
    return found


def _realtime_matcher_paths():
    match = re.search(r'^\s*@realtime path (.+)$', CADDYFILE, re.M)
    assert match, 'в Caddyfile нет матчера @realtime'
    return set(match.group(1).split())


def test_roles_declared_with_exact_set():
    assert "OTP_ROLE = (os.getenv('OTP_ROLE') or 'all').strip().lower()" in MONOLITH
    assert "if OTP_ROLE not in ('all', 'api', 'realtime', 'worker'):" in MONOLITH
    assert "ROLE_SERVES_HTTP = OTP_ROLE != 'worker'" in MONOLITH
    assert "ROLE_RUNS_BOT = OTP_ROLE in ('all', 'worker')" in MONOLITH


def test_main_branches_by_role():
    main_block = MONOLITH[MONOLITH.index("if __name__ == '__main__':"):]
    assert 'if not ROLE_RUNS_BOT:' in main_block
    assert '_start_bot_sender_loop()' in main_block
    assert 'if ROLE_SERVES_HTTP:' in main_block
    # Без polling цикл для отправки обязан быть запомнен там же, где его запоминает on_startup.
    assert "_SZOV_BOT_LOOP['loop'] = loop" in MONOLITH[MONOLITH.index('def _start_bot_sender_loop'):]


def test_stream_limits_capped_by_thread_budget():
    assert "WAITRESS_THREADS = _env_int('WAITRESS_THREADS', 96, minimum=32, maximum=4000)" in MONOLITH
    assert 'threads=WAITRESS_THREADS,' in MONOLITH
    assert 'connection_limit=WAITRESS_CONN_LIMIT,' in MONOLITH
    for name in ('SHIFT_AUCTION_STREAM_LIMIT', 'WAZZUP_PILOT_STREAM_LIMIT', 'PBX_STREAM_LIMIT',
                 'OP_WALLBOARD_LIVE_STREAM_LIMIT', 'BELL_STREAM_LIMIT'):
        pattern = rf"_env_int\('{name}', \d+, minimum=\d+, maximum=WAITRESS_THREADS\)"
        hits = [file for file, text in BACKEND.items() if re.search(pattern, text)]
        assert len(hits) == 1, f'{name}: нужен потолок WAITRESS_THREADS ровно в одном файле, найдено {hits}'


def test_caddy_routes_every_stream_to_realtime():
    paths = _realtime_matcher_paths()
    missing = set(STREAM_PATHS) - paths
    assert not missing, f'в Caddyfile не хватает путей стримов: {sorted(missing)}'
    assert 'reverse_proxy @realtime realtime:8080' in CADDYFILE
    assert 'reverse_proxy api:8080' in CADDYFILE


def test_every_event_stream_file_is_accounted_for():
    """Новый файл с SSE — либо путь в STREAM_PATHS и в Caddyfile, либо осознанно короткий стрим."""
    files = _files_with_event_stream()
    known = set(STREAM_PATHS.values()) | SHORT_STREAM_FILES
    unknown = files - known
    assert not unknown, (f'новые SSE-ручки без маршрута в realtime: {sorted(unknown)} — '
                         f'добавь путь в STREAM_PATHS и в @realtime Caddyfile')
    gone = known - files
    assert not gone, f'файлы со стримами переехали, обнови STREAM_PATHS: {sorted(gone)}'


def test_compose_runs_three_roles_of_one_image():
    for role in ('api', 'realtime', 'worker'):
        assert f'OTP_ROLE: {role}' in COMPOSE, role
    # Сумма лимитов потоков realtime меньше его нитей: остаток на подключение и короткие ответы.
    realtime = COMPOSE[COMPOSE.index('  realtime:'):COMPOSE.index('  worker:')]
    threads = int(re.search(r'WAITRESS_THREADS: "(\d+)"', realtime).group(1))
    limits = [int(value) for value in re.findall(r'_STREAM_LIMIT: "(\d+)"', realtime)]
    assert len(limits) == 5 and sum(limits) < threads, (threads, limits)
    assert int(re.search(r'WAITRESS_CONN_LIMIT: "(\d+)"', realtime).group(1)) > threads
