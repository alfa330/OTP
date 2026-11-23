# -*- coding: utf-8 -*-
"""«Дисциплина»: PDF со скриптом или запуском программы не принимается
(владелец, 23.11.2026, «закрой дыры»).

Опасных файлов тесты не собирают: решение проверяется на строках (is_active,
raw_actions), а обход объектов — на безобидных PDF с ключом-меткой
/ICoreMarker и действием-меткой /ICoreAction. Где обход видит метку, там
увидит и скрипт; чтобы пройти путь проверки целиком, метку на время теста
объявляют «опасной».
Базы не нужно.
"""
import io
import subprocess
import sys

import pytest

pypdf = pytest.importorskip('pypdf')

from discipline import pdf_guard  # noqa: E402
from tests import pdf_samples  # noqa: E402

MARKER = b'/ICoreMarker true'
ACTION = b'/S /ICoreAction'


@pytest.fixture(autouse=True)
def fresh_cache():
    pdf_guard._CACHE.clear()
    yield
    pdf_guard._CACHE.clear()


def _keys(data):
    reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    return {str(key) for keys, _action in pdf_guard.dictionaries(reader) for key in keys}


def _written(user_password=None):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(595, 842)
    if user_password is not None:
        writer.encrypt(user_password=user_password, owner_password='владелец', algorithm='AES-256')
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ── Решение — на строках ────────────────────────────────────────────────────

@pytest.mark.parametrize('keys, action, active', [
    (('/Type', '/S', '/JS'), '/JavaScript', True),     # действие-скрипт
    (('/S', '/JS'), '/Rendition', True),               # скрипт внутри другого действия
    (('/JavaScript',), None, True),                    # скрипты документа в /Names
    (('/Fields', '/XFA'), None, True),                 # форма XFA
    (('/S', '/F', '/Win'), '/Launch', True),           # запуск программы или файла
    (('/Type', '/Subtype', '/OnInstantiate'), None, True),  # скрипт 3D-модели
    (('/S', '/URI'), '/URI', False),                   # обычная ссылка
    (('/S', '/D'), '/GoTo', False),                    # переход внутри документа
    (('/Type', '/Font', '/BaseFont'), None, False),
    (('/Type', '/Page', '/Annots', '/Contents'), None, False),
])
def test_active_dictionary(keys, action, active):
    assert pdf_guard.is_active(keys, action) is active


def test_raw_actions_need_the_action_type_and_decode_codes():
    assert pdf_guard.raw_actions(b'<< /S /J#61vaScript >>') == {'/JavaScript'}
    assert pdf_guard.raw_actions(b'<</S/Launch/F(x)>>') == {'/Launch'}
    assert pdf_guard.raw_actions(b'<< /#53 /L#61#75nch >>') == {'/Launch'}
    assert pdf_guard.raw_actions(b'<< /S %\xd0\xbf\xd1\x80\xd0\xbe\n/JavaScript >>') == {'/JavaScript'}
    # Обычная ссылка на страницу про JavaScript — не действие, как и имена длиннее.
    assert pdf_guard.raw_actions(b'<< /S /URI /URI (https://developer.mozilla.org/docs/Web/JavaScript) >>') == set()
    assert pdf_guard.raw_actions(b'<< /S /LaunchPad >> << /S /JavaScripts >> /JavaScript /Launch') == set()


# ── Обход — на безобидных PDF ───────────────────────────────────────────────

def test_walk_reaches_every_object():
    # Открытым текстом, кодами, в объекте, на который никто не ссылается, сжатым
    # в поток объектов и спрятанным внутри данных чужого потока.
    for data in (pdf_samples.pdf(MARKER), pdf_samples.pdf(b'/I#43oreMarker true'),
                 pdf_samples.pdf(more=[b'<< ' + MARKER + b' >>']),
                 pdf_samples.pdf_with_object_stream(MARKER),
                 pdf_samples.pdf_with_object_in_stream_data(MARKER)):
        assert '/ICoreMarker' in _keys(data)
    assert '/ICoreMarker' not in _keys(pdf_samples.pdf_with_object_stream())
    # Сжатого объекта в сырых байтах не видно — поэтому файл и разбирает pypdf.
    assert pdf_guard.raw_actions(pdf_samples.pdf_with_object_stream(ACTION), {'/ICoreAction'}) == set()
    assert pdf_guard.raw_actions(pdf_samples.pdf(ACTION), {'/ICoreAction'}) == {'/ICoreAction'}


def test_walk_does_not_keep_checked_objects():
    # pypdf держит разобранное в кэше до конца: PDF на 262 страницы занимал так 423 МБ.
    for data in (pdf_samples.pdf(more=[b'<< ' + MARKER + b' >>']), pdf_samples.pdf_with_object_stream(MARKER)):
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        assert len(list(pdf_guard.dictionaries(reader))) >= 4
        assert not reader.resolved_objects


def test_scan_refuses_what_either_pass_finds(monkeypatch):
    # Разбор: метка в сжатом объекте.
    monkeypatch.setattr(pdf_guard, 'SCRIPT_KEYS', pdf_guard.SCRIPT_KEYS | {'/ICoreMarker'})
    assert pdf_guard.scan(pdf_samples.pdf_with_object_stream(MARKER)) == pdf_guard.SCRIPT
    assert pdf_guard.scan(pdf_samples.pdf_with_object_in_stream_data(MARKER)) == pdf_guard.SCRIPT
    assert pdf_guard.scan(pdf_samples.pdf_with_object_stream()) == pdf_guard.OK
    # Тип действия: сжатый объект находит разбор, действие в данных потока (разбор
    # его не видит) — проход по байтам.
    in_data = pdf_samples.pdf(more=[b'<< /Length %d >>\nstream\n' % len(ACTION) + ACTION + b'\nendstream'])
    reader = pypdf.PdfReader(io.BytesIO(in_data), strict=False)
    assert '/ICoreAction' not in {action for _keys, action in pdf_guard.dictionaries(reader)}
    assert pdf_guard.scan(in_data) == pdf_guard.OK
    monkeypatch.setattr(pdf_guard, 'ACTIVE_ACTIONS', pdf_guard.ACTIVE_ACTIONS | {'/ICoreAction'})
    assert pdf_guard.scan(pdf_samples.pdf_with_object_stream(ACTION)) == pdf_guard.SCRIPT
    assert pdf_guard.scan(in_data) == pdf_guard.SCRIPT


# ── Вердикт из отдельного процесса ──────────────────────────────────────────

def test_ordinary_pdfs_pass():
    for data in (pdf_samples.pdf(), pdf_samples.pdf_with_object_stream(), _written(),
                 pdf_samples.pdf_of_size(10 * 1024 * 1024)):
        assert pdf_guard.verdict(data) == pdf_guard.OK


def test_password_and_broken_files():
    # Пароль только владельца (печать, копирование) — файл открывается и проверяется.
    assert pdf_guard.verdict(_written(user_password='')) == pdf_guard.OK
    assert pdf_guard.verdict(_written(user_password='секрет')) == pdf_guard.PASSWORD
    assert pdf_guard.verdict(b'%PDF-1.4\n' + b'0' * 200) == pdf_guard.BROKEN


def test_same_file_is_checked_once(monkeypatch):
    calls = []
    real = subprocess.run
    monkeypatch.setattr(pdf_guard.subprocess, 'run', lambda *a, **k: calls.append(a) or real(*a, **k))
    data = pdf_samples.pdf()
    assert pdf_guard.verdict(data) == pdf_guard.verdict(data) == pdf_guard.OK
    assert len(calls) == 1


def test_slow_check_counts_as_too_heavy_and_is_not_remembered(monkeypatch):
    monkeypatch.setattr(pdf_guard, 'TIMEOUT_SECONDS', 0.001)
    assert pdf_guard.verdict(pdf_samples.pdf()) == pdf_guard.HEAVY
    assert not pdf_guard._CACHE


def test_one_check_at_a_time(monkeypatch):
    monkeypatch.setattr(pdf_guard, 'TIMEOUT_SECONDS', 0.05)
    assert pdf_guard._SLOT.acquire(timeout=1)
    try:
        assert pdf_guard.verdict(pdf_samples.pdf()) == pdf_guard.FAILED
    finally:
        pdf_guard._SLOT.release()


@pytest.mark.parametrize('returncode, stdout, stderr, expected', [
    (-24, b'', b'', pdf_guard.HEAVY),                   # SIGXCPU: кончилось процессорное время
    (-9, b'', b'', pdf_guard.HEAVY),
    (1, b'', b'Traceback ...\nMemoryError', pdf_guard.HEAVY),
    (1, b'', b"ModuleNotFoundError: No module named 'pypdf'", pdf_guard.FAILED),
    (0, b'', b'', pdf_guard.FAILED),
    (0, b'whatever', b'', pdf_guard.FAILED),
])
def test_what_a_failed_check_means(monkeypatch, caplog, returncode, stdout, stderr, expected):
    monkeypatch.setattr(pdf_guard.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr))
    assert pdf_guard.verdict(pdf_samples.pdf()) == expected
    assert not pdf_guard._CACHE
    assert ('проверка PDF упала' in caplog.text) is (expected == pdf_guard.FAILED)


def test_check_process_gets_no_portal_secrets(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:secret@example.com/db')
    monkeypatch.setenv('BOT_TOKEN', 'secret')
    env = pdf_guard._child_env()
    assert 'DATABASE_URL' not in env and 'BOT_TOKEN' not in env
    assert set(env) <= {'PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='потолок памяти процесса — только на Linux')
def test_check_process_limits_its_memory_and_cpu():
    code = ('import importlib.util, resource\n'
            'spec = importlib.util.spec_from_file_location("guard", %r)\n'
            'guard = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(guard)\n'
            'guard._limit_resources()\n'
            'print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0])\n'
            % pdf_guard._SCRIPT_PATH)
    done = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, check=True, timeout=30)
    assert done.stdout.split() == [str(pdf_guard.MEMORY_BYTES).encode(), str(pdf_guard.CPU_SECONDS).encode()]
