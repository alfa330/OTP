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

# Второй источник отметок — Clockster: в нём отмечается центральный офис, которого в
# Workpace нет вовсе (задача #273). ТОЛЬКО ЧТЕНИЕ: интеграция статусов операторов
# через Clockster была убрана 01.08.2026 после «висящих приходов», и возвращать её
# нельзя — ни в operator_status_events, ни в сегменты, ни в учёт часов отсюда не
# пишется ничего.
CLOCKSTER_BASE_URL = (os.getenv("CLOCKSTER_API_URL")
                      or "https://api.clockster.com/company/v2").rstrip("/")
CLOCKSTER_API_TOKEN = (os.getenv("CLOCKSTER_API_TOKEN") or "").strip()


def is_clockster_configured() -> bool:
    """Без токена второй источник просто не подключается — раздел работает на Workpace."""
    return bool(CLOCKSTER_API_TOKEN)

# Опоздание/ранний уход считаем нарушением от этого числа минут.
LATE_THRESHOLD_MINUTES = _env_int("LATE_THRESHOLD_MINUTES", 1)
# «Отсутствует на месте» — столько минут после начала смены без отметки о приходе.
# 15 минут, а не 10, по задаче #273 (кадровый учёт): столько компания даёт на дорогу
# до терминала, и с этой же минуты не отметившийся считается опоздавшим. Значение
# специально держим в одном месте — его читают и опрос, и фиксация опоздания.
MISSING_IN_AFTER_MINUTES = _env_int("GROUP_LATE_MISSING_IN_MINUTES", 15)
# «Нет отметки об уходе» и «поздний уход» — столько минут после конца смены.
MISSING_OUT_AFTER_MINUTES = _env_int("GROUP_LATE_MISSING_OUT_MINUTES", 60)

# Обед, который вычитается из времени в работе (задача #273: «с прихода до ухода за
# минусом 1ч обеденного перерыва»). Час — не выдумка: по правилам перерывов проекта
# (work_schedule_break_rules) стандартная восьмичасовая смена даёт ровно 60 минут.
LUNCH_BREAK_MINUTES = _env_int("GROUP_LATE_LUNCH_BREAK_MINUTES", 60)
# Короче этой смены обед не вычитаем: ни у одного направления в правилах перерывов
# нет перерыва для смены короче 300 минут. Иначе у человека, отработавшего два часа,
# «время в работе» ушло бы в минус или в час с лишним неправды.
LUNCH_BREAK_MIN_WORK_MINUTES = _env_int("GROUP_LATE_LUNCH_MIN_WORK_MINUTES", 300)

# Глубина хранения истории: журнал опросов пишется каждые 2 минуты и без чистки
# растёт быстрее всего остального.
RETENTION_EVENTS_DAYS = _env_int("GROUP_LATE_RETENTION_EVENTS_DAYS", 180)
RETENTION_REPORT_FILES_DAYS = _env_int("GROUP_LATE_RETENTION_REPORT_FILES_DAYS", 60)
RETENTION_POLL_RUNS_DAYS = _env_int("GROUP_LATE_RETENTION_POLL_RUNS_DAYS", 7)

# Период отчёта ограничен, чтобы одна выгрузка не тянула из Workpace полгода.
MAX_REPORT_DAYS = _env_int("GROUP_LATE_MAX_REPORT_DAYS", 31)

# Отделы, у которых ПЛАН берётся из графика iCore, а не из расписания Workpace:
# {название отдела в Workpace: код нашего отдела}. Факт (отметки терминала) в
# любом случае остаётся за Workpace — своего источника прихода/ухода у нас нет.
#
# Отдел обязан быть в паре GROUP_LATE_BOT_DEPARTMENT_SCOPES (bot_schedule2.py):
# без объявленной пары сотрудника Workpace не с кем сопоставить у себя, и матчить
# по всей базе нельзя — тёзка из соседнего отдела подставил бы чужого человека.
# Сверку держит tests/test_group_late_icore_plan.py.
ICORE_PLAN_DEPARTMENTS = {'Регионы': 'front_office'}


def is_configured() -> bool:
    """Без логина и пароля Workpace опрос и отчёты работать не могут."""
    return bool(WORKPACE_LOGIN and WORKPACE_PASSWORD)
