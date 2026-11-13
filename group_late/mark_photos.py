"""Фото отметок Workpace в окне «Отметки за день» (13.11.2026).

Где фото есть. Проверено на боевом 13.11.2026: снимок есть у отметок с общих
планшетов в офисах; у личных телефонов — только когда в Workpace включено «фото
при отметке» (в тот день у 1 из 23 телефонных отметок, и то как причина
подозрения). Поэтому окно показывает миниатюру только там, где фото правда есть.

Откуда. В списке отметок Workpace ссылки на фото нет — только в карточке отметки
(`fileId`), то есть ещё один запрос на отметку. Ответ кэшируется в
`glb_mark_photos`: фото у отметки появляется в момент её создания и потом не
меняется, так что второй раз карточку не спрашиваем. Сам снимок не хранится —
его отдаёт Workpace, а здесь он только держится в памяти на повторный показ.

Граница доступа. Главе отдела раздел выдан в пределах одного отдела Workpace.
Отметку (и её фото) он получает, только если отдел в САМОЙ отметке Workpace —
его. Номер отметки с экрана доступа не даёт: фото отдаётся только по строке
кэша, которую записал поиск, и отдел сверяется по этой строке.
"""

import logging
import threading
import time
from collections import OrderedDict
from datetime import date as date_cls, datetime, timedelta

from group_late.departments import clean_department_filters, department_matches
from group_late.helpers import parse_dt
from group_late.workpace import WorkpaceError, workpace_client

logger = logging.getLogger(__name__)

# Отметок одного человека за день столько не бывает; потолок — от перебора.
MAX_ITEMS = 40
# Список отметок дня держим минуту: окно дня открывают подряд для нескольких
# людей, и каждый раз тянуть весь день из Workpace незачем.
DAY_LIST_TTL_SECONDS = 60
# Снимок ~25–50 КБ: 64 штуки — пара мегабайт памяти.
PHOTO_CACHE_ITEMS = 64
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")

_day_lists: dict = {}
_day_lists_lock = threading.Lock()
_photo_cache: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_photo_cache_lock = threading.Lock()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД")


def _day_marks(day, client):
    """Все отметки Workpace за день (с кэшем на минуту)."""
    now = time.monotonic()
    with _day_lists_lock:
        cached = _day_lists.get(day)
        if cached and now - cached[0] < DAY_LIST_TTL_SECONDS:
            return cached[1]
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    marks = client.get_marks(start, end)
    with _day_lists_lock:
        _day_lists[day] = (now, marks)
        for stale in [key for key, (ts, _) in _day_lists.items() if now - ts >= DAY_LIST_TTL_SECONDS]:
            _day_lists.pop(stale, None)
    return marks


def _same_person(mark, item) -> bool:
    employee_id = str(item.get("employee_id") or "").strip()
    if employee_id and str(mark.get("employeeId") or "") == employee_id:
        return True
    name = str(item.get("employee") or "").strip().casefold()
    return bool(name) and str(mark.get("employeeName") or "").strip().casefold() == name


def _match(item, by_id, by_at):
    """Отметка Workpace для строки окна: по номеру, а у дней, собранных до
    13.11.2026 (номера в кэше нет), — по секунде отметки."""
    mark_id = str(item.get("id") or "").strip()
    if mark_id:
        return by_id.get(mark_id)
    at = parse_dt(str(item.get("at") or "").strip() or None)
    if at is None:
        return None
    candidates = by_at.get(at.replace(microsecond=0), [])
    if len(candidates) == 1:
        # Строка окна собрана из этого же списка Workpace, так что единственная
        # отметка этой секунды — она и есть, даже если человек со второй карточкой.
        return candidates[0]
    same = [mark for mark in candidates if _same_person(mark, item)]
    return same[0] if len(same) == 1 else None


def lookup(db, day, items, scope=None, client=None) -> dict:
    """Какие из отметок окна дня с фото. `items` — [{key, id?, at?, employee_id?, employee?}].

    Возвращает {"items": [{key, mark_id, has_photo}]} только по найденным
    отметкам в границах отдела; остальные просто не попадают в ответ."""
    client = client or workpace_client
    target = _as_date(day)
    wanted = [item for item in (items or []) if isinstance(item, dict)][:MAX_ITEMS]
    if not wanted:
        return {"items": []}

    marks = _day_marks(target, client)
    by_id, by_at = {}, {}
    for mark in marks:
        if mark.get("id"):
            by_id[str(mark["id"])] = mark
        at = parse_dt(mark.get("markDate"))
        if at is not None:
            by_at.setdefault(at.replace(microsecond=0), []).append(mark)

    filters = clean_department_filters(scope) if scope else None
    matched = []
    for item in wanted:
        mark = _match(item, by_id, by_at)
        if mark is None or not mark.get("id"):
            continue
        if filters and not department_matches(mark.get("departmentName"), filters):
            continue
        matched.append((str(item.get("key") or mark["id"]), mark))
    if not matched:
        return {"items": []}

    known = db.glb_mark_photo_rows([mark["id"] for _, mark in matched]) if db is not None else {}
    fresh = []
    for _, mark in matched:
        mark_id = str(mark["id"])
        if mark_id in known:
            continue
        try:
            card = client.get_mark_card(mark_id)
        except WorkpaceError:
            logger.warning("Отметки: карточка отметки %s из Workpace не получена", mark_id)
            continue
        row = {
            "mark_id": mark_id,
            "employee_id": str(mark.get("employeeId") or "") or None,
            "employee_name": mark.get("employeeName"),
            "department_name": mark.get("departmentName"),
            "mark_at": parse_dt(mark.get("markDate")),
            "origin": mark.get("origin"),
            "file_id": str(card.get("fileId") or "").strip() or None,
        }
        known[mark_id] = {"file_id": row["file_id"], "department_name": row["department_name"]}
        fresh.append(row)
    if fresh and db is not None:
        try:
            db.glb_store_mark_photos(fresh)
        except Exception:
            # Не запомнили — в следующий раз спросим карточку снова, экран не страдает.
            logger.exception("Отметки: не удалось запомнить фото отметок")

    result = []
    for key, mark in matched:
        info = known.get(str(mark["id"]))
        if info is None:
            continue
        result.append({"key": key, "mark_id": str(mark["id"]), "has_photo": bool(info.get("file_id"))})
    return {"items": result}


def photo(db, mark_id, scope=None, client=None) -> tuple[bytes, str]:
    """Снимок отметки: (байты, тип). LookupError — фото нет или отметка не своя."""
    client = client or workpace_client
    key = str(mark_id or "").strip()
    row = db.glb_mark_photo_rows([key]).get(key) if key and db is not None else None
    if not row or not row.get("file_id"):
        raise LookupError("Фото нет")
    if scope and not department_matches(row.get("department_name"), clean_department_filters(scope)):
        # Чужую отметку не отличаем от несуществующей.
        raise LookupError("Фото нет")
    file_id = row["file_id"]
    with _photo_cache_lock:
        cached = _photo_cache.get(file_id)
        if cached is not None:
            _photo_cache.move_to_end(file_id)
            return cached
    data, content_type = client.download_photo(file_id)
    content_type = _image_type(data, content_type)
    if not content_type:
        raise LookupError("Фото нет")
    with _photo_cache_lock:
        _photo_cache[file_id] = (data, content_type)
        while len(_photo_cache) > PHOTO_CACHE_ITEMS:
            _photo_cache.popitem(last=False)
    return data, content_type


def _image_type(data: bytes, declared: str):
    """Тип картинки по её первым байтам: отдаём только настоящие JPEG/PNG/WebP,
    а не то, что назвал источник."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if declared in PHOTO_TYPES:
        logger.warning("Отметки: Workpace назвал фото %s, но байты не похожи на картинку", declared)
    return None
