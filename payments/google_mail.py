"""Вход через Google для почты компании: письма уходят через Gmail API, без пароля приложения.

Просьба владельца 21.10.2026: «нельзя просто добавить почту … без каких-либо лишних
действий» → выбрал кнопку «Войти через Google». Google с 14.03.2025 не пускает
почтовые программы по обычному паролю (только пароль приложения при двухэтапной
проверке или OAuth). Здесь OAuth: человек выбирает аккаунт и разрешает отправку,
портал хранит токен обновления (зашифрованным, как пароль — mailbox.encrypt) и шлёт
письма через Gmail API. Доступ — только `gmail.send`: читать почту портал не может.

Настройка — один раз (инструкция владельцу 21.10.2026): в Google Cloud клиент OAuth
«веб-приложение» с адресом возврата <API>/api/payments/mailbox/google/callback, Gmail API
включён, приложение опубликовано (в режиме тестирования Google отзывает доступ через
7 дней). На Render:
    PAYMENTS_GOOGLE_CLIENT_ID, PAYMENTS_GOOGLE_CLIENT_SECRET
    PAYMENTS_GOOGLE_REDIRECT_URI — если API живёт не на https://otp-2-fos4.onrender.com
Без них кнопки нет — работает подключение по паролю приложения.

Кто подключает — администратор раздела: ссылку на Google выдаёт ручка за входом в
портал, а в `state` — подписанные компания, человек, окно портала и срок. Ответ Google
приходит на открытую ручку: без подписи и вовремя в нём ничего не примут.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from email import policy
from urllib.parse import urlencode

from . import mail

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
SCOPES = ('openid', 'email', 'https://www.googleapis.com/auth/gmail.send')
DEFAULT_REDIRECT = 'https://otp-2-fos4.onrender.com/api/payments/mailbox/google/callback'
HOST = 'gmail.googleapis.com'
STATE_SECONDS = 15 * 60
HTTP_SECONDS = 15
# Окна портала, которым ответ Google сообщает «почта подключена» (postMessage).
PORTAL_ORIGINS = ('https://alfa330.github.io',)

_TOKENS = {}   # хэш токена обновления → (токен доступа, до какого момента годен)


class GoogleError(Exception):
    """Вход через Google не удался — словами для окна."""


def config(env=None):
    """(client_id, client_secret, redirect_uri) либо None — вход через Google не настроен."""
    env = os.environ if env is None else env
    client_id = str(env.get('PAYMENTS_GOOGLE_CLIENT_ID') or '').strip()
    secret = str(env.get('PAYMENTS_GOOGLE_CLIENT_SECRET') or '').strip()
    if not client_id or not secret:
        return None
    redirect = str(env.get('PAYMENTS_GOOGLE_REDIRECT_URI') or '').strip() or DEFAULT_REDIRECT
    return {'client_id': client_id, 'client_secret': secret, 'redirect_uri': redirect,
            # Только для стенда: подменный Google на этой же машине.
            'auth_url': str(env.get('PAYMENTS_GOOGLE_AUTH_URL') or '').strip() or AUTH_URL,
            'token_url': str(env.get('PAYMENTS_GOOGLE_TOKEN_URL') or '').strip() or TOKEN_URL,
            'send_url': str(env.get('PAYMENTS_GMAIL_SEND_URL') or '').strip() or SEND_URL,
            'revoke_url': str(env.get('PAYMENTS_GOOGLE_REVOKE_URL') or '').strip() or REVOKE_URL}


def enabled(env=None):
    return config(env) is not None


def portal_origin(origin, env=None):
    """Окно портала, которому можно сообщить итог: из списка, а на стенде — localhost."""
    origin = str(origin or '').strip().rstrip('/')
    if origin in PORTAL_ORIGINS:
        return origin
    allow_local = str((os.environ if env is None else env).get('PAYMENTS_MAIL_ALLOW_LOCAL') or '').strip().lower()
    if allow_local in ('1', 'true', 'yes') and (origin.startswith('http://localhost:') or origin.startswith('http://127.0.0.1:')):
        return origin
    return PORTAL_ORIGINS[0]


# ─── Подписанный state ───────────────────────────────────────────────────────

def _state_key():
    secret = (os.getenv('JWT_SECRET') or '').strip()
    if not secret:
        raise GoogleError('Не задан JWT_SECRET — подписать вход нечем')
    return hashlib.sha256(('payments-google-state:' + secret).encode('utf-8')).digest()


def _b64(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def sign_state(payload, *, now=None):
    body = dict(payload, exp=int((now or time.time()) + STATE_SECONDS))
    raw = _b64(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    return raw + '.' + _b64(hmac.new(_state_key(), raw.encode('ascii'), hashlib.sha256).digest())


def read_state(token, *, now=None):
    """Содержимое state либо None — подпись не та, испорчен или срок вышел."""
    try:
        raw, signature = str(token or '').split('.', 1)
        expected = _b64(hmac.new(_state_key(), raw.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        body = json.loads(_unb64(raw).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, GoogleError):
        return None
    if not isinstance(body, dict) or int(body.get('exp') or 0) < (now or time.time()):
        return None
    return body


# ─── OAuth ───────────────────────────────────────────────────────────────────

def auth_url(cfg, state, login_hint=None):
    """Куда отправить человека: выбор аккаунта и «Разрешить». `prompt=consent` —
    чтобы Google выдал токен обновления и при повторном входе."""
    params = {'client_id': cfg['client_id'], 'redirect_uri': cfg['redirect_uri'], 'response_type': 'code',
              'scope': ' '.join(SCOPES), 'access_type': 'offline', 'prompt': 'consent', 'state': state,
              'include_granted_scopes': 'true'}
    if login_hint:
        params['login_hint'] = login_hint
    return cfg['auth_url'] + '?' + urlencode(params)


def _post(url, data, timeout=HTTP_SECONDS, **kwargs):
    import requests

    return requests.post(url, data=data, timeout=timeout, **kwargs)


def exchange(cfg, code):
    """Код из ответа Google → токены. Не вышло — GoogleError словами."""
    try:
        response = _post(cfg['token_url'], {'code': code, 'client_id': cfg['client_id'],
                                             'client_secret': cfg['client_secret'],
                                             'redirect_uri': cfg['redirect_uri'], 'grant_type': 'authorization_code'})
    except Exception as exc:  # noqa: BLE001 — сеть
        raise GoogleError('Google не ответил (%s) — попробуйте ещё раз' % exc.__class__.__name__) from None
    body = _json(response)
    if response.status_code != 200 or not body.get('access_token'):
        raise GoogleError('Google не выдал доступ: %s' % (body.get('error_description') or body.get('error')
                                                          or response.status_code))
    if 'gmail.send' not in str(body.get('scope') or ''):
        raise GoogleError('Не разрешена отправка писем: в окне Google отметьте «Отправлять письма от вашего имени»')
    if not body.get('refresh_token'):
        raise GoogleError('Google не выдал постоянный доступ — войдите ещё раз')
    return body


def email_of(tokens):
    """Адрес почты, в которую вошли: из id_token (он пришёл от Google напрямую, по TLS)."""
    try:
        payload = json.loads(_unb64(str(tokens.get('id_token') or '').split('.')[1]).decode('utf-8'))
    except (IndexError, ValueError, UnicodeDecodeError):
        raise GoogleError('Google не сообщил адрес почты — войдите ещё раз') from None
    email = str(payload.get('email') or '').strip().lower()
    if not email or payload.get('email_verified') is False:
        raise GoogleError('У аккаунта Google нет подтверждённого адреса почты')
    return email


def _json(response):
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def access_token(cfg, refresh_token, *, clock=time.monotonic):
    """Токен доступа по токену обновления (живёт около часа — помним до истечения).
    Отозвали доступ — MailError('transport', reason='login'): нужен новый вход."""
    key = hashlib.sha256(str(refresh_token).encode('utf-8')).hexdigest()
    cached = _TOKENS.get(key)
    if cached and cached[1] > clock():
        return cached[0]
    try:
        response = _post(cfg['token_url'], {'refresh_token': refresh_token, 'client_id': cfg['client_id'],
                                             'client_secret': cfg['client_secret'], 'grant_type': 'refresh_token'})
    except Exception as exc:  # noqa: BLE001
        raise mail.MailError('transport', 'Google не ответил (%s)' % exc.__class__.__name__, reason='connect') from None
    body = _json(response)
    if response.status_code == 200 and body.get('access_token'):
        _TOKENS[key] = (body['access_token'], clock() + max(60, int(body.get('expires_in') or 3600) - 120))
        return body['access_token']
    if body.get('error') in ('invalid_grant', 'unauthorized_client', 'invalid_client'):
        raise mail.MailError('transport', 'Google отозвал доступ к почте (%s) — войдите через Google заново'
                             % body.get('error'), reason='login')
    raise mail.MailError('transport', 'Google не выдал доступ к почте (%s)' % (body.get('error') or response.status_code),
                         reason='connect')


def revoke(refresh_token):
    """Отключили почту — доступ портала к ней Google тоже забывает. Не вышло — не беда."""
    cfg = config()
    try:
        _post((cfg or {}).get('revoke_url') or REVOKE_URL, {'token': refresh_token}, timeout=5)
    except Exception:  # noqa: BLE001
        pass


# ─── Отправка через Gmail API ────────────────────────────────────────────────

_THROTTLED = ('rateLimitExceeded', 'userRateLimitExceeded', 'dailyLimitExceeded', 'quotaExceeded')


def _refusal(response):
    """Ответ Gmail API → MailError нужного вида (как ответы SMTP-сервера в mail.py)."""
    body = _json(response).get('error') or {}
    reasons = [str(item.get('reason') or '') for item in body.get('errors') or [] if isinstance(item, dict)]
    text = ' '.join(str(body.get('message') or response.text or '').split())[:300]
    detail = '%s %s' % (response.status_code, text)
    if response.status_code == 429 or any(reason in _THROTTLED for reason in reasons):
        return mail.MailError('throttled', 'Gmail ограничил отправку: %s' % detail)
    if response.status_code in (401, 403):
        return mail.MailError('transport', 'Gmail не пускает отправку: %s — войдите через Google заново' % detail,
                              reason='login')
    if 400 <= response.status_code < 500:
        return mail.MailError('permanent', 'Gmail не принял письмо: %s' % detail)
    return mail.MailError('temporary', 'Gmail не ответил как надо: %s' % detail)


class Transport:
    """Тот же разговор, что у mail.Transport (`with … as smtp: smtp.send(message)`), но
    через Gmail API. В `cfg.password` — токен обновления."""

    def __init__(self, cfg, timeout=HTTP_SECONDS, google=None):
        self.cfg = cfg
        self.timeout = timeout
        self.google = google
        self.token = None

    def __enter__(self):
        self.google = self.google or config()
        if not self.google:
            raise mail.MailError('transport', 'Вход через Google не настроен на сервере', reason='connect')
        self.token = access_token(self.google, self.cfg.password)
        return self

    def __exit__(self, *_exc):
        self.token = None
        return False

    @property
    def closed(self):
        return self.token is None

    def deadline(self, seconds):
        self.timeout = max(1.0, float(seconds))

    def send(self, message):
        raw = _b64(message.as_bytes(policy=policy.SMTP))
        try:
            response = _post(self.google['send_url'], None, timeout=self.timeout, json={'raw': raw},
                             headers={'Authorization': 'Bearer ' + self.token})
        except Exception as exc:  # noqa: BLE001
            raise mail.MailError('temporary', 'Связь с Gmail оборвалась (%s)' % exc.__class__.__name__) from None
        if response.status_code == 200:
            return message['Message-ID']
        raise _refusal(response)
