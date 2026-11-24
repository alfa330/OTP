# -*- coding: utf-8 -*-
"""«Термокороба» — зеркало Google-таблицы (24.11.2026): лист читается сам, раздел только показывает.

Что сторожится:
  * лист разбирается как настоящий: «11.0» в ячейке — 11, памятка справа от
    офисов со значками и «Ответственными», объединённая ячейка подзаголовка;
  * строки, заведённые разовым переносом 02.10.2026 (без ключа листа),
    узнаются по городу и адресу — ни одна не дублируется и не пропадает;
  * повтор без изменений в листе ничего не пишет; правка в листе — одна
    запись в историю строки от имени «Google-таблица»;
  * офис пропал из листа — убран, вернулся — возвращён; пропала бо́льшая
    часть — это переделанный лист, таблицу не трогаем;
  * опечатка в ячейке не роняет офис и не пишет чужое число;
  * объединённая ячейка «Город» — тот же город; строка без города, опечатка
    в названии города, не узнанная строка — в «не перенесено», и офисы её
    города (или все, если город не узнан) не убираются;
  * убранный офис не подхватывает новую строку своего города, копия строки
    не привязывается ни к чему, строка, уже видевшая лист, без совпадения
    дома узнаётся только на той же улице; «дом без литеры» в справочнике
    не заводится сам;
  * страж массового скрытия: один офис убирается всегда, половина — нет;
  * ошибка базы посреди записи — в состоянии, прогон не падает;
  * лист не прочитан (нет доступа, сеть, вход аккаунта отозван) — данные
    раздела прежние, ошибка в состоянии, причина — кто читает и что сделать —
    администратору;
  * читает вход аккаунта, которому открыта таблица (OAuth-токен), если он
    настроен — и из окружения, и из .env.codex.local, куда его пишет скрипт;
    сломанный вход — ошибка, а не тихий уход на сервисную учётку;
  * правка в разделе закрыта (409), «Обновить из таблицы» — глобальному админу;
  * плановое чтение подключено в планировщике бота.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from thermoboxes import access, queries, rules, sheet, sync  # noqa: E402
from tests import source_cache  # noqa: E402
from tests.test_thermoboxes import Flask, _Base, _row, person  # noqa: E402

HEADER = ('Город,Адрес,Бесплатные термокороба ,Термопакет,Б/У короб,Тариф,Заказы,Срок,'
          'Отдельное условие,Депозит,,ПАМЯТКА ДЛЯ СОТРУДНИКОВ,,Ответственные')


def office_line(city, address, free='18', bags='0', used='0', tariff='Выдаются авто-курьерам',
                orders='20+ заказов', special='', deposit='5 000 тенге', memo=('', '', '')):
    cells = [city, address, free, bags, used, tariff, orders, 'Неделя (7 дней)', special, deposit, '']
    cells += list(memo)
    return ','.join('"%s"' % cell.replace('"', '""') for cell in cells)


def sheet_csv(*lines):
    return '\n'.join((HEADER,) + lines) + '\n'


# Лист в том виде, в каком его отдаёт выгрузка CSV (24.11.2026): подзаголовок
# памятки — объединённая ячейка во второй строке, пункты — значок, текст, кто.
REAL = sheet_csv(
    office_line('Актау', 'мкр. 2, д. 47А, БЦ «Орда»', free='11.0', used='1', orders='15+заказов',
                memo=('Термопакеты и термокороба — правила выдачи', '', '')),
    office_line('Алматы', '7-й микрорайон, 5', free='18',
                memo=('⛔', '2 ДОНГЕЛЕК — В ОФИСАХ НЕ ОБСЛУЖИВАЕТСЯ.\n НЕ ОТПРАВЛЯТЬ курьеров', 'КЦ')),
    office_line('Алматы', 'ул. Жамбыла, 172', free='0', memo=('🚫', 'В офисах СНЯТЬ деньги нельзя.', 'КЦ')),
    office_line('Астана', 'пр. Сарыарка, 31', free='136', special='НЕ новички',
                memo=('📊', 'Информация о наличии должна\nсоответствовать таблице', 'Регионы')),
    office_line('Туркестан', 'ул. Нышанов, 14, БЦ «Балнур»', free='55', orders='15+заказов', deposit='Нет депозита'),
)


TURKESTAN_LINE = office_line('Туркестан', 'ул. Нышанов, 14, БЦ «Балнур»', free='55', orders='15+заказов',
                             deposit='Нет депозита')


def existing_rows():
    """Строки раздела так, как их завёл разовый перенос: без ключа листа,
    адрес — из справочника вики (не текст листа)."""
    return [
        _row(1, 'Актау', office_id=40, address='2-й микрорайон, 47А', free_boxes=11, used_boxes=1),
        _row(2, 'Алматы', office_id=48, address='7-й микрорайон, 5', free_boxes=20, min_orders=20),
        _row(3, 'Алматы', office_id=47, address='Улица Жамбыла, 172В угол улицы Байзакова',
             free_boxes=0, min_orders=20),
        _row(4, 'Астана', office_id=49, address='Проспект Сарыарка, 31 угол улицы Алиби Жангельдин',
             free_boxes=150, min_orders=20, special_condition='НЕ новички'),
        _row(5, 'Туркестан', office_id=63, address='улица Нышанов 15', free_boxes=60, deposit_tenge=0),
    ]


def apply_plan(rows, plan):
    """Как применил бы план прогон: для проверки «повтор ничего не меняет»."""
    by_id = {row['id']: row for row in rows}
    for item in plan['updates']:
        by_id[item['row_id']].update(item['fields'])
    for item in plan['keys']:
        by_id[item['row_id']]['sheet_key'] = item['sheet_key']
    for row_id in plan['show']:
        by_id[row_id]['is_active'] = True
    for row_id in plan['hide']:
        by_id[row_id]['is_active'] = False
    return rows


class SheetReadTests(unittest.TestCase):

    def test_real_layout(self):
        rows, memo = sheet.read_csv_text('﻿' + REAL)
        self.assertEqual([(row['line'], row['city']) for row in rows],
                         [(2, 'Актау'), (3, 'Алматы'), (4, 'Алматы'), (5, 'Астана'), (6, 'Туркестан')])
        self.assertEqual(sheet.parse_fields(rows[0]), {
            'free_boxes': 11, 'thermo_bags': 0, 'used_boxes': 1, 'tariff': 'auto_couriers',
            'min_orders': 15, 'period_days': 7, 'deposit_tenge': 5000, 'special_condition': None})
        self.assertEqual(sheet.parse_fields(rows[4])['deposit_tenge'], 0)
        self.assertEqual(memo['title'], 'Термопакеты и термокороба — правила выдачи')
        self.assertEqual([(item['kind'], item['owner']) for item in memo['items']],
                         [('forbidden', 'kc'), ('forbidden', 'kc'), ('data', 'regions')])
        self.assertEqual(memo['items'][0]['text'], '2 ДОНГЕЛЕК — В ОФИСАХ НЕ ОБСЛУЖИВАЕТСЯ.\nНЕ ОТПРАВЛЯТЬ курьеров')
        self.assertEqual(memo['items'][2]['text'], 'Информация о наличии должна соответствовать таблице')

    def test_fraction_is_not_a_whole_number(self):
        self.assertEqual(sheet.parse_value('free_boxes', '11.0'), 11)
        self.assertEqual(sheet.parse_value('free_boxes', '11,00'), 11)
        for bad in ('11.5', '-3', 'много', ''):
            with self.assertRaises(rules.FieldError, msg=bad):
                sheet.parse_value('free_boxes', bad)

    def test_rebuilt_sheet_is_refused(self):
        with self.assertRaises(sheet.SheetError):
            sheet.read_csv_text('Офис,Коробы\nАлматы,5\n')
        with self.assertRaises(sheet.SheetError):
            sheet.read_csv_text(HEADER + '\n')
        with self.assertRaises(sheet.SheetError):
            sheet.read_csv_text('')

    def test_merged_city_cell_is_the_city_above(self):
        # Объединённая ячейка «Город»: выгрузка оставляет город только в верхней строке.
        merged = REAL.replace('"Алматы","ул. Жамбыла, 172"', '"","ул. Жамбыла, 172"')
        rows, _ = sheet.read_csv_text(merged)
        self.assertEqual([(row['line'], row['city']) for row in rows[1:3]], [(3, 'Алматы'), (4, 'Алматы')])
        # Строка без города, оторванная от офисов пустой строкой, чужой город не получает.
        rows, _ = sheet.read_csv_text(REAL + '\n' + office_line('', 'ул. Абая, 1') + '\n')
        self.assertEqual((rows[-1]['line'], rows[-1]['city'], rows[-1]['address']), (8, '', 'ул. Абая, 1'))
        # Пустые строки и строки одной памятки справа — не офисы.
        rows, _ = sheet.read_csv_text(REAL + ',,,,,,,,,,,⛔,Пункт,КЦ\n')
        self.assertEqual(len(rows), 5)


class PlanTests(unittest.TestCase):

    def plan(self, text, existing, directory=()):
        rows, _ = sheet.read_csv_text(text)
        return sheet.plan_mirror(rows, existing, list(directory))

    def keyed(self):
        """Строки раздела после первого чтения листа: у каждой — ключ своей строки."""
        return apply_plan(existing_rows(), self.plan(REAL, existing_rows()))

    def test_first_read_recognises_transferred_rows(self):
        existing = existing_rows()
        plan = self.plan(REAL, existing)
        self.assertEqual(plan['problems'], [])
        self.assertEqual((plan['creates'], plan['hide'], plan['show']), ([], [], []))
        # Ключ листа запомнен у всех пяти — дальше строки узнаются по нему.
        self.assertEqual(sorted(item['row_id'] for item in plan['keys']), [1, 2, 3, 4, 5])
        changes = {item['row_id']: item['fields'] for item in plan['updates']}
        # Актау совпал; в Алматы адреса развели строки; Туркестан узнан как
        # единственный офис города, хотя дом в листе 14, а в вики 15.
        self.assertEqual(changes, {
            2: {'free_boxes': 18},
            4: {'free_boxes': 136},
            5: {'free_boxes': 55},
        })

    def test_repeat_changes_nothing(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        plan = self.plan(REAL, existing)
        self.assertEqual(plan, {'updates': [], 'creates': [], 'keys': [], 'hide': [], 'show': [], 'problems': []})

    def test_edit_in_sheet_is_one_change(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        edited = REAL.replace('"Астана","пр. Сарыарка, 31","136"', '"Астана","пр. Сарыарка, 31","130"')
        plan = self.plan(edited, existing)
        self.assertEqual([(item['row_id'], item['diff']) for item in plan['updates']],
                         [(4, [{'field': 'free_boxes', 'from': 136, 'to': 130}])])
        self.assertEqual(plan['keys'], [])

    def test_rewritten_address_keeps_the_row(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        moved = REAL.replace('пр. Сарыарка, 31', 'проспект Сарыарка, 31, 2 этаж')
        plan = self.plan(moved, existing)
        self.assertEqual(plan['hide'], [])
        self.assertEqual([item['row_id'] for item in plan['keys']], [4])

    def test_new_office_from_directory_is_added(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        text = REAL + office_line('Актобе', '11-й мкр, 3Д', free='7') + '\n'
        plan = self.plan(text, existing, directory=[
            {'id': 50, 'city': 'Актобе', 'name': 'Офис Актобе', 'address': '11 микрорайон, 3Д'}])
        (created,) = plan['creates']
        self.assertEqual((created['office']['id'], created['fields']['free_boxes']), (50, 7))
        self.assertEqual(created['sheet_key'], sheet.sheet_key('Актобе', '11-й мкр, 3Д'))

    def test_unknown_office_is_reported_and_nothing_else_breaks(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        plan = self.plan(REAL + office_line('Павлодар', 'ул. Ленина, 1') + '\n', existing)
        self.assertEqual(plan['creates'], [])
        self.assertEqual(plan['hide'], [])
        (problem,) = plan['problems']
        self.assertIn('Павлодар', problem)
        self.assertIn('строка 7 листа', problem)

    def test_office_gone_from_sheet_is_hidden_and_comes_back(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        without = REAL.replace(office_line('Туркестан', 'ул. Нышанов, 14, БЦ «Балнур»', free='55',
                                           orders='15+заказов', deposit='Нет депозита') + '\n', '')
        plan = self.plan(without, existing)
        self.assertEqual(plan['hide'], [5])
        apply_plan(existing, plan)
        plan = self.plan(REAL, existing)
        self.assertEqual((plan['show'], plan['hide'], plan['updates']), ([5], [], []))

    def test_typo_in_a_cell_keeps_the_office_and_its_numbers(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        typo = REAL.replace('"Астана","пр. Сарыарка, 31","136"', '"Астана","пр. Сарыарка, 31","13б"')
        plan = self.plan(typo, existing)
        self.assertEqual((plan['updates'], plan['hide']), ([], []))
        (problem,) = plan['problems']
        self.assertIn('Астана', problem)
        self.assertIn('13б', problem)

    def test_most_offices_gone_means_a_rebuilt_sheet(self):
        existing = apply_plan(existing_rows(), self.plan(REAL, existing_rows()))
        only_one = sheet_csv(office_line('Актау', 'мкр. 2, д. 47А, БЦ «Орда»', free='11', used='1',
                                         orders='15+заказов'))
        with self.assertRaises(sheet.SheetError):
            self.plan(only_one, existing)

    def test_merged_city_cell_keeps_the_office(self):
        merged = REAL.replace('"Алматы","ул. Жамбыла, 172"', '"","ул. Жамбыла, 172"')
        self.assertEqual(self.plan(merged, self.keyed()),
                         {'updates': [], 'creates': [], 'keys': [], 'hide': [], 'show': [], 'problems': []})

    def test_line_without_city_holds_every_office(self):
        text = REAL.replace(TURKESTAN_LINE + '\n', '') + '\n' + office_line('', 'ул. Нышанов, 14') + '\n'
        plan = self.plan(text, self.keyed())
        self.assertEqual(plan['hide'], [])
        line, held = plan['problems']
        self.assertIn('не указан город', line)
        self.assertIn('Туркестан', held)

    def test_typo_in_a_city_name_hides_nothing(self):
        typo = REAL.replace('"Астана","пр. Сарыарка, 31"', '"Астанаа","пр. Сарыарка, 31"')
        plan = self.plan(typo, self.keyed())
        self.assertEqual((plan['hide'], plan['updates'], plan['creates']), ([], [], []))
        line, held = plan['problems']
        self.assertIn('города «Астанаа» нет', line)
        self.assertIn('Астана, Проспект Сарыарка', held)

    def test_unrecognised_line_holds_its_city_only(self):
        # Из листа ушли Жамбыла и Туркестан, а в Алматы появилась строка, которую не узнать:
        # Жамбыла остаётся (может, это она с опечаткой), Туркестан убирается.
        text = (REAL.replace('"Алматы","ул. Жамбыла, 172","0"', '"Алматы","ул. Абая, 1","9"')
                .replace(TURKESTAN_LINE + '\n', ''))
        plan = self.plan(text, self.keyed())
        self.assertEqual(plan['hide'], [5])
        self.assertEqual((plan['updates'], plan['keys']), ([], []))
        line, held = plan['problems']
        self.assertIn('ул. Абая, 1', line)
        self.assertIn('офис не узнан', line)
        self.assertIn('Жамбыла', held)
        self.assertNotIn('Туркестан', held)

    def test_hidden_office_is_not_taken_by_a_new_line_of_its_city(self):
        existing = self.keyed() + [
            _row(6, 'Актобе', office_id=60, address='11 микрорайон, 3Д',
                 sheet_key=sheet.sheet_key('Актобе', '11-й мкр, 3Д')),
            _row(7, 'Актобе', office_id=51, address='ул. Абая, 10', is_active=False),
        ]
        text = (REAL + office_line('Актобе', '11-й мкр, 3Д', free='7') + '\n'
                + office_line('Актобе', 'пр. Санкибай батыра, 1', free='40') + '\n')
        plan = self.plan(text, existing, directory=[
            {'id': 77, 'city': 'Актобе', 'name': 'Офис Актобе 2', 'address': 'проспект Санкибай батыра, 1'}])
        self.assertEqual(plan['show'], [])
        self.assertNotIn(7, [item['row_id'] for item in plan['updates'] + plan['keys']])
        (created,) = plan['creates']
        self.assertEqual((created['office']['id'], created['fields']['free_boxes']), (77, 40))
        self.assertEqual(plan['problems'], [])

    def test_copied_line_is_reported_and_binds_nothing(self):
        existing = self.keyed() + [_row(6, 'Алматы', office_id=46, address='ул. Абылай хана, 1', is_active=False)]
        plan = self.plan(REAL + office_line('Алматы', '7-й микрорайон, 5', free='18') + '\n', existing)
        self.assertEqual((plan['show'], plan['keys'], plan['updates'], plan['hide'], plan['creates']),
                         ([], [], [], [], []))
        (problem,) = plan['problems']
        self.assertIn('строка 7 листа', problem)
        self.assertIn('повторяет строку 3 листа', problem)

    def test_house_number_differs_in_a_city_with_several_offices(self):
        # Первое чтение: в листе «Жамбыла, 170», в справочнике «Жамбыла, 172В». Вторая
        # строка Алматы уже узнана по адресу — оставшиеся строка листа и строка раздела
        # Алматы — один офис, как с Туркестаном.
        plan = self.plan(REAL.replace('ул. Жамбыла, 172', 'ул. Жамбыла, 170'), existing_rows())
        self.assertEqual((plan['problems'], plan['hide'], plan['creates']), ([], [], []))
        self.assertEqual(sorted(item['row_id'] for item in plan['keys']), [1, 2, 3, 4, 5])

    def test_new_line_of_a_city_does_not_take_a_removed_office(self):
        # Жамбылу из листа убрали, в Алматы добавили офис на другой улице. Строка Жамбылы
        # уже видела лист: новую строку ей не отдаём — иначе на экране был бы адрес
        # Жамбылы с цифрами нового офиса.
        text = REAL.replace('"Алматы","ул. Жамбыла, 172","0"', '"Алматы","ул. Абая, 1","9"')
        plan = self.plan(text, self.keyed(), directory=[
            {'id': 45, 'city': 'Алматы', 'name': 'Офис Абая', 'address': 'проспект Абая, 1'}])
        self.assertEqual((plan['updates'], plan['keys'], plan['problems']), ([], [], []))
        (created,) = plan['creates']
        self.assertEqual((created['office']['id'], created['fields']['free_boxes']), (45, 9))
        # Новая строка узнана как новый офис, а Жамбылы в листе больше нет — она убирается.
        self.assertEqual(plan['hide'], [3])

    def test_address_edited_on_the_same_street_keeps_the_row(self):
        text = REAL.replace('ул. Нышанов, 14, БЦ «Балнур»', 'ул. Нышанов, 14А, БЦ «Балнур»')
        plan = self.plan(text, self.keyed())
        self.assertEqual((plan['problems'], plan['hide'], plan['creates']), ([], [], []))
        self.assertEqual([item['row_id'] for item in plan['keys']], [5])

    def test_loose_directory_match_is_left_to_a_person(self):
        text = REAL + office_line('Шымкент', 'ул. Тауке хана, 15', free='4') + '\n'
        plan = self.plan(text, self.keyed(), directory=[
            {'id': 90, 'city': 'Шымкент', 'name': 'Офис Шымкент', 'address': 'ул. Тауке хана, 15А'}])
        self.assertEqual(plan['creates'], [])
        (problem,) = plan['problems']
        self.assertIn('дом совпал без литеры', problem)
        self.assertIn('id 90', problem)

    def test_mass_hide_guard(self):
        def rows(count):
            return [_row(n, 'Город %d' % n, address='ул. Абая, %d' % n,
                         sheet_key=sheet.sheet_key('Город %d' % n, 'ул. Абая, %d' % n)) for n in range(1, count + 1)]

        def sheet_of(kept):
            return sheet_csv(*(office_line('Город %d' % n, 'ул. Абая, %d' % n) for n in kept))

        # Один офис убирается всегда — даже один из двух.
        self.assertEqual(self.plan(sheet_of([1]), rows(2))['hide'], [2])
        # Два из трёх и ровно половина — это переделанный лист.
        for kept, total in (([1], 3), ([1, 2], 4)):
            with self.assertRaises(sheet.SheetError, msg=(kept, total)):
                self.plan(sheet_of(kept), rows(total))
        # 7 из 15 — ещё закрытые офисы, 8 из 15 — уже нет.
        self.assertEqual(len(self.plan(sheet_of(range(1, 9)), rows(15))['hide']), 7)
        with self.assertRaises(sheet.SheetError):
            self.plan(sheet_of(range(1, 8)), rows(15))

    def test_memo_compare_ignores_service_fields(self):
        _, memo = sheet.read_csv_text(REAL)
        clean = rules.clean_memo(memo)
        stored = dict(clean, updated_by_name='Google-таблица', updated_at='2026-11-24T10:00:00')
        self.assertFalse(sheet.memo_differs(stored, clean))
        self.assertTrue(sheet.memo_differs(dict(stored, items=stored['items'][:1]), clean))


class _Response:
    def __init__(self, status, content_type, body):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {'content-type': content_type}
        self.content = body.encode('utf-8')


class _Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if self.error:
            raise self.error
        return self.response


SA_EMAIL = 'portal@example.iam.gserviceaccount.com'
ACCOUNT = 'reader@example.com'
OAUTH = {'account': ACCOUNT, 'client_id': 'cid.apps.googleusercontent.com', 'client_secret': 'sec',
         'refresh_token': '1//refresh'}


class FetchTests(unittest.TestCase):

    def fetch(self, session, kind='service'):
        return sync.fetch_csv(session, who=SA_EMAIL if kind == 'service' else ACCOUNT, kind=kind)

    def test_reads_only_the_conditions_sheet(self):
        session = _Session(_Response(200, 'text/csv', '\ufeff' + REAL))
        self.assertEqual(self.fetch(session), REAL)
        ((url, params, timeout),) = session.calls
        self.assertIn(sync.SHEET_FILE_ID, url)
        # Один лист по gid, CSV — не весь файл с журналами выдачи и ФИО курьеров.
        self.assertEqual(params, {'format': 'csv', 'gid': sync.SHEET_GID})
        self.assertEqual(timeout, sync.TIMEOUT_SECONDS)

    def test_no_access_says_who_reads_and_what_to_do(self):
        for response in (_Response(403, 'text/html', '<title>Доступ закрыт</title>'),
                         _Response(200, 'text/html; charset=utf-8', '<html>вход</html>'),
                         _Response(404, 'text/html', '')):
            with self.assertRaises(sync.SyncError) as caught:
                self.fetch(_Session(response))
            # Кто читает и что сделать: вход аккаунта, которому таблица открыта, — или доступ учётке.
            self.assertIn(SA_EMAIL, str(caught.exception))
            self.assertIn(sync.AUTH_SCRIPT, str(caught.exception))
            self.assertIn('владельца файла', str(caught.exception))
            with self.assertRaises(sync.SyncError) as caught:
                self.fetch(_Session(response), kind='oauth')
            self.assertIn('у %s' % ACCOUNT, str(caught.exception))

    def test_disabled_drive_api_is_named_as_such(self):
        body = '{"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [{"reason": "SERVICE_DISABLED"}]}}'
        with self.assertRaises(sync.SyncError) as caught:
            self.fetch(_Session(_Response(403, 'application/json', body)), kind='oauth')
        self.assertIn('Drive API', str(caught.exception))
        self.assertNotIn('Нет доступа', str(caught.exception))

    def test_revoked_account_login_asks_to_confirm_again(self):
        from google.auth.exceptions import RefreshError

        with self.assertRaises(sync.SyncError) as caught:
            self.fetch(_Session(error=RefreshError('invalid_grant: Token has been expired or revoked.')), kind='oauth')
        message = str(caught.exception)
        self.assertIn(ACCOUNT, message)
        self.assertIn('заново', message)
        self.assertIn(sync.AUTH_SCRIPT, message)

    def test_other_failures_are_errors_too(self):
        cases = (_Session(_Response(500, 'text/html', '')), _Session(error=TimeoutError('read timed out')),
                 _Session(_Response(200, 'application/pdf', '%PDF')))
        for session in cases:
            with self.assertRaises(sync.SyncError):
                self.fetch(session)


class ReaderTests(unittest.TestCase):
    """Кто читает: вход аккаунта, если настроен, иначе сервисная учётка."""

    def reader(self, oauth=None, sa=None, dev_file=None):
        from call_qa import config

        env = {sync.OAUTH_ENV: oauth} if oauth is not None else {}
        # .env.codex.local машины разработчика подменён: настоящий вход в нём
        # не должен решать за тест, кто читает.
        with mock.patch.dict('os.environ', env, clear=False), \
                mock.patch.object(config, '_dev_env', lambda: dict(dev_file or {})), \
                mock.patch.object(sync, 'service_account_info', lambda: sa):
            if oauth is None:
                import os
                os.environ.pop(sync.OAUTH_ENV, None)
            return sync.reader()

    def test_account_login_goes_first(self):
        session, who, kind = self.reader(json.dumps(OAUTH), sa={'client_email': SA_EMAIL})
        self.assertEqual((who, kind), (ACCOUNT, 'oauth'))
        credentials = session.credentials
        self.assertEqual((credentials.refresh_token, credentials.client_id), ('1//refresh', OAUTH['client_id']))
        self.assertEqual(tuple(credentials.scopes), sync.SCOPES)

    def test_broken_login_is_an_error_not_a_silent_fallback(self):
        for raw, part in (('{не json', 'не JSON'), (json.dumps({'client_id': 'x'}), 'refresh_token')):
            with self.assertRaises(sync.SyncError) as caught:
                self.reader(raw, sa={'client_email': SA_EMAIL})
            self.assertIn(part, str(caught.exception))

    def test_service_account_when_no_login(self):
        fake = mock.Mock(name='sa-credentials')
        with mock.patch('google.oauth2.service_account.Credentials.from_service_account_info',
                        return_value=fake) as build:
            _, who, kind = self.reader(sa={'client_email': SA_EMAIL})
        self.assertEqual((who, kind), (SA_EMAIL, 'service'))
        self.assertEqual(build.call_args.kwargs['scopes'], list(sync.SCOPES))

    def test_nothing_configured(self):
        with self.assertRaises(sync.SyncError) as caught:
            self.reader(sa=None)
        self.assertIn(sync.OAUTH_ENV, str(caught.exception))

    def test_login_written_by_the_script_is_read_locally(self):
        # Скрипт входа пишет вход в .env.codex.local, а монолит dotenv не грузит:
        # сервер на машине разработчика должен читать его оттуда, как и ключ учётки.
        _, who, kind = self.reader(sa={'client_email': SA_EMAIL}, dev_file={sync.OAUTH_ENV: json.dumps(OAUTH)})
        self.assertEqual((who, kind), (ACCOUNT, 'oauth'))


class AuthScriptTests(unittest.TestCase):
    """Однократный вход аккаунта: разбор клиента, PKCE, почта из id_token, запись в .env."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))
        import thermoboxes_google_auth
        self.script = thermoboxes_google_auth

    def test_client_json_from_console(self):
        self.assertEqual(self.script.client_from_json({'installed': {'client_id': 'a', 'client_secret': 'b'}}),
                         ('a', 'b'))
        with self.assertRaises(SystemExit):
            self.script.client_from_json({'installed': {'client_id': 'a'}})

    def test_pkce_challenge_is_sha256_of_verifier(self):
        import base64
        import hashlib

        verifier, challenge = self.script.pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge, expected)

    def test_email_from_id_token(self):
        import base64

        payload = base64.urlsafe_b64encode(json.dumps({'email': ACCOUNT}).encode()).rstrip(b'=').decode()
        self.assertEqual(self.script.id_token_email('h.%s.s' % payload), ACCOUNT)
        self.assertIsNone(self.script.id_token_email(None))
        self.assertIsNone(self.script.id_token_email('мусор'))

    def test_env_line_is_replaced_or_appended(self):
        value = self.script.env_value(ACCOUNT, 'cid', 'sec', 'rt')
        self.assertEqual(json.loads(value), {'account': ACCOUNT, 'client_id': 'cid', 'client_secret': 'sec',
                                             'refresh_token': 'rt'})
        appended = self.script.upsert_env_line('A=1\n', sync.OAUTH_ENV, value)
        self.assertTrue(appended.startswith('A=1\n\n#'))
        self.assertTrue(appended.endswith('%s=%s\n' % (sync.OAUTH_ENV, value)))
        replaced = self.script.upsert_env_line(appended, sync.OAUTH_ENV, 'new')
        self.assertEqual(replaced.count(sync.OAUTH_ENV + '='), 1)
        self.assertIn('%s=new' % sync.OAUTH_ENV, replaced)
        self.assertIn('A=1', replaced)


@unittest.skipIf(Flask is None, 'Flask не установлен')
class RunTests(_Base):
    """Прогон целиком на подменённом слое запросов (тот же Store, что у ручек)."""

    def setUp(self):
        super().setUp()
        self.store.rows = {row['id']: row for row in existing_rows()}
        self.store.events = []

    def run_sync(self, text=REAL):
        return sync.run(self.db, triggered_by='test', fetch=lambda: text)

    def test_first_read_updates_rows_and_writes_history_as_the_sheet(self):
        result = self.run_sync()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.store.rows[4]['free_boxes'], 136)
        self.assertEqual(self.store.rows[4]['updated_by_name'], 'Google-таблица')
        self.assertEqual(sorted((row_id, kind) for row_id, kind, _, _ in self.store.events),
                         [(2, 'edited'), (4, 'edited'), (5, 'edited')])
        self.assertTrue(all(actor is None for *_, actor in self.store.events))
        self.assertEqual(self.store.memo['title'], 'Термопакеты и термокороба — правила выдачи')
        state = self.store.sync_state
        self.assertEqual((state['status'], state['problems']), ('ok', []))
        self.assertEqual(state['changed_at'], state['read_at'])

    def test_repeat_writes_only_the_check_mark(self):
        self.run_sync()
        events, memo_saves = len(self.store.events), self.store.memo_saves
        self.store.sync_state['changed_at'] = '2026-11-24T09:00:00'
        self.run_sync()
        self.assertEqual(len(self.store.events), events)
        self.assertEqual(self.store.memo_saves, memo_saves)
        self.assertEqual(self.store.sync_state['changed_at'], '2026-11-24T09:00:00')
        self.assertEqual(self.store.sync_state['status'], 'ok')

    def test_unreadable_sheet_leaves_the_data_alone(self):
        before = {row_id: dict(row) for row_id, row in self.store.rows.items()}

        def no_access():
            raise sync.SyncError('Нет доступа к Google-таблице: откройте её на чтение адресу x@y')

        result = sync.run(self.db, triggered_by='test', fetch=no_access)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.store.rows, before)
        self.assertEqual(self.store.events, [])
        self.assertEqual(self.store.sync_state['status'], 'error')
        self.assertIn('x@y', self.store.sync_state['error'])
        self.assertIsNone(self.store.sync_state['read_at'])

    def test_same_error_is_logged_once(self):
        def no_access():
            raise sync.SyncError('Нет доступа к Google-таблице')

        with mock.patch.object(sync.logging, 'warning') as warning, mock.patch.object(sync.logging, 'debug') as debug:
            for _ in range(3):
                sync.run(self.db, triggered_by='test', fetch=no_access)
        self.assertEqual((warning.call_count, debug.call_count), (1, 2))

    def test_rebuilt_sheet_leaves_the_data_alone(self):
        result = self.run_sync('Офис,Коробы\nАлматы,5\n')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.store.events, [])

    def test_too_long_memo_item_does_not_stop_the_numbers(self):
        long_memo = REAL.replace('В офисах СНЯТЬ деньги нельзя.', 'Д' * (rules.MEMO_TEXT_MAX + 1))
        result = self.run_sync(long_memo)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.store.rows[4]['free_boxes'], 136)
        self.assertTrue(any(problem.startswith('Памятка') for problem in result['problems']))

    def test_database_error_is_recorded_not_raised(self):
        def broken(*args, **kwargs):
            raise RuntimeError('value too long for type character varying(200)\nDETAIL: …')

        with mock.patch.object(sync.queries, 'update_row', broken), \
                mock.patch.object(sync.logging, 'error') as error, mock.patch.object(sync.logging, 'debug') as debug:
            first, second = self.run_sync(), self.run_sync()
        self.assertEqual((first['status'], second['status']), ('error', 'error'))
        state = self.store.sync_state
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['error'], 'Лист прочитан, но не записан: value too long for type character varying(200)')
        self.assertIsNone(state['read_at'])
        # Та же поломка второй раз — не новость для лога; оба прогона откатились.
        self.assertEqual((error.call_count, debug.call_count), (1, 1))
        self.assertEqual(self.db.rollbacks, 2)


@unittest.skipIf(Flask is None, 'Flask не установлен')
class MirrorRouteTests(_Base):
    """Раздел — зеркало: правки закрыты, источник виден, перечитать — админу."""

    mirror = True

    def test_nobody_edits_in_the_section(self):
        self.assertTrue(access.SHEET_MIRROR)
        for viewer in (person(), person(role='super_admin', department_code=None),
                       person(role='sv', department_code='szov'),
                       person(role='admin', department_code=None, headed_codes=('front_office',))):
            caps = access.capabilities(viewer)
            self.assertFalse(caps['can_edit'] or caps['can_manage'] or caps['can_edit_memo'], viewer['role'])
        self.viewer = person(role='super_admin', department_code=None)
        calls = (
            ('put', '/api/thermoboxes/rows', {'items': [{'id': 1, 'version': 1, 'free_boxes': 3}]}),
            ('post', '/api/thermoboxes/rows', {'office_id': 50}),
            ('patch', '/api/thermoboxes/rows/1/visibility', {'is_active': False}),
            ('put', '/api/thermoboxes/memo', {'title': 'x', 'items': []}),
        )
        for method, url, body in calls:
            response = getattr(self.client, method)(url, json=body)
            self.assertEqual(response.status_code, 409, url)
            self.assertEqual(response.get_json()['code'], 'THERMO_SHEET_MIRROR', url)
        self.assertEqual((self.store.updates, self.store.events), ([], []))

    def test_screen_tells_where_the_numbers_come_from(self):
        self.store.sync_state.update(status='error', error='Нет доступа к Google-таблице',
                                     problems=['строка 7 листа (Павлодар, ул. Ленина, 1): нет офиса'],
                                     checked_at='2026-11-24T10:05:00', read_at='2026-11-24T09:40:00')
        operator = self.client.get('/api/thermoboxes').get_json()['source']
        self.assertEqual(operator, {'kind': 'google_sheet', 'status': 'error', 'checked_at': '2026-11-24T10:05:00',
                                    'read_at': '2026-11-24T09:40:00', 'changed_at': None})
        self.viewer = person(role='super_admin', department_code=None)
        data = self.client.get('/api/thermoboxes').get_json()
        self.assertTrue(data['capabilities']['can_sync'])
        self.assertEqual(data['source']['error'], 'Нет доступа к Google-таблице')
        self.assertEqual(len(data['source']['problems']), 1)
        self.assertIn(sync.SHEET_FILE_ID, data['source']['url'])

    def test_source_is_light_and_follows_rights(self):
        self.store.sync_state.update(status='error', error='Нет доступа к Google-таблице',
                                     problems=['строка 7 листа: не узнан'], checked_at='2026-11-24T10:05:00',
                                     read_at='2026-11-24T09:40:00', changed_at='2026-11-24T09:00:00')
        with mock.patch.object(queries, 'list_rows', side_effect=AssertionError('строки опрос не читает')):
            operator = self.client.get('/api/thermoboxes/source')
            self.assertEqual(operator.status_code, 200)
            self.assertEqual(operator.get_json()['source'], {
                'kind': 'google_sheet', 'status': 'error', 'checked_at': '2026-11-24T10:05:00',
                'read_at': '2026-11-24T09:40:00', 'changed_at': '2026-11-24T09:00:00'})
            self.viewer = person(role='super_admin', department_code=None)
            admin = self.client.get('/api/thermoboxes/source').get_json()['source']
        self.assertEqual((admin['error'], admin['problems']), ('Нет доступа к Google-таблице', ['строка 7 листа: не узнан']))
        # Не своему разделу — 403, как у экрана.
        self.viewer = person(role='operator', department_code='hr')
        self.assertEqual(self.client.get('/api/thermoboxes/source').status_code, 403)
        # Правку вернули в раздел — опрашивать нечего.
        self.viewer = person(role='super_admin', department_code=None)
        with mock.patch.object(access, 'SHEET_MIRROR', False):
            self.assertEqual(self.client.get('/api/thermoboxes/source').get_json(), {'source': None})

    def test_sync_now_is_for_global_admin(self):
        response = self.client.post('/api/thermoboxes/sync', json={})
        self.assertEqual(response.status_code, 403)
        self.viewer = person(role='super_admin', department_code=None)
        self.store.rows = {row['id']: row for row in existing_rows()}
        with mock.patch.object(sync, 'fetch_csv', lambda: REAL):
            data = self.client.post('/api/thermoboxes/sync', json={}).get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['summary']['updated'], 3)
        self.assertEqual(data['source']['status'], 'ok')
        self.assertEqual(self.store.rows[5]['free_boxes'], 55)


class CreateRowTests(unittest.TestCase):
    """Зеркало заводит офис из справочника вики сам — длинное имя не должно ронять прогон."""

    def test_long_directory_name_is_cut_to_the_column(self):
        from thermoboxes import queries

        class Cursor:
            def execute(self, sql, params):
                self.sql, self.params = sql, params

            def fetchone(self):
                return (1,)

        cursor = Cursor()
        office = {'id': 7, 'city': 'Алматы', 'name': 'Я' * 255, 'address': 'ул. Абая, 1'}
        queries.create_row(cursor, office, {}, sync.ACTOR)
        # В справочнике вики имя до 255 знаков, в thermobox_offices.name — до 200.
        self.assertEqual(cursor.params[:2], [7, 'Алматы'])
        self.assertEqual(cursor.params[2], 'Я' * 200)


class WiringTests(unittest.TestCase):

    def test_scheduler_reads_the_sheet_every_minute(self):
        # «Чтобы таблица обновлялась, когда внесены правки»: лист читается каждую
        # минуту — правка в таблице в разделе в течение минуты.
        source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        self.assertIn("thermobox_sheet_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='thermobox-sheet')",
                      source)
        job = source.split("id='thermoboxes_sheet_sync'")[0].rsplit('scheduler.add_job(', 1)[1]
        self.assertIn('run_thermoboxes_sheet_sync_async', job)
        self.assertIn("CronTrigger(minute='*', timezone=ZoneInfo('Asia/Almaty'))", job)
        options = source.split("id='thermoboxes_sheet_sync'")[1].split(')\n', 1)[0]
        self.assertIn('max_instances=1', options)
        self.assertIn('coalesce=True', options)
        self.assertIn('return thermoboxes_sync.run(db, triggered_by=\'scheduler\')', source)
        self.assertIn('run_in_executor(thermobox_sheet_pool, thermoboxes_sheet_sync_job)', source)

    def test_screen_shows_the_source_and_the_sync_button(self):
        view = (ROOT / 'src' / 'components' / 'thermoboxes' / 'ThermoboxesView.jsx').read_text(encoding='utf-8')
        self.assertIn('<SectionTitle updated={updated} source={source} actions={headerActions} />', view)
        menu = view.split('const menuItems = [')[1].split('];')[0]
        self.assertIn("capabilities.can_sync && { key: 'sync'", menu)
        self.assertIn('icon: RefreshCw', menu)
        self.assertIn('/api/thermoboxes/sync', view)

    def test_open_screen_picks_up_a_sheet_edit_by_itself(self):
        # Открытый экран сверяет /source по тычку живого канала (сервер прочитал
        # лист, thermoboxes/live.py), при возврате на вкладку и — пока канала нет
        # или он замолчал — раз в SOURCE_POLL_MS; строки перечитывает, только
        # когда сдвинулся changed_at, и подсвечивает, что поменялось. Поведение
        # канала и сверки по одной — tests/thermobox_live.test.mjs.
        view = (ROOT / 'src' / 'components' / 'thermoboxes' / 'ThermoboxesView.jsx').read_text(encoding='utf-8')
        once = view.split('const checkOnce = useCallback(')[1].split('}, [apiBaseUrl, headers, applyFresh]);')[0]
        self.assertIn('/api/thermoboxes/source', once)
        self.assertIn('timeout: CHECK_TIMEOUT_MS', once)
        # Сравнение — с тем, что на экране в момент ответа, и опоздавший ответ не ложится поверх нового.
        self.assertIn('const current = dataRef.current;', once.split('/api/thermoboxes/source')[1])
        self.assertIn('!sourceNotOlder(current.source, source)', once)
        self.assertIn('source.changed_at === current.source.changed_at', once)
        self.assertIn('applyFresh(response.data, { quiet: syncingRef.current })', once)
        check = view.split('const checkSource = useMemo(() => createSourceCheck({')[1].split('}), []);')[0]
        self.assertIn('run: () => checkOnceRef.current()', check)
        self.assertIn("document.visibilityState === 'visible'", check)
        self.assertIn('window.setInterval(() => { if (!liveRef.current) checkSource(); }, SOURCE_POLL_MS)', view)
        live = view.split('const watcher = watchSheetSource({')[1].split('});')[0]
        self.assertIn('`${apiBaseUrl}/api/thermoboxes/stream`', live)
        self.assertIn('runStream: runSseStream', live)
        self.assertIn('onPulse: checkSource', live)
        self.assertIn('onLive: (value) => { liveRef.current = value; }', live)
        fresh = view.split('const applyFresh = useCallback(')[1].split('}, [toast]);')[0]
        self.assertIn('if (current && !sourceNotOlder(current.source, next.source)) return;', fresh)
        sync_now = view.split('const syncNow = async () => {')[1].split('\n    };')[0]
        self.assertIn('syncingRef.current = true;', sync_now)
        self.assertIn('syncingRef.current = false;', sync_now.split('finally')[1])
        self.assertIn('rowChanges(current.rows, next.rows)', view)
        self.assertIn('hint={hintOf(row, field)}', view)
        ui = (ROOT / 'src' / 'components' / 'thermoboxes' / 'thermoboxUi.jsx').read_text(encoding='utf-8')
        self.assertIn('tabular-nums text-slate-600', ui)

    def test_import_script_uses_the_same_parser(self):
        script = (ROOT / 'scripts' / 'import_thermoboxes.py').read_text(encoding='utf-8')
        self.assertIn('from thermoboxes.sheet import', script)
        self.assertNotIn('def match_office', script)
        self.assertNotIn('def parse_value', script)


if __name__ == '__main__':
    unittest.main()
