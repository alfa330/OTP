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

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({'url': url, 'params': dict(params or {}), 'headers': dict(headers or {}),
                           'timeout': timeout, 'allow_redirects': allow_redirects})
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

    def test_crooked_key_is_refused_before_it_can_reach_an_error_text(self):
        """requests отвергает заголовок с переводом строки исключением, в тексте которого —
        сам ключ (опыт разбора 19.11)."""
        for bad in ('slk_live_SECRET0123\nabcdef', 'slk_live SECRET0123456', 'slk_ключ0123456789'):
            with mock.patch.dict(os.environ, {client.ENV_KEY: bad, client.ENV_URL: ''}):
                self.assertIsNone(client.config(), repr(bad))


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

    def test_redirect_is_not_followed_and_the_key_does_not_travel(self):
        """requests несёт свои заголовки и на чужой хост; ответ чужого хоста — не заявки."""
        session = FakeSession([FakeResponse(302, {'data': [], 'nextOffset': None})])
        with self.assertRaises(client.SyntonyLeadsError) as caught:
            self.fetch(session, created_from=date(2026, 11, 3))
        self.assertIn('302', str(caught.exception))
        self.assertIs(False, session.calls[0]['allow_redirects'])

    def test_any_request_failure_keeps_its_text_to_itself(self):
        leak = requests.exceptions.InvalidHeader("bad header value: 'X-Api-Key: %s'" % KEY)
        with self.assertRaises(client.SyntonyLeadsError) as caught:
            self.fetch(FakeSession([leak]), created_from=date(2026, 11, 3))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn('InvalidHeader', str(caught.exception))

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


def syn(when, tags='', utm='', campaign='', source='facebook', form=None):
    return amo_leads.syntony_row({'created_at': when, 'tags': tags, 'utm_source': utm,
                                  'utm_campaign': campaign, 'source': source, 'form_name': form})


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

    def test_department_is_left_out_whichever_way_it_is_spelled(self):
        """Таблица сайтов владельца 23.11: Департамент в Syntony — department_N, в amoCRM было
        departament_N; правила отсекают его во всех строках — и так же у заявок Syntony."""
        cases = [
            syn(at(21, 10), tags='department_1', utm='google', source='website',
                form='Форма на сайте yandextaxi.kz'),
            syn(at(21, 10), tags='department_5', utm='chatgpt.com', source='website',
                form='Форма на сайте taxopark.kz'),
            syn(at(21, 10), tags='Forma_Департамент_Такси', source='website'),
            syn(at(21, 10), tags='department_1, call_цр_wb', utm='google', source='website'),
            # с сайта Департамента, но с тегом формы — узнаётся по форме
            syn(at(21, 10), tags='forma_taxopark_sait', source='website', form='Форма на сайте taxopark.kz'),
            syn(at(21, 10), tags='forma_yandextaxi_sait', utm='google', source='website',
                form='Форма на сайте yandextaxi.kz'),
        ]
        for row in cases:
            self.assertEqual(0, amo_leads.count_by_source([row])['Общее'], row)
        # как сделка amoCRM того же сайта
        self.assertEqual(0, amo_leads.count_by_source(
            [amo(at(21, 10), tags='departament_1', utm='google')])['Общее'])

    def test_neighbour_sites_are_not_taken_for_the_department(self):
        """yandex-taxi.kz — сайт Адал, не Департамента; qazaq-taksopark.kz — не taxopark.kz."""
        adal = syn(at(21, 10), tags='forma_adal_sait', utm='youtube', source='website',
                   form='Форма на сайте адал')
        self.assertEqual(1, amo_leads.count_by_source([adal])['YouTube'])
        for form in ('Форма на сайте yandex-taxi.kz', 'Форма на сайте qazaq-taksopark.kz'):
            row = syn(at(21, 10), tags='forma_adal_sait', utm='google', source='website', form=form)
            self.assertEqual(1, amo_leads.count_by_source([row])['Google'], form)

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

    def test_calls_count_from_amocrm_only(self):
        """Решение владельца 20.11: звонки — как в amoCRM. Syntony заводит заявку на любой звонок
        с номера без открытой заявки (и от действующих водителей): 276 против 129 сделок amoCRM
        за 19–20.11. Звонковые заявки Syntony не считаются вовсе — ни с парой, ни без."""
        deals = [amo(at(12, 9, 36, 56), tags='call_itaxi_2gis_wb')]
        leads = [syn(at(12, 9, 40, 30), tags='call_itaxi_2gis_wb', source='phone'),
                 syn(at(12, 9, 50, 0), tags='call_цр_wb', source='phone')]
        result = amo_leads.combined_counts(deals, leads, *DAY, detail=True)
        self.assertEqual(1, result['total']['2GIS'])
        self.assertEqual(0, result['total']['Звонки'], 'линия ЦР WB есть только в Syntony')
        self.assertEqual((1, 0), (result['amo']['2GIS'], result['syntony']['2GIS']))
        self.assertEqual((1, 2), (result['amo_calls'], result['syntony_calls']))

    def test_same_campaign_is_not_a_twin_key(self):
        """По кампании в 10 минутах идут десятки разных лидов; близнецы лид-форм — в 10 с."""
        result = counts([amo(at(12, 11, 0, 0), tags='forma_itaxi_общая', utm='fb',
                             campaign='iTaxi/16.09.2026')],
                        [syn(at(12, 11, 1, 10), utm='facebook', campaign='iTaxi/16.09.2026')])
        self.assertEqual(2, result['FB'])

    # Склейка по тегу работает и для форм сайта; звонками её проверять нагляднее всего
    # (у них общий тег и задержка в минуты), поэтому здесь — прямо drop_twins.
    def test_call_does_not_steal_the_deal_of_another_line(self):
        """Опыт разбора 19.11: заявка звонка jana в 5 с от сделки itaxi не отнимает её у
        своей пары — сперва склеиваются заявки с общим тегом, потом остальное."""
        deals = [amo(at(19, 10, 0, 0), tags='call_jana_wb1'), amo(at(19, 10, 0, 25), tags='call_itaxi_wb')]
        leads = [syn(at(19, 10, 0, 30), tags='call_jana_wb1', source='phone'),
                 syn(at(19, 10, 1, 10), tags='call_itaxi_wb', source='phone')]
        self.assertEqual([], amo_leads.drop_twins(deals, leads))

    def test_two_calls_of_one_line_with_a_late_syntony_are_two(self):
        """Опыт разбора 19.11: ближайшая к первой заявке сделка — второго звонка; жадные
        «ближайшие первыми» оставили бы вторую заявку без пары."""
        base = at(20, 10, 0, 0)
        deals = [amo(base, tags='call_jana_wb1'), amo(base + timedelta(seconds=420), tags='call_jana_wb1')]
        leads = [syn(base + timedelta(seconds=240), tags='call_jana_wb1', source='phone'),
                 syn(base + timedelta(seconds=660), tags='call_jana_wb1', source='phone')]
        self.assertEqual([], amo_leads.drop_twins(deals, leads))

    def test_site_form_twin_minutes_later_counts_once(self):
        """Тот же тег формы в двух CRM в пределах 10 минут — один лид."""
        result = counts([amo(at(18, 15, 30, 0), tags='forma_jana_sait', utm='google')],
                        [syn(at(18, 15, 33, 0), tags='forma_jana_sait', utm='google', source='website')],
                        window=(at(18, 0), at(19, 0)))
        self.assertEqual(1, result['Google'])

    def test_call_and_site_form_seconds_apart_are_two_leads(self):
        result = counts([amo(at(12, 10, 0, 0), tags='call_jana_google')],
                        [syn(at(12, 10, 0, 3), tags='forma_jana_sait', utm='google', source='website')])
        self.assertEqual(2, result['Google'])

    def test_call_tag_added_to_an_open_chat_is_not_a_call(self):
        """Опыт разбора 19.11: чат ИИ-агента 15.11 получил тег линии от звонка 19.11.
        Звонок — сделкой amoCRM 19.11, а чат звонком за 15.11 не становится."""
        chat = syn(at(15, 14, 19), tags='call_цр_wb', source='whatsapp')
        self.assertEqual('', chat['tags'])
        deal = amo(at(19, 10, 0), tags='call_jana_wb1')
        self.assertEqual(0, counts([deal], [chat], window=(at(15, 0), at(16, 0)))['Звонки'])
        self.assertEqual(1, counts([deal], [chat], window=(at(19, 0), at(20, 0)))['Звонки'])
        mixed = syn(at(15, 14, 19), tags='call_цр_wb, whatsapp_itaxi_sait', source='whatsapp')
        self.assertEqual('whatsapp_itaxi_sait', mixed['tags'], 'метка сайта остаётся')

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


class BulkImportTests(unittest.TestCase):
    """Догрузка истории: подключая форму, Syntony заводит её старые лиды разом (19.11:
    «Жана» — 504 заявки за 14:53–14:55, «Честный» — 213 за 14:01–14:03; отбивка за 19.11
    показала FB 846 против 103). Обычный поток кампании — до 5 заявок за 10 минут."""

    WINDOW = (at(19, 0), at(20, 0))

    def burst(self, n, start, campaign='Жана/2% комиссия/23.07.2026', step=1):
        return [syn(start + timedelta(seconds=i * step), utm='facebook', campaign=campaign)
                for i in range(n)]

    def test_history_dumped_at_once_is_not_counted_but_the_new_flow_is(self):
        dump = self.burst(504, at(19, 14, 53), step=0.3)
        flow = [syn(at(19, 15, 26) + timedelta(minutes=40 * i), utm='facebook',
                    campaign='Жана/2% комиссия/23.07.2026') for i in range(12)]
        result = amo_leads.combined_counts([], dump + flow, *self.WINDOW, detail=True)
        self.assertEqual(12, result['total']['FB'])
        self.assertEqual(504, result['imported']['FB'])

    def test_a_busy_but_normal_campaign_is_not_mistaken_for_a_dump(self):
        """19 заявок в 20 минутах — вчетверо выше обычного максимума, но ещё не догрузка."""
        result = amo_leads.combined_counts([], self.burst(19, at(19, 10), step=60), *self.WINDOW,
                                           detail=True)
        self.assertEqual(19, result['total']['FB'])
        self.assertEqual(0, result['imported']['FB'])

    def test_different_campaigns_at_the_same_time_are_not_one_dump(self):
        leads = (self.burst(10, at(19, 10), campaign='iTaxi/16.09.2026', step=30)
                 + self.burst(10, at(19, 10), campaign='Global/28.10.26', step=30))
        self.assertEqual(20, counts([], leads, window=self.WINDOW)['FB'])

    def test_dump_across_midnight_is_cut_on_both_days(self):
        """Окно читается шире на 10 минут — догрузка видна целиком и у края суток."""
        dump = self.burst(40, at(19, 23, 58), step=6)
        margin = amo_leads.TWIN_MARGIN
        today = (at(20, 0), at(21, 0))
        rows = [r for r in dump if today[0] - margin <= r['created_at'] < today[1] + margin]
        result = amo_leads.combined_counts([], rows, *today, detail=True)
        self.assertEqual(0, result['total']['FB'])
        self.assertGreater(result['imported']['FB'], 0)

    def test_measured_dump_of_19_11_is_cut_to_the_real_day(self):
        """Срез 19.11: 719 догруженных FB-заявок и обычный поток — в счёт только поток."""
        dump = (self.burst(504, at(19, 14, 53), step=0.4)
                + self.burst(215, at(19, 14, 1), campaign='Честный/27.07.2026', step=0.6))
        flow = [syn(at(19, h, 7), utm='facebook', campaign='iTaxi/16.09.2026') for h in range(24)]
        result = amo_leads.combined_counts([], dump + flow, *self.WINDOW, detail=True)
        self.assertEqual((24, 719), (result['total']['FB'], result['imported']['FB']))


class SplitColumnTests(unittest.TestCase):
    """Столбец «amo+Syn» — откуда лиды (просьба владельца 20.11)."""

    def detail(self):
        deals = [amo(at(19, 9), tags='call_jana_wb1'), amo(at(19, 9, 30), tags='forma_jana_sait', utm='google')]
        leads = [syn(at(19, 10), utm='facebook', campaign='iTaxi/16.09.2026'),
                 syn(at(19, 11), tags='forma_jana_sait', utm='google', source='website')]
        extra = [{'tags': 'forma_olx_itaxi', 'utm_source': '', 'created_at': at(19, 12)}]
        return amo_leads.combined_counts(deals, leads, at(19, 0), at(20, 0), extra_rows=extra, detail=True)

    def test_parts_add_up_to_the_total_in_every_line(self):
        result = self.detail()
        for name in amo_leads.SOURCE_ORDER + ['Общее']:
            self.assertEqual(result['total'][name], result['amo'][name] + result['syntony'][name], name)
        self.assertEqual((1, 1), (result['amo']['Google'], result['syntony']['Google']))
        self.assertEqual((0, 1), (result['amo']['OLX'], result['syntony']['OLX']),
                         'обращения OLX ведёт Syntony — в разбивке они на её стороне')

    def render(self, split):
        rows = amo_leads.analyze(split['total'], split['total'])
        return amo_leads.render_alert_report(rows, 'w', 'b', split=split)

    def test_table_shows_where_the_leads_come_from_and_fits_a_phone(self):
        text = self.render(self.detail())
        table = text[text.index('<pre>') + 5:text.index('</pre>')].strip().splitlines()
        self.assertEqual(['Источник', 'лиды', 'база', 'Δ', 'amo+Syn'], table[0].split())
        self.assertEqual(['Google', '2', '2', '+0%', '1+1'], next(l for l in table if l.startswith('Google')).split())
        self.assertEqual(['FB', '1', '1', '+0%', '0+1'], next(l for l in table if l.startswith('FB')).split())
        self.assertEqual(['Общее', '5', '5', '+0%', '2+3'], table[-1].split())
        self.assertTrue(all(len(line) <= 40 for line in table), max(table, key=len))

    def test_dumped_history_and_silent_amocrm_calls_are_said_under_the_table(self):
        split = self.detail()
        split['imported'] = dict(split['imported'], FB=719, TikTok=1)
        split['amo_calls'], split['syntony_calls'] = 0, 36
        text = self.render(split)
        self.assertIn('Не в счёт — Syntony догрузила старые заявки: TikTok 1, FB 719.', text)
        self.assertIn('⚠️ Звонков в amoCRM нет, в Syntony — 36: «Звонки» считаются по amoCRM.', text)

    def test_nothing_extra_under_the_table_on_an_ordinary_day(self):
        text = self.render(self.detail())
        self.assertNotIn('Не в счёт', text)
        self.assertNotIn('Звонков в amoCRM нет', text)


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
#
# Часы заморожены: досинк amoCRM и пауза выгрузки Syntony зависят от «сейчас», и тест,
# написанный 19.11, не должен поменять смысл через 16 дней, когда окно уйдёт за глубину
# выгрузки.

BOT_PATH = ROOT / 'bot_schedule2.py'
BOT_FUNCTIONS = ('syntony_leads_sync', '_syntony_leads_journal', '_leads_data_until',
                 '_olx_received_rows', '_leads_counts_between', '_amo_leads_alert_text',
                 '_amo_leads_journal')
CLOCK = at(19, 12, 5)


def frozen_datetime(clock):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = clock['now']
            return moment.astimezone(tz) if tz else moment.astimezone(TZ).replace(tzinfo=None)
    return Frozen


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
    def __init__(self, amo_rows=(), syntony_rows=(), olx_received=(), clock=None, amo_synced_ago=1):
        self.clock = clock or {'now': CLOCK}
        self.amo_rows = list(amo_rows)
        self.syntony_rows = list(syntony_rows)
        self.olx_received = list(olx_received)
        self.syncs = []
        now = self.clock['now']
        self.amo_syncs = [] if amo_synced_ago is None else [
            {'id': 1, 'started_at': now - timedelta(minutes=amo_synced_ago),
             'finished_at': now - timedelta(minutes=amo_synced_ago) + timedelta(seconds=90),
             'status': 'ok', 'error': None}]
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
                           'started_at': self.clock['now'], 'finished_at': None, 'error': None})
        return len(self.syncs)

    def finish_syntony_lead_sync(self, sync_id, leads_fetched=None, error=None):
        sync = self.syncs[sync_id - 1]
        sync.update(status='error' if error else 'ok', error=error, leads_fetched=leads_fetched,
                    finished_at=self.clock['now'] + timedelta(seconds=20))

    def get_last_syntony_lead_sync(self, status=None, mode=None, finished=True):
        rows = [s for s in self.syncs
                if (not finished or s['finished_at'])
                and (status is None or s['status'] == status) and (mode is None or s['mode'] == mode)]
        return rows[-1] if rows else None

    def upsert_syntony_leads(self, rows):
        self.upserted.extend(rows)
        return len(rows)


class FakeSyntonyClient(object):
    SyntonyLeadsError = client.SyntonyLeadsError

    def __init__(self, configured=True, items=(), fail=None):
        self.configured = configured
        self.items = list(items)
        self.fail = fail
        self.calls = []

    def is_configured(self):
        return self.configured

    def fetch(self, created_from=None, updated_since=None):
        self.calls.append({'created_from': created_from, 'updated_since': updated_since})
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            raise client.SyntonyLeadsError(self.fail)
        return list(self.items)

    @staticmethod
    def map_leads(items):
        return client.map_leads(items)


class Log(object):
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *args, **kwargs: self.lines.append((level, message % args if args else message))


def bot_namespace(db, syntony_client, amo_sync=None):
    tree = source_cache.parse(source_cache.read(BOT_PATH))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in BOT_FUNCTIONS]
    missing = set(BOT_FUNCTIONS) - {n.name for n in nodes}
    if missing:
        raise AssertionError('в боте нет функций: %s' % ', '.join(sorted(missing)))

    def amo_sync_not_expected():
        raise AssertionError('досинк amoCRM здесь не нужен')

    namespace = {
        'db': db, 'amo_leads': amo_leads, 'syntony_leads_client': syntony_client,
        'datetime': frozen_datetime(db.clock), 'timedelta': timedelta, 'timezone': timezone,
        'logging': Log(),
        'AMO_LEADS_STALE_MINUTES': 30, 'AMO_LEADS_RESYNC_PAUSE_MINUTES': 10,
        'SYNTONY_LEADS_FULL_EVERY': timedelta(hours=24),
        'SYNTONY_LEADS_OVERLAP': timedelta(minutes=10),
        'SYNTONY_LEADS_RESYNC_PAUSE': timedelta(minutes=2),
        'amo_leads_sync': amo_sync or amo_sync_not_expected,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), 'exec'), namespace)
    return namespace


class SyncTests(unittest.TestCase):
    def test_first_sync_takes_the_whole_window_then_only_changes(self):
        db = FakeDb()
        fake = FakeSyntonyClient(items=[api_lead(utm='facebook'), api_lead('L2', source='whatsapp')])
        ns = bot_namespace(db, fake)

        self.assertEqual(1, ns['syntony_leads_sync']())
        self.assertEqual(date(2026, 11, 2), fake.calls[0]['created_from'],
                         '17 суток назад в UTC: сутки Алматы начинаются в 19:00 UTC накануне')
        self.assertEqual(['full'], [s['mode'] for s in db.syncs])
        self.assertEqual(['L1'], [r['lead_id'] for r in db.upserted], 'чат без меток не хранится')

        # Через минуту — пауза: API не дёргаем.
        db.clock['now'] += timedelta(minutes=1)
        self.assertEqual(0, ns['syntony_leads_sync']())
        self.assertEqual(1, len(fake.calls))

        # Ещё через пять минут — только изменённое с прошлой удачной, с запасом.
        db.clock['now'] += timedelta(minutes=5)
        ns['syntony_leads_sync']()
        self.assertEqual('updated', db.syncs[-1]['mode'])
        self.assertEqual(CLOCK - timedelta(minutes=10), fake.calls[-1]['updated_since'])

    def test_full_window_again_after_a_day(self):
        db = FakeDb()
        ns = bot_namespace(db, FakeSyntonyClient())
        ns['syntony_leads_sync']()
        db.clock['now'] += timedelta(hours=25)
        ns['syntony_leads_sync']()
        self.assertEqual(['full', 'full'], [s['mode'] for s in db.syncs])

    def test_after_a_failed_full_window_the_next_one_is_full_again(self):
        db = FakeDb()
        fake = FakeSyntonyClient(fail='Syntony не ответила вовремя')
        ns = bot_namespace(db, fake)
        with self.assertRaises(client.SyntonyLeadsError):
            ns['syntony_leads_sync']()
        db.clock['now'] += timedelta(minutes=3)
        fake.fail = None
        ns['syntony_leads_sync']()
        self.assertEqual([('full', 'error'), ('full', 'ok')], [(s['mode'], s['status']) for s in db.syncs])

    def test_failure_is_journaled_and_raised(self):
        db = FakeDb()
        ns = bot_namespace(db, FakeSyntonyClient(fail='Syntony: HTTP 401 invalid_api_key'))
        with self.assertRaises(client.SyntonyLeadsError):
            ns['syntony_leads_sync']()
        self.assertEqual('error', db.syncs[-1]['status'])
        self.assertEqual('Syntony: HTTP 401 invalid_api_key', db.syncs[-1]['error'])

    def test_foreign_exception_leaves_only_its_type_in_the_journal(self):
        """Текст ошибки уходит в подпись отбивки в группе — чужой текст туда не пускаем."""
        db = FakeDb()
        ns = bot_namespace(db, FakeSyntonyClient(fail=ValueError('X-Api-Key: %s' % KEY)))
        with self.assertRaises(ValueError):
            ns['syntony_leads_sync']()
        self.assertEqual('ValueError', db.syncs[-1]['error'])

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

    @staticmethod
    def line(text, source):
        return next(line for line in text.splitlines() if line.startswith(source)).split()

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

        self.assertEqual(['FB', '12', '12', '+0%', '0+12'], self.line(text, 'FB'))
        self.assertEqual(['OLX', '1', '0', '—', '0+1'], self.line(text, 'OLX'))
        self.assertIn('Данные обновлены: amoCRM 12:05, Syntony 12:05.', text)
        self.assertNotIn('Критично', text)
        self.assertEqual(1, len(fake.calls), 'перед ответом Syntony догружается')

    def test_syntony_failure_is_visible_but_the_report_still_goes(self):
        db = FakeDb(amo_rows=[amo(at(19, 9), tags='forma_itaxi_общая', utm='fb')])
        ns = bot_namespace(db, FakeSyntonyClient(fail='Syntony: HTTP 401 invalid_api_key'))
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertIn('⚠️ Syntony: выгрузка (12:05) не удалась: Syntony: HTTP 401 invalid_api_key', text)
        self.assertIn('Syntony —', text, 'удачной выгрузки Syntony не было')

    def test_without_syntony_the_report_is_as_before(self):
        db = FakeDb(amo_rows=[amo(at(19, 9), tags='forma_itaxi_общая', utm='fb')])
        ns = bot_namespace(db, FakeSyntonyClient(configured=False))
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertNotIn('Syntony', text)
        self.assertNotIn('amo+Syn', text, 'без Syntony столбец «откуда» не нужен')
        self.assertEqual(['FB', '1', '0', '—'], self.line(text, 'FB'))
        self.assertIn('Данные обновлены 12:05.', text)

    def test_dumped_history_is_left_out_and_said_under_the_table(self):
        """Отбивка 20.11 00:00 за 19.11 показала FB 846: Syntony догрузила 719 старых заявок."""
        dump = [self.stored(at(19, 14, 53) + timedelta(seconds=i), utm_source='facebook',
                            utm_campaign='Жана/2% комиссия/23.07.2026') for i in range(60)]
        fresh = [self.stored(at(19, 9, i * 5), utm_source='facebook', utm_campaign='iTaxi/16.09.2026')
                 for i in range(3)]
        db = FakeDb(syntony_rows=dump + fresh, clock={'now': at(20, 0, 5)})
        ns = bot_namespace(db, FakeSyntonyClient())
        text = ns['_amo_leads_alert_text'](now=at(20, 0, 0))
        self.assertEqual(['FB', '3', '0', '—', '0+3'], self.line(text, 'FB'))
        self.assertIn('Не в счёт — Syntony догрузила старые заявки: FB 60.', text)

    def test_syntony_calls_are_not_counted_and_a_silent_amocrm_is_flagged(self):
        calls = [self.stored(at(19, 9, i), tags='call_jana_wb1', source='phone') for i in range(4)]
        db = FakeDb(syntony_rows=calls)
        ns = bot_namespace(db, FakeSyntonyClient())
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertEqual(['Звонки', '0', '0', '—', '0+0'], self.line(text, 'Звонки'))
        self.assertIn('⚠️ Звонков в amoCRM нет, в Syntony — 4: «Звонки» считаются по amoCRM.', text)

    def test_empty_amocrm_window_is_not_a_reason_to_fail_when_syntony_has_leads(self):
        """Опыт разбора 19.11: лиды ушли в Syntony, в окне amoCRM пусто, досинк amoCRM упал —
        отбивка всё равно уходит по тому, что есть."""
        calls = []

        def amo_down():
            calls.append(1)
            raise RuntimeError('amoCRM: HTTP 401')

        db = FakeDb(syntony_rows=[self.stored(at(19, 9), utm_source='facebook')], amo_synced_ago=None)
        ns = bot_namespace(db, FakeSyntonyClient(), amo_sync=amo_down)
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertEqual(1, len(calls), 'выгрузки amoCRM не было — досинк пробуем')
        self.assertEqual(['FB', '1', '0', '—', '0+1'], self.line(text, 'FB'))

    def test_nothing_in_either_crm_and_a_failed_resync_is_an_error_not_zeros(self):
        def amo_down():
            raise RuntimeError('amoCRM: HTTP 401')

        db = FakeDb(amo_synced_ago=None)
        ns = bot_namespace(db, FakeSyntonyClient(configured=False), amo_sync=amo_down)
        with self.assertRaises(RuntimeError):
            ns['_amo_leads_alert_text'](now=self.NOW)

    def test_lying_syntony_does_not_make_every_leads_command_reload_amocrm(self):
        """Опыт разбора 19.11: Syntony лежит с 09:20, amoCRM свежая — окно режется по
        Syntony, но гонять полторы минуты выгрузку amoCRM на каждый /leads незачем."""
        calls = []
        # amoCRM выгружена 20 минут назад: свежая, и пауза между досинками уже прошла.
        db = FakeDb(amo_rows=[amo(at(19, 9), tags='forma_itaxi_общая', utm='fb')], amo_synced_ago=20)
        db.syncs = [{'id': 1, 'mode': 'full', 'status': 'ok', 'error': None,
                     'started_at': at(19, 9, 20), 'finished_at': at(19, 9, 21)}]
        ns = bot_namespace(db, FakeSyntonyClient(fail='Syntony: HTTP 502'),
                           amo_sync=lambda: calls.append(1))
        text = ns['_amo_leads_alert_text'](now=self.NOW)
        self.assertEqual([], calls, 'amoCRM свежая — досинк не нужен')
        self.assertIn('19.11, 00:00–09:20', text, 'окно — по самой несвежей CRM')
        self.assertIn('⚠️ Syntony: выгрузка', text)


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
