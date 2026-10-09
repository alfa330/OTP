"""Общие мелочи SQL-слоя раздела: время, приведение значений к JSON, разбор строки.

Вынесено из queries.py, когда запросы разошлись по модулям (заявки, справочники,
имущество, уведомления): всем им нужны одни и те же три вещи, а импортировать
их друг у друга значило бы связать модули в кольцо.

* Время в базе — настенные часы Алматы (`NOW_SQL`), наружу — isoformat без зоны
  (`plain()`), иначе jsonify припишет «GMT» и браузер уведёт даты на +5 часов.
* Деньги — Decimal в базе, float наружу: фронту нужны числа, а тенге в float
  до сотен миллиардов представляются точно с копейками.
* SELECT и разбор строки собираются из ОДНОГО списка полей (`row_map`) —
  позиционная раскладка на сорока колонках ждёт своего часа, это уже стоило
  500-х «Посылкам».
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

NOW_SQL = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


def plain(value):
    """Рекурсивно приводит значение к тому, что jsonify отдаст без сюрпризов."""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def like_pattern(text):
    """Подстрока для ILIKE. Знаки «%» и «_» из запроса ищутся буквально: иначе
    «%%%» в поиске совпадало бы со всем подряд."""
    escaped = str(text or '').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + escaped + '%'


def row_map(fields, row):
    return dict(zip(fields, row)) if row else None


def columns(alias, fields):
    """«r.id, r.name» — список колонок с псевдонимом таблицы."""
    return ', '.join('%s.%s' % (alias, field) for field in fields)
