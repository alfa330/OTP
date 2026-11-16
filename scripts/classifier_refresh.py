"""Пересобрать файл «Классификатора авто» с базы знаний Яндекс Про.

    PYTHONPATH=. venv/bin/python scripts/classifier_refresh.py [--check]

Качает страницы auto-list всех городов Казахстана тем же кодом, что ночная
сверка (wiki/classifier_store.build_now), и пишет wiki/data/classifier-data.json —
справочник, который читается, пока в базе нет слепка (свежая база, тесты,
первый запуск после выкладки). На проде справочник обновляет ночная сверка
сама; файл нужен, чтобы и без неё данные были не старее коммита.

--check — только собрать и сравнить с файлом, ничего не записывая.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki import classifier_source as source  # noqa: E402
from wiki import classifier_store as store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='не записывать файл')
    args = parser.parse_args()

    data, warnings, failed = store.build_now()
    if failed:
        for code, reason in sorted(failed.items()):
            print('НЕ ОТКРЫЛАСЬ %s: %s' % (code, reason))
        print('Файл не записан: справочник неполный.')
        return 1
    for line in warnings:
        print('Замечание: %s' % line)
    print('Городов %d, машин %d, таблиц %d, строк %d.' % (
        len(data['cities']), len(data['cars']), len(data['lists']), store.rows_total(data)))
    try:
        old = store.read_bundled()
    except (OSError, ValueError):
        old = None
    same = old is not None and source.fingerprint(old) == source.fingerprint(data)
    print('С файлом: %s.' % ('совпадает' if same else 'отличается'))
    if args.check or same:
        return 0
    store.write_bundled(data)
    print('Записан %s' % store.DATA_PATH)
    return 0


if __name__ == '__main__':
    sys.exit(main())
