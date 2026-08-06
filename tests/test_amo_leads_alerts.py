# -*- coding: utf-8 -*-
"""Алерты по лидам — методика из «анализ_лидов_алерты.xlsx».

CPL и правило «расход без лидов» сюда не перенесены осознанно: расходов в
amoCRM нет, и владелец решил, что считать их не нужно. Проверяем то, что
осталось: дельты, отсечку малой выборки, пороги 6ч/12ч и итоговый вердикт.
Эталонные дельты взяты с листа «Пример 05.08 vs 29.07».
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_leads

CURRENT = {"Google": 107, "YouTube": 41, "SEO": 39, "TikTok": 385, "FB": 263,
           "OLX": 33, "Яндекс": 96, "2GIS": 25, "Звонки": 251}
BASE = {"Google": 83, "YouTube": 37, "SEO": 24, "TikTok": 189, "FB": 160,
        "OLX": 38, "Яндекс": 54, "2GIS": 23, "Звонки": 226}


def _rows(current=None, base=None, period=amo_leads.PERIOD_12H):
    current = current or {**CURRENT, "Общее": sum(CURRENT.values())}
    base = base or {**BASE, "Общее": sum(BASE.values())}
    return {r["source"]: r for r in amo_leads.analyze(current, base, period=period)}


def test_example_day_has_no_lead_alerts():
    """На 05.08 против 29.07 по лидам всё в норме — как в примере."""
    rows = _rows()
    for source in amo_leads.SOURCE_ORDER + ["Общее"]:
        assert rows[source]["leads_status"] == "Норма", source
        assert rows[source]["verdict"] == "Норма", source


def test_deltas_match_the_spec():
    rows = _rows()
    assert round(rows["Google"]["delta_leads"], 6) == round(0.289156626506024, 6)
    assert round(rows["Общее"]["delta_leads"], 6) == round(0.486810551558753, 6)


def test_olx_drop_stays_normal_just_above_the_threshold():
    """-13% ещё не «Внимание»: порог -15%."""
    assert _rows()["OLX"]["leads_status"] == "Норма"


def test_twelve_hour_thresholds():
    assert _rows({"FB": 84, "Общее": 84}, {"FB": 100, "Общее": 100})["FB"]["leads_status"] \
        == "Внимание: лиды ниже нормы"
    assert _rows({"FB": 55, "Общее": 55}, {"FB": 100, "Общее": 100})["FB"]["leads_status"] \
        == "Критично: лиды упали"
    assert _rows({"FB": 86, "Общее": 86}, {"FB": 100, "Общее": 100})["FB"]["leads_status"] \
        == "Норма"


def test_six_hour_check_uses_only_the_hard_threshold():
    """На 6ч реагируем только на явный провал."""
    six = amo_leads.PERIOD_6H
    assert _rows({"FB": 60, "Общее": 60}, {"FB": 100, "Общее": 100}, six)["FB"]["leads_status"] \
        == "Норма"
    assert _rows({"FB": 30, "Общее": 30}, {"FB": 100, "Общее": 100}, six)["FB"]["leads_status"] \
        == "Критично: лиды упали"


def test_small_sample_is_not_judged_by_percent():
    rows = _rows({"2GIS": 1, "Общее": 1}, {"2GIS": 4, "Общее": 4})
    assert rows["2GIS"]["leads_status"] == "Недостаточно данных"
    assert rows["2GIS"]["verdict"] == "Мало данных"


def test_zero_base_is_not_a_division_error():
    rows = _rows({"SEO": 5, "Общее": 5}, {"SEO": 0, "Общее": 0})
    assert rows["SEO"]["delta_leads"] is None
    assert rows["SEO"]["leads_status"] == "Недостаточно данных"


def test_verdicts_map_to_advice():
    critical = _rows({"FB": 10, "Общее": 10}, {"FB": 100, "Общее": 100})["FB"]
    assert critical["verdict"] == "АЛЕРТ"
    assert "паузу расхода" in critical["advice"]
    warning = _rows({"FB": 80, "Общее": 80}, {"FB": 100, "Общее": 100})["FB"]
    assert warning["verdict"] == "Проверить"
    assert "следующем чекпоинте" in warning["advice"]


def test_report_lists_only_what_needs_attention():
    rows = amo_leads.analyze({"FB": 55, "Общее": 55}, {"FB": 100, "Общее": 100})
    text = amo_leads.render_alert_report(rows, window_label="06.08, 00:00–12:00")
    assert "FB" in text
    assert "Критично" in text
    assert "06.08, 00:00–12:00" in text
    # CPL нигде не упоминается — его сознательно не считаем.
    assert "CPL" not in text


def test_quiet_report_says_so():
    rows = amo_leads.analyze({**CURRENT, "Общее": sum(CURRENT.values())},
                             {**BASE, "Общее": sum(BASE.values())})
    text = amo_leads.render_alert_report(rows, window_label="05.08 (сутки)")
    assert "Отклонений нет" in text


def test_windows_compare_the_same_slice_a_week_apart():
    import datetime

    noon = datetime.datetime(2026, 8, 6, 12, 0, tzinfo=amo_leads.TZ)
    w = amo_leads.alert_windows(noon)
    assert w["current_start"] == datetime.datetime(2026, 8, 6, 0, 0, tzinfo=amo_leads.TZ)
    assert w["current_end"] == noon
    assert w["base_start"] == datetime.datetime(2026, 7, 30, 0, 0, tzinfo=amo_leads.TZ)
    assert w["base_end"] == datetime.datetime(2026, 7, 30, 12, 0, tzinfo=amo_leads.TZ)


def test_midnight_window_is_the_whole_previous_day():
    import datetime

    midnight = datetime.datetime(2026, 8, 6, 0, 0, tzinfo=amo_leads.TZ)
    w = amo_leads.alert_windows(midnight)
    assert w["current_start"] == datetime.datetime(2026, 8, 5, 0, 0, tzinfo=amo_leads.TZ)
    assert w["current_end"] == midnight
    assert w["base_start"] == datetime.datetime(2026, 7, 29, 0, 0, tzinfo=amo_leads.TZ)
    assert "05.08" in w["window_label"]


def test_base_label_shows_that_it_is_the_same_half_of_the_day():
    """Получатель должен видеть, что сравнили с половиной дня, а не с сутками."""
    import datetime

    noon = amo_leads.alert_windows(
        datetime.datetime(2026, 8, 6, 12, 0, tzinfo=amo_leads.TZ))
    assert noon["base_label"] == "30.07, 00:00–12:00 (неделю назад)"

    midnight = amo_leads.alert_windows(
        datetime.datetime(2026, 8, 6, 0, 0, tzinfo=amo_leads.TZ))
    assert midnight["base_label"] == "29.07 (сутки, неделю назад)"


def test_daytime_base_never_spans_a_whole_day():
    """Дневная база всегда короче суток — иначе сравнивали бы несравнимое."""
    import datetime

    for hour in (6, 12, 18, 23):
        w = amo_leads.alert_windows(
            datetime.datetime(2026, 8, 6, hour, 0, tzinfo=amo_leads.TZ))
        assert (w["base_end"] - w["base_start"]) == (w["current_end"] - w["current_start"])
        assert (w["base_end"] - w["base_start"]) < datetime.timedelta(days=1)
        assert w["base_end"] - w["current_end"] == -datetime.timedelta(days=7)
