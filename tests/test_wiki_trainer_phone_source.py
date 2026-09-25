# -*- coding: utf-8 -*-
"""Источник попытки 'phone' — тренажёр открыт из iCORE Phone (вкладка «Тренажёры»).

Что здесь важно проверить и почему.

1. СПИСОК И CHECK ОБЯЗАНЫ СОВПАДАТЬ. start_run подменяет неизвестный источник
   на 'article' — молча. Добавь 'phone' только в один из двух списков, и
   попытки из телефона либо запишутся как «из статьи» (список без 'phone'),
   либо упадут на CHECK в базе (CHECK без 'phone'). Тест держит оба вместе.

2. CHECK ЛЕЖИТ В CREATE TABLE, то есть на проде он со старым списком. Без
   миграции ошибка проявилась бы только на проде и только у телефона. Тест
   фиксирует, что пересборка ограничения есть и что она условная (не гоняет
   ALTER TABLE на каждом старте).
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki import schema as wiki_schema  # noqa: E402
from wiki import trainers as wiki_trainers  # noqa: E402


def _source_check_values(sql):
    """Значения из CHECK (source IN (...)) в тексте SQL."""
    match = re.search(r"source IN \(([^)]*)\)", sql)
    if not match:
        return None
    return sorted(v.strip().strip("'") for v in match.group(1).split(','))


class PhoneSourceTest(unittest.TestCase):
    def test_phone_is_a_known_source(self):
        self.assertIn('phone', wiki_trainers.SOURCES)

    def test_start_run_keeps_phone(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (7,)
        run_id = wiki_trainers.start_run(
            cursor, trainer_key='taxi-pro-avr', user_id=42, article_id=None,
            source='phone', stages_total=8,
        )
        self.assertEqual(run_id, 7)
        params = cursor.execute.call_args[0][1]
        self.assertEqual(params['source'], 'phone')

    def test_unknown_source_still_falls_back(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        wiki_trainers.start_run(
            cursor, trainer_key='taxi-pro-avr', user_id=42, article_id=None,
            source='telegram', stages_total=8,
        )
        self.assertEqual(cursor.execute.call_args[0][1]['source'], 'article')

    def test_create_table_check_matches_sources(self):
        create = next(s for s in wiki_schema._TRAINER_STATEMENTS
                      if 'CREATE TABLE IF NOT EXISTS wiki_trainer_runs' in s)
        self.assertEqual(_source_check_values(create), sorted(wiki_trainers.SOURCES))

    def test_existing_constraint_is_rebuilt_once(self):
        rebuild = [s for s in wiki_schema._TRAINER_STATEMENTS
                   if 'ADD CONSTRAINT wiki_trainer_runs_source_check' in s]
        self.assertEqual(len(rebuild), 1, 'пересборка CHECK должна быть ровно одна')
        sql = rebuild[0]
        self.assertEqual(_source_check_values(sql), sorted(wiki_trainers.SOURCES))
        # Условная: не трогать таблицу, если 'phone' в определении уже есть.
        self.assertIn("position('phone' in pg_get_constraintdef(oid))", sql)
        self.assertIn('DROP CONSTRAINT IF EXISTS wiki_trainer_runs_source_check', sql)


if __name__ == '__main__':
    unittest.main()
