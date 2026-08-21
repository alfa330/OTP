# -*- coding: utf-8 -*-
"""Центр уведомлений: порядок, изоляция источников и правила «прочитано».

Отдельного внимания стоит последний класс. Список того, что колокол разрешает
гасить, существует в двух местах — в Python (mark_seen) и в JSX (CLEARABLE), —
и это ровно тот вид раздвоения, на котором ломаются такие вещи: расхождение не
даст ни ошибки, ни падения, просто кнопка «отметить прочитанным» начнёт
обнулять счётчик обязательного документа, ничего с ним не сделав. Поэтому
списки сверяются по исходникам.
"""

import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from notifications import realtime, sources
from tests import prod_db

ROOT = Path(__file__).resolve().parents[1]
BELL_JSX = ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx'


class FakeCursor:
    """Курсор, которому важны только SAVEPOINT'ы: сами источники подменяются."""

    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql.strip().split()[0].upper())

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _item(source, title, tone='default', at=None):
    return {'source': source, 'id': 1, 'title': title, 'body': '', 'at': at,
            'view': source, 'target': None, 'tone': tone}


class CollectOrderTest(unittest.TestCase):
    """Порядок в общем списке."""

    def setUp(self):
        self.original = dict(sources._HANDLERS)

    def tearDown(self):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(self.original)

    def _stub(self, mapping):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(mapping)

    def test_overdue_goes_first(self):
        """Просроченное поднимается ВОПРЕКИ порядку источников.

        Важно, какой источник помечен просроченным: wiki_ack идёт первым в
        SOURCES, и тест с ним проходил бы даже при полностью удалённой
        сортировке — порядок обеспечила бы сама константа. Поэтому «горит»
        здесь four_you, последний в SOURCES: подняться наверх он может только
        сортировкой.
        """
        self._stub({
            'wiki_ack': lambda c, v, limit: (1, [_item('wiki_ack', 'Регламент')]),
            'events': lambda c, v, limit: (1, [_item('events', 'Новый пост')]),
            'four_you': lambda c, v, limit: (1, [_item('four_you', 'Просрочено', tone='warning')]),
        })
        _, items, _meta = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual('Просрочено', items[0]['title'])
        # Остальные обязаны сохранить свой порядок — сортировка устойчивая.
        self.assertEqual(['Просрочено', 'Регламент', 'Новый пост'],
                         [i['title'] for i in items])

    def test_sources_keep_their_own_order(self):
        """Внутри источника порядок задаёт его ORDER BY и трогать его нельзя.

        У ознакомлений и опросов `at` — это СРОК, а не время события. Сортировка
        всего списка по дате подняла бы наверх самый дальний дедлайн.
        """
        self._stub({'wiki_ack': lambda c, v, limit: (3, [
            _item('wiki_ack', 'Завтра', at='2026-08-10T00:00:00'),
            _item('wiki_ack', 'Через месяц', at='2026-09-10T00:00:00'),
            _item('wiki_ack', 'Без срока', at=None),
        ])})
        _, items, _meta = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(['Завтра', 'Через месяц', 'Без срока'],
                         [i['title'] for i in items])

    def test_total_is_sum_of_sources(self):
        self._stub({
            'events': lambda c, v, limit: (2, []),
            'lms': lambda c, v, limit: (3, []),
        })
        counts, _, _meta = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(5, counts['total'])

    def test_broken_source_does_not_kill_the_rest(self):
        """Сломанный раздел даёт ноль, а не 500 на весь колокол."""
        def explode(cursor, viewer, limit):
            raise RuntimeError('таблицы ещё нет')

        self._stub({'wiki_ack': explode, 'lms': lambda c, v, limit: (4, [_item('lms', 'Урок')])})
        with self.assertLogs(level='ERROR'):
            counts, items, _meta = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(0, counts['wiki_ack'])
        self.assertEqual(4, counts['lms'])
        self.assertEqual(4, counts['total'])
        self.assertEqual(['Урок'], [i['title'] for i in items])

    def test_broken_source_rolls_back_to_savepoint(self):
        """Иначе упавший источник оставил бы транзакцию в aborted-состоянии."""
        def explode(cursor, viewer, limit):
            cursor.execute('SELECT неверно')
            raise RuntimeError('boom')

        self._stub({'events': explode})
        cursor = FakeCursor()
        with self.assertLogs(level='ERROR'):
            sources.collect(cursor, {'user_id': 1})
        self.assertIn('ROLLBACK', cursor.commands)
        self.assertEqual(cursor.commands.count('SAVEPOINT'), cursor.commands.count('RELEASE'))

    def test_hidden_source_is_skipped_entirely(self):
        called = []
        self._stub({'four_you': lambda c, v, limit: (called.append(1), (9, []))[1]})
        counts, _, _meta = sources.collect(FakeCursor(), {'user_id': 1, 'hidden_sources': ('four_you',)})
        self.assertEqual(0, counts['four_you'])
        self.assertEqual([], called)


class PaginationTest(unittest.TestCase):
    """Догрузка порциями: счётчик считает всё, элементы приходят частями.

    Без неё бейдж «6» висел над пятью карточками, и до шестой было не добраться:
    пять элементов помещаются в панель целиком, так что даже прокрутить список
    было нельзя.
    """

    def setUp(self):
        self.original = dict(sources._HANDLERS)

    def tearDown(self):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(self.original)

    def _stub(self, mapping):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(mapping)

    @staticmethod
    def _source_of(name, count):
        """Источник с `count` элементами, честно отдающий не больше лимита."""
        def handler(cursor, viewer, limit):
            return count, [_item(name, '%s %d' % (name, i)) for i in range(1, min(count, limit) + 1)]
        return handler

    def test_limit_reaches_the_sixth_item(self):
        self._stub({'tasks': self._source_of('tasks', 6)})

        counts, items, meta = sources.collect(FakeCursor(), {'user_id': 1}, limit=5)
        self.assertEqual(6, counts['total'], 'счётчик всегда считает всё')
        self.assertEqual(5, len(items))
        self.assertTrue(meta['has_more'], 'шестой элемент есть — клиент обязан узнать об этом')

        counts, items, meta = sources.collect(FakeCursor(), {'user_id': 1}, limit=10)
        self.assertEqual(6, len(items), 'следующая порция дотягивает остаток')
        self.assertFalse(meta['has_more'], 'больше нечего подгружать')

    def test_aggregated_source_never_asks_for_more(self):
        """«4 You» сворачивает 12 фото в ОДНУ строку.

        Наивное «счётчик больше показанного» заставило бы клиент бесконечно
        просить следующую порцию, ничего нового не получая.
        """
        self._stub({'four_you': lambda c, v, limit: (12, [_item('four_you', 'Новые фото: 12')])})
        counts, items, meta = sources.collect(FakeCursor(), {'user_id': 1}, limit=5)
        self.assertEqual(12, counts['total'])
        self.assertEqual(1, len(items))
        self.assertFalse(meta['has_more'])

    def test_limit_is_clamped_to_sane_range(self):
        """Кривой параметр — повод взять ближайшее допустимое, а не отдать 400."""
        seen = []

        def handler(cursor, viewer, limit):
            seen.append(limit)
            return 0, []

        self._stub({'lms': handler})
        sources.collect(FakeCursor(), {'user_id': 1}, limit=10 ** 6)
        sources.collect(FakeCursor(), {'user_id': 1}, limit=0)
        sources.collect(FakeCursor(), {'user_id': 1}, limit=-5)
        sources.collect(FakeCursor(), {'user_id': 1}, limit='пять')
        self.assertEqual(
            [sources.MAX_ITEMS_PER_SOURCE] + [sources.ITEMS_PER_SOURCE] * 3, seen,
            'слишком большая порция режется по потолку, любая бессмыслица — дефолт',
        )

    def test_has_more_survives_a_broken_source(self):
        """Упавший источник даёт ноль и не мешает догружать соседний."""
        def explode(cursor, viewer, limit):
            raise RuntimeError('таблицы ещё нет')

        self._stub({'wiki_ack': explode, 'tasks': self._source_of('tasks', 9)})
        with self.assertLogs(level='ERROR'):
            counts, items, meta = sources.collect(FakeCursor(), {'user_id': 1}, limit=5)
        self.assertEqual(0, counts['wiki_ack'])
        self.assertEqual(5, len(items))
        self.assertTrue(meta['has_more'])


class NextChangeAtTest(unittest.TestCase):
    """Момент следующего перехода по часам — замена фоновой сверке.

    Всё остальное в колоколе меняется от записи в БД и приезжает триггером
    мгновенно. Ход часов записи не оставляет, и раньше его ловил опрос раз в
    минуту; теперь сервер называет точный момент, а клиент спит до него.
    """

    def test_asks_only_for_sources_the_viewer_can_see(self):
        """Скрытому источнику незачем будить чужую вкладку своими дедлайнами."""
        class Cursor(FakeCursor):
            def __init__(self):
                super().__init__()
                self.sql = []

            def execute(self, sql, params=None):
                self.sql.append(sql)
                super().execute(sql, params)

            def fetchone(self):
                return (None,)

        cursor = Cursor()
        sources.next_change_at(cursor, {'user_id': 1, 'hidden_sources': ('tasks', 'surveys')})
        sql = ' '.join(cursor.sql)
        self.assertIn('wiki_ack_assignments', sql)
        self.assertNotIn('FROM tasks', sql)
        self.assertNotIn('survey_assignments', sql)

    def test_returns_none_when_every_source_is_hidden(self):
        """Ни одного источника — ни одного запроса и никакого таймера."""
        cursor = FakeCursor()
        self.assertIsNone(sources.next_change_at(
            cursor, {'user_id': 1, 'hidden_sources': sources.SOURCES}))
        self.assertEqual([], cursor.commands, 'пустой запрос в базу не уходит')

    def test_collect_reports_it_and_survives_a_broken_query(self):
        # Ровно через час: клиенту уходит ИНТЕРВАЛ, а не абсолютное время —
        # наивную строку браузер прочитал бы как своё локальное время.
        upcoming = sources._almaty_now() + timedelta(hours=1)

        class Cursor(FakeCursor):
            def fetchone(self):
                return (upcoming,)

        original = dict(sources._HANDLERS)
        sources._HANDLERS.clear()
        sources._HANDLERS.update({'lms': lambda c, v, limit: (0, [])})
        # Дни рождения скрыты намеренно: они добавляют к моменту перехода
        # ближайшую полночь, и после 23:00 она обогнала бы проверяемый здесь
        # срок из базы — тест падал бы раз в сутки на пустом месте. Сама
        # полночь проверяется отдельно, в BirthdaysSourceTest.
        viewer = {'user_id': 1, 'hidden_sources': ('birthdays',)}
        try:
            _, _, meta = sources.collect(Cursor(), viewer)
            self.assertAlmostEqual(3600, meta['next_change_in'], delta=2)

            class Broken(FakeCursor):
                def fetchone(self):
                    raise RuntimeError('нет таблицы опросов')

            cursor = Broken()
            with self.assertLogs(level='ERROR'):
                _, _, meta = sources.collect(cursor, viewer)
            self.assertIsNone(meta['next_change_in'], 'сводка важнее таймера')
            self.assertIn('ROLLBACK', cursor.commands)
            self.assertEqual(cursor.commands.count('SAVEPOINT'),
                             cursor.commands.count('RELEASE'))
        finally:
            sources._HANDLERS.clear()
            sources._HANDLERS.update(original)


class ClientWakesUpOnScheduleTest(unittest.TestCase):
    """Клиент обязан спать до названного момента, а не опрашивать сервер."""

    SOURCE = BELL_JSX.read_text(encoding='utf-8')

    def test_schedules_a_timer_instead_of_polling(self):
        self.assertIn('next_change_in', self.SOURCE)
        self.assertIn('scheduleNextChange', self.SOURCE)
        self.assertNotIn('setInterval', self.SOURCE)

    def test_timer_is_bounded_to_a_day(self):
        """setTimeout переполняется на 24,8 днях и срабатывает мгновенно —
        это дало бы ровно тот бесконечный цикл, ради ухода от которого всё."""
        self.assertIn('24 * 60 * 60 * 1000', self.SOURCE)

    def test_wakes_up_after_the_moment_not_before(self):
        """Пробуждение «за миг до» вернуло бы ту же сводку и тот же интервал."""
        self.assertIn('untilChange * 1000 + 1000', self.SOURCE)

    def test_interval_not_absolute_time_crosses_timezones(self):
        """Наивный ISO браузер читает как локальное время — в другом поясе
        таймер уехал бы на часы. Поэтому сервер шлёт секунды."""
        self.assertNotIn('next_change_at', self.SOURCE)
        self.assertIn('Number(seconds)', self.SOURCE)


class IncomingNotificationEffectsTest(unittest.TestCase):
    """Звон колокола и выезжающая карточка при новом уведомлении."""

    SOURCE = BELL_JSX.read_text(encoding='utf-8')
    STYLES = (ROOT / 'src' / 'styles.css').read_text(encoding='utf-8')
    TAILWIND = (ROOT / 'tailwind.config.cjs').read_text(encoding='utf-8')

    def test_ring_animation_is_defined_and_used(self):
        self.assertIn("'bell-ring'", self.TAILWIND)
        self.assertIn('animate-bell-ring', self.SOURCE)
        # Качается вокруг точки крепления, а не вокруг центра значка.
        self.assertIn('.bell-icon-ring', self.STYLES)
        self.assertIn('transform-origin', self.STYLES)

    def test_second_notification_in_a_row_also_rings(self):
        """Без смены key React переиспользует элемент и анимация не повторится."""
        self.assertIn('key={ringNonce}', self.SOURCE)
        self.assertIn('setRingNonce((value) => value + 1)', self.SOURCE)

    def test_three_guards_against_false_alarms(self):
        """Вход в портал, догрузка порции и открытая панель — не повод звенеть."""
        start = self.SOURCE.index('const announce = useCallback(')
        block = self.SOURCE[start:self.SOURCE.index('announceRef.current = announce;', start)]
        # Первый ответ только запоминает состав (иначе звон всему накопленному).
        self.assertIn('!known', block)
        # Догрузка не растит счётчик — значит и уведомлений не прибавилось.
        self.assertIn('nextTotal <= prevTotal', block)
        # При открытом списке человек и так всё видит.
        self.assertIn('openRef.current', block)

    def test_rings_even_when_the_new_one_is_beyond_the_page(self):
        """Реальный случай: супервайзер поставил задачу — счётчик вырос, звона нет.

        Список отсортирован по важности, и свежая задача «поручена, работа не
        начата» стоит ЗА просроченными, то есть за пределами порции из пяти.
        Значит звонить надо по росту счётчика, а что именно пришло — выяснять
        отдельным запросом расширенной выдачи.
        """
        start = self.SOURCE.index('const announce = useCallback(')
        block = self.SOURCE[start:self.SOURCE.index('announceRef.current = announce;', start)]
        # Звон — сразу по росту счётчика, до выяснения подробностей. Сравниваем
        # именно с разведкой при росте: в ветке первой загрузки она тоже есть,
        # но там звонить не о чем.
        ring_at = block.index('setRingNonce')
        probe_at = block.index('probeBeyondPage(known)')
        self.assertLess(ring_at, probe_at, 'сигнал важнее деталей — звон не ждёт запроса')
        self.assertIn('if (!fresh.length) fresh = await probeBeyondPage(known);', block)
        # Не нашли конкретное — показываем хотя бы сам факт.
        self.assertIn('added: nextTotal - prevTotal', block)

    def test_probe_remembers_the_tail(self):
        """Иначе хвост выдачи прозвенит повторно при следующем росте счётчика."""
        start = self.SOURCE.index('const probeBeyondPage = useCallback(')
        block = self.SOURCE[start:self.SOURCE.index('/* Пришла новая сводка', start)]
        self.assertIn('knownKeysRef.current?.add', block)
        self.assertIn('limit: MAX_PAGE_SIZE', block)

    def test_first_answer_learns_the_hidden_tail_too(self):
        """Иначе карточка покажет давно лежащий хвост вместо новой задачи.

        Клиент видит порцию из пяти; всё, что за ней, при первом же росте
        счётчика выглядело бы «новым» — и в карточку попадала бы чужая старая
        задача, а не только что поставленная.
        """
        start = self.SOURCE.index('const announce = useCallback(')
        block = self.SOURCE[start:self.SOURCE.index('announceRef.current = announce;', start)]
        self.assertIn('if (hasMoreItems) await probeBeyondPage(nextKeys);', block)
        # Признак «есть скрытое» приходит из той же сводки.
        self.assertIn("Boolean(response?.data?.has_more)", self.SOURCE)

    def test_toast_is_not_marked_as_the_dropdown(self):
        """Класс .notifications-dropdown держит свёрнутый сайдбар развёрнутым
        (правило :has), и каждое уведомление дёргало бы всю вёрстку."""
        start = self.SOURCE.index('const toastCard =')
        block = self.SOURCE[start:self.SOURCE.index('const toastNode =', start)]
        self.assertIn('notifications-toast', block)
        self.assertNotIn('notifications-dropdown', block)
        # На мобильном вправо выпадать некуда — там своё позиционирование.
        self.assertIn('.sidebar .notifications-toast', self.STYLES)

    def test_details_are_shown_on_the_card(self):
        """Ради деталей карточка и раскрывается — в списке им места нет."""
        start = self.SOURCE.index('const toastCard =')
        block = self.SOURCE[start:self.SOURCE.index('const toastNode =', start)]
        self.assertIn('toastItem.body', block)
        self.assertIn('toastItem.title', block)

    def test_reduced_motion_is_respected(self):
        self.assertIn('prefers-reduced-motion', self.STYLES)
        block = self.STYLES[self.STYLES.index('prefers-reduced-motion'):]
        self.assertIn('.bell-icon-ring', block[:400])
        self.assertIn('.notifications-toast', block[:400])


class MobileIncomingTest(unittest.TestCase):
    """Телефон: сайдбар за краем экрана, поэтому сигналит гамбургер.

    Без этого уведомление на телефоне не видно вообще: и колокол, и карточка
    живут внутри сайдбара, который при закрытом меню уехал за экран.
    """

    BELL = BELL_JSX.read_text(encoding='utf-8')
    APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
    STYLES = (ROOT / 'src' / 'styles.css').read_text(encoding='utf-8')

    def test_bell_tells_the_app_about_incoming(self):
        self.assertIn('onIncoming?.();', self.BELL)
        self.assertIn('onIncoming={stableNotificationsIncoming}', self.APP)

    def test_hamburger_turns_into_a_ringing_bell(self):
        start = self.APP.index('className={`hamburger-btn')
        block = self.APP[start:self.APP.index('</button>', start)]
        # Меню закрыто и что-то пришло — вместо полосок колокол, и он звенит.
        self.assertIn("mobileIncomingNonce > 0 ? 'fa-bell bell-icon-ring animate-bell-ring'", block)
        # Открытое меню важнее: там крестик, а не сигнал.
        self.assertIn("mobileMenuOpen\n                                    ? 'fa-times'", block)
        # key перезапускает качание на каждом следующем уведомлении.
        self.assertIn('key={mobileMenuOpen ? \'close\' : `bell-${mobileIncomingNonce}`}', block)

    def test_button_and_card_disappear_together(self):
        """Иначе колокол остался бы висеть, когда карточка уже пропала."""
        self.assertIn('const MOBILE_INCOMING_VISIBLE_MS = 7000;', self.APP)
        self.assertIn('const TOAST_VISIBLE_MS = 7000;', self.BELL)

    def test_card_leaves_the_hidden_sidebar_on_a_phone(self):
        self.assertIn('const toastDetached = isNarrow && !mobileMenuOpen;', self.BELL)
        self.assertIn('createPortal(toastCard, document.body)', self.BELL)
        # Тот же порог, что у CSS сайдбара, иначе состояния разъедутся.
        self.assertIn("matchMedia('(max-width: 768px)')", self.BELL)
        # И встаёт она ровно под гамбургером (он занимает 16..60px).
        self.assertIn('.notifications-toast-floating', self.STYLES)
        block = self.STYLES[self.STYLES.index('.notifications-toast-floating'):]
        self.assertIn('top: 68px;', block[:260])

    def test_floating_card_is_above_the_hamburger_layer(self):
        """Гамбургер сидит на z-index 60 — карточка обязана быть выше."""
        self.assertIn('notifications-toast-floating fixed z-[61]', self.BELL)
        block = self.STYLES[self.STYLES.index('.hamburger-btn {'):]
        self.assertIn('z-index: 60;', block[:400])


class ClientPaginationContractTest(unittest.TestCase):
    """Клиент и сервер обязаны сойтись в размере порции и в её потолке."""

    SOURCE = BELL_JSX.read_text(encoding='utf-8')

    def test_page_size_matches_server(self):
        self.assertIn('const PAGE_SIZE = %d;' % sources.ITEMS_PER_SOURCE, self.SOURCE)
        self.assertIn('const MAX_PAGE_SIZE = %d;' % sources.MAX_ITEMS_PER_SOURCE, self.SOURCE)

    def test_client_asks_for_the_next_page_by_observing_the_bottom(self):
        """Обработчик прокрутки тут не работает: пять карточек не прокручиваются."""
        self.assertIn('IntersectionObserver', self.SOURCE)
        self.assertIn('params: { limit: pageSizeRef.current }', self.SOURCE)
        self.assertIn('has_more', self.SOURCE)


class SurveyWindowTimezoneTest(unittest.TestCase):
    """Окно теста считается во времени Алматы, а не в UTC базы.

    starts_at/ends_at хранятся наивными во времени Алматы: их пишет
    _parse_survey_schedule_value без tzinfo, а сравнивает survey_test_status с
    datetime.now() — процесс живёт в Asia/Almaty. База стоит в UTC, поэтому
    голый CURRENT_TIMESTAMP в запросе даёт сдвиг ровно на 5 часов, и колокол
    показывает тест открытым ещё пять часов после закрытия.

    Дефект молчаливый: ошибки нет, цифра просто неверная. Поэтому проверяется и
    исходник (чтобы CURRENT_TIMESTAMP не вернулся), и поведение на настоящей
    базе.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')

    def test_window_does_not_use_bare_current_timestamp(self):
        block = re.search(r'def surveys\(.*?\n    \)\n', self.SOURCE, re.S)
        self.assertIsNotNone(block, 'не найдено тело запроса опросов')
        sql = block.group(0)
        self.assertNotRegex(
            sql, r'(starts_at|ends_at)\s*[<>]=?\s*CURRENT_TIMESTAMP',
            'окно теста снова сравнивается с UTC-временем базы — сдвиг 5 часов',
        )
        self.assertIn('%(now)s', sql, 'время должно приходить параметром')

    def test_almaty_now_matches_process_clock(self):
        """Тот же вызов, что и у Database.survey_test_status."""
        self.assertAlmostEqual(
            sources._almaty_now().timestamp(), datetime.now().timestamp(), delta=2,
        )

    def test_closed_window_is_closed_on_real_postgres(self):
        """Окно, закрывшееся два часа назад, обязано считаться закрытым."""
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)
        now = sources._almaty_now()
        closed = now - timedelta(hours=2)
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute('SELECT %(ends)s::timestamp > %(now)s::timestamp',
                           {'ends': closed, 'now': now})
            self.assertFalse(cursor.fetchone()[0],
                             'закрывшийся тест не должен считаться открытым')

            # Показываем, чем именно был дефект: сдвиг между часами процесса и
            # часами базы. Утверждение делаем только если сдвиг реально есть —
            # на машине разработчика в UTC его не будет, и падать тут не за что.
            cursor.execute('SELECT CURRENT_TIMESTAMP::timestamp')
            db_now = cursor.fetchone()[0]
            shift_hours = (now - db_now).total_seconds() / 3600
            if shift_hours <= 2:
                self.skipTest('часы процесса и базы совпадают (сдвиг %.1f ч) — '
                              'сравнивать не с чем' % shift_hours)
            cursor.execute('SELECT %(ends)s::timestamp > CURRENT_TIMESTAMP',
                           {'ends': closed})
            self.assertTrue(
                cursor.fetchone()[0],
                'при сдвиге %.1f ч голый CURRENT_TIMESTAMP обязан был бы считать '
                'закрытый тест открытым — иначе дефект был не в этом' % shift_hours,
            )
        finally:
            prod_db.rollback()
            cursor.close()


class MarkSeenRulesTest(unittest.TestCase):
    """Что вообще можно погасить просмотром, а что только действием."""

    def test_action_bound_sources_are_not_clearable(self):
        cursor = FakeCursor()
        for source in ('wiki_ack', 'surveys', 'tasks'):
            self.assertFalse(
                sources.mark_seen(cursor, 1, source),
                'источник %s нельзя гасить: он снимается действием, иначе счётчик '
                'обязательного документа обнулялся бы просмотром колокола' % source,
            )
        self.assertEqual([], cursor.commands)

    def test_watermark_sources_are_clearable(self):
        for source in ('events', 'four_you', 'lms'):
            cursor = FakeCursor()
            self.assertTrue(sources.mark_seen(cursor, 1, source))
            self.assertTrue(cursor.commands, 'должен был выполнить запрос')

    def test_unknown_source_is_ignored(self):
        self.assertFalse(sources.mark_seen(FakeCursor(), 1, 'нет-такого'))


class TasksSourceRulesTest(unittest.TestCase):
    """Источник «Задачи» — третья копия правил «задача ждёт вас».

    Первые две — SQL бейджа (database.py::get_task_action_needs_summary) и
    клиентские правила раздела (taskActionNeeds.js). Дрейф копий не падает и
    не ошибается — числа на экране просто молча расходятся, поэтому маркеры
    правил сверяются по исходнику, как это уже делает
    test_task_backlog_board.ActionNeedsBadgeTests для первых двух.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')

    def _block(self):
        start = self.SOURCE.index('def tasks(cursor, viewer, limit):')
        return self.SOURCE[start:self.SOURCE.index('_HANDLERS = {', start)]

    def test_rules_match_badge_sql(self):
        block = self._block()
        # Просрочка — только у исполнителя и только по живым статусам.
        self.assertIn("t.status IN ('assigned', 'in_progress', 'returned')", block)
        # Приёмку ждёт поручитель, а если его нет — постановщик.
        self.assertIn("COALESCE(t.requested_by_id, t.created_by) = %(user_id)s", block)
        self.assertIn("t.status = 'completed'", block)
        # Бэклог не считается: это очередь планирования.
        self.assertIn("t.is_backlog = FALSE", block)
        # У каждой причины своя проверка отметки «просмотрено».
        for kind in ('overdue', 'returned', 'review', 'fresh'):
            self.assertIn(
                "r.kind <> '%s' OR r.seen_at < t.updated_at" % kind, block,
            )

    def test_acceptance_reaches_the_assignee(self):
        """«Работу приняли» — единственное уведомление «к сведению».

        Остальные четыре означают «сделай», и принятая задача в них не
        помещалась: делать с ней нечего, поэтому раньше исполнитель о приёмке
        не узнавал вовсе.
        """
        block = self._block()
        self.assertIn("t.status = 'accepted'", block)
        # Исполнителем считается ЛЮБОЙ из состава: задачу могли поручить
        # нескольким, и о приёмке должен узнать каждый.
        self.assertIn("AND EXISTS (SELECT 1 FROM task_assignees ta", block)
        # Отметка ВЕЧНАЯ: сравнения с updated_at быть не должно, иначе правка
        # отчёта воскресит приёмку недельной давности и колокол зазвонит снова.
        self.assertIn("r.kind <> 'accepted'", block)
        self.assertNotIn("r.kind <> 'accepted' OR r.seen_at < t.updated_at", block)
        # Сам себе принял — уведомлять не о чем.
        self.assertIn("COALESCE(t.requested_by_id, t.created_by) IS DISTINCT FROM %(user_id)s", block)
        self.assertIn("'accepted': 'Работу приняли'", self.SOURCE)

    def test_information_request_reaches_the_side_that_answers(self):
        """«Просят информацию» — причина стороны постановки, а не исполнителя."""
        block = self._block()
        self.assertIn("t.info_request_id IS NOT NULL", block)
        self.assertIn("r.kind <> 'info' OR r.seen_at < t.updated_at", block)
        # Спрашивает исполнитель — ни одному из состава вопрос обратно не
        # показываем: причина адресована стороне постановки.
        self.assertIn("AND NOT EXISTS (SELECT 1 FROM task_assignees ta", block)
        self.assertIn("'info': 'Исполнителю не хватает информации'", self.SOURCE)
        # Бэклог у этой причины НЕ отсекается: вопрос задал живой человек, и
        # «задача ещё в очереди» ответа не отменяет.
        info_start = block.index("OR (t.info_request_id IS NOT NULL")
        info_tail = block[info_start:block.index("OR (EXISTS (SELECT 1 FROM task_assignees ta", info_start)]
        self.assertNotIn("is_backlog", info_tail)

    def test_information_request_is_classified_before_the_deadline_check(self):
        """Зритель тут не исполнитель: «просрочена» и «не начата» — про другого."""
        block = self._block()
        info_at = block.index("THEN 'info'")
        overdue_at = block.index("THEN 'overdue'")
        self.assertLess(info_at, overdue_at)

    def test_accepted_is_classified_before_the_deadline_check(self):
        """У принятой задачи дедлайн давно позади — иначе она станет «просрочена»."""
        block = self._block()
        accepted_at = block.index("WHEN t.status = 'accepted' THEN 'accepted'")
        overdue_at = block.index("THEN 'overdue'")
        self.assertLess(accepted_at, overdue_at)

    def test_accepted_is_last_in_order(self):
        """Информация уступает делам: принятая уходит в конец списка."""
        block = self._block()
        self.assertIn(
            "ARRAY['overdue', 'returned', 'info', 'review', 'fresh', 'accepted']", block
        )

    def test_now_is_process_clock_not_db_clock(self):
        """due_at хранится наивным во времени Алматы, база — в UTC.

        Сравнение с голым CURRENT_TIMESTAMP давало бы сдвиг на 5 часов, как это
        уже было с окнами тестов у опросов: задача считалась бы просроченной на
        5 часов раньше срока.
        """
        block = self._block()
        self.assertNotRegex(
            block, r'due_at\s*[<>]=?\s*CURRENT_TIMESTAMP',
            'дедлайн снова сравнивается с UTC-временем базы — сдвиг 5 часов',
        )
        self.assertIn('%(now)s', block, 'время должно приходить параметром')

    def test_items_shape_and_tone(self):
        """overdue горит и показывает срок; остальные — момент события."""
        rows = [
            (7, 'Отчёт по сменам', datetime(2026, 8, 1), datetime(2026, 7, 20), 'overdue', 2),
            (9, 'Витрина KPI', None, datetime(2026, 8, 8), 'fresh', 2),
        ]

        class Cursor(FakeCursor):
            def fetchall(self):
                return rows

        total, items = sources.tasks(Cursor(), {'user_id': 1}, sources.ITEMS_PER_SOURCE)
        self.assertEqual(2, total)
        self.assertEqual(['warning', 'default'], [i['tone'] for i in items])
        self.assertEqual('2026-08-01T00:00:00', items[0]['at'])   # срок
        self.assertEqual('2026-08-08T00:00:00', items[1]['at'])   # когда поручили
        self.assertEqual(['tasks', 'tasks'], [i['view'] for i in items])
        self.assertEqual([7, 9], [i['target'] for i in items])
        self.assertEqual('Просрочена', items[0]['body'])


class FailureResponseTest(unittest.TestCase):
    """Отказ инфраструктуры обязан выглядеть отказом, а не пустой сводкой.

    Клиент пишет counts прямо в бейджи сайдбара. Отдай сервер 200 с нулями при
    исчерпанном пуле — у человека погасли бы «Ивенты» и «4 You», а просроченный
    документ под обязательное ознакомление исчез бы с экрана, и отличить это от
    честного «ничего нет» было бы нельзя ни ему, ни фронту.
    """

    def _client(self, *, resolver=None, viewer_ctx=None, db=None):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        class DeadDb:
            def _get_cursor(self):
                raise TimeoutError('POSTGRES_POOL_ACQUIRE_TIMEOUT')

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=db or DeadDb(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=resolver or (lambda: (2, None, None)),
            viewer_context=viewer_ctx or (lambda rid, r: {'user_id': rid}),
        ))
        return app.test_client()

    def test_pool_exhaustion_is_503_not_empty_summary(self):
        with self.assertLogs(level='ERROR'):
            response = self._client().get('/api/notifications')
        self.assertEqual(503, response.status_code)
        self.assertNotIn('counts', response.get_json())

    def test_failure_inside_viewer_context_is_also_json(self):
        """Периметр зрителя тоже ходит в базу — раньше отсюда летел HTML-500."""
        def boom(requester_id, requester):
            raise TimeoutError('POSTGRES_POOL_ACQUIRE_TIMEOUT')

        with self.assertLogs(level='ERROR'):
            response = self._client(viewer_ctx=boom).get('/api/notifications')
        self.assertEqual(503, response.status_code)
        self.assertEqual('application/json', response.headers['Content-Type'].split(';')[0])

    def test_auth_error_is_not_swallowed_into_503(self):
        client = self._client(resolver=lambda: (None, None, ('Unauthorized', 401)))
        response = client.get('/api/notifications')
        self.assertEqual(401, response.status_code)


class SeenEndpointTest(unittest.TestCase):
    """Разбор тела запроса у POST /seen — на подставном курсоре, без базы."""

    def setUp(self):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        self.executed = []
        outer = self

        class Cursor:
            def execute(self, sql, params=None):
                outer.executed.append(sql.strip().split()[0].upper())

        class Db:
            def _get_cursor(self):
                import contextlib

                @contextlib.contextmanager
                def cm():
                    yield Cursor()
                return cm()

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=Db(), require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (2, None, None),
            viewer_context=lambda rid, r: {'user_id': rid},
        ))
        self.client = app.test_client()

    def post(self, payload):
        return self.client.post('/api/notifications/seen', json=payload)

    def test_duplicates_are_collapsed(self):
        """Иначе лишний UPDATE и ответ вида ["events", "events"]."""
        response = self.post({'sources': ['events', 'events', 'four_you']})
        self.assertEqual(200, response.status_code)
        self.assertEqual(['events', 'four_you'], response.get_json()['marked'])
        self.assertEqual(2, len(self.executed), 'на каждый источник ровно один запрос')

    def test_unknown_source_is_rejected_without_touching_db(self):
        response = self.post({'sources': ['нет-такого']})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.executed)

    def test_single_source_form_works(self):
        self.assertEqual(['lms'], self.post({'source': 'lms'}).get_json()['marked'])

    def test_empty_body_is_rejected(self):
        self.assertEqual(400, self.post({}).status_code)

    def test_action_bound_source_is_accepted_but_marks_nothing(self):
        """Фронт его не шлёт, но ответ обязан быть честным: ничего не погашено."""
        response = self.post({'sources': ['wiki_ack']})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.get_json()['marked'])
        self.assertEqual([], self.executed)


class RealtimeStateMixin:
    """Модуль realtime держит состояние процесса — тесты обязаны его вернуть."""

    def setUp(self):
        self._saved = (list(realtime._ticks), realtime._seq,
                       realtime._active_streams, realtime._listener_started)
        realtime._ticks.clear()
        realtime._seq = 0
        realtime._active_streams = 0
        # Слушатель в тестах не поднимается: базы нет, поток крутился бы в
        # цикле переподключений до конца прогона.
        realtime._listener_started = True

    def tearDown(self):
        ticks, seq, streams, started = self._saved
        realtime._ticks.clear()
        realtime._ticks.extend(ticks)
        realtime._seq = seq
        realtime._active_streams = streams
        realtime._listener_started = started


class RealtimeTicksTest(RealtimeStateMixin, unittest.TestCase):
    """Разбор payload'ов триггеров и матчинг тычков по адресату."""

    def test_payload_parsing(self):
        self.assertIsNone(realtime._parse_payload('{"b":1}'), 'широковещательный')
        self.assertEqual(frozenset({3, 7}), realtime._parse_payload('{"u":[3,7]}'))
        # Мусор игнорируется, а не будит всех: сломанный payload не повод
        # устраивать всем клиентам массовую перечитку.
        self.assertIs(False, realtime._parse_payload('не-json'))
        self.assertIs(False, realtime._parse_payload('[1,2]'))
        self.assertIs(False, realtime._parse_payload('{"u":[]}'))
        self.assertIs(False, realtime._parse_payload(''))

    def test_targeted_tick_reaches_only_its_user(self):
        realtime._publish(frozenset({5}))
        poked, seq = realtime.wait_for_tick(0, 5, 0.1)
        self.assertTrue(poked)
        poked_other, _ = realtime.wait_for_tick(0, 6, 0.1)
        self.assertFalse(poked_other, 'чужой адресный тычок не должен будить')
        # Курсор продвинулся — при следующем ожидании тот же тычок не всплывёт.
        poked_again, _ = realtime.wait_for_tick(seq, 5, 0.1)
        self.assertFalse(poked_again)

    def test_broadcast_reaches_everyone(self):
        realtime._publish(None)
        for user_id in (1, 99):
            poked, _ = realtime.wait_for_tick(0, user_id, 0.1)
            self.assertTrue(poked)

    def test_buffer_gap_forces_reload_even_without_matching_tick(self):
        """Вытеснённый адресный тик мог быть нашим — угадывать нельзя."""
        for _ in range(realtime.TICK_BUFFER_MAXLEN + 1):
            realtime._publish(frozenset({999}))

        poked, seq = realtime.wait_for_tick(0, 5, 0.1)

        self.assertTrue(poked, 'разрыв курсора обязан принудить полную сверку')
        self.assertEqual(realtime.current_seq(), seq)

    def test_subscribe_broadcasts_resync_after_listen(self):
        commands = []

        class Cursor:
            def execute(self, sql):
                commands.append(sql)

        class Connection:
            def set_session(self, **kwargs):
                self.session = kwargs

            def cursor(self):
                return Cursor()

        connection = Connection()
        realtime._subscribe(connection)

        self.assertEqual({'autocommit': True}, connection.session)
        self.assertEqual(['LISTEN %s' % realtime.BELL_NOTIFY_CHANNEL], commands)
        for user_id in (1, 99):
            poked, _ = realtime.wait_for_tick(0, user_id, 0.1)
            self.assertTrue(poked, 'после LISTEN все живые потоки сверяют сводку')

    def test_stream_slots_are_bounded(self):
        self.assertTrue(realtime.try_acquire_stream_slot(2))
        self.assertTrue(realtime.try_acquire_stream_slot(2))
        self.assertFalse(realtime.try_acquire_stream_slot(2), 'мест ровно limit')
        realtime.release_stream_slot()
        self.assertTrue(realtime.try_acquire_stream_slot(2))


class StreamEndpointTest(RealtimeStateMixin, unittest.TestCase):
    """SSE-канал: выключен без базы, ограничен слотами, тычок будит клиента."""

    def _client(self, *, listen_connect=None, stream_limit=50):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=object(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (2, None, None),
            viewer_context=lambda rid, r: {'user_id': rid},
            listen_connect=listen_connect,
            stream_limit=stream_limit,
        ))
        return app.test_client()

    def test_disabled_without_listener_factory(self):
        """Юнит-тесты и сломанная фабрика: канал честно 503, колокол на фокусе."""
        response = self._client().get('/api/notifications/stream')
        self.assertEqual(503, response.status_code)

    def test_over_capacity_is_503_with_retry_after(self):
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        self.assertTrue(realtime.try_acquire_stream_slot(1))  # место занято
        response = client.get('/api/notifications/stream')
        self.assertEqual(503, response.status_code)
        self.assertEqual('300', response.headers.get('Retry-After'))
        # Отказ не должен съесть слот.
        self.assertEqual(1, realtime.active_stream_count())

    def test_tick_wakes_stream_and_slot_returns_on_close(self):
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        response = client.get('/api/notifications/stream', buffered=False)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/event-stream', response.mimetype)
        stream = response.response if hasattr(response.response, '__next__') \
            else iter(response.response)
        first = next(stream)
        self.assertIn(b'connected', first)
        self.assertEqual(1, realtime.active_stream_count(), 'поток держит слот')

        realtime._publish(frozenset({2}))  # адресный тычок нашему зрителю
        self.assertIn(b'event: reload', next(stream))

        response.close()
        self.assertEqual(0, realtime.active_stream_count(),
                         'слот обязан вернуться при закрытии ответа')

    def test_idle_stream_never_asks_the_client_to_reload(self):
        """Страж от возврата фонового опроса.

        Периодическая сверка (она тут была и слала reload раз в минуту каждой
        открытой вкладке) — это обычный polling: 1440 перечиток в сутки на
        вкладку, ~285 тысяч SELECT в сутки на всех, ловивших ноль изменений.
        Молчащий поток обязан слать только heartbeat-комментарии; переходы по
        часам клиент ждёт по next_change_at из самой сводки.
        """
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        original_heartbeat = realtime.HEARTBEAT_SECONDS
        realtime.HEARTBEAT_SECONDS = 0.01
        try:
            response = client.get('/api/notifications/stream', buffered=False)
            stream = response.response if hasattr(response.response, '__next__') \
                else iter(response.response)
            self.assertIn(b'connected', next(stream))
            for _ in range(10):
                frame = next(stream)
                self.assertNotIn(b'event: reload', frame,
                                 'молчащий поток не должен требовать перечитку')
                self.assertIn(b'heartbeat', frame)
            response.close()
        finally:
            realtime.HEARTBEAT_SECONDS = original_heartbeat

    def test_no_periodic_reconcile_constant_remains(self):
        """Константа интервала сверки не должна вернуться ни в каком виде."""
        self.assertFalse(hasattr(realtime, 'RECONCILE_SECONDS'))
        routes_src = (ROOT / 'notifications' / 'routes.py').read_text(encoding='utf-8')
        self.assertNotIn('RECONCILE', routes_src)


class RealtimeTriggersPinnedTest(unittest.TestCase):
    """Триггеры в схеме и слушатель обязаны говорить об одном канале."""

    DATABASE = (ROOT / 'database.py').read_text(encoding='utf-8')

    def test_channel_name_matches_listener(self):
        self.assertIn("BELL_EVENTS_NOTIFY_CHANNEL = '%s'" % realtime.BELL_NOTIFY_CHANNEL,
                      self.DATABASE)
        block = self._trigger_block()
        self.assertIn("pg_notify('%s'" % realtime.BELL_NOTIFY_CHANNEL, block)

    def _trigger_block(self):
        start = self.DATABASE.index('def _init_bell_notify_schema_tx(self, cursor):')
        return self.DATABASE[start:self.DATABASE.index('def _init_amo_leads_schema_tx', start)]

    # Таблицы, у которых обязан быть триггер колокола. Список жёсткий
    # намеренно: он ловит удаление триггера у живого источника. Но одного его
    # мало — см. второй тест ниже, он закрывает обратную сторону.
    # `surveys` — не назначения, а сам опрос: уход в архив убирает его из
    # сводки у всех, кому он был назначен, и без своего триггера колокол узнал
    # бы об этом только по возвращении фокуса на вкладку.
    BELL_TRIGGER_TABLES = ('events', 'four_you_images', 'lms_notifications',
                           'surveys', 'survey_assignments', 'wiki_ack_assignments',
                           'tasks', 'task_assignees', 'task_action_reads',
                           'event_reads', 'four_you_reads', 'birthday_reads',
                           'crm_tickets')

    def test_every_source_table_has_a_trigger(self):
        """Таблица без триггера = источник без реалтайма, молча."""
        block = self._trigger_block()
        for table in self.BELL_TRIGGER_TABLES:
            self.assertIn("'%s'" % table, block)

    def test_expected_list_covers_every_table_that_has_a_trigger(self):
        """Обратная сторона: таблица с триггером, ВЫПАВШАЯ из списка выше.

        Проверка сверху односторонняя — она ищет таблицы из списка в тексте
        функции. Уберите таблицу ИЗ списка, и не упадёт ничего: тест просто
        перестанет её проверять, ровно так же молча, как предупреждает его
        собственный docstring.

        Это не гипотеза. Строку `birthday_reads` пришлось разрешать руками при
        переносе коммита между ветками (в одной стороне конфликта был
        `task_assignees`, в другой — `birthday_reads`), и любое «взял одну
        сторону целиком» унесло бы соседнюю таблицу, не покраснев ни одним
        тестом. Поэтому ожидания сверяются с тем, что РЕАЛЬНО объявлено в
        _init_bell_notify_schema_tx, в обе стороны.
        """
        # Множество, а не список: объявлений триггеров БОЛЬШЕ, чем таблиц —
        # у survey_assignments и wiki_ack_assignments их по два (отдельно на
        # INSERT и на UPDATE). Без дедупликации assertEqual сравнивал бы 13 с
        # 11 и падал бы на ровном месте.
        #
        # И \s* после открывающей скобки обязателен: четыре объявления
        # написаны с переносом строки после неё, и шаблон без \s* терял
        # crm_tickets — обратная проверка тогда «не находила» существующий
        # триггер и врала бы в другую сторону.
        block = self._trigger_block()
        declared = {m.group(1) for m in re.finditer(
            r"\(\s*'trg_\w+',\s*'(\w+)'", block)}
        self.assertTrue(declared, 'не нашёл ни одного объявления триггера — '
                                  'изменился формат списка, проверка ослепла')
        self.assertEqual(
            declared, set(self.BELL_TRIGGER_TABLES),
            'список ожидаемых таблиц разошёлся с объявленными триггерами: '
            'лишняя — триггера нет, недостающая — источник без проверки',
        )

    def test_watermark_updates_wake_only_the_same_user(self):
        block = self._trigger_block()
        self.assertIn("TG_TABLE_NAME IN ('event_reads', 'four_you_reads')", block)
        self.assertIn("targets := ARRAY[NEW.user_id]", block)

    def test_survey_and_wiki_progress_updates_are_filtered(self):
        block = self._trigger_block()
        self.assertIn('AFTER UPDATE OF operator_id, survey_id, status', block)
        self.assertIn(
            'AFTER UPDATE OF user_id, article_id, due_at, acknowledged_at, status',
            block,
        )
        self.assertIn("(COALESCE(OLD.status, '') = 'completed') IS NOT DISTINCT FROM", block)
        self.assertIn('OLD.acknowledged_at IS NULL', block)
        self.assertIn("COALESCE(OLD.status IN ('superseded', 'cancelled'), FALSE)", block)
        self.assertIn('OLD.due_at IS NOT DISTINCT FROM NEW.due_at', block)

    def test_trigger_never_breaks_the_write(self):
        """Ошибка тычка не должна откатывать само действие пользователя."""
        block = self._trigger_block()
        self.assertIn('EXCEPTION WHEN OTHERS THEN', block)
        # И вся установка триггеров — под SAVEPOINT в _init_db.
        self.assertIn('SAVEPOINT sp_bell_notify', self.DATABASE)

    def test_bell_client_keeps_focus_fallback(self):
        """SSE — ускорение, а не замена: обновление по фокусу обязано остаться."""
        source = BELL_JSX.read_text(encoding='utf-8')
        self.assertIn('/api/notifications/stream', source)
        self.assertIn("visibilityState === 'hidden'", source)
        self.assertIn('REFRESH_GAP_MS', source)


class FrontendAgreesWithBackendTest(unittest.TestCase):
    """Списки гасимых источников во фронте и в бэке обязаны совпадать."""

    def test_clearable_lists_match(self):
        source = BELL_JSX.read_text(encoding='utf-8')
        match = re.search(r"const CLEARABLE = \[([^\]]*)\]", source)
        self.assertIsNotNone(match, 'в компоненте пропал список CLEARABLE')
        frontend = set(re.findall(r"'([a-z_]+)'", match.group(1)))

        backend = {name for name in sources.SOURCES
                   if sources.mark_seen(FakeCursor(), 1, name)}
        self.assertEqual(
            backend, frontend,
            'фронт предлагает гасить не то, что умеет гасить сервер: '
            'кнопка «отметить прочитанным» будет обнулять счётчик впустую',
        )

    def test_every_source_has_a_label_in_the_bell(self):
        """Иначе в колоколе вместо раздела покажется его технический код."""
        source = BELL_JSX.read_text(encoding='utf-8')
        block = re.search(r"const SOURCE_META = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(block)
        labelled = set(re.findall(r"^\s{4}([a-z_]+):", block.group(1), re.M))
        self.assertEqual(set(sources.SOURCES), labelled)

class BirthdaysSourceTest(unittest.TestCase):
    """Дни рождения: периметр отдела и переход через полночь.

    Периметр здесь — не удобство, а требование: дата рождения сотрудника не
    должна попадать человеку из чужого отдела. Дыра в этом источнике не падает
    и не логируется — она просто показывает лишних людей, поэтому проверяется
    и текстом запроса, и, где есть база, поведением на синтетических данных.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')
    SERVER = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')

    class Recorder(FakeCursor):
        """Курсор, запоминающий сам запрос и его параметры."""

        def __init__(self, rows=()):
            super().__init__()
            self.rows = list(rows)
            self.sql = None
            self.params = None

        def execute(self, sql, params=None):
            self.sql, self.params = sql, params
            super().execute(sql, params)

        def fetchall(self):
            return self.rows

    def _block(self):
        start = self.SOURCE.index('def birthdays(cursor, viewer, limit):')
        return self.SOURCE[start:self.SOURCE.index('_HANDLERS = {', start)]

    # ── периметр ─────────────────────────────────────────────────────────────
    def test_scoped_viewer_sees_only_own_department_and_self(self):
        cursor = self.Recorder()
        sources.birthdays(cursor, {'user_id': 7, 'birthday_is_global': False,
                                   'birthday_department_id': 3}, 5)
        self.assertIn('u.department_id = %(dept)s', cursor.sql)
        self.assertIn('OR u.id = %(user_id)s', cursor.sql,
                      'свой день рождения обязан проходить и без отдела')
        self.assertEqual(3, cursor.params['dept'])
        self.assertEqual(7, cursor.params['user_id'])

    def test_viewer_without_department_sees_only_himself(self):
        """Отдела нет — сравнение с NULL не даст ни строки, кроме своей."""
        cursor = self.Recorder()
        sources.birthdays(cursor, {'user_id': 7, 'birthday_is_global': False,
                                   'birthday_department_id': None}, 5)
        self.assertIn('u.department_id = %(dept)s', cursor.sql)
        self.assertIsNone(cursor.params['dept'])

    def test_global_admin_sees_everyone(self):
        cursor = self.Recorder()
        sources.birthdays(cursor, {'user_id': 7, 'birthday_is_global': True,
                                   'birthday_department_id': None}, 5)
        self.assertNotIn('u.department_id', cursor.sql)

    def test_missing_perimeter_is_not_open_season(self):
        """Зритель без обоих ключей — это НЕ «показать всех»."""
        cursor = self.Recorder()
        sources.birthdays(cursor, {'user_id': 7}, 5)
        self.assertIn('u.department_id = %(dept)s', cursor.sql)

    def test_trainer_is_not_global_here(self):
        """У «Ивентов» тренер глобален, у дней рождения — нет.

        Правило владельца сформулировано узко: «только внутри отделов, админам
        можно показывать все». Скопировать _events_viewer_scope значило бы
        выдать тренеру дни рождения всей компании.
        """
        start = self.SERVER.index('def _birthdays_viewer_scope(')
        block = self.SERVER[start:self.SERVER.index('\ndef ', start + 1)]
        self.assertIn('_is_global_admin_requester(role, requester_id)', block)
        self.assertNotIn("role == 'trainer'", block)
        # requester_id обязателен: без него _headed_department_id(None) вернёт
        # None и любой админ-глава отдела молча станет глобальным.
        self.assertNotIn('_is_global_admin_requester(role)', block)

    def test_endpoint_uses_the_same_perimeter(self):
        """/api/birthdays/today не должен быть обходным путём мимо колокола."""
        start = self.SERVER.index("@app.route('/api/birthdays/today'")
        block = self.SERVER[start:self.SERVER.index('@app.route', start + 1)]
        self.assertIn('_birthdays_viewer_scope(', block)
        self.assertIn('all_departments=', block)
        self.assertIn('include_user_id=', block)
        self.assertNotIn("request.headers.get('X-User-Id')", block,
                         'личность зрителя берётся из токена, а не из заголовка')
        # Наружу уходят только id и имя: полную дату рождения с годом эндпоинт
        # раздавал, пока список рисовал баннер. Единственному потребителю —
        # вопросу «сегодня ли мой день рождения» — хватает id.
        self.assertIn('"id": row.get("id"), "name": row.get("name")', block)
        self.assertNotIn('"birthdays": birthdays', block)

    def test_viewer_context_carries_the_birthday_perimeter(self):
        """Ключи периметра обязаны приезжать из bot_schedule2: без них источник
        решит, что периметра нет, и покажет зрителю только его самого."""
        start = self.SERVER.index('def _notifications_viewer_context(')
        block = self.SERVER[start:self.SERVER.index('\ntry:', start)]
        self.assertIn("'birthday_is_global'", block)
        self.assertIn("'birthday_department_id'", block)
        # И считаться они обязаны ТОЙ ЖЕ функцией, что у эндпоинта. Правило,
        # переписанное рядом, однажды поедет в одном месте и промолчит в
        # другом, а расхождение значений ни один тест не заметит.
        self.assertIn('_birthdays_viewer_scope(', block)
        self.assertNotIn('_is_global_admin_requester(role, requester_id)', block)

    # ── содержимое строки ────────────────────────────────────────────────────
    def test_items_shape(self):
        rows = [(7, 'Мария Иванова', 'Чаты', True, 2),
                (9, 'Пётр Ким', None, False, 2)]
        total, items = sources.birthdays(
            self.Recorder(rows), {'user_id': 7, 'birthday_is_global': False,
                                  'birthday_department_id': 3}, 5)
        self.assertEqual(2, total, 'счётчик обязан равняться числу строк: '
                                   'has_more до остальных иначе не доберётся')
        self.assertEqual(['Мария Иванова (вы)', 'Пётр Ким'],
                         [i['title'] for i in items])
        self.assertEqual(['Чаты', ''], [i['body'] for i in items])
        self.assertEqual([None, None], [i['at'] for i in items],
                         'момента у праздника нет: fmtWhen округляет и после '
                         'полудня написал бы «вчера» про сегодняшний день')
        self.assertEqual([None, None], [i['view'] for i in items])
        self.assertEqual(['default', 'default'], [i['tone'] for i in items],
                         "'warning' поднял бы праздник над просрочками")
        self.assertEqual([7, 9], [i['id'] for i in items],
                         'id — это id сотрудника: из него собираются ключ '
                         'строки и тег плашки на рабочем столе')

    def test_own_birthday_goes_first(self):
        self.assertIn('ORDER BY is_self DESC', self._block())

    # ── время ────────────────────────────────────────────────────────────────
    def test_day_comes_from_the_process_clock(self):
        """База живёт в UTC: с 19:00 до полуночи её CURRENT_DATE — это вчера."""
        block = self._block()
        self.assertNotIn('CURRENT_DATE', block)
        self.assertIn('_almaty_now().date()', block)
        cursor = self.Recorder()
        before = sources._almaty_now().date()
        sources.birthdays(cursor, {'user_id': 1, 'birthday_is_global': True}, 5)
        after = sources._almaty_now().date()
        # Диапазон, а не точное равенство: прогон, попавший ровно на полночь,
        # иначе падал бы — дату здесь берут дважды, и это разные сутки.
        self.assertIn((cursor.params['month'], cursor.params['day']),
                      [(d.month, d.day) for d in (before, after)])

    def test_mark_seen_writes_a_date_from_the_process_clock(self):
        cursor = self.Recorder()
        before = sources._almaty_now().date()
        self.assertTrue(sources.mark_seen(cursor, 42, 'birthdays'))
        after = sources._almaty_now().date()
        self.assertNotIn('CURRENT_DATE', cursor.sql)
        self.assertEqual(42, cursor.params[0])
        self.assertIn(cursor.params[1], (before, after))

    def test_midnight_is_scheduled_without_touching_the_database(self):
        """Полночь считается в Python: запрос за ней унёс бы весь таймер.

        next_change_at — один сплавленный UNION ALL, и его SAVEPOINT защищает
        сводку, а не таймер: упавший в нём кусок забрал бы с собой и окна
        тестов, и дедлайны задач.
        """
        cursor = FakeCursor()
        hidden = tuple(s for s in sources.SOURCES if s != 'birthdays')
        moment = sources.next_change_at(cursor, {'user_id': 1, 'hidden_sources': hidden})
        self.assertEqual([], cursor.commands, 'за полночью в базу не ходят')
        now = sources._almaty_now()
        self.assertEqual(now.date() + timedelta(days=1), moment.date())
        self.assertEqual((0, 0, 0), (moment.hour, moment.minute, moment.second))
        self.assertGreater(
            moment, now,
            'момент в прошлом клиент отработал бы как опрос раз в секунду')

    def test_the_earlier_of_deadline_and_midnight_wins(self):
        soon = sources._almaty_now() + timedelta(minutes=5)

        class Cursor(FakeCursor):
            def fetchone(self):
                return (soon,)

        self.assertEqual(soon, sources.next_change_at(Cursor(), {'user_id': 1}))

    def test_hidden_birthdays_do_not_wake_the_tab_at_midnight(self):
        self.assertIsNone(sources.next_change_at(
            FakeCursor(), {'user_id': 1, 'hidden_sources': sources.SOURCES}))

    # ── боевой SQL на синтетических данных ───────────────────────────────────
    FIXTURE = """
        WITH users(id, name, birth_date, status, department_id, direction_id) AS (
            VALUES (1, 'Свой', DATE %(bd)s, 'working', 10, 1),
                   (2, 'Коллега по отделу', DATE %(bd)s, 'working', 10, 1),
                   (3, 'Чужой отдел', DATE %(bd)s, 'working', 20, 1),
                   (4, 'Уволенный свой', DATE %(bd)s, 'fired', 10, 1),
                   (5, 'Без отдела', DATE %(bd)s, 'working', NULL, 1),
                   (6, 'Свой, но не сегодня', DATE %(other)s, 'working', 10, 1)
        ), directions(id, name) AS (VALUES (1, 'Чаты')),
           birthday_reads(user_id, last_seen_on) AS (
            SELECT NULL::int, NULL::date WHERE FALSE
        )
    """

    def _fixture_params(self, base):
        today = sources._almaty_now().date()
        born = today.replace(year=1990)
        return dict(base, bd=born.isoformat(),
                    other=(born + timedelta(days=1)).isoformat())

    def test_other_department_is_filtered_out_on_real_postgres(self):
        """Тот же запрос, но users/directions/birthday_reads подменены CTE.

        Текстовых проверок мало: условие можно написать синтаксически верно и
        всё равно пропустить чужой отдел (скажем, OR вместо AND). Здесь
        выполняется РОВНО тот SQL, который уходит в прод.
        """
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)

        scoped = self.Recorder()
        sources.birthdays(scoped, {'user_id': 1, 'birthday_is_global': False,
                                   'birthday_department_id': 10}, 5)
        wide = self.Recorder()
        sources.birthdays(wide, {'user_id': 1, 'birthday_is_global': True}, 5)

        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(self.FIXTURE + scoped.sql, self._fixture_params(scoped.params))
            rows = cursor.fetchall()
            self.assertEqual(['Свой', 'Коллега по отделу'], [r[1] for r in rows],
                             'чужой отдел, уволенный и не сегодняшний обязаны отпасть')
            self.assertEqual(2, rows[0][4], 'счётчик считает то же, что и выдача')

            cursor.execute(self.FIXTURE + wide.sql, self._fixture_params(wide.params))
            self.assertEqual(
                ['Свой', 'Без отдела', 'Коллега по отделу', 'Чужой отдел'],
                [r[1] for r in cursor.fetchall()],
                'глобальному админу периметр не сужаем')
        finally:
            prod_db.rollback()
            cursor.close()

    def test_dismissal_expires_at_midnight_on_real_postgres(self):
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)

        recorder = self.Recorder()
        sources.birthdays(recorder, {'user_id': 1, 'birthday_is_global': True}, 5)
        today = sources._almaty_now().date()
        cursor = prod_db.connection().cursor()
        try:
            for seen_on, expected in ((today, []), (today - timedelta(days=1), ['Свой'])):
                cursor.execute("""
                    WITH users(id, name, birth_date, status, department_id, direction_id) AS (
                        VALUES (1, 'Свой', DATE %(bd)s, 'working', 10, 1)
                    ), directions(id, name) AS (VALUES (1, 'Чаты')),
                       birthday_reads(user_id, last_seen_on) AS (
                        VALUES (1, DATE %(seen)s)
                    )
                """ + recorder.sql,
                    dict(self._fixture_params(recorder.params), seen=seen_on.isoformat()))
                self.assertEqual(expected, [r[1] for r in cursor.fetchall()],
                                 'вчерашняя отметка не должна прятать сегодняшний праздник')
        finally:
            prod_db.rollback()
            cursor.close()

if __name__ == '__main__':
    unittest.main()
