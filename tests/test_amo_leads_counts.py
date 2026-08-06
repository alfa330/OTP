# -*- coding: utf-8 -*-
"""Подсчёт лидов должен совпадать со «Сводкой по Дням» Google-таблицы.

Фикстура — снимок вкладки «Импорт ДЕНЬ» за 05.08.2026 (1654 сделки). Эталон —
цифры, которые в тот день показывала сама таблица. Тест держит перенос честным:
если правило поедет, цифры бота разойдутся с тем, что видят маркетологи.
"""

import csv
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_leads

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures_amo_sheet_2026_08_05.csv")

# Ровно то, что стояло в «Сводке по Дням» на 05.08.2026.
SHEET_NUMBERS = {
    "Google": 107,
    "YouTube": 41,
    "SEO": 39,
    "TikTok": 385,
    "FB": 263,
    "OLX": 33,
    "Яндекс": 96,
    "2GIS": 25,
    "Звонки": 251,
    "Общее": 1240,
}


@pytest.fixture(scope="module")
def sheet_rows():
    with io.open(FIXTURE, encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_fixture_is_the_whole_day(sheet_rows):
    assert len(sheet_rows) == 1654


@pytest.mark.parametrize("source", sorted(SHEET_NUMBERS))
def test_source_matches_sheet(sheet_rows, source):
    counts = amo_leads.count_by_source(sheet_rows)
    assert counts[source] == SHEET_NUMBERS[source]


def test_total_is_sum_of_sources(sheet_rows):
    counts = amo_leads.count_by_source(sheet_rows)
    assert counts["Общее"] == sum(counts[name] for name in amo_leads.SOURCE_ORDER)


def test_unattributed_deals_are_visible(sheet_rows):
    """В таблице потеря не видна — в отбивке она должна быть посчитана явно."""
    summary = amo_leads.summarize(sheet_rows)
    assert summary["total_deals"] == 1654
    assert summary["total_leads"] == 1240
    assert summary["unattributed"] == 414


def test_report_renders_every_source(sheet_rows):
    import datetime

    summary = amo_leads.summarize(sheet_rows)
    text = amo_leads.render_report(datetime.date(2026, 8, 5), summary)
    assert "05.08.2026" in text
    for source in amo_leads.SOURCE_ORDER:
        assert source in text
    assert "1240" in text


def test_2gis_mixed_tag_is_double_counted_like_the_sheet():
    """Тег с обеими формами написания даёт 2 — так считает исходная таблица.

    Это не описка, а зафиксированное поведение «Сводки по Дням»: 2GIS там
    складывается из двух счётчиков. Чинить нельзя — цифры бота должны совпадать
    с таблицей, а не расходиться с ней «в лучшую сторону». На реальных данных
    таких тегов нет, поэтому расхождения не возникает.
    """
    rows = [{"tags": "call_itaxi_2gis_2ГИС_wb", "utm_source": ""}]
    assert amo_leads.count_by_source(rows)["2GIS"] == 2


def test_2gis_still_catches_both_spellings():
    rows = [
        {"tags": "call_itaxi_2gis_wb", "utm_source": ""},
        {"tags": "WZ (2ГИС - не писать первыми )", "utm_source": ""},
    ]
    assert amo_leads.count_by_source(rows)["2GIS"] == 2


def test_sync_error_is_html_escaped():
    """Ошибка amoCRM попадает в сообщение с parse_mode=HTML."""
    import datetime

    summary = amo_leads.summarize([])
    text = amo_leads.render_report(
        datetime.date(2026, 8, 5), summary,
        sync_error='401 <b>Unauthorized</b> & "token" expired')
    assert "<b>Unauthorized</b>" not in text
    assert "&lt;b&gt;Unauthorized&lt;/b&gt;" in text
    assert "&amp;" in text


def test_arenda_and_departament_are_excluded_everywhere():
    """Общий фильтр формул — по колонке тегов, а не по utm."""
    rows = [
        {"tags": "forma_itaxi_arenda", "utm_source": "fb"},
        {"tags": "departament_1", "utm_source": "google_ads"},
    ]
    counts = amo_leads.count_by_source(rows)
    assert counts["FB"] == 0
    assert counts["Google"] == 0
    assert counts["Общее"] == 0
