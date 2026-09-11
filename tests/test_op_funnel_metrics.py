# -*- coding: utf-8 -*-
"""Правила подсчёта воронки ОП (op_funnel/metrics.py).

Тест закрепляет формулы, снятые с рабочих файлов супервайзеров, — и прежде всего
те случаи, на которых расчёт РАСХОДИЛСЯ с эталоном, пока его не поправили. Без
них правка правил выглядит безопасной, а тихо ломает отчёт.

Эталон сверялся на живых файлах задач #301, #302, #303, #305 (данные заказчика,
в репозиторий им нельзя — см. память «Не коммитить персональные данные»). Здесь
те же правила на придуманных строках, а числа эталона приведены в комментариях:
по «Потоку» — 140 строк «оператор × сутки» за 01–07.09.2026, ноль расхождений
по «Исходу» (5787), «Дозвону» (2940) и «Согласиям» (1107).
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import metrics


class StreamClassificationTests(unittest.TestCase):
    """«Поток»: два статуса СРМ плюс свободная причина отказа."""

    def classify(self, call, dialog, reason=None):
        return metrics.classify_stream_lead(call, dialog, reason)

    def test_явный_дозвон(self):
        self.assertEqual(self.classify('Дозвон', 'Приглашен')['reach_outcome'], 'dozvon')

    def test_недозвон_схлопнутый_ручкой(self):
        # Партнёрская ручка отдаёт «Недозвон» без номера попытки, а в старых
        # выгрузках с экрана СРМ лежат «Недозвон 1/2/3». Понимать надо оба вида.
        for value in ('Недозвон', 'Недозвон 1', 'Недозвон 2', 'Недозвон 3'):
            self.assertEqual(self.classify(value, '-')['reach_outcome'], 'nedozvon', value)

    def test_прочие_недозвонные_статусы_старых_выгрузок(self):
        for value in ('Гудок не идет', 'чс', 'уволен', 'сброс', 'Неверный номер'):
            self.assertEqual(self.classify(value, '-')['reach_outcome'], 'nedozvon', value)

    def test_пустой_статус_звонка_но_диалог_есть_это_дозвон(self):
        # Оператор поговорил, а статус звонка не проставил. Excel считает такие
        # лиды дозвоном осознанно: без этой ветки дозвон у части операторов
        # занижается вдвое.
        for call in (None, '', '-', '   '):
            self.assertEqual(self.classify(call, 'Отказ')['reach_outcome'], 'dozvon', repr(call))

    def test_пустой_статус_и_диалог_про_недозвон_не_считается_вовсе(self):
        # РЕГРЕССИЯ. Здесь расчёт расходился с эталоном: такие лиды Excel
        # вычитает из дозвона и в «Исход» не добавляет, то есть исключает
        # совсем. Пока они считались недозвоном, «Исход» был завышен на 38
        # лидов из 5787 (0,7 %) — ровно 37 «Неверный номер» и один «Конечный
        # недозвон» за 01–07.09.2026.
        for dialog in ('Неверный номер', 'Конечный недозвон'):
            outcome = self.classify('-', dialog)
            self.assertEqual(outcome['reach_outcome'], 'new', dialog)
        acc = metrics.aggregate_leads([self.classify('-', 'Неверный номер')])
        self.assertEqual(acc['handled'], 0)
        self.assertEqual(acc['not_reached'], 0)

    def test_новый_лид_не_обработан(self):
        for call in ('Новый', 'В работе'):
            self.assertEqual(self.classify(call, '-')['reach_outcome'], 'new', call)

    def test_согласия_включают_все_четыре_формулировки(self):
        for dialog in ('Приглашен', 'Вышел на линию', 'Согласился перейти', 'Согласился выйти'):
            acc = metrics.aggregate_leads([self.classify('Дозвон', dialog)])
            self.assertEqual(acc['agreed'], 1, dialog)

    def test_успех_считается_и_согласием(self):
        # Иначе «% согласий от дозвона» провалился бы у лучших операторов.
        acc = metrics.aggregate_leads([self.classify('Дозвон', 'Вышел на линию')])
        self.assertEqual((acc['succeeded'], acc['agreed']), (1, 1))

    def test_незнакомый_статус_не_дарит_оператору_разговор(self):
        outcome = self.classify('Что-то новое', '-')
        self.assertEqual(outcome['reach_outcome'], 'new')

    def test_опечатки_причины_сводятся_к_одной(self):
        codes = {
            self.classify('Дозвон', 'Отказ', text)['reason_code']
            for text in ('не интересно', 'не интеросно', 'неинтересно', 'Не интересует')
        }
        self.assertEqual(len(codes), 1, 'четыре написания одной причины должны дать один код')

    def test_незнакомая_причина_попадает_в_разбивку_сразу(self):
        # Поле в СРМ свободное: за август-сентябрь приехало 40 разных написаний.
        # Новая причина обязана появиться в разбивке в тот же день, а не после релиза.
        outcome = self.classify('Дозвон', 'Отказ', 'уехал в другой город насовсем')
        self.assertTrue(outcome['reason_code'])
        self.assertEqual(outcome['reason_bucket'], metrics.STREAM_DEFAULT_BUCKET)

    def test_отказ_без_причины_остаётся_строкой_разбивки(self):
        # Иначе сумма причин не сходится с числом отказов, и колонка выглядит сломанной.
        outcome = self.classify('Дозвон', 'Отказ', None)
        self.assertEqual(outcome['reason_code'], 'no_reason')
        self.assertEqual(outcome['reason_bucket'], 'otkaz')

    def test_сброс_у_молчащего_лида_это_недозвон(self):
        outcome = self.classify('-', None, 'Сброс')
        self.assertEqual(outcome['reach_outcome'], 'new')
        outcome = self.classify('Дозвон', None, 'Сброс')
        self.assertEqual(outcome['reach_outcome'], 'nedozvon')

    def test_имя_с_двойными_пробелами_не_мешает_разбору(self):
        # В выгрузках встречается «Санақ Ерсұлтан  » с двумя пробелами на конце.
        self.assertEqual(self.classify('  Дозвон ', ' Приглашен  ')['dialog_outcome'], 'agree')


class PaidHireClassificationTests(unittest.TestCase):
    """«Яндекс Регистрация»: статус лида сам называет корзину причины."""

    def test_статус_задаёт_корзину(self):
        cases = {
            'Вышел на линию': ('dozvon', 'success', ''),
            'Приглашен': ('dozvon', 'agree', ''),
            'Отказ': ('dozvon', 'reject', 'otkaz'),
            'Нецелевой': ('dozvon', 'untargeted', 'netsel'),
            'Недозвон': ('nedozvon', 'none', 'nedozvon'),
            'Новый': ('new', 'none', ''),
        }
        for status, expected in cases.items():
            outcome = metrics.classify_paid_hire_lead(status, '', '')
            got = (outcome['reach_outcome'], outcome['dialog_outcome'], outcome['reason_bucket'])
            self.assertEqual(got, expected, status)

    def test_перезвон_назначен_это_состоявшийся_контакт(self):
        outcome = metrics.classify_paid_hire_lead('Перезвон назначен', '', '')
        self.assertEqual(outcome['reach_outcome'], 'dozvon')
        self.assertEqual(outcome['dialog_outcome'], 'callback')

    def test_код_причины_сильнее_статуса(self):
        # Статус оператор мог не переставить, а код причины выбирается из списка.
        outcome = metrics.classify_paid_hire_lead('Перезвон назначен', 'not_available', 'not_available')
        self.assertEqual(outcome['reach_outcome'], 'nedozvon')
        self.assertEqual(outcome['reason_bucket'], 'nedozvon')
        # Перезвон при этом не теряется: в файле супервайзера он стоит и среди
        # причин недозвона, и отдельной колонкой воронки диалогов.
        self.assertEqual(outcome['dialog_outcome'], 'callback')

    def test_сырые_коды_получают_русскую_подпись(self):
        # Ручка отдаёт их «как есть»: в СРМ у этих четырёх кодов подписи нет,
        # и на экране был бы not_available.
        for code, title in (('not_available', 'Недоступен'), ('call_reset', 'Сброс'),
                            ('barrier_not_passed', 'Не прошёл барьер 5 звонков'),
                            ('invalid_number', 'Неправильный номер')):
            outcome = metrics.classify_paid_hire_lead('Недозвон', code, code)
            self.assertEqual(outcome['reason_title'], title, code)

    def test_уточнение_причины_приклеивается_к_подписи(self):
        outcome = metrics.classify_paid_hire_lead('Отказ', 'competitor',
                                                  'Работает с ДТ (конкурент)', 'Конкурент')
        self.assertIn('Конкурент', outcome['reason_title'])

    def test_причина_у_согласившегося_в_разбивку_не_идёт(self):
        outcome = metrics.classify_paid_hire_lead('Приглашен', 'no_auto', 'Нет авто')
        self.assertEqual(outcome['reason_bucket'], '')
        self.assertEqual(outcome['reason_code'], '')


class AmoClassificationTests(unittest.TestCase):
    """«Основа ОП»: этап воронки 5524684 плюс причина закрытия в скобках."""

    def test_этап_и_причина_разделяются(self):
        self.assertEqual(
            metrics.amo_split_stage('Закрыто и не реализовано (Нет авто (не цел))'),
            ('Закрыто и не реализовано', 'Нет авто (не цел)'),
        )
        self.assertEqual(
            metrics.amo_split_stage('Закрыто и не реализовано'),
            ('Закрыто и не реализовано', ''),
        )
        self.assertEqual(metrics.amo_split_stage('недозвон 1'), ('недозвон 1', ''))

    def test_регистрация_это_успех_и_по_имени_и_по_номеру(self):
        # В воронке 5524684 статус 142 называется «ПРОШЕЛ РЕГИСТРАЦИЮ», а в
        # четырёх остальных воронках — «Успешно реализовано». Текущая ночная
        # выгрузка собирает имена по всем воронкам подряд и перетирает подпись,
        # поэтому в базе лежит чужая. Понимать надо оба варианта.
        for stage in ('ПРОШЕЛ РЕГИСТРАЦИЮ', 'Успешно реализовано'):
            self.assertEqual(metrics.classify_amo_lead(stage)['dialog_outcome'], 'success', stage)
        outcome = metrics.classify_amo_lead('что угодно', None, metrics.AMO_SUCCESS_STATUS_ID)
        self.assertEqual(outcome['dialog_outcome'], 'success')

    def test_суффикс_подписи_задаёт_корзину(self):
        cases = {
            'Закрыто и не реализовано (Нет авто (не цел))': 'netsel',
            'Закрыто и не реализовано (Высокая комиссия (отказ))': 'otkaz',
            'Закрыто и не реализовано (Неправильный номер (недозвон))': 'nedozvon',
        }
        for stage, bucket in cases.items():
            self.assertEqual(metrics.classify_amo_lead(stage)['reason_bucket'], bucket, stage)

    def test_технические_причины_не_отказы(self):
        # РЕГРЕССИЯ. Семь из 27 причин закрытия amoCRM — это названия этапов и
        # воронок: ими помечают лид, ушедший в другой процесс. Сводные листы
        # супервайзера их не считают (в «Причинах отказов» ровно одиннадцать
        # колонок, и ни одной из этих там нет). Пока они считались отказами,
        # отказы удваивались: 14 вместо 7 у одного оператора за 01.09.2026.
        for stage in ('Закрыто и не реализовано (Диалоги)',
                      'Закрыто и не реализовано (YaPROREG)',
                      'Закрыто и не реализовано (Дожим приглашенные)',
                      'Закрыто и не реализовано (Звонки (не закрытые))',
                      'Закрыто и не реализовано'):
            outcome = metrics.classify_amo_lead(stage)
            self.assertEqual(outcome['reason_bucket'], metrics.BUCKET_MOVED, stage)
            self.assertEqual(outcome['dialog_outcome'], 'none', stage)

    def test_увели_считается_в_обработано_но_не_в_воронку(self):
        acc = metrics.aggregate_leads([
            metrics.classify_amo_lead('Закрыто и не реализовано (Диалоги)'),
        ])
        self.assertEqual(acc['leads_total'], 1)
        self.assertEqual(acc['moved'], 1)
        self.assertEqual(acc['rejected'], 0)
        self.assertEqual(metrics.handled_for_source(acc, 'amo'), 1)

    def test_недозвонные_этапы(self):
        for stage in ('недозвон 1', 'недозвон 2', 'недозвон 3', 'недозвон 4'):
            self.assertEqual(metrics.classify_amo_lead(stage)['reach_outcome'], 'nedozvon', stage)

    def test_согласия(self):
        for stage in ('Приглашен на регистрацию', 'Дожим приглашенные', 'Отправлен в офис (аренда)'):
            self.assertEqual(metrics.classify_amo_lead(stage)['dialog_outcome'], 'agree', stage)

    def test_неразобранное_не_обработано(self):
        for stage in ('Неразобранное', 'Новая заявка', 'Принято в работу', 'Звонки', 'Диалоги'):
            self.assertEqual(metrics.classify_amo_lead(stage)['reach_outcome'], 'new', stage)


class HandledPerSourceTests(unittest.TestCase):
    """«Обработано лидов» считается по-разному у разных источников."""

    def test_у_срм_это_дозвон_плюс_недозвон(self):
        acc = metrics.aggregate_leads([
            metrics.classify_stream_lead('Дозвон', 'Приглашен', None),
            metrics.classify_stream_lead('Недозвон', '-', None),
            metrics.classify_stream_lead('Новый', '-', None),   # до него не дошли
        ])
        self.assertEqual(acc['leads_total'], 3)
        self.assertEqual(metrics.handled_for_source(acc, 'crm_stream'), 2)

    def test_у_amo_это_все_строки_выгрузки(self):
        # В выгрузку amoCRM попадает только то, что оператор трогал, поэтому
        # «обработано» там — это просто число строк. Проверено на файле #302:
        # число строк совпадает с колонкой «Обработаны лиды» вплоть до оператора,
        # а «дозвон + недозвон» — нет.
        acc = metrics.aggregate_leads([
            metrics.classify_amo_lead('ПРОШЕЛ РЕГИСТРАЦИЮ'),
            metrics.classify_amo_lead('Закрыто и не реализовано (Диалоги)'),
            metrics.classify_amo_lead('Неразобранное'),
        ])
        self.assertEqual(metrics.handled_for_source(acc, 'amo'), 3)


class RatesTests(unittest.TestCase):

    def test_процент_дозвона_от_обработанных(self):
        # Единый стандарт, решение владельца 11.09.2026 (пункт 3.1 ТЗ #301).
        rates = metrics.derive_rates({'handled': 100, 'reached': 40, 'not_reached': 60})
        self.assertAlmostEqual(rates['reach_rate'], 0.4)

    def test_второй_знаменатель_остался_отдельным_показателем(self):
        # Он отвечает на другой вопрос — «какого качества были попытки».
        rates = metrics.derive_rates({'handled': 200, 'reached': 40, 'not_reached': 60})
        self.assertAlmostEqual(rates['reach_rate'], 0.2)
        self.assertAlmostEqual(rates['attempt_rate'], 0.4)

    def test_нет_данных_это_none_а_не_ноль(self):
        # «Нет данных» и «ноль процентов» на экране выглядят по-разному.
        rates = metrics.derive_rates({'handled': 0, 'reached': 0})
        self.assertIsNone(rates['reach_rate'])

    def test_план_на_сутки_из_часов_и_нормы(self):
        plan = metrics.plan_for_day(6, {'reached_per_hour': 20, 'agreed_per_hour': 5})
        self.assertEqual(plan, {'plan_reached': 120.0, 'plan_agreed': 30.0})

    def test_ноль_часов_даёт_ноль_плана(self):
        plan = metrics.plan_for_day(0, {'reached_per_hour': 20, 'agreed_per_hour': 5})
        self.assertEqual(plan['plan_reached'], 0)

    def test_месячный_план_умножается_на_ставку(self):
        self.assertEqual(metrics.monthly_plan(1.0, {'plan_per_fte': 160}), 160.0)
        self.assertEqual(metrics.monthly_plan(0.75, {'plan_per_fte': 160}), 120.0)
        self.assertEqual(metrics.monthly_plan(0.5, {'plan_per_fte': 160}), 80.0)

    def test_план_новичка_на_двадцать_процентов_меньше(self):
        # Тот же коэффициент, что в калькуляторах зарплат (salaryFormula.js).
        self.assertEqual(metrics.monthly_plan(1.0, {'plan_per_fte': 160}, newbie=True), 128.0)

    def test_верификатор_разница_от_таргета_положительная_когда_быстрее(self):
        # В файле супервайзера это «таргет / факт − 1»: ответили за 60 секунд при
        # таргете 120 — разница +1,0, и знак менять нельзя, его читают каждый день.
        rates = metrics.verificator_rates(
            {'chats': 80, 'tickets': 8, 'work_hours': 8, 'chat_reply_seconds': 60},
            {'chats_per_hour': 16, 'reply_seconds': 120, 'quality': 90},
        )
        self.assertEqual(rates['handled'], 88)
        self.assertAlmostEqual(rates['chats_per_hour'], 11.0)
        self.assertAlmostEqual(rates['reply_gap'], 1.0)


class ColorTests(unittest.TestCase):

    def test_нейтральное_состояние_не_красим(self):
        # Требование владельца: цвет только там, где несёт смысл.
        self.assertEqual(metrics.color_bucket(None), '')

    def test_пороги(self):
        self.assertEqual(metrics.color_bucket(1.0), 'green')
        self.assertEqual(metrics.color_bucket(0.9), 'amber')
        self.assertEqual(metrics.color_bucket(0.5), 'red')

    def test_пороги_настраиваемые(self):
        self.assertEqual(metrics.color_bucket(0.7, green_from=0.6, amber_from=0.4), 'green')


class AnomalyTests(unittest.TestCase):

    def test_часы_ноль_при_обработанных_лидах(self):
        found = metrics.detect_anomalies([
            {'user_id': 7, 'work_day': date(2026, 9, 1), 'work_hours': 0, 'handled': 41,
             'reached': 20, 'plan_reached': 0},
        ])
        kinds = [item['kind'] for item in found]
        self.assertIn(metrics.ANOMALY_ZERO_HOURS, kinds)

    def test_кратный_скачок_объёма(self):
        found = metrics.detect_anomalies([
            {'user_id': 7, 'work_day': date(2026, 9, 1), 'work_hours': 8, 'handled': 40,
             'reached': 30, 'plan_reached': 30},
            {'user_id': 7, 'work_day': date(2026, 9, 2), 'work_hours': 8, 'handled': 95,
             'reached': 60, 'plan_reached': 60},
        ])
        self.assertIn(metrics.ANOMALY_VOLUME_JUMP, [item['kind'] for item in found])

    def test_мелкие_объёмы_скачком_не_считаются(self):
        # С двух лидов до пяти — это не дубль базы.
        found = metrics.detect_anomalies([
            {'user_id': 7, 'work_day': date(2026, 9, 1), 'work_hours': 8, 'handled': 2,
             'reached': 2, 'plan_reached': 2},
            {'user_id': 7, 'work_day': date(2026, 9, 2), 'work_hours': 8, 'handled': 5,
             'reached': 5, 'plan_reached': 5},
        ])
        self.assertNotIn(metrics.ANOMALY_VOLUME_JUMP, [item['kind'] for item in found])

    def test_два_дня_подряд_ниже_порога(self):
        rows = [
            {'user_id': 7, 'work_day': date(2026, 9, d), 'work_hours': 8, 'handled': 50,
             'reached': 10, 'plan_reached': 100}
            for d in (1, 2)
        ]
        found = metrics.detect_anomalies(rows)
        self.assertIn(metrics.ANOMALY_BELOW_TARGET, [item['kind'] for item in found])

    def test_хороший_день_обрывает_серию(self):
        rows = [
            {'user_id': 7, 'work_day': date(2026, 9, 1), 'work_hours': 8, 'handled': 50,
             'reached': 10, 'plan_reached': 100},
            {'user_id': 7, 'work_day': date(2026, 9, 2), 'work_hours': 8, 'handled': 50,
             'reached': 100, 'plan_reached': 100},
            {'user_id': 7, 'work_day': date(2026, 9, 3), 'work_hours': 8, 'handled': 50,
             'reached': 10, 'plan_reached': 100},
        ]
        found = metrics.detect_anomalies(rows)
        self.assertNotIn(metrics.ANOMALY_BELOW_TARGET, [item['kind'] for item in found])


class PeriodTests(unittest.TestCase):

    def test_предыдущий_период_той_же_длины_встык(self):
        self.assertEqual(
            metrics.previous_period(date(2026, 9, 8), date(2026, 9, 14)),
            (date(2026, 9, 1), date(2026, 9, 7)),
        )

    def test_один_день_сравнивается_с_предыдущим(self):
        self.assertEqual(
            metrics.previous_period(date(2026, 9, 11), date(2026, 9, 11)),
            (date(2026, 9, 10), date(2026, 9, 10)),
        )

    def test_рост_отказов_это_ухудшение(self):
        result = metrics.compare({'rejected': 20}, {'rejected': 10}, metrics=('rejected',))
        self.assertEqual(result['rejected']['direction'], 'down')

    def test_рост_согласий_это_улучшение(self):
        result = metrics.compare({'agreed': 20}, {'agreed': 10}, metrics=('agreed',))
        self.assertEqual(result['agreed']['direction'], 'up')

    def test_сравнивать_нечего_если_данных_нет(self):
        result = metrics.compare({'agreed': 20}, {}, metrics=('agreed',))
        self.assertIsNone(result['agreed']['delta'])

    def test_границы_месяца(self):
        self.assertEqual(metrics.month_bounds(date(2026, 9, 11)),
                         (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(metrics.month_bounds(date(2026, 12, 5)),
                         (date(2026, 12, 1), date(2026, 12, 31)))


class ReasonDictTests(unittest.TestCase):

    def test_сид_справочника_собирается_без_потерь(self):
        index = metrics.build_reason_index()
        self.assertEqual(index[metrics.normalize_reason_key('Сброс')][2], 'nedozvon')
        self.assertEqual(index[metrics.normalize_reason_key('Работа с ДТ')][2], 'otkaz')
        self.assertEqual(index[metrics.normalize_reason_key('Нет авто')][2], 'netsel')

    def test_ключ_причины_не_зависит_от_регистра_и_пунктуации(self):
        self.assertEqual(metrics.normalize_reason_key('  Не  Работает с Яндекс!  '),
                         metrics.normalize_reason_key('не работает с яндекс'))


if __name__ == '__main__':
    unittest.main()
