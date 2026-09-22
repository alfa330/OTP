# -*- coding: utf-8 -*-
"""«Маркетинговый мониторинг» в ИИ-оценке (ТЗ #317).

Сторожит три вещи, которые ломаются молча:
  * разбор шести осей (ФТ-05…ФТ-10) — неверное значение это 400, а не снятый
    фильтр; причина без этапа «Закрыто-нереализовано» — отказ;
  * SQL предиката — оси попадают в запрос ровно с теми параметрами и в том же
    порядке, что и плейсхолдеры;
  * зеркала фронт/бэк — маркеры «закрытого» этапа и перечень осей одинаковы в
    filters.py и filters.js, иначе панель разрешала бы отбор, который сервер
    отвергает.

Чистые тесты: ни базы, ни сети. Схема развёрнута через SAVEPOINT и проверена
EXPLAIN'ом на боевой схеме отдельно (см. память проекта).
"""
import re
import unittest
from pathlib import Path

from call_qa.marketing import filters as mkt
from call_qa.marketing import export as mkt_export
from call_qa.marketing.schema import PARK_SEED, CHANNEL_SEED, QA_MARKETING_SCHEMA_SQL

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


class NormaliseTests(unittest.TestCase):
    def test_empty_is_no_filter(self):
        self.assertEqual(mkt.normalise({}), {})
        self.assertEqual(mkt.normalise(None), {})
        self.assertEqual(mkt.normalise({'parks': '', 'stages': [], 'deal_id': ' '}), {})

    def test_codes_are_lowercased_and_deduplicated(self):
        out = mkt.normalise({'parks': 'iTaxi,jana,ITAXI', 'channels': ['TikTok', 'none']})
        self.assertEqual(out['parks'], ['itaxi', 'jana'])
        self.assertEqual(out['channels'], ['tiktok', 'none'])

    def test_bad_code_is_an_error_not_a_silent_drop(self):
        with self.assertRaises(ValueError):
            mkt.normalise({'parks': 'i taxi'})
        with self.assertRaises(ValueError):
            mkt.normalise({'channels': ["tiktok' OR 1=1"]})

    def test_raw_labels_keep_commas_and_case(self):
        out = mkt.normalise({'stages': ['Нет авто (не цел), аренда', 'Закрыто и не реализовано']})
        self.assertEqual(out['stages'], ['Нет авто (не цел), аренда', 'Закрыто и не реализовано'])

    def test_reason_requires_lost_stage(self):
        with self.assertRaises(ValueError):
            mkt.normalise({'reasons': ['Нет авто (не цел)']})
        with self.assertRaises(ValueError):
            mkt.normalise({'stages': ['Звонки'], 'reasons': ['Нет авто (не цел)']})
        out = mkt.normalise({'stages': ['Закрыто и не реализовано'], 'reasons': ['Нет авто (не цел)', 'none']})
        self.assertEqual(out['reasons'], ['Нет авто (не цел)', 'none'])

    def test_modes_only_with_values(self):
        # Одинокий режим без значений — чип ни о чём; сервер его не запоминает.
        self.assertNotIn('stage_mode', mkt.normalise({'stage_mode': 'at_call'}))
        self.assertNotIn('handler_mode', mkt.normalise({'handler_mode': 'crm'}))
        out = mkt.normalise({'stages': ['x'], 'stage_mode': 'at_call',
                             'handler_ids': '5', 'handler_mode': 'crm'})
        self.assertEqual(out['stage_mode'], 'at_call')
        self.assertEqual(out['handler_mode'], 'crm')
        with self.assertRaises(ValueError):
            mkt.normalise({'stages': ['x'], 'stage_mode': 'yesterday'})

    def test_handler_ids_are_ints(self):
        out = mkt.normalise({'handler_ids': ['5', '6', '5'], 'handler_group_ids': '7'})
        self.assertEqual(out['handler_ids'], [5, 6])
        self.assertEqual(out['handler_group_ids'], [7])
        with self.assertRaises(ValueError):
            mkt.normalise({'handler_ids': 'петров'})

    def test_deal_id_is_numeric(self):
        self.assertEqual(mkt.normalise({'deal_id': ' 34210987 '})['deal_id'], '34210987')
        with self.assertRaises(ValueError):
            mkt.normalise({'deal_id': 'ABC'})

    def test_too_many_values_refused(self):
        with self.assertRaises(ValueError):
            mkt.normalise({'parks': ','.join(f'p{i}' for i in range(mkt.MAX_VALUES + 1))})


class PredicateTests(unittest.TestCase):
    OP = "COALESCE(c.operator_id, e.operator_user_id)"

    def _sql(self, raw):
        return mkt.predicate(mkt.normalise(raw), operator_id_sql=self.OP,
                             group_of_person=lambda person: f"GROUP_OF({person})")

    def test_empty_filters_add_nothing(self):
        self.assertEqual(self._sql({}), ("", ()))

    def test_placeholders_match_params_in_order(self):
        sql, params = self._sql({
            'parks': 'itaxi,none', 'channels': 'tiktok', 'campaigns': ['spring'],
            'stages': ['Закрыто и не реализовано'], 'stage_mode': 'at_call',
            'reasons': ['Нет авто (не цел)', 'none'],
            'handler_ids': '5', 'handler_group_ids': '7', 'handler_mode': 'crm',
            'deal_id': '123'})
        self.assertEqual(sql.count('%s'), len(params))
        self.assertEqual(list(params), [['itaxi'], ['tiktok'], ['spring'],
                                        ['Закрыто и не реализовано'], ['Нет авто (не цел)'],
                                        [5], [7], '123'])
        # Корзина «Не определено» — сравнение с пустой строкой, без параметра.
        self.assertIn(f"{mkt.PARK_CODE} = ''", sql)
        self.assertIn(f"{mkt.REASON_CURRENT} = ''", sql)
        # «На момент разговора» — журнал, а не снимок.
        self.assertIn(mkt.STAGE_AT_CALL, sql)
        self.assertNotIn(f"{mkt.STAGE_CURRENT} = ANY", sql)
        # Ответственный в CRM — через сопоставление, группа — правилом раздела.
        self.assertIn(f"{mkt.RESPONSIBLE_ID} = ANY(%s)", sql)
        self.assertIn(f"GROUP_OF({mkt.RESPONSIBLE_ID}) = ANY(%s)", sql)

    def test_spoke_mode_uses_the_sections_operator(self):
        sql, params = self._sql({'handler_ids': '5', 'handler_group_ids': '7'})
        self.assertIn(f"{self.OP} = ANY(%s)", sql)
        self.assertIn(f"GROUP_OF({self.OP}) = ANY(%s)", sql)
        self.assertNotIn(mkt.RESPONSIBLE_ID, sql)
        self.assertEqual(list(params), [[5], [7]])

    def test_current_stage_prefers_journal_over_snapshot(self):
        # Снимок op_funnel_leads у зафиксированных суток застывает; текущий
        # этап обязан идти из журнала, а снимок — только запасной вариант.
        self.assertTrue(mkt.STAGE_CURRENT.startswith("COALESCE(btrim(slast.stage_raw)"))
        self.assertIn("btrim(l.stage_raw)", mkt.STAGE_CURRENT)
        # И пустая причина из журнала НЕ подменяется снимком (нет NULLIF).
        self.assertNotIn("NULLIF", mkt.REASON_CURRENT)

    def test_join_uses_ordinary_jsonb_ops_index(self):
        # jsonb_path_ops не поддерживает оператор ?, индекс был бы мёртвым.
        index_line = next(line for line in QA_MARKETING_SCHEMA_SQL.splitlines()
                          if 'ON qa_marketing_dict USING GIN' in line)
        self.assertIn("USING GIN (aliases)", index_line)
        self.assertNotIn("jsonb_path_ops", index_line)
        self.assertIn("mpark.aliases ? lower(btrim", mkt.JOIN_SQL)


class LostStageTests(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(mkt.is_lost_stage('Закрыто и не реализовано'))
        self.assertTrue(mkt.is_lost_stage('  ЗАКРЫТО-НЕРЕАЛИЗОВАНО '))
        self.assertFalse(mkt.is_lost_stage('ПРОШЕЛ РЕГИСТРАЦИЮ'))
        self.assertFalse(mkt.is_lost_stage(''))


class SeedTests(unittest.TestCase):
    def test_spec_brands_present(self):
        # Семь брендов из ФТ-05 и написания, которые реально лежат в проде.
        codes = {code for code, *_ in PARK_SEED}
        self.assertEqual(codes, {'itaxi', 'jana', 'tenge', 'amanat', 'noltaxi', 'adal', 'qazaq'})
        aliases = {alias for *_, items in PARK_SEED for alias in items}
        for raw in ('ноль такси', 'аманат', 'адал', 'jana taxi', 'tenge taxi', 'itaxi (доставка)'):
            self.assertIn(raw, aliases)

    def test_channels_cover_normaliser_output(self):
        # Нормализатор воронки отдаёт эти коды; каждый обязан найти свой канал,
        # иначе TikTok в базе не совпал бы с TikTok в фильтре.
        aliases = {alias for *_, items in CHANNEL_SEED for alias in items} | {code for code, *_ in CHANNEL_SEED}
        for produced in ('google', 'facebook', 'tiktok', 'yandex', 'olx', '2gis', 'wz', 'звонки'):
            self.assertIn(produced, aliases)


class ExportTests(unittest.TestCase):
    def test_xlsx_and_csv_carry_deal_columns(self):
        items = [{"id": 1, "subject": "call", "subject_datetime": "01.09 10:00", "direction": "Основа ОП",
                  "operator": "Иванов", "ai": 80, "human": 90,
                  "deal": {"id": "123", "channel_title": "TikTok", "campaign": "spring",
                           "park_title": "Jana", "stage": "Закрыто и не реализовано",
                           "reason": "Нет авто (не цел)", "responsible": "Петров"}},
                 {"id": 2, "subject": "wz_episode", "direction": "Верификатор", "operator": "Сидоров",
                  "ai": None, "human": None, "deal": None}]
        xlsx = mkt_export.build_xlsx(items)
        self.assertTrue(xlsx.startswith(b'PK'))
        csv_text = mkt_export.build_csv(items).decode('utf-8-sig')
        lines = csv_text.strip().split('\r\n')
        self.assertEqual(lines[0].split(';')[8], 'Канал')
        self.assertIn('TikTok;spring;Jana', lines[1])
        self.assertIn('Чат Wazzup', lines[2])
        # Сделки нет — ячейки пустые, строка на месте.
        self.assertTrue(lines[2].endswith(';;;;;;;'))


class WiringTests(unittest.TestCase):
    """Модуль подключён туда, где обещано, — иначе всё выше тестирует воздух."""

    def test_schema_initialised_under_savepoint(self):
        src = _read('database.py')
        self.assertIn('self._init_qa_marketing_schema_tx(cursor)', src)
        self.assertIn('SAVEPOINT qa_marketing_schema', src)
        self.assertIn('from call_qa.marketing.schema import init_qa_marketing_schema', src)

    def test_api_lists_and_counts_share_the_join(self):
        src = _read('call_qa', 'api.py')
        self.assertIn('from .marketing import filters as mkt', src)
        # Списки — с колонками сделки, счётчики — с той же связкой при отборе.
        self.assertEqual(src.count('_marketing_join(cur, filters, need_columns=True)'), 2)
        self.assertEqual(src.count('_marketing_join(cur, filters)'), 2)
        self.assertIn("marketing = filters.get('marketing')", src)
        self.assertIn('def marketing_options(', src)
        self.assertIn('def deal_for_subject(', src)

    def test_routes_and_scheduler(self):
        src = _read('bot_schedule2.py')
        for route in ('/api/ai-qa/marketing-options', '/api/ai-qa/presets',
                      '/api/ai-qa/presets/<int:preset_id>', '/api/ai-qa/export',
                      '/api/ai-qa/marketing/relink'):
            self.assertIn(f"@app.route('{route}'", src)
        self.assertIn("id='op_funnel_amo_incremental'", src)
        self.assertIn("minute='3,18,33,48'", src)
        # Обе формы массива: axios шлёт со скобками, curl — без.
        self.assertIn("request.args.getlist(key + '[]')", src)
        # Карточка несёт сделку.
        self.assertIn('payload["deal"] = deal_for_subject(subject, call_id)', src)

    def test_stage_journal_written_by_both_syncs(self):
        src = _read('op_funnel', 'sync.py')
        self.assertEqual(src.count('queries.log_lead_stages(cursor,'), 2)
        self.assertIn('def sync_amo_changes(', src)
        self.assertIn("filter[updated_at][from]", _read('op_funnel', 'sources.py'))
        self.assertIn('CREATE TABLE IF NOT EXISTS op_funnel_lead_stages', _read('op_funnel', 'schema.py'))

    def test_frontend_mirror(self):
        js = _read('src', 'components', 'call_qa', 'filters.js')
        # Маркеры «закрытого» этапа совпадают буквально: панель гасит причину
        # ровно там, где сервер её отвергает.
        py_markers = set(mkt.LOST_STAGE_MARKERS)
        js_markers = set(re.findall(r"'([^']+)'", js.split('LOST_STAGE_MARKERS = [', 1)[1].split('];', 1)[0]))
        self.assertEqual(py_markers, js_markers)
        self.assertIn("NONE_BUCKET = 'none'", js)
        self.assertEqual(mkt.NONE_BUCKET, 'none')
        for key in ('parks', 'channels', 'campaigns', 'stages', 'reasons', 'handler_ids', 'handler_group_ids'):
            self.assertIn(f"'{key}'", js)
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        for piece in ('/api/ai-qa/marketing-options', '/api/ai-qa/presets', '/api/ai-qa/export',
                      'function MarketingFilters', 'Сначала этап «Закрыто и не реализовано»'):
            self.assertIn(piece, panel)
        for name in ('EvaluationsList.jsx', 'QueueList.jsx', 'CallReviewCard.jsx'):
            self.assertIn("import DealBadge from './DealBadge'", _read('src', 'components', 'call_qa', name))


if __name__ == '__main__':
    unittest.main()
