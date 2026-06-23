import asyncio
import json
import re
import httpx
from loguru import logger
from database import db, IT_TICKET_CATALOG
from collections import defaultdict
import os
from datetime import datetime, date

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

MASTER_PROMPT_MONTHLY = """ТЫ — Dos, опытный и дружелюбный тренер/ментор для операторов колл-центра.
Твоя задача — проанализировать результаты оценок за выбранный месяц и сгенерировать развёрнутую, практичную обратную связь на основе мониторинговой шкалы.

Входные данные:
1) META: общая статистика за месяц (месяц, направление, количество оценённых звонков, средняя оценка и т.д.)
2) CRITERIA: список критериев мониторинговой шкалы с агрегированной статистикой по каждому критерию.
3) COMMENTS: список комментариев за месяц (включая комментарии супервайзеров). Эти комментарии НЕЛЬЗЯ игнорировать.

Для каждого критерия передаются:
- criterion_name: название критерия
- criterion_description: описание/требование по критерию
- weight: вес (если не критический)
- is_critical: критический ли критерий
- deficiency: (опционально) недочёт: weight и description
- stats: агрегированная статистика за месяц: correct/incorrect/na/total/incorrect_rate
- examples: несколько примеров комментариев оценщиков (если есть)

ТЫ ДОЛЖЕН:
1) Найти сильные стороны и слабые места оператора за месяц по каждому критерию, опираясь на stats и описания критериев.
2) Обязательно использовать COMMENTS: учитывать все комментарии (включая длинные) при объяснении проблем и при выборе приоритетов.
   Если в COMMENTS есть комментарии супервайзера (sv_request_comment) — считать их приоритетными сигналами и напрямую отражать их смысл в рекомендациях.
3) Для проблемных критериев предложить конкретные рекомендации и 2–3 техники/фразы.
4) Выставить приоритеты: high/medium/low. Приоритет повышается если:
   - критерий критический и есть ошибки,
   - доля ошибок высокая,
   - критерий имеет большой вес.
5) Сформировать summary: общее заключение, 3 главных приоритета и план тренировки.
6) Вернуть результат ИСКЛЮЧИТЕЛЬНО в формате JSON, без каких-либо дополнительных слов или форматирования.

Обязательная структура JSON-ответа:
{
  "meta": {
    "month": "YYYY-MM",
    "direction": "<название направления или пусто>",
    "evaluated_calls": <int>,
    "avg_score": <number|null>
  },
  "per_criterion": [
    {
      "criterion": "<название критерия>",
      "priority": "<high|medium|low>",
      "strengths": "<кратко что получается>",
      "issues": "<кратко что не получается>",
      "recommendation": "<что делать чтобы улучшить>",
      "techniques": ["<техника/фраза 1>", "<техника/фраза 2>", "<техника/фраза 3>"]
    }
  ],
  "summary": {
    "overall_level": "<needs_improvement|good|excellent>",
    "top_priorities": ["<приоритет 1>", "<приоритет 2>", "<приоритет 3>"],
    "training_plan": ["<шаг 1>", "<шаг 2>", "<шаг 3>"]
  }
}
"""

MASTER_PROMPT_BIRTHDAY = """ТЫ — Dos, дружелюбный и тактичный тренер/ментор для сотрудников колл-центра.
Твоя задача — написать короткое персональное поздравление с днем рождения.

Входные данные:
- NAME: ФИО сотрудника
- ROLE: роль (admin|sv|supervisor|trainer|operator)
- DIRECTION: направление (если есть)
- GENDER: male|female|unknown
- HIRE_DATE: дата найма (если есть)
- TENURE_MONTHS: стаж в месяцах (если есть)
- DATE: сегодняшняя дата

Требования:
1) 2–4 предложения, до 60 слов.
2) Тон: теплый, профессиональный, уважительный.
3) Не упоминай возраст, год рождения, зарплату, политику, религию и любые конфиденциальные данные.
4) Если GENDER неизвестен — используй нейтральное обращение.
5) Можно добавить 1–2 аккуратные эмодзи.
6) Верни результат ТОЛЬКО в JSON: {"greeting": "<текст>"}.
"""

# Рекомендуемые параметры генерации (более детерминированно)
generation_config = {
    "temperature": 0.2,
    "topP": 0.9,
    "topK": 40,
    "maxOutputTokens": 1500000,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]


def _norm_status(value: object) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in {"correct", "ok", "true", "да", "верно"}:
        return "Correct"
    if v in {"incorrect", "error", "false", "нет", "неверно"}:
        return "Incorrect"
    if v in {"n/a", "na", "неприменимо", "-"}:
        return "N/A"
    return None


def _pick_direction(evaluations: list[dict]) -> dict | None:
    counts: dict[tuple[int | None, str | None], int] = defaultdict(int)
    by_key: dict[tuple[int | None, str | None], dict] = {}
    for ev in evaluations:
        direction = ev.get("direction") if isinstance(ev, dict) else None
        if not direction or not isinstance(direction, dict):
            continue
        did = direction.get("id")
        dname = direction.get("name")
        key = (did, dname)
        counts[key] += 1
        by_key[key] = direction
    if not counts:
        return None
    best_key = max(counts.items(), key=lambda x: x[1])[0]
    return by_key.get(best_key)


def _build_monthly_criteria_payload(evaluations: list[dict], direction: dict | None) -> list[dict]:
    criteria = []
    if direction and isinstance(direction, dict):
        criteria = direction.get("criteria") or []
    if not isinstance(criteria, list):
        criteria = []

    agg: list[dict] = []
    for cidx, crit in enumerate(criteria):
        crit = crit if isinstance(crit, dict) else {}
        agg.append(
            {
                "criterion_name": crit.get("name") or f"Критерий {cidx + 1}",
                "criterion_description": crit.get("value") or "",
                "weight": crit.get("weight"),
                "is_critical": bool(crit.get("isCritical")),
                "deficiency": crit.get("deficiency"),
                "stats": {"correct": 0, "incorrect": 0, "na": 0, "total": 0, "incorrect_rate": None},
                "examples": [],
            }
        )

    for ev in evaluations:
        scores = ev.get("scores") if isinstance(ev, dict) else None
        comments = ev.get("criterion_comments") if isinstance(ev, dict) else None
        if not isinstance(scores, list):
            continue
        for cidx in range(min(len(scores), len(agg))):
            status = _norm_status(scores[cidx])
            if not status:
                continue
            st = agg[cidx]["stats"]
            st["total"] += 1
            if status == "Correct":
                st["correct"] += 1
            elif status == "Incorrect":
                st["incorrect"] += 1
            elif status == "N/A":
                st["na"] += 1

            if isinstance(comments, list) and cidx < len(comments):
                cmt = comments[cidx]
                if cmt and isinstance(cmt, str):
                    agg[cidx]["examples"].append(cmt.strip())

    for item in agg:
        st = item["stats"]
        denom = st.get("total") or 0
        if denom > 0:
            st["incorrect_rate"] = round((st.get("incorrect") or 0) / denom, 4)
        else:
            st["incorrect_rate"] = None

        if isinstance(item.get("examples"), list) and item["examples"]:
            seen = set()
            unique = []
            for x in item["examples"]:
                if not isinstance(x, str):
                    continue
                k = x.strip()
                if not k:
                    continue
                if k in seen:
                    continue
                seen.add(k)
                unique.append(k)
            item["examples"] = unique

    return agg


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _calc_tenure_months(hire_date: date | None, on_date: date) -> int | None:
    if not hire_date:
        return None
    months = (on_date.year - hire_date.year) * 12 + (on_date.month - hire_date.month)
    if on_date.day < hire_date.day:
        months -= 1
    return max(months, 0)


async def generate_monthly_feedback_with_ai(operator_id: int, month: str) -> dict | None:
    if not GEMINI_API_KEY:
        logger.error("Gemini API key is not configured.")
        return None


async def generate_birthday_greeting_with_ai(user_payload: dict, for_date: str) -> dict | None:
    if not GEMINI_API_KEY:
        logger.error("Gemini API key is not configured.")
        return None

    if not isinstance(user_payload, dict):
        return None

    user_id = user_payload.get("id")
    if not user_id:
        return None

    date_obj = _parse_date(for_date) or datetime.now().date()
    date_key = date_obj.isoformat()

    cached_greeting = db.get_ai_birthday_greeting_cache(int(user_id), date_key)
    if cached_greeting:
        logger.info(f"Returning cached AI birthday greeting for user {user_id}, date {date_key}")
        return cached_greeting["greeting_data"]

    name = (user_payload.get("name") or "Сотрудник").strip()
    role = (user_payload.get("role") or "").strip()
    direction = (user_payload.get("direction") or "").strip()
    gender = (user_payload.get("gender") or "unknown").strip().lower() or "unknown"
    hire_date = _parse_date(user_payload.get("hire_date"))
    tenure_months = _calc_tenure_months(hire_date, date_obj)
    hire_date_text = hire_date.isoformat() if hire_date else ""

    full_prompt = (
        f"{MASTER_PROMPT_BIRTHDAY}\n"
        f"---DATA---\n"
        f"NAME: {name}\n"
        f"ROLE: {role}\n"
        f"DIRECTION: {direction}\n"
        f"GENDER: {gender}\n"
        f"HIRE_DATE: {hire_date_text}\n"
        f"TENURE_MONTHS: {tenure_months if tenure_months is not None else ''}\n"
        f"DATE: {date_key}\n"
        f"---END DATA---\n"
        f"ВЕРНИТЕ JSON ПО ШАБЛОНУ."
    )

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": generation_config,
        "safetySettings": safety_settings,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
            result = response.json()
            if "candidates" not in result or not result["candidates"]:
                logger.error("Gemini response empty or blocked.")
                return None
            candidate = result["candidates"][0]
            if "finishReason" in candidate and candidate["finishReason"] != "STOP":
                logger.warning(f"Finish reason: {candidate['finishReason']}")
            raw_text = candidate.get("content", {}).get("parts", [])[0].get("text", "")

            json_match = re.search(r'```json\s*(\{.*\})\s*```', raw_text, re.DOTALL)
            if json_match:
                cleaned = json_match.group(1)
            else:
                start = raw_text.find('{')
                end = raw_text.rfind('}')
                if start != -1 and end != -1 and end > start:
                    cleaned = raw_text[start:end + 1]
                else:
                    cleaned = raw_text

            try:
                parsed = json.loads(cleaned)
                db.save_ai_birthday_greeting_cache(int(user_id), date_key, parsed)
                logger.info(f"Cached AI birthday greeting for user {user_id}, date {date_key}")
                return parsed
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}. Cleaned: {cleaned}")
                return {"error": "json_parse_error", "raw_response": raw_text, "cleaned": cleaned}

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error while contacting Gemini: {e}")
        return None


# ─── IT-ticket assistant ───────────────────────────────────────────────────────

MASTER_PROMPT_IT_TICKET = """ТЫ — ассистент, который помогает супервайзеру колл-центра составить грамотную заявку (тикет) в IT-отдел.
Твоя цель — собрать всю информацию, нужную IT-специалисту, чтобы он сразу понял суть проблемы и приступил к решению без лишних уточнений.

Тебе передаются:
- PROFILE: профиль каталога (op = Отдел продаж, szov = СЗоВ)
- CATALOG: список категорий и типовых проблем для этого профиля
- CATEGORY / SUBCATEGORY: выбранная категория и подкатегория (могут быть пустыми)
- DESCRIPTION: свободное описание проблемы от супервайзера (может быть кратким)
- FIELDS: уже заполненные поля формы (объект ключ→значение)
- ANSWERS: ответы на ранее заданные уточняющие вопросы (объект)
- CONTEXT: кто создаёт заявку (имя, роль, отдел/направление, текущие дата и время)
- MODE: draft | finalize

ОБЩИЕ ПРАВИЛА:
- Пиши по-русски, КРАТКО и ПО ДЕЛУ, без воды, канцелярита и вводных фраз. Каждая строка — факт.
- НЕ выдумывай факты. Если данных нет — задай вопрос или пропусти поле.
- Ответы вроде «ничего», «нет», «не знаю», «все», «—» и пустые значения считай ОТСУТСТВИЕМ информации:
  НЕ вставляй их в тикет и не сочиняй текст вместо них — просто опусти соответствующий раздел.
- Опирайся на CATALOG: подбирай категорию/подкатегорию из него.

ЕСЛИ MODE = draft:
1. Определи наиболее подходящие category и subcategory из CATALOG (если они не заданы).
2. Сформируй компактную форму form.fields — набор полей, которые нужно заполнить именно под эту проблему.
   Каждое поле: key (латиница, snake_case), label (рус.), type (text|textarea|select|date|time|number),
   required (bool), placeholder, options (массив строк только для type=select), hint (короткая подсказка).
   Включай только релевантные поля (обычно 3–7). Примеры: номер рабочего места/ПК, ФИО или логин сотрудника,
   когда началось, как часто повторяется, массовость (сколько человек затронуто), что уже пробовали,
   ссылка/скриншот. Предзаполни value поля, если оно явно следует из DESCRIPTION.
3. Сформируй questions — 1–4 уточняющих вопроса ТОЛЬКО про недостающую информацию. Если всего достаточно — пустой массив.
4. priority: low|medium|high|critical — оцени по влиянию (массовость, простой в работе, блокировка).
5. ticket — предварительный черновик (title, summary, markdown).
6. status = "draft".

ЕСЛИ MODE = finalize:
1. Старайся собрать заявку из того, что есть. status="need_more_info" возвращай ТОЛЬКО если непонятна
   сама суть проблемы (что именно и где сломалось) — тогда задай 1–2 точечных вопроса. Иначе status="ready".
2. При status="ready" составь финальный тикет ticket:
   - title: короткий заголовок (до 80 символов), по сути проблемы.
   - summary: 1 предложение сути.
   - markdown: ГОТОВЫЙ КОРОТКИЙ текст для Telegram, БЕЗ ВОДЫ. Жёсткие правила:
     • Объём: до ~8 строк / 700 символов; короче — лучше.
     • Только HTML-теги Telegram: <b>…</b>, <i>…</i>, <code>…</code>. Никаких *, #, markdown, <br>, <ul>, <p>.
     • Переносы строк — обычным символом новой строки. Текст ошибки/код — в <code>…</code>.
     • Включай ТОЛЬКО строки, по которым есть конкретная информация. Пустое/«ничего»/«все»/«не знаю» —
       пропускай целиком (НЕ пиши «не указано»). Не повторяй одно и то же, без вводных и общих фраз.
     • НЕ дублируй категорию, приоритет и автора — они и так добавляются отдельно.
     • Каркас (бери ТОЛЬКО нужные строки; заголовки выделяй <b>):
       <b>Проблема:</b> …
       <b>Кого затрагивает:</b> …
       <b>Когда:</b> …
       <b>Детали:</b> …
       <b>Уже пробовали:</b> …
       <b>Нужно:</b> …

ФОРМАТ ОТВЕТА — СТРОГО ОДИН JSON-объект, без markdown-ограждений и текста вокруг:
{
  "status": "draft" | "need_more_info" | "ready",
  "profile": "op" | "szov",
  "category": "<категория>",
  "subcategory": "<подкатегория>",
  "priority": "low" | "medium" | "high" | "critical",
  "questions": [ {"id": "q1", "question": "<вопрос>", "why": "<зачем это IT-отделу>"} ],
  "form": { "fields": [ {"key": "...", "label": "...", "type": "text", "required": false, "placeholder": "", "options": [], "hint": "", "value": ""} ] },
  "ticket": { "title": "...", "summary": "...", "markdown": "..." }
}
"""


def _extract_json_block(raw_text: str):
    json_match = re.search(r'```json\s*(\{.*\})\s*```', raw_text, re.DOTALL)
    if json_match:
        cleaned = json_match.group(1)
    else:
        start = raw_text.find('{')
        end = raw_text.rfind('}')
        if start != -1 and end != -1 and end > start:
            cleaned = raw_text[start:end + 1]
        else:
            cleaned = raw_text
    return cleaned


# Коды ответа Gemini, при которых имеет смысл повторить запрос (перегрузка / временный сбой)
GEMINI_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 404 = модель недоступна для ключа → пробуем следующую модель в цепочке
GEMINI_FALLBACK_STATUS = GEMINI_RETRYABLE_STATUS | {404}

# Цепочка моделей: если первая перегружена / недоступна / таймаутит — берём следующую.
# У моделей раздельные пулы мощностей, поэтому перегрузка одной не означает перегрузку всех.
DEFAULT_GEMINI_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]


def _gemini_model_chain():
    """Цепочка моделей; можно переопределить через env GEMINI_MODEL_CHAIN (через запятую)."""
    raw = os.getenv("GEMINI_MODEL_CHAIN", "")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    return models or list(DEFAULT_GEMINI_MODEL_CHAIN)


async def _gemini_generate_once(model: str, payload: dict, timeout: float, attempts: int):
    """Запрос к одной модели (с ретраями внутри). Возвращает (result, try_next).

    try_next=True → имеет смысл попробовать следующую модель цепочки (перегрузка/таймаут/404).
    result: распарсенный dict при успехе; {'error': <code>} или None при ошибке.
    """
    api_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(api_url, json=payload)

            if response.status_code in GEMINI_FALLBACK_STATUS:
                logger.warning(
                    f"IT-ticket Gemini {model} → {response.status_code} (попытка {attempt}/{attempts})"
                )
                if attempt < attempts and response.status_code in GEMINI_RETRYABLE_STATUS:
                    await asyncio.sleep(min(2 ** attempt, 6))
                    continue
                return {"error": "ai_unavailable", "status": response.status_code}, True

            response.raise_for_status()
            result = response.json()
            if "candidates" not in result or not result["candidates"]:
                logger.error(f"Gemini {model}: пустой/заблокированный ответ (IT ticket).")
                return {"error": "ai_blocked"}, False
            candidate = result["candidates"][0]
            raw_text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
            cleaned = _extract_json_block(raw_text)
            try:
                return json.loads(cleaned), False
            except json.JSONDecodeError as e:
                logger.error(f"IT-ticket JSON parse error ({model}): {e}. Cleaned: {cleaned[:300]}")
                return {"error": "json_parse_error", "raw_response": raw_text}, False

        except (httpx.TimeoutException, httpx.TransportError) as e:
            logger.warning(
                f"IT-ticket Gemini {model} network/timeout (попытка {attempt}/{attempts}): {e!r}"
            )
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 6))
                continue
            return {"error": "ai_timeout"}, True
        except httpx.HTTPStatusError as e:
            # Неретраебельная ошибка (например, 400) — общая для всех моделей, дальше не идём
            logger.error(f"IT-ticket HTTP error ({model}): {e.response.status_code} - {e.response.text[:300]}")
            return None, False
        except Exception as e:
            logger.exception(f"Unexpected error contacting Gemini ({model}, IT ticket): {e}")
            return None, False

    return {"error": "ai_unavailable"}, True


async def _call_gemini_json(full_prompt: str, timeout: float = 30.0, attempts: int = 1) -> dict | None:
    """Вызывает Gemini с ЦЕПОЧКОЙ моделей (fallback при перегрузке/таймауте/404).

    Если первая модель отвечает 503 «high demand» / 429 / таймаутит / 404 — берётся
    следующая модель из цепочки. Возвращает:
      - dict с результатом при успехе;
      - {'error': <code>} для понятных клиенту ошибок (ai_unavailable / ai_timeout /
        ai_blocked / json_parse_error);
      - None при невосстановимой/неожиданной ошибке (например, 400).
    """
    if not GEMINI_API_KEY:
        logger.error("Gemini API key is not configured.")
        return None

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {**generation_config, "maxOutputTokens": 8192},
        "safetySettings": safety_settings,
    }

    chain = _gemini_model_chain()
    last_error = {"error": "ai_unavailable"}
    for idx, model in enumerate(chain):
        result, try_next = await _gemini_generate_once(model, payload, timeout, attempts)
        if not try_next:
            return result
        if isinstance(result, dict) and result.get("error"):
            last_error = result
        if idx + 1 < len(chain):
            logger.warning(f"IT-ticket: переключаюсь на следующую модель после {model}")
            await asyncio.sleep(0.5)
    return last_error


def _it_catalog_block(profile: str) -> str:
    prof = profile if profile in IT_TICKET_CATALOG else "op"
    cat = IT_TICKET_CATALOG.get(prof, {})
    lines = [f"PROFILE_LABEL: {cat.get('label', prof)}"]
    for entry in cat.get("categories", []):
        lines.append(f"- {entry.get('name')}:")
        for item in entry.get("items", []):
            lines.append(f"    • {item}")
    return "\n".join(lines)


async def generate_it_ticket_with_ai(mode: str, payload: dict) -> dict | None:
    """Помощник по составлению IT-тикета.

    mode='draft'    — подобрать категорию, сгенерировать форму и уточняющие вопросы.
    mode='finalize' — собрать финальный тикет или вернуть недостающие вопросы.
    """
    if not isinstance(payload, dict):
        return None
    if not GEMINI_API_KEY:
        logger.error("Gemini API key is not configured.")
        return None

    mode = (mode or "draft").strip().lower()
    if mode not in ("draft", "finalize"):
        mode = "draft"

    profile = str(payload.get("profile") or "op").strip().lower()
    if profile not in IT_TICKET_CATALOG:
        profile = "op"
    category = str(payload.get("category") or "").strip()
    subcategory = str(payload.get("subcategory") or "").strip()
    description = str(payload.get("description") or "").strip()
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}

    # Дополнительные инструкции от админа/главы отдела (актуальные изменения),
    # которые могут быть не отражены в мастер-промпте. При конфликте — приоритетнее.
    try:
        extra_instructions = db.get_combined_it_ticket_instructions(profile) or ""
    except Exception:
        logger.exception("Failed to load IT-ticket admin instructions")
        extra_instructions = ""

    instructions_block = (
        "---АКТУАЛЬНЫЕ ИНСТРУКЦИИ ОТ АДМИНИСТРАТОРА / ГЛАВЫ ОТДЕЛА---\n"
        "Эти инструкции добавлены вручную и описывают недавние изменения. "
        "При конфликте с общими правилами выше — следуй ИМ.\n"
        f"{extra_instructions if extra_instructions else '(инструкций нет)'}\n"
        "---КОНЕЦ ИНСТРУКЦИЙ---\n"
    )

    full_prompt = (
        f"{MASTER_PROMPT_IT_TICKET}\n"
        f"{instructions_block}"
        f"---INPUT---\n"
        f"MODE: {mode}\n"
        f"PROFILE: {profile}\n"
        f"CATALOG:\n{_it_catalog_block(profile)}\n"
        f"CATEGORY: {category}\n"
        f"SUBCATEGORY: {subcategory}\n"
        f"DESCRIPTION: {description}\n"
        f"FIELDS: {json.dumps(fields, ensure_ascii=False)}\n"
        f"ANSWERS: {json.dumps(answers, ensure_ascii=False)}\n"
        f"CONTEXT: {json.dumps(context, ensure_ascii=False)}\n"
        f"---END INPUT---\n"
        f"ВЕРНИ СТРОГО ОДИН JSON-ОБЪЕКТ ПО ШАБЛОНУ."
    )

    result = await _call_gemini_json(full_prompt)
    if isinstance(result, dict) and not result.get("error"):
        result.setdefault("profile", profile)
        if category:
            result.setdefault("category", category)
        if subcategory:
            result.setdefault("subcategory", subcategory)
    return result


async def _legacy_monthly_feedback_continuation(operator_id, month):
    # Сначала проверяем кэш
    cached_feedback = db.get_ai_feedback_cache(operator_id, month)
    if cached_feedback:
        logger.info(f"Returning cached AI feedback for operator {operator_id}, month {month}")
        return cached_feedback['feedback_data']

    raw = db.get_call_evaluations(operator_id, month=month)
    evaluated = [
        ev
        for ev in raw
        if isinstance(ev, dict)
        and not ev.get("is_imported")
        and not ev.get("is_draft")
        and ev.get("score") is not None
    ]

    if not evaluated:
        return {"error": "no_evaluated_calls", "month": month, "operator_id": operator_id}

    direction = _pick_direction(evaluated)
    criteria_payload = _build_monthly_criteria_payload(evaluated, direction)

    if not direction or not criteria_payload:
        return {"error": "missing_direction_or_criteria", "month": month, "operator_id": operator_id}

    comments_payload = []
    criteria_names = [c.get("criterion_name") for c in criteria_payload]
    for ev in evaluated:
        phone = ev.get("phone_number")
        evaluation_date = ev.get("evaluation_date")
        evaluator = ev.get("evaluator")
        call_comment = ev.get("comment")
        sv_comment = ev.get("sv_request_comment")

        if sv_comment:
            comments_payload.append(
                {
                    "type": "sv_request_comment",
                    "phone_number": phone,
                    "evaluation_date": evaluation_date,
                    "comment": sv_comment,
                }
            )
        if call_comment:
            comments_payload.append(
                {
                    "type": "call_comment",
                    "phone_number": phone,
                    "evaluation_date": evaluation_date,
                    "evaluator": evaluator,
                    "comment": call_comment,
                }
            )

        scores_arr = ev.get("scores") if isinstance(ev.get("scores"), list) else []
        crit_comments_arr = ev.get("criterion_comments") if isinstance(ev.get("criterion_comments"), list) else []
        for cidx in range(min(len(scores_arr), len(criteria_names), len(crit_comments_arr))):
            cmt = crit_comments_arr[cidx]
            if not cmt:
                continue
            comments_payload.append(
                {
                    "type": "criterion_comment",
                    "phone_number": phone,
                    "evaluation_date": evaluation_date,
                    "criterion": criteria_names[cidx] or f"Критерий {cidx + 1}",
                    "status": scores_arr[cidx],
                    "comment": cmt,
                }
            )

    comments_block = json.dumps(comments_payload, ensure_ascii=False)

    scores = [ev.get("score") for ev in evaluated if isinstance(ev.get("score"), (int, float))]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    direction_name = direction.get("name") if isinstance(direction, dict) else ""

    meta_block = (
        f"MONTH: {month}\n"
        f"DIRECTION: {direction_name}\n"
        f"EVALUATED_CALLS: {len(evaluated)}\n"
        f"AVG_SCORE: {avg_score}\n"
    )

    items_text = []
    for i, c in enumerate(criteria_payload, start=1):
        items_text.append(
            f"{i}. CRITERION_NAME: {c.get('criterion_name')}\n"
            f"   CRITERION_DESCRIPTION: {c.get('criterion_description')}\n"
            f"   WEIGHT: {c.get('weight')}\n"
            f"   IS_CRITICAL: {c.get('is_critical')}\n"
            f"   DEFICIENCY: {json.dumps(c.get('deficiency'), ensure_ascii=False)}\n"
            f"   STATS: {json.dumps(c.get('stats'), ensure_ascii=False)}\n"
            f"   EXAMPLES: {json.dumps(c.get('examples'), ensure_ascii=False)}\n"
        )
    items_block = "\n".join(items_text)

    full_prompt = (
        f"{MASTER_PROMPT_MONTHLY}\n"
        f"---META---\n{meta_block}\n---END META---\n"
        f"---CRITERIA---\n{items_block}\n---END CRITERIA---\n"
        f"---COMMENTS---\n{comments_block}\n---END COMMENTS---\n"
        f"ВЕРНИТЕ JSON ПО ШАБЛОНУ."
    )

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": generation_config,
        "safetySettings": safety_settings,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
            result = response.json()
            if "candidates" not in result or not result["candidates"]:
                logger.error("Gemini response empty or blocked.")
                return None
            candidate = result["candidates"][0]
            if "finishReason" in candidate and candidate["finishReason"] != "STOP":
                logger.warning(f"Finish reason: {candidate['finishReason']}")
            raw_text = candidate.get("content", {}).get("parts", [])[0].get("text", "")

            json_match = re.search(r'```json\s*(\{.*\})\s*```', raw_text, re.DOTALL)
            if json_match:
                cleaned = json_match.group(1)
            else:
                start = raw_text.find('{')
                end = raw_text.rfind('}')
                if start != -1 and end != -1 and end > start:
                    cleaned = raw_text[start:end + 1]
                else:
                    cleaned = raw_text

            try:
                parsed = json.loads(cleaned)
                # Сохраняем результат в кэш
                db.save_ai_feedback_cache(operator_id, month, parsed)
                logger.info(f"Cached AI feedback for operator {operator_id}, month {month}")
                return parsed
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}. Cleaned: {cleaned}")
                return {"error": "json_parse_error", "raw_response": raw_text, "cleaned": cleaned}

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error while contacting Gemini: {e}")
        return None
