import asyncio
import json
import re
import sys
import httpx
from loguru import logger
from database import db, IT_TICKET_CATALOG
from collections import defaultdict
import os
from datetime import datetime, date

# loguru пишет СВОИМ стоком, мимо stdlib logging, поэтому фильтр секретов
# монолита (_SecretScrubber в bot_schedule2.py) его записи не видит вовсе.
# А у стока по умолчанию diagnose=True: при исключении loguru печатает
# ЗНАЧЕНИЯ переменных из кадров стека — то есть заголовки с ключом целиком.
# Проверено на loguru 0.7.3: ключ выходил в stderr открытым текстом.
logger.remove()
logger.add(sys.stderr, level=os.getenv('LOGURU_LEVEL', 'INFO'),
           backtrace=True, diagnose=False)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# По умолчанию ходим в Gemini через VERTEX AI, а не по ключу AI Studio.
#
# Дело не только в биллинге (Vertex платит с общего счёта проекта, а ключ
# AI Studio живёт на предоплаченных кредитах и отдаёт 429, когда те кончились;
# gemini-2.5-flash и 2.5-flash-lite новым проектам он вообще не выдаёт — 404).
# Дело и в безопасности: у Vertex постоянного ключа НЕТ. Авторизация — короткий
# OAuth-токен сервисного аккаунта, всегда в заголовке, так что утечь в адресе
# там нечему. Ключ AI Studio 22.08.2026 утёк именно через адрес. Тем же путём
# давно ходит помощник вики — см. wiki/ai/providers.py.
VERTEX_REGION = os.getenv('GEMINI_VERTEX_REGION', 'global')

_vertex_credentials = None


def _vertex_token():
    """Токен сервисного аккаунта. Кредентиалы кешируем: они обновляются сами."""
    global _vertex_credentials

    if _vertex_credentials is None:
        from google.oauth2 import service_account

        from call_qa import config as qa_config

        info = qa_config.google_sa_info()
        if not info:
            return None, None
        _vertex_credentials = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
    if not _vertex_credentials.valid:
        import google.auth.transport.requests as gtr

        _vertex_credentials.refresh(gtr.Request())
    return _vertex_credentials.token, _vertex_credentials.project_id


def gemini_endpoint(model: str):
    """Адрес и заголовки для одной модели: сначала Vertex, потом ключ AI Studio.

    Ключ остаётся запасным путём — на случай окружения без сервисного аккаунта.
    Секрет в обоих случаях уходит ЗАГОЛОВКОМ и никогда не попадает в адрес:
    httpx пишет в лог полный URL на уровне INFO.
    """
    if os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT'):
        try:
            token, project = _vertex_token()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f'Vertex недоступен ({type(exc).__name__}), '
                           f'идём по ключу AI Studio')
            token = None
        if token:
            host = ('aiplatform.googleapis.com' if VERTEX_REGION == 'global'
                    else f'{VERTEX_REGION}-aiplatform.googleapis.com')
            return (f'https://{host}/v1/projects/{project}/locations/{VERTEX_REGION}'
                    f'/publishers/google/models/{model}:generateContent',
                    {'Authorization': f'Bearer {token}'})
    return (f'https://generativelanguage.googleapis.com/v1beta/models/'
            f'{model}:generateContent',
            {'x-goog-api-key': GEMINI_API_KEY or ''})

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

    api_url, api_headers = gemini_endpoint("gemini-2.5-flash")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": generation_config,
        "safetySettings": safety_settings,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, json=payload, headers=api_headers)
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

IT_TICKET_PROMPT_CORE = """ТЫ — ассистент, который помогает супервайзеру колл-центра составить чёткий тикет в IT-отдел.

ГЛАВНЫЙ ПРИНЦИП IT-отдела: сисадмин должен прочитать заявку за 5 секунд и сразу понять, КУДА идти и
ЧТО взять/сделать. Нет конкретики — заявка висит часами. Запрещены расплывчатые «подойдите/помогите».

Тебе передаются:
- PROFILE: профиль каталога (op = Отдел продаж, szov = СЗоВ)
- CATALOG: категории и типовые проблемы профиля
- CATEGORY / SUBCATEGORY: выбранные категория и тип (могут быть пустыми)
- DESCRIPTION: свободное описание проблемы (кратко, на любом языке)
- FIELDS: уже заполненные поля формы (объект ключ→значение)
- CONTEXT: кто создаёт заявку (имя, роль, отдел/направление, дата/время)
- MASS_CONTEXT: масштаб (сколько РМ затронуто из размера кабинета/отдела) — для оценки приоритета

ОБЩИЕ ПРАВИЛА:
- Пиши по-русски, КРАТКО и КОНКРЕТНО, без воды и общих фраз. Каждая строка — факт.
- НЕ выдумывай факты. Пустые значения и ответы «ничего/нет/не знаю/все/—» считай ОТСУТСТВИЕМ информации:
  не вставляй их в тикет и не сочиняй текст — просто опусти.
- category и subcategory ВСЕГДА бери строго из CATALOG (точные строки).
- Автокоррекция темы: если CATEGORY/SUBCATEGORY не соответствуют сути DESCRIPTION — сам выбери правильные
  из CATALOG, поставь "category_adjusted": true и кратко поясни в "category_adjustment_note". Иначе false.

ТИП ЗАДАЧИ — определи сам по описанию:
- «Починить» (сломалось/глючит/не работает): ОБЯЗАТЕЛЬНО нужны ГДЕ (отдел + номер РМ; если наклейки РМ нет —
  ориентир) и ЧТО ИМЕННО случилось (конкретный симптом — чтобы заранее знать, какой инструмент брать).
- «Сделать» (создать доступ/настроить/установить): ОБЯЗАТЕЛЬНО нужны ЧТО сделать (конечная цель) и
  ДЛЯ КОГО (отдел + РМ или ФИО/логин; при необходимости очереди, ссылки, доп. данные).

Примеры хороших формулировок (ориентир по стилю и объёму):
«Мерцает экран, синие полоски, РМ16 СЗоВ»; «Дёргается мышка, РМ17 ОП, супервайзер»; «Не включается ПК, РОП»;
«Пропал интернет, вся правая сторона кабинета СЗоВ»; «Не открывается xyz.kz, ошибка DNS_PROBE_FINISHED, РМ67 ОП 18 этаж»;
«Создать внутренний номер сотруднику Тестова Ару, ОП, очереди …, тг @…».

ПРИОРИТЕТ — выбирай ЧЁТКО по реальному влиянию, НЕ ставь «high» по умолчанию. Базовый уровень — medium;
повышай только при явных признаках. Оценивай в первую очередь МАССОВОСТЬ (сколько людей/РМ затронуто) и
блокировку работы. Если в MASS_CONTEXT есть числа — используй процент затронутых РМ как главный ориентир.
- critical (критический): МАССОВЫЙ сбой — затронуто ≥50% отдела/кабинета, ИЛИ «лежит» целая сторона/ряд/этаж/
  весь кабинет, ИЛИ массовый отказ телефонии/интернета/Oktell/электричества у группы операторов, ИЛИ явно
  сказано «массовая». Работа группы полностью встала.
- high (высокий): затронуто примерно 25–49% (несколько РМ, но меньше половины) ИЛИ один оператор ПОЛНОСТЬЮ не
  может работать без обходного пути (ПК не включается, нет звонков у единственного РМ) — и это срочно блокирует.
- medium (средний): работа возможна / есть обходной путь; единичная частичная проблема (замена мыши/клавиатуры,
  периодический глюк, один РМ с неполным сбоем); обычная задача «сделать» с дедлайном.
- low (низкий): мелочь без влияния на работу, косметика, плановая/несрочная задача «сделать».
Правило массовости: 1 РМ — обычно medium (high только при полной блокировке); несколько РМ (<50%) — high;
≥50% отдела/кабинета или явная массовость — critical. Один человек с мелкой неполадкой — это НЕ high.

ТЕКСТ ТИКЕТА (ticket):
- title: короткий заголовок (до 80 символов). summary: 1 предложение сути.
- markdown: ГОТОВЫЙ КОРОТКИЙ текст для Telegram, читается за 5 секунд. Правила:
  • Только HTML-теги Telegram: <b>…</b>, <i>…</i>, <code>…</code>. Без *, #, markdown, <br>, <ul>, <p>.
  • Текст ошибки/код — в <code>…</code>.
  • Каждый пункт — с НОВОЙ строки в формате «<эмодзи> <b>Метка:</b> значение». Между пунктами
    один перенос строки (\\n), без двойных пустых строк. Включай ТОЛЬКО строки с конкретикой;
    пустое/«ничего»/«все» — пропускай (не пиши «не указано»). Без воды и общих фраз.
  • НЕ дублируй категорию, приоритет и автора — они добавляются отдельно.
  • Каркас — бери ТОЛЬКО нужные строки, каждая на своей строке:
    для «починить»:
      🔧 <b>Что:</b> <симптом>
      📍 <b>Где:</b> отдел + № РМ (или ориентир)
      🕒 <b>Когда/частота:</b> …
      ✅ <b>Уже пробовали:</b> …
    для «сделать»:
      🎯 <b>Сделать:</b> <конечная цель>
      👤 <b>Кому:</b> отдел + РМ / ФИО
      📝 <b>Детали:</b> очереди / ссылки / доп. данные

ПОЛЯ ФОРМЫ (form.fields) — ОДИН набор полей под задачу (это и есть «уточнения», отдельный список вопросов
НЕ создавай). Поле: key (латиница snake_case), label (рус.), type
(text|textarea|select|date|time|number|workplace), required (bool), placeholder, options (для select), hint, value.
ТИП workplace — для поля «рабочее место / № РМ» (где проблема или для кого делается): у супервайзера
откроется визуальная схема кабинетов, он кликом выберет нужные РМ; key такого поля — workplace.
Обязательные поля под тип задачи: для «починить» — «Где (№ РМ)» (type=workplace) и «Что случилось»;
для «сделать» — «Что сделать» и «Для кого (№ РМ)» (type=workplace, если применимо). Плюс при уместности:
когда началось, частота, массовость, что уже пробовали, текст ошибки, ссылка/скрин.
Обычно 3–6 полей. required=true только для критичных (Где, Что). Предзаполняй value из DESCRIPTION
(для workplace — перечисли распознанные номера РМ, напр. «РМ 16»; если их нет, оставь value пустым).

КРИТИЧНЫЙ МИНИМУМ для готовой заявки: для «починить» — ГДЕ и ЧТО; для «сделать» — ЧТО и ДЛЯ КОГО.

ЧТО ПОПРОБОВАТЬ ДО ЗАЯВКИ (checks) — только для задач «починить»; для «сделать» верни [].
Это первое, что спросит сисадмин («что уже пробовали?»), и половина таких заявок закрывается
на месте. Дай 2–4 проверки, которые супервайзер сделает САМ за минуту, БЕЗ прав администратора
и без захода в серверные настройки.
Требования к каждому пункту:
- Конкретное физическое действие с конкретным объектом: что нажать, что переподключить,
  что посмотреть. Формулируй так, чтобы было понятно, выполнено оно или нет.
- Строго под ЭТОТ симптом из DESCRIPTION. Общие советы уровня «проверьте настройки»,
  «убедитесь, что всё работает», «перезагрузите ПК» (без указания, что именно это проверяет),
  «обратитесь к специалисту», «проверьте кабель» (какой?) — ЗАПРЕЩЕНЫ.
- Порядок: сначала то, что чаще всего и решает проблему.
- Если проверять реально нечего (сгорел монитор, не включается ПК, нужен доступ) — верни [],
  НЕ придумывай пункты ради количества.
Хорошо: «Переподключить гарнитуру в другой USB-порт»; «В Windows → Параметры звука выбрать
устройство вывода — гарнитуру, а не монитор»; «Проверить колёсико громкости на самой
гарнитуре и кнопку mute»; «Позвонить на тестовый номер с соседнего РМ той же гарнитурой».
Плохо: «Проверить оборудование»; «Перезагрузить»; «Убедиться, что гарнитура работает».
"""

IT_TICKET_PROMPT_DRAFT = """ТВОЯ ЗАДАЧА СЕЙЧАС (первый проход):
1. Определи правильные category и subcategory из CATALOG.
2. Сформируй form.fields по правилам выше и priority по блоку ПРИОРИТЕТ (учитывай MASS_CONTEXT).
3. Составь ticket (title, summary, markdown) из того, что уже известно.
4. status: если в DESCRIPTION/FIELDS уже есть КРИТИЧНЫЙ МИНИМУМ — ставь "ready" (заявку можно отправлять
   как есть, поля остаются для необязательного уточнения). Если критичного не хватает — ставь "draft"
   и пометь недостающие поля required=true.
"""

IT_TICKET_PROMPT_FINALIZE = """ТВОЯ ЗАДАЧА СЕЙЧАС (сборка финальной заявки):
1. Собери заявку из имеющегося. status="need_more_info" возвращай ТОЛЬКО если не хватает КРИТИЧНОГО
   МИНИМУМА: тогда добавь эти поля в form.fields с required=true. Иначе status="ready".
2. При status="ready" составь ticket (title, summary, markdown) по правилам выше.
"""

IT_TICKET_PROMPT_JSON = """ФОРМАТ ОТВЕТА — СТРОГО ОДИН JSON-объект, без markdown-ограждений и текста вокруг:
{
  "status": "draft" | "need_more_info" | "ready",
  "profile": "op" | "szov",
  "category": "<категория из CATALOG>",
  "subcategory": "<подкатегория из CATALOG>",
  "category_adjusted": false,
  "category_adjustment_note": "",
  "priority": "low" | "medium" | "high" | "critical",
  "form": { "fields": [ {"key": "...", "label": "...", "type": "text", "required": false, "placeholder": "", "options": [], "hint": "", "value": ""} ] },
  "checks": ["<что попробовать до заявки>"],
  "ticket": { "title": "...", "summary": "...", "markdown": "..." }
}
"""

# Схема ответа для structured output (Gemini responseSchema / OpenAI-совместимый json_schema).
# Модель декодирует строго в эту форму — не тратит токены на ограждения ```json и не ломает парсер.
IT_TICKET_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["draft", "need_more_info", "ready"]},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "category_adjusted": {"type": "boolean"},
        "category_adjustment_note": {"type": "string"},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "form": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "label": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["text", "textarea", "select", "date", "time", "number", "workplace"],
                            },
                            "required": {"type": "boolean"},
                            "placeholder": {"type": "string"},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "hint": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["key", "label", "type"],
                    },
                },
            },
            "required": ["fields"],
        },
        # Проверки «что попробовать до заявки». Обязательны в схеме (пустой список —
        # допустимый ответ для задач «сделать»), иначе модель их просто не заполнит.
        "checks": {"type": "array", "items": {"type": "string"}},
        "ticket": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "markdown": {"type": "string"},
            },
            "required": ["title", "summary", "markdown"],
        },
    },
    # ВАЖНО: необязательные поля модель под схемой просто не заполняет. Поэтому
    # subcategory и category_adjusted* обязаны быть здесь: без subcategory на экране
    # остаётся пустым «Тип проблемы», а без category_adjusted фронт не применит
    # исправленную ИИ тему (см. callAi в ITTicketModal.jsx) — пользователь останется
    # со своей неверной категорией. Пустая строка/false — допустимые значения.
    "required": [
        "status", "category", "subcategory", "priority",
        "category_adjusted", "category_adjustment_note", "form", "checks", "ticket",
    ],
}


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


# Коды ответа, при которых имеет смысл повторить запрос (перегрузка / временный сбой)
GEMINI_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 404 = модель недоступна для ключа → пробуем следующую модель в цепочке
GEMINI_FALLBACK_STATUS = GEMINI_RETRYABLE_STATUS | {404}

# Цепочка моделей: если первая перегружена / недоступна / таймаутит — берём следующую.
# У моделей раздельные пулы мощностей, поэтому перегрузка одной не означает перегрузку всех.
#
# Порядок — по скорости, а не по «крутизне»: задача (разобрать описание и собрать JSON)
# простая, и самая лёгкая модель решает её не хуже, но заметно быстрее. У flash-lite ещё и
# выше лимит бесплатных запросов, поэтому она первая.
# gemini-2.0-flash убрана 22.08.2026: Google её отключил совсем, на любой запрос
# 404 «no longer available». Третьей ступенью взята gemini-3.5-flash-lite —
# проверена на Vertex тем же промптом тикета.
DEFAULT_GEMINI_MODEL_CHAIN = ["gemini-2.5-flash-lite", "gemini-2.5-flash",
                              "gemini-3.5-flash-lite"]

# Модели, у которых «мышление» (reasoning) можно выключить нулевым бюджетом. Для этой задачи
# оно не нужно, а стоит нескольких секунд и токенов на каждый запрос.
# gemini-2.5-pro сюда НЕ входит: у неё мышление не отключается и нулевой бюджет вернёт 400.
GEMINI_THINKING_OFF_MODELS = ("gemini-2.5-flash",)

# Groq (бесплатный тариф) — OpenAI-совместимый API, отдаёт токены в разы быстрее Gemini.
# Подключается сам, как только в окружении появляется GROQ_API_KEY.
#
# Замеры 2026-07-31 на реальном промпте IT-тикета (~3000 токенов на запрос):
#   llama-3.3-70b-versatile — 1,9с, лимит 12000 ток/мин → ~4 заявки в минуту;
#   openai/gpt-oss-20b      — 1,9с, лимит  8000 ток/мин → ~2 заявки в минуту;
#   openai/gpt-oss-120b     — 4,7с (модель «рассуждает»), лимит 8000 — не берём.
# Лимит токенов у каждой модели свой, поэтому вторая модель — это запас на пик,
# а не дубль: упёрлись в лимит первой → сразу пробуем вторую, и только потом Gemini.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEFAULT_GROQ_MODEL_CHAIN = ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"]
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Таймаут одной попытки. Держим коротким: лучше быстро уйти на следующую модель,
# чем ждать зависшую. Переопределяется env IT_TICKET_AI_TIMEOUT.
DEFAULT_IT_TICKET_TIMEOUT = 20.0


def _it_ticket_timeout() -> float:
    try:
        value = float(os.getenv("IT_TICKET_AI_TIMEOUT", "") or DEFAULT_IT_TICKET_TIMEOUT)
    except (TypeError, ValueError):
        return DEFAULT_IT_TICKET_TIMEOUT
    return value if value > 0 else DEFAULT_IT_TICKET_TIMEOUT


def _gemini_model_chain():
    """Цепочка моделей; можно переопределить через env GEMINI_MODEL_CHAIN (через запятую)."""
    raw = os.getenv("GEMINI_MODEL_CHAIN", "")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    return models or list(DEFAULT_GEMINI_MODEL_CHAIN)


def _groq_model_chain():
    """Цепочка моделей Groq; переопределяется env GROQ_MODEL_CHAIN (через запятую)."""
    raw = os.getenv("GROQ_MODEL_CHAIN", "")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    return models or list(DEFAULT_GROQ_MODEL_CHAIN)


def _it_ticket_provider_chain():
    """Общая цепочка «провайдер:модель» для IT-тикетов.

    Groq (если задан ключ) идёт первым — он самый быстрый; Gemini остаётся запасным,
    так что при отсутствии/сбое Groq всё продолжает работать как раньше.
    Полностью переопределяется env IT_TICKET_AI_CHAIN, напр.:
        IT_TICKET_AI_CHAIN=groq:llama-3.3-70b-versatile,gemini:gemini-2.5-flash-lite
    """
    raw = os.getenv("IT_TICKET_AI_CHAIN", "")
    if raw.strip():
        chain = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            provider, _, model = item.partition(":")
            provider = provider.strip().lower()
            model = model.strip()
            if provider in ("groq", "gemini") and model:
                chain.append((provider, model))
        if chain:
            return chain

    chain = []
    if GROQ_API_KEY:
        chain.extend(("groq", m) for m in _groq_model_chain())
    if GEMINI_API_KEY:
        chain.extend(("gemini", m) for m in _gemini_model_chain())
    return chain


def _gemini_generation_config(model: str, plain: bool = False) -> dict:
    """generationConfig под быстрый структурированный ответ.

    Ключевое для скорости: thinkingBudget=0 у моделей 2.5-flash* — без него Gemini тратит
    несколько секунд на скрытые рассуждения, которые этой задаче не нужны.

    plain=True — «безопасный» вариант без ускоряющих полей: используется как запасной,
    если модель отвергла запрос (400). Так новая настройка не может сломать фичу целиком.
    """
    config = {
        "temperature": 0.2,
        "topP": 0.9,
        "maxOutputTokens": 4096,
    }
    if plain:
        return config
    config["responseMimeType"] = "application/json"
    config["responseSchema"] = IT_TICKET_RESPONSE_SCHEMA
    if model.startswith(GEMINI_THINKING_OFF_MODELS):
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return config


async def _gemini_generate_once(model: str, prompt: str, timeout: float, attempts: int):
    """Запрос к одной модели Gemini (с ретраями внутри). Возвращает (result, try_next).

    try_next=True → имеет смысл попробовать следующую модель цепочки (перегрузка/таймаут/404).
    result: распарсенный dict при успехе; {'error': <code>} или None при ошибке.
    """
    api_url, api_headers = gemini_endpoint(model)
    plain_config = False
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": _gemini_generation_config(model),
        "safetySettings": safety_settings,
    }
    attempt = 0
    while attempt < attempts:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(api_url, json=payload, headers=api_headers)

            # 400 на ускоренной конфигурации (схема ответа / нулевой бюджет мышления)
            # — один раз переспрашиваем ту же модель «как раньше», без этих полей.
            # Отдельная попытка: она не должна съедать бюджет обычных ретраев.
            if response.status_code == 400 and not plain_config:
                logger.warning(
                    f"IT-ticket Gemini {model} → 400 на ускоренной конфигурации, "
                    f"повтор без неё: {response.text[:200]}"
                )
                plain_config = True
                payload["generationConfig"] = _gemini_generation_config(model, plain=True)
                attempt -= 1
                continue

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
            # Сбой одной модели (блокировка, битый JSON) — повод отдать запрос следующей,
            # а не показывать супервайзеру ошибку: у моделей разные фильтры и декодеры.
            if "candidates" not in result or not result["candidates"]:
                logger.error(f"Gemini {model}: пустой/заблокированный ответ (IT ticket).")
                return {"error": "ai_blocked"}, True
            candidate = result["candidates"][0]
            raw_text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
            cleaned = _extract_json_block(raw_text)
            try:
                return json.loads(cleaned), False
            except json.JSONDecodeError as e:
                logger.error(f"IT-ticket JSON parse error ({model}): {e}. Cleaned: {cleaned[:300]}")
                return {"error": "json_parse_error", "raw_response": raw_text}, True

        except (httpx.TimeoutException, httpx.TransportError) as e:
            logger.warning(
                f"IT-ticket Gemini {model} network/timeout (попытка {attempt}/{attempts}): {e!r}"
            )
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 6))
                continue
            return {"error": "ai_timeout"}, True
        except httpx.HTTPStatusError as e:
            logger.error(f"IT-ticket HTTP error ({model}): {e.response.status_code} - {e.response.text[:300]}")
            return {"error": "ai_failed"}, True
        except Exception as e:
            logger.exception(f"Unexpected error contacting Gemini ({model}, IT ticket): {e}")
            return {"error": "ai_failed"}, True

    return {"error": "ai_unavailable"}, True


async def _groq_generate_once(model: str, prompt: str, timeout: float, attempts: int):
    """Запрос к одной модели Groq. Контракт возврата — как у _gemini_generate_once."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(GROQ_API_URL, json=payload, headers=headers)

            if response.status_code in GEMINI_FALLBACK_STATUS:
                logger.warning(
                    f"IT-ticket Groq {model} → {response.status_code} (попытка {attempt}/{attempts})"
                )
                if attempt < attempts and response.status_code in GEMINI_RETRYABLE_STATUS:
                    await asyncio.sleep(min(2 ** attempt, 6))
                    continue
                return {"error": "ai_unavailable", "status": response.status_code}, True

            response.raise_for_status()
            result = response.json()
            choices = result.get("choices") or []
            if not choices:
                logger.error(f"Groq {model}: пустой ответ (IT ticket).")
                return {"error": "ai_blocked"}, True
            raw_text = (choices[0].get("message") or {}).get("content") or ""
            cleaned = _extract_json_block(raw_text)
            try:
                return json.loads(cleaned), False
            except json.JSONDecodeError as e:
                logger.error(f"IT-ticket JSON parse error (groq {model}): {e}. Cleaned: {cleaned[:300]}")
                # Битый JSON от одной модели — повод попробовать следующую, а не сдаваться.
                return {"error": "json_parse_error", "raw_response": raw_text}, True

        except (httpx.TimeoutException, httpx.TransportError) as e:
            logger.warning(f"IT-ticket Groq {model} network/timeout (попытка {attempt}/{attempts}): {e!r}")
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 6))
                continue
            return {"error": "ai_timeout"}, True
        except httpx.HTTPStatusError as e:
            logger.error(f"IT-ticket Groq HTTP error ({model}): {e.response.status_code} - {e.response.text[:300]}")
            return {"error": "ai_failed"}, True
        except Exception as e:
            logger.exception(f"Unexpected error contacting Groq ({model}, IT ticket): {e}")
            return {"error": "ai_failed"}, True

    return {"error": "ai_unavailable"}, True


async def _call_ai_json(full_prompt: str, timeout: float | None = None, attempts: int = 1) -> dict | None:
    """Вызывает ЦЕПОЧКУ «провайдер:модель» до первого удачного ответа.

    Если модель перегружена (503/429), таймаутит или недоступна (404) — берётся следующая.
    Возвращает:
      - dict с результатом при успехе;
      - {'error': <code>} для понятных клиенту ошибок (ai_unavailable / ai_timeout /
        ai_blocked / json_parse_error);
      - None, если не настроен ни один ключ.
    """
    chain = _it_ticket_provider_chain()
    if not chain:
        logger.error("IT-ticket: не настроен ни один ключ ИИ (GEMINI_API_KEY / GROQ_API_KEY).")
        return None

    if timeout is None:
        timeout = _it_ticket_timeout()

    last_error = {"error": "ai_unavailable"}
    for idx, (provider, model) in enumerate(chain):
        runner = _groq_generate_once if provider == "groq" else _gemini_generate_once
        result, try_next = await runner(model, full_prompt, timeout, attempts)
        if not try_next:
            return result
        if isinstance(result, dict) and result.get("error"):
            last_error = result
        if idx + 1 < len(chain):
            logger.warning(f"IT-ticket: переключаюсь на следующую модель после {provider}:{model}")
    return last_error


def _effective_it_catalog():
    """Действующий каталог (с учётом правок админа), фолбэк на дефолт."""
    try:
        catalog = db.get_it_ticket_catalog()
        if isinstance(catalog, dict) and catalog:
            return catalog
    except Exception:
        logger.exception("Failed to load editable IT-ticket catalog; using default")
    return IT_TICKET_CATALOG


def _it_catalog_block(profile: str) -> str:
    catalog = _effective_it_catalog()
    prof = profile if profile in catalog else "op"
    cat = catalog.get(prof, {})
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
    mass_context = str(payload.get("mass_context") or "").strip()

    # Дополнительные инструкции от админа/главы отдела (актуальные изменения),
    # которые могут быть не отражены в мастер-промпте. При конфликте — приоритетнее.
    try:
        extra_instructions = db.get_combined_it_ticket_instructions(profile) or ""
    except Exception:
        logger.exception("Failed to load IT-ticket admin instructions")
        extra_instructions = ""

    # Пустой блок инструкций не отправляем вовсе — это лишние токены в каждом запросе.
    instructions_block = (
        "---АКТУАЛЬНЫЕ ИНСТРУКЦИИ ОТ АДМИНИСТРАТОРА / ГЛАВЫ ОТДЕЛА---\n"
        "Эти инструкции добавлены вручную и описывают недавние изменения. "
        "При конфликте с общими правилами выше — следуй ИМ.\n"
        f"{extra_instructions}\n"
        "---КОНЕЦ ИНСТРУКЦИЙ---\n"
    ) if extra_instructions.strip() else ""

    # В запрос уходит только блок нужного режима: инструкции второго режима модели
    # не нужны, а каждый лишний токен промпта — это время ответа.
    mode_block = IT_TICKET_PROMPT_DRAFT if mode == "draft" else IT_TICKET_PROMPT_FINALIZE

    full_prompt = (
        f"{IT_TICKET_PROMPT_CORE}\n"
        f"{mode_block}\n"
        f"{IT_TICKET_PROMPT_JSON}\n"
        f"{instructions_block}"
        f"---INPUT---\n"
        f"PROFILE: {profile}\n"
        f"CATALOG:\n{_it_catalog_block(profile)}\n"
        f"CATEGORY: {category}\n"
        f"SUBCATEGORY: {subcategory}\n"
        f"DESCRIPTION: {description}\n"
        f"FIELDS: {json.dumps(fields, ensure_ascii=False)}\n"
        f"CONTEXT: {json.dumps(context, ensure_ascii=False)}\n"
        f"MASS_CONTEXT: {mass_context if mass_context else '(масштаб не указан — оцени по описанию)'}\n"
        f"---END INPUT---\n"
        f"ВЕРНИ СТРОГО ОДИН JSON-ОБЪЕКТ ПО ШАБЛОНУ."
    )

    result = await _call_ai_json(full_prompt)
    if isinstance(result, dict) and not result.get("error"):
        result.setdefault("profile", profile)
        if category:
            result.setdefault("category", category)
        if subcategory:
            result.setdefault("subcategory", subcategory)
        # Пользователь не выбирал категорию — исправлять было нечего, что бы ни ответила
        # модель. Иначе на экране всплывает баннер «ИИ скорректировал тему» на пустом месте.
        if not category and not subcategory:
            result["category_adjusted"] = False
            result["category_adjustment_note"] = ""
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

    api_url, api_headers = gemini_endpoint("gemini-2.5-flash")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": generation_config,
        "safetySettings": safety_settings,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, json=payload, headers=api_headers)
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
