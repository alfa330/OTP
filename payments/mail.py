"""Почта раздела «Оплата счетов»: адреса поставщиков и чья почта у нашей компании.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов». Решение 27.10.2026: «пока
убери автоотправку и сделай полу ручной» — портал писем сам больше не отправляет:
кнопка «Отправить письмо» открывает почту компании с готовым письмом, а отправляет
человек (requisites_mail.py). Отправка по SMTP и вход через Google жили здесь и в
mailbox.py / google_mail.py до 27.10.2026 — в git (коммиты 216f22ff, c1c023ba, 91cac050).

Здесь — адреса и какая это почта. Публичная (gmail.com, yandex.ru, mail.ru …) — по
таблице, свой домен — по MX-записи (Google Workspace, Яндекс 360, VK WorkSpace,
Microsoft 365). По ней кнопка решает, что открыть: Gmail — в браузере, сразу в
аккаунте компании, остальное — почтовой программой компьютера.
"""

import re
import time

# Сколько адресов у одного поставщика: бухгалтерия, менеджер, общий ящик.
MAX_EMAILS = 5
MAX_EMAIL_LENGTH = 254

# Адрес латиницей: имя@домен.зона. Кириллические домены (почта.рф) не
# принимаем — их понимает не каждый почтовый сервер на пути письма.
_EMAIL_RE = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
# Невидимое, что приезжает при копировании из Telegram, сайтов и Word, — убирается
# до разбора. Разделители — явным набором, а не \s: у Python и JavaScript «пробел»
# понимается по-разному, а форма (paymentsMeta.parseEmails) обязана решать так же.
_INVISIBLE_RE = re.compile('[\u200b\u200c\u200d\u2060\ufeff\u00ad]')
_SPLIT_RE = re.compile('[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000,;]+')

# Публичная почта — по домену адреса.
_PUBLIC = {
    'gmail.com': 'gmail', 'googlemail.com': 'gmail',
    'yandex.ru': 'yandex', 'yandex.kz': 'yandex', 'yandex.by': 'yandex', 'yandex.com': 'yandex',
    'ya.ru': 'yandex', 'narod.ru': 'yandex',
    'mail.ru': 'mailru', 'bk.ru': 'mailru', 'list.ru': 'mailru', 'inbox.ru': 'mailru', 'internet.ru': 'mailru',
}
# Почта на своём домене — по тому, куда указывает его MX-запись. MX спрашиваем у DNS
# через HTTPS: своего резолвера MX у сервера нет.
_MX = (('google.com', 'gmail'), ('googlemail.com', 'gmail'), ('yandex.net', 'yandex'), ('yandex.ru', 'yandex'),
       ('mail.ru', 'mailru'), ('protection.outlook.com', 'microsoft'))
_DOH = ('https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query')
MX_TIMEOUT_SECONDS = 3
MX_CACHE_SECONDS = 3600
_MX_CACHE = {}


class EmailError(ValueError):
    """Строка с адресами не разобрана: в `token` — что именно не похоже на адрес."""

    def __init__(self, message, token=None):
        super().__init__(message)
        self.token = token


def _tokens(value):
    text = ','.join(str(item or '') for item in value) if isinstance(value, (list, tuple)) else str(value or '')
    return [part for part in _SPLIT_RE.split(_INVISIBLE_RE.sub('', text)) if part]


def _clean(part):
    """Слово из поля → адрес в нижнем регистре либо None, если это не адрес."""
    token = part.strip('<>')
    if token.lower().startswith('mailto:'):
        token = token[len('mailto:'):]
    token = token.rstrip('.').lower()
    if not token:
        return ''
    local = token.partition('@')[0]
    if (len(token) > MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(token) or '..' in token
            or local.startswith('.') or local.endswith('.')):
        return None
    return token


def parse_emails(value):
    """Адреса поставщика из поля формы: строка через запятую, точку с запятой,
    пробел или перенос строки — либо список. В нижнем регистре, без повторов,
    в том порядке, в каком их написали. Пусто — пустой список.

    Не похожее на адрес не выбрасывается молча, а возвращается ошибкой: письмо о
    реквизитах, ушедшее «в никуда» из-за опечатки, хуже отказа сохранить."""
    result = []
    for part in _tokens(value):
        token = _clean(part)
        if token is None:
            raise EmailError('«%s» — не похоже на адрес почты' % part[:80], token=part)
        if token and token not in result:
            result.append(token)
    if len(result) > MAX_EMAILS:
        raise EmailError('Не больше %s адресов у одного поставщика' % MAX_EMAILS)
    return result


def find_emails(text):
    """Адреса из свободного текста («Айдос, +7 700…, buh@x.kz»): всё, что похоже на
    адрес, остальное пропускается. Для переноса почты из поля «Контакт»."""
    result = []
    for part in _tokens(text):
        token = _clean(part.strip('()[]{}<>:;,.!?"\'«»'))
        if token and '@' in token and token not in result:
            result.append(token)
    return result[:MAX_EMAILS]


def one_email(value):
    """Ровно один адрес (строка справочника, почта компании): в нижнем регистре,
    без «mailto:» и невидимого. Пусто, не адрес или несколько — EmailError."""
    found = parse_emails(value)
    if len(found) != 1:
        raise EmailError('Укажите один адрес почты' if found else 'Укажите адрес почты')
    return found[0]


# ─── Какая это почта ─────────────────────────────────────────────────────────

def domain_of(address):
    return str(address or '').rpartition('@')[2].strip().lower()


def _mx_hosts(domain):
    """MX-хосты домена через DNS по HTTPS. Не ответил ни один — пусто (почта не узнана)."""
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
