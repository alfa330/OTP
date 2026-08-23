# -*- coding: utf-8 -*-
"""Барьер против утечки персональных данных в репозиторий.

Репозиторий публичный. Один раз в него уже уехала фикстура с 361 номером
клиентов и ФИО 25 сотрудников (tests/fixtures_amo_sheet_2026_08_05.csv) —
чтобы это не повторилось, тест проверяет КАЖДЫЙ файл под контролем Git.

Ловим два класса, на которых обжигались:
  * выгрузка-«дамп» — много разных мобильных номеров в одном файле;
  * личные почты сотрудников (gmail, mail.ru и прочие некорпоративные).

Одиночный номер-пример в юнит-тесте не ловим намеренно: такой тест быстро
начинают выключать. Нужен образец — берите заведомо выдуманный номер.
"""

import io
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Больше этого числа РАЗНЫХ мобильных в одном файле — это уже выгрузка, а не пример.
DUMP_THRESHOLD = 10

_PHONE_RE = re.compile(r"(?<![0-9])(?:\+?7|8)(7[0-9]{9})(?![0-9])")

# Учебные номера тренажёра: 7 7XX 555 XX XX и 7 7XX 000 00 XX. Такие стоят в
# слепках дел (voice_trainer/cases/) и в учебных данных экранов — их там по два
# десятка на файл, потому что список исполнителей без соседей и однофамильцев не
# список, а поиск по номеру звонящего без них не проверить.
#
# Блоков два, потому что дорожки писались параллельно и договорились задним
# числом: слепки взяли «555», экраны — «000». Обе конвенции очевидно
# синтетические, и обе здесь признаны.
#
# Почему это не дыра в страже. Исключение узкое: под него подходит только номер
# со сплошным блоком «555» или «000» в середине, и такие номера считаются
# ОТДЕЛЬНО — порог в десять разных номеров для всех остальных остаётся прежним.
# Чтобы настоящая выгрузка проскочила, все её номера до единого должны оказаться
# из учебного блока.
_LEARNING_RE = re.compile(r"^7[0-9]{2}(?:555|000)[0-9]{4}$")
_PERSONAL_MAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@(?:gmail|mail\.ru|yandex\.ru|bk\.ru|inbox\.ru|list\.ru|icloud)\b",
    re.IGNORECASE)

# Собственные линии компании лежат в bot_schedule2.py как рабочий конфиг —
# это не персональные данные, а телефонная схема, по которой считается билинг.
ALLOWED_DUMPS = {"bot_schedule2.py"}

_SKIP_DIRS = ("node_modules/", "dist/", "build/", ".venv/")
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
             ".ttf", ".eot", ".pdf", ".mp3", ".wav", ".zip", ".xlsx", ".map")


def _tracked_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT)
    for name in out.decode("utf-8").splitlines():
        if name.startswith(_SKIP_DIRS) or name.lower().endswith(_SKIP_EXT):
            continue
        yield name


def _read(name):
    try:
        with io.open(os.path.join(ROOT, name), encoding="utf-8") as handle:
            return handle.read()
    except (UnicodeDecodeError, IOError, OSError):
        return ""


class NoPersonalDataTests(unittest.TestCase):
    def test_no_phone_number_dumps(self):
        offenders = []
        for name in _tracked_files():
            if name in ALLOWED_DUMPS:
                continue
            distinct = set(_PHONE_RE.findall(_read(name)))
            real = {n for n in distinct if not _LEARNING_RE.match(n)}
            if len(real) > DUMP_THRESHOLD:
                offenders.append("%s — %d разных номеров" % (name, len(real)))
        self.assertEqual([], offenders,
                         "Выгрузка с номерами в публичном репозитории:\n" +
                         "\n".join(offenders))

    def test_no_personal_email_addresses(self):
        offenders = []
        for name in _tracked_files():
            for match in set(_PERSONAL_MAIL_RE.findall(_read(name))):
                offenders.append("%s — %s" % (name, match))
        self.assertEqual([], offenders,
                         "Личные почты в публичном репозитории:\n" +
                         "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
