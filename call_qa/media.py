"""Расшифровка вложений чата: изображения → Claude vision, голосовые → Soniox.

Зачем: транскрипт эпизода Wazzup содержит только заглушки («[фото]»,
«[голосовое]»), а у Верификаторов клиент присылает документы фотографиями и
отвечает голосом. Оценивать переписку по заглушкам — значит не видеть половину
диалога, поэтому перед оценкой вложения превращаются в текст.

Инварианты:

* Одно вложение расшифровывается ОДИН раз. Результат живёт в
  ``wz_media_annotations`` и переживает 45-дневный ретеншн ``wazzup_messages``:
  переоценка эпизода через полгода даёт тот же текст, что и первая оценка.
* Идентичность = (message_id, provider, model, config_hash, source_hash).
  Смена модели или промпта — это новая расшифровка, а не перезапись старой.
* Неуспешная попытка сохраняется со статусом и повторяется при следующем
  прогоне; успешная — не перезаписывается никогда.
* Недоступное вложение НЕ молчит: в транскрипт попадает явная пометка
  «не удалось получить», чтобы модель не штрафовала оператора за пустоту.

ПДн: фотографии Верификаторов — это в основном документы, поэтому извлечённый
текст относится к тому же классу данных, что и сам транскрипт, и хранится там
же (прод-БД). Ничего дополнительного (лиц, геометрии, эмбеддингов) не просим.

Claude вызывается сырым httpx через :mod:`call_qa.llm` — как и остальной
call_qa, без anthropic SDK (он тянет pydantic v2, конфликтующий с aiogram 2.x).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
from psycopg2.extras import Json

from . import config
from . import llm
from .asr import soniox
from .evaluation.fingerprint import content_hash

# Версия промпта/разметки описания. Меняется вместе со смыслом результата —
# входит в config_hash, поэтому старые описания остаются, но не используются.
ANNOTATOR_VERSION = "wz-media-v1"

_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "visible_text": {"type": "string"},
        "kind": {"type": "string",
                 "enum": ["document", "screenshot", "photo", "receipt", "other", "unreadable"]},
    },
    "required": ["description", "visible_text", "kind"],
    "additionalProperties": False,
}

_IMAGE_SYSTEM = """Ты помощник, который описывает вложения из рабочей переписки службы поддержки.

Опиши изображение ФАКТИЧЕСКИ и коротко, по-русски. Твоя задача — заменить
картинку текстом так, чтобы проверяющий понял, что именно прислали.

Правила:
1. description — 1-3 предложения: что на изображении, что это за объект/документ.
2. visible_text — читаемый текст с изображения дословно (номера, суммы,
   даты, ФИО, статусы). Если текста нет — пустая строка. Если текста ОЧЕНЬ
   много (плотный документ), выпиши самое значимое и оборви на ~1500 символах:
   ответ обязан помещаться целиком, обрезанный ответ считается неудачей.
3. kind — тип вложения.
4. Не оценивай работу оператора, не давай советов, не делай выводов о качестве
   обслуживания. Только содержание изображения.
5. Если изображение нечитаемо (размыто, обрезано, пустое) — kind='unreadable' и
   честно скажи об этом в description. Не додумывай содержание.
"""

_DOCUMENT_SYSTEM = """Ты помощник, который описывает документы из рабочей переписки службы поддержки.

Опиши PDF ФАКТИЧЕСКИ и коротко, по-русски. Твоя задача — заменить файл текстом
так, чтобы проверяющий понял, что именно прислали, не открывая документ.

Правила:
1. description — 1-3 предложения: что это за документ, кем и когда выдан, чему
   посвящён. Если страниц несколько — скажи сколько и что на них.
2. visible_text — ключевые данные дословно (номера, ИИН/БИН, суммы, даты, ФИО,
   статусы, реквизиты). Выпиши самое значимое и оборви на ~2000 символах: ответ
   обязан помещаться целиком, обрезанный ответ считается неудачей.
3. kind — тип вложения.
4. Не оценивай работу оператора, не давай советов, не делай выводов о качестве
   обслуживания. Только содержание документа.
5. Если документ нечитаем (пустой скан, битый файл, только подпись) —
   kind='unreadable' и честно скажи об этом. Не додумывай содержание.
"""

_MEDIA_UNAVAILABLE = "не удалось получить вложение"


# ── идентичности ─────────────────────────────────────────────────────────────

def source_identity(*, content_uri: str, media_type: str) -> str:
    """Идентичность объекта-вложения. Байты недоступны до скачивания, поэтому
    ключом кэша служит ссылка Wazzup + заявленный тип (ссылка неизменяема)."""
    return content_hash({"version": 1, "content_uri": str(content_uri or ""),
                         "media_type": str(media_type or "")})


def image_config() -> dict:
    return {"annotator": ANNOTATOR_VERSION, "provider": "anthropic",
            "model": config.CLAUDE_MODEL_VISION, "effort": config.CLAUDE_VISION_EFFORT,
            "max_tokens": config.VISION_MAX_TOKENS, "schema": _IMAGE_SCHEMA,
            "system": _IMAGE_SYSTEM, "thinking": "disabled"}


def document_config() -> dict:
    return {"annotator": ANNOTATOR_VERSION, "provider": "anthropic",
            "model": config.CLAUDE_MODEL_VISION, "effort": config.CLAUDE_VISION_EFFORT,
            "max_tokens": config.DOCUMENT_MAX_TOKENS, "schema": _IMAGE_SCHEMA,
            "system": _DOCUMENT_SYSTEM, "thinking": "disabled"}


def audio_config() -> dict:
    cfg = dict(soniox_asr_config())
    cfg["annotator"] = ANNOTATOR_VERSION
    return cfg


def soniox_asr_config() -> dict:
    return {"provider": "soniox", "model": config.SONIOX_MODEL,
            "language_hints": list(config.SONIOX_LANGS),
            "language_identification": True, "speaker_diarization": False}


def _uri_extension(content_uri: str) -> str:
    """Расширение из ссылки Wazzup: она несёт ?filename=...pdf."""
    uri = str(content_uri or "")
    name = uri
    if "filename=" in uri:
        name = uri.split("filename=", 1)[1].split("&", 1)[0]
    else:
        name = uri.split("?", 1)[0]
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 and len(name) - dot <= 6 else ""


def _kind_of(media_type: str, content_uri: str | None = None) -> str:
    """Чем читать вложение.

    Тип из Wazzup — не последняя инстанция: 'document' у них означает «файл», и
    там встречаются и PDF (99%), и обычные картинки, отправленные как документ, и
    сертификаты. Поэтому для 'document' решает расширение файла."""
    mt = str(media_type or "").strip().lower()
    if mt in config.MEDIA_IMAGE_TYPES:
        return "image"
    if mt in config.MEDIA_AUDIO_TYPES:
        return "audio"
    if mt in config.MEDIA_DOCUMENT_TYPES:
        ext = _uri_extension(content_uri)
        if ext == ".pdf":
            return "document"
        if ext in _EXT_MEDIA_TYPES:      # .jpg/.png, присланные как «документ»
            return "image"
        return "file"                    # docx, сертификаты и прочее — читать нечем
    if mt == "video":
        return "video"
    return "unknown"


def annotatable(media_type: str, content_uri: str | None = None) -> bool:
    """Расшифровываем только то, что реально можем прочитать."""
    return _kind_of(media_type, content_uri) in ("image", "audio", "document")


# ── хранилище ────────────────────────────────────────────────────────────────

def _lookup(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Готовые расшифровки для набора вложений: {(message_id, source_hash): row}."""
    if not rows:
        return {}
    keys = [(r["message_id"], r["provider"], r["model"], r["config_hash"], r["source_hash"])
            for r in rows]
    out: dict[tuple[str, str], dict] = {}
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT message_id, source_hash, status, annotation, error
                     FROM wz_media_annotations
                    WHERE (message_id, provider, model, config_hash, source_hash)
                          IN (SELECT * FROM unnest(%s::text[], %s::text[], %s::text[],
                                                   %s::bpchar[], %s::bpchar[]))""",
                ([k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys],
                 [k[3] for k in keys], [k[4] for k in keys]))
            for message_id, source_hash, status, annotation, error in cur.fetchall():
                out[(message_id, source_hash)] = {
                    "status": status, "annotation": annotation, "error": error}
    except Exception as exc:
        # Схема ещё не применена / нет доступа: оценка продолжается по заглушкам.
        logging.warning("wz media: кэш расшифровок недоступен (%s)", exc)
        return {}
    finally:
        conn.close()
    return out


_INSERT_ANNOTATION = """INSERT INTO wz_media_annotations
       (message_id, channel_id, chat_id, media_kind, source_hash,
        provider, model, config_hash, status, annotation, error,
        source_bytes, latency_ms, input_tokens, output_tokens, estimated_cost)
     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
     ON CONFLICT (message_id, provider, model, config_hash, source_hash)
     DO UPDATE SET status=EXCLUDED.status, annotation=EXCLUDED.annotation,
                   error=EXCLUDED.error, source_bytes=EXCLUDED.source_bytes,
                   latency_ms=EXCLUDED.latency_ms,
                   input_tokens=EXCLUDED.input_tokens,
                   output_tokens=EXCLUDED.output_tokens,
                   estimated_cost=EXCLUDED.estimated_cost,
                   updated_at=now()
      WHERE wz_media_annotations.status <> 'ready'"""


def _annotation_cost(result: dict, *, batch: bool) -> float | None:
    """Стоимость описания по тарифам из окружения. Нет тарифов — нет цифры
    (выдуманная цена в дашборде хуже её отсутствия)."""
    usage = result.get("usage") or {}
    if not usage:
        return None
    prefix = "CLAUDE_BATCH_" if batch else "CLAUDE_"
    prices = {}
    for key, env_name in (("input_tokens", f"{prefix}INPUT_USD_PER_MTOK"),
                          ("output_tokens", f"{prefix}OUTPUT_USD_PER_MTOK")):
        raw = config.env(env_name)
        if raw is None:
            return None
        prices[key] = float(raw)
    return round(sum(int(usage.get(k) or 0) * prices[k] for k in prices) / 1_000_000, 8)


def _annotation_params(item: dict, result: dict, *, batch: bool) -> tuple:
    return (item["message_id"], item.get("channel_id"), item.get("chat_id"),
            item["media_kind"], item["source_hash"], item["provider"],
            item["model"], item["config_hash"], result["status"],
            result.get("annotation"), (result.get("error") or None),
            result.get("source_bytes"), result.get("latency_ms"),
            (result.get("usage") or {}).get("input_tokens"),
            (result.get("usage") or {}).get("output_tokens"),
            _annotation_cost(result, batch=batch))


@contextmanager
def _store_session(*, batch: bool = True):
    """Одно соединение на серию записей.

    Ночной прогон сохраняет сотни расшифровок подряд; отдельный connect/close на
    каждую строку — лишняя нагрузка на пул прод-БД."""
    conn = config.connect_rw()
    try:
        def write(item: dict, result: dict) -> None:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_ANNOTATION,
                                _annotation_params(item, result, batch=batch))
        yield write
    finally:
        conn.close()


def _store(item: dict, result: dict) -> None:
    """Пишет расшифровку. Успешную запись не перезаписываем (ON CONFLICT ... WHERE)."""
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_INSERT_ANNOTATION,
                            _annotation_params(item, result, batch=False))
    finally:
        conn.close()


# ── загрузка вложения ────────────────────────────────────────────────────────

def _download(url: str, *, limit: int | None = None) -> tuple[bytes, str]:
    """Скачивает вложение с ограничением размера. Ссылки Wazzup публичные
    (store.wazzup24.com, без авторизации) — как их читает и браузер."""
    limit = config.MEDIA_MAX_BYTES if limit is None else limit
    with httpx.stream("GET", url, timeout=config.MEDIA_HTTP_TIMEOUT,
                      follow_redirects=True) as r:
        r.raise_for_status()
        declared = r.headers.get("content-length")
        if declared and int(declared) > limit:
            raise ValueError(f"вложение больше лимита: {declared} > {limit} байт")
        chunks, total = [], 0
        for chunk in r.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise ValueError(f"вложение больше лимита: > {limit} байт")
            chunks.append(chunk)
        return b"".join(chunks), (r.headers.get("content-type") or "").split(";")[0].strip()


_IMAGE_MEDIA_TYPES = {
    "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg",
    "image/png": "image/png", "image/gif": "image/gif", "image/webp": "image/webp",
}
_EXT_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}


def _image_media_type(content_type: str, url: str) -> str:
    """Anthropic принимает только jpeg/png/gif/webp — тип определяем явно."""
    mt = _IMAGE_MEDIA_TYPES.get((content_type or "").lower())
    if mt:
        return mt
    ext = os.path.splitext((url or "").split("?")[0])[1].lower()
    mt = _EXT_MEDIA_TYPES.get(ext)
    if mt:
        return mt
    raise ValueError(f"неподдерживаемый тип изображения: {content_type or ext or '?'}")


def _audio_suffix(content_type: str, url: str) -> str:
    ext = os.path.splitext((url or "").split("?")[0])[1].lower()
    if ext in (".ogg", ".opus", ".oga", ".mp3", ".m4a", ".aac", ".wav", ".mp4", ".amr"):
        return ext
    guess = {"audio/ogg": ".ogg", "audio/opus": ".opus", "audio/mpeg": ".mp3",
             "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/wav": ".wav",
             "audio/x-wav": ".wav", "audio/amr": ".amr"}
    return guess.get((content_type or "").lower(), ".ogg")


# ── распознавание одного вложения ────────────────────────────────────────────

def _vision_thinking() -> dict | None:
    """`thinking: disabled` принимают не все модели.

    Fable/Mythos отвергают явное отключение всегда, Opus 5 — на effort выше
    high. Модель и effort задаются переменными окружения, поэтому решаем по
    факту: если не уверены — не шлём поле вовсе (адаптивный режим работает
    везде и лишь чуть дороже)."""
    model = str(config.CLAUDE_MODEL_VISION or "").lower()
    effort = str(config.CLAUDE_VISION_EFFORT or "").lower()
    if "fable" in model or "mythos" in model:
        return None
    if "opus-5" in model and effort in ("xhigh", "max"):
        return None
    return {"type": "disabled"}


def image_request_body(image_b64: str, media_type: str) -> dict:
    """Тело запроса описания картинки. Одна точка для синхронного вызова и Batch."""
    return llm.build_body(
        model=config.CLAUDE_MODEL_VISION, system=_IMAGE_SYSTEM,
        user=[{"type": "image",
               "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
              {"type": "text", "text": "Опиши это вложение из рабочей переписки."}],
        schema=_IMAGE_SCHEMA, max_tokens=config.VISION_MAX_TOKENS,
        # Системный блок короткий (сотни токенов) и порога кэширования не
        # достигает — cache_control здесь ничего не экономит, только шумит.
        cache_system=False, effort=config.CLAUDE_VISION_EFFORT,
        thinking=_vision_thinking())


def document_request_body(pdf_b64: str) -> dict:
    """Тело запроса описания PDF.

    document-блок Anthropic принимает только application/pdf (и простой текст):
    каждая страница обрабатывается и как текст, и как изображение, поэтому
    сканы и фотографии документов читаются наравне с «текстовыми» PDF."""
    return llm.build_body(
        model=config.CLAUDE_MODEL_VISION, system=_DOCUMENT_SYSTEM,
        user=[{"type": "document",
               "source": {"type": "base64", "media_type": "application/pdf",
                          "data": pdf_b64}},
              {"type": "text", "text": "Опиши этот документ из рабочей переписки."}],
        schema=_IMAGE_SCHEMA, max_tokens=config.DOCUMENT_MAX_TOKENS,
        cache_system=False, effort=config.CLAUDE_VISION_EFFORT,
        thinking=_vision_thinking())


def render_image_annotation(parsed: dict) -> str:
    """Структурный ответ модели → одна строка для транскрипта."""
    desc = str((parsed or {}).get("description") or "").strip()
    text = str((parsed or {}).get("visible_text") or "").strip()
    kind = str((parsed or {}).get("kind") or "").strip()
    parts = [desc or "изображение без описания"]
    if kind and kind != "other":
        parts[0] = f"{parts[0]} (тип: {kind})"
    if text:
        parts.append(f"текст на изображении: «{text}»")
    return "; ".join(parts)


def _annotate_image(item: dict) -> dict:
    import base64
    started = time.perf_counter()
    raw, content_type = _download(item["content_uri"])
    media_type = _image_media_type(content_type, item["content_uri"])
    body = image_request_body(base64.standard_b64encode(raw).decode("ascii"), media_type)
    parsed = llm.post_body(body, timeout=120.0, include_meta=True)
    meta = parsed.pop("_llm_meta", None) or {}
    if str(meta.get("stop_reason") or "") == "max_tokens":
        # Плотный документ не поместился в max_tokens: JSON оборван, и то, что
        # удалось распарсить, — половина текста. Молча выдавать её за описание
        # нельзя: попытка помечается неудачной и повторится с новым лимитом.
        return {"status": "failed",
                "error": f"ответ модели обрезан (max_tokens={config.VISION_MAX_TOKENS})",
                "source_bytes": len(raw), "usage": meta.get("usage") or {},
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    return {"status": "ready", "annotation": render_image_annotation(parsed),
            "source_bytes": len(raw), "usage": meta.get("usage") or {},
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def _annotate_document(item: dict) -> dict:
    """PDF → описание. Не-PDF сюда не попадает (см. _kind_of), но файл всё равно
    проверяется по сигнатуре: расширение в ссылке может врать."""
    import base64
    started = time.perf_counter()
    raw, content_type = _download(item["content_uri"], limit=config.MEDIA_PDF_MAX_BYTES)
    if not raw.startswith(b"%PDF") and "pdf" not in (content_type or "").lower():
        return {"status": "failed", "error": f"файл не PDF ({content_type or '?'})",
                "source_bytes": len(raw),
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    body = document_request_body(base64.standard_b64encode(raw).decode("ascii"))
    parsed = llm.post_body(body, timeout=180.0, include_meta=True)
    meta = parsed.pop("_llm_meta", None) or {}
    if str(meta.get("stop_reason") or "") == "max_tokens":
        return {"status": "failed",
                "error": f"ответ модели обрезан (max_tokens={config.DOCUMENT_MAX_TOKENS})",
                "source_bytes": len(raw), "usage": meta.get("usage") or {},
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    return {"status": "ready", "annotation": render_image_annotation(parsed),
            "source_bytes": len(raw), "usage": meta.get("usage") or {},
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def _annotate_audio(item: dict) -> dict:
    started = time.perf_counter()
    raw, content_type = _download(item["content_uri"])
    suffix = _audio_suffix(content_type, item["content_uri"])
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, f"voice{suffix}")
        with open(path, "wb") as fh:
            fh.write(raw)
        tokens = soniox.transcribe_file(path, diarize=False)
    asm = soniox.assemble(tokens)
    text = " ".join(line["text"] for line in asm["lines"] if line.get("text")).strip()
    if not text:
        return {"status": "failed", "error": "Soniox вернул пустой транскрипт",
                "source_bytes": len(raw),
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    langs = "/".join(asm["languages"].keys()) if asm.get("languages") else ""
    prefix = f"[{langs}] " if langs else ""
    return {"status": "ready", "annotation": f"{prefix}{text}", "source_bytes": len(raw),
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def _annotate_one(item: dict) -> dict:
    try:
        if item["media_kind"] == "image":
            return _annotate_image(item)
        if item["media_kind"] == "document":
            return _annotate_document(item)
        return _annotate_audio(item)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        # 404/410 у Wazzup — файл удалён на их стороне: повторять бессмысленно.
        kind = "unavailable" if status in (403, 404, 410) else "failed"
        return {"status": kind, "error": f"HTTP {status}"}
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}


# ── публичный вход ───────────────────────────────────────────────────────────

def plan(messages: list[dict]) -> list[dict]:
    """Из сообщений эпизода собирает список вложений к расшифровке (в порядке
    появления, не больше MEDIA_MAX_PER_EPISODE)."""
    items = []
    for m in messages:
        uri = m.get("content_uri")
        media_type = m.get("type")
        if not uri or not annotatable(media_type, uri):
            continue
        kind = _kind_of(media_type, uri)
        cfg = {"image": image_config, "document": document_config,
               "audio": audio_config}[kind]()
        items.append({
            "message_id": str(m.get("message_id")),
            "channel_id": m.get("channel_id"), "chat_id": m.get("chat_id"),
            "media_kind": kind, "content_uri": uri,
            "source_hash": source_identity(content_uri=uri, media_type=media_type),
            "provider": "soniox" if kind == "audio" else "anthropic",
            "model": config.SONIOX_MODEL if kind == "audio" else config.CLAUDE_MODEL_VISION,
            "config_hash": content_hash(cfg),
        })
        if len(items) >= config.MEDIA_MAX_PER_EPISODE:
            break
    return items


def annotate(messages: list[dict], *, workers: int = 4,
             allow_remote: bool = True, items: list[dict] | None = None) -> dict[str, dict]:
    """Расшифровки вложений эпизода: {message_id: {status, annotation, error}}.

    Готовые берутся из кэша; отсутствующие считаются (параллельно — открытие
    карточки не должно ждать десять картинок по очереди) и сохраняются.
    ``allow_remote=False`` — только кэш (для путей, где платный вызов запрещён).
    ``items`` — уже посчитанный plan(), чтобы вызывающий и расшифровка работали
    ровно с одним и тем же набором вложений.
    """
    items = plan(messages) if items is None else items
    if not items:
        return {}
    cached = _lookup(items)
    out: dict[str, dict] = {}
    todo = []
    for item in items:
        hit = cached.get((item["message_id"], item["source_hash"]))
        # 'unavailable' — файл удалён на стороне Wazzup; повторная попытка будет
        # такой же 404, поэтому не тратим на неё ни время открытия карточки, ни
        # трафик. Повторяем только 'failed' (сеть/модель моргнули).
        if hit and hit.get("status") in ("ready", "unavailable"):
            out[item["message_id"]] = hit
        elif allow_remote:
            todo.append(item)
        elif hit:
            out[item["message_id"]] = hit
    if len(todo) > config.MEDIA_MAX_INTERACTIVE and allow_remote:
        # Бюджет открытия карточки: остальные вложения останутся нерасшифрованными
        # с честной пометкой, зато карточка откроется, а не отвалится по таймауту.
        # Ночной пакетный прогон расшифрует их без ограничения по времени.
        logging.info("wz media: расшифровываем %s из %s вложений (бюджет открытия карточки)",
                     config.MEDIA_MAX_INTERACTIVE, len(todo))
        todo = todo[:config.MEDIA_MAX_INTERACTIVE]
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            for item, result in zip(todo, pool.map(_annotate_one, todo)):
                try:
                    _store(item, result)
                except Exception:
                    logging.exception("wz media: не удалось сохранить расшифровку %s",
                                      item["message_id"])
                out[item["message_id"]] = {"status": result["status"],
                                           "annotation": result.get("annotation"),
                                           "error": result.get("error")}
    return out


def manifest(messages: list[dict], annotations: dict[str, dict],
             items: list[dict] | None = None) -> list[dict]:
    """Свод расшифровок для fingerprint: смена/появление описания вложения —
    это другой транскрипт, а значит другая оценка, а не тихая подмена."""
    out = []
    for item in (plan(messages) if items is None else items):
        ann = annotations.get(item["message_id"]) or {}
        out.append({
            "message_id": item["message_id"], "media_kind": item["media_kind"],
            "source_hash": item["source_hash"], "provider": item["provider"],
            "model": item["model"], "config_hash": item["config_hash"],
            "status": ann.get("status") or "missing",
            "annotation_hash": content_hash(ann.get("annotation") or ""),
        })
    return out


def annotation_text(media_kind: str, ann: dict | None) -> str:
    """Текст вложения для транскрипта.

    Неудача видна модели явно и отличается от «ещё не расшифровано»: иначе
    оценщик не понял бы, почему содержания нет, и мог бы штрафовать за пустоту."""
    label = {"image": "фото", "audio": "голосовое",
             "document": "документ"}.get(media_kind, "вложение")
    if not ann:
        return f"[{label}: не расшифровано — не оценивать содержание]"
    if ann.get("status") != "ready" or not ann.get("annotation"):
        reason = ann.get("error")
        detail = f": {reason}" if reason else ""
        return f"[{label}: {_MEDIA_UNAVAILABLE}{detail} — не оценивать содержание]"
    if media_kind == "audio":
        return f"[{label}, расшифровка: {ann['annotation']}]"
    if media_kind == "document":
        return f"[{label} (PDF): {ann['annotation']}]"
    return f"[{label}: {ann['annotation']}]"


# ── Batch: описание картинок со скидкой 50% ──────────────────────────────────

def batch_media_requests(items: list[dict]) -> tuple[list[dict], dict]:
    """Готовит Batch-запросы описания картинок и PDF-документов.

    Batch API даёт −50% на ВСЕ токены, включая токены изображений и страниц
    документов, поэтому ночной массовый прогон читает вложения вдвое дешевле
    интерактивного. Возвращает (requests, {custom_id: item}); недокачанные
    вложения сразу отдаются как ошибки во втором элементе (item['_error']).
    """
    import base64
    requests_out, by_id = [], {}
    for idx, item in enumerate(items):
        kind = item["media_kind"]
        custom_id = f"{'doc' if kind == 'document' else 'img'}-{idx}-{item['source_hash'][:16]}"
        try:
            if kind == "document":
                raw, content_type = _download(item["content_uri"],
                                              limit=config.MEDIA_PDF_MAX_BYTES)
                if not raw.startswith(b"%PDF") and "pdf" not in (content_type or "").lower():
                    raise ValueError(f"файл не PDF ({content_type or '?'})")
                media_type = "application/pdf"
            else:
                raw, content_type = _download(item["content_uri"])
                media_type = _image_media_type(content_type, item["content_uri"])
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            item = dict(item, _error={"status": "unavailable" if status in (403, 404, 410)
                                      else "failed", "error": f"HTTP {status}"})
            by_id[custom_id] = item
            continue
        except Exception as exc:
            item = dict(item, _error={"status": "failed",
                                      "error": f"{type(exc).__name__}: {exc}"[:500]})
            by_id[custom_id] = item
            continue
        encoded = base64.standard_b64encode(raw).decode("ascii")
        body = (document_request_body(encoded) if kind == "document"
                else image_request_body(encoded, media_type))
        item = dict(item, _source_bytes=len(raw))
        by_id[custom_id] = item
        requests_out.append({"custom_id": custom_id, "params": body})
    return requests_out, by_id


def _submit_batch(requests_out: list[dict], headers: dict, log) -> str:
    r = httpx.post(llm.BATCHES_URL, json={"requests": requests_out},
                   headers=headers, timeout=600.0)
    r.raise_for_status()
    batch_id = r.json()["id"]
    log(f"wz media: batch описания картинок {batch_id}, запросов {len(requests_out)}")
    return batch_id


def _poll_batch(batch_id: str, headers: dict, *, poll_interval: int, deadline_s: int,
                log) -> dict:
    """Ждёт завершения батча. Сетевые сбои статуса не роняют ожидание (батч живёт
    на стороне Anthropic), но у ожидания есть предел: без него ночной прогон
    молча висел бы вечно на подвисшем батче."""
    started = time.monotonic()
    info: dict = {}
    while True:
        try:
            st = httpx.get(f"{llm.BATCHES_URL}/{batch_id}", headers=headers, timeout=60.0)
            st.raise_for_status()
            info = st.json()
            if info.get("processing_status") == "ended":
                return info
        except Exception as exc:
            log(f"wz media: статус батча {batch_id} недоступен ({exc}) — продолжаю ждать")
        if time.monotonic() - started > deadline_s:
            raise TimeoutError(
                f"батч {batch_id} не завершился за {deadline_s // 60} мин; "
                "результаты доступны 29 дней — прогон можно повторить")
        time.sleep(poll_interval)


def _read_batch_results(info: dict, batch_id: str, headers: dict,
                        by_id: dict, out: dict, conn_store) -> None:
    results_url = info.get("results_url") or f"{llm.BATCHES_URL}/{batch_id}/results"
    with httpx.stream("GET", results_url, headers=headers, timeout=600.0) as stream:
        stream.raise_for_status()
        for line in stream.iter_lines():
            if not line.strip():
                continue
            payload = json.loads(line)
            item = by_id.get(payload.get("custom_id"))
            if not item:
                continue
            result = payload.get("result") or {}
            if result.get("type") != "succeeded":
                stored = {"status": "failed",
                          "error": str(result.get("error") or result.get("type"))[:500]}
            else:
                try:
                    message = result["message"]
                    if str(message.get("stop_reason") or "") == "max_tokens":
                        stored = {"status": "failed",
                                  "error": "ответ модели обрезан (max_tokens)",
                                  "usage": message.get("usage") or {}}
                    else:
                        parsed = llm.parse_message(message)
                        stored = {"status": "ready",
                                  "annotation": render_image_annotation(parsed),
                                  "source_bytes": item.get("_source_bytes"),
                                  "usage": message.get("usage") or {}}
                except Exception as exc:
                    stored = {"status": "failed",
                              "error": f"{type(exc).__name__}: {exc}"[:500]}
            try:
                conn_store(item, stored)
            except Exception:
                logging.exception("wz media: не удалось сохранить batch-расшифровку %s",
                                  item["message_id"])
            out[item["message_id"]] = {"status": stored["status"],
                                       "annotation": stored.get("annotation"),
                                       "error": stored.get("error")}


def annotate_media_batch(items: list[dict], *, poll_interval: int = 30,
                         log=logging.info, resume=None, remember=None) -> dict[str, dict]:
    """Описывает картинки и PDF через Batch API и сохраняет результаты.

    Возвращает {message_id: {status, annotation, error}}. Ошибки скачивания и
    неуспешные ответы сохраняются так же, как в синхронном пути, чтобы прогон не
    зависал на одном битом вложении.

    Батч режется на части: одна картинка (а тем более PDF) в base64 весит
    мегабайты, а у Batch API есть предел на размер запроса — «весь месяц одним
    POST» упёрся бы в него и в память процесса. Каждая часть отправляется и
    вычитывается отдельно.

    ``resume``/``remember`` — колбэки хранения id уже отправленных частей (см.
    batch_eval.media_batch_stage). Без них повтор после обрыва сети отправил бы
    те же картинки вторым батчем и заплатил дважды.
    """
    out: dict[str, dict] = {}
    requests_out, by_id = batch_media_requests(items)
    with _store_session() as store:
        for custom_id, item in by_id.items():
            if item.get("_error"):
                store(item, item["_error"])
                out[item["message_id"]] = dict(item["_error"], annotation=None)
    if not requests_out:
        return out
    headers = llm._headers()
    known = dict(resume() or {}) if callable(resume) else {}
    for chunk_index, chunk in enumerate(_chunk_requests(requests_out)):
        key = f"chunk-{chunk_index}"
        batch_id = known.get(key)
        if batch_id:
            log(f"wz media: продолжаю уже отправленный батч {batch_id} (часть {chunk_index})")
        else:
            batch_id = _submit_batch(chunk, headers, log)
            if callable(remember):
                remember(key, batch_id)
        info = _poll_batch(batch_id, headers, poll_interval=poll_interval,
                           deadline_s=config.MEDIA_BATCH_DEADLINE_S, log=log)
        with _store_session() as store:
            _read_batch_results(info, batch_id, headers, by_id, out, store)
    return out


def _chunk_requests(requests_out: list[dict]):
    """Режет запросы на части по суммарному размеру и количеству."""
    max_bytes = config.MEDIA_BATCH_MAX_BYTES
    max_items = config.MEDIA_BATCH_MAX_ITEMS
    chunk, size = [], 0
    for request in requests_out:
        item_size = len(json.dumps(request, ensure_ascii=False))
        if chunk and (size + item_size > max_bytes or len(chunk) >= max_items):
            yield chunk
            chunk, size = [], 0
        chunk.append(request)
        size += item_size
    if chunk:
        yield chunk
