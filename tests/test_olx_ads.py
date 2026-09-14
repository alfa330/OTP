# -*- coding: utf-8 -*-
"""Раздел «Объявления OLX» (задача #299): права, правила площадки, ИИ, запись, подключение.

Без базы и без сети: модули раздела нарочно не импортируют ни database, ни
flask на уровне модуля (кроме routes), поэтому читаются здесь напрямую.
"""

import json
import re
import unittest
from pathlib import Path

from olx_ads import access, ai, schema, service, validate

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding='utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Права
# ─────────────────────────────────────────────────────────────────────────────

class AccessTests(unittest.TestCase):

    def _ctx(self, role='operator', department_code=None, headed_codes=()):
        return {
            'role': role,
            'department_code': department_code,
            'headed_department_ids': [1] if headed_codes else [],
            'headed_department_codes': list(headed_codes),
        }

    def test_global_admin_does_everything(self):
        caps = access.capabilities(self._ctx(role='admin'))
        self.assertEqual(caps, {'can_view': True, 'can_write_content': True,
                                'can_apply': True})

    def test_heads_of_marketing_and_op_publish(self):
        for code in ('marketing', 'op'):
            caps = access.capabilities(self._ctx(role='head', headed_codes=[code]))
            self.assertTrue(caps['can_apply'], code)

    def test_rank_and_file_marketer_prepares_but_does_not_publish(self):
        # Решение владельца 14.09.2026: периметр как в «Лидах OLX» — запись
        # наружу у админа и глав, маркетолог готовит тексты.
        caps = access.capabilities(self._ctx(role='marketing_manager',
                                             department_code='marketing'))
        self.assertTrue(caps['can_view'])
        self.assertTrue(caps['can_write_content'])
        self.assertFalse(caps['can_apply'])

    def test_strangers_see_nothing(self):
        for ctx in (self._ctx(role='operator', department_code='szov'),
                    self._ctx(role='head', headed_codes=['szov']),
                    self._ctx(role='admin', headed_codes=['szov']),
                    {}):
            self.assertFalse(access.can_view(ctx), ctx)
            self.assertFalse(access.can_apply(ctx), ctx)

    def test_admin_who_heads_a_department_is_not_global(self):
        # Назначение главой ЗАМЕНЯЕТ базовую роль — семантика всего портала.
        self.assertFalse(access.can_apply(self._ctx(role='admin', headed_codes=['szov'])))

    def test_perimeter_is_taken_from_leads_not_copied(self):
        # Второй список отделов под ту же аудиторию разъехался бы с первым.
        source = _read('olx_ads', 'access.py')
        self.assertIn('from olx_amo import access as leads_access', source)
        self.assertNotIn("('op', 'marketing')", source)


# ─────────────────────────────────────────────────────────────────────────────
# Правила площадки
# ─────────────────────────────────────────────────────────────────────────────

_LONG_BODY = ('Подключаем водителей к Яндекс Про в нашем таксопарке, выплаты '
              'каждый день и поддержка в офисе без выходных.')


def _codes(problems):
    return sorted(p['code'] for p in problems)


class ValidateTests(unittest.TestCase):

    def test_title_limits_are_the_platform_ones(self):
        self.assertEqual((validate.TITLE_MIN, validate.TITLE_MAX), (16, 70))
        self.assertIn('too_short', _codes(validate.check_title('Такси')))
        self.assertIn('too_long', _codes(validate.check_title('а' * 71)))
        self.assertEqual([], validate.check_title('Работа в такси Алматы, выплаты'))
        # Ровно 70 — допустимо: шесть живых объявлений лежат ровно на пределе.
        self.assertNotIn('too_long', _codes(validate.check_title('б' * 35 + ' ' + 'в' * 34)))

    def test_caps_share_is_counted_from_letters(self):
        self.assertIn('caps', _codes(validate.check_title('РАБОТА В ТАКСИ АЛМАТЫ СЕГОДНЯ')))
        self.assertAlmostEqual(validate.capital_share('Ab 12 !!'), 0.5)

    def test_prize_money_is_not_a_phone(self):
        # Наши же тексты сплошь в суммах: наивная проверка «много цифр»
        # ругалась бы на призовой фонд.
        body = '<p>Призовой фонд 1 600 000 тг и бонус 15 000 ₸. %s</p>' % _LONG_BODY
        self.assertNotIn('phone', _codes(validate.check_description(body, 1812)))

    def test_real_phone_and_email_are_caught(self):
        with_phone = '<p>Звоните 8 700 123 45 67. %s</p>' % _LONG_BODY
        self.assertIn('phone', _codes(validate.check_description(with_phone, 1812)))
        with_mail = '<p>Пишите hr@example.com. %s</p>' % _LONG_BODY
        self.assertIn('email', _codes(validate.check_description(with_mail, 1812)))

    def test_triple_punctuation_is_caught(self):
        self.assertIn('punctuation', _codes(validate.check_title('Работа в такси!!! Звоните')))

    def test_only_allowed_tags_pass(self):
        ok = '<p>%s</p><ul><li>Раз</li><li>Два</li></ul>' % _LONG_BODY
        self.assertEqual([], validate.check_description(ok, 1812))
        bad = '<div>%s</div>' % _LONG_BODY
        self.assertIn('tags', _codes(validate.check_description(bad, 1812)))

    def test_html_outside_jobs_is_only_a_warning(self):
        # Аренда авто (3031) — не рубрика «Работа». Предупреждаем, не запрещаем.
        problems = validate.check_description('<p>%s</p>' % _LONG_BODY, 3031)
        self.assertEqual(['html_outside_jobs'], _codes(problems))
        self.assertEqual([], validate.blocking(problems))

    def test_title_is_trimmed_on_a_word_boundary(self):
        # ТЗ 2.4: не обрезать посередине слова; висящий «|» — тоже брак.
        cut = validate.trim_title('Яндекс таксопарк | Комфорт и Комфорт+ водитель | Работа в такси', 50)
        self.assertLessEqual(len(cut), 50)
        self.assertFalse(cut.endswith('|'))
        self.assertTrue('Яндекс таксопарк | Комфорт и Комфорт+ водитель | Работа в такси'.startswith(cut))

    def test_plain_text_drops_markup(self):
        self.assertEqual('Раз\nДва', validate.plain_text('<p>Раз</p><p>Два</p>'))


# ─────────────────────────────────────────────────────────────────────────────
# ИИ
# ─────────────────────────────────────────────────────────────────────────────

class AiTests(unittest.TestCase):

    def test_answer_envelope_is_parsed(self):
        title, body = ai.parse_answer(
            'Вот текст:\nЗАГОЛОВОК: Работа в такси Алматы\nОПИСАНИЕ:\n<p>Раз</p><p>Два</p>')
        self.assertEqual('Работа в такси Алматы', title)
        self.assertEqual('<p>Раз</p><p>Два</p>', body)

    def test_plain_answer_is_wrapped_into_paragraphs(self):
        title, body = ai.parse_answer('Курьер в Астане\nПервый\n\nВторой')
        self.assertEqual('Курьер в Астане', title)
        self.assertEqual('<p>Первый</p><p>Второй</p>', body)

    def test_direction_comes_from_the_category(self):
        self.assertEqual('courier', ai.direction_for(1802)['code'])
        self.assertEqual('rent', ai.direction_for(3031)['code'])
        self.assertEqual('other', ai.direction_for(None)['code'])

    def test_prompt_carries_city_brand_brief_and_forbids_repeating_the_old_title(self):
        prompt = ai.build_user_prompt(
            {'category_id': 1812, 'city_name': 'Костанай', 'company_name': 'Tenge taxi',
             'title': 'Старый заголовок'},
            brief={'offer': 'Комиссия 0% месяц', 'raffle': 'Розыгрыш iPhone'},
            instruction='короче')
        for piece in ('Костанай', 'Tenge taxi', 'Комиссия 0% месяц', 'Розыгрыш iPhone',
                      'Старый заголовок', 'короче'):
            self.assertIn(piece, prompt)
        self.assertIn('ЗАМЕНИТЬ', prompt)

    def test_system_prompt_states_the_hard_platform_rules(self):
        for rule in ('70', 'телефона', '<p>', 'половины'):
            self.assertIn(rule, ai.SYSTEM_PROMPT)

    def test_one_retry_with_complaints_then_mechanical_trim(self):
        calls = []
        long_title = 'Работа ' * 15

        def fake(system, user, **kwargs):
            calls.append(user)
            return {'text': 'ЗАГОЛОВОК: %s\nОПИСАНИЕ:\n<p>%s</p>' % (long_title, _LONG_BODY),
                    'model': 'fake'}

        result = ai.generate_for_advert({'category_id': 1812, 'city_name': 'Алматы'},
                                        brief={'offer': 'x'}, generate_fn=fake)
        # Ровно две попытки: вторая неудача значит, что модель не поняла задачу,
        # и жечь токены в цикле незачем.
        self.assertEqual(2, len(calls))
        self.assertIn('НЕ ПРОШЛА ПРОВЕРКУ', calls[1])
        self.assertLessEqual(len(result['title']), validate.TITLE_MAX)
        self.assertTrue(result['ok'])

    def test_clean_first_answer_costs_one_call(self):
        calls = []

        def fake(system, user, **kwargs):
            calls.append(user)
            return {'text': 'ЗАГОЛОВОК: Работа в такси Алматы, без ИП\nОПИСАНИЕ:\n<p>%s</p>'
                            % _LONG_BODY}

        result = ai.generate_for_advert({'category_id': 1812}, generate_fn=fake)
        self.assertEqual(1, len(calls))
        self.assertTrue(result['ok'])

    def test_provider_chain_is_reused_from_wiki(self):
        self.assertIn('from wiki.ai import providers', _read('olx_ads', 'ai.py'))


# ─────────────────────────────────────────────────────────────────────────────
# Запись в OLX
# ─────────────────────────────────────────────────────────────────────────────

_PAYLOAD = {
    'id': 392351952,
    'status': 'active',
    'title': 'Старый',
    'description': '<p>Старое</p>',
    'category_id': 1812,
    'advertiser_type': 'business',
    'external_id': None,
    'external_url': None,
    'contact': {'name': 'Парк', 'phone': None},
    'location': {'city_id': 19, 'district_id': 205, 'latitude': '51.2', 'longitude': '51.3'},
    'images': [],
    'price': None,
    'salary': {'value_from': 300000, 'value_to': 1100000, 'currency': 'KZT',
               'negotiable': False, 'type': 'monthly'},
    'attributes': [
        {'code': 'job_timing', 'value': 'part', 'values': None},
        {'code': 'driving_license', 'value': None, 'values': ['b']},
    ],
    'auto_extend_enabled': True,
}


class UpdateBodyTests(unittest.TestCase):

    def setUp(self):
        self.body = service.build_update_body(_PAYLOAD, 'Новый заголовок объявления',
                                              '<p>Новое</p>')

    def test_required_fields_are_all_present(self):
        for field in ('title', 'description', 'category_id', 'advertiser_type',
                      'contact', 'location', 'attributes'):
            self.assertIn(field, self.body)

    def test_only_text_changes(self):
        self.assertEqual('Новый заголовок объявления', self.body['title'])
        self.assertEqual('<p>Новое</p>', self.body['description'])
        self.assertEqual(_PAYLOAD['location']['city_id'], self.body['location']['city_id'])
        self.assertEqual(_PAYLOAD['salary'], self.body['salary'])
        self.assertEqual(1812, self.body['category_id'])

    def test_auto_extend_is_not_sent(self):
        # Отсутствие поля в PUT = «не менять» (документация + живая проба).
        self.assertNotIn('auto_extend_enabled', self.body)

    def test_no_null_halves_in_attributes_or_contact(self):
        self.assertEqual([{'code': 'job_timing', 'value': 'part'},
                          {'code': 'driving_license', 'values': ['b']}],
                         self.body['attributes'])
        self.assertNotIn('phone', self.body['contact'])

    def test_missing_required_attributes_are_not_invented(self):
        # experience_seeker/education схема зовёт обязательными, живых значений
        # нет ни у одного объявления, и PUT без них проходит.
        codes = {a['code'] for a in self.body['attributes']}
        self.assertNotIn('experience_seeker', codes)
        self.assertNotIn('education', codes)


class OlxProblemTests(unittest.TestCase):

    def _exc(self, payload):
        exc = RuntimeError('boom')
        exc.payload = payload
        return exc

    def test_validation_envelope_is_readable(self):
        text = service._olx_problem(self._exc({'error': {'validation': [
            {'field': 'title', 'title': 'Значение не должно быть пустым.'}]}}))
        self.assertEqual('title: Значение не должно быть пустым.', text)

    def test_detail_envelope_is_readable(self):
        text = service._olx_problem(self._exc({'error': {'detail': 'Advert not found'}}))
        self.assertEqual('Advert not found', text)


class ServiceInvariantsTests(unittest.TestCase):
    """То, что нельзя сломать молча, проверяем по исходнику."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read('olx_ads', 'service.py')

    def test_advert_is_reread_right_before_the_write(self):
        apply_text = self.source.split('def apply_text', 1)[1].split('\ndef ', 1)[0]
        self.assertLess(apply_text.index('client.advert(advert_id)'),
                        apply_text.index('client.update_advert('))

    def test_history_is_written_before_the_snapshot(self):
        apply_text = self.source.split('def apply_text', 1)[1].split('\ndef ', 1)[0]
        self.assertLess(apply_text.index("result='applied'"),
                        apply_text.index('queries.upsert_advert('))

    def test_failures_land_in_history_too(self):
        self.assertIn("result='failed'", self.source)

    def test_rollback_is_itself_recorded(self):
        rollback = self.source.split('def rollback', 1)[1].split('\ndef ', 1)[0]
        self.assertIn("source='rollback'", rollback)
        self.assertIn('rolled_back', rollback)

    def test_bulk_has_a_ceiling(self):
        self.assertIn('MAX_BULK', self.source)


# ─────────────────────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────────────────────

class _Cursor(object):
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)


class SchemaTests(unittest.TestCase):

    def test_tables_go_before_indexes(self):
        cursor = _Cursor()
        schema.init_olx_ads_schema(cursor)
        first_index = next(i for i, s in enumerate(cursor.statements)
                           if 'INDEX' in s.upper())
        last_table = max(i for i, s in enumerate(cursor.statements)
                         if 'CREATE TABLE' in s.upper())
        self.assertLess(last_table, first_index)

    def test_everything_is_idempotent(self):
        cursor = _Cursor()
        schema.init_olx_ads_schema(cursor)
        for statement in cursor.statements:
            if 'CREATE' in statement.upper():
                self.assertIn('IF NOT EXISTS', statement.upper())

    def test_history_keeps_full_before_and_after(self):
        ddl = ' '.join(schema._STATEMENTS)
        for column in ('old_title', 'new_title', 'old_description', 'new_description',
                       'actor_name', 'error_text'):
            self.assertIn(column, ddl)

    def test_one_live_draft_per_advert(self):
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn("uniq_olx_ads_draft_live", ddl)
        self.assertIn("WHERE status = 'draft'", ddl)

    def test_one_active_brief_per_cabinet_is_held_by_the_database(self):
        # Решение владельца 14.09.2026: разный бриф на разные кабинеты. Правило
        # «у кабинета один действующий бриф» — уникальным индексом, не кодом.
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('CREATE TABLE IF NOT EXISTS olx_ads_brief_cabinets', ddl)
        self.assertIn('uniq_olx_ads_brief_cabinet_active', ddl)
        self.assertIn('ON olx_ads_brief_cabinets (cabinet_code) WHERE is_active', ddl)

    def test_old_global_active_brief_index_is_dropped_not_recreated(self):
        # Старый индекс «один действующий бриф на всё» запрещал бы второй
        # действующий бриф на другом кабинете.
        ddl = ' '.join(schema._STATEMENTS)
        self.assertNotIn('uniq_olx_ads_brief_active ', ddl)
        migrations = ' '.join(schema._MIGRATIONS)
        self.assertIn('DROP INDEX IF EXISTS uniq_olx_ads_brief_active', migrations)

    def test_brief_enabled_before_cabinets_is_carried_to_all_cabinets(self):
        from olx_amo import cabinets

        backfill = [m for m in schema._MIGRATIONS if 'olx_ads_brief_cabinets' in m]
        self.assertEqual(1, len(backfill))
        self.assertIn('WHERE b.is_active', backfill[0])
        self.assertIn('NOT EXISTS', backfill[0])
        for cab in cabinets.CABINETS:
            self.assertIn("'%s'" % cab.code, backfill[0])

    def test_brief_migrations_run_before_the_new_cabinet_index(self):
        cursor = _Cursor()
        schema.init_olx_ads_schema(cursor)
        joined = [s for s in cursor.statements]
        drop = next(i for i, s in enumerate(joined) if 'DROP INDEX IF EXISTS uniq_olx_ads_brief_active' in s)
        backfill = next(i for i, s in enumerate(joined) if 'INSERT INTO olx_ads_brief_cabinets' in s)
        new_index = next(i for i, s in enumerate(joined) if 'uniq_olx_ads_brief_cabinet_active' in s)
        self.assertLess(drop, new_index)
        self.assertLess(backfill, new_index)

    def test_schema_is_wired_into_database_init(self):
        database = _read('database.py')
        self.assertIn('self._init_olx_ads_schema_tx(cursor)', database)
        self.assertIn('def _init_olx_ads_schema_tx', database)
        self.assertIn('SAVEPOINT olx_ads_schema', database)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP и подключение
# ─────────────────────────────────────────────────────────────────────────────

class RoutesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.routes = _read('olx_ads', 'routes.py')

    def _decorator_of(self, handler):
        # Аргументы декоратора читаем до конца СТРОКИ, а не до первой «)»: у
        # ручек с методом внутри стоит кортеж `methods=('POST',)`.
        match = re.search(r"@section_route\(([^\n]*)\)\s*\n\s*def %s\(" % handler, self.routes)
        self.assertIsNotNone(match, handler)
        return match.group(1)

    def test_writes_to_olx_require_apply_right(self):
        for handler in ('olx_ads_apply', 'olx_ads_rollback'):
            self.assertIn('apply=True', self._decorator_of(handler), handler)

    def test_content_work_requires_content_right(self):
        for handler in ('olx_ads_generate', 'olx_ads_brief_create',
                        'olx_ads_brief_update', 'olx_ads_brief_activate',
                        'olx_ads_draft_save', 'olx_ads_draft_discard'):
            self.assertIn('content=True', self._decorator_of(handler), handler)

    def test_reading_needs_no_extra_right(self):
        for handler in ('olx_ads_ping', 'olx_ads_list', 'olx_ads_card',
                        'olx_ads_history', 'olx_ads_check'):
            decorator = self._decorator_of(handler)
            self.assertNotIn('apply=True', decorator, handler)

    def test_blueprint_is_registered(self):
        app = _read('bot_schedule2.py')
        self.assertIn('from olx_ads.routes import build_olx_ads_blueprint', app)
        self.assertIn('build_olx_ads_blueprint(', app)


class JsonShapeTests(unittest.TestCase):
    """Что уходит в браузер: время без сдвига и без лишнего веса."""

    def test_datetimes_leave_as_isoformat_without_zone(self):
        # Flask без своего провайдера написал бы «… GMT», и браузер сдвинул бы
        # настенные часы Алматы на пять часов.
        from datetime import date, datetime

        from olx_ads import routes

        out = routes._plain({'at': datetime(2026, 9, 14, 10, 58),
                             'days': [date(2026, 9, 1)], 'n': 1, 'none': None})
        self.assertEqual({'at': '2026-09-14T10:58:00', 'days': ['2026-09-01'],
                          'n': 1, 'none': None}, out)

    def test_every_response_goes_through_the_wrapper(self):
        source = _read('olx_ads', 'routes.py')
        self.assertIn('jsonify as _flask_jsonify', source)
        self.assertIn('def jsonify(', source)
        # Ни одна ручка не зовёт flask.jsonify в обход обёртки.
        self.assertEqual(1, source.count('_flask_jsonify(_plain('))

    def test_list_does_not_ship_the_raw_olx_object(self):
        from olx_ads import queries as ads_queries

        self.assertNotIn('a.*', ads_queries._LIST_SQL)
        self.assertNotIn('a.payload', ads_queries._LIST_SQL)


class BriefPerCabinetGenerationTests(unittest.TestCase):
    """ИИ берёт бриф КАБИНЕТА объявления; кабинет без брифа не роняет пачку.

    Решение владельца 14.09.2026: разный бриф на разные кабинеты. База здесь
    подменена на уровне функций queries — проверяется поведение сервиса, а не SQL.
    """

    def setUp(self):
        from contextlib import contextmanager

        self.adverts = {
            ('tenge', '1'): {'cabinet_code': 'tenge', 'advert_id': '1',
                             'category_id': 1812, 'city_name': 'Костанай'},
            ('adal', '2'): {'cabinet_code': 'adal', 'advert_id': '2',
                            'category_id': 1812, 'city_name': 'Алматы'},
            ('jana', '3'): {'cabinet_code': 'jana', 'advert_id': '3',
                            'category_id': 1812, 'city_name': 'Шымкент'},
        }
        self.briefs = {
            'tenge': {'id': 7, 'title': 'Акция Тенге', 'offer': 'Комиссия 0 процентов'},
            'adal': {'id': 5, 'title': 'Сентябрь', 'offer': 'Бонус пятнадцать тысяч'},
        }
        self.drafts = []

        class _Db(object):
            @contextmanager
            def _get_cursor(inner):
                yield object()

        self.db = _Db()
        self._saved = {name: getattr(service.queries, name)
                       for name in ('get_advert', 'active_briefs_for', 'upsert_draft')}
        service.queries.get_advert = (
            lambda cursor, cab, adv: self.adverts.get((cab, str(adv))))
        service.queries.active_briefs_for = (
            lambda cursor, codes: {c: self.briefs[c] for c in codes if c in self.briefs})

        def _upsert(cursor, cab, adv, title, description, **kwargs):
            self.drafts.append({'cabinet': cab, 'advert_id': adv,
                                'brief_id': kwargs.get('brief_id'),
                                'description': description})
            return len(self.drafts)

        service.queries.upsert_draft = _upsert

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(service.queries, name, fn)

    @staticmethod
    def _fake_ai(system, user, **kwargs):
        # В описание кладём оффер, пришедший в промпт: по нему видно, из какого
        # брифа писали текст.
        offer = next((o for o in ('Комиссия 0 процентов', 'Бонус пятнадцать тысяч')
                      if o in user), 'без брифа')
        return {'text': 'ЗАГОЛОВОК: Работа в такси без ИП и отчётности\nОПИСАНИЕ:\n'
                        '<p>%s. %s</p>' % (offer, _LONG_BODY), 'model': 'fake'}

    def _targets(self, *keys):
        return [{'cabinet': cab, 'advert_id': adv} for cab, adv in keys]

    def test_each_advert_takes_the_brief_of_its_own_cabinet(self):
        result = service.generate_drafts(
            self.db, self._targets(('tenge', '1'), ('adal', '2')),
            generate_fn=self._fake_ai)
        self.assertEqual(2, len(result['made']))
        by_cabinet = {d['cabinet']: d for d in self.drafts}
        self.assertEqual(7, by_cabinet['tenge']['brief_id'])
        self.assertIn('Комиссия 0 процентов', by_cabinet['tenge']['description'])
        self.assertEqual(5, by_cabinet['adal']['brief_id'])
        self.assertIn('Бонус пятнадцать тысяч', by_cabinet['adal']['description'])
        self.assertEqual({5, 7}, {b['id'] for b in result['briefs']})

    def test_cabinet_without_brief_is_skipped_not_fatal(self):
        result = service.generate_drafts(
            self.db, self._targets(('tenge', '1'), ('jana', '3')),
            generate_fn=self._fake_ai)
        self.assertEqual(['tenge'], [m['cabinet'] for m in result['made']])
        self.assertEqual(1, len(result['failed']))
        self.assertEqual('jana', result['failed'][0]['cabinet'])
        self.assertIn('нет действующего брифа', result['failed'][0]['error'])

    def test_no_brief_for_any_selected_cabinet_is_a_clear_refusal(self):
        with self.assertRaises(service.AdsError) as ctx:
            service.generate_drafts(self.db, self._targets(('jana', '3')),
                                    generate_fn=self._fake_ai)
        self.assertEqual('no_brief', ctx.exception.code)
        self.assertEqual([], self.drafts)


class BriefQueriesInvariantsTests(unittest.TestCase):
    """Порядок операций, без которого уникальный индекс по кабинету отвергнет запись."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read('olx_ads', 'queries.py')

    def _function(self, name):
        return self.source.split('def %s(' % name, 1)[1].split('\ndef ', 1)[0]

    def test_activation_claims_cabinets_before_lighting_its_own(self):
        body = self._function('activate_brief')
        self.assertLess(body.index('_claim_cabinets('),
                        body.index('UPDATE olx_ads_brief_cabinets SET is_active = TRUE'))

    def test_saving_cabinets_of_an_active_brief_claims_before_insert(self):
        body = self._function('set_brief_cabinets')
        self.assertLess(body.index('_claim_cabinets('),
                        body.index('INSERT INTO olx_ads_brief_cabinets'))

    def test_claim_removes_cabinet_from_the_old_brief_and_turns_off_empty_ones(self):
        body = self._function('_claim_cabinets')
        self.assertIn('DELETE FROM olx_ads_brief_cabinets', body)
        self.assertIn('SET is_active = FALSE', body)
        self.assertIn('NOT EXISTS', body)
        # Выключенные брифы — заготовки, их выбор не трогаем.
        self.assertIn('WHERE is_active AND brief_id <> %s', body)

    def test_activation_without_cabinets_is_refused(self):
        body = self._function('activate_brief')
        self.assertIn('return None', body)


class BriefRoutesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.routes = _read('olx_ads', 'routes.py')

    def test_deactivation_needs_the_content_right(self):
        match = re.search(r"@section_route\(([^\n]*)\)\s*\n\s*def olx_ads_brief_deactivate\(",
                          self.routes)
        self.assertIsNotNone(match)
        self.assertIn('content=True', match.group(1))

    def test_brief_cannot_be_created_without_cabinets(self):
        create = self.routes.split('def olx_ads_brief_create(', 1)[1].split('@section_route', 1)[0]
        self.assertIn('_cabinet_codes(', create)
        self.assertIn('Выберите хотя бы один кабинет', self.routes)
        self.assertIn('Неизвестные кабинеты', self.routes)

    def test_ping_tells_which_brief_each_cabinet_has(self):
        self.assertIn("'briefs_by_cabinet'", self.routes)
        self.assertNotIn("'active_brief'", self.routes)

    def test_switching_cabinets_is_reported_back(self):
        for handler in ('olx_ads_brief_create', 'olx_ads_brief_update', 'olx_ads_brief_activate'):
            body = self.routes.split('def %s(' % handler, 1)[1].split('@section_route', 1)[0]
            self.assertIn("'moved'", body, handler)


class ClientTests(unittest.TestCase):

    def test_client_can_list_and_update_adverts(self):
        client = _read('olx_amo', 'olx_client.py')
        for method in ('def adverts(', 'def all_adverts(', 'def update_advert(',
                       'def moderation_reason('):
            self.assertIn(method, client)
        self.assertIn("'PUT', '/adverts/%s'", client)


class FrontendWiringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _read('src', 'App.jsx')
        cls.view = _read('src', 'components', 'olx', 'OlxAdsView.jsx')

    def test_menu_item_is_declared_once(self):
        self.assertEqual(1, self.app.count("handleSidebarViewNavigation(e, 'olx_ads')"))

    def test_section_is_lazy_loaded_and_gated(self):
        self.assertIn("import('./components/olx/OlxAdsView')", self.app)
        self.assertIn('view === "olx_ads" && canAccessOlxAdsSection', self.app)
        self.assertIn("if (view === 'olx_ads' && canAccessOlxAdsSection) return;", self.app)

    def test_leads_counters_are_untouched(self):
        # Закреплённые числа сайдбара «Лидов OLX» не должны сдвинуться.
        self.assertEqual(1, self.app.count("handleSidebarViewNavigation(e, 'olx_leads')"))

    def test_view_talks_only_to_its_api(self):
        self.assertIn('/api/olx_ads/', self.view)
        self.assertNotIn('/api/olx_amo/', self.view)

    def test_view_hides_publish_without_the_right(self):
        self.assertIn('caps.can_apply &&', self.view)
        self.assertIn('caps.can_write_content &&', self.view)


class BriefFrontendTests(unittest.TestCase):
    """Экран брифов по кабинетам: читает карту, выбирает кабинеты, умеет выключать."""

    @classmethod
    def setUpClass(cls):
        cls.view = _read('src', 'components', 'olx', 'OlxAdsView.jsx')

    def test_view_reads_the_brief_of_each_cabinet(self):
        self.assertIn('briefs_by_cabinet', self.view)
        self.assertNotIn('active_brief', self.view)

    def test_brief_form_picks_cabinets_and_sends_them(self):
        self.assertIn('data-cabinet=', self.view)
        self.assertIn('data-cabinet-all', self.view)
        self.assertIn('cabinets:', self.view)

    def test_brief_can_be_turned_off(self):
        self.assertIn('/deactivate', self.view)

    def test_cabinet_switch_is_announced(self):
        # Смена брифа у чужого кабинета не должна пройти молча.
        self.assertIn('data?.moved', self.view)
        self.assertIn('data-brief-conflict', self.view)

    def test_generation_warns_about_cabinets_without_brief(self):
        self.assertIn('data-no-brief-note', self.view)


if __name__ == '__main__':
    unittest.main()
