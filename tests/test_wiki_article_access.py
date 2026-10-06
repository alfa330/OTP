# -*- coding: utf-8 -*-
"""Справка в статье «где лежит и кому открыта» (wiki/article_access.py, wiki/readers.py).

Решение владельца 06.10.2026: находясь в статье, тот, кто вправе её править,
видит, в каком месте дерева она лежит и кому открыта, — людьми, списком, с тем,
как именно каждому открыто.

Что здесь сторожится и почему именно это.

1. СПИСОК ЛЮДЕЙ ОБЯЗАН СОВПАДАТЬ С ПЕРИМЕТРОМ. Кто статью читает на самом деле,
   считает периметр — по одному человеку (queries.allowed_section_ids,
   articles.visible_article_ids). Список считает другой модуль и наоборот: на
   всех людей сразу. Разойдись они — окно назовёт читателем того, кто статьи не
   видит, или промолчит о том, кто видит, и по такому экрану пойдут править
   доступ. Поэтому главный тест файла гоняет НАСТОЯЩИЙ периметр по каждому
   человеку синтетической компании и сверяет с ним и состав списка, и права.
   То же — на случайных компаниях: сочетаний правил, границ, архивов, гостей и
   режимов руками не перебрать.

2. ПРАВИЛА СВЕРХУ. Раздел открывают и правила разделов над ним, выданные
   «вместе с подразделами». Вверх они идут только через живые разделы.

3. ЧУЖУЮ ВЕТКУ НЕ НАЗЫВАЕМ. Статья лежит сразу в нескольких разделах; раздел за
   периметром смотрящего в ответ не попадает (только счётчиком), и люди,
   читающие статью только через него, — тоже.

4. ДВЕРЬ — ПО ПРАВУ ПРАВИТЬ, и ничего не пишет. Не по лестнице выдачи: тренер
   и автор статьи доступ не раздают, а где лежит их текст, знать вправе.
   СПИСОК ЛЮДЕЙ — ТОЛЬКО СУПЕР-АДМИНУ (решение владельца 06.10.2026: «кому
   открыт — у суперадмина, а где находится сама статья — редакторам и выше»):
   остальным он не считается и не уезжает в ответе.

5. АРХИВ — КАК В «СТРУКТУРЕ». Архивный раздел называется управляющему и
   держателю ветки, в которой лежит его родитель; остальным он «ещё один раздел».

SQL проверяется приёмом из test_wiki_section_perimeter: CTE в PostgreSQL
перекрывает одноимённую таблицу, поэтому боевой текст запросов исполняется над
синтетическими строками. Соединение read-only — боевые таблицы не читаются.
Там, где базы нет (сборочный сервер), эти тесты пропускаются — расчёт списка
без базы держат SectionReadersTests и соседние классы, параметры и условия
запросов — QueryShapeTests.
"""

import json
import random
import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from tests import prod_db  # noqa: E402
from wiki import access as wiki_access  # noqa: E402
from wiki import article_access as aa  # noqa: E402
from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import queries, readers, structure  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

PERMS = ('can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve')


def node(ident, name, *, status='active', dept=None, space=1, scope='restricted', owner=None):
    return {'id': ident, 'name': name, 'status': status, 'space_id': space,
            'department_id': dept, 'visibility_scope': scope, 'owner_user_id': owner}


def rule(ident, section, subject_type='department', subject_id=1, *, role=None,
         level=None, job=None, deep=False, label='СЗоВ', **flags):
    out = {'id': ident, 'section_id': section, 'subject_type': subject_type,
           'subject_id': subject_id, 'subject_role': role, 'min_role_level': level,
           'job_title': job, 'grant_subsections': deep, 'manage_subsections': False,
           'subject_label': label}
    out.update({name: False for name in PERMS})
    out['can_read'] = True
    out.update(flags)
    return out


# Ветка СЗоВ, как её рисуют руками: должности вложены друг в друга.
OPERATOR = node(4, 'Оператор')
SUPERVISOR = node(3, 'Супервайзер')
HEAD = node(2, 'Руководитель группы')
BRANCH = node(19, 'СЗоВ', dept=1)
DIRECTOR = node(1, 'Коммерческий директор')
CHAIN = [OPERATOR, SUPERVISOR, HEAD, BRANCH, DIRECTOR]


# ─────────────────────────────────────────────────────────────────────────────
# Дерево: докуда достают правила сверху и что рисуется на экране
# ─────────────────────────────────────────────────────────────────────────────

class ChainTests(unittest.TestCase):

    def test_live_chain_runs_to_the_root_of_a_living_branch(self):
        self.assertEqual([n['id'] for n in aa.live_chain(CHAIN)], [4, 3, 2, 19, 1])

    def test_live_chain_stops_at_an_archived_ancestor(self):
        """Правило над архивным разделом вниз не проходит — как в периметре."""
        chain = [OPERATOR, SUPERVISOR, dict(HEAD, status='archived'), BRANCH, DIRECTOR]
        self.assertEqual([n['id'] for n in aa.live_chain(chain)], [4, 3])

    def test_archived_section_has_no_live_chain(self):
        """В архивном разделе не действует ни его правило, ни правило сверху."""
        chain = [dict(OPERATOR, status='archived'), SUPERVISOR, HEAD]
        self.assertEqual(aa.live_chain(chain), [])

    def test_path_is_drawn_from_the_root_down(self):
        self.assertEqual([n['name'] for n in aa.display_path(CHAIN)],
                         ['Коммерческий директор', 'СЗоВ', 'Руководитель группы',
                          'Супервайзер', 'Оператор'])

    def test_path_lifts_the_section_above_an_archived_parent(self):
        """Живой раздел под архивным родителем «Структура» рисует в корне —
        здесь он обязан стоять там же."""
        chain = [OPERATOR, SUPERVISOR, dict(HEAD, status='archived'), BRANCH, DIRECTOR]
        self.assertEqual([n['name'] for n in aa.display_path(chain)],
                         ['Супервайзер', 'Оператор'])

    def test_archived_section_itself_stays_in_the_path(self):
        chain = [dict(OPERATOR, status='archived'), SUPERVISOR]
        self.assertEqual([n['name'] for n in aa.display_path(chain)],
                         ['Супервайзер', 'Оператор'])

    def test_empty_chain_draws_nothing(self):
        self.assertEqual(aa.display_path([]), [])
        self.assertEqual(aa.live_chain(None), [])


# ─────────────────────────────────────────────────────────────────────────────
# Какие правила действуют в разделе
# ─────────────────────────────────────────────────────────────────────────────

class ReachingRulesTests(unittest.TestCase):

    def reaching(self, rules, chain=None):
        return [r['id'] for r in readers.reaching_rules(
            rules, aa.live_chain(chain if chain is not None else CHAIN))]

    def test_own_rule_counts_with_or_without_subsections(self):
        self.assertEqual(self.reaching([rule(1, 4), rule(2, 4, deep=True)]), [1, 2])

    def test_rule_above_comes_down_only_with_subsections(self):
        """Тумблер «вместе с подразделами» — дословное обещание: без него
        правило родителя в подраздел не спускается."""
        self.assertEqual(self.reaching([rule(1, 3, deep=True), rule(2, 3, deep=False)]), [1])

    def test_rule_from_several_levels_up_arrives(self):
        self.assertEqual(self.reaching([rule(1, 1, deep=True), rule(2, 19, deep=True)]),
                         [1, 2])

    def test_rule_above_an_archived_section_does_not_arrive(self):
        chain = [OPERATOR, SUPERVISOR, dict(HEAD, status='archived'), BRANCH]
        self.assertEqual(self.reaching([rule(1, 19, deep=True), rule(2, 2, deep=True),
                                        rule(3, 3, deep=True)], chain), [3])

    def test_rule_without_reading_still_reaches(self):
        """Права идут вместе с правилом целиком (queries._SECTION_RIGHTS_CTE);
        открывает ли оно раздел НА ЧТЕНИЕ, решается отдельно — по can_read."""
        self.assertEqual(self.reaching([rule(1, 4, can_read=False, can_edit=True)]), [1])

    def test_rule_of_a_neighbour_section_is_ignored(self):
        self.assertEqual(self.reaching([rule(1, 999, deep=True)]), [])

    def test_archived_section_has_no_rules_at_all(self):
        chain = [dict(OPERATOR, status='archived'), SUPERVISOR]
        self.assertEqual(self.reaching([rule(1, 4), rule(2, 3, deep=True)], chain), [])


# ─────────────────────────────────────────────────────────────────────────────
# Гостевая выдача: докуда она доходит
# ─────────────────────────────────────────────────────────────────────────────

def grant(user, *, section=None, space=None, article=None, deep=True):
    return {'user_id': user, 'section_id': section, 'space_id': space,
            'article_id': article, 'include_subsections': deep}


class GuestCoverageTests(unittest.TestCase):

    def test_grant_on_the_section_itself(self):
        self.assertEqual(aa.guests_of_place(CHAIN, [grant(7, section=4, deep=False)]), {7})

    def test_grant_above_needs_subsections(self):
        grants = [grant(7, section=3, deep=True), grant(8, section=3, deep=False)]
        self.assertEqual(aa.guests_of_place(CHAIN, grants), {7})

    def test_archived_section_in_between_blocks_the_grant(self):
        chain = [OPERATOR, dict(SUPERVISOR, status='archived'), HEAD]
        grants = [grant(7, section=2, deep=True)]
        self.assertEqual(aa.guests_of_place(chain, grants), set())

    def test_granted_section_may_itself_be_archived(self):
        """Выданный раздел берётся как есть (queries._GUEST_SECTIONS_CTE)."""
        chain = [OPERATOR, dict(SUPERVISOR, status='archived'), HEAD]
        self.assertEqual(aa.guests_of_place(chain, [grant(7, section=3, deep=True)]), {7})
        archived = [dict(OPERATOR, status='archived'), SUPERVISOR]
        self.assertEqual(aa.guests_of_place(archived, [grant(7, section=4)]), {7})
        self.assertEqual(aa.guests_of_place(archived, [grant(7, section=3)]), set())

    def test_whole_space_covers_only_its_living_sections(self):
        self.assertEqual(aa.guests_of_place(CHAIN, [grant(7, space=1)]), {7})
        self.assertEqual(aa.guests_of_place(CHAIN, [grant(7, space=2)]), set())
        archived = [dict(OPERATOR, status='archived')]
        self.assertEqual(aa.guests_of_place(archived, [grant(7, space=1)]), set())

    def test_article_grants_are_counted_separately(self):
        self.assertEqual(aa.guests_of_place(CHAIN, [grant(7, article=500)]), set())


# ─────────────────────────────────────────────────────────────────────────────
# Кто читает статью — расчёт без базы
#
# Совпадения правил с людьми (кто под какое правило подпадает) здесь заданы
# руками: их считает запрос, и проверяется он ниже, на базе. Здесь — всё, что
# делается с совпадениями дальше: периметр раздела, условия видимости статьи,
# права, пометки и кого из читателей можно назвать смотрящему.
# ─────────────────────────────────────────────────────────────────────────────

WIKI_ADMIN = {'id': 9, 'code': 'wiki_admin', 'can_read': True, 'can_create': True,
              'can_edit': True, 'can_delete': True, 'can_publish': True,
              'can_approve': True, 'can_manage_users': True,
              'can_manage_structure': True, 'can_manage_access': True}
WIKI_READER = {'id': 8, 'code': 'reader', 'can_read': True, 'can_create': False,
               'can_edit': False, 'can_delete': False, 'can_publish': False,
               'can_approve': False, 'can_manage_users': False,
               'can_manage_structure': False, 'can_manage_access': False}


def person(uid, role='operator', dept=1, *, name=None, headed=(), groups=(),
           direction=None, wiki_roles=(), job=None, mode='auto', wiki_enabled=True,
           guest=False):
    """Строка человека — как её отдаёт readers.load_people."""
    row = {
        'user_id': uid, 'name': name or 'Человек %d' % uid, 'otp_role': role,
        'department_id': dept, 'department_name': 'Отдел %s' % dept if dept else None,
        'direction_id': direction, 'job_title': job, 'wiki_enabled': wiki_enabled,
        'headed_department_ids': list(headed), 'group_ids': list(groups),
        'wiki_roles': list(wiki_roles), 'access_mode': mode, 'has_guest_access': guest,
    }
    row['subjects'] = wiki_access.collect_subjects(
        user_id=uid, otp_role=role, department_id=dept, headed_department_ids=headed,
        direction_id=direction, group_ids=groups,
        wiki_role_ids=[r['id'] for r in wiki_roles], job_title=job)
    row['role_capabilities'] = wiki_access.resolve_capabilities(
        role, row['wiki_roles'], is_department_head=bool(headed))
    return row


def place(ident=4, *, space=1, named=True, active=True, rules=(), public=None,
          owner=None, guests=()):
    return {'id': ident, 'active': active, 'space_id': space, 'named': named,
            'reach_rules': list(rules), 'public': public, 'owner_user_id': owner,
            'guests': set(guests)}


ARTICLE = {'id': 500, 'title': 'Работа с разделом Акции', 'status': 'published',
           'visibility_mode': 'inherit', 'strict_mode': False,
           'author_id': 99, 'owner_user_id': None, 'section_ids': [4, 31]}

# Пространство 1 выдано отделам 1, 2 и 3; пространство 2 — отделу 4.
SPACES_OF = {1: {1, 2, 3}, 2: {4}}


def who(people, places, *, article=None, hits=None, article_rules=(), article_hits=None,
        article_guests=(), spaces=None, manual=None, caps=None):
    found = readers.readers(
        dict(article or ARTICLE), people, places,
        space_departments=SPACES_OF if spaces is None else spaces,
        section_hits=hits or {}, article_rules=list(article_rules),
        article_hits=article_hits or {}, article_guests=set(article_guests),
        manual_perimeter=manual, capabilities_of=caps)
    return {p['user_id']: p for p in found}


def rights(found, uid):
    return ''.join(letter for letter, name in aa.RIGHT_LETTERS
                   if found[uid]['permissions'][name])


def captions(found, uid):
    return [(v['kind'], v['label']) for v in found[uid]['via']]


def article_rule(ident, subject_type='user', subject_id=10, *, mode='grant', role=None,
                 label=None, **flags):
    out = {'id': ident, 'article_id': 500, 'subject_type': subject_type,
           'subject_id': subject_id, 'subject_role': role, 'mode': mode,
           'subject_label': label}
    out.update({name: False for name in PERMS})
    out['can_read'] = True
    out.update(flags)
    return out


class SectionReadersTests(unittest.TestCase):
    """Обычная статья: её читают те, кому открыт её раздел."""

    def test_matched_reading_rule_opens_the_section(self):
        found = who([person(10), person(11)], [place(rules=[rule(1, 4)])], hits={1: {10}})
        self.assertEqual(sorted(found), [10])
        self.assertEqual(rights(found, 10), 'r')
        self.assertTrue(found[10]['listed'])

    def test_rights_of_every_matched_rule_add_up(self):
        """Своё правило читает, правило сверху правит — человек в итоге правит
        (access._merge_grants)."""
        rules = [rule(1, 4), rule(2, 3, level=30, deep=True, can_create=True, can_edit=True)]
        found = who([person(10, 'sv')], [place(rules=rules)], hits={1: {10}, 2: {10}})
        self.assertEqual(rights(found, 10), 'rce')

    def test_rule_without_reading_does_not_open_the_section(self):
        """В базе такое правило лежать может. Раздел оно не открывает —
        периметр собирает его только по can_read."""
        rules = [rule(1, 4, can_read=False, can_edit=True)]
        self.assertEqual(who([person(10)], [place(rules=rules)], hits={1: {10}}), {})

    def test_rule_without_reading_adds_rights_to_one_who_reads_otherwise(self):
        rules = [rule(1, 4, can_read=False, can_edit=True)]
        found = who([person(10), person(11)], [place(rules=rules, public=set())],
                    hits={1: {10}})
        self.assertEqual((rights(found, 10), rights(found, 11)), ('re', 'r'))

    def test_right_written_by_a_rule_works_for_an_operator(self):
        """Инцидент 21.08.2026: право, выписанное правилом, гасло у оператора —
        способность выводилась из одной должности. Список обязан показывать то,
        что человек на самом деле может."""
        rules = [rule(1, 4, 'user', 10, can_create=True, can_edit=True, can_delete=True,
                      can_publish=True, can_approve=True)]
        found = who([person(10, 'operator')], [place(rules=rules)], hits={1: {10}})
        self.assertEqual(rights(found, 10), 'rcepad')

    def test_space_boundary_beats_a_matched_rule(self):
        """Граница пространства — последнее слово: правило на роль «оператор»
        совпало с оператором Тез КЦ, а пространство его отделу не выдано."""
        people = [person(10, dept=1), person(40, dept=4), person(50, dept=None)]
        found = who(people, [place(rules=[rule(1, 4, 'otp_role', None, role='operator')])],
                    hits={1: {10, 40, 50}})
        self.assertEqual(sorted(found), [10])

    def test_space_without_a_department_list_is_open_to_everyone(self):
        people = [person(10, dept=1), person(40, dept=4), person(50, dept=None)]
        found = who(people, [place(space=3, rules=[rule(1, 4)])], hits={1: {10, 40, 50}})
        self.assertEqual(sorted(found), [10, 40, 50])

    def test_headed_department_counts_for_the_boundary(self):
        """Глава отдела входит в него субъектом, даже числясь в другом."""
        found = who([person(40, 'admin', dept=4, headed=[2])],
                    [place(rules=[rule(1, 4)])], hits={1: {40}})
        self.assertEqual(sorted(found), [40])

    def test_public_section_with_and_without_a_department_list(self):
        people = [person(10, dept=1), person(30, dept=3), person(40, dept=4)]
        everyone = who(people, [place(public=set())])
        self.assertEqual(sorted(everyone), [10, 30])       # Тез КЦ режет граница
        narrowed = who(people, [place(public={1, 2})])
        self.assertEqual(sorted(narrowed), [10])
        self.assertEqual(who(people, [place(public=None)]), {})

    def test_owner_reads_his_section(self):
        found = who([person(10), person(11)], [place(owner=10)])
        self.assertEqual(sorted(found), [10])
        self.assertEqual(captions(found, 10), [('owner', None)])
        # Владение границу пространства не обходит.
        self.assertEqual(who([person(40, dept=4)], [place(owner=40)]), {})

    def test_guest_crosses_the_boundary_but_only_reads(self):
        """Именная гостевая выдача — единственное исключение из границы
        (решение владельца 25.08.2026). Прав она не даёт, и правило на роль,
        под которое гость подпадает, ему их тоже не добавляет: права из правил
        граница режет без гостевой щели."""
        rules = [rule(1, 4, 'otp_role', None, role='operator', can_edit=True)]
        found = who([person(40, dept=4, guest=True)], [place(rules=rules, guests={40})],
                    hits={1: {40}})
        self.assertEqual(rights(found, 40), 'r')
        self.assertEqual(captions(found, 40), [('guest', None)])

    def test_archived_section_opens_nothing_except_to_its_guest(self):
        people = [person(10), person(11, guest=True)]
        found = who(people, [place(active=False, public=set(), owner=10, guests={11})])
        self.assertEqual(sorted(found), [11])

    def test_archived_section_is_not_opened_by_a_rule_either(self):
        """Правил архивному разделу сборка не подаёт (live_chain пуст) — но и
        поданное правило его не откроет: архив закрыт сам по себе."""
        found = who([person(10)], [place(active=False, rules=[rule(1, 4)])], hits={1: {10}})
        self.assertEqual(found, {})

    def test_people_who_cannot_enter_the_wiki_are_not_readers(self):
        """Тумблер «раздел Вики выдан отделу» закрывает дверь целиком: правило
        на такого человека в базе лежит, а войти ему некуда."""
        people = [person(10, wiki_enabled=False), person(11),
                  person(12, wiki_enabled=False, guest=True),
                  person(13, 'super_admin', wiki_enabled=False),
                  person(14, wiki_enabled=False, wiki_roles=[WIKI_ADMIN]),
                  # Роль вики заменила должностные способности — супер-админа
                  # пускает сама его роль, а не «управление структурой».
                  person(15, 'super_admin', wiki_enabled=False, wiki_roles=[WIKI_READER])]
        found = who(people, [place(rules=[rule(1, 4)])],
                    hits={1: {10, 11, 12, 13, 14, 15}})
        self.assertEqual(sorted(found), [11, 12, 13, 14, 15])

    def test_status_of_the_article_does_not_change_the_list(self):
        """Список — те, кто откроет статью, когда она выйдет: редактор черновика
        спрашивает именно это."""
        people, places = [person(10), person(11)], [place(rules=[rule(1, 4)])]
        published = who(people, places, hits={1: {10}})
        for status in ('draft', 'on_approval', 'archived'):
            other = who(people, places, hits={1: {10}}, article=dict(ARTICLE, status=status))
            self.assertEqual(other, published, status)

    def test_everyone_in_the_list_reads(self):
        """Чтение даёт и публичность, и гостевая выдача — расчёт прав на запись
        о них не знает, и без поправки в списке стоял бы «читатель без чтения»."""
        found = who([person(10), person(11, guest=True)],
                    [place(public=set(), guests={11})])
        self.assertTrue(all(p['permissions']['can_read'] for p in found.values()))


class MasterKeyTests(unittest.TestCase):

    def test_super_admin_reads_everything_alive(self):
        boss = person(1, 'super_admin', dept=4)
        found = who([boss], [place()])
        self.assertEqual(rights(found, 1), 'rcepad')
        self.assertEqual(captions(found, 1), [], 'роль и так стоит в строке')
        self.assertTrue(found[1]['listed'])

    def test_wiki_admin_reads_everything_and_is_marked(self):
        admin = person(2, 'operator', dept=2, wiki_roles=[WIKI_ADMIN])
        found = who([admin], [place(named=False)])
        self.assertEqual(rights(found, 2), 'rcepad')
        self.assertEqual(captions(found, 2), [('wiki_admin', None)])
        self.assertTrue(found[2]['listed'], 'мастер-ключ читает поверх разделов')

    def test_master_key_explains_the_access_alone(self):
        """Носитель мастер-ключа заодно подпадает под правило статьи, состоит в
        группе, значится автором — но читает он всё и так. Пометка у него одна
        («администратор вики»), у супер-админа нет ни одной."""
        people = [person(1, 'super_admin'), person(2, dept=2, wiki_roles=[WIKI_ADMIN])]
        article = dict(ARTICLE, author_id=2)
        found = who(people, [place(rules=[rule(1, 4, 'group', 5, label='Основа')])],
                    article=article, hits={1: {1, 2}},
                    article_rules=[article_rule(1, 'otp_role', None, role='operator')],
                    article_hits={1: {1, 2}}, article_guests={1, 2})
        self.assertEqual((captions(found, 1), captions(found, 2)),
                         ([], [('wiki_admin', None)]))

    def test_wiki_admin_outside_the_space_does_not_reach_an_archived_only_article(self):
        """Мастер-ключ открывает ЖИВЫЕ разделы. У статьи, лежащей только в
        архивном, остаётся граница пространства — и чужому отделу она закрыта."""
        places = [place(active=False)]
        inside = who([person(2, 'operator', dept=2, wiki_roles=[WIKI_ADMIN])], places)
        outside = who([person(3, 'operator', dept=4, wiki_roles=[WIKI_ADMIN])], places)
        boss = who([person(1, 'super_admin', dept=4)], places)
        self.assertEqual((sorted(inside), sorted(outside), sorted(boss)), ([2], [], [1]))

    def test_rights_of_a_master_key_holder_come_from_the_real_calculation(self):
        """У носителя мастер-ключа права на статью равны его способностям, а их
        могло поднять правило в любом разделе — такие считает настоящий
        load_capabilities, и расчёт обязан взять их, а не собрать свои."""
        limited = dict(WIKI_ADMIN, can_delete=False)
        admin = person(2, 'operator', dept=2, wiki_roles=[limited])
        self.assertEqual(rights(who([admin], [place()]), 2), 'rcepa')
        lifted = dict(admin['role_capabilities'], can_delete=True)
        self.assertEqual(rights(who([admin], [place()], caps={2: lifted}), 2), 'rcepad')


class ManualModeTests(unittest.TestCase):

    def test_manual_perimeter_decides_not_the_rules(self):
        """В ручном режиме правила раздел не открывают: периметр человека
        берётся настоящим расчётом и подаётся готовым."""
        people = [person(10, mode='manual'), person(11, mode='manual')]
        found = who(people, [place(rules=[rule(1, 4)])], hits={1: {10, 11}},
                    manual={10: {4}, 11: set()})
        self.assertEqual(sorted(found), [10])
        self.assertEqual(captions(found, 10), [('manual', None)])

    def test_rules_do_not_lift_capabilities_in_manual_mode(self):
        """queries.load_capabilities: в ручном режиме выписанное правилами
        способность не поднимает — оператор остаётся читателем."""
        rules = [rule(1, 4, can_edit=True)]
        found = who([person(10, mode='manual'), person(11, 'sv', mode='manual')],
                    [place(rules=rules)], hits={1: {10, 11}}, manual={10: {4}, 11: {4}})
        self.assertEqual((rights(found, 10), rights(found, 11)), ('r', 're'))

    def test_public_section_is_named_as_such_for_a_manual_reader(self):
        found = who([person(10, mode='manual')], [place(public=set())], manual={10: {4}})
        self.assertEqual(captions(found, 10), [])


class ArticleRulesTests(unittest.TestCase):
    """Правила самой статьи: выдача поверх раздела, запрет, «по списку», строгий режим."""

    def test_grant_opens_the_article_over_the_section(self):
        grants = [article_rule(1, 'user', 30, can_edit=True)]
        found = who([person(10), person(30, dept=3)], [place(rules=[rule(1, 4)])],
                    hits={1: {10}}, article_rules=grants, article_hits={1: {30}})
        self.assertEqual(sorted(found), [10, 30])
        self.assertEqual(rights(found, 30), 're')
        self.assertEqual(captions(found, 30), [('article_rule', None)])

    def test_deny_closes_the_article_to_a_reader_of_the_section(self):
        denies = [article_rule(1, 'user', 10, mode='deny', **{n: True for n in PERMS})]
        found = who([person(10), person(11)], [place(rules=[rule(1, 4)])],
                    hits={1: {10, 11}}, article_rules=denies, article_hits={1: {10}})
        self.assertEqual(sorted(found), [11])

    def test_partial_deny_takes_the_right_but_not_the_reading(self):
        denies = [article_rule(1, 'user', 10, mode='deny', can_read=False, can_edit=True)]
        found = who([person(10, 'sv')], [place(rules=[rule(1, 4, can_edit=True)])],
                    hits={1: {10}}, article_rules=denies, article_hits={1: {10}})
        self.assertEqual(rights(found, 10), 'r')

    def test_deny_does_not_stop_the_wiki_admin_but_stops_a_super_admin_without_the_key(self):
        """Запрет перекрывает способность «управление доступами», а не роль. У
        супер-админа с назначенной ролью вики её может не быть (роли вики
        заменяют должностные) — и запрет на него действует."""
        denies = [article_rule(1, 'otp_role', None, mode='deny', role='operator',
                               **{n: True for n in PERMS})]
        people = [person(2, 'operator', dept=2, wiki_roles=[WIKI_ADMIN]),
                  person(3, 'super_admin', dept=1, wiki_roles=[WIKI_READER]),
                  person(1, 'super_admin', dept=1)]
        found = who(people, [place()], article_rules=denies, article_hits={1: {1, 2, 3}})
        self.assertEqual(sorted(found), [1, 2])

    def test_by_list_article_is_not_opened_by_the_section(self):
        article = dict(ARTICLE, visibility_mode='restricted')
        grants = [article_rule(1, 'department', 2, label='ОП')]
        people = [person(10), person(20, dept=2), person(21, dept=2)]
        found = who(people, [place(rules=[rule(1, 4)], public=set())], article=article,
                    hits={1: {10}}, article_rules=grants, article_hits={1: {20, 21}})
        self.assertEqual(sorted(found), [20, 21])
        self.assertEqual(captions(found, 20), [], 'у статьи по списку правило — обычный путь')

    def test_by_list_grant_is_named_by_its_addressee(self):
        article = dict(ARTICLE, visibility_mode='restricted')
        grants = [article_rule(1, 'user', 30), article_rule(2, 'group', 5, label='Основа')]
        found = who([person(30, dept=3), person(31, dept=3)], [place()], article=article,
                    article_rules=grants, article_hits={1: {30}, 2: {31}})
        self.assertEqual(captions(found, 30), [('user', None)])
        self.assertEqual(captions(found, 31), [('group', 'Основа')])

    def test_by_list_article_is_not_explained_by_the_section(self):
        """Раздел статью «по списку» не открывает — и пометок раздела у её
        читателя нет: владелец раздела и гость раздела названы тем, что на
        самом деле открыло им статью."""
        grants = [article_rule(1, 'user', 10)]
        for mode in ({'visibility_mode': 'restricted'}, {'strict_mode': True}):
            found = who([person(10, guest=True)], [place(owner=10, guests={10})],
                        article=dict(ARTICLE, **mode), article_rules=grants,
                        article_hits={1: {10}})
            self.assertEqual(captions(found, 10), [('user', None)], mode)

    def test_author_who_also_owns_the_article_is_named_once(self):
        article = dict(ARTICLE, visibility_mode='restricted', author_id=10, owner_user_id=10)
        found = who([person(10)], [place()], article=article)
        self.assertEqual(captions(found, 10), [('author', None)])

    def test_author_reads_and_edits_his_article(self):
        article = dict(ARTICLE, visibility_mode='restricted', author_id=10, owner_user_id=11)
        found = who([person(10), person(11), person(12)], [place()], article=article)
        self.assertEqual(sorted(found), [10, 11])
        self.assertEqual((rights(found, 10), rights(found, 11)), ('re', 're'))
        self.assertEqual(captions(found, 10), [('author', None)])
        self.assertEqual(captions(found, 11), [('article_owner', None)])

    def test_article_guest_reads_by_list_but_not_in_strict_mode(self):
        people = [person(40, dept=4, guest=True)]
        by_list = who(people, [place()], article=dict(ARTICLE, visibility_mode='restricted'),
                      article_guests={40})
        strict = who(people, [place()], article=dict(ARTICLE, strict_mode=True),
                     article_guests={40})
        self.assertEqual((sorted(by_list), sorted(strict)), ([40], []))
        self.assertEqual(captions(by_list, 40), [('guest', None)])

    def test_strict_mode_needs_an_explicit_grant(self):
        """Строгий режим: читают только явный грант, автор и супер-админ. Ни
        раздел, ни мастер-ключ администратора вики, ни владение статьёй его не
        открывают (articles._VISIBLE_ARTICLES_SQL)."""
        article = dict(ARTICLE, strict_mode=True, author_id=12, owner_user_id=13)
        grants = [article_rule(1, 'user', 11)]
        people = [person(10), person(11), person(12), person(13),
                  person(2, 'operator', dept=2, wiki_roles=[WIKI_ADMIN]),
                  person(1, 'super_admin')]
        found = who(people, [place(rules=[rule(1, 4)])], article=article,
                    hits={1: {10, 11, 12, 13}}, article_rules=grants, article_hits={1: {11}})
        self.assertEqual(sorted(found), [1, 11, 12])

    def test_strict_mode_keeps_the_rights_of_the_section(self):
        """Чтение — только по списку, а права у попавшего в список складываются
        из правил раздела и статьи (access.resolve_article_permissions)."""
        article = dict(ARTICLE, strict_mode=True)
        found = who([person(11, 'sv')], [place(rules=[rule(1, 4, can_edit=True)])],
                    article=article, hits={1: {11}},
                    article_rules=[article_rule(1, 'user', 11)], article_hits={1: {11}})
        self.assertEqual(rights(found, 11), 're')

    def test_boundary_of_the_space_holds_for_the_article_itself(self):
        """Правило статьи и авторство границу пространства не пробивают: так
        статья-классификатор и осталась закрытой для Тез КЦ."""
        article = dict(ARTICLE, visibility_mode='restricted', author_id=41)
        grants = [article_rule(1, 'otp_role', None, role='operator')]
        people = [person(10), person(40, dept=4), person(41, dept=4)]
        closed = who(people, [place()], article=article, article_rules=grants,
                     article_hits={1: {10, 40, 41}})
        self.assertEqual(sorted(closed), [10])
        # Хоть один раздел статьи в пространстве без границы — и она снята.
        opened = who(people, [place(), place(70, space=3, named=False)], article=article,
                     article_rules=grants, article_hits={1: {10, 40, 41}})
        self.assertEqual(sorted(opened), [10, 40, 41])

    def test_article_without_sections_belongs_to_no_space(self):
        article = dict(ARTICLE, visibility_mode='restricted', section_ids=[])
        found = who([person(40, dept=4)], [], article=article,
                    article_rules=[article_rule(1, 'user', 40)], article_hits={1: {40}})
        self.assertEqual(sorted(found), [40])


class NamingTests(unittest.TestCase):
    """Кого из читателей можно назвать смотрящему и чем подписать."""

    def test_reader_of_a_hidden_place_is_not_named(self):
        """Статья лежит и в чужой ветке. Её читателей смотрящему не называем —
        как и сам раздел."""
        places = [place(4, rules=[rule(1, 4)]), place(31, named=False, rules=[rule(2, 31)])]
        found = who([person(10), person(20, dept=2)], places, hits={1: {10}, 2: {20}})
        self.assertEqual({uid: p['listed'] for uid, p in found.items()}, {10: True, 20: False})

    def test_rights_count_every_place_even_a_hidden_one(self):
        """Назван человек по своему разделу, а правит статью по чужому: права у
        него настоящие, по всем разделам статьи."""
        places = [place(4, rules=[rule(1, 4)]),
                  place(31, named=False, rules=[rule(2, 31, can_edit=True)])]
        found = who([person(10, 'sv')], places, hits={1: {10}, 2: {10}})
        self.assertEqual((found[10]['listed'], rights(found, 10)), (True, 're'))

    def test_hidden_place_does_not_explain_itself(self):
        places = [place(4, public=set()),
                  place(31, named=False, rules=[rule(2, 31, 'user', 10)])]
        found = who([person(10)], places, hits={2: {10}})
        self.assertEqual(captions(found, 10), [])

    def test_article_level_reader_is_named_whatever_the_places(self):
        found = who([person(30, dept=3)], [place(31, named=False)],
                    article_rules=[article_rule(1, 'user', 30)], article_hits={1: {30}})
        self.assertTrue(found[30]['listed'])

    def test_usual_way_needs_no_caption(self):
        """По отделу, должности и публичности — обычный путь: отдел и должность
        и так стоят в строке человека."""
        rules = [rule(1, 4), rule(2, 4, 'otp_role', None, role='operator')]
        found = who([person(10)], [place(rules=rules, public=set())], hits={1: {10}, 2: {10}})
        self.assertEqual(captions(found, 10), [])

    def test_extra_source_is_named_only_when_it_adds_rights(self):
        """Оператор читает по отделу и состоит в группе с тем же чтением —
        пометка была бы шумом. Личное правило на правку объясняет «Правку»."""
        rules = [rule(1, 4), rule(2, 4, 'group', 5, label='Основа'),
                 rule(3, 4, 'user', 11, can_edit=True)]
        found = who([person(10), person(11)], [place(rules=rules)],
                    hits={1: {10, 11}, 2: {10}, 3: {11}})
        self.assertEqual(captions(found, 10), [])
        self.assertEqual(captions(found, 11), [('user', None)])

    def test_reader_without_the_usual_way_is_always_explained(self):
        rules = [rule(2, 4, 'group', 5, label='Основа'), rule(3, 4, 'direction', 7, label='Регионы'),
                 rule(4, 4, 'department_head', 2, label='Глава: ОП'),
                 rule(5, 4, 'wiki_role', 3, label='Редактор')]
        people = [person(20, dept=2), person(21, dept=2), person(22, 'admin', dept=2),
                  person(23, dept=2)]
        found = who(people, [place(rules=rules)], hits={2: {20}, 3: {21}, 4: {22}, 5: {23}})
        self.assertEqual([captions(found, uid) for uid in (20, 21, 22, 23)],
                         [[('group', 'Основа')], [('direction', 'Регионы')],
                          [('department_head', None)], [('wiki_role', 'Редактор')]])

    def test_captions_go_from_personal_to_general_without_repeats(self):
        rules = [rule(1, 4, 'group', 5, label='Основа'), rule(2, 4, 'user', 30),
                 rule(3, 3, 'user', 30, deep=True, can_edit=True)]
        found = who([person(30, dept=3, guest=True)], [place(rules=rules, guests={30})],
                    hits={1: {30}, 2: {30}, 3: {30}})
        self.assertEqual(captions(found, 30),
                         [('user', None), ('group', 'Основа'), ('guest', None)])

    def test_person_row_carries_what_the_window_shows(self):
        row = person(10, 'marketing_manager', dept=3, name='Видеограф Вера', job='Видеограф')
        found = who([row], [place(rules=[rule(1, 4)])], hits={1: {10}})
        self.assertEqual({k: found[10][k] for k in ('name', 'role', 'job_title', 'department')},
                         {'name': 'Видеограф Вера', 'role': 'marketing_manager',
                          'job_title': 'Видеограф', 'department': 'Отдел 3'})


# ─────────────────────────────────────────────────────────────────────────────
# Сборка ответа
# ─────────────────────────────────────────────────────────────────────────────

# Та же статья лежит ещё и в ветке ОП — близнеце ветки СЗоВ.
OP_CHAIN = [node(31, 'Оператор'), node(30, 'Супервайзер'), node(28, 'ОП', dept=2), DIRECTOR]

READER_CAPS = {'can_read': True}
ADMIN_CAPS = {'can_read': True, 'can_manage_structure': True, 'can_manage_access': True}
# Способности по одной: архив «Структура» отдаёт любой из двух.
STRUCTURE_CAPS = {'can_read': True, 'can_manage_structure': True}
ACCESS_CAPS = {'can_read': True, 'can_manage_access': True}


class DescribeTests(unittest.TestCase):

    def setUp(self):
        self.chains = {4: CHAIN, 31: OP_CHAIN}
        self.rules = [rule(1, 4), rule(2, 3, level=30, deep=True, can_create=True, can_edit=True),
                      rule(3, 31, subject_id=2, label='Отдел продаж')]
        self.people = [person(10, name='Оператор СЗоВ'), person(11, 'sv', name='Супервайзер СЗоВ'),
                       person(20, dept=2, name='Оператор ОП')]
        self.section_hits = {1: {10, 11}, 2: {11}, 3: {20}}
        self.article_hits = {}
        self.grants = []
        self.article_rules = []
        self.public = {}
        self.space_names = {1: 'Таксопарки'}
        self.space_departments = {1: {1, 2, 3}}
        # Ветки, где смотрящему выдано «заводить подразделы», и сколько раз о
        # них спросили: запрос отдельный, и без архивного места он не нужен.
        self.branches = frozenset()
        self.branch_calls = []
        self.rule_calls = []
        self.guest_calls = []
        self.public_calls = []
        self.match_calls = []
        self.manual_calls = []
        self.manual_allowed = set()
        self.exact_calls = []

        def branches(_cursor, ctx, subjects):
            self.branch_calls.append((ctx['user_id'], subjects))
            return self.branches

        def rules(_cursor, section_id=None, section_ids=None):
            self.rule_calls.append(sorted(section_ids))
            return [r for r in self.rules if r['section_id'] in section_ids]

        def guests(_cursor, article_id, section_ids, space_ids):
            self.guest_calls.append((article_id, sorted(section_ids), sorted(space_ids)))
            return list(self.grants)

        def public(_cursor, ids):
            self.public_calls.append(sorted(ids))
            return {i: set(self.public[i]) for i in ids if i in self.public}

        def matched(_cursor, people, *, section_rule_ids=(), article_rule_ids=()):
            self.match_calls.append((sorted(p['user_id'] for p in people),
                                     sorted(section_rule_ids), sorted(article_rule_ids)))
            return dict(self.section_hits), dict(self.article_hits)

        def allowed(_cursor, ctx, subjects, **_kw):
            self.manual_calls.append((ctx['user_id'], ctx['capabilities'], subjects))
            return set(self.manual_allowed)

        def exact(_cursor, row):
            self.exact_calls.append(row['user_id'])
            return dict(row['role_capabilities'], can_delete=True)

        patches = [
            (aa, 'section_chains',
             lambda _c, ids: {i: self.chains[i] for i in ids if i in self.chains}),
            (aa, '_spaces', lambda _c, ids: {
                i: {'id': i, 'name': self.space_names.get(i, 'Пространство %s' % i),
                    'icon': None} for i in ids}),
            (aa, '_space_departments', lambda _c, ids: {
                i: set(self.space_departments[i]) for i in ids if i in self.space_departments}),
            (aa, '_public_departments', public),
            (aa, '_guest_grants', guests),
            (structure, 'list_section_rules', rules),
            (queries, 'manage_section_ids', branches),
            (queries, 'allowed_section_ids', allowed),
            (wiki_edit, 'list_article_rules', lambda _c, _id: list(self.article_rules)),
            (readers, 'load_people', lambda _c: list(self.people)),
            (readers, 'matched_rules', matched),
            (readers, 'exact_capabilities', exact),
        ]
        for module, name, replacement in patches:
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, replacement)

    def describe(self, *, allowed=(4, 31), caps=None, article=None, people=True):
        ctx = {'capabilities': dict(caps or READER_CAPS), 'user_id': 42,
               'subjects': {'user': [42]}}
        # Кому отдать список людей, решает дверь (aa.sees_readers); сборка
        # проверяется здесь для любого смотрящего.
        return aa.describe(None, ctx, dict(article or ARTICLE), set(allowed),
                           with_people=people)

    def names(self, got):
        return [p['name'] for p in got['people']]

    # ── места ────────────────────────────────────────────────────────────

    def test_both_places_are_drawn_from_the_root_down(self):
        got = self.describe()
        self.assertEqual([p['section_id'] for p in got['places']], [31, 4])
        op, szov = got['places']
        self.assertEqual([n['name'] for n in szov['path']],
                         ['Коммерческий директор', 'СЗоВ', 'Руководитель группы',
                          'Супервайзер', 'Оператор'])
        self.assertEqual([n['branch'] for n in szov['path']],
                         [False, True, False, False, False])
        # Место названо разделом, в котором лежит статья, а не корнем дерева.
        self.assertEqual((szov['name'], op['name']), ('Оператор', 'Оператор'))
        self.assertEqual(szov['space'], {'id': 1, 'name': 'Таксопарки', 'icon': None})
        self.assertFalse(szov['archived'])
        self.assertEqual(sorted(szov), ['archived', 'name', 'path', 'section_id', 'space'])
        self.assertEqual(got['hidden_places'], 0)

    def test_section_outside_the_viewer_perimeter_is_only_counted(self):
        """Супервайзеру СЗоВ ветку ОП не называем: ни имени, ни отдела, ни людей."""
        got = self.describe(allowed=(4,))
        self.assertEqual([p['section_id'] for p in got['places']], [4])
        self.assertEqual(got['hidden_places'], 1)
        dump = json.dumps(got, ensure_ascii=False)
        self.assertNotIn('Отдел продаж', dump)
        self.assertNotIn('Оператор ОП', dump)
        self.assertEqual(self.names(got), ['Оператор СЗоВ', 'Супервайзер СЗоВ'])

    def test_article_seen_only_as_an_author_names_no_section(self):
        got = self.describe(allowed=())
        self.assertEqual(got['places'], [])
        self.assertEqual(got['hidden_places'], 2)
        self.assertEqual(got['people'], [])

    def test_section_that_vanished_is_counted_as_hidden(self):
        """Раздел, которого в дереве уже нет, названным быть не может — но и
        пропасть из счёта не должен: «лежит ещё где-то» остаётся правдой."""
        got = self.describe(article=dict(ARTICLE, section_ids=[4, 31, 99]))
        self.assertEqual((len(got['places']), got['hidden_places']), (2, 1))

    def test_archived_section_is_named_only_to_those_who_see_the_archive(self):
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        hidden = self.describe()
        self.assertEqual([p['section_id'] for p in hidden['places']], [31])
        self.assertEqual(hidden['hidden_places'], 1)

        shown = self.describe(caps=ADMIN_CAPS)
        found = next(p for p in shown['places'] if p['section_id'] == 4)
        self.assertTrue(found['archived'])
        self.assertEqual(shown['hidden_places'], 0)

    def test_either_management_capability_shows_the_archive(self):
        """«Структура» отдаёт архив и управляющему структурой, и управляющему
        доступами — справка обязана решать так же, и без лишнего запроса."""
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        for caps in (STRUCTURE_CAPS, ACCESS_CAPS):
            got = self.describe(caps=caps)
            self.assertEqual(sorted(p['section_id'] for p in got['places']), [4, 31], caps)
        self.assertEqual(self.branch_calls, [])

    def test_branch_holder_sees_the_archive_of_his_own_subsections(self):
        """Решение владельца 07.09.2026: кто заводит подразделы, тот их и убирает
        — и «Структура» показывает ему архив своей ветки. Окно статьи, спрятав
        такой раздел в «ещё один», спорило бы со вкладкой, где он его возвращает.
        Свой подраздел — тот, чей РОДИТЕЛЬ лежит в выданной ветке."""
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        self.branches = frozenset({3})                     # родитель раздела 4
        got = self.describe()
        self.assertEqual(sorted(p['section_id'] for p in got['places']), [4, 31])
        self.assertEqual(got['hidden_places'], 0)
        # Спрашиваем про смотрящего и его же субъектов — не про кого-то ещё.
        self.assertEqual(self.branch_calls, [(42, {'user': [42]})])

    def test_holder_of_another_branch_does_not_see_the_archive(self):
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        # Чужая ветка — и сам архивный раздел: якорь ветки человек не убирает,
        # решает родитель, а не раздел.
        for branches in ({30}, {4}, set()):
            self.branches = frozenset(branches)
            got = self.describe()
            self.assertEqual([p['section_id'] for p in got['places']], [31], branches)
            self.assertEqual(got['hidden_places'], 1, branches)

    def test_archived_root_section_has_no_branch_above_it(self):
        """У раздела в корне пространства родителя нет — держателю ветки он не
        «свой подраздел», и спрашивать о ветках незачем."""
        self.chains[4] = [dict(OPERATOR, status='archived')]
        self.branches = frozenset({4, 3})
        got = self.describe()
        self.assertEqual([p['section_id'] for p in got['places']], [31])
        self.assertEqual(self.branch_calls, [])

    def test_branches_are_not_asked_without_an_archived_place(self):
        self.describe()
        self.assertEqual(self.branch_calls, [])

    def test_branches_are_asked_once_for_several_archived_places(self):
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        self.chains[31] = [dict(OP_CHAIN[0], status='archived')] + OP_CHAIN[1:]
        self.branches = frozenset({3})
        got = self.describe()
        self.assertEqual([p['section_id'] for p in got['places']], [4])
        self.assertEqual(len(self.branch_calls), 1)

    def test_archived_section_is_not_named_by_the_perimeter_alone(self):
        """Архивного раздела в периметре не бывает, но проверка обязана стоять
        на статусе, а не на счастливом совпадении множеств."""
        self.chains[4] = [dict(OPERATOR, status='archived')] + CHAIN[1:]
        got = self.describe(allowed=(4, 31), caps=READER_CAPS)
        self.assertEqual([p['section_id'] for p in got['places']], [31])

    def test_every_department_section_of_the_path_is_marked_as_a_branch(self):
        self.chains[4] = [OPERATOR, node(19, 'СЗоВ', dept=1), node(1, 'Коммерческий блок', dept=9)]
        (found,) = self.describe(allowed=(4,), article=dict(ARTICLE, section_ids=[4]))['places']
        self.assertEqual([(n['name'], n['branch']) for n in found['path']],
                         [('Коммерческий блок', True), ('СЗоВ', True), ('Оператор', False)])

    def test_places_of_one_space_stand_together(self):
        """Сперва пространство, потом путь: иначе места двух пространств шли бы
        вперемешку по алфавиту разделов."""
        self.chains[31] = [dict(n, space_id=2) for n in OP_CHAIN]
        self.chains[77] = [node(77, 'Архив акций')]
        got = self.describe(allowed=(4, 31, 77),
                            article=dict(ARTICLE, section_ids=[31, 77, 4]))
        self.assertEqual([(p['space']['id'], p['section_id']) for p in got['places']],
                         [(1, 77), (1, 4), (2, 31)])

    def test_places_come_in_the_same_order_every_time(self):
        """array_agg порядка не обещает — место не должно прыгать между открытиями."""
        first = [p['section_id'] for p in self.describe()['places']]
        second = [p['section_id'] for p in self.describe(
            article=dict(ARTICLE, section_ids=[31, 4]))['places']]
        self.assertEqual(first, second)

    def test_duplicate_section_ids_do_not_duplicate_places(self):
        got = self.describe(article=dict(ARTICLE, section_ids=[4, 4, 31]))
        self.assertEqual(len(got['places']), 2)

    def test_article_card_rides_into_the_answer(self):
        """По статусу окно оговаривает черновик и архив: потеряйся он по дороге,
        список под неопубликованной статьёй читался бы как «эти люди её уже
        видят»."""
        for status in ('draft', 'on_approval', 'archived', 'published'):
            card = self.describe(article=dict(ARTICLE, status=status))['article']
            self.assertEqual((card['id'], card['title'], card['status']),
                             (500, 'Работа с разделом Акции', status))

    def test_restricted_and_strict_articles_are_by_list_only(self):
        for patch in ({'visibility_mode': 'restricted'}, {'strict_mode': True}):
            got = self.describe(article=dict(ARTICLE, **patch))
            self.assertTrue(got['article']['by_list_only'], patch)
        self.assertFalse(self.describe()['article']['by_list_only'])

    # ── люди ─────────────────────────────────────────────────────────────

    def test_people_are_listed_with_their_rights(self):
        got = self.describe()
        self.assertEqual(got['people'], [
            {'name': 'Оператор ОП', 'role': 'operator', 'job_title': None,
             'department': 'Отдел 2', 'rights': 'r', 'via': []},
            {'name': 'Оператор СЗоВ', 'role': 'operator', 'job_title': None,
             'department': 'Отдел 1', 'rights': 'r', 'via': []},
            {'name': 'Супервайзер СЗоВ', 'role': 'sv', 'job_title': None,
             'department': 'Отдел 1', 'rights': 'rce', 'via': []},
        ])

    def test_people_carry_no_numbers(self):
        """В список идёт то, что рисует окно. Номера людей ему не нужны —
        значит, и ехать им незачем."""
        self.assertNotIn('user_id', json.dumps(self.describe()))

    def test_names_go_in_order_whatever_the_case_and_the_yo(self):
        nameless = person(14)
        nameless['name'] = None
        self.people = [person(10, name='ёлкин'), person(11, name='Яковлев'),
                       person(12, name='Егоров'), person(13, name='абаев'), nameless]
        self.section_hits = {1: {10, 11, 12, 13, 14}}
        self.assertEqual(self.names(self.describe()),
                         [None, 'абаев', 'Егоров', 'ёлкин', 'Яковлев'])

    def test_rights_are_spelled_in_the_order_of_the_ladder(self):
        self.rules = [rule(1, 4, can_delete=True, can_approve=True, can_publish=True,
                           can_edit=True, can_create=True)]
        self.people = [person(10, 'admin')]
        self.section_hits = {1: {10}}
        self.assertEqual(self.describe()['people'][0]['rights'], 'rcepad')

    def test_rules_of_every_place_are_asked_even_of_a_hidden_one(self):
        """Права человека считаются по всем разделам статьи: спросив правила
        только названных, окно занизило бы права тому, кто в списке."""
        self.describe(allowed=(4,))
        self.assertEqual(self.rule_calls, [[1, 2, 3, 4, 19, 28, 30, 31]])
        (_people, section_rules, _article_rules), = self.match_calls
        self.assertEqual(section_rules, [1, 2, 3])

    def test_rule_of_a_neighbour_branch_is_not_matched(self):
        """Спрашиваются правила всей цепочки, а действуют только дошедшие:
        правило раздела выше без «вместе с подразделами» в расчёт не идёт."""
        self.rules.append(rule(4, 2, deep=False))
        self.describe()
        self.assertEqual(self.match_calls[0][1], [1, 2, 3])

    def test_everyone_is_matched_in_one_go(self):
        self.article_rules = [article_rule(7, 'user', 20)]
        self.describe()
        self.assertEqual(self.match_calls, [([10, 11, 20], [1, 2, 3], [7])])

    def test_guests_are_looked_up_along_every_branch(self):
        """Раздел открывает и выдача на раздел выше, и выдача пространства:
        спросить обязаны о всех разделах пути и о пространстве."""
        self.describe(allowed=(4,))
        self.assertEqual(self.guest_calls, [(500, [1, 2, 3, 4, 19, 28, 30, 31], [1])])

    def test_guests_of_a_section_and_of_the_article_are_listed(self):
        self.people += [person(40, dept=4, guest=True, name='Гость раздела'),
                        person(41, dept=4, guest=True, name='Гость статьи'),
                        person(42, dept=4, guest=True, name='Гость чужой ветки')]
        self.grants = [grant(40, section=3, deep=True), grant(41, article=500),
                       grant(42, section=31)]
        got = self.describe(allowed=(4,))
        self.assertEqual(self.names(got), ['Гость раздела', 'Гость статьи',
                                           'Оператор СЗоВ', 'Супервайзер СЗоВ'])
        self.assertEqual(got['people'][0]['via'], [{'kind': 'guest', 'label': None}])

    def test_public_departments_are_asked_only_for_living_public_sections(self):
        self.chains[4] = [dict(OPERATOR, visibility_scope='public')] + CHAIN[1:]
        self.chains[31] = [dict(OP_CHAIN[0], status='archived',
                                visibility_scope='public')] + OP_CHAIN[1:]
        self.public = {4: {2}}
        got = self.describe(allowed=(4,))
        self.assertEqual(self.public_calls, [[4]])
        # Публичен для отдела 2 — оператор ОП читает, хотя правил на него нет.
        self.assertIn('Оператор ОП', self.names(got))

    def test_public_section_without_a_list_is_open_to_the_whole_space(self):
        self.chains[4] = [dict(OPERATOR, visibility_scope='public')] + CHAIN[1:]
        self.people.append(person(40, dept=4, name='Оператор Тез'))
        self.section_hits = {}
        got = self.describe(allowed=(4,), article=dict(ARTICLE, section_ids=[4]))
        self.assertEqual(self.names(got), ['Оператор ОП', 'Оператор СЗоВ', 'Супервайзер СЗоВ'])

    def test_manual_reader_goes_through_the_real_perimeter(self):
        """Ручной режим живёт по своим правилам — их считает настоящий
        queries.allowed_section_ids, по одному запросу на такого человека."""
        manual = person(60, mode='manual', name='Ручной режим')
        self.people.append(manual)
        self.manual_allowed = {4}
        got = self.describe()
        self.assertIn('Ручной режим', self.names(got))
        self.assertEqual(self.manual_calls,
                         [(60, manual['role_capabilities'], manual['subjects'])])

    def test_ordinary_people_cost_no_extra_queries(self):
        self.describe()
        self.assertEqual((self.manual_calls, self.exact_calls), ([], []))

    def test_master_key_holder_with_partial_rights_is_recalculated(self):
        """Способность могла поднять выдача правилом в любом разделе вики —
        такого носителя мастер-ключа считает настоящий load_capabilities. У
        кого должность и так даёт все шесть прав, пересчитывать нечего."""
        limited = dict(WIKI_ADMIN, can_delete=False)
        self.people += [person(2, dept=2, wiki_roles=[limited], name='Админ без удаления'),
                        person(3, dept=2, wiki_roles=[WIKI_ADMIN], name='Админ вики'),
                        person(1, 'super_admin', name='Супер-админ'),
                        person(60, mode='manual', wiki_roles=[limited], name='Админ вручную')]
        got = self.describe()
        self.assertEqual(sorted(self.exact_calls), [2, 60])
        self.assertEqual(self.manual_calls, [], 'мастер-ключ сильнее ручного режима')
        by_name = {p['name']: p['rights'] for p in got['people']}
        self.assertEqual(by_name['Админ без удаления'], 'rcepad')

    def test_article_rules_reach_the_calculation(self):
        self.article_rules = [article_rule(7, 'user', 20, can_edit=True)]
        self.article_hits = {7: {20}}
        got = self.describe(allowed=(4,))
        row = next(p for p in got['people'] if p['name'] == 'Оператор ОП')
        self.assertEqual((row['rights'], row['via']),
                         ('re', [{'kind': 'article_rule', 'label': None}]))

    def test_article_without_sections_lists_those_it_is_given_to(self):
        self.article_rules = [article_rule(7, 'user', 20)]
        self.article_hits = {7: {20}}
        got = self.describe(article=dict(ARTICLE, visibility_mode='restricted',
                                         section_ids=[]))
        self.assertEqual((got['places'], got['hidden_places']), ([], 0))
        self.assertEqual(self.names(got), ['Оператор ОП'])
        self.assertEqual(self.rule_calls, [], 'разделов нет — и правил спрашивать не у кого')


class WhoSeesThePeopleTests(unittest.TestCase):
    """Список людей — только супер-админу; остальным он не считается вовсе."""

    def ctx(self, role, wiki_roles=()):
        return {'otp_role': role, 'wiki_roles': list(wiki_roles),
                'capabilities': wiki_access.resolve_capabilities(role, list(wiki_roles))}

    def test_only_the_super_admin_role_sees_the_people(self):
        self.assertTrue(aa.sees_readers(self.ctx('super_admin')))
        for role in ('admin', 'sv', 'trainer', 'operator', None):
            self.assertFalse(aa.sees_readers(self.ctx(role)), role)

    def test_it_is_the_role_not_the_capability(self):
        """Администратор вики несёт все девять способностей — и списка не
        получает; супер-админ с ролью читателя их лишён — и получает."""
        self.assertFalse(aa.sees_readers(self.ctx('operator', [WIKI_ADMIN])))
        self.assertTrue(aa.sees_readers(self.ctx('super_admin', [WIKI_READER])))

    def test_without_the_people_nothing_about_them_is_asked_or_sent(self):
        """Не показанное не должно ни считаться, ни ехать по сети: пустой
        список в ответе читался бы как «статья не открыта никому»."""
        def boom(*_a, **_k):
            raise AssertionError('список людей считать не просили')

        patches = [
            (aa, 'section_chains', lambda _c, ids: {4: CHAIN}),
            (aa, '_spaces', lambda _c, ids: {1: {'id': 1, 'name': 'Таксопарки', 'icon': None}}),
            (aa, 'who_reads', boom),
            (readers, 'load_people', boom),
        ]
        for module, name, replacement in patches:
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, replacement)
        ctx = {'user_id': 42, 'capabilities': READER_CAPS, 'subjects': {}}
        got = aa.describe(None, ctx, dict(ARTICLE, section_ids=[4]), {4}, with_people=False)
        self.assertIsNone(got['people'])
        self.assertEqual([p['section_id'] for p in got['places']], [4])
        self.assertEqual(got['hidden_places'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# Дверь
# ─────────────────────────────────────────────────────────────────────────────

READ_RULE = {'can_read': True, 'can_create': False, 'can_edit': False,
             'can_delete': False, 'can_publish': False, 'can_approve': False}
EDIT_RULE = dict(READ_RULE, can_create=True, can_edit=True)


@unittest.skipIf(Flask is None, 'flask не установлен')
class RouteGateTests(unittest.TestCase):

    def build(self, *, role='sv', user_id=42, rules=(EDIT_RULE,), visible=(500,),
              article=None, granted=None, rules_by_section=None, wiki_roles=()):
        self.described = []
        self.written = []
        article = dict(article or ARTICLE)

        context = {
            'user_id': user_id, 'otp_role': role, 'department_id': 1,
            'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
            'wiki_roles': [dict(r) for r in wiki_roles], 'access_mode': 'auto',
        }
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _describe(_cursor, ctx, row, sections, *, with_people):
            self.described.append((row['id'], set(sections), 'content' in row))
            self.with_people = with_people
            self.context_keys = set(ctx)
            return {'places': [], 'hidden_places': 0}

        def _boom(*_a, **_k):
            raise AssertionError('справка не должна тянуть тело статьи')

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'granted_rule_rights',
             lambda _c, _s, _u: (dict(granted or {}), [])),
            (queries, 'allowed_section_ids', lambda _c, _ctx, _s, **k: {4}),
            (queries, 'section_rules_for_user',
             lambda _c, ids, _s, _u: {
                 sid: list(rules if rules_by_section is None
                           else rules_by_section.get(sid, ()))
                 for sid in (ids or ())}),
            (queries, 'log_action', lambda _c, **kw: self.written.append(kw)),
            (queries, 'spaces_for_user', lambda *a, **k: [1]),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: set(visible)),
            (wiki_articles, 'list_articles',
             lambda _c, ids, **k: [dict(article)] if article['id'] in ids else []),
            (wiki_articles, 'get_article', _boom),
            (wiki_articles, 'register_view',
             lambda *a, **k: self.written.append({'action': 'view'})),
            (wiki_articles, 'article_rules_for_user', lambda *a, **k: {}),
            (aa, 'describe', _describe),
        ]
        for module, name, replacement in patches:
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, replacement)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
        ))
        app.config['TESTING'] = True
        return app.test_client()

    URL = '/api/wiki/articles/500/access'

    def test_editor_gets_the_answer(self):
        response = self.build().get(self.URL)
        self.assertEqual(response.status_code, 200, response.get_json())
        # Периметр смотрящего уходит в справку: по нему она решает, что назвать.
        self.assertEqual(self.described, [(500, {4}, False)])
        # ...а по субъектам и способностям — чей архив и чьи правила показать.
        self.assertLessEqual({'subjects', 'user_id', 'capabilities'}, self.context_keys)

    def test_editor_gets_the_places_but_not_the_people(self):
        """«Кому открыт — у суперадмина, а где находится сама статья —
        редакторам и выше»: редактору дверь открыта, но список людей сборке
        не заказан."""
        for role in ('sv', 'trainer', 'admin'):
            self.assertEqual(self.build(role=role).get(self.URL).status_code, 200, role)
            self.assertIs(self.with_people, False, role)

    def test_super_admin_gets_the_people(self):
        self.assertEqual(self.build(role='super_admin').get(self.URL).status_code, 200)
        self.assertIs(self.with_people, True)

    def test_wiki_admin_is_not_a_super_admin(self):
        """Список — по РОЛИ, а не по способности: администратор вики с ролью,
        назначенной руками, правит статью и видит, где она лежит, — и только."""
        client = self.build(role='operator', wiki_roles=[WIKI_ADMIN])
        self.assertEqual(client.get(self.URL).status_code, 200)
        self.assertIs(self.with_people, False)

    def test_super_admin_gets_in_even_without_the_edit_right(self):
        """Роль вики, назначенная руками, заменяет должностные способности — и
        у супер-админа с ролью читателя права правки может не быть. «Выше
        редактора» он от этого быть не перестаёт: опция владельцем выдана ему."""
        client = self.build(role='super_admin', rules=(), wiki_roles=[WIKI_READER])
        self.assertEqual(client.get(self.URL).status_code, 200)
        self.assertIs(self.with_people, True)
        # Невидимая статья и ему отвечает «не найдена».
        client = self.build(role='super_admin', visible=())
        self.assertEqual(client.get(self.URL).status_code, 404)

    def test_reader_is_refused(self):
        """Читает — не значит правит: оператору с правилом «Чтение» дверь закрыта."""
        response = self.build(role='operator', rules=(READ_RULE,)).get(self.URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_FORBIDDEN')
        self.assertEqual(self.described, [])

    def test_rule_without_the_capability_is_not_enough(self):
        """Право правки требует и правила, и способности — как на самой правке."""
        response = self.build(role='operator', rules=(EDIT_RULE,)).get(self.URL)
        self.assertEqual(response.status_code, 403)

    def test_operator_with_an_edit_rule_gets_in(self):
        """Выписанное правилом право поднимает способность (21.08.2026) — и
        дверь обязана считать его так же, как считает правку."""
        client = self.build(role='operator', rules=(EDIT_RULE,),
                            granted={'can_read': True, 'can_edit': True})
        self.assertEqual(client.get(self.URL).status_code, 200)

    def test_trainer_who_edits_gets_in_without_the_grant_ladder(self):
        """Тренер доступ не раздаёт (GRANT_CEILING), но кто прочитает его
        текст, знать вправе: гейт — правка статьи, а не лестница выдачи."""
        self.assertEqual(self.build(role='trainer').get(self.URL).status_code, 200)

    def test_author_gets_in_without_any_rule(self):
        client = self.build(role='operator', user_id=99, rules=())
        self.assertEqual(client.get(self.URL).status_code, 200)

    def test_invisible_article_is_not_found(self):
        """404, а не 403: отказ подтвердил бы, что статья с таким номером есть."""
        response = self.build(visible=()).get(self.URL)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.described, [])

    def test_vanished_article_is_not_found(self):
        response = self.build(visible=(500, 501)).get('/api/wiki/articles/501/access')
        self.assertEqual(response.status_code, 404)

    def test_reader_learns_nothing_about_an_invisible_article(self):
        """Видимость проверяется РАНЬШЕ права: читателю на невидимую статью
        обязан прийти тот же 404, что и на несуществующую. Поменяй проверки
        местами — и 403 подтвердит, что статья с таким номером есть."""
        client = self.build(role='operator', rules=(READ_RULE,), visible=())
        response = client.get(self.URL)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {'error': 'Статья не найдена'})
        self.assertEqual(self.described, [])

    def test_edit_right_counts_only_inside_the_viewer_perimeter(self):
        """Статья лежит в 4 и в 31, смотрящий видит только 4. Правило правки
        на разделе 31 — не его: на странице у него кнопки «Править» нет, и
        дверь обязана считать так же, по периметру, а не по всем разделам
        статьи."""
        client = self.build(rules_by_section={4: [READ_RULE], 31: [EDIT_RULE]})
        self.assertEqual(client.get(self.URL).status_code, 403)
        self.assertEqual(self.described, [])
        # То же правило на своём разделе дверь открывает.
        client = self.build(rules_by_section={4: [EDIT_RULE], 31: [READ_RULE]})
        self.assertEqual(client.get(self.URL).status_code, 200)

    def test_door_is_read_only(self):
        client = self.build()
        for method in ('post', 'patch', 'put', 'delete'):
            self.assertEqual(getattr(client, method)(self.URL, json={}).status_code, 405,
                             method)
        client.get(self.URL)
        self.assertEqual(self.written, [],
                         'справка не пишет ни журнал, ни просмотры статьи')

    def test_preflight_needs_no_rights(self):
        self.assertEqual(self.build(visible=()).options(self.URL).status_code, 204)


# ─────────────────────────────────────────────────────────────────────────────
# Запросы без базы
#
# Тесты ниже по файлу исполняют боевой SQL, но только там, где задана база
# (DATABASE_URL_READONLY): на сборочном сервере её нет, и они пропускаются. Здесь
# — то из запросов, что можно удержать и без неё: какие параметры в них уходят
# и какие условия в них стоят.
# ─────────────────────────────────────────────────────────────────────────────

class RecordingCursor(object):
    """Курсор, который ничего не исполняет и помнит, о чём его просили.

    rows — ответ на каждый запрос; список списков отвечает по очереди.
    """

    def __init__(self, rows=(), *more):
        self.calls = []
        self._answers = [list(rows)] + [list(extra) for extra in more]

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def _answer(self):
        index = min(len(self.calls), len(self._answers)) - 1
        return self._answers[max(index, 0)]

    def fetchall(self):
        return list(self._answer())

    def fetchone(self):
        rows = self._answer()
        return rows[0] if rows else None


class QueryShapeTests(unittest.TestCase):

    def test_section_list_narrows_the_rules(self):
        cursor = RecordingCursor()
        structure.list_section_rules(cursor, section_ids=[12, '13'])
        sql, params = cursor.calls[0]
        self.assertEqual(params['sections'], [12, 13])
        # Оба фильтра — через И: список разделов сужает, а не расширяет.
        self.assertIn(
            'WHERE (%(section)s::int IS NULL OR r.section_id = %(section)s::int) '
            'AND (%(sections)s::int[] IS NULL OR r.section_id = ANY(%(sections)s::int[]))',
            ' '.join(sql.split()))

    def test_empty_section_list_stays_empty(self):
        """Пустой список — «ни одного раздела», а не «без фильтра»: иначе в
        справку статьи без разделов приехали бы правила всей вики."""
        cursor = RecordingCursor()
        structure.list_section_rules(cursor, section_ids=[])
        self.assertEqual(cursor.calls[0][1]['sections'], [])

    def test_without_a_section_list_nothing_is_narrowed(self):
        cursor = RecordingCursor()
        structure.list_section_rules(cursor, section_id=13)
        sql, params = cursor.calls[0]
        self.assertIsNone(params['sections'])
        self.assertEqual(params['section'], 13)
        self.assertIn('%(sections)s::int[] IS NULL OR', sql)

    def test_guest_query_takes_only_living_grants_of_this_article(self):
        cursor = RecordingCursor()
        aa._guest_grants(cursor, 500, {13, 12}, {1})
        sql, params = cursor.calls[0]
        self.assertEqual(params, {'article': 500, 'sections': [12, 13], 'spaces': [1]})
        flat = ' '.join(sql.split())
        self.assertIn('g.revoked_at IS NULL', flat)
        self.assertIn('g.expires_at > ' + ' '.join(aa.NOW_SQL.split()), flat)
        # Выдача на ЭТУ статью, а не на любую: иначе в список встали бы гости
        # чужих статей.
        self.assertIn('g.article_id = %(article)s', flat)
        self.assertNotIn('g.article_id IS NOT NULL', flat)

    def test_guest_query_without_sections_matches_nothing_by_them(self):
        """Пустой массив в ANY — это «ничего», но параметром уходит заведомо
        непопадающее значение: так же делает периметр (queries: [-1])."""
        cursor = RecordingCursor()
        aa._guest_grants(cursor, 500, set(), set())
        self.assertEqual(cursor.calls[0][1],
                         {'article': 500, 'sections': [-1], 'spaces': [-1]})

    def test_chain_query_is_bounded_in_depth(self):
        """Петель сервер не допускает, но зациклиться здесь значит подвесить
        запрос — ограничитель обязан стоять в самом тексте."""
        cursor = RecordingCursor()
        aa.section_chains(cursor, [13, '13', 0, None, 12])
        sql, params = cursor.calls[0]
        self.assertEqual(params, {'sections': [12, 13]})
        self.assertIn('up.depth < 50', sql)

    def test_chain_is_put_in_order_whatever_the_base_returns(self):
        """«От раздела к корню» — на этом порядке стоит и докуда доходят
        правила, и путь на экране. Порядка строк ответа никто не обещал."""
        def row(root, depth, ident, name):
            return (root, depth, ident, name, 'active', 1, None, 'restricted', None)
        cursor = RecordingCursor([
            row(13, 2, 11, 'Руководитель группы'), row(12, 1, 11, 'Руководитель группы'),
            row(13, 0, 13, 'Оператор'), row(12, 0, 12, 'Супервайзер'),
            row(13, 1, 12, 'Супервайзер')])
        chains = aa.section_chains(cursor, [13, 12])
        self.assertEqual([n['id'] for n in chains[13]], [13, 12, 11])
        self.assertEqual([n['id'] for n in chains[12]], [12, 11])
        self.assertEqual(sorted(chains[13][0]), sorted(aa._CHAIN_KEYS))

    def test_spaces_are_named_for_the_tree(self):
        cursor = RecordingCursor([(1, 'Таксопарки', '🚕'), (3, 'Общее', None)])
        spaces = aa._spaces(cursor, {3, 1, None})
        self.assertEqual(cursor.calls[0][1], ([1, 3],))
        self.assertEqual(spaces, {1: {'id': 1, 'name': 'Таксопарки', 'icon': '🚕'},
                                  3: {'id': 3, 'name': 'Общее', 'icon': None}})

    def test_space_boundary_is_the_list_of_its_departments(self):
        """Пространства без строк в ответе нет — оно открыто всем отделам."""
        cursor = RecordingCursor([(1, 1), (1, 2), (2, 4)])
        self.assertEqual(aa._space_departments(cursor, {1, 2, 3}), {1: {1, 2}, 2: {4}})
        sql, params = cursor.calls[0]
        self.assertEqual(params, ([1, 2, 3],))
        self.assertIn('wiki_space_departments', sql)
        nothing = RecordingCursor()
        self.assertEqual(aa._space_departments(nothing, []), {})
        self.assertEqual(nothing.calls, [])

    def test_public_departments_are_grouped_by_section(self):
        cursor = RecordingCursor([(30, 1), (30, 2), (31, 3)])
        self.assertEqual(aa._public_departments(cursor, [31, 30, 30]), {30: {1, 2}, 31: {3}})
        self.assertEqual(cursor.calls[0][1], ([30, 31],))
        nothing = RecordingCursor()
        self.assertEqual(aa._public_departments(nothing, []), {})
        self.assertEqual(nothing.calls, [])

    # ── люди и совпадения правил ─────────────────────────────────────────

    def test_matching_is_the_very_text_the_perimeter_uses(self):
        """Условие «правило ↔ человек» одно на весь раздел (queries.SUBJECT_MATCH).
        Для набора людей оно то же самое, только параметры одного человека
        заменены колонками набора — и ни один параметр не должен остаться
        неподставленным."""
        wanted = set(re.findall(r'%\((\w+)\)s', queries.SUBJECT_MATCH))
        columns = {name for name, _kind in readers._SUBJECT_COLUMNS}
        self.assertEqual(wanted, columns)
        row = person(10)
        self.assertEqual(set(queries.subject_params(row['subjects'], 10)), columns)

        text = readers.subject_match_for_people()
        self.assertNotIn('%(', text)
        expected = queries.SUBJECT_MATCH
        for name in columns:
            expected = expected.replace('%%(%s)s' % name, 'p.%s' % name)
        self.assertEqual(text, expected)

    def test_rules_are_matched_against_everyone_at_once(self):
        people = [person(10), person(20, 'sv', dept=2, groups=[5])]
        cursor = RecordingCursor([(1, 10), (1, 20), (3, 20)], [(7, 10)])
        section_hits, article_hits = readers.matched_rules(
            cursor, people, section_rule_ids=[3, 1, 1], article_rule_ids=[7])
        self.assertEqual(section_hits, {1: {10, 20}, 3: {20}})
        self.assertEqual(article_hits, {7: {10}})

        (section_sql, section_params), (article_sql, article_params) = cursor.calls
        self.assertEqual(section_params['rules'], [1, 3])
        self.assertEqual(article_params['rules'], [7])
        self.assertIn('FROM wiki_section_access_rules r', section_sql)
        self.assertIn('FROM wiki_article_access_rules r', article_sql)
        for sql in (section_sql, article_sql):
            self.assertIn(readers.subject_match_for_people(), sql)
            self.assertIn('jsonb_to_recordset(%(subjects)s::jsonb)', sql)
        # Субъекты — те же, что периметр подставляет параметрами.
        self.assertEqual(json.loads(section_params['subjects']),
                         [queries.subject_params(p['subjects'], p['user_id']) for p in people])

    def test_nothing_to_match_costs_no_query(self):
        cursor = RecordingCursor()
        self.assertEqual(readers.matched_rules(cursor, [person(10)]), ({}, {}))
        self.assertEqual(readers.matched_rules(cursor, [], section_rule_ids=[1]), ({}, {}))
        self.assertEqual(cursor.calls, [])

    def test_people_query_takes_everyone_who_can_sign_in(self):
        """Отсев по статусу — как у входа в портал: уволенным дверь закрыта, а
        отпуск и больничный читать не мешают."""
        self.assertIn("WHERE COALESCE(u.status, '') NOT IN ('fired', 'dismissal')",
                      ' '.join(readers._PEOPLE_SQL.split()))

    def test_person_row_is_what_the_perimeter_calls_a_context(self):
        row = (7, 'Видеограф Вера', 'marketing_manager', 3, 'Маркетинг', None, 'Видеограф',
               None, [3], [5, 6], [dict(WIKI_READER)], None, 1)
        (found,) = readers.load_people(RecordingCursor([row]))
        self.assertEqual({key: found[key] for key in readers._PEOPLE_KEYS}, {
            'user_id': 7, 'name': 'Видеограф Вера', 'otp_role': 'marketing_manager',
            'department_id': 3, 'department_name': 'Маркетинг', 'direction_id': None,
            'job_title': 'Видеограф', 'wiki_enabled': True, 'headed_department_ids': [3],
            'group_ids': [5, 6], 'wiki_roles': [WIKI_READER], 'access_mode': 'auto',
            'has_guest_access': True})
        self.assertEqual(found['subjects'], wiki_access.collect_subjects(
            user_id=7, otp_role='marketing_manager', department_id=3,
            headed_department_ids=[3], group_ids=[5, 6], wiki_role_ids=[8],
            job_title='Видеограф'))
        # Роли вики ЗАМЕНЯЮТ должностные способности — как в resolve_capabilities.
        self.assertEqual(found['role_capabilities'], wiki_access.resolve_capabilities(
            'marketing_manager', [WIKI_READER], is_department_head=True))


# ─────────────────────────────────────────────────────────────────────────────
# Настоящий SQL на синтетическом дереве
# ─────────────────────────────────────────────────────────────────────────────

class Raw(str):
    """Кусок SQL, который кладётся в VALUES как есть (например срок выдачи)."""


def _literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _table(name, columns, rows):
    """CTE, перекрывающий одноимённую таблицу. columns — «имя тип» через запятую.

    Типы проставляются ЯВНО: колонка из одних NULL в VALUES получает тип text,
    и без приведения боевой запрос падал бы на сравнении с числом.
    """
    pairs = [pair.split() for pair in columns.split(',')]
    names = ', '.join(col for col, _ in pairs)
    casts = ', '.join('%s::%s' % (col, kind) for col, kind in pairs)
    # VALUES пустым не бывает: пустая таблица — строка из NULL за WHERE FALSE.
    body = ', '.join('(' + ', '.join(_literal(v) for v in row) + ')' for row in rows) \
        or '(' + ', '.join('NULL' for _ in pairs) + ')'
    return '%s AS (SELECT %s FROM (VALUES %s) AS t(%s)%s)' % (
        name, casts, body, names, '' if rows else ' WHERE FALSE')


class StubCursor(object):
    """Курсор, дописывающий к каждому запросу заглушки таблиц.

    Так на синтетических строках исполняются НАСТОЯЩИЕ функции модуля и
    настоящий периметр — без копий запросов в тесте, которые разошлись бы с
    оригиналом.
    """

    def __init__(self, cursor, stubs):
        self._cursor = cursor
        self._stubs = ', '.join(stubs)
        self.queries = 0

    def execute(self, sql, params=None):
        text = sql.strip()
        upper = text.upper()
        # RECURSIVE допустим и там, где рекурсии нет, — зато подходит всем
        # боевым запросам сразу.
        if upper.startswith('WITH RECURSIVE'):
            text = 'WITH RECURSIVE ' + self._stubs + ', ' + text[len('WITH RECURSIVE'):]
        elif upper.startswith('WITH'):
            text = 'WITH RECURSIVE ' + self._stubs + ', ' + text[len('WITH'):]
        else:
            text = 'WITH RECURSIVE ' + self._stubs + ' ' + text
        self.queries += 1
        return self._cursor.execute(text, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


SOON = Raw("(now() + interval '2 days')")
GONE = Raw("(now() - interval '2 days')")
EARLIER = Raw('(CURRENT_DATE - 30)')
LATER = Raw('(CURRENT_DATE + 30)')
YESTERDAY = Raw('(CURRENT_DATE - 1)')

# ── Синтетическая компания ───────────────────────────────────────────────────
# id, имя, глава, жив ли отдел, выдана ли ему вики.
# 1 СЗоВ, 2 ОП, 3 Маркетинг (без линии), 4 Тез КЦ (чужая вика), 5 — отдел,
# которому вики не выдана, 6 — расформированный: глава у него числится, но
# возглавляемым он уже не считается.
DEPARTMENTS = [
    (1, 'СЗоВ', 103, True, True), (2, 'ОП', 113, True, True),
    (3, 'Маркетинг', 123, True, True), (4, 'Тез КЦ', None, True, True),
    (5, 'Без вики', None, True, False), (6, 'Расформированный', 113, False, True),
]
# id, имя, значок, статус, отдел ручного режима. Пространство 3 никому не выдано
# списком — то есть открыто всем отделам.
SPACES = [(1, 'Таксопарки', None, 'active', None), (2, 'Тез', None, 'active', 4),
          (3, 'Общее', None, 'active', None)]
SPACE_DEPARTMENTS = [(1, 1), (1, 2), (1, 3), (1, 5), (2, 4)]

# id, пространство, родитель, имя, статус, порядок, отдел, вид, публичность, владелец
SECTIONS = [
    (1, 1, None, 'Коммерческий директор', 'active', 0, None, 'common', 'restricted', None),
    (10, 1, 1, 'СЗоВ', 'active', 1, 1, 'department', 'restricted', None),
    (11, 1, 10, 'Руководитель группы', 'active', 2, None, 'common', 'restricted', None),
    (12, 1, 11, 'Супервайзер', 'active', 3, None, 'common', 'restricted', None),
    (13, 1, 12, 'Оператор', 'active', 4, None, 'common', 'restricted', None),
    (20, 1, 1, 'ОП', 'active', 5, 2, 'department', 'restricted', None),
    (21, 1, 20, 'Руководитель группы', 'active', 6, None, 'common', 'restricted', None),
    (22, 1, 21, 'Супервайзер', 'active', 7, None, 'common', 'restricted', None),
    (23, 1, 22, 'Оператор', 'active', 8, None, 'common', 'restricted', None),
    (30, 1, None, 'Общий сотрудник', 'active', 9, None, 'common', 'public', None),
    (31, 1, None, 'Объявления', 'active', 10, None, 'common', 'public', None),
    (40, 1, None, 'Маркетинг', 'active', 11, 3, 'department', 'restricted', None),
    (41, 1, 40, 'Видеограф', 'active', 12, None, 'common', 'restricted', None),
    (42, 1, 40, 'Старые материалы', 'archived', 13, None, 'common', 'restricted', None),
    (43, 1, 42, 'Под архивом', 'active', 14, None, 'common', 'restricted', None),
    (50, 1, None, 'Личный раздел', 'active', 15, None, 'common', 'restricted', 900),
    (60, 2, None, 'Тез: регламенты', 'active', 16, 4, 'department', 'restricted', None),
    # Пространство 3 — без границы: «всем» в нём значит всем отделам компании.
    (70, 3, None, 'Общие правила', 'active', 17, None, 'common', 'public', None),
    (71, 3, None, 'Закрытый угол', 'active', 18, None, 'common', 'restricted', None),
]
PUBLIC_DEPARTMENTS = [(30, 1), (30, 2)]           # «Объявления» (31) — всем

R, W, FULL = (True, False, False, False, False, False), \
    (True, True, True, False, False, False), (True, True, True, True, True, True)

# id, раздел, тип, id субъекта, роль, шесть прав, вглубь, порог, должность
RULES = [
    (1, 13, 'department', 1, None) + R + (False, None, None),
    (2, 13, 'department', 1, None) + R + (False, 20, None),
    (3, 12, 'department', 1, None) + W + (True, 30, None),       # спускается в 13
    (4, 11, 'department', 1, None) + FULL + (True, 40, None),    # спускается в 12 и 13
    (5, 1, 'user', 901, None) + R + (True, None, None),          # вся ветка директора
    (6, 10, 'group', 5, None) + R + (False, None, None),         # только сам раздел
    (7, 22, 'department', 2, None) + R + (False, 30, None),
    (8, 23, 'otp_role', None, 'operator') + R + (False, None, None),
    (9, 41, 'department', 3, None) + R + (False, 10, 'Видеограф'),
    (10, 40, 'department', 3, None) + R + (True, None, None),    # в 41 — да, в 43 — нет
    (11, 42, 'department', 3, None) + R + (True, None, None),    # правило архивного раздела
    (12, 20, 'department_head', 2, None) + R + (True, None, None),
    (13, 21, 'direction', 7, None) + R + (False, None, None),
    (14, 21, 'wiki_role', 3, None) + R + (False, None, None),
    (15, 60, 'department', 4, None) + R + (True, None, None),
    # Правило без чтения: в базе такое лежать может, раздел оно не открывает.
    (16, 50, 'department', 1, None) + (False, True, True, False, False, False)
    + (False, None, None),
    (17, 43, 'user', 902, None) + R + (False, None, None),
    # Доступ ТОЛЬКО сверху: своего правила на «Операторе» у человека нет.
    (18, 10, 'user', 903, None) + R + (True, None, None),
    # Пространство без границы: правило на отдел, которому не выдано ничего.
    (19, 71, 'department', 4, None) + R + (False, None, None),
    # Полный набор администратору без удаления — в ДРУГОМ разделе: способность
    # поднимается правилом откуда угодно.
    (20, 31, 'user', 108, None) + FULL + (False, None, None),
    # Группа есть, но она закрыта: её участники под правило не подпадают.
    (21, 13, 'group', 6, None) + FULL + (False, None, None),
    # Правка оператору лично — поверх чтения по отделу.
    (22, 13, 'user', 902, None) + W + (False, None, None),
    # Группа читает «Оператора» СЗоВ: кому-то это единственный путь.
    (23, 13, 'group', 5, None) + R + (False, None, None),
]

# id, человек, раздел, статья, пространство, с подразделами, отозвана, срок
GUESTS = [
    (1, 950, 12, None, None, True, None, SOON),      # 12 и 13
    (2, 951, None, None, 1, True, None, SOON),       # всё пространство
    (3, 952, 13, None, None, True, None, GONE),      # истекла
    (4, 953, 13, None, None, True, SOON, SOON),      # отозвана
    (5, 954, 11, None, None, False, None, SOON),     # без подразделов — только 11
    (6, 955, None, 500, None, True, None, SOON),     # на саму статью
    (7, 956, 42, None, None, True, None, SOON),      # на архивный раздел: 42 и 43
    (8, 957, None, 501, None, True, None, SOON),     # на ДРУГУЮ статью — не гость 500
    (9, 151, 13, None, None, False, None, SOON),     # гость из отдела без вики
    (10, 958, None, 502, None, True, None, SOON),    # на статью «по списку»
    (11, 959, None, 503, None, True, None, SOON),    # на строгую — не читает
]

# id, имя, роль, отдел, статус, должность, направление
USERS = [
    (100, 'Оператор СЗоВ', 'operator', 1, 'working', None, None),
    (101, 'Тренер СЗоВ', 'trainer', 1, 'working', None, None),
    (102, 'Супервайзер СЗоВ', 'sv', 1, 'working', None, None),
    (103, 'Руководитель СЗоВ', 'admin', 1, 'working', None, None),
    (104, 'Супер-админ', 'super_admin', 1, 'working', None, None),
    (105, 'Администратор вики', 'operator', 2, 'working', None, None),
    (106, 'Супер-админ с ролью читателя', 'super_admin', 1, 'working', None, None),
    (107, 'Администратор вики из Тез', 'operator', 4, 'working', None, None),
    (108, 'Администратор без удаления', 'operator', 2, 'working', None, None),
    # Роль вики заменила должностные способности: войти ему даёт уже не
    # «управление структурой», а сама роль супер-админа.
    (109, 'Супер-админ из отдела без вики', 'super_admin', 5, 'working', None, None),
    (110, 'Оператор ОП', 'operator', 2, 'working', None, None),
    (112, 'Супервайзер ОП', 'sv', 2, 'working', None, None),
    (113, 'Глава ОП', 'admin', 2, 'working', None, None),
    (120, 'Видеограф', 'marketing_manager', 3, 'working', 'Видеограф', None),
    (121, 'Таргетолог', 'marketing_manager', 3, 'working', 'Таргетолог', None),
    (123, 'Руководитель маркетинга', 'admin', 3, 'working', None, None),
    (130, 'Оператор Тез', 'operator', 4, 'working', None, None),
    (140, 'Без отдела', 'operator', None, 'working', None, None),
    (150, 'Из отдела без вики', 'operator', 5, 'working', None, None),
    (151, 'Гость из отдела без вики', 'operator', 5, 'working', None, None),
    (160, 'Ручной режим: раздел', 'operator', 1, 'working', None, None),
    (161, 'Ручной режим: отдел', 'operator', 4, 'working', None, None),
    (162, 'Ручной режим: супервайзер', 'sv', 1, 'working', None, None),
    (170, 'Уволенный', 'operator', 1, 'fired', None, None),
    (171, 'В отпуске', 'operator', 1, 'annual_leave', None, None),
    (172, 'Без статуса', 'operator', 1, None, None, None),
    (173, 'На увольнении', 'operator', 1, 'dismissal', None, None),
    (900, 'Владелец раздела', 'operator', 1, 'working', None, None),
    (901, 'Чужой с личным правилом', 'operator', 4, 'working', None, None),
    (902, 'Свой с личным правилом', 'operator', 1, 'working', None, None),
    (903, 'Сосед с правилом на ветку', 'operator', 2, 'working', None, None),
    (950, 'Гость раздела', 'operator', 4, 'working', None, None),
    (951, 'Гость пространства', 'operator', 4, 'working', None, None),
    (952, 'Гость с истёкшим сроком', 'operator', 4, 'working', None, None),
    (953, 'Гость с отозванной выдачей', 'operator', 4, 'working', None, None),
    (954, 'Гость без подразделов', 'operator', 4, 'working', None, None),
    (955, 'Гость статьи', 'operator', 4, 'working', None, None),
    (956, 'Гость архивного раздела', 'operator', 4, 'working', None, None),
    (957, 'Гость другой статьи', 'operator', 4, 'working', None, None),
    (958, 'Гость статьи по списку', 'operator', 4, 'working', None, None),
    (959, 'Гость строгой статьи', 'operator', 4, 'working', None, None),
    (960, 'Участник группы', 'operator', 2, 'working', None, None),
    (961, 'Человек направления', 'operator', 2, 'working', None, 7),
    (962, 'Носитель роли вики', 'operator', 2, 'working', None, None),
    (963, 'Супервайзер группы', 'sv', 3, 'working', None, None),
    (964, 'Вышел из группы', 'operator', 3, 'working', None, None),
    (965, 'В закрытой группе', 'operator', 2, 'working', None, None),
    (966, 'В группе с будущей даты', 'operator', 3, 'working', None, None),
    (967, 'Супервайзер группы с будущей даты', 'sv', 3, 'working', None, None),
    (968, 'Супервайзер, вышедший из группы', 'sv', 3, 'working', None, None),
    (969, 'Супервайзер закрытой группы', 'sv', 3, 'working', None, None),
]
SIGNED_IN = [u[0] for u in USERS if u[4] not in ('fired', 'dismissal')]

GROUPS = [(5, 'Основа', 'active'), (6, 'Закрытая', 'closed')]
# группа, человек, с какого дня, по какой
OPERATOR_MEMBERSHIPS = [(5, 960, EARLIER, None), (5, 964, EARLIER, YESTERDAY),
                        (6, 965, EARLIER, None), (5, 966, LATER, None)]
SUPERVISOR_MEMBERSHIPS = [(5, 963, EARLIER, LATER), (5, 967, LATER, None),
                          (5, 968, EARLIER, YESTERDAY), (6, 969, EARLIER, None)]

# id, код, имя, девять способностей: читать, создавать, править, удалять,
# публиковать, согласовывать, люди, структура, доступы
WIKI_ROLES = [
    (3, 'editor', 'Редактор', True, True, True, False, False, False, False, False, False),
    (8, 'reader', 'Читатель', True, False, False, False, False, False, False, False, False),
    (9, 'wiki_admin', 'Администратор вики') + (True,) * 9,
    (10, 'keeper', 'Администратор без удаления', True, True, True, False, True, True,
     True, True, True),
]
WIKI_USER_ROLES = [(962, 3), (105, 9), (106, 8), (107, 9), (108, 10), (109, 8)]
ACCESS_SETTINGS = [(160, 'manual'), (161, 'manual'), (162, 'manual')]
# человек, отдел, раздел
MANUAL_ACCESS = [(160, None, 12), (161, 4, None), (162, None, 13)]

# id, режим, статус, автор, владелец, строгий
ARTICLES = [
    (500, 'inherit', 'published', 140, None, False),
    (501, 'inherit', 'published', None, None, False),
    (502, 'restricted', 'published', 140, 121, False),
    (503, 'inherit', 'published', 101, 102, True),
    (504, 'restricted', 'published', None, None, True),
    (505, 'inherit', 'published', 100, None, False),
    (506, 'inherit', 'published', None, None, False),
    (507, 'inherit', 'published', None, None, False),
    (508, 'inherit', 'published', None, None, False),
    (510, 'inherit', 'published', None, None, False),
    (511, 'inherit', 'draft', 100, None, False),
    (512, 'inherit', 'published', None, None, False),
    (513, 'inherit', 'published', None, None, False),
]
ARTICLE_SECTIONS = {
    500: [13, 23, 41, 43, 42, 60], 501: [13], 502: [13], 503: [13], 504: [12],
    505: [], 506: [70, 60], 507: [42], 508: [50], 510: [43], 511: [13],
    # Публичные разделы: 30 открыт отделам 1 и 2, 31 — всем отделам пространства.
    512: [30], 513: [31],
}
# id, статья, тип, id субъекта, роль, режим, шесть прав
ARTICLE_RULES = [
    (1, 500, 'user', 100, None, 'deny') + R,
    (2, 500, 'otp_role', None, 'sv', 'grant') + W,
    (3, 502, 'department', 2, None, 'grant') + R,
    (4, 502, 'user', 130, None, 'grant') + R,          # Тез КЦ: режет граница
    (5, 502, 'user', 110, None, 'deny') + FULL,
    (6, 503, 'user', 100, None, 'grant') + W,
    (7, 504, 'otp_role', None, 'trainer', 'grant') + R,
    (8, 505, 'group', 5, None, 'grant') + R,
    (9, 501, 'user', 902, None, 'deny') + (False, False, True, False, False, False),
    # Выдача без чтения: в базе такая лежать может, статью она не открывает.
    (10, 508, 'user', 110, None, 'grant') + (False, False, True, False, False, False),
]


def article_row(article_id):
    """Статья — как её отдаёт wiki_articles.list_articles."""
    _id, mode, status, author, owner, strict = next(a for a in ARTICLES if a[0] == article_id)
    return {'id': article_id, 'title': 'Статья %d' % article_id, 'status': status,
            'visibility_mode': mode, 'strict_mode': strict, 'author_id': author,
            'owner_user_id': owner, 'section_ids': list(ARTICLE_SECTIONS[article_id])}


def _stubs(manage=()):
    """manage — id правил с тумблером «может заводить подразделы»."""
    return [
        _table('departments', 'id int, name text, head_user_id int, is_active boolean,'
                              ' wiki_enabled boolean', DEPARTMENTS),
        _table('directions', 'id int, name text', [(7, 'Регионы')]),
        _table('groups', 'id int, name text, status text', GROUPS),
        _table('group_operator_memberships',
               'group_id int, operator_id int, start_date date, end_date date',
               OPERATOR_MEMBERSHIPS),
        _table('group_supervisor_memberships',
               'group_id int, supervisor_id int, start_date date, end_date date',
               SUPERVISOR_MEMBERSHIPS),
        _table('wiki_roles',
               'id int, code text, name text, can_read boolean, can_create boolean,'
               ' can_edit boolean, can_delete boolean, can_publish boolean,'
               ' can_approve boolean, can_manage_users boolean,'
               ' can_manage_structure boolean, can_manage_access boolean', WIKI_ROLES),
        _table('wiki_user_roles', 'user_id int, wiki_role_id int', WIKI_USER_ROLES),
        _table('wiki_user_access_settings', 'user_id int, access_mode text', ACCESS_SETTINGS),
        _table('wiki_user_manual_access', 'user_id int, department_id int, section_id int',
               MANUAL_ACCESS),
        _table('users', 'id int, name text, role text, department_id int, status text,'
                        ' job_title text, direction_id int', USERS),
        _table('wiki_spaces', 'id int, name text, icon text, status text, department_id int',
               SPACES),
        _table('wiki_space_departments', 'space_id int, department_id int',
               SPACE_DEPARTMENTS),
        _table('wiki_sections',
               'id int, space_id int, parent_section_id int, name text, status text,'
               ' position int, department_id int, section_kind text,'
               ' visibility_scope text, owner_user_id int', SECTIONS),
        _table('wiki_section_public_departments', 'section_id int, department_id int',
               PUBLIC_DEPARTMENTS),
        _table('wiki_section_access_rules',
               'id int, section_id int, subject_type text, subject_id int,'
               ' subject_role text, can_read boolean, can_create boolean,'
               ' can_edit boolean, can_delete boolean, can_publish boolean,'
               ' can_approve boolean, grant_subsections boolean, min_role_level int,'
               ' job_title text, manage_subsections boolean',
               [row + (row[0] in manage,) for row in RULES]),
        _table('wiki_guest_access',
               'id int, user_id int, section_id int, article_id int, space_id int,'
               ' include_subsections boolean, revoked_at timestamp,'
               ' expires_at timestamp', GUESTS),
        _table('wiki_articles',
               'id int, visibility_mode text, status text, author_id int,'
               ' owner_user_id int, strict_mode boolean', ARTICLES),
        _table('wiki_article_sections', 'article_id int, section_id int',
               [(article, section) for article, sections in ARTICLE_SECTIONS.items()
                for section in sections]),
        _table('wiki_article_access_rules',
               'id int, article_id int, subject_type text, subject_id int,'
               ' subject_role text, mode text, can_read boolean, can_create boolean,'
               ' can_edit boolean, can_delete boolean, can_publish boolean,'
               ' can_approve boolean, min_role_level int, job_title text',
               [row if len(row) == 14 else row + (None, None) for row in ARTICLE_RULES]),
    ]


class _SqlHarness(object):

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def setUp(self):
        self._real = self.conn.cursor()
        self.cursor = StubCursor(self._real, _stubs())
        self.addCleanup(self._close)

    def _close(self):
        prod_db.rollback()
        self._real.close()

    def context(self, user_id, cursor=None):
        """Контекст человека — так, как его собирает сам портал на запрос."""
        cursor = cursor or self.cursor
        ctx = queries.load_access_context(cursor, user_id)
        ctx['subjects'] = wiki_access.collect_subjects(
            user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            department_id=ctx['department_id'],
            headed_department_ids=ctx['headed_department_ids'],
            direction_id=ctx['direction_id'], group_ids=ctx['group_ids'],
            wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
            job_title=ctx.get('job_title'))
        queries.load_capabilities(cursor, ctx, ctx['subjects'])
        return ctx


def _enters(ctx):
    """Гейт входа в раздел — как он написан в wiki/routes.py."""
    return ctx.get('wiki_enabled', True) or bool(
        wiki_access.normalize_role(ctx['otp_role']) == 'super_admin'
        or ctx['capabilities'].get('can_manage_structure')
        or ctx.get('has_guest_access'))


def _real_access(cursor, context):
    """{человек: (контекст, периметр, видимые статьи)} настоящим расчётом портала."""
    out = {}
    for user_id in SIGNED_IN:
        ctx = context(user_id, cursor)
        allowed = queries.allowed_section_ids(cursor, ctx, ctx['subjects'])
        visible = wiki_articles.visible_article_ids(cursor, ctx, ctx['subjects'], allowed)
        out[user_id] = (ctx, allowed, visible)
    return out


def _listed(cursor, article):
    chains = aa.section_chains(cursor, article['section_ids'])
    return {p['user_id']: p for p in aa.who_reads(cursor, article, chains, set(chains))}


def _disagreements(cursor, context):
    """Где список расходится с порталом: (расхождения, читающих пар, закрытых пар).

    Слева — то, чем портал отвечает человеку, когда тот открывает статью сам:
    контекст, способности, периметр разделов, видимость статьи, права на неё.
    Справа — список, посчитанный одним заходом на всех. Пара — человек и
    вышедшая статья.
    """
    real = _real_access(cursor, context)
    names = {u[0]: u[1] for u in USERS}
    wrong, reading, closed = [], 0, 0
    for article_id in [a[0] for a in ARTICLES if a[2] == 'published']:
        article = article_row(article_id)
        found = _listed(cursor, article)
        for user_id in sorted(set(found) - set(real)):
            wrong.append('статья %d: в списке тот, кому закрыт вход в портал (%s)' % (
                article_id, names[user_id]))
        for user_id, (ctx, allowed, visible) in real.items():
            reads = bool(_enters(ctx) and article_id in visible)
            if reads != (user_id in found):
                wrong.append('статья %d, %s: портал %s, список %s' % (
                    article_id, names[user_id],
                    'открывает' if reads else 'не открывает',
                    'называет' if user_id in found else 'не называет'))
                continue
            if not reads:
                closed += 1
                continue
            reading += 1
            permissions = wiki_access.permissions_only(
                wiki_articles.effective_permissions(
                    cursor, ctx, article, ctx['subjects'], allowed,
                    queries.section_rules_for_user))
            permissions['can_read'] = True
            if permissions != found[user_id]['permissions']:
                wrong.append('статья %d, %s: права на портале %s, в списке %s' % (
                    article_id, names[user_id],
                    sorted(k for k, v in permissions.items() if v),
                    sorted(k for k, v in found[user_id]['permissions'].items() if v)))
    return wrong, reading, closed


class SqlTests(_SqlHarness, unittest.TestCase):
    """Запросы модуля исполняются и отдают то, что от них ждёт сборка."""

    def test_chains_walk_up_to_the_root(self):
        chains = aa.section_chains(self.cursor, [13, 43, 60])
        self.assertEqual([n['id'] for n in chains[13]], [13, 12, 11, 10, 1])
        self.assertEqual([(n['id'], n['status']) for n in chains[43]],
                         [(43, 'active'), (42, 'archived'), (40, 'active')])
        self.assertEqual(chains[13][3]['department_id'], 1)
        self.assertEqual(chains[60][0]['space_id'], 2)

    def test_chains_of_nothing_do_not_touch_the_base(self):
        self.assertEqual(aa.section_chains(self.cursor, []), {})
        self.assertEqual(self.cursor.queries, 0)

    def test_owner_and_publicity_ride_with_the_section(self):
        chains = aa.section_chains(self.cursor, [50, 30])
        self.assertEqual(chains[50][0]['owner_user_id'], 900)
        self.assertEqual(chains[30][0]['visibility_scope'], 'public')

    def test_rules_of_several_sections_in_one_query(self):
        got = structure.list_section_rules(self.cursor, section_ids=[12, 13])
        self.assertEqual(sorted(r['id'] for r in got), [1, 2, 3, 21, 22, 23])
        self.assertEqual(self.cursor.queries, 1)

    def test_empty_section_list_is_not_all_rules(self):
        """Пустой список — «ни одного раздела». Отдай он все правила, в справку
        статьи без разделов приехала бы вся вика."""
        self.assertEqual(structure.list_section_rules(self.cursor, section_ids=[]), [])

    def test_single_section_filter_still_works(self):
        got = structure.list_section_rules(self.cursor, section_id=12)
        self.assertEqual(sorted(r['id'] for r in got), [3])
        self.assertEqual(len(structure.list_section_rules(self.cursor)), len(RULES))

    def test_guest_grants_are_only_the_living_ones(self):
        got = aa._guest_grants(self.cursor, 500, {13, 12, 11, 10, 1}, {1})
        # 952 истекла, 953 отозвана, 957 выдана на другую статью.
        self.assertEqual(sorted(g['user_id'] for g in got), [151, 950, 951, 954, 955])

    def test_spaces_and_their_boundaries(self):
        self.assertEqual(aa._spaces(self.cursor, {1, 3}),
                         {1: {'id': 1, 'name': 'Таксопарки', 'icon': None},
                          3: {'id': 3, 'name': 'Общее', 'icon': None}})
        self.assertEqual(aa._space_departments(self.cursor, {1, 2, 3}),
                         {1: {1, 2, 3, 5}, 2: {4}})

    def test_public_departments_are_listed_by_number(self):
        self.assertEqual(aa._public_departments(self.cursor, [30, 31]), {30: {1, 2}})


class PeopleSqlTests(_SqlHarness, unittest.TestCase):
    """Общий запрос на всех людей против запроса портала на одного."""

    def test_everyone_who_can_sign_in_and_nobody_else(self):
        """Уволенным дверь закрыта; отпуск и пустой статус читать не мешают —
        как у входа в портал."""
        found = {p['user_id'] for p in readers.load_people(self.cursor)}
        self.assertEqual(found, set(SIGNED_IN))
        self.assertNotIn(170, found)
        self.assertNotIn(173, found)
        self.assertTrue({171, 172} <= found)

    def test_one_query_whatever_the_headcount(self):
        readers.load_people(self.cursor)
        self.assertEqual(self.cursor.queries, 1)

    def test_person_row_equals_the_context_the_portal_builds(self):
        """Поле в поле: возглавляемые отделы, действующие группы (и оператором,
        и супервайзером), роли вики, режим доступа, тумблер отдела, гостевая
        выдача. Разойдись запросы — список считался бы по другим людям, чем
        сам доступ."""
        people = {p['user_id']: p for p in readers.load_people(self.cursor)}
        keys = ('user_id', 'otp_role', 'department_id', 'direction_id', 'job_title',
                'wiki_enabled', 'headed_department_ids', 'group_ids', 'access_mode',
                'has_guest_access')
        for user_id in SIGNED_IN:
            ctx = self.context(user_id)
            mine = people[user_id]
            for key in keys:
                a, b = ctx[key], mine[key]
                if isinstance(a, list):
                    a, b = sorted(a), sorted(b)
                self.assertEqual(a, b, (user_id, key))
            self.assertEqual(sorted(r['id'] for r in ctx['wiki_roles']),
                             sorted(r['id'] for r in mine['wiki_roles']), user_id)
            self.assertEqual(mine['subjects'], ctx['subjects'], user_id)
            self.assertEqual(mine['role_capabilities'], ctx['role_capabilities'], user_id)

    def test_the_fixture_has_every_kind_of_person(self):
        """Равенство на одних операторах ничего бы не доказало."""
        people = {p['user_id']: p for p in readers.load_people(self.cursor)}
        self.assertEqual(sorted(people[113]['headed_department_ids']), [2],
                         'расформированный отдел возглавляемым не считается')
        self.assertEqual(people[960]['group_ids'], [5])
        self.assertEqual(people[963]['group_ids'], [5], 'супервайзер группы — тоже в ней')
        self.assertEqual(people[964]['group_ids'], [], 'вышел вчера')
        self.assertEqual(people[965]['group_ids'], [], 'группа закрыта')
        self.assertEqual(people[966]['group_ids'], [], 'ещё не вошёл')
        # Те же три отсева — у супервайзера группы: членство у него в своей таблице.
        self.assertEqual(people[967]['group_ids'], [], 'супервайзер ещё не вошёл')
        self.assertEqual(people[968]['group_ids'], [], 'супервайзер вышел вчера')
        self.assertEqual(people[969]['group_ids'], [], 'группа супервайзера закрыта')
        self.assertEqual(people[961]['direction_id'], 7)
        self.assertEqual([r['code'] for r in people[105]['wiki_roles']], ['wiki_admin'])
        self.assertEqual(people[160]['access_mode'], 'manual')
        self.assertFalse(people[150]['wiki_enabled'])
        self.assertTrue(people[151]['has_guest_access'])
        self.assertFalse(people[952]['has_guest_access'], 'выдача истекла')
        self.assertEqual(people[120]['job_title'], 'Видеограф')
        self.assertEqual((people[100]['name'], people[100]['department_name']),
                         ('Оператор СЗоВ', 'СЗоВ'))


class MatchSqlTests(_SqlHarness, unittest.TestCase):
    """Совпадение правил со ВСЕМИ людьми против совпадения с каждым по отдельности."""

    def test_section_rules_match_as_they_do_for_one_person(self):
        people = readers.load_people(self.cursor)
        hits, _ = readers.matched_rules(self.cursor, people,
                                        section_rule_ids=[r[0] for r in RULES])
        for row in people:
            self.cursor.execute(
                'SELECT r.id FROM wiki_section_access_rules r WHERE ('
                + queries.SUBJECT_MATCH + ')',
                queries.subject_params(row['subjects'], row['user_id']))
            alone = {found[0] for found in self.cursor.fetchall()}
            together = {rule_id for rule_id, users in hits.items() if row['user_id'] in users}
            self.assertEqual(together, alone, row['name'])

    def test_article_rules_match_as_they_do_for_one_person(self):
        people = readers.load_people(self.cursor)
        _, hits = readers.matched_rules(self.cursor, people,
                                        article_rule_ids=[r[0] for r in ARTICLE_RULES])
        for row in people:
            self.cursor.execute(
                'SELECT r.id FROM wiki_article_access_rules r WHERE ('
                + queries.SUBJECT_MATCH + ')',
                queries.subject_params(row['subjects'], row['user_id']))
            alone = {found[0] for found in self.cursor.fetchall()}
            together = {rule_id for rule_id, users in hits.items() if row['user_id'] in users}
            self.assertEqual(together, alone, row['name'])

    def test_only_the_asked_rules_are_matched(self):
        people = readers.load_people(self.cursor)
        hits, _ = readers.matched_rules(self.cursor, people, section_rule_ids=[1, 8])
        self.assertEqual(sorted(hits), [1, 8])

    def test_two_queries_whatever_the_headcount(self):
        people = readers.load_people(self.cursor)
        before = self.cursor.queries
        readers.matched_rules(self.cursor, people, section_rule_ids=[1, 2, 3],
                              article_rule_ids=[1, 2])
        self.assertEqual(self.cursor.queries - before, 2)

    def test_the_fixture_matches_by_every_kind_of_subject(self):
        people = readers.load_people(self.cursor)
        hits, _ = readers.matched_rules(self.cursor, people,
                                        section_rule_ids=[r[0] for r in RULES])
        cases = {
            'отдел целиком': (1, 100, True),
            'порог должности не пройден': (2, 100, False),
            'порог должности пройден': (2, 101, True),
            'должность внутри отдела': (9, 120, True),
            'другая должность того же отдела': (9, 121, False),
            'старший над должностью': (9, 123, True),
            'человек лично': (5, 901, True),
            'группа оператором': (6, 960, True),
            'группа супервайзером': (6, 963, True),
            'вышедший из группы': (6, 964, False),
            'закрытая группа': (21, 965, False),
            'роль и все, кто выше': (8, 113, True),
            'глава отдела': (12, 113, True),
            'не глава': (12, 112, False),
            'направление': (13, 961, True),
            'роль вики': (14, 962, True),
        }
        for name, (rule_id, user_id, expected) in cases.items():
            self.assertEqual(user_id in hits.get(rule_id, ()), expected, name)


class AgreementTests(_SqlHarness, unittest.TestCase):
    """Список против настоящего периметра — по каждому человеку и статье.

    Совпасть обязаны и состав, и права (_disagreements): расхождение в любую
    сторону означает, что окно называет читателем того, кто статьи не видит,
    молчит о том, кто видит, или приписывает человеку не те права.
    """

    def real(self):
        return _real_access(self.cursor, self.context)

    def listed(self, article):
        return _listed(self.cursor, article)

    def test_the_list_agrees_with_the_real_perimeter(self):
        wrong, _reading, _closed = _disagreements(self.cursor, self.context)
        self.assertEqual(wrong, [])

    def test_the_fixture_actually_exercises_every_source(self):
        """Согласие на пустом месте ничего не доказывает: в компании обязаны
        быть и правила сверху, и архивный разрыв, и граница пространства, и
        все виды правил статьи."""
        real = self.real()
        cases = {
            # что проверяем: (статья, человек, читает ли)
            'правило раздела': (501, 100, True),
            'правило родителя доходит': (501, 903, True),
            'личное правило чужому отделу режет граница': (501, 901, False),
            'отдел без вики': (501, 150, False),
            'гость из отдела без вики': (501, 151, True),
            'гость раздела выше': (501, 950, True),
            'гость пространства': (501, 951, True),
            'истёкшая выдача': (501, 952, False),
            'гость другой статьи': (502, 957, False),
            'гость самой статьи': (500, 955, True),
            'запрет правилом статьи': (500, 100, False),
            'запрет правки не закрывает чтение': (501, 902, True),
            'супер-админ': (501, 104, True),
            'супер-админ с ролью вики из отдела без вики': (501, 109, True),
            'администратор вики': (501, 105, True),
            'администратор вики чужой компании': (501, 107, True),
            'ручной режим: раздел с подразделами': (501, 160, True),
            'ручной режим: чужое пространство': (501, 161, False),
            'ручной режим: отдел пространства': (506, 161, True),
            'по списку: правило статьи': (502, 112, True),
            'по списку: раздел не открывает': (502, 100, False),
            'по списку: запрет': (502, 110, False),
            'по списку: правило чужому отделу режет граница': (502, 130, False),
            'по списку: автор без отдела': (502, 140, False),
            'по списку: владелец статьи': (502, 121, True),
            'по списку: гость статьи': (502, 958, True),
            'строгая: явный грант': (503, 100, True),
            'строгая: автор': (503, 101, True),
            'строгая: владелец не читает': (503, 102, False),
            'строгая: администратор вики не читает': (503, 105, False),
            'строгая: супер-админ читает': (503, 104, True),
            'строгая: гость не читает': (503, 959, False),
            'без разделов: автор': (505, 100, True),
            'без разделов: группа': (505, 960, True),
            'пространство без границы': (506, 130, True),
            'только архивный раздел: гость': (507, 956, True),
            'только архивный раздел: отдел': (507, 120, False),
            'только архивный раздел: администратор вики': (507, 105, True),
            'только архивный раздел: администратор чужой компании': (507, 107, False),
            'владелец раздела': (508, 900, True),
            'правило без чтения не открывает': (508, 100, False),
            'выдача статьи без чтения не открывает': (508, 110, False),
            'публичный раздел со списком отделов: свой отдел': (512, 110, True),
            'публичный раздел со списком отделов: чужой отдел': (512, 120, False),
            'публичный раздел без списка: любой отдел пространства': (513, 120, True),
            'публичный раздел: отдел за границей пространства': (513, 130, False),
            'под архивом: правило сверху не доходит': (510, 120, False),
            'под архивом: личное правило': (510, 902, True),
            'под архивом: гость архивного родителя': (510, 956, True),
        }
        for name, (article_id, user_id, expected) in cases.items():
            ctx, _allowed, visible = real[user_id]
            self.assertEqual(_enters(ctx) and article_id in visible, expected, name)

    def test_draft_is_listed_as_it_will_be_read_once_out(self):
        """Статус в расчёт не входит: у черновика в том же разделе список тот
        же, что у вышедшей статьи, — плюс её автор."""
        draft = self.listed(article_row(511))
        published = self.listed(article_row(501))
        # У вышедшей статьи есть ещё свой гость (957) — выдача была на неё саму.
        self.assertEqual(set(draft), set(published) - {957})
        self.assertIn(100, draft)

    def test_rights_include_what_a_rule_elsewhere_lifted(self):
        """Администратору без удаления полный набор выписан в другом разделе —
        и на этой статье он удаляет: способность поднимается правилом откуда
        угодно, а у носителя мастер-ключа права равны способностям."""
        found = self.listed(article_row(501))
        self.assertTrue(found[108]['permissions']['can_delete'])

    def test_cost_does_not_grow_with_the_headcount(self):
        """Запросов на статью — постоянное число плюс по одному на человека в
        ручном режиме и на носителя мастер-ключа с неполной должностью."""
        article = article_row(501)
        chains = aa.section_chains(self.cursor, article['section_ids'])
        before = self.cursor.queries
        aa.who_reads(self.cursor, article, chains, set(chains))
        spent = self.cursor.queries - before
        # правила, гости, правила статьи, люди, два совпадения, граница
        # пространства = 7 (публичность не спрашивается: раздел закрытый);
        # в ручном режиме трое; мастер-ключ с неполной должностью у троих.
        self.assertEqual(spent, 7 + 3 + 3)


_ROLES = ('operator', 'operator', 'trainee', 'trainer', 'sv', 'admin', 'super_admin',
          'supervisor', 'marketing_manager', 'hr_manager', 'Operator', 'неизвестная', None)
_STATUSES = ('working', 'working', 'working', None, 'annual_leave', 'fired', 'dismissal')
_SUBJECT_KINDS = ('department', 'department', 'department_head', 'direction', 'group',
                  'otp_role', 'wiki_role', 'user')


def random_company(seed):
    """Случайная компания — те же таблицы, что у синтетической выше.

    Зерно задаёт её целиком: красный тест называет номер, и ту же компанию
    можно собрать заново и разобрать руками. Роли вики и направление берутся
    из общей заглушки (WIKI_ROLES, направление 7).
    """
    r = random.Random(seed)

    def chance(probability):
        return r.random() < probability

    def rights():
        # Чтение отмечено чаще остальных прав, но не всегда: правило без чтения
        # в базе лежать может.
        return (chance(0.75),) + tuple(chance(0.4) for _ in range(5))

    def subject():
        kind = r.choice(_SUBJECT_KINDS)
        ident = {'department': r.randint(1, 5), 'department_head': r.randint(1, 5),
                 'direction': 7, 'group': r.choice((5, 6)), 'otp_role': None,
                 'wiki_role': r.choice((3, 8, 9, 10)), 'user': r.choice(people)}[kind]
        role = r.choice(('operator', 'sv', 'trainer', 'admin', 'supervisor', 'trainee',
                         'super_admin')) if kind == 'otp_role' else None
        return kind, ident, role

    def threshold():
        return (r.choice((None, None, 10, 20, 30, 40, 50)),
                r.choice((None, None, None, 'А', 'Б')))

    people = list(range(100, 100 + r.randint(12, 22)))
    # Отдел 9 в таблице отделов не значится: человек с битой ссылкой на отдел.
    users = [(uid, 'Человек %d' % uid, r.choice(_ROLES),
              r.choice((None, 1, 1, 2, 2, 3, 4, 5, 9)), r.choice(_STATUSES),
              r.choice((None, None, 'А', 'Б')), r.choice((None, None, 7))) for uid in people]
    departments = [(dept, 'Отдел %d' % dept, r.choice([None] + people), chance(0.8),
                    chance(0.75)) for dept in range(1, 6)]
    spaces = [(1, 'Первое', None, 'active', r.choice((None, 1, 2))),
              (2, 'Второе', None, 'active', r.choice((None, 3, 4))),
              (3, 'Третье', None, r.choice(('active', 'archived')), r.choice((None, 5)))]
    space_departments = sorted({(space, dept) for space in (1, 2, 3) if chance(0.7)
                                for dept in range(1, 6) if chance(0.4)})

    sections = []
    for ident in range(1, r.randint(6, 14) + 1):
        parent = r.choice([None] + [s[0] for s in sections]) if sections else None
        # Подраздел обычно живёт в пространстве родителя — но не обязан.
        space = (next(s[1] for s in sections if s[0] == parent)
                 if parent is not None and chance(0.85) else r.choice((1, 2, 3)))
        sections.append((ident, space, parent, 'Раздел %d' % ident,
                         'archived' if chance(0.25) else 'active', ident,
                         r.choice((None, None, 1, 2)), 'common',
                         'public' if chance(0.2) else 'restricted',
                         r.choice([None, None, None] + people)))
    section_ids = [s[0] for s in sections]
    public_departments = sorted({(s[0], dept) for s in sections if chance(0.3)
                                 for dept in range(1, 6) if chance(0.4)})
    rules = [(ident, r.choice(section_ids)) + subject() + rights() + (chance(0.5),)
             + threshold() for ident in range(1, r.randint(8, 26))]

    def membership():
        # Чаще действующее: начато раньше и не окончено. Реже — ещё не начатое
        # или оконченное вчера.
        return (r.choice((5, 5, 6)), r.choice(people),
                r.choice((EARLIER, EARLIER, EARLIER, LATER)),
                r.choice((None, None, LATER, YESTERDAY)))

    operators = [membership() for _ in range(r.randint(2, 8))]
    supervisors = [membership() for _ in range(r.randint(0, 4))]
    if chance(0.5):
        # Один человек — и оператор, и супервайзер одной группы.
        both = r.choice(people)
        operators.append((5, both, EARLIER, None))
        supervisors.append((5, both, EARLIER, None))

    manual = r.sample(people, r.randint(0, 4))
    manual_access = [(uid, r.choice((1, 2, 3, 4, 5)) if chance(0.5) else None,
                      r.choice(section_ids) if chance(0.6) else None)
                     for uid in manual for _ in range(r.randint(0, 2))]

    articles, article_sections = [], {}
    for ident in range(500, 500 + r.randint(4, 9)):
        articles.append((ident, 'restricted' if chance(0.2) else 'inherit', 'published',
                         r.choice([None, None] + people),
                         r.choice([None, None, None] + people), chance(0.15)))
        article_sections[ident] = r.sample(section_ids, r.choice((0, 1, 1, 1, 2, 2, 3)))
    article_rules = [(ident, r.choice(articles)[0]) + subject()
                     + (r.choice(('grant', 'grant', 'deny')),) + rights() + threshold()
                     for ident in range(1, r.randint(1, 12))]

    guests = []
    for ident in range(1, r.randint(1, 9)):
        target = r.choice(('section', 'section', 'section', 'space', 'article'))
        guests.append((ident, r.choice(people),
                       r.choice(section_ids) if target == 'section' else None,
                       r.choice(articles)[0] if target == 'article' else None,
                       r.choice((1, 2, 3)) if target == 'space' else None,
                       chance(0.5), SOON if chance(0.12) else None,
                       GONE if chance(0.12) else SOON))
    for article in articles:
        # Автор статьи бывает ещё и её гостем.
        if article[3] and chance(0.2):
            guests.append((900 + article[0], article[3], None, article[0], None, True,
                           None, SOON))

    return {
        'USERS': users,
        'SIGNED_IN': [u[0] for u in users if u[4] not in ('fired', 'dismissal')],
        'DEPARTMENTS': departments, 'SPACES': spaces, 'SPACE_DEPARTMENTS': space_departments,
        'SECTIONS': sections, 'PUBLIC_DEPARTMENTS': public_departments, 'RULES': rules,
        'GROUPS': [(5, 'Пятая', 'active'), (6, 'Шестая', r.choice(('active', 'closed')))],
        'OPERATOR_MEMBERSHIPS': operators, 'SUPERVISOR_MEMBERSHIPS': supervisors,
        'WIKI_USER_ROLES': sorted({(r.choice(people), r.choice((3, 8, 9, 10)))
                                   for _ in range(r.randint(0, 5))}),
        'ACCESS_SETTINGS': [(uid, r.choice(('manual', 'manual', 'auto'))) for uid in manual],
        'MANUAL_ACCESS': manual_access, 'ARTICLES': articles,
        'ARTICLE_SECTIONS': article_sections, 'ARTICLE_RULES': article_rules, 'GUESTS': guests,
    }


class RandomCompaniesTests(_SqlHarness, unittest.TestCase):
    """То же согласие списка с порталом — на случайных компаниях.

    Компания выше собрана руками и сторожит то, о чём её автор подумал.
    Случайные сторожат остальное: расчёт списка — зеркало периметра, и разойтись
    они могут в любой день, когда правят одну из сторон.
    """

    COMPANIES = 60

    def test_the_list_agrees_on_random_companies(self):
        module = sys.modules[__name__]
        wrong, reading, closed = [], 0, 0
        for seed in range(self.COMPANIES):
            with mock.patch.multiple(module, **random_company(seed)):
                found, reads, shut = _disagreements(
                    StubCursor(self._real, _stubs()), self.context)
            wrong += ['компания %d — %s' % (seed, line) for line in found]
            reading, closed = reading + reads, closed + shut
        self.assertEqual(wrong, [])
        # Согласие на пустом месте ничего не доказывает: в случайных компаниях
        # обязаны быть и те, кому статьи открыты, и те, кому закрыты.
        self.assertGreater(reading, 1000)
        self.assertGreater(closed, 1000)

    def test_the_same_seed_builds_the_same_company(self):
        self.assertEqual(repr(random_company(7)), repr(random_company(7)))
        self.assertNotEqual(repr(random_company(7)), repr(random_company(8)))


class DescribeOnSqlTests(_SqlHarness, unittest.TestCase):
    """Вся сборка целиком на настоящих запросах."""

    def describe(self, user_id, article_id=500, *, cursor=None):
        cursor = cursor or self.cursor
        ctx = self.context(user_id, cursor)
        allowed = queries.allowed_section_ids(cursor, ctx, ctx['subjects'])
        return aa.describe(cursor, ctx, article_row(article_id), allowed, with_people=True)

    def names(self, got):
        return [p['name'] for p in got['people']]

    def test_supervisor_of_one_branch(self):
        """Супервайзер СЗоВ видит свой «Оператор» и «Оператор» ОП (тот открыт
        правилом на роль всей компании); разделы маркетинга и Тез ему не видны."""
        got = self.describe(102)
        self.assertEqual([p['section_id'] for p in got['places']], [23, 13])
        # 41, 43, 60 — вне периметра, 42 — в архиве.
        self.assertEqual(got['hidden_places'], 4)
        found = got['places'][1]
        self.assertEqual([n['name'] for n in found['path']],
                         ['Коммерческий директор', 'СЗоВ', 'Руководитель группы',
                          'Супервайзер', 'Оператор'])
        self.assertEqual(found['space'], {'id': 1, 'name': 'Таксопарки', 'icon': None})

        people = {p['name']: p for p in got['people']}
        # Оператору 100 статья закрыта запретом — в списке его нет.
        self.assertNotIn('Оператор СЗоВ', people)
        self.assertEqual((people['Тренер СЗоВ']['rights'], people['Тренер СЗоВ']['via']),
                         ('r', []))
        # Супервайзеру правку даёт раздел выше: по отделу и должности, без пометки.
        self.assertEqual((people['Супервайзер СЗоВ']['rights'],
                          people['Супервайзер СЗоВ']['via']), ('rce', []))
        self.assertEqual(people['Руководитель СЗоВ']['rights'], 'rcepad')
        # Личное правило добавило правку к чтению по отделу — оно и названо.
        self.assertEqual((people['Свой с личным правилом']['rights'],
                          people['Свой с личным правилом']['via']),
                         ('rce', [{'kind': 'user', 'label': None}]))
        # Участник группы читает и по роли («Оператор» ОП) — группа ничего не
        # добавила, и пометки нет.
        self.assertEqual(people['Участник группы']['via'], [])
        for guest in ('Гость раздела', 'Гость пространства', 'Гость статьи'):
            self.assertEqual(people[guest]['via'], [{'kind': 'guest', 'label': None}], guest)
        self.assertEqual(people['Ручной режим: раздел']['via'],
                         [{'kind': 'manual', 'label': None}])
        self.assertEqual(people['Администратор вики']['via'],
                         [{'kind': 'wiki_admin', 'label': None}])
        # Супер-админ подпадает и под правило статьи — но читает он всё и так,
        # и пометка была бы шумом.
        self.assertEqual((people['Супер-админ']['rights'], people['Супер-админ']['via']),
                         ('rcepad', []))

    def test_people_of_the_hidden_branches_are_not_named(self):
        """Оператор Тез читает статью через регламенты Тез, гость архивного
        раздела — через раздел под ним. Супервайзеру СЗоВ эти разделы не видны,
        и их читателей окно ему не называет."""
        got = self.describe(102)
        names = self.names(got)
        for hidden in ('Оператор Тез', 'Чужой с личным правилом', 'Ручной режим: отдел',
                       'Гость архивного раздела'):
            self.assertNotIn(hidden, names, hidden)
        dump = json.dumps(got, ensure_ascii=False)
        for name in ('Тез: регламенты', 'Старые материалы', 'Под архивом'):
            self.assertNotIn(name, dump, name)
        # Администратор видит все разделы — и тех же людей уже называет.
        full = self.names(self.describe(104))
        for shown in ('Оператор Тез', 'Чужой с личным правилом', 'Гость архивного раздела'):
            self.assertIn(shown, full, shown)

    def test_supervisor_right_of_the_article_rule_reaches_other_departments(self):
        """Правило статьи выдаёт её супервайзерам и выше всей компании — они
        названы и супервайзеру СЗоВ: источник у них не раздел, а сама статья."""
        got = self.describe(102)
        people = {p['name']: p for p in got['people']}
        self.assertEqual(people['Супервайзер ОП']['rights'], 'rce')
        self.assertEqual(people['Супервайзер ОП']['via'],
                         [{'kind': 'article_rule', 'label': None}])

    def test_administrator_sees_every_place_and_everyone(self):
        got = self.describe(104)
        self.assertEqual(got['hidden_places'], 0)
        self.assertEqual({p['section_id'] for p in got['places']}, {13, 23, 41, 42, 43, 60})
        archived = next(p for p in got['places'] if p['section_id'] == 42)
        self.assertTrue(archived['archived'])
        # Живой раздел под архивным: путь без архивного родителя.
        under = next(p for p in got['places'] if p['section_id'] == 43)
        self.assertEqual([n['name'] for n in under['path']], ['Под архивом'])
        names = self.names(got)
        for name in ('Оператор ОП', 'Видеограф', 'Оператор Тез', 'Гость архивного раздела'):
            self.assertIn(name, names, name)
        self.assertEqual(names, sorted(names, key=lambda n: n.lower().replace('ё', 'е')))

    def test_branch_holder_gets_the_archive_of_his_branch(self):
        """Руководителю маркетинга выдано «заводить подразделы» в «Маркетинге»:
        архивные «Старые материалы» — его подраздел, и «Структура» ему их
        показывает. Супервайзеру СЗоВ с той же выдачей в базе — нет: ветка не
        его."""
        mine = self.describe(123, cursor=StubCursor(self._real, _stubs(manage={10})))
        self.assertEqual(sorted(p['section_id'] for p in mine['places']), [23, 41, 42])
        foreign = self.describe(102, cursor=StubCursor(self._real, _stubs(manage={10})))
        self.assertEqual(sorted(p['section_id'] for p in foreign['places']), [13, 23])

    def test_without_the_toggle_the_archive_stays_hidden(self):
        got = self.describe(123)
        self.assertEqual(sorted(p['section_id'] for p in got['places']), [23, 41])

    def test_by_list_article_lists_those_on_the_list(self):
        got = self.describe(104, 502)
        self.assertTrue(got['article']['by_list_only'])
        names = self.names(got)
        self.assertIn('Супервайзер ОП', names)
        self.assertIn('Таргетолог', names)                 # владелец статьи
        self.assertIn('Гость статьи по списку', names)
        for absent in ('Оператор СЗоВ', 'Оператор ОП', 'Оператор Тез', 'Без отдела'):
            self.assertNotIn(absent, names, absent)

    def test_article_without_sections(self):
        got = self.describe(104, 505)
        self.assertEqual((got['places'], got['hidden_places']), ([], 0))
        names = self.names(got)
        self.assertIn('Оператор СЗоВ', names)              # автор
        self.assertIn('Участник группы', names)


# ─────────────────────────────────────────────────────────────────────────────
# Исходники: то, что ломается молча
# ─────────────────────────────────────────────────────────────────────────────

def _read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding='utf-8')


def _code(source):
    """Исходник без комментариев: объяснения называют то, чего в коде уже нет."""
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'(?m)^\s*//[^\n]*$', '', source)


class SourceGuardTests(unittest.TestCase):

    def test_button_and_sheet_wait_for_the_edit_right(self):
        """Признак один и тот же у кнопки, у окна и у двери на сервере: иначе
        кнопка появится у того, кому сервер ответит отказом."""
        article = _code(_read('src', 'components', 'wiki', 'WikiArticle.jsx'))
        self.assertIn('const seesPlaces = !!(article?.permissions?.can_edit'
                      ' || article?.can_view_readers);', article)
        button = article[article.index('setAccessOpen(true)') - 400:
                         article.index('setAccessOpen(true)')]
        self.assertIn('{seesPlaces && (', button)
        sheet = article[article.index('<WikiArticleAccess') - 120:
                        article.index('<WikiArticleAccess')]
        self.assertIn('{seesPlaces && (', sheet)

        route = _read('wiki', 'routes_articles.py')
        door = route[route.index("@wiki_route('/articles/<int:article_id>/access')"):]
        door = door[:door.index('def _display_urls')]
        self.assertIn("permissions.get('can_edit')", door)
        self.assertIn('wiki_articles.effective_permissions(', door)
        self.assertIn('with_people = wiki_article_access.sees_readers(ctx)', door)

    def test_page_and_door_ask_one_function_who_sees_the_people(self):
        """Кнопку называет карточка статьи, список отдаёт дверь справки — и обе
        обязаны спрашивать одно и то же, иначе «Доступ» пообещает список тому,
        кому он не придёт."""
        route = _read('wiki', 'routes_articles.py')
        self.assertEqual(route.count('wiki_article_access.sees_readers(ctx)'), 2)
        self.assertIn("article['can_view_readers'] = wiki_article_access.sees_readers(ctx)",
                      route)

    def test_door_does_not_write(self):
        route = _read('wiki', 'routes_articles.py')
        door = route[route.index("@wiki_route('/articles/<int:article_id>/access')"):]
        door = door[:door.index('def _display_urls')]
        for forbidden in ('log_action', 'register_view', 'get_article(', "methods="):
            self.assertNotIn(forbidden, door, forbidden)

        for name in ('article_access.py', 'readers.py'):
            module = _read('wiki', name)
            statements = re.findall(r'"""(.*?)"""', module, re.S)
            sql = ' '.join(s for s in statements if 'SELECT' in s)
            for verb in ('INSERT', 'UPDATE', 'DELETE'):
                self.assertNotRegex(sql, r'\b%s\b' % verb, name)

    def test_readers_are_built_from_the_real_parts(self):
        """Обратный расчёт собран из деталей прямого: своего условия «правило ↔
        человек», своих субъектов и своего расчёта прав в нём быть не должно —
        второй вычислитель доступа однажды разойдётся с первым."""
        module = _read('wiki', 'readers.py')
        for part in ('queries.SUBJECT_MATCH', 'queries.subject_params(',
                     'wiki_access.collect_subjects(', 'wiki_access.resolve_capabilities(',
                     'wiki_access.resolve_article_permissions(', 'queries.load_capabilities('):
            self.assertIn(part, module, part)
        code = re.sub(r'(?m)#[^\n]*$', '', re.sub(r'""".*?"""', '', module, flags=re.S))
        self.assertNotIn('subject_type =', code, 'условие совпадения переписано руками')

    def test_sheet_has_no_controls_that_change_access(self):
        """Окно — справка. Выдача живёт в «Структуре» со своей лестницей."""
        sheet = _code(_read('src', 'components', 'wiki', 'WikiArticleAccess.jsx'))
        for call in ('axios.post', 'axios.patch', 'axios.put', 'axios.delete',
                     'IosToggle', 'CustomSelect'):
            self.assertNotIn(call, sheet, call)
        # Единственное обращение к сети — чтение через общий загрузчик.
        self.assertEqual(sheet.count('axios.'), 1)
        self.assertIn('loadArticleAccess(axios.get, { base, articleId, headers })', sheet)

    def test_sheet_asks_the_very_door_the_server_opens(self):
        """Адрес собирает клиент, правило маршрута пишет сервер — двумя руками
        в двух файлах. Разойдись они на букву, окно отвечало бы отказом каждому,
        а тесты обеих сторон остались бы зелёными."""
        client = _code(_read('src', 'components', 'wiki', 'articleAccess.js'))
        template = re.search(
            r'articleAccessUrl = \(base, articleId\) => `\$\{base\}([^`]+)`', client)
        self.assertIsNotNone(template, 'адрес двери собирается не там, где ждёт страж')
        route = _read('wiki', 'routes_articles.py')
        rule_ = re.search(r"@wiki_route\('([^']+)'\)\s+def wiki_article_access_view", route)
        self.assertIsNotNone(rule_)
        self.assertEqual(template.group(1).replace('${articleId}', '<int:article_id>'),
                         rule_.group(1))

    def test_both_sides_spell_the_rights_and_the_sources_alike(self):
        """Права едут буквами, источники доступа — словами. Буква или слово,
        которых не знает другая сторона, молча выпали бы из строки человека."""
        client = _code(_read('src', 'components', 'wiki', 'articleAccess.js'))
        keys = dict(re.findall(r"(\w): '(can_\w+)'", client[client.index('const RIGHT_KEYS'):
                                                           client.index('export const permissionsFromRights')]))
        self.assertEqual(keys, dict(aa.RIGHT_LETTERS))
        captions = client[client.index('const VIA_CAPTION = {'):]
        captions = captions[:captions.index('};')]
        self.assertEqual(sorted(re.findall(r'(?m)^\s+(\w+): \(', captions)),
                         sorted(readers.VIA_ORDER))

    def test_preset_names_live_in_one_module(self):
        """«Чтение», «Правка», «Полный доступ» называет один модуль: в двух
        копиях одно и то же правило звалось бы в двух окнах по-разному."""
        grants = _read('src', 'components', 'wiki', 'sectionGrants.js')
        self.assertIn('export const PRESETS = [', grants)
        for name in ('WikiSectionAccess.jsx', 'WikiArticleAccess.jsx', 'articleAccess.js'):
            source = _code(_read('src', 'components', 'wiki', name))
            self.assertNotIn('const PRESETS', source, name)
            self.assertNotIn("summary: 'Полный доступ'", source, name)


if __name__ == '__main__':
    unittest.main()
