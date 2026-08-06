# -*- coding: utf-8 -*-
"""Алерты должны повторять лист «Пример 05.08 vs 29.07» из анализ_лидов_алерты.xlsx.

Эталон — статусы, которые посчитал сам Excel в присланном файле. Тест держит
методику в узде: пороги и ветвления менять можно только осознанно.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_leads

# Колонки C (лиды текущие), E (лиды база), B (расходы), F (CPL базы) из примера.
CURRENT = {"Google": 107, "YouTube": 41, "SEO": 39, "TikTok": 385, "FB": 263,
           "OLX": 33, "Яндекс": 96, "2GIS": 25, "Звонки": 251}
BASE = {"Google": 83, "YouTube": 37, "SEO": 24, "TikTok": 189, "FB": 160,
        "OLX": 38, "Яндекс": 54, "2GIS": 23, "Звонки": 226}
SPEND = {"Google": 176014, "YouTube": 99058, "SEO": 0, "TikTok": 262185, "FB": 257255,
         "OLX": 39000, "Яндекс": 203300, "2GIS": 56000, "Звонки": 0}
BASE_CPL = {"Google": 1619, "YouTube": 1714, "SEO": 0, "TikTok": 1319, "FB": 1844,
            "OLX": 1026, "Яндекс": 1591, "2GIS": 2435, "Звонки": 0}
BASE_SPEND = {k: BASE_CPL[k] * BASE[k] for k in BASE}

# Ожидаемые «Статус: CPL» и «Итог» — то, что показал Excel.
EXPECTED = {
    "Google":  ("Норма", "Норма", "Норма"),
    "YouTube": ("Норма", "Внимание: CPL растёт", "Проверить"),
    "SEO":     ("Норма", "Не применимо (нет CPL)", "Норма"),
    "TikTok":  ("Норма", "Норма", "Норма"),
    "FB":      ("Норма", "Норма", "Норма"),
    "OLX":     ("Норма", "Норма", "Норма"),
    "Яндекс":  ("Норма", "Внимание: CPL растёт", "Проверить"),
    "2GIS":    ("Норма", "Норма", "Норма"),
    "Звонки":  ("Норма", "Не применимо (нет CPL)", "Норма"),
    "Общее":   ("Норма", "Норма", "Норма"),
}


def _rows():
    totals = {**CURRENT, "Общее": sum(CURRENT.values())}
    base_totals = {**BASE, "Общее": sum(BASE.values())}
    return {r["source"]: r for r in amo_leads.analyze(
        totals, base_totals, period=amo_leads.PERIOD_12H,
        spend=SPEND, base_spend=BASE_SPEND)}


@pytest.mark.parametrize("source", sorted(EXPECTED))
def test_statuses_match_the_spec(source):
    row = _rows()[source]
    leads_status, cpl_status, verdict = EXPECTED[source]
    assert row["leads_status"] == leads_status
    assert row["cpl_status"] == cpl_status
    assert row["verdict"] == verdict


def test_deltas_match_the_spec():
    rows = _rows()
    assert round(rows["Google"]["delta_leads"], 6) == round(0.289156626506024, 6)
    assert round(rows["Google"]["delta_cpl"], 6) == round(0.0160535232894425, 6)
    assert round(rows["Общее"]["delta_leads"], 6) == round(0.486810551558753, 6)
    assert round(rows["Общее"]["delta_cpl"], 6) == round(-0.203707559751385, 6)


def test_cpl_matches_the_spec():
    rows = _rows()
    assert round(rows["Google"]["cpl"], 4) == round(1644.99065420561, 4)
    assert round(rows["Общее"]["cpl"], 1) == 881.3


def test_small_sample_is_not_judged_by_percent():
    """База ниже 10 лидов — по процентам не судим."""
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"2GIS": 1, "Общее": 1}, {"2GIS": 4, "Общее": 4})}
    assert rows["2GIS"]["leads_status"] == "Недостаточно данных"
    assert rows["2GIS"]["verdict"] == "Мало данных"


def test_spend_without_leads_is_always_critical():
    """Расход есть, лидов ноль — «Критично» независимо от размера выборки."""
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 0, "Общее": 0}, {"FB": 3, "Общее": 3}, spend={"FB": 50000})}
    assert rows["FB"]["leads_status"] == "Критично: расход без лидов"
    assert rows["FB"]["verdict"] == "АЛЕРТ"


def test_six_hour_check_uses_only_the_hard_threshold():
    """На 6ч реагируем только на явный провал, CPL не считаем вовсе."""
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 60, "Общее": 60}, {"FB": 100, "Общее": 100}, period=amo_leads.PERIOD_6H)}
    assert rows["FB"]["leads_status"] == "Норма"      # -40% на 6ч ещё не алерт
    assert rows["FB"]["cpl_status"] == "—"
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 30, "Общее": 30}, {"FB": 100, "Общее": 100}, period=amo_leads.PERIOD_6H)}
    assert rows["FB"]["leads_status"] == "Критично: лиды упали"


def test_twelve_hour_thresholds():
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 84, "Общее": 84}, {"FB": 100, "Общее": 100})}
    assert rows["FB"]["leads_status"] == "Внимание: лиды ниже нормы"
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 55, "Общее": 55}, {"FB": 100, "Общее": 100})}
    assert rows["FB"]["leads_status"] == "Критично: лиды упали"


def test_without_spend_cpl_is_honest_about_it():
    """Без расходов CPL не выдумываем."""
    rows = {r["source"]: r for r in amo_leads.analyze(
        {"FB": 100, "Общее": 100}, {"FB": 90, "Общее": 90})}
    assert rows["FB"]["cpl_status"] == "Нет данных о расходах"
    assert rows["FB"]["cpl"] is None
    assert rows["FB"]["verdict"] == "Норма"


def test_report_renders_problems_and_spend_notice():
    rows = amo_leads.analyze({"FB": 55, "Общее": 55}, {"FB": 100, "Общее": 100})
    text = amo_leads.render_alert_report(rows, window_label="00:00–12:00")
    assert "FB" in text
    assert "Критично" in text
    assert "расходы в amoCRM не хранятся" in text
