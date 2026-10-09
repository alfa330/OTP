"""Номера банковских карт: хранение, маска, проверка. Ни базы, ни Flask.

ТЗ «Закуп и оплата», п. 5.2 и п. 13: «для выполнения перевода в заявке хранится
полный номер карты. Полный номер доступен финансовому отделу и иным ролям
только в соответствии с правами доступа».

Отсюда три правила, которые держит этот модуль.

* **В базе нет открытого номера.** Номер шифруется перед записью (Fernet:
  AES-128-CBC + HMAC) и лежит в `card_number_enc`; рядом — последние четыре
  цифры для подписи «•••• 1234». Резервная копия базы, выгрузка таблицы и
  read-only доступ к проду номер не раскрывают.
* **Полный номер уходит наружу одной ручкой** (`/requests/<id>/card`), по
  запросу и только тем, кому положено (payments/access.can_view_card_number).
  В списках, на досках, в карточке и в Excel — только маска.
* **Ключ не лежит в коде.** `PAYMENTS_CARD_KEY` из окружения; пока его не
  завели, ключ выводится из `JWT_SECRET` — он обязателен для старта сервера, так
  что раздел работает без новой настройки.

Про смену ключей. Читаются номера обоими ключами (своим и производным от
`JWT_SECRET`), пишутся — главным: своим, если он есть. Поэтому свой ключ можно
завести когда угодно: старые номера остаются читаемыми, а при следующем старте
сервера перешифровываются своим ключом (`legacy.rotate_cards`). Менять
`JWT_SECRET` безопасно только после этого: без своего ключа смена секрета
сделает сохранённые номера нечитаемыми.
"""

import base64
import hashlib
import os

MIN_DIGITS = 13
MAX_DIGITS = 19
MASK_BULLETS = '••••'


class CardKeyMissing(Exception):
    """Нет ни PAYMENTS_CARD_KEY, ни JWT_SECRET — шифровать нечем."""


def _from_phrase(text):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(text.encode('utf-8')).digest()))


def _own_key():
    """Свой ключ из `PAYMENTS_CARD_KEY`: готовый ключ Fernet либо фраза."""
    from cryptography.fernet import Fernet

    raw = (os.getenv('PAYMENTS_CARD_KEY') or '').strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode('ascii'))
    except Exception:  # noqa: BLE001 — задана фраза, а не готовый ключ Fernet
        return _from_phrase(raw)


def _derived_key():
    secret = (os.getenv('JWT_SECRET') or '').strip()
    return _from_phrase('payments-card-number:' + secret) if secret else None


def _keys():
    """Ключи по старшинству: первым шифруют, читают любым."""
    keys = [key for key in (_own_key(), _derived_key()) if key]
    if not keys:
        raise CardKeyMissing('Не задан ключ для номеров карт (PAYMENTS_CARD_KEY)')
    return keys


def _fernet():
    from cryptography.fernet import MultiFernet

    return MultiFernet(_keys())


def has_own_key():
    """Заведён ли свой ключ (`PAYMENTS_CARD_KEY`), а не только производный."""
    return bool((os.getenv('PAYMENTS_CARD_KEY') or '').strip())


def key_ready():
    try:
        _fernet()
        return True
    except Exception:  # noqa: BLE001
        return False


def digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def problem(value):
    """Что не так с номером — словами; '' — номер годится.

    Проверяется только длина: у номера карты от 13 до 19 цифр. Контрольную
    цифру (алгоритм Луна) подсказывает форма, но заявку она не останавливает —
    отказ на редкой карте без контрольной цифры некому было бы обойти.
    """
    number = digits(value)
    if not number:
        return 'Укажите номер карты'
    if not MIN_DIGITS <= len(number) <= MAX_DIGITS:
        return 'Номер карты: от %s до %s цифр' % (MIN_DIGITS, MAX_DIGITS)
    return ''


def encrypt(value):
    """Номер → строка для `card_number_enc`. Пустой номер — None."""
    number = digits(value)
    if not number:
        return None
    return _fernet().encrypt(number.encode('ascii')).decode('ascii')


def decrypt(token):
    """Строка из базы → номер цифрами. None — нечего или нечем расшифровать
    (сменили ключ): раздел тогда показывает маску, а не падает."""
    if not token:
        return None
    try:
        return _fernet().decrypt(str(token).encode('ascii')).decode('ascii')
    except Exception:  # noqa: BLE001 — InvalidToken, нет ключа
        return None


def rotated(token):
    """Тот же номер, зашифрованный главным ключом, — если сейчас он зашифрован
    другим (завели свой `PAYMENTS_CARD_KEY`). None — перешифровывать нечего:
    номер уже под главным ключом либо не читается вовсе."""
    if not token:
        return None
    raw = str(token).encode('ascii')
    try:
        _keys()[0].decrypt(raw)
        return None
    except Exception:  # noqa: BLE001 — зашифровано не главным ключом
        number = decrypt(token)
    return encrypt(number) if number else None


def last4(value):
    number = digits(value)
    return number[-4:] if len(number) >= 4 else number


def mask(tail):
    """«•••• 1234» по последним четырём цифрам; без цифр — пусто."""
    tail = digits(tail)
    return '%s %s' % (MASK_BULLETS, tail) if tail else ''


def pretty(value):
    """Полный номер группами по четыре: «4400 4301 2345 6789»."""
    number = digits(value)
    return ' '.join(number[index:index + 4] for index in range(0, len(number), 4))
