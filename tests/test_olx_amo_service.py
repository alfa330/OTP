# -*- coding: utf-8 -*-
"""Поведение робота «Лиды OLX» на одном обращении (задача #223).

Здесь проверяется то, ради чего робот и написан: что происходит с КОНКРЕТНЫМ
сообщением кандидата. База и оба внешних сервиса подменены, поэтому тест
быстрый и не ходит ни в боевой Postgres, ни в amoCRM, ни в OLX.

Отдельно сторожатся два свойства, которые не видны в поведении, но стоили
проекту дорого в других разделах:

  * СЕТЬ ВНЕ ТРАНЗАКЦИИ. У запроса в amoCRM таймаут 60 секунд, кабинетов
    девять, в пуле сорок соединений. Держи обработчик курсор через поход в CRM
    — один подвисший amoCRM занимал бы четверть пула на минуту, дважды в
    минуту, и вставало бы всё приложение.

  * КЛИЕНТ amoCRM НА ПОТОК. `requests.Session` потокобезопасной не объявлена,
    а кабинеты опрашиваются девятью потоками сразу.
"""

import sys
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from olx_amo import cabinets, service  # noqa: E402
from olx_amo.amo_writer import AmoWriteError  # noqa: E402
from olx_amo.olx_client import OlxError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class FakeDb(object):
    """База, которой хватает роботу: журнал, состояние чатов и счётчик курсоров.

    Считает, сколько курсоров открыто ОДНОВРЕМЕННО и был ли курсор открыт в
    момент сетевого вызова — именно это и проверяется.
    """

    def __init__(self):
        self.journal = []
        self.threads = {}
        self.open_cursors = 0
        self.max_open = 0

    @contextmanager
    def _get_cursor(self):
        self.open_cursors += 1
        self.max_open = max(self.max_open, self.open_cursors)
        try:
            yield self
        finally:
            self.open_cursors -= 1


class FakeQueries(object):
    """Подмена SQL-слоя. Хранит то же, что хранил бы Postgres."""

    def __init__(self, db):
        self.db = db

    # -- журнал --------------------------------------------------------
    def write_journal(self, cursor, cabinet_code, result, **fields):
        phone = fields.get('phone_normalized')
        at = fields.get('message_at') or datetime(2026, 8, 31, 12, 0)
        if result in ('lead_created', 'manual_review') and phone:
            key = (cabinet_code, phone, at.date())
            if any((r['cabinet_code'], r.get('phone_normalized'),
                    (r.get('message_at') or at).date()) == key
                   and r['result'] in ('lead_created', 'manual_review')
                   for r in self.db.journal):
                return None            # сработал уникальный индекс дедупликации
        row = dict(fields, cabinet_code=cabinet_code, result=result,
                   id=len(self.db.journal) + 1)
        self.db.journal.append(row)
        return row

    def find_recent_lead(self, cursor, cabinet_code, phone, day=None):
        for row in self.db.journal:
            if (row['cabinet_code'] == cabinet_code
                    and row.get('phone_normalized') == phone
                    and row['result'] in ('lead_created', 'manual_review')):
                return row
        return None

    # -- состояние чата ------------------------------------------------
    def get_thread(self, cursor, cabinet_code, thread_id):
        return self.db.threads.get((cabinet_code, str(thread_id)))

    def upsert_thread(self, cursor, cabinet_code, thread_id, **fields):
        key = (cabinet_code, str(thread_id))
        state = self.db.threads.setdefault(key, {})
        state.update({k: v for k, v in fields.items() if v is not None})
        return state

    def mark_canned_reply_sent(self, cursor, cabinet_code, thread_id):
        key = (cabinet_code, str(thread_id))
        self.db.threads.setdefault(key, {})['canned_reply_sent_at'] = datetime.utcnow()

    # -- прочее --------------------------------------------------------
    @staticmethod
    def now_almaty():
        return datetime(2026, 8, 31, 12, 0, 30)

    @staticmethod
    def today_almaty():
        return datetime(2026, 8, 31).date()


class FakeOlx(object):
    def __init__(self, db=None, fail=False):
        self.sent = []
        self.read_marks = []
        self.fail = fail
        self.db = db
        self.cursors_when_called = []

    def send_message(self, thread_id, text):
        if self.db is not None:
            self.cursors_when_called.append(self.db.open_cursors)
        if self.fail:
            raise OlxError('OLX недоступен')
        self.sent.append((str(thread_id), text))

    def mark_read(self, thread_id):
        self.read_marks.append(str(thread_id))


class FakeAmo(object):
    def __init__(self, db=None, fail=False):
        self.created = []
        self.fail = fail
        self.db = db
        self.cursors_when_called = []

    def create_lead(self, phone, cabinet_code, needs_manual_review=False, note=None):
        if self.db is not None:
            self.cursors_when_called.append(self.db.open_cursors)
        if self.fail:
            raise AmoWriteError('amoCRM отклонила запись (400)')
        self.created.append((phone, cabinet_code, needs_manual_review))
        return 5000 + len(self.created), 9000 + len(self.created)


def message(text='', phone=None, mid='1', minutes_ago=0):
    stamp = datetime(2026, 8, 31, 12, 0) - timedelta(minutes=minutes_ago)
    payload = {'id': mid, 'type': 'received', 'text': text,
               'created_at': stamp.isoformat() + '+05:00'}
    if phone:
        payload['phone'] = phone
    return payload


class HandleMessageTests(unittest.TestCase):
    """Три ветки ТЗ на одном сообщении."""

    def setUp(self):
        self.db = FakeDb()
        self.queries = FakeQueries(self.db)
        self._real_queries = service.queries
        service.queries = self.queries
        self.addCleanup(lambda: setattr(service, 'queries', self._real_queries))

        self.cab = cabinets.BY_CODE['itaxi']
        self.olx = FakeOlx(self.db)
        self.amo = FakeAmo(self.db)
        self.writers = service._Writers(self.olx, self.amo)
        self.counters = service.CabinetResult(self.cab.code)

    def handle(self, msg):
        service._handle_message(self.db, self.writers, self.cab,
                                {'id': '77'}, msg, self.counters)

    # -- ветка 1: номер распознан --------------------------------------
    def test_number_creates_a_lead_and_a_journal_row(self):
        self.handle(message('Здравствуйте, мой номер 8 775 702 51 44'))

        self.assertEqual([('77757025144', 'itaxi', False)], self.amo.created)
        self.assertEqual(1, self.counters.leads_created)
        row = self.db.journal[-1]
        self.assertEqual('lead_created', row['result'])
        self.assertEqual('77757025144', row['phone_normalized'])
        self.assertEqual('forma_olx_itaxi', row['tag'])
        self.assertIsNotNone(row['amo_lead_id'])
        # SLA считается от времени отклика, а не от времени записи строки.
        self.assertEqual(30000, row['latency_ms'])

    def test_journal_keeps_the_number_as_the_candidate_wrote_it(self):
        """Раздел 7 ТЗ требует обе формы номера — до и после нормализации.

        Две одинаковые колонки спорный случай «почему распознался вот так»
        разобрать не помогают, поэтому исходное написание сохраняется буквально,
        вместе с пробелами и скобками.
        """
        self.handle(message('звоните: 8 (775) 702-51-44'))

        row = self.db.journal[-1]
        self.assertEqual('77757025144', row['phone_normalized'])
        self.assertEqual('8 (775) 702-51-44', row['phone_raw'])

    def test_candidate_phone_never_reaches_the_log(self):
        """Логи Render читают люди; телефон кандидата — персональные данные."""
        source = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')
        for line in source.splitlines():
            if 'log.' in line and ('%s' in line or '%r' in line):
                self.assertNotIn(' phone', line.replace('phones.', ''),
                                 'номер кандидата в строке лога: %s' % line.strip())

    # -- ветка 2: номер писали, но кривой -------------------------------
    def test_broken_number_still_creates_a_lead_marked_for_review(self):
        """ТЗ: терять обращение нельзя даже с нечитаемым номером."""
        self.handle(message('мой номер +996 555 123456'))

        self.assertEqual(1, len(self.amo.created))
        self.assertTrue(self.amo.created[0][2], 'сделка должна быть помечена на проверку')
        self.assertEqual('manual_review', self.db.journal[-1]['result'])
        self.assertEqual([], self.olx.sent, 'кривой номер — не повод слать автоответ')

    # -- ветка 3: номера нет вовсе --------------------------------------
    def test_message_without_a_number_gets_the_canned_reply_of_its_cabinet(self):
        self.handle(message('А какие условия работы?'))

        self.assertEqual([], self.amo.created, 'сделки без номера быть не должно')
        self.assertEqual(1, len(self.olx.sent))
        thread_id, text = self.olx.sent[0]
        self.assertEqual('77', thread_id)
        self.assertIn('87470939685', text, 'в ответе должен быть телефон ЭТОГО кабинета')
        self.assertEqual('canned_reply', self.db.journal[-1]['result'])
        self.assertEqual(1, self.counters.replies_sent)

    def test_canned_reply_is_sent_only_once_per_appeal(self):
        """ТЗ запрещает повторную отправку одному кандидату в рамках обращения."""
        self.handle(message('Здравствуйте', mid='1'))
        self.handle(message('Ответьте пожалуйста', mid='2'))

        self.assertEqual(1, len(self.olx.sent))
        self.assertEqual(1, self.counters.replies_sent)

    # -- дедупликация ---------------------------------------------------
    def test_same_number_same_cabinet_same_day_does_not_create_a_second_lead(self):
        self.handle(message('мой номер 87757025144', mid='1'))
        self.handle(message('ещё раз: 8 775 702 51 44', mid='2'))

        self.assertEqual(1, len(self.amo.created))
        self.assertEqual('duplicate', self.db.journal[-1]['result'])
        self.assertEqual(1, self.counters.leads_created)

    def test_same_number_in_another_cabinet_is_a_separate_lead(self):
        """Дедупликация — в границах кабинета: источник лида разный."""
        self.handle(message('мой номер 87757025144', mid='1'))
        self.cab = cabinets.BY_CODE['jana']
        self.handle(message('мой номер 87757025144', mid='2'))

        self.assertEqual(2, len(self.amo.created))
        self.assertEqual({'itaxi', 'jana'}, {c[1] for c in self.amo.created})

    # -- отказы ---------------------------------------------------------
    def test_amo_failure_is_written_as_an_error_not_swallowed(self):
        """Строка `error` в журнале и есть очередь на повтор."""
        self.writers = service._Writers(self.olx, FakeAmo(self.db, fail=True))
        self.handle(message('мой номер 87757025144'))

        row = self.db.journal[-1]
        self.assertEqual('error', row['result'])
        self.assertEqual('77757025144', row['phone_normalized'],
                         'номер обязан сохраниться — иначе повторять нечем')
        self.assertIn('amoCRM', row['error_text'])
        self.assertEqual(1, self.counters.errors)

    def test_olx_failure_on_the_reply_is_written_as_an_error(self):
        self.writers = service._Writers(FakeOlx(self.db, fail=True), self.amo)
        self.handle(message('А какие условия?'))

        self.assertEqual('error', self.db.journal[-1]['result'])
        self.assertEqual(1, self.counters.errors)

    # -- сеть вне транзакции --------------------------------------------
    def test_no_database_cursor_is_held_while_talking_to_amocrm(self):
        """Иначе подвисший amoCRM занимает четверть пула соединений на минуту."""
        self.handle(message('мой номер 87757025144'))
        self.assertEqual([0], self.amo.cursors_when_called)

    def test_no_database_cursor_is_held_while_talking_to_olx(self):
        self.handle(message('А какие условия?'))
        self.assertEqual([0], self.olx.cursors_when_called)

    def test_transactions_stay_short(self):
        """Ни одна транзакция не охватывает больше одного шага."""
        self.handle(message('мой номер 87757025144'))
        self.assertEqual(1, self.db.max_open)


class JournalWriteTests(unittest.TestCase):
    """Запись в журнал на курсоре, который ведёт себя как настоящий psycopg2.

    Прод-инцидент 01.09.2026. Курсор psycopg2 держит результат ПОСЛЕДНЕГО
    запроса, а `RELEASE SAVEPOINT` — тоже запрос: он затирал выдачу
    `INSERT ... RETURNING`, и следующий `fetchone()` падал с «no results to
    fetch». Снаружи это выглядело как «ошибка записи в журнал», но откатывало
    транзакцию вместе с отметкой «автоответ уже отправлен» — и кандидат получал
    заготовленное сообщение заново каждые полминуты. 172 копии одному человеку.

    Заглушка ниже повторяет ровно это поведение: запрос без выдачи обнуляет
    результат, а fetch по пустому результату бросает ту же ошибку.
    """

    class Cursor(object):
        """Курсор, обнуляющий результат на каждом запросе без RETURNING."""

        def __init__(self):
            self.statements = []
            self._rows = None
            self.description = None

        def execute(self, sql, params=None):
            self.statements.append(' '.join(str(sql).split())[:60])
            if 'RETURNING' in str(sql).upper():
                self.description = [('id',), ('cabinet_code',), ('result',)]
                self._rows = [(1, (params or {}).get('cabinet_code'),
                               (params or {}).get('result'))]
            else:
                # Как в psycopg2: запрос без выдачи оставляет курсор пустым.
                self.description = None
                self._rows = None

        def fetchone(self):
            if self._rows is None:
                raise RuntimeError('no results to fetch')
            return self._rows[0] if self._rows else None

        def fetchall(self):
            if self._rows is None:
                raise RuntimeError('no results to fetch')
            return list(self._rows)

    def test_written_row_survives_the_savepoint_release(self):
        from olx_amo import queries as real_queries

        cursor = self.Cursor()
        row = real_queries.write_journal(
            cursor, 'itaxi', 'canned_reply', thread_id='77', message_id='5')

        self.assertIsNotNone(row, 'строка журнала обязана вернуться')
        self.assertEqual('canned_reply', row['result'])
        # И порядок должен быть именно такой: вставка → чтение → release.
        self.assertIn('RELEASE SAVEPOINT olx_journal_write', cursor.statements[-1])

    def test_canned_reply_is_marked_before_it_is_sent(self):
        """Отметка после отправки откатывалась вместе с транзакцией.

        Цена обратного порядка — не отправленный автоответ при сбое отправки;
        это осознанный выбор: ТЗ запрещает повторную отправку, а молчание видно
        в журнале и поправимо человеком.
        """
        source = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')
        branch = source[source.index('# ── ветка 3'):]
        branch = branch[:branch.index('needs_manual = phone is None')]
        mark = branch.index('mark_canned_reply_sent')
        send = branch.index('send_message')
        self.assertLess(mark, send, 'отметка обязана стоять ДО отправки')

    def test_mark_creates_the_thread_row_if_it_is_not_there_yet(self):
        """Закладка по чату пишется в конце разбора, а автоответ уходит раньше.

        UPDATE по несуществующей строке молча трогает ноль строк — отметка не
        сохранялась бы, и на следующем опросе ушла бы вторая копия.
        """
        source = (ROOT / 'olx_amo' / 'queries.py').read_text(encoding='utf-8')
        block = source[source.index('def mark_canned_reply_sent('):]
        block = block[:block.index('\ndef ')]
        self.assertIn('INSERT INTO olx_threads', block)
        self.assertIn('ON CONFLICT (cabinet_code, thread_id) DO UPDATE', block)


class MessageTimeTests(unittest.TestCase):
    """Время сообщения OLX. Ошибка здесь тихо съедает обращения.

    Прод-инцидент 01.09.2026: OLX отдаёт время В UTC и БЕЗ пометки о зоне,
    строкой вида `'2026-09-01 16:57:32'` — даже не по ISO, с пробелом вместо
    «T». Код считал такое время местным, из-за чего каждое сообщение выглядело
    на пять часов старше, и горизонт в 15 минут отсекал ПЕРВОЕ сообщение любого
    нового чата. Закладка при этом вставала, и обращение не возвращалось никогда.
    """

    @staticmethod
    def at(raw):
        from olx_amo.olx_client import message_time
        return message_time({'created_at': raw})

    def test_naive_timestamp_is_utc_not_local(self):
        """Форма, в которой время реально приходит с боевого OLX."""
        self.assertEqual(datetime(2026, 9, 1, 21, 57, 32),
                         self.at('2026-09-01 16:57:32'))

    def test_iso_with_explicit_zone_still_works(self):
        self.assertEqual(datetime(2026, 9, 1, 21, 57, 32),
                         self.at('2026-09-01T16:57:32+00:00'))
        self.assertEqual(datetime(2026, 9, 1, 21, 57, 32),
                         self.at('2026-09-01T16:57:32Z'))
        # Уже местное со своим смещением — не должно сдвинуться второй раз.
        self.assertEqual(datetime(2026, 9, 1, 21, 57, 32),
                         self.at('2026-09-01T21:57:32+05:00'))

    def test_garbage_and_emptiness_do_not_crash(self):
        for raw in ('', None, 'вчера', '2026-13-45'):
            self.assertIsNone(self.at(raw), repr(raw))

    def test_fresh_message_passes_a_short_horizon(self):
        """Главное следствие: только что пришедшее обращение не считается старым.

        Ровно это и ломалось — при горизонте в 15 минут отклик, поступивший
        секунду назад, выглядел пятичасовой давностью и отбрасывался.

        Горизонт задаём ЯВНО, а не берём из окружения: на проде он 15 минут, а
        локально по умолчанию шесть часов — на шести часах этот тест прошёл бы и
        со старым, сломанным разбором времени, то есть ничего бы не сторожил.
        """
        original = service.HORIZON
        service.HORIZON = timedelta(minutes=15)
        self.addCleanup(lambda: setattr(service, 'HORIZON', original))

        now = datetime(2026, 9, 1, 21, 58, 2)
        message = {'id': '1', 'type': 'received', 'text': '',
                   'created_at': '2026-09-01 16:57:32'}   # это 21:57:32 по Алматы
        fresh = service._after_bookmark([message], None, now=now)
        self.assertEqual(['1'], [m['id'] for m in fresh],
                         'сообщение минутной давности обязано пройти горизонт')


class BookmarkTests(unittest.TestCase):
    """Что робот считает «ещё не разобранным». Ошибка здесь стоит дороже всего.

    Слева — потеря обращения, справа — сотни сделок задним числом на первом же
    запуске. Обе крайности достижимы, поэтому у закладки три правила, и каждое
    проверяется отдельно.
    """

    NOW = datetime(2026, 8, 31, 12, 0)

    @staticmethod
    def msg(mid, minutes_ago):
        stamp = BookmarkTests.NOW - timedelta(minutes=minutes_ago)
        return {'id': mid, 'type': 'received', 'text': '',
                'created_at': stamp.isoformat() + '+05:00'}

    def test_bookmark_by_id_cuts_everything_up_to_it(self):
        messages = [self.msg('1', 30), self.msg('2', 20), self.msg('3', 10)]
        fresh = service._after_bookmark(messages, '2', now=self.NOW)
        self.assertEqual(['3'], [m['id'] for m in fresh])

    def test_order_of_the_api_response_does_not_matter(self):
        """Сортировка выдачи OLX не документирована — полагаться на неё нельзя."""
        messages = [self.msg('3', 10), self.msg('1', 30), self.msg('2', 20)]
        fresh = service._after_bookmark(messages, '2', now=self.NOW)
        self.assertEqual(['3'], [m['id'] for m in fresh])

    def test_missing_bookmark_falls_back_to_time_not_to_everything(self):
        """Чат разросся, страница до закладки не достала.

        Взять «всё, что видим» нельзя: если OLX отдаёт страницу от старых к
        новым, робот раз за разом разбирал бы одну и ту же древнюю переписку.
        """
        messages = [self.msg('10', 300), self.msg('11', 200), self.msg('12', 5)]
        fresh = service._after_bookmark(
            messages, 'закладки-нет-в-выдаче',
            seen_until=self.NOW - timedelta(minutes=100), now=self.NOW)
        self.assertEqual(['12'], [m['id'] for m in fresh])

    def test_brand_new_thread_is_limited_by_the_horizon(self):
        """Предохранитель первого запуска: историю чатов робот не поднимает."""
        messages = [self.msg('1', 60 * 24 * 7), self.msg('2', 60 * 10),
                    self.msg('3', 3)]
        fresh = service._after_bookmark(messages, None, now=self.NOW)
        self.assertEqual(['3'], [m['id'] for m in fresh],
                         'старше горизонта разбирать нельзя')

    def test_horizon_is_configurable(self):
        """На первом боевом запуске его осмысленно сузить и посмотреть журнал."""
        source = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')
        self.assertIn("os.getenv('OLX_MESSAGE_HORIZON_HOURS')", source)


class CandidateTests(unittest.TestCase):
    """Кого робот берёт в разбор. Ошибка здесь тихо теряет живые обращения.

    Прод, 02.09.2026: отбор шёл только по непрочитанному, и чат, который
    маркетолог открыл раньше робота, выпадал навсегда — счётчик непрочитанного
    гасился человеком. Так потерялись четыре обращения, два из них с телефоном.

    Обратная крайность не менее опасна: чат, отсечённый горизонтом, робот
    намеренно не помечает прочитанным, поэтому тот висит непрочитанным вечно —
    и без отсева занимал бы весь лимит цикла, вытесняя свежие обращения.
    """

    def setUp(self):
        self.db = FakeDb()
        self.states = []

        class _Q(object):
            now_almaty = staticmethod(FakeQueries.now_almaty)

            @staticmethod
            def threads_state(cursor, cabinet_code, thread_ids):
                return self.states

        self._real = service.queries
        service.queries = _Q
        self.addCleanup(lambda: setattr(service, 'queries', self._real))

        self._horizon = service.HORIZON
        service.HORIZON = timedelta(minutes=15)
        self.addCleanup(lambda: setattr(service, 'HORIZON', self._horizon))

        self.cab = cabinets.BY_CODE['itaxi']

    def pick(self, threads):
        return [str(t['id']) for t in service._candidates(self.db, self.cab, threads)]

    def test_unread_thread_is_always_taken(self):
        self.states = [{'thread_id': '77', 'last_message_id': '5',
                        'last_total_count': 3}]
        self.assertEqual(['77'], self.pick([{'id': '77', 'unread_count': 1,
                                             'total_count': 3}]))

    def test_known_thread_with_a_new_message_is_taken_even_if_read_by_a_human(self):
        """Главный случай инцидента: человек открыл чат, счётчик обнулился."""
        self.states = [{'thread_id': '77', 'last_message_id': '5',
                        'last_total_count': 3}]
        self.assertEqual(['77'], self.pick([{'id': '77', 'unread_count': 0,
                                             'total_count': 4}]))

    def test_known_thread_without_changes_is_skipped(self):
        self.states = [{'thread_id': '77', 'last_message_id': '5',
                        'last_total_count': 3}]
        self.assertEqual([], self.pick([{'id': '77', 'unread_count': 0,
                                         'total_count': 3}]))

    def test_thread_from_before_the_change_has_no_total_and_is_taken(self):
        """Строки, заведённые старым кодом, пропускаем: лучше лишний запрос."""
        self.states = [{'thread_id': '77', 'last_message_id': '5',
                        'last_total_count': None}]
        self.assertEqual(['77'], self.pick([{'id': '77', 'unread_count': 0,
                                             'total_count': 3}]))

    def test_brand_new_thread_is_taken_even_without_unread(self):
        """Кандидат написал, человек открыл раньше нас — чат всё равно наш."""
        self.states = []
        created = (FakeQueries.now_almaty() - timedelta(minutes=2)
                   - timedelta(hours=5))          # OLX отдаёт UTC
        self.assertEqual(['99'], self.pick([{
            'id': '99', 'unread_count': 0, 'total_count': 1,
            'created_at': created.strftime('%Y-%m-%d %H:%M:%S')}]))

    def test_old_unknown_thread_without_unread_is_left_alone(self):
        """Иначе первый же опрос полез бы читать всю историю кабинета."""
        self.states = []
        created = (FakeQueries.now_almaty() - timedelta(days=3)
                   - timedelta(hours=5))
        self.assertEqual([], self.pick([{
            'id': '99', 'unread_count': 0, 'total_count': 1,
            'created_at': created.strftime('%Y-%m-%d %H:%M:%S')}]))


class ThreadSafetyTests(unittest.TestCase):
    def test_each_thread_gets_its_own_amocrm_client(self):
        """requests.Session потокобезопасной не объявлена, а потоков девять."""
        seen = {}

        class _Stub(object):
            pass

        def fake_client():
            state = service._thread_state
            client = getattr(state, 'amo_client', None)
            if client is None:
                client = _Stub()
                state.amo_client = client
            return client

        def worker(name):
            seen[name] = id(fake_client())
            seen[name + '_again'] = id(fake_client())

        threads = [threading.Thread(target=worker, args=('a',)),
                   threading.Thread(target=worker, args=('b',))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(seen['a'], seen['a_again'], 'внутри потока клиент один')
        self.assertNotEqual(seen['a'], seen['b'], 'между потоками клиенты разные')


class OauthLandingTests(unittest.TestCase):
    """Страница, на которую OLX возвращает браузер после согласия владельца.

    У неё два свойства, и оба неочевидные. Она ОБЯЗАНА быть без авторизации —
    сюда приходит редирект от OLX, заголовка с токеном портала в нём нет. И она
    обязана ничего не делать с кодом: обменяй она его сразу, любой открывший
    ссылку подключал бы кабинеты к нашей CRM без проверки прав.
    """

    @classmethod
    def setUpClass(cls):
        from contextlib import contextmanager

        from flask import Flask

        from olx_amo import routes

        class _Db(object):
            @contextmanager
            def _get_cursor(self):
                yield None

        def _deny(*_a, **_kw):
            raise AssertionError('страница возврата не должна спрашивать права')

        app = Flask(__name__)
        app.register_blueprint(routes.build_olx_amo_blueprint(
            db=_Db(),
            require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=_deny,
        ))
        cls.client = app.test_client()
        cls.routes = routes

    def test_landing_shows_the_code_without_asking_for_rights(self):
        response = self.client.get(
            '/api/olx_amo/oauth/callback?code=abc123XYZ&state=itaxi')
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('abc123XYZ', body)
        self.assertIn('itaxi_olx', body, 'человек должен видеть, какой это кабинет')
        # Код одноразовый и короткоживущий — в кеше ему делать нечего.
        self.assertEqual('no-store', response.headers.get('Cache-Control'))

    def test_landing_reports_a_refusal_instead_of_a_blank_page(self):
        response = self.client.get(
            '/api/olx_amo/oauth/callback?error=access_denied'
            '&error_description=User+refused&state=cr')
        self.assertIn('User refused', response.get_data(as_text=True))

    def test_landing_escapes_what_came_in_the_address(self):
        """Параметры приходят снаружи — вставлять их в разметку как есть нельзя."""
        response = self.client.get(
            '/api/olx_amo/oauth/callback?code=%3Cscript%3Ealert(1)%3C/script%3E&state=cr')
        body = response.get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_pasted_address_works_as_well_as_a_bare_code(self):
        """У части приложений в заявке OLX стоит голый адрес сервиса.

        Тогда после согласия браузер уезжает на «Bot is alive!», и код остаётся
        в адресной строке. Требовать «выкусите подстроку между code= и &» —
        верный способ получить в поле половину адреса.
        """
        extract = self.routes._extract_code
        self.assertEqual('def456', extract('def456'))
        self.assertEqual('def456', extract('  def456  '))
        self.assertEqual('def456', extract(
            'https://otp-2-fos4.onrender.com/?code=def456&state=itaxi'))
        self.assertEqual('def456', extract(
            'https://otp-2-fos4.onrender.com/api/olx_amo/oauth/callback'
            '?code=def456&state=cr'))
        self.assertEqual('def456', extract('?code=def456&state=cr'))
        self.assertEqual('', extract(''))


class AlertChatsTests(unittest.TestCase):
    """Куда слать отбивку — выбирается в разделе из групп, где уже есть бот.

    Свой реестр групп раздел не заводит: те, куда добавлен бот, копятся в общей
    таблице портала, и второй справочник тех же групп разошёлся бы с первым.
    Поэтому проверяется главное: выбрать можно только то, что реально доступно.
    """

    BOT_CHATS = [
        {'chat_id': -1001, 'title': 'ОП · заявки', 'chat_type': 'supergroup', 'username': None},
        {'chat_id': -1002, 'title': 'Маркетинг', 'chat_type': 'group', 'username': None},
    ]

    def setUp(self):
        from contextlib import contextmanager

        from flask import Flask

        from olx_amo import queries as real_queries
        from olx_amo import routes

        store = self.store = []
        bot_chats = self.BOT_CHATS

        class _Db(object):
            @contextmanager
            def _get_cursor(self):
                yield None

            @staticmethod
            def list_it_ticket_channels(active_only=True):
                return bot_chats

        class _Q(object):
            def __getattr__(self, name):
                return getattr(real_queries, name)

            @staticmethod
            def load_access_context(cursor, user_id):
                return {'user_id': 1, 'role': 'admin', 'headed_department_ids': [],
                        'headed_department_codes': []}

            @staticmethod
            def list_alert_chats(cursor):
                return list(store)

            @staticmethod
            def set_alert_chats(cursor, chats, actor_id=None):
                store[:] = [dict(c, last_sent_at=None) for c in chats]
                return list(store)

        self._real = routes.queries
        routes.queries = _Q()
        self.addCleanup(lambda: setattr(routes, 'queries', self._real))

        app = Flask(__name__)
        app.register_blueprint(routes.build_olx_amo_blueprint(
            db=_Db(),
            require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None),
        ))
        self.client = app.test_client()

    def test_lists_groups_where_the_bot_already_is(self):
        body = self.client.get('/api/olx_amo/chats').get_json()
        self.assertEqual([-1001, -1002], [c['chat_id'] for c in body['available']])
        self.assertEqual([], body['chosen_ids'])

    def test_saves_and_reads_back_the_choice(self):
        saved = self.client.put('/api/olx_amo/chats', json={'chat_ids': [-1002]})
        self.assertEqual(200, saved.status_code)
        body = self.client.get('/api/olx_amo/chats').get_json()
        self.assertEqual([-1002], body['chosen_ids'])
        self.assertEqual('Маркетинг', body['selected'][0]['title'],
                         'название берём из реестра бота, а не из тела запроса')

    def test_refuses_a_chat_the_bot_is_not_in(self):
        """Иначе отбивка молча уходила бы в никуда."""
        response = self.client.put('/api/olx_amo/chats', json={'chat_ids': [-9999]})
        self.assertEqual(400, response.status_code)
        self.assertIn('-9999', response.get_json()['error'])

    def test_refuses_garbage_instead_of_a_list(self):
        self.assertEqual(400, self.client.put(
            '/api/olx_amo/chats', json={'chat_ids': 'нет'}).status_code)
        self.assertEqual(400, self.client.put(
            '/api/olx_amo/chats', json={'chat_ids': ['ой']}).status_code)

    def test_empty_list_turns_notifications_off(self):
        self.client.put('/api/olx_amo/chats', json={'chat_ids': [-1002]})
        response = self.client.put('/api/olx_amo/chats', json={'chat_ids': []})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], self.client.get('/api/olx_amo/chats').get_json()['chosen_ids'])

    def test_job_reads_recipients_from_the_database_not_from_the_environment(self):
        """Список меняют живые люди чаще, чем выкатывается релиз."""
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertNotIn('OLX_ALERT_CHAT_IDS', bot)
        job = bot[bot.index('async def olx_amo_alerts_job'):]
        job = job[:job.index('def _mark_olx_alert_sent')]
        self.assertIn('list_alert_chats', job)
        self.assertIn('if not chats:', job,
                      'некому слать — незачем и собирать поводы')


class PoolWiringTests(unittest.TestCase):
    """Как робот раздаётся по потокам — арифметика, а не вкус."""

    @classmethod
    def setUpClass(cls):
        cls.bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        cls.service = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')

    def test_job_dispatches_cabinets_to_the_dedicated_pool(self):
        """Координатор не должен занимать место в пуле, места которого раздаёт."""
        job = self.bot[self.bot.index('async def olx_amo_poll_job'):]
        job = job[:job.index('async def olx_amo_retry_job')]
        self.assertIn('run_in_executor(olx_amo_pool, olx_amo_service.poll_cabinet', job)
        self.assertNotIn('executor_pool,', job,
                         'опрос не должен занимать общий пул бота (в нём 4 места)')
        self.assertIn('return_exceptions=True', job,
                      'падение одного кабинета не должно ронять остальные восемь')

    def test_handler_takes_the_database_not_a_cursor(self):
        """Подпись сторожит правило «сеть вне транзакции»."""
        self.assertIn('def _handle_message(db, writers, cabinet, thread, message, counters):',
                      self.service)

    def test_retry_checks_amocrm_before_creating_a_second_lead(self):
        """Сбой мог случиться ПОСЛЕ создания сделки: ответ не доехал."""
        self.assertIn('_lead_already_there(writer, phone', self.service)


if __name__ == '__main__':
    unittest.main()
