# -*- coding: utf-8 -*-
"""Выборка паритета экрана и помощника не протухла.

tests/data/classifier_parity.json — ожидания питона (wiki/classifier.requirement и
verdict) на настоящем справочнике; по ним node-тест (tests/classifier_data.test.mjs)
сверяет расчёт экрана. Поменяли правило в питоне или пересобрали справочник
(scripts/classifier_refresh.py) — этот тест краснеет, пока выборку не пересчитают:

    PYTHONPATH=. venv/bin/python tests/gen_classifier_parity.py

Иначе node-тест молча сверял бы JS со вчерашним питоном.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / 'tests')):
    if path not in sys.path:
        sys.path.insert(0, path)

import gen_classifier_parity  # noqa: E402
from wiki import classifier  # noqa: E402


class ParityFixtureTest(unittest.TestCase):
    def test_fixture_matches_python_and_the_data_file(self):
        stored = json.loads(Path(gen_classifier_parity.PATH).read_text(encoding='utf-8'))['cases']
        fresh = gen_classifier_parity.build_cases(classifier.build_book(classifier.read_data()))
        self.assertEqual(len(stored), len(fresh))
        self.assertEqual(stored, json.loads(json.dumps(fresh, ensure_ascii=False)))

    def test_every_kind_and_verdict_is_covered(self):
        cases = json.loads(Path(gen_classifier_parity.PATH).read_text(encoding='utf-8'))['cases']
        kinds = {case['kind'] for case in cases}
        verdicts = {value for case in cases for value in case['verdicts'].values()}
        self.assertEqual(kinds, {'row', 'brand', 'other', 'absent', 'any'})
        self.assertTrue({'fits', 'old', 'no', 'except'} <= verdicts)
        self.assertEqual({case['city'] for case in cases},
                         {city['id'] for city in classifier.read_data()['cities']})


if __name__ == '__main__':
    unittest.main()
