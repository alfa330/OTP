# -*- coding: utf-8 -*-
"""«Активация водителей» (СЗоВ, 26.11.2026): правила без базы и сети — разбор файла «Новички по поездкам с ЭДО»,
подписи поездок и провайдера, источники CRM и Fleet на подделках, профиль для телефона.
Полный путь (загрузка, проверки, успешки, телефон, журнал) — tests/test_dial_list_activation_postgres.py."""
import io
import os
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import activation  # noqa: E402
from dial_list import activation_check  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.activation_sources import CrmSource, FleetSource, SourceError  # noqa: E402

HEADER = ['Диспетчерская', 'ID водителя', 'ФИО', 'Телефон', 'Водительское удостоверение', 'ИИН', 'Тип профиля',
          'Дата регистрации', 'Условия работы', 'Лимит', 'Статус СМЗ', 'Статус работы', 'Завершено поездок',
          'Дата 50-й поездки', 'Дней до 50-й', 'Последняя поездка', 'Ранее в наших парках: с',
          'Ранее в наших парках: где', 'Автор регистрации (ФИО)', 'Примечание к автору', 'Актуальная акция',
          'Статус участия', 'Подключена к акции', 'Прогресс по акции', 'Последняя закрытая акция', 'Провайдер ЭДО']

ID1, ID2, ID3, ID4 = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32


def _row(driver_id, phone, trips, provider, park='Jana (Алматы)', iin='950101300123', fio='Тестов Тест'):
    row = [''] * len(HEADER)
    row[0], row[1], row[2], row[3], row[5] = park, driver_id, fio, phone, iin
    row[7], row[12], row[25] = '2026-11-06 12:32:35', trips, provider
    return row


def _xlsx(sheets):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append(HEADER)
        for r in rows:
            ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class ParseFileTests(unittest.TestCase):
    def test_both_sheets_are_read_and_goal_reached_rows_are_skipped(self):
        raw = _xlsx([
            ('До 50 поездок', [_row(ID1, '77001112233', 34, 'Бумажный документооборот'),
                               _row(ID2.upper(), '87012223344', '7', 'Не найден', park='iTaxi (доставка) (Алматы)')]),
            ('Более 50 поездок', [_row(ID3, '77013334455', 64, 'Sapar'), _row(ID4, '77014445566', 49, 'Payda')]),
        ])
        rows, extras, counts = activation.parse_activation_file(raw, '.xlsx', goal=50)
        self.assertEqual(counts['sheets'], 2)
        self.assertEqual(counts['rows_goal_reached'], 1)
        self.assertEqual([r[3] for r in rows], ['77001112233', '77012223344', '77014445566'])
        first = extras['77001112233']
        self.assertEqual(first['driver_id'], ID1)
        self.assertEqual(first['trips_total'], 34)
        self.assertEqual(first['edm_provider'], 'Бумажный документооборот')
        self.assertEqual(first['park_name'], 'Jana (Алматы)')
        self.assertEqual(first['registered_at'].astimezone(timezone.utc), datetime(2026, 11, 6, 7, 32, 35, tzinfo=timezone.utc))
        # ID — в нижний регистр; «Не найден» — провайдер неизвестен, а не имя.
        self.assertEqual(extras['77012223344']['driver_id'], ID2)
        self.assertEqual(extras['77012223344']['edm_provider'], '')
        # Город — из последних скобок «Диспетчерской»; ИИН — как в файле (проверит import_leads).
        self.assertEqual(rows[0][5], 'Алматы')
        self.assertEqual(rows[1][5], 'Алматы')
        self.assertEqual(rows[0][4], '950101300123')

    def test_goal_boundary_and_row_numbers_on_their_own_sheet(self):
        """Разбор 27.11: ровно цель в файле — не грузится; номер строки — строка Excel на своём листе (пустые строки
        тоже считаются), а листов с данными несколько — с именем листа (раньше счёт шёл сквозь листы)."""
        raw = _xlsx([
            ('До 50 поездок', [_row(ID1, '77001110001', 50, 'Sapar'), _row(ID2, '77001110002', 49, 'Sapar')]),
            ('Более 50', [_row('bad', '77001110003', 3, 'Sapar'), [''] * len(HEADER),
                          _row('bad-again', '77001110004', 3, 'Sapar')]),
        ])
        rows, _extras, counts = activation.parse_activation_file(raw, '.xlsx', goal=50)
        self.assertEqual(counts['rows_goal_reached'], 1)
        self.assertEqual([r[0] for r in rows], ['«До 50 поездок»: 3'])
        self.assertEqual(counts['bad_id_rows'], ['«Более 50»: 2', '«Более 50»: 4'])
        # Один лист с данными — просто номер строки Excel.
        raw = _xlsx([('Лист1', [[''] * len(HEADER), _row(ID1, '77001110001', 10, 'Sapar')])])
        rows, _extras, _counts = activation.parse_activation_file(raw, '.xlsx', goal=50)
        self.assertEqual([r[0] for r in rows], [3])

    def test_higher_goal_takes_drivers_of_the_second_sheet(self):
        raw = _xlsx([('Более 50 поездок', [_row(ID3, '77013334455', 64, 'Sapar')])])
        rows, _extras, counts = activation.parse_activation_file(raw, '.xlsx', goal=70)
        self.assertEqual(len(rows), 1)
        self.assertEqual(counts['rows_goal_reached'], 0)

    def test_bad_ids_and_repeated_ids(self):
        raw = _xlsx([('До 50', [_row('not-an-id', '77001112233', 3, ''), _row(ID1, '77002223344', 3, ''),
                                _row(ID1, '77003334455', 4, '')])])
        rows, _extras, counts = activation.parse_activation_file(raw, '.xlsx')
        self.assertEqual(counts['rows_bad_id'], 1)
        self.assertEqual(counts['bad_id_rows'], [2])
        self.assertEqual(counts['duplicate_ids'], 1)
        self.assertEqual(len(rows), 1)

    def test_file_without_id_or_phone_column_is_refused(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(['ФИО', 'Телефон'])
        wb.active.append(['Тест', '77001112233'])
        out = io.BytesIO()
        wb.save(out)
        with self.assertRaises(ValueError) as caught:
            activation.parse_activation_file(out.getvalue(), '.xlsx')
        self.assertIn('ID водителя', str(caught.exception))
        csv = 'ID водителя;ФИО\n{};Тест\n'.format(ID1).encode('utf-8')
        with self.assertRaises(ValueError) as caught:
            activation.parse_activation_file(csv, '.csv')
        self.assertIn('Телефон', str(caught.exception))

    def test_csv_with_short_headers(self):
        csv = 'id;fio;phone;поездок;эдо\n{};Тест;87001112233;12;Sapar\n'.format(ID1).encode('utf-8')
        rows, extras, _counts = activation.parse_activation_file(csv, '.csv')
        self.assertEqual(rows[0][3], '77001112233')
        self.assertEqual(extras['77001112233']['edm_provider'], 'Sapar')
        self.assertEqual(extras['77001112233']['trips_total'], 12)


class LabelTests(unittest.TestCase):
    def test_trips_label_and_plurals(self):
        self.assertEqual(activation.trips_label(34, 50), '34 из 50 поездок')
        self.assertEqual(activation.trips_label(3, 21), '3 из 21 поездки')
        self.assertEqual(activation.trips_label(None, 50), 'Поездки не проверены')
        self.assertEqual(activation.trips_label(52, 50), 'Добрал 52 поездки')
        self.assertEqual(activation.trips_label(50, 50), 'Добрал 50 поездок')
        self.assertEqual(activation.trips_label(49, 50), '49 из 50 поездок')
        self.assertEqual(activation.trips_label(51, 50, reached_at=datetime.now(timezone.utc)), 'Добрал 51 поездку')
        self.assertEqual([activation.trips_word(n) for n in (1, 2, 5, 11, 12, 21, 104)],
                         ['поездка', 'поездки', 'поездок', 'поездок', 'поездок', 'поездка', 'поездки'])

    def test_provider_label(self):
        self.assertEqual(activation.provider_label('Бумажный документооборот', 'file'), 'ЭДО: Бумажный')
        self.assertEqual(activation.provider_label('Sapar', 'card'), 'ЭДО: Sapar')
        self.assertEqual(activation.provider_label('', 'none'), 'ЭДО: нет')
        self.assertEqual(activation.provider_label('', ''), 'ЭДО: не знаем')
        self.assertTrue(activation.is_sapar(' sapar '))
        self.assertFalse(activation.is_sapar('Payda'))

    def test_phone_block_has_no_number_and_no_link(self):
        block = activation.driver_block(34, 50, None, 'Бумажный документооборот', 'file', city='Алматы')
        self.assertEqual(block['label'], '34 из 50 поездок · ЭДО: Бумажный')
        self.assertEqual(block['city'], 'Алматы')
        # Парк водителя (владелец 27.11.2026) — в конце строки, пробелы схлопнуты.
        block = activation.driver_block(34, 50, None, 'Бумажный документооборот', 'file', city='Алматы',
                                        park='  Jana   (Алматы) ')
        self.assertEqual(block['label'], '34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы)')
        self.assertEqual(block['profile_url'], '')
        self.assertNotIn('phone', block)
        self.assertEqual(block['driver_id'], '')
        # ID водителя (владелец 27.11.2026): отдельным полем всегда; старому телефону — ещё и последним в строке.
        block = activation.driver_block(34, 50, None, 'Бумажный документооборот', 'file', park='Jana (Алматы)',
                                        driver_id=' ' + ID1.upper() + ' ')
        self.assertEqual(block['label'], f'34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы) · ID: {ID1}')
        self.assertEqual(block['driver_id'], ID1)
        block = activation.driver_block(34, 50, None, 'Бумажный документооборот', 'file', park='Jana (Алматы)',
                                        driver_id=ID1, id_in_label=False)
        self.assertEqual((block['label'], block['driver_id']),
                         ('34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы)', ID1))
        # Без парка ID идёт сразу за провайдером; не ID (мусор из файла) — не показываем.
        self.assertEqual(activation.driver_label(34, 50, None, '', 'none', driver_id=ID2),
                         f'34 из 50 поездок · ЭДО: нет · ID: {ID2}')
        self.assertEqual(activation.driver_block(34, 50, None, '', 'none', driver_id='12345')['label'],
                         '34 из 50 поездок · ЭДО: нет')

    def test_fleet_url_and_city(self):
        self.assertEqual(activation.fleet_url(ID1, ID2),
                         f'https://fleet.yandex.kz/contractors/{ID1}/details?park_id={ID2}')
        self.assertEqual(activation.fleet_url(ID1, ''), '')
        self.assertEqual(activation.city_from_park('Ноль такси (Алматы)'), 'Алматы')
        self.assertEqual(activation.city_from_park('Jana Taxi'), '')

    def test_goal_bounds(self):
        self.assertEqual(activation.clean_goal('70'), 70)
        self.assertEqual(activation.clean_goal(0), 50)
        self.assertEqual(activation.clean_goal('abc'), 50)


class _Resp:
    def __init__(self, status, payload=None, text=False):
        self.status_code = status
        self._payload = payload
        self._text = text

    def json(self):
        if self._text:
            raise ValueError('html')
        return self._payload


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json, headers))
        return self.responses.pop(0)


CFG = {'url': 'https://crm.example/api/partners/driver-info', 'token': 'tkn'}


class CrmSourceTests(unittest.TestCase):
    def test_orders_batch(self):
        http = _Http([_Resp(200, {'data': {ID1: {'orders_count': 45, 'last_order_at': '2026-11-23 15:55:01'},
                                           ID2: {'orders_count': None}}})])
        crm = CrmSource(config=CFG, session=http)
        answer = crm.orders([ID1, ID2.upper()], date(2026, 11, 1), date(2026, 11, 26))
        self.assertEqual(answer, {ID1: {'count': 45, 'last_order_at': '2026-11-23 15:55:01'}})
        url, body, headers = http.calls[0]
        self.assertEqual(url, 'https://crm.example/api/partners/orders')
        self.assertEqual(body, {'account_ids': [ID1, ID2], 'from': '2026-11-01', 'to': '2026-11-26'})
        self.assertEqual(headers['X-Integration-Token'], 'tkn')

    def test_html_answer_and_503_are_errors_not_zeroes(self):
        crm = CrmSource(config=CFG, session=_Http([_Resp(200, text=True), _Resp(503, {})]))
        with self.assertRaises(SourceError):
            crm.orders([ID1], date(2026, 11, 1), date(2026, 11, 26))
        with self.assertRaises(SourceError):
            crm.orders([ID1], date(2026, 11, 1), date(2026, 11, 26))

    def test_driver_card(self):
        http = _Http([_Resp(200, {'data': {'park': {'yandex_id': ID2.upper(), 'name': 'Jana  Taxi Алматы'},
                                           'orders': {'total': 14, 'last_order_at': '2026-11-26 03:22:00'},
                                           'employment': {'created_date': '2026-11-24 11:38:39',
                                                          'employment_type': 'selfemployed'}}}),
                      _Resp(404, {'error': 'not_found'})])
        crm = CrmSource(config=CFG, session=http)
        info = crm.driver(ID1)
        self.assertEqual(info['park_id'], ID2)
        self.assertEqual(info['park_name'], 'Jana Taxi Алматы')
        self.assertEqual(info['total'], 14)
        self.assertIsNone(crm.driver(ID3))
        self.assertTrue(crm.configured())
        self.assertFalse(CrmSource(config={'token': ''}).configured())


class _Card:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def driver_card(self, park_id, driver_id):
        self.asked.append((park_id, driver_id))
        value = self.answers.get(driver_id)
        if isinstance(value, Exception):
            raise value
        return value


class FleetSourceTests(unittest.TestCase):
    def test_provider_from_card(self):
        card = _Card({ID1: {'edm_provider': 'Sapar\n'}, ID2: None, ID3: {'edm_provider': ''}})
        fleet = FleetSource(db=None, make_client=lambda: card)
        self.assertEqual(fleet.provider(ID4, ID1), ('Sapar', True))
        self.assertEqual(fleet.provider(ID4, ID2), ('', False))
        self.assertEqual(fleet.provider(ID4, ID3), ('', True))
        self.assertTrue(fleet.configured())

    def test_expired_session_backs_off(self):
        from fleet_edm.client import FleetSessionExpired
        card = _Card({ID1: FleetSessionExpired('401')})
        clock = [1000.0]
        fleet = FleetSource(db=None, make_client=lambda: card, clock=lambda: clock[0])
        with self.assertRaises(SourceError):
            fleet.provider(ID4, ID1)
        with self.assertRaises(SourceError):
            fleet.provider(ID4, ID1)
        self.assertEqual(len(card.asked), 1, 'после протухшей сессии кабинет не спрашиваем до конца паузы')


class CheckRulesTests(unittest.TestCase):
    def test_window_starts_at_registration_month(self):
        moment = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(activation_check.window_start(moment), date(2026, 11, 1))
        self.assertEqual(activation_check.window_start(None), activation_check.CRM_ORDERS_SINCE)
        self.assertEqual(activation_check.window_start(datetime(2025, 1, 5, tzinfo=timezone.utc)),
                         activation_check.CRM_ORDERS_SINCE)

    def test_reached_moment_is_never_after_the_check(self):
        now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(activation_check.reached_moment('2026-11-26 12:00:00', now),
                         datetime(2026, 11, 26, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(activation_check.reached_moment('2026-11-26 20:00:00', now), now)
        self.assertEqual(activation_check.reached_moment(None, now), now)


class ProfileTests(unittest.TestCase):
    def test_phone_sees_activation_as_tez(self):
        self.assertEqual(dial_service.phone_profile({'profile': 'activation'}), 'tez')
        self.assertEqual(dial_service.phone_profile({'profile': 'tez'}), 'tez')
        self.assertEqual(dial_service.phone_profile({'profile': 'remote_cc'}), 'remote_cc')
        self.assertTrue(dial_service.is_autodial({'profile': 'activation'}))
        self.assertFalse(dial_service.is_tez({'profile': 'activation'}))
        self.assertIn('activation', dial_service.PROFILES)

    def test_progress_counts_any_talk_as_a_dial_like_the_operators_tab(self):
        """«Мой прогресс» на движке автообзвона («Активация», Тез): дозвон — любой разговор, как во вкладке
        «Операторы» руководителя (overview с tez); удалённый КЦ — разговор от 10 с (владелец, 29.10.2026)."""
        import contextlib

        class Cur:
            def __init__(self):
                self.params = []

            def execute(self, sql, params=None):
                self.params.append(params)

            def fetchone(self):
                return None

            def fetchall(self):
                return []

        for profile, any_talk in (('activation', True), ('tez', True), ('remote_cc', False)):
            cur = Cur()

            class Db:
                @contextlib.contextmanager
                def _get_cursor(self):
                    yield cur

            svc = dial_service.DialListService(db=Db())
            svc.operator_context = lambda uid: {"user_id": uid, "department_id": 2100, "name": "Оператор",
                                                "settings": {"profile": profile, "_period": date(2026, 11, 1)}}
            svc.operator_progress(7)
            progress = next(p for p in cur.params if isinstance(p, dict) and 'any_talk' in p)
            self.assertIs(progress["any_talk"], any_talk, profile)

    def test_phone_distribution_skips_activation_on_borrowed_lines(self):
        """Разбор 27.11: отдел «Активации» без своих линий (СЗоВ на линиях удалённого КЦ) дистрибутив iCORE Phone всему
        отделу не открывает — телефон получают те, кто сидит на линиях. Исполняется НАСТОЯЩАЯ функция монолита."""
        import ast
        import logging

        from tests import source_cache
        node = source_cache.function_copy(source_cache.monolith_flat_path(), '_dial_list_department_allowed')
        namespace = {"logging": logging}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), 'bot_schedule2', 'exec'),
             namespace)
        allowed = namespace['_dial_list_department_allowed']

        class Svc:
            def __init__(self, rows):
                self.rows = rows

            def list_departments(self, ids):
                return self.rows

        namespace['_dial_list_service'] = Svc([{"department_id": 1, "host_department_id": 1954}])
        self.assertIs(allowed(1), False)
        namespace['_dial_list_service'] = Svc([{"department_id": 1954, "host_department_id": None}])
        self.assertIs(allowed(1954), True)
        namespace['_dial_list_service'] = Svc([])
        self.assertIs(allowed(2200), False)

    def test_activation_outcomes_seed(self):
        names = [o[0] for o in dial_service.DEFAULT_ACTIVATION_OUTCOMES]
        self.assertIn('Перейдёт на Sapar', names)
        kinds = {o[0]: o[3] for o in dial_service.DEFAULT_ACTIVATION_OUTCOMES}
        self.assertEqual(kinds['Будет ездить'], dial_service.DUE_ONLINE)
        self.assertEqual(kinds['Перезвонить'], dial_service.DUE_CALLBACK)


if __name__ == '__main__':
    unittest.main()
