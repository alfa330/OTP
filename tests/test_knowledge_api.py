# -*- coding: utf-8 -*-
"""Ручка знаний для партнёрского ИИ (knowledge_api): ключ, парк, фрагменты, журнал.

Что здесь сторожится и почему:

* **До ключа не делается ничего.** Нет ключа, неверный, отозванный — один ответ
  401 и ни одного обращения к поиску; ключ без области «Ручка знаний» — 403;
  схема ключей не развёрнута — 503.
* **Один парк на вызов.** Фрагменты про чужой парк не отдаются; общие и «все
  парки, кроме…» — отдаются (решения владельца 29.10.2026).
* **«Данных нет» — тоже ответ**: status no_data, вопрос уходит в «Вопросы
  операторов», в журнале — строка с question_id.
* **Журнал — строкой на вызов** (partner_keys), включая плохой запрос и предел.

Сети и базы нет: поиск подменяется, курсор — двойник. Ключи, их выпуск и
админка проверяются в tests/test_partner_keys.py.
"""

import ast
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from knowledge_api import parks as park_match, routes  # noqa: E402
from knowledge_api import search as knowledge_search  # noqa: E402
from knowledge_api.routes import build_knowledge_blueprint  # noqa: E402
from partner_keys import keys  # noqa: E402
from tests import source_cache

BOT = source_cache.monolith_flat_path()
README = ROOT / 'README.md'
DOC = ROOT / 'docs' / 'SYNTONY_KNOWLEDGE_API.md'

SEARCH_URL = '/api/knowledge/search'
# Ключ выдуман; настоящий живёт только в журнале выпуска.
KEY = 'otpk_FAKE-key-for-tests-0123456789-abcdefghijk'
KEY_ROW = (7, 'Syntony', keys.mark(KEY), ['knowledge'], 'operator', 1,
           'СЗоВ — Служба заботы о водителях', 'szov', 3, 'Админ', None, None, 5, None, None, None)
REVOKED_ROW = KEY_ROW[:13] + ('2026-10-29T10:00:00', 3, 'Админ')
OTHER_DOOR_ROW = KEY_ROW[:3] + (['sign_links'],) + KEY_ROW[4:]
PARK_ROW = (5, 'global', 'Global', None, 'active', 11)

PARKS = [
    {'id': 1, 'slug': 'itaxi', 'name': 'iTaxi'},
    {'id': 4, 'slug': 'chestnyy-taksopark', 'name': 'Честный'},
    {'id': 5, 'slug': 'global', 'name': 'Global'},
    {'id': 8, 'slug': 'halyk', 'name': 'Халык'},
    {'id': 12, 'slug': 'jana-taksi', 'name': 'Jana такси'},
    {'id': 13, 'slug': '2dongelek', 'name': '2dongelek'},
    {'id': 14, 'slug': 'tenge-taksi', 'name': 'Тенге Такси'},
    {'id': 16, 'slug': 'nol-taksi', 'name': 'Ноль такси'},
    {'id': 17, 'slug': 'itaxi-vip', 'name': 'iTaxi VIP'},
    {'id': 19, 'slug': 'centr-registraciy-regiony', 'name': 'Центр Регистраций (Регионы)'},
]


def article_row(**extra):
    row = {'chunk_id': 4812, 'article_id': 33, 'title': 'Все акции', 'slug': 'vse-akcii',
           'heading_path': 'Акции СМЗ > ВСЕ ПАРКИ', 'text': 'Парк: Все парки; Даты: ∞.',
           'requires_ack': False, 'score': 0.31, 'similarity': 0.84, 'found_by': [0, 1],
           'historical': False, 'park_match': 'all_parks'}
    row.update(extra)
    return row


def result(status='ok', fragments=None):
    rows = [article_row()] if fragments is None else fragments
    return {
        'status': status,
        'park': None if status == 'park_not_found' else {'id': 5, 'slug': 'global', 'name': 'Global'},
        'fragments': [knowledge_search.fragment(index, row) for index, row in enumerate(rows, 1)]
        if status == 'ok' else [],
        'notes': [], 'degraded': False, 'vector_error': None, 'branches': {},
        'perimeter': {'articles': 50, 'hash': 'x', 'space_id': 11} if status in ('ok', 'no_data') else None,
        'today': '2026-11-19' if status in ('ok', 'no_data') else None,
    }


# ── Парк во фрагменте ───────────────────────────────────────────────────────────

class ParkMatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = park_match.ParkMatcher(PARKS)

    def classify(self, text, park_id=5):
        return self.matcher.classify(park_id, text)

    def test_requested_park_named_is_kept_even_next_to_others(self):
        self.assertEqual(self.classify('Таксопарк: Global; Номер WhatsApp: 77001234567'), 'named')
        self.assertEqual(self.classify('условия в Global и iTaxi VIP одинаковые'), 'named')
        self.assertEqual(self.classify('Парк: Глобал такси; Город: Алматы'), 'named')

    def test_only_other_parks_named_is_dropped(self):
        self.assertEqual(self.classify('№: 3; Парк: Jana Алматы; Город: Алматы'), 'other')
        self.assertEqual(self.classify('Парк: Tenge Taxi; Даты: ∞'), 'other')
        self.assertEqual(self.classify('от Anytime iTaxi — цена за день'), 'other')
        self.assertEqual(self.classify('Скрипт обзвона водителей таксопарка Ноль такси'), 'other')
        self.assertEqual(self.classify('Парк: 2Донгелек; Город: все'), 'other')

    def test_all_parks_and_exclusion_lists_are_handed_over_as_they_are(self):
        self.assertEqual(self.classify('Парк: Все парки, кроме Eki Dongelek, Anytime, Ноль такси'),
                         'all_parks')
        self.assertEqual(self.classify('ПАРКИ ПАРТНЕРЫ ТАКСИ ПРО Исключение 2Донгелек'), 'all_parks')
        self.assertEqual(self.classify('Таксопарки iGroup: 17 парков'), 'all_parks')

    def test_text_without_parks_is_general(self):
        self.assertEqual(self.classify('Как включить цепочку заказов в Яндекс Про'), 'general')

    def test_everyday_words_are_not_parks_without_park_context(self):
        self.assertEqual(self.classify('честный ответ оператора'), 'general')
        self.assertEqual(self.classify('Бонус 5000 тенге за 100 поездок'), 'general')
        self.assertEqual(self.classify('Пополнение через Halyk Bank или Kaspi'), 'general')
        self.assertEqual(self.classify('регионы Казахстана'), 'general')
        self.assertEqual(self.classify('Таксопарк: Халык; Номер WhatsApp: 7700'), 'other')
        self.assertEqual(self.classify('Парк: Честный; Город: Все города'), 'other')
        self.assertEqual(self.classify('Парк: Честный; Город: Все города', park_id=4), 'named')

    def test_longer_name_wins_over_its_prefix(self):
        self.assertEqual(self.matcher.mentions('условия iTaxi VIP'), {17: 'iTaxi VIP'})
        self.assertEqual(self.matcher.mentions('условия iTaxi'), {1: 'iTaxi'})

    def test_other_parks_in_the_question_are_named_for_the_note(self):
        self.assertEqual(self.matcher.others_in(5, 'Акции Jana и Global'), ['Jana такси'])
        self.assertEqual(self.matcher.others_in(5, 'Акции Global'), [])

    def test_normalize_keeps_positions(self):
        text = 'Парк: «Global» — Алматы!'
        self.assertEqual(len(park_match.normalize(text)), len(text))


# ── Поиск без базы ──────────────────────────────────────────────────────────────

class SearchHelpersTests(unittest.TestCase):
    def test_private_links_are_stripped_from_fragments(self):
        text = 'Инструкция (http://192.168.88.186:3000/ru/actions) и сайт https://taxi.yandex.kz/tariff'
        cleaned = knowledge_search.clean_text(text)
        self.assertNotIn('192.168', cleaned)
        self.assertIn('https://taxi.yandex.kz/tariff', cleaned)

    def test_fragment_names_branches_and_hides_negative_chunk_ids(self):
        item = knowledge_search.fragment(1, article_row(found_by=[0, 1, 4]))
        self.assertEqual(item['found_by'], ['words', 'meaning', 'directory'])
        self.assertEqual(item['kind'], 'article')
        self.assertEqual(item['chunk_id'], 4812)
        self.assertEqual(item['park_match'], 'all_parks')
        self.assertFalse(item['outdated'])
        directory = knowledge_search.fragment(2, article_row(chunk_id=-1, source_kind='office',
                                                             office={'id': 3, 'kind': 'partner',
                                                                     'partner_label': 'Брендирование'}))
        self.assertIsNone(directory['chunk_id'])
        self.assertEqual(directory['kind'], 'office')
        self.assertEqual(directory['office']['note'], 'Партнёрская точка, не офис таксопарка')

    def test_park_reads_stay_inside_the_key_spaces(self):
        """Граница пространства — в самом SQL (страж tests/test_wiki_directory_space.py)."""
        cursor = _Cursor(park_row=None)
        self.assertIsNone(knowledge_search.resolve_park(cursor, spaces=[11], park_id=5))
        text, params = cursor.calls[-1]
        self.assertIn('space_id = ANY(%(spaces)s)', text)
        self.assertEqual(params['spaces'], [11])
        self.assertIsNone(knowledge_search.resolve_park(cursor, spaces=[], park='global'))
        text, params = cursor.calls[-1]
        self.assertIn('space_id = ANY(%(spaces)s)', text)
        self.assertEqual(params['spaces'], [-1])

    def test_journal_keeps_references_but_not_text(self):
        journal = knowledge_search.journal_fragments([knowledge_search.fragment(1, article_row())])
        self.assertEqual(journal[0]['article_id'], 33)
        self.assertNotIn('text', journal[0])

    def test_offices_pass_the_park_sieve(self):
        rows = [
            {'source_kind': 'office', 'ref_id': 1, 'text': 'свой'},
            {'source_kind': 'office', 'ref_id': 2, 'text': 'чужой'},
            {'source_kind': 'office', 'ref_id': 3, 'text': 'общий'},
            {'source_kind': 'office', 'ref_id': 4, 'text': 'для всех'},
            {'source_kind': 'city', 'ref_id': 9, 'text': 'город'},
        ]
        offices = [
            {'id': 1, 'kind': 'park', 'all_parks': False, 'parks': [{'park_id': 5}]},
            {'id': 2, 'kind': 'park', 'all_parks': False, 'parks': [{'park_id': 12}]},
            {'id': 3, 'kind': 'partner', 'partner_label': 'Брендирование', 'all_parks': False, 'parks': []},
            {'id': 4, 'kind': 'park', 'all_parks': True, 'parks': []},
        ]
        kept = knowledge_search.narrow_offices(rows, offices, {'id': 5})
        self.assertEqual([row['ref_id'] for row in kept], [1, 3, 4, 9])
        self.assertEqual([row['park_match'] for row in kept], ['named', 'general', 'named', 'general'])
        self.assertEqual(kept[1]['office'], {'id': 3, 'kind': 'partner', 'partner_label': 'Брендирование'})


# ── Сборка ответа: материал помощника оператора ─────────────────────────────────

TODAY = date(2026, 11, 19)
SZOV_KEY = {'id': 7, 'name': 'Syntony', 'otp_role': 'operator', 'department_id': 1}
GLOBAL = {'id': 5, 'slug': 'global', 'name': 'Global', 'city': None, 'status': 'active', 'space_id': 11}
PERIMETER = {'article_ids': frozenset({33, 34, 35, 36, 37}), 'section_ids': frozenset(),
             'hash': 'h', 'read_count': 9}


class _RunPatches:
    """Подмена всего, что ходит в базу и к провайдеру, вокруг knowledge_search.run.

    Сборка справочников (routes_ai.directory_rows) остаётся настоящей, если
    не подменить её отдельно (directory=…)."""

    def __init__(self, test, *, articles=(), directory=None, classifier=(), park=GLOBAL):
        self.test = test
        self.calls = {}
        self.patches = [
            patch.object(knowledge_search.perimeter, 'service_context', side_effect=self.context),
            patch.object(knowledge_search.perimeter, 'spaces', return_value=[11]),
            patch.object(knowledge_search.perimeter, 'scope', return_value=PERIMETER),
            patch.object(knowledge_search, 'resolve_park', return_value=dict(park)),
            patch.object(knowledge_search, 'load_parks',
                         return_value=[dict(item, status='active', space_id=11) for item in PARKS]),
            patch.object(knowledge_search.ai_retrieve, 'search_hybrid', side_effect=self.search),
            patch.object(knowledge_search.assistant, 'classifier_rows', side_effect=self.classifier),
        ]
        if directory is not None:
            self.patches.append(patch.object(knowledge_search.assistant, 'directory_rows',
                                             side_effect=self.directory))
        self.articles, self.directory_result, self.classifier_result = (
            list(articles), directory, list(classifier))

    def context(self, cursor, *, otp_role, department_id):
        self.calls['context'] = (otp_role, department_id)
        return {'user_id': 0, 'otp_role': otp_role, 'department_id': department_id}

    def search(self, cursor, *, article_ids, query, query_vector, limit, per_article):
        self.calls['search'] = {'article_ids': article_ids, 'query': query, 'limit': limit}
        return {'rows': [dict(row) for row in self.articles], 'branches': {'lexical': 1},
                'degraded': False}

    def directory(self, cursor, ctx, question, space_id, **kwargs):
        self.calls['directory'] = dict(kwargs, question=question, space_id=space_id)
        return [dict(row) for row in self.directory_result]

    def classifier(self, cursor, question, article_ids, *args, **kwargs):
        self.calls['classifier'] = article_ids
        return [dict(row) for row in self.classifier_result]

    def run(self, question, **kwargs):
        for item in self.patches:
            item.start()
        try:
            return knowledge_search.run(MagicMock(), key=SZOV_KEY, question=question, park_id=5,
                                        embed=lambda _q: None, on_date=TODAY, **kwargs)
        finally:
            for item in reversed(self.patches):
                item.stop()


class RunTests(unittest.TestCase):
    REGISTRATION = {'source_kind': 'registration', 'chunk_id': -3001, 'title': 'Города',
                    'heading_path': 'Рудный › Регистрация водителей', 'found_by': [4],
                    'text': 'Регистрация водителей из города Рудный, парк Global: …'}
    OFFICE = {'source_kind': 'office', 'chunk_id': -1, 'title': 'Офисы', 'ref_id': 3,
              'heading_path': 'Алматы › Офис', 'found_by': [4], 'text': 'Офис…',
              'park_match': 'general'}
    # Без park_match, как приходят из сборки: карточка встаёт после сита офисов.
    CARD = {'source_kind': 'park', 'chunk_id': -4001, 'title': 'Парки', 'heading_path': 'Global',
            'found_by': [4], 'text': 'Таксопарк «Global».\n(срок истёк 30.09.2026) акция до 30.09.2026'}
    CAR = {'source_kind': 'classifier', 'chunk_id': -2001, 'title': 'Классификатор авто',
           'heading_path': 'Toyota Camry', 'found_by': [4], 'text': 'Camry: Комфорт+ с 2019 г.'}

    def test_material_is_the_assistant_assembly_with_the_park_of_the_call(self):
        patches = _RunPatches(self, articles=[article_row()],
                              directory=[self.REGISTRATION, self.OFFICE, self.CARD],
                              classifier=[self.CAR])
        result = patches.run('Какие акции?', limit=3)
        self.assertEqual([(item['kind'], item['park_match']) for item in result['fragments']],
                         [('registration', 'named'), ('office', 'general'), ('park', 'named'),
                          ('classifier', 'general'), ('article', 'all_parks')])
        self.assertEqual([item['rank'] for item in result['fragments']], [1, 2, 3, 4, 5])
        call = patches.calls['directory']
        self.assertEqual(call['park'], {'id': 5, 'name': 'Global'})
        self.assertTrue(callable(call['narrow']))
        self.assertEqual((call['question'], call['space_id']), ('Какие акции?', 11))
        self.assertNotIn('history', call)
        self.assertEqual(result['today'], '2026-11-19')
        self.assertEqual((result['branches']['registration'], result['branches']['park'],
                          result['branches']['classifier']), (1, 1, 1))

    def test_rights_are_those_of_an_operator_of_the_key_department(self):
        """Ни статьи, ни классификатор не выходят за периметр оператора отдела
        ключа: владелец 19.11.2026 — «только для руководителей … не передавать»."""
        patches = _RunPatches(self, articles=[article_row()], directory=[], classifier=[self.CAR])
        patches.run('Какие акции?')
        self.assertEqual(patches.calls['context'], ('operator', 1))
        self.assertEqual(patches.calls['search']['article_ids'], PERIMETER['article_ids'])
        self.assertEqual(patches.calls['classifier'], PERIMETER['article_ids'])
        self.assertEqual(patches.calls['search']['limit'], 8 * knowledge_search.CANDIDATE_FACTOR)

    def test_perimeter_is_the_assistant_one_without_master_key(self):
        source = (ROOT / 'knowledge_api' / 'perimeter.py').read_text(encoding='utf-8')
        self.assertIn('wiki_perimeter.assistant_perimeter(cursor, ctx, space_id)', source)
        self.assertNotIn('master_key=True', source)
        tree = ast.parse((ROOT / 'knowledge_api' / 'search.py').read_text(encoding='utf-8'))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        # «Списки Байги» (ФИО, ВУ, доход водителей) ручка не читает ни под каким видом.
        self.assertFalse({'baiga_rows', 'baiga_assistant', 'baiga'} & names)

    def test_articles_about_other_parks_are_dropped(self):
        rows = [article_row(chunk_id=1, heading_path='Бонусы', text='№: 3; Парк: Jana Алматы; бонус'),
                article_row(chunk_id=2, heading_path='Бонусы', text='Таксопарк: Global; бонус 5000'),
                article_row(chunk_id=3, text='Как включить цепочку заказов', heading_path='Цепочка'),
                article_row(chunk_id=4, heading_path='Бонусы', text='Парк: Все парки, кроме Ноль такси'),
                article_row(chunk_id=5, heading_path='Бонусы', text='без опоры', found_by=[1],
                            similarity=0.5)]
        result = _RunPatches(self, articles=rows, directory=[]).run('бонусы')
        kept = [(item['chunk_id'], item['park_match']) for item in result['fragments']]
        self.assertEqual(kept, [(2, 'named'), (3, 'general'), (4, 'all_parks')])
        self.assertEqual(result['branches']['dropped_other_park'], 1)

    def test_freshness_is_the_assistant_rule(self):
        rows = [article_row(chunk_id=1, historical=True, text='Парк: Global; акция'),
                article_row(chunk_id=2, title='Архивные акции', text='Парк: Global'),
                article_row(chunk_id=3, text='Парк: Global; Даты: по 30.09.2026'),
                article_row(chunk_id=4, text='Парк: Global; Даты: по 30.09.2026\n'
                                             'Парк: Global; Даты: по 31.12.2026'),
                article_row(chunk_id=5, text='Парк: Global; Даты: по 31.12.2026')]
        result = _RunPatches(self, articles=rows, directory=[self.CARD]).run('акции', limit=10)
        marks = {item['chunk_id'] or item['kind']: (item['outdated'], item['outdated_note'])
                 for item in result['fragments']}
        self.assertEqual(marks[1], (True, 'АРХИВ — сведения уже не действуют'))
        self.assertEqual(marks[2], (True, 'АРХИВ — сведения уже не действуют'))
        self.assertEqual(marks[3], (True, 'СРОК ИСТЁК 30.09.2026'))
        # Истекла часть сроков: фрагмент живой, пометка просит сверить даты.
        self.assertFalse(marks[4][0])
        self.assertIn('ЧАСТЬ СРОКОВ ИСТЕКЛА, последний 30.09.2026', marks[4][1])
        self.assertEqual(marks[5], (False, None))
        # Карточка парка — живые данные; истёкшую строку она помечает сама.
        self.assertEqual(marks['park'], (False, None))

    def test_no_fragments_is_no_data(self):
        result = _RunPatches(self, articles=[], directory=[]).run('есть ли парковка у офиса?')
        self.assertEqual((result['status'], result['fragments']), ('no_data', []))
        self.assertEqual(result['perimeter']['space_id'], 11)


class OfficeSieveTests(unittest.TestCase):
    """Сито офисов стоит ДО решения «на вопрос уже ответили офисы» (routes_ai):
    выброшенный чужой офис не должен снимать карточку парка."""

    OTHER_OFFICE = {'source_kind': 'office', 'chunk_id': -1, 'ref_id': 2, 'title': 'Офисы',
                    'heading_path': 'Алматы › Jana', 'text': 'Офис Jana', 'found_by': [4]}

    def test_foreign_office_is_dropped_before_the_card_decision(self):
        cursor = MagicMock()
        seen = {}

        def park_rows(*args, **kwargs):
            seen.update(kwargs)
            return [dict(RunTests.CARD)]

        offices = [{'id': 2, 'kind': 'park', 'all_parks': False, 'parks': [{'park_id': 12}]}]
        with patch.object(knowledge_search.assistant.wiki_registration, 'ai_rows', return_value=[]), \
             patch.object(knowledge_search.assistant.wiki_directory, 'ai_rows',
                          return_value=[dict(self.OTHER_OFFICE)]), \
             patch.object(knowledge_search.assistant.wiki_park_cards, 'ai_rows', side_effect=park_rows), \
             patch.object(knowledge_search.wiki_offices, 'list_offices', return_value=offices):
            found = knowledge_search.assistant.directory_rows(
                cursor, {}, 'где офис', 11, park={'id': 5, 'name': 'Global'},
                narrow=knowledge_search.office_sieve(cursor, 11, GLOBAL))
        self.assertEqual([row['source_kind'] for row in found], ['park'])
        self.assertEqual(seen['answered'], set())
        self.assertEqual(seen['park'], {'id': 5, 'name': 'Global'})

    def test_unreadable_offices_are_not_handed_over(self):
        cursor = MagicMock()
        rows = [dict(self.OTHER_OFFICE), dict(RunTests.REGISTRATION)]
        with patch.object(knowledge_search.wiki_offices, 'list_offices',
                          side_effect=RuntimeError('база')), \
             patch.object(knowledge_search.logging, 'exception'):
            kept = knowledge_search.office_sieve(cursor, 11, GLOBAL)(rows)
        self.assertEqual([(row['source_kind'], row['park_match']) for row in kept],
                         [('registration', 'named')])
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn('ROLLBACK TO SAVEPOINT knowledge_offices', statements)


class OfficeSummaryTests(unittest.TestCase):
    """Сводка «Все офисы» (вопрос про офисы без города) для парка, известного
    заранее: его офисы и общие, офисов только чужих парков нет. Нашёл сквозной
    прогон на стенде 19.11.2026: партнёру Global уходил адрес офиса Jana —
    строка сводки шла мимо сита narrow_offices."""

    OFFICES = [
        {'id': 1, 'name': 'Офис Global', 'city': 'Алматы', 'address': 'Абая, 10', 'kind': 'park',
         'all_parks': False, 'parks': [{'park_id': 5}], 'schedule': None},
        {'id': 2, 'name': 'Офис Jana', 'city': 'Алматы', 'address': 'Сейфуллина, 5', 'kind': 'park',
         'all_parks': False, 'parks': [{'park_id': 12}], 'schedule': None},
        {'id': 3, 'name': 'Общий офис', 'city': 'Астана', 'address': 'Мира, 1', 'kind': 'park',
         'all_parks': False, 'parks': [], 'schedule': None},
        {'id': 4, 'name': 'Офис всех парков', 'city': 'Шымкент', 'address': 'Тауке хана, 2',
         'kind': 'park', 'all_parks': True, 'parks': [], 'schedule': None},
    ]

    def summary(self, named_parks):
        from datetime import datetime
        from wiki import directory as wiki_directory
        rows = wiki_directory.build_ai_rows(
            wiki_directory.analyze_question('где офис?'), space={'id': 11, 'offices': True,
                                                               'cities': True},
            offices=self.OFFICES, cities=[], parks=[{'id': 5, 'name': 'Global'},
                                                    {'id': 12, 'name': 'Jana такси'}],
            day=TODAY, now=datetime(2026, 11, 19, 12, 0), named_parks=named_parks)
        self.assertEqual([row['source_kind'] for row in rows], ['offices'])
        return rows[0]['text']

    def test_partner_summary_has_no_offices_of_other_parks(self):
        text = self.summary([{'id': 5, 'name': 'Global'}])
        for name in ('Офис Global', 'Общий офис', 'Офис всех парков'):
            self.assertIn(name, text)
        self.assertNotIn('Офис Jana', text)
        self.assertNotIn('Сейфуллина', text)

    def test_assistant_summary_keeps_every_office(self):
        self.assertIn('Офис Jana', self.summary(None))


class AssistantParityTests(unittest.TestCase):
    """Тот же материал, что помощник даёт оператору (владелец 19.11.2026), — на
    настоящей сборке routes_ai.directory_rows: регистрация и «Парки» читают
    справочники тестов wiki, база подменена только на их входе."""

    @classmethod
    def setUpClass(cls):
        import test_wiki_park_cards as cards_data
        import test_wiki_registration as registration_data
        cls.cards = cards_data
        cls.registration = registration_data

    def assembled(self, question, *, park=None):
        """(строки ручки, строки помощника) на одном вопросе."""
        from wiki import directory as wiki_directory
        from wiki import park_cards, registration
        from wiki import parks as wiki_parks

        def space(_cursor, _ctx, requested, tabs=wiki_directory.DIRECTORY_TABS):
            return dict({'id': requested}, **{tab: True for tab in tabs})

        details = {key: value for key, value in self.cards.CATALOG.items() if key != 'parks'}
        with patch.object(wiki_directory, 'directory_space', side_effect=space), \
             patch.object(wiki_directory, 'ai_rows', return_value=[]), \
             patch.object(registration, 'load', return_value=self.registration.CATALOG), \
             patch.object(wiki_parks, 'list_parks', return_value=self.cards.PARKS), \
             patch.object(park_cards, 'load_details', return_value=details), \
             patch.object(park_cards.currency, 'today', return_value=self.cards.DAY):
            cursor = MagicMock()
            partner = knowledge_search.assistant.directory_rows(
                cursor, {}, question, 11, park=park,
                narrow=knowledge_search.office_sieve(cursor, 11, park))
            operator = knowledge_search.assistant.directory_rows(cursor, {}, question, 11)
        return partner, operator

    def test_registration_text_is_the_assistant_one(self):
        partner, _ = self.assembled('куда зарегистрироваться, я из рудного',
                                    park={'id': 1, 'name': 'iTaxi'})
        _, operator = self.assembled('куда зарегать водителя айтакси если он обращается с рудного')
        self.assertEqual([row['source_kind'] for row in partner], ['registration'])
        self.assertEqual(partner[0]['text'], operator[0]['text'])
        self.assertEqual(partner[0]['park_match'], 'named')
        self.assertIn('регистрировать в городе Костанай', partner[0]['text'])

    def test_park_card_text_is_the_assistant_one(self):
        partner, _ = self.assembled('какая комиссия при выводе?', park={'id': 4, 'name': 'Честный'})
        _, operator = self.assembled('какая комиссия при выводе в парке честный')
        self.assertEqual([row['source_kind'] for row in partner], ['park'])
        self.assertEqual(partner[0]['text'], operator[0]['text'])
        self.assertIn('При снятии суммы от 1000тг до 10 000тг', partner[0]['text'])

    def test_no_summary_of_all_parks_for_the_partner(self):
        partner, operator = self.assembled('комиссия всех парков', park={'id': 5, 'name': 'Global'})
        self.assertEqual(operator[0]['heading_path'], 'Все парки')
        self.assertEqual([row['heading_path'] for row in partner], ['Global'])
        self.assertNotIn('iTaxi', partner[0]['text'])


class RateLimiterTests(unittest.TestCase):
    def test_minute_window_refuses_the_extra_call_and_names_the_wait(self):
        clock = {'now': 100.0}
        limiter = routes.RateLimiter(2, clock=lambda: clock['now'])
        self.assertEqual(limiter.allow(7), (True, 0))
        self.assertEqual(limiter.allow(7), (True, 0))
        allowed, wait = limiter.allow(7)
        self.assertFalse(allowed)
        self.assertGreaterEqual(wait, 1)
        self.assertEqual(limiter.allow(8), (True, 0))  # другой ключ — своя минута
        clock['now'] += 61
        self.assertEqual(limiter.allow(7), (True, 0))


# ── Курсор-двойник и сборка ручки ───────────────────────────────────────────────

class _Cursor:
    """Помнит SQL и параметры; отвечает на то, что ручка спрашивает у базы."""

    def __init__(self, *, ready=True, key_row=KEY_ROW, park_row=PARK_ROW):
        self.calls = []
        self.ready = ready
        self.key_row = key_row
        self.park_row = park_row
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one, self._all = None, []
        if 'to_regclass' in text:
            self._one = (self.ready,)
        elif 'FROM partner_keys k' in text and 'WHERE k.key_hash' in text:
            self._one = self.key_row if params and params[0] == keys.digest(KEY) else None
        elif 'FROM wiki_taxi_parks' in text and 'WHERE id = %(id)s' in text:
            self._one = self.park_row if params and params['id'] == 5 else None
        elif 'FROM wiki_taxi_parks' in text and 'lower(slug)' in text:
            self._one = self.park_row if params and params['value'].lower() == 'global' else None
        elif text.startswith('INSERT INTO partner_key_calls'):
            self._one = (101,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)

    @property
    def rowcount(self):
        return 1

    def journal(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO partner_key_calls')]

    def touched(self):
        return [params for text, params in self.calls
                if text.startswith('UPDATE partner_keys') and 'calls_count' in text]


@unittest.skipIf(Flask is None, 'flask не установлен')
class Harness(unittest.TestCase):
    def build(self, *, ready=True, key_row=KEY_ROW, park_row=PARK_ROW, result_=None,
              raise_=None, per_minute=None, lanes=None):
        self.cursor = _Cursor(ready=ready, key_row=key_row, park_row=park_row)
        self.searches = []
        self.escalations = []
        outcome = result() if result_ is None else result_

        def fake_search(cursor, *, key, question, park_id, park, limit):
            self.searches.append({'key': key['id'], 'park_id': park_id, 'park': park,
                                  'question': question, 'limit': limit})
            if raise_:
                raise raise_
            return outcome

        def fake_escalate(cursor, **fields):
            self.escalations.append(fields)
            return 55

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(build_knowledge_blueprint(
            db=db, client_ip=lambda: '203.0.113.7', search=fake_search, environ={},
            per_minute=per_minute, lanes=lanes, escalate=fake_escalate))
        return self.app.test_client()

    def post(self, client, body, key=KEY):
        headers = {keys.HEADER: key} if key is not None else {}
        return client.post(SEARCH_URL, data=json.dumps(body), content_type='application/json',
                           headers=headers)


class DoorTests(Harness):
    def test_without_schema_the_door_is_closed_not_open(self):
        client = self.build(ready=False)
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_NOT_CONFIGURED')
        self.assertEqual(self.searches, [])

    def test_missing_wrong_and_revoked_keys_get_the_same_refusal(self):
        for key, row in ((None, KEY_ROW), ('otpk_wrong-key-of-the-right-length-0123456789', KEY_ROW),
                         (KEY, REVOKED_ROW)):
            client = self.build(key_row=row)
            response = self.post(client, {'park_id': 5, 'question': 'акции'}, key=key)
            self.assertEqual(response.status_code, 401, key)
            self.assertEqual(response.get_json(), {'error': 'Ключ не принят',
                                                   'code': 'KNOWLEDGE_API_UNAUTHORIZED'})
            self.assertEqual(self.searches, [])
            self.assertEqual(self.cursor.journal(), [])

    def test_a_key_without_this_door_is_403(self):
        client = self.build(key_row=OTHER_DOOR_ROW)
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_FORBIDDEN')
        self.assertEqual(self.searches, [])
        self.assertEqual(client.get('/api/knowledge/ping', headers={keys.HEADER: KEY}).status_code, 403)

    def test_response_is_never_cached(self):
        client = self.build()
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')


class RequestShapeTests(Harness):
    def test_bad_bodies_are_refused_and_journaled(self):
        client = self.build()
        cases = [
            (None, 'JSON'), ({'park_id': 5}, 'question'), ({'question': 'акции'}, 'park'),
            ({'park_id': 'five', 'question': 'акции'}, 'park_id'),
            ({'park_id': 5, 'question': 'акции', 'limit': 0}, 'limit'),
            ({'park_id': 5, 'question': 'x' * 1001}, '1000'),
        ]
        for body, word in cases:
            response = client.post(SEARCH_URL, data='not json' if body is None else json.dumps(body),
                                   content_type='application/json', headers={keys.HEADER: KEY})
            self.assertEqual(response.status_code, 400, word)
            payload = response.get_json()
            self.assertEqual(payload['code'], 'KNOWLEDGE_API_BAD_REQUEST')
            self.assertIn(word, payload['error'])
            self.assertIn('request_id', payload)
        self.assertEqual(self.searches, [])
        self.assertEqual([entry['outcome'] for entry in self.cursor.journal()],
                         ['bad_request'] * len(cases))

    def test_unknown_park_is_404_and_points_to_the_list(self):
        client = self.build(result_=result('park_not_found'))
        response = self.post(client, {'park_id': 99, 'question': 'акции'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_PARK_NOT_FOUND')
        self.assertEqual(self.searches[0]['park_id'], 99)
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['outcome'], entry['status']), ('bad_request', 404))
        self.assertEqual(json.loads(entry['details'])['error_text'], 'park_not_found')

    def test_park_can_be_named_by_slug(self):
        client = self.build()
        response = self.post(client, {'park': 'Global', 'question': 'акции'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.searches[0]['park_id'], self.searches[0]['park']), (None, 'Global'))


class AnswerTests(Harness):
    def test_fragments_are_handed_over_and_the_call_is_journaled(self):
        client = self.build()
        response = self.post(client, {'park_id': 5, 'question': ' Какие  акции? ', 'limit': 3})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['park'], {'id': 5, 'slug': 'global', 'name': 'Global'})
        self.assertEqual(payload['question'], 'Какие акции?')
        self.assertEqual(payload['fragments'][0]['title'], 'Все акции')
        self.assertEqual(payload['perimeter'], {'articles': 50})
        self.assertEqual(payload['today'], '2026-11-19')
        self.assertEqual((payload['fragments'][0]['outdated'], payload['fragments'][0]['outdated_note']),
                         (False, None))
        self.assertEqual(self.searches, [{'key': 7, 'park_id': 5, 'park': None,
                                          'question': 'Какие акции?', 'limit': 3}])
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['key'], entry['scope'], entry['endpoint'], entry['outcome'],
                          entry['status'], entry['summary'], entry['ip']),
                         (7, 'knowledge', SEARCH_URL, 'ok', 200, 'Какие акции?', '203.0.113.7'))
        details = json.loads(entry['details'])
        self.assertEqual((details['park_id'], details['park_name'], details['fragments_count']),
                         (5, 'Global', 1))
        self.assertEqual(details['fragments'][0]['article_id'], 33)
        self.assertNotIn('text', details['fragments'][0])
        self.assertEqual(self.cursor.touched(), [(7,)])
        self.assertEqual(self.escalations, [])

    def test_no_data_goes_to_the_supervisors_and_says_so(self):
        client = self.build(result_=result('no_data'))
        response = self.post(client, {'park_id': 5, 'question': 'Есть ли парковка у офиса?'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'no_data')
        self.assertEqual(payload['fragments'], [])
        self.assertEqual(payload['message'], 'Данных нет')
        self.assertTrue(payload['question_logged'])
        self.assertEqual(self.escalations, [{
            'key_id': 7, 'key_name': 'Syntony', 'department_id': 1, 'space_id': 11,
            'park_id': 5, 'park_name': 'Global', 'question': 'Есть ли парковка у офиса?'}])
        entry = self.cursor.journal()[0]
        self.assertEqual(entry['outcome'], 'no_data')
        self.assertEqual(json.loads(entry['details'])['question_id'], 55)

    def test_an_archived_park_is_403(self):
        client = self.build(result_=result('park_unavailable'))
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_PARK_UNAVAILABLE')
        self.assertEqual(self.cursor.journal()[0]['outcome'], 'park_unavailable')

    def test_a_broken_search_is_500_and_still_journaled(self):
        client = self.build(raise_=RuntimeError('провайдер лёг'))
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_FAILED')
        self.assertNotIn('провайдер', response.get_data(as_text=True))
        entry = self.cursor.journal()[0]
        self.assertEqual(entry['outcome'], 'error')
        self.assertEqual(json.loads(entry['details'])['error_text'], 'RuntimeError')


class LimitTests(Harness):
    def test_the_minute_limit_answers_429_with_retry_after(self):
        client = self.build(per_minute=1)
        self.assertEqual(self.post(client, {'park_id': 5, 'question': 'акции'}).status_code, 200)
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_LIMIT')
        self.assertTrue(int(response.headers['Retry-After']) >= 1)
        self.assertEqual(len(self.searches), 1)
        self.assertEqual([entry['outcome'] for entry in self.cursor.journal()], ['ok', 'limit'])


class PingTests(Harness):
    def test_ping_names_the_key_and_its_rights(self):
        client = self.build()
        response = client.get('/api/knowledge/ping', headers={keys.HEADER: KEY})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['key'], 'Syntony')
        self.assertEqual(payload['acts_as']['department_code'], 'szov')
        self.assertEqual(payload['limits']['max_question_chars'], routes.MAX_QUESTION_CHARS)

    def test_ping_without_key_is_401(self):
        client = self.build()
        self.assertEqual(client.get('/api/knowledge/ping').status_code, 401)


# ── Проводка ────────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT.read_text(encoding='utf-8-sig')

    def test_the_external_entry_is_registered_without_the_session_decorator(self):
        start = self.bot.index('build_knowledge_blueprint(')
        call = self.bot[start:self.bot.index('))', start)]
        self.assertIn('db=db', call)
        self.assertIn('client_ip=_client_ip', call)
        self.assertNotIn('require_api_key', call)

    def test_the_partner_document_and_the_readme_name_the_contract(self):
        doc = DOC.read_text(encoding='utf-8')
        readme = README.read_text(encoding='utf-8')
        for token in (SEARCH_URL, '/api/knowledge/parks', '/api/knowledge/ping', keys.HEADER,
                      'no_data', 'park_match', 'all_parks', 'KNOWLEDGE_API_UNAUTHORIZED',
                      'KNOWLEDGE_API_PARK_NOT_FOUND', 'KNOWLEDGE_API_LIMIT', 'Retry-After'):
            self.assertIn(token, doc, token)
        for token in (SEARCH_URL, keys.HEADER, 'KNOWLEDGE_API_PER_MINUTE', 'KNOWLEDGE_API_LANES',
                      'docs/SYNTONY_KNOWLEDGE_API.md'):
            self.assertIn(token, readme, token)
        for kind in ('article', 'park', 'office', 'offices', 'city', 'cities', 'classifier',
                     'registration'):
            self.assertIn('`%s`' % kind, doc, kind)
        for token in ('outdated_note', '`today`', 'Версия 1.2'):
            self.assertIn(token, doc, token)
        for name in knowledge_search.FOUND_BY.values():
            self.assertIn('`%s`' % name, doc, name)

    def test_partner_questions_have_a_source_and_a_park_in_the_queue(self):
        from wiki import questions as wiki_questions
        for key in ('source', 'source_label', 'park_name', 'asked_count'):
            self.assertIn(key, wiki_questions._ROW_KEYS)
        wiki_schema = (ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        for column in ("source VARCHAR(16) NOT NULL DEFAULT 'assistant'", 'source_label VARCHAR(120)',
                       'park_id INTEGER', 'park_name VARCHAR(255)', 'asked_count INTEGER NOT NULL DEFAULT 1',
                       'ALTER COLUMN asker_id DROP NOT NULL'):
            self.assertIn(column, wiki_schema, column)
        jsx = (ROOT / 'src' / 'components' / 'wiki' / 'WikiQuestions.jsx').read_text(encoding='utf-8')
        self.assertIn("item.source === 'api'", jsx)
        sources = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')
        self.assertIn('COALESCE(asker.name, q.source_label)', sources)

    def test_the_sessions_section_shows_the_keys_tab_to_the_super_admin_only(self):
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn('canManageKeys={isSuperAdmin}', app)
        self.assertIn("{ value: 'keys', label: 'Ключи' }", app)


if __name__ == '__main__':
    unittest.main()
