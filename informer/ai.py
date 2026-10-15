# -*- coding: utf-8 -*-
"""ИИ для текста рассылки (владелец, 15.10.2026: «ИИ подправление написанного текста или генерация»).

Та же дорога к Gemini, что у скрипта раздела (dial_list.ai._ask: Vertex, цепочка моделей).
Текст пишется для СЛУХА: его прочитает голос Soniox в телефонной трубке, поэтому короткие фразы,
без скобок, сокращений и разметки. Данных водителей здесь нет — только описание от руководителя
и сам текст. Ничего не сохраняется: руководитель смотрит результат и решает сам."""
from dial_list import ai as script_ai

BRIEF_MAX = 2000
TEXT_MAX = 1500
ScriptAIError = script_ai.ScriptAIError

SPOKEN_RULES = (
    "Правила текста для голосового оповещения (его читает синтезатор речи в телефонной трубке):\n"
    "- короткие простые фразы, обращение на «вы», без канцелярита;\n"
    "- никакой разметки, эмодзи, скобок, кавычек-ёлочек, сокращений и списков;\n"
    "- суммы и количества — так, как их говорят (5000 тенге — можно цифрами);\n"
    "- в начале — приветствие, в конце — короткий призыв, что сделать;\n"
    "- не выдумывай условия, сроки и суммы, которых нет в исходном тексте или описании."
)

PROMPT_GENERATE = """Ты — редактор голосовых оповещений для водителей такси в Казахстане.
Напиши короткое голосовое сообщение автоинформатора по описанию руководителя: на казахском и то же самое на
русском. Каждая версия звучит 10–20 секунд (25–50 слов).

{rules}

Описание от руководителя:
---
{brief}
---

Верни ТОЛЬКО JSON: {{"kz": "<текст на казахском>", "ru": "<текст на русском>"}}"""

PROMPT_POLISH = """Ты — редактор голосовых оповещений. Подправь текст ниже ({lang}) так, чтобы его было легко
понять на слух: исправь ошибки, сократи длинные фразы, убери повторы. Смысл, суммы и условия не меняй, ничего не
добавляй. Язык ответа — тот же ({lang}).

{rules}

Текст:
---
{text}
---

Верни ТОЛЬКО JSON: {{"text": "<подправленный текст>"}}"""

PROMPT_TRANSLATE = """Ты — переводчик голосовых оповещений для водителей такси в Казахстане. Переведи текст
с языка «{source}» на язык «{target}» естественно, как говорят живые люди, а не дословно. Суммы и условия — точно
как в исходнике, ничего не добавляй.

{rules}

Текст:
---
{text}
---

Верни ТОЛЬКО JSON: {{"text": "<перевод>"}}"""

LANG_NAMES = {"kz": "казахский", "ru": "русский"}

SCHEMA_PAIR = {"type": "OBJECT", "properties": {"kz": {"type": "STRING"}, "ru": {"type": "STRING"}},
               "required": ["kz", "ru"]}
SCHEMA_TEXT = {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}


def _clean(value, limit):
    return " ".join(str(value or "").replace("\r", " ").split())[:limit]


def generate(brief, post=None):
    brief = str(brief or "").strip()[:BRIEF_MAX]
    if len(brief) < 10:
        raise ScriptAIError("Опишите одной-двумя фразами, о чём сообщить водителям", 400)
    parsed = script_ai._ask(PROMPT_GENERATE.format(rules=SPOKEN_RULES, brief=brief), SCHEMA_PAIR, post=post)
    kz, ru = _clean(parsed.get("kz"), TEXT_MAX), _clean(parsed.get("ru"), TEXT_MAX)
    if not kz or not ru:
        raise ScriptAIError("ИИ вернул пустой текст — опишите подробнее и попробуйте ещё раз", 502)
    return {"kz": kz, "ru": ru}


def polish(text, lang, post=None):
    if lang not in LANG_NAMES:
        raise ScriptAIError("Язык — казахский или русский", 400)
    text = _clean(text, TEXT_MAX)
    if len(text) < 5:
        raise ScriptAIError("Сначала напишите текст — ИИ подправит его, а не придумает", 400)
    parsed = script_ai._ask(PROMPT_POLISH.format(rules=SPOKEN_RULES, lang=LANG_NAMES[lang], text=text),
                            SCHEMA_TEXT, post=post)
    result = _clean(parsed.get("text"), TEXT_MAX)
    if not result:
        raise ScriptAIError("ИИ вернул пустой текст, попробуйте ещё раз", 502)
    return {"text": result}


def translate(text, source, target, post=None):
    if source not in LANG_NAMES or target not in LANG_NAMES or source == target:
        raise ScriptAIError("Перевод — между казахским и русским", 400)
    text = _clean(text, TEXT_MAX)
    if len(text) < 5:
        raise ScriptAIError("Сначала напишите текст, который перевести", 400)
    parsed = script_ai._ask(PROMPT_TRANSLATE.format(rules=SPOKEN_RULES, source=LANG_NAMES[source],
                                                    target=LANG_NAMES[target], text=text),
                            SCHEMA_TEXT, post=post)
    result = _clean(parsed.get("text"), TEXT_MAX)
    if not result:
        raise ScriptAIError("ИИ вернул пустой перевод, попробуйте ещё раз", 502)
    return {"text": result}
