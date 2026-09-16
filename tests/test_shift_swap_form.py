# -*- coding: utf-8 -*-
"""Форма запроса на замену в «Моих сменах»: интервал всегда принадлежит выбранной дате.

Дефект 16.09.2026: селект «Дата смены» менял только дату, а время оставалось от
прошлой даты. У оператора-чат-менеджера со сменами 15:00–00:00 и одной поздней
18:30–01:00 перенесённый интервал 17:00–18:00 в этот день уже не попадал внутрь
смены — форма считалась некорректной и запрос кандидатов не уходил вовсе: экран
«Кто заменит» оставался пустым. На боевом графике так умирали 6 дат из 16.

Проверки читают исходник текстом: решение здесь про то, КАКИМИ путями
выставляется интервал. Само правило разбора смен закреплено в
tests/swap_default_interval.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
MODULE_PATH = ROOT / 'src' / 'components' / 'schedule' / 'swapDefaultInterval.js'
MODULE = MODULE_PATH.read_text(encoding='utf-8')

HELPER = 'buildDefaultSwapIntervalForDate'


def object_literal_at(source, brace_index):
    """Скобочно-сбалансированный объектный литерал, начиная с `{`."""
    depth = 0
    for i in range(brace_index, len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[brace_index:i + 1]
    raise AssertionError('незакрытый объектный литерал')


def swap_form_patches(source):
    """Все объектные литералы, которыми правится swapForm."""
    patches = []
    for opener in ('setSwapForm(prev => ({', 'phoneSwapFieldChange({'):
        start = 0
        while True:
            found = source.find(opener, start)
            if found < 0:
                break
            brace = source.index('{', found + len(opener) - 2)
            patches.append(object_literal_at(source, brace))
            start = found + len(opener)
    return patches


class SwapIntervalFollowsDateTests(unittest.TestCase):
    def test_rule_lives_in_its_own_module_without_react(self):
        """Выбор даты есть в трёх местах — правило должно быть одно на всех."""
        self.assertIn(
            "import { defaultSwapIntervalForDate } from './components/schedule/swapDefaultInterval';",
            APP,
        )
        self.assertIn('export const defaultSwapIntervalForDate', MODULE)
        # Чистый модуль: ни React, ни сети — только карта «день → смены».
        self.assertNotIn('import ', MODULE)
        self.assertNotIn('fetch(', MODULE)

    def test_helper_reads_the_same_schedule_as_the_form(self):
        """Живая выдача −7/+90 дней, а не видимый период: форма показывает её же дни."""
        self.assertIn(
            'defaultSwapIntervalForDate((myLiveScheduleData || myScheduleData)?.shifts, dayKey)',
            APP,
        )

    def test_every_swap_form_patch_carries_the_time_with_the_date(self):
        """Ни одна правка формы не меняет дату в одиночку: иначе в форме остаётся
        интервал прошлого дня и кандидаты молча не грузятся."""
        checked = 0
        for patch in swap_form_patches(APP):
            if not re.search(r'\bswapDate\b\s*[:,]', patch):
                continue
            if re.search(r"\bswapDate:\s*''", patch):   # сброс пустой формы
                continue
            checked += 1
            carries_times = HELPER in patch or ('startTime' in patch and 'endTime' in patch)
            self.assertTrue(carries_times, patch)
        self.assertGreaterEqual(checked, 3)

    def test_both_date_selects_take_the_interval_of_that_date(self):
        """Телефон и компьютер — два разных селекта, дефект был в обоих."""
        phone = APP.index('aria-label="Дата смены"')
        phone_start = APP.rindex('<select', 0, phone)
        self.assertIn(f'...{HELPER}(e.target.value)', APP[phone_start:phone])

        desktop = APP.index('<option value="">Выберите дату смены</option>')
        desktop_start = APP.rindex('<select', 0, desktop)
        self.assertIn(f'...{HELPER}(nextDate)', APP[desktop_start:desktop])

    def test_prefill_moves_the_interval_when_it_moves_the_date(self):
        """Дату уводит и само предзаполнение — когда её не стало в графике."""
        effect = APP[APP.index('const firstDate = mySwapSourceShiftDays[0];'):]
        effect = effect[:effect.index('const swapTimeValidation')]
        self.assertIn('if (swapDate !== prev.swapDate || !parsedRange.isValid) {', effect)
        self.assertIn(f'const fallback = {HELPER}(swapDate);', effect)


if __name__ == '__main__':
    unittest.main()
