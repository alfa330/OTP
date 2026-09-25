# -*- coding: utf-8 -*-
"""ИИ для скрипта разговора: «Создать с ИИ» и «Оформить с ИИ» (владелец, 25.09.2026).

Ходит в Gemini той же дорогой, что остальной проект (ai_feedback.service: Vertex с
сервисным аккаунтом, запасной путь — ключ AI Studio, цепочка моделей с переходом
на следующую при перегрузке). Данные водителей сюда не попадают ни в каком виде —
только текст скрипта и описание кампании от руководителя.

Разметка в ответе — ровно та, что понимают сайт (scriptMarkup.jsx) и телефон
(DialScript.cpp): # / ## заголовки, «- » пункты, «> » примечание, «---»,
**жирный**, ==выделение==. Правила проговариваются модели целиком (MARKUP_RULES).
"""
import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

BRIEF_MAX = 2000
TEXT_MAX = 20000
QUESTION_MAX = 200
ANSWER_MAX = 8000
QUESTIONS_MAX = 12
# Одна попытка к модели; цепочка моделей даёт запас, поэтому таймаут не резиновый.
TIMEOUT_SEC = float(os.getenv("DIAL_LIST_AI_TIMEOUT", "") or 60)

MARKUP_RULES = (
    "Разметка скрипта (только она — никакого другого Markdown и никакого HTML):\n"
    "- строка «# Заголовок» — заголовок раздела; «## Подзаголовок» — подраздел;\n"
    "- строка «- пункт» — пункт списка; строка «> текст» — примечание для оператора (показывается серым);\n"
    "- строка «---» — разделитель; пустая строка — новый абзац;\n"
    "- внутри текста «**жирный**» — что сказать обязательно, «==выделение==» — ключевая фраза, цифра или условие, "
    "которое надо произнести точно.\n"
    "Пиши по-русски, разговорно и коротко: оператор читает это вслух во время звонка."
)

PROMPT_GENERATE = """Ты — тренер колл-центра. Напиши скрипт исходящего звонка для оператора и быстрые вопросы к нему.

{rules}

Контекст: операторы удалённого колл-центра звонят из телефона по списку контактов; описание кампании — от руководителя:
---
{brief}
---

Требования к скрипту (поле body): приветствие и представление, цель звонка одной фразой, основная часть по шагам,
2–3 варианта, что сказать при возражении, завершение разговора. Реплики оператора — прямой речью. 120–350 слов.
Требования к вопросам (поле questions): 5–8 быстрых вопросов или возражений клиента («Сколько платят?», «Мне неинтересно»…),
на каждый — ответ оператора в той же разметке, 2–5 предложений.

Верни ТОЛЬКО JSON: {{"body": "<скрипт с разметкой>", "questions": [{{"question": "<вопрос>", "answer": "<ответ с разметкой>"}}]}}"""

PROMPT_POLISH = """Ты — редактор скриптов колл-центра. Оформи текст ниже по правилам разметки, не меняя смысла и фактов:
расставь заголовки, разбей на короткие абзацы и пункты, выдели **обязательные фразы** и ==ключевые цифры и условия==,
убери канцелярит и повторы, исправь опечатки. Ничего не выдумывай и не добавляй новых условий.

{rules}

Текст ({what}):
---
{text}
---

Верни ТОЛЬКО JSON: {{"text": "<оформленный текст с разметкой>"}}"""

SCHEMA_GENERATE = {
    "type": "OBJECT",
    "properties": {
        "body": {"type": "STRING"},
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"question": {"type": "STRING"}, "answer": {"type": "STRING"}},
                "required": ["question", "answer"],
            },
        },
    },
    "required": ["body", "questions"],
}
SCHEMA_POLISH = {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}


class ScriptAIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _post(url, headers, payload):
    with httpx.Client(timeout=TIMEOUT_SEC) as client:
        response = client.post(url, json=payload, headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data


def _candidate_text(data):
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    except AttributeError:
        return ""


def _ask(prompt, schema, post=None):
    """Один структурированный ответ Gemini по цепочке моделей. Секреты — в заголовках
    (gemini_endpoint), в лог уходит только имя модели и код ответа."""
    from ai_feedback import service as ai   # лениво: ai_feedback тянет database/loguru

    if not (getattr(ai, "GEMINI_API_KEY", None) or os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")):
        raise ScriptAIError("ИИ не настроен на сервере: нет ключа Gemini", 503)
    last = ""
    for model in ai._gemini_model_chain():
        url, headers = ai.gemini_endpoint(model)
        config = {
            "temperature": 0.4,
            "topP": 0.9,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if model.startswith(ai.GEMINI_THINKING_OFF_MODELS):
            config["thinkingConfig"] = {"thinkingBudget": 0}
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
            "safetySettings": ai.safety_settings,
        }
        try:
            status, data = (post or _post)(url, headers, payload)
        except httpx.HTTPError as exc:
            last = f"{model}: {type(exc).__name__}"
            log.warning("dial_list ai: %s", last)
            continue
        if status in ai.GEMINI_FALLBACK_STATUS:
            last = f"{model}: HTTP {status}"
            log.warning("dial_list ai: %s — следующая модель", last)
            continue
        if status != 200:
            log.warning("dial_list ai: %s: HTTP %s", model, status)
            raise ScriptAIError(f"ИИ ответил ошибкой (HTTP {status})", 502)
        text = _candidate_text(data)
        if not text.strip():
            last = f"{model}: пустой ответ"
            continue
        try:
            parsed = json.loads(ai._extract_json_block(text))
        except ValueError:
            last = f"{model}: ответ не JSON"
            continue
        if isinstance(parsed, dict):
            return parsed
        last = f"{model}: неожиданная структура"
    raise ScriptAIError("ИИ сейчас недоступен, попробуйте ещё раз" + (f" ({last})" if last else ""), 502)


def _clean_text(value, limit):
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:limit]


def generate_script(brief, post=None):
    """«Создать с ИИ»: по описанию кампании — скрипт и быстрые вопросы."""
    brief = _clean_text(brief, BRIEF_MAX)
    if len(brief) < 10:
        raise ScriptAIError("Опишите кампанию хотя бы одной фразой: кому звоним, что предлагаем, чего хотим", 400)
    parsed = _ask(PROMPT_GENERATE.format(rules=MARKUP_RULES, brief=brief), SCHEMA_GENERATE, post=post)
    body = _clean_text(parsed.get("body"), TEXT_MAX)
    questions = []
    for raw in (parsed.get("questions") or [])[:QUESTIONS_MAX]:
        if not isinstance(raw, dict):
            continue
        question = _clean_text(raw.get("question"), QUESTION_MAX)
        answer = _clean_text(raw.get("answer"), ANSWER_MAX)
        if question:
            questions.append({"question": question, "answer": answer})
    if not body and not questions:
        raise ScriptAIError("ИИ вернул пустой скрипт, попробуйте описать кампанию подробнее", 502)
    return {"body": body, "questions": questions}


def polish_text(text, what="скрипт", post=None):
    """«Оформить с ИИ»: тот же смысл, наша разметка."""
    text = _clean_text(text, TEXT_MAX)
    if len(text) < 10:
        raise ScriptAIError("Сначала напишите текст — ИИ оформит его, а не придумает", 400)
    parsed = _ask(PROMPT_POLISH.format(rules=MARKUP_RULES, what=what, text=text), SCHEMA_POLISH, post=post)
    result = _clean_text(parsed.get("text"), TEXT_MAX)
    if not result:
        raise ScriptAIError("ИИ вернул пустой текст, попробуйте ещё раз", 502)
    return result
