# -*- coding: utf-8 -*-
"""«Основные средства» (ACC-1): чистые правила, права и сторожи подключения — без базы и Flask.

Весь серверный путь на настоящем Postgres — tests/test_fixed_assets_postgres.py. Здесь: правила
ТЗ бухгалтерии (два фото, проверка перед списанием, путь запроса), права ролей (раздел 2 ТЗ),
печатная форма акта, выгрузка — и то, что экран называет коды теми же словами, что сервер, а
раздел подключён к порталу во всех нужных местах.
"""
import ast
import hashlib
import io
import re
import unittest
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fixed_assets import access, acts, queries, report, rules, schema
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]

# Хеши (sha256, первые 16 знаков) названия, БИН и фамилий из присланного образца акта: в коде их быть
# не должно, а сами слова в открытый репозиторий не идут.
SAMPLE_ACT_WORDS = {'15ce340ca0bec78e', '1a82fe7bfc5e91df', 'dd5d7b1d8ef5a382', 'd9f83bd6672b6c3e'}
META = (ROOT / 'src/components/fixed_assets/fixedAssetsMeta.js').read_text(encoding='utf-8')
APP = (ROOT / 'src/App.jsx').read_text(encoding='utf-8')
FA_ICON = (ROOT / 'src/components/common/FaIcon.jsx').read_text(encoding='utf-8')
BOT_PATH = ROOT / 'bot_schedule2.py'
TODAY = date(2026, 11, 17)


def js_object(name, text=META):
    """Литерал `export const NAME = { key: 'value', ... };` → словарь строк."""
    match = re.search(r'(?:export )?const %s = \{' % re.escape(name), text)
    assert match, name
    depth, start = 1, match.end()
    for index in range(start, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return dict(re.findall(r"(\w+): '([^']*)'", text[start:index]))
    raise AssertionError('незакрытый объект ' + name)


def ctx(user_id=10, *, department=None, headed=(), member=(), is_super=False, has_assets=False, involved=False):
    codes = {1: 'accounting', 2: 'hr', 3: 'it', 4: 'analytik', 5: 'op', 6: 'marketing'}
    return {
        'user_id': user_id, 'name': 'Сотрудник %s' % user_id, 'is_super': is_super,
        'department_id': {code: number for number, code in codes.items()}.get(department),
        'department_code': department,
        'headed_department_ids': list(headed), 'headed_department_codes': [codes[item] for item in headed],
        'member_roles': list(member), 'has_assets': has_assets, 'involved': involved,
    }


SUPER = ctx(1, is_super=True)
ACCOUNTANT = ctx(2, department='accounting')
HR = ctx(3, department='hr')
IT = ctx(4, department='it')
ANALYTICS_HEAD = ctx(5, department='analytik', headed=(4,))
ANALYST = ctx(6, department='analytik')
SALES_HEAD = ctx(7, department='op', headed=(5,))
HOLDER = ctx(8, department='op', has_assets=True)
OUTSIDER = ctx(9, department='marketing')
RECEIVER = ctx(20, department='marketing', involved=True)        # принимающая сторона акта
MARKETING_HEAD = ctx(60, department='marketing', headed=(6,))


def request_row(**fields):
    row = {'id': 1, 'kind': rules.KIND_TRANSFER, 'status': rules.STATUS_SIGNING, 'created_by': 7,
           'from_user_id': 8, 'from_department_id': 5, 'to_user_id': 20, 'to_department_id': 6,
           'review_by': None, 'review_department_id': None, 'need_for_user_id': None}
    row.update(fields)
    return row


class PilotOff(unittest.TestCase):
    """Правила ролей проверяются с открытым разделом; сам пилот — отдельным тестом."""

    def setUp(self):
        self._pilot = access.PILOT_SUPER_ADMIN_ONLY
        access.PILOT_SUPER_ADMIN_ONLY = False

    def tearDown(self):
        access.PILOT_SUPER_ADMIN_ONLY = self._pilot


class InventoryNumberTests(unittest.TestCase):
    def test_next_number_keeps_prefix_and_zero_padding(self):
        self.assertEqual(rules.next_inventory_number('ОС-000123'), 'ОС-000124')
        self.assertEqual(rules.next_inventory_number('0457'), '0458')
        self.assertEqual(rules.next_inventory_number('ИНВ-99'), 'ИНВ-100')
        self.assertEqual(rules.next_inventory_number('А-12/2026-007'), 'А-12/2026-008')

    def test_no_digits_means_no_suggestion(self):
        self.assertEqual(rules.next_inventory_number('без номера'), '')
        self.assertEqual(rules.next_inventory_number(None), '')


class PhotoRuleTests(unittest.TestCase):
    """ТЗ: «Не менее 2 фото. Первое показывает ОС целиком, второе даёт чёткий инвентарный номер.
    Без обоих фото кнопка «Отправить» не срабатывает, система показывает причину»."""

    ITEMS = [{'asset_id': 1, 'name': 'Ноутбук', 'inventory_number': 'ОС-1'},
             {'asset_id': 2, 'name': 'Кресло', 'inventory_number': 'ОС-2'}]

    def test_both_photos_are_required_for_every_asset(self):
        photos = [(1, rules.PHOTO_WHOLE), (1, rules.PHOTO_NUMBER), (2, rules.PHOTO_WHOLE)]
        gaps = rules.photo_gaps(self.ITEMS, photos)
        self.assertEqual([(gap['asset_id'], gap['missing']) for gap in gaps], [(2, [rules.PHOTO_NUMBER])])

    def test_extra_photos_do_not_replace_required_ones(self):
        photos = [(1, rules.PHOTO_EXTRA), (1, rules.PHOTO_EXTRA), (1, rules.PHOTO_WHOLE)]
        gaps = rules.photo_gaps(self.ITEMS[:1], photos)
        self.assertEqual(gaps[0]['missing'], [rules.PHOTO_NUMBER])

    def test_no_gaps_when_complete(self):
        photos = [(asset, kind) for asset in (1, 2) for kind in rules.REQUIRED_PHOTOS]
        self.assertEqual(rules.photo_gaps(self.ITEMS, photos), [])
        self.assertEqual(rules.photo_gap_message([]), '')

    def test_reason_names_the_asset_and_the_missing_photo(self):
        message = rules.photo_gap_message(rules.photo_gaps(self.ITEMS, [(1, rules.PHOTO_WHOLE)]))
        self.assertIn('Ноутбук (инв. № ОС-1) — инвентарный номер', message)
        self.assertIn('Кресло (инв. № ОС-2) — ОС целиком и инвентарный номер', message)
        self.assertIn('два снимка', message)

    def test_long_list_is_cut_but_counted(self):
        items = [{'asset_id': number, 'name': 'ОС %d' % number, 'inventory_number': str(number)} for number in range(1, 7)]
        message = rules.photo_gap_message(rules.photo_gaps(items, []))
        self.assertIn('и ещё у 3 ОС', message)


class RequestPathTests(unittest.TestCase):
    """ТЗ, раздел 5: черновик → на проверке (только списание) → на подписании → ждёт оригинал → закрыт."""

    def test_where_request_goes_after_submit(self):
        self.assertEqual(rules.after_submit(rules.KIND_TRANSFER), rules.STATUS_SIGNING)
        self.assertEqual(rules.after_submit(rules.KIND_DISMISSAL), rules.STATUS_SIGNING)
        self.assertEqual(rules.after_submit(rules.KIND_WRITEOFF), rules.STATUS_REVIEW)
        self.assertEqual(rules.after_submit(rules.KIND_NEED), rules.STATUS_REVIEW)

    def test_writeoff_is_checked_by_it_or_department_head(self):
        self.assertEqual(rules.review_group(rules.KIND_WRITEOFF, ['it', 'it']), rules.REVIEW_IT)
        self.assertEqual(rules.review_group(rules.KIND_WRITEOFF, ['head']), rules.REVIEW_HEAD)
        self.assertEqual(rules.review_group(rules.KIND_NEED, []), rules.REVIEW_ACCOUNTING)

    def test_tech_and_furniture_do_not_mix_in_one_writeoff(self):
        with self.assertRaises(rules.FaError) as caught:
            rules.review_group(rules.KIND_WRITEOFF, ['it', 'head'])
        self.assertEqual(caught.exception.code, 'FA_MIXED_CHECK')

    def test_default_categories_follow_the_tz(self):
        by_name = dict(rules.DEFAULT_CATEGORIES)
        self.assertEqual(by_name['Компьютеры и ноутбуки'], rules.REVIEW_IT)
        self.assertEqual(by_name['Оргтехника'], rules.REVIEW_IT)
        self.assertEqual(by_name['Мебель'], rules.REVIEW_HEAD)
        self.assertEqual(by_name['Прочее'], rules.REVIEW_HEAD)

    def test_track_marks_done_current_and_todo(self):
        steps = rules.track({'kind': rules.KIND_WRITEOFF, 'status': rules.STATUS_SIGNING, 'review_by': 'it'})
        self.assertEqual([step['code'] for step in steps],
                         ['draft', 'review', 'signing', 'original', 'closed'])
        self.assertEqual([step['state'] for step in steps], ['done', 'done', 'current', 'todo', 'todo'])
        self.assertEqual(steps[1]['label'], 'На проверке IT')

    def test_transfer_has_no_review_step(self):
        steps = rules.track({'kind': rules.KIND_TRANSFER, 'status': rules.STATUS_ORIGINAL})
        self.assertEqual([step['code'] for step in steps], ['draft', 'signing', 'original', 'closed'])

    def test_closed_request_is_all_done_and_rejected_stops_on_its_step(self):
        closed = rules.track({'kind': rules.KIND_TRANSFER, 'status': rules.STATUS_CLOSED})
        self.assertEqual({step['state'] for step in closed}, {'done'})
        rejected = rules.track({'kind': rules.KIND_WRITEOFF, 'status': rules.STATUS_REJECTED,
                                'rejected_from': rules.STATUS_REVIEW, 'review_by': 'head'})
        self.assertEqual([step['state'] for step in rejected], ['done', 'stopped', 'todo', 'todo', 'todo'])

    def test_signed_act_statuses_unlock_dismissal(self):
        # «Подписывается акт, сотрудник прикрепляет скан. После этого HR получает возможность оформить приказ».
        self.assertEqual(rules.SIGNED_STATUSES, (rules.STATUS_ORIGINAL, rules.STATUS_CLOSED))

    def test_what_is_missing_before_submit(self):
        self.assertEqual(set(rules.missing_for_submit({'kind': rules.KIND_TRANSFER}, [])), {'asset_ids', 'to_user_id'})
        self.assertEqual(set(rules.missing_for_submit({'kind': rules.KIND_WRITEOFF}, [{'asset_id': 1}])), {'comment'})
        self.assertEqual(set(rules.missing_for_submit({'kind': rules.KIND_NEED}, [])),
                         {'need_title', 'need_for_user_id', 'comment'})
        complete = {'kind': rules.KIND_TRANSFER, 'to_user_id': 5}
        self.assertEqual(rules.missing_for_submit(complete, [{'asset_id': 1}]), {})

    def test_status_label_names_the_checker(self):
        self.assertEqual(rules.status_label({'status': 'review', 'review_by': 'head'}), 'На проверке у руководителя отдела')
        self.assertEqual(rules.status_label({'status': 'review', 'review_by': 'accounting'}), 'На рассмотрении бухгалтерии')
        self.assertEqual(rules.status_label({'status': 'original'}), 'Ждёт оригинал в бухгалтерии')


class CleanValuesTests(unittest.TestCase):
    def test_money_accepts_spaces_and_comma(self):
        self.assertEqual(rules.clean_money('389 900,50'), Decimal('389900.50'))
        self.assertEqual(rules.clean_money('389 900'), Decimal('389900.00'))
        self.assertIsNone(rules.clean_money(''))

    def test_money_rejects_garbage_and_negative(self):
        for value in ('abc', '-5', True, 'NaN', '1e30'):
            with self.subTest(value=value), self.assertRaises(rules.FaError):
                rules.clean_money(value)

    def test_dates(self):
        self.assertEqual(rules.clean_date('2026-11-17'), TODAY)
        self.assertEqual(rules.clean_date('17.11.2026'), TODAY)
        self.assertIsNone(rules.clean_date(''))
        with self.assertRaises(rules.FaError):
            rules.clean_date('17/11/2026')
        with self.assertRaises(rules.FaError):
            rules.clean_date('9999-12-31')

    def test_event_date_cannot_be_in_future_or_too_old(self):
        self.assertEqual(rules.past_date(TODAY, TODAY, label='Дата'), TODAY)
        with self.assertRaises(rules.FaError) as future:
            rules.past_date(TODAY + timedelta(days=3), TODAY, label='Дата подписания')
        self.assertEqual(future.exception.code, 'FA_DATE_IN_FUTURE')
        with self.assertRaises(rules.FaError) as old:
            rules.past_date(TODAY - timedelta(days=800), TODAY, label='Дата подписания')
        self.assertEqual(old.exception.code, 'FA_DATE_TOO_OLD')

    def test_asset_card_requires_name_category_and_number(self):
        with self.assertRaises(rules.FaError):
            rules.clean_asset({'name': '', 'category_id': 1, 'inventory_number': '1'}, TODAY)
        with self.assertRaises(rules.FaError):
            rules.clean_asset({'name': 'Ноутбук', 'category_id': 1, 'inventory_number': ' '}, TODAY)
        fields = rules.clean_asset({'name': ' Ноутбук ', 'category_id': '3', 'inventory_number': 'ОС-1', 'cost': '100'}, TODAY)
        self.assertEqual((fields['name'], fields['category_id'], fields['acquired_on']), ('Ноутбук', 3, TODAY))

    def test_partial_correction_touches_only_sent_fields(self):
        self.assertEqual(rules.clean_asset({'serial_number': ' SN-1 '}, TODAY, partial=True), {'serial_number': 'SN-1'})

    def test_barcode_is_stored_without_spaces(self):
        # Сканер и человек добавляют пробелы («2 000000 056784») — в карточке код без них.
        self.assertEqual(rules.clean_barcode(' 2000000 056784 '), '2000000056784')
        self.assertEqual(rules.clean_barcode('K2201N0012345'), 'K2201N0012345')
        self.assertEqual(rules.clean_barcode('ЕК0000456'), 'ЕК0000456')
        self.assertIsNone(rules.clean_barcode('  '))
        self.assertIsNone(rules.clean_barcode(None))
        for bad in ('20000;00056784', 'x' * (rules.BARCODE_MAX + 1), 'код#1'):
            with self.subTest(bad=bad), self.assertRaises(rules.FaError) as caught:
                rules.clean_barcode(bad)
            self.assertEqual(caught.exception.code, 'FA_BARCODE_INVALID')
        fields = rules.clean_asset({'name': 'Ноутбук', 'category_id': 1, 'inventory_number': 'ЕК0000456',
                                    'barcode': '2000000056784 '}, TODAY)
        self.assertEqual(fields['barcode'], '2000000056784')
        # Исправление карточки штрихкод не меняет: только привязка — со строкой «штрихкод» в истории и
        # без списанных ОС.
        self.assertNotIn('barcode', rules.ASSET_CORRECTABLE)
        self.assertEqual(rules.ASSET_FIELD_LABELS['barcode'], 'Штрихкод')

    def test_scanned_code_and_card_number_compare_in_one_form(self):
        # Пробелы внутри, регистр и кириллица-двойник латиницы (ЕК0000456 набран как EK0000456).
        self.assertEqual(rules.fold_code(' ЕК 0000456\n'), 'ek0000456')
        self.assertEqual(rules.fold_code('EK0000456'), 'ek0000456')
        self.assertEqual(rules.fold_code('ОС 000123'), rules.fold_code('OC000123'))
        self.assertEqual(rules.fold_code(None), '')
        # Буквы без латинского двойника остаются собой.
        self.assertEqual(rules.fold_code('Ж-15'), 'ж-15')

    def test_upc_a_is_looked_up_in_both_spellings(self):
        self.assertEqual(rules.barcode_variants('012345678905'), ['012345678905', '0012345678905'])
        self.assertEqual(rules.barcode_variants('0012345678905'), ['0012345678905', '012345678905'])
        self.assertEqual(rules.barcode_variants(' 2000000056784 '), ['2000000056784'])
        self.assertEqual(rules.barcode_variants('K2201N0012345'), ['k2201n0012345'])
        self.assertEqual(rules.barcode_variants(''), [])

    def test_scan_code_is_what_the_scanner_read_without_spaces(self):
        self.assertEqual(rules.scan_code(' 2000000056784\n'), '2000000056784')
        self.assertIsNone(rules.scan_code(''))
        self.assertEqual(len(rules.scan_code('1' * 500)), 120)

    def test_barcode_is_bound_by_accounting_while_the_asset_is_on_books(self):
        active, gone = {'status': 'active'}, {'status': 'written_off'}
        self.assertTrue(access.can_set_barcode(ctx(department='accounting'), active))
        self.assertTrue(access.can_set_barcode(ctx(is_super=True), active))
        self.assertFalse(access.can_set_barcode(ctx(department='accounting'), gone))
        for viewer in (ctx(department='it'), ctx(department='hr'), ctx(headed=[5]), ctx(has_assets=True)):
            with self.subTest(viewer=viewer):
                self.assertFalse(access.can_set_barcode(viewer, active))

    def test_barcode_event_says_what_happened_to_the_sticker(self):
        self.assertEqual(rules.event_label('barcode_set', {'from': None, 'to': '2000000056784'}), 'Привязан штрихкод')
        self.assertEqual(rules.event_label('barcode_set', {'from': '2000000056784', 'to': '2000000001012'}),
                         'Изменён штрихкод')
        self.assertEqual(rules.event_label('barcode_set', {'from': '2000000056784', 'to': None}), 'Снят штрихкод')
        self.assertEqual(rules.event_label('barcode_set', None), 'Снят штрихкод')
        self.assertEqual(rules.event_label('moved', {'to': 'x'}), 'Перемещено')
        self.assertEqual(rules.event_label('no_such_kind'), 'no_such_kind')

    def test_id_list_keeps_order_and_drops_repeats(self):
        self.assertEqual(rules.clean_id_list([5, '3', 5, 8]), [5, 3, 8])
        with self.assertRaises(rules.FaError):
            rules.clean_id_list(list(range(1, rules.MAX_ASSETS_PER_REQUEST + 2)))

    def test_transfer_must_change_something(self):
        asset = {'mol_user_id': 8, 'department_id': 5, 'location': 'каб. 204', 'on_stock': False}
        same = {'user_id': 8, 'department_id': 5, 'location': 'каб. 204', 'on_stock': False}
        self.assertFalse(rules.holder_changes(asset, same))
        self.assertTrue(rules.holder_changes(asset, dict(same, location='каб. 206')))
        self.assertTrue(rules.holder_changes(asset, dict(same, user_id=9)))
        self.assertTrue(rules.holder_changes(asset, dict(same, on_stock=True)))


class SignersTests(unittest.TestCase):
    """ТЗ: «Акт подписывают отдающий, принимающий и руководители отделов»."""

    def test_transfer_is_signed_by_both_sides_and_both_heads(self):
        row = request_row(from_name='Ахметов', to_name='Садыкова')
        signers = rules.signers(row, {5: 'Нурланова', 6: 'Ким'})
        self.assertEqual(signers, [
            ('Сдал', 'Ахметов'), ('Принял', 'Садыкова'),
            ('Руководитель отдела (сдающая сторона)', 'Нурланова'),
            ('Руководитель отдела (принимающая сторона)', 'Ким'), ('Бухгалтер', None)])

    def test_one_head_for_both_sides_signs_once(self):
        row = request_row(from_name='Ахметов', to_name='Садыкова', to_department_id=5)
        roles = [role for role, _name in rules.signers(row, {5: 'Нурланова'})]
        self.assertEqual(roles, ['Сдал', 'Принял', 'Руководитель отдела (сдающая сторона)', 'Бухгалтер'])

    def test_head_who_is_a_side_signs_once(self):
        row = request_row(from_name='Байжанов', to_name='Нурланова')
        signers = rules.signers(row, {5: 'Нурланова', 6: 'Нурланова'})
        self.assertEqual(signers, [('Сдал', 'Байжанов'), ('Принял', 'Нурланова'), ('Бухгалтер', None)])

    def test_writeoff_act_carries_the_checker(self):
        row = request_row(kind=rules.KIND_WRITEOFF, from_name='Ахметов', checked_by_name='Омаров')
        self.assertIn(('Проверил', 'Омаров'), rules.signers(row, {5: 'Нурланова'}))


class RoleTests(PilotOff):
    """ТЗ, раздел 2: кто что делает."""

    def test_roles_come_from_departments(self):
        self.assertTrue(access.is_accounting(ACCOUNTANT) and access.is_keeper(ACCOUNTANT))
        self.assertTrue(access.is_hr(HR) and access.can_run_dismissal(HR))
        self.assertTrue(access.is_it(IT))
        self.assertFalse(access.is_keeper(HR) or access.is_keeper(IT))

    def test_analytics_role_belongs_to_the_head_only(self):
        self.assertTrue(access.is_observer(ANALYTICS_HEAD) and access.sees_all(ANALYTICS_HEAD))
        self.assertFalse(access.is_observer(ANALYST) or access.can_open(ANALYST))

    def test_named_members_extend_departments(self):
        named = ctx(30, department='marketing', member=(rules.MEMBER_IT,))
        self.assertTrue(access.is_it(named) and access.can_open(named))

    def test_who_opens_the_section(self):
        for person in (SUPER, ACCOUNTANT, HR, IT, ANALYTICS_HEAD, SALES_HEAD, HOLDER,
                       ctx(40, department='marketing', involved=True)):
            with self.subTest(person=person['user_id']):
                self.assertTrue(access.can_open(person))
        self.assertFalse(access.can_open(OUTSIDER))
        self.assertFalse(access.can_open(None))

    def test_tabs(self):
        # МОЛ — «Мои ОС» и своя история; вкладки «Запросы» у него нет: запросы он не ведёт.
        self.assertEqual(access.tabs(HOLDER), ['my', 'history'])
        self.assertEqual(access.tabs(ctx(41, department='op', involved=True)), ['my', 'history'])
        self.assertEqual(access.tabs(SALES_HEAD), ['registry', 'requests', 'history'])
        self.assertEqual(access.tabs(dict(SALES_HEAD, has_assets=True)), ['my', 'registry', 'requests', 'history'])
        self.assertEqual(access.tabs(IT), ['registry', 'requests', 'history'])
        self.assertEqual(access.tabs(HR), ['registry', 'requests', 'dismissal', 'history'])
        self.assertEqual(access.tabs(ACCOUNTANT), ['registry', 'requests', 'history', 'settings'])
        self.assertEqual(access.tabs(ANALYTICS_HEAD), ['registry', 'requests', 'history'])
        self.assertEqual(access.tabs(SUPER), ['registry', 'requests', 'dismissal', 'history', 'settings'])
        self.assertEqual(access.tabs(dict(ACCOUNTANT, has_assets=True))[0], 'my')

    def test_only_accounting_takes_assets_on_the_books(self):
        # «Бухгалтерия создаёт карточку ОС… Другим способом ОС в систему не попадает».
        self.assertTrue(access.can_intake(ACCOUNTANT) and access.can_intake(SUPER))
        for person in (HR, IT, ANALYTICS_HEAD, SALES_HEAD, HOLDER):
            self.assertFalse(access.can_intake(person))

    def test_who_creates_which_request(self):
        self.assertEqual(access.creatable_kinds(HOLDER), [])                # МОЛ запросов не заводит
        self.assertEqual(access.creatable_kinds(dict(SALES_HEAD, has_assets=True)), ['transfer', 'writeoff'])
        self.assertEqual(access.creatable_kinds(SALES_HEAD), ['transfer', 'writeoff'])
        self.assertEqual(access.creatable_kinds(IT), ['transfer', 'writeoff', 'need'])
        self.assertEqual(access.creatable_kinds(HR), ['transfer', 'dismissal'])
        self.assertEqual(access.creatable_kinds(ACCOUNTANT), ['transfer', 'writeoff'])
        self.assertEqual(access.creatable_kinds(SUPER), ['transfer', 'writeoff', 'dismissal', 'need'])
        self.assertEqual(access.creatable_kinds(OUTSIDER), [])

    def test_which_asset_goes_into_whose_request(self):
        own = {'status': 'active', 'mol_user_id': 8, 'department_id': 5}
        foreign = {'status': 'active', 'mol_user_id': 99, 'department_id': 6}
        for kind in ('transfer', 'writeoff'):                                # своё ОС права не даёт
            self.assertFalse(access.can_pick_asset(HOLDER, own, kind))
            self.assertFalse(access.can_pick_asset(HOLDER, foreign, kind))
        self.assertTrue(access.can_pick_asset(SALES_HEAD, own, 'writeoff'))
        self.assertFalse(access.can_pick_asset(SALES_HEAD, foreign, 'writeoff'))
        for person in (IT, ACCOUNTANT):
            self.assertTrue(access.can_pick_asset(person, foreign, 'transfer'))
            self.assertTrue(access.can_pick_asset(person, foreign, 'writeoff'))
        self.assertTrue(access.can_pick_asset(HR, foreign, 'transfer'))
        self.assertFalse(access.can_pick_asset(HR, foreign, 'writeoff'))
        self.assertFalse(access.can_pick_asset(ACCOUNTANT, dict(own, status='written_off'), 'transfer'))

    def test_card_levels(self):
        asset = {'mol_user_id': 8, 'department_id': 5}
        for person in (SUPER, ACCOUNTANT, HR, ANALYTICS_HEAD):
            self.assertEqual(access.asset_level(person, asset), access.LEVEL_FULL)
        self.assertEqual(access.asset_level(HOLDER, asset), access.LEVEL_OWN)
        self.assertEqual(access.asset_level(SALES_HEAD, asset), access.LEVEL_SCOPED)
        # Руководитель, за которым числится ОС его же отдела, видит его как руководитель.
        self.assertEqual(access.asset_level(SALES_HEAD, {'mol_user_id': 7, 'department_id': 5}), access.LEVEL_SCOPED)
        self.assertEqual(access.asset_level(dict(IT, has_assets=True), {'mol_user_id': 4, 'department_id': 3}),
                         access.LEVEL_OWN)
        # IT: «наличие на складе ОС и МОЛ» — без истории и стоимости.
        self.assertEqual(access.asset_level(IT, asset), access.LEVEL_BRIEF)
        self.assertIsNone(access.asset_level(OUTSIDER, asset))
        self.assertIsNone(access.asset_level(ctx(50, department='op', has_assets=True), asset))

    def test_cost_is_shown_only_to_those_who_see_everything(self):
        self.assertTrue(access.can_see_cost(ACCOUNTANT) and access.can_see_cost(ANALYTICS_HEAD))
        self.assertFalse(access.can_see_cost(IT) or access.can_see_cost(HOLDER) or access.can_see_cost(SALES_HEAD))

    def test_correction_only_while_intake_is_open(self):
        self.assertTrue(access.can_correct(ACCOUNTANT, {'intake_open': True}))
        self.assertFalse(access.can_correct(ACCOUNTANT, {'intake_open': False}))
        self.assertFalse(access.can_correct(HR, {'intake_open': True}))


class RequestAccessTests(PilotOff):
    def test_draft_is_seen_by_its_author_only(self):
        draft = request_row(status=rules.STATUS_DRAFT)
        self.assertTrue(access.can_view_request(SALES_HEAD, draft))
        self.assertTrue(access.can_view_request(SUPER, draft))
        for person in (ACCOUNTANT, HR, HOLDER, IT, MARKETING_HEAD):
            self.assertFalse(access.can_view_request(person, draft))

    def test_who_sees_a_sent_request(self):
        row = request_row()
        for person in (ACCOUNTANT, HR, ANALYTICS_HEAD, HOLDER, SALES_HEAD, RECEIVER, MARKETING_HEAD):
            with self.subTest(person=person['user_id']):
                self.assertTrue(access.can_view_request(person, row))
        self.assertFalse(access.can_view_request(IT, row))
        self.assertFalse(access.can_view_request(ctx(70, department='op', has_assets=True), row))
        self.assertTrue(access.can_view_request(IT, request_row(kind=rules.KIND_WRITEOFF, status='review', review_by='it')))
        self.assertTrue(access.can_view_request(IT, request_row(kind=rules.KIND_NEED, status='review', created_by=4)))

    def test_draft_actions(self):
        draft = request_row(status=rules.STATUS_DRAFT)
        self.assertEqual(access.allowed_actions(SALES_HEAD, draft), ['edit', 'submit', 'delete'])
        self.assertEqual(access.allowed_actions(ACCOUNTANT, draft), [])
        self.assertEqual(access.allowed_actions(HOLDER, draft), [])

    def test_writeoff_is_checked_only_by_its_checker(self):
        by_it = request_row(kind=rules.KIND_WRITEOFF, status='review', review_by='it')
        self.assertEqual(access.allowed_actions(IT, by_it), ['check', 'reject'])
        self.assertEqual(access.allowed_actions(SALES_HEAD, by_it), ['reject'])   # автор может отозвать
        self.assertEqual(access.allowed_actions(HOLDER, by_it), [])
        self.assertEqual(access.allowed_actions(ACCOUNTANT, by_it), ['reject'])
        by_head = request_row(kind=rules.KIND_WRITEOFF, status='review', review_by='head', review_department_id=5,
                              created_by=4)
        self.assertEqual(access.allowed_actions(SALES_HEAD, by_head), ['check', 'reject'])
        self.assertNotIn('check', access.allowed_actions(MARKETING_HEAD, by_head))
        self.assertEqual(access.allowed_actions(IT, by_head), ['reject'])         # автор, но не проверяющий

    def test_need_is_accepted_by_accounting(self):
        need = request_row(kind=rules.KIND_NEED, status='review', review_by='accounting', created_by=4,
                           from_user_id=None, to_user_id=None, from_department_id=None, to_department_id=None)
        self.assertEqual(access.allowed_actions(ACCOUNTANT, need), ['accept', 'reject'])
        self.assertEqual(access.allowed_actions(IT, need), ['reject'])
        self.assertEqual(access.allowed_actions(HR, need), [])

    def test_signing_step(self):
        row = request_row()
        self.assertEqual(access.allowed_actions(SALES_HEAD, row), ['act', 'scan', 'reject'])       # автор и руководитель
        self.assertEqual(access.allowed_actions(MARKETING_HEAD, row), ['act', 'scan', 'reject'])   # руководитель стороны
        self.assertEqual(access.allowed_actions(ACCOUNTANT, row), ['act', 'scan', 'reject'])
        self.assertEqual(access.allowed_actions(ANALYTICS_HEAD, row), ['act'])    # только чтение
        self.assertEqual(access.allowed_actions(HR, row), ['act'])
        # Сдающий и принимающий подписывают бумагу — в системе у них только акт для печати.
        self.assertEqual(access.allowed_actions(HOLDER, row), ['act'])
        self.assertEqual(access.allowed_actions(RECEIVER, row), ['act'])
        dismissal = request_row(kind=rules.KIND_DISMISSAL, created_by=3)
        self.assertIn('scan', access.allowed_actions(HR, dismissal))
        self.assertEqual(access.allowed_actions(HOLDER, dismissal), ['act'])      # увольняющийся скан не прикладывает

    def test_intake_is_never_rejected(self):
        intake = request_row(kind=rules.KIND_INTAKE, created_by=2, from_user_id=None, from_department_id=None)
        self.assertEqual(access.allowed_actions(ACCOUNTANT, intake), ['act', 'scan'])
        waiting = dict(intake, status=rules.STATUS_ORIGINAL)
        self.assertEqual(access.allowed_actions(ACCOUNTANT, waiting), ['act', 'receive', 'rescan'])

    def test_original_is_received_by_accounting_only(self):
        row = request_row(status=rules.STATUS_ORIGINAL)
        self.assertEqual(access.allowed_actions(ACCOUNTANT, row), ['act', 'receive', 'reject', 'rescan'])
        for person in (HOLDER, RECEIVER, SALES_HEAD, MARKETING_HEAD, HR, ANALYTICS_HEAD):
            self.assertEqual(access.allowed_actions(person, row), ['act'])

    def test_wrong_scan_is_replaced_by_its_uploader_or_accounting(self):
        row = request_row(status=rules.STATUS_ORIGINAL, scan_by=7)
        self.assertEqual(access.allowed_actions(SALES_HEAD, row), ['act', 'rescan'])
        self.assertEqual(access.allowed_actions(MARKETING_HEAD, row), ['act'])
        self.assertEqual(access.allowed_actions(HOLDER, row), ['act'])
        self.assertFalse(access.can_replace_scan(ACCOUNTANT, request_row(status=rules.STATUS_SIGNING)))
        self.assertFalse(access.can_replace_scan(ACCOUNTANT, request_row(status=rules.STATUS_CLOSED)))

    def test_closed_and_rejected_requests_are_read_only(self):
        self.assertEqual(access.allowed_actions(ACCOUNTANT, request_row(status=rules.STATUS_CLOSED)), ['act'])
        self.assertEqual(access.allowed_actions(ACCOUNTANT, request_row(status=rules.STATUS_REJECTED)), [])

    def test_waiting_for_me(self):
        self.assertTrue(access.is_waiting(SALES_HEAD, request_row(status=rules.STATUS_DRAFT)))
        review = request_row(kind=rules.KIND_WRITEOFF, status='review', review_by='it')
        self.assertTrue(access.is_waiting(IT, review))
        self.assertFalse(access.is_waiting(SUPER, review))          # чужие проверки суперадмину не сыплются
        self.assertFalse(access.is_waiting(SALES_HEAD, review))
        self.assertTrue(access.is_waiting(SALES_HEAD, request_row()))
        self.assertTrue(access.is_waiting(MARKETING_HEAD, request_row()))
        self.assertFalse(access.is_waiting(ACCOUNTANT, request_row()))
        original = request_row(status=rules.STATUS_ORIGINAL)
        self.assertTrue(access.is_waiting(ACCOUNTANT, original))
        self.assertFalse(access.is_waiting(SALES_HEAD, original))
        self.assertFalse(access.is_waiting(ACCOUNTANT, request_row(status=rules.STATUS_CLOSED)))


class HolderOnlyWatchesTests(PilotOff):
    """Решение владельца 17.11.2026: «у МОЛ не должно быть возможности составлять запросы или
    отклонять их, а лишь смотреть за своим ОС и историю, где участвует он с этим ОС»."""

    def test_holder_never_gets_an_action_button(self):
        for kind in rules.KINDS:
            for status in rules.STATUSES:
                for review_by in (None, 'it', 'head', 'accounting'):
                    row = request_row(kind=kind, status=status, review_by=review_by, review_department_id=6,
                                      scan_by=7, need_for_user_id=8)
                    for person in (HOLDER, RECEIVER):
                        with self.subTest(kind=kind, status=status, review_by=review_by, person=person['user_id']):
                            # «act» — скачать акт для печати: это просмотр, а не шаг запроса.
                            self.assertLessEqual(set(access.allowed_actions(person, row)), {'act'})
                            self.assertFalse(access.is_waiting(person, row))
                            self.assertFalse(access.can_attach_scan(person, row))
                            self.assertFalse(access.can_replace_scan(person, row))

    def test_holder_creates_nothing_and_manages_nothing(self):
        for person in (HOLDER, RECEIVER):
            self.assertEqual(access.creatable_kinds(person), [])
            self.assertFalse(access.can_intake(person) or access.can_edit_settings(person)
                             or access.can_run_dismissal(person) or access.works_with_requests(person))
            self.assertNotIn('requests', access.tabs(person))
        self.assertEqual(access.capabilities(HOLDER)['create_kinds'], [])

    def test_holder_still_sees_requests_about_own_assets(self):
        self.assertTrue(access.can_view_request(HOLDER, request_row()))
        self.assertTrue(access.can_view_request(RECEIVER, request_row(status=rules.STATUS_CLOSED)))

    def test_being_a_holder_takes_nothing_from_a_role(self):
        # Права складываются: бухгалтер или руководитель, за которым числится ОС, работает как прежде.
        for person in (ACCOUNTANT, SALES_HEAD, IT, HR):
            with_assets = dict(person, has_assets=True)
            self.assertEqual(access.creatable_kinds(with_assets), access.creatable_kinds(person))
            self.assertEqual(access.allowed_actions(with_assets, request_row()), access.allowed_actions(person, request_row()))


class PilotTests(unittest.TestCase):
    def test_section_is_in_pilot_until_the_owner_opens_it(self):
        # Раздел выкладывается закрытым: пока владелец не снял флаг, его видят только суперадмины.
        self.assertIs(access.PILOT_SUPER_ADMIN_ONLY, True)

    def test_pilot_closes_the_section_for_everyone_but_super_admins(self):
        self.assertTrue(access.can_open(SUPER))
        for person in (ACCOUNTANT, HR, IT, SALES_HEAD, HOLDER):
            self.assertFalse(access.can_open(person))
        self.assertEqual(access.allowed_actions(ACCOUNTANT, request_row(status=rules.STATUS_ORIGINAL)), [])


class ActTests(unittest.TestCase):
    ITEMS = [{'name': 'Ноутбук Lenovo', 'inventory_number': 'ОС-000101', 'serial_number': 'PF4K2M7N',
              'category_name': 'Компьютеры и ноутбуки', 'before_department_name': 'Отдел продаж',
              'before_location': 'каб. 204', 'before_on_stock': False,
              'asset': {'cost': Decimal('141210.00'), 'acquired_on': date(2023, 4, 13)}}]
    REQUISITES = {
        'org_name': 'ТОО «Образец»', 'org_bin': '123456789012', 'head_name': 'Директоров Дамир Маратович',
        'commission_order_number': '1', 'commission_order_date': '2025-11-28',
        'chairman_title': 'Генеральный директор', 'chairman_name': 'Директоров Дамир Маратович',
        'members': [{'title': 'Главный бухгалтер', 'name': 'Главбухова Гульмира Сериковна'}],
        'chief_accountant_name': 'Главбухова Гульмира Сериковна',
    }

    def _text(self, content):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read('word/document.xml').decode('utf-8')
        return ' '.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', xml))

    def _writeoff(self, **fields):
        row = request_row(id=7, kind=rules.KIND_WRITEOFF, from_name='Ахметов Тимур Русланович', act_date=date(2025, 12, 1),
                          comment='Износ ОС', checked_by=4, checked_by_name='Омаров Данияр Кайратович',
                          check_comment='Плата сгорела', review_by='it')
        row.update(fields)
        return row

    def test_every_kind_builds_a_word_act(self):
        for kind in rules.ACT_TITLES:
            row = request_row(id=31, kind=kind, from_name='Ахметов Тимур Русланович', to_name='Садыкова Камила',
                              act_date=TODAY, comment='Причина', from_department_name='Отдел продаж',
                              to_department_name='Отдел продаж', to_location='каб. 206')
            content = acts.build(row, self.ITEMS, rules.signers(row, {5: 'Нурланова Алия'}))
            text = self._text(content)
            with self.subTest(kind=kind):
                self.assertTrue(content.startswith(b'PK'))
                self.assertIn('ОС-000101', text)
                if kind == rules.KIND_WRITEOFF:
                    self.assertIn('Форма № ДА-3', text)
                    self.assertIn('17.11.2026', text)
                else:
                    self.assertIn('АКТ № 31', text)
                    self.assertIn('«17» ноября 2026 г.', text)

    def test_organization_comes_from_settings_not_from_code(self):
        row = request_row(id=31, from_name='Ахметов', to_name='Садыкова', act_date=TODAY)
        blank = self._text(acts.build(row, self.ITEMS, []))
        self.assertIn('Организация: ____', blank)
        named = self._text(acts.build(row, self.ITEMS, [], requisites=self.REQUISITES))
        self.assertIn('Организация: ТОО «Образец», БИН 123456789012', named)
        # В открытом репозитории нет ни названия компании, ни БИН, ни фамилий из образца бухгалтерии.
        # Сами слова здесь только хешами: иначе тест сам выложил бы то, от чего сторожит.
        source = (ROOT / 'fixed_assets/acts.py').read_text(encoding='utf-8').lower()
        words = re.findall(r'\w+', source)
        grams = set(words) | {' '.join(pair) for pair in zip(words, words[1:])}
        self.assertFalse({hashlib.sha256(gram.encode('utf-8')).hexdigest()[:16] for gram in grams} & SAMPLE_ACT_WORDS)

    def test_free_form_acts_print_on_a4(self):
        import docx

        for kind in (rules.KIND_INTAKE, rules.KIND_TRANSFER, rules.KIND_DISMISSAL):
            row = request_row(id=31, kind=kind, from_name='Ахметов', to_name='Садыкова', act_date=TODAY)
            section = docx.Document(io.BytesIO(acts.build(row, self.ITEMS, []))).sections[0]
            with self.subTest(kind=kind):
                self.assertEqual((round(section.page_width.cm, 1), round(section.page_height.cm, 1)), (21.0, 29.7))

    def test_writeoff_act_is_form_da3(self):
        text = self._text(acts.build(self._writeoff(), self.ITEMS, [], {8: {'job_title': 'Менеджер'}},
                                     requisites=self.REQUISITES, with_cost=True, heads={5: 'Нурланова Алия Бауыржановна'}))
        for piece in ('Приложение 18', 'от 20 декабря 2012 года № 562', 'Форма № ДА-3', 'ТОО «Образец»', '123456789012',
                      'УТВЕРЖДАЮ', 'Директоров Д. М.', 'НА ВЫБЫТИЕ (СПИСАНИЕ) ДОЛГОСРОЧНЫХ АКТИВОВ', '01.12.2025',
                      'Комиссия, назначенная приказом от 28.11.2025 года № 1', 'и установила его (их) надлежащим списанию.',
                      'Износ ОС', 'Плата сгорела', 'Оборотная сторона формы ДА-3', 'Председатель комиссии:',
                      'Члены комиссии:', 'Материально-ответственное лицо:', 'Главный бухгалтер (бухгалтер)',
                      'В карточке (книге) учета выбытие отмечено', 'Ахметов Т. Р.', 'Главбухова Г. С.'):
            with self.subTest(piece=piece):
                self.assertIn(piece, text)

    def test_da3_row_follows_the_accounting_sample(self):
        # Образец бухгалтерии: принято 13.04.2023, акт 01.12.2025 — срок 32, стоимость «141 210,00».
        self.assertEqual(acts.da3_rows(self._writeoff(), self.ITEMS, with_cost=True), [
            ['1', 'Ноутбук Lenovo', '', '13 апреля 2023 г.', '32', '141 210,00', '', '', 'PF4K2M7N', 'ОС-000101']])
        self.assertEqual(len(acts.DA3_HEADINGS), 10)
        self.assertEqual(len(acts.DA3_HEADING_LINES), 10)
        for plain, lined in zip(acts.DA3_HEADINGS, acts.DA3_HEADING_LINES):
            self.assertEqual(lined.replace('-\n', '').replace('\n', ' '), plain)

    def test_cost_is_printed_only_for_those_who_see_it(self):
        hidden = acts.da3_rows(self._writeoff(), self.ITEMS, with_cost=False)[0]
        self.assertEqual(hidden[5], '')
        text = self._text(acts.build(self._writeoff(), self.ITEMS, []))
        self.assertNotIn('141 210,00', text)

    def test_service_months_ignore_the_day_like_the_sample(self):
        self.assertEqual(rules.service_months(date(2023, 4, 13), date(2025, 12, 1)), 32)
        self.assertEqual(rules.service_months(date(2026, 11, 1), date(2026, 11, 30)), 0)
        self.assertIsNone(rules.service_months(None, TODAY))
        self.assertIsNone(rules.service_months(date(2026, 12, 1), TODAY))

    def test_commission_adds_the_checker_and_the_head_once(self):
        people = {8: {'job_title': 'Менеджер'}, 4: {'job_title': 'Системный администратор'}}
        heads = {5: 'Нурланова Алия Бауыржановна'}
        commission = acts.da3_commission(self._writeoff(), self.REQUISITES, people, heads)
        self.assertEqual(commission['chairman'], ('Генеральный директор', 'Директоров Д. М.'))
        self.assertEqual(commission['members'], [
            ('Главный бухгалтер', 'Главбухова Г. С.'), ('Системный администратор', 'Омаров Д. К.'),
            ('Руководитель отдела', 'Нурланова А. Б.')])
        self.assertEqual(commission['mol'], ('Менеджер', 'Ахметов Т. Р.'))
        # Проверял сам руководитель отдела — во второй раз в комиссию он не попадает.
        by_head = self._writeoff(review_by='head', checked_by=7, checked_by_name='Нурланова Алия Бауыржановна')
        names = [name for _title, name in acts.da3_commission(by_head, {}, {}, heads)['members']]
        self.assertEqual(names, ['Нурланова А. Б.'])
        # Без настроек и проверки комиссия пустая — строки на бумаге остаются для подписи от руки.
        empty = acts.da3_commission(self._writeoff(checked_by_name=None), {}, {}, {})
        self.assertEqual((empty['chairman'], empty['members']), (('', ''), []))

    def test_order_line_keeps_blanks_until_settings_are_filled(self):
        self.assertEqual(acts.da3_order_line({}), 'Комиссия, назначенная приказом от «___» __________ 20___ года № _____')
        self.assertEqual(acts.da3_order_line(self.REQUISITES), 'Комиссия, назначенная приказом от 28.11.2025 года № 1')

    def test_names_for_signature_lines(self):
        self.assertEqual(acts.short_name('Ахметов Тимур Русланович'), 'Ахметов Т. Р.')
        self.assertEqual(acts.short_name('Ли'), 'Ли')
        self.assertEqual(acts.file_name({'kind': rules.KIND_WRITEOFF, 'id': 7}), 'Акт списания № 7.docx')
        self.assertEqual(acts.money(Decimal('42400')), '42 400,00')
        self.assertEqual(acts.money(38500.5), '38 500,50')
        self.assertEqual(acts.money(None), '')


class RequisitesTests(unittest.TestCase):
    def test_everything_is_optional(self):
        clean = rules.clean_requisites({})
        self.assertEqual(clean['members'], [])
        self.assertTrue(all(clean[key] is None for key, _label, _limit in rules.REQUISITE_TEXTS))
        self.assertIsNone(clean['org_bin'])
        self.assertIsNone(clean['commission_order_date'])

    def test_bin_is_twelve_digits(self):
        self.assertEqual(rules.clean_requisites({'org_bin': '123 456 789 012'})['org_bin'], '123456789012')
        for value in ('12345', '1234567890123', 'БИН-123456789'):
            with self.subTest(value=value), self.assertRaises(rules.FaError) as caught:
                rules.clean_requisites({'org_bin': value})
            self.assertEqual(caught.exception.code, 'FA_BIN_INVALID')

    def test_commission_drops_empty_rows_and_has_a_limit(self):
        rows = [{'title': ' Главный бухгалтер ', 'name': ' Главбухова '}, {'title': '', 'name': ''}, {'name': 'Юрист'}]
        self.assertEqual(rules.clean_requisites({'members': rows})['members'], [
            {'title': 'Главный бухгалтер', 'name': 'Главбухова'}, {'title': None, 'name': 'Юрист'}])
        crowd = [{'name': 'Человек %d' % number} for number in range(rules.MAX_COMMISSION_MEMBERS + 1)]
        with self.assertRaises(rules.FaError):
            rules.clean_requisites({'members': crowd})
        with self.assertRaises(rules.FaError):
            rules.clean_requisites({'members': 'все'})

    def test_order_date_is_parsed(self):
        self.assertEqual(rules.clean_requisites({'commission_order_date': '28.11.2025'})['commission_order_date'],
                         date(2025, 11, 28))


class SchemaTests(unittest.TestCase):
    def test_statements_are_fully_formatted(self):
        statements = schema.statements()
        text = '\n'.join(statements)
        for table in ('fa_categories', 'fa_members', 'fa_assets', 'fa_requests', 'fa_request_items', 'fa_files',
                      'fa_events', 'fa_settings'):
            self.assertIn('CREATE TABLE IF NOT EXISTS %s' % table, text)
        self.assertNotRegex(text, r'\{[a-z_]+\}')          # ни одной неподставленной метки
        for value in rules.KINDS + rules.STATUSES + rules.FILE_KINDS + rules.MEMBER_ROLES:
            self.assertIn("'%s'" % value, text)

    def test_there_is_no_hr_call(self):
        # Решение владельца 17.11.2026: «момент, когда человек зовёт HR, лучше убрать».
        self.assertNotIn('fa_hr_calls', '\n'.join(schema.statements()))
        self.assertNotIn('hr_called', rules.EVENT_LABELS)
        for name in ('routes', 'queries', 'access', 'rules'):
            source = (ROOT / ('fixed_assets/%s.py' % name)).read_text(encoding='utf-8')
            for word in ('dismissals/call', 'hr_call', 'open_call', 'create_call'):
                self.assertNotIn(word, source, name)
        for path in sorted((ROOT / 'src/components/fixed_assets').iterdir()):
            source = path.read_text(encoding='utf-8')
            for word in ('dismissals/call', 'Вызвать HR', 'вызвал HR', 'hr_called', 'Увольняетесь'):
                self.assertNotIn(word, source, path.name)

    def test_history_is_append_only_at_database_level(self):
        # ТЗ: «Записи нельзя редактировать или удалять» — правило держит триггер, а не только код.
        text = '\n'.join(schema.statements())
        self.assertIn('BEFORE UPDATE OR DELETE ON fa_events', text)
        self.assertIn('BEFORE TRUNCATE ON fa_events', text)
        self.assertIn('RAISE EXCEPTION', text)

    def test_history_has_no_foreign_keys(self):
        # Каскад от удалённого сотрудника был бы правкой строки истории.
        events = next(item for item in schema.statements() if 'CREATE TABLE IF NOT EXISTS fa_events' in item)
        self.assertNotIn('REFERENCES', events)

    def test_no_route_changes_holder_outside_a_request(self):
        # «Нет обхода»: МОЛ, место и статус ОС пишет только закрытие запроса.
        routes = (ROOT / 'fixed_assets/routes.py').read_text(encoding='utf-8')
        sql = (ROOT / 'fixed_assets/queries.py').read_text(encoding='utf-8')
        self.assertNotIn('mol_user_id', ''.join(rules.ASSET_CORRECTABLE))
        self.assertEqual(sql.count('SET mol_user_id'), 1)                    # только apply_move
        self.assertEqual(sql.count("SET status = 'written_off'"), 1)        # только apply_writeoff
        self.assertEqual(routes.count('queries.apply_move('), 1)
        self.assertEqual(routes.count('queries.apply_writeoff('), 1)
        receive = routes.split("def fa_request_original(", 1)[1].split('@route(', 1)[0]
        self.assertIn('queries.apply_move(', receive)
        self.assertIn('queries.apply_writeoff(', receive)


class ReportTests(unittest.TestCase):
    ASSET = {'inventory_number': '000457', 'name': 'Ноутбук', 'category_name': 'Компьютеры', 'serial_number': '0012',
             'status_label': 'На учёте', 'mol_name': 'Ахметов', 'department_name': 'Отдел продаж', 'location': 'каб. 204',
             'on_stock': False, 'acquired_on': TODAY, 'cost': Decimal('389900.00'), 'open_request': None}

    def test_cost_column_only_for_those_who_see_it(self):
        with_cost, rows = report.asset_rows([self.ASSET], with_cost=True)
        self.assertIn('Стоимость, ₸', [title for title, _width in with_cost])
        self.assertIn(389900.0, rows[0])
        without, rows = report.asset_rows([self.ASSET], with_cost=False)
        self.assertNotIn('Стоимость, ₸', [title for title, _width in without])
        self.assertNotIn(389900.0, rows[0])

    def test_book_has_registry_and_history(self):
        from openpyxl import load_workbook

        event = {'kind': 'moved', 'label': 'Перемещено', 'created_at': TODAY, 'actor_name': 'Сейткали', 'request_id': 5,
                 'request_kind_label': 'Перемещение', 'asset_name': 'Ноутбук', 'inventory_number': '000457',
                 'payload': {'from': {'mol_name': 'Ахметов'}, 'to': {'mol_name': 'Садыкова'}}}
        book = load_workbook(io.BytesIO(report.build([self.ASSET], [event], with_cost=True)))
        self.assertEqual(book.sheetnames, ['Реестр ОС', 'История'])
        self.assertEqual(book['Реестр ОС']['A2'].value, '000457')          # номер текстом, нули целы
        self.assertEqual(book['История']['G2'].value, 'Ахметов → Садыкова')

    def test_registry_formats_follow_headers_not_column_numbers(self):
        # Столбец «Штрихкод» сдвинул стоимость на одно место: дата не должна стать деньгами.
        from openpyxl import load_workbook

        asset = dict(self.ASSET, barcode='2000000056784')
        sheet = load_workbook(io.BytesIO(report.build([asset], [], with_cost=True)))['Реестр ОС']
        cells = {sheet.cell(1, column).value: sheet.cell(2, column) for column in range(1, sheet.max_column + 1)}
        self.assertEqual(cells['Штрихкод'].value, '2000000056784')
        self.assertEqual(cells['Принято к учёту'].number_format, 'DD.MM.YYYY')
        self.assertEqual(cells['Стоимость, ₸'].number_format, '#,##0.00')
        self.assertEqual(cells['Стоимость, ₸'].value, 389900.0)

    def test_history_export_says_which_sticker_was_bound(self):
        base = {'kind': 'barcode_set', 'created_at': TODAY}
        self.assertEqual(report.event_text(dict(base, payload={'from': None, 'to': '2000000056784'})), '2000000056784')
        self.assertEqual(report.event_text(dict(base, payload={'from': '2000000056784', 'to': '2000000001012'})),
                         '2000000056784 → 2000000001012')
        self.assertEqual(report.event_text(dict(base, payload={'from': '2000000056784', 'to': None})), '2000000056784')


class FrontendMirrorTests(unittest.TestCase):
    """Экран называет коды теми же словами, что сервер: иначе статус в списке и в акте разошлись бы."""

    def test_kind_and_status_labels(self):
        self.assertEqual(js_object('KIND_LABELS'), rules.KIND_LABELS)
        self.assertEqual(js_object('STATUS_LABELS'), rules.STATUS_LABELS)
        self.assertEqual(js_object('CHECK_BY_LABELS'), rules.CHECK_GROUP_LABELS)

    def test_every_status_has_a_tone_and_an_order(self):
        self.assertEqual(set(js_object('STATUS_TONE')), set(rules.STATUSES))
        order = re.search(r"STATUS_ORDER = \[([^\]]+)\]", META).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", order), list(rules.STATUSES))

    def test_photo_kinds(self):
        for name, value in (('PHOTO_WHOLE', rules.PHOTO_WHOLE), ('PHOTO_NUMBER', rules.PHOTO_NUMBER),
                            ('PHOTO_EXTRA', rules.PHOTO_EXTRA)):
            self.assertIn("export const %s = '%s';" % (name, value), META)

    def test_registry_slices_and_tabs(self):
        slices = re.search(r'ASSET_SLICES = \[(.*?)\];', META, re.S).group(1)
        self.assertEqual(set(re.findall(r"key: '(\w+)'", slices)), set(queries.ASSET_SLICES))
        self.assertEqual(set(re.findall(r'^    (\w+): \{ title', META, re.M)), set(access.TAB_ORDER))

    def test_event_filter_offers_only_real_events(self):
        options = re.search(r'EVENT_OPTIONS = \[(.*?)\];', META, re.S).group(1)
        values = [value for value in re.findall(r"value: '(\w*)'", options) if value]
        self.assertTrue(values)
        self.assertLessEqual(set(values), set(rules.EVENT_LABELS))

    def test_creatable_kinds_have_menu_items(self):
        self.assertEqual(set(re.findall(r"^    (\w+): \{ label", META, re.M)), set(rules.REQUESTABLE_KINDS))


def function_source(name):
    return ast.unparse(source_cache.function_node(BOT_PATH, name))


class PortalWiringTests(unittest.TestCase):
    """Раздел подключён к порталу во всех местах, где это делают соседние разделы."""

    def test_profile_carries_the_flag(self):
        payload = function_source('_get_user_payload')
        self.assertIn('fixed_assets_access = _fixed_assets_section_open_for(user_id)', payload)
        self.assertIn("'fixed_assets_access': fixed_assets_access", payload)
        self.assertIn("const canAccessFixedAssetsForUser = (userLike) => userLike?.fixed_assets_access === true;", APP)

    def test_menu_item_is_declared_once_for_all_roles(self):
        self.assertEqual(APP.count("handleSidebarViewNavigation(e, 'fixed_assets')"), 1)
        self.assertEqual(APP.count('<span className="sidebar-text">Основные средства</span>'), 1)
        self.assertIn("'fa-computer': 'Computer'", FA_ICON)
        self.assertEqual(FA_ICON.count('    Computer,'), 2)        # импорт и реестр компонентов

    def test_screen_guards_and_render(self):
        self.assertIn("const FixedAssetsView = lazyWithRetry(() => import('./components/fixed_assets/FixedAssetsView'));", APP)
        self.assertEqual(APP.count("{view === 'fixed_assets' && canAccessFixedAssetsSection && ("), 1)
        self.assertIn("(requestedViewFromUrl !== 'fixed_assets' || canAccessFixedAssetsSection) &&", APP)
        self.assertIn("if (view === 'fixed_assets' && !canAccessFixedAssetsSection) {", APP)
        self.assertIn("if (view === 'fixed_assets' && canAccessFixedAssetsSection) return;", APP)
        self.assertIn('|| canAccessFixedAssetsSection', APP)
        self.assertIn("    'fixed_assets',", APP.split('const TRAINER_ALLOWED_VIEWS', 1)[1].split(']);', 1)[0])

    def test_section_marks_leave_the_address_with_the_section(self):
        line = "if (nextView !== 'fixed_assets') ['fa_tab', 'fa_request', 'fa_asset'].forEach((name) => url.searchParams.delete(name));"
        self.assertEqual(APP.count(line), 2)                       # buildAppViewUrl и syncAppViewWithUrl
        view = (ROOT / 'src/components/fixed_assets/FixedAssetsView.jsx').read_text(encoding='utf-8')
        for name in ('fa_tab', 'fa_request', 'fa_asset'):
            self.assertIn("'%s'" % name, view)

    def test_dismissal_is_held_in_both_portal_paths(self):
        # ТЗ: «Приказ в HR недоступен, пока у сотрудника есть ОС без подписанного акта».
        update = function_source('admin_update_user')
        self.assertIn("if value in ('fired', 'dismissal'):", update)
        self.assertIn('_fixed_assets_dismissal_block(user_id)', update)
        period = function_source('save_work_schedule_status_period')
        self.assertIn('_fixed_assets_dismissal_block(int(target_operator[0]))', period)
        self.assertLess(period.index('_fixed_assets_dismissal_block'), period.index('db.save_schedule_status_period('))

    def test_pilot_section_does_not_hold_dismissals(self):
        block = function_source('_fixed_assets_dismissal_block')
        self.assertIn('if fixed_assets_access.PILOT_SUPER_ADMIN_ONLY:', block)
        self.assertLess(block.index('PILOT_SUPER_ADMIN_ONLY'), block.index('dismissal_blockers'))


if __name__ == '__main__':
    unittest.main()
