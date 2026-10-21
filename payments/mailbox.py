"""Почта для писем компании: с какого ящика уходят письма поставщикам о смене её реквизитов.

Решение владельца 21.10.2026 (ответ на вопрос с вариантами): у каждой нашей компании
своя почта. В окне компании («Реквизиты компаний») — блок «Почта для писем»: адрес и
пароль приложения, сервер подставляется сам по адресу; «Подключить» сначала шлёт
пробное письмо на этот же адрес и только потом сохраняет; пароль хранится
зашифрованным и наружу не отдаётся; письма компании без своей почты ждут.

Устройство.
  * Строка payment_entity_mailboxes на компанию. Пароль — Fernet, как номера карт
    (payments/cards.py): свой ключ раздела PAYMENTS_CARD_KEY, если его завели, иначе
    ключ из JWT_SECRET со своей солью. Пароль не читается (сменили ключ) — с ящика не
    отправляем, окно просит ввести пароль заново.
  * Сервер — по адресу: публичная почта (gmail.com, yandex.ru, mail.ru …) — по
    таблице, свой домен — по MX-записи (Google Workspace, Яндекс 360, VK WorkSpace,
    Microsoft 365). MX спрашиваем у DNS через HTTPS: своего резолвера MX у сервера нет.
    Не узнали — окно просит сервер и порт.
  * Сервер вручную — только наружу и только почтовые порты: ручка не должна стучаться
    во внутреннюю сеть. Стенд пускает 127.0.0.1 без шифрования переменной
    PAYMENTS_MAIL_ALLOW_LOCAL=1.
  * Ящик перестал пускать (сменили пароль, запретили почтовые программы) — планировщик
    пишет причину в `problem` и больше в этот ящик не стучится: вход с неверным паролем
    раз в десять минут почта сочла бы подбором. Письма ждут, окно компании показывает
    причину; новый пароль или удачная «Проверить» её снимают.
  * Gmail — и кнопкой «Войти через Google» (google_mail.py, решение владельца 21.10.2026):
    тогда `security = 'oauth'`, в `password_enc` — токен обновления, письма уходят через
    Gmail API (opener()), а не по SMTP.
"""

import html as _html
import ipaddress
import logging
import os
import re
import socket
import time
from collections import namedtuple

from . import cards, google_mail, mail
from .sqlutil import NOW_SQL, row_map

# Почтовые порты наружу: 465 — SSL с первого байта, 587 и 2525 — STARTTLS.
# Вход через Google — не SMTP: `security = 'oauth'`, письма уходят через Gmail API.
# Порт 25 Render не выпускает вовсе.
PORTS = {465: 'ssl', 587: 'starttls', 2525: 'starttls'}

PROVIDERS = {
    'gmail': {
        'label': 'Gmail', 'host': 'smtp.gmail.com', 'port': 465,
        'help_url': 'https://myaccount.google.com/apppasswords',
        'password_hint': 'Нужен пароль приложения из аккаунта Google (16 букв), а не пароль от почты. '
                         'Он появляется, когда в аккаунте включена двухэтапная проверка.',
    },
    'yandex': {
        'label': 'Яндекс', 'host': 'smtp.yandex.ru', 'port': 465,
        'help_url': 'https://id.yandex.ru/security/app-passwords',
        'password_hint': 'Нужен пароль приложения Яндекса для почты, а не пароль от почты. '
                         'В настройках Яндекс Почты должен быть разрешён доступ почтовым программам.',
    },
    'mailru': {
        'label': 'Mail.ru', 'host': 'smtp.mail.ru', 'port': 465,
        'help_url': 'https://account.mail.ru/user/2-step-auth/passwords/',
        'password_hint': 'Нужен пароль для внешнего приложения из настроек безопасности Mail.ru, а не пароль от почты.',
    },
    'microsoft': {
        'label': 'Microsoft 365', 'host': 'smtp.office365.com', 'port': 587,
        'help_url': None,
        'password_hint': 'В Microsoft 365 отправку по паролю (SMTP AUTH) для ящика включает администратор почты.',
    },
}
MANUAL_HINT = 'Обычно нужен пароль приложения из настроек безопасности почты, а не пароль от входа в неё.'

# Публичная почта — по домену адреса.
_PUBLIC = {
    'gmail.com': 'gmail', 'googlemail.com': 'gmail',
    'yandex.ru': 'yandex', 'yandex.kz': 'yandex', 'yandex.by': 'yandex', 'yandex.com': 'yandex',
    'ya.ru': 'yandex', 'narod.ru': 'yandex',
    'mail.ru': 'mailru', 'bk.ru': 'mailru', 'list.ru': 'mailru', 'inbox.ru': 'mailru', 'internet.ru': 'mailru',
}
# Почта на своём домене — по тому, куда указывает его MX-запись.
_MX = (('google.com', 'gmail'), ('googlemail.com', 'gmail'), ('yandex.net', 'yandex'), ('yandex.ru', 'yandex'),
       ('mail.ru', 'mailru'), ('protection.outlook.com', 'microsoft'))
_DOH = ('https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query')
MX_TIMEOUT_SECONDS = 3
MX_CACHE_SECONDS = 3600
_MX_CACHE = {}

_HOST_RE = re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+')

UNREADABLE = 'Сохранённый пароль не читается — введите его заново'
TEST_SUBJECT = 'Проверка почты для писем поставщикам'

Settings = namedtuple('Settings', 'address provider host port security login password')

# Почтовый сервер для «Подключить» и «Проверить»; тесты подменяют.
TRANSPORT = mail.Transport
OAUTH = 'oauth'


def opener(settings):
    """Чем отправлять: Gmail API для входа через Google, иначе SMTP."""
    return google_mail.Transport if settings.security == OAUTH else TRANSPORT


def google_settings(email, refresh_token):
    """Почта, подключённая кнопкой «Войти через Google»."""
    return Settings(address=email, provider='gmail', host=google_mail.HOST, port=443, security=OAUTH,
                    login=email, password=refresh_token)


class MailboxError(Exception):
    """Почту не подключить: `field` — у какого поля окна сказать, `help_url` — где взять пароль."""

    def __init__(self, message, *, field=None, code='PAYMENTS_MAILBOX_INVALID', help_url=None):
        super().__init__(message)
        self.field = field
        self.code = code
        self.help_url = help_url


def local_allowed(env=None):
    """Стенд: почтовый сервер на этой же машине, без шифрования."""
    env = os.environ if env is None else env
    return str(env.get('PAYMENTS_MAIL_ALLOW_LOCAL') or '').strip().lower() in ('1', 'true', 'yes')


def domain_of(address):
    return str(address or '').rpartition('@')[2].strip().lower()


# ─── Какая это почта ─────────────────────────────────────────────────────────

def _mx_hosts(domain):
    """MX-хосты домена через DNS по HTTPS. Не ответил ни один — пусто (окно попросит сервер)."""
    import requests

    cached = _MX_CACHE.get(domain)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    for url in _DOH:
        try:
            response = requests.get(url, params={'name': domain, 'type': 'MX'},
                                    headers={'accept': 'application/dns-json'}, timeout=MX_TIMEOUT_SECONDS)
            if response.status_code != 200:
                continue
            answers = response.json().get('Answer') or []
        except Exception:  # noqa: BLE001 — сеть, не JSON: спросим другой
            continue
        hosts = [str(item.get('data') or '').split()[-1].rstrip('.').lower()
                 for item in answers if item.get('type') == 15 and str(item.get('data') or '').split()]
        _MX_CACHE[domain] = (time.monotonic() + MX_CACHE_SECONDS, hosts)
        return hosts
    return []


def provider_of(address, *, mx=None):
    """Ключ почты (gmail, yandex, mailru, microsoft) по адресу либо None."""
    return provider_of_domain(domain_of(address), mx=mx)


def provider_of_domain(domain, *, mx=None):
    """То же по домену: публичная почта — по таблице, свой домен — по MX."""
    domain = str(domain or '').strip().lower()
    if not domain:
        return None
    if domain in _PUBLIC:
        return _PUBLIC[domain]
    for host in (mx or _mx_hosts)(domain):
        for suffix, key in _MX:
            if host == suffix or host.endswith('.' + suffix):
                return key
    return None


def provider_by_host(host):
    """Сервер ввели руками, а он из известных (smtp.gmail.com) — подсказки те же."""
    return next((key for key, item in PROVIDERS.items() if item['host'] == host), None)


def server_view(key):
    if not key:
        return None
    item = PROVIDERS[key]
    return {'provider': key, 'label': item['label'], 'host': item['host'], 'port': item['port'],
            'security': PORTS[item['port']], 'help_url': item['help_url'], 'password_hint': item['password_hint']}


def detect(address, *, mx=None):
    """Окну, пока человек вводит адрес: какая это почта и где взять пароль приложения.
    Не узнали — `server` пустой, а `guess` подсказывает имя сервера."""
    try:
        email = mail.one_email(address)
    except mail.EmailError as exc:
        raise MailboxError(str(exc), field='address', code='PAYMENTS_EMAIL_INVALID') from None
    server = server_view(provider_of(email, mx=mx))
    return {'address': email, 'server': server, 'guess': None if server else 'smtp.' + domain_of(email)}


def _inside(host):
    """None — сервер снаружи; иначе — почему с него нельзя."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return 'Сервер %s не найден — проверьте имя' % host
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split('%')[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return 'Сервер %s — во внутренней сети, отправлять с него нельзя' % host
    return None


def manual_server(host, port, *, allow_local=False, resolve=None):
    """Сервер и порт, введённые руками, → (сервер, порт, шифрование) либо MailboxError."""
    host = str(host or '').strip().lower().rstrip('.')
    if not host:
        raise MailboxError('Укажите сервер исходящей почты (SMTP)', field='host', code='PAYMENTS_MAILBOX_SERVER')
    try:
        port = int(str(port or '').strip() or 465)
    except ValueError:
        raise MailboxError('Порт — число: 465 или 587', field='port', code='PAYMENTS_MAILBOX_SERVER') from None
    if allow_local and host in mail.LOCAL_HOSTS:
        return host, port, 'none'
    if not _HOST_RE.fullmatch(host):
        raise MailboxError('Сервер — имя вроде smtp.example.kz', field='host', code='PAYMENTS_MAILBOX_SERVER')
    if port not in PORTS:
        raise MailboxError('Порт: 465 (SSL) или 587 (STARTTLS)', field='port', code='PAYMENTS_MAILBOX_SERVER')
    problem = (resolve or _inside)(host)
    if problem:
        raise MailboxError(problem, field='host', code='PAYMENTS_MAILBOX_SERVER')
    return host, port, PORTS[port]


# ─── Пароль ──────────────────────────────────────────────────────────────────

def _keys():
    """Ключи по старшинству: первым шифруют, читают любым (как у номеров карт)."""
    keys = []
    own = cards._own_key()
    if own:
        keys.append(own)
    secret = (os.getenv('JWT_SECRET') or '').strip()
    if secret:
        keys.append(cards._from_phrase('payments-mailbox-password:' + secret))
    return keys


def encrypt(password):
    from cryptography.fernet import MultiFernet

    keys = _keys()
    if not keys:
        raise MailboxError('Не задан ключ шифрования (JWT_SECRET) — пароль не сохранить', code='PAYMENTS_MAILBOX_KEY')
    return MultiFernet(keys).encrypt(str(password).encode('utf-8')).decode('ascii')


def decrypt(token):
    """Пароль из базы; None — нечего или нечем расшифровать (сменили ключ)."""
    if not token:
        return None
    try:
        from cryptography.fernet import MultiFernet

        return MultiFernet(_keys()).decrypt(str(token).encode('ascii')).decode('utf-8')
    except Exception:  # noqa: BLE001 — InvalidToken, нет ключей
        return None


# ─── Что прислало окно ───────────────────────────────────────────────────────

def prepare(data, *, current=None, allow_local=False, mx=None, resolve=None):
    """Адрес, пароль и (по желанию) сервер из окна → Settings либо MailboxError у поля.

    Пароль можно не вводить, если адрес прежний: остаётся сохранённый — так меняют
    только сервер или порт. Сервер не прислали — подставляется по адресу."""
    data = data or {}
    try:
        address = mail.one_email(data.get('address'))
    except mail.EmailError as exc:
        raise MailboxError(str(exc), field='address', code='PAYMENTS_EMAIL_INVALID') from None
    password = str(data.get('password') or '').strip()
    if not password:
        # Пароль можно не вводить только у почты по паролю: у входа через Google в
        # password_enc — токен обновления, по SMTP он не пароль.
        if not current or current.get('address') != address or current.get('security') == OAUTH:
            raise MailboxError('Введите пароль приложения', field='password', code='PAYMENTS_MAILBOX_PASSWORD')
        password = decrypt(current.get('password_enc'))
        if password is None:
            raise MailboxError(UNREADABLE, field='password', code='PAYMENTS_MAILBOX_PASSWORD')
    if str(data.get('host') or '').strip():
        host, port, security = manual_server(data.get('host'), data.get('port'), allow_local=allow_local,
                                             resolve=resolve)
        provider = provider_by_host(host)
    else:
        provider = provider_of(address, mx=mx)
        if not provider:
            raise MailboxError('Сервер этой почты по адресу не определился — укажите его', field='host',
                               code='PAYMENTS_MAILBOX_SERVER')
        host, port = PROVIDERS[provider]['host'], PROVIDERS[provider]['port']
        security = PORTS[port]
    if provider == 'gmail':
        # Google показывает пароль приложения группами по четыре буквы: пробелы — не часть пароля.
        password = ''.join(password.split())
    return Settings(address=address, provider=provider, host=host, port=port, security=security,
                    login=address, password=password)


def config(settings):
    return mail.Config(host=settings.host, port=settings.port, user=settings.login, password=settings.password,
                       sender=settings.address, reply_to=None, security=settings.security)


def test_letter(settings, entity_name):
    """Пробное письмо на тот же ящик: доказывает и вход, и отправку."""
    name = ' '.join(str(entity_name or '').split()) or 'нашей компании'
    text = ('Здравствуйте!\n\nЭто проверочное письмо из раздела «Оплата счетов». С этого ящика поставщики будут '
            'получать письма о смене реквизитов %s.\n\nОтвечать на него не нужно.' % name)
    body = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>' + _html.escape(TEST_SUBJECT)
            + '</title></head><body style="margin:0;padding:24px 12px;background:#f5f5f7;'
            'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
            'color:#0f172a;font-size:15px;line-height:1.5">'
            '<div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px 24px">'
            '<p style="margin:0 0 12px">Здравствуйте!</p>'
            '<p style="margin:0 0 16px">Это проверочное письмо из раздела «Оплата счетов». С этого ящика поставщики '
            'будут получать письма о смене реквизитов <b>' + _html.escape(name) + '</b>.</p>'
            '<p style="margin:0;color:#64748b">Отвечать на него не нужно.</p></div></body></html>')
    return mail.build_message(config(settings), to=[settings.address], subject=TEST_SUBJECT, text=text, html=body,
                              display_name=name)


def _server_reply(exc):
    """«(535 5.7.8 …)» из текста ошибки — что ответил сервер."""
    text = str(exc)
    start = text.rfind('(')
    return text[start:] if start != -1 and text.endswith(')') else ''


def refusal(exc, settings):
    """Отказ почты при «Подключить» и «Проверить» → MailboxError у того поля, которое править."""
    item = PROVIDERS.get(settings.provider) or {}
    help_url = item.get('help_url')
    if settings.security == OAUTH:
        # Вход через Google: пароля нет — чинится новым входом.
        code = 'PAYMENTS_MAILBOX_LOGIN' if exc.reason == 'login' else 'PAYMENTS_MAILBOX_SEND'
        return MailboxError(str(exc), code=code)
    if exc.reason == 'login':
        hint = item.get('password_hint') or MANUAL_HINT
        return MailboxError(('Почта не приняла пароль. %s %s' % (hint, _server_reply(exc))).strip(), field='password',
                            code='PAYMENTS_MAILBOX_LOGIN', help_url=help_url)
    if exc.reason == 'connect':
        return MailboxError('Не удалось связаться с сервером %s:%s — %s' % (settings.host, settings.port, exc),
                            field=None if settings.provider else 'host', code='PAYMENTS_MAILBOX_CONNECT')
    if exc.reason == 'sender':
        return MailboxError('Ящик не разрешает отправлять от %s: адрес должен быть самим этим ящиком'
                            % settings.address, field='address', code='PAYMENTS_MAILBOX_SENDER')
    return MailboxError('Пробное письмо не ушло: %s' % exc, code='PAYMENTS_MAILBOX_SEND')


def verify(settings, entity_name, *, transport=None):
    """Войти в ящик и отправить пробное письмо на его же адрес. Вернёт Message-ID; не
    вышло — MailboxError. Соединение — на одну проверку."""
    message = test_letter(settings, entity_name)
    try:
        with (transport or opener(settings))(config(settings)) as smtp:
            return smtp.send(message)
    except mail.MailError as exc:
        raise refusal(exc, settings) from None


# ─── База ────────────────────────────────────────────────────────────────────

_FIELDS = ('legal_entity_id', 'address', 'provider', 'host', 'port', 'security', 'login', 'password_enc',
           'checked_at', 'problem', 'problem_at', 'updated_at', 'updated_by_name')


def read(cursor, entity_id):
    cursor.execute("SELECT %s FROM payment_entity_mailboxes WHERE legal_entity_id = %%s" % ', '.join(_FIELDS),
                   (int(entity_id),))
    row = cursor.fetchone()
    return row_map(_FIELDS, row) if row else None


def view(row):
    """Что окну можно знать о ящике: без логина и пароля."""
    if not row:
        return None
    item = PROVIDERS.get(row.get('provider')) or {}
    google = row['security'] == OAUTH
    return {'address': row['address'], 'provider': row.get('provider'), 'label': item.get('label'),
            'host': row['host'], 'port': row['port'], 'security': row['security'],
            'method': 'google' if google else 'password',
            'manual': not google and (not row.get('provider') or item.get('host') != row['host']),
            'help_url': item.get('help_url'), 'checked_at': row.get('checked_at'), 'problem': row.get('problem'),
            'problem_at': row.get('problem_at'), 'updated_at': row.get('updated_at'),
            'updated_by_name': row.get('updated_by_name')}


def views(cursor):
    """Компания → ящик (для строки списка и окна компании)."""
    cursor.execute("SELECT %s FROM payment_entity_mailboxes" % ', '.join(_FIELDS))
    return {row[0]: view(row_map(_FIELDS, row)) for row in cursor.fetchall()}


def works(item):
    """С ящика сейчас отправляют: подключён и не сообщал о беде."""
    return bool(item) and not item.get('problem')


def ready(cursor, entity_id):
    cursor.execute("SELECT 1 FROM payment_entity_mailboxes WHERE legal_entity_id = %s AND problem IS NULL",
                   (int(entity_id),))
    return cursor.fetchone() is not None


def save(cursor, entity_id, settings, actor):
    """Ящик проверен пробным письмом — записать (зашифровав пароль) и снять прежнюю беду."""
    actor = actor or {}
    cursor.execute(
        "INSERT INTO payment_entity_mailboxes (legal_entity_id, address, provider, host, port, security, login, "
        "       password_enc, checked_at, problem, problem_at, updated_at, updated_by, updated_by_name) "
        "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, %%s, %%s, %s, NULL, NULL, %s, %%s, %%s) "
        "ON CONFLICT (legal_entity_id) DO UPDATE SET address = EXCLUDED.address, provider = EXCLUDED.provider, "
        "       host = EXCLUDED.host, port = EXCLUDED.port, security = EXCLUDED.security, login = EXCLUDED.login, "
        "       password_enc = EXCLUDED.password_enc, checked_at = EXCLUDED.checked_at, problem = NULL, "
        "       problem_at = NULL, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by, "
        "       updated_by_name = EXCLUDED.updated_by_name" % (NOW_SQL, NOW_SQL),
        (int(entity_id), settings.address, settings.provider, settings.host, int(settings.port), settings.security,
         settings.login, encrypt(settings.password), actor.get('id'), actor.get('name')))


def note_ok(cursor, entity_id):
    """Письмо с ящика ушло: ящик работает."""
    cursor.execute("UPDATE payment_entity_mailboxes SET checked_at = %s, problem = NULL, problem_at = NULL "
                   "WHERE legal_entity_id = %%s" % NOW_SQL, (int(entity_id),))


def note_problem(cursor, entity_id, text):
    """Ящик не пускает — причину видно в окне компании, планировщик в него больше не стучится."""
    cursor.execute("UPDATE payment_entity_mailboxes SET problem = %%s, problem_at = %s WHERE legal_entity_id = %%s"
                   % NOW_SQL, (str(text)[:1000], int(entity_id)))
    if cursor.rowcount:
        logging.warning('Оплата счетов: почта компании %s не пускает — %s', entity_id, text)


def remove(cursor, entity_id):
    cursor.execute("DELETE FROM payment_entity_mailboxes WHERE legal_entity_id = %s", (int(entity_id),))
    return cursor.rowcount


def settings_of(row):
    """Строка базы → Settings; None — пароль не читается."""
    password = decrypt(row.get('password_enc')) if row else None
    if password is None:
        return None
    return Settings(address=row['address'], provider=row.get('provider'), host=row['host'], port=int(row['port']),
                    security=row['security'], login=row['login'], password=password)


def outboxes(cursor, entity_ids):
    """Компания → Settings ящика, с которого сейчас можно отправлять. Ящика нет или он
    сообщил о беде — компании в ответе нет: её письма ждут. Пароль не читается — ящику
    пишется беда (окно попросит пароль заново), письма тоже ждут."""
    ids = sorted({int(entity_id) for entity_id in entity_ids or ()})
    if not ids:
        return {}
    cursor.execute("SELECT %s FROM payment_entity_mailboxes WHERE legal_entity_id = ANY(%%s) AND problem IS NULL"
                   % ', '.join(_FIELDS), (ids,))
    result = {}
    for found in cursor.fetchall():
        row = row_map(_FIELDS, found)
        settings = settings_of(row)
        if settings is None:
            note_problem(cursor, row['legal_entity_id'], UNREADABLE)
            continue
        result[row['legal_entity_id']] = settings
    return result


def pause_key(settings):
    """Пауза после отказа — у ящика, а не у компании: две компании с одним ящиком ждут вместе."""
    return (settings.host, settings.login)
