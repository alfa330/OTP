"""Исключённые оценки: кнопка «Исключить оценку» в карточке ИИ-оценки.

Часть разговоров оценивать незачем: водитель ошибся номером, звонок сорвался
на первой секунде, это тестовая переписка, спам. Такая оценка только шумит —
висит в очереди ревью и тянет вниз средние. Человек исключает её из раздела,
и обязан сказать почему.

ЧТО ИСКЛЮЧАЕТСЯ. Субъект (разговор), а не прогон модели: «Переоценить» и смена
модели его не возвращают. Из раздела он уходит одним условием —
api._SUBJECT_NOT_EXCLUDED внутри api._SUBJECT_EXISTS, тем же местом, что и
номера «Реестра тестовых номеров», — то есть сразу из очереди, «Звонков» и
«Чатов», «Обзора» и «Сводки». Журнал оценок не трогается: исключение — решение
о ИИ-оценке, а не о качестве сотрудника. Вернуть можно в любой момент —
строка остаётся в таблице с отметкой restored_*.

ПРИЧИНА И КАТЕГОРИИ. Причина обязательна: категория из списка ИЛИ свои слова.
Свои слова без категории разбирает модель (categorize_pending): относит к
подходящей категории отдела или заводит новую. Так список категорий растёт из
того, что люди пишут, и в следующий раз причину выбирают одним нажатием.
Категории — свои у каждого отдела: у отдела продаж, СЗоВ и Тез КЦ разные
разговоры, и чужие причины в списке были бы шумом. Разбор идёт фоном, своим
потоком: человек не ждёт модель, а сбой модели не теряет исключение — оно
остаётся «без категории» и разбирается повторно при следующем исключении или
открытии окна (не больше CATEGORIZE_MAX_ATTEMPTS попыток на строку).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from . import config

REASON_MIN = 3        # «.» и «ок» — не причина
REASON_MAX = 500
TITLE_MAX = 60
CATEGORIZE_MAX_ATTEMPTS = 3
# После сбоя модели разбор отдела не повторяется раньше этой паузы: окно «Исключить
# оценку» будит разбор при каждом открытии, и без паузы сбой Z.ai (429, таймаут)
# превращался бы в серию платных повторов подряд.
CATEGORIZE_COOLDOWN_S = 600
CATEGORIZE_BATCH = 20
EXAMPLES_PER_CATEGORY = 3
EXAMPLE_MAX = 160

CATEGORY_PICKED = "picked"
CATEGORY_AI = "ai"

# «Другое» — категория-корзина (её заводит ИИ для непонятных причин). Выбрать её
# и ничего не написать — это обойти обязательную причину одним нажатием, поэтому
# с ней свои слова обязательны. Зеркало — isOtherCategory в exclusionRules.js.
OTHER_TITLES = ("другое", "прочее", "иное", "другая причина", "прочие")


def is_other_title(title) -> bool:
    return normalise_title(title).lower() in OTHER_TITLES

# Advisory-лок разбора причин отдела: при выкладке два инстанса живут разом, и
# каждый мог бы завести одну и ту же новую категорию. Первый ключ — пространство
# раздела (соседние: 71624 — миграция схемы, 71630 — ночная выборка).
_LOCK_SPACE = 71641


class ExclusionError(ValueError):
    """Отказ по существу — текст для человека. `status` — HTTP-код ручки."""

    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def normalise_reason(value) -> str:
    """Причина своими словами: пробелы схлопнуты, длина ограничена."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:REASON_MAX]


def _family(subject_kind: str) -> str:
    return "chats" if subject_kind in config.CHAT_SUBJECT_KINDS else "calls"


def _state_row(row) -> dict | None:
    """Строка выборки _STATE_SQL → состояние исключения для карточки и списков."""
    if not row:
        return None
    (ex_id, category_id, title, source, reason, by_id, by_name, at_text,
     attempts) = row
    return {
        "id": int(ex_id),
        "category_id": int(category_id) if category_id is not None else None,
        "category": title or None,
        "category_source": source,
        "reason": reason or "",
        "excluded_by": by_name or None,
        "excluded_by_id": int(by_id) if by_id is not None else None,
        "excluded_at": at_text,
        # Своими словами, а категорию ИИ ещё не подобрал (или сдался).
        "category_pending": category_id is None and int(attempts or 0) < CATEGORIZE_MAX_ATTEMPTS,
    }


# Время — по Алматы и строкой: timestamptz, нарезанный на клиенте, показал бы UTC.
_STATE_COLUMNS = """x.id, x.category_id, cat.title, x.category_source, x.reason,
                    x.excluded_by, u.name,
                    TO_CHAR(x.excluded_at AT TIME ZONE 'Asia/Almaty', 'DD.MM.YYYY HH24:MI'),
                    x.categorize_attempts"""
_STATE_JOINS = """LEFT JOIN ai_qa_exclusion_categories cat ON cat.id = x.category_id
                  LEFT JOIN users u ON u.id = x.excluded_by"""


def state_for(subject_kind: str, call_id: int, cur=None) -> dict | None:
    """Действующее исключение субъекта или None."""
    own = cur is None
    conn = config.connect_ro() if own else None
    try:
        cur = conn.cursor() if own else cur
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(f"""SELECT {_STATE_COLUMNS}
                          FROM ai_qa_exclusions x {_STATE_JOINS}
                         WHERE x.subject_kind = %s AND x.call_id = %s AND x.restored_at IS NULL""",
                    (str(subject_kind), int(call_id)))
        return _state_row(cur.fetchone())
    finally:
        if own:
            conn.close()


def exclude(*, subject_kind: str, call_id: int, department: str, direction_id, user_id: int,
            category_id=None, reason=None) -> dict:
    """Исключить субъект. Возвращает состояние исключения (форма state_for).

    Причина обязательна: категория отдела или свои слова. Уже исключённый —
    ExclusionError 409: второе исключение того же разговора значит, что кто-то
    успел раньше, и человеку надо увидеть ЕГО причину, а не затереть её своей."""
    department = config.normalise_department_code(department)
    if not department:
        raise ExclusionError("не удалось определить отдел разговора")
    text = normalise_reason(reason)
    if category_id in ("", None):
        category_id = None
    else:
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise ExclusionError("категория: ожидается число")
    if category_id is None and len(text) < REASON_MIN:
        raise ExclusionError("Укажите причину: выберите её из списка или напишите своими словами")
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            if category_id is not None:
                cur.execute("""SELECT title FROM ai_qa_exclusion_categories
                                WHERE id = %s AND department_code = %s""",
                            (category_id, department))
                row = cur.fetchone()
                if not row:
                    raise ExclusionError("Такой категории у отдела нет — обновите окно", 409,
                                         "category_missing")
                if is_other_title(row[0]) and len(text) < REASON_MIN:
                    raise ExclusionError(f"Для «{row[0]}» напишите причину своими словами")
            cur.execute(
                """INSERT INTO ai_qa_exclusions
                       (subject_kind, call_id, department_code, direction_id, category_id,
                        category_source, reason, excluded_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (subject_kind, call_id) WHERE restored_at IS NULL DO NOTHING
                   RETURNING id""",
                (str(subject_kind), int(call_id), department,
                 int(direction_id) if direction_id is not None else None,
                 category_id, CATEGORY_PICKED if category_id is not None else None,
                 text or None, int(user_id)))
            inserted = cur.fetchone()
            state = state_for(subject_kind, call_id, cur)
        if not inserted:
            error = ExclusionError("Эту оценку уже исключили", 409, "already_excluded")
            error.state = state
            raise error
        return state
    finally:
        conn.close()


def restore(*, subject_kind: str, call_id: int, user_id: int) -> bool:
    """Вернуть исключённый субъект в раздел. False — исключения уже нет."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE ai_qa_exclusions
                              SET restored_at = now(), restored_by = %s
                            WHERE subject_kind = %s AND call_id = %s AND restored_at IS NULL""",
                        (int(user_id), str(subject_kind), int(call_id)))
            return cur.rowcount > 0
    finally:
        conn.close()


def categories(department: str, subject_kind: str | None = None) -> dict:
    """Категории причин отдела для окна «Исключить оценку».

    Порядок — чем чаще выбирают, тем выше: сперва по числу исключений того же
    рода (звонки или переписка — у них свои частые причины), потом по отделу
    целиком. Считаются только действующие исключения: возвращённая по ошибке
    оценка категорию «популярнее» не делает.

    `pending` — сколько причин своими словами ИИ ещё не разобрал. Окно по нему
    решает, будить ли разбор: так повтор после сбоя модели не ждёт следующего
    исключения."""
    department = config.normalise_department_code(department)
    family_kinds = list(config.SUBJECT_FAMILIES.get(_family(subject_kind), ())) if subject_kind else []
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT c.id, c.title, c.description,
                      COUNT(x.id) FILTER (WHERE x.subject_kind = ANY(%s)) AS same_family,
                      COUNT(x.id) AS total
                 FROM ai_qa_exclusion_categories c
                 LEFT JOIN ai_qa_exclusions x
                        ON x.category_id = c.id AND x.restored_at IS NULL
                WHERE c.department_code = %s
                GROUP BY c.id
                ORDER BY 4 DESC, 5 DESC, lower(c.title)""",
            (family_kinds, department))
        items = [{"id": int(r[0]), "title": r[1], "description": r[2] or "",
                  "count": int(r[4] or 0)} for r in cur.fetchall()]
        cur.execute("""SELECT COUNT(*) FROM ai_qa_exclusions
                        WHERE department_code = %s AND restored_at IS NULL
                          AND category_id IS NULL AND reason IS NOT NULL
                          AND categorize_attempts < %s""",
                    (department, CATEGORIZE_MAX_ATTEMPTS))
        pending = int(cur.fetchone()[0] or 0)
        return {"items": items, "pending": pending}
    finally:
        conn.close()


# ── Разбор причин моделью ─────────────────────────────────────────────────────

_SYSTEM = """Ты помогаешь контролю качества колл-центра такси-сервиса. Проверяющие \
исключают из ИИ-оценки разговоры, которые оценивать незачем: звонок или переписка \
оказались нецелевыми, сорвались, это тест, спам и т. п. Причину каждый раз пишут \
своими словами. Твоя задача — разложить новые причины по категориям, чтобы в \
следующий раз проверяющий выбрал причину из списка одним нажатием.

Правила:
1. Если причина по смыслу входит в существующую категорию — верни её номер. Новую \
категорию заводи, только если ни одна существующая не подходит.
2. Название новой категории — 1–4 слова по-русски, с заглавной буквы, без точки в \
конце, обобщённо: без имён, телефонов, дат, сумм, названий таксопарков и городов. \
Примеры хороших названий: «Ошибся номером», «Тестовый звонок», «Звонок сорвался», \
«Нецелевое обращение», «Автоответчик», «Спам».
3. Не дроби: близкие по смыслу причины — одна категория. Если две новые причины \
в этом ответе про одно и то же — дай им одно и то же название.
4. Описание новой категории — одно короткое предложение: что в неё входит.
5. Причина непонятна или не объясняет, почему разговор не нужен, — категория «Другое».

Ответ — строго JSON-объект без пояснений вокруг:
{"items": [{"id": <номер причины>, "category_id": <номер существующей категории или 0>,
            "new_title": "<название новой категории или пустая строка>",
            "new_description": "<описание новой категории или пустая строка>"}]}
category_id = 0 означает новую категорию — тогда new_title обязателен. По одному \
элементу на каждую причину из запроса."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "category_id": {"type": "integer"},
                    "new_title": {"type": "string"},
                    "new_description": {"type": "string"},
                },
                "required": ["id", "category_id", "new_title", "new_description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

_KIND_WORD = {"calls": "звонок", "chats": "переписка"}


def normalise_title(value) -> str:
    """Название категории от модели → то, что встанет в список.

    Модель пишет и «тестовый звонок.», и «  Тестовый   звонок»: без приведения
    это были бы три разные категории в одном списке."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip("«»\"'").strip()
    text = text.rstrip(".;:!").strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    if len(text) > TITLE_MAX:
        text = text[:TITLE_MAX].rsplit(" ", 1)[0].strip() or text[:TITLE_MAX]
    return text


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_prompt(department_name: str, existing: list[dict], pending: list[dict]) -> str:
    """Запрос модели: категории отдела с примерами и новые причины."""
    lines = [f"ОТДЕЛ: {department_name}", "", "СУЩЕСТВУЮЩИЕ КАТЕГОРИИ:"]
    if existing:
        for item in existing:
            line = f"[{item['id']}] {item['title']}"
            if item.get("description"):
                line += f" — {_clip(item['description'], 200)}"
            examples = [f"«{_clip(text, EXAMPLE_MAX)}»" for text in (item.get("examples") or []) if text]
            if examples:
                line += f". Примеры причин: {'; '.join(examples)}"
            lines.append(line)
    else:
        lines.append("(пока нет — заведи первые)")
    lines += ["", "НОВЫЕ ПРИЧИНЫ:"]
    for item in pending:
        kind = _KIND_WORD.get(_family(item.get("subject_kind") or ""), "разговор")
        lines.append(f"#{item['id']} ({kind}): «{_clip(item['reason'], REASON_MAX)}»")
    return "\n".join(lines)


def _call_model(system: str, user: str) -> dict:
    from . import llm
    body = llm.build_body(model=config.CLAUDE_MODEL_HARD, system=system, user=user,
                          schema=_SCHEMA, max_tokens=4000, effort="low")
    return llm.post_body(body, timeout=60.0)


def plan_assignments(answer, pending_ids, existing_ids) -> dict:
    """Ответ модели → {id исключения: ('existing', id категории) | ('new', название, описание)}.

    Чужие номера причин и выдуманные номера категорий отбрасываются: строка без
    ответа останется «без категории» и разберётся следующим проходом, а не
    уедет в чужую категорию."""
    plan: dict[int, tuple] = {}
    items = answer.get("items") if isinstance(answer, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            ex_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if ex_id not in pending_ids or ex_id in plan:
            continue
        try:
            category_id = int(item.get("category_id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        if category_id and category_id in existing_ids:
            plan[ex_id] = ("existing", category_id)
            continue
        title = normalise_title(item.get("new_title"))
        if title:
            plan[ex_id] = ("new", title, _clip(item.get("new_description") or "", 240))
    return plan


def _department_name(cur, department: str) -> str:
    cur.execute("SELECT name FROM departments WHERE lower(code) = %s LIMIT 1", (department,))
    row = cur.fetchone()
    return row[0] if row and row[0] else department.upper()


def categorize_pending(department: str, *, model_call=None) -> dict:
    """Разобрать причины отдела, у которых ещё нет категории. Возвращает счётчики.

    Попытка засчитывается ДО вызова модели и отдельной транзакцией: упади модель
    или процесс посреди разбора, строка не будет вечно просить новый вызов."""
    department = config.normalise_department_code(department)
    model_call = model_call or _call_model
    out = {"pending": 0, "assigned": 0, "created": 0}
    conn = config.connect_rw()
    lock_key = zlib.crc32(department.encode("utf-8")) & 0x7FFFFFFF
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_SPACE, lock_key))
        if not cur.fetchone()[0]:
            return out
        try:
            cur.execute("""UPDATE ai_qa_exclusions
                              SET categorize_attempts = categorize_attempts + 1
                            WHERE id IN (SELECT id FROM ai_qa_exclusions
                                          WHERE department_code = %s AND restored_at IS NULL
                                            AND category_id IS NULL AND reason IS NOT NULL
                                            AND categorize_attempts < %s
                                          ORDER BY id LIMIT %s)
                        RETURNING id, reason, subject_kind""",
                        (department, CATEGORIZE_MAX_ATTEMPTS, CATEGORIZE_BATCH))
            pending = [{"id": int(r[0]), "reason": r[1], "subject_kind": r[2]}
                       for r in cur.fetchall()]
            out["pending"] = len(pending)
            if not pending:
                return out
            pending.sort(key=lambda item: item["id"])
            cur.execute(
                """SELECT c.id, c.title, c.description,
                          ARRAY(SELECT x.reason FROM ai_qa_exclusions x
                                 WHERE x.category_id = c.id AND x.reason IS NOT NULL
                                 ORDER BY x.id DESC LIMIT %s)
                     FROM ai_qa_exclusion_categories c
                    WHERE c.department_code = %s
                    ORDER BY c.id""",
                (EXAMPLES_PER_CATEGORY, department))
            existing = [{"id": int(r[0]), "title": r[1], "description": r[2] or "",
                         "examples": list(r[3] or [])} for r in cur.fetchall()]
            prompt = build_prompt(_department_name(cur, department), existing, pending)
            try:
                answer = model_call(_SYSTEM, prompt)
            except Exception:
                # Модель не ответила (сеть, 429, таймаут) — попытку не засчитываем: иначе
                # три открытия окна во время сбоя сожгли бы все попытки, и причины навсегда
                # остались бы без категории. Засчитывается ответ модели (и падение процесса
                # посреди вызова), а частоту повторов держит пауза в schedule_categorize.
                cur.execute("""UPDATE ai_qa_exclusions
                                  SET categorize_attempts = GREATEST(categorize_attempts - 1, 0)
                                WHERE id = ANY(%s)""", ([item["id"] for item in pending],))
                raise
            plan = plan_assignments(answer, {item["id"] for item in pending},
                                    {item["id"] for item in existing})
            out.update(_apply_plan(cur, department, plan))
            return out
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (_LOCK_SPACE, lock_key))
    finally:
        conn.close()


def _apply_plan(cur, department: str, plan: dict) -> dict:
    """Записать разбор. Новая категория с уже существующим названием (в другом
    регистре, заведённая параллельно) не дублируется — берётся та."""
    assigned = created = 0
    by_title: dict[str, int] = {}
    model = config.CLAUDE_MODEL_HARD
    for ex_id, decision in sorted(plan.items()):
        if decision[0] == "existing":
            category_id = decision[1]
        else:
            title, description = decision[1], decision[2]
            key = title.lower()
            category_id = by_title.get(key)
            if category_id is None:
                cur.execute("""INSERT INTO ai_qa_exclusion_categories
                                   (department_code, title, description, model)
                               VALUES (%s, %s, %s, %s)
                               ON CONFLICT (department_code, lower(title)) DO NOTHING
                               RETURNING id""",
                            (department, title, description or None, model))
                row = cur.fetchone()
                if row:
                    created += 1
                else:
                    cur.execute("""SELECT id FROM ai_qa_exclusion_categories
                                    WHERE department_code = %s AND lower(title) = lower(%s)""",
                                (department, title))
                    row = cur.fetchone()
                if not row:
                    continue
                category_id = int(row[0])
                by_title[key] = category_id
        cur.execute("""UPDATE ai_qa_exclusions
                          SET category_id = %s, category_source = %s
                        WHERE id = %s AND category_id IS NULL""",
                    (int(category_id), CATEGORY_AI, int(ex_id)))
        assigned += cur.rowcount
    return {"assigned": assigned, "created": created}


# Свой поток на одно место: разбор — один короткий вызов модели, и в общем пуле
# бота (четыре места на всё приложение) он ждал бы отбивок или держал их.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-qa-exclusions")
_guard = threading.Lock()
_running: set[str] = set()
_again: set[str] = set()
_cooldown_until: dict[str, float] = {}


def schedule_categorize(department: str) -> bool:
    """Разобрать причины отдела фоном. Пока разбор отдела идёт, новый запрос не
    ставит второй, а просит прогнать ещё раз после текущего: причина, записанная
    посреди вызова модели, иначе ждала бы следующего исключения."""
    department = config.normalise_department_code(department)
    if not department:
        return False
    with _guard:
        if time.monotonic() < _cooldown_until.get(department, 0.0):
            return False
        if department in _running:
            _again.add(department)
            return False
        _running.add(department)

    def run():
        try:
            for _ in range(3):
                with _guard:
                    _again.discard(department)
                try:
                    result = categorize_pending(department)
                    if result.get("pending"):
                        logging.info("ai-qa: причины исключений %s разобраны: %s", department, result)
                    with _guard:
                        _cooldown_until.pop(department, None)
                except Exception:
                    logging.exception("ai-qa: не удалось разобрать причины исключений %s", department)
                    with _guard:
                        _cooldown_until[department] = time.monotonic() + CATEGORIZE_COOLDOWN_S
                    return
                with _guard:
                    if department not in _again:
                        return
        finally:
            with _guard:
                _running.discard(department)
                _again.discard(department)

    _executor.submit(run)
    return True
