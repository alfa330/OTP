# -*- coding: utf-8 -*-
"""Раздел «Тренажёр»: собеседник договаривает то, что не прозвучало.

Задача этих тестов одна: модель обязана видеть РАЗГОВОР, а не стенограмму
синтеза. На проде 22.08.2026 пятая часть реплик собеседника не звучала вовсе,
но в историю уходила целиком — и следующая реплика продолжала мысль, которой
человек не слышал ни одним звуком.

Правило, которое здесь закреплено:

    прозвучало целиком          → текст как есть, ничего не меняется;
    оборвано на полуслове       → услышанное + пометка о непрозвучавшем;
    старая реплика без замеров  → ведёт себя ровно как раньше.

Молча обрезать нельзя: модель придумает окончание заново, и у водителя меняются
фамилия и сумма — этот класс ошибок раздел уже ловил браузерной проверкой.
"""

import contextlib
import json
import unittest
from unittest import mock

from voice_trainer import scenarios


class Cursor:
    """Курсор-заглушка: отвечает по смыслу запроса, записи копит.

    История разговора задаётся полем `history` — списком кортежей ровно в том
    виде, в каком их отдаёт _load_history.
    """

    def __init__(self):
        self.executed = []
        self._last = ''
        self.history = []
        self.spoken_before = 0
        self.next_turn_id = 100
        self.detail_turn = None       # строка реплики для журнала
        self.detail_session = None    # строка сессии для журнала

    def execute(self, sql, params=None):
        self._last = ' '.join(str(sql).split())
        self.executed.append((self._last, params))

    def fetchone(self):
        if 'FROM users' in self._last:
            return (7, 'Тест', 'super_admin')
        if 'FROM trainer_sessions WHERE id' in self._last:
            # Журнал читает ту же таблицу другим запросом — различаем по составу.
            if self.detail_session is not None and 'scenario_title' in self._last:
                return self.detail_session
            return (1, 'driver', 'cancel_calm', 'active', 7)
        if 'COALESCE(spoken_ms' in self._last:
            return (self.spoken_before,)
        if 'INSERT INTO trainer_turns' in self._last:
            self.next_turn_id += 1
            return (self.next_turn_id,)
        if 'INSERT INTO trainer_sessions' in self._last:
            import datetime
            return (1, datetime.datetime(2026, 8, 22, 12, 0, 0))
        if 'FROM trainer_turns t JOIN trainer_sessions' in self._last:
            return (42,)
        return None

    def fetchall(self):
        # История разговора и сводка замеров читаются РАЗНЫМИ запросами из одной
        # таблицы: различаем по составу колонок, а не по имени таблицы.
        if 'SELECT role, text, kind' in self._last:
            return self.history
        if self.detail_turn is not None and 'endpoint_delay_ms' in self._last:
            return [self.detail_turn]
        return []

    def sql_index(self, needle):
        """Порядковый номер первого запроса, содержащего needle."""
        for index, (sql, _) in enumerate(self.executed):
            if needle in sql:
                return index
        return -1


class Db:
    def __init__(self):
        self.cursor = Cursor()

    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield self.cursor
        return cm()


def build(env_map=None):
    from flask import Flask

    from voice_trainer.routes import build_trainer_blueprint

    base = {'SONIOX_API_KEY': 's',
            'GOOGLE_APPLICATION_CREDENTIALS_CONTENT': '{"type": "service_account"}'}
    base.update(env_map or {})
    db = Db()
    app = Flask(__name__)
    app.register_blueprint(build_trainer_blueprint(
        db=db,
        require_api_key=lambda f: f,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (7, None, None),
        is_super_admin_role=lambda value: str(value).strip().lower() == 'super_admin',
        env=lambda key, default=None: base.get(key, default),
    ))
    return app.test_client(), db


class FakeCreds:
    valid = True
    token = 'ya29.fake'
    project_id = 'test-project'

    @classmethod
    def from_service_account_info(cls, _info, **_kwargs):
        return cls()


class FakeReply:
    """Ответ Vertex на generateContent."""

    status_code = 200
    text = ''

    @staticmethod
    def json():
        return {'candidates': [{'content': {'parts': [{'text': 'Ага.'}]}}],
                'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 2}}


@contextlib.contextmanager
def driver_answers():
    """Модель отвечает без сети; тело запроса кладём в seen['body']."""
    seen = {}

    def post(_self, _url, **kwargs):
        seen['body'] = kwargs.get('json')
        return FakeReply()

    with mock.patch('google.oauth2.service_account.Credentials.from_service_account_info',
                    FakeCreds.from_service_account_info), \
            mock.patch('httpx.Client.post', post):
        yield seen


def history_row(role, text, *, cut=False, chars=None):
    """Строка истории в том виде, в каком её отдаёт _load_history."""
    return (role, text, None, cut, chars)


CUT_REPLY = 'Заказ был сегодня в два двадцать, отменил его сам клиент.'


class CutTest(unittest.TestCase):

    def test_model_sees_heard_text_and_a_note_about_the_rest(self):
        """Оборванная реплика уходит в модель услышанным куском плюс пометкой."""
        client, db = build()
        db.cursor.history = [
            history_row('driver', CUT_REPLY, cut=True, chars=len('Заказ был сегодня')),
            history_row('trainee', 'А кто отменил?'),
        ]
        with driver_answers() as seen:
            client.post('/api/trainer/sessions/1/turn', json={'text': 'Так кто отменил?'})

        said = json.dumps(seen['body']['contents'], ensure_ascii=False)
        self.assertIn('Заказ был сегодня', said)
        self.assertIn('ТЕБЯ ПЕРЕБИЛИ ЗДЕСЬ', said)
        self.assertIn('отменил его сам клиент', said)   # хвост назван непрозвучавшим
        # Но НЕ как обычный текст реплики: между услышанным и хвостом обязана
        # стоять пометка, иначе модель считает, что человек всё слышал.
        self.assertLess(said.index('Заказ был сегодня'), said.index('ТЕБЯ ПЕРЕБИЛИ'))
        self.assertLess(said.index('ТЕБЯ ПЕРЕБИЛИ'), said.index('отменил его сам клиент'))

    def test_reply_that_sounded_in_full_is_untouched(self):
        """Прозвучавшая целиком реплика идёт в модель без единого изменения.

        Так лежат ВСЕ реплики, записанные до появления замеров, — поведение для
        них не меняется.
        """
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY)]
        with driver_answers() as seen:
            client.post('/api/trainer/sessions/1/turn', json={'text': 'Понял вас.'})

        said = json.dumps(seen['body']['contents'], ensure_ascii=False)
        self.assertIn(CUT_REPLY, said)
        self.assertNotIn('ПЕРЕБИЛИ', said)

    def test_spoken_is_applied_before_history_is_read(self):
        """prev записывается ДО чтения истории, иначе резать нечего.

        Порядок здесь не косметика: история собирается тем же запросом, который
        читает spoken_chars. Придёт факт позже — модель получит несказанное.
        """
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY)]
        with driver_answers():
            client.post('/api/trainer/sessions/1/turn', json={
                'text': 'Ага.',
                'prev': {'turn_id': 55, 'spoken_ms': 1180, 'spoken_chars': 17, 'cut': True},
            })

        wrote = db.cursor.sql_index('SET spoken_ms')
        read = db.cursor.sql_index('FROM trainer_turns WHERE session_id')
        self.assertGreater(wrote, -1, 'prev не записан вовсе')
        self.assertGreater(read, -1)
        self.assertLess(wrote, read, 'услышанное записано ПОСЛЕ чтения истории')

    def test_heard_milliseconds_are_added_once_even_if_the_fact_arrives_twice(self):
        """Один и тот же факт приезжает и полем prev, и запросом PATCH.

        Прибавлять надо РАЗНИЦУ, иначе сессионный итог «сколько человек
        услышал» задваивается на каждой реплике.
        """
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY)]
        spoken = {'turn_id': 55, 'spoken_ms': 1180, 'spoken_chars': 17, 'cut': True}
        with driver_answers():
            client.post('/api/trainer/sessions/1/turn', json={'text': 'Ага.', 'prev': spoken})
        db.cursor.spoken_before = 1180        # значение уже записано
        client.patch('/api/trainer/turns/55', json={
            'spoken_ms': 1180, 'spoken_chars': 17, 'speech_cut': True})

        added = [params[0] for sql, params in db.cursor.executed
                 if 'audio_heard_ms = GREATEST' in sql]
        self.assertEqual([1180], added,
                         'услышанное прибавилось дважды или не прибавилось вовсе')

    def test_patch_never_touches_the_price_of_synthesis(self):
        """audio_out_ms прибавляет только поток озвучки.

        Второе прибавление здесь удвоило бы стоимость прогона, а браузер
        tts_audio_ms не шлёт вовсе.
        """
        client, db = build()
        client.patch('/api/trainer/turns/55', json={
            'spoken_ms': 900, 'spoken_chars': 12, 'speech_cut': True})
        self.assertFalse([sql for sql, _ in db.cursor.executed if 'audio_out_ms' in sql])

    def test_review_reads_the_conversation_that_happened(self):
        """Разбор оценивает стажёра по услышанному, а не по сгенерированному.

        Иначе тренажёр снимает баллы за то, чего стажёр физически не слышал.
        """
        client, db = build()
        db.cursor.history = [
            history_row('driver', CUT_REPLY, cut=True, chars=len('Заказ был сегодня')),
            history_row('trainee', 'А кто отменил?'),
        ]
        seen = {}

        def post(_self, _url, **kwargs):
            seen['body'] = kwargs.get('json')
            return FakeReplyReview()

        class FakeReplyReview:
            status_code = 200
            text = ''

            @staticmethod
            def json():
                return {'candidates': [{'content': {'parts': [{'text': json.dumps({
                    'score': 40, 'criteria': [], 'done': [], 'missed': [],
                    'critical': [], 'recommendation': 'ок'})}]}}]}

        with mock.patch('google.oauth2.service_account.Credentials.from_service_account_info',
                        FakeCreds.from_service_account_info), \
                mock.patch('httpx.Client.post', post):
            client.post('/api/trainer/sessions/1/finish')

        transcript = json.dumps(seen['body']['contents'], ensure_ascii=False)
        self.assertIn('Заказ был сегодня', transcript)
        self.assertIn('ПЕРЕБИЛИ', transcript)

    def test_journal_reads_columns_by_the_right_position(self):
        """Колонки журнала читаются ПО НОМЕРУ, и сдвиг на единицу молчит.

        Добавление колонки в SELECT — самый дешёвый способ незаметно сдвинуть
        весь хвост: ошибок не будет, в журнале просто окажутся чужие числа.
        Поэтому кладём метки на известные места и проверяем, что каждая пришла
        туда, куда должна.
        """
        client, db = build()
        turn = (901, 3, 'driver', 'Заказ был сегодня.', None,
                'ru', {'ru': 9}, 0.97, 9, 3400, 640,
                'vertex', 'gemini-3-flash-preview', 700, 1100, 4600, 12,
                'vertex:gemini-3.1-flash-tts-preview', 530, 8480, 407040,
                1906, True, None,
                'answer', 300, 1180, 17, True)
        session = (1, 'driver', 'cancel_calm', 'Отмена заказа', 3, 'ru', 'finished',
                   None, None, 240000, 6, 2, 12000, 34000, 1900, 3200,
                   40, None, 0.0071, None, None, None,
                   'vertex', 'gemini-3-flash-preview', 'vertex:tts', None, 5600)
        db.cursor.detail_turn = turn
        db.cursor.detail_session = session

        got = client.get('/api/trainer/sessions/1').get_json()
        row = got['turns'][0]
        self.assertEqual(1180, row['spoken']['ms'])
        self.assertEqual(17, row['spoken']['chars'])
        self.assertTrue(row['spoken']['cut'])
        self.assertEqual('answer', row['kind'])
        self.assertEqual(300, row['hold_ms'])
        # Соседние поля не должны съехать вместе с новыми.
        self.assertEqual(1906, row['pace_ms'])
        self.assertEqual(8480, row['tts']['audio_ms'])
        self.assertEqual(0.97, round(row['stt']['confidence'], 2))
        self.assertEqual(5600, got['session']['audio_heard_ms'])
        self.assertEqual(34000, got['session']['audio_out_ms'])

    def test_opening_turn_id_is_returned_so_its_metrics_can_be_written(self):
        """Приветствие получает id: без него его замеры не пишутся никогда.

        На проде так остались без единой цифры все десять приветствий.
        """
        client, _ = build()
        created = client.post('/api/trainer/sessions',
                              json={'mode': 'driver', 'scenario': 'cancel_calm'}).get_json()
        self.assertEqual(scenarios.SCENARIOS['cancel_calm']['opening'], created['opening'])
        self.assertIsNotNone(created['opening_turn_id'])


if __name__ == '__main__':
    unittest.main()


class ModelTextTest(unittest.TestCase):
    """Служебные куски модели не должны доходить до синтеза.

    На приёмке казахского сценария 22.08.2026 пять реплик из тринадцати начались
    с префикса «thought:» — синтез прочитал бы его вслух латиницей посреди
    казахской фразы. Плюс Vertex помечает куски «мышления» флагом, и склейка
    всех кусков подряд подмешивала их в реплику.
    """

    def _reply(self, parts):
        client, db = build()
        db.cursor.history = []

        class Reply:
            status_code = 200
            text = ''

            @staticmethod
            def json():
                return {'candidates': [{'content': {'parts': parts}}],
                        'usageMetadata': {}}

        with mock.patch('google.oauth2.service_account.Credentials.from_service_account_info',
                        FakeCreds.from_service_account_info), \
                mock.patch('httpx.Client.post', lambda *a, **k: Reply()):
            got = client.post('/api/trainer/sessions/1/turn', json={'text': 'Алло?'})
        return got.get_json().get('text')

    def test_thinking_parts_are_dropped(self):
        text = self._reply([
            {'text': 'Надо ответить коротко и уклончиво.', 'thought': True},
            {'text': 'Жақында ғана өткенмін ғой.'},
        ])
        self.assertEqual('Жақында ғана өткенмін ғой.', text)

    def test_literal_thought_prefix_is_stripped(self):
        self.assertEqual('Жоқ, жоқ, ауыстырған жоқпын.',
                         self._reply([{'text': 'thought:Жоқ, жоқ, ауыстырған жоқпын.'}]))

    def test_markup_never_reaches_the_synthesizer(self):
        # Промпт разметку запрещает, но запрет в промпте — просьба, а звёздочки
        # синтез читает вслух.
        self.assertEqual('Мне за ожидание не пришло.',
                         self._reply([{'text': '**Мне за ожидание** не пришло.\n\n'}]))


class UnheardTest(unittest.TestCase):
    """Реплика, не прозвучавшая НИ ОДНИМ словом, — отдельный случай.

    Перебивание в первые доли секунды на проде было массовым: 9 реплик из 45 не
    издали ни байта. Пометка «часть слов не прозвучала» тут врёт, а сама реплика
    в истории состояла бы из одной служебной строки без единого своего слова.
    """

    def test_reply_that_never_sounded_is_named_so(self):
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY, cut=True, chars=0)]
        with driver_answers() as seen:
            client.post('/api/trainer/sessions/1/turn', json={'text': 'Алло?'})
        said = json.dumps(seen['body']['contents'], ensure_ascii=False)
        self.assertIn('НЕ ПРОЗВУЧАЛА ВООБЩЕ', said)
        self.assertNotIn('ПЕРЕБИЛИ ЗДЕСЬ', said)
        self.assertIn('Заказ был сегодня', said)   # что хотел сказать — назвали

    def test_broken_measurement_does_not_kill_the_turn(self):
        """Кривой замер из браузера не должен ронять весь ход.

        Разбор голым int() внутри той же транзакции, что рождает ответ, откатил
        бы её целиком: реплика человека не сохранилась бы, а ответ пропал.
        """
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY)]
        with driver_answers():
            got = client.post('/api/trainer/sessions/1/turn', json={
                'text': 'Ага.',
                'prev': {'turn_id': 'не число', 'spoken_ms': None, 'spoken_chars': 'ой'},
            })
        self.assertEqual(200, got.status_code)
        self.assertTrue(got.get_json().get('text'))

    def test_spoken_from_another_session_is_ignored(self):
        """turn_id из чужого разговора не должен править чужие замеры."""
        client, db = build()
        db.cursor.history = [history_row('driver', CUT_REPLY)]
        with driver_answers():
            client.post('/api/trainer/sessions/1/turn', json={
                'text': 'Ага.',
                'prev': {'turn_id': 55, 'spoken_ms': 100, 'spoken_chars': 5, 'cut': True},
            })
        checks = [params for sql, params in db.cursor.executed
                  if 'COALESCE(spoken_ms' in sql]
        self.assertTrue(checks, 'проверки принадлежности реплики нет вовсе')
        self.assertEqual((55, 1), checks[0], 'реплика ищется без оглядки на сессию')
