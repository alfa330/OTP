"""Единая точка вызова модели (сырой httpx, без SDK — см. заголовок evaluator.py).
Структурный вывод через output_config.format (json_schema). Используется оценщиком,
формулировкой разборов и пакетной оценкой — правки протокола API делаются здесь один раз.

Провайдеров три: Anthropic (Claude), Vertex (Gemini) и Z.ai (GLM). Выбор — по имени
модели, а не по отдельному флагу, поэтому вызывающему коду достаточно передать `model`;
всё, что не Claude, уходит в call_qa/providers.py. Так подпись оценки (модель в
evaluation_fingerprint) и фактический адресат запроса не могут разойтись.

Форма тела Anthropic здесь не просто «одна из трёх» — она общий контракт: в ней
замораживаются заявки батча и в неё укладывают ответ остальные провайдеры."""
from __future__ import annotations
import json
import time

import httpx

from . import config
from . import providers

_API_URL = "https://api.anthropic.com/v1/messages"
BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"


def _headers() -> dict:
    key = config.anthropic_key()
    if not key:
        raise RuntimeError("нет ключа Claude (CLAUDE_API_KEY / ANTHROPIC_API_KEY)")
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}


def build_body(*, model, system, user, schema, max_tokens=8000, cache_system=False,
               cache_ttl=None, effort=None, thinking=None) -> dict:
    """Тело запроса /v1/messages. cache_system=True вешает prompt-cache на системный блок
    (повторяющийся промпт: и в обычных вызовах, и в батче — скидки складываются).

    cache_ttl задаёт время жизни записи кеша ('5m' по умолчанию у API, '1h' — дороже
    на запись, 2x против 1.25x, но переживает длинный прогон). Для батча это решающе:
    Batch API не гарантирует порядок обработки, поэтому сортировка заявок по
    направлению не спасает — при 5 минутах запись протухает раньше, чем придёт
    следующий звонок того же направления, и системный блок пишется заново почти
    на каждом звонке (замер 2026-08: попаданий 25%, 27 записей на 9 чтений).

    user — строка или список content-блоков (для картинок: {"type":"image", ...}).
    effort/thinking переопределяют дефолты для дешёвых вспомогательных вызовов
    (описание вложений): рассуждать над картинкой не нужно, а effort='high'
    удваивал бы её цену."""
    if providers.provider_for(model) != providers.ANTHROPIC:
        return providers.build_body(
            model=model, system=system, user=user, schema=schema, max_tokens=max_tokens,
            cache_system=cache_system, cache_ttl=cache_ttl, effort=effort, thinking=thinking)
    sys_block = {"type": "text", "text": system}
    if cache_system:
        sys_block["cache_control"] = {"type": "ephemeral"}
        if cache_ttl:
            sys_block["cache_control"]["ttl"] = cache_ttl
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [sys_block],
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "effort": effort or config.CLAUDE_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
    }
    if thinking is not None:
        body["thinking"] = thinking
    return body


def parse_message(message: dict) -> dict:
    """Ответ /v1/messages → распарсенный dict по json_schema."""
    text = next((b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"), "")
    return json.loads(text)


def post_body(body: dict, *, timeout=120.0, include_meta=False) -> dict:
    if body.get("_provider"):
        return providers.post_body(body, timeout=timeout, include_meta=include_meta)
    started = time.perf_counter()
    r = httpx.post(_API_URL, json=body, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    message = r.json()
    parsed = parse_message(message)
    if include_meta:
        parsed["_llm_meta"] = {
            "request_id": message.get("id"),
            "model": message.get("model") or body.get("model"),
            "stop_reason": message.get("stop_reason"),
            "usage": message.get("usage") or {},
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    return parsed


def claude_json(*, model, system, user, schema, max_tokens=8000, timeout=120.0, cache_system=False) -> dict:
    """Один синхронный вызов Claude → распарсенный dict по json_schema."""
    return post_body(build_body(model=model, system=system, user=user, schema=schema,
                                max_tokens=max_tokens, cache_system=cache_system), timeout=timeout)
