"""Логи не должны пропускать секреты.

Поставлено после инцидента 22.08.2026: httpx пишет в лог полный URL на уровне
INFO, ключ Gemini передавался query-параметром — и лежал в логах Render
открытым текстом с 20.08, пока сканер GitHub не отдал его Google и тот ключ не
отозвал. Сами вызовы переведены на заголовки, но одной правкой мест такую
утечку не закрыть: завтра появится четвёртое.

Проверяем НАСТОЯЩИЙ модуль log_secrets — тот, что стоит на проде, а не копию.

ВАЖНО ПРО ОБРАЗЦЫ. Ключи здесь заведомо выдуманные. Однажды в этот файл
подставили боевое значение GEMINI_API_KEY «как пример утечки» — и коммит с ним
ушёл в публичный репозиторий. Образец секрета берётся из головы, никогда из
окружения; за этим отдельно следит tests/test_no_secrets_in_repo.py.
"""

import io
import logging
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import log_secrets  # noqa: E402

FAKE_GOOGLE = 'AIzaSyFAKEKEY_FOR_TESTS_000000000000000'
FAKE_BOT = '1234567890:AAFAKE-bot-token-for-tests-000000000'


class ScrubTextTest(unittest.TestCase):
    """Сама чистка текста, без логгера."""

    def test_key_in_query_string(self):
        out = log_secrets.scrub(
            'HTTP Request: POST https://generativelanguage.googleapis.com'
            f'/v1alpha/auth_tokens?key={FAKE_GOOGLE} "HTTP/1.1 200 OK"')
        self.assertNotIn('AIzaSyFAKE', out)
        # Адрес обязан остаться читаемым: иначе чистка лечит утечку ценой логов.
        self.assertIn('auth_tokens?key=', out)
        self.assertIn('200 OK', out)

    def test_bare_google_key_anywhere(self):
        self.assertNotIn('AIzaSyFAKE', log_secrets.scrub(f'ключ {FAKE_GOOGLE} в тексте'))

    def test_anthropic_and_groq(self):
        out = log_secrets.scrub('x-api-key sk-ant-api03-abcdefghijklmnop и gsk_' + 'q' * 25)
        self.assertNotIn('sk-ant-api03-abcdefghij', out)
        self.assertNotIn('gsk_qqqqq', out)

    def test_telegram_token_in_url_path(self):
        """Токен бота лежит в ПУТИ, а не в параметре — прежние правила его не брали."""
        out = log_secrets.scrub(
            'HTTPSConnectionPool(host=\'api.telegram.org\', port=443): Max retries '
            f'exceeded with url: /bot{FAKE_BOT}/sendMessage')
        self.assertNotIn('AAFAKE-bot-token', out)
        self.assertNotIn('1234567890:', out)
        self.assertIn('/bot', out)          # видно, что это был вызов Bot API
        self.assertIn('sendMessage', out)   # и какой именно метод

    def test_password_in_connection_string(self):
        out = log_secrets.scrub('could not connect to postgres://otp_user:FAKEpassword@db.host:5432/otp')
        self.assertNotIn('FAKEpassword', out)
        self.assertIn('otp_user', out)
        self.assertIn('db.host:5432', out)

    def test_authorization_header(self):
        out = log_secrets.scrub("headers={'Authorization': 'Bearer abcdef1234567890abcdef'}")
        self.assertNotIn('abcdef1234567890', out)

    def test_jwt(self):
        jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop'
        self.assertNotIn('eyJzdWIiOiIxMjM0', log_secrets.scrub(f'token={jwt} истёк'))

    def test_plain_lines_untouched(self):
        line = 'Раздел «Тренажёр»: Blueprint подключён на /api/trainer'
        self.assertEqual(line, log_secrets.scrub(line))

    def test_exact_env_values(self):
        """Главное правило: точные значения переменных окружения.

        Только оно покрывает форматы, которых мы не предугадали, — у проекта
        два десятка внешних сервисов.
        """
        try:
            log_secrets.refresh_env_secrets({'SOME_VENDOR_TOKEN': 'zZq7-vendor-value-9911',
                                             'PUBLIC_REGION': 'europe-west1'})
            out = log_secrets.scrub('ответ сервиса: zZq7-vendor-value-9911, регион europe-west1')
            self.assertNotIn('zZq7-vendor-value', out)
            self.assertIn('europe-west1', out)   # не-секретные переменные не трогаем
        finally:
            log_secrets.refresh_env_secrets(os.environ)


class FormatterTest(unittest.TestCase):
    """Чистка стоит ФОРМАТТЕРОМ, поэтому обязана покрывать и traceback."""

    def setUp(self):
        self.buffer = io.StringIO()
        handler = logging.StreamHandler(self.buffer)
        handler.setFormatter(logging.Formatter('%(levelname)s:%(message)s'))
        self.log = logging.getLogger('scrubber-test')
        self.log.handlers = [handler]
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        log_secrets.install(self.log, quiet_http_clients=False)

    def test_message(self):
        self.log.info('запрос %s', f'https://x/y?key={FAKE_GOOGLE}')
        self.assertNotIn('AIzaSyFAKE', self.buffer.getvalue())

    def test_traceback_is_scrubbed_too(self):
        """Прежний фильтр правил только message — traceback шёл мимо него."""
        try:
            raise RuntimeError(f'Max retries exceeded with url: /bot{FAKE_BOT}/sendMessage')
        except RuntimeError:
            self.log.exception('не отправилось')
        out = self.buffer.getvalue()
        self.assertIn('Traceback', out)
        self.assertIn('не отправилось', out)
        self.assertNotIn('AAFAKE-bot-token', out)

    def test_existing_format_is_preserved(self):
        self.log.warning('обычная строка')
        self.assertIn('WARNING:обычная строка', self.buffer.getvalue())

    def test_install_is_idempotent(self):
        before = self.log.handlers[0].formatter
        log_secrets.install(self.log, quiet_http_clients=False)
        self.assertIs(before, self.log.handlers[0].formatter)


class CallSiteTest(unittest.TestCase):
    """Чистка — страховка. Ключ не должен попадать в URL в принципе."""

    def test_no_module_puts_a_key_into_a_url(self):
        import re

        offenders = []
        # Исключения только служебные. Прототип voice_trainer/server.py раньше
        # стоял в списке по имени файла — и молча держал ключ в четырёх адресах.
        for path in ROOT.rglob('*.py'):
            text = str(path)
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
                         'ключ передаётся в URL — клиент запишет его в лог:\n' +
                         '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
