# -*- coding: utf-8 -*-
"""«Расчет ресурсов · Основа ОП» — входящая линия отдела продаж (IC-1, #470).

Постановка — руководство ОП, файл «Erlang_C_.xlsx»: портал сам считает,
сколько операторов «Основы» нужно в каждый час, и по этому расчёту строит смены для
аукциона. Решения владельца 27.11.2026:
  * пока только «Основа» (верификаторы — следующим шагом);
  * исходящие — только самой «Основы»: у «Яндекс Регистрации» и «Потока» свои люди;
  * шаблоны смен вносятся руками, аукцион руководители запускают сами, как у СЗоВ;
  * раздел, генератор смен, планы и аукцион — ТЕ ЖЕ, что у линии СЗоВ, и линия не
    должна измениться: этот модуль только читает её общие функции, ничего в них не меняя.

Модель — та, что в файле постановщика:
  входящие  — Erlang C: наименьшее число людей, при котором доля звонков, принятых за
              sl_seconds, не ниже sl_target;
  исходящие — нагрузка в эрлангах = (соединённые × AHT исх. + недозвоны × время недозвона) / 3600;
  людей в час = max(Erlang C; ⌈(нагрузка вход + нагрузка исход) / предел занятости⌉) и не
              меньше минимума часа: пул общий, входящие в приоритете, исходящие заполняют
              свободное время;
  FTE часа  = люди / UR. Штат периода — FTE-часы / (часы недели × дни / 7) / усушка; это
              считает общий _build_forecast_payload линии, своей арифметики здесь нет.
На данных файла (01–24.11.2026, исходящие всего ОП) модель повторяет его до единицы:
532 агенто-часа, 560 FTE-часов, 16,5 → 17 FTE, пик 9 (tests/test_resource_op_osnova.py).

Данные — «Касания» (cdr_touches): только закрытые сутки (cdr_sync_days) и без реестра
тестовых номеров — то же определение, что у ночного прогноза ОП (forecast_engine):
  * дневной объём входящих — прогноз движка для отдела 'op' (TimesFM, сделан ДО дня), а без
    него — среднее тех же дней недели за history_weeks;
  * доля каждого часа и исходящие — среднее тех же дней недели за history_weeks, известных
    до дня (у прошедших дней — только то, что было до них);
  * исходящие — звонки с внутренних номеров операторов «Основы ОП».

Профиль дня отдаётся в форме профиля линии (hourly_profile, daily_fte …), поэтому вкладки
«Прогнозы» и «Графики» и генератор смен работают на нём без единой правки. Ключи engine /
engine_agents здесь намеренно НЕ ставятся: по ним общий код линии включает Erlang A.
"""
import json
import logging
import math
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from psycopg2.extras import Json

from resource_fte.calculations import (
    _build_forecast_payload,
    _empty_incident_uplift_profile,
    _next_week_start_date,
)
from resource_fte.common import (
    WEEKDAYS_RU,
    _json_safe,
    _parse_report_date,
    _round_value,
    _to_float,
    _to_int,
)
from resource_fte.schedule_generation import _generate_schedule_preview_from_forecast

# Направление и отдел прогона аукциона «Основы ОП» — те же строки, что в каталоге
# аукциона (db/core.py, SHIFT_AUCTION_MODE_DIRECTION_NAME / _DEPARTMENT_CODE): состав,
# который считает расчёт, и участники аукциона обязаны быть одними и теми же людьми.
# Страж — tests/test_resource_op_osnova.py.
OP_DEPARTMENT_CODE = 'op'
OP_DIRECTION_NAME = 'Основа ОП'
OP_AUCTION_MODE = 'op_osnova'

SETTINGS_TABLE = 'resource_op_settings'
# Комментарии «Биллинг → Группировка» «Основы»: своя таблица — у линии она одна на дату и
# час без отдела, и комментарий СВ ОП лёг бы в «Группировку» СЗоВ (resource_fte/op_billing.py).
COMMENTS_TABLE = 'resource_op_billing_hour_comments'
SCHEMA_RETRY_SECONDS = 600
MAX_PERIOD_DAYS = 62
HISTORY_LIMIT_DAYS = 120

INBOUND_TYPES = ('Входящий', 'Входящий (не приняли)')
INBOUND_ANSWERED = 'Входящий'
INBOUND_LOST = 'Входящий (не приняли)'
OUTBOUND_TYPE = 'Исходящий'
OUTBOUND_CONNECTED = 'Разговор'

INBOUND_SOURCES = ('timesfm', 'history')
ROUNDING_MODES = ('none', 'ceil', 'floor', 'round')

# Вводные файла постановщика («Erlang_C_.xlsx», лист «Расчет ресурсов»).
DEFAULT_SETTINGS: Dict[str, Any] = {
    'aht_in_seconds': 187.0,
    'aht_out_seconds': 187.0,
    'noanswer_seconds': 30.0,
    'sl_target': 0.8,
    'sl_seconds': 20.0,
    'occ': 0.85,
    'ur': 0.95,
    'weekly_hours_per_operator': 40.0,
    'shrinkage_coeff': 0.85,
    'min_agents_per_hour': 1,
    'history_weeks': 4,
    'include_outbound': True,
    'inbound_source': 'timesfm',
    'fte_rounding': 'none',
    'shift_rounding': 'ceil',
}

# (ключ, наименьшее, наибольшее, целое ли, подпись для ошибки)
_NUMERIC_BOUNDS = (
    ('aht_in_seconds', 10, 1800, False, 'Средний разговор входящих — от 10 до 1800 секунд'),
    ('aht_out_seconds', 10, 1800, False, 'Средний разговор исходящих — от 10 до 1800 секунд'),
    ('noanswer_seconds', 0, 600, False, 'Время на недозвон — от 0 до 600 секунд'),
    ('sl_target', 0.5, 0.99, False, 'Цель SL — от 50 до 99 %'),
    ('sl_seconds', 5, 300, False, 'Время ответа для SL — от 5 до 300 секунд'),
    ('occ', 0.5, 1.0, False, 'Предел занятости — от 50 до 100 %'),
    ('ur', 0.5, 1.0, False, 'Утилизация (UR) — от 50 до 100 %'),
    ('weekly_hours_per_operator', 10, 60, False, 'Часов в неделю на ставку — от 10 до 60'),
    ('shrinkage_coeff', 0.5, 1.0, False, 'Усушка — от 0,5 до 1'),
    ('min_agents_per_hour', 0, 10, True, 'Минимум операторов в час — от 0 до 10'),
    ('history_weeks', 1, 12, True, 'История — от 1 до 12 недель'),
)
_SETTING_LABELS = {key: message for key, _, _, _, message in _NUMERIC_BOUNDS}

_schema_lock = threading.Lock()
_schema_state = {'done': False, 'failed_at': None}


# ── Erlang C ─────────────────────────────────────────────────────────────────

def _erlang_c(traffic: float, agents: int) -> float:
    """Вероятность ожидания (формула Эрланга C) через устойчивую рекурсию Эрланга B."""
    if agents <= 0 or agents <= traffic:
        return 1.0
    blocking = 1.0
    for n in range(1, agents + 1):
        blocking = traffic * blocking / (n + traffic * blocking)
    return agents * blocking / (agents - traffic * (1.0 - blocking))


def erlang_c_service_level(calls_per_hour: float, aht_seconds: float, agents: int,
                           answer_seconds: float) -> float:
    """Доля звонков, принятых не дольше answer_seconds, при agents людях на линии."""
    calls = max(0.0, float(calls_per_hour or 0))
    if calls <= 0:
        return 1.0
    aht = max(1.0, float(aht_seconds or 0))
    traffic = calls * aht / 3600.0
    agents = int(agents or 0)
    if agents <= traffic:
        return 0.0
    wait = _erlang_c(traffic, agents)
    return max(0.0, 1.0 - wait * math.exp(-(agents - traffic) * float(answer_seconds or 0) / aht))


@lru_cache(maxsize=8192)
def _erlang_c_agents_cached(calls_milli: int, aht_ms: int, target_permille: int, answer_ms: int) -> int:
    calls = calls_milli / 1000.0
    aht = aht_ms / 1000.0
    target = target_permille / 1000.0
    answer = answer_ms / 1000.0
    traffic = calls * aht / 3600.0
    agents = max(1, int(math.floor(traffic)) + 1)
    while agents < 5000 and erlang_c_service_level(calls, aht, agents, answer) + 1e-12 < target:
        agents += 1
    return agents


def erlang_c_agents(calls_per_hour: float, aht_seconds: float, sl_target: float,
                    answer_seconds: float) -> int:
    """Наименьшее число людей, при котором SL часа не ниже цели. Нет звонков — ноль."""
    calls = max(0.0, float(calls_per_hour or 0))
    if calls <= 0:
        return 0
    return _erlang_c_agents_cached(
        int(round(calls * 1000)),
        int(round(max(1.0, float(aht_seconds or 0)) * 1000)),
        int(round(min(0.999, max(0.0, float(sl_target or 0))) * 1000)),
        int(round(max(0.0, float(answer_seconds or 0)) * 1000)),
    )


def staff_hour(inbound_calls: float, out_connected: float, out_noanswer: float,
               settings: Dict[str, Any]) -> Dict[str, Any]:
    """Люди часа по модели файла: max(Erlang C; ⌈нагрузка / предел занятости⌉; минимум)."""
    return _staff_hour(inbound_calls, out_connected, out_noanswer, normalize_settings(settings))


def _staff_hour(inbound_calls: float, out_connected: float, out_noanswer: float,
                cfg: Dict[str, Any]) -> Dict[str, Any]:
    """staff_hour на уже разобранных настройках — в цикле по часам без повторного разбора."""
    inbound = max(0.0, float(inbound_calls or 0))
    connected = max(0.0, float(out_connected or 0)) if cfg['include_outbound'] else 0.0
    noanswer = max(0.0, float(out_noanswer or 0)) if cfg['include_outbound'] else 0.0
    load_in = inbound * cfg['aht_in_seconds'] / 3600.0
    load_out = (connected * cfg['aht_out_seconds'] + noanswer * cfg['noanswer_seconds']) / 3600.0
    agents_in = erlang_c_agents(inbound, cfg['aht_in_seconds'], cfg['sl_target'], cfg['sl_seconds'])
    load = load_in + load_out
    blended = int(math.ceil(load / cfg['occ'] - 1e-9)) if load > 0 else 0
    agents = max(agents_in, blended, int(cfg['min_agents_per_hour']))
    return {
        'inbound_calls': inbound,
        'out_connected': connected,
        'out_noanswer': noanswer,
        'load_in': load_in,
        'load_out': load_out,
        'agents_inbound': agents_in,
        'agents': agents,
        'fte': agents / cfg['ur'] if cfg['ur'] > 0 else float(agents),
        'occupancy': (load / agents) if agents > 0 else 0.0,
        'sl': erlang_c_service_level(inbound, cfg['aht_in_seconds'], agents, cfg['sl_seconds']) if inbound > 0 else None,
    }


# ── settings ─────────────────────────────────────────────────────────────────

def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'on', 'да'):
        return True
    if text in ('0', 'false', 'no', 'off', 'нет', ''):
        return False
    return default


def normalize_settings(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Хранимые настройки поверх умолчаний; негодное значение — умолчание, а не ошибка:
    чтение не должно падать из-за строки, записанной старой версией."""
    raw = raw or {}
    result = dict(DEFAULT_SETTINGS)
    for key, low, high, integer, _ in _NUMERIC_BOUNDS:
        if key not in raw or raw.get(key) in (None, ''):
            continue
        try:
            value = float(str(raw[key]).replace(',', '.'))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < low or value > high:
            continue
        result[key] = int(round(value)) if integer else value
    result['include_outbound'] = _coerce_bool(raw.get('include_outbound'), DEFAULT_SETTINGS['include_outbound'])
    source = str(raw.get('inbound_source') or '').strip().lower()
    result['inbound_source'] = source if source in INBOUND_SOURCES else DEFAULT_SETTINGS['inbound_source']
    for key in ('fte_rounding', 'shift_rounding'):
        mode = str(raw.get(key) or '').strip().lower()
        result[key] = mode if mode in ROUNDING_MODES else DEFAULT_SETTINGS[key]
    return result


def validate_settings_payload(payload: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Новые настройки из формы: вне пределов — отказ с понятной причиной, а не тихое умолчание."""
    payload = payload or {}
    merged = dict(current)
    for key, low, high, integer, message in _NUMERIC_BOUNDS:
        if key not in payload:
            continue
        raw = payload.get(key)
        if raw in (None, ''):
            raise ValueError(message)
        try:
            value = float(str(raw).replace(',', '.'))
        except (TypeError, ValueError):
            raise ValueError(message)
        if not math.isfinite(value) or value < low or value > high:
            raise ValueError(message)
        merged[key] = int(round(value)) if integer else value
    if 'include_outbound' in payload:
        merged['include_outbound'] = _coerce_bool(payload.get('include_outbound'), True)
    if 'inbound_source' in payload:
        source = str(payload.get('inbound_source') or '').strip().lower()
        if source not in INBOUND_SOURCES:
            raise ValueError('Источник прогноза входящих — «Прогноз ОП» или «Среднее за недели»')
        merged['inbound_source'] = source
    for key in ('fte_rounding', 'shift_rounding'):
        if key in payload:
            mode = str(payload.get(key) or '').strip().lower()
            if mode not in ROUNDING_MODES:
                raise ValueError('Неизвестный способ округления')
            merged[key] = mode
    return normalize_settings(merged)


SCHEMA_SQL = (
    f"""
    CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
        id SMALLINT PRIMARY KEY CHECK (id = 1),
        settings JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"INSERT INTO {SETTINGS_TABLE} (id) VALUES (1) ON CONFLICT (id) DO NOTHING",
    f"""
    CREATE TABLE IF NOT EXISTS {COMMENTS_TABLE} (
        report_date DATE NOT NULL,
        hour SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
        comment TEXT NOT NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (report_date, hour)
    )
    """,
)


def ensure_schema_db(db) -> bool:
    """Таблица настроек — своей транзакцией и один раз на процесс, как у движка прогноза:
    общий монолит схемы (database.py, db/schema.py) этот раздел не трогает."""
    if _schema_state['done']:
        return True
    with _schema_lock:
        if _schema_state['done']:
            return True
        failed_at = _schema_state['failed_at']
        if failed_at is not None and time.monotonic() - failed_at < SCHEMA_RETRY_SECONDS:
            return False
        try:
            with db._get_cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                for statement in SCHEMA_SQL:
                    cursor.execute(statement)
        except Exception:  # noqa: BLE001
            logging.exception('resource_op_settings could not be created')
            _schema_state['failed_at'] = time.monotonic()
            return False
        _schema_state['done'] = True
        _schema_state['failed_at'] = None
        return True


def _settings_table_ready(cursor) -> bool:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (SETTINGS_TABLE,))
    row = cursor.fetchone()
    return bool(row and row[0])


def _stored_settings_tx(cursor) -> Dict[str, Any]:
    if not _settings_table_ready(cursor):
        return {}
    cursor.execute(f"SELECT settings FROM {SETTINGS_TABLE} WHERE id = 1")
    row = cursor.fetchone()
    value = row[0] if row else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def osnova_direction_ids_tx(cursor) -> List[int]:
    """Направления «Основы ОП» — и нынешнее, и прежние версии (id 91 → 73): по прежнему
    id числятся люди, которых ещё не перевели на новую версию направления."""
    cursor.execute(
        """
        SELECT d.id
          FROM directions d
          JOIN departments dep ON dep.id = d.department_id
         WHERE BTRIM(COALESCE(d.name, '')) = %s
           AND LOWER(BTRIM(COALESCE(dep.code, ''))) = %s
         ORDER BY d.id
        """,
        (OP_DIRECTION_NAME, OP_DEPARTMENT_CODE),
    )
    return [int(row[0]) for row in cursor.fetchall()]


def settings_tx(cursor) -> Dict[str, Any]:
    """Настройки раздела + поля, которые ждут общие функции линии (штат, доступность,
    переходящие смены и сводка периода читают их из того же словаря)."""
    settings = normalize_settings(_stored_settings_tx(cursor))
    settings['selected_direction_ids'] = osnova_direction_ids_tx(cursor)
    # «Принято» в модели нет: входящие уже считаются целиком, с непринятыми. Общая сводка
    # периода и наплыв читают ключ, поэтому он есть и всегда равен единице.
    settings['answer_rate'] = 1.0
    settings['department'] = OP_DEPARTMENT_CODE
    settings['direction_name'] = OP_DIRECTION_NAME
    settings['auction_mode'] = OP_AUCTION_MODE
    return settings


DIRECTION_MISSING_MESSAGE = (
    'Не найдено направление «Основа ОП» отдела продаж: расчёт не по кому вести. '
    'Проверьте название направления в разделе «Направления».'
)


def require_direction(settings: Dict[str, Any]) -> List[int]:
    """Направления «Основы» обязаны найтись. Общие функции линии читают ПУСТОЙ список как
    «фильтра нет» — штат, смены по графику и переходящие ночи всей компании легли бы в
    расчёт «Основы» молча (например, после переименования направления)."""
    direction_ids = list(settings.get('selected_direction_ids') or [])
    if not direction_ids:
        raise ValueError(DIRECTION_MISSING_MESSAGE)
    return direction_ids


def get_settings(db) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        return _json_safe(settings_tx(cursor))


def update_settings(db, payload: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    if not ensure_schema_db(db):
        raise RuntimeError('Таблица настроек «Основы ОП» недоступна — попробуйте позже')
    with db._get_cursor() as cursor:
        cursor.execute(f"SELECT settings FROM {SETTINGS_TABLE} WHERE id = 1 FOR UPDATE")
        row = cursor.fetchone()
        stored = row[0] if row and isinstance(row[0], dict) else {}
        merged = validate_settings_payload(payload, normalize_settings(stored))
        cursor.execute(
            f"""
            INSERT INTO {SETTINGS_TABLE} (id, settings, updated_by, updated_at)
            VALUES (1, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE
               SET settings = EXCLUDED.settings,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at
            """,
            (Json({key: merged[key] for key in DEFAULT_SETTINGS}), user_id),
        )
        return _json_safe(settings_tx(cursor))


# ── data ─────────────────────────────────────────────────────────────────────

def _not_test_sql(cursor, phone_expr: str) -> str:
    """Тот же вычет реестра тестовых номеров, что у ночного прогноза ОП."""
    from resource_fte import forecast_engine
    return forecast_engine._not_test_sql(cursor, phone_expr)


def osnova_exts_tx(cursor, direction_ids: Sequence[int]) -> List[str]:
    """Внутренние номера «Основы ОП», по которым считаются её исходящие.

    Номер берётся у всех её людей, и у уволенных тоже: их звонки за прошлые недели —
    работа «Основы». Номер, который сейчас у работающего человека ДРУГОГО направления,
    не берётся: уволившийся отдал его новичку, и чужие исходящие легли бы «Основе»."""
    if not direction_ids:
        return []
    cursor.execute(
        """
        WITH people AS (
            SELECT COALESCE(NULLIF(BTRIM(u.sip_number), ''), NULLIF(BTRIM(op.sip_number), '')) AS ext,
                   COALESCE(u.direction_id, op.direction_id) = ANY(%s) AS osnova,
                   COALESCE(u.status, 'working') = 'working' AS working
              FROM users u
              LEFT JOIN operator_profiles op ON op.user_id = u.id
        )
        SELECT DISTINCT ext FROM people p
         WHERE p.ext IS NOT NULL AND p.osnova
           AND NOT EXISTS (
                SELECT 1 FROM people other
                 WHERE other.ext = p.ext AND other.working AND NOT other.osnova
           )
         ORDER BY ext
        """,
        (list(direction_ids),),
    )
    return [str(row[0]) for row in cursor.fetchall() if row[0]]


class OpData:
    """Почасовые входящие и исходящие «Основы» за отрезок — двумя запросами на весь
    расчёт, а не по запросу на день: месяц истории не должен ходить в базу 30 раз."""

    def __init__(self, cursor, date_from: date, date_to: date, exts: Sequence[str], today: date):
        self.today = today
        self.date_from = date_from
        self.date_to = date_to
        self.inbound: Dict[date, Dict[int, Dict[str, float]]] = defaultdict(dict)
        self.outbound: Dict[date, Dict[int, Dict[str, float]]] = defaultdict(dict)
        cursor.execute(
            """
            SELECT day FROM cdr_sync_days
             WHERE day BETWEEN %s AND %s AND status = 'done' AND complete
            """,
            (date_from, date_to),
        )
        self.complete_days = sorted(row[0] for row in cursor.fetchall())
        self.complete_set = set(self.complete_days)
        if not self.complete_days:
            return
        not_test = _not_test_sql(cursor, "t.phone")
        cursor.execute(
            f"""
            SELECT t.call_day, EXTRACT(HOUR FROM t.started_at)::int,
                   COUNT(*),
                   COUNT(*) FILTER (WHERE t.call_type = %s),
                   COUNT(*) FILTER (WHERE t.call_type = %s),
                   COALESCE(SUM(COALESCE(t.talk_measured_seconds, t.talk_seconds)) FILTER (WHERE t.call_type = %s), 0)
              FROM cdr_touches t
              JOIN cdr_sync_days d ON d.day = t.call_day AND d.status = 'done' AND d.complete
             WHERE t.call_day BETWEEN %s AND %s
               AND t.queue <> ''
               AND t.call_type IN %s{not_test}
             GROUP BY 1, 2
            """,
            (INBOUND_ANSWERED, INBOUND_LOST, INBOUND_ANSWERED, date_from, date_to, INBOUND_TYPES),
        )
        for day, hour, offered, answered, lost, talk in cursor.fetchall():
            self.inbound[day][int(hour)] = {
                'offered': float(offered or 0), 'answered': float(answered or 0),
                'lost': float(lost or 0), 'talk_seconds': float(talk or 0),
            }
        if exts:
            cursor.execute(
                f"""
                SELECT t.call_day, EXTRACT(HOUR FROM t.started_at)::int,
                       COUNT(*) FILTER (WHERE t.result = %s),
                       COUNT(*) FILTER (WHERE t.result <> %s)
                  FROM cdr_touches t
                  JOIN cdr_sync_days d ON d.day = t.call_day AND d.status = 'done' AND d.complete
                 WHERE t.call_day BETWEEN %s AND %s
                   AND t.call_type = %s
                   AND t.ext = ANY(%s){not_test}
                 GROUP BY 1, 2
                """,
                (OUTBOUND_CONNECTED, OUTBOUND_CONNECTED, date_from, date_to, OUTBOUND_TYPE, list(exts)),
            )
            for day, hour, connected, noanswer in cursor.fetchall():
                self.outbound[day][int(hour)] = {'connected': float(connected or 0), 'noanswer': float(noanswer or 0)}

    def window(self, forecast_date: date, weeks: int) -> List[date]:
        """Последние `weeks` закрытых дней того же дня недели, известных до дня прогноза."""
        end = min(forecast_date, self.today)
        days = [day for day in self.complete_days if day < end and day.weekday() == forecast_date.weekday()]
        return sorted(days, reverse=True)[:max(1, int(weeks))]

    def day_inbound(self, day: date) -> List[float]:
        hours = self.inbound.get(day, {})
        return [hours.get(hour, {}).get('offered', 0.0) for hour in range(24)]

    def day_outbound(self, day: date) -> Tuple[List[float], List[float]]:
        hours = self.outbound.get(day, {})
        return ([hours.get(hour, {}).get('connected', 0.0) for hour in range(24)],
                [hours.get(hour, {}).get('noanswer', 0.0) for hour in range(24)])


def _history_start(period_start: date, settings: Dict[str, Any]) -> date:
    # Окно каждого дня — history_weeks тех же дней недели до него; запас в неделю на
    # пропуски (незакрытые сутки окно продлевают в прошлое).
    return period_start - timedelta(days=7 * (int(settings['history_weeks']) + 1))


def load_data(cursor, date_from: date, date_to: date, settings: Dict[str, Any], today: date,
              exts: Optional[Sequence[str]] = None) -> OpData:
    """Данные на отрезок и на окна его дней. Окно дня — до min(день, сегодня), поэтому у
    периода далеко впереди история берётся от сегодняшнего дня, а не от начала периода."""
    if exts is None:
        exts = osnova_exts_tx(cursor, settings.get('selected_direction_ids') or [])
    return OpData(cursor, _history_start(min(date_from, today), settings), min(date_to, today), exts, today)


def _engine_forecasts_tx(cursor, date_from: date, date_to: date) -> Dict[date, dict]:
    """Дневной прогноз движка для ОП — пусто, если его таблиц ещё нет или он не считался.
    Прогноз необязателен: расчёт идёт по истории, а сбой чтения не должен ронять отчёт."""
    from resource_fte import forecast_engine

    def read() -> Dict[date, dict]:
        if not forecast_engine.schema_ready(cursor):
            return {}
        return forecast_engine.daily_forecast(cursor, OP_DEPARTMENT_CODE, date_from, date_to)

    return optional_tx(cursor, read, {}, 'дневной прогноз движка не прочитан')


# ── profiles ─────────────────────────────────────────────────────────────────

def _mean(rows: List[List[float]]) -> List[float]:
    if not rows:
        return [0.0] * 24
    return [sum(row[hour] for row in rows) / len(rows) for hour in range(24)]


def profile_for_date(data: OpData, forecast_date: date, settings: Dict[str, Any],
                     engine_forecasts: Optional[Dict[date, dict]] = None) -> Dict[str, Any]:
    """День в форме профиля линии: звонки и FTE по часам, сумма FTE-часов дня."""
    cfg = normalize_settings(settings)
    window = data.window(forecast_date, cfg['history_weeks'])
    inbound_rows = [data.day_inbound(day) for day in window]
    outbound_rows = [data.day_outbound(day) for day in window]
    history_by_hour = _mean(inbound_rows)
    history_total = sum(history_by_hour)
    connected_by_hour = _mean([row[0] for row in outbound_rows])
    noanswer_by_hour = _mean([row[1] for row in outbound_rows])

    engine_day = (engine_forecasts or {}).get(forecast_date) if cfg['inbound_source'] == 'timesfm' else None
    if history_total > 0:
        # Объём дня — прогноз движка, а форма дня по часам — те же дни недели истории.
        daily_calls = float(engine_day['calls']) if engine_day else history_total
        shares = [value / history_total for value in history_by_hour]
    else:
        # Без истории дня недели формы нет, и раскладывать объём движка по часам нечем:
        # день остаётся пустым с пометкой «мало истории», а не равномерным выдуманным.
        engine_day = None
        daily_calls = 0.0
        shares = [0.0] * 24
    calls_by_hour = [daily_calls * share for share in shares]

    hourly_profile = []
    daily_fte = 0.0
    daily_agents = 0
    peak_agents = 0
    peak_fte = 0.0
    for hour in range(24):
        staff = _staff_hour(calls_by_hour[hour], connected_by_hour[hour], noanswer_by_hour[hour], cfg)
        daily_fte += staff['fte']
        daily_agents += staff['agents']
        peak_agents = max(peak_agents, staff['agents'])
        peak_fte = max(peak_fte, staff['fte'])
        workload_minutes = (staff['load_in'] + staff['load_out']) * 60.0
        hourly_profile.append({
            'hour': hour,
            'avg_calls': calls_by_hour[hour],
            'aht_seconds': cfg['aht_in_seconds'],
            'distribution': shares[hour],
            'workload_minutes': workload_minutes,
            'effective_fte_minutes': (workload_minutes / staff['fte']) if staff['fte'] > 0 else 0.0,
            'fte': staff['fte'],
            'fte_rounded': round(_round_value(staff['fte'], cfg['fte_rounding']), 4),
            'source_calls': [],
            'op_out_connected': staff['out_connected'],
            'op_out_noanswer': staff['out_noanswer'],
            'op_agents_inbound': staff['agents_inbound'],
            'op_agents': staff['agents'],
            'op_occupancy': staff['occupancy'],
            'op_sl': staff['sl'],
        })
    weekday = forecast_date.weekday()
    return {
        **WEEKDAYS_RU[weekday],
        'weekday': weekday,
        'forecast_date': forecast_date.isoformat(),
        'history_dates': [day.isoformat() for day in window],
        'history_count': len(window),
        # «Истории не хватает» — тех же дней недели закрыто меньше, чем просили в настройках;
        # без единого дня форма по часам не строится вовсе (ниже).
        'insufficient_history': len(window) < int(cfg['history_weeks']),
        'used_source_count': len(window),
        'avg_daily_calls': daily_calls,
        'aht_seconds': cfg['aht_in_seconds'],
        'forecast_aht_seconds': cfg['aht_in_seconds'],
        'daily_fte': daily_fte,
        # Пик часа — на карточке дня вместо «истории 2/2» линии (как у чата).
        'peak_fte': peak_fte,
        'hourly_profile': hourly_profile,
        'op_model': {
            'inbound_source': 'timesfm' if engine_day else 'history',
            'method_label': engine_day.get('method_label') if engine_day else 'Среднее за недели',
            'made_on': engine_day.get('made_on') if engine_day else None,
            'calls_low': engine_day.get('calls_low') if engine_day else None,
            'calls_high': engine_day.get('calls_high') if engine_day else None,
            'history_calls': history_total,
            'out_connected': sum(connected_by_hour) if cfg['include_outbound'] else 0.0,
            'out_noanswer': sum(noanswer_by_hour) if cfg['include_outbound'] else 0.0,
            'agent_hours': daily_agents,
            'peak_agents': peak_agents,
        },
    }


def period_profiles(data: OpData, period_start: date, period_end: date, settings: Dict[str, Any],
                    engine_forecasts: Optional[Dict[date, dict]] = None) -> List[Dict[str, Any]]:
    profiles = []
    current = period_start
    while current <= period_end:
        profiles.append(profile_for_date(data, current, settings, engine_forecasts))
        current += timedelta(days=1)
    return profiles


def actual_for_period(data: OpData, period_start: date, period_end: date,
                      settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """«Факт» дня в форме линии: звонки, разговор и потребность той же моделью на звонках,
    что пришли на деле, — разница с прогнозом показывает ошибку прогноза, а не модели."""
    cfg = normalize_settings(settings)
    by_day: Dict[str, Dict[str, Any]] = {}
    current = period_start
    while current <= period_end:
        if current in data.complete_set and current < data.today:
            inbound = data.inbound.get(current, {})
            connected, noanswer = data.day_outbound(current)
            day = {
                'has_actual_report': True,
                'actual_received_calls': 0, 'actual_accepted_calls': 0, 'actual_lost_calls': 0,
                'actual_talk_time_seconds': 0.0, 'actual_workload_minutes': 0.0,
                'actual_report_fte': 0.0, 'actual_aht_seconds': 0.0, 'hourly': {},
            }
            for hour in range(24):
                row = inbound.get(hour, {})
                offered = row.get('offered', 0.0)
                answered = row.get('answered', 0.0)
                staff = _staff_hour(offered, connected[hour], noanswer[hour], cfg)
                workload_minutes = (staff['load_in'] + staff['load_out']) * 60.0
                day['actual_received_calls'] += int(offered)
                day['actual_accepted_calls'] += int(answered)
                day['actual_lost_calls'] += int(row.get('lost', 0.0))
                day['actual_talk_time_seconds'] += row.get('talk_seconds', 0.0)
                day['actual_workload_minutes'] += workload_minutes
                day['actual_report_fte'] += staff['fte']
                day['hourly'][hour] = {
                    'has_actual_report': True,
                    'actual_received_calls': int(offered),
                    'actual_accepted_calls': int(answered),
                    'actual_lost_calls': int(row.get('lost', 0.0)),
                    'actual_talk_time_seconds': row.get('talk_seconds', 0.0),
                    'actual_aht_seconds': (row.get('talk_seconds', 0.0) / answered) if answered > 0 else 0.0,
                    'actual_workload_minutes': workload_minutes,
                    'actual_report_fte': staff['fte'],
                }
            accepted = day['actual_accepted_calls']
            day['actual_aht_seconds'] = day['actual_talk_time_seconds'] / accepted if accepted > 0 else 0.0
            by_day[current.isoformat()] = day
        current += timedelta(days=1)
    return by_day


# ── payloads in the line's shape ─────────────────────────────────────────────

def _today_tx(cursor) -> date:
    """Сегодня по Алматы. Не CURRENT_DATE: сессии пула в UTC (common/pg_session.py), и до
    05:00 по Алматы вчерашние сутки, уже закрытые мостом, считались «сегодняшними» и
    пропадали из «Звонков» и «Обзора»."""
    from cdr import queries as cdr_queries
    return cdr_queries.today_almaty()


def optional_tx(cursor, step: Callable[[], Any], default: Any, label: str) -> Any:
    """Необязательная часть отчёта под SAVEPOINT (приём resource_fte/calculations.py): её
    сбой SQL откатывается до точки, и транзакция, на которой стоят звонки, живёт дальше.
    Без точки отката пойманная ошибка оставляла транзакцию прерванной, и падал весь отчёт."""
    savepoint = False
    try:
        cursor.execute("SAVEPOINT op_optional_part")
        savepoint = True
    except Exception:  # noqa: BLE001 — нет транзакции (autocommit): защищать нечего
        savepoint = False
    try:
        result = step()
    except Exception:  # noqa: BLE001 — часть необязательна, отчёт идёт без неё
        logging.exception('ОП: %s — часть отчёта пропущена', label)
        if savepoint:
            cursor.execute("ROLLBACK TO SAVEPOINT op_optional_part")
        return default
    if savepoint:
        cursor.execute("RELEASE SAVEPOINT op_optional_part")
    return result


def _period(as_of_date: date, forecast_week_start_value=None, forecast_date_from_value=None,
            forecast_date_to_value=None) -> Tuple[date, date]:
    from resource_fte.service import _normalize_forecast_period
    start, end = _normalize_forecast_period(
        as_of_date,
        forecast_week_start_value=forecast_week_start_value,
        forecast_date_from_value=forecast_date_from_value,
        forecast_date_to_value=forecast_date_to_value,
    )
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f'Период прогноза — не длиннее {MAX_PERIOD_DAYS} дней')
    return start, end


def build_forecast_tx(cursor, period_start: date, period_end: date, settings: Dict[str, Any],
                      today: date, with_actual: bool = True) -> Dict[str, Any]:
    """Прогноз периода — общим построителем линии на профилях «Основы»."""
    from resource_fte.service import _current_operator_fte_tx
    require_direction(settings)
    data = load_data(cursor, period_start, period_end, settings, today)
    engine = _engine_forecasts_tx(cursor, period_start, period_end) if settings['inbound_source'] == 'timesfm' else {}
    profiles = period_profiles(data, period_start, period_end, settings, engine)
    capacity = _current_operator_fte_tx(cursor, settings, on_date=period_start)
    actual = actual_for_period(data, period_start, period_end, settings) if with_actual else {}
    payload = _build_forecast_payload(
        period_start, period_end, profiles, settings,
        capacity['current_operator_fte'], actual, _empty_incident_uplift_profile(),
    )
    payload['opModel'] = _op_model_summary(profiles, settings)
    # «История для расчета» на экране — настоящее окно «Основы» (те же дни недели за
    # history_weeks до каждого дня), а не пары −21/−14 дней, которые подставил бы
    # построитель линии.
    window_days = sorted({day for profile in profiles for day in profile['history_dates']})
    if window_days:
        window = {'start': window_days[0], 'end': window_days[-1]}
        payload.update({'history_periods': [window], 'history_weeks': [window],
                        'history_start': window['start'], 'history_end': window['end']})
    else:
        payload.update({'history_periods': [], 'history_weeks': []})
    return {'payload': payload, 'capacity': capacity, 'profiles': profiles, 'data': data}


def _op_model_summary(profiles: List[Dict[str, Any]], settings: Dict[str, Any]) -> Dict[str, Any]:
    engine_days = [p for p in profiles if p['op_model']['inbound_source'] == 'timesfm']
    return {
        'days': len(profiles),
        'engine_days': len(engine_days),
        'history_weeks': settings['history_weeks'],
        'include_outbound': settings['include_outbound'],
        'inbound_source': settings['inbound_source'],
        'made_on': max((p['op_model']['made_on'] or '' for p in engine_days), default='') or None,
        'agent_hours': sum(p['op_model']['agent_hours'] for p in profiles),
        'peak_agents': max((p['op_model']['peak_agents'] for p in profiles), default=0),
        'out_connected': round(sum(p['op_model']['out_connected'] for p in profiles), 1),
        'out_noanswer': round(sum(p['op_model']['out_noanswer'] for p in profiles), 1),
        'insufficient_days': [p['forecast_date'] for p in profiles if p['insufficient_history']],
    }


def get_overview(db, date_from: Optional[str] = None, date_to: Optional[str] = None,
                 as_of_date_value: Optional[str] = None, forecast_week_start_value: Optional[str] = None,
                 forecast_date_from_value: Optional[str] = None,
                 forecast_date_to_value: Optional[str] = None) -> Dict[str, Any]:
    from resource_fte.service import _period_operator_availability_tx
    with db._get_cursor() as cursor:
        settings = settings_tx(cursor)
        today = _today_tx(cursor)
        as_of_date = _parse_report_date(as_of_date_value) if as_of_date_value else today
        period_start, period_end = _period(as_of_date, forecast_week_start_value,
                                           forecast_date_from_value, forecast_date_to_value)
        built = build_forecast_tx(cursor, period_start, period_end, settings, today)
        forecast = built['payload']
        availability = _period_operator_availability_tx(
            cursor, period_start, period_end, settings, include_details=False,
            direction_ids_override=settings['selected_direction_ids'],
        )
        available_fte = _to_float(availability.get('period_available_operator_fte'))
        forecast.update({
            'periodAvailableOperatorFte': available_fte,
            'periodAvailableOperatorCount': _to_int(availability.get('period_available_operator_count')),
            'periodOperatorCount': _to_int(availability.get('period_operator_count')),
            'periodPartialOperatorCount': _to_int(availability.get('period_partial_operator_count')),
            'periodUnavailableOperatorCount': _to_int(availability.get('period_unavailable_operator_count')),
            'periodWorkingDaysThreshold': _to_float(availability.get('period_working_days_threshold')),
            'periodAvailableOperatorFteGap': round(available_fte - _to_float(forecast.get('operatorsWithShrinkage')), 4),
            'periodAvailableOperatorRateCounts': availability.get('period_rate_counts') or {},
            'periodAvailableOperatorRates': availability.get('period_rate_capacity') or [],
            'periodOperatorStatusSummary': availability.get('period_status_summary') or {},
            'periodOperatorAvailabilityDetails': [],
        })
        history, loaded = _history_tx(cursor, settings, today, date_from, date_to)
    uplift = _empty_incident_uplift_profile()
    return {
        'settings': _json_safe(settings),
        'as_of_date': as_of_date.isoformat(),
        'directions': [],
        'incident_uplift_dashboard': _json_safe({**uplift, 'projection': {'days': []}}),
        'next_week_forecast': _json_safe(forecast),
        'loaded_report_dates': loaded,
        'history': history,
    }


def _history_tx(cursor, settings: Dict[str, Any], today: date, date_from: Optional[str],
                date_to: Optional[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Прошедшие закрытые дни отрезка: звонки, прогноз (по тому, что было известно до дня)
    и потребность по факту — в форме истории «Обзора» линии, свежие сверху."""
    end = _parse_report_date(date_to) if date_to else today - timedelta(days=1)
    end = min(end, today - timedelta(days=1))
    start = _parse_report_date(date_from) if date_from else end.replace(day=1)
    if start > end:
        return [], []
    require_direction(settings)
    start = max(start, end - timedelta(days=HISTORY_LIMIT_DAYS - 1))
    from resource_fte import op_billing
    data = load_data(cursor, start, end, settings, today)
    engine = _engine_forecasts_tx(cursor, start, end) if settings['inbound_source'] == 'timesfm' else {}
    actual = actual_for_period(data, start, end, settings)
    planned = op_billing.planned_hourly_fte(cursor, settings['selected_direction_ids'], start, end)
    history = []
    for day_iso in sorted(actual, reverse=True):
        day = _parse_report_date(day_iso)
        fact = actual[day_iso]
        profile = profile_for_date(data, day, settings, engine)
        received = fact['actual_received_calls']
        lost = fact['actual_lost_calls']
        forecast_fte = _to_float(profile['daily_fte'])
        planned_fte = round(sum(planned.get(day_iso) or [0.0]), 4)
        history.append({
            'report_date': day_iso,
            'weekday': day.weekday(),
            'weekday_short': WEEKDAYS_RU[day.weekday()]['short'],
            'total_received': received,
            'total_accepted': fact['actual_accepted_calls'],
            'total_lost': lost,
            'no_answer_rate': (lost / received) if received > 0 else 0.0,
            'forecast_fte_total': round(forecast_fte, 4),
            'planned_fte_total': planned_fte,
            # Как у линии: «Факт FTE» дня — часы смен по графику; потребность по звонкам,
            # что пришли на деле, — actual_report_fte_total (карточка «Факт FTE периода»).
            'actual_fte_total': planned_fte,
            'fact_forecast_delta_total': round(fact['actual_report_fte'] - forecast_fte, 4),
            'filename': None,
            'updated_at': None,
            'actual_report_fte_total': round(fact['actual_report_fte'], 4),
            'actual_report_workload_minutes': round(fact['actual_workload_minutes'], 2),
            'forecast_calls_total': round(_to_float(profile['avg_daily_calls']), 2),
        })
    loaded = [day.isoformat() for day in data.complete_days if day < today]
    return history, loaded


def get_day(db, report_date_value: str) -> Dict[str, Any]:
    """День «Обзора» и «Звонков»: звонки по часам, прогноз дня и смены по «Графикам работы».

    «Факт FTE» часа — как у линии: часы смен «Основы» по графику (у линии это
    `_refresh_actual_fte_for_day_tx`), а не потребность по звонкам."""
    from resource_fte import op_billing
    report_date = _parse_report_date(report_date_value)
    with db._get_cursor() as cursor:
        settings = settings_tx(cursor)
        require_direction(settings)
        today = _today_tx(cursor)
        if report_date >= today:
            raise ValueError('DAY_NOT_FOUND')
        data = load_data(cursor, report_date, report_date, settings, today)
        actual = actual_for_period(data, report_date, report_date, settings).get(report_date.isoformat())
        if not actual:
            raise ValueError('DAY_NOT_FOUND')
        engine = _engine_forecasts_tx(cursor, report_date, report_date) if settings['inbound_source'] == 'timesfm' else {}
        profile = profile_for_date(data, report_date, settings, engine)
        planned = op_billing.planned_hourly_fte(
            cursor, settings['selected_direction_ids'], report_date, report_date).get(report_date.isoformat()) or [0.0] * 24
        extras = op_billing.day_hour_extras(cursor, report_date)
    inbound = data.inbound.get(report_date, {})
    hours = []
    for hour in range(24):
        fact_hour = actual['hourly'][hour]
        forecast_hour = profile['hourly_profile'][hour]
        received = fact_hour['actual_received_calls']
        lost = fact_hour['actual_lost_calls']
        accepted = fact_hour['actual_accepted_calls']
        extra = extras.get(hour) or {}
        hours.append({
            'hour': hour,
            'hour_label': f'{hour:02d}:00',
            'received_calls': received,
            'accepted_calls': accepted,
            'lost_calls': lost,
            'no_answer_rate': (lost / received) if received > 0 else 0.0,
            'avg_talk_seconds': (inbound.get(hour, {}).get('talk_seconds', 0.0) / accepted) if accepted > 0 else 0.0,
            'avg_wait_seconds': (extra.get('wait_sum', 0.0) / extra['wait_count']) if extra.get('wait_count') else 0.0,
            'forecast_calls': round(_to_float(forecast_hour['avg_calls']), 2),
            'forecast_fte': round(_to_float(forecast_hour['fte']), 4),
            'planned_fte': round(_to_float(planned[hour]), 4),
            'actual_fte': round(_to_float(planned[hour]), 4),
            'fact_forecast_delta': round(_to_float(planned[hour]) - _to_float(forecast_hour['fte']), 4),
            'comments': '',
            'greeting_abandoned': int(extra.get('greeting') or 0),
            'queue_abandoned': lost,
        })
    received_total = actual['actual_received_calls']
    lost_total = actual['actual_lost_calls']
    forecast_total = sum(_to_float(row['fte']) for row in profile['hourly_profile'])
    planned_total = sum(_to_float(value) for value in planned)
    wait_sum = sum((extras.get(hour) or {}).get('wait_sum', 0.0) for hour in range(24))
    wait_count = sum((extras.get(hour) or {}).get('wait_count', 0.0) for hour in range(24))
    weekday = report_date.weekday()
    return {
        'summary': {
            'report_date': report_date.isoformat(),
            'weekday': weekday,
            'weekday_short': WEEKDAYS_RU[weekday]['short'],
            'weekday_label': WEEKDAYS_RU[weekday]['label'],
            'total_received': received_total,
            'total_accepted': actual['actual_accepted_calls'],
            'total_lost': lost_total,
            'no_answer_rate': (lost_total / received_total) if received_total > 0 else 0.0,
            'avg_talk_seconds': actual['actual_aht_seconds'],
            'avg_wait_seconds': (wait_sum / wait_count) if wait_count else 0.0,
            'forecast_fte_total': round(forecast_total, 4),
            'planned_fte_total': round(planned_total, 4),
            'actual_fte_total': round(planned_total, 4),
            'fact_forecast_delta_total': round(planned_total - forecast_total, 4),
        },
        'hours': hours,
    }


def _payload_period(payload: Dict[str, Any], today: date) -> Tuple[date, date]:
    start_value = payload.get('date_from') or payload.get('dateFrom') or payload.get('period_start')
    end_value = payload.get('date_to') or payload.get('dateTo') or payload.get('period_end')
    week_value = payload.get('week_start') or payload.get('weekStart')
    if start_value:
        start = _parse_report_date(start_value)
        end = _parse_report_date(end_value) if end_value else start
    elif week_value:
        start = _parse_report_date(week_value)
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=6)
    else:
        start = _next_week_start_date(today)
        end = start + timedelta(days=6)
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f'Период графика — не длиннее {MAX_PERIOD_DAYS} дней')
    return start, end


def build_schedule_preview(db, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Смены «Основы» строит ТОТ ЖЕ генератор, что смены линии: отличается только
    почасовая потребность на входе. Штат по ставкам и переходящие ночные смены — общими
    функциями линии, но по направлениям «Основы»."""
    from resource_fte.schedule_generation import _normalize_shift_templates
    from resource_fte.service import _current_operator_fte_tx, _resource_work_shift_carry_in_tx
    payload = payload or {}
    templates = _normalize_shift_templates(payload.get('templates') if 'templates' in payload else [])
    with db._get_cursor() as cursor:
        settings = settings_tx(cursor)
        today = _today_tx(cursor)
        period_start, period_end = _payload_period(payload, today)
        built = build_forecast_tx(cursor, period_start, period_end, settings, today, with_actual=False)
        operator_capacity = _current_operator_fte_tx(cursor, settings, on_date=period_start)
        carry_in = _resource_work_shift_carry_in_tx(cursor, period_start, settings)
    return _generate_schedule_preview_from_forecast(
        built['payload'],
        templates,
        operator_capacity,
        respect_operator_capacity=bool(payload.get('respect_operator_capacity') or payload.get('respectOperatorCapacity')),
        carry_in_shifts=carry_in,
    )


def get_shift_templates() -> Dict[str, Any]:
    """Шаблонов по умолчанию у «Основы» нет: их вносит руководитель (решение владельца
    27.11.2026 — «шаблоны я буду вносить вручную»). Ставки — все три, как в отделе."""
    return {
        'templates': [],
        'rates': [
            {'rate': 1.0, 'label': '1'},
            {'rate': 0.75, 'label': '0.75'},
            {'rate': 0.5, 'label': '0.5'},
        ],
        'source': 'manual',
    }


def get_operator_availability(db, as_of_date_value=None, forecast_week_start_value=None,
                              forecast_date_from_value=None, forecast_date_to_value=None) -> Dict[str, Any]:
    from resource_fte.service import get_resource_operator_availability_details
    with db._get_cursor() as cursor:
        direction_ids = osnova_direction_ids_tx(cursor)
    require_direction({'selected_direction_ids': direction_ids})
    return get_resource_operator_availability_details(
        db,
        as_of_date_value=as_of_date_value,
        forecast_week_start_value=forecast_week_start_value,
        forecast_date_from_value=forecast_date_from_value,
        forecast_date_to_value=forecast_date_to_value,
        direction_ids_override=direction_ids,
    )
