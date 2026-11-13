# -*- coding: utf-8 -*-
"""ИИ-проверка фото: Gemini на Vertex той же дорогой, что весь проект
(ai_feedback.service: сервисный аккаунт, цепочка моделей, переход на следующую
при перегрузке). Фото и текст правил уходят в один запрос, ответ — строгий JSON.

Ответу модели здесь не верят на слово: `normalize` сводит его к закрытому набору
значений, и любое противоречие («нарушение» без единого признака, «соответствует»
со списком нарушений, незнакомый код качества) уходит в «не удалось определить» —
то есть на ручную проверку, как велит ТЗ п. 4 («при сомнении — ручная проверка»).
"""

import base64
import json
import logging
import os
import time

import httpx

from . import AI_OK, AI_UNCLEAR, AI_VIOLATION, QUALITY_HINTS, QUALITY_OK
from .rules import FIXED_LIMITS

log = logging.getLogger(__name__)

# Зрение важнее скорости: первой идёт flash, запасом — облегчённые.
DEFAULT_MODEL_CHAIN = ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3.5-flash-lite')
ATTEMPT_TIMEOUT_SEC = float(os.getenv('DRESS_CODE_AI_TIMEOUT', '') or 25)
# Человек ждёт ответа с камерой в руках: весь проход по цепочке — не дольше этого.
TOTAL_BUDGET_SEC = 45

LIST_MAX = 8
TEXT_MAX = 300

PROMPT = """Ты проверяешь соблюдение дресс-кода по фото сотрудника, сделанному при приходе на работу.
Оцени КАЧЕСТВО КАДРА и ВИДИМУЮ ОДЕЖДУ по положению ниже.

Положение о внешнем виде (действующая версия):
---
{rules}
---

{limits}

Качество кадра (поле quality), выбери одно:
- "ok" — один человек, голова целиком в кадре, верх одежды виден так, что его можно оценить;
- "no_person" — человека нет;
- "several_people" — людей несколько;
- "head_cropped" — голова обрезана сверху;
- "body_cropped" — одежду не видно (в кадре почти только лицо или шея);
- "too_dark" — слишком темно, одежду не разобрать;
- "blurry" — снимок размыт;
- "clothes_hidden" — одежда закрыта сумкой, руками, верхней одеждой.

Вывод (поле verdict): "ok" — по видимому нарушений нет; "violation" — есть конкретный видимый признак нарушения
положения; "unclear" — признаки не видны, сомнение или случай для ручной проверки.

Заполни:
- visible — коротко, что видно из одежды (например «белая рубашка», «тёмный пиджак»);
- violations — только для verdict "violation": признак (sign) и пункт положения (rule), который он нарушает;
- remarks — неоднозначные замечания, которые нарушением не являются;
- unclear_reason — если verdict "unclear", почему.
Пиши по-русски, коротко. Верни ТОЛЬКО JSON."""

SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'quality': {'type': 'STRING', 'enum': [QUALITY_OK, *QUALITY_HINTS.keys()]},
        'verdict': {'type': 'STRING', 'enum': [AI_OK, AI_VIOLATION, AI_UNCLEAR]},
        'visible': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'violations': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'sign': {'type': 'STRING'}, 'rule': {'type': 'STRING'}},
                'required': ['sign'],
            },
        },
        'remarks': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'unclear_reason': {'type': 'STRING'},
    },
    'required': ['quality', 'verdict', 'visible', 'violations', 'remarks'],
}


class DressCodeAIError(Exception):
    """ИИ недоступен: фото сохраняется со статусом «Ожидает анализа»."""


def _text(value, limit=TEXT_MAX):
    return ' '.join(str(value or '').split())[:limit]


def _texts(values):
    out = []
    for value in values if isinstance(values, list) else []:
        text = _text(value)
        if text and text not in out:
            out.append(text)
        if len(out) >= LIST_MAX:
            break
    return out


def normalize(raw):
    """Ответ модели → {quality, verdict, visible, violations, remarks, note}. Чистая функция."""
    if not isinstance(raw, dict):
        return {'quality': QUALITY_OK, 'verdict': AI_UNCLEAR, 'visible': [], 'violations': [],
                'remarks': [], 'note': 'ИИ вернул ответ непонятного вида'}
    quality = str(raw.get('quality') or '').strip()
    verdict = str(raw.get('verdict') or '').strip()
    violations = []
    for item in raw.get('violations') if isinstance(raw.get('violations'), list) else []:
        if isinstance(item, dict):
            sign, rule = _text(item.get('sign')), _text(item.get('rule'))
        else:
            sign, rule = _text(item), ''
        if sign:
            violations.append({'sign': sign, 'rule': rule})
        if len(violations) >= LIST_MAX:
            break
    result = {
        'quality': quality,
        'verdict': verdict,
        'visible': _texts(raw.get('visible')),
        'violations': violations,
        'remarks': _texts(raw.get('remarks')),
        'note': _text(raw.get('unclear_reason')),
    }
    if quality != QUALITY_OK and quality not in QUALITY_HINTS:
        # Незнакомое качество — кадр не оценён: на руки человеку, а не в «годный».
        result.update(quality=QUALITY_OK, verdict=AI_UNCLEAR,
                      note=result['note'] or 'ИИ не оценил качество кадра')
        return result
    if quality != QUALITY_OK:
        # Плохой кадр не оценивает одежду: вывод по нему — не вывод.
        result.update(verdict=AI_UNCLEAR, violations=[], note=QUALITY_HINTS[quality])
        return result
    if verdict not in (AI_OK, AI_VIOLATION, AI_UNCLEAR):
        result.update(verdict=AI_UNCLEAR, note=result['note'] or 'ИИ не дал вывода')
    elif verdict == AI_VIOLATION and not violations:
        result.update(verdict=AI_UNCLEAR, note=result['note'] or 'ИИ не назвал признак нарушения')
    elif verdict == AI_OK and violations:
        # Признаки оставляем: руководитель увидит, что именно смутило модель.
        result.update(verdict=AI_UNCLEAR, note=result['note'] or 'ИИ противоречит сам себе')
    return result


def _model_chain():
    raw = os.getenv('DRESS_CODE_MODEL_CHAIN', '')
    models = [m.strip() for m in raw.split(',') if m.strip()] if raw else []
    return models or list(DEFAULT_MODEL_CHAIN)


def _post(url, headers, payload):
    with httpx.Client(timeout=ATTEMPT_TIMEOUT_SEC) as client:
        response = client.post(url, json=payload, headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data


def _candidate_text(data):
    try:
        candidates = data.get('candidates') or []
        if not candidates:
            return ''
        parts = (candidates[0].get('content') or {}).get('parts') or []
        return ''.join(str(p.get('text') or '') for p in parts if isinstance(p, dict))
    except AttributeError:
        return ''


def build_payload(model, image_bytes, rules_text, mime='image/jpeg'):
    from ai_feedback import service as ai   # лениво: ai_feedback тянет database/loguru

    config = {
        'temperature': 0.1,
        'maxOutputTokens': 2048,
        'responseMimeType': 'application/json',
        'responseSchema': SCHEMA,
    }
    if model.startswith(ai.GEMINI_THINKING_OFF_MODELS):
        config['thinkingConfig'] = {'thinkingBudget': 0}
    prompt = PROMPT.format(rules=rules_text, limits=FIXED_LIMITS)
    return {
        'contents': [{'role': 'user', 'parts': [
            {'text': prompt},
            {'inlineData': {'mimeType': mime, 'data': base64.b64encode(image_bytes).decode('ascii')}},
        ]}],
        'generationConfig': config,
        'safetySettings': ai.safety_settings,
    }


def analyze(image_bytes, rules_text, *, post=None, budget_sec=TOTAL_BUDGET_SEC):
    """Проверка одного фото. Возвращает normalize(...) + {'model'}; DressCodeAIError — ИИ недоступен."""
    from ai_feedback import service as ai

    if not (getattr(ai, 'GEMINI_API_KEY', None) or os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')):
        raise DressCodeAIError('ИИ не настроен на сервере')
    started = time.monotonic()
    last = ''
    for model in _model_chain():
        if time.monotonic() - started > budget_sec:
            last = last or 'истёк бюджет времени'
            break
        url, headers = ai.gemini_endpoint(model)
        try:
            status, data = (post or _post)(url, headers, build_payload(model, image_bytes, rules_text))
        except httpx.HTTPError as exc:
            last = f'{model}: {type(exc).__name__}'
            log.warning('Дресс-код, ИИ: %s', last)
            continue
        if status in ai.GEMINI_FALLBACK_STATUS:
            last = f'{model}: HTTP {status}'
            log.warning('Дресс-код, ИИ: %s — следующая модель', last)
            continue
        if status != 200:
            last = f'{model}: HTTP {status}'
            log.warning('Дресс-код, ИИ: %s', last)
            continue
        text = _candidate_text(data)
        if not text.strip():
            last = f'{model}: пустой ответ'
            continue
        try:
            parsed = json.loads(ai._extract_json_block(text))
        except ValueError:
            last = f'{model}: ответ не JSON'
            continue
        result = normalize(parsed)
        result['model'] = model
        return result
    raise DressCodeAIError(last or 'ИИ недоступен')
