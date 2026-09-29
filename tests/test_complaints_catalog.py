# -*- coding: utf-8 -*-
"""Раздел «Жалобы»: справочники и правила — буква в букву по ТЗ задачи #297.

Списки здесь сверяются с ТЗ дословно. Это не «тест ради теста»: у жалоб
аналитика строится по кодам за месяцы назад, и тихо переименованный или
потерянный пункт причины сломал бы отчёт «основные причины жалоб», не уронив
ни одной строки кода.
"""

import unittest

from complaints import catalog


def titles(target_code):
    return [item['title'] for item in catalog.target(target_code)['reasons']]


class TargetsTest(unittest.TestCase):
    def test_first_level_is_the_specification_list_in_its_order(self):
        """«Первый уровень выбора: оператор колл-центра; аренда авто;
        фронт-офис; таксопарк; Яндекс»."""
        self.assertEqual([item['title'] for item in catalog.TARGETS],
                         ['Оператор колл-центра', 'Аренда авто', 'Фронт-офис',
                          'Таксопарк', 'Яндекс'])

    def test_call_center_reasons(self):
        self.assertEqual(titles('call_center'), [
            'Грубость / некорректное общение', 'Не решили вопрос',
            'Предоставили неверную информацию',
            'Не добавили в акцию / некорректно обработали вопрос по акции',
            'Долго не отвечали', 'Не ответили на звонок / сообщение', 'Другое'])

    def test_car_rental_reasons(self):
        self.assertEqual(titles('car_rental'), [
            'Не отвечают на звонки', 'Не отвечают в WhatsApp', 'Отказали в помощи',
            'Вопрос по автомобилю не решён', 'Условия аренды', 'Другое'])

    def test_front_office_reasons(self):
        self.assertEqual(titles('front_office'), [
            'Грубое общение', 'Отказали в помощи / консультации', 'Вопрос не решили',
            'Долгое ожидание', 'Другое'])

    def test_taxi_park_reasons(self):
        self.assertEqual(titles('taxi_park'), [
            'Комиссия', 'Условия работы', 'Акции / бонусы', 'Выплаты', 'Обслуживание',
            'Неудовлетворённость условиями парка', 'Другое'])

    def test_yandex_reasons(self):
        self.assertEqual(titles('yandex'), [
            'Тарифы', 'Стоимость заказов', 'Комиссии / удержания', 'Работа приложения',
            'Распределение заказов', 'Блокировки / ограничения', 'Другое'])

    def test_reason_codes_are_unique_inside_a_target(self):
        for item in catalog.TARGETS:
            codes = [reason['code'] for reason in item['reasons']]
            self.assertEqual(len(codes), len(set(codes)), item['code'])
            self.assertEqual(codes[-1], catalog.OTHER, item['code'])

    def test_call_center_needs_a_department_but_not_an_employee(self):
        """«Необходимо дополнительно определить направление», но «если оператор
        не смог определить сотрудника, обращение всё равно должно быть создано»."""
        spec = catalog.target('call_center')
        self.assertTrue(spec['unit_required'])
        self.assertTrue(spec['employee'])
        clean, errors = catalog.clean_complaint({
            'target': 'call_center', 'reason': 'rude', 'unit_id': 367,
            'driver_name': 'Ф', 'driver_phone': '1', 'city': 'Алматы', 'description': 'д'})
        self.assertEqual(errors, {})
        self.assertIsNone(clean['employee_id'])

    def test_departments_follow_the_specification_order(self):
        """«техподдержка; отдел продаж; при необходимости другие подразделения КЦ»."""
        self.assertEqual(catalog.CALL_CENTER_DEPARTMENT_CODES[:2], ('szov', 'op'))
        self.assertEqual(catalog.department_label('szov', 'СЗоВ'), 'Техподдержка (СЗоВ)')
        self.assertEqual(catalog.department_label('op', 'Отдел продаж'), 'Отдел продаж')
        self.assertEqual(catalog.department_label('remote_cc', 'Удаленный КЦ'), 'Удаленный КЦ')

    def test_tez_is_not_part_of_complaints(self):
        """Владелец, 29.09.2026: «у никого ТЭЗ КЦ не должно быть» — ни в выборе
        подразделения, ни среди отделов, которые разбирают жалобы."""
        self.assertNotIn('tez', catalog.CALL_CENTER_DEPARTMENT_CODES)
        self.assertNotIn('tez', catalog.HANDLER_DEPARTMENT_CODES)


class ProcessingTest(unittest.TestCase):
    """«Жалоба зафиксирована» против «Требует обработки»."""

    def test_yandex_never_goes_to_the_group(self):
        """«В Telegram-группу не передаём обращения „для Яндекса“» — даже если
        клиент прислал обратное."""
        self.assertFalse(catalog.requires_processing('yandex', True))

    def test_taxi_park_is_the_operators_choice(self):
        self.assertTrue(catalog.requires_processing('taxi_park', True))
        self.assertFalse(catalog.requires_processing('taxi_park', False))

    def test_employees_and_rental_always_go(self):
        for code in ('call_center', 'front_office', 'car_rental'):
            self.assertTrue(catalog.requires_processing(code, False), code)


class ClosingRuleTest(unittest.TestCase):
    """Жалоба закрыта = итог есть И работа с сотрудником не висит."""

    def test_employee_work_keeps_the_complaint_open(self):
        """«Жалоба на сотрудника не могла считаться полностью закрытой до тех
        пор, пока супервайзер не завершил обязательную работу»."""
        self.assertFalse(catalog.is_closed(requires_processing=True, result_code='confirmed',
                                           work_state=catalog.WORK_PENDING))

    def test_result_is_required_for_processed_complaints(self):
        self.assertFalse(catalog.is_closed(requires_processing=True, result_code=None,
                                           work_state=catalog.WORK_DONE))
        self.assertTrue(catalog.is_closed(requires_processing=True, result_code='confirmed',
                                          work_state=catalog.WORK_DONE))

    def test_recorded_only_complaint_is_closed_at_once(self):
        self.assertTrue(catalog.is_closed(requires_processing=False, result_code=None,
                                          work_state=None))

    def test_unknown_employee_does_not_block_an_unconfirmed_complaint(self):
        """Не подтвердилась, «недостаточно данных» — разбирать некого, итог
        это и объясняет."""
        for code in ('no_data', 'not_confirmed', 'out_of_scope', 'explained'):
            self.assertTrue(catalog.is_closed(requires_processing=True, result_code=code,
                                              work_state=catalog.WORK_UNASSIGNED), code)

    def test_confirmed_complaint_waits_for_the_employee(self):
        """Подтверждённая жалоба на сотрудника без сотрудника — это работа,
        которую никто не провёл: закрывать её одним нажатием «Итог» нельзя
        (сверка с ТЗ 29.09.2026). Закрыть — определив сотрудника или записав,
        почему работать не с кем (work_state станет done)."""
        for code in catalog.CONFIRMED_RESULTS:
            self.assertFalse(catalog.is_closed(requires_processing=True, result_code=code,
                                               work_state=catalog.WORK_UNASSIGNED), code)
        self.assertTrue(catalog.is_closed(requires_processing=True, result_code='confirmed',
                                          work_state=catalog.WORK_DONE))

    def test_only_explanations_close_the_work_without_an_employee(self):
        self.assertEqual(set(catalog.UNASSIGNED_ACTIONS), {'review', 'no_training', 'other'})
        for code in catalog.UNASSIGNED_ACTIONS:
            spec = catalog.WORK_ACTION_BY_CODE[code]
            self.assertTrue(spec['closes'] and not spec['training'], code)

    def test_work_state_by_facts(self):
        self.assertIsNone(catalog.work_state_for('yandex', None, False))
        self.assertEqual(catalog.work_state_for('call_center', None, False),
                         catalog.WORK_UNASSIGNED)
        self.assertEqual(catalog.work_state_for('front_office', 7, False), catalog.WORK_PENDING)
        self.assertEqual(catalog.work_state_for('call_center', 7, True), catalog.WORK_DONE)


class ResultsAndWorkTest(unittest.TestCase):
    def test_results_are_the_specification_list(self):
        self.assertEqual([item['title'] for item in catalog.RESULTS], [
            'Жалоба подтверждена', 'Жалоба не подтверждена',
            'Информация частично подтверждена', 'Предоставлено разъяснение',
            'Вопрос решён', 'Передано ответственному подразделению', 'Не в зоне влияния',
            'Недостаточно данных для проверки'])

    def test_confirmed_pair(self):
        self.assertEqual(catalog.CONFIRMED_RESULTS, ('confirmed', 'partial'))
        self.assertEqual(catalog.NOT_CONFIRMED_RESULTS, ('not_confirmed',))

    def test_work_actions_cover_the_specification(self):
        """«обратная связь проведена; проведён дополнительный разбор ситуации;
        назначен / проведён тренинг; дополнительное обучение не требуется;
        приняты другие меры» — «назначен / проведён» разведены на два факта."""
        self.assertEqual([item['title'] for item in catalog.WORK_ACTIONS], [
            'Обратная связь проведена', 'Проведён дополнительный разбор ситуации',
            'Назначен тренинг', 'Проведён тренинг',
            'Дополнительное обучение не требуется', 'Приняты другие меры'])

    def test_only_feedback_and_training_write_to_trainings(self):
        writes = {item['code'] for item in catalog.WORK_ACTIONS if item['training']}
        self.assertEqual(writes, {'feedback', 'training'})

    def test_training_reasons_are_allowed_by_the_trainings_check(self):
        """Вид занятия уходит в trainings.reason под CHECK из 11 литералов."""
        allowed = {'Обратная связь', 'Собрание', 'Тех. сбой', 'Мотивационная беседа',
                   'Дисциплинарный тренинг', 'Тренинг по качеству. Разбор ошибок',
                   'Тренинг по качеству. Объяснение МШ', 'Тренинг по продукту',
                   'Мониторинг', 'Практика в офисе таксопарка', 'Другое'}
        self.assertTrue(set(catalog.TRAINING_REASONS) <= allowed)
        for item in catalog.WORK_ACTIONS:
            if item.get('default_reason'):
                self.assertIn(item['default_reason'], catalog.TRAINING_REASONS)


class WorkFlagsTest(unittest.TestCase):
    """Цепочка «сотрудник определён → ОС → тренинг → работа завершена»."""

    START = {'feedback_done': False, 'training_required': False, 'training_done': False}

    def test_feedback_closes_the_work(self):
        flags = catalog.next_work_flags(self.START, 'feedback')
        self.assertEqual(flags, {'feedback_done': True, 'training_required': False,
                                 'training_done': False, 'closed': True})

    def test_feedback_with_training_needed_keeps_it_open(self):
        flags = catalog.next_work_flags(self.START, 'feedback', need_training=True)
        self.assertTrue(flags['training_required'])
        self.assertFalse(flags['closed'])
        flags = catalog.next_work_flags(flags, 'training')
        self.assertEqual(flags, {'feedback_done': True, 'training_required': False,
                                 'training_done': True, 'closed': True})

    def test_assigned_training_never_closes(self):
        flags = catalog.next_work_flags(self.START, 'training_assigned')
        self.assertTrue(flags['training_required'])
        self.assertFalse(flags['closed'])

    def test_assigned_training_is_not_cancelled_by_feedback_or_review(self):
        """«Назначен тренинг», потом «ОС проведена» — работа НЕ закрыта:
        тренинг так и не провели (сверка с ТЗ 29.09.2026)."""
        for code in ('feedback', 'review', 'other'):
            flags = catalog.next_work_flags(
                catalog.next_work_flags(self.START, 'training_assigned'), code)
            self.assertTrue(flags['training_required'], code)
            self.assertFalse(flags['closed'], code)

    def test_no_training_needed_closes_and_explains_why(self):
        """«…либо не указал, почему дополнительная работа не требуется»."""
        flags = catalog.next_work_flags({'training_required': True}, 'no_training')
        self.assertFalse(flags['training_required'])
        self.assertTrue(flags['closed'])

    def test_need_training_is_ignored_where_it_is_not_asked(self):
        flags = catalog.next_work_flags(self.START, 'other', need_training=True)
        self.assertFalse(flags['training_required'])
        self.assertTrue(flags['closed'])

    def test_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            catalog.next_work_flags(self.START, 'fired')

    def test_summary_carries_facts_only(self):
        """Образец ТЗ: «Жалоба обработана. Сотрудник определён. Обратная связь
        проведена. Тренинг зафиксирован»."""
        self.assertEqual(
            catalog.work_summary(feedback_done=True, training_done=True, complaint_closed=True),
            'Жалоба обработана. Сотрудник определён. Обратная связь проведена. '
            'Тренинг зафиксирован.')
        self.assertEqual(
            catalog.work_summary(feedback_done=False, training_done=False,
                                 complaint_closed=False),
            'Работа с сотрудником завершена. Сотрудник определён.')
        self.assertEqual(
            catalog.work_summary(feedback_done=False, training_done=False,
                                 complaint_closed=True, employee_known=False),
            'Жалоба обработана. Сотрудник не определён.')


class CleanComplaintTest(unittest.TestCase):
    BASE = {'target': 'car_rental', 'reason': 'no_calls', 'driver_name': ' Сериков  Ерлан ',
            'driver_phone': '+7 701', 'city': 'Алматы', 'description': 'Не берут трубку'}

    def test_required_driver_data(self):
        """«Оператор указывает основные данные»: ФИО, телефон, город и
        описание обязательны; ID / ВУ — «при наличии», дата — «если нужно»."""
        _clean, errors = catalog.clean_complaint({'target': 'car_rental', 'reason': 'no_calls'})
        self.assertEqual(set(errors), {'driver_name', 'driver_phone', 'city', 'description'})

    def test_whitespace_is_normalised(self):
        clean, errors = catalog.clean_complaint(self.BASE)
        self.assertEqual(errors, {})
        self.assertEqual(clean['driver_name'], 'Сериков Ерлан')
        self.assertIsNone(clean['driver_ref'])

    def test_reason_must_belong_to_the_target(self):
        _clean, errors = catalog.clean_complaint(dict(self.BASE, reason='tariffs'))
        self.assertIn('reason', errors)

    def test_unknown_target(self):
        _clean, errors = catalog.clean_complaint(dict(self.BASE, target='boss'))
        self.assertEqual(set(errors), {'target'})

    def test_employee_is_dropped_where_the_target_has_none(self):
        clean, _errors = catalog.clean_complaint(dict(self.BASE, employee_id=40, unit_id=5))
        self.assertIsNone(clean['employee_id'])
        self.assertIsNone(clean['unit_id'])

    def test_event_time_format(self):
        _clean, errors = catalog.clean_complaint(dict(self.BASE, event_at='вчера'))
        self.assertIn('event_at', errors)
        # Несуществующая дата — понятная ошибка, а не отказ базы (500).
        _clean, errors = catalog.clean_complaint(dict(self.BASE, event_at='2026-02-30'))
        self.assertIn('event_at', errors)
        clean, errors = catalog.clean_complaint(dict(self.BASE, event_at='2026-09-28T14:30'))
        self.assertEqual(errors, {})
        self.assertEqual((clean['event_at'], clean['event_time_known']),
                         ('2026-09-28 14:30:00', True))

    def test_event_without_time_is_marked_so(self):
        """«Дату / примерное время»: время необязательно, и что его не называли,
        хранится явно — иначе в карточке появилось бы «00:00»."""
        clean, errors = catalog.clean_complaint(dict(self.BASE, event_at='2026-09-28'))
        self.assertEqual(errors, {})
        self.assertEqual((clean['event_at'], clean['event_time_known']),
                         ('2026-09-28 00:00:00', False))

    def test_long_description_is_refused_not_cut(self):
        """Вставленный хвост переписки не должен пропадать молча."""
        clean, errors = catalog.clean_complaint(dict(self.BASE, description='я' * 4001))
        self.assertIn('description', errors)
        self.assertEqual(len(clean['description']), 4001)


if __name__ == '__main__':
    unittest.main()
