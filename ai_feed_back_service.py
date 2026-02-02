import json
import re
import httpx
from loguru import logger
from database import db
from collections import defaultdict
import os

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

MASTER_PROMPT_MONTHLY = """ТЫ — Jana, опытный и дружелюбный тренер/ментор для операторов колл-центра.
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

# Рекомендуемые параметры генерации (более детерминированно)
generation_config = {
    "temperature": 0.2,
    "topP": 0.9,
    "topK": 40,
    "maxOutputTokens": 1500,
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


async def generate_monthly_feedback_with_ai(operator_id: int, month: str) -> dict | None:
    if not GEMINI_API_KEY:
        logger.error("Gemini API key is not configured.")
        return None

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
