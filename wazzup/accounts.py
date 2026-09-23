"""Реестр аккаунтов Wazzup отдела продаж.

Единственное место, где перечислены аккаунты и их отличия. Бэкенд валидирует
параметр `account` по этому реестру, фронт получает ярлыки и воркспейсы из
`/api/wazzup/accounts` — константы в компоненте не дублируются.

Ключи API и токены вебхуков здесь НЕ лежат: только имена переменных
окружения, значения читаются в момент обращения.
"""
import hmac
import os

DEFAULT_ACCOUNT = 'op'

# Порядок — порядок в переключателе раздела.
ACCOUNTS = {
    'op': {
        'key': 'op',
        'label': 'Верификаторы',
        # воркспейс веб-приложения Wazzup: ссылки «В Wazzup» ведут в него
        'workspace': '6757-7677',
        'api_key_env': 'WAZZUP_OP_API_KEY',
        'webhook_token_env': 'WAZZUP_WEBHOOK_TOKEN',
        # сообщения приходят вебхуком Wazzup на наш приёмник
        'source': 'webhook',
    },
    'potok': {
        'key': 'potok',
        'label': 'Поток',
        'workspace': '2682-1109',
        'account_id': '26821109',
        'api_key_env': 'WAZZUP_OP_POTOK_API_KEY',
        'webhook_token_env': 'WAZZUP_POTOK_WEBHOOK_TOKEN',
        # вебхук аккаунта занят backend.yataxi.kz — переписку забираем сами
        # внутренним API окна чатов (potok_sync), вебхук принимаем как
        # дополнительный источник, если его нам когда-нибудь пересылать начнут
        'source': 'pull',
        # пользователь Wazzup, от имени которого выписывается окно чатов:
        # у него должен быть доступ ко ВСЕМ чатам канала (у части людей список
        # урезан — см. potok_client)
        'viewer_user_env': 'WAZZUP_OP_POTOK_VIEWER_USER_ID',
        'viewer_user_default': '166',
    },
}
ORDER = ('op', 'potok')


def normalize_account(value, default=DEFAULT_ACCOUNT):
    """Ключ аккаунта из параметра запроса; пусто → аккаунт по умолчанию,
    незнакомое значение → None (маршрут отвечает 400)."""
    key = str(value or '').strip().lower()
    if not key:
        return default
    return key if key in ACCOUNTS else None


def api_key(account):
    """Ключ пользовательского API v3 аккаунта из окружения ('' — не задан)."""
    meta = ACCOUNTS[account]
    return (os.getenv(meta['api_key_env']) or '').strip()


def webhook_token(account):
    meta = ACCOUNTS[account]
    return (os.getenv(meta['webhook_token_env']) or '').strip()


def account_by_webhook_token(token):
    """Аккаунт по секретному сегменту пути вебхука; None — токен никому не
    принадлежит. Сравнение постоянное по времени, как и раньше в приёмнике."""
    token = str(token or '')
    for key in ORDER:
        expected = webhook_token(key)
        if expected and hmac.compare_digest(token, expected):
            return key
    return None


def viewer_user_id(account):
    meta = ACCOUNTS[account]
    env_name = meta.get('viewer_user_env')
    value = (os.getenv(env_name) or '').strip() if env_name else ''
    return value or meta.get('viewer_user_default') or ''


def public_accounts():
    """То, что уходит на фронт: без имён переменных окружения."""
    return [{'key': ACCOUNTS[k]['key'], 'label': ACCOUNTS[k]['label'],
             'workspace': ACCOUNTS[k]['workspace'], 'source': ACCOUNTS[k]['source']}
            for k in ORDER]
