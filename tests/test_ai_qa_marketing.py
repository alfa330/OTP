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

import json

from call_qa.marketing import filters as mkt
from call_qa.marketing import export as mkt_export
from call_qa.marketing import brands
from call_qa.marketing import linker
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
            'parks': 'itaxi,none', 'channels': 'tiktok', 'campaigns': ['google|spring'],
            'stages': ['Закрыто и не реализовано'], 'stage_mode': 'at_call',
            'reasons': ['Нет авто (не цел)', 'none'],
            'handler_ids': '5', 'handler_group_ids': '7', 'handler_mode': 'crm',
            'handler_keys': ['amo:8303491'],
            'deal_id': '123'})
        self.assertEqual(sql.count('%s'), len(params))
        self.assertEqual(list(params), [['itaxi'], ['tiktok'], ['google'], ['spring'],
                                        ['Закрыто и не реализовано'], ['Нет авто (не цел)'],
                                        [5], [7], ['amo:8303491'], '123'])
        # Учётка CRM без сотрудника — по ключу «источник:учётка».
        self.assertIn(f"{mkt.RESPONSIBLE_KEY} = ANY(%s)", sql)
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
                           "reason": "Нет авто (не цел)", "responsible": "Петров",
                           "lead_type": "форма", "stage_at_call": ""}},
                 {"id": 2, "subject": "wz_episode", "direction": "Верификатор", "operator": "Сидоров",
                  "ai": None, "human": None, "deal": None}]
        xlsx = mkt_export.build_xlsx(items)
        self.assertTrue(xlsx.startswith(b'PK'))
        csv_text = mkt_export.build_csv(items).decode('utf-8-sig')
        lines = csv_text.strip().split('\r\n')
        header = lines[0].split(';')
        self.assertEqual(header[8], 'Канал')
        self.assertIn('Тип лида', header)
        self.assertIn('Этап на момент разговора', header)
        self.assertIn('TikTok;spring;форма;Jana', lines[1])
        # Ответственный доезжает до файла (прежде столбец был пуст всегда:
        # строка списка его не несла).
        self.assertTrue(lines[1].endswith(';Петров'))
        self.assertIn('Чат Wazzup', lines[2])
        # Сделки нет — ячейки пустые, строка на месте.
        deal_columns = len(header) - header.index('№ сделки')
        self.assertTrue(lines[2].endswith(';' * deal_columns))


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
        self.assertEqual(src.count('_marketing_join(cur, filters, need_columns=with_deals)'), 2)
        # Счётчик очереди, фильтр подтяжки, набор субъектов и очередь «Обзора».
        self.assertEqual(src.count('_marketing_join(cur, filters)'), 4)
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
        self.assertIn('payload["deal"] = (deal_for_subject(subject, call_id)', src)

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
        for key in ('parks', 'channels', 'campaigns', 'stages', 'reasons', 'handler_ids',
                    'handler_group_ids', 'handler_keys'):
            self.assertIn(f"'{key}'", js)
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        for piece in ('/api/ai-qa/marketing-options', '/api/ai-qa/presets', '/api/ai-qa/export',
                      'function MarketingFilters', 'Сначала этап «Закрыто и не реализовано»'):
            self.assertIn(piece, panel)
        for name in ('EvaluationsList.jsx', 'QueueList.jsx', 'CallReviewCard.jsx'):
            self.assertIn("import DealBadge from './DealBadge'", _read('src', 'components', 'call_qa', name))



class BrandTests(unittest.TestCase):
    """Парк сводится к бренду: иначе фильтр «iTaxi» ловил треть сделок iTaxi."""

    def test_spec_brands_absorb_cities_and_services(self):
        for raw, code in (('itaxi (алматы)', 'itaxi'), ('ITAXI 2 (Астана)', 'itaxi'),
                          ('itaxi_2_astana', 'itaxi'), ('itaxi  (доставка)', 'itaxi'),
                          ('itaxi vip шымкент', 'itaxi'), ('jana taxi алматы', 'jana'),
                          ('жана межгород алматы', 'jana'), ('ноль такси алматы', 'noltaxi'),
                          ('0 такси', 'noltaxi'), ('tenge taxi астана', 'tenge'),
                          ('qazaq  алматы', 'qazaq'), ('адал шымкент', 'adal'), ('аманат', 'amanat')):
            self.assertEqual(brands.park_brand(raw)[0], code, raw)
        self.assertEqual(brands.park_brand('itaxi (алматы)')[1], 'iTaxi')

    def test_multiselect_glue_goes_to_its_first_brand(self):
        glued = 'itaxi (алматы), itaxi (астана), ipartner (астана), itaxi (шымкент)'
        self.assertEqual(brands.park_brand(glued), ('itaxi', 'iTaxi'))

    def test_other_parks_lose_city_and_trailing_taxi(self):
        self.assertEqual(brands.park_brand('достойный астана'), ('dostoynyy', 'Достойный'))
        self.assertEqual(brands.park_brand('департамент такси кокшетау'),
                         brands.park_brand('департамент'))
        self.assertEqual(brands.park_brand('Бизнес Партнёр  Алматы')[0], 'biznes_partner')
        # «Такси» в начале — часть имени, латинское «taxi» — тоже.
        self.assertEqual(brands.park_brand('такси 24 астана'), ('taksi_24', 'Такси 24'))
        self.assertEqual(brands.park_brand('salam taxi алматы')[0], 'salam_taxi')

    def test_city_alone_is_not_a_park(self):
        for raw in ('усть-каменогорск', 'жанаозен', 'алматы', 'ekibastuz', 'temirtau',
                    '', '   ', None):
            self.assertIsNone(brands.park_brand(raw), raw)


class _DictCursor:
    """Словарь парков в памяти вместо таблицы: чистка проверяется целиком."""

    def __init__(self, rows):
        self.rows = {code: dict(title=title, aliases=list(aliases), order=order, by=by)
                     for code, title, aliases, order, by in rows}
        self.writes = 0
        self.rowcount = 1
        self._result = []

    def execute(self, sql, params=None):
        head = sql.split()[0].upper()
        if head == 'SELECT':
            self._result = [(code, row['title'], row['aliases'], row['order'], row['by'])
                            for code, row in self.rows.items()]
            return
        self.writes += 1
        if head == 'DELETE':
            self.rows.pop(params[0], None)
        elif head == 'UPDATE':
            self.rows[params[2]].update(aliases=json.loads(params[0]), title=params[1])
        elif head == 'INSERT':
            _kind, code, title, order, aliases = params
            if code in self.rows:
                merged = set(self.rows[code]['aliases']) | set(json.loads(aliases))
                self.rows[code]['aliases'] = sorted(merged)
            else:
                self.rows[code] = dict(title=title, aliases=json.loads(aliases), order=order, by=None)

    def fetchall(self):
        return self._result


class DictionaryCleanupTests(unittest.TestCase):
    ROWS = [
        ('itaxi', 'iTaxi', ['itaxi', 'itaxi 2'], 10, None),
        ('itaxi_almaty', 'itaxi (алматы)', ['itaxi_almaty', 'itaxi (алматы)'], 900, None),
        ('dostoynyy_astana', 'достойный астана', ['dostoynyy_astana', 'достойный астана'], 900, None),
        ('chestnyy', 'честный', ['chestnyy', 'честный'], 900, None),
        ('global', 'global', ['global'], 900, None),
        ('global_astana', 'global астана', ['global_astana', 'global астана'], 900, None),
        # Транслит-код рядом с настоящим написанием не заводит бренд-призрак.
        ('zhanataksi', 'жанатакси', ['zhanataksi', 'жанатакси'], 900, None),
        # Транслит города рядом с самим городом — не парк.
        ('ekibastuz', 'экибастуз', ['ekibastuz', 'экибастуз'], 900, None),
        ('ust_kamenogorsk', 'усть-каменогорск', ['ust_kamenogorsk', 'усть-каменогорск'], 900, None),
        # Правленная человеком строка — неприкосновенна.
        ('itaxi_karaganda', 'Караганда (вручную)', ['itaxi караганда'], 900, 7),
    ]

    def test_learned_rows_fold_into_brands(self):
        cur = _DictCursor(self.ROWS)
        linker.normalise_park_dictionary(cur)
        rows = cur.rows
        self.assertNotIn('itaxi_almaty', rows)
        self.assertIn('itaxi (алматы)', rows['itaxi']['aliases'])
        self.assertEqual(rows['itaxi']['title'], 'iTaxi')           # засеянная подпись цела
        self.assertEqual(rows['dostoynyy']['title'], 'Достойный')
        self.assertNotIn('dostoynyy_astana', rows)
        # Подпись — из написания, а не из транслита кода.
        self.assertEqual(rows['chestnyy']['title'], 'Честный')
        # Однословный код — это и настоящее написание: сделки «global» не теряются.
        self.assertIn('global', rows['global']['aliases'])
        self.assertIn('global астана', rows['global']['aliases'])
        self.assertNotIn('zhanataksi', rows)
        self.assertNotIn('ekibastuz', rows)
        self.assertIn('жанатакси', rows['jana']['aliases'])
        # Один город — не парк: строка уходит, сделка остаётся «Не определено».
        self.assertNotIn('ust_kamenogorsk', rows)
        self.assertEqual(rows['itaxi_karaganda']['title'], 'Караганда (вручную)')

    def test_second_pass_changes_nothing(self):
        cur = _DictCursor(self.ROWS)
        linker.normalise_park_dictionary(cur)
        cur.writes = 0
        self.assertEqual(linker.normalise_park_dictionary(cur), 0)
        self.assertEqual(cur.writes, 0)


class LinkTimeTests(unittest.TestCase):
    """Момент разговора не сдвигается на пять часов (ТЗ #317, сверка 24.09.2026)."""

    def test_moment_reads_wall_clock_fields_without_shift(self):
        moment = linker._SUBJECT_MOMENT
        # Дата обращения — уже часы Алматы.
        self.assertIn('c.appeal_date,', moment)
        self.assertNotIn("c.appeal_date AT TIME ZONE", moment)
        # datetime_raw — часы Алматы с ярлыком UTC: снимаем ярлык, не переводим.
        self.assertIn("ic.datetime_raw AT TIME ZONE 'UTC'", moment)

    def test_no_double_shift_of_imported_calls_anywhere_in_ai_qa(self):
        for path in sorted((ROOT / 'call_qa').rglob('*.py')):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn("datetime_raw AT TIME ZONE 'Asia/Almaty'", text, str(path))

    def test_old_rules_version_is_relinked_and_stale_links_dropped(self):
        self.assertGreaterEqual(linker.LINK_RULES_VERSION, 2)
        self.assertIn('ADD COLUMN IF NOT EXISTS rules_version', QA_MARKETING_SCHEMA_SQL)

        class Cur:
            def __init__(self):
                self.calls = []
                self.rowcount = 1

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

            def fetchall(self):
                return []

        cur = Cur()
        linker._fetch_subjects(cur, only_new=True)
        sql, params = cur.calls[-1]
        self.assertIn('sd.rules_version >= %s', sql)
        self.assertEqual(params, (linker.LINK_RULES_VERSION,))

        cur = Cur()
        subjects = [{'subject_kind': 'imported_call', 'call_id': 5570},
                    {'subject_kind': 'call', 'call_id': 9833}]
        linker._drop_stale(cur, subjects, {('call', 9833)})
        sql, params = cur.calls[-1]
        self.assertTrue(sql.lstrip().startswith('DELETE FROM qa_subject_deals'))
        self.assertEqual(params, (['imported_call'], [5570]))


class HandlerKeyTests(unittest.TestCase):
    def test_crm_account_only_in_responsible_mode(self):
        out = mkt.normalise({'handler_keys': ['amo:8303491'], 'handler_mode': 'crm'})
        self.assertEqual(out, {'handler_keys': ['amo:8303491'], 'handler_mode': 'crm'})
        with self.assertRaises(ValueError):
            mkt.normalise({'handler_keys': ['amo:8303491']})
        with self.assertRaises(ValueError):
            mkt.normalise({'handler_keys': ['без источника'], 'handler_mode': 'crm'})

    def test_responsible_name_never_shows_a_bare_account_number(self):
        # Сотрудник портала → имя из сопоставления, если не номер → имя учётки
        # из справочника Wazzup → и только потом номер.
        name = mkt.RESPONSIBLE_NAME
        self.assertLess(name.index('hu.name'), name.index('omap.external_name'))
        self.assertLess(name.index('omap.external_name'), name.index('wmap.external_name'))
        self.assertIn("!~ '^[0-9]*$'", name)
        self.assertIn("LEFT JOIN users hu ON hu.id = omap.user_id", mkt.JOIN_SQL)
        self.assertIn("wmap.source = 'wazzup'", mkt.JOIN_SQL)


class BucketTests(unittest.TestCase):
    def test_undetermined_means_deal_without_value(self):
        # Разговор без сделки в «Не определено» не попадает: иначе корзина
        # возвращала почти весь раздел при «Не определено · 4» в справочнике.
        sql, _ = mkt.predicate(mkt.normalise({'parks': 'none', 'channels': 'none'}),
                               operator_id_sql='OP', group_of_person=lambda p: p)
        self.assertIn(f"({mkt.DEAL_LINKED} AND {mkt.PARK_CODE} = '')", sql)
        self.assertIn(f"({mkt.DEAL_LINKED} AND {mkt.CHANNEL_CODE} = '')", sql)


class ChannelTests(unittest.TestCase):
    def test_youtube_is_its_own_channel(self):
        self.assertIn('youtube', {code for code, *_ in CHANNEL_SEED})
        # Сырой utm_source сделки нужен ДО словаря каналов.
        self.assertLess(mkt.JOIN_SQL.index('LEFT JOIN amo_leads al'),
                        mkt.JOIN_SQL.index('LEFT JOIN qa_marketing_dict mchan'))
        self.assertIn(f"mchan.aliases ? {mkt.CHANNEL_RAW}", mkt.JOIN_SQL)
        self.assertIn("al.utm_source", mkt.CHANNEL_RAW)
        # Нормализатор воронки не тронут: по нему сходятся её отчёты.
        self.assertIn("'youtube': 'google'", _read('op_funnel', 'sources.py'))


class OptionsTests(unittest.TestCase):
    def test_full_directories_and_reason_source(self):
        src = _read('call_qa', 'api.py')
        body = src.split('def marketing_options(', 1)[1].split('\ndef ', 1)[0]
        # Полные справочники, а не только встреченное у связанных разборов.
        self.assertIn('FROM qa_marketing_dict', body)
        self.assertIn('FROM op_funnel_leads', body)
        self.assertIn('op_funnel_lead_stages', body)
        self.assertIn('mkt.REASON_SOURCES', body)
        self.assertEqual(mkt.REASON_SOURCES, ('amo',))
        # Отдел без сделок — блок не рисуется.
        self.assertEqual(mkt.DEAL_DEPARTMENTS, ('op',))
        sql, params = mkt.lost_stage_sql('X')
        self.assertEqual(sql.count('%s'), 1)
        self.assertNotIn('%%', sql)
        self.assertTrue(all(p.startswith('%') for p in params[0]))

    def test_deal_row_carries_export_fields(self):
        from call_qa import api
        self.assertEqual(api._DEAL_COLUMNS_EMPTY.count('NULL'), api._DEAL_COLUMN_COUNT)
        row = ('lead', 'itaxi', 'iTaxi', 'google', 'Google', 'spring', 'Звонки', '', 2,
               'Петров', 'форма', 'Новая заявка')
        deal = api._deal_row(('x',) + row, 1)
        self.assertEqual((deal['responsible'], deal['lead_type'], deal['stage_at_call']),
                         ('Петров', 'форма', 'Новая заявка'))
        self.assertEqual(deal['shared_phone'], 2)


class TabWiringTests(unittest.TestCase):
    """Фильтры доступны на «Обзоре» и в «Базе разборов» (ТЗ #317, п. 2.1)."""

    def test_backend(self):
        src = _read('bot_schedule2.py')
        stats_route = src.split("@app.route('/api/ai-qa/stats'", 1)[1].split('@app.route', 1)[0]
        self.assertIn('_ai_qa_list_filters()', stats_route)
        self.assertIn('filters=filters', stats_route)
        rag_route = src.split("@app.route('/api/ai-qa/adjudications', methods", 1)[1].split('@app.route', 1)[0]
        self.assertIn("filters={'marketing': filters['marketing']}", rag_route)
        self.assertIn("'handler_keys': many('handler_keys')", src)
        api_src = _read('call_qa', 'api.py')
        self.assertIn('def _filtered_stats(', api_src)
        self.assertIn('def _rules_of_deals_predicate(', api_src)
        self.assertIn('_reviewed_metrics(cur, subject_keys=keys)', api_src)

    def test_frontend(self):
        view = _read('src', 'components', 'call_qa', 'CallQaView.jsx')
        self.assertIn("const FILTERABLE_TABS = ['queue', 'chats', 'evals'];", view)
        self.assertIn("const MARKETING_FILTERABLE_TABS = ['overview', 'rag'];", view)
        self.assertIn("marketingOnly={tab === 'rag'}", view)
        self.assertIn('filters={marketingAccess ? filters : undefined}', view)
        self.assertIn('filtersToParams(filters)', _read('src', 'components', 'call_qa', 'QaDashboard.jsx'))
        self.assertIn('...dealParams', _read('src', 'components', 'call_qa', 'AdjudicationsRag.jsx'))
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        # ФТ-09: значение причины — поле value ответа сервера.
        self.assertIn('valueOf: (item) => item.value, titleOf: (item) => item.title, countOf: (item) => item.calls', panel)
        self.assertNotIn('disabled: !person.matched', panel)
        self.assertIn('parent: CH + channel.code', panel)
        self.assertIn("expandLabel: 'кампании'", panel)
        self.assertIn('parent', _read('src', 'components', 'ui', 'CustomSelect.jsx'))


class ReviewFixTests(unittest.TestCase):
    """Замечания независимого разбора коммита (сверка 24.09.2026)."""

    def test_stage_at_call_compares_in_utc(self):
        # Журнал этапов пишется в UTC, момент разговора — часы Алматы.
        shist = mkt.JOIN_SQL.split(') shist ON TRUE', 1)[0].rsplit('LEFT JOIN LATERAL', 1)[1]
        self.assertIn("(sd.happened_at AT TIME ZONE 'Asia/Almaty') AT TIME ZONE 'UTC'", shist)
        self.assertNotIn("ls.seen_at <= COALESCE(sd.happened_at, NOW())", shist)

    def test_legacy_campaign_without_channel_is_any_channel(self):
        # Так лежат пресеты 22.09 и так шлёт вкладка со старым фронтом:
        # отказ ронял бы 400 каждый запрос этого отбора.
        sql, params = mkt.predicate(mkt.normalise({'campaigns': ['brand', 'google|x']}),
                                    operator_id_sql='OP', group_of_person=lambda p: p)
        self.assertIn(f"{mkt.CAMPAIGN} = ANY(%s)", sql)
        self.assertEqual(list(params), [['google'], ['x'], ['brand']])
        with self.assertRaises(ValueError):
            mkt.normalise({'campaigns': ['bad code|x']})
        with self.assertRaises(ValueError):
            mkt.normalise({'campaigns': ['google|']})

    def test_campaign_is_a_channel_pair_ored_with_channels(self):
        sql, params = mkt.predicate(
            mkt.normalise({'channels': 'tiktok', 'campaigns': ['google|brand_kz', 'none|x']}),
            operator_id_sql='OP', group_of_person=lambda p: p)
        # «Весь TikTok» ИЛИ «Google → brand_kz», а не их пересечение.
        self.assertIn(' OR ', sql)
        self.assertEqual(sql.count(' AND ('), 1)
        self.assertEqual(list(params), [['tiktok'], ['google', ''], ['brand_kz', 'x']])

    def test_overview_counts_subjects_by_latest_evaluation(self):
        src = _read('call_qa', 'api.py')
        body = src.split('def _filtered_stats(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('SELECT DISTINCT ON (rc.subject_kind, rc.call_id)', body)
        self.assertIn('out["evaluated"] = len(keys)', body)
        # Без отбора — тоже разговоры, а не строки кэша.
        self.assertIn('COUNT(DISTINCT (rc.subject_kind, rc.call_id)) FROM ai_review_cache rc', src)

    def test_export_is_not_capped_at_list_page(self):
        src = _read('bot_schedule2.py')
        route = src.split("@app.route('/api/ai-qa/export'", 1)[1].split('@app.route', 1)[0]
        self.assertIn('max_limit=_export.MAX_ROWS + 1', route)
        self.assertIn("response.headers['X-Truncated']", route)
        self.assertIn('Access-Control-Expose-Headers', route)
        self.assertIn('max_limit=500', _read('call_qa', 'api.py'))
        self.assertIn("'x-truncated'", _read('src', 'components', 'call_qa', 'QaFilters.jsx'))

    def test_utm_fresh_every_15_minutes(self):
        from op_funnel import queries, schema, sources
        for column in ('utm_source_raw', 'utm_campaign'):
            self.assertIn(column, queries._LEAD_COLUMNS)
            self.assertIn(f'ADD COLUMN IF NOT EXISTS {column}', ' '.join(schema.OP_FUNNEL_SCHEMA_MIGRATIONS))
        # Строка без сырых UTM (другая CRM) не роняет вставку NOT NULL.
        self.assertEqual(queries._lead_value({}, 'utm_campaign'), '')
        self.assertIsNone(queries._lead_value({}, 'phone'))
        self.assertEqual(sources.AMO_FIELD_UTM_CAMPAIGN, 892235)
        self.assertTrue(mkt.CAMPAIGN.startswith("COALESCE(NULLIF(btrim(l.utm_campaign)"))
        self.assertIn('l.utm_source_raw', mkt.CHANNEL_RAW)

    def test_amo_row_carries_raw_utm(self):
        from op_funnel import sources
        lead = {'id': 1, 'created_at': 1789983704, 'status_id': 1, 'responsible_user_id': 5,
                'custom_fields_values': [
                    {'field_id': sources.AMO_FIELD_UTM_SOURCE, 'values': [{'value': 'youtube'}]},
                    {'field_id': sources.AMO_FIELD_UTM_CAMPAIGN, 'values': [{'value': 'noltaxi_youtube_ki'}]},
                ]}
        rows, _seen = sources.amo_rows([lead], {1: 'Новая заявка'}, 'op_osnova')
        self.assertEqual(rows[0]['utm_source'], 'google')          # нормализатор не тронут
        self.assertEqual(rows[0]['utm_source_raw'], 'youtube')
        self.assertEqual(rows[0]['utm_campaign'], 'noltaxi_youtube_ki')

    def test_cached_card_refreshes_conversation_time(self):
        self.assertIn('cached["datetime"] = subject.get("datetime")', _read('call_qa', 'api.py'))

    def test_presets_reload_after_rule_catalog(self):
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        self.assertIn('useEffect(loadPresets, [apiBaseUrl, department, marketingOnly, marketingAccess]);', panel)
        rag = _read('src', 'components', 'call_qa', 'AdjudicationsRag.jsx')
        rollout = rag.split('function RolloutPanel(', 1)[1].split('export default function AdjudicationsRag', 1)[0]
        self.assertNotIn('dealSignature', rollout)


class MarketingAccessTests(unittest.TestCase):
    """Модуль видят маркетинг (раздел 3 ТЗ) и глобальные админы; остальным раздел
    такой, каким был до модуля (решение владельца 25.09.2026)."""

    def test_single_server_rule(self):
        src = _read('bot_schedule2.py')
        rule = src.split('def _ai_qa_can_use_marketing(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('_is_global_admin_requester(role, requester_id)', rule)
        self.assertIn('_is_marketing_observer(requester_id, role)', rule)
        self.assertIn("'marketing' in set(_headed_department_codes(requester_id))", rule)

    def test_every_entry_point_is_gated(self):
        src = _read('bot_schedule2.py')

        def route(path):
            return src.split(f"@app.route('{path}'", 1)[1].split('@app.route', 1)[0]
        self.assertIn('"marketing": _ai_qa_can_use_marketing(requester_id)', route('/api/ai-qa/departments'))
        self.assertIn('if not _ai_qa_can_use_marketing(requester_id):', route('/api/ai-qa/marketing-options'))
        self.assertIn('if not _ai_qa_can_use_marketing(requester_id):', route('/api/ai-qa/export'))
        self.assertIn('with_deals=_ai_qa_can_use_marketing(requester_id)', route('/api/ai-qa/evaluations'))
        self.assertIn('with_deals=_ai_qa_can_use_marketing(requester_id)', route('/api/ai-qa/review-queue'))
        self.assertIn('if _ai_qa_can_use_marketing(requester_id) else None',
                      route('/api/ai-qa/call/<int:call_id>'))
        guard = src.split('def _ai_qa_presets_guard(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('_ai_qa_can_use_marketing(requester_id)', guard)
        parser = src.split('def _ai_qa_list_filters(', 1)[1].split('\n@app.route', 1)[0]
        self.assertIn("filters.get('marketing') and not _ai_qa_can_use_marketing", parser)

    def test_lists_skip_deals_without_access(self):
        api = _read('call_qa', 'api.py')
        self.assertEqual(api.count('deal_join = _marketing_join(cur, filters, need_columns=with_deals)'), 2)

    def test_frontend_follows_server_flag(self):
        view = _read('src', 'components', 'call_qa', 'CallQaView.jsx')
        self.assertIn('setMarketingAccess(!!r.data?.marketing)', view)
        self.assertIn('canExport={marketingAccess && EXPORT_TABS.includes(tab)}', view)
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        self.assertIn('if (!apiBaseUrl || !department || !marketingAccess) {', panel)
        self.assertIn('marketingOnly || !marketingAccess) return;', panel)
        self.assertIn('{marketingAccess && (', panel)


class DealBlockLookTests(unittest.TestCase):
    """Вид блока «Сделка в amoCRM» (25.09.2026): группа как раздел настроек macOS,
    ровные ряды, число разборов колонкой справа, пустые значения — серым ниже."""

    def test_panel_layout(self):
        panel = _read('src', 'components', 'call_qa', 'QaFilters.jsx')
        # Переключатели режимов — маленьким сегментом в строке подписи.
        self.assertEqual(panel.count('<IosSegmented size="sm"'), 2)
        self.assertIn('function DealField(', panel)
        # Значения без разборов — отдельной частью списка, серым.
        self.assertIn("const ZERO_GROUP = 'Без разборов';", panel)
        self.assertIn('muted: true, groupLabel: ZERO_GROUP', panel)
        # В кнопке — названия выбранного, а не «Выбрано: N».
        self.assertIn('renderValue={(values) => pickedLabel(values, props.options)}', panel)
        # Сетка не распирает карточку на телефоне.
        self.assertIn('grid grid-cols-1 gap-x-3 gap-y-3.5 sm:grid-cols-2 lg:grid-cols-3', panel)
        # Блок сделки — последним в панели, после обычных фильтров.
        body = panel.split('export default function QaFilters', 1)[1].split('\nfunction ', 1)[0]
        self.assertLess(body.index('Балл ИИ'), body.rindex('<MarketingFilters'))

    def test_shared_primitives_stay_backward_compatible(self):
        select = _read('src', 'components', 'ui', 'CustomSelect.jsx')
        # meta/muted — необязательные поля опции: без них строка прежняя.
        self.assertIn("o.meta != null && o.meta !== ''", select)
        self.assertIn('o.muted && !isSel', select)
        ios = _read('src', 'components', 'ui', 'ios.jsx')
        self.assertIn("const small = size === 'sm';", ios)
        self.assertIn(": 'rounded-[8px] px-3 py-[5px] text-[12.5px] font-medium'", ios)

if __name__ == '__main__':
    unittest.main()
