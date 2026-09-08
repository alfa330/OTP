# -*- coding: utf-8 -*-
"""Барьер против утечки СЕКРЕТОВ в репозиторий.

Репозиторий публичный. 22.08.2026 в него уехал боевой GEMINI_API_KEY — причём
тем самым коммитом, который чинил утечку ключа в логи: значение подставили в
тест «как пример». Через полчаса сканер GitHub отдал ключ Google, и тот его
принудительно отозвал.

Проверяем КАЖДЫЙ файл под контролем Git двумя способами:
  * по форме — известные форматы ключей (Google, Anthropic, Groq, Render,
    Telegram, JWT, PEM, пароль в строке подключения);
  * по значению — прямым сравнением с .env.codex.local, если он есть рядом.
    Это единственное, что ловит формат, который мы не предугадали. В CI файла
    нет, и эта проверка молча пропускается — она для машины разработчика,
    то есть ровно для того места, где секрет и попадает в коммит.

Значения секретов тест НЕ печатает никогда: только файл, строка и класс. Иначе
сам отчёт о падении станет новой утечкой.

Рядом живёт tests/test_no_personal_data_in_repo.py — он про ФИО, телефоны и
почты; здесь только доступы.
"""

import io
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Заведомо выдуманные значения. Без этого списка тест красит собственные
# образцы в тестах, его сразу начинают выключать, и барьера не остаётся.
_SYNTHETIC = ('FAKE', 'EXAMPLE', 'ПРИМЕР', 'PLACEHOLDER', 'XXXX', 'ЗДЕСЬ',
              'YOUR_', 'DUMMY', 'TEST_TOKEN', '000000', 'abcdef', 'user:pass@',
              'ЛОГИН:ПАРОЛЬ')

_PATTERNS = (
    ('ключ Google (AIza…)', re.compile(r'\bAIza[0-9A-Za-z_\-]{20,}')),
    ('ключ Anthropic (sk-ant…)', re.compile(r'\bsk-ant-[A-Za-z0-9\-_]{20,}')),
    ('ключ OpenAI (sk-proj…)', re.compile(r'\bsk-proj-[A-Za-z0-9\-_]{20,}')),
    ('ключ Groq (gsk_…)', re.compile(r'\bgsk_[A-Za-z0-9]{40,}')),
    ('ключ Render (rnd_…)', re.compile(r'\brnd_[A-Za-z0-9]{25,}')),
    ('токен Telegram-бота', re.compile(r'\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}')),
    ('закрытый ключ PEM', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('JWT', re.compile(r'\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}')),
    ('пароль в строке подключения',
     re.compile(r'\b[a-z][a-z0-9+.\-]*://[^\s:/@\'"]{2,}:[^\s@/\'"]{6,}@')),
)

_SKIP_DIRS = ("node_modules/", "dist/", "build/", ".venv/")
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
             ".ttf", ".eot", ".pdf", ".mp3", ".wav", ".zip", ".xlsx", ".map",
             ".lock")
# Замки пакетов держат хеши целостности, а не доступы; они длинные и шумные.
_SKIP_FILES = ("package-lock.json",)

# Не всякая переменная окружения — секрет: адреса, регионы и id доступа не дают.
_SECRET_NAME_RE = re.compile(
    r'KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|DATABASE_URL|DSN|AUTH', re.I)
_MIN_SECRET_LEN = 12

# Логин — половина учётки, и его прячут хуже, чем пароль: он попадает в код как
# «реалистичный пример». Так BINOTEL_OPERATOR_SIP_LOGIN (8 символов) прожил в
# трёх файлах с 02.09.2026, а 08.09.2026 в публичный коммит уехал ещё и пароль
# от той же линии. Под _SECRET_NAME_RE логин не подпадал, под порог в 12
# символов — тоже.
_LOGIN_NAME_RE = re.compile(r'LOGIN|USERNAME|\bUSER\b', re.I)
_MIN_LOGIN_LEN = 8


def _is_secret_env(name, value):
    """Считать ли значение из .env секретом, который нельзя встречать в файлах."""
    if len(value) >= _MIN_SECRET_LEN and _SECRET_NAME_RE.search(name):
        return True
    # Логины сверяем с порогом ниже, но требуем И букву, И цифру: иначе сюда
    # попадут «postgres», «admin» и прочие словарные значения, которые встретятся
    # в репозитории тысячу раз и утопят проверку в шуме.
    if (_LOGIN_NAME_RE.search(name)
            and len(value) >= _MIN_LOGIN_LEN
            and re.search(r'[A-Za-z]', value)
            and re.search(r'\d', value)):
        return True
    return False


def _tracked_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT)
    for name in out.decode("utf-8").splitlines():
        if name.startswith(_SKIP_DIRS) or name.lower().endswith(_SKIP_EXT):
            continue
        if os.path.basename(name) in _SKIP_FILES:
            continue
        yield name


def _strip_tags(chunk):
    """Текст ячейки без разметки и лишних пробелов."""
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', chunk)).strip()


def _read(name):
    # Читаем БАЙТАМИ и декодируем с заменой. Раньше здесь стоял
    # `except UnicodeDecodeError: return ""`, и любой файл не в UTF-8 (дамп
    # кабинета в cp1251, выгрузка из Excel) молча выпадал из проверки целиком —
    # то есть страж отворачивался ровно от того файла, который подозрительнее
    # остальных.
    try:
        with io.open(os.path.join(ROOT, name), "rb") as handle:
            data = handle.read()
    except (IOError, OSError):
        return ""
    if b"\x00" in data[:8192]:
        return ""  # двоичный файл: сканировать нечего
    return data.decode("utf-8", errors="replace")


def _looks_synthetic(fragment):
    upper = fragment.upper()
    if any(mark.upper() in upper for mark in _SYNTHETIC):
        return True
    # Заглушки, принятые в фикстурах кабинета: sip-fixture-903, Fixture-Pass-903,
    # line903@example.kz. Держим их здесь, а не в _SYNTHETIC, чтобы общий список
    # оставался про «слова-маркеры», а не про формат конкретных фикстур.
    return bool(re.match(r'(?:sip-fixture-|fixture-pass-|line\d+@)', fragment, re.I))


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


# Заголовки колонок и ключи, за которыми в дампах кабинетов лежат доступы.
# Прежний словарь состоял из четырёх слов (логин/пароль/login/password), и
# колонка «Ключ», «Секрет», «Token» или «PIN» прошла бы мимо стража.
_CREDENTIAL_LABEL_RE = re.compile(
    r'логин|парол|ключ|секрет|учет|учёт|login|user(name)?|pass(word|wd)?|pwd|'
    r'secret|token|api[_-]?key|\bpin\b|credential|hash', re.I)

# Что ИМЕННО считается заглушкой в проверке «по месту». Здесь нужен строгий
# шаблон, а не _looks_synthetic: тот признаёт выдуманным всё, что СОДЕРЖИТ
# маркер, и потому пропустил бы боевой пароль вида «abcdefQ7x91».
_PLACEHOLDER_RE = re.compile(
    r'(?:sip-fixture-\d+|fixture-pass-\d+|fixture0+\d+|line\d+@example\.[a-z]+|'
    r'ext-hash-\d+|-|—|\*+|\.\.\.)\Z', re.I)


def _is_placeholder(value):
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _json_credential_values(node, path="$"):
    """Пары (путь, значение) под ключами, которые пахнут доступом.

    Дампы кабинетов приезжают не только в html: employees_day.json — такой же
    дамп, и до 08.09.2026 страж «по месту» его не смотрел вовсе.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (path, key)
            if isinstance(value, (dict, list)):
                for item in _json_credential_values(value, child):
                    yield item
            elif isinstance(value, str) and _CREDENTIAL_LABEL_RE.search(str(key)):
                yield child, value
    elif isinstance(node, list):
        for index, value in enumerate(node):
            for item in _json_credential_values(value, "%s[%d]" % (path, index)):
                yield item


class NoSecretsInRepoTests(unittest.TestCase):
    def test_no_known_secret_shapes(self):
        offenders = []
        for name in _tracked_files():
            text = _read(name)
            if not text:
                continue
            for label, pattern in _PATTERNS:
                for match in pattern.finditer(text):
                    if _looks_synthetic(match.group(0)):
                        continue
                    offenders.append("%s:%d — %s" % (name, _line_of(text, match.start()), label))
        self.assertEqual([], offenders,
                         "Секрет в публичном репозитории (значения намеренно не "
                         "печатаются):\n" + "\n".join(offenders))

    def test_credential_columns_in_fixtures_are_placeholders(self):
        """Учётки в сохранённых страницах кабинета — только выдуманные.

        08.09.2026 в репозиторий уехал `tests/fixtures/tez_wallboard/endpoints.html`
        — страница кабинета Binotel с таблицей SIP-аккаунтов: 19 живых логинов и
        19 живых паролей. Проверка по .env поймала ровно ОДИН из них (наш
        собственный), потому что остальные 18 в .env не лежат и сверять их не с
        чем. Формат у них тоже свой, под известные ключи не подходит.

        Поэтому здесь другой признак: не значение, а МЕСТО. Если в таблице есть
        колонка «Логин» или «Пароль», её ячейки обязаны быть заглушками.
        """
        bad = []
        checked = 0
        for path in _tracked_files():
            if not path.startswith('tests/fixtures/'):
                continue

            if path.endswith('.html'):
                html = _read(path)
                # Таблицы без вложенности: «.*?» дотягивалась до чужого </table>
                # и склеивала две таблицы в одну, сбивая нумерацию колонок.
                for table in re.findall(r'<table\b(?:(?!<table).)*?</table>', html, re.S | re.I):
                    # Заголовок бывает и в <th>, и в первой строке <td>.
                    headers = [_strip_tags(h) for h in
                               re.findall(r'<th[^>]*>(.*?)</th>', table, re.S | re.I)]
                    if not headers:
                        first = re.search(r'<tr[^>]*>(.*?)</tr>', table, re.S | re.I)
                        if first:
                            headers = [_strip_tags(c) for c in
                                       re.findall(r'<td[^>]*>(.*?)</td>', first.group(1), re.S | re.I)]
                    guarded = [i for i, h in enumerate(headers)
                               if _CREDENTIAL_LABEL_RE.search(h)]
                    if not guarded:
                        continue
                    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S | re.I):
                        cells = [_strip_tags(c) for c in
                                 re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)]
                        for i in guarded:
                            if i >= len(cells) or not cells[i]:
                                continue
                            checked += 1
                            if _is_placeholder(cells[i]):
                                continue
                            bad.append('%s — колонка «%s», строка %s' % (
                                path, headers[i].strip(), cells[0] if cells else '?'))

            elif path.endswith('.json'):
                try:
                    data = json.loads(_read(path) or 'null')
                except ValueError:
                    continue
                for where, value in _json_credential_values(data):
                    if not value:
                        continue
                    checked += 1
                    if _is_placeholder(value):
                        continue
                    bad.append('%s — поле %s' % (path, where))

        self.assertEqual(
            [], sorted(set(bad)),
            'Живая учётка в фикстуре. Замените значение на заглушку '
            '(sip-fixture-<номер> / Fixture-Pass-<номер>) и СМЕНИТЕ доступ '
            'в кабинете: репозиторий публичный.')
        # Страж, который ничего не проверил, неотличим от зелёного. 08.09.2026
        # он и был таким: разбор не находил колонок, и тест радостно проходил.
        self.assertTrue(
            checked,
            'проверка не нашла НИ ОДНОЙ ячейки с логином или паролем в '
            'tests/fixtures/ — почти наверняка сломался разбор, а не исчезли '
            'дампы кабинетов')

    def test_no_env_values_in_tracked_files(self):
        """Прямое сравнение с .env.codex.local — ловит форматы, которых мы не знали."""
        env_path = os.path.join(ROOT, ".env.codex.local")
        if not os.path.exists(env_path):
            self.skipTest(".env.codex.local рядом нет — проверка только для машины разработчика")

        secrets = {}
        with io.open(env_path, encoding="utf-8-sig") as handle:
            for line in handle:
                match = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
                if not match:
                    continue
                name, value = match.group(1), match.group(2).strip().strip('"').strip("'")
                if _is_secret_env(name, value):
                    secrets[name] = value

        self.assertTrue(secrets, "в .env.codex.local не нашлось ни одного секрета — "
                                 "проверка бессмысленна, посмотрите разбор файла")
        offenders = []
        for name in _tracked_files():
            text = _read(name)
            if not text:
                continue
            for var, value in secrets.items():
                index = text.find(value)
                if index != -1:
                    offenders.append("%s:%d — значение %s" % (name, _line_of(text, index), var))
        self.assertEqual([], offenders,
                         "Боевое значение из .env.codex.local лежит в файле под "
                         "контролем Git:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
