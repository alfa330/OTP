"""Настройки контроля опозданий. Из окружения нужны только доступы к Workpace."""

import os
from zoneinfo import ZoneInfo


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


TZ = ZoneInfo(os.getenv("GROUP_LATE_TIMEZONE", "Asia/Almaty"))

WORKPACE_BASE_URL = (os.getenv("WORKPACE_BASE_URL") or "https://api.workpace.kz").rstrip("/")
WORKPACE_LOGIN = (os.getenv("WORKPACE_LOGIN") or "").strip()
WORKPACE_PASSWORD = (os.getenv("WORKPACE_PASSWORD") or "").strip()

# Опоздание/ранний уход считаем нарушением от этого числа минут.
LATE_THRESHOLD_MINUTES = _env_int("LATE_THRESHOLD_MINUTES", 1)
# «Отсутствует на месте» — столько минут после начала смены без отметки о приходе.
MISSING_IN_AFTER_MINUTES = _env_int("GROUP_LATE_MISSING_IN_MINUTES", 10)
# «Нет отметки об уходе» и «поздний уход» — столько минут после конца смены.
MISSING_OUT_AFTER_MINUTES = _env_int("GROUP_LATE_MISSING_OUT_MINUTES", 60)

# Глубина хранения истории: журнал опросов пишется каждые 2 минуты и без чистки
# растёт быстрее всего остального.
RETENTION_EVENTS_DAYS = _env_int("GROUP_LATE_RETENTION_EVENTS_DAYS", 180)
RETENTION_REPORT_FILES_DAYS = _env_int("GROUP_LATE_RETENTION_REPORT_FILES_DAYS", 60)
RETENTION_POLL_RUNS_DAYS = _env_int("GROUP_LATE_RETENTION_POLL_RUNS_DAYS", 7)

# Период отчёта ограничен, чтобы одна выгрузка не тянула из Workpace полгода.
MAX_REPORT_DAYS = _env_int("GROUP_LATE_MAX_REPORT_DAYS", 31)


def is_configured() -> bool:
    """Без логина и пароля Workpace опрос и отчёты работать не могут."""
    return bool(WORKPACE_LOGIN and WORKPACE_PASSWORD)
