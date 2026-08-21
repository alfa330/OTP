"""Фильтр логов не должен пропускать секреты.

Поставлен после инцидента 22.08.2026: httpx пишет в лог полный URL на уровне
INFO, ключ Gemini передавался query-параметром — и лежал в логах Render
открытым текстом с 20.08. Сами вызовы переведены на заголовок, но фильтр нужен
как последний рубеж: одной правкой мест такую утечку не закрыть, завтра
появится четвёртое.

Класс берём из монолита, а не копию: копия разошлась бы с рабочим кодом молча,
а проверять надо именно то, что стоит на проде.
"""

import io
import logging
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')


def _load_scrubber():
    """Достаёт класс фильтра из монолита без импорта самого монолита.

    Импортировать bot_schedule2 в тесте нельзя: он поднимает планировщик, бота и
    соединения с базой. Вырезаем объявление класса и исполняем его отдельно.
    """
    start = SOURCE.index('class _SecretScrubber(logging.Filter):')
    end = SOURCE.index('# Фильтры вешаются на ОБРАБОТЧИКИ', start)
    namespace = {'logging': logging, 're': re}
    exec(compile(SOURCE[start:end], 'scrubber', 'exec'), namespace)  # noqa: S102
    return namespace['_SecretScrubber']


class ScrubberTest(unittest.TestCase):
    def setUp(self):
        self.buffer = io.StringIO()
        handler = logging.StreamHandler(self.buffer)
        handler.addFilter(_load_scrubber()())
        self.log = logging.getLogger('scrubber-test')
        self.log.handlers = [handler]
        self.log.setLevel(logging.INFO)
        self.log.propagate = False

    def emit(self, message):
        self.log.info(message)
        return self.buffer.getvalue()

    def test_key_in_query_string_is_removed(self):
        out = self.emit('HTTP Request: POST https://generativelanguage.googleapis.com'
                        '/v1alpha/auth_tokens?key=AIzaSyFAKEKEY_FOR_TESTS_000000000000000'
                        ' "HTTP/1.1 200 OK"')
        self.assertNotIn('AIzaSyFAKE', out)
        self.assertIn('<СКРЫТО>', out)
        # Адрес обязан остаться читаемым: иначе фильтр лечит утечку ценой логов.
        self.assertIn('auth_tokens?key=', out)
        self.assertIn('200 OK', out)

    def test_bare_google_key_anywhere_in_text(self):
        out = self.emit('ключ AIzaSyFAKEKEY_FOR_TESTS_000000000000000 в тексте')
        self.assertNotIn('AIzaSyFAKE', out)

    def test_anthropic_key_is_removed(self):
        out = self.emit('x-api-key sk-ant-api03-abcdefghijklmnop свалился в лог')
        self.assertNotIn('sk-ant-api03-abcdefghij', out)

    def test_token_and_access_token_params(self):
        out = self.emit('GET /api/x?access_token=abcdef123456&token=zzzz9999&page=2')
        self.assertNotIn('abcdef123456', out)
        self.assertNotIn('zzzz9999', out)
        self.assertIn('page=2', out)      # обычные параметры не трогаем

    def test_plain_lines_are_untouched(self):
        out = self.emit('Раздел «Тренажёр»: Blueprint подключён на /api/trainer')
        self.assertIn('Blueprint подключён на /api/trainer', out)
        self.assertNotIn('<СКРЫТО>', out)

    def test_formatted_records_are_scrubbed_too(self):
        """Секрет часто приходит аргументом, а не готовой строкой."""
        self.log.info('запрос %s', 'https://x/y?key=AIzaSyFAKEKEY_FOR_TESTS_000000000000000')
        out = self.buffer.getvalue()
        self.assertNotIn('AIzaSyFAKE', out)


class CallSiteTest(unittest.TestCase):
    """Сам фильтр — страховка. Ключ не должен попадать в URL в принципе."""

    def test_no_module_puts_the_gemini_key_into_a_url(self):
        offenders = []
        for path in ROOT.rglob('*.py'):
            text = str(path)
            # Исключения только служебные. Прототип voice_trainer/server.py раньше
            # стоял в списке — и молча держал ключ в четырёх адресах; вычищен
            # 22.08.2026, и обратно в исключения не возвращается.
            if any(skip in text for skip in ('worktrees', 'node_modules', '__pycache__',
                                             'tests')):
                continue
            source = path.read_text(encoding='utf-8-sig', errors='ignore')
            for line in source.splitlines():
                if line.strip().startswith('#') or '"""' in line:
                    continue
                if re.search(r"\?key=\{|params=\{'key':|params=\{\"key\":", line):
                    offenders.append(f'{path.relative_to(ROOT)}: {line.strip()[:90]}')
        self.assertEqual([], offenders,
                         'ключ передаётся в URL — httpx запишет его в лог:\n' +
                         '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
