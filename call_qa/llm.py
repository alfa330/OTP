"""Единая точка вызова Claude (сырой httpx, без anthropic SDK — см. заголовок evaluator.py).
Структурный вывод через output_config.format (json_schema). Используется оценщиком,
формулировкой разборов и пакетной оценкой — правки протокола API делаются здесь один раз."""
from __future__ import annotations
import json
import time

import httpx

from . import config

_API_URL = "https://api.anthropic.com/v1/messages"
BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"


def _headers() -> dict:
    key = config.anthropic_key()
    if not key:
        raise RuntimeError("нет ключа Claude (CLAUDE_API_KEY / ANTHROPIC_API_KEY)")
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}


def build_body(*, model, system, user, schema, max_tokens=8000, cache_system=False) -> dict:
    """Тело запроса /v1/messages. cache_system=True вешает prompt-cache на системный блок
    (повторяющийся промпт: и в обычных вызовах, и в батче — скидки складываются)."""
    sys_block = {"type": "text", "text": system}
    if cache_system:
        sys_block["cache_control"] = {"type": "ephemeral"}
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [sys_block],
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "effort": config.CLAUDE_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
    }


def parse_message(message: dict) -> dict:
    """Ответ /v1/messages → распарсенный dict по json_schema."""
    text = next((b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"), "")
    return json.loads(text)


def post_body(body: dict, *, timeout=120.0, include_meta=False) -> dict:
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
