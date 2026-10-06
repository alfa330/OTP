# -*- coding: utf-8 -*-
"""«Списки Байги» в ИИ-помощнике вики (baiga/assistant.py; решение владельца 05.10.2026).

Что сторожится:
  * доступ: помощник читает списки ровно тем, кому открыт раздел, а тем, кого
    спрашивает QR-замок, — только за подтверждённой сессией (сетка ролей ×
    отделов × главенства — та же, что у ручек раздела); помощник пространства
    «Тез КЦ» списков не читает никому; до проверки права к строкам не ходят, а
    вопрос не про Байгу базы не касается вовсе; ответ со строками раздела за
    пределы разговора автора (в очередь супервайзера) не передаётся;
  * лишняя строка: слово, совпавшее с фамилией из списка, водителя не называет,
    пока вопрос этого не подтвердил («Мороз на улице, рейтинг упадёт?»);
    незнакомое слово водителем не объявляется («водитель Яндекса», «в Астане»);
  * вопрос по месту (05.10.2026, владелец спросил шарик «кто на прошлой неделе в
    Алматы занял первое место?»): место словом и цифрой, «топ-3», «победитель»;
    город вопроса — зачёт; зачёт не назван — это место в каждом зачёте; чужое
    «первое место» («в рейтинге операторов») строк не тянет;
  * вопрос: тема трёх степеней, водитель — номер ВУ в любом написании, ID,
    фамилия в падеже и с казахскими буквами («у Жумабая», «Ли А.»); неделя —
    «на этой», «на прошлой», дата, номер; фамилия, начинающаяся со слова темы
    («Байгабулов», «Орынбаев»), остаётся фамилией;
  * неделя: спрошенная, последняя загруженная, «ещё нет», «в разделе нет» — и
    другой неделей без оговорки не подменяется;
  * «водителя в списке нет» — ответ, а не тишина и не отказ;
  * разговор: «а приз какой?», «ну а сколько денег он заработал?», «а на
    прошлой?», «а Петров?» — про водителя; «а когда приз?», «а как заблокировать
    Жусипова?» — уже нет; реплики читаются по одной, и последняя неделя главнее;
  * контракт фрагмента: поля ретривера, числа ответа лежат в тексте, прошедшая
    неделя не получает пометку «срок истёк», ID водителя модели не уходит;
  * слой ответа: подпись и правило приходят только с такими фрагментами, неделя
    и числа рядом с ней не считаются выдумкой, строка однофамильца не
    подшивается, а строку, найденную кодом, отказ модели не отнимает;
  * история: источник показывается, пока раздел открыт;
  * проводка: ключ QR доходит от блюпринта до помощника, пользовательский текст
    в SQL не попадает, чип ведёт в раздел.

Все ФИО, номера ВУ и ID здесь ВЫДУМАНЫ: настоящий файл недели — персональные
данные водителей, и в публичный репозиторий он не идёт ни целиком, ни строкой.

SQL здесь не проверяется — его проверяет прогон на стенде с базой.
"""

import inspect
import re
import sys
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from baiga import access, assistant, parse, queries  # noqa: E402
from tests.test_baiga import fake_id, grid, person  # noqa: E402
from wiki import questions as wiki_questions  # noqa: E402
from wiki import routes_ai  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki.ai import answer as ai_answer  # noqa: E402
from wiki.ai import currency  # noqa: E402
from wiki.ai import store as ai_store  # noqa: E402

TODAY = date(2026, 10, 5)          # понедельник: списка «этой недели» ещё нет

SHEETS = [{'name': 'Алматы-Каскелен', 'rows': 4}, {'name': 'Астана', 'rows': 4},
          {'name': 'МОТО БАЙГА', 'rows': 1}]
W39 = {'id': 2, 'period_start': date(2026, 9, 21), 'period_end': date(2026, 9, 27),
       'week_number': 39, 'sheets': SHEETS}
W40 = {'id': 3, 'period_start': date(2026, 9, 28), 'period_end': date(2026, 10, 4),
       'week_number': 40, 'sheets': SHEETS}
WEEKS = [W40, W39]                 # свежие сверху, как queries.list_weeks

SHEET_ORDER = {'Алматы-Каскелен': 0, 'Астана': 1, 'МОТО БАЙГА': 2,
               'Петропавловск-Уральск-Усть-Каме': 3}


def row(week, zachet, position, name, license_value, number, *, amount, trips,
        prize='Не указано', city='Алматы', park='Тестовый парк'):
    has_prize, prize_amount, _known = parse.prize_amount(prize)
    driver = fake_id(number)
    order = SHEET_ORDER.get(zachet, 9)
    return {
        'id': week['id'] * 1000 + number * 10 + order,
        'period_start': week['period_start'], 'zachet': zachet,
        'sheet_order': order, 'row_number': position + 1, 'position': position,
        'date_text': week['period_start'].strftime('%d.%m.%y'), 'week_number': week['week_number'],
        'driver_name': name, 'prize_name': prize, 'prize_amount': prize_amount,
        'has_prize': has_prize, 'amount': amount, 'trips': trips, 'city': city, 'park': park,
        'license': license_value, 'driver_id': driver, 'upload_id': week['id'],
        'license_key': parse.license_key(license_value), 'driver_key': parse.driver_key(driver),
        'search_text': parse.search_text(name, license_value, driver),
    }


ROWS = [
    row(W40, 'Алматы-Каскелен', 1, 'Жүсіпов А. Б.', 'ZZ123456', 1, amount=669460, trips=427,
        prize='200 000 тенге'),
    row(W40, 'МОТО БАЙГА', 5, 'Жүсіпов А. Б.', 'ZZ123456', 1, amount=180300, trips=60,
        prize='20 000 тенге'),
    row(W40, 'Алматы-Каскелен', 12, 'Ахметов Д. К.', 'ZY654321', 2, amount=345000, trips=120,
        prize='5000 тенге'),
    row(W40, 'Астана', 40, 'Ахметов Е.', 'ZX111111', 3, amount=250000, trips=90, city='Астана'),
    row(W40, 'Астана', 105, 'Иванова М. С.', 'ZW222222', 4, amount=239740, trips=48, city='Астана'),
    row(W40, 'Алматы-Каскелен', 14, 'Ким-Заде Р.', 'ZU444444', 7, amount=340000, trips=118,
        prize='5000 тенге'),
    row(W40, 'Астана', 22, 'Жумабай Е.', 'ZJ000001', 10, amount=280000, trips=77, city='Астана'),
    row(W40, 'Астана', 23, 'Коваль П.', 'ZJ000002', 11, amount=279000, trips=76, city='Астана'),
    row(W40, 'Астана', 24, 'Ли А.', 'ZJ000003', 12, amount=278000, trips=75, city='Астана'),
    row(W40, 'Астана', 25, 'Шин О.', 'ZJ000004', 13, amount=277000, trips=74, city='Астана'),
    row(W40, 'Астана', 26, 'Мороз И.', 'ZJ000005', 14, amount=276000, trips=73, city='Астана',
        park='Глобал'),
    row(W39, 'Алматы-Каскелен', 3, 'Жүсіпов А. Б.', 'ZZ123456', 1, amount=600120, trips=400,
        prize='100 000 тенге'),
    row(W39, 'Астана', 7, 'Иванова М. С.', 'ZW222222', 4, amount=300500, trips=100,
        prize='10 000 тенге', city='Астана'),
    row(W39, 'Астана', 55, 'Ким В.', '1234567890', 5, amount=240000, trips=80, city='Астана'),
    # Тот же человек (тот же номер ВУ), заведённый в парке заново: ID другой.
    row(W39, 'Астана', 61, 'Ахметов Е.', 'ZX111111', 9, amount=228000, trips=70, city='Астана'),
]


# Недели для вопросов по месту: в каждом зачёте места идут с первого, как в файле.
# Название четвёртого листа обрезано пределом Excel в 31 символ — так оно и
# лежит на проде («…Усть-Каменогорск»).
PLACE_SHEETS = [{'name': 'Алматы-Каскелен', 'rows': 3}, {'name': 'Астана', 'rows': 3},
                {'name': 'МОТО БАЙГА', 'rows': 1}, {'name': 'Петропавловск-Уральск-Усть-Каме', 'rows': 2}]
P39 = dict(W39, id=12, sheets=PLACE_SHEETS)
P40 = dict(W40, id=13, sheets=PLACE_SHEETS)
PLACE_WEEKS = [P40, P39]
PLACE_ROWS = [
    row(P40, 'Алматы-Каскелен', 1, 'Первов А.', 'ZP000001', 101, amount=700000, trips=430, prize='200 000 тенге'),
    row(P40, 'Алматы-Каскелен', 2, 'Вторых Б.', 'ZP000002', 102, amount=650000, trips=410, prize='150 000 тенге'),
    row(P40, 'Алматы-Каскелен', 3, 'Третьяк В.', 'ZP000003', 103, amount=610000, trips=400, prize='100 000 тенге',
        city='Каскелен'),
    row(P40, 'Астана', 1, 'Астанов Г.', 'ZP000004', 104, amount=690000, trips=420, prize='200 000 тенге', city='Астана'),
    row(P40, 'Астана', 2, 'Сериков Д.', 'ZP000005', 105, amount=640000, trips=405, prize='150 000 тенге', city='Астана'),
    row(P40, 'Астана', 3, 'Жумабай Е.', 'ZP000006', 106, amount=600000, trips=390, prize='100 000 тенге', city='Астана'),
    row(P40, 'МОТО БАЙГА', 1, 'Мотов И.', 'ZP000007', 107, amount=300000, trips=200, prize='50 000 тенге'),
    row(P40, 'Петропавловск-Уральск-Усть-Каме', 1, 'Каменев Ж.', 'ZP000008', 108, amount=500000, trips=300,
        prize='200 000 тенге', city='Усть-Каменогорск'),
    row(P40, 'Петропавловск-Уральск-Усть-Каме', 2, 'Уральский З.', 'ZP000009', 109, amount=480000, trips=290,
        prize='150 000 тенге', city='Уральск'),
    row(P39, 'Алматы-Каскелен', 1, 'Старов К.', 'ZP000010', 110, amount=680000, trips=415, prize='200 000 тенге'),
    row(P39, 'Алматы-Каскелен', 2, 'Первов А.', 'ZP000001', 101, amount=660000, trips=412, prize='150 000 тенге'),
    row(P39, 'Астана', 1, 'Астанов Г.', 'ZP000004', 104, amount=670000, trips=418, prize='200 000 тенге', city='Астана'),
]


def places(question, prior=(), *, weeks=PLACE_WEEKS, lists=None):
    """Фрагменты к вопросу на неделях, где места идут с первого."""
    return fragments(question, prior, weeks=weeks, rows=PLACE_ROWS, lists=lists)


def line(name, license_value, zachet, position, amount, trips, prize):
    return '%s (номер ВУ %s): зачёт «%s», %d место, сумма за неделю %s ₸, поездок %d, приз %s.' % (
        name, license_value, zachet, position, amount, trips, prize)


class Lists:
    """Списки в памяти вместо базы: те же три вопроса, что задаёт queries.py."""

    def __init__(self, rows=ROWS):
        self.rows = list(rows)
        self.calls = []

    def find(self, driver_keys=(), license_keys=(), name_regex=None):
        self.calls.append(('find', tuple(driver_keys), tuple(license_keys), name_regex))
        found = {}
        for item in sorted(self.rows, key=lambda r: r['period_start'], reverse=True):
            fits = (item['driver_key'] in driver_keys
                    or (item['license_key'] and item['license_key'] in license_keys)
                    or bool(name_regex and re.search(name_regex, item['search_text'])))
            if fits:
                found.setdefault(item['driver_key'], {
                    name: item[name] for name in queries.ASSISTANT_CANDIDATE_FIELDS})
        # Как в запросе (DISTINCT ON … ORDER BY r.driver_key): порядок — по ID,
        # к неделе и к месту он отношения не имеет.
        return [found[key] for key in sorted(found)]

    def load(self, driver_keys=(), license_keys=()):
        self.calls.append(('load', tuple(driver_keys), tuple(license_keys)))
        rows = [item for item in self.rows
                if item['driver_key'] in driver_keys
                or (item['license_key'] and item['license_key'] in license_keys)]
        return sorted(rows, key=lambda r: (-r['period_start'].toordinal(), r['sheet_order'],
                                           r['position']))

    def places(self, upload_id=None, zachets=(), first=1, last=1, limit=None):
        self.calls.append(('places', upload_id, tuple(zachets), first, last, limit))
        rows = [item for item in self.rows
                if item['upload_id'] == upload_id and first <= item['position'] <= last
                and (not zachets or item['zachet'] in zachets)]
        return sorted(rows, key=lambda r: (r['sheet_order'], r['position']))[:limit]


def analyze(text):
    return assistant.analyze(text, today=TODAY)


def fragments(question, prior=(), *, weeks=WEEKS, rows=ROWS, lists=None):
    """Фрагменты к вопросу — как их соберёт ai_rows после проверки доступа.
    prior — прошлые вопросы человека, от старого к новому."""
    lists = lists or Lists(rows)
    own = analyze(question)
    priors = [analyze(text) for text in list(prior)[-assistant.PRIOR_TURNS:]]
    if not assistant._worth_asking(own, priors):
        return []
    return assistant.build_rows(own, priors, weeks=list(weeks), find=lists.find, load=lists.load,
                                load_places=lists.places)


def keys(found):
    return [item['ref_key'] for item in found]


def texts(found):
    return [item['text'] for item in found]


def names(text):
    return [token['fold'] for token in analyze(text)['tokens'] if token['kind'] == 'name']


# ─────────────────────────────────────────────────────────────────────────────
# Доступ
# ─────────────────────────────────────────────────────────────────────────────

class AccessTests(unittest.TestCase):

    def reads(self, ctx, qr):
        asked = []

        def granted(user_id, cursor=None):
            asked.append((user_id, cursor))
            return qr

        cursor = object()
        with mock.patch.object(queries, 'load_access_context', lambda cur, user_id: ctx):
            opened = assistant.reader_context(cursor, 10, sensitive_access_granted=granted)
        return opened is not None, asked, cursor

    def test_the_circle_named_by_the_owner(self):
        """Поимённо, а не формулой: кому помощник читает списки и кому нужен QR."""
        always = [person('super_admin', None), person('admin', 'marketing', ('marketing',)),
                  person('admin', 'op', ('op',)), person('admin', 'szov', ('szov',)),
                  person('sv', 'op'), person('supervisor', 'szov')]
        after_qr = [person('operator', 'op'), person('operator', 'szov'),
                    person('marketing_manager', 'marketing'), person('operator', 'marketing')]
        never = [person('operator', 'tez'), person('admin', 'tez', ('tez',)), person('sv', 'tez'),
                 person('trainer', 'szov'), person('trainee', 'op'), person('admin', 'szov'),
                 person('sv', 'marketing'), person('operator', 'front_office'),
                 person('admin', 'front_office', ('front_office',))]
        for ctx in always:
            for qr in (False, True):
                self.assertTrue(self.reads(ctx, qr)[0], (ctx['role'], ctx['department_code']))
        for ctx in after_qr:
            self.assertFalse(self.reads(ctx, False)[0], (ctx['role'], ctx['department_code']))
            self.assertTrue(self.reads(ctx, True)[0], (ctx['role'], ctx['department_code']))
        for ctx in never:
            for qr in (False, True):
                self.assertFalse(self.reads(ctx, qr)[0], (ctx['role'], ctx['department_code']))

    def test_same_two_gates_as_the_section_routes(self):
        """Помощник — второй путь к строкам, и гейты у него те же, что у ручек
        раздела (baiga_route), на всей сетке ролей, отделов и главенства."""
        for role, code, heads in grid():
            ctx = person(role, code, heads)
            for qr in (False, True):
                by_route = access.can_open_section(ctx) and (
                    not access.requires_sensitive_qr(ctx) or qr)
                self.assertEqual(self.reads(ctx, qr)[0], by_route, (role, code, heads, qr))

    def test_qr_is_asked_about_this_person_on_this_cursor(self):
        _opened, asked, cursor = self.reads(person('operator', 'op'), True)
        self.assertEqual(asked, [(10, cursor)])
        # Кого замок не спрашивает и кому раздел закрыт — про QR не спрашивают вовсе.
        self.assertEqual(self.reads(person('sv', 'op'), True)[1], [])
        self.assertEqual(self.reads(person('operator', 'tez'), True)[1], [])

    def test_named_analyst_reads_without_qr_like_in_the_section(self):
        """Аналитику из именного списка раздел открыт без замка (решение
        владельца 06.10.2026) — и помощник читает ему списки так же, не
        спрашивая QR. Сосед по отделу с тем же профилем не читает вовсе."""
        analyst = person('operator', 'analytik', user_id=min(access.ANALYST_USER_IDS))
        for qr in (False, True):
            opened, asked, _cursor = self.reads(analyst, qr)
            self.assertTrue(opened, qr)
            self.assertEqual(asked, [], qr)
        neighbour = person('operator', 'analytik', user_id=max(access.ANALYST_USER_IDS) + 1)
        for qr in (False, True):
            self.assertFalse(self.reads(neighbour, qr)[0], qr)

    def test_unknown_person_reads_nothing(self):
        self.assertFalse(self.reads(None, True)[0])

    def test_assistant_of_tez_space_reads_no_lists(self):
        asked = []

        def spaces(cursor, codes):
            asked.append(tuple(codes))
            return [9]

        with mock.patch('wiki.structure.space_ids_for_departments', spaces):
            self.assertFalse(assistant.space_reads_lists(object(), 9))
            self.assertTrue(assistant.space_reads_lists(object(), 1))
        self.assertEqual(set(asked), {('tez',)})
        # Нет пространства — нет помощника: читать некому.
        self.assertFalse(assistant.space_reads_lists(object(), None))


class GateOrderTests(unittest.TestCase):
    """ai_rows: право — до данных, а вопрос не про Байгу базы не касается."""

    def setUp(self):
        self.lists = Lists()
        self.viewer = person('super_admin', None)
        self.touched = []
        test = self

        def weeks(cursor, campaign):
            test.touched.append('weeks')
            test.assertEqual(campaign, parse.CAMPAIGN)
            return list(WEEKS)

        def candidates(cursor, limit=None, **found_by):
            test.touched.append('find')
            test.assertEqual(limit, assistant.CANDIDATE_LIMIT)
            return test.lists.find(**found_by)

        def context(cursor, user_id):
            test.touched.append('context')
            return test.viewer

        def rows(cursor, **found_by):
            test.touched.append('load')
            return test.lists.load(**found_by)

        def spaces(cursor, codes):
            test.touched.append('spaces')
            return [9]

        def by_place(cursor, **asked):
            test.touched.append('places')
            test.assertEqual(asked['limit'], assistant.RANK_ROWS + 1)
            return test.lists.places(**asked)

        patches = mock.patch.multiple(queries, load_access_context=context, list_weeks=weeks,
                                      assistant_candidates=candidates, assistant_rows=rows,
                                      assistant_places=by_place)
        patches.start()
        self.addCleanup(patches.stop)
        patch = mock.patch('wiki.structure.space_ids_for_departments', spaces)
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, question, space_id=1, qr=True, prior=()):
        self.touched = []
        return assistant.ai_rows(
            mock.MagicMock(), user_id=10, question=question, space_id=space_id, prior=prior,
            today=TODAY, sensitive_access_granted=lambda user_id, cursor=None: qr)

    def test_answer_for_who_may_read(self):
        found = self.ask('ВУ ZZ123456 какое место на прошлой неделе')
        self.assertEqual(len(found), 1)
        self.assertIn('1 место', found[0]['text'])
        self.assertEqual(self.touched, ['spaces', 'context', 'weeks', 'find', 'load'])

    def test_closed_section_means_no_rows_and_no_reading(self):
        self.viewer = person('trainer', 'szov')
        self.assertEqual(self.ask('ВУ ZZ123456 какое место'), [])
        self.assertEqual(self.touched, ['spaces', 'context'])

    def test_unconfirmed_session_means_no_rows_and_no_reading(self):
        self.viewer = person('operator', 'op')
        self.assertEqual(self.ask('ВУ ZZ123456 какое место', qr=False), [])
        self.assertEqual(self.touched, ['spaces', 'context'])
        self.assertEqual(len(self.ask('ВУ ZZ123456 какое место', qr=True)), 1)

    def test_tez_space_stops_even_the_super_admin(self):
        self.assertEqual(self.ask('ВУ ZZ123456 какое место', space_id=9), [])
        self.assertEqual(self.touched, ['spaces'])

    def test_other_questions_never_touch_the_database(self):
        for question in ('как подключить тариф комфорт', 'адрес офиса в Алматы', 'спасибо',
                         'какие призы в байге', 'сколько заработал водитель',  # водитель не назван
                         'ZZ123456', 'Жусипов Алмас'):                         # нет слова темы
            self.assertEqual(self.ask(question), [], question)
            self.assertEqual(self.touched, [], question)

    def test_word_that_is_no_driver_costs_one_lookup_and_no_rows(self):
        """Слово темы есть, а фамилия ли остальное — знает только база. Строк под
        такой вопрос нет, и до них дело не доходит."""
        for question in ('сколько поездок нужно для бонуса', 'в каком городе есть аренда'):
            self.assertEqual(self.ask(question), [], question)
            self.assertEqual(self.touched, ['spaces', 'context', 'weeks', 'find'], question)
        # Вопрос об условиях акции: место названо, но не спрошено «кто» и не назван зачёт.
        self.assertEqual(self.ask('какой приз за первое место в байге'), [])
        self.assertEqual(self.touched, ['spaces', 'context', 'weeks'])

    def test_question_about_a_place_passes_the_same_gates(self):
        """«Кто занял первое место» — тот же второй путь к строкам: право до данных."""
        question = 'кто на прошлой неделе в алматы занял первое место?'
        found = self.ask(question)
        self.assertEqual(keys(found), ['ZZ123456'])
        self.assertEqual(self.touched, ['spaces', 'context', 'weeks', 'places'])
        self.assertEqual(self.lists.calls, [('places', W40['id'], ('Алматы-Каскелен',), 1, 1,
                                             assistant.RANK_ROWS + 1)])
        self.viewer = person('trainer', 'szov')
        self.assertEqual(self.ask(question), [])
        self.assertEqual(self.touched, ['spaces', 'context'])
        self.viewer = person('operator', 'op')
        self.assertEqual(self.ask(question, qr=False), [])
        self.assertEqual(self.touched, ['spaces', 'context'])
        self.assertEqual(keys(self.ask(question, qr=True)), ['ZZ123456'])
        self.viewer = person('super_admin', None)
        self.assertEqual(self.ask(question, space_id=9), [])
        self.assertEqual(self.touched, ['spaces'])
        # Место названо, водителя нет — вопрос всё равно «про Байгу».
        self.ask('кто занял первое место')
        self.assertEqual(self.touched, ['spaces', 'context', 'weeks', 'places'])

    def test_case_of_letters_does_not_decide(self):
        """Целиком строчными пишут часто: судить о слове по заглавной букве
        нельзя — фамилию в нём узнаёт список."""
        for question in ('сколько поездок у жусипова за неделю', 'Сколько поездок у жусипова за неделю',
                         'СКОЛЬКО ПОЕЗДОК У ЖУСИПОВА ЗА НЕДЕЛЮ', 'итоги водителя ВУ ZZ123456'):
            self.assertEqual(keys(self.ask(question)), ['ZZ123456'], question)

    def test_follow_up_reads_the_driver_from_previous_turns(self):
        self.assertEqual(keys(self.ask('а приз какой?', prior=['Жусипов какое место в байге'])),
                         ['ZZ123456'])
        # Разговор мог начаться и со слабого слова темы.
        self.assertEqual(keys(self.ask('а приз какой?', prior=['Жусипов сколько поездок за неделю'])),
                         ['ZZ123456'])
        self.assertEqual(self.ask('а приз какой?', prior=['как оформить возврат']), [])
        self.assertEqual(self.touched, [])

    def test_conversation_remembers_a_dozen_questions_and_no_more(self):
        """Замер на стенде 05.10.2026: с памятью в три вопроса водитель терялся на
        пятом — «какое место», «сколько заработал», «а на прошлой», «какой приз»…"""
        more = ['а приз какой?', 'а сколько заработал?', 'а поездок?'] * 4
        self.assertEqual(assistant.PRIOR_TURNS, 12)
        named = ['Жусипов какое место в байге']
        self.assertEqual(keys(self.ask('а в каком он парке?', prior=named + more[:11])), ['ZZ123456'])
        # Тринадцатый с конца вопрос уже не в памяти — и водителя назвать некому.
        self.assertEqual(self.ask('а в каком он парке?', prior=named + more), [])
        self.assertEqual(self.touched, [])

    def test_empty_turns_are_not_turns(self):
        self.assertEqual(keys(self.ask('а приз какой?', prior=['Жусипов какое место в байге', '', '  '])),
                         ['ZZ123456'])


# ─────────────────────────────────────────────────────────────────────────────
# Разбор реплики
# ─────────────────────────────────────────────────────────────────────────────

class TopicTests(unittest.TestCase):

    def test_three_degrees_of_the_topic(self):
        for text in ('байга', 'в байге', 'бәйге', 'списки байги'):
            self.assertEqual(assistant.topic(analyze(text)), 3, text)
        for text in ('какое место занял', 'на какой позиции', 'какой приз', 'сколько заработал',
                     'в топе', 'қандай жүлде берілді', 'нешінші орын алды', 'қанша тапты', 'ұтты ма'):
            self.assertEqual(assistant.topic(analyze(text)), 2, text)
        for text in ('сколько поездок', 'на этой неделе', 'есть в списке', 'итоги', 'результат',
                     'в каком парке', 'из какого города', 'неделя 21.09.2026'):
            self.assertEqual(assistant.topic(analyze(text)), 1, text)
        for text in ('как подключить тариф', 'адрес офиса', 'признак блокировки', 'сумматор',
                     'спасибо', 'сколько получил'):
            self.assertEqual(assistant.topic(analyze(text)), 0, text)

    def test_surname_that_starts_like_a_topic_word_stays_a_surname(self):
        """«Байгабулов» — не слово «Байга»: иначе водитель перестал бы находиться,
        а вопрос о нём стал бы «вопросом про Байгу»."""
        for surname in ('Байгабулов', 'Орынбаев', 'Неделько', 'Городецкий', 'Найденов',
                        'Зачетин', 'Сумматов', 'Местоев', 'Аптаев', 'Табысов', 'Топорков',
                        'Парковский'):
            parsed = analyze('водитель %s' % surname)
            self.assertEqual(assistant.topic(parsed), 0, surname)
            self.assertEqual([token['text'] for token in parsed['tokens'] if token['kind'] == 'name'],
                             [surname])

    def test_service_words_are_not_names(self):
        self.assertEqual(names('скажи пожалуйста какое место занял водитель в байге на этой неделе'), [])
        self.assertEqual(names('Жусипов какое место занял'), ['жусипов'])
        # Казахское «кім» («кто») после свёртки совпало бы с фамилией «Ким».
        self.assertEqual(names('кім бүгін орын алды'), ['бугин'])
        self.assertEqual(names('кимге приз'), [])
        self.assertEqual(names('Ким какое место'), ['ким'])

    def test_surname_that_is_a_service_word_needs_to_be_given_as_a_name(self):
        """«Ли», «Нам», «Мен», «Им» — и фамилии, и служебные слова."""
        self.assertEqual(names('водитель Ли А. какое место занял'), ['ли'])
        self.assertEqual(names('Ли А. какое место'), ['ли'])
        self.assertEqual(names('водитель Нам какое место'), ['нам'])
        self.assertEqual(names('Нам какой приз положен'), [])
        self.assertEqual(names('есть ли приз'), [])
        self.assertEqual(names('ВОДИТЕЛЬ ЛИ ПОЛУЧИТ ПРИЗ'), [])            # заглавными — всё

    def test_initial_is_a_capital_letter_with_a_dot(self):
        def initials(text):
            return [token['fold'] for token in analyze(text)['tokens'] if token['kind'] == 'initial']

        self.assertEqual(initials('Жусипов А.Б. какое место'), ['а', 'б'])
        self.assertEqual(initials('Ахметова в байге'), [])
        self.assertEqual(initials('в Астане т. е. первое место, г. Алматы, д. 5'), [])

    def test_numbers_and_general_words(self):
        self.assertTrue(analyze('какой приз за 3 поездки')['numbers'])
        self.assertFalse(analyze('какой приз у ВУ ZZ123456 за неделю 21.09.2026')['numbers'])
        # Номер места — не «число в вопросе»: он разобран как место.
        self.assertFalse(analyze('какой приз за 3 место')['numbers'])
        for text in ('а когда приз?', 'а какие призы есть?', 'а сколько всего призовых мест?',
                     'а как получить приз?', 'а где список?', 'қашан береді?'):
            self.assertTrue(analyze(text)['general'], text)
        for text in ('а приз какой?', 'а сколько он заработал?', 'а какое место?'):
            self.assertFalse(analyze(text)['general'], text)


class KeyTests(unittest.TestCase):

    def keys(self, text):
        parsed = analyze(text)
        return ([(item['key'], item['explicit']) for item in parsed['licenses']],
                [item['key'] for item in parsed['drivers']])

    def test_license_as_people_type_it(self):
        for text in ('ZZ123456 какое место', 'zz123456', 'ZZ 123456', 'ZZ-123456', 'ВУ ZZ123456',
                     'в/у: ZZ123456', 'удостоверение № ZZ 123456', 'права zz123456',
                     'ву номер ZZ123456', 'ВУ ZZ 123 456 какое место'):
            self.assertEqual(self.keys(text)[0], [('ZZ123456', True)], text)
        # Русская раскладка: буквы-двойники — те же латинские.
        self.assertEqual(self.keys('ву ав123456')[0], [('AB123456', True)])
        # Номер, записанный числом, и нестандартный — как в файле.
        self.assertEqual(self.keys('ВУ 1234567890')[0], [('1234567890', True)])
        self.assertEqual(self.keys('ZZZZ9999999')[0], [('ZZZZ9999999', True)])

    def test_what_is_not_a_license(self):
        for text in ('занял место на 123456', 'заработал за 150000', 'ID 1234567', 'топ 100000',
                     # заглавные предлоги из букв-двойников латиницы
                     'ПРИЗ НА 500000 ТЕНГЕ', 'СУММА ОТ 200000', 'НЕ 123456', '+77070000011'):
            self.assertEqual(self.keys(text)[0], [], text)
        # Слово и число, а не серия и номер: ищем, но «нет в списке» не говорим.
        for text in ('ИП 123456', 'СВ 123456'):
            found = self.keys(text)[0]
            self.assertEqual([explicit for _key, explicit in found], [False], text)
        # Строчными так пишут слово и число: «кв 123456» серией «KB» не читается.
        for text in ('сумма ип 123456', 'кв 123456'):
            self.assertEqual(self.keys(text)[0], [], text)
        # Голые цифры — может быть и телефон: только поиск.
        self.assertEqual(self.keys('водитель 1234567890')[0], [('1234567890', False)])

    def test_driver_id(self):
        licenses, drivers = self.keys('байга %s' % fake_id(7).upper())
        self.assertEqual((licenses, drivers), ([], [fake_id(7)]))

    def test_key_letters_and_digits_are_not_read_twice(self):
        parsed = analyze('ВУ ZZ123456 какое место')
        self.assertEqual([token['fold'] for token in parsed['tokens'] if token['kind'] == 'name'], [])
        self.assertFalse(parsed['numbers'])
        self.assertIsNone(parsed['week']['asked'])


class WeekTests(unittest.TestCase):

    def asked(self, text):
        week = analyze(text)['week']['asked']
        if week is None:
            return None
        return week['number'] if week['kind'] == 'number' else (week['kind'], week['start'])

    def test_relative_weeks(self):
        monday = date(2026, 10, 5)
        for text in ('на этой неделе', 'на этой-то неделе', 'на этой то неделе', 'за текущую неделю',
                     'осы аптада', 'бұл аптада'):
            self.assertEqual(self.asked(text), ('current', monday), text)
        for text in ('на прошлой неделе', 'за прошлую неделю', 'предыдущая неделя', 'неделю назад',
                     'өткен аптада'):
            self.assertEqual(self.asked(text), ('previous', date(2026, 9, 28)), text)
        for text in ('на позапрошлой неделе', 'две недели назад', '2 недели назад'):
            self.assertEqual(self.asked(text), ('before_previous', date(2026, 9, 21)), text)
        for text in ('за неделю', 'за последнюю неделю', 'какое место'):
            self.assertIsNone(self.asked(text), text)

    def test_dates(self):
        monday = date(2026, 9, 21)
        for text in ('за неделю 21.09-27.09', '21.09.2026 – 27.09.2026', 'неделя 23.09.26',
                     'с 21 по 27 сентября', '25 сентября 2026', 'неделя 21.09.', 'неделя 21/09',
                     '2026 жылғы 24 қыркүйек'):
            self.assertEqual(self.asked(text), ('date', monday), text)
        # Без года — ближайшая: «28.12» в октябре — прошлый декабрь, а «13.10» —
        # на этой неделе, а не год назад.
        self.assertEqual(self.asked('неделя 28.12'), ('date', date(2025, 12, 22)))
        self.assertEqual(self.asked('неделя 13.10'), ('date', date(2026, 10, 12)))
        # Дата точнее слов: «на прошлой неделе, 21–27.09» — про 21.09.
        self.assertEqual(self.asked('на прошлой неделе 21.09-27.09'), ('date', monday))
        for text in ('у него 1.5 ставки', 'скидка 2.05%', 'версия 10.30', 'сумма 5.000'):
            self.assertIsNone(self.asked(text), text)

    def test_week_number_named_firmly(self):
        for text in ('39-я неделя', 'неделя № 39', 'за 39-ю неделю', '39-шы апта'):
            self.assertEqual(self.asked(text), 39, text)
        self.assertIsNone(self.asked('неделя № 60'))

    def test_week_number_that_may_be_something_else(self):
        """«На 39 неделе» — номер, «3 неделя подряд» — срок: решает то, загружена
        ли такая неделя (resolve_week), а «за 1 неделю» — срок всегда."""
        def number(text):
            week = analyze(text)['week']
            self.assertIsNone(week['asked'], text)
            return week['number']

        for text in ('на 39 неделе', 'неделя 39', '39 неделя', '39 аптада', '39-аптада',
                     'заработал за 39 неделю'):
            self.assertEqual(number(text), 39, text)
        self.assertEqual(number('за 11 неделю'), 11)
        for text in ('за 1 неделю', 'за 2 недели', 'итоги за 3 недели', 'неделя 3 место',
                     'заработал за 1 неделю', '2 апта'):
            self.assertIsNone(number(text), text)
        self.assertEqual(self.asked('на этой неделе 3 место'), ('current', date(2026, 10, 5)))

    def test_week_said_in_passing(self):
        def said(text):
            week = analyze(text)['week']
            return (week['bare'] and week['bare']['start'], week['shift'], week['unknown'])

        self.assertEqual(said('а на прошлой?'), (date(2026, 9, 28), 0, False))
        self.assertEqual(said('а за позапрошлую?'), (date(2026, 9, 21), 0, False))
        self.assertEqual(said('а неделей раньше?'), (None, 1, False))
        self.assertEqual(said('а за неделю до этого?'), (None, 1, False))
        for text in ('а в сентябре?', 'а в мае?', 'а раньше какое место было?'):
            self.assertEqual(said(text), (None, 0, True), text)
        self.assertEqual(said('а приз какой?'), (None, 0, False))

    def test_week_digits_are_not_a_license_or_a_number(self):
        parsed = analyze('неделя 21.09.2026 – 27.09.2026, 39-я неделя')
        self.assertEqual((parsed['licenses'], parsed['numbers']), ([], False))


class SurnameTests(unittest.TestCase):

    def test_case_endings(self):
        same = [('жусипова', 'жусипов'), ('жусипову', 'жусипов'), ('жусиповым', 'жусипов'),
                ('жусиповтын', 'жусипов'), ('кима', 'ким'), ('кимге', 'ким'),
                ('ивановой', 'иванова'), ('иванову', 'иванова'), ('иванов', 'иванова'),
                ('ли', 'ли'), ('нурлана', 'нурлан'), ('ковалевского', 'ковалевский'),
                ('ковалевским', 'ковалевский'), ('ковалевской', 'ковалевская'),
                ('толстого', 'толстой'), ('белому', 'белый'),
                # последняя буква фамилии заменяется: «у Жумабая», «Ковалю»
                ('жумабая', 'жумабай'), ('жумабаю', 'жумабай'), ('жумабаем', 'жумабай'),
                ('абая', 'абай'), ('коваля', 'коваль'), ('ковалю', 'коваль'), ('короля', 'король')]
        for word, surname in same:
            self.assertTrue(assistant.same_surname(word, surname), (word, surname))
        different = [('ахмет', 'ахметов'), ('ахметов', 'ахмет'), ('ким', 'кимбаев'), ('кимов', 'ким'),
                     ('жусипбеков', 'жусипов'), ('лия', 'ли'), ('ал', 'али'), ('иванович', 'иванов'),
                     ('иванович', 'иванова'), ('ивановна', 'иванова'), ('ивановских', 'иванова'),
                     ('иванко', 'иванова'), ('ковалевскими', 'ковалевский'), ('белого', 'бел'),
                     ('ого', 'ий'), ('жумабаев', 'жумабай'), ('ковалев', 'коваль'), ('ая', 'ай')]
        for word, surname in different:
            self.assertFalse(assistant.same_surname(word, surname), (word, surname))

    def test_database_is_asked_for_namesakes_only(self):
        """Выражение для search_text несёт то же правило, что same_surname: база
        отдаёт однофамильцев, а не всех на «Ахмет…»."""
        def found(words, name):
            pattern = assistant.names_regex(words)
            return bool(pattern and re.search(pattern, parse.search_text(name, 'ZZ000001', fake_id(1))))

        self.assertTrue(found(['жусипова'], 'Жүсіпов А. Б.'))
        self.assertTrue(found(['жусиповтын'], 'Жүсіпов А. Б.'))
        self.assertTrue(found(['ивановой'], 'Иванова М. С.'))
        self.assertTrue(found(['кима'], 'Ким В.'))
        self.assertTrue(found(['ким'], 'Ким-Заде Р.'))          # часть двойной фамилии
        self.assertTrue(found(['заде'], 'Ким-Заде Р.'))
        self.assertTrue(found(['иванов'], 'Иванов( А. Б.'))     # опечатка в ячейке файла
        self.assertTrue(found(['ли'], 'Ли А.'))
        self.assertTrue(found(['ковалевского'], 'Ковалевский А.'))
        self.assertTrue(found(['ковалевской'], 'Ковалевская А.'))
        self.assertTrue(found(['жумабая'], 'Жумабай Е.'))
        self.assertTrue(found(['ковалю'], 'Коваль П.'))
        self.assertFalse(found(['ким'], 'Кимбаев А.'))
        self.assertFalse(found(['ахмет'], 'Ахметов Д. К.'))
        self.assertFalse(found(['жусип'], 'Жүсіпов А. Б.'))
        self.assertFalse(found(['ли'], 'Лиев А.'))
        # Номер ВУ и ID лежат в той же строке — фамилией они не находятся.
        self.assertFalse(found(['zz'], 'Жүсіпов А. Б.'))
        # В выражение идут только буквы: всё прочее в него не попадает.
        self.assertIsNone(assistant.names_regex(['a.*', 'х|у', '']))

    def test_every_guess_is_a_true_case_form(self):
        """Что база отдала по выражению, то и same_surname признаёт: списки одни."""
        for word in ('жусипова', 'ивановой', 'ковалевского', 'жумабая', 'ковалю', 'кимге', 'ли'):
            for guess in assistant._surname_guesses(word):
                self.assertTrue(guess == word or assistant.same_surname(word, guess), (word, guess))


# ─────────────────────────────────────────────────────────────────────────────
# Кто назван и что отвечает список
# ─────────────────────────────────────────────────────────────────────────────

class DriverTests(unittest.TestCase):

    def test_by_license_whatever_the_spelling(self):
        for question in ('ВУ ZZ123456 какое место в байге', 'какой приз у zz 123456',
                         'сколько заработал ZZ123456 за неделю'):
            found = fragments(question)
            self.assertEqual(len(found), 1, question)
            self.assertIn('Жүсіпов А. Б. (номер ВУ ZZ123456): зачёт «Алматы-Каскелен», 1 место, '
                          'сумма за неделю 669 460 ₸, поездок 427, приз 200 000 тенге.',
                          found[0]['text'])

    def test_by_driver_id_and_by_number_license(self):
        self.assertIn('Ким-Заде Р.', fragments('байга %s' % fake_id(7))[0]['text'])
        found = fragments('водитель 1234567890 какое место занял на 39 неделе')
        self.assertIn('Ким В. (номер ВУ 1234567890): зачёт «Астана», 55 место', found[0]['text'])

    def test_by_surname_in_any_case_and_alphabet(self):
        for question in ('какое место у Жусипова', 'Жүсіпов какое место', 'приз жусипову',
                         'Жүсіповтың орны қандай?', 'на какой позиции ЖУСИПОВ А.Б. В БАЙГЕ',
                         'жусипов алмас какое место', 'Алмас Жусипов сколько заработал'):
            self.assertEqual(keys(fragments(question)), ['ZZ123456'], question)
        self.assertEqual(keys(fragments('какой приз у Ивановой')), ['ZW222222'])

    def test_surname_whose_last_letter_changes(self):
        """«Жумабай» — «у Жумабая», «Коваль» — «Ковалю»: фамилии на «-бай/-ай»
        массовые, и про них же помощник отвечал «такой фамилии нет»."""
        for question, key in (('какое место у Жумабая', 'ZJ000001'), ('Жумабаю какой приз', 'ZJ000001'),
                              ('какое место у Жумабая Е. в байге', 'ZJ000001'),
                              ('приз Коваля', 'ZJ000002'), ('водитель Коваль П. какое место', 'ZJ000002')):
            found = fragments(question)
            self.assertEqual(keys(found), [key], question)
            self.assertNotIn('нет', found[0]['heading_path'])

    def test_surname_that_is_a_service_word(self):
        for question in ('водитель Ли А. какое место занял', 'Ли А. какое место',
                         'какой приз у водителя Ли'):
            self.assertEqual(keys(fragments(question)), ['ZJ000003'], question)

    def test_both_sheets_of_one_week_are_one_driver(self):
        text = fragments('ВУ ZZ123456 какое место')[0]['text']
        self.assertIn('зачёт «Алматы-Каскелен», 1 место', text)
        self.assertIn('зачёт «МОТО БАЙГА», 5 место, сумма за неделю 180 300 ₸, поездок 60, '
                      'приз 20 000 тенге', text)

    def test_namesakes_come_together_and_initials_narrow(self):
        self.assertEqual(keys(fragments('какое место у Ахметова в байге')), ['ZY654321', 'ZX111111'])
        self.assertEqual(keys(fragments('какое место у Ахметова Е. в байге')), ['ZX111111'])
        self.assertEqual(keys(fragments('Ахметов Д.К. какой приз')), ['ZY654321'])
        # Имя словом однофамильцев не сужает — это оставлено модели; опечатка в
        # инициале никого не прячет.
        self.assertEqual(len(fragments('Ахметов Данияр какой приз')), 2)
        self.assertEqual(len(fragments('Ахметов Я. какой приз')), 2)

    def test_preposition_is_not_an_initial(self):
        """«Ахметова в байге»: «в» — предлог. Инициал — заглавная буква с точкой."""
        pair = [row(W40, 'Астана', 4, 'Ахметов В.', 'ZA000001', 51, amount=300000, trips=90, city='Астана'),
                row(W40, 'Астана', 9, 'Ахметов Д.', 'ZA000002', 52, amount=290000, trips=80, city='Астана')]
        self.assertEqual(len(fragments('какое место у Ахметова в байге', rows=pair)), 2)
        self.assertEqual(len(fragments('АХМЕТОВ В БАЙГЕ КАКОЕ МЕСТО', rows=pair)), 2)   # заглавная без точки
        self.assertEqual(keys(fragments('какое место у Ахметова В. в байге', rows=pair)), ['ZA000001'])

    def test_drivers_of_the_asked_week_come_first(self):
        """База отдаёт однофамильцев по ID; при однофамильцах ответ — о тех, кто в
        спрошенном списке есть."""
        pair = [row(W39, 'Астана', 4, 'Сериков А.', 'ZS000001', 40, amount=300000, trips=90, city='Астана'),
                row(W40, 'Астана', 9, 'Сериков Б.', 'ZS000002', 41, amount=290000, trips=80, city='Астана')]
        found = fragments('какое место у Серикова', rows=pair)
        self.assertEqual(keys(found), ['ZS000002', 'ZS000001'])
        self.assertIn('водителя Сериков А. (номер ВУ ZS000001) нет', found[1]['text'])

    def test_double_surname_is_one_surname(self):
        self.assertEqual(keys(fragments('Ким-Заде какое место занял')), ['ZU444444'])
        # Часть двойной фамилии находит и её носителя, и однофамильца части.
        self.assertEqual(sorted(keys(fragments('какое место у Кима на 39 неделе'))),
                         ['1234567890', 'ZU444444'])
        # Двойной фамилии в списке нет — однофамильцы по её частям не приходят:
        # ни по первой, ни по второй.
        self.assertEqual(fragments('Ким-Оглы какое место занял'), [])
        self.assertEqual(fragments('Оглы-Ахметов в байге какое место'), [])
        found = fragments('какое место занял Ким-Оглы')
        self.assertEqual(len(found), 1)
        self.assertIn('водителя с фамилией «Ким-Оглы» нет', found[0]['text'])

    def test_one_person_under_two_ids_is_one_fragment(self):
        """Водитель, заведённый в парке заново: ID новый, номер ВУ прежний."""
        found = fragments('ВУ ZX111111 какое место на 39 неделе')
        self.assertEqual(len(found), 1)
        self.assertIn('61 место', found[0]['text'])
        # Назван нынешним ID — строка той недели, где он значился под прежним.
        found = fragments('байга %s на 39 неделе' % fake_id(3))
        self.assertEqual(len(found), 1)
        self.assertIn('61 место', found[0]['text'])

    def test_one_id_with_the_license_written_differently_is_one_person(self):
        """Тот же ID, а номер ВУ в одной из недель записан иначе или пуст: неделя
        не выпадает, и про водителя из списка не говорится «в списке нет»."""
        for other in ('ZQ12345', ''):
            pair = [row(W40, 'Астана', 4, 'Сериков А.', 'ZQ123456', 60, amount=300000, trips=90, city='Астана'),
                    row(W39, 'Астана', 8, 'Сериков А.', other, 60, amount=250000, trips=70, city='Астана')]
            for question in ('Сериков какое место на 39 неделе', 'ВУ ZQ123456 какое место на 39 неделе'):
                found = fragments(question, rows=pair)
                self.assertEqual(len(found), 1, (other, question))
                self.assertIn('8 место, сумма за неделю 250 000 ₸', found[0]['text'], (other, question))

    def test_no_prize_is_said_plainly(self):
        self.assertIn('поездок 90, приза нет.', fragments('ВУ ZX111111 приз')[0]['text'])

    def test_city_and_park_of_the_row(self):
        self.assertIn('Город: Астана. Таксопарк: «Тестовый парк».',
                      fragments('ВУ ZX111111 приз')[0]['text'])

    def test_driver_must_be_named(self):
        for question in ('какой приз за первое место в байге', 'какие призы в байге',
                         'водитель говорит что не получил приз', 'сколько заработал водитель',
                         'выгрузи список байги за неделю', 'покажи всех водителей байги'):
            self.assertEqual(fragments(question), [], question)

    def test_no_more_than_six_drivers_go_to_the_model(self):
        crowd = [row(W40, 'Астана', 10 + number, 'Сериков %s.' % 'АБВГДЕЖЗ'[number],
                     'ZS%06d' % number, 20 + number, amount=200000, trips=50, city='Астана')
                 for number in range(8)]
        found = fragments('какое место у Серикова', rows=crowd)
        self.assertEqual(len(found), assistant.MAX_PEOPLE + 1)
        self.assertIn('подходят 8 водителей, показаны первые 6', found[-1]['text'])
        self.assertIn('номеру ВУ', found[-1]['text'])


class ExtraRowTests(unittest.TestCase):
    """Главный враг — лишняя строка: слово, совпавшее с фамилией из списка,
    водителя ещё не называет."""

    def test_word_that_matches_a_surname_is_not_a_driver(self):
        """Вопросы операторов, сработавшие зря на состязательном разборе."""
        for question in ('оператор Ахметова заняла первое место в рейтинге операторов',
                         'ИП Ахметов сумма налога',
                         'СВ Иванова сказала что приз выдают в офисе',
                         'шины менял, сумма',                               # «Шин О.»
                         'Мороз на улице, водитель не выходит, рейтинг упадет?',
                         'Водитель около Ким Шатыр, не может найти место подачи',
                         'ким первый занял место',                          # казахское «кім»
                         'кимге приз',
                         'сколько поездок нужно Жусипову для бонуса',
                         'ВУ ZZ123456 заблокирован на неделю'):
            self.assertEqual(fragments(question), [], question)

    def test_weaker_topic_asks_for_a_cleaner_question(self):
        # «Байга» в вопросе — водителя называет любое совпавшее слово.
        self.assertEqual(keys(fragments('Жусипов говорил что в байге был первым, проверь')), ['ZZ123456'])
        # Место, приз, заработок — фамилия подана как ФИО…
        self.assertEqual(keys(fragments('водитель Жусипов утверждает что занял первое место')), ['ZZ123456'])
        self.assertEqual(keys(fragments('Жусипов А. утверждает что занял первое место')), ['ZZ123456'])
        # …или в вопросе нет посторонних слов.
        self.assertEqual(keys(fragments('Жусипов какое место занял')), ['ZZ123456'])
        self.assertEqual(keys(fragments('Жусипов утверждает что занял первое место')), ['ZZ123456'])
        self.assertEqual(fragments('Жусипов утверждает что приз не начислили'), [])
        # Номер ВУ — имя твёрдое.
        self.assertEqual(keys(fragments('ZZ123456 утверждает что занял первое место')), ['ZZ123456'])
        # Неделя, поездки, список — только вопрос без посторонних слов.
        self.assertEqual(keys(fragments('Жусипов сколько поездок сделал за неделю')), ['ZZ123456'])
        self.assertEqual(keys(fragments('жусипов есть в списке?')), ['ZZ123456'])
        self.assertEqual(keys(fragments('итоги водителя ВУ ZZ123456')), ['ZZ123456'])
        self.assertEqual(fragments('водитель Жусипов опоздал на неделю'), [])
        # Названная неделя — тоже слово темы: «Жусипов 21.09-27.09».
        found = fragments('Жусипов 21.09-27.09')
        self.assertEqual(keys(found), ['ZZ123456'])
        self.assertIn('(неделя № 39).', found[0]['text'])
        self.assertEqual(fragments('Жусипов с 21.09.2026 в отпуске'), [])

    def test_city_and_park_of_the_driver_are_not_stray_words(self):
        self.assertEqual(keys(fragments('Жусипов из Алматы какое место')), ['ZZ123456'])
        self.assertEqual(keys(fragments('Мороз из парка Глобал какое место')), ['ZJ000005'])
        self.assertEqual(keys(fragments('мороз глобал какое место')), ['ZJ000005'])

    def test_first_name_must_agree_with_the_initial(self):
        """«шины менял» совпадает с фамилией Шин, но «менял» — не имя Шина О."""
        self.assertEqual(keys(fragments('шин олег какое место')), ['ZJ000004'])
        self.assertEqual(fragments('шины менял какое место'), [])
        # В списке одна фамилия, без инициалов: сверить имя не с чем, и именем
        # считается только слово с заглавной.
        alone = [row(W40, 'Астана', 6, 'Одинцов', 'ZO000001', 45, amount=300000, trips=90, city='Астана')]
        self.assertEqual(keys(fragments('Одинцов Иван какое место', rows=alone)), ['ZO000001'])
        self.assertEqual(fragments('одинцов уехал какое место', rows=alone), [])


class MissingTests(unittest.TestCase):

    def test_unknown_license_is_an_answer(self):
        found = fragments('байга ву XX999999')
        self.assertEqual(texts(found), [
            'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) водителя с номером ВУ '
            'XX999999 нет.\nВ остальных загруженных списках его тоже нет.'])
        self.assertEqual(found[0]['ref_key'], 'XX999999')
        # Одна неделя в разделе — «остальных списков» нет.
        self.assertEqual(texts(fragments('байга ву XX999999', weeks=[W40])), [
            'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) водителя с номером ВУ '
            'XX999999 нет.'])

    def test_bare_digits_are_never_declared_missing(self):
        self.assertEqual(fragments('водитель 87070000011 какое место занял'), [])

    def test_driver_absent_this_week_but_known(self):
        found = fragments('Ким В. какое место занял на прошлой неделе')
        self.assertEqual(texts(found), [
            'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) водителя Ким В. '
            '(номер ВУ 1234567890) нет.\nПоследний список, в котором он есть, — за неделю '
            '21.09.2026 – 27.09.2026 (неделя № 39): зачёт «Астана», 55 место, сумма за неделю '
            '240 000 ₸, поездок 80, приза нет.'])
        # Чип ведёт туда, где строка есть.
        self.assertEqual((found[0]['ref_id'], found[0]['ref_key']), (W39['id'], '1234567890'))

    def test_unknown_surname_given_as_a_full_name(self):
        for question in ('водитель Петров Иван какой приз получил', 'какое место у Петрова И. в байге',
                         'ФИО Петров И. какое место'):
            found = fragments(question)
            self.assertEqual(len(found), 1, question)
            self.assertIn('нет — поиск шёл по фамилии.', found[0]['text'])
            self.assertIn('В остальных загруженных списках такой фамилии тоже нет.', found[0]['text'])
            self.assertIn('Точнее всего водителя находит номер ВУ.', found[0]['text'])
        self.assertIn('водителя «Петров Иван» нет', fragments(
            'водитель Петров Иван какой приз получил')[0]['text'])

    def test_one_capital_word_is_only_a_guess(self):
        for question in ('какое место занял Петров', 'водитель Петров какое место занял',
                         'ФИО Петров, какое место'):
            self.assertEqual(texts(fragments(question)), [
                'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) водителя с фамилией '
                '«Петров» нет, в остальных загруженных списках — тоже.\n'
                'Если «Петров» — не фамилия водителя, этот фрагмент к вопросу не относится.'], question)
        self.assertIn('«Петров» нет.\n', fragments('какое место занял Петров', weeks=[W40])[0]['text'])

    def test_ordinary_words_are_never_declared_a_driver(self):
        for question in ('водитель говорит что не получил приз', 'какое место занял петров',
                         # город зачёта — в любом падеже
                         'какой приз в Байге по Алматы',
                         'какое место в зачёте Астана', 'Какие призы в Байге в Астане',
                         'Сколько мест призовых в Караганде', 'какие призы в байге в Каскелене',
                         # сервис, тариф, акция
                         'какое место в рейтинге Яндекс', 'какой приз получит водитель Яндекса за первое место',
                         'водитель Тез такси какой приз может получить',
                         'водитель Комфорт класса сумма бонуса', 'Какой приз в Лимонопад',
                         'какой приз в зачёте МОТО',                        # слово названия зачёта
                         'водитель опаздывает, какой приз в байге',         # строчное после «водитель»
                         'какой приз за 3 место в Жетысу',                  # число — вопрос об условиях
                         # сокращения — не инициалы
                         'Какой приз в Байге в Астане т. е. в столице',
                         'Сумма заказа г. Алматы ул. Абая д. 5',
                         'КАКОЕ МЕСТО ЗАНЯЛ ВОДИТЕЛЬ ВЧЕРА ВЕЧЕРОМ',        # заглавными — всё
                         'ПРИЗ НА 500000 ТЕНГЕ КАК ПОЛУЧИТЬ',
                         # Первое слово вопроса с заглавной пишут всегда — фамилию
                         # в нём узнаёт только список.
                         'Петров какое место занял', 'Нужна сумма приза'):
            self.assertEqual(fragments(question), [], question)

    def test_known_driver_is_not_reported_missing_for_first_name(self):
        found = fragments('водитель Жусипов Алмас какой приз получил')
        self.assertEqual(len(found), 1)
        self.assertIn('200 000 тенге', found[0]['text'])


class PlaceTests(unittest.TestCase):
    """Вопрос по месту: «кто на прошлой неделе в Алматы занял первое место?» —
    дословно вопрос владельца шарику 05.10.2026, на который списки промолчали."""

    HEAD = 'Список Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40)'
    FIRST = [line('Первов А.', 'ZP000001', 'Алматы-Каскелен', 1, '700 000', 430, '200 000 тенге'),
             line('Астанов Г.', 'ZP000004', 'Астана', 1, '690 000', 420, '200 000 тенге'),
             line('Мотов И.', 'ZP000007', 'МОТО БАЙГА', 1, '300 000', 200, '50 000 тенге'),
             line('Каменев Ж.', 'ZP000008', 'Петропавловск-Уральск-Усть-Каме', 1, '500 000', 300, '200 000 тенге')]
    ASTANA = [FIRST[1], line('Сериков Д.', 'ZP000005', 'Астана', 2, '640 000', 405, '150 000 тенге'),
              line('Жумабай Е.', 'ZP000006', 'Астана', 3, '600 000', 390, '100 000 тенге')]

    def rank(self, text):
        found = analyze(text)['rank']
        return found and (found['first'], found['last'])

    def test_place_as_people_say_it(self):
        for text in ('кто занял первое место', 'на первом месте', '1 место', '1-е место', 'место № 1',
                     'кім бірінші орын алды', '1-орын', 'победитель недели', 'кто победил', 'кто выиграл',
                     'лидер зачёта', 'жеңімпаз кім'):
            self.assertEqual(self.rank(text), (1, 1), text)
        for text, place in (('второе место', 2), ('третьего места', 3), ('на 35-м месте', 35),
                            ('105 место', 105), ('десятое место', 10), ('екінші орын', 2)):
            self.assertEqual(self.rank(text), (place, place), text)
        for text, last in (('топ-3', 3), ('топ 5', 5), ('первая тройка', 3), ('тройка лидеров', 3),
                           ('первые пять мест', 5), ('первые 3 места', 3), ('первая десятка', 10),
                           ('лидеры', 3), ('топ-30', assistant.RANK_TOP)):
            self.assertEqual(self.rank(text), (1, last), text)
        self.assertEqual(analyze('топ-30')['rank']['asked'], 30)
        for text in ('35 призовых мест', 'сколько мест', 'место подачи', 'первый раз', 'в первую очередь',
                     'у него 1.5 ставки', 'на 39 неделе', 'место 5 раз'):
            self.assertIsNone(self.rank(text), text)

    def test_who_is_asked(self):
        for text in ('кто занял первое место', 'кому достался приз', 'кім бірінші', 'победитель в Астане',
                     'топ-3 в Астане'):
            self.assertTrue(analyze(text)['who'], text)
        for text in ('какой приз за первое место', 'ким первый занял место', 'первое место в Астане'):
            self.assertFalse(analyze(text)['who'], text)

    def test_place_words_are_neither_names_nor_numbers(self):
        parsed = analyze('кто на прошлой неделе в алматы занял 1 место?')
        self.assertEqual((names('кто на прошлой неделе в алматы занял первое место?'), parsed['numbers']),
                         (['алматы'], False))
        # Место цифрой помнится: «за 3 место» спрашивают и об условиях акции.
        self.assertTrue(parsed['rank']['digits'])
        self.assertFalse(analyze('первое место')['rank']['digits'])

    def test_the_owners_question(self):
        found = places('кто на прошлой неделе в алматы занял первое место?')
        self.assertEqual(texts(found), [
            self.HEAD + '.\n' + self.FIRST[0] + '\nГород: Алматы. Таксопарк: «Тестовый парк».'])
        item = found[0]
        self.assertEqual((item['heading_path'], item['ref_id'], item['ref_key'], item['evidence']),
                         ('Неделя 28.09.2026 – 04.10.2026 › Алматы-Каскелен', P40['id'], 'ZP000001', self.FIRST[0]))

    def test_city_of_the_question_is_the_zachet(self):
        """Место считается внутри зачёта — листа файла. Город называют как угодно:
        в падеже, по-старому, по-казахски; «Усть-Каменогорск» в названии листа
        обрезан пределом Excel."""
        for question, key in (('кто победил в Астане', 'ZP000004'), ('победитель по Алмате', 'ZP000001'),
                              ('кто первый в Каскелене', 'ZP000001'),
                              ('кто занял первое место в Усть-Каменогорске', 'ZP000008'),
                              ('кто занял первое место в Оскемене', 'ZP000008'),
                              ('кто выиграл в Уральске', 'ZP000008'), ('кто первый в мото байге', 'ZP000007'),
                              ('Астанада кім бірінші орын алды', 'ZP000004'),
                              ('первое место в Астане', 'ZP000004'),          # зачёт назван — «кто» не нужно
                              ('какой приз за первое место в Астане', 'ZP000004')):
            self.assertEqual(keys(places(question)), [key], question)
        self.assertEqual(keys(places('кто занял первое место в Алматы и Астане')), ['ZP000001', 'ZP000004'])

    def test_no_zachet_means_this_place_in_every_zachet(self):
        for question in ('кто занял первое место', 'кто победил на прошлой неделе', 'победители байги',
                         'кім бірінші орын алды'):
            found = places(question)
            self.assertEqual(len(found), 1, question)
            self.assertEqual(found[0]['text'].split('\n')[1:], ['1 место в каждом зачёте:'] + self.FIRST, question)
            self.assertEqual((found[0]['heading_path'], found[0]['ref_key']),
                             ('Неделя 28.09.2026 – 04.10.2026 › 1 место по зачётам', None), question)
            self.assertEqual(found[0]['evidence'], '\n'.join(self.FIRST))
        self.assertTrue(places('кто занял первое место')[0]['text'].startswith(
            self.HEAD + ' — последний загруженный.\n'))

    def test_first_places_of_one_zachet(self):
        for question in ('топ-3 в Астане', 'первая тройка в астане', 'лидеры в Астане',
                         'покажи первые три места в Астане'):
            found = places(question)
            self.assertEqual(texts(found), [self.HEAD + ' — последний загруженный.\n' + '\n'.join(self.ASTANA)],
                             question)
            self.assertEqual((found[0]['heading_path'], found[0]['ref_key']),
                             ('Неделя 28.09.2026 – 04.10.2026 › Астана', None))
        self.assertEqual(keys(places('топ-2 в Усть-Каменогорске')), [None])
        # Просили больше, чем отдаём, — сказано прямо.
        self.assertTrue(places('топ-30 в Астане')[0]['text'].endswith(
            '\nПоказаны первые 10 мест из 30 спрошенных — остальные в разделе.'))

    def test_first_places_need_a_zachet(self):
        """Первые места всех зачётов разом — уже выгрузка: просим назвать зачёт."""
        for question in ('топ-3 байги', 'покажи первую десятку байги', 'кто в тройке лидеров'):
            self.assertEqual(texts(places(question)), [
                'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) зачётов 4: «Алматы-Каскелен», '
                '«Астана», «МОТО БАЙГА», «Петропавловск-Уральск-Усть-Каме». Первые места показываются по одному '
                'зачёту — нужно назвать зачёт.'], question)

    def test_no_more_rows_than_the_limit(self):
        sheets = [{'name': 'Зачёт %s' % letter, 'rows': 1} for letter in 'АБВГДЕЖЗИКЛМНО']
        week = dict(W40, id=21, sheets=sheets)
        rows = []
        for number, sheet in enumerate(sheets):
            SHEET_ORDER[sheet['name']] = number
            rows.append(row(week, sheet['name'], 1, 'Победителев %s.' % sheet['name'][-1], 'ZL%06d' % number,
                            200 + number, amount=500000, trips=300, prize='100 000 тенге'))
        found = fragments('кто занял первое место', weeks=[week], rows=rows)
        lines = found[0]['text'].split('\n')
        self.assertEqual(len([text for text in lines if 'номер ВУ' in text]), assistant.RANK_ROWS)
        self.assertEqual(lines[-1], 'Показаны первые 12 строк — остальные в разделе.')
        self.assertEqual((assistant.RANK_ROWS, assistant.RANK_TOP), (12, 10))

    def test_place_that_is_not_in_the_list(self):
        self.assertEqual(texts(places('кто занял 50 место в Астане')), [
            'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) в зачёте «Астана» места № 50 нет. '
            'Мест в зачёте «Астана» — 3.'])
        self.assertEqual(texts(places('кто занял 50 место')), [
            'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) ни в одном зачёте места № 50 нет.'])
        self.assertEqual(places('кто занял 50 место в Астане')[0]['heading_path'],
                         'Неделя 28.09.2026 – 04.10.2026 › нет в списке')

    def test_city_that_is_not_a_zachet(self):
        for question in ('кто занял первое место в Жанаозене', 'кто победил в Экибастузе'):
            self.assertEqual(texts(places(question)), [
                'В списке Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) зачёта по такому городу нет. '
                'Зачёты этой недели: «Алматы-Каскелен», «Астана», «МОТО БАЙГА», '
                '«Петропавловск-Уральск-Усть-Каме».'], question)

    def test_somebody_elses_first_place_gets_no_rows(self):
        for question in ('кто занял первое место в рейтинге операторов',
                         'оператор Иванова заняла первое место в рейтинге операторов',
                         'какой приз за первое место в байге',            # условия акции: ни «кто», ни зачёта
                         'какой приз за 3 место в Жетысу', 'кто выиграл в Лимонопаде',
                         'кто первый?', 'кто первый должен позвонить водителю',
                         'кто первый раз участвует в байге', 'кто на первой линии',
                         'кто занял первое место из 500 участников в Астане',
                         'кто занял первое место в Астане за 2025',          # число — другой вопрос
                         'водитель 87070000011 занял первое место в Астане',  # спросили про водителя
                         'сколько призовых мест в Астане', 'место подачи в Алматы'):
            self.assertEqual(places(question), [], question)
        # Со словом «Байга» посторонние слова вопросу не мешают.
        self.assertEqual(keys(places('кто занял первое место в байге среди таксистов Астаны')), ['ZP000004'])

    def test_named_driver_is_still_a_question_about_the_driver(self):
        self.assertEqual(keys(places('Сериков занял первое место?')), ['ZP000005'])
        self.assertEqual(keys(places('ВУ ZP000006 занял первое место в Астане?')), ['ZP000006'])

    def test_week_of_the_place(self):
        for question, key in (('кто занял первое место в Алматы на 39 неделе', 'ZP000010'),
                              ('кто победил в Астане на позапрошлой неделе', 'ZP000004'),
                              ('кто занял 2 место в алматы 23.09', 'ZP000001')):
            found = places(question)
            self.assertEqual(keys(found), [key], question)
            self.assertIn('21.09.2026 – 27.09.2026 (неделя № 39).', found[0]['text'], question)
        # Прод 05.10.2026: прошлая неделя ещё не загружена — сказано, и дан последний список.
        found = places('кто на прошлой неделе в алматы занял первое место?', weeks=[P39])
        self.assertEqual(keys(found), [None, 'ZP000010'])
        self.assertIn('эта неделя уже прошла, но её список ещё не загружен', found[0]['text'])
        self.assertTrue(found[1]['text'].startswith(
            'Список Байги за неделю 21.09.2026 – 27.09.2026 (неделя № 39) — последний загруженный.'))
        self.assertEqual(len({item['chunk_id'] for item in found}), 2)
        # Недели нет в разделе — другой неделей её не подменяем.
        found = places('кто занял первое место в Алматы за неделю 07.09.2026')
        self.assertEqual(len(found), 1)
        self.assertIn('Списка Байги за неделю 07.09.2026 – 13.09.2026 в разделе нет.', found[0]['text'])

    def test_conversation_about_a_place(self):
        prior = ['кто занял первое место в Алматы']
        for question, key in (('а в Астане?', 'ZP000004'), ('а на втором?', 'ZP000002'),
                              ('а второе место?', 'ZP000002'), ('а кто третий?', 'ZP000003'),
                              ('а на позапрошлой?', 'ZP000010'), ('а сколько он заработал?', 'ZP000001'),
                              ('а приз какой?', 'ZP000001'), ('а Сериков?', 'ZP000005'),
                              ('а в Усть-Каменогорске кто победил?', 'ZP000008')):
            self.assertEqual(keys(places(question, prior)), [key], question)
        self.assertEqual(keys(places('а на втором?', prior + ['а в Астане?'])), ['ZP000005'])
        self.assertEqual(keys(places('а приз какой?', prior + ['а Сериков?'])), ['ZP000005'])
        top = places('а топ-3?', prior)
        self.assertEqual((top[0]['heading_path'], len(top[0]['text'].split('\n'))),
                         ('Неделя 28.09.2026 – 04.10.2026 › Алматы-Каскелен', 4))
        for question in ('а когда приз?', 'а как оформить возврат?', 'а в Астане какие условия?',
                         'а какой адрес офиса в Астане?', 'спасибо', 'а в сентябре?'):
            self.assertEqual(places(question, prior), [], question)

    def test_place_after_a_driver_and_back(self):
        prior = ['Сериков какое место в байге']
        found = places('а кто на первом месте?', prior)
        self.assertEqual(found[0]['text'].split('\n')[1:], ['1 место в каждом зачёте:'] + self.FIRST)
        # «а в Астане?» продолжает разговор о месте, а не о водителе.
        self.assertEqual(places('а в Астане?', prior), [])
        # Место цифрой после разговора о водителе — вопрос об условиях, как и раньше.
        self.assertEqual(places('а какой приз за 3 место?', prior), [])
        self.assertEqual(keys(places('а он занял первое место?', prior)), ['ZP000005'])

    def test_honest_answer_about_a_place_keeps_its_source(self):
        question = 'кто на прошлой неделе в алматы занял первое место?'
        result, calls = compose(question, (
            'На прошлой неделе (28.09.2026 – 04.10.2026) первое место в зачёте «Алматы-Каскелен» занял '
            'Первов А. (ВУ ZP000001): сумма за неделю 700 000 ₸, 430 поездок, приз 200 000 тенге.\n'
            'ИСТОЧНИКИ: [1]'), places(question))
        self.assertEqual((result['kind'], len(calls)), ('answer', 1))
        self.assertIn('11. СПИСКИ БАЙГИ', calls[0]['system'])
        self.assertEqual(baiga_keys(result), ['ZP000001'])
        self.assertEqual(result['sources'][0]['quote'], self.FIRST[0])
        # Несколько зачётов в одном фрагменте: чип ведёт в неделю, без водителя.
        result, _calls = compose('кто занял первое место', (
            'Первые места за неделю 28.09.2026 – 04.10.2026: «Алматы-Каскелен» — Первов А. (ВУ ZP000001), '
            '«Астана» — Астанов Г. (ВУ ZP000004).\nИСТОЧНИКИ: [1]'), places('кто занял первое место'))
        self.assertEqual([(source['ref_id'], source['ref_key']) for source in result['sources']
                          if source['source_kind'] == 'baiga'], [(P40['id'], None)])


class WeekAnswerTests(unittest.TestCase):

    def test_latest_week_when_none_is_asked(self):
        text = fragments('ВУ ZZ123456 какое место')[0]['text']
        self.assertTrue(text.startswith(
            'Список Байги за неделю 28.09.2026 – 04.10.2026 (неделя № 40) — последний загруженный.'))

    def test_asked_week_that_is_loaded(self):
        for question in ('ВУ ZZ123456 какое место на прошлой неделе', 'ВУ ZZ123456 место 30.09',
                         'ВУ ZZ123456 место, неделя № 40', 'ВУ ZZ123456 место на 40 неделе'):
            text = fragments(question)[0]['text']
            self.assertTrue(text.startswith('Список Байги за неделю 28.09.2026 – 04.10.2026 '
                                            '(неделя № 40).\n'), question)
            self.assertIn('1 место', text)
        for question in ('ВУ ZZ123456 место на позапрошлой неделе', 'Жүсіпов 39 аптада нешінші орын алды',
                         'сколько Жусипов заработал за 39 неделю'):
            text = fragments(question)[0]['text']
            self.assertIn('неделю 21.09.2026 – 27.09.2026 (неделя № 39).', text, question)
            self.assertIn('3 место, сумма за неделю 600 120 ₸', text, question)

    def test_number_that_is_not_a_loaded_week_is_not_a_week(self):
        """«За 1 неделю» — срок: дословно формулировка владельца о заработке за
        неделю. Отвечать «списка за неделю № 1 нет» на неё нельзя."""
        for question in ('сколько Жусипов заработал за 1 неделю', 'ВУ ZZ123456 3 неделя подряд в призах',
                         'ВУ ZZ123456 неделя 3 место'):
            found = fragments(question)
            self.assertEqual(len(found), 1, question)
            self.assertIn('— последний загруженный.', found[0]['text'], question)

    def test_this_week_has_no_list_yet(self):
        found = fragments('на этой неделе водитель Жусипов какое место занял')
        self.assertEqual(found[0]['heading_path'], 'Недели')
        self.assertEqual(found[0]['text'], (
            'Списка Байги за неделю 05.10.2026 – 11.10.2026 в разделе пока нет: список загружают, '
            'когда неделя прошла. Последний загруженный список — за неделю 28.09.2026 – 04.10.2026 '
            '(неделя № 40).'))
        self.assertIn('— последний загруженный.', found[1]['text'])
        self.assertIn('1 место', found[1]['text'])

    def test_finished_week_without_a_list_is_not_called_unfinished(self):
        """Прод 05.10.2026: последний список — за 21–27.09, спросили «на прошлой
        неделе» (28.09–04.10). Она уже прошла, и «список загружают, когда неделя
        прошла» читалось бы так, будто ещё идёт."""
        for question in ('ВУ ZZ123456 какое место на прошлой неделе', 'ВУ ZZ123456 какое место 30.09'):
            found = fragments(question, weeks=[W39])
            self.assertEqual(found[0]['text'], (
                'Списка Байги за неделю 28.09.2026 – 04.10.2026 в разделе пока нет: эта неделя уже '
                'прошла, но её список ещё не загружен. Последний загруженный список — за неделю '
                '21.09.2026 – 27.09.2026 (неделя № 39).'), question)
            self.assertIn('(неделя № 39) — последний загруженный.', found[1]['text'], question)
            self.assertIn('3 место', found[1]['text'], question)
        # Последний день недели — она ещё идёт.
        sunday = assistant.analyze('ВУ ZZ123456 какое место на этой неделе', today=date(2026, 10, 11))
        lists = Lists()
        text = assistant.build_rows(sunday, [], weeks=list(WEEKS), find=lists.find, load=lists.load,
                                    load_places=lists.places)[0]['text']
        self.assertIn('в разделе пока нет: список загружают, когда неделя прошла.', text)
        # «Сегодня» — то же, по которому разобран вопрос, а не часы сервера.
        later = assistant.analyze('ВУ ZZ123456 какое место на позапрошлой неделе', today=date(2099, 1, 19))
        text = assistant.build_rows(later, [], weeks=list(WEEKS), find=lists.find, load=lists.load,
                                    load_places=lists.places)[0]['text']
        self.assertIn('в разделе пока нет: эта неделя уже прошла, но её список ещё не загружен.', text)

    def test_missing_week_is_not_replaced_by_another(self):
        found = fragments('ВУ ZZ123456 какое место за неделю 07.09.2026')
        self.assertEqual(texts(found), [
            'Списка Байги за неделю 07.09.2026 – 13.09.2026 в разделе нет. Загруженные недели: '
            '28.09.2026 – 04.10.2026; 21.09.2026 – 27.09.2026.'])
        self.assertEqual(texts(fragments('байга: неделя № 12, ВУ ZZ123456')), [
            'Списка Байги за неделю № 12 в разделе нет. Загруженные недели: '
            '28.09.2026 – 04.10.2026; 21.09.2026 – 27.09.2026.'])

    def test_long_list_of_weeks_is_cut(self):
        many = [dict(W40, id=100 + step, week_number=None,
                     period_start=date(2026, 9, 28) - step * timedelta(days=7),
                     period_end=date(2026, 10, 4) - step * timedelta(days=7))
                for step in range(9)]
        text = fragments('ВУ ZZ123456 место за неделю 05.01.2026', weeks=many)[0]['text']
        self.assertEqual(text.count(' – ') - 1, assistant.WEEKS_LISTED)
        self.assertTrue(text.endswith(' и ещё 3.'))

    def test_empty_section_says_so(self):
        self.assertEqual(texts(fragments('ВУ ZZ123456 какое место', weeks=[], rows=[])), [
            'В разделе «Списки Байги» пока нет ни одной загруженной недели.'])
        self.assertEqual(fragments('какой приз за первое место в байге', weeks=[], rows=[]), [])


class ConversationTests(unittest.TestCase):
    PRIOR = ['Жусипов какое место в байге']

    def test_question_about_the_same_driver(self):
        for question in ('а приз какой?', 'а сколько он заработал?', 'а сколько поездок?',
                         # обычные слова разговор не рвут
                         'ну а приз какой?', 'хорошо, а приз какой?', 'ок, а приз?',
                         'а сколько денег он заработал?', 'а какое место он занял тогда?',
                         'а приз ему дали?', 'а приз ему выплатили?', 'а сколько поездок сделал?',
                         'а сколько поездок он совершил?', 'а приз точно есть?', 'а место какое вышло?',
                         'а в каком он парке?', 'ал жүлдесі қандай?', 'ал қанша тапты?',
                         'а сколько поездок он сделал за ту же неделю и приз какой у него был'):
            self.assertEqual(keys(fragments(question, self.PRIOR)), ['ZZ123456'], question)

    def test_week_asked_in_passing(self):
        for question, week in (('а на прошлой?', 40), ('а на прошлой неделе?', 40),
                               ('а за позапрошлую?', 39), ('а на 39 неделе?', 39),
                               ('а 39 аптада?', 39), ('а неделей раньше?', 39),
                               ('а на позапрошлой неделе тоже первое место?', 39)):
            found = fragments(question, self.PRIOR)
            self.assertEqual(keys(found), ['ZZ123456'], question)
            self.assertIn('(неделя № %d)' % week, found[0]['text'], question)

    def test_another_driver_in_the_same_conversation(self):
        self.assertEqual(keys(fragments('а Ахметов?', self.PRIOR)), ['ZY654321', 'ZX111111'])
        self.assertEqual(keys(fragments('а у ZW222222?', self.PRIOR)), ['ZW222222'])
        self.assertEqual(keys(fragments('а приз Ахметову Е. дали?', self.PRIOR)), ['ZX111111'])
        self.assertIn('водителя с фамилией «Петров» нет', fragments('а Петров?', self.PRIOR)[0]['text'])

    def test_new_topic_gets_no_rows(self):
        for question in ('а как заблокировать Жусипова?', 'спасибо', 'а какой приз за 3 место?',
                         'а как оформить возврат?', 'а какие условия в Алматы?',
                         'а что с водителем Петровым по штрафам?',
                         # вопрос об условиях акции, а не о водителе
                         'а когда приз?', 'а какие призы есть?', 'а сколько всего призовых мест?',
                         'а итоги когда?', 'а список где?', 'сколько призов в байге',
                         'а кто получает приз?', 'а как получить приз?',
                         # другой человек или тема — заглавное незнакомое слово
                         'а приз Яндекс когда начислит', 'а приз Ахметову не пришёл',
                         # время, которое не разобрать, прежней неделей не продолжают
                         'а в сентябре?', 'а раньше какое место было?',
                         # число — уже другой вопрос, и с фамилией из списка тоже
                         'а точно 1 место?', 'а Ахметов 2?'):
            self.assertEqual(fragments(question, self.PRIOR), [], question)

    def test_previous_turn_must_have_been_about_the_lists(self):
        """Водитель из прошлой реплики на другую тему строку за собой не тянет."""
        for prior, question in ((['водитель Ахметов жалуется на списание комиссии'], 'а какая сумма?'),
                                (['как заблокировать водителя Иванова'], 'а на какое место?'),
                                (['Оператор Иванова на смене?'], 'а рейтинг?'),
                                (['ИП Ахметов налоги'], 'сумма?'),
                                (['что такое Лимонопад'], 'а приз какой?')):
            self.assertEqual(fragments(question, prior), [], (prior, question))

    def test_other_topic_in_between_ends_the_conversation(self):
        self.assertEqual(fragments('а приз какой?', self.PRIOR + ['а как оформить возврат?']), [])
        self.assertEqual(keys(fragments('а приз какой?', self.PRIOR + ['а сколько заработал?'])),
                         ['ZZ123456'])

    def test_the_last_named_driver_wins(self):
        prior = ['Жусипов какое место в байге', 'а у ZW222222?']
        self.assertEqual(keys(fragments('а приз какой?', prior)), ['ZW222222'])

    def test_driver_and_week_survive_a_long_talk(self):
        prior = ['Жусипов какое место в байге', 'ну а сколько денег он заработал?', 'а на 39 неделе?',
                 'а сколько заработал?', 'а поездок сколько?', 'а в каком он парке?']
        found = fragments('а приз какой?', prior)
        self.assertEqual(keys(found), ['ZZ123456'])
        self.assertIn('(неделя № 39)', found[0]['text'])
        # Другой водитель в том же разговоре — и неделя разговора за ним.
        found = fragments('а Ахметов Е.?', prior + ['а приз какой?'])
        self.assertEqual(keys(found), ['ZX111111'])
        self.assertIn('(неделя № 39)', found[0]['text'])

    def test_the_latest_week_of_the_conversation_wins(self):
        """Реплики читаются по одной: в склейке дата из позапрошлой реплики
        побеждала «прошлую неделю» из последней."""
        for first in ('ВУ ZZ123456 место 23.09', 'ВУ ZZ123456 место на позапрошлой неделе',
                      'ВУ ZZ123456 место на 39 неделе'):
            found = fragments('а приз какой?', [first, 'а на прошлой неделе?'])
            self.assertIn('(неделя № 40).', found[0]['text'], first)
        # Новый водитель в том же разговоре — та же неделя.
        found = fragments('а у ZW222222 какое место?', ['ВУ ZZ123456 какое место на позапрошлой неделе'])
        self.assertIn('(неделя № 39).', found[0]['text'])
        # Дата из разговора на другую тему неделей списка не становится.
        found = fragments('ВУ ZZ123456 какое место', ['с 21.09.2026 новые условия аренды'])
        self.assertIn('— последний загруженный.', found[0]['text'])

    def test_question_is_read_once(self):
        """Прошлые реплики приходят списком — сам вопрос в них не повторяется, и
        база по нему спрашивается один раз."""
        lists = Lists()
        fragments('ВУ ZZ123456 какое место', [], lists=lists)
        self.assertEqual([call[0] for call in lists.calls], ['find', 'load'])


# ─────────────────────────────────────────────────────────────────────────────
# Фрагмент: контракт ретривера и проверок помощника
# ─────────────────────────────────────────────────────────────────────────────

ALL_KINDS = (
    'ВУ ZZ123456 какое место',                                  # строка водителя
    'кто на прошлой неделе в алматы занял первое место?',       # строка по месту
    'на этой неделе водитель Жусипов какое место занял',        # «списка ещё нет» + строка
    'Ким В. какое место занял на прошлой неделе',               # нет в неделе, был раньше
    'байга ву XX999999',                                        # номера нет
    'водитель Петров Иван какой приз получил',                  # фамилии нет
    'какое место занял Петров',                                 # догадка
    'ВУ ZZ123456 какое место за неделю 07.09.2026',             # недели нет
)


class FragmentContractTests(unittest.TestCase):

    def every(self):
        return [item for question in ALL_KINDS for item in fragments(question)]

    def test_row_shape_of_the_retriever(self):
        found = self.every()
        self.assertEqual(len(found), len(ALL_KINDS) + 1)
        for item in found:
            self.assertEqual(
                {name: item[name] for name in ('article_id', 'chunk_idx', 'title', 'slug',
                                              'requires_ack', 'historical', 'similarity',
                                              'found_by', 'directory_hit', 'source_kind', 'tab',
                                              'space_id', 'ref_city')},
                {'article_id': None, 'chunk_idx': 0, 'title': 'Списки Байги', 'slug': '',
                 'requires_ack': False, 'historical': False, 'similarity': None, 'found_by': [4],
                 'directory_hit': True, 'source_kind': 'baiga', 'tab': 'baiga', 'space_id': None,
                 'ref_city': None})
            # Свой отрицательный диапазон: у справочника (-1, -2, …) другой.
            self.assertLess(item['chunk_id'], -1000)
        two = fragments('на этой неделе водитель Жусипов какое место занял')
        self.assertEqual(len({item['chunk_id'] for item in two}), len(two))

    def test_rows_pass_the_floor_and_skip_clarify(self):
        found = fragments('какое место у Ахметова в байге')
        self.assertEqual(ai_answer.usable_chunks(found), found)
        article = {'chunk_id': 5, 'article_id': 77, 'text': 'x', 'similarity': 0.7, 'found_by': [1]}
        self.assertFalse(ai_answer.should_clarify('место Ахметова', found + [article])[0])

    def test_past_week_is_not_an_expired_term(self):
        """Неделя в прошлом — не «срок истёк»: иначе помощник подписывал бы итоги
        Байги «сведения уже не действуют»."""
        for item in currency.mark_chunks(self.every(), TODAY):
            self.assertFalse(item['stale'], item['text'])
            self.assertEqual((item['stale_note'], item['stale_kind']), ('', None))

    def test_words_of_the_uploaded_file_cannot_mark_the_row_stale(self):
        """Зачёт, приз и парк приходят из загруженного Excel: «Архив Алматы» или
        «сертификат до 31.12.2025» не делают итог недели недействующим."""
        odd = [row(W40, 'Архив Алматы', 9, 'Бывшев А.', 'ZB000001', 31, amount=300000, trips=99,
                   prize='сертификат до 31.12.2025', park='ТОО «По 01.01.2026»'),
               row(W40, 'Итоги до 04.10.2026', 3, 'Архивов Б.', 'ZB000002', 32, amount=300000, trips=99)]
        found = fragments('ВУ ZB000001 какое место', rows=odd) + fragments('ВУ ZB000002 приз', rows=odd)
        self.assertEqual(len(found), 2)
        for item in currency.mark_chunks(found, TODAY):
            self.assertFalse(item['stale'], item['heading_path'])
        # Те же слова в статье пометку ставят, как и раньше.
        for item in found:
            article = dict(item, source_kind=None)
            self.assertTrue(currency.mark_chunks([article], TODAY)[0]['stale'], item['heading_path'])

    def test_driver_id_is_not_sent_to_the_model(self):
        for item in self.every():
            self.assertNotRegex(item['text'], r'[0-9a-f]{32}')
            self.assertNotRegex(item['heading_path'], r'[0-9a-f]{32}')

    def test_every_number_of_an_honest_answer_is_in_the_text(self):
        found = fragments('ВУ ZZ123456 какое место на позапрошлой неделе')
        for answer in (
                'Жүсіпов А. Б. (ВУ ZZ123456): 3 место, сумма за неделю 600 120 ₸, 400 поездок, '
                'приз 100 000 тенге — неделя 21.09.2026 – 27.09.2026 (№ 39).',
                'Неделя 21.09-27.09.2026: приз 100000 тенге.',
                'За 21-27.09.2026 заработал 600 120 ₸.',
                'За 21-27 сентября — 3 место.',
                'Итоги недели 21.09.26: приз 100 тыс. тенге.',
                'На 05.10.2026 последний список — за 21.09.2026-27.09.2026.',
                # число сразу за датой или номером недели — другое число, а не то же
                'За неделю 21.09.2026 – 27.09.2026: 400 поездок, сумма 600 120 ₸.',
                'Неделя № 39, 21.09.2026 – 27.09.2026 — 3 место.',
                'Неделя № 39, 400 поездок, сумма 600 120 ₸.',
                'За неделю 21.09.2026 – 27.09.2026:\n\n1. Место: 3\n2. Сумма: 600 120 ₸'):
            self.assertEqual(ai_answer.ungrounded_numbers(answer, found, on_date=TODAY), [], answer)
        for answer in ('Приз 150 000 тенге.', 'Неделя 21.09-28.09.2026.', 'Неделя 14-20.09.2026.',
                       'Заработал 600 121 ₸.', 'Неделя № 39, 500 поездок.', 'Приз за 21-27 места.',
                       'Неделя 27.09-21.09.2026.'):
            self.assertTrue(ai_answer.ungrounded_numbers(answer, found, on_date=TODAY), answer)

    def test_evidence_is_the_driver_line_without_the_week(self):
        item = fragments('ВУ ZX111111 приз')[0]
        self.assertEqual(item['evidence'], 'Ахметов Е. (номер ВУ ZX111111): зачёт «Астана», 40 место, '
                                           'сумма за неделю 250 000 ₸, поездок 90, приза нет.')
        self.assertIn(item['evidence'], item['text'])
        self.assertIsNone(fragments('байга ву XX999999')[0]['evidence'])

    def test_chip_carries_week_and_driver(self):
        item = fragments('ВУ ZZ123456 какое место на позапрошлой неделе')[0]
        self.assertEqual((item['ref_id'], item['ref_key']), (W39['id'], 'ZZ123456'))
        self.assertEqual(item['heading_path'], 'Неделя 21.09.2026 – 27.09.2026 › Алматы-Каскелен')
        # Номер — в том виде, в каком его ищет раздел: написание файла с дефисом
        # поиск раздела не находит.
        dashed = [row(W40, 'Астана', 2, 'Дефисов А.', 'zz-12 3456', 34, amount=1, trips=1, city='Астана')]
        item = fragments('ВУ ZZ123456 какое место', rows=dashed)[0]
        self.assertEqual(item['ref_key'], 'ZZ123456')
        self.assertIn('Дефисов А. (номер ВУ zz-12 3456)', item['text'])
        # Без номера ВУ в строке водителя находит его ID.
        nameless = [row(W40, 'Астана', 2, 'Безномерный А.', '', 33, amount=1, trips=1, city='Астана')]
        item = fragments('какое место у Безномерного', rows=nameless)[0]
        self.assertEqual(item['ref_key'], fake_id(33))
        self.assertIn('Безномерный А. (номер ВУ в списке не указан)', item['text'])


# ─────────────────────────────────────────────────────────────────────────────
# Слой ответа
# ─────────────────────────────────────────────────────────────────────────────

ARTICLE = {'chunk_id': 501, 'article_id': 77, 'chunk_idx': 0, 'title': 'Акция «Байга»',
           'slug': 'baiga', 'heading_path': 'Условия', 'requires_ack': False, 'historical': False,
           'similarity': 0.8, 'found_by': [1],
           'text': 'В каждом зачёте 35 призовых мест. Итоги недели публикуются в понедельник.'}
DIRECTORY = {'chunk_id': -1, 'article_id': None, 'title': 'Офисы', 'heading_path': 'Шымкент › Офис',
             'text': 'Адрес: проспект Республики 17.', 'found_by': [4], 'directory_hit': True,
             'source_kind': 'office', 'tab': 'offices', 'similarity': None}


def compose(question, replies, found=None):
    """Ответ помощника на вопрос: модель отвечает replies по очереди (последний
    повторяется). Возвращает (ответ, вызовы модели)."""
    calls = []
    replies = [replies] if isinstance(replies, str) else list(replies)

    def generate(system, user, history=()):
        calls.append({'system': system, 'user': user})
        return replies[min(len(calls), len(replies)) - 1], {'provider': 'fake', 'model': 'fake'}

    found = fragments(question) if found is None else found
    return ai_answer.compose(question, found + [ARTICLE], generate, on_date=TODAY), calls


def baiga_keys(result):
    return [source['ref_key'] for source in result['sources'] if source['source_kind'] == 'baiga']


class AnswerLayerTests(unittest.TestCase):

    def test_rule_comes_only_with_the_rows(self):
        self.assertNotIn('СПИСКИ БАЙГИ', ai_answer.SYSTEM_PROMPT)
        self.assertEqual(ai_answer.system_prompt([ARTICLE]), ai_answer.SYSTEM_PROMPT)
        # Справочник вики — тоже «взятый, а не найденный» фрагмент, но правило не его.
        self.assertEqual(ai_answer.system_prompt([DIRECTORY, ARTICLE]), ai_answer.SYSTEM_PROMPT)
        with_rule = ai_answer.system_prompt([ARTICLE] + fragments('ВУ ZZ123456 место'))
        # Правило — между правилами и форматом ответа, остальной промпт цел.
        self.assertLess(with_rule.index('10. СПРАВОЧНИК'), with_rule.index('11. СПИСКИ БАЙГИ'))
        self.assertLess(with_rule.index('11. СПИСКИ БАЙГИ'), with_rule.index('ФОРМАТ ОТВЕТА'))
        self.assertEqual(with_rule.replace('\n' + ai_answer.BAIGA_RULE, ''), ai_answer.SYSTEM_PROMPT)
        rule = ' '.join(ai_answer.BAIGA_RULE.split())
        for must in ('это «сумма за неделю»', 'ВСЕГДА называй неделю', 'это ответ на вопрос, а не отказ',
                     'цифры разных водителей не складывай', 'буква в букву', 'слово «зачёт» не переводи',
                     'итог ОДНОЙ недели', 'Не пиши, что данных за неё нет'):
            self.assertIn(must, rule)

    def test_prompt_labels_the_section(self):
        found = fragments('ВУ ZZ123456 место')
        prompt = ai_answer.build_user_prompt('место', found + [ARTICLE, DIRECTORY], TODAY)
        self.assertIn('[1] Раздел «Списки Байги», запись «Неделя 28.09.2026 – 04.10.2026 › '
                      'Алматы-Каскелен»\nТЕКСТ:\nСписок Байги за неделю', prompt)
        self.assertIn('[2] Статья «Акция «Байга»», раздел «Условия»', prompt)
        self.assertIn('[3] Справочник «Офисы», запись «Шымкент › Офис»', prompt)
        self.assertIn('Раздел «Списки Байги»', ai_answer.BAIGA_RULE)

    def test_model_gets_the_rule_and_the_answer_keeps_its_source(self):
        result, calls = compose(
            'ВУ ZZ123456 какое место на позапрошлой неделе',
            'Жүсіпов А. Б. (ВУ ZZ123456) за неделю 21.09.2026 – 27.09.2026 занял 3 место, '
            'сумма за неделю 600 120 ₸, приз 100 000 тенге.\nИСТОЧНИКИ: [1]')
        self.assertEqual(len(calls), 1)
        self.assertIn('11. СПИСКИ БАЙГИ', calls[0]['system'])
        self.assertEqual(result['kind'], 'answer')
        self.assertEqual(len(result['sources']), 1)
        source = result['sources'][0]
        self.assertEqual((source['source_kind'], source['tab'], source['ref_id'], source['ref_key']),
                         ('baiga', 'baiga', W39['id'], 'ZZ123456'))
        self.assertIsNone(source['article_id'])
        self.assertEqual(source['quote'], 'Жүсіпов А. Б. (номер ВУ ZZ123456): зачёт «Алматы-Каскелен», '
                                          '3 место, сумма за неделю 600 120 ₸, поездок 400, '
                                          'приз 100 000 тенге.')
        self.assertEqual(result['notes'], [])

    def test_quote_is_the_driver_line_even_when_the_answer_names_only_the_week(self):
        """Строка недели есть в каждом фрагменте и совпадает с ответом датами —
        цитатой под ответом она быть не должна."""
        result, _calls = compose(
            'ВУ ZZ123456 какое место на позапрошлой неделе',
            'За неделю 21.09.2026 – 27.09.2026 Жүсіпов А. Б. занял 3 место.\nИСТОЧНИКИ: [1]')
        self.assertTrue(result['sources'][0]['quote'].startswith('Жүсіпов А. Б. (номер ВУ ZZ123456)'))

    def test_kazakh_names_do_not_make_a_russian_answer_kazakh(self):
        """ФИО переносятся буква в букву, а они казахские: без оговорки модель
        звали второй раз с указанием «исправь язык» — то есть переписать фамилии."""
        twice = [row(W40, 'Астана', 1, 'Жүсіпов А. Б.', 'ZK000001', 71, amount=300000, trips=90, city='Қызылорда'),
                 row(W40, 'Астана', 2, 'Әбілов Н.', 'ZK000002', 72, amount=290000, trips=80, city='Қызылорда')]
        found = fragments('ВУ ZK000001 и ВУ ZK000002 какое место', rows=twice)
        self.assertEqual(len(found), 2)
        reply = ('Жүсіпов А. Б. — 1 место, Әбілов Н. — 2 место, оба из города Қызылорда.\n'
                 'ИСТОЧНИКИ: [1] [2]')
        self.assertEqual(ai_answer.detect_language(reply), 'kk')        # а по словам самой модели — русский
        result, calls = compose('ВУ ZK000001 и ВУ ZK000002 какое место', reply, found)
        self.assertEqual((result['kind'], len(calls)), ('answer', 1))
        # Казахский ответ на русский вопрос по-прежнему переспрашивается — тем же промптом.
        result, calls = compose('ВУ ZZ123456 какое место', [
            'Жүргізуші бірінші орын алды, жүлдесі бар.\nИСТОЧНИКИ: [1]',
            'Жүсіпов А. Б. занял 1 место.\nИСТОЧНИКИ: [1]'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['system'], calls[0]['system'])
        self.assertIn('11. СПИСКИ БАЙГИ', calls[1]['system'])
        self.assertIn('занял 1 место', result['text'])

    def test_week_written_with_a_hyphen_is_not_an_invention(self):
        result, _calls = compose(
            'ВУ ZZ123456 какое место на позапрошлой неделе',
            'За неделю 21.09-27.09.2026: 3 место, приз 100 000 тенге.\nИСТОЧНИКИ: [1]')
        self.assertEqual(result['kind'], 'answer')
        self.assertNotIn('fallback', result['meta'])

    def test_row_found_by_the_code_is_never_withheld(self):
        """Строку нашёл код, и она верна, как бы модель её ни пересказала: вместо
        «в статьях этого нет» человек получает сами строки раздела."""
        result, _calls = compose('ВУ ZZ123456 какое место',
                                 'Приз 250 000 тенге, всего 1 345 участников.\nИСТОЧНИКИ: [1]')
        self.assertEqual(result['kind'], 'answer')
        self.assertEqual(result['text'], fragments('ВУ ZZ123456 какое место')[0]['text'])
        self.assertEqual(baiga_keys(result), ['ZZ123456'])
        self.assertEqual(result['meta']['ungrounded'], ['250 000', '1 345'])
        self.assertTrue(result['meta']['fallback'])
        # Без строк раздела выдумка по-прежнему придерживается целиком.
        result, _calls = compose('какой приз', 'Приз 250 000 тенге.\nИСТОЧНИКИ: [1]', [])
        self.assertEqual((result['kind'], result['text']), ('no_answer', ai_answer.NO_ANSWER_TEXT))

    def test_namesake_is_not_attached_by_coincidence(self):
        """Замер 05.10.2026: неделя № 40 в ответе совпала с 40-м местом однофамильца,
        и его строка подшилась источником «сопоставлено»."""
        found = fragments('Ахметов Данияр какой приз')
        reply = ('За неделю 28.09.2026 – 04.10.2026 (неделя № 40) Ахметов Д. К. (ВУ ZY654321) занял '
                 '12 место и получил приз 5000 тенге.')
        for tail in ('\nИСТОЧНИКИ: [1]', '', '\nИСТОЧНИКИ: [3]', '\nИСТОЧНИКИ: [9]'):
            result, _calls = compose('Ахметов Данияр какой приз', reply + tail, found)
            self.assertEqual(baiga_keys(result), ['ZY654321'], tail)

    def test_row_is_matched_by_the_number_only_it_has(self):
        """Строку, которую модель не назвала, сервер подшивает по номеру ВУ или
        сумме — а не по месту, призу и общим словам, которые есть и у соседей."""
        found = fragments('Ахметов Данияр какой приз')
        result, _calls = compose('Ахметов Данияр какой приз',
                                 'Ахметов — 12 место, приз 5000 тенге, поездок 120.', found)
        self.assertEqual(baiga_keys(result), [])
        result, _calls = compose('Ахметов Данияр какой приз',
                                 'Ахметов Д. К.: сумма за неделю 345 000 ₸.', found)
        self.assertEqual([(source['ref_key'], source['attributed']) for source in result['sources']
                          if source['source_kind'] == 'baiga'], [('ZY654321', True)])

    def test_not_in_the_list_is_an_answer_and_not_a_refusal(self):
        """«Нет в списке» и «списка ещё нет» — содержание ответа. Фраза со словом
        «доступных» и без числа читалась отказом: терялся чип, а вопрос уходил
        супервайзеру, которому ответить на него нечем."""
        for question, reply in (
                ('водитель Петров Иван какой приз получил',
                 'В доступных списках Байги водителя «Петров Иван» нет.\nИСТОЧНИКИ: [1]'),
                ('какое место занял Петров',
                 'В представленных списках Байги водителя с фамилией «Петров» нет.'),
                ('байга ву XX999999', 'Қолжетімді Бәйге тізімдерінде мұндай жүргізуші жоқ.')):
            # Без строк раздела такая фраза — отказ.
            self.assertTrue(ai_answer.is_refusal(ai_answer.split_sources(reply)[0]), reply)
            result, _calls = compose(question, reply)
            self.assertEqual(result['kind'], 'answer', reply)
        # Настоящий отказ — не про списки — отказом и остаётся.
        result, _calls = compose('какое место занял Петров',
                                 'В доступных вам статьях этого нет. Уточните у супервайзера.')
        self.assertEqual((result['kind'], result['sources']), ('no_answer', []))

    def test_answer_layer_for_articles_is_untouched(self):
        """Те же ответы без строк раздела ведут себя как прежде."""
        result, _calls = compose('какой приз', 'В доступных списках Байги такого нет.', [])
        self.assertEqual(result['kind'], 'no_answer')
        sources = ai_answer.build_sources([], [ARTICLE], 'В каждом зачёте 35 призовых мест, итоги в понедельник.')
        self.assertEqual([source['article_id'] for source in sources], [77])


# ─────────────────────────────────────────────────────────────────────────────
# История разговора
# ─────────────────────────────────────────────────────────────────────────────

def source_row(**values):
    """Строка источника в том порядке колонок, в каком её читает store."""
    blank = dict.fromkeys(ai_store.SOURCE_ROW)
    blank.update(message_id=1, ord=0, title='', slug='', heading_path='', quote='',
                 quote_ok=True, requires_ack=False, attributed=False, stale=False,
                 stale_note='', stale_kind='', source_kind='article')
    unknown = set(values) - set(blank)
    assert not unknown, unknown
    blank.update(values)
    return tuple(blank[name] for name in ai_store.SOURCE_ROW)


def message_row(**values):
    blank = dict.fromkeys(ai_store._MESSAGE_FIELDS)
    blank.update(id=1, seq=1, role='assistant', kind='answer', text='текст')
    unknown = set(values) - set(blank)
    assert not unknown, unknown
    blank.update(values)
    return tuple(blank[name] for name in ai_store._MESSAGE_FIELDS)


BAIGA_SOURCE = dict(title='Списки Байги', heading_path='Неделя 28.09.2026 – 04.10.2026 › Астана',
                    quote='Ахметов Е. (номер ВУ ZX111111): зачёт «Астана», 40 место',
                    source_kind='baiga', tab='baiga', ref_id=3, ref_key='ZX111111')
ARTICLE_SOURCE = dict(ord=1, article_id=7, title='Аренда', slug='rent', heading_path='Условия',
                      quote='срок 14 дней', visible=True)


class HistoryTests(unittest.TestCase):

    def read(self, sources, baiga_access, message=None):
        cursor = mock.MagicMock()
        cursor.fetchall.side_effect = [[message or message_row()],
                                       [source_row(**source) for source in sources]]
        return ai_store.chat_messages(cursor, 5, visible_article_ids={7},
                                      baiga_access=baiga_access)[0]

    def test_source_is_shown_only_while_the_section_is_open(self):
        shown = self.read([BAIGA_SOURCE], lambda: True)['sources'][0]
        self.assertTrue(shown['available'])
        self.assertEqual((shown['source_kind'], shown['tab'], shown['ref_id'], shown['ref_key']),
                         ('baiga', 'baiga', 3, 'ZX111111'))
        self.assertIn('Ахметов Е.', shown['quote'])
        for closed_by in (lambda: False, None):
            closed = self.read([BAIGA_SOURCE], closed_by)['sources'][0]
            self.assertFalse(closed['available'])
            self.assertEqual((closed['title'], closed['quote'], closed['heading_path'], closed['tab'],
                              closed['ref_id'], closed['ref_key']),
                             ('Раздел недоступен', '', '', None, None, None))

    def test_article_source_reads_as_before(self):
        shown = self.read([ARTICLE_SOURCE], None)['sources'][0]
        self.assertEqual((shown['available'], shown['title'], shown['slug'], shown['quote'], shown['ord']),
                         (True, 'Аренда', 'rent', 'срок 14 дней', 1))
        hidden = self.read([dict(ARTICLE_SOURCE, visible=False)], None)['sources'][0]
        self.assertEqual((hidden['available'], hidden['title'], hidden['slug'], hidden['quote']),
                         (False, 'Статья недоступна', None, ''))

    def test_access_is_asked_once_and_only_when_needed(self):
        asked = []

        def access_now():
            asked.append(1)
            return True

        self.read([ARTICLE_SOURCE], access_now)
        self.assertEqual(asked, [])
        self.read([BAIGA_SOURCE, dict(BAIGA_SOURCE, ord=1), ARTICLE_SOURCE], access_now)
        self.assertEqual(asked, [1])

    def test_gate_of_the_answer_reaches_the_reader(self):
        self.assertEqual(self.read([], None, message_row(gated_by='baiga'))['gated_by'], 'baiga')
        self.assertIsNone(self.read([], None)['gated_by'])

    def test_columns_are_named_once(self):
        """Колонку нельзя добавить в перечень и забыть в значениях; чтение идёт
        по именам, а не по номерам позиций."""
        self.assertEqual(
            ai_store._INSERT_SOURCE,
            'INSERT INTO wiki_ai_message_sources (%s) VALUES (%s)' % (
                ', '.join(ai_store._INSERT_FIELDS),
                ', '.join('%%(%s)s' % name for name in ai_store._INSERT_FIELDS)))
        select = ' '.join(ai_store._SOURCES.split())
        self.assertTrue(select.startswith(
            'SELECT %s, (s.article_id = ANY(%%(visible)s)) AS visible FROM'
            % ', '.join('s.%s' % name for name in ai_store.SOURCE_ROW[:-1])), select)
        self.assertEqual(ai_store.SOURCE_ROW[-1], 'visible')
        for name in ('ref_key', 'ref_id', 'tab', 'source_kind', 'space_id'):
            self.assertIn(name, ai_store._INSERT_FIELDS)
            self.assertIn(name, ai_store.SOURCE_ROW)
        messages = ' '.join(ai_store._MESSAGES.split())
        self.assertTrue(messages.startswith('SELECT %s FROM wiki_ai_messages'
                                            % ', '.join(ai_store._MESSAGE_FIELDS)), messages)
        self.assertIn('gated_by', ai_store._MESSAGE_FIELDS)

    def test_source_and_gate_are_stored(self):
        cursor = mock.MagicMock()
        cursor.fetchone.side_effect = [(1,), (10, None)]
        found = fragments('ВУ ZX111111 приз')
        sources = ai_answer.build_sources([1], found, 'Ахметов Е. (ВУ ZX111111): 40 место, 250 000 ₸.')
        ai_store.append_message(cursor, 5, role='assistant', text='ответ', sources=sources,
                                gated_by='baiga')
        message_sql, message = cursor.execute.call_args_list[1].args
        self.assertIn('gated_by', message_sql)
        self.assertEqual(message['gated_by'], 'baiga')
        sql, params = cursor.execute.call_args_list[-1].args
        self.assertEqual(sql, ai_store._INSERT_SOURCE)
        self.assertEqual(set(params), set(ai_store._INSERT_FIELDS))
        self.assertEqual((params['source_kind'], params['tab'], params['ref_id'], params['ref_key'],
                          params['article_id'], params['chunk_id'], params['space_id']),
                         ('baiga', 'baiga', W40['id'], 'ZX111111', None, None, None))
        # Обычный ответ вики пометки не несёт.
        cursor = mock.MagicMock()
        cursor.fetchone.side_effect = [(1,), (10, None)]
        ai_store.append_message(cursor, 5, role='assistant', text='ответ')
        self.assertIsNone(cursor.execute.call_args_list[1].args[1]['gated_by'])


# ─────────────────────────────────────────────────────────────────────────────
# Роуты помощника
# ─────────────────────────────────────────────────────────────────────────────

class _Db:
    def __init__(self):
        self.cursor = mock.MagicMock()

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


def wiki_person(role='operator'):
    """Тот же человек глазами вики (wiki.queries.load_access_context)."""
    return {'user_id': 42, 'otp_role': role, 'department_id': 191, 'direction_id': None,
            'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [],
            'access_mode': 'auto', 'wiki_enabled': True}


@unittest.skipIf(Flask is None, 'Flask не установлен')
class RouteTests(unittest.TestCase):
    """/ask, /ai/search и чтение чата целиком: строки раздела доходят до ответа —
    с ключом QR блюпринта, выбранным пространством и прошлыми вопросами — и за
    пределы разговора не уходят."""

    HISTORY = [{'role': 'user', 'kind': 'question', 'text': 'Жусипов какое место в байге'},
               {'role': 'assistant', 'kind': 'answer', 'text': 'Жүсіпов А. Б. — 1 место.'}]

    def setUp(self):
        from wiki.routes import build_wiki_blueprint

        self.db = _Db()
        self.key = mock.Mock(return_value=True)
        self.lists = Lists()
        self.wiki_viewer = wiki_person('operator')
        self.viewer = person('operator', 'op', user_id=42)
        self.reply = 'Жүсіпов А. Б. (ВУ ZZ123456) — 1 место, приз 200 000 тенге.\nИСТОЧНИКИ: [1]'
        self.history = []
        self.questions = []
        self.asked_for = []
        self.seen = {}
        self.stored = []
        self.escalated = []
        test = self

        def generate(system, user, history=()):
            test.seen.update(system=system, user=user)
            return test.reply, {'provider': 'fake', 'model': 'fake', 'elapsed': 0.1}

        def append(cursor, chat_id, **message):
            test.stored.append(message)
            return {'id': 100 + len(test.stored), 'seq': len(test.stored), 'created_at': None}

        def escalate(cursor, **question):
            test.escalated.append(question)
            return {'id': 1, 'status': 'open'}

        def questions(cursor, chat_id, *, limit):
            test.asked_for.append((chat_id, limit, len(test.stored)))
            return list(test.questions)

        wiki = routes_ai
        patches = [
            mock.patch.object(wiki.queries, 'load_access_context',
                              lambda cursor, user_id: dict(test.wiki_viewer)),
            mock.patch.object(wiki.queries, 'granted_rule_rights', lambda cursor, subjects, user_id: ({}, [])),
            mock.patch.object(wiki.queries, 'spaces_for_user', lambda cursor, ctx, **kw: [7, 9]),
            mock.patch.object(wiki.structure, 'list_spaces',
                              lambda cursor, **kw: [{'id': 7, 'name': 'Таксопарки', 'features': {}},
                                                    {'id': 9, 'name': 'Тез', 'features': {}}]),
            mock.patch.object(wiki.structure, 'space_ids_for_departments', lambda cursor, codes: [9]),
            mock.patch.object(wiki.wiki_perimeter, 'assistant_perimeter',
                              lambda cursor, ctx, space_id: {'article_ids': [1], 'read_count': 1,
                                                             'hash': 'x' * 12}),
            mock.patch.object(wiki.ai_embed, 'embed_query', mock.Mock(side_effect=RuntimeError('нет ключа'))),
            mock.patch.object(wiki.ai_retrieve, 'search_hybrid',
                              lambda cursor, **kw: {'rows': [dict(ARTICLE)], 'branches': {}, 'degraded': True}),
            mock.patch.object(wiki.wiki_directory, 'ai_rows', lambda *args, **kw: []),
            mock.patch.object(wiki.wiki_directory, 'access_map', lambda cursor, ctx: {}),
            mock.patch.object(wiki.ai_providers, 'generate', generate),
            mock.patch.object(wiki.ai_store, 'owned_chat', lambda cursor, user_id, chat_id: {'id': chat_id}),
            mock.patch.object(wiki.ai_store, 'recent_turns',
                              lambda cursor, chat_id, limit=6: list(test.history)),
            mock.patch.object(wiki.ai_store, 'recent_questions', questions),
            mock.patch.object(wiki.ai_store, 'append_message', append),
            mock.patch.object(wiki.ai_store, 'touch_chat', lambda *args, **kw: None),
            mock.patch.object(wiki.wiki_questions, 'table_ready', lambda cursor: True),
            mock.patch.object(wiki.wiki_questions, 'escalate', escalate),
            mock.patch.object(wiki.wiki_questions, 'chat_marks', lambda cursor, chat_id: []),
            mock.patch.object(wiki.wiki_questions, 'mark_answers_seen', lambda cursor, **kw: None),
            mock.patch.object(queries, 'load_access_context', lambda cursor, user_id: dict(test.viewer)),
            mock.patch.object(queries, 'list_weeks', lambda cursor, campaign: list(WEEKS)),
            mock.patch.object(queries, 'assistant_candidates',
                              lambda cursor, limit=None, **found_by: test.lists.find(**found_by)),
            mock.patch.object(queries, 'assistant_rows',
                              lambda cursor, **found_by: test.lists.load(**found_by)),
            mock.patch.object(currency, 'today', lambda: TODAY),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=self.db, require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=self.key, client_ip=lambda: '127.0.0.1'))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def ask(self, question, space_id=7):
        self.seen.clear()
        del self.stored[:]
        del self.escalated[:]
        del self.asked_for[:]
        return self.client.post('/api/wiki/ai/chats/5/ask',
                                json={'question': question, 'space_id': space_id})

    def test_rows_reach_the_model_first_and_the_answer_carries_the_chip(self):
        response = self.ask('ВУ ZZ123456 какое место')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data['kind'], 'answer')
        self.assertIn('[1] Раздел «Списки Байги»', self.seen['user'])
        self.assertIn('[2] Статья «Акция «Байга»»', self.seen['user'])
        self.assertIn('11. СПИСКИ БАЙГИ', self.seen['system'])
        source = data['sources'][0]
        self.assertEqual((source['source_kind'], source['tab'], source['ref_id'], source['ref_key']),
                         ('baiga', 'baiga', W40['id'], 'ZZ123456'))
        self.assertEqual(self.stored[-1]['sources'][0]['ref_key'], 'ZZ123456')

    def test_qr_key_of_the_blueprint_is_the_one_asked(self):
        self.ask('ВУ ZZ123456 какое место')
        # Первый вызов — гейт самой вики, второй — раздела: про того же человека
        # и на курсоре запроса.
        self.assertEqual(self.key.call_args_list, [mock.call(42, cursor=self.db.cursor)] * 2)
        self.key.side_effect = [True, False]             # вика открыта, раздел — нет
        self.ask('ВУ ZZ123456 какое место')
        self.assertNotIn('Списки Байги', self.seen['user'])

    def test_section_gate_is_asked_even_when_the_wiki_gate_is_not(self):
        """Кого вика про QR не спрашивает (кадровик в отделе раздела), раздел
        спрашивает: наборы должностей под двумя замками разные."""
        self.wiki_viewer = wiki_person('hr_manager')
        self.viewer = person('hr_manager', 'szov', user_id=42)
        self.key.return_value = False
        response = self.ask('ВУ ZZ123456 какое место')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.key.call_args_list, [mock.call(42, cursor=self.db.cursor)])
        self.assertNotIn('Списки Байги', self.seen['user'])
        self.assertEqual([source for source in response.get_json()['sources']
                          if source['source_kind'] == 'baiga'], [])
        self.key.return_value = True
        self.ask('ВУ ZZ123456 какое место')
        self.assertIn('Списки Байги', self.seen['user'])

    def test_tez_space_and_closed_section_get_no_rows(self):
        self.ask('ВУ ZZ123456 какое место', space_id=9)
        self.assertNotIn('Списки Байги', self.seen['user'])
        self.assertNotIn('СПИСКИ БАЙГИ', self.seen['system'])
        self.viewer = person('trainer', 'szov', user_id=42)
        self.ask('ВУ ZZ123456 какое место')
        self.assertNotIn('Списки Байги', self.seen['user'])

    def test_previous_questions_carry_the_driver(self):
        self.reply = 'Приз — 200 000 тенге (неделя 28.09.2026 – 04.10.2026).\nИСТОЧНИКИ: [1]'
        self.history = list(self.HISTORY)
        # Вопросов разделу отдают больше, чем реплик модели: водитель назван
        # четыре вопроса назад, а в истории модели его уже нет.
        self.questions = ['Жусипов какое место в байге', 'а сколько заработал?', 'а поездок сколько?',
                          'а в каком он парке?']
        self.ask('а приз какой?')
        self.assertIn('Жүсіпов А. Б. (номер ВУ ZZ123456)', self.seen['user'])
        # Спрошены вопросы этого чата, сколько помнит раздел, и ДО записи нового.
        self.assertEqual(self.asked_for, [(5, assistant.PRIOR_TURNS, 0)])
        self.questions = []
        self.ask('а приз какой?')
        self.assertNotIn('Списки Байги', self.seen['user'])

    def test_answer_built_on_the_rows_never_leaves_the_conversation(self):
        """Очередь «Вопросы операторов» читают и те, кому раздел закрыт. Ответ
        со строками раздела в неё не уходит — даже начатый словами признания."""
        self.reply = ('В доступных мне фрагментах нет данных за текущую неделю. За неделю '
                      '28.09.2026 – 04.10.2026 Жүсіпов А. Б. (ВУ ZZ123456) занял 1 место.\nИСТОЧНИКИ: [2]')
        data = self.ask('на этой неделе ВУ ZZ123456 какое место').get_json()
        self.assertTrue(wiki_questions.should_escalate('operator', data['kind'], data['text']))
        self.assertEqual((data['kind'], data['gated_by'], data['escalation']), ('answer', 'baiga', None))
        self.assertEqual(self.escalated, [])
        self.assertEqual(self.stored[-1]['gated_by'], 'baiga')
        # Обычный ответ вики с тем же признанием передаётся, как и раньше.
        self.reply = 'В доступных мне фрагментах нет данных о комиссии. В зачёте 35 призовых мест.'
        data = self.ask('какая комиссия у парка').get_json()
        self.assertEqual((data['kind'], data['gated_by'], bool(data['escalation'])), ('answer', None, True))
        self.assertIsNone(self.stored[-1]['gated_by'])
        # Отказ строк не содержит — это пробел в базе знаний, и он передаётся.
        self.reply = 'В доступных вам статьях этого нет.'
        data = self.ask('какое место занял Петров').get_json()
        self.assertEqual((data['kind'], data['gated_by'], bool(data['escalation'])),
                         ('no_answer', None, True))
        self.assertIsNone(self.stored[-1]['gated_by'])

    def test_search_showcase_gets_the_same_rows_for_the_same_space(self):
        def search(space_id):
            return self.client.get('/api/wiki/ai/search', query_string={
                'q': 'ВУ ZZ123456 какое место', 'space_id': space_id, 'lexical_only': 1}).get_json()

        data = search(7)
        self.assertEqual(data['branches']['baiga'], 1)
        self.assertEqual((data['results'][0]['source_kind'], data['results'][0]['tab'],
                          data['results'][1]['source_kind']), ('baiga', 'baiga', 'article'))
        self.assertEqual(search(9)['branches']['baiga'], 0)

    def test_history_shows_the_chip_by_the_access_of_now(self):
        for qr in (True, False):
            self.key.reset_mock()
            self.key.side_effect = [True, qr]            # гейт вики пройден, гейт раздела — как задано
            self.db.cursor.fetchall.side_effect = [[message_row(gated_by='baiga')],
                                                   [source_row(**BAIGA_SOURCE)]]
            response = self.client.get('/api/wiki/ai/chats/5', query_string={'space_id': 7})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            message = response.get_json()['messages'][0]
            source = message['sources'][0]
            self.assertEqual((source['available'], source['ref_key'], source['quote'] != ''),
                             (qr, 'ZX111111' if qr else None, qr))
            self.assertEqual(message['gated_by'], 'baiga')
            self.assertEqual(self.key.call_args_list, [mock.call(42, cursor=self.db.cursor)] * 2)

    def test_hand_escalation_of_a_gated_answer_is_refused_with_a_reason(self):
        self.db.cursor.fetchone.return_value = (5, 4, 'assistant', 'answer', 7, 'baiga')
        response = self.client.post('/api/wiki/ai/messages/100/escalate', json={'space_id': 7})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'WIKI_ESCALATE_GATED')
        self.assertIn('Списки Байги', response.get_json()['error'])
        self.assertEqual(self.escalated, [])


class EscalationGateTests(unittest.TestCase):
    """«Отправить супервайзеру» под ответом со строками раздела."""

    def escalate(self, gated_by):
        cursor = mock.MagicMock()
        cursor.fetchone.side_effect = [(5, 4, 'assistant', 'answer', 7, gated_by), None, (11, 'вопрос')]
        with mock.patch.object(wiki_questions, 'escalate',
                               return_value={'id': 1, 'status': 'open'}) as made:
            result = wiki_questions.escalate_by_asker(cursor, asker_id=42, department_id=1,
                                                      space_id=7, message_id=100)
        return result, made.called

    def test_gated_answer_is_not_handed_over(self):
        self.assertEqual(self.escalate('baiga'), ((None, 'gated'), False))
        self.assertEqual(self.escalate(None), (({'id': 1, 'status': 'open'}, None), True))

    def test_both_surfaces_keep_the_gate_of_the_answer(self):
        """Пометку ответа несут обе витрины помощника: по ней под ответом нет
        кнопки (tests/assistant_source_target.test.mjs)."""
        for parts in (('assistant', 'useAssistantChat.js'), ('wiki', 'WikiAssistant.jsx')):
            self.assertIn('gated_by: data.gated_by || null,',
                          ROOT.joinpath('src', 'components', *parts).read_text(encoding='utf-8'), parts)


# ─────────────────────────────────────────────────────────────────────────────
# Проводка
# ─────────────────────────────────────────────────────────────────────────────

def _read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding='utf-8')


class _Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return []


class WiringTests(unittest.TestCase):

    def test_qr_key_has_no_default(self):
        self.assertIn('routes_ai.register(bp, wiki_route, db, _ip, sensitive_access_granted)',
                      _read('wiki', 'routes.py'))
        key = inspect.signature(routes_ai.register).parameters['sensitive_access_granted']
        self.assertIs(key.default, inspect.Parameter.empty)

    def test_route_passes_the_person_the_space_the_turns_and_the_key(self):
        cursor, key = mock.MagicMock(), object()
        with mock.patch.object(routes_ai.baiga_assistant, 'ai_rows', return_value=['строка']) as ai_rows:
            found = routes_ai.baiga_rows(cursor, {'user_id': 42}, 'вопрос', 7, ('первый', 'второй'), key)
        self.assertEqual(found, ['строка'])
        self.assertEqual(ai_rows.call_args.args, (cursor,))
        self.assertEqual(ai_rows.call_args.kwargs, {
            'user_id': 42, 'question': 'вопрос', 'space_id': 7, 'prior': ['первый', 'второй'],
            'sensitive_access_granted': key})
        self.assertEqual([call.args[0] for call in cursor.execute.call_args_list],
                         ['SAVEPOINT wiki_ai_baiga', 'RELEASE SAVEPOINT wiki_ai_baiga'])

    def test_broken_lists_do_not_cost_the_answer(self):
        cursor = mock.MagicMock()
        with mock.patch.object(routes_ai.baiga_assistant, 'ai_rows', side_effect=RuntimeError('нет таблицы')), \
                self.assertLogs(level='ERROR'):
            self.assertEqual(routes_ai.baiga_rows(cursor, {'user_id': 1}, 'вопрос', 1, (), None), [])
        self.assertEqual(cursor.execute.call_args_list[-1].args[0], 'ROLLBACK TO SAVEPOINT wiki_ai_baiga')

    def test_transaction_broken_inside_the_key_is_rolled_back_too(self):
        """Ключ QR глотает ошибку своего запроса: исключения нет, а транзакция
        оборвана — и падает уже RELEASE. Ответ по статьям это не должно ронять."""
        calls = ((lambda cursor: routes_ai.baiga_rows(cursor, {'user_id': 1}, 'вопрос', 1, (), None),
                  'wiki_ai_baiga'),
                 (lambda cursor: routes_ai.baiga_access_now(cursor, {'user_id': 1}, None),
                  'wiki_ai_baiga_access'))
        for call, name in calls:
            cursor = mock.MagicMock()

            def execute(sql, *args):
                if sql.startswith('RELEASE'):
                    raise RuntimeError('current transaction is aborted')

            cursor.execute.side_effect = execute
            with mock.patch.object(routes_ai.baiga_assistant, 'ai_rows', return_value=['строка']), \
                    mock.patch.object(routes_ai.baiga_assistant, 'reader_context', return_value={}), \
                    self.assertLogs(level='ERROR'):
                self.assertFalse(call(cursor), name)
            self.assertEqual(cursor.execute.call_args_list[-1].args[0], 'ROLLBACK TO SAVEPOINT %s' % name)

    def test_history_access_asks_the_real_gates(self):
        """Без подмены гейта: оператор ОП видит цитату только с подтверждённым QR,
        и спрошен ключ про него и на курсоре запроса."""
        cursor = mock.MagicMock()
        for qr in (True, False):
            key = mock.Mock(return_value=qr)
            with mock.patch.object(queries, 'load_access_context', lambda cur, user_id: person('operator', 'op')):
                self.assertEqual(routes_ai.baiga_access_now(cursor, {'user_id': 42}, key), qr)
            key.assert_called_once_with(42, cursor=cursor)
        with mock.patch.object(queries, 'load_access_context', lambda cur, user_id: person('trainer', 'szov')):
            self.assertFalse(routes_ai.baiga_access_now(cursor, {'user_id': 42}, mock.Mock(return_value=True)))
        with mock.patch.object(routes_ai.baiga_assistant, 'reader_context', side_effect=RuntimeError('x')), \
                self.assertLogs(level='ERROR'):
            self.assertFalse(routes_ai.baiga_access_now(cursor, {'user_id': 1}, None))

    def test_user_text_never_reaches_the_sql(self):
        cursor = _Recorder()
        queries.assistant_candidates(cursor, driver_keys=['a' * 32], license_keys=['ZZ123456'],
                                     name_regex='^(?:жусипов)(?:[^a-zа-я-]|$)', limit=7)
        sql, params = cursor.calls[0]
        self.assertEqual(sql, "SELECT DISTINCT ON (r.driver_key) r.driver_key, r.driver_name, r.license, "
                              "r.license_key, r.city, r.park, r.zachet FROM baiga_rows r "
                              "WHERE r.driver_key = ANY(%(drivers)s) OR "
                              "(r.license_key <> '' AND r.license_key = ANY(%(licenses)s)) OR "
                              "r.search_text ~ %(names)s ORDER BY r.driver_key, r.period_start DESC, "
                              "r.id DESC LIMIT %(limit)s")
        self.assertEqual(params, {'drivers': ['a' * 32], 'licenses': ['ZZ123456'],
                                  'names': '^(?:жусипов)(?:[^a-zа-я-]|$)', 'limit': 7})
        queries.assistant_rows(cursor, driver_keys=['b' * 32], license_keys=['ZZ123456'])
        sql, params = cursor.calls[1]
        self.assertIn("WHERE r.driver_key = ANY(%(drivers)s) OR (r.license_key <> '' AND "
                      "r.license_key = ANY(%(licenses)s)) ORDER BY r.period_start DESC, r.sheet_order, "
                      "r.position, r.id LIMIT %(limit)s", sql)
        self.assertEqual(params, {'drivers': ['b' * 32], 'licenses': ['ZZ123456'], 'limit': 2000})
        for column in ('r.upload_id', 'r.license_key', 'r.driver_key', 'r.zachet', 'r.position'):
            self.assertIn(column, sql)

    def test_previous_questions_are_the_persons_own_and_in_order(self):
        """Ответы помощника вопросами не считаются: в них ФИО водителей, и по ним
        разговор «назвал» бы того, о ком человек не спрашивал."""
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [('третий',), (None,), ('первый',)]      # свежие сверху
        self.assertEqual(ai_store.recent_questions(cursor, 5, limit=12), ['первый', '', 'третий'])
        sql, params = cursor.execute.call_args.args
        self.assertEqual(' '.join(sql.split()),
                         "SELECT text FROM wiki_ai_messages WHERE chat_id = %(chat_id)s AND role = 'user' "
                         "ORDER BY seq DESC LIMIT %(limit)s")
        self.assertEqual(params, {'chat_id': 5, 'limit': 12})

    def test_place_query_is_parametrised_too(self):
        cursor = _Recorder()
        queries.assistant_places(cursor, upload_id=7, first=1, last=3, zachets=['Астана'], limit=13)
        sql, params = cursor.calls[0]
        self.assertIn("FROM baiga_rows r WHERE r.upload_id = %(upload)s AND r.position BETWEEN %(first)s AND "
                      "%(last)s AND r.zachet = ANY(%(zachets)s) ORDER BY r.sheet_order, r.position, r.id "
                      "LIMIT %(limit)s", sql)
        self.assertEqual(params, {'upload': 7, 'first': 1, 'last': 3, 'zachets': ['Астана'], 'limit': 13})
        for column in ('r.upload_id', 'r.license_key', 'r.driver_key', 'r.zachet', 'r.position', 'r.prize_name'):
            self.assertIn(column, sql)
        # Зачёт не назван — условие по зачёту не ставится вовсе.
        queries.assistant_places(cursor, upload_id=7, first=1, last=1)
        sql, params = cursor.calls[1]
        self.assertNotIn('zachet = ANY', sql)
        self.assertEqual(params, {'upload': 7, 'first': 1, 'last': 1, 'limit': 13})

    def test_nothing_asked_means_no_query(self):
        """Пустые ключи не превращаются в «все строки» (ловушка пустого номера ВУ)."""
        cursor = _Recorder()
        self.assertEqual(queries.assistant_candidates(cursor), [])
        self.assertEqual(queries.assistant_rows(cursor), [])
        self.assertEqual(cursor.calls, [])

    def test_columns_are_added_idempotently(self):
        ddl = [' '.join(str(statement).split()) for statement in wiki_schema._AI_STATEMENTS]
        gate = 'ALTER TABLE wiki_ai_messages ADD COLUMN IF NOT EXISTS gated_by VARCHAR(16);'
        self.assertIn('ALTER TABLE wiki_ai_message_sources ADD COLUMN IF NOT EXISTS ref_key VARCHAR(64);', ddl)
        self.assertIn(gate, ddl)
        created = next(index for index, sql in enumerate(ddl)
                       if 'CREATE TABLE IF NOT EXISTS wiki_ai_messages' in sql)
        self.assertLess(created, ddl.index(gate))

    def test_chip_opens_the_section_on_the_week_and_the_driver(self):
        """Куда ведёт чип, решают чистые функции (tests/assistant_source_target.test.mjs,
        tests/baiga_meta.test.mjs); здесь — что экраны зовут именно их."""
        thread = _read('src', 'components', 'assistant', 'assistantThread.jsx')
        self.assertIn('const SOURCE_ICONS = { offices: MapPin, cities: Percent, baiga: Trophy };', thread)
        self.assertIn('disabled={sourceDisabled(source)}', thread)
        wiki = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertLess(wiki.index("if (sourceDoor(source) === 'baiga') { onOpenBaiga?.(source); return; }"),
                        wiki.index('if (!slug && source?.tab) { openDirectory(source); return; }'))
        app = _read('src', 'App.jsx')
        self.assertLess(app.index("if (sourceDoor(target) === 'baiga') { openBaigaSource(target); return; }"),
                        app.index('setWikiDirectoryFocus({ ...target, nonce: Date.now() });'))
        opener = app.split('const openBaigaSource = useCallback((source) => {', 1)[1].split(
            '}, [navigateToView]);', 1)[0]
        self.assertIn('setBaigaFocus({ ...baigaFocusOf(source), nonce: Date.now() });', opener)
        self.assertIn("navigateToView('baiga');", opener)
        self.assertIn("if (view !== 'baiga') setBaigaFocus(null);", app)
        for wire in ('focus={baigaFocus}', 'onFocusConsumed={clearBaigaFocus}', 'onOpenBaiga={openBaigaSource}'):
            self.assertIn(wire, app)
        view = _read('src', 'components', 'baiga', 'BaigaView.jsx')
        self.assertIn('if (!focus || !screen) return;', view)
        self.assertIn('updateFilters(focusFilters(focus, weeks, filtersRef.current.zachet));', view)
        self.assertIn('onFocusConsumed?.();', view)

    def test_package_note_names_the_module(self):
        self.assertIn('assistant.py', _read('baiga', '__init__.py'))


if __name__ == '__main__':
    unittest.main()
