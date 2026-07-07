"""Единая точка вызова Claude (сырой httpx, без anthropic SDK — см. заголовок evaluator.py).
Структурный вывод через output_config.format (json_schema). Используется оценщиком и
формулировкой разборов — правки протокола API делаются здесь один раз."""
from __future__ import annotations
import json

import httpx

from . import config

_API_URL = "https://api.anthropic.com/v1/messages"


def claude_json(*, model, system, user, schema, max_tokens=8000, timeout=120.0, cache_system=False) -> dict:
    """Один вызов Claude → распарсенный dict по json_schema.
    cache_system=True вешает prompt-cache на системный блок (для повторяющихся промптов)."""
    key = config.anthropic_key()
    if not key:
        raise RuntimeError("нет ключа Claude (CLAUDE_API_KEY / ANTHROPIC_API_KEY)")
    sys_block = {"type": "text", "text": system}
    if cache_system:
        sys_block["cache_control"] = {"type": "ephemeral"}
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [sys_block],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = httpx.post(_API_URL, json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
    return json.loads(text)
