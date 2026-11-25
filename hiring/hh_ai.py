# -*- coding: utf-8 -*-
"""ИИ поиска по базе hh.kz (владелец 25.11.2026: «с помощью ключевых слов с ИИ… чтобы ИИ могла по портрету сотрудника
определять и давать скорбалл кандидату»).

Две задачи:
  * suggest_keywords — по портрету сотрудника и названию вакансии предложить фразы, которыми кандидаты называют
    должность в резюме (по ним мост ищет на hh); руководитель их правит;
  * score_candidates — балл 0–100 каждому кандидату по портрету, с одной фразой «почему» и плюсами/минусами. Кандидаты
    идут пачкой: портрет в запросе один на всю пачку, так дешевле.

Ходит в Gemini той же дорогой, что остальной проект (ai_feedback.service: Vertex с сервисным аккаунтом, запасной путь —
ключ AI Studio, цепочка моделей с переходом на следующую при перегрузке). В запрос уходят только портрет и резюме без
имён и контактов (hh_base.candidate_text) — их у портала до «Взять в работу» и нет.
"""

import json
import logging
import os
import time

import httpx

from . import hh_base

log = logging.getLogger(__name__)

TIMEOUT_SEC = float(os.getenv('HIRING_HH_AI_TIMEOUT', '') or 90)
# Балл — суждение, а не разбор текста: первой — gemini-2.5-flash без «мышления», лёгкие модели — запасом на перегрузку.
DEFAULT_MODELS = ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3.5-flash-lite')
BATCH = 8

KEYWORDS_PROMPT = """Ты — рекрутер. Составь поисковые фразы для поиска резюме в базе hh.kz под вакансию.

Вакансия: {vacancy}
Портрет сотрудника:
---
{portrait}
---

Требования к фразам:
- 5–8 фраз; каждая — как кандидат называет свою должность или профессию в резюме, 1–4 слова, по-русски;
- включи синонимы и написания (например «оператор call-центра», «оператор колл-центра», «специалист контакт-центра»)
  и смежные должности, опыт в которых подходит под портрет;
- не включай должности, которые портрету явно не подходят;
- без кавычек и без слов OR, AND, NOT.

Верни ТОЛЬКО JSON: {{"keywords": ["фраза", "..."]}}"""

SCORE_PROMPT = """Ты — опытный рекрутер. Оцени, насколько каждый кандидат подходит под портрет сотрудника на вакансию.

Вакансия: {vacancy}
Портрет сотрудника:
---
{portrait}
---

Кандидаты (резюме с hh.kz, без имён и контактов):
{candidates}

Для каждого кандидата:
- score — целое от 0 до 100: 90 и выше — точно по портрету, 70–89 — хороший кандидат, 50–69 — сомнительный, ниже 50 — не
  подходит;
- verdict — одна короткая фраза, почему такой балл (до 140 знаков);
- pros — до трёх коротких плюсов; cons — до трёх коротких минусов, а если минусов нет — чего в резюме не хватает для
  уверенной оценки (cons не оставляй пустым, если в verdict сказано, что данных мало).
Оценивай только по тому, что написано в резюме, ничего не додумывай.

Верни ТОЛЬКО JSON: {{"results": [{{"id": "<id кандидата>", "score": 0, "verdict": "…", "pros": ["…"], "cons": ["…"]}}]}}"""

SCHEMA_KEYWORDS = {'type': 'OBJECT', 'properties': {'keywords': {'type': 'ARRAY', 'items': {'type': 'STRING'}}},
                   'required': ['keywords']}
SCHEMA_SCORE = {
    'type': 'OBJECT',
    'properties': {'results': {'type': 'ARRAY', 'items': {
        'type': 'OBJECT',
        'properties': {'id': {'type': 'STRING'}, 'score': {'type': 'INTEGER'}, 'verdict': {'type': 'STRING'},
                       'pros': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                       'cons': {'type': 'ARRAY', 'items': {'type': 'STRING'}}},
        'required': ['id', 'score', 'verdict', 'pros', 'cons']}}},
    'required': ['results'],
}


class HhAiError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _models():
    raw = os.getenv('HIRING_HH_AI_MODELS', '')
    models = [item.strip() for item in raw.split(',') if item.strip()]
    return models or list(DEFAULT_MODELS)


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
        parts = ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
        return ''.join(str(part.get('text') or '') for part in parts if isinstance(part, dict))
    except (AttributeError, IndexError):
        return ''


def ask(prompt, schema, post=None, temperature=0.2):
    """Один структурированный ответ Gemini по цепочке моделей → (словарь, сведения о вызове: модель, мс, токены).
    Секреты — в заголовках (gemini_endpoint), в лог уходит только имя модели и код ответа."""
    from ai_feedback import service as ai   # лениво: ai_feedback тянет database/loguru

    if post is None and not (getattr(ai, 'GEMINI_API_KEY', None) or os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')):
        raise HhAiError('ИИ не настроен на сервере: нет ключа Gemini', 503)
    last = ''
    for model in _models():
        url, headers = ai.gemini_endpoint(model)
        config = {'temperature': temperature, 'maxOutputTokens': 8192, 'responseMimeType': 'application/json',
                  'responseSchema': schema}
        if model.startswith(ai.GEMINI_THINKING_OFF_MODELS):
            config['thinkingConfig'] = {'thinkingBudget': 0}
        payload = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}], 'generationConfig': config,
                   'safetySettings': ai.safety_settings}
        started = time.monotonic()
        try:
            status, data = (post or _post)(url, headers, payload)
        except httpx.HTTPError as exc:
            last = '%s: %s' % (model, type(exc).__name__)
            log.warning('hh ai: %s', last)
            continue
        if status in ai.GEMINI_FALLBACK_STATUS:
            last = '%s: HTTP %s' % (model, status)
            log.warning('hh ai: %s — следующая модель', last)
            continue
        if status != 200:
            log.warning('hh ai: %s: HTTP %s', model, status)
            raise HhAiError('ИИ ответил ошибкой (HTTP %s)' % status)
        text = _candidate_text(data)
        if not text.strip():
            last = '%s: пустой ответ' % model
            continue
        try:
            parsed = json.loads(ai._extract_json_block(text))
        except ValueError:
            last = '%s: ответ не JSON' % model
            continue
        if isinstance(parsed, dict):
            usage = data.get('usageMetadata') if isinstance(data.get('usageMetadata'), dict) else {}
            return parsed, {'model': model, 'ms': int((time.monotonic() - started) * 1000),
                            'tokens_in': usage.get('promptTokenCount'), 'tokens_out': usage.get('candidatesTokenCount')}
        last = '%s: неожиданная структура' % model
    raise HhAiError('ИИ сейчас недоступен, попробуйте ещё раз' + (' (%s)' % last if last else ''))


def suggest_keywords(vacancy, portrait, post=None):
    """Фразы для поиска по портрету. Портрет пуст — подсказывать не по чему."""
    portrait = str(portrait or '').strip()
    if len(portrait) < 10:
        raise HhAiError('Сначала опишите портрет сотрудника — ИИ подберёт слова по нему', 400)
    parsed, _meta = ask(KEYWORDS_PROMPT.format(vacancy=vacancy or 'не указана', portrait=portrait[:hh_base.PORTRAIT_MAX]),
                        SCHEMA_KEYWORDS, post=post, temperature=0.4)
    keywords = hh_base.clean_keywords([item for item in (parsed.get('keywords') or []) if isinstance(item, str)])
    if not keywords:
        raise HhAiError('ИИ не предложил ни одной фразы — допишите портрет и попробуйте ещё раз')
    return keywords


def score_candidates(vacancy, portrait, candidates, post=None):
    """Балл пачке кандидатов: candidates — [(id, текст резюме)]. → ({id: {score, verdict, pros, cons}}, сведения о
    вызове). Кандидата, по которому ИИ не ответил или ответил мусором, в ответе нет — его оценят в следующий раз."""
    blocks = ['Кандидат id=%s\n%s' % (key, text) for key, text in candidates]
    prompt = SCORE_PROMPT.format(vacancy=vacancy or 'не указана', portrait=str(portrait or '')[:hh_base.PORTRAIT_MAX],
                                 candidates='\n\n'.join(blocks))
    parsed, meta = ask(prompt, SCHEMA_SCORE, post=post)
    wanted = {str(key) for key, _text in candidates}
    out = {}
    for item in parsed.get('results') or []:
        if not isinstance(item, dict) or str(item.get('id')) not in wanted:
            continue
        score = hh_base.clean_score(item)
        if score is not None:
            out[str(item['id'])] = score
    return out, meta
