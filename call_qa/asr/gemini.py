"""Gemini как распознаватель звонка: запись → реплики с голосами, языком и таймингом.

Только через Vertex (сервисный аккаунт проекта), запись уходит в запрос целиком.
Промпт и схема — те, что прошли замер 30.09.2026 на 106 звонках; правка любого из них
меняет отпечаток расшифровки (config_identity), и уже распознанные записи при
следующей оценке распознаются заново.

Чего здесь нет по сравнению с Soniox: уверенности по словам (неразборчивое Gemini
помечает прямо в тексте — «[неразборчиво]») и повторяемости — одну и ту же невнятную
фразу она от прогона к прогону слышит по-разному. Поэтому расшифровка сохраняется
один раз и не пересчитывается (ai_transcript_cache), а сам модуль ничего не решает:
звать ли Gemini — дело second_pass.py.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

from .. import config
from .. import providers

PROMPT = """Ты — система дословной расшифровки телефонных разговоров колл-центра в Казахстане.
В записи говорят по-казахски, по-русски или смешивая языки в одной фразе.

Расшифруй запись ДОСЛОВНО:
- на языке оригинала: казахскую речь — казахской кириллицей (ә, ғ, қ, ң, ө, ұ, ү, һ, і), русскую — по-русски;
  НИКОГДА не переводи, не пересказывай, не исправляй грамматику, сохраняй смешение языков как сказано;
- оставляй повторы, оговорки и слова-паразиты («ну», «вот», «э-э»);
- числа, номера и суммы пиши словами так, как их произнесли;
- неразборчивое место помечай [неразборчиво]; не додумывай и не добавляй того, чего не было сказано;
- гудки, музыку и тишину не расшифровывай; голосовое меню и автоответчик — отдельным говорящим "IVR".

Раздели речь на реплики по говорящим. Говорящих нумеруй S1, S2, … в порядке появления и
укажи роль: "оператор" (сотрудник компании), "клиент" (водитель/собеседник), "IVR" или "неизвестно".
Для каждой реплики — время начала и конца от начала записи в формате ММ:СС.с (например 01:07.4)
и язык: "kk", "ru" или "mixed".
Покрой ВСЮ запись до конца, ничего не пропускай."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "segments": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "start": {"type": "STRING"}, "end": {"type": "STRING"},
                "speaker": {"type": "STRING"}, "role": {"type": "STRING"},
                "lang": {"type": "STRING"}, "text": {"type": "STRING"}},
            "required": ["start", "end", "speaker", "role", "lang", "text"]}}},
    "required": ["segments"]}

_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}
# Пустой или оборванный ответ при оплаченном выходе случается (≈1 % на замере) — повторяем.
_ATTEMPTS = 3
# Повторы Vertex на 429/5xx внутри одной попытки. Вместе с попытками и таймаутом это
# потолок ожидания карточки, поэтому скромно, и сверху ещё общий срок (transcribe_file).
_HTTP_TRIES = 2
_MAX_OUTPUT_TOKENS = 32768
# Модель иногда заворачивает JSON в ```json … ```, хотя её просили чистый JSON.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:[.,]\d+)?)$")
_SECONDS_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
# Язык реплики → чьи это буквы в языковом составе. «mixed» — фраза на двух языках
# сразу: делим поровну; неизвестная метка в состав не идёт.
_LANGUAGE_SHARES = {"kk": ("kk",), "ru": ("ru",), "mixed": ("kk", "ru")}


class GeminiAsrError(RuntimeError):
    pass


def config_identity(model: str) -> dict:
    """Что определяет расшифровку — ключ кэша и часть отпечатка оценки."""
    from ..evaluation.fingerprint import content_hash
    return {"provider": "gemini", "model": model, "prompt_hash": content_hash(PROMPT),
            "schema_hash": content_hash(SCHEMA), "temperature": 0, "assembler_version": 1}


def _milliseconds(value) -> int | None:
    """«01:07.4» / «1:01:07,4» / «67.4» → миллисекунды; мусор → None."""
    text = str(value or "").strip()
    match = _TIME_RE.match(text)
    if match:
        hours, minutes, seconds = int(match.group(1) or 0), int(match.group(2)), match.group(3)
        return int((hours * 3600 + minutes * 60 + float(seconds.replace(",", "."))) * 1000)
    if _SECONDS_RE.match(text):
        return int(float(text.replace(",", ".")) * 1000)
    return None


def _payload(path: str) -> dict:
    with open(path, "rb") as audio:
        data = base64.b64encode(audio.read()).decode()
    mime = _MIME.get(os.path.splitext(path)[1].lower(), "audio/mpeg")
    return {
        "contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": mime, "data": data}},
                                                {"text": PROMPT}]}],
        # Рассуждать над записью незачем, а «мышление» тарифицируется как выход.
        "generationConfig": {"temperature": 0, "maxOutputTokens": _MAX_OUTPUT_TOKENS,
                             "responseMimeType": "application/json", "responseSchema": SCHEMA,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }


def _generate(model: str, payload: dict, timeout: float) -> dict:
    try:
        return providers.vertex_generate(model, payload, timeout=timeout, tries=_HTTP_TRIES)
    except providers.VertexError as exc:
        # Часть моделей не принимает гашение «мышления» — тот же откат, что у оценки.
        if exc.status == 400 and "thinkingConfig" in payload["generationConfig"]:
            payload["generationConfig"].pop("thinkingConfig", None)
            return providers.vertex_generate(model, payload, timeout=timeout, tries=_HTTP_TRIES)
        raise


def parse_segments(answer: dict) -> list[dict]:
    """Ответ Vertex → реплики. Оборванный лимитом или пустой ответ — ошибка, а не
    короткая расшифровка: по обрезанному разговору оценку ставить нельзя."""
    candidate = (answer.get("candidates") or [{}])[0]
    finish = candidate.get("finishReason")
    text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
                   if not part.get("thought"))
    if finish == "MAX_TOKENS":
        raise GeminiAsrError("ответ оборван лимитом токенов")
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        raw = json.loads(text).get("segments")
    except (ValueError, AttributeError) as exc:
        raise GeminiAsrError(f"ответ не JSON, finishReason={finish}") from exc
    segments = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        body = " ".join(str(item.get("text") or "").split())
        if not body:
            continue
        segments.append({"speaker": str(item.get("speaker") or "").strip() or "?",
                         "role": str(item.get("role") or "").strip() or None,
                         "lang": str(item.get("lang") or "").strip().lower() or None,
                         "start_ms": _milliseconds(item.get("start")),
                         "end_ms": _milliseconds(item.get("end")), "text": body})
    if not segments:
        raise GeminiAsrError(f"в ответе нет реплик, finishReason={finish}")
    return segments


def transcribe_file(path: str, *, model: str | None = None, timeout: float | None = None) -> dict:
    """{"segments": [...], "meta": {...}} или GeminiAsrError/VertexError.

    В meta — всё, за что заплачено: токены (звук тарифицируется как вход), версия
    модели, число попыток и задержка. Новая попытка не начинается, если с начала
    прошло больше двух таймаутов: карточка и ночная выборка ждут этот ответ, и
    лучше остаться на Soniox, чем держать их десятки минут на неудачных повторах."""
    model = model or config.ASR_SECOND_PASS_MODEL
    timeout = timeout or config.ASR_SECOND_PASS_TIMEOUT
    started = time.perf_counter()
    payload = _payload(path)
    failed, last_error = [], None
    for attempt in range(_ATTEMPTS):
        if attempt and time.perf_counter() - started > 2 * timeout:
            raise GeminiAsrError(f"нет пригодного ответа за {2 * timeout:.0f} с: {last_error}")
        answer = _generate(model, payload, timeout)
        try:
            segments = parse_segments(answer)
        except GeminiAsrError as exc:
            last_error = exc
            failed.append({"usage": answer.get("usageMetadata"), "error": str(exc)})
            continue
        return {"segments": segments,
                "meta": {"model": model, "model_version": answer.get("modelVersion"),
                         "usage": answer.get("usageMetadata"), "failed_attempts": failed,
                         "latency_ms": round((time.perf_counter() - started) * 1000),
                         "file_size_bytes": os.path.getsize(path)}}
    raise GeminiAsrError(f"нет пригодного ответа за {_ATTEMPTS} попытки: {last_error}")


def assemble(segments: list[dict]) -> dict:
    """Реплики Gemini → то же, что отдаёт soniox.assemble + api._lines_from_tokens:
    текст для оценщика («[S1] …»), реплики карточки с меткой голоса и языковой состав.

    Голоса нумеруются заново по порядку появления: Gemini может назвать говорящего
    «IVR» или сбить нумерацию, а дальше по конвейеру голос — это число, как у Soniox."""
    voices: dict[str, str] = {}
    lines, text_lines, weight = [], [], {}
    for segment in segments:
        voice = voices.setdefault(segment["speaker"], str(len(voices) + 1))
        timing = {key: segment[src] for key, src in (("start_time_ms", "start_ms"), ("end_time_ms", "end_ms"))
                  if segment.get(src) is not None}
        line = {"spk": voice, "seg": [{"t": segment["text"], **timing}]}
        if segment.get("start_ms") is not None:
            line["start_ms"] = segment["start_ms"]
        if segment.get("end_ms") is not None:
            line["end_ms"] = segment["end_ms"]
        lines.append(line)
        text_lines.append(f"[S{voice}] {segment['text']}")
        letters = sum(1 for char in segment["text"] if char.isalpha())
        shares = _LANGUAGE_SHARES.get(segment.get("lang") or "", ())
        for lang in shares:
            weight[lang] = weight.get(lang, 0) + letters / len(shares)
    total = sum(weight.values()) or 1
    languages = {lang: round(100 * value / total)
                 for lang, value in sorted(weight.items(), key=lambda item: -item[1]) if value}
    return {"text": "\n".join(text_lines), "lines": lines, "languages": languages}
