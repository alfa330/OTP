# -*- coding: utf-8 -*-
"""Объявление Oktell за интервалом «Тренинг» в «Графиках работы» (задача #382).

Обязательное объявление, отправленное в Oktell, на время чтения ставит оператору
перерыв «Тренинг», и супервайзер подтверждает этот интервал в окне дня. Задача —
показать ему, что человек читал, и записать это в «Тренинги».

Тесты чистые — ни базы, ни сети. Запрос проверяется записывающим курсором и
чтением SQL, проводка монолита и интерфейса — чтением исходников, как у
колокола и вики. Сами правила сопоставления окна с интервалом живут в
интерфейсе и проверяются отдельно: tests/training_news.test.mjs.
"""

import os
import re
import unittest

from news import queries as news_queries
from news import schema as news_schema
from trainings import schema as trainings_schema
from work_schedules import training_news

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


class _Cursor:
    """Курсор, который помнит запросы и отдаёт заранее заданные строки."""

    def __init__(self, rows=(), single=None):
        self.rows = list(rows)
        self.single = single
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.single


class OktellWindowsQueryTests(unittest.TestCase):
    """news.queries.oktell_windows: окна объявлений человека за сутки."""

    def test_rows_become_windows_in_seconds_from_midnight(self):
        cursor = _Cursor(rows=[(39, 'Новый тариф', 36132, 36220)])
        windows = news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        self.assertEqual(windows, [
            {'news_id': 39, 'title': 'Новый тариф', 'start_sec': 36132, 'end_sec': 36220},
        ])

    def test_an_unconfirmed_window_has_no_end(self):
        """Неподтверждённое окно — без конца, а не «открыто до сих пор»: иначе
        снятое без подтверждения объявление подписало бы все будущие тренинги."""
        cursor = _Cursor(rows=[(40, 'Не дочитал', 67800, None)])
        windows = news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        self.assertIsNone(windows[0]['end_sec'])
        self.assertEqual(windows[0]['start_sec'], 67800)

    def test_a_window_from_yesterday_keeps_its_negative_start(self):
        """Показали вчера вечером, подтвердили сегодня утром — такое окно
        объясняет утренний интервал и приходит с отрицательным началом."""
        cursor = _Cursor(rows=[(41, 'Ночное', -18050, 32460)])
        windows = news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        self.assertEqual((windows[0]['start_sec'], windows[0]['end_sec']), (-18050, 32460))

    def test_the_person_and_the_day_go_as_parameters(self):
        cursor = _Cursor()
        news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        self.assertEqual(len(cursor.calls), 1)
        _, params = cursor.calls[0]
        self.assertEqual(params, {'user_id': 7, 'day': '2026-10-06'})

    def test_only_what_was_sent_to_oktell_is_asked(self):
        """Объявление портала статус в Oktell не трогает — подписывать им
        интервал «Тренинг» значило бы выдумать связь."""
        cursor = _Cursor()
        news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        sql, _ = cursor.calls[0]
        self.assertIn("p.channel = 'oktell'", sql)
        self.assertIn('r.user_id = %(user_id)s', sql)

    def test_the_window_must_touch_the_day(self):
        """Показали до конца суток и окно ещё жило в их начале — обе границы.
        Одной нет: без второй день получал бы всю историю объявлений человека."""
        cursor = _Cursor()
        news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        sql, _ = cursor.calls[0]
        self.assertIn('r.shown_at < %(day)s::date + 1', sql)
        self.assertIn('COALESCE(r.confirmed_at, r.shown_at) >= %(day)s::date', sql)

    def test_seconds_are_counted_from_the_midnight_of_the_asked_day(self):
        cursor = _Cursor()
        news_queries.oktell_windows(cursor, user_id=7, day='2026-10-06')
        sql, _ = cursor.calls[0]
        self.assertIn('EXTRACT(EPOCH FROM (r.shown_at - %(day)s::date))', sql)
        self.assertIn('EXTRACT(EPOCH FROM (r.confirmed_at - %(day)s::date))', sql)

    def test_the_lookup_has_its_index(self):
        """Ключ news_reads начинается с news_id, а прежний индекс по человеку
        частичный: без своего индекса запрос перебирал бы весь журнал портала."""
        statements = [s for s in news_schema._STATEMENTS if 'idx_news_reads_user_shown' in s]
        self.assertEqual(len(statements), 1)
        self.assertIn('ON news_reads(user_id, shown_at)', statements[0])
        self.assertIn('IF NOT EXISTS', statements[0])

    def test_the_index_is_created_after_its_table(self):
        """Схема новостей применяется одной точкой отката: индекс раньше таблицы
        сорвал бы весь разворот раздела."""
        table_at = next(index for index, s in enumerate(news_schema._STATEMENTS)
                        if 'CREATE TABLE IF NOT EXISTS news_reads' in s)
        index_at = next(index for index, s in enumerate(news_schema._STATEMENTS)
                        if 'idx_news_reads_user_shown' in s)
        self.assertLess(table_at, index_at)


class WindowsForDayTests(unittest.TestCase):
    """work_schedules.training_news: мост графиков к разделу «Новости»."""

    def setUp(self):
        self._saved = training_news._channel['ready']
        training_news._channel['ready'] = False
        self._query = news_queries.oktell_windows
        self._probe = news_schema.channel_ready

    def tearDown(self):
        training_news._channel['ready'] = self._saved
        news_queries.oktell_windows = self._query
        news_schema.channel_ready = self._probe

    def test_without_the_channel_column_there_are_no_windows(self):
        """Нет колонки канала — объявлений в Oktell не было вовсе. Запрос с
        p.channel на такой базе упал бы и унёс с собой окно дня."""
        news_schema.channel_ready = lambda cursor: False
        asked = []
        news_queries.oktell_windows = lambda cursor, **kwargs: asked.append(kwargs) or []
        self.assertEqual(training_news.windows_for_day(object(), operator_id=7, day='2026-10-06'), [])
        self.assertEqual(asked, [])

    def test_the_windows_come_from_the_news_section(self):
        news_schema.channel_ready = lambda cursor: True
        asked = []

        def fake(cursor, **kwargs):
            asked.append(kwargs)
            return [{'news_id': 39, 'title': 'Новый тариф', 'start_sec': 1, 'end_sec': 2}]

        news_queries.oktell_windows = fake
        result = training_news.windows_for_day(object(), operator_id='7', day='2026-10-06')
        self.assertEqual(result[0]['news_id'], 39)
        self.assertEqual(asked, [{'user_id': 7, 'day': '2026-10-06'}])

    def test_the_column_is_asked_once_per_process(self):
        probes = []
        news_schema.channel_ready = lambda cursor: probes.append(1) or True
        news_queries.oktell_windows = lambda cursor, **kwargs: []
        for _ in range(3):
            training_news.windows_for_day(object(), operator_id=7, day='2026-10-06')
        self.assertEqual(len(probes), 1)

    def test_a_missing_column_is_asked_again(self):
        """«Нет» не запоминается: колонка приезжает деплоем, и процесс обязан
        её заметить без перезапуска."""
        probes = []
        news_schema.channel_ready = lambda cursor: probes.append(1) and False
        news_queries.oktell_windows = lambda cursor, **kwargs: []
        for _ in range(2):
            training_news.windows_for_day(object(), operator_id=7, day='2026-10-06')
        self.assertEqual(len(probes), 2)

    def test_the_bridge_does_not_import_the_news_section_at_load(self):
        """«Графики работы» обязаны открываться и там, где раздел новостей не
        развернулся: импорт — только внутри функций."""
        source = _read('work_schedules', 'training_news.py')
        top_level = [line for line in source.splitlines()
                     if line.startswith('from ') or line.startswith('import ')]
        self.assertEqual(top_level, [])


class RouteTests(unittest.TestCase):
    """Ручка /api/work_schedules/training_news в монолите."""

    @classmethod
    def setUpClass(cls):
        source = _read('bot_schedule2.py')
        cls.body = source.split('def get_work_schedule_training_news', 1)[1].split('\n@app.route', 1)[0]
        cls.head = source.split('def get_work_schedule_training_news', 1)[0].rsplit('@app.route', 1)[1]

    def test_it_is_a_read_only_get(self):
        self.assertIn("'/api/work_schedules/training_news', methods=['GET']", self.head)
        self.assertIn('@require_api_key', self.head)

    def test_the_perimeter_is_the_one_of_reading_schedules(self):
        """Тренер графики смотрит, но не правит — название объявления ему видно
        так же, как сам интервал. Мутационный вход здесь был бы уже чтения."""
        self.assertIn('_resolve_work_schedule_viewer()', self.body)
        self.assertNotIn('_resolve_management_requester', self.body)

    def test_a_foreign_operator_is_refused(self):
        self.assertIn(
            "if not _filter_operators_for_requester_scope(viewer, viewer_id, [{'id': operator_id}]):",
            self.body)
        self.assertIn('"Forbidden for this operator"}), 403', self.body)

    def test_the_scope_is_checked_before_the_database_is_asked(self):
        scope_at = self.body.index('_filter_operators_for_requester_scope')
        query_at = self.body.index('windows_for_day')
        self.assertLess(scope_at, query_at)

    def test_a_broken_date_is_refused(self):
        self.assertIn("datetime.strptime(date_raw, '%Y-%m-%d')", self.body)
        self.assertIn('"date must be in YYYY-MM-DD format"}), 400', self.body)

    def test_the_news_bridge_is_imported_lazily(self):
        self.assertIn('from work_schedules import training_news as schedule_training_news', self.body)


class SavedTrainingTests(unittest.TestCase):
    """Что уходит в «Тренинги» при подтверждении интервала с объявлением."""

    @classmethod
    def setUpClass(cls):
        cls.module = _read('src', 'components', 'schedule', 'trainingNews.js')
        cls.app = _read('src', 'App.jsx')
        cls.reason = re.search(r"export const NEWS_TRAINING_REASON = '([^']+)';", cls.module).group(1)

    def test_the_reason_is_accepted_by_the_server(self):
        """Тему интерфейс подставляет сам, а сервер пускает в новый тренинг
        только действующие темы: архивная или незнакомая дала бы 400 на каждом
        подтверждении объявления."""
        self.assertIn(self.reason, trainings_schema.active_default_reasons())

    def test_the_reason_is_in_the_planner_select(self):
        """Иначе подставленная тема не нашла бы свой пункт, и поле «Причина»
        выглядело бы пустым при заполненном значении."""
        options = self.app.split('const plannerTrainingReasonOptions = useMemo(() => ([', 1)[1].split(']), []);', 1)[0]
        self.assertIn('"%s"' % self.reason, options)

    def test_the_title_goes_to_the_comment(self):
        self.assertIn('return `Новость в Oktell: ${titles.map((title) => `«${title}»`).join(\'; \')}`;', self.module)

    def test_bulk_confirmation_saves_what_the_window_showed(self):
        """Записи «Подтвердить все» считает один планировщик — на окно и на
        сохранение. Второй расчёт в обработчике разошёлся бы с показанным."""
        submit = self.app.split('const submitPlannerTrainingModal = async () => {', 1)[1].split(
            'const deletePlannerTraining', 1)[0]
        self.assertIn('plannerTrainingModalPlan.map(save => (save.news.length > 0', submit)
        self.assertIn('reason: NEWS_TRAINING_REASON, comment: buildNewsComment(save.news)', submit)
        self.assertNotIn('planTrainingSaves(', submit)

    def test_every_record_is_posted_with_its_own_reason(self):
        """Причина формы — только для интервалов без объявления. Общая на все
        записи затёрла бы подставленную тему у интервалов с объявлением."""
        submit = self.app.split('const submitPlannerTrainingModal = async () => {', 1)[1].split(
            'const deletePlannerTraining', 1)[0]
        post = submit.split("fetch(`${API_BASE_URL}/api/trainings`, {", 1)[1].split('});', 1)[0]
        self.assertIn('reason: interval.reason,', post)
        self.assertIn('comment: interval.comment || null,', post)

    def test_the_form_reason_is_required_only_without_news(self):
        submit = self.app.split('const submitPlannerTrainingModal = async () => {', 1)[1].split(
            'const deletePlannerTraining', 1)[0]
        self.assertIn('if (!reason && plannerTrainingModalPlan.some(save => save.news.length === 0)) {', submit)

    def test_loading_the_news_never_writes_a_training(self):
        """Тема подставляется, но запись создаёт только нажатие «Подтвердить»:
        загрузка объявлений дня ничего не сохраняет сама."""
        loader = self.app.split(
            'const fetchPlannerTrainingNews = useCallback(async (operatorId, dayKey) => {', 1)[1].split(
            '}, [API_BASE_URL, user?.id, withAccessTokenHeader]);', 1)[0]
        self.assertNotIn("method: 'POST'", loader)
        self.assertNotIn('/api/trainings', loader)

    def test_the_day_asks_for_news_only_when_it_has_training_intervals(self):
        self.assertIn('if (!modalTrainingNewsKey || !modalHasTrainingSegments) return;', self.app)
        self.assertIn('/api/work_schedules/training_news?', self.app)

    def test_a_failed_request_does_not_block_the_day(self):
        """Не ответил сервер — день остаётся без названий, как до задачи, а
        подтверждать интервалы по-прежнему можно."""
        effect = self.app.split('fetchPlannerTrainingNews(operatorId, dayKey).catch(error => {', 1)[1].split('});', 1)[0]
        self.assertIn("console.error('Error loading planner training news:', error);", effect)
        self.assertNotIn('emitAppToast', effect)

    def test_the_news_is_shown_in_the_day_list_and_in_the_confirmation_window(self):
        self.assertIn('<TrainingNewsLine matches={modalTrainingNewsBySegmentId[seg.id]} />', self.app)
        self.assertIn('<TrainingNewsLine matches={newsForSegment(seg, plannerTrainingModalNewsWindows)} />', self.app)
        self.assertIn('<TrainingNewsLine matches={plannerTrainingModalState.news} />', self.app)

    def test_the_phone_row_names_the_news_of_the_day(self):
        self.assertIn("note={item.key === 'training' ? (modalTrainingNewsNote || null) : null}", self.app)

    def test_the_news_line_survives_the_phone_shell(self):
        """Телефонная оболочка переписывает разделам общие раскладки: ряду на
        flex с gap-* разрешает перенос с отступом (время отрывалось от
        названия), сетку с grid-cols-[…] сворачивает в одну колонку (значок
        вставал отдельной строкой). Оба варианта уже были и оба ломались."""
        line = re.sub(r'/\*[\s\S]*?\*/', '', _read('src', 'components', 'schedule', 'TrainingNewsLine.jsx'))
        self.assertNotIn('grid-cols-[', line)
        for class_name in re.findall(r'className="([^"]*)"', line):
            if 'flex' in class_name.split():
                self.assertNotIn('gap-', class_name)
                self.assertNotIn('flex-1', class_name)


if __name__ == '__main__':
    unittest.main()
