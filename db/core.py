"""Модульный уровень базы данных: пул соединений, константы, помощники.

Байт в байт то, что стояло в database.py до класса Database (вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026).
Отсюда импортируют миксины db/*.py; database.py реэкспортирует эти имена наружу."""
import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256
from datetime import datetime, timedelta, date, time as dt_time, timezone as dt_timezone
from zoneinfo import ZoneInfo
import time
import uuid
import requests
import openpyxl
import re
import json
import csv
import io
import math
from io import BytesIO
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import gc
from psycopg2.pool import ThreadedConnectionPool  # Added for connection pooling
from psycopg2.extras import execute_values, execute_batch, Json
import calendar
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Border, Side, Alignment
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from openpyxl.styles.fills import Fill
from collections import defaultdict
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional

import supervisor_hours
from supervisor_hours import SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES
import task_production
import task_projects
from task_production import queries as task_production_queries
from task_production import schema as task_production_schema

logging.basicConfig(level=logging.INFO)

os.environ['TZ'] = 'Asia/Almaty'
time.tzset()

# Отличает «поле не пришло» от «поле пришло пустым»: снять значение — это записать
# NULL, а None здесь уже занят смыслом «не трогать».
# Часовой ровно один и живёт в модуле. Одноимённый атрибут внутри класса — ловушка:
# значения по умолчанию в сигнатурах методов свяжутся с классовым объектом, а сравнения
# `x is not _UNSET` в телах методов уйдут к модульному (область класса в разрешении имён
# внутри функций не участвует), и «поле не пришло» перестанет отличаться от «пришло».
_UNSET = object()
# Своё имя у маркера «группа отбивки не передана»: общий _UNSET выше уже однажды оказался
# затенён одноимённым атрибутом класса, и путать «не прислали» с «весь отдел» тут нельзя.
_UNSET_GROUP = object()

def _env_int(name, default, minimum=None):
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    return value


def _env_float(name, default, minimum=None):
    try:
        value = float(str(os.getenv(name, default)).strip())
    except Exception:
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    return value


def _is_schema_lock_conflict(error):
    return getattr(error, 'pgcode', None) in ('55P03', '40P01')


def get_gcs_client():
    """Клиент Google Cloud Storage — тот же, что get_gcs_client() в bot_schedule2.py: ключ
    сервисного аккаунта из GOOGLE_APPLICATION_CREDENTIALS_CONTENT, иначе окружение.

    Раньше имени в этом модуле не было: add_call_evaluation при замене аудио падала с
    NameError внутри своего try/except, и старый файл в бакете не удалялся (ruff F821,
    02.11.2026)."""
    from google.cloud import storage as gcs_storage
    credentials_content = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')
    if credentials_content:
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(json.loads(credentials_content))
        return gcs_storage.Client(credentials=credentials)
    return gcs_storage.Client()


# Global connection pool. Keep a warm floor so the first browser burst does not
# pay for creating PostgreSQL connections one by one on the request path.
MIN_CONN = _env_int('POSTGRES_POOL_MIN_CONN', 16, minimum=1)
MAX_CONN = max(MIN_CONN, _env_int('POSTGRES_POOL_MAX_CONN', 40, minimum=MIN_CONN))
POOL_ACQUIRE_TIMEOUT_SECONDS = _env_float('POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS', 30, minimum=1)
POOL_WAIT_WARN_SECONDS = _env_float('POSTGRES_POOL_WAIT_WARN_SECONDS', 2, minimum=0)
POOL = None
POOL_LOCK = threading.Lock()
POOL_SEMAPHORE = None
STATUS_IMPORT_INSERT_PAGE_SIZE = max(200, int(os.getenv('STATUS_IMPORT_INSERT_PAGE_SIZE', '2000')))
# Retention horizon for raw operator_status_events. They are only a rebuild source for
# operator_status_segments (the durable report data, which is never purged). The daily purge
# (purge_old_operator_status_events) drops events older than this, and segment rebuilds are
# clamped to this floor so they never depend on already-purged events. Minimum 30d guard rail.
STATUS_EVENTS_RETENTION_DAYS = _env_int('STATUS_EVENTS_RETENTION_DAYS', 120, minimum=30)
# Два источника статусов у одного человека (оператор СЗоВ: Oktell выгрузкой + iCORE Phone на
# обзвоне). Пока телефон подключён, учёт идёт по нему; если он замолчал без «выключен» дольше
# этого порога, а в выгрузке после этого есть события, сессия телефона считается оборванной
# (OperatorStatusMixin._overlay_live_status_sessions). Час — длиннее обычного перерыва.
LIVE_STATUS_SILENCE_LIMIT_MINUTES = _env_int('LIVE_STATUS_SILENCE_LIMIT_MINUTES', 60, minimum=5)
# Прогоны парсера резюме: каждый хранит полный снимок выдачи, а раздел читает только
# последний успешный. Две недели — запас на разбор «что нашлось вчера»; без чистки 98%
# recruiting_resumes были повторами (89 тыс. строк на 1,8 тыс. разных резюме, 19.10.2026).
RECRUITING_RUNS_KEEP_DAYS = _env_int('RECRUITING_RUNS_KEEP_DAYS', 14, minimum=1)
SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL = 'shift_auction_test_events'
# Канал «тычков» колокола уведомлений: pg_notify шлют триггеры на таблицах
# источников (см. _init_bell_notify_schema_tx), слушает notifications/realtime.py.
BELL_EVENTS_NOTIFY_CHANNEL = 'bell_events'
SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS = _env_float('SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS', 1.5, minimum=0)
SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS = _env_float('SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS', 2, minimum=0)
# Кеши держим ПО НАПРАВЛЕНИЯМ: один слот на оба аукциона отдавал бы снимок линии
# на запрос чата и наоборот. Слоты создаются лениво, поэтому словари не зависят от
# порядка объявления констант ниже.
SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE = {}
SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE_LOCK = threading.Lock()
SHIFT_AUCTION_PARTICIPANT_CACHE = {}
SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK = threading.Lock()


def _shift_auction_snapshot_cache_slot(direction_mode):
    return SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE.setdefault(
        normalize_shift_auction_mode(direction_mode),
        {"key": None, "expires_at": 0.0, "value": None},
    )


def _shift_auction_participant_cache_slot(direction_mode):
    return SHIFT_AUCTION_PARTICIPANT_CACHE.setdefault(
        normalize_shift_auction_mode(direction_mode),
        {"expires_at": 0.0, "ids": frozenset()},
    )
SHIFT_AUCTION_DIRECTION_NAME = 'Основа'
SHIFT_AUCTION_DEPARTMENT_CODE = 'szov'
SHIFT_AUCTION_ACTIVE_OPERATOR_STATUS = 'working'

# Аукцион смен идёт на нескольких направлениях. В СЗоВ их два — линия («Основа») и
# чат («Чат менеджер»); с задачи #124 к ним добавились две линии отдела Тез КЦ.
# Каждое направление — независимый прогон на общих таблицах: они разделены колонкой
# direction_mode ровно так же, как планы графиков (resource_saved_schedule_plans.
# direction_mode). У линии режим 'line' стоит по умолчанию, поэтому существующий
# аукцион миграцией не тронут.
SHIFT_AUCTION_MODE_LINE = 'line'
SHIFT_AUCTION_MODE_CHAT = 'chat'
# Тез КЦ (задача #124): «ТП линия» (техподдержка) и «ОП линия» (продажи). Нагрузка у
# отдела распределена не так, как в СЗоВ, и смены им не генерирует «Расчёт ресурсов»:
# руководитель загружает готовый график файлом, а каждая линия разыгрывает свои смены
# отдельным прогоном — у техподдержки и продаж разные люди и разная работа.
SHIFT_AUCTION_MODE_TEZ_TP = 'tez_tp'
SHIFT_AUCTION_MODE_TEZ_OP = 'tez_op'
SHIFT_AUCTION_MODES = (
    SHIFT_AUCTION_MODE_LINE,
    SHIFT_AUCTION_MODE_CHAT,
    SHIFT_AUCTION_MODE_TEZ_TP,
    SHIFT_AUCTION_MODE_TEZ_OP,
)
# Тот же отбор направлений, что в resource_fte/chat.py, НО с границей отдела: в
# компании есть ещё «ТП чат» отдела Тез, и без dep.code его люди уехали бы в
# аукцион СЗоВ (см. direction-rename-detaches-operators — имена направлений дублируются).
SHIFT_AUCTION_CHAT_DIRECTION_PATTERN = '%чат%'
SHIFT_AUCTION_TEZ_DEPARTMENT_CODE = 'tez'
# Отдел каждого прогона: граница, без которой одноимённые направления разных отделов
# смешались бы в одном аукционе.
SHIFT_AUCTION_MODE_DEPARTMENT_CODE = {
    SHIFT_AUCTION_MODE_LINE: SHIFT_AUCTION_DEPARTMENT_CODE,
    SHIFT_AUCTION_MODE_CHAT: SHIFT_AUCTION_DEPARTMENT_CODE,
    SHIFT_AUCTION_MODE_TEZ_TP: SHIFT_AUCTION_TEZ_DEPARTMENT_CODE,
    SHIFT_AUCTION_MODE_TEZ_OP: SHIFT_AUCTION_TEZ_DEPARTMENT_CODE,
}
# Точное имя направления у прогонов с одним направлением. У чата его нет: там
# шаблон SHIFT_AUCTION_CHAT_DIRECTION_PATTERN.
SHIFT_AUCTION_MODE_DIRECTION_NAME = {
    SHIFT_AUCTION_MODE_LINE: SHIFT_AUCTION_DIRECTION_NAME,
    SHIFT_AUCTION_MODE_TEZ_TP: 'ТП линия',
    SHIFT_AUCTION_MODE_TEZ_OP: 'ОП линия',
}
SHIFT_AUCTION_MODE_LABELS = {
    SHIFT_AUCTION_MODE_LINE: 'Линия',
    SHIFT_AUCTION_MODE_CHAT: 'Чат',
    SHIFT_AUCTION_MODE_TEZ_TP: 'ТП линия',
    SHIFT_AUCTION_MODE_TEZ_OP: 'ОП линия',
}
# Где смены прогона берутся из файла руководителя, а не из «Расчёта ресурсов».
SHIFT_AUCTION_UPLOAD_MODES = (SHIFT_AUCTION_MODE_TEZ_TP, SHIFT_AUCTION_MODE_TEZ_OP)
SHIFT_AUCTION_DEPARTMENT_LABELS = {
    SHIFT_AUCTION_DEPARTMENT_CODE: 'СЗоВ',
    SHIFT_AUCTION_TEZ_DEPARTMENT_CODE: 'Тез КЦ',
}
# Где день не запирается первой сменой: можно взять ещё смену в тот же день, если
# она не перекрывает уже взятое (недельная норма при этом действует). Чат — решение
# владельца 29.08.2026; Тез — 12.11.2026: клетку файла «11*15 18*20» выставляем
# двумя отдельными сменами, и взять обе должен мочь один человек.
SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES = (
    SHIFT_AUCTION_MODE_CHAT,
    SHIFT_AUCTION_MODE_TEZ_TP,
    SHIFT_AUCTION_MODE_TEZ_OP,
)
# Ночные смены прогона: две такие подряд брать нельзя. В СЗоВ ночь — ровно «20*08»
# (решение владельца 29.08.2026). У Тез КЦ своя ночь — «21*09»: по графикам
# октября–ноября 2026 это самая частая смена техподдержки после утренней.
SHIFT_AUCTION_NIGHT_SHIFTS = {
    SHIFT_AUCTION_MODE_LINE: (('20:00', '08:00'),),
    SHIFT_AUCTION_MODE_CHAT: (('20:00', '08:00'),),
    SHIFT_AUCTION_MODE_TEZ_TP: (('20:00', '08:00'), ('21:00', '09:00')),
    SHIFT_AUCTION_MODE_TEZ_OP: (('20:00', '08:00'), ('21:00', '09:00')),
}
# Настройки каждого направления — своя строка бывшей singleton-таблицы
# shift_auction_test_access. Строка линии остаётся первой, чат получает вторую.
SHIFT_AUCTION_SETTINGS_ROW_ID = {
    SHIFT_AUCTION_MODE_LINE: 1,
    SHIFT_AUCTION_MODE_CHAT: 2,
    SHIFT_AUCTION_MODE_TEZ_TP: 3,
    SHIFT_AUCTION_MODE_TEZ_OP: 4,
}


def normalize_shift_auction_mode(value):
    """Направление аукциона из чего угодно: неизвестное значение — линия.

    Аукцион линии боевой, поэтому «не понял параметр» обязано означать именно его,
    а не отказ: иначе опечатка в запросе увела бы оператора в пустой чат-аукцион.
    """
    mode = _normalize_shift_auction_scope_value(value)
    return mode if mode in SHIFT_AUCTION_MODES else SHIFT_AUCTION_MODE_LINE


def shift_auction_settings_row_id(direction_mode):
    return SHIFT_AUCTION_SETTINGS_ROW_ID[normalize_shift_auction_mode(direction_mode)]


def shift_auction_direction_scope_sql(direction_mode, direction_alias='d', department_alias='dep'):
    """Условие «человек принадлежит направлению этого аукциона» + его параметры.

    Отдел берётся у прогона (СЗоВ или Тез КЦ), направление — точным именем, а у
    чата шаблоном. Возвращаем пару (SQL, params), чтобы ни один вызов не собирал
    условие строкой у себя.
    """
    mode = normalize_shift_auction_mode(direction_mode)
    department_code = SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode]
    department_clause = f"LOWER(BTRIM(COALESCE({department_alias}.code, ''))) = %s"
    if mode == SHIFT_AUCTION_MODE_CHAT:
        return (
            f"BTRIM(COALESCE({direction_alias}.name, '')) ILIKE %s AND {department_clause}",
            (SHIFT_AUCTION_CHAT_DIRECTION_PATTERN, department_code),
        )
    return (
        f"BTRIM(COALESCE({direction_alias}.name, '')) = %s AND {department_clause}",
        (SHIFT_AUCTION_MODE_DIRECTION_NAME[mode], department_code),
    )


def shift_auction_mode_matches_direction(direction_name, department_code):
    """Направление человека → режим аукциона, или None, если он вне аукциона вовсе."""
    name = _normalize_shift_auction_scope_value(direction_name)
    department = _normalize_shift_auction_scope_value(department_code)
    if not name:
        return None
    for mode in SHIFT_AUCTION_MODES:
        if department != _normalize_shift_auction_scope_value(SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode]):
            continue
        if mode == SHIFT_AUCTION_MODE_CHAT:
            if 'чат' in name:
                return mode
            continue
        if name == _normalize_shift_auction_scope_value(SHIFT_AUCTION_MODE_DIRECTION_NAME[mode]):
            return mode
    return None


def shift_auction_modes_for_departments(department_codes):
    """Прогоны аукциона, которые принадлежат этим отделам, в порядке SHIFT_AUCTION_MODES."""
    codes = {
        _normalize_shift_auction_scope_value(code)
        for code in (department_codes or ())
        if _normalize_shift_auction_scope_value(code)
    }
    return tuple(
        mode for mode in SHIFT_AUCTION_MODES
        if SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode] in codes
    )


def shift_auction_night_shifts(direction_mode):
    """Пары (начало, конец) ночных смен прогона — по ним действует «две ночи подряд нельзя»."""
    return SHIFT_AUCTION_NIGHT_SHIFTS[normalize_shift_auction_mode(direction_mode)]


def shift_auction_direction_rules(direction_mode):
    """Что экрану аукциона нужно знать о направлении: подпись, отдел и правила.

    Фронт не сравнивает режим со словом 'chat' у себя: правила приходят отсюда,
    поэтому новое направление не требует правок в каждом месте экрана.
    """
    mode = normalize_shift_auction_mode(direction_mode)
    return {
        "key": mode,
        "label": SHIFT_AUCTION_MODE_LABELS[mode],
        "department_code": SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode],
        "department_label": SHIFT_AUCTION_DEPARTMENT_LABELS.get(SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode], ''),
        "shift_source": "upload" if mode in SHIFT_AUCTION_UPLOAD_MODES else "plan",
        "partial_claims": mode == SHIFT_AUCTION_MODE_CHAT,
        "several_shifts_per_day": mode in SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES,
        "night_shifts": [
            {"start": start, "end": end}
            for start, end in SHIFT_AUCTION_NIGHT_SHIFTS[mode]
        ],
    }

# Отдел, чьи менеджеры считаются в обзвоне регионов (задача #159).
FRONT_OFFICE_DEPARTMENT_CODE = 'front_office'

# Отделы, где статусы приезжают ПУШЕМ с телефона (iCORE Phone шлёт событие сразу,
# см. append_operator_status_event), а не выгрузкой по расписанию. Только у них
# таймлайн и соответствие имеет смысл показывать «живыми»: у Oktell забор идёт
# несколько раз в сутки, и дорисованный до «сейчас» хвост был бы выдумкой.
# Задача #330: владелец ограничил живой режим отделом Тез КЦ.
LIVE_STATUS_TRACK_DEPARTMENT_CODES = ('tez',)

PROXY_STATUS_VALUES = ('lost', 'returned_to_hr', 'not_received', 'on_hand')


def _normalize_shift_auction_scope_value(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def _is_shift_auction_operational_participant_row(row, direction_mode=SHIFT_AUCTION_MODE_LINE):
    """Строка участника «в строю» для ЭТОГО направления аукциона.

    Границу отдела проверяет запрос, который эти строки собрал (там есть dep.code),
    здесь остаётся направление из row[6] и рабочий статус.
    """
    if not row or len(row) <= 6:
        return False
    if _normalize_shift_auction_scope_value(row[3]) != SHIFT_AUCTION_ACTIVE_OPERATOR_STATUS:
        return False
    direction_name = _normalize_shift_auction_scope_value(row[6])
    mode = normalize_shift_auction_mode(direction_mode)
    if mode == SHIFT_AUCTION_MODE_CHAT:
        return 'чат' in direction_name
    return direction_name == _normalize_shift_auction_scope_value(SHIFT_AUCTION_MODE_DIRECTION_NAME[mode])


def _coerce_four_you_annotations(value):
    """JSONB may come back as a dict (psycopg2 auto-parse) or a str — normalise to dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
PROXY_STATUS_LABELS = {
    'lost': 'Утерян',
    'returned_to_hr': 'Сдан в HR',
    'not_received': 'Не получал',
    'on_hand': 'На руках',
}


def normalize_proxy_status_value(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    aliases = {
        'утерян': 'lost',
        'lost': 'lost',
        'сдан в hr': 'returned_to_hr',
        'сдан hr': 'returned_to_hr',
        'returned_to_hr': 'returned_to_hr',
        'returned': 'returned_to_hr',
        'не получал': 'not_received',
        'не получил': 'not_received',
        'not_received': 'not_received',
        'not received': 'not_received',
        'на руках': 'on_hand',
        'on_hand': 'on_hand',
        'on hand': 'on_hand',
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in PROXY_STATUS_VALUES:
        raise ValueError("Invalid proxy_status")
    return normalized


# SIP-номера и коды: цифры, буквы и служебные символы набора (*, #, +).
# Пробелы/скобки/дефисы из копипаста вычищаем — АТС их не принимает.
SIP_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9*#+._-]{1,64}$')


# База пароля может быть шаблоном: «Secret{номер}!» → «Secret1024!».
# Без плейсхолдера работает по-старому: база + номер.
SIP_PASSWORD_PLACEHOLDER_RE = re.compile(r'\{[^{}]*\}')


def build_sip_password(template, number) -> str:
    """Пароль SIP-аккаунта из базы/шаблона и номера."""
    template = str(template or '')
    number = str(number or '').strip()
    if not template or not number:
        return ''
    if SIP_PASSWORD_PLACEHOLDER_RE.search(template):
        return SIP_PASSWORD_PLACEHOLDER_RE.sub(number, template)
    return f'{template}{number}'


# Провайдер телефонии отдела. «asterisk» — локальная АТС: сервер и база пароля
# общие для отдела, логин = внутренний номер, пароль = база + номер, есть FOP2.
# «binotel» — облако Binotel: у каждого сотрудника свой сервер, свой непрозрачный
# логин и свой пароль, FOP2 нет вообще.
SIP_PROVIDERS = ('asterisk', 'binotel')
BINOTEL_CABINET_URL_DEFAULT = 'https://my.binotel.kz'
SIP_PROVIDER_DEFAULT = 'asterisk'


def normalize_sip_provider(value, default=SIP_PROVIDER_DEFAULT) -> str:
    """Провайдер из payload'а. Неизвестное значение — ошибка, а не тихий дефолт:
    молча съеденный «binotel» отдал бы тезовцам пароль «база + номер»."""
    if value is None or str(value).strip() == '':
        return default
    provider = str(value).strip().lower()
    if provider not in SIP_PROVIDERS:
        raise ValueError(f"Неизвестный провайдер телефонии: {value}")
    return provider


SIP_FLAG_FALSE = ('', '0', 'false', 'no', 'off')


def parse_sip_flag(value, default=True) -> bool:
    """Булев флаг из payload'а: None = «не меняем», строку разбираем руками.

    Отдельно от текстовых полей, потому что str(False).strip() дал бы непустую
    строку 'False' — то есть «истину», и выключатель никогда бы не выключался.
    JSON присылает bool, multipart-форма — '0'/'1'.
    """
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() not in SIP_FLAG_FALSE
    return bool(value)


# Автопринятие звонка: сколько секунд телефон показывает окно входящего до того,
# как ответить сам. Два — то, что просил заказчик: увидеть, кто звонит, оператор
# успевает, а отказаться уже не может. Потолок — чтобы опечатка не превратила
# настройку в «минуту окна без кнопок» у оператора на линии.
SIP_AUTO_ANSWER_DELAY_DEFAULT = 2
SIP_AUTO_ANSWER_DELAY_MAX = 30


def parse_sip_delay(value, default=SIP_AUTO_ANSWER_DELAY_DEFAULT) -> int:
    """Задержка автопринятия из payload'а, в секундах.

    Своя функция, а не parse_sip_flag: у числа другая семантика пустоты. Для
    флага '' — это «выключено», а здесь '' значит «оставить как было» (поле в
    форме просто не заполнили), тогда как '0' — осмысленное «отвечать сразу».
    Мусор тоже возвращает прежнее значение: сорванная валидация на фронте не
    повод молча выставить оператору ноль.
    """
    # Разбор один на двоих с parse_sip_delay_tri — две копии клампа и проверки
    # bool разъехались бы при первой же правке. Здесь только «сплющиваем»
    # третье состояние: в двухпозиционном разборе «как у отдела» смысла не имеет.
    parsed = parse_sip_delay_tri(value, default)
    return int(default if parsed is None else parsed)


# Третье состояние автопринятия — «как у отдела». Своим токеном, а не пустотой:
# None в payload'е уже занят под «поле не прислали, не меняем», а пустая строка
# у флага означает «выключено» (SIP_FLAG_FALSE). Без отдельного слова снять
# персональную настройку было бы нечем — она стала бы неснимаемой.
SIP_INHERIT_TOKEN = 'inherit'


def is_sip_inherit(value) -> bool:
    """Просят ли вернуть поле к настройке отдела."""
    return isinstance(value, str) and value.strip().lower() == SIP_INHERIT_TOKEN


def parse_sip_flag_tri(value, default=None):
    """Трёхпозиционный флаг: True / False / None («как у отдела»).

    None на входе — «поле не прислали», возвращаем прежнее значение; токен
    SIP_INHERIT_TOKEN — «наследовать», то есть NULL в базе.
    """
    if value is None:
        return default
    if is_sip_inherit(value):
        return None
    return parse_sip_flag(value, default if default is not None else False)


def parse_sip_delay_tri(value, default=None):
    """То же для задержки: число / None («как у отдела»).

    Пустая строка здесь по-прежнему «оставить как было», а не «наследовать»:
    поле в форме просто не заполнили, и молча снимать настройку из-за этого
    нельзя. Разбор свой, а не через parse_sip_delay: тот на мусоре возвращает
    ЧИСЛО по умолчанию, и унаследованное значение молча превратилось бы в
    персональные две секунды — то есть человек отцепился бы от настройки
    отдела из-за сорванной валидации на фронте.
    """
    if value is None:
        return default
    if is_sip_inherit(value):
        return None
    # bool — это тоже int в Python, поэтому проверка до попытки разбора:
    # auto_answer_delay: true не должно стать «1 секунда».
    if isinstance(value, bool):
        return default
    if isinstance(value, str) and value.strip() == '':
        return default
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(0, min(SIP_AUTO_ANSWER_DELAY_MAX, seconds))


def resolve_sip_auto_answer(personal, department) -> bool:
    """Эффективное автопринятие: персональное → отдела → выключено.

    Ярус отдела появился позже персонального, поэтому порядок именно такой:
    у кого стоит своё — тот общее не читает (решение владельца по задаче #309).
    """
    if personal is not None:
        return bool(personal)
    if department is not None:
        return bool(department)
    return False


def resolve_sip_auto_answer_delay(personal, department) -> int:
    """Эффективная задержка окна; наследуется отдельно от самого флага."""
    if personal is not None:
        return int(personal)
    if department is not None:
        return int(department)
    return int(SIP_AUTO_ANSWER_DELAY_DEFAULT)


# Автосброс исходящего без ответа (запрос владельца 11.10.2026): если абонент не
# взял трубку за столько гудков, телефон кладёт её сам, и звонок пишется как
# «Не поднял трубку». Ярус один — отдел, личного значения нет. NULL в строке
# отдела — «не задавали», и действует умолчание: сброс включён у всех сразу, так
# решил владелец. 0 — не сбрасывать. Гудок в СНГ — секунда тона и четыре тишины,
# отсюда пять секунд на гудок.
SIP_RING_TIMEOUT_RINGS_DEFAULT = 5
SIP_RING_SECONDS_PER_RING = 5
SIP_RING_TIMEOUT_RINGS_MAX = 20
# Обычный исходящий телефон считает от первого 180/183, и класть трубку надо в
# тишине после N-го гудка: он отзвучал к N·5−4 с, следующий начнётся на N·5 с.
# Берём N·5−3 — пять гудков дают 22 с, и это раньше шестого даже у гудка самого
# телефона (он повторяется раз в 4,5 с, шестой прозвучал бы на 22,5 с). Обзвону
# из телефона это вычитание не нужно: там отсчёт от момента, когда Binotel начал
# набирать водителя, и время — ровно N·5.
SIP_RING_TIMEOUT_LEAD_SEC = 3


def parse_sip_ring_timeout_rings(value, default=None):
    """Число гудков до автосброса из payload'а: число / None («по умолчанию»).

    В отличие от задержки автоприёма, пустая строка здесь — «по умолчанию»
    (NULL), а не «оставить как было»: так поле в карточке отдела и подписано
    («Пусто — 5»), и иначе вернуть отдел к умолчанию было бы нечем. «Поле не
    прислали» — это отсутствие ключа, его разбирает вызывающий. bool и мусор
    оставляют прежнее значение: сорванная валидация на фронте не повод молча
    выключить сброс целому отделу.
    """
    if value is None or is_sip_inherit(value):
        return None
    # bool — тоже int: ring_timeout_rings: true не должно стать «1 гудок».
    if isinstance(value, bool):
        return default
    if isinstance(value, str) and value.strip() == '':
        return None
    try:
        rings = int(float(value))
    except (TypeError, ValueError, OverflowError):
        # OverflowError — от Infinity: json.loads его пропускает, а 500 из-за
        # одного числа в форме был бы хуже, чем прежнее значение.
        return default
    return max(0, min(SIP_RING_TIMEOUT_RINGS_MAX, rings))


def resolve_sip_ring_timeout_rings(department) -> int:
    """Эффективное число гудков: значение отдела, не задано — умолчание.

    Потолок держим и здесь: значение, вписанное в базу руками, не должно уехать
    в телефон за пределами диапазона, который допускает форма.
    """
    if department is None:
        return int(SIP_RING_TIMEOUT_RINGS_DEFAULT)
    return max(0, min(SIP_RING_TIMEOUT_RINGS_MAX, int(department)))


def sip_ring_timeout_seconds(rings) -> int:
    """Секунды от первого 180/183 до автосброса обычного исходящего; 0 — не сбрасывать."""
    rings = int(rings or 0)
    if rings <= 0:
        return 0
    return rings * SIP_RING_SECONDS_PER_RING - SIP_RING_TIMEOUT_LEAD_SEC


def normalize_sip_identifier(value, field="SIP-номер"):
    """Нормализует SIP-номер/код: '' если пусто, иначе валидирует набор символов."""
    if value is None:
        return ""
    cleaned = re.sub(r'[\s()–—]', '', str(value))
    if not cleaned:
        return ""
    if not SIP_IDENTIFIER_RE.match(cleaned):
        raise ValueError(f"{field}: допустимы только цифры, латиница и символы * # + . _ -")
    return cleaned


def _invalidate_shift_auction_runtime_caches(include_participants=False, direction_mode=None):
    """Сбросить кеши аукциона. Без direction_mode — оба направления сразу.

    Точечный сброс нужен там, где направление известно: чат не должен ронять кеш
    линии на каждый свой клик. Общий сброс остаётся для мест, которые меняют данные
    сразу обоих прогонов (например, правки в карточках людей).
    """
    modes = (
        SHIFT_AUCTION_MODES
        if direction_mode is None
        else (normalize_shift_auction_mode(direction_mode),)
    )
    with SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE_LOCK:
        for mode in modes:
            slot = _shift_auction_snapshot_cache_slot(mode)
            slot["key"] = None
            slot["expires_at"] = 0.0
            slot["value"] = None
    if include_participants:
        with SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK:
            for mode in modes:
                slot = _shift_auction_participant_cache_slot(mode)
                slot["expires_at"] = 0.0
                slot["ids"] = frozenset()

ROLE_ALIASES = {
    'supervisor': 'sv',
    'superadmin': 'super_admin',
    'super-admin': 'super_admin',
    'super admin': 'super_admin'
}

# hr_manager / accounting_manager / marketing_manager — рядовые сотрудники
# отделов без линии (Бухгалтерия, HR, Маркетинг). Уровень тот же, что у
# оператора: они не на линии, но и не начальство.
# Уровень читает не только _has_min_role: wiki/access.py::expand_otp_roles
# раздаёт человеку все роли не выше его уровня, поэтому на десятке новые роли
# подпадают под уже написанные правила вики на 'operator'.
ROLE_HIERARCHY = {
    'operator': 10,
    'trainee': 10,
    'hr_manager': 10,
    'accounting_manager': 10,
    'marketing_manager': 10,
    'trainer': 20,
    'sv': 30,
    'admin': 40,
    'super_admin': 50
}

# Рядовые сотрудники отделов без линии (Бухгалтерия, HR, Маркетинг). Зеркалит
# BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT в src/utils/departmentViews.js и
# одноимённый набор в bot_schedule2.py.
#
# «Бэк-офис» в имени набора читается как «отдел без линии»: у Маркетинга, как у
# Бухгалтерии и HR, нет ни направлений, ни групп, ни телефонии, и рядовой его
# сотрудник заводится своей ролью, а не 'operator'. Имя оставлено прежним
# намеренно — набор сторожится дословными assertIn в тестах, и переименование
# стоило бы правки десятка мест ради одного слова.
BACK_OFFICE_EMPLOYEE_ROLES = frozenset({'hr_manager', 'accounting_manager', 'marketing_manager'})

# Роли с ЛИЧНЫМ охватом задач: человек видит только то, где он сам постановщик,
# поручитель или исполнитель, и не принимает работу за других. Набор один на три
# места (_task_scope_filters, get_task_recipients, get_task_board_people) — до
# этого список ролей был переписан в каждом из них отдельно.
#
# Зеркалит _can_access_tasks в bot_schedule2.py: тот решает, пускать ли в раздел
# вообще, этот — что человек в разделе увидит. Разъедься они, раздел откроется и
# молча покажет ноль задач вместо отказа. Совпадение держит тест.
TASK_PERSONAL_SCOPE_ROLES = frozenset({'sv', 'trainer'}) | BACK_OFFICE_EMPLOYEE_ROLES

# Кого вообще можно поставить исполнителем задачи. Рядовой бэк-офиса здесь не
# ради своей доски, а чтобы задачу можно было поручить ЕМУ: без этой строки
# кадровик не появлялся бы в выпадашке ни у главы отдела, ни у админа, и раздел
# держал бы у него только то, что он завёл сам.
TASK_RECIPIENT_ROLES = ('super_admin', 'admin', 'sv', *sorted(BACK_OFFICE_EMPLOYEE_ROLES))
# Значения — константы модуля, а не пользовательский ввод: подставляются в SQL
# как литералы, отдельными плейсхолдерами их не сделать (список ролей меняет
# длину, а параметры этого запроса считаются по позициям).
_TASK_RECIPIENT_ROLES_SQL = ", ".join(f"'{role}'" for role in TASK_RECIPIENT_ROLES)

# Типы задачи («Тип» в форме). Один список на CHECK, создание, правку и фильтр
# списка: до «Продакшна» он был переписан литералом в четырёх местах, и новый
# тип пришлось бы не забыть ни в одном. Продакшн — задача #354, бриф к нему
# проверяет task_production.
TASK_TAG_CODES = ('task', 'problem', 'suggestion', task_production.PRODUCTION_TAG)

CALCULATION_MODEL_OPERATOR = 'operator'
CALCULATION_MODEL_CHAT_MANAGER = 'chat_manager'
# Модели отдела TEZ (направления «Оператор Линия TEZ» и «Оператор ОП TEZ»).
# Часы обеих моделей считаются из посегментной выгрузки статусов TEZ
# (active/work in crm = работа, break in work = перерыв, inactive = игнор),
# поэтому они делят один статус-профиль (_status_profile_for_calculation_model).
CALCULATION_MODEL_TEZ_LINE = 'tez_line'
CALCULATION_MODEL_TEZ_OP = 'tez_op'
CALCULATION_MODEL_TEZ_CODES = {CALCULATION_MODEL_TEZ_LINE, CALCULATION_MODEL_TEZ_OP}
# Модели отдела продаж (ОП): по одной модели на направление отдела
# («Верификатор», «Яндекс Регистрация», «Основа ОП», «Поток»). Пока в них
# только ручной учёт отработанных часов и штрафы (опоздания фиксируются
# штрафом); новые метрики добавляются в CALCULATION_MODEL_METRICS по мере
# запуска. Часы считаются по операторскому статус-профилю (default-ветка
# _status_profile_for_calculation_model).
CALCULATION_MODEL_OP_VERIFICATOR = 'op_verificator'
CALCULATION_MODEL_OP_YANDEX_REG = 'op_yandex_reg'
CALCULATION_MODEL_OP_OSNOVA = 'op_osnova'
CALCULATION_MODEL_OP_POTOK = 'op_potok'
CALCULATION_MODEL_OP_SALES_CODES = {
    CALCULATION_MODEL_OP_VERIFICATOR,
    CALCULATION_MODEL_OP_YANDEX_REG,
    CALCULATION_MODEL_OP_OSNOVA,
    CALCULATION_MODEL_OP_POTOK,
}
CALCULATION_MODEL_ALLOWED = {
    CALCULATION_MODEL_OPERATOR,
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_TEZ_LINE,
    CALCULATION_MODEL_TEZ_OP
} | CALCULATION_MODEL_OP_SALES_CODES
CALCULATION_MODEL_DESCRIPTIONS = {
    CALCULATION_MODEL_OPERATOR: {
        'code': CALCULATION_MODEL_OPERATOR,
        'name': 'Операторская модель',
        'description': 'Звонковая модель: рабочие часы по операторским статусам, звонки в час, качество, стандартная формула зарплаты.'
    },
    CALCULATION_MODEL_CHAT_MANAGER: {
        'code': CALCULATION_MODEL_CHAT_MANAGER,
        'name': 'Модель чат-менеджера',
        'description': 'Чат-модель: рабочие часы по Chat2Desk-статусам, чаты в час, средняя оценка, время ответа, формула зарплаты чат-менеджера.'
    },
    CALCULATION_MODEL_TEZ_LINE: {
        'code': CALCULATION_MODEL_TEZ_LINE,
        'name': 'TEZ — Линия (тех поддержка)',
        'description': 'TEZ-модель «Линия/ТП»: рабочие часы по выгрузке статусов TEZ (active/work in crm), качество, надбавка за стаж.'
    },
    CALCULATION_MODEL_TEZ_OP: {
        'code': CALCULATION_MODEL_TEZ_OP,
        'name': 'TEZ — ОП',
        'description': 'TEZ-модель «ОП»: рабочие часы по выгрузке статусов TEZ, качество и месячный план (% выполнения по сделкам).'
    },
    CALCULATION_MODEL_OP_VERIFICATOR: {
        'code': CALCULATION_MODEL_OP_VERIFICATOR,
        'name': 'ОП — Верификатор',
        'description': 'Модель направления «Верификатор» отдела продаж: ручной учёт отработанных часов и штрафы (опоздания).'
    },
    CALCULATION_MODEL_OP_YANDEX_REG: {
        'code': CALCULATION_MODEL_OP_YANDEX_REG,
        'name': 'ОП — Яндекс Регистрация',
        'description': 'Модель направления «Яндекс Регистрация» отдела продаж: ручной учёт отработанных часов и штрафы (опоздания).'
    },
    CALCULATION_MODEL_OP_OSNOVA: {
        'code': CALCULATION_MODEL_OP_OSNOVA,
        'name': 'ОП — Основа',
        'description': 'Модель направления «Основа ОП» отдела продаж: ручной учёт отработанных часов и штрафы (опоздания).'
    },
    CALCULATION_MODEL_OP_POTOK: {
        'code': CALCULATION_MODEL_OP_POTOK,
        'name': 'ОП — Поток',
        'description': 'Модель направления «Поток» отдела продаж: ручной учёт отработанных часов и штрафы (опоздания).'
    }
}


def normalize_role_value(role_value: Optional[str]) -> str:
    role_norm = str(role_value or '').strip().lower()
    return ROLE_ALIASES.get(role_norm, role_norm)


def role_has_min(role_value: Optional[str], required_role: str) -> bool:
    role_level = int(ROLE_HIERARCHY.get(normalize_role_value(role_value), 0))
    required_level = int(ROLE_HIERARCHY.get(normalize_role_value(required_role), 0))
    if required_level <= 0:
        return False
    return role_level >= required_level


def role_is_any(role_value: Optional[str], allowed_roles: List[str]) -> bool:
    role_norm = normalize_role_value(role_value)
    if not role_norm:
        return False
    allowed = {normalize_role_value(item) for item in (allowed_roles or [])}
    return role_norm in allowed


def _normalize_direction_name_key(direction_name: Optional[str]) -> str:
    return ' '.join(str(direction_name or '').strip().lower().split())


def normalize_calculation_model_code(model_code: Optional[str], direction_name: Optional[str] = None) -> str:
    code = str(model_code or '').strip().lower()
    if code in CALCULATION_MODEL_ALLOWED:
        return code
    return CALCULATION_MODEL_OPERATOR


def get_calculation_model_catalog() -> List[Dict[str, str]]:
    return [
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_OPERATOR]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_CHAT_MANAGER]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_TEZ_LINE]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_TEZ_OP]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_OP_VERIFICATOR]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_OP_YANDEX_REG]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_OP_OSNOVA]),
        dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_OP_POTOK]),
    ]


# ──────────────────────────────────────────────────────────────────────────
# РЕЕСТР МЕТРИК НА МОДЕЛЬ РАСЧЁТА.
# Декларативное описание метрик каждой модели: что показывать в учёте часов и
# отчётах и как метрика заполняется. Добавление новой модели/метрики = запись
# здесь, без правок веток рендера в коде.
#   fill='manual'   — заполняется руками в учёте часов (редактируемая ячейка);
#   fill='import'   — подтягивается импортом/из источника (только чтение);
#   fill='computed' — считается формулой (только чтение).
#   store           — куда сохраняется значение: колонка таблицы
#                     ('daily_hours.work_time'), отдельная таблица
#                     ('daily_bonuses') или ключ в daily_hours.extra_metrics
#                     ('daily_hours.extra_metrics.<key>') для новых метрик без
#                     выделенной колонки.
#   tab             — ключ группировки/вкладки на экране учёта часов.
# ──────────────────────────────────────────────────────────────────────────
def _calc_metric(key, label, unit='', fill='manual', store=None, tab=None):
    return {
        'key': key,
        'label': label,
        'unit': unit,
        'fill': fill,
        'store': store,
        'tab': tab or key,
    }


# Общие метрики, одинаковые для всех моделей (часы/тренинги/штрафы/бонусы).
_CALC_METRICS_HEAD = [
    _calc_metric('work_time', 'Отработанные часы', 'ч', 'manual', 'daily_hours.work_time'),
    _calc_metric('break_time', 'Перерыв', 'ч', 'manual', 'daily_hours.break_time'),
]
_CALC_METRICS_TAIL = [
    _calc_metric('trainings', 'Тренинги', '', 'manual', 'daily_hours.training_time'),
    _calc_metric('technical_issues', 'Тех причины', 'ч', 'manual', 'daily_hours.technical_reason_minutes'),
    _calc_metric('offline_activity', 'Офлайн активность', 'ч', 'manual', 'operator_offline_activities'),
    _calc_metric('no_phone', 'Без телефона', 'ч', 'manual', 'daily_hours.no_phone_minutes'),
    _calc_metric('bonuses', 'Бонусы', '₸', 'manual', 'daily_bonuses'),
    _calc_metric('fines', 'Штрафы', '₸', 'manual', 'daily_fines'),
]
# Минимальный набор моделей направлений ОП: часы работы + штрафы (опоздания).
_CALC_METRICS_OP_SALES = [
    _calc_metric('work_time', 'Отработанные часы', 'ч', 'manual', 'daily_hours.work_time'),
    _calc_metric('fines', 'Штрафы', '₸', 'manual', 'daily_fines'),
]

CALCULATION_MODEL_METRICS = {
    CALCULATION_MODEL_OPERATOR: _CALC_METRICS_HEAD + [
        _calc_metric('calls', 'Звонки', '', 'import', 'daily_hours.calls'),
        _calc_metric('efficiency', 'Эффективность', 'ч', 'computed', 'daily_hours.efficiency'),
        _calc_metric('talk_time', 'Время в разговоре', 'ч', 'import', 'daily_hours.talk_time'),
    ] + _CALC_METRICS_TAIL,
    CALCULATION_MODEL_CHAT_MANAGER: _CALC_METRICS_HEAD + [
        _calc_metric('chats', 'Чаты', '', 'import', 'chat_manager_daily_metrics.chats_count'),
        _calc_metric('avg_score', 'Средняя оценка', 'балл', 'import', 'chat_manager_daily_metrics.avg_score'),
        _calc_metric('response_time', 'Время ответа', 'сек', 'import', 'chat_manager_daily_metrics.avg_response_time_seconds'),
        _calc_metric('transfers', 'Переводы чатов', '', 'import', 'chat_manager_daily_metrics.transfer_chat_count'),
    ] + _CALC_METRICS_TAIL,
    # TEZ-модели: часы/перерыв считаются авто из выгрузки статусов TEZ
    # (active+work in crm = работа, break in work = перерыв), остальное — общий хвост.
    CALCULATION_MODEL_TEZ_LINE: _CALC_METRICS_HEAD + _CALC_METRICS_TAIL,
    CALCULATION_MODEL_TEZ_OP: _CALC_METRICS_HEAD + [
        _calc_metric('calls', 'Звонки', '', 'import', 'daily_hours.calls'),
        _calc_metric('dial_time', 'Время набора', 'ч', 'import', 'daily_hours.extra_metrics.dial_time'),
        _calc_metric('talk_time', 'В разговоре', 'ч', 'import', 'daily_hours.extra_metrics.talk_time'),
        _calc_metric('chats', 'Чаты', '', 'import', 'daily_hours.extra_metrics.chats'),
        _calc_metric('tez_successes', 'Успешки', 'шт', 'computed', 'tez_lead_successes'),
    ] + _CALC_METRICS_TAIL,
    # Модели направлений ОП: пока только отработанные часы (ручной ввод) и
    # штрафы (учёт опозданий). Состав вкладок учёта часов фронт берёт из этого
    # реестра, поэтому новая метрика = новая строка здесь.
    CALCULATION_MODEL_OP_VERIFICATOR: list(_CALC_METRICS_OP_SALES),
    CALCULATION_MODEL_OP_YANDEX_REG: list(_CALC_METRICS_OP_SALES),
    CALCULATION_MODEL_OP_OSNOVA: list(_CALC_METRICS_OP_SALES),
    CALCULATION_MODEL_OP_POTOK: list(_CALC_METRICS_OP_SALES),
}


def get_calculation_model_metrics(model_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Список метрик для модели расчёта (копии, безопасно мутировать у вызывающего)."""
    code = normalize_calculation_model_code(model_code)
    metrics = CALCULATION_MODEL_METRICS.get(code, CALCULATION_MODEL_METRICS[CALCULATION_MODEL_OPERATOR])
    return [dict(m) for m in metrics]

# Вставьте/адаптируйте этот helper в ваш модуль
def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return None
    # Оставим, например, последние 10-11 цифр (зависит от вашей логики)
    return digits


_CALL_END_PARTIES = frozenset({'operator', 'client', 'system', 'transfer', 'unknown'})


def _normalize_call_end_party(value: Optional[str]) -> Optional[str]:
    """Return the canonical call-ending party stored by the evaluation journal."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _CALL_END_PARTIES else 'unknown'


def _parse_datetime_raw(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    # ожидаемый формат 'dd.mm.yyyy hh:mm:ss'
    formats = ['%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M']  # запасные форматы
    for fmt in formats:
        try:
            # возвращаем timezone-naive datetime; при вставке используем AT TIME ZONE 'Asia/Almaty' или локаль
            return datetime.strptime(dt_str, fmt)
        except Exception:
            continue
    return None

def _time_to_minutes(time_str: str) -> int:
    """Преобразует время в формате 'HH:MM' в минуты от начала дня."""
    try:
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes
    except (ValueError, AttributeError):
        return 0

def _minutes_to_time(minutes: int) -> str:
    """Преобразует минуты от начала дня в формат 'HH:MM'."""
    minutes = minutes % (24 * 60)  # нормализация для смен через полночь
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"

WORK_SHIFT_TYPE_REGULAR = 'regular'
WORK_SHIFT_TYPE_OFFICE_PRACTICE = 'office_practice'
WORK_SHIFT_TYPE_PHONE_SHIFT = 'phone_shift'
WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT = 'Практика в офисе'
WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT = 'смена на телефонах'
WORK_SHIFT_TYPE_ALLOWED = {
    WORK_SHIFT_TYPE_REGULAR,
    WORK_SHIFT_TYPE_OFFICE_PRACTICE,
    WORK_SHIFT_TYPE_PHONE_SHIFT
}
WORK_SHIFT_TYPE_PRIORITY = {
    WORK_SHIFT_TYPE_REGULAR: 0,
    WORK_SHIFT_TYPE_OFFICE_PRACTICE: 1,
    WORK_SHIFT_TYPE_PHONE_SHIFT: 2
}

# Прошедшие перерывы не пересчитываются: автоматика планирует только остаток
# смены, начиная не раньше чем через SHIFT_BREAK_PLANNING_BUFFER_MINUTES минут,
# чтобы перерыв не появлялся у оператора «через пару минут».
SHIFT_BREAK_PLANNING_BUFFER_MINUTES = 15
# Минимальные отступы, при которых перерыв ещё имеет смысл ставить в остаток смены.
# Не влезло — перерыв не ставится вовсе: это лучше, чем перерыв впритык к концу смены.
SHIFT_BREAK_MIN_EDGE_MARGIN_MINUTES = 30
SHIFT_BREAK_MIN_GAP_MINUTES = 15

# История изменений графика. Действие описывает, что стало со сменой, источник —
# откуда пришла правка. Пара «действие + источник» и даёт подпись в интерфейсе
# («Взята с аукциона», «Добавлена супервайзером ...»), поэтому источник хранится
# отдельным полем, а не зашит в текст: тексты меняются, данные — нет.
WORK_SHIFT_CHANGE_ACTIONS = (
    'added',
    'removed',
    'changed',
    'day_off_set',
    'day_off_cleared',
)
WORK_SHIFT_CHANGE_SOURCES = (
    'supervisor',          # ручная правка в разделе «Графики работы»
    'auction',             # публикация аукциона смен
    'auction_topup',       # оператор сам добрал смену после аукциона
    'auction_topup_cancel',  # оператор отменил свой добор
    'auction_admin',       # смену с аукциона выдал/снял администратор
    'swap',                # обмен сменами между операторами
    'shift_request',       # заявка оператора по своей смене: сократить, доп. часть, перенос, выходной
    'import',              # загрузка графика из файла
    'status_period',       # отпуск/больничный/увольнение стирает смены периода
    'system',              # источник не определён
)

# Пересечение перерывов РАЗНЫХ операторов направления (задача #175).
# Перерывы больше не разбегаются от любого пересечения — иначе на плотном
# направлении свободными остаются только края смены, и вся норма сбивается
# в начало или конец. Вместо этого:
#   * пересечение пары перерывов допустимо в пределах SHIFT_BREAK_PAIR_OVERLAP_*
#     (15 мин → 10, 30 мин → 15);
#   * одновременно на перерыве не больше SHIFT_BREAK_MAX_CONCURRENT операторов;
#   * третий выходит только на пересдаче — не дольше
#     SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES минут подряд.
SHIFT_BREAK_MAX_CONCURRENT = 2
SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES = 5
SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES = 5
SHIFT_BREAK_PAIR_OVERLAP_SHARE = 3
# Веса раскладки: сначала правила пересечений, затем пожелание держать дистанцию
# (настройка направления), затем просто пересечение, и всё это против
# неравномерности рабочих отрезков между перерывами.
SHIFT_BREAK_WEIGHT_CONCURRENCY = 300
SHIFT_BREAK_WEIGHT_PAIR_EXCESS = 200
SHIFT_BREAK_WEIGHT_CROSS_GAP = 200
SHIFT_BREAK_WEIGHT_OVERLAP = 1
SHIFT_BREAK_WEIGHT_EVENNESS = 10
# Слабое притяжение к уже стоящей позиции: решает только при равном счёте.
SHIFT_BREAK_WEIGHT_KEEP_PLACE = 1

# Равномерность (доработка задачи #175, 2026-08-18).
# Перерывы смены подбираются НЕ по одному, а совместно: цель — одинаковые
# рабочие отрезки между ними, а не близость каждого перерыва к своей точке.
# Иначе сдвиг одного обеда на два часа оставлял соседние на месте и получалось
# «275 минут работы подряд, потом три перерыва через 45 минут».
#   * SHIFT_BREAK_EVEN_WINDOW_SHARE — насколько перерыв вообще может отойти от
#     своей равномерной точки (доля рабочего отрезка). Это и гарантия
#     равномерности, и сужение перебора;
#   * SHIFT_BREAK_LAYOUT_PASSES — сколько раз пройтись по операторам направления.
#     За один проход последним места не остаётся: 11 нарушений «трое дольше пяти
#     минут» против нуля за три прохода (замер на боевых графиках).
SHIFT_BREAK_EVEN_WINDOW_SHARE = 0.30
SHIFT_BREAK_LAYOUT_PASSES = 3


def _normalize_work_shift_type_value(value: Optional[str]) -> str:
    raw = str(value or '').strip().lower()
    if raw in ('', 'regular', 'обычная', 'обычная смена'):
        return WORK_SHIFT_TYPE_REGULAR
    if raw in ('office_practice', 'practice', 'практика', 'практика в офисе', 'практика в офисе таксопарка'):
        return WORK_SHIFT_TYPE_OFFICE_PRACTICE
    if raw in ('phone_shift', 'phones', 'phone', 'телефоны', 'на телефонах', 'смена на телефонах'):
        return WORK_SHIFT_TYPE_PHONE_SHIFT
    if raw not in WORK_SHIFT_TYPE_ALLOWED:
        raise ValueError("Invalid shift_type")
    return raw

def _work_shift_type_priority(value: Optional[str]) -> int:
    return int(WORK_SHIFT_TYPE_PRIORITY.get(_normalize_work_shift_type_value(value), 0))

def _merge_shifts_for_date(shifts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Объединяет перекрывающиеся смены на одну дату.
    shifts: список словарей с ключами 'start', 'end', 'breaks', 'id'
    Возвращает список объединенных смен.
    """
    if not shifts or len(shifts) == 0:
        return []
    
    # Преобразуем смены в интервалы в минутах
    intervals = []
    for shift in shifts:
        start_min = _time_to_minutes(shift.get('start', '00:00'))
        end_min = _time_to_minutes(shift.get('end', '00:00'))
        
        # Обработка смен через полночь
        if end_min <= start_min:
            end_min += 24 * 60
        
        intervals.append({
            'start': start_min,
            'end': end_min,
            'original': shift,
            'breaks': shift.get('breaks', []),
            'shift_type': _normalize_work_shift_type_value(
                shift.get('shift_type') if isinstance(shift, dict) and ('shift_type' in shift) else shift.get('shiftType')
            )
        })
    
    # Сортируем по времени начала
    intervals.sort(key=lambda x: x['start'])
    
    # Объединяем перекрывающиеся интервалы
    merged = []
    for interval in intervals:
        if not merged:
            merged.append(interval.copy())
        else:
            last = merged[-1]
            # Проверяем перекрытие: если начало новой смены <= конца последней
            if interval['start'] <= last['end']:
                # Объединяем: расширяем конец до максимума
                last['end'] = max(last['end'], interval['end'])
                # Объединяем перерывы
                last['breaks'].extend(interval['breaks'])
                # Сохраняем ID оригинальных смен для удаления старых
                if 'original_ids' not in last:
                    last['original_ids'] = [last['original'].get('id')]
                if interval['original'].get('id'):
                    last['original_ids'].append(interval['original'].get('id'))
                # Для объединенного интервала оставляем самый специфичный тип смены.
                if _work_shift_type_priority(interval.get('shift_type')) > _work_shift_type_priority(last.get('shift_type')):
                    last['shift_type'] = interval.get('shift_type')
            else:
                merged.append(interval.copy())
    
    # Преобразуем обратно в формат смен
    result = []
    for m in merged:
        start_time = _minutes_to_time(m['start'])
        end_time = _minutes_to_time(m['end'] % (24 * 60))
        
        # Объединяем и нормализуем перерывы (убираем дубликаты, сортируем)
        breaks = m.get('breaks', [])
        # Если перерывы в формате {'start': minutes, 'end': minutes}, оставляем как есть
        # Если в формате {'start': 'HH:MM', 'end': 'HH:MM'}, преобразуем
        normalized_breaks = []
        seen_breaks = set()
        for b in breaks:
            if isinstance(b, dict):
                start_br = b.get('start')
                end_br = b.get('end')
                # Если это строки времени, преобразуем в минуты
                if isinstance(start_br, str):
                    start_br_min = _time_to_minutes(start_br)
                else:
                    start_br_min = int(start_br) if start_br is not None else 0
                
                if isinstance(end_br, str):
                    end_br_min = _time_to_minutes(end_br)
                else:
                    end_br_min = int(end_br) if end_br is not None else 0
                
                break_key = (start_br_min, end_br_min)
                if break_key not in seen_breaks:
                    seen_breaks.add(break_key)
                    normalized_breaks.append({
                        'start': start_br_min,
                        'end': end_br_min
                    })
        
        # Сортируем перерывы по началу
        normalized_breaks.sort(key=lambda x: x['start'])
        
        result.append({
            'start': start_time,
            'end': end_time,
            'breaks': normalized_breaks,
            'shift_type': _normalize_work_shift_type_value(m.get('shift_type'))
        })
    
    return result

SCHEDULE_SPECIAL_STATUS_META: Dict[str, Dict[str, str]] = {
    'bs': {
        'label': 'Б/С',
        'kind': 'absence'
    },
    'sick_leave': {
        'label': 'Больничный',
        'kind': 'absence'
    },
    'annual_leave': {
        'label': 'Ежегодный отпуск',
        'kind': 'absence'
    },
    'dismissal': {
        'label': 'Увольнение',
        'kind': 'dismissal'
    }
}

SCHEDULE_DISMISSAL_REASONS: List[str] = [
    'Б/С на летний период',
    'Мошенничество',
    'Нарушение дисциплины',
    'Не может совмещать с учебой',
    'Не может совмещать с работой',
    'Не нравится работа',
    'Выгорание',
    'Не устраивает доход',
    'Перевод в другой отдел',
    'Переезд',
    'По состоянию здоровья',
    'Пропал',
    'Слабый/не выполняет kpi',
    'Забрали в армию',
    'Нашел работу по профессии',
    'По семейным обстоятельствам'
]

USER_STATUS_ALLOWED_VALUES: List[str] = [
    'working',
    'fired',
    'unpaid_leave',
    'bs',
    'sick_leave',
    'annual_leave',
    'dismissal'
]

SCHEDULE_STATUS_TO_USER_STATUS: Dict[str, str] = {
    'bs': 'bs',
    'sick_leave': 'sick_leave',
    'annual_leave': 'annual_leave',
    # Для совместимости с остальными экранами "увольнение" продолжаем отражать как fired
    'dismissal': 'fired'
}

SCHEDULE_AUTO_AGGREGATION_START_DATE = date(2026, 3, 1)
SCHEDULE_AUTO_FLAG_PENDING = 'pending'
SCHEDULE_AUTO_FLAG_CONFIRMED = 'confirmed'
SCHEDULE_AUTO_FLAG_REJECTED = 'rejected'
SCHEDULE_AUTO_FLAG_ALLOWED = {
    SCHEDULE_AUTO_FLAG_PENDING,
    SCHEDULE_AUTO_FLAG_CONFIRMED,
    SCHEDULE_AUTO_FLAG_REJECTED
}
# 'исход', 'соединение' и 'автодозвон' — статусы iCORE Phone у групп ОП (Основа/ЯР/Поток,
# oktell_guard/phone.py): «Исход» у них отдельный ключ от прежнего 'перезвон', «Соединение» —
# набор до ответа, «Автодозвон» — работа через систему автодозвона. Это рабочее время, и
# кладём его в ОБЩИЙ набор, а не в отдельный профиль op_*: таймлайн берёт модель по текущему
# направлению, а часы — по группе на дату, и свой профиль развёл бы флаги и часы. Oktell и
# Binotel этих слов не шлют. 'офлайн' (простой без звонков, «выброс») — намеренно НИ в одном
# наборе: по ТЗ это не отработанное время.
SCHEDULE_AUTO_WORK_STATUS_KEYS = {'готов', 'занят', 'занята', 'перезвон', 'зарезервировано',
                                  'исход', 'соединение', 'автодозвон'}
SCHEDULE_AUTO_TALK_STATUS_KEYS = {'занят', 'занята'}
SCHEDULE_AUTO_BREAK_STATUS_KEYS = {'перерыв', 'авто'}
SCHEDULE_AUTO_NO_PHONE_STATUS_KEY = 'без телефона'
SCHEDULE_AUTO_TRAINING_STATUS_KEY = 'тренинг'
CHAT_MANAGER_WORK_STATUS_KEYS = {'online', 'holiday', 'онлайн', 'закрытие чатов'}
CHAT_MANAGER_BREAK_STATUS_KEYS = {'break', 'перерыв'}
CHAT_MANAGER_NON_WORK_STATUS_KEYS = {'busy', 'занят', 'занята'}
CHAT_MANAGER_TRAINING_STATUS_KEYS = {'study', 'training', 'тренинг'}
CHAT_MANAGER_LOGIN_STATUS_KEYS = {'login', 'вход', 'подключение'}
CHAT_MANAGER_LOGOUT_STATUS_KEYS = {'logout', 'выход', 'отключение'}
CHAT_MANAGER_ACTION_STATUS_KEYS = {'transfer chat', 'take chat', 'передача чата', 'взятие чата'}
# Статусы посегментной выгрузки TEZ (общие для моделей tez_line/tez_op):
# active + work in crm = рабочее время, break in work = перерыв, inactive = офлайн (игнор).
TEZ_WORK_STATUS_KEYS = {'active', 'work in crm', 'работа в crm'}
TEZ_BREAK_STATUS_KEYS = {'break in work'}
TEZ_IGNORED_STATUS_KEYS = {'inactive'}
SCHEDULE_STATUS_KEY_LABELS = {
    'готов': 'Готов',
    'занят': 'Занят',
    'занята': 'Занята',
    'перезвон': 'Перезвон',
    'перерыв': 'Перерыв',
    'авто': 'Авто',
    'вышел': 'Вышел',
    'тренинг': 'Тренинг',
    'training': 'Training',
    'без телефона': 'Без телефона',
    'нет статуса': 'Нет статуса',
    'отключен': 'Отключен',
    'отключена': 'Отключена',
    'отключено': 'Отключено',
    'online': 'Online',
    'holiday': 'Закрытие чатов',
    'break': 'Обеденный перерыв',
    'busy': 'Busy',
    'study': 'Тренинг',
    'login': 'Вход в систему',
    'logout': 'Выход из системы',
    'transfer chat': 'Передача чата',
    'take chat': 'Взятие чата',
    'active': 'Активен',
    'work in crm': 'Работа в CRM',
    'работа в crm': 'Работа в CRM',
    'break in work': 'Перерыв',
    'inactive': 'Неактивен',
    # Статусы iCORE Phone групп ОП — подписи как в пилюле телефона. «Офлайн» здесь —
    # неоплачиваемый простой, не «Офлайн активность» (оплачиваемая работа вне телефона).
    'исход': 'Исход',
    'соединение': 'Соединение',
    'автодозвон': 'Автодозвон',
    'офлайн': 'Офлайн'
}
SCHEDULE_AUTO_FINE_RATE_PER_MINUTE = float(os.getenv('SCHEDULE_AUTO_FINE_RATE_PER_MINUTE', '50'))

TECHNICAL_ISSUE_REASONS: List[str] = [
    'Не работает интернет',
    'Замена мыши',
    'Не работает микрофон',
    'Не работает Oktell',
    'Проблема с маршрутизацией Oktell (не идут исходящие звонки), переключение в ручной режим',
    'Замена клавиатуры',
    'Не заходит в корпоративный чат',
    'Не включается компьютер',
    'Переполнена память',
    'Кнопка "Войти в колл-центр" в Oktell не реагирует на действия',
    'Виснет компьютер',
    'Не работают программы на ПК (ошибка "Меню \"Пуск\" не работает")',
    'Проблема с подключением к сайту Oktell',
    'Не работал рабочий сайт',
    'Не может войти в учетную запись ПК',
    'Не поступают звонки',
    'Не может войти в учетную запись Oktell',
    'Отключение света',
    'Массовая проблема с Октелл',
    'Массовая проблема с интернетом',
    'Массовая проблема с телефонией'
]
TECHNICAL_ISSUE_REASONS_SET = set(TECHNICAL_ISSUE_REASONS)

# Причины, для которых одного пункта из списка мало: без подробностей инцидент
# потом не разобрать. У таких причин комментарий обязателен, а подсказка о том,
# что именно писать, живёт здесь — форма получает её из API и не дублирует текст.
TECHNICAL_ISSUE_REASON_COMMENT_RULES: Dict[str, Dict[str, str]] = {
    'Не работал рабочий сайт': {
        'hint': 'Укажите название сайта, что именно не работало и какая ошибка отображалась.',
        'example': 'Не открывался сайт CRM, при входе отображалась ошибка 502',
    },
}


def technical_issue_comment_rule(reason) -> Optional[Dict[str, str]]:
    """Правило обязательного комментария для причины (или None, если его нет)."""
    return TECHNICAL_ISSUE_REASON_COMMENT_RULES.get(str(reason or '').strip())


def technical_issue_comment_rules_payload() -> List[Dict[str, str]]:
    """Справочник правил для фронта: причина + подсказка + пример."""
    return [
        {
            'reason': reason,
            'hint': str(rule.get('hint') or ''),
            'example': str(rule.get('example') or ''),
        }
        for reason, rule in TECHNICAL_ISSUE_REASON_COMMENT_RULES.items()
    ]


def technical_issue_comment_required_error(reason) -> str:
    rule = technical_issue_comment_rule(reason) or {}
    hint = str(rule.get('hint') or '').strip()
    reason_text = str(reason or '').strip()
    message = f'Комментарий обязателен для причины «{reason_text}»'
    return f'{message}. {hint}' if hint else f'{message}.'

# Верхняя граница номера РМ. Раньше было 30 (раскладка тех-поддержки 1–30);
# расширено до 200, т.к. у отдела продаж РМ нумеруются до 97 (3 кабинета, 28–97).
TECHNICAL_ISSUE_WORKPLACE_MAX = 200

# ─── IT-ticket catalog ─────────────────────────────────────────────────────────
# Каталог тематик заявок в IT-отдел. Источник — согласованные документы
# «Типы заявок в IT отдел от ОП» и «Типы заявок в IT отдел СЗоВ».
# Профиль выбирается автоматически по отделу супервайзера (см. resolve_it_ticket_profile).

IT_TICKET_CATALOG: Dict[str, Dict[str, Any]] = {
    "op": {
        "label": "Отдел продаж (ОП)",
        "categories": [
            {
                "name": "Рабочие места сотрудников",
                "items": [
                    "Медленная работа компьютера",
                    "Замена или подключение оборудования (монитор, гарнитура, клавиатура, мышь)",
                    "Настройка нового рабочего места",
                    "Недостаточно свободной памяти на ПК",
                    "Проблемы со входом в Windows",
                    "Блокировка учётной записи Windows",
                ],
            },
            {
                "name": "Телефония",
                "items": [
                    "Массовый сбой телефонии",
                    "Ошибка авторизации",
                    "Проблемы со звуком (не слышно клиента / оператора)",
                    "Плохое качество связи (эхо, шумы, прерывания)",
                    "Не меняются статусы оператора",
                    "Не работает перевод звонков",
                    "Не записываются разговоры",
                    "Не отображается карточка клиента",
                    "Неактивен внутренний номер",
                ],
            },
            {
                "name": "Сетевые и интернет-проблемы",
                "items": [
                    "Отсутствует доступ в интернет",
                    "Нестабильное интернет-соединение",
                    "Проблемы доступа с сетевыми папками и ресурсами",
                    "Не работает принтер",
                ],
            },
            {
                "name": "Корпоративные системы и доступы",
                "items": [
                    "Ускорить процесс создания учётных записей новым сотрудникам",
                    "Удаление учётных записей уволенных сотрудников",
                    "Предоставление доступа к корпоративному чату",
                    "Предоставление доступа к Wiki",
                    "Настройка сайтов у операторов и СВ",
                ],
            },
            {
                "name": "Платрум",
                "items": [
                    "Создание учётной записи сотрудника",
                    "Удаление учётной записи сотрудника",
                ],
            },
            {
                "name": "Почта и коммуникации",
                "items": [
                    "Создание корпоративной почты",
                    "Сброс пароля электронной почты",
                    "Настройка почты на устройстве",
                ],
            },
            {
                "name": "Программное обеспечение",
                "items": [
                    "Установка программного обеспечения",
                    "Обновление программного обеспечения",
                    "Удаление программного обеспечения",
                    "Ошибки в работе программ",
                ],
            },
        ],
    },
    "szov": {
        "label": "СЗоВ",
        "categories": [
            {
                "name": "Рабочие места сотрудников",
                "items": [
                    "Проблемы со входом в Windows",
                    "Блокировка учётной записи Windows",
                    "Недостаточно свободной памяти на ПК",
                    "Настройка нового рабочего места",
                    "Замена или подключение оборудования (мышь, клавиатура, монитор, гарнитура)",
                ],
            },
            {
                "name": "Телефония и Oktell",
                "items": [
                    "Не поступают звонки в Oktell",
                    "Ошибка авторизации в Oktell",
                    "Не работает веб-телефон Oktell",
                    "Не работают наушники/гарнитура",
                    "Проблемы со статусами операторов",
                    "Ошибки маршрутизации звонков",
                ],
            },
            {
                "name": "Сетевые и интернет-проблемы",
                "items": [
                    "Отсутствует доступ в интернет",
                    "Нестабильное интернет-соединение",
                    "Проблемы с сетевыми папками и ресурсами",
                ],
            },
            {
                "name": "Корпоративные системы и доступы",
                "items": [
                    "Создание учётных записей новым сотрудникам",
                    "Удаление учётных записей уволенных сотрудников",
                    "Предоставление доступа к корпоративному чату",
                    "Предоставление доступа к Wiki",
                ],
            },
            {
                "name": "Платрум",
                "items": [
                    "Создание учётной записи сотрудника",
                    "Удаление учётной записи сотрудника",
                ],
            },
            {
                "name": "Принтеры и периферия",
                "items": [
                    "Проблемы с принтером",
                    "Настройка принтера",
                ],
            },
            {
                "name": "Face ID и СКУД",
                "items": [
                    "Подключение доступа к Face ID",
                    "Удаление доступа",
                    "Обновление данных сотрудника в СКУД",
                    "Проблемы с проходом через систему контроля доступа",
                ],
            },
            {
                "name": "Почта и коммуникации",
                "items": [
                    "Создание корпоративной почты",
                    "Сброс пароля электронной почты",
                    "Настройка почты на устройстве",
                ],
            },
            {
                "name": "Программное обеспечение",
                "items": [
                    "Установка программного обеспечения",
                    "Обновление программного обеспечения",
                    "Удаление программного обеспечения",
                    "Ошибки в работе программ",
                ],
            },
        ],
    },
}

IT_TICKET_DEFAULT_PROFILE = "op"


def resolve_it_ticket_profile(department_code: object = None, department_name: object = None) -> str:
    """Определяет профиль каталога (op|szov) по отделу супервайзера.

    Эвристика по коду/названию отдела. Если ничего не совпало — профиль по умолчанию.
    Фронтенд может переопределить профиль вручную (актуально для админов без отдела).
    """
    code = str(department_code or "").strip().lower()
    name = str(department_name or "").strip().lower()
    haystack = f"{code} {name}"
    szov_markers = ("szov", "сзов", "сзв", "забот", "care", "tez")
    if any(marker in haystack for marker in szov_markers):
        return "szov"
    op_markers = ("op", "sales", "прод", "оп ", "колл", "call")
    if code == "op" or any(marker in haystack for marker in op_markers):
        return "op"
    return IT_TICKET_DEFAULT_PROFILE


def resolve_it_ticket_profile_strict(department_code: object = None, department_name: object = None) -> "Optional[str]":
    """Строгое сопоставление отдел→профиль ДЛЯ ПРОВЕРКИ ПРАВ (без fallback на 'op').

    Возвращает 'op'/'szov' только при однозначном совпадении, иначе None — чтобы
    глава непрофильного отдела (или отдела, который не удалось сопоставить) НЕ получал
    прав на редактирование/закрепление чужого профиля. Не использовать для UI-дефолта —
    для него есть нестрогий resolve_it_ticket_profile.
    """
    code = str(department_code or "").strip().lower()
    name = str(department_name or "").strip().lower()
    if code == "op":
        return "op"
    if code == "szov":
        return "szov"
    haystack = f"{code} {name}"
    if any(marker in haystack for marker in ("сзов", "szov", "сзв", "забот")):
        return "szov"
    if "продаж" in name:
        return "op"
    return None


def get_pool():
    global POOL, POOL_SEMAPHORE
    if POOL is None:
        with POOL_LOCK:
            if POOL is None:
                conn_params = {
                    'dbname': os.getenv('POSTGRES_DB'),
                    'user': os.getenv('POSTGRES_USER'),
                    'password': os.getenv('POSTGRES_PASSWORD'),
                    'host': os.getenv('POSTGRES_HOST'),
                    'port': os.getenv('POSTGRES_PORT', 5432)
                }
                POOL = ThreadedConnectionPool(MIN_CONN, MAX_CONN, **conn_params)
                POOL_SEMAPHORE = threading.BoundedSemaphore(MAX_CONN)
                logging.info(
                    "PostgreSQL pool initialized with min=%s max=%s acquire_timeout=%ss",
                    MIN_CONN,
                    MAX_CONN,
                    POOL_ACQUIRE_TIMEOUT_SECONDS
                )
    return POOL


# ── Привязка дня к группе оператора ───────────────────────────────────────────
# «Учёт часов» фильтрует дни по daily_hours.group_id, а «Мои часы» показывают все
# дни оператора (агрегат work_hours не привязан к группе). Пока членство в группе
# заводят задним числом — а его почти всегда заводят позже, чем человек реально
# вышел в группу, — дни ДО start_date не покрыты ни одним членством, получают
# group_id = NULL и выпадают из группового отчёта. Итог: у оператора в «Моих
# часах» больше часов, чем СВ видит в «Учёте часов».
#
# Поэтому день, не покрытый ни одним членством, достаётся БЛИЖАЙШЕМУ по времени
# членству — но только из тех, что пересекают ТОТ ЖЕ МЕСЯЦ. Месяц здесь единица
# отчётности: так день не уедет в группу, к которой оператор в этом месяце вообще
# не имел отношения, и закрытые месяцы (снимки) не переписываются задним числом.
# Оператор совсем без членства в этом месяце остаётся с NULL — это уже другой
# случай (человека не завели в группу), и лечится он заведением членства.
#
# {m} — алиас group_operator_memberships, {day} — выражение даты дня.
MEMBERSHIP_MONTH_OVERLAP_SQL = (
    "{m}.start_date <= (date_trunc('month', {day}) + INTERVAL '1 month - 1 day')::date"
    " AND ({m}.end_date IS NULL OR {m}.end_date >= date_trunc('month', {day})::date)"
)
# 0 — членство покрывает день; иначе расстояние в днях до ближайшей его границы.
MEMBERSHIP_DAY_DISTANCE_SQL = (
    "CASE"
    " WHEN {m}.start_date > {day} THEN {m}.start_date - {day}"
    " WHEN {m}.end_date IS NOT NULL AND {m}.end_date < {day} THEN {day} - {m}.end_date"
    " ELSE 0 END"
)


# Исключение методов низких оценок; в database.py стояло после класса, методам имя нужно
# только в момент вызова.
class LowRatingAttributionConflict(ValueError):
    """Кому засчитана низкая оценка, поменял другой проверяющий уже после того,
    как сохраняющий открыл карточку (задача #286). Решение об атрибуции одно на
    разбор, поэтому сохранение по устаревшим отметкам молча стёрло бы чужое."""


class CoreMixin:
    """Соединения и пул. Эти методы читают и перепривязывают глобалы POOL и
    POOL_SEMAPHORE этого модуля, поэтому живут рядом с ними, а не в разделе."""
    @contextmanager
    def _get_connection(self):
        pool = get_pool()
        semaphore = POOL_SEMAPHORE
        acquire_started_at = time.perf_counter()
        acquired = True
        if semaphore is not None:
            acquired = semaphore.acquire(timeout=POOL_ACQUIRE_TIMEOUT_SECONDS)
        if not acquired:
            raise TimeoutError("POSTGRES_POOL_ACQUIRE_TIMEOUT")

        waited_seconds = time.perf_counter() - acquire_started_at
        if POOL_WAIT_WARN_SECONDS and waited_seconds >= POOL_WAIT_WARN_SECONDS:
            logging.warning(
                "Waited %.2fs for PostgreSQL pool slot (max=%s)",
                waited_seconds,
                MAX_CONN
            )

        conn = None
        try:
            conn = pool.getconn()
            yield conn
        except Exception:
            raise
        finally:
            if conn is not None:
                pool.putconn(conn)
            if semaphore is not None:
                semaphore.release()

    @contextmanager
    def _get_cursor(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()

    @staticmethod
    def _reset_pool_after_transient_error():
        """Сбросить глобальный пул соединений после транзиентного обрыва БД,
        чтобы повторная инициализация построила свежие соединения."""
        global POOL, POOL_SEMAPHORE
        with POOL_LOCK:
            if POOL is not None:
                try:
                    POOL.closeall()
                except Exception:
                    pass
            POOL = None
            POOL_SEMAPHORE = None
