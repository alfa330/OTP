"""Чтение секретов для прототипа тренажёра.

Отдельный загрузчик, а не call_qa.config, по одной причине: наивный построчный
парсер .env.codex.local обрезает многострочные значения. GOOGLE_APPLICATION_CREDENTIALS_CONTENT
занимает 15 строк, и построчное чтение отдаёт «{» вместо JSON — сервис-аккаунт
молча оказывается битым. Здесь продолжение строки берётся, только пока не закрыты
фигурные скобки, поэтому однострочные ключи не склеиваются с комментариями и
пустыми строками (иначе к SONIOX_API_KEY приклеивался хвост и ключ становился
длиннее настоящего).
"""
from __future__ import annotations

import functools
import io
import json
import os
import re

_ENV_FILE = os.path.join(os.path.dirname(__file__), os.pardir, ".env.codex.local")


@functools.lru_cache(maxsize=1)
def _file_env() -> dict:
    try:
        lines = io.open(_ENV_FILE, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return {}
    out, i = {}, 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)=(.*)$", lines[i])
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2)
        probe = val.strip().strip('"').strip("'")
        if probe.startswith("{") and probe.count("{") != probe.count("}"):
            buf, depth, i = [val], probe.count("{") - probe.count("}"), i + 1
            while i < len(lines) and depth > 0:
                buf.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            out[key] = "\n".join(buf)
        else:
            out[key] = val
            i += 1
    return {k: v.strip().strip('"').strip("'") for k, v in out.items()}


def env(key: str, default=None):
    """os.environ имеет приоритет; иначе — dev-файл."""
    return os.environ.get(key) or _file_env().get(key) or default


def google_sa_info() -> dict | None:
    raw = env("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
    if not raw:
        return None
    raw = raw.lstrip()
    if raw[:1] in ("'", '"'):
        raw = raw[1:]
    return json.JSONDecoder().raw_decode(raw[raw.find("{"):])[0]
