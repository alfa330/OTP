# -*- coding: utf-8 -*-
"""Заявки Syntony в отбивке «Лиды по источникам» (19.11.2026).

Три слоя:
  * клиент API (syntony_leads/client.py): страницы, фильтры, ошибки без ключа,
    разбор заявки и то, что НЕ хранится;
  * правила (amocrm/leads.py): заявка Syntony считается теми же правилами, что и
    сделка amoCRM, один лид в обеих CRM — один раз;
  * бот (bot_schedule2.py): выгрузка (полная/изменённое, пауза, журнал) и текст
    отбивки из обеих CRM — функции бота исполняются на подставной базе.

Времена и кампании — как в настоящих выгрузках 06–19.11.2026; люди и номера
выдуманы, телефонов в тестах нет вовсе.
"""

import ast
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from amocrm import leads as amo_leads  # noqa: E402
from syntony_leads import client  # noqa: E402
from tests import source_cache  # noqa: E402

TZ = amo_leads.TZ
KEY = 'slk_test_0123456789abcdef'


def at(day, hh, mm=0, ss=0):
    return datetime(2026, 11, day, hh, mm, ss, tzinfo=TZ)


# ── клиент API ────────────────────────────────────────────────────────────────

class FakeResponse(object):
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'params': dict(params or {}), 'headers': dict(headers or {}),
                           'timeout': timeout})
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def page(items, next_offset=None, total=None):
    return FakeResponse(200, {'apiVersion': 1, 'data': items, 'nextOffset': next_offset,
                              'total': total if total is not None else len(items)})


def api_lead(lead_id='L1', created='2026-11-19T05:00:00Z', source='facebook', tags=(), utm=None,
             campaign=None, **extra):
    item = {
        'id': lead_id, 'createdAt': created, 'updatedAt': created,
        'park': {'id': 'P', 'name': 'iTaxi'},
        'pipeline': {'id': 'F', 'name': 'Продажи', 'kind': 'sales'},
        'stage': {'id': 'S', 'name': 'Новая заявка', 'code': 'new', 'stageType': 'new'},
        'name': 'Айгуль', 'phoneMasked': '+7 (***) ***-**-67',
        'source': source, 'createdByChannel': 'lead_ads', 'createdBy': 'client',
        'tags': [{'id': 'T%d' % i, 'name': name} for i, name in enumerate(tags)],
        'attribution': ({'utmSource': utm, 'utmMedium': 'lead_ads', 'utmCampaign': campaign,
                         'form': {'id': 'F1', 'name': 'Форма'}} if (utm or campaign) else None),
    }
    item.update(extra)
    return item


class ConfigTests(unittest.TestCase):
    def test_without_a_key_there_is_nothing_to_fetch_with(self):
        with mock.patch.dict(os.environ, {client.ENV_KEY: ''}):
            self.assertIsNone(client.config())
            self.assertFalse(client.is_configured())

    def test_default_address_is_the_documented_one(self):
        with mock.patch.dict(os.environ, {client.ENV_KEY: KEY, client.ENV_URL: ''}):
            self.assertEqual((client.DEFAULT_URL, KEY), client.config())

    def test_key_never_goes_to_a_foreign_or_plain_address(self):
        for url in ('http://app.syntony.kz/api/leads-api/v1/leads',
                    'https://syntony.kz.evil.example/leads',
                    'https://evilsyntony.kz/leads'):
            with mock.patch.dict(os.environ, {client.ENV_KEY: KEY, client.ENV_URL: url}):
                self.assertIsNone(client.config(), url)


class FetchTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {client.ENV_KEY: KEY, client.ENV_URL: ''})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pauses = []

    def fetch(self, session, **kwargs):
        return client.fetch(session=session, sleep=self.pauses.append, **kwargs)

    def test_pages_follow_next_offset_until_null(self):
        session = FakeSession([page([{'id': 'a'}], next_offset=500),
                               page([{'id': 'b'}], next_offset=None)])
        items = self.fetch(session, created_from=date(2026, 11, 3))

        self.assertEqual(['a', 'b'], [i['id'] for i in items])
        self.assertEqual([0, 500], [c['params']['offset'] for c in session.calls])
        self.assertEqual({'2026-11-03'}, {c['params']['createdFrom'] for c in session.calls})
        self.assertEqual({500}, {c['params']['limit'] for c in session.calls})
        # Предел API — 60 запросов в минуту: между страницами пауза, после последней — нет.
        self.assertEqual([client.PAGE_PAUSE_SECONDS], self.pauses)

    def test_key_goes_only_in_the_header(self):
        session = FakeSession([page([])])
        self.fetch(session, created_from=date(2026, 11, 3))
        call = session.calls[0]
        self.assertEqual(KEY, call['headers']['X-Api-Key'])
        self.assertNotIn(KEY, call['url'])
        self.assertNotIn(KEY, repr(call['params']))

    def test_updated_since_is_sent_in_utc(self):
        session = FakeSession([page([])])
        self.fetch(session, updated_since=at(19, 12, 30))
        self.assertEqual('2026-11-19T07:30:00Z', session.calls[0]['params']['updatedSince'])
        self.assertNotIn('createdFrom', session.calls[0]['params'])

    def test_exactly_one_filter(self):
        with self.assertRaises(ValueError):
            self.fetch(FakeSession([]))
        with self.assertRaises(ValueError):
            self.fetch(FakeSession([]), created_from=date(2026, 11, 3), updated_since=at(19, 1))

    def test_errors_say_what_happened_without_the_key(self):
        cases = [
            (FakeResponse(401, {'error': 'invalid_api_key'}), '401 invalid_api_key'),
            (FakeResponse(429, None), '429'),
            (FakeResponse(200, ValueError('not json')), 'не JSON'),
            (FakeResponse(200, {'data': 'нет'}), 'нет списка'),
        ]
        for response, expected in cases:
            with self.assertRaises(client.SyntonyLeadsError) as caught:
                self.fetch(FakeSession([response]), created_from=date(2026, 11, 3))
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))

    def test_dropped_connection_is_retried_once_then_reported(self):
        drop = requests.exceptions.ConnectionError('reset by %s' % KEY)
        with mock.patch.object(client.time, 'sleep'):
            items = self.fetch(FakeSession([drop, page([{'id': 'a'}])]),
                               created_from=date(2026, 11, 3))
            self.assertEqual(['a'], [i['id'] for i in items])
            with self.assertRaises(client.SyntonyLeadsError) as caught:
                self.fetch(FakeSession([drop, drop]), created_from=date(2026, 11, 3))
        self.assertNotIn(KEY, str(caught.exception))

    def test_broken_pagination_does_not_loop_forever(self):
        session = FakeSession([page([{'id': 'a'}], next_offset=0)])
        with self.assertRaises(client.SyntonyLeadsError):
            self.fetch(session, created_from=date(2026, 11, 3))

    def test_without_a_key_nothing_is_requested(self):
        with mock.patch.dict(os.environ, {client.ENV_KEY: ''}):
            session = FakeSession([])
            with self.assertRaises(client.SyntonyLeadsError):
                self.fetch(session, created_from=date(2026, 11, 3))
            self.assertEqual([], session.calls)


class MapLeadTests(unittest.TestCase):
    def test_lead_form_is_kept_with_what_the_report_needs(self):
        row = client.map_lead(api_lead(tags=('fb_forma_itaxi_obshchaya', 'call_itaxi_wb'),
                                       utm='facebook', campaign='iTaxi/16.09.2026'))
        self.assertEqual('L1', row['lead_id'])
        self.assertEqual(datetime(2026, 11, 19, 5, 0, tzinfo=timezone.utc), row['created_at'])
        self.assertEqual('fb_forma_itaxi_obshchaya, call_itaxi_wb', row['tags'], 'порядок API')
        self.assertEqual('facebook', row['utm_source'])
        self.assertEqual('iTaxi/16.09.2026', row['utm_campaign'])
        self.assertEqual('sales', row['pipeline_kind'])
        self.assertEqual('iTaxi', row['park'])
        self.assertEqual('new', row['stage_type'])

    def test_name_and_phone_are_not_stored(self):
        row = client.map_lead(api_lead(utm='facebook'))
        for value in row.values():
            self.assertNotIn('Айгуль', str(value))
            self.assertNotIn('67', str(value) if isinstance(value, str) else '')
        self.assertFalse({'name', 'phone', 'phoneMasked', 'phone_masked'} & set(row))

    def test_olx_is_not_taken_from_syntony(self):
        """Лиды OLX отбивка считает по журналу робота: Syntony заводит их на каждый чат."""
        self.assertIsNone(client.map_lead(api_lead(source='olx', tags=('olx',), utm='olx')))

    def test_lead_without_tag_and_mark_is_not_stored(self):
        """Чаты ИИ-агента и рассылки по базе отбивке посчитать нечем."""
        self.assertIsNone(client.map_lead(api_lead(source='whatsapp')))
        self.assertIsNone(client.map_lead(api_lead(source='other')))

    def test_lead_form_without_a_mark_is_still_stored(self):
        self.assertIsNotNone(client.map_lead(api_lead(source='tiktok')))

    def test_garbage_does_not_crash(self):
        for item in (None, {}, {'id': 'x'}, {'id': 'x', 'createdAt': 'вчера'}, 'строка'):
            self.assertIsNone(client.map_lead(item))

    def test_repeated_ids_are_written_once(self):
        rows = client.map_leads([api_lead(utm='facebook'), api_lead(utm='facebook'),
                                 api_lead(source='olx')])
        self.assertEqual(['L1'], [r['lead_id'] for r in rows])


# ── правила: заявка Syntony и склейка с amoCRM ────────────────────────────────

def amo(when, tags='', utm='', campaign=''):
    return {'created_at': when, 'tags': tags, 'utm_source': utm, 'utm_campaign': campaign}


def syn(when, tags='', utm='', campaign='', source='facebook'):
    return amo_leads.syntony_row({'created_at': when, 'tags': tags, 'utm_source': utm,
                                  'utm_campaign': campaign, 'source': source})


DAY = (at(12, 0), at(13, 0))


def counts(amo_rows, syntony_rows, window=DAY, extra=()):
    return amo_leads.combined_counts(amo_rows, syntony_rows, window[0], window[1], extra_rows=extra)


class SyntonyRowTests(unittest.TestCase):
    def test_only_the_tag_the_lead_was_created_with_counts(self):
        """Проверочный звонок 19.11 собрал на одной заявке 15 тегов линий парка."""
        row = syn(at(19, 7, 16), tags='call_itaxi_market3, call_itaxi_youtube, call_itaxi_olx',
                  source='phone')
        self.assertEqual('call_itaxi_market3', row['tags'])
        self.assertEqual(1, amo_leads.count_by_source([row])['Общее'])

    def test_rental_campaign_without_a_rental_tag_is_cut_like_in_amocrm(self):
        rental = syn(at(12, 10), utm='facebook', campaign='iTaxi / Аренда авто / Видео / 06.04.2026')
        tagged = syn(at(12, 10), tags='fb_forma_itaxi_arenda', utm='facebook')
        plain = syn(at(12, 10), utm='facebook', campaign='iTaxi/16.09.2026')
        self.assertEqual(0, amo_leads.count_by_source([rental])['FB'])
        self.assertEqual(0, amo_leads.count_by_source([tagged])['FB'])
        self.assertEqual(1, amo_leads.count_by_source([plain])['FB'])
        # Так же, как сделка той же кампании в amoCRM:
        self.assertEqual(0, amo_leads.count_by_source(
            [amo(at(12, 10), tags='forma_itaxi_arenda', utm='fb')])['FB'])

    def test_lead_form_without_a_mark_counts_by_its_source(self):
        self.assertEqual(1, amo_leads.count_by_source([syn(at(12, 10), source='tiktok')])['TikTok'])
        self.assertEqual(1, amo_leads.count_by_source([syn(at(12, 10), source='facebook')])['FB'])

    def test_site_form_and_call_count_by_the_same_rules_as_in_amocrm(self):
        form = syn(at(18, 9), tags='forma_jana_sait', utm='google', source='website')
        organic = syn(at(18, 9), tags='forma_jana_sait', source='website')
        call = syn(at(19, 9), tags='call_jana_wb1', source='phone')
        olx_call = syn(at(19, 9), tags='call_tenge_olx', source='phone')
        self.assertEqual(1, amo_leads.count_by_source([form])['Google'])
        self.assertEqual(1, amo_leads.count_by_source([organic])['SEO'])
        self.assertEqual(1, amo_leads.count_by_source([call])['Звонки'])
        self.assertEqual(1, amo_leads.count_by_source([olx_call])['OLX'])


class TwinTests(unittest.TestCase):
    def test_lead_form_in_both_crms_within_seconds_counts_once(self):
        """12–17.11 лид-формы FB приходили в обе CRM: 207 пар из 211 — в 10 с."""
        result = counts([amo(at(12, 10, 0, 0), tags='forma_itaxi_общая', utm='fb')],
                        [syn(at(12, 10, 0, 4), utm='facebook', campaign='iTaxi/16.09.2026')])
        self.assertEqual(1, result['FB'])
        self.assertEqual(1, result['Общее'])

    def test_different_leads_minutes_apart_both_count(self):
        """В дни переключения форм сайтов (18.11) лиды не дублировались."""
        result = counts([amo(at(12, 10, 0), tags='forma_jana_sait', utm='google')],
                        [syn(at(12, 10, 3), tags='forma_dostavka_sait', utm='google', source='website')])
        self.assertEqual(2, result['Google'])

    def test_call_reaches_syntony_minutes_later_and_still_counts_once(self):
        """Звонок Syntony заводит после amoCRM — на 19.11 до 4 минут, тег линии тот же."""
        result = counts([amo(at(12, 9, 36, 56), tags='call_itaxi_2gis_wb')],
                        [syn(at(12, 9, 40, 30), tags='call_itaxi_2gis_wb', source='phone')])
        self.assertEqual(1, result['2GIS'])

    def test_minutes_apart_without_a_common_key_are_two_leads(self):
        result = counts([amo(at(12, 9, 36), tags='call_itaxi_wb')],
                        [syn(at(12, 9, 40), tags='call_jana_wb1', source='phone')])
        self.assertEqual(2, result['Звонки'])

    def test_same_campaign_lead_form_a_minute_apart_counts_once(self):
        result = counts([amo(at(12, 11, 0, 0), tags='forma_itaxi_общая', utm='fb',
                             campaign='iTaxi/16.09.2026')],
                        [syn(at(12, 11, 1, 10), utm='facebook', campaign='iTaxi/16.09.2026')])
        self.assertEqual(1, result['FB'])

    def test_twin_must_count_in_the_same_row(self):
        """FB-заявка и звонок в одну секунду — разные лиды."""
        result = counts([amo(at(12, 10), tags='call_jana_wb1')],
                        [syn(at(12, 10), utm='facebook', campaign='iTaxi/16.09.2026')])
        self.assertEqual(1, result['FB'])
        self.assertEqual(1, result['Звонки'])

    def test_one_deal_pairs_with_one_lead_only(self):
        result = counts([amo(at(12, 10, 0, 0), tags='forma_itaxi_общая', utm='fb')],
                        [syn(at(12, 10, 0, 2), utm='facebook'),
                         syn(at(12, 10, 0, 5), utm='facebook')])
        self.assertEqual(2, result['FB'])

    def test_closest_pairs_win(self):
        """Склеиваются ближайшие, а не первые по списку: иначе заявка в 00:01 осталась
        бы без пары — сделку в 00:00 уже заняла бы заявка в 00:09."""
        result = counts([amo(at(12, 10, 0, 0), utm='tiktok'), amo(at(12, 10, 0, 18), utm='tiktok')],
                        [syn(at(12, 10, 0, 9), utm='tiktok', source='tiktok'),
                         syn(at(12, 10, 0, 1), utm='tiktok', source='tiktok')])
        self.assertEqual(2, result['TikTok'])

    def test_twin_across_the_window_edge_counts_once_in_total(self):
        """Сделка в 23:59:58 вчера, заявка в 00:00:03 сегодня — один лид, во вчерашнем окне."""
        deal = amo(at(12, 23, 59, 58), tags='forma_itaxi_общая', utm='fb')
        lead = syn(at(13, 0, 0, 3), utm='facebook')
        yesterday = counts([deal], [lead], window=(at(12, 0), at(13, 0)))
        today = counts([deal], [lead], window=(at(13, 0), at(14, 0)))
        self.assertEqual(1, yesterday['FB'] + today['FB'])
        self.assertEqual(1, yesterday['FB'])

    def test_rows_outside_the_window_do_not_count(self):
        result = counts([amo(at(13, 0, 5), utm='fb')], [syn(at(11, 23, 55), utm='facebook')])
        self.assertEqual(0, result['Общее'])

    def test_rental_twins_count_nowhere(self):
        result = counts([amo(at(12, 10, 0, 0), tags='forma_itaxi_arenda', utm='fb')],
                        [syn(at(12, 10, 0, 3), utm='facebook',
                             campaign='iTaxi / Аренда авто / Видео / 06.04.2026')])
        self.assertEqual(0, result['Общее'])

    def test_olx_journal_rows_count_in_the_olx_line(self):
        extra = [{'tags': 'forma_olx_itaxi', 'utm_source': '', 'created_at': at(12, 15)},
                 {'tags': 'forma_arenda_olx', 'utm_source': '', 'created_at': at(12, 15)}]
        result = counts([], [], extra=extra)
        self.assertEqual(1, result['OLX'], 'кабинет аренды отсекается, как его сделки')

    def test_measured_day_from_the_parallel_week(self):
        """Срез 12.11: обе CRM получали одни и те же лид-формы, сумма не удваивается."""
        deals, leads = [], []
        for minute in range(0, 60, 6):
            deals.append(amo(at(12, 14, minute, 0), tags='forma_itaxi_общая', utm='fb',
                             campaign='iTaxi/16.09.2026'))
            leads.append(syn(at(12, 14, minute, 3), utm='facebook', campaign='iTaxi/16.09.2026'))
        leads.append(syn(at(12, 14, 33, 0), utm='facebook', campaign='Global/28.10.26'))
        deals.append(amo(at(12, 14, 44, 0), tags='forma_jana_obshchaya', utm='fb',
                         campaign='Жана/2% комиссия/23.07.2026'))
        result = counts(deals, leads)
        self.assertEqual(12, result['FB'], '10 общих + Global только в Syntony + Жана только в amoCRM')


class FooterTests(unittest.TestCase):
    def rows(self):
        return amo_leads.analyze({'FB': 10, 'Общее': 10}, {'FB': 10, 'Общее': 10})

    def test_footer_names_both_crms(self):
        text = amo_leads.render_alert_report(
            self.rows(), 'w', 'b', synced_at=at(19, 12, 10),
            more_sources=[{'name': 'Syntony', 'synced_at': at(19, 12, 11)}])
        self.assertIn('Данные обновлены: amoCRM 12:10, Syntony 12:11.', text)
        self.assertNotIn('⚠️', text)

    def test_syntony_failure_is_its_own_line(self):
        text = amo_leads.render_alert_report(
            self.rows(), 'w', 'b', synced_at=at(19, 12, 10),
            more_sources=[{'name': 'Syntony', 'synced_at': at(19, 9, 20),
                           'error': 'Syntony: HTTP 401 invalid_api_key', 'failed_at': at(19, 12, 11)}])
        self.assertIn('amoCRM 12:10, Syntony 09:20', text)
        self.assertIn('⚠️ Syntony: выгрузка (12:11) не удалась: Syntony: HTTP 401 invalid_api_key', text)

    def test_never_loaded_syntony_is_shown_as_missing(self):
        text = amo_leads.render_alert_report(
            self.rows(), 'w', 'b', synced_at=at(19, 12, 10),
            more_sources=[{'name': 'Syntony', 'synced_at': None}])
        self.assertIn('Syntony —', text)


# ── бот: выгрузка и текст отбивки на подставной базе ──────────────────────────

BOT_PATH = ROOT / 'bot_schedule2.py'
BOT_FUNCTIONS = ('syntony_leads_sync', '_syntony_leads_journal', '_leads_data_until',
                 '_olx_received_rows', '_leads_counts_between', '_amo_leads_alert_text',
                 '_amo_leads_journal')


class FakeCursor(object):
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, sql, params=None):
        if 'to_regclass' in sql:
            self.rows = [(True,)]
        elif 'FROM olx_journal' in sql:
            self.rows = [(code, when) for code, when in self.db.olx_received
                         if params['start'] <= when < params['end']]
        else:
            raise AssertionError('неожиданный запрос: %s' % sql)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return list(self.rows)


class FakeDb(object):
    def __init__(self, amo_rows=(), syntony_rows=(), olx_received=()):
        self.amo_rows = list(amo_rows)
        self.syntony_rows = list(syntony_rows)
        self.olx_received = list(olx_received)
        self.syncs = []
        self.amo_syncs = [{'id': 1, 'started_at': datetime.now(timezone.utc) - timedelta(minutes=1),
                           'finished_at': datetime.now(timezone.utc), 'status': 'ok', 'error': None}]
        self.upserted = []

    @contextmanager
    def _get_cursor(self):
        yield FakeCursor(self)

    def get_amo_leads_between(self, start, end):
        return [r for r in self.amo_rows if start <= r['created_at'] < end]

    def get_syntony_leads_between(self, start, end):
        return [r for r in self.syntony_rows if start <= r['created_at'] < end]

    def get_last_amo_lead_sync(self, status=None):
        rows = [s for s in self.amo_syncs if status is None or s['status'] == status]
        return rows[-1] if rows else None

    # Журнал выгрузок Syntony
    def start_syntony_lead_sync(self, mode):
        self.syncs.append({'id': len(self.syncs) + 1, 'mode': mode, 'status': 'running',
                           'started_at': datetime.now(timezone.utc), 'finished_at': None,
                           'error': None})
        return len(self.syncs)

    def finish_syntony_lead_sync(self, sync_id, leads_fetched=None, error=None):
        sync = self.syncs[sync_id - 1]
        sync.update(status='error' if error else 'ok', error=error, leads_fetched=leads_fetched,
                    finished_at=datetime.now(timezone.utc))

    def get_last_syntony_lead_sync(self, status=None, mode=None, finished=True):
        rows = [s for s in self.syncs
                if (not finished or s['finished_at'])
                and (status is None or s['status'] == status) and (mode is None or s['mode'] == mode)]
        return rows[-1] if rows else None

    def upsert_syntony_leads(self, rows):
        self.upserted.extend(rows)
        return len(rows)


class FakeSyntonyClient(object):
    def __init__(self, configured=True, items=(), fail=None):
        self.configured = configured
        self.items = list(items)
        self.fail = fail
        self.calls = []

    def is_configured(self):
        return self.configured

    def fetch(self, created_from=None, updated_since=None):
        self.calls.append({'created_from': created_from, 'updated_since': updated_since})
        if self.fail:
            raise RuntimeError(self.fail)
        return list(self.items)

    @staticmethod
    def map_leads(items):
        return client.map_leads(items)


class Log(object):
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *args, **kwargs: self.lines.append((level, message % args if args else message))


def bot_namespace(db, syntony_client):
    tree = source_cache.parse(source_cache.read(BOT_PATH))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in BOT_FUNCTIONS]
    missing = set(BOT_FUNCTIONS) - {n.name for n in nodes}
    if missing:
        raise AssertionError('в боте нет функций: %s' % ', '.join(sorted(missing)))
    namespace = {
        'db': db, 'amo_leads': amo_leads, 'syntony_leads_client': syntony_client,
        'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone, 'logging': Log(),
        'AMO_LEADS_STALE_MINUTES': 30, 'AMO_LEADS_RESYNC_PAUSE_MINUTES': 10,
        'SYNTONY_LEADS_FULL_EVERY': timedelta(hours=24),
        'SYNTONY_LEADS_OVERLAP': timedelta(minutes=10),
        'SYNTONY_LEADS_RESYNC_PAUSE': timedelta(minutes=2),
        'amo_leads_sync': lambda: (_ for _ in ()).throw(AssertionError('amoCRM свежая')),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), 'exec'), namespace)
    return namespace


class SyncTests(unittest.TestCase):
    def test_first_sync_takes_the_whole_window_then_only_changes(self):
        db = FakeDb()
        fake = FakeSyntonyClient(items=[api_lead(utm='facebook'), api_lead('L2', source='whatsapp')])
        ns = bot_namespace(db, fake)

        self.assertEqual(1, ns['syntony_leads_sync']())
        first = fake.calls[0]
        self.assertIsNotNone(first['created_from'])
        self.assertEqual((datetime.now(timezone.utc) - timedelta(days=amo_leads.SYNC_DAYS + 1)).date(),
                         first['created_from'])
        self.assertEqual(['full'], [s['mode'] for s in db.syncs])
        self.assertEqual(['L1'], [r['lead_id'] for r in db.upserted], 'чат без меток не хранится')

        # Сразу второй раз — пауза: API не дёргаем.
        self.assertEqual(0, ns['syntony_leads_sync']())
        self.assertEqual(1, len(fake.calls))

        # Через пять минут — только изменённое с прошлой удачной, с запасом.
        db.syncs[-1]['started_at'] -= timedelta(minutes=5)
        ns['syntony_leads_sync']()
        self.assertEqual('updated', db.syncs[-1]['mode'])
        self.assertEqual(db.syncs[0]['started_at'] - timedelta(minutes=10),
                         fake.calls[-1]['updated_since'])

    def test_full_window_again_after_a_day(self):
        db = FakeDb()
        fake = FakeSyntonyClient()
        ns = bot_namespace(db, fake)
        ns['syntony_leads_sync']()
        db.syncs[-1]['started_at'] -= timedelta(hours=25)
        ns['syntony_leads_sync']()
        self.assertEqual(['full', 'full'], [s['mode'] for s in db.syncs])

    def test_failure_is_journaled_and_raised(self):
        db = FakeDb()
        ns = bot_namespace(db, FakeSyntonyClient(fail='Syntony: HTTP 401 invalid_api_key'))
        with self.assertRaises(RuntimeError):
            ns['syntony_leads_sync']()
        self.assertEqual('error', db.syncs[-1]['status'])
        self.assertIn('401', db.syncs[-1]['error'])

    def test_without_a_key_nothing_happens(self):
        db = FakeDb()
        ns = bot_namespace(db, FakeSyntonyClient(configured=False))
        self.assertIsNone(ns['syntony_leads_sync']())
        self.assertEqual([], db.syncs)


class AlertTextTests(unittest.TestCase):
    """Текст отбивки из обеих CRM — на подставной базе, функциями самого бота."""

    NOW = at(19, 12, 0)

    def stored(self, when, **fields):
        row = {'created_at': when, 'tags': '', 'utm_source': None, 'utm_campaign': None,
               'source': 'facebook'}
        row.update(fields)
        return row

    def test_today_from_syntony_against_the_parallel_week(self):
        # База 12.11: 10 лид-форм в обеих CRM (близнецы) + 2 только в amoCRM.
        amo_rows, syn_rows = [], []
        for i in range(10):
            amo_rows.append(amo(at(12, 9, i * 3, 0), tags='forma_itaxi_общая', utm='fb'))
            syn_rows.append(self.stored(at(12, 9, i * 3, 4), utm_source='facebook'))
        amo_rows += [amo(at(12, 10, 40), tags='forma_jana_obshchaya', utm='fb'),
                     amo(at(12, 10, 50), tags='forma_jana_obshchaya', utm='fb')]
        # Сегодня: 12 лид-форм — все уже только в Syntony; 1 обращение OLX из журнала.
        syn_rows += [self.stored(at(19, 9, i * 3), utm_source='facebook') for i in range(12)]
        db = FakeDb(amo_rows=amo_rows, syntony_rows=syn_rows,
                    olx_received=[('itaxi', datetime(2026, 11, 19, 10, 0))])
        fake = FakeSyntonyClient()
        ns = bot_namespace(db, fake)

        text = ns['_amo_leads_alert_text'](now=self.NOW)

        fb = next(line for line in text.splitlines() if line.startswith('FB'))
        self.assertEqual(['FB', '12', '12', '+0%'], fb.split())
        olx = next(line for line in text.splitlines() if line.startswith('OLX'))
        self.assertEqual(['OLX', '1', '0', '—'], olx.split())
        self.assertIn('Syntony', text.splitlines()[-1])
        self.assertNotIn('Критично', text)
        self.assertEqual(1, len(fake.calls), 'перед ответом Syntony догружается')

    def test_syntony_failure_is_visible_but_the_report_still_goes(self):
        db = FakeDb(amo_rows=[amo(at(19, 9), tags='forma_itaxi_общая', utm='fb')])
        ns = bot_namespace(db, FakeSyntonyClient(fail='Syntony: HTTP 401 invalid_api_key'))
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertIn('⚠️ Syntony: выгрузка', text)
        self.assertIn('401', text)
        self.assertIn('Syntony —', text, 'удачной выгрузки Syntony не было')

    def test_without_syntony_the_report_is_as_before(self):
        db = FakeDb(amo_rows=[amo(at(19, 9), tags='forma_itaxi_общая', utm='fb')])
        ns = bot_namespace(db, FakeSyntonyClient(configured=False))
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertNotIn('Syntony', text)
        self.assertIn('Данные обновлены ', text)


class WiringTests(unittest.TestCase):
    def test_sync_job_is_scheduled_apart_from_amocrm(self):
        source = source_cache.read(BOT_PATH)
        anchor = source.index("id='syntony_leads_sync'")
        block = source[anchor - 400:anchor + 200]
        self.assertIn('syntony_leads_sync_job', block)
        self.assertIn("minute=20", block)
        self.assertIn('next_run_time=datetime.now(amo_leads.TZ) + timedelta(minutes=3)', block,
                      'после выкладки таблица заполняется сразу, а не к :20')

    def test_table_and_journal_are_created_with_the_leads_schema(self):
        schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
        start = schema.index('def _init_amo_leads_schema_tx')
        block = schema[start:schema.index('\n    def ', start + 10)]
        self.assertIn('CREATE TABLE IF NOT EXISTS syntony_leads', block)
        self.assertIn('CREATE TABLE IF NOT EXISTS syntony_lead_syncs', block)
        self.assertIn('idx_syntony_leads_created_at', block)
        for column in client.map_lead(api_lead(utm='facebook')):
            self.assertIn(column, block, column)


if __name__ == '__main__':
    unittest.main()
