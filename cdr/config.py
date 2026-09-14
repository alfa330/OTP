# -*- coding: utf-8 -*-
"""Доступы раздела «Касания» со стороны портала. Секретов в коде нет.

Портал к станции не ходит — у него единственный секрет — общий с мостом токен
`CDR_AGENT_TOKEN`, которым мост подписывает свои запросы. Адрес станции и всё
остальное живёт на стороне моста (`cdr_bridge/`), внутри корпоративной сети.

Форма та же, что у call_qa/config.py и voice_trainer/env_local.py: приоритет у
окружения (на проде переменные заводятся в дашборде Render), а `.env.codex.local`
— фолбэк для локальных прогонов. Ловушка, которая стоила отдельного разбора:
джоба крутится внутри прод-приложения, а оно читает ТОЛЬКО окружение, поэтому
ключ, положенный лишь в dev-файл, на проде не существует.
"""

import functools
import os

_ENV_FILE = os.path.join(os.path.dirname(__file__), os.pardir, '.env.codex.local')

TOKEN_ENV = 'CDR_AGENT_TOKEN'

# Открытые ключи мостов, через запятую. Задан хотя бы один — токен больше не
# принимается вовсе: два способа входа одновременно — это два способа ошибиться.
KEYS_ENV = 'CDR_AGENT_KEYS'


@functools.lru_cache(maxsize=1)
def _dev_env():
    """Разбор .env.codex.local. Многострочные значения здесь не нужны — ключ
    раздела однострочный, — поэтому парсер простой и без сюрпризов."""
    out = {}
    try:
        with open(_ENV_FILE, encoding='utf-8-sig') as handle:
            lines = handle.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def env(key, default=None):
    """os.environ имеет приоритет; иначе — dev-файл."""
    return os.environ.get(key) or _dev_env().get(key) or default


def agent_token():
    return (env(TOKEN_ENV) or '').strip()


def agent_keys_raw():
    """Открытые ключи мостов как записаны в настройке. Разбор — в agent_auth:
    здесь только чтение, чтобы этот модуль не тянул криптографию."""
    return (env(KEYS_ENV) or '').strip()


def bridge_configured():
    """Есть ли чем проверять мост — ключами или, по-старому, токеном. Нет ни
    того ни другого — ручки моста отвечают 503 с внятным текстом, а не пускают
    кого угодно: пустой токен не должен совпадать с пустым заголовком."""
    return bool(agent_keys_raw() or agent_token())
