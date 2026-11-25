"""Однократный вход аккаунта Google для зеркала «Термокоробов» (thermoboxes/sync.py).

Зачем. Таблицу «Кол-во термопакетов и термокоробов» её владелец открыл людям, а
управлять доступом разрешил только себе: редактор не может открыть её
сервисной учётке портала. Поэтому сервер читает лист от имени аккаунта, которому
таблица открыта: аккаунт один раз разрешает «Просмотр файлов на Google Диске»,
сервер хранит только токен. Пароль аккаунта серверу не нужен и в портал не
попадает. Адреса аккаунтов в репозиторий не пишутся (он публичный) — адрес
передаётся параметром --account.

Что нужно заранее — OAuth-клиент в Google Cloud Console, в облачном проекте
портала (тот же project_id, что у ключа GOOGLE_APPLICATION_CREDENTIALS_CONTENT):
  1. Google Auth Platform → «Брендинг»: название приложения (например «iCORE
     Термокороба»), почта поддержки.
  2. «Аудитория»: тип «Внешний» (External), затем «Опубликовать приложение»
     (In production). В статусе «Тестирование» Google гасит токен через 7 дней.
  3. «Клиенты» → «Создать клиента» → тип «Приложение для ПК» (Desktop app) →
     скачать JSON.

Запуск — на компьютере, где в браузере открыт этот аккаунт:

    python scripts/thermoboxes_google_auth.py --client-json ~/Downloads/client_secret_….json --account <адрес>

Скрипт откроет страницу входа Google: выбрать этот аккаунт и разрешить просмотр
файлов (предупреждение «Приложение не проверено» — «Дополнительные настройки» →
«Перейти на страницу…»: приложение своё). Подтверждают на другом устройстве —
адрес, на который Google вернёт (http://127.0.0.1:<порт>/?state=…&code=…),
нужно открыть на этой машине, пока скрипт ждёт. Потом скрипт сам проверит, что
лист читается этим входом, и запишет THERMOBOXES_GOOGLE_OAUTH в .env.codex.local.
На прод то же значение кладётся в переменную окружения Render
THERMOBOXES_GOOGLE_OAUTH.
"""

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from thermoboxes import sheet, sync  # noqa: E402

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
# openid и email — только чтобы узнать, каким аккаунтом вошли: токен должен
# принадлежать тому, кому открыта таблица, а не случайно залогиненному.
SCOPES = ('openid', 'email') + sync.SCOPES
# Сколько ждать нажатий в Google: выбрать аккаунт, пройти «Приложение не
# проверено», отметить доступ — человеку нужно несколько минут.
WAIT_SECONDS = 900


def client_from_json(data):
    """(client_id, client_secret) из JSON, скачанного в консоли."""
    block = data.get('installed') or data.get('web') or data
    client_id, client_secret = block.get('client_id'), block.get('client_secret')
    if not client_id or not client_secret:
        raise SystemExit('В JSON клиента нет client_id или client_secret')
    return client_id, client_secret


def pkce_pair():
    """(verifier, challenge) — PKCE S256: код из адресной строки без verifier бесполезен."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    return verifier, challenge


def id_token_email(id_token):
    """Почта из id_token. Подпись не проверяется: токен пришёл прямо от Google
    по TLS в ответ на наш запрос, и почта нужна только для сверки с аккаунтом."""
    try:
        payload = id_token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get('email')
    except (IndexError, ValueError, AttributeError):
        return None


def env_value(account, client_id, client_secret, refresh_token):
    return json.dumps({'account': account, 'client_id': client_id, 'client_secret': client_secret,
                       'refresh_token': refresh_token}, ensure_ascii=False, separators=(',', ':'))


def upsert_env_line(text, key, value):
    """Строка KEY=value в тексте .env: заменить старую или дописать в конец."""
    lines = text.splitlines()
    line = '%s=%s' % (key, value)
    for index, existing in enumerate(lines):
        if existing.startswith(key + '='):
            lines[index] = line
            break
    else:
        if lines and lines[-1].strip():
            lines.append('')
        lines += ['#Термокороба: вход Google-аккаунта для чтения таблицы (scripts/thermoboxes_google_auth.py)', line]
    return '\n'.join(lines) + '\n'


def wait_for_code(redirect_state, wait_seconds=WAIT_SECONDS):
    """Локальный приём ответа Google: (сервер, функция ожидания кода)."""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — имя задаёт http.server
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get('state', [''])[0] == redirect_state:
                result['code'] = query.get('code', [None])[0]
                result['error'] = query.get('error', [None])[0]
            done = 'code' in result and result['code']
            body = ('Готово — вход подтверждён, окно можно закрыть.' if done
                    else 'Вход не подтверждён: %s' % (result.get('error') or 'нет кода')).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # тишина в консоли
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout = 1

    def wait():
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and 'code' not in result:
            server.handle_request()
        server.server_close()
        if not result.get('code'):
            raise SystemExit('Вход не подтверждён: %s' % (result.get('error') or 'нет ответа за %d с' % wait_seconds))
        return result['code']

    return server, wait


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--client-json', required=True, help='JSON OAuth-клиента (Desktop app) из Google Cloud Console')
    parser.add_argument('--account', required=True, help='адрес аккаунта Google, которому открыта таблица')
    parser.add_argument('--env-file', default=str(ROOT / '.env.codex.local'), help='куда записать вход')
    parser.add_argument('--no-save', action='store_true', help='только проверить, ничего не записывать')
    parser.add_argument('--wait', type=int, default=WAIT_SECONDS, help='сколько секунд ждать подтверждения')
    parser.add_argument('--no-browser', action='store_true',
                        help='не открывать браузер: адрес входа только напечатать')
    args = parser.parse_args()

    import requests
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    client_id, client_secret = client_from_json(json.loads(Path(args.client_json).expanduser().read_text()))
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    server, wait = wait_for_code(state, args.wait)
    redirect_uri = 'http://127.0.0.1:%d' % server.server_address[1]
    url = '%s?%s' % (AUTH_URL, urllib.parse.urlencode({
        'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'scope': ' '.join(SCOPES), 'access_type': 'offline', 'prompt': 'consent',
        'login_hint': args.account, 'code_challenge': challenge, 'code_challenge_method': 'S256',
        'state': state,
    }))
    print('Откройте страницу входа Google (если не открылась сама):\n%s\n' % url, flush=True)
    # Вход подтверждают на другом устройстве — Google вернёт на адрес
    # http://127.0.0.1:<порт>/?state=…&code=…, которого там нет. Этот адрес
    # из строки браузера достаточно открыть на этой машине, пока скрипт ждёт.
    print('Ждём подтверждения до %d с на %s.' % (args.wait, redirect_uri), flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    code = wait()

    response = requests.post(sync.TOKEN_URI, data={
        'code': code, 'client_id': client_id, 'client_secret': client_secret, 'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code', 'code_verifier': verifier,
    }, timeout=30)
    if response.status_code != 200:
        raise SystemExit('Google не обменял код на токен: %s %s' % (response.status_code, response.text[:200]))
    tokens = response.json()
    refresh_token = tokens.get('refresh_token')
    if not refresh_token:
        raise SystemExit('Google не выдал refresh_token. Уберите доступ приложения в настройках аккаунта '
                         '(myaccount.google.com → Безопасность → Сторонние приложения) и запустите снова')
    # Без почты в id_token не сверить, кто вошёл, — а токен должен быть именно
    # того аккаунта, которому открыта таблица.
    email = id_token_email(tokens.get('id_token'))
    if not email:
        raise SystemExit('Google не сообщил, каким аккаунтом вошли: запустите снова')
    if email.lower() != args.account.lower():
        raise SystemExit('Вошли как %s, а таблица открыта %s — войдите нужным аккаунтом' % (email, args.account))

    credentials = Credentials(tokens.get('access_token'), refresh_token=refresh_token, client_id=client_id,
                              client_secret=client_secret, token_uri=sync.TOKEN_URI, scopes=list(sync.SCOPES))
    text = sync.fetch_csv(AuthorizedSession(credentials), who=email, kind='oauth')
    rows, memo = sheet.read_csv_text(text)
    print('Лист читается входом %s: офисов %d, пунктов памятки %d.' % (email, len(rows), len(memo['items'])))

    if args.no_save:
        print('--no-save: вход нигде не записан.')
        return
    value = env_value(email, client_id, client_secret, refresh_token)
    env_path = Path(args.env_file)
    if not env_path.exists():
        # С 25.11.2026 локальные секреты — в зашифрованном хранилище, открытого .env нет.
        sys.path.insert(0, str(ROOT / 'scripts'))
        import otp_secrets
        otp_secrets.Vault().put(sync.OAUTH_ENV, value)
        print('Записано: %s в хранилище секретов (scripts/otp_secrets.py). На прод — то же значение '
              'в переменную окружения %s.' % (sync.OAUTH_ENV, sync.OAUTH_ENV))
        return
    current = env_path.read_text(encoding='utf-8')
    env_path.write_text(upsert_env_line(current, sync.OAUTH_ENV, value), encoding='utf-8')
    print('Записано: %s в %s. На прод — то же значение в переменную Render %s.'
          % (sync.OAUTH_ENV, env_path.name, sync.OAUTH_ENV))


if __name__ == '__main__':
    main()
