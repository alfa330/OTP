# -*- coding: utf-8 -*-
"""Ежедневная выборка на ИИ-оценку: до N случайных разговоров на направление в день.

Решение владельца 30.09.2026. Каждую ночь по каждому направлению каждого отдела
раздела берётся до config.AI_QA_DAILY_SAMPLE_SIZE (30) случайных ЗВОНКОВ и
столько же ПЕРЕПИСОК вчерашнего дня по Алматы и оценивается боевой моделью.
Оценки ложатся в «Очередь ревью» на день разговора — там их проверяют СВ.

Что здесь неочевидно:

* Выборка записывается в ai_qa_daily_samples, строка на субъект. Второй проход
  того же дня ДОБИРАЕТ направление до N, а не берёт ещё N. Сорвавшийся субъект
  (записи нет, оценка не удалась N раз) из счёта выпадает, и следующий проход
  того же дня заменяет его другим.
* Звонки приносят адаптеры АТС (CallSource): ОП — касания CDR (запись везёт мост
  по заказу, обычно минуты), СЗоВ — Oktell, Тез КЦ — Binotel. Живут адаптеры в
  bot_schedule2 рядом с «Из АТС» — там их хелперы. Звонок ложится в
  imported_calls со статусом IMPORT_STATUS, то есть ВНЕ журнала и плана
  прослушки: журнал показывает только 'not_evaluated', норма «Деления звонков»
  считает их же. Двести с лишним чужих «Не оценён» в сутки засорили бы журнал
  супервайзеров и закрыли бы им норму.
* Переписки — из тех же пулов, что «Случайный чат» раздела
  (api._CHAT_CANDIDATE_SQL): тот же гейт атрибуции и тот же день разговора, что
  у очереди. У СЗоВ готовых снапшотов Chat2Desk за день 5–30, поэтому недостающее
  выборка скачивает сама (ChatSource из bot_schedule2) — пока остаток месячной
  квоты их API выше запаса AI_QA_DAILY_SAMPLE_C2D_QUOTA_RESERVE: на той же квоте
  живёт суточный синк статистики.
* Длительность звонков — своё окно выборки (от 5 с, без верхней границы), а не
  окно «Деления звонков».
* Оценка — ровно путь открытия карточки (api.review_payload): тот же отпечаток,
  поэтому разговор из выборки не откроется «устаревшим» и не оценится дважды.
* Проход держит advisory-лок: при выкладке два инстанса живут одновременно, и
  без лока каждый набрал бы свои N.
"""
from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import config
from . import subjects as subjects_mod

ALMATY = ZoneInfo("Asia/Almaty")

FAMILY_CALLS = "calls"
FAMILY_CHATS = "chats"

STATUS_PICKED = "picked"
STATUS_EVALUATED = "evaluated"
STATUS_FAILED = "failed"

# imported_calls.status у звонков выборки. Колонка без CHECK, а журнал, норма
# «Деления звонков» и «Случайный звонок» журнала смотрят только на
# 'not_evaluated' / 'evaluated' — строка выборки им не видна. Оценка человеком
# из карточки переводит её в 'evaluated' тем же UPDATE ... status != 'evaluated'.
IMPORT_STATUS = "ai_sample"

AUDIO_READY = "ready"
AUDIO_PENDING = "pending"
AUDIO_MISSING = "missing"

# Пространство advisory-локов call_qa: 71623…71629 заняты субъектами и миграцией.
_PASS_LOCK = (71630, 1)
# Столько отказов АТС подряд на подтяжке — и отдел в этом проходе бросаем:
# лежащую АТС перебор не лечит, а каждый отказ — ещё один запрос к ней.
_IMPORT_ERROR_LIMIT = 3
# Как часто проход проверяет, доехали ли записи моста.
_AUDIO_POLL_S = 60
# Сколько дней назад проход дооценивает то, что не успел прошлый (запись не
# доехала, модель отказала). Выбирает он при этом только свой день; что старше
# окна и так и не оценилось, закрывается как сорвавшееся — иначе висело бы
# «ждёт» вечно.
_EVALUATE_LOOKBACK_DAYS = 2
# Оценка идёт порциями: между ними проход перечитывает выборку, и звонок ОП,
# чья запись доехала от моста, встаёт в очередь сразу, а не после всех чатов.
_EVALUATE_CHUNK_PER_WORKER = 4


@dataclass(frozen=True)
class CallSource:
    """АТС отдела для выборки звонков.

    candidates(day) → звонки дня: dict с 'direction_id' (каноническое направление
    оператора, см. operator_directions), 'operator_id' и 'key' (id звонка в АТС,
    он же imported_calls.external_id); прочие поля адаптер кладёт для себя и
    получает обратно в import_call.
    import_call(candidate) → id строки imported_calls со статусом IMPORT_STATUS
    или None, если звонок взять нельзя (записи нет, уже лежит в пуле).
    audio_state(imported_id) → AUDIO_READY / AUDIO_PENDING / AUDIO_MISSING."""
    department: str
    candidates: Callable
    import_call: Callable
    audio_state: Callable


@dataclass(frozen=True)
class ChatSource:
    """Переписки, которых в базе ещё нет: их скачивает адаптер (СЗоВ — Chat2Desk).

    candidates(day) → как у CallSource; import_chat(candidate) → id субъекта (снапшота),
    уже прошедшего гейт честной оценки, или None — переписки нет или её нельзя
    честно приписать одному оператору. Выборка зовёт его, только когда готового
    пула (_sample_chats) на направление не хватило."""
    department: str
    subject_kind: str
    candidates: Callable
    import_chat: Callable


def sample_day(now: datetime | None = None) -> date:
    """Вчерашний день по Алматы. Сервер живёт в UTC, и «вчера» считаем строго от
    местного времени: в 03:00 по Алматы по UTC ещё позавчерашний вечер."""
    now = now or datetime.now(ALMATY)
    return now.astimezone(ALMATY).date() - timedelta(days=1)


def parse_day(value) -> date:
    """'YYYY-MM-DD' / date → date; ValueError на мусоре."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()


def operator_directions(operator_ids) -> dict:
    """{id оператора: каноническое направление}. Берётся users.direction_id —
    ровно так направление звонка из АТС определяет сама оценка
    (subjects._load_imported_call), иначе выборка и шкала разошлись бы."""
    ids = sorted({int(value) for value in (operator_ids or []) if value is not None})
    if not ids:
        return {}
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, COALESCE(d.canonical_id, d.id)
                             FROM users u
                             LEFT JOIN directions d ON d.id = u.direction_id
                            WHERE u.id = ANY(%s)""", (ids,))
            return {int(user_id): int(direction_id)
                    for user_id, direction_id in cur.fetchall() if direction_id is not None}
    finally:
        conn.close()


# ── хранилище выборки ─────────────────────────────────────────────────────────

@contextmanager
def _pass_lock():
    """Сессионный advisory-лок прохода на отдельном соединении. autocommit — чтобы
    лок не держал открытой транзакцию на часы; закрытие соединения снимает лок."""
    conn = config.connect_rw()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", _PASS_LOCK)
            acquired = bool(cur.fetchone()[0])
        yield acquired
    finally:
        conn.close()


def _cell_counts(cur, day) -> dict:
    """{(направление, семейство): сколько в выборке} — без сорвавшихся: их место
    свободно, и следующий проход того же дня возьмёт замену."""
    cur.execute("""SELECT direction_id, family, count(*)
                     FROM ai_qa_daily_samples
                    WHERE sample_day = %s AND status <> %s
                    GROUP BY direction_id, family""", (day, STATUS_FAILED))
    return {(int(direction_id), str(family)): int(n) for direction_id, family, n in cur.fetchall()}


def _record(cur, day, department, direction_id, family, subject_kind, subject_id) -> bool:
    cur.execute("""INSERT INTO ai_qa_daily_samples
                       (sample_day, department_code, direction_id, family, subject_kind, subject_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (subject_kind, subject_id) DO NOTHING
                   RETURNING id""",
                (day, department, int(direction_id), family, subject_kind, int(subject_id)))
    return cur.fetchone() is not None


def _direction_departments(cur, direction_ids) -> dict:
    """{каноническое направление: код отдела}. Отдел — у живой строки шкалы."""
    ids = sorted({int(value) for value in direction_ids if value is not None})
    if not ids:
        return {}
    cur.execute("""SELECT d.id, lower(COALESCE(dep.code, ''))
                     FROM directions d
                     LEFT JOIN departments dep ON dep.id = d.department_id
                    WHERE d.id = ANY(%s)""", (ids,))
    return {int(direction_id): (code or None) for direction_id, code in cur.fetchall()}


def _mark(sample_id, status, *, error=None, attempt=False) -> None:
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE ai_qa_daily_samples
                              SET status = %s,
                                  last_error = %s,
                                  attempts = attempts + %s,
                                  evaluated_at = CASE WHEN %s = 'evaluated' THEN now()
                                                      ELSE evaluated_at END
                            WHERE id = %s""",
                        (status, str(error)[:500] if error else None, 1 if attempt else 0,
                         status, int(sample_id)))
    finally:
        conn.close()


def _open_samples(first_day, last_day) -> list[dict]:
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, department_code, direction_id, family, subject_kind,
                                  subject_id, attempts
                             FROM ai_qa_daily_samples
                            WHERE sample_day BETWEEN %s AND %s AND status = %s
                            ORDER BY sample_day, id""",
                        (first_day, last_day, STATUS_PICKED))
            rows = cur.fetchall()
    finally:
        conn.close()
    # Старые дни — первыми: им ближе всего до выхода из окна дооценки.
    return [{"id": int(row[0]), "department": row[1], "direction_id": int(row[2]),
             "family": row[3], "subject_kind": row[4], "subject_id": int(row[5]),
             "attempts": int(row[6] or 0)} for row in rows]


def _expire_stale(day) -> int:
    """Закрыть как сорвавшееся то, что так и не оценилось за окно дооценки."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE ai_qa_daily_samples
                              SET status = %s, last_error = %s
                            WHERE status = %s AND sample_day < %s""",
                        (STATUS_FAILED, f"не оценён за {_EVALUATE_LOOKBACK_DAYS + 1} дня",
                         STATUS_PICKED, day - timedelta(days=_EVALUATE_LOOKBACK_DAYS)))
            return int(cur.rowcount or 0)
    finally:
        conn.close()


# ── выборка ──────────────────────────────────────────────────────────────────

def _sample_chats(day, department, size) -> dict:
    """Переписки отдела за день: {направление: сколько добавлено}."""
    kind = config.chat_subject_kind(department)
    if not kind:
        return {}
    from . import api
    added = {}
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            family = subjects_mod.chat_direction_family(cur, department)
            if not family:
                return {}
            cur.execute("SELECT DISTINCT COALESCE(canonical_id, id) FROM directions WHERE id = ANY(%s)",
                        (list(family),))
            directions = sorted(int(row[0]) for row in cur.fetchall())
            have = _cell_counts(cur, day)
            base, params = api._CHAT_CANDIDATE_SQL[kind](family)
            for direction_id in directions:
                need = size - have.get((direction_id, FAMILY_CHATS), 0)
                if need <= 0:
                    continue
                # Тот же отбор, что у «Случайного чата» с фильтром «направление +
                # день»: направление сравнивается каноническим id, день — тем же
                # выражением, по которому очередь раскладывает разговоры по дням.
                pick_sql, pick_params = api._pick_filters_predicate(
                    {"direction_id": direction_id, "date_from": day, "date_to": day},
                    operator_col="u.id", day_expr=api._CHAT_CANDIDATE_DAY[kind],
                    direction_expr="COALESCE(d.canonical_id, d.id)")
                cur.execute(base + pick_sql + """
                      AND NOT EXISTS (SELECT 1 FROM ai_qa_daily_samples s
                                       WHERE s.subject_kind = %s AND s.subject_id = t.id)
                      AND NOT EXISTS (SELECT 1 FROM ai_review_cache rc
                                       WHERE rc.subject_kind = %s AND rc.call_id = t.id
                                         AND rc.model = %s)
                    ORDER BY random() LIMIT %s""",
                            (*params, *pick_params, kind, kind, config.CLAUDE_MODEL, need))
                ids = [int(row[0]) for row in cur.fetchall()]
                taken = sum(_record(cur, day, department, direction_id, FAMILY_CHATS, kind, subject_id)
                            for subject_id in ids)
                if taken:
                    added[direction_id] = taken
    finally:
        conn.close()
    return added


def _sample_calls(day, source: CallSource, size, rng, *, deadline=None,
                  monotonic=time.monotonic) -> dict:
    """Звонки отдела за день из его АТС: {направление: сколько добавлено}.

    Подтяжка — это скачивание записей с АТС по одной, поэтому и она уважает
    предел прохода: недобранное доберёт следующий проход того же дня."""
    candidates = [c for c in (source.candidates(day) or []) if c.get("key")]
    keys = sorted({str(c["key"]) for c in candidates})
    taken_keys = set()
    if keys:
        conn = config.connect_rw()
        try:
            with conn, conn.cursor() as cur:
                # Звонок, который уже лежит в пуле (план прослушки, «Из АТС», прошлая
                # выборка), второй строкой не берём: у imported_calls ключ (external_id,
                # month), и повторная подтяжка всё равно вернула бы None — но уже после
                # платного скачивания записи.
                cur.execute("SELECT external_id FROM imported_calls WHERE external_id = ANY(%s)",
                            (keys,))
                taken_keys = {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()
    return _take(day, source.department, candidates, source.import_call, size, rng,
                 family=FAMILY_CALLS, subject_kind=config.SUBJECT_IMPORTED_CALL,
                 taken_keys=taken_keys, deadline=deadline, monotonic=monotonic)


def _sample_downloaded_chats(day, source: ChatSource, size, rng, *, deadline=None,
                             monotonic=time.monotonic) -> dict:
    """Переписки, которых в базе ещё нет: адаптер их скачивает (СЗоВ — Chat2Desk) и
    сам проверяет гейтом честной оценки. Идёт ПОСЛЕ готового пула (_sample_chats) и
    добирает только то, чего пулу не хватило: каждое скачивание — квота чужого API."""
    candidates = [c for c in (source.candidates(day) or []) if c.get("key")]
    return _take(day, source.department, candidates, source.import_chat, size, rng,
                 family=FAMILY_CHATS, subject_kind=source.subject_kind, taken_keys=set(),
                 deadline=deadline, monotonic=monotonic)


def _take(day, department, candidates, importer, size, rng, *, family, subject_kind,
          taken_keys, deadline=None, monotonic=time.monotonic) -> dict:
    """Добор из источника: по каждому направлению отдела до size, в случайном порядке.

    importer(candidate) кладёт субъект в базу (звонок в пул, переписку в снапшот) и
    отдаёт его id или None — «этот не годится, берите следующего». Отказы источника
    подряд обрывают отдел на этот проход: лежащую АТС или API перебор не лечит."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            departments = _direction_departments(cur, [c.get("direction_id") for c in candidates])
            have = _cell_counts(cur, day)
    finally:
        conn.close()

    by_direction: dict[int, list] = {}
    foreign = 0
    for candidate in candidates:
        direction_id = candidate.get("direction_id")
        # Направление чужого отдела (оператор перешёл, у линии общий номер) — разговор
        # оценился бы по чужой шкале. Такие не берём вовсе.
        if direction_id is None or departments.get(int(direction_id)) != department:
            foreign += 1
            continue
        by_direction.setdefault(int(direction_id), []).append(candidate)
    if foreign:
        logging.info("ai-qa выборка %s: %s разговоров (%s, %s) вне направлений отдела", day,
                     foreign, family, department)

    added = {}
    errors = 0
    for direction_id in sorted(by_direction):
        need = size - have.get((direction_id, family), 0)
        if need <= 0:
            continue
        pool = by_direction[direction_id]
        rng.shuffle(pool)
        taken = 0
        for candidate in pool:
            if taken >= need or errors >= _IMPORT_ERROR_LIMIT:
                break
            if deadline is not None and monotonic() >= deadline:
                break
            key = str(candidate["key"])
            if key in taken_keys:
                continue
            taken_keys.add(key)
            try:
                subject_id = importer(candidate)
            except Exception:
                errors += 1
                logging.exception("ai-qa выборка %s: не удалось подтянуть %s %s (%s)",
                                  day, family, key, department)
                continue
            errors = 0
            if not subject_id:
                continue
            conn = config.connect_rw()
            try:
                with conn, conn.cursor() as cur:
                    taken += _record(cur, day, department, direction_id, family,
                                     subject_kind, subject_id)
            finally:
                conn.close()
        if taken:
            added[direction_id] = taken
        if errors >= _IMPORT_ERROR_LIMIT:
            logging.warning("ai-qa выборка %s: источник %s отдела %s отказывает подряд — "
                            "в этом проходе пропущен", day, family, department)
            break
        if deadline is not None and monotonic() >= deadline:
            logging.warning("ai-qa выборка %s: предел прохода вышел на подтяжке (%s, %s) — "
                            "доберёт следующий проход", day, family, department)
            break
    return added


# ── оценка ───────────────────────────────────────────────────────────────────

def _review_payload(subject_id, subject_kind):
    from . import api
    return api.review_payload(subject_id, refresh=False, subject_kind=subject_kind)


def _in_queue(subject_id, subject_kind) -> bool:
    """Есть ли оценка в проекции ai_review_cache, которую читают очередь и списки."""
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM ai_review_cache
                            WHERE subject_kind = %s AND call_id = %s AND model = %s""",
                        (subject_kind, int(subject_id), config.CLAUDE_MODEL))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _count_attempt(sample, error) -> str:
    attempts = sample["attempts"] + 1
    status = (STATUS_FAILED if attempts >= config.AI_QA_DAILY_SAMPLE_MAX_ATTEMPTS
              else STATUS_PICKED)
    _mark(sample["id"], status, error=error, attempt=True)
    return status


def _evaluate_one(sample, evaluate, in_queue) -> str:
    try:
        evaluate(sample["subject_id"], sample["subject_kind"])
    except (subjects_mod.SubjectNotFound, subjects_mod.SubjectNotEvaluable) as exc:
        # Отказ по существу (эпизод не проходит гейт атрибуции, субъект удалён) —
        # повтор его не изменит, место в выборке освобождаем сразу.
        _mark(sample["id"], STATUS_FAILED, error=str(exc), attempt=True)
        return STATUS_FAILED
    except Exception as exc:
        logging.warning("ai-qa выборка: оценка %s %s не удалась: %s",
                        sample["subject_kind"], sample["subject_id"], exc)
        return _count_attempt(sample, f"{type(exc).__name__}: {exc}")
    if not in_queue(sample["subject_id"], sample["subject_kind"]):
        # Проекцию в ai_review_cache оценка пишет «по возможности» и сбой глотает, а
        # без неё разговора нет ни в очереди, ни в списке. Повтор возьмёт готовый
        # прогон бесплатно и поставит проекцию заново.
        return _count_attempt(sample, "оценка есть, но в очередь не попала")
    _mark(sample["id"], STATUS_EVALUATED)
    return STATUS_EVALUATED


def _evaluate_batch(samples, evaluate, in_queue, deadline, monotonic) -> None:
    def one(sample):
        if monotonic() >= deadline:
            return None     # останется 'picked' — доделает следующий проход
        try:
            return _evaluate_one(sample, evaluate, in_queue)
        except Exception:
            logging.exception("ai-qa выборка: сбой учёта оценки %s", sample["id"])
            return None

    workers = max(1, min(config.AI_QA_DAILY_SAMPLE_WORKERS, len(samples)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-qa-sample-eval") as pool:
        list(pool.map(one, samples))


def _audio_state(source, sample) -> str:
    if source is None:
        # Звонок выборки без адаптера своей АТС (отдел убрали из раздела) — запись
        # взять неоткуда.
        return AUDIO_MISSING
    try:
        return source.audio_state(sample["subject_id"])
    except Exception:
        logging.exception("ai-qa выборка: не удалось проверить запись звонка %s",
                          sample["subject_id"])
        return AUDIO_PENDING


def _evaluate_open(day, sources, evaluate, in_queue, deadline, *, sleep, monotonic) -> None:
    """Оценивает всё, что ждёт в выборке (свой день и два предыдущих).

    Звонок ОП ждёт записи, которую везёт мост, — проход проверяет её раз в
    _AUDIO_POLL_S, пока не выйдет AI_QA_DAILY_SAMPLE_AUDIO_WAIT_S; не доехавшая
    запись тратит попытку. Оценка идёт порциями, и между ними выборка
    перечитывается: доехавший звонок встаёт в работу сразу. Каждый разговор
    проход трогает один раз: упавшую оценку повторит уже следующий проход."""
    audio_deadline = min(deadline, monotonic() + config.AI_QA_DAILY_SAMPLE_AUDIO_WAIT_S)
    chunk = max(1, config.AI_QA_DAILY_SAMPLE_WORKERS * _EVALUATE_CHUNK_PER_WORKER)
    tried: set[int] = set()
    audio_ready: set[int] = set()     # запись уже в облаке — второй раз не проверяем
    left = 0
    while monotonic() < deadline:
        ready, pending = [], []
        for sample in _open_samples(day - timedelta(days=_EVALUATE_LOOKBACK_DAYS), day):
            if sample["id"] in tried:
                continue
            if sample["family"] == FAMILY_CHATS or sample["id"] in audio_ready:
                ready.append(sample)
                continue
            state = _audio_state(sources.get(sample["department"]), sample)
            if state == AUDIO_READY:
                audio_ready.add(sample["id"])
                ready.append(sample)
            elif state == AUDIO_MISSING:
                tried.add(sample["id"])
                _mark(sample["id"], STATUS_FAILED, error="нет записи разговора", attempt=True)
            else:
                pending.append(sample)
        left = len(ready) + len(pending)
        if ready:
            portion = ready[:chunk]
            tried.update(sample["id"] for sample in portion)
            _evaluate_batch(portion, evaluate, in_queue, deadline, monotonic)
            continue    # пока шла оценка, записи могли доехать — перечитываем
        if not pending:
            return
        if monotonic() >= audio_deadline:
            for sample in pending:
                _count_attempt(sample, "запись разговора не пришла за проход")
            return
        sleep(_AUDIO_POLL_S)
    if left:
        open_now = len(_open_samples(day - timedelta(days=_EVALUATE_LOOKBACK_DAYS), day))
        logging.warning("ai-qa выборка %s: предел прохода вышел, ждут оценки %s — "
                        "доделает следующий проход", day, open_now)


# ── проход ───────────────────────────────────────────────────────────────────

def run(day=None, *, call_sources=(), chat_sources=(), evaluate=None, in_queue=None, size=None,
        rng=None, now=None, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    """Один проход выборки: добрать день до N по каждому направлению и оценить.

    day — день разговоров (по умолчанию вчерашний по Алматы). Повторный проход
    того же дня безопасен: он добирает недостающее и дооценивает недоделанное."""
    day = parse_day(day) if day is not None else sample_day(now)
    size = int(size or config.AI_QA_DAILY_SAMPLE_SIZE)
    sources = {config.normalise_department_code(source.department): source
               for source in (call_sources or ())}
    downloads = {config.normalise_department_code(source.department): source
                 for source in (chat_sources or ())}
    evaluate = evaluate or _review_payload
    in_queue = in_queue or _in_queue
    rng = rng or random.Random()
    started = monotonic()
    deadline = started + config.AI_QA_DAILY_SAMPLE_BUDGET_S
    with _pass_lock() as acquired:
        if not acquired:
            logging.info("ai-qa выборка %s: другой проход ещё идёт — пропускаю", day)
            return {"status": "skipped", "reason": "locked", "day": day.isoformat()}
        expired = _expire_stale(day)
        if expired:
            logging.warning("ai-qa выборка %s: %s разговоров так и не оценились за окно "
                            "дооценки — закрыты как сорвавшиеся", day, expired)
        added = {}
        for department in config.DEPARTMENT_CODES:
            code = config.normalise_department_code(department)
            added[code] = {FAMILY_CALLS: {}, FAMILY_CHATS: {}}
            # Отделы и семейства независимы: сбой одной АТС не должен оставить без
            # выборки переписку этого же отдела и соседние отделы.
            try:
                added[code][FAMILY_CHATS] = _sample_chats(day, code, size)
            except Exception:
                logging.exception("ai-qa выборка %s: переписки отдела %s не выбраны", day, code)
            download = downloads.get(code)
            if download is not None:
                # Сначала готовый пул (бесплатно), потом скачивание недостающего.
                try:
                    for direction_id, n in _sample_downloaded_chats(
                            day, download, size, rng, deadline=deadline,
                            monotonic=monotonic).items():
                        chats = added[code][FAMILY_CHATS]
                        chats[direction_id] = chats.get(direction_id, 0) + n
                except Exception:
                    logging.exception("ai-qa выборка %s: переписки отдела %s не скачаны", day, code)
            source = sources.get(code)
            if source is not None:
                try:
                    added[code][FAMILY_CALLS] = _sample_calls(day, source, size, rng,
                                                              deadline=deadline,
                                                              monotonic=monotonic)
                except Exception:
                    logging.exception("ai-qa выборка %s: звонки отдела %s не выбраны", day, code)
        _evaluate_open(day, sources, evaluate, in_queue, deadline,
                       sleep=sleep, monotonic=monotonic)
    result = status(day)
    result.update(status="success", added=added, expired=expired,
                  elapsed_s=round(monotonic() - started, 1))
    return result


def status(day) -> dict:
    """Сводка выборки дня: по отделу, направлению и семейству — оценено, ждёт, сорвалось."""
    day = parse_day(day)
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute("""SELECT s.department_code, s.direction_id, d.name, s.family,
                                  count(*) FILTER (WHERE s.status = 'evaluated'),
                                  count(*) FILTER (WHERE s.status = 'picked'),
                                  count(*) FILTER (WHERE s.status = 'failed')
                             FROM ai_qa_daily_samples s
                             LEFT JOIN directions d ON d.id = s.direction_id
                            WHERE s.sample_day = %s
                            GROUP BY s.department_code, s.direction_id, d.name, s.family
                            ORDER BY s.department_code, d.name, s.family""", (day,))
            cells = [{"department": row[0], "direction_id": row[1], "direction": row[2],
                      "family": row[3], "evaluated": int(row[4]), "pending": int(row[5]),
                      "failed": int(row[6])} for row in cur.fetchall()]
            cur.execute("""SELECT subject_kind, subject_id, last_error
                             FROM ai_qa_daily_samples
                            WHERE sample_day = %s AND status = 'failed'
                            ORDER BY id LIMIT 50""", (day,))
            failures = [{"subject": row[0], "id": int(row[1]), "error": row[2]}
                        for row in cur.fetchall()]
    finally:
        conn.close()
    totals = {key: sum(cell[key] for cell in cells) for key in ("evaluated", "pending", "failed")}
    return {"day": day.isoformat(), "size": config.AI_QA_DAILY_SAMPLE_SIZE,
            "cells": cells, "totals": totals, "failures": failures}
