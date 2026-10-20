"""Почта раздела «Оплата счетов»: адреса поставщиков и отправка писем по SMTP.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов». Что пишем и кому —
requisites_mail.py; здесь только адреса и сама отправка.

Отправляет корпоративный ящик по SMTP (решение владельца 20.10.2026). Адрес и
пароль приложения лежат в окружении Render, в коде их нет:

    PAYMENTS_SMTP_HOST       smtp.gmail.com, smtp.yandex.ru, smtp.mail.ru …
    PAYMENTS_SMTP_PORT       465 — SSL (по умолчанию), 587 — STARTTLS
    PAYMENTS_SMTP_USER       логин ящика, обычно сам адрес
    PAYMENTS_SMTP_PASSWORD   пароль приложения (не пароль от входа в почту)
    PAYMENTS_MAIL_FROM       от кого, если не совпадает с логином
    PAYMENTS_MAIL_REPLY_TO   куда отвечать, если не на ящик отправителя
    PAYMENTS_SMTP_SECURITY   ssl | starttls | none; none — только 127.0.0.1 (стенд)

Render на платном тарифе выпускает наружу порты 465 и 587, порт 25 закрыт всегда.

Ошибки отправки делятся на четыре вида (MailError.kind), от вида зависит, что
делать дальше:
    temporary  — сеть, тайм-аут, ответ 4xx: письмо пробуем ещё раз позже;
    permanent  — сервер не принял адрес или письмо (5xx): повтор не поможет;
    throttled  — ящик упёрся в свой лимит или фильтр («sending quota exceeded»,
                 «suspicion of spam»): остальные адреса не виноваты — прогон встаёт на
                 паузу, а этому адресу засчитывается попытка (иначе отказ, который
                 на деле про адрес, крутил бы паузы вечно);
    transport  — не войти в ящик или ящик не пускает отправителя: виновата
                 настройка — письма ждут, попытки не тратятся.

Сервер отправки (smtp.gmail.com, smtp.yandex.ru) принимает письма на чужие
домены, не проверяя адрес: «такого ящика нет» придёт позже письмом-отказом в
сам ящик. Поэтому «отправлено» здесь значит «принял сервер отправки».
"""

import os
import re
import smtplib
import socket
import ssl
from collections import namedtuple
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

# Сколько адресов у одного поставщика: бухгалтерия, менеджер, общий ящик.
MAX_EMAILS = 5
MAX_EMAIL_LENGTH = 254
# Терпение к почтовому серверу на одну операцию сокета. Отправка идёт из общего
# пула потоков бота, и висящее соединение держало бы место в нём: прогон ещё и
# урезает тайм-аут до остатка своего бюджета (requisites_mail.run, deadline()).
TIMEOUT_SECONDS = 15
QUIT_SECONDS = 3

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
_LOCAL_HOSTS = ('127.0.0.1', 'localhost', '::1')

# Отказ, в котором виноват сам ящик, а не адрес: суточный лимит отправки,
# частота, подозрение на спам. Такой ответ приходит и на RCPT, и на DATA с кодом
# 5xx, и если считать его «адрес не принят», за один прогон сгорел бы весь список.
# «Over quota» без «sending» — переполненный ящик ПОЛУЧАТЕЛЯ: это про адрес.
_SENDER_TROUBLE_RE = re.compile(
    r'(\b5\.4\.5\b|\b4\.7\.0\b|sending (?:limit|quota)|daily [^.]{0,40}(?:limit|quota)|rate limit|'
    r'too many (?:messages|mails|emails|connections|recipients)|spam|throttl)', re.IGNORECASE)

Config = namedtuple('Config', 'host port user password sender reply_to security')


class EmailError(ValueError):
    """Строка с адресами не разобрана: в `token` — что именно не похоже на адрес."""

    def __init__(self, message, token=None):
        super().__init__(message)
        self.token = token


class MailError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


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


def _env(env, name):
    return str(env.get(name) or '').strip()


def config(env=None):
    """Настройка ящика из окружения либо None — почта не подключена."""
    env = os.environ if env is None else env
    host = _env(env, 'PAYMENTS_SMTP_HOST')
    user = _env(env, 'PAYMENTS_SMTP_USER')
    password = str(env.get('PAYMENTS_SMTP_PASSWORD') or '')
    sender = _env(env, 'PAYMENTS_MAIL_FROM') or user
    if not host or not sender or bool(user) != bool(password):
        return None
    try:
        port = int(_env(env, 'PAYMENTS_SMTP_PORT') or 465)
    except ValueError:
        return None
    security = _env(env, 'PAYMENTS_SMTP_SECURITY').lower() or ('ssl' if port == 465 else 'starttls')
    if security not in ('ssl', 'starttls', 'none'):
        return None
    # Без шифрования пароль и письма шли бы по сети открытым текстом: такое
    # разрешено только почтовому серверу на этой же машине (стенд).
    if security == 'none' and host not in _LOCAL_HOSTS:
        return None
    if not user and security != 'none':
        return None
    _name, address = parseaddr(sender)
    if not address or not _EMAIL_RE.fullmatch(address.lower()):
        return None
    return Config(host=host, port=port, user=user, password=password, sender=sender,
                  reply_to=_env(env, 'PAYMENTS_MAIL_REPLY_TO') or None, security=security)


def ready(env=None):
    return config(env) is not None


def sender_of(cfg, display_name=None):
    """(имя, адрес) отправителя, как их увидит поставщик; без почты — адреса нет."""
    if cfg is None:
        return display_name or '', None
    own_name, address = parseaddr(cfg.sender)
    return own_name or display_name or '', address


def build_message(cfg, *, to, subject, text, html, display_name=None):
    """Письмо: текст и HTML одним сообщением. Имя отправителя — название нашей
    компании (у разных компаний свои письма), если в PAYMENTS_MAIL_FROM не
    задано своё."""
    own_name, address = parseaddr(cfg.sender)
    local, _at, domain = address.partition('@')
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = Address(display_name=own_name or display_name or '', username=local, domain=domain)
    message['To'] = to if isinstance(to, str) else ', '.join(to)
    if cfg.reply_to:
        message['Reply-To'] = cfg.reply_to
    message['Date'] = formatdate(usegmt=True)
    message['Message-ID'] = make_msgid(domain=domain or None)
    # Письмо отправлено программой: автоответы «я в отпуске» сюда не нужны.
    message['Auto-Submitted'] = 'auto-generated'
    message['X-Auto-Response-Suppress'] = 'OOF, AutoReply'
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype='html')
    return message


def _reply_text(exc):
    """Ответ сервера словами: код и первая строка без служебного мусора."""
    code = getattr(exc, 'smtp_code', None)
    raw = getattr(exc, 'smtp_error', b'') or b''
    text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
    text = ' '.join(text.split())[:300]
    return ('%s %s' % (code, text)).strip() if code else (text or exc.__class__.__name__)


def refusal_kind(code, text):
    """Чей это отказ: ящика (throttled), временный (temporary) или адреса (permanent)."""
    if _SENDER_TROUBLE_RE.search(str(text or '')):
        return 'throttled'
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
    return 'temporary' if 400 <= code < 500 else 'permanent'


class Transport:
    """Одно соединение с почтовым сервером на весь прогон: войти один раз и
    отправить несколько писем. `with Transport(cfg) as smtp: smtp.send(message)`."""

    def __init__(self, cfg, timeout=TIMEOUT_SECONDS):
        self.cfg = cfg
        self.timeout = timeout
        self.smtp = None

    def __enter__(self):
        cfg = self.cfg
        try:
            if cfg.security == 'ssl':
                self.smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=self.timeout,
                                             context=ssl.create_default_context())
            else:
                self.smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=self.timeout)
                self.smtp.ehlo()
                if cfg.security == 'starttls':
                    self.smtp.starttls(context=ssl.create_default_context())
                    self.smtp.ehlo()
            if cfg.user:
                self.smtp.login(cfg.user, cfg.password)
        except smtplib.SMTPAuthenticationError as exc:
            self.close()
            raise MailError('transport', 'Почта не приняла логин или пароль приложения (%s)' % _reply_text(exc))
        except (smtplib.SMTPException, OSError) as exc:
            self.close()
            raise MailError('transport', 'Нет связи с почтовым сервером %s:%s — %s'
                            % (cfg.host, cfg.port, _reply_text(exc) if isinstance(exc, smtplib.SMTPException)
                               else exc.__class__.__name__))
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    @property
    def closed(self):
        """Связь оборвалась посреди прогона — остальные письма ждут следующего."""
        return self.smtp is None

    def deadline(self, seconds):
        """Сколько ждать сервер на следующей операции: прогон отдаёт остаток бюджета."""
        sock = getattr(self.smtp, 'sock', None)
        if sock is not None:
            sock.settimeout(max(1.0, float(seconds)))

    def close(self):
        smtp, self.smtp = self.smtp, None
        if smtp is None:
            return
        try:
            if getattr(smtp, 'sock', None) is not None:
                smtp.sock.settimeout(QUIT_SECONDS)
            smtp.quit()
        except Exception:  # noqa: BLE001 — соединение и так закрывается
            try:
                smtp.close()
            except Exception:  # noqa: BLE001
                pass

    def _drop_if_closed(self):
        # На ответ 421 smtplib сам закрывает сокет: следующее письмо не должно
        # тратить попытку на «соединение закрыто».
        if self.smtp is not None and getattr(self.smtp, 'sock', None) is None:
            self.close()

    def send(self, message):
        """Отправить письмо. Возвращает Message-ID; не вышло — MailError."""
        if self.smtp is None:
            raise MailError('temporary', 'Соединение с почтовым сервером закрыто')
        try:
            refused = self.smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            self._drop_if_closed()
            code, reply = next(iter(exc.recipients.values()), (None, b''))
            detail = ' '.join((reply.decode('utf-8', 'replace') if isinstance(reply, bytes) else str(reply)).split())
            raise MailError(refusal_kind(code, detail), 'Сервер не принял адрес: %s %s' % (code, detail[:300]))
        except smtplib.SMTPSenderRefused as exc:
            self._drop_if_closed()
            raise MailError('transport', 'Ящик не пускает отправителя %s (%s)' % (exc.sender, _reply_text(exc)))
        except smtplib.SMTPResponseException as exc:
            self._drop_if_closed()
            reply = _reply_text(exc)
            raise MailError(refusal_kind(exc.smtp_code, reply), 'Сервер не принял письмо: %s' % reply)
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPException, socket.timeout, OSError) as exc:
            self.close()
            raise MailError('temporary', 'Связь с почтовым сервером оборвалась (%s)' % exc.__class__.__name__)
        if refused:
            code, reply = next(iter(refused.values()))
            detail = ' '.join((reply.decode('utf-8', 'replace') if isinstance(reply, bytes) else str(reply)).split())
            raise MailError(refusal_kind(code, detail), 'Сервер не принял адрес: %s %s' % (code, detail[:300]))
        return message['Message-ID']
