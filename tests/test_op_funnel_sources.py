# -*- coding: utf-8 -*-
"""Разбор источников «Воронки ОП» (op_funnel/sources.py, op_funnel/manual_import.py).

Проверяются те места, где ошибка не видна глазом: к каким суткам отнесён лид,
не сдвинулось ли время, что попало в «встреченные операторы» и что именно
разбирается из файла супервайзера.

Сетевых походов здесь нет — только чистые преобразования.
"""

import os
import sys
import unittest
from datetime import date, datetime
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import manual_import, sources


class MomentTests(unittest.TestCase):

    def test_разбирает_формат_срм(self):
        self.assertEqual(sources.parse_moment('2026-09-05 14:36:52'),
                         datetime(2026, 9, 5, 14, 36, 52))

    def test_разбирает_iso_с_зулу(self):
        self.assertEqual(sources.parse_moment('2026-09-05T14:36:52Z'),
                         datetime(2026, 9, 5, 14, 36, 52))

    def test_пояс_не_сдвигается(self):
        # Времена источников уже местные (Алматы). Перевод в UTC сдвинул бы
        # сутки на пять часов — в проекте это уже ломало отчёт Chat2Desk.
        moment = sources.parse_moment('2026-09-05 23:30:00')
        self.assertEqual(moment.date(), date(2026, 9, 5))
        self.assertEqual(moment.hour, 23)

    def test_пустое_это_none(self):
        for value in (None, '', '   '):
            self.assertIsNone(sources.parse_moment(value))


class StreamRowsTests(unittest.TestCase):

    def lead(self, **over):
        base = {
            'lead_id': 210125, 'owner': 'Ахат Нурали',
            'call_status': 'Дозвон', 'dialog_status': 'Приглашен', 'reject_reason': None,
            'taken_at': '2026-09-06 17:33:03', 'created_at': '2026-09-05 14:36:52',
            'updated_at': '2026-09-11 01:00:18',
            'full_name': 'Муханов Алмас', 'phone': '77082490083',
            'park_name': 'Jana Taxi Алматы', 'base_title': 'Отток', 'comment': '',
        }
        base.update(over)
        return base

    def test_сутки_по_дате_взятия_в_работу(self):
        rows, _ = sources.stream_rows([self.lead()], 1, 'op_potok')
        self.assertEqual(rows[0]['work_day'], date(2026, 9, 6))

    def test_невзятый_лид_ложится_по_дате_загрузки_базы(self):
        rows, _ = sources.stream_rows([self.lead(taken_at=None)], 1, 'op_potok')
        self.assertEqual(rows[0]['work_day'], date(2026, 9, 5))

    def test_лид_без_обеих_дат_не_ложится_никуда(self):
        rows, _ = sources.stream_rows([self.lead(taken_at=None, created_at=None)], 1, 'op_potok')
        self.assertEqual(rows, [])

    def test_номер_потока_попадает_в_строку(self):
        # Потоки 1 и 2 — это группы 14 и 38, разные люди и разная зарплата.
        rows, _ = sources.stream_rows([self.lead()], 2, 'op_potok')
        self.assertEqual(rows[0]['stream_type'], 2)

    def test_оператор_подставляется_по_сопоставлению(self):
        rows, seen = sources.stream_rows([self.lead()], 1, 'op_potok', {'Ахат Нурали': 42})
        self.assertEqual(rows[0]['user_id'], 42)
        self.assertEqual(seen, {'Ахат Нурали': 'Ахат Нурали'})

    def test_несопоставленный_оператор_не_теряет_лид(self):
        # Лид уйдёт в строку «не сопоставлен», а не пропадёт из суммы.
        rows, seen = sources.stream_rows([self.lead()], 1, 'op_potok', {})
        self.assertIsNone(rows[0]['user_id'])
        self.assertEqual(rows[0]['owner_raw'], 'Ахат Нурали')
        self.assertIn('Ахат Нурали', seen)

    def test_исход_считается_метриками(self):
        rows, _ = sources.stream_rows([self.lead()], 1, 'op_potok')
        self.assertEqual(rows[0]['reach_outcome'], 'dozvon')
        self.assertEqual(rows[0]['dialog_outcome'], 'agree')


class PaidHireRowsTests(unittest.TestCase):

    def lead(self, **over):
        base = {
            'lead_id': 64844, 'owner': 'Сарсенбаева Эльдана',
            'status': 'Перезвон назначен', 'closed_reason_code': 'not_available',
            'closed_reason': 'not_available', 'closed_sub_reason': None,
            'driver_registered_at': '2026-09-09 21:31:37',
            'updated_at': '2026-09-11 11:38:47',
            'full_name': 'Арап Әбдірайм', 'phone': '7077060298',
            'city': 'Астана', 'park_name': 'Global Астана', 'comment': 'взял 3 лицо',
        }
        base.update(over)
        return base

    def test_сутки_по_дате_регистрации_водителя(self):
        rows, _ = sources.paid_hire_rows([self.lead()], 'op_yandex_reg')
        self.assertEqual(rows[0]['work_day'], date(2026, 9, 9))

    def test_код_причины_переводит_лид_в_недозвон(self):
        rows, _ = sources.paid_hire_rows([self.lead()], 'op_yandex_reg')
        self.assertEqual(rows[0]['reach_outcome'], 'nedozvon')
        self.assertEqual(rows[0]['reason_bucket'], 'nedozvon')

    def test_без_даты_регистрации_лид_пропускается(self):
        rows, _ = sources.paid_hire_rows([self.lead(driver_registered_at=None)], 'op_yandex_reg')
        self.assertEqual(rows, [])


class AmoRowsTests(unittest.TestCase):

    def lead(self, **over):
        base = {
            'id': 123, 'responsible_user_id': 12666570, 'status_id': 142,
            'pipeline_id': 5524684, 'name': 'Заявка',
            'created_at': 1788000000, 'updated_at': 1788100000,
            'custom_fields_values': [
                {'field_id': sources.AMO_FIELD_PARK, 'values': [{'value': 'Ноль Такси Алматы'}]},
                {'field_id': sources.AMO_FIELD_CITY, 'values': [{'value': 'Алматы'}]},
            ],
        }
        base.update(over)
        return base

    def test_имя_этапа_берётся_из_справочника_своей_воронки(self):
        # В воронке 5524684 статус 142 называется «ПРОШЕЛ РЕГИСТРАЦИЮ», а не
        # «Успешно реализовано» — общий справочник по всем воронкам затирает
        # подпись, и это живой дефект ночной выгрузки amo_leads.py.
        rows, _ = sources.amo_rows([self.lead()], {142: 'ПРОШЕЛ РЕГИСТРАЦИЮ'}, 'op_osnova')
        self.assertEqual(rows[0]['stage_raw'], 'ПРОШЕЛ РЕГИСТРАЦИЮ')
        self.assertEqual(rows[0]['dialog_outcome'], 'success')

    def test_успех_узнаётся_по_номеру_статуса_даже_с_чужой_подписью(self):
        rows, _ = sources.amo_rows([self.lead()], {142: 'Успешно реализовано'}, 'op_osnova')
        self.assertEqual(rows[0]['dialog_outcome'], 'success')

    def test_кастомные_поля_раскладываются(self):
        rows, _ = sources.amo_rows([self.lead()], {142: 'ПРОШЕЛ РЕГИСТРАЦИЮ'}, 'op_osnova')
        self.assertEqual(rows[0]['park_name'], 'Ноль Такси Алматы')
        self.assertEqual(rows[0]['city'], 'Алматы')

    def test_ответственный_остаётся_числом_и_идёт_в_сопоставление(self):
        # /api/v4/users закрыт (403 «Admin access only»), имени у нас нет.
        rows, seen = sources.amo_rows([self.lead()], {142: 'ПРОШЕЛ РЕГИСТРАЦИЮ'}, 'op_osnova')
        self.assertEqual(rows[0]['owner_raw'], '12666570')
        self.assertIn('12666570', seen)

    def test_причина_из_вложения(self):
        lead = self.lead(status_id=143, _embedded={'loss_reason': {'name': 'Нет авто (не цел)'}})
        rows, _ = sources.amo_rows([lead], {143: 'Закрыто и не реализовано'}, 'op_osnova')
        self.assertEqual(rows[0]['reason_bucket'], 'netsel')


class ReasonsFromRowsTests(unittest.TestCase):

    def test_разбивка_считается_из_тех_же_строк(self):
        rows = [
            {'direction_code': 'op_potok', 'work_day': date(2026, 9, 1), 'user_id': 7,
             'reason_bucket': 'otkaz', 'reason_code': 'competitor', 'reason_raw': 'Работа с ДТ'},
            {'direction_code': 'op_potok', 'work_day': date(2026, 9, 1), 'user_id': 7,
             'reason_bucket': 'otkaz', 'reason_code': 'competitor', 'reason_raw': 'Работа с ДТ'},
            {'direction_code': 'op_potok', 'work_day': date(2026, 9, 1), 'user_id': 7,
             'reason_bucket': '', 'reason_code': '', 'reason_raw': ''},
        ]
        found = sources.reasons_from_rows(rows)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['leads'], 2)

    def test_несопоставленный_складывается_в_ноль(self):
        rows = [{'direction_code': 'op_potok', 'work_day': date(2026, 9, 1), 'user_id': None,
                 'reason_bucket': 'otkaz', 'reason_code': 'x', 'reason_raw': 'X'}]
        self.assertEqual(sources.reasons_from_rows(rows)[0]['user_id'], 0)


class TicketsTests(unittest.TestCase):
    """Тикеты обращений СРМ: сутки СМЕНЫ, обработчик, среднее время."""

    def ticket(self, **over):
        base = {'ticket_id': 1, 'created_date': '02.09.2026', 'created_time': '14:00:00',
                'assignee': 'Сарқытова Дана', 'time_to_accept': 100, 'time_to_process': 70}
        base.update(over)
        return base

    def test_ночная_часть_смены_уходит_на_предыдущие_сутки(self):
        # Ночная смена идёт с 20:00 до 08:00, и тикет в 02:00 для супервайзера
        # принадлежит вчерашней смене. Без этого у ночных операторов расходилась
        # половина строк: 15 против 6 и 9 против 3.
        self.assertEqual(sources._ticket_day(self.ticket(created_time='02:15:00')),
                         date(2026, 9, 1))
        self.assertEqual(sources._ticket_day(self.ticket(created_time='07:59:00')),
                         date(2026, 9, 1))

    def test_дневные_часы_остаются_своими_сутками(self):
        self.assertEqual(sources._ticket_day(self.ticket(created_time='08:00:00')),
                         date(2026, 9, 2))
        self.assertEqual(sources._ticket_day(self.ticket(created_time='23:30:00')),
                         date(2026, 9, 2))

    def test_время_обработки_это_ожидание_плюс_работа(self):
        # time_total — весь жизненный цикл обращения (медиана 48 часов), и брать
        # его за «время обработки» нельзя.
        self.assertEqual(sources._ticket_seconds(self.ticket()), 170)
        self.assertIsNone(sources._ticket_seconds(
            self.ticket(time_to_accept=None, time_to_process=None)))

    def test_незакрытые_тикеты_не_занижают_среднее(self):
        # У новых обращений этапы не наступили; считать их нулями значит
        # уполовинить среднее.
        rows = [self.ticket(), self.ticket(time_to_accept=None, time_to_process=None)]
        out = sources.tickets_daily(rows, {'сарқытова дана': 324})
        self.assertEqual(out[0]['tickets'], 2)
        self.assertEqual(out[0]['handle_seconds'], 170.0)

    def test_незанятое_обращение_не_идёт_в_строку_не_сопоставлен(self):
        # «Не указан» — это необработанное обращение, а не человек без связки.
        out = sources.tickets_daily([self.ticket(assignee='Не указан')])
        self.assertEqual(out, [])

    def test_неизвестный_обработчик_остаётся_видимым(self):
        out = sources.tickets_daily([self.ticket(assignee='Новый Сотрудник')], {})
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]['user_id'])
        self.assertEqual(out[0]['assignee'], 'Новый Сотрудник')

    def test_список_обработчиков_для_сопоставления(self):
        rows = [self.ticket(), self.ticket(assignee='Не указан'),
                self.ticket(assignee='Кисапова Айша')]
        self.assertEqual(sources.tickets_assignees_seen(rows),
                         {'Сарқытова Дана': 'Сарқытова Дана', 'Кисапова Айша': 'Кисапова Айша'})


class ManualImportTests(unittest.TestCase):

    def test_дата_из_имени_вкладки_с_хвостовыми_пробелами(self):
        # В живом файле вкладки называются «01.09», «03.09 », «04.09  ».
        self.assertEqual(manual_import.sheet_day('01.09', 2026), date(2026, 9, 1))
        self.assertEqual(manual_import.sheet_day('03.09 ', 2026), date(2026, 9, 3))
        self.assertEqual(manual_import.sheet_day('04.09  ', 2026), date(2026, 9, 4))

    def test_сводные_вкладки_не_считаются_сутками(self):
        for title in ('Воронка', 'Продажи', 'Общий'):
            self.assertIsNone(manual_import.sheet_day(title, 2026), title)

    def test_среднее_время_записано_выражением(self):
        # В файле это «=1367/8» — всего секунд делить на число тикетов.
        self.assertAlmostEqual(manual_import._number('=1367/8'), 170.875)
        self.assertAlmostEqual(manual_import._number('=914/6'), 152.3333, places=3)

    def test_выполнять_содержимое_файла_нельзя(self):
        # Файл приходит извне. Разбор узкий: только цифры и четыре операции.
        for value in ('=__import__("os").system("dir")', '=open("x")', '=A1+B1'):
            self.assertIsNone(manual_import._number(value), value)

    def test_число_с_запятой(self):
        self.assertAlmostEqual(manual_import._number('8,5'), 8.5)

    def test_подтверждённое_соответствие_сильнее_похожести(self):
        people = [{'id': 1, 'name': 'Алан Аман Нурбекұлы'}]
        # «Аман Алан» и «Алан Аман Нурбекұлы» автоматом не сходятся и не должны.
        self.assertIsNone(manual_import._match_person('Аман Алан', people))
        # А подтверждённое человеком соответствие срабатывает.
        self.assertEqual(manual_import._match_person('Аман Алан', people, {'аман алан': 1}), 1)

    def test_совпадение_по_набору_слов(self):
        people = [{'id': 5, 'name': 'Сарсеке Мерей'}]
        self.assertEqual(manual_import._match_person('Мерей  Сарсеке', people), 5)

    def test_одна_буква_разницы_не_считается_совпадением(self):
        # «Саркытова» против «Сарқытова» — это решение человека, не автомата:
        # ошибка поставит чужие часы в чужую строку, и снаружи это не видно.
        people = [{'id': 9, 'name': 'Сарқытова Дана'}]
        self.assertIsNone(manual_import._match_person('Саркытова Дана', people))

    def test_подвал_вкладки_не_принимается_за_операторов(self):
        blob = _workbook_with_footer()
        result = manual_import.parse_manual_workbook(
            blob, 'op_verificator', [{'id': 1, 'name': 'Сарсеке Мерей'}], year=2026)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['unmapped'], [],
                         'нормативы из подвала не должны приезжать как операторы')
        self.assertEqual(result['rows'][0]['chats'], 164)
        self.assertEqual(result['rows'][0]['work_hours'], 9.0)

    def test_повторная_загрузка_исправляет_а_не_удваивает(self):
        # Ключ строки — сутки и оператор, поэтому два одинаковых листа дают одну
        # строку, а не две.
        blob = _workbook_with_footer(duplicate_day=True)
        result = manual_import.parse_manual_workbook(
            blob, 'op_verificator', [{'id': 1, 'name': 'Сарсеке Мерей'}], year=2026)
        self.assertEqual(len(result['rows']), 1)


def _workbook_with_footer(duplicate_day=False):
    """Книга по образцу файла супервайзера: шапка, оператор, итог, блок норм."""
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    titles = ['01.09', '01.09 '] if duplicate_day else ['01.09']
    for title in titles:
        sheet = book.create_sheet(title)
        sheet.append(['Смена', 'ФИО оператора', 'Ставка', 'Отработано часов', 'Чаты WZ',
                      'Тикеты', 'Обработано', 'Чаты/час', '% факт',
                      'Ср. время обработки WZ', 'Разница от таргета',
                      'Ср. время обработки тикет'])
        sheet.append(['День', 'Сарсеке Мерей', 0.75, 9, 164, 6, 170, 18.9, 1.18, 96, 0.25, 152])
        sheet.append(['На смене', 7, None, None, None, None])
        sheet.append([])
        sheet.append([None, None, None, 'День', 'Ночь', 'Вес показателя'])
        sheet.append([None, 'Таргет по времени ответа', None, 120, 180, 0.5])
        sheet.append([None, 'Таргет по обработке чатов в час', None, 16, 12, 0.05])
        sheet.append([None, 'Качество', None, 90, 90, 0.45])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


if __name__ == '__main__':
    unittest.main()
