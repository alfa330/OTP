# -*- coding: utf-8 -*-
"""«Активация водителей» (СЗоВ, 26.11.2026) — профиль 'activation' движка обзвона: правила без сети и базы.

Задача владельца: «есть обзвон по активации водителей, где есть список водителей, которые не выполнили 50
поездок, но должны были (эту цифру можно будет изменять), а также мы должны смотреть провайдер водителя и
пытаться перевести его на Sapar». Решения владельца 26.11.2026:
  • две цели раздельно: водитель добрал trips_goal поездок — уходит из обзвона, успешка оператору (как «вышел
    на линию» у Тез: signed_at, sign_status 'trips'); перешёл на провайдера ЭДО Sapar — отдельная успешка
    оператору (sapar_*), водитель остаётся в обзвоне, пока не доберёт поездки;
  • сначала сервер и сайт: телефон показывает строку «34 из 50 поездок · ЭДО: Бумажный» (driver.label — поле,
    которое телефон автообзвона Тез уже рисует), сервер освежает данные перед звонком, кнопки «Обновить» — у
    руководителя в журнале; кнопки в iCORE Phone — следующим выпуском телефона.

Движок — автообзвона Тез (автоцикл в «Активном», постобработка, сроки перезвона, офферы), поэтому телефону
отдел этого профиля показывается профилем 'tez' (phone_profile в service.py): телефон знает только два профиля.
Номер водителя телефону не уходит — звонок через АТС, как у удалённого КЦ (СЗоВ).

Файл базы — выгрузка «Новички по поездкам с ЭДО»: «ID водителя» (32 hex, обязателен — по нему CRM и Fleet),
«ФИО», «Телефон» (обязателен — звонить), «ИИН», «Диспетчерская», «Завершено поездок», «Провайдер ЭДО», «Дата
регистрации». Читаются все листы с такими колонками; водитель, у которого в файле уже trips_goal поездок и
больше, не загружается (лист «Более 50 поездок» при цели 50 целиком мимо, при цели 70 — те, у кого меньше 70).
"""
import io
import re
from datetime import datetime

from common.kz_phone import normalize_kz_phone
from common.leads_file import FIO_HEADERS, IIN_HEADERS, PHONE_HEADERS, CITY_HEADERS, MAX_LEAD_ROWS, _iin_cell
from . import signing

PROFILE_ACTIVATION = "activation"
SECTION_TITLE = "Активация водителей"
# sign_status лида, добравшего поездки: не статус Sapar, а пометка, откуда signed_at (как 'online' у Тез).
GOAL_STATUS = "trips"
TRIPS_GOAL_DEFAULT = 50
TRIPS_GOAL_MIN = 1
TRIPS_GOAL_MAX = 1000
# Провайдер ЭДО «Sapar» — так его имя пишет кабинет Fleet (справочник edm-providers: 2KZSP «Sapar»).
SAPAR = "Sapar"
# Откуда известен провайдер (dial_list_leads.edm_source).
EDM_FROM_FILE = "file"
EDM_FROM_CARD = "card"
EDM_NONE = "none"
# «Бумажный документооборот» — провайдера нет, документы бумагой: на экране короче.
PAPER_NAMES = ("бумажный документооборот", "бумажный", "paperdo")
PAPER_SHORT = "Бумажный"
# Значения колонки провайдера в файле, которые значат «не знаем», а не имя провайдера.
UNKNOWN_PROVIDER_CELLS = {"", "-", "—", "не найден", "нет данных", "не определён", "не определен", "unknown"}

ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Шапки колонок файла (после _norm_header: нижний регистр, пробелы → «_»).
DRIVER_ID_HEADERS = {"id_водителя", "id", "driver_id", "contractor_id", "id_профиля", "yandex_driver_id",
                     "айди_водителя", "account_id"}
PARK_HEADERS = {"диспетчерская", "парк", "таксопарк", "park", "park_name"}
TRIPS_HEADERS = {"завершено_поездок", "завершённых_поездок", "завершенных_поездок", "поездок", "поездки",
                 "trips", "orders", "orders_count"}
PROVIDER_HEADERS = {"провайдер_эдо", "провайдер", "эдо", "edm_provider", "provider"}
REGISTERED_HEADERS = {"дата_регистрации", "регистрация", "registered_at", "created_date"}

NO_DRIVER_ID_COLUMN = ("В файле нет колонки «ID водителя». Нужна выгрузка «Новички по поездкам с ЭДО»: по ID "
                       "сервер узнаёт поездки водителя и его провайдера ЭДО.")
NO_PHONE_COLUMN = "В файле нет колонки «Телефон»: звонить водителям некуда."
NO_ROWS = "В файле не нашлось ни одной строки с водителем"


def _norm_header(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("ё", "е") if value is not None else ""


def _header_set(names):
    return {name.replace("ё", "е") for name in names}


_DRIVER_ID_HEADERS = _header_set(DRIVER_ID_HEADERS)
_FIO_HEADERS = _header_set(FIO_HEADERS)
_PHONE_HEADERS = _header_set(PHONE_HEADERS)
_IIN_HEADERS = _header_set(IIN_HEADERS)
_CITY_HEADERS = _header_set(CITY_HEADERS)
_PARK_HEADERS = _header_set(PARK_HEADERS)
_TRIPS_HEADERS = _header_set(TRIPS_HEADERS)
_PROVIDER_HEADERS = _header_set(PROVIDER_HEADERS)
_REGISTERED_HEADERS = _header_set(REGISTERED_HEADERS)


def clean_goal(value, default=TRIPS_GOAL_DEFAULT):
    """Цель по поездкам из настроек → int в пределах; не число — default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if TRIPS_GOAL_MIN <= number <= TRIPS_GOAL_MAX else default


def clean_account_id(value):
    """ID водителя из файла → 32 hex в нижнем регистре или ''."""
    text = str(value or "").strip().lower()
    return text if ACCOUNT_ID_RE.match(text) else ""


def clean_trips(value):
    """«Завершено поездок» из файла → int ≥ 0 или None (пусто, мусор)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip().replace(" ", "").replace(" ", "")
    return int(text) if text.isdigit() else None


def clean_provider(value):
    """Провайдер ЭДО из файла или карточки → имя без хвостовых пробелов; «не найден» и пусто → ''."""
    text = " ".join(str(value or "").split())
    return "" if text.lower() in UNKNOWN_PROVIDER_CELLS else text[:64]


def is_sapar(name):
    """Провайдер — Sapar (без учёта регистра и пробелов)."""
    return " ".join(str(name or "").split()).lower() == SAPAR.lower()


def provider_short(name):
    """Имя провайдера для строки телефона и журнала: «Бумажный документооборот» → «Бумажный»."""
    text = " ".join(str(name or "").split())
    return PAPER_SHORT if text.lower() in PAPER_NAMES else text


def city_from_park(park_name):
    """Город из «Диспетчерской» файла: последнее значение в скобках («iTaxi (доставка) (Алматы)» → «Алматы»)."""
    found = re.findall(r"\(([^()]*)\)", str(park_name or ""))
    return found[-1].strip()[:64] if found else ""


def _moment(value):
    """«Дата регистрации» из файла → datetime с поясом Алматы или None."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=signing.PERIOD_TZ)
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=signing.PERIOD_TZ)
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=signing.PERIOD_TZ)


def _sheets(raw_bytes, file_ext):
    """Листы файла → [(имя листа, [строки])]. CSV — один «лист» без имени."""
    ext = str(file_ext or "").lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        try:
            return [(str(ws.title or ""), [list(row) for row in ws.iter_rows(values_only=True)])
                    for ws in wb.worksheets]
        finally:
            wb.close()
    if ext == ".csv":
        from common.leads_file import _rows_from_csv
        return [("", _rows_from_csv(raw_bytes))]
    raise ValueError("Поддерживаются только .csv, .xlsx и .xlsm")


def _filled(row):
    return any(str(c or "").strip() for c in row)


def _columns(header):
    """Шапка листа → {поле: номер колонки}; первое совпадение каждого поля."""
    names = [_norm_header(c) for c in header]
    wanted = (("driver_id", _DRIVER_ID_HEADERS), ("fio", _FIO_HEADERS), ("phone", _PHONE_HEADERS),
              ("iin", _IIN_HEADERS), ("city", _CITY_HEADERS), ("park", _PARK_HEADERS), ("trips", _TRIPS_HEADERS),
              ("provider", _PROVIDER_HEADERS), ("registered", _REGISTERED_HEADERS))
    found = {}
    for idx, name in enumerate(names):
        for key, headers in wanted:
            if key not in found and name in headers:
                found[key] = idx
                break
    return found


def parse_activation_file(raw_bytes, file_ext, goal=TRIPS_GOAL_DEFAULT, max_rows=None):
    """Файл базы активации → (строки, счёт).

    Строки — для DialListService.import_leads: (row_number, ФИО, сырой телефон, phone_norm, ИИН как в файле,
    город) и dict extras по phone_norm: driver_id, park_name, trips_total (из файла), edm_provider, registered_at.
    Счёт — rows_goal_reached (в файле уже goal поездок и больше: не грузим), rows_bad_id (нет ID водителя или он
    не 32 hex), duplicate_ids (тот же ID ещё раз — повтор), sheets (сколько листов прочитано).

    Нет листа с колонками «ID водителя» и «Телефон» — ValueError с понятным текстом."""
    limit = int(max_rows) if max_rows is not None else MAX_LEAD_ROWS
    goal = clean_goal(goal)
    sheets = _sheets(raw_bytes, file_ext)
    rows, extras = [], {}
    counts = {"rows_goal_reached": 0, "rows_bad_id": 0, "bad_id_rows": [], "duplicate_ids": 0, "sheets": 0}
    seen_ids = set()
    saw_id_column = saw_phone_column = False
    # Номер строки для руководителя — строка Excel на своём листе (пустые строки тоже считаются); листов с данными
    # больше одного — с именем листа: «Более 50»: 3 (разбор 27.11: сквозной счёт по листам указывал не туда).
    numbered_sheets = []
    for name, sheet in sheets:
        numbered = [(index, row) for index, row in enumerate(sheet, start=1) if _filled(row)]
        if numbered:
            numbered_sheets.append((name, numbered, _columns(numbered[0][1])))
    data_sheets = sum(1 for _name, _rows, cols in numbered_sheets if "driver_id" in cols and "phone" in cols)
    for name, numbered, cols in numbered_sheets:
        saw_id_column = saw_id_column or "driver_id" in cols
        saw_phone_column = saw_phone_column or "phone" in cols
        if "driver_id" not in cols or "phone" not in cols:
            continue
        counts["sheets"] += 1

        def cell(raw, key):
            idx = cols.get(key)
            return raw[idx] if idx is not None and idx < len(raw) else None

        for excel_row, raw in numbered[1:]:
            row_label = f"«{name}»: {excel_row}" if data_sheets > 1 and name else excel_row
            if len(rows) >= limit:
                raise ValueError(f"В файле больше {limit} строк с данными. Разделите его на несколько файлов.")
            driver_id = clean_account_id(cell(raw, "driver_id"))
            if not driver_id:
                counts["rows_bad_id"] += 1
                if len(counts["bad_id_rows"]) < 20:
                    counts["bad_id_rows"].append(row_label)
                continue
            trips = clean_trips(cell(raw, "trips"))
            if trips is not None and trips >= goal:
                counts["rows_goal_reached"] += 1
                continue
            if driver_id in seen_ids:
                counts["duplicate_ids"] += 1
                continue
            seen_ids.add(driver_id)
            phone_cell = cell(raw, "phone")
            if isinstance(phone_cell, float):
                phone_cell = f"{phone_cell:.0f}"
            phone_raw = str(phone_cell or "").strip()
            phone_norm = normalize_kz_phone(phone_raw)
            park = " ".join(str(cell(raw, "park") or "").split())[:128]
            city = " ".join(str(cell(raw, "city") or "").split())[:64] or city_from_park(park)
            iin_raw = _iin_cell(cell(raw, "iin")) if "iin" in cols else ""
            rows.append((row_label, str(cell(raw, "fio") or "").strip(), phone_raw, phone_norm, iin_raw, city))
            if phone_norm and phone_norm not in extras:
                extras[phone_norm] = {
                    "driver_id": driver_id,
                    "park_name": park,
                    "trips_total": trips,
                    "edm_provider": clean_provider(cell(raw, "provider")) if "provider" in cols else "",
                    "registered_at": _moment(cell(raw, "registered")),
                }
    if not saw_id_column:
        raise ValueError(NO_DRIVER_ID_COLUMN)
    if not saw_phone_column:
        raise ValueError(NO_PHONE_COLUMN)
    if not rows and not counts["rows_goal_reached"]:
        raise ValueError(NO_ROWS)
    return rows, extras, counts


# ── подписи ──────────────────────────────────────────────────────────────────────────────────────────

def trips_word(n):
    """«поездка / поездки / поездок» для числа n."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "поездка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "поездки"
    return "поездок"


def _trips_word_acc(n):
    """«добрал 51 поездку / 52 поездки / 50 поездок»: после глагола — винительный падеж."""
    word = trips_word(n)
    return "поездку" if word == "поездка" else word


def _of_goal_word(goal):
    """«из 50 поездок», «из 21 поездки»: после «из» — родительный падеж."""
    goal = abs(int(goal))
    return "поездки" if goal % 10 == 1 and goal % 100 != 11 else "поездок"


def trips_label(trips_total, goal, reached_at=None):
    """Поездки водителя одной фразой: «34 из 50 поездок», «Добрал 50 поездок», «Поездки не проверены»."""
    goal = clean_goal(goal)
    if reached_at is not None or (trips_total is not None and int(trips_total) >= goal):
        n = int(trips_total) if trips_total is not None else goal
        return f"Добрал {n} {_trips_word_acc(n)}"
    if trips_total is None:
        return "Поездки не проверены"
    return f"{int(trips_total)} из {goal} {_of_goal_word(goal)}"


def provider_label(provider, source):
    """Провайдер ЭДО одной фразой: «ЭДО: Sapar», «ЭДО: Бумажный», «ЭДО: нет», «ЭДО: не знаем»."""
    if source == EDM_NONE:
        return "ЭДО: нет"
    name = provider_short(provider)
    return f"ЭДО: {name}" if name else "ЭДО: не знаем"


def driver_label(trips_total, goal, reached_at, provider, source, park="", driver_id=""):
    """Строка водителя в телефоне (driver.label): «34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы)».
    Парк — «Диспетчерская» из файла (владелец 27.11.2026: «срочно надо добавить парк водителя в отображаемой
    информации у операторов»); в конце строки: телефон показывает label как есть, длинную строку режет с хвоста.
    driver_id — ID водителя последним («… · ID: 0f1e…», владелец 27.11.2026: «так же в отображение и id водителя»):
    только телефону, который не показывает его отдельным полем (driver_block, id_in_label)."""
    label = f"{trips_label(trips_total, goal, reached_at)} · {provider_label(provider, source)}"
    park = " ".join(str(park or "").split())
    if park:
        label = f"{label} · Парк: {park}"
    driver_id = clean_account_id(driver_id)
    return f"{label} · ID: {driver_id}" if driver_id else label


def driver_block(trips_total, goal, reached_at, provider, source, city="", park="", driver_id="",
                 id_in_label=True):
    """Блок driver для телефона (тот же, что у Тез: label, profile_url, city, registered, registration_label).
    Ссылки в админку нет: кабинета Fleet у операторов нет. Номера нет. driver_id — ID водителя (32 hex): телефон
    3.22.67+ показывает его отдельной строкой с копированием (id_in_label=False — в label его тогда нет), старый —
    в конце label."""
    driver_id = clean_account_id(driver_id)
    return {"label": driver_label(trips_total, goal, reached_at, provider, source, park=park,
                                  driver_id=driver_id if id_in_label else ""),
            "profile_url": "",
            "city": (city or "").strip(),
            "registered": None,
            "registration_label": "",
            "driver_id": driver_id}


def fleet_url(driver_id, park_id):
    """Карточка водителя в кабинете Fleet — для руководителя в журнале; без парка или ID — ''."""
    driver_id, park_id = clean_account_id(driver_id), clean_account_id(park_id)
    if not driver_id or not park_id:
        return ""
    return f"https://fleet.yandex.kz/contractors/{driver_id}/details?park_id={park_id}"
