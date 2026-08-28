# -*- coding: utf-8 -*-
"""Свежесть фрагмента: архив по ярлыку и истёкший срок по датам.

Разбор инцидента 27.08.2026. На вопрос «актуальные акции» помощник выдал таблицу
«Актуальные акции для водителей», и строкой в ней стояла акция «Приведи друга»
со сроком 01.03.2025-30.04.2025. Источник — статья «Архивные акции TEZ» (id 731),
опубликованная: люди должны её читать. Разбор целиком — в шапке wiki/ai/currency.py.

Тест герметичный, без базы: currency — чистые функции, и им незачем ходить в
Postgres. Дата «сегодня» задаётся параметром именно ради этого: иначе тест
пришлось бы переписывать каждый раз, когда очередной срок из корпуса истечёт.

Разделение проверок отражает асимметрию цены ошибки, из-за которой правила и
устроены так узко. ЛОЖНАЯ пометка дороже пропуска: «уже не действует» на живой
акции — это оператор, который не предложил водителю действующий бонус, и потеря,
которую никто не заметит. Пропуск же ловится вторым слоем — ярлыком на статье.
Поэтому ложных срабатываний здесь закреплено больше, чем верных.
"""

import datetime
import unittest

from wiki.ai import currency

# Дата инцидента. Все сроки в тестах считаются относительно неё.
TODAY = datetime.date(2026, 8, 28)


class HistoricalLabelTest(unittest.TestCase):
    """Ярлык архива в названии статьи или пути заголовков."""

    def test_incident_article_is_caught_by_its_title(self):
        self.assertTrue(currency.historical_label('Архивные акции TEZ'))

    def test_bare_uppercase_label_is_caught(self):
        """Главный пласт архива на проде подписан существительным, не прилагательным.

        16 из 26 кусков статьи «Все акции» (id 33) имеют heading_path «ВСЕ ПАРКИ
        АРХИВ», и эти куски уже стали источником 62 раза в 44 ответах операторам.
        Правило только по «архивн» прошло бы мимо самого крупного случая.
        """
        self.assertTrue(currency.historical_label('ВСЕ ПАРКИ АРХИВ'))

    def test_other_wordings(self):
        for text in ('Неактуальные тарифы', 'Устаревшие правила',
                     'Акции в архиве', 'Архив акций 2025', 'Прошедшие розыгрыши'):
            with self.subTest(text=text):
                self.assertTrue(currency.historical_label(text), text)

    def test_topic_enumeration_is_not_a_label(self):
        """«12. Статус и архив» — раздел методички «Как заполнять вики» (id 216).

        Единственное ложное срабатывание на боевом корпусе из 497 кусков: там
        архив — ТЕМА раздела, а не пометка на содержимом.
        """
        self.assertIsNone(currency.historical_label('12. Статус и архив'))

    def test_action_is_not_a_label(self):
        """«Убрать в архив» — действие. Различает падеж: «в архив» ≠ «в архиве»."""
        for text in ('Как убрать статью в архив', 'Перевод в архив'):
            with self.subTest(text=text):
                self.assertIsNone(currency.historical_label(text))

    def test_cancellation_words_are_not_markers(self):
        """«отмен» и «заверш» выброшены из списка: ложное срабатывание на проде.

        «Инструкция по Отмене/завершению корпоративного заказа (Эконом)» (id 13)
        — действующая инструкция, которую помощник объявил бы недействующей.
        """
        for text in ('Инструкция по Отмене/завершению корпоративного заказа',
                     'Завершение смены', 'Отмена заказа'):
            with self.subTest(text=text):
                self.assertIsNone(currency.historical_label(text))

    def test_current_article_is_left_alone(self):
        self.assertIsNone(currency.historical_label(
            'Актуальные акции и спецпредложения для водителей Tez Taxi'))


class DeadlineTest(unittest.TestCase):
    """Разбор сроков: дата берётся только с явным указателем конца."""

    def expired(self, text):
        value = currency.expired_on(text, TODAY)
        return value.strftime('%d.%m.%Y') if value else None

    def test_incident_dates(self):
        """Текст статьи 731 дословно."""
        self.assertEqual(
            self.expired('дата запуска: 01.03.2025г\nдата окончания: 30.04.2025г'),
            '30.04.2025')

    def test_year_followed_by_russian_letter(self):
        """«г» после года стоит в вике повсеместно, и \\b на конце её не переживал.

        Первая редакция регулярки кончалась на \\b и не нашла ровно ту дату, из-за
        которой всё затевалось: между «5» и «г» границы слова нет.
        """
        self.assertEqual(self.expired('Кызылорда “Цели ” до 1.03.2026г'), '01.03.2026')

    def test_range_takes_the_end(self):
        self.assertEqual(self.expired('Акция действует с 01.03.2025 по 30.04.2025'),
                         '30.04.2025')

    def test_textual_month(self):
        self.assertEqual(self.expired('Действует по 30 апреля 2025 года'), '30.04.2025')

    def test_every_month_is_parsed(self):
        """Май ломался дважды: корень у него записан классом символов «ма[йя]».

        Сначала класс попадал в ключ поиска целиком, потом «марта» перехватывал
        совпадение по общему началу «ма». Обе поломки были молчаливыми — дата
        просто не находилась, и фрагмент считался живым.
        """
        months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
                  'августа', 'сентября', 'октября', 'ноября', 'декабря')
        for number, month in enumerate(months, start=1):
            with self.subTest(month=month):
                self.assertEqual(self.expired('до 15 %s 2025 года' % month),
                                 '15.%02d.2025' % number)

    def test_transition_period_from_the_corpus(self):
        """Статьи 2 и 4 «Информация по СМЗ»: будущее время про истёкший срок.

        Самое трудноуловимое протухание — без слова «архив». Оператор скажет
        водителю «у вас ещё есть время определиться», когда времени уже нет.
        """
        self.assertEqual(self.expired(
            'Налоговая автоматически закроет ИП в конце переходного периода '
            '– до 1 марта 2026, уведомления придут позже'), '01.03.2026')

    def test_start_date_is_not_a_deadline(self):
        """Прошедшая дата в ЖИВОМ утверждении — не повод помечать фрагмент."""
        for text in ('дата запуска: 01.03.2025г',
                     'С 28 июля 2025 года все виды бонусных программ '
                     'в Кокшетау и Атырау временно приостанавливаются',
                     'Реестр акций таксопарка iGroup от 24.07.2026',
                     'Туркестан 5% (с 03.08.2026)',
                     'после 0:00 1 января 2021 г. полис действует'):
            with self.subTest(text=text[:40]):
                self.assertIsNone(self.expired(text))

    def test_future_deadline_is_alive(self):
        self.assertIsNone(self.expired('Регистрация до 15.09.2026 включительно'))

    def test_extension_wins_inside_one_line(self):
        """«до X, продлена до Y» — одно утверждение о продлении, а не два срока."""
        self.assertIsNone(self.expired(
            'Акция продлена: до 30.04.2025, затем продлена до 31.12.2026'))

    def test_year_must_be_four_digits(self):
        """«25.05 по 31.05» без года — угадывание, а не разбор."""
        self.assertIsNone(self.expired('Период: с 25.05 (понедельник) по 31.05'))

    def test_numbers_that_are_not_dates(self):
        for text in ('Заказы по 3 000 ₸ включительно',
                     'Выполнить 50 поездок в течение 14 календарных дней',
                     'срок действия банковской карты не истёк',
                     'Бонус 10 000 ₸ за 100 заказов'):
            with self.subTest(text=text[:40]):
                self.assertIsNone(self.expired(text))


class ChunkCurrencyTest(unittest.TestCase):
    """Итоговый вердикт по куску: что увидят модель и оператор."""

    def test_flag_on_the_article_wins(self):
        state = currency.chunk_currency(
            {'title': 'Акции 2025', 'heading_path': '', 'text': 'условия', 'historical': True},
            TODAY)
        self.assertEqual(state['kind'], 'historical')
        self.assertIn('АРХИВ', state['note'])

    def test_archive_outranks_the_deadline(self):
        """Две пометки на одном фрагменте — спор с самим собой.

        «Эти сведения вообще не действуют» сильнее, чем «срок истёк такого-то».
        """
        state = currency.chunk_currency(
            {'title': 'Архивные акции TEZ', 'heading_path': 'Приведи друга',
             'text': 'дата окончания: 30.04.2025г'}, TODAY)
        self.assertEqual(state['kind'], 'historical')

    def test_deadline_in_the_heading_path_counts(self):
        """У статьи «Яндекс Заправка» (id 604) срок стоит в пути заголовков.

        В теле его нет вовсе, а путь заголовков вдобавок идёт в поиск с весом B
        против D у текста — то есть протухший кусок этой строкой ещё и
        поднимается в выдаче.
        """
        state = currency.chunk_currency(
            {'title': 'Яндекс Заправка',
             'heading_path': '¶ Баллы приоритета > ¶ До 15.05.2025 есть '
                             'возможность получить +6 балла приоритета',
             'text': '◼️ Бензином или ДТ - от 20 л'}, TODAY)
        self.assertEqual(state['kind'], 'expired')
        self.assertEqual(state['deadline'], datetime.date(2025, 5, 15))

    def test_mixed_table_is_marked_softly(self):
        """Кусок — чаще всего таблица, и в ней соседствуют мёртвая и живая строки.

        По куску целиком побеждал бы позднейший срок, и живая строка прикрывала
        бы мёртвую — ровно тот способ спрятать протухшее, из-за которого всё и
        затевалось. Но и «срок истёк» целиком тут неправда: модель выбросила бы
        и живые строки.
        """
        state = currency.chunk_currency(
            {'title': 'Все акции', 'heading_path': 'Розыгрыши',
             'text': 'Название: Кубок Про; Даты: по 18.06.2026\n'
                     'Название: Байга; Даты: по 31.12.2026'}, TODAY)
        self.assertEqual(state['kind'], 'expired')
        self.assertIn('ЧАСТЬ СРОКОВ', state['note'])

    def test_clean_chunk_is_clean(self):
        state = currency.chunk_currency(
            {'title': 'Актуальные акции и спецпредложения для водителей Tez Taxi',
             'heading_path': 'Бонус за брендинг автомобиля',
             'text': 'Условие: выполнить 120 заказов за месяц. Бонус: 30 000 ₸.'},
            TODAY)
        self.assertFalse(state['stale'])
        self.assertEqual(state['note'], '')

    def test_mark_chunks_does_not_mutate_the_input(self):
        source = [{'title': 'Архивные акции TEZ', 'heading_path': '', 'text': 'x'}]
        marked = currency.mark_chunks(source, TODAY)
        self.assertTrue(marked[0]['stale'])
        self.assertNotIn('stale', source[0])


class StaleTitlesTest(unittest.TestCase):
    def test_kinds_are_kept_apart(self):
        """Архив и истёкший срок — разные утверждения, и оговорки у них разные."""
        sources = [
            {'stale': True, 'stale_kind': 'historical', 'title': 'Архивные акции TEZ'},
            {'stale': True, 'stale_kind': 'expired', 'title': 'Яндекс Заправка'},
            {'stale': False, 'stale_kind': None, 'title': 'Актуальные акции'},
        ]
        self.assertEqual(currency.stale_titles(sources, 'historical'),
                         ['Архивные акции TEZ'])
        self.assertEqual(currency.stale_titles(sources, 'expired'), ['Яндекс Заправка'])
        self.assertEqual(len(currency.stale_titles(sources)), 2)


if __name__ == '__main__':
    unittest.main()
