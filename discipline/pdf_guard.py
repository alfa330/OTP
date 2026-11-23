# -*- coding: utf-8 -*-
"""PDF без того, что просмотрщик выполнит сам, когда файл откроют.

Acrobat и другие просмотрщики исполняют JavaScript из PDF — при открытии, по
щелчку, в формах XFA — и по действию Launch запускают программу на компьютере.
Антивируса нет (решение владельца 23.10.2026), поэтому такой PDF не
принимается вовсе, как DOCX с макросами (владелец, 23.11.2026).

Ищем не по байтам: объекты PDF бывают сжаты внутри потоков (ObjStm) и
зашифрованы, а имена записаны кодами (/J#61vaScript — тот же /JavaScript).
Файл разбирает pypdf и обходит каждый объект из таблицы ссылок — так же их
находит просмотрщик. Сверху — проход по сырым байтам за «/S /JavaScript» и
«/S /Launch»: действие, которого pypdf не увидел, а просмотрщик найдёт.

Разбор — в отдельном процессе с потолком памяти и времени: подделанный PDF в
несколько мегабайт при разборе раздувается в гигабайты, а портал работает
одним экземпляром. Что не удалось разобрать или проверить в этих пределах, не
принимается.
"""

import hashlib
import io
import logging
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict

OK = 'ok'
SCRIPT = 'script'          # скрипт или запуск программы
PASSWORD = 'password'      # без пароля не открыть — и не проверить
BROKEN = 'broken'          # pypdf не разобрал
HEAVY = 'heavy'            # не уложился в потолок памяти или времени
FAILED = 'failed'          # проверка сама не запустилась или не дождалась очереди
VERDICTS = frozenset({OK, SCRIPT, PASSWORD, BROKEN, HEAVY})

# Ключи словаря со скриптом: /JS — код действия, /JavaScript — скрипты
# документа в /Names, /XFA — форма XFA (скрипты внутри её XML),
# /OnInstantiate — скрипт 3D-модели.
SCRIPT_KEYS = frozenset({'/JS', '/JavaScript', '/XFA', '/OnInstantiate'})
# Тип действия (/S): JavaScript — скрипт, Launch — запуск программы или файла.
ACTIVE_ACTIONS = frozenset({'/JavaScript', '/Launch'})

# Обычный PDF проверяется за 0,1–0,2 с; 262 страницы (120 тыс. объектов) — 3 с
# и 105 МБ на Mac. Пределы — с запасом на медленный процессор сервера.
TIMEOUT_SECONDS = 30
CPU_SECONDS = 25
MEMORY_BYTES = 512 * 1024 * 1024
# Один разбор за раз: потолок памяти — на процесс, и несколько PDF подряд не
# должны вместе занять память портала.
_SLOT = threading.BoundedSemaphore(1)
_CACHE = OrderedDict()     # sha256 → вердикт: тот же файл второй раз не разбираем
_CACHE_SIZE = 256
_CACHE_LOCK = threading.Lock()
_SCRIPT_PATH = os.path.abspath(__file__)


def is_active(keys, action):
    """Словарь PDF со скриптом или запуском: его ключи и тип действия /S."""
    return any(str(key) in SCRIPT_KEYS for key in keys) or str(action or '') in ACTIVE_ACTIONS


# «/S», пробелы или комментарии, имя до разделителя. Просто «/JavaScript» не
# годится: так выглядит и ссылка «…/Web/JavaScript» в обычном PDF.
_RAW_ACTION = re.compile(rb'/(?:S|#53)(?:[\x00\t\n\x0c\r ]|%[^\r\n]*[\r\n])*'
                         rb'/((?:[A-Za-z]|#[0-9A-Fa-f]{2}){1,40})(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|$)')
_HEX = re.compile(rb'#([0-9A-Fa-f]{2})')


def raw_actions(data, wanted=None):
    """Типы действий из wanted (по умолчанию ACTIVE_ACTIONS), записанные в
    байтах файла открытым текстом: «/S /JavaScript», коды #xx раскрыты."""
    wanted = ACTIVE_ACTIONS if wanted is None else wanted
    found = set()
    for match in _RAW_ACTION.finditer(data):
        name = '/' + _HEX.sub(lambda code: bytes((int(code.group(1), 16),)), match.group(1)).decode('latin-1')
        if name in wanted:
            found.add(name)
    return found


def dictionaries(reader):
    """(ключи, /S) каждого словаря во всех объектах файла: объекты по таблице
    ссылок, включая сжатые в ObjStm, вложенные словари и массивы — без перехода
    по ссылкам (объект, на который ссылаются, и так есть в таблице).

    Разобранное pypdf держит в кэше до конца: PDF на 262 страницы (120 тыс.
    объектов) занимал так 423 МБ. Проверенный объект из кэша убираем, сжатые —
    всем потоком сразу: поток разбирается целиком на первом же объекте."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    cache = getattr(reader, 'resolved_objects', {})

    def walk(root):
        stack = [root]
        while stack:
            item = stack.pop()
            if isinstance(item, DictionaryObject):
                action = dict.get(item, '/S')
                if isinstance(action, IndirectObject):
                    action = action.get_object()
                yield tuple(item.keys()), action
                stack.extend(dict.values(item))
            elif isinstance(item, ArrayObject):
                stack.extend(list.__iter__(item))

    yield from walk(reader.trailer)
    packed = reader.xref_objStm  # номер объекта → (поток, место в потоке)
    plain = {(num, generation) for generation, table in reader.xref.items() for num in table
             if not (generation == 0 and num in packed)}
    for num, generation in sorted(plain):
        yield from walk(reader.get_object(IndirectObject(num, generation, reader)))
        cache.pop((generation, num), None)
    streams = {}
    for num, (stream, _place) in packed.items():
        streams.setdefault(stream, []).append(num)
    for stream, nums in sorted(streams.items()):
        for num in nums:
            yield from walk(reader.get_object(IndirectObject(num, 0, reader)))
        for num in nums + [stream]:
            cache.pop((0, num), None)


def scan(data):
    """Вердикт по байтам PDF. Запускается в отдельном процессе (verdict)."""
    if raw_actions(data):
        return SCRIPT
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(''):
            return PASSWORD
        if any(is_active(keys, action) for keys, action in dictionaries(reader)):
            return SCRIPT
    except (MemoryError, RecursionError):
        return HEAVY
    except Exception:  # noqa: BLE001 — разбор чужого файла: любая ошибка = не разобран
        return BROKEN
    return OK


def verdict(data):
    """Вердикт PDF из отдельного процесса с потолком памяти и времени."""
    key = hashlib.sha256(data).hexdigest()
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    if not _SLOT.acquire(timeout=TIMEOUT_SECONDS):
        return FAILED
    try:
        done = subprocess.run([sys.executable, '-I', _SCRIPT_PATH], input=data, capture_output=True,
                              timeout=TIMEOUT_SECONDS, env=_child_env(), check=False)
    except subprocess.TimeoutExpired:
        return HEAVY
    except OSError:
        logging.exception('Дисциплина: проверка PDF не запустилась')
        return FAILED
    finally:
        _SLOT.release()
    answer = done.stdout.decode('ascii', 'replace').strip()
    if done.returncode == 0 and answer in VERDICTS:
        with _CACHE_LOCK:
            _CACHE[key] = answer
            while len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
        return answer
    # Убит за процессорное время (SIGXCPU/SIGKILL) или не получил память.
    if done.returncode < 0 or b'MemoryError' in done.stderr:
        return HEAVY
    logging.error('Дисциплина: проверка PDF упала (код %s): %s', done.returncode,
                  done.stderr[-800:].decode('utf-8', 'replace'))
    return FAILED


def _child_env():
    """Процессу проверки не нужны ключи и пароли портала из окружения."""
    keep = ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'LANG', 'LC_ALL')
    return {name: os.environ[name] for name in keep if name in os.environ}


def _limit_resources():
    try:
        import resource
    except ImportError:  # Windows: потолков нет, остаётся таймаут
        return
    for limit, value in ((resource.RLIMIT_AS, MEMORY_BYTES), (resource.RLIMIT_CPU, CPU_SECONDS)):
        try:
            _soft, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(limit, (value, hard))
        except (ValueError, OSError):  # macOS не даёт ставить RLIMIT_AS
            pass


def _main():
    _limit_resources()
    # Картинки проверке не нужны, а Pillow тянет numpy: под потолком памяти на
    # Linux тот поднимает потоки OpenBLAS и не стартует. pypdf живёт и без них.
    sys.modules.update({'PIL': None, 'numpy': None})
    logging.disable(logging.CRITICAL)  # предупреждения pypdf о кривых файлах никому не нужны
    data = sys.stdin.buffer.read()
    sys.stdout.write(scan(data))


if __name__ == '__main__':
    _main()
