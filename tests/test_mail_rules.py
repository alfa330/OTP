# -*- coding: utf-8 -*-
"""«Почта» (#417): чистые правила и права — без базы и Flask.

Весь серверный путь на настоящем Postgres — tests/test_mail_postgres.py. Здесь: рабочие дни
и сроки (ТЗ п. 7), проверка полей (п. 3, 14), построчная сверка (п. 6), права ролей (п. 11),
и что фронт называет коды теми же словами, что сервер (иначе экран и Excel разошлись бы).
"""
import re
import unittest
from unittest import mock
from datetime import date, datetime
from pathlib import Path

from mail import access, report, rules

ROOT = Path(__file__).resolve().parents[1]
META = (ROOT / 'src/components/mail/mailMeta.js').read_text(encoding='utf-8')
NO_HOLIDAYS = frozenset()


def js_object(name, text=META):
    """Литерал `export const NAME = { key: 'value', ... };` → словарь строк (скобки — по счёту,
    объект бывает и в одну строку, и в несколько)."""
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


class WorkingDaysTests(unittest.TestCase):
    def test_counting_starts_after_handover_and_skips_weekends(self):
        friday = date(2026, 11, 13)
        self.assertEqual(rules.add_working_days(friday, 1, NO_HOLIDAYS), date(2026, 11, 16))
        self.assertEqual(rules.add_working_days(friday, 3, NO_HOLIDAYS), date(2026, 11, 18))
        saturday = date(2026, 11, 14)
        self.assertEqual(rules.add_working_days(saturday, 1, NO_HOLIDAYS), date(2026, 11, 16))

    def test_kz_holidays_are_not_working_days(self):
        holidays = rules.kz_holidays()
        self.assertIn(date(2026, 3, 23), holidays)  # Наурыз — понедельник
        friday = date(2026, 3, 20)
        # Пт → (пн 23.03 и вт 24.03 — праздники) → ср 25.03.
        self.assertEqual(rules.add_working_days(friday, 1, holidays), date(2026, 3, 25))

    def test_days_left_is_signed_in_working_days(self):
        monday, friday = date(2026, 11, 16), date(2026, 11, 20)
        self.assertEqual(rules.working_days_left(monday, friday, NO_HOLIDAYS), 4)
        self.assertEqual(rules.working_days_left(friday, friday, NO_HOLIDAYS), 0)
        self.assertEqual(rules.working_days_left(date(2026, 11, 23), friday, NO_HOLIDAYS), -1)

    def test_deadline_state_only_for_running_sla(self):
        due = date(2026, 11, 18)
        self.assertIsNone(rules.deadline_state('ready', due, date(2026, 11, 20), NO_HOLIDAYS))
        self.assertIsNone(rules.deadline_state('accepted', due, date(2026, 11, 20), NO_HOLIDAYS))
        self.assertEqual(rules.deadline_state('in_transit', due, date(2026, 11, 20), NO_HOLIDAYS),
                         {'kind': 'overdue', 'days': -2})
        self.assertEqual(rules.deadline_state('handed_over', due, due, NO_HOLIDAYS)['kind'], 'today')
        self.assertEqual(rules.deadline_state('handed_over', due, date(2026, 11, 17), NO_HOLIDAYS)['kind'], 'soon')
        # «Следующий рабочий день» в субботу — понедельник, а не «завтра».
        self.assertEqual(rules.deadline_state('handed_over', date(2026, 11, 16), date(2026, 11, 14), NO_HOLIDAYS),
                         {'kind': 'soon', 'days': 1})
        self.assertTrue(rules.is_overdue('not_delivered', due, date(2026, 11, 19)))
        self.assertFalse(rules.is_overdue('delivered', due, date(2026, 11, 19)))

    def test_sla_window_and_deadline(self):
        zone = {'min_days': 2, 'max_days': 3, 'eco_min_days': None, 'eco_max_days': None}
        self.assertEqual(rules.sla_window(zone, 'standard'), (2, 3))
        self.assertIsNone(rules.sla_window(zone, 'economy'))
        self.assertEqual(rules.sla_window({'min_days': None, 'max_days': 1}, 'standard'), (1, 1))
        self.assertEqual(rules.deadline(date(2026, 11, 13), (2, 3), NO_HOLIDAYS),
                         (date(2026, 11, 17), date(2026, 11, 18)))

    def test_zone_for_route(self):
        routes = {('алматы', 'павлодар'): '1'}
        self.assertEqual(rules.zone_for_route(' Алматы ', 'ПАВЛОДАР', routes), '1')
        self.assertEqual(rules.zone_for_route('Алматы', 'алматы', routes), rules.INTRACITY_ZONE)
        self.assertIsNone(rules.zone_for_route('Павлодар', 'Алматы', routes))
        self.assertEqual(rules.normalize_city('Усть-Каменогорск  ё'), 'усть-каменогорск е')

    def test_default_zones_follow_tz(self):
        self.assertEqual([(z['code'], z['min_days'], z['max_days']) for z in rules.DEFAULT_ZONES],
                         [('2', 1, 1), ('1', 2, 3), ('3', 7, 15)])


class ShipmentFieldsTests(unittest.TestCase):
    def base(self, **overrides):
        payload = {
            'sender_office_id': 1, 'recipient_office_id': 2, 'sender_name': 'А', 'sender_phone': '+7',
            'recipient_name': 'Б', 'recipient_phone': '+7', 'recipient_address': 'ул. 1', 'payer_entity_id': 3,
            'weight_kg': '0,5', 'items': [{'doc_type': 'avr', 'qty': '5'}],
        }
        payload.update(overrides)
        return payload

    def test_other_needs_description_and_other_types_drop_it(self):
        with self.assertRaises(rules.MailError) as caught:
            rules.clean_shipment(self.base(items=[{'doc_type': 'other', 'qty': 1, 'other_description': '  '}]))
        self.assertEqual(caught.exception.fields, {'items.0.other_description': 'Укажите, что именно отправляется'})
        _fields, items = rules.clean_shipment(self.base(items=[{'doc_type': 'contract', 'qty': 1,
                                                                 'other_description': 'x' * 900}]))
        self.assertIsNone(items[0]['other_description'])
        with self.assertRaises(rules.MailError) as caught:
            rules.clean_shipment(self.base(items=[{'doc_type': 'other', 'qty': 1, 'other_description': 'x' * 501}]))
        self.assertIn('items.0.other_description', caught.exception.fields)

    def test_field_checks_are_collected_together(self):
        with self.assertRaises(rules.MailError) as caught:
            rules.clean_shipment(self.base(recipient_office_id=1, recipient_postcode='05A', places=0, weight_kg='-1',
                                           items=[{'doc_type': 'nope', 'qty': 0, 'period_from': '2026-10-31',
                                                   'period_to': '2026-10-01'}]))
        self.assertEqual(set(caught.exception.fields), {
            'recipient_office_id', 'recipient_postcode', 'places', 'weight_kg', 'items.0.doc_type', 'items.0.qty',
            'items.0.period_to'})

    def test_numbers_accept_comma_and_draft_may_be_incomplete(self):
        fields, items = rules.clean_shipment({'sender_office_id': '1', 'weight_kg': '1,25', 'items': []})
        self.assertEqual(fields['weight_kg'], 1.25)
        self.assertEqual(fields['places'], 1)
        self.assertEqual(set(rules.missing_for_ready(fields, items)), {
            'recipient_office_id', 'recipient_name', 'recipient_phone', 'recipient_address', 'sender_name',
            'sender_phone', 'payer_entity_id', 'items'})
        fields, items = rules.clean_shipment(self.base())
        self.assertEqual(rules.missing_for_ready(fields, items), {})

    def test_changes_ignore_decimal_vs_float_noise(self):
        from decimal import Decimal
        self.assertEqual(rules.changed_fields({'weight_kg': Decimal('0.500')}, {'weight_kg': 0.5}), {})
        self.assertEqual(rules.changed_fields({'comment': 'a'}, {'comment': 'b'}), {'comment': ['a', 'b']})

    def test_number_format(self):
        self.assertEqual(rules.format_number(2026, 125), 'MAIL-2026-00125')

    def test_weight_is_checked_after_rounding(self):
        with self.assertRaises(rules.MailError) as caught:
            rules.clean_shipment(self.base(weight_kg='0,0001'))
        self.assertIn('weight_kg', caught.exception.fields)

    def test_time_with_offset_becomes_almaty_time(self):
        self.assertEqual(rules.datetime_or_none('2026-11-13T10:00+06:00'), datetime(2026, 11, 13, 9, 0))
        self.assertEqual(rules.datetime_or_none('2026-11-13T05:00:00Z'), datetime(2026, 11, 13, 10, 0))
        self.assertEqual(rules.datetime_or_none('2026-11-13 15:30'), datetime(2026, 11, 13, 15, 30))

    def test_item_line_for_history(self):
        self.assertEqual(rules.item_line({'doc_type': 'avr', 'qty': 5, 'period_from': '2026-09-01',
                                          'period_to': date(2026, 9, 30), 'copy_kind': 'original',
                                          'doc_number': '12', 'is_confidential': True}),
                         'АВР ×5 · 01.09.2026–30.09.2026 · оригинал · № 12 · конфиденциально')
        self.assertEqual(rules.item_line({'doc_type': 'other', 'other_description': 'Кепки', 'qty': 10,
                                          'copy_kind': 'copy'}), 'Иное «Кепки» ×10 · копия')

    def test_economy_ready(self):
        self.assertFalse(rules.economy_ready([dict(zone) for zone in rules.DEFAULT_ZONES]))
        self.assertTrue(rules.economy_ready([{'code': '1', 'eco_max_days': 12}]))

    def test_items_summary(self):
        items = [{'doc_type': 'avr', 'qty': 5}, {'doc_type': 'avr_return', 'qty': 3},
                 {'doc_type': 'other', 'other_description': 'Кепки', 'qty': 10}, {'doc_type': 'contract', 'qty': 1}]
        self.assertEqual(rules.items_summary(items), 'АВР (5), АВР возвратный (3), Кепки (10) и ещё 1')


class ReconcileTests(unittest.TestCase):
    ITEMS = [{'id': 1, 'qty': 5}, {'id': 2, 'qty': 3}]

    def test_every_line_needs_an_answer(self):
        with self.assertRaises(rules.MailError) as caught:
            rules.clean_reconcile_lines([{'id': 1, 'line_status': 'ok'}], self.ITEMS)
        self.assertEqual(caught.exception.fields, {'lines.1.line_status': 'Сверьте строку'})

    def test_outcome_and_quantities(self):
        lines, outcome = rules.clean_reconcile_lines(
            [{'id': 1, 'line_status': 'ok'}, {'id': 2, 'line_status': 'ok'}], self.ITEMS)
        self.assertEqual(outcome, 'accepted')
        self.assertEqual([line['received_qty'] for line in lines], [5, 3])
        lines, outcome = rules.clean_reconcile_lines(
            [{'id': 1, 'line_status': 'shortage', 'received_qty': '4'}, {'id': 2, 'line_status': 'excess',
                                                                         'received_qty': 4}], self.ITEMS)
        self.assertEqual(outcome, 'problem')
        for bad in ({'line_status': 'shortage', 'received_qty': 5}, {'line_status': 'excess', 'received_qty': 5},
                    {'line_status': 'damaged'}, {'line_status': 'maybe'}):
            with self.assertRaises(rules.MailError):
                rules.clean_reconcile_lines([dict(bad, id=1), {'id': 2, 'line_status': 'ok'}], self.ITEMS)


class TransitionTests(unittest.TestCase):
    def test_transitions(self):
        self.assertEqual(rules.check_transition('hand_over', 'courier_requested')['to'], 'handed_over')
        with self.assertRaises(rules.MailError) as caught:
            rules.check_transition('cancel', 'handed_over')
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(rules.MailError) as caught:
            rules.action_spec('teleport')
        self.assertEqual(caught.exception.status, 404)
        # Отменить можно только до передачи курьеру (ТЗ п. 4).
        self.assertEqual(rules.ACTIONS['cancel']['from'], rules.BEFORE_HANDOVER)

    def test_settings_cleaning(self):
        zones = rules.clean_zones([{'code': '1', 'label': 'Зона 1', 'min_days': '2', 'max_days': '3'}])
        self.assertEqual(zones[0]['max_days'], 3)
        with self.assertRaises(rules.MailError):
            rules.clean_zones([{'code': '1', 'min_days': 4, 'max_days': 3}])
        with self.assertRaises(rules.MailError):
            rules.clean_zones([{'code': '1', 'max_days': 3}, {'code': '1', 'max_days': 4}])
        routes = rules.clean_routes([{'from_city': 'Алматы', 'to_city': 'Павлодар', 'zone_code': '1'}], {'1'})
        self.assertEqual({(r['from_city'], r['to_city']) for r in routes}, {('Алматы', 'Павлодар'), ('Павлодар', 'Алматы')})
        one_way = rules.clean_routes([{'from_city': 'Алматы', 'to_city': 'Шымкент', 'zone_code': '1', 'both_ways': False}],
                                     {'1'})
        self.assertEqual(len(one_way), 1)
        with self.assertRaises(rules.MailError):
            rules.clean_routes([{'from_city': 'Алматы', 'to_city': 'Шымкент', 'zone_code': '9'}], {'1'})


def ctx(role=None, offices=(), user_id=10, is_super=False):
    return {'user_id': user_id, 'name': 'Т', 'role': role, 'office_ids': list(offices), 'is_super': is_super}


def shipment(status='handed_over', sender=1, recipient=2, created_by=10, confidential=False):
    return {'status': status, 'sender_office_id': sender, 'recipient_office_id': recipient, 'created_by': created_by,
            'is_confidential': confidential}


class AccessTests(unittest.TestCase):
    def setUp(self):
        # Правила для участников; пилот «пока только суперадминам» — PilotTests.
        patcher = mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_who_opens_and_which_tabs(self):
        self.assertFalse(access.can_open(ctx()))
        self.assertTrue(access.can_open(ctx(is_super=True)))
        self.assertEqual(access.tabs(ctx('office', [1])), ['overview', 'outgoing', 'incoming', 'registry', 'problems'])
        self.assertEqual(access.tabs(ctx('accounting')), ['overview', 'registry', 'problems'])
        self.assertEqual(access.tabs(ctx('admin')), ['overview', 'registry', 'problems', 'participants', 'settings'])
        self.assertEqual(access.tabs(ctx('admin', [1]))[:3], ['overview', 'outgoing', 'incoming'])

    def test_drafts_are_own_and_incoming_shows_after_handover(self):
        draft = shipment('draft', created_by=99)
        self.assertFalse(access.can_view(ctx('office', [1]), draft))
        self.assertTrue(access.can_view(ctx('coordinator', [1]), draft))
        self.assertTrue(access.can_view(ctx('office', [1], user_id=99), draft))
        self.assertFalse(access.can_view(ctx('accounting'), draft))
        self.assertFalse(access.can_view(ctx('office', [2]), shipment('ready', created_by=99)))
        self.assertTrue(access.can_view(ctx('office', [2]), shipment('handed_over', created_by=99)))
        self.assertFalse(access.can_view(ctx('office', [3]), shipment('handed_over', created_by=99)))

    def test_actions_by_side(self):
        on_way = shipment('handed_over', created_by=10)
        # Своё не принимает никто, даже администратор.
        self.assertNotIn('reconcile', access.allowed_actions(ctx('admin', user_id=10), on_way))
        self.assertIn('reconcile', access.allowed_actions(ctx('office', [2], user_id=11), on_way))
        self.assertEqual(access.allowed_actions(ctx('regional', [1, 2], user_id=12), on_way), [])
        self.assertEqual(access.allowed_actions(ctx('accounting', user_id=12), on_way), [])
        self.assertNotIn('resolve', access.allowed_actions(ctx('office', [2], user_id=11), shipment('problem')))
        self.assertIn('resolve', access.allowed_actions(ctx('coordinator', [2], user_id=11), shipment('problem')))
        self.assertIn('deliver', access.allowed_actions(ctx('office', [1], user_id=11), on_way))

    def test_edit_rights_follow_status(self):
        sender = ctx('office', [1])
        self.assertEqual(access.edit_rights(sender, shipment('ready')), (True, False, True))
        self.assertEqual(access.edit_rights(sender, shipment('in_transit')), (True, True, True))
        self.assertEqual(access.edit_rights(sender, shipment('accepted')), (False, True, False))
        self.assertEqual(access.edit_rights(ctx('admin'), shipment('accepted')), (True, True, False))
        self.assertEqual(access.edit_rights(ctx('office', [2], user_id=11), shipment('in_transit'))[0], False)
        # Строки после передачи — только стороне отправки, и не после «Доставлено курьером».
        self.assertEqual(access.edit_rights(ctx('coordinator', [2], user_id=11), shipment('in_transit')),
                         (True, True, False))
        self.assertEqual(access.edit_rights(sender, shipment('delivered')), (True, True, False))

    def test_author_never_gets_an_accepting_step(self):
        problem = shipment('problem', created_by=10)
        self.assertNotIn('resolve', access.allowed_actions(ctx('coordinator', [1, 2], user_id=10), problem))
        self.assertNotIn('resolve', access.allowed_actions(ctx('admin', user_id=10), problem))
        self.assertIn('resolve', access.allowed_actions(ctx('central', user_id=12), problem))
        self.assertIn('start_return', access.allowed_actions(ctx('coordinator', [1, 2], user_id=10), problem))

    def test_accounting_does_not_open_confidential_files(self):
        self.assertFalse(access.can_open_files(ctx('accounting'), shipment(confidential=True)))
        self.assertTrue(access.can_open_files(ctx('accounting'), shipment()))
        self.assertTrue(access.can_open_files(ctx('office', [2], user_id=11), shipment(confidential=True)))

    def test_sender_offices(self):
        self.assertEqual(access.sender_office_ids(ctx('office', [2]), [1, 2, 3]), [2])
        self.assertEqual(access.sender_office_ids(ctx('central'), [1, 2, 3]), [1, 2, 3])
        self.assertEqual(access.sender_office_ids(ctx('regional', [1]), [1, 2, 3]), [])
        self.assertEqual(access.default_sender_office(ctx('admin', [3]), [1, 2, 3]), 3)
        self.assertIsNone(access.default_sender_office(ctx('admin'), [1, 2, 3]))

    def test_participant_is_an_office_employee_with_one_office(self):
        """«Участники» — сотрудники офисов, у каждого свой офис (решение владельца 13.11.2026)."""
        known = {1, 2, 3}
        self.assertEqual(access.clean_participant({'office_ids': [2]}, known), ('office', [2]))
        self.assertEqual(access.clean_participant({'office_id': '3'}, known), ('office', [3]))
        # Роль из запроса не читается: ни «админа», ни «координатора» вкладкой не выдать.
        self.assertEqual(access.clean_participant({'role': 'admin', 'office_ids': [1]}, known), ('office', [1]))
        self.assertEqual(access.clean_participant({'role': 'coordinator', 'office_ids': [1]}, known), ('office', [1]))
        # Администратор раздела остаётся администратором; офис ему — по желанию.
        self.assertEqual(access.clean_participant({'office_ids': []}, known, current_role='admin'), ('admin', []))
        self.assertEqual(access.clean_participant({'office_ids': [2]}, known, current_role='admin'), ('admin', [2]))
        for bad in ({'office_ids': [1, 2]}, {'office_ids': []}, {}, {'office_ids': [9]}, {'office_ids': ['x']}):
            with self.assertRaises(rules.MailError):
                access.clean_participant(bad, known)
        with self.assertRaises(rules.MailError):
            access.clean_participant({'office_ids': [1, 2]}, known, current_role='admin')


class PilotTests(unittest.TestCase):
    """Пилот 13.11.2026: раздел пока открыт только суперадминам (флаг включён в коде)."""

    def test_pilot_is_on_and_lets_in_super_admins_only(self):
        self.assertTrue(access.PILOT_SUPER_ADMIN_ONLY)
        self.assertTrue(access.can_open(ctx(is_super=True)))
        for role in rules.ROLES:
            self.assertFalse(access.can_open(ctx(role, [1])), role)
        self.assertEqual(access.tabs(ctx('office', [1])), [])


class FrontMirrorTests(unittest.TestCase):
    """Подписи кодов на экране — дословно те же, что в rules.py (и значит в Excel)."""

    def test_labels_match(self):
        pairs = {
            'STATUS_LABELS': rules.STATUS_LABELS, 'DOC_TYPE_LABELS': rules.DOC_TYPE_LABELS,
            'COPY_KIND_LABELS': rules.COPY_KIND_LABELS, 'DELIVERY_TYPE_LABELS': rules.DELIVERY_TYPE_LABELS,
            'URGENCY_LABELS': rules.URGENCY_LABELS, 'LINE_STATUS_LABELS': rules.LINE_STATUS_LABELS,
            'FILE_KIND_LABELS': rules.FILE_KIND_LABELS, 'EVENT_LABELS': rules.EVENT_LABELS,
            'FIELD_LABELS': rules.FIELD_LABELS, 'ROLE_LABELS': rules.ROLE_LABELS, 'NOTE_REQUIRED': rules.NOTE_REQUIRED,
        }
        for name, expected in pairs.items():
            self.assertEqual(js_object(name), expected, name)
        self.assertEqual(js_object('ACTION_LABELS'), {code: spec['label'] for code, spec in rules.ACTIONS.items()
                                                      if code != 'reconcile'} | {'reconcile': 'Сверить'})
        quick = dict(re.findall(r"\{ value: '(\w+)', label: '([^']+)' \}",
                                re.search(r'export const QUICK_FILTERS = \[(.*?)\];', META, re.S).group(1)))
        self.assertEqual(quick, {code: item['label'] for code, item in rules.QUICK_FILTERS.items()})

class WiringTests(unittest.TestCase):
    """Раздел подключён во всех местах монолита и экрана."""

    def test_backend_wiring(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn('from mail.routes import build_mail_blueprint', source)
        self.assertIn('"mail_access": mail_access,', source)
        self.assertIn("_MAIL_SECTION['ready'] = True", source)

    def test_frontend_wiring(self):
        app = (ROOT / 'src/App.jsx').read_text(encoding='utf-8')
        for needle in (
                "const MailView = lazyWithRetry(() => import('./components/mail/MailView'));",
                "const canAccessMailForUser = (userLike) => userLike?.mail_access === true;",
                "const canAccessMailSection = canAccessMailForUser(user);",
                "(requestedViewFromUrl !== 'mail' || canAccessMailSection) &&",
                "if (view === 'mail' && !canAccessMailSection) {",
                "if (view === 'mail' && canAccessMailSection) return;",
                "|| canAccessMailSection",
                "handleSidebarViewNavigation(e, 'mail')",
                "{view === 'mail' && canAccessMailSection && (",
        ):
            self.assertIn(needle, app)
        # Предикат стоит в списках зависимостей всех трёх эффектов-гардов.
        self.assertEqual(app.count('canAccessHrOfficeSection, canAccessMailSection'), 3)
        # Вкладка раздела — своей меткой: `tab` портал снимает как метку вики. Снимается и при
        # уходе в другой раздел — в обеих функциях адреса.
        self.assertEqual(app.count("if (nextView !== 'mail') url.searchParams.delete('mail_tab');"), 2)
        view = (ROOT / 'src/components/mail/MailView.jsx').read_text(encoding='utf-8')
        self.assertIn("const TAB_PARAM = 'mail_tab';", view)
        # Тренера-участника гард «чистого тренера» не выкидывает из раздела в «Опросы».
        trainer = app[app.index('const TRAINER_ALLOWED_VIEWS = Object.freeze(['):]
        self.assertIn("'mail',", trainer[:trainer.index(']);')])
        icons = (ROOT / 'src/components/common/FaIcon.jsx').read_text(encoding='utf-8')
        self.assertIn("'fa-envelope': 'Mail',", icons)


class ReportTests(unittest.TestCase):
    def test_workbook_sheets_and_text_columns(self):
        from openpyxl import load_workbook
        row = {
            'id': 1, 'number': 'MAIL-2026-00001', 'status': 'in_transit', 'created_at': datetime(2026, 11, 10, 9, 0),
            'sender_city': 'Алматы', 'recipient_city': 'Павлодар', 'recipient_phone': '+77010000011',
            'track_number': '000123', 'due_on': date(2026, 11, 12), 'sla_min_days': 2, 'sla_max_days': 3,
            'zone_code': '1', 'zone_manual': False, 'delivery_type': 'standard', 'urgency': 'normal', 'places': 1,
            'qty_total': 5, 'items_summary': 'АВР (5)', 'comment': '\x07=1+1',
        }
        stream = report.build_workbook(
            [row], {1: [{'position': 1, 'doc_type': 'avr', 'qty': 5, 'copy_kind': 'original'}]},
            {1: [{'kind': 'created', 'to_status': 'ready', 'actor_name': 'Т', 'at': datetime(2026, 11, 10, 9, 0),
                  'payload': {}}]},
            zones=[{'code': '1', 'label': 'Зона 1'}], today=date(2026, 11, 13), holidays=NO_HOLIDAYS,
            generated_at=datetime(2026, 11, 13, 10, 0), generated_by='Т')
        book = load_workbook(stream)
        sheet = book['Отправления']
        header = [cell.value for cell in sheet[1]]
        values = dict(zip(header, [cell.value for cell in sheet[2]]))
        self.assertEqual(values['Трек-номер'], '000123')
        self.assertEqual(values['Дней до срока / просрочки'], -1)
        self.assertEqual(values['Срок, раб. дней'], '2–3')
        self.assertEqual(values['Комментарий'], ' =1+1')
        self.assertEqual(sheet.cell(2, header.index('Комментарий') + 1).data_type, 's')
        self.assertEqual(set(report.TEXT_SHIPMENT_KEYS),
                         {'number', 'sender_phone', 'recipient_phone', 'recipient_postcode', 'track_number'})

    def test_problem_column_names_other_by_description_and_both_sheets_lose_green_corner(self):
        self.assertEqual(report._problem_text([
            {'doc_type': 'other', 'other_description': 'Кепки', 'line_status': 'shortage', 'qty': 5, 'received_qty': 3},
            {'doc_type': 'avr', 'line_status': 'ok', 'qty': 1, 'received_qty': 1}]),
            'Кепки: недостача, пришло 3 из 5')
        patched = []

        def patch(stream, sqref, sheet_path):
            patched.append((sheet_path, sqref))
            stream.seek(0)
            return stream

        report.build_workbook(
            [{'id': 1, 'number': 'MAIL-2026-00001', 'status': 'ready', 'qty_total': 1}],
            {1: [{'position': 1, 'doc_type': 'contract', 'qty': 1, 'doc_number': '00123', 'copy_kind': 'original'}]},
            {}, zones=[], today=date(2026, 11, 13), holidays=NO_HOLIDAYS, generated_at=datetime(2026, 11, 13, 10, 0),
            text_warning_patch=patch)
        self.assertEqual([path for path, _ in patched], ['xl/worksheets/sheet2.xml', 'xl/worksheets/sheet3.xml'])
        self.assertEqual(patched[1][1], 'A2:A2 I2:I2')


if __name__ == '__main__':
    unittest.main()
