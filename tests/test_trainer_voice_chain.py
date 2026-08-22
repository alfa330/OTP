# -*- coding: utf-8 -*-
"""Озвучка раздела «Тренажёр»: цепочка провайдеров и отказ, который слышно.

Раздел сутки молчал (22.08.2026) не потому, что сломался код, а потому что у
провайдера кончились деньги — и молчание доходило до человека БЕЗ ЕДИНОГО СЛОВА
о причине: сервер честно досылал «done» с нулём байт, браузер честно ничего не
играл. Тесты держат три правила, которые из этого следуют:

  1. ноль байт — это ошибка с текстом, а не успех;
  2. пока не прозвучало ни куска, отказ провайдера — повод взять следующего;
  3. как только звук пошёл, переключаться НЕЛЬЗЯ: второй провайдер прочитал бы
     ту же реплику с начала поверх первой.
"""

import base64
import contextlib
import json
import unittest
from unittest import mock

import websocket


AUDIO = base64.b64encode(b'\x01\x02' * 240).decode()


class Cursor:
    """Курсор-заглушка: отвечает по смыслу запроса, записи копит."""

    def __init__(self):
        self.executed = []
        self._last = ''
        # Сценарий сессии: от него зависит МАНЕРА речи, которая подставляется в
        # промпт озвучки. Берётся из базы, а не из тела запроса.
        self.scenario_key = None

    def execute(self, sql, params=None):
        self._last = ' '.join(str(sql).split())
        self.executed.append((self._last, params))

    def fetchone(self):
        if 'FROM users' in self._last:
            return (7, 'Тест', 'super_admin')
        if 'tts_voice' in self._last:
            return ('Charon', self.scenario_key)
        return None

    def fetchall(self):
        return []


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

    base = {'SONIOX_API_KEY': 's', 'GEMINI_API_KEY': 'g',
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


def events_from(raw):
    """События из одного куска потока."""
    out = []
    for part in raw.decode('utf-8').split('\n\n'):
        part = part.strip()
        if part.startswith('data:'):
            out.append(json.loads(part[5:].strip()))
    return out


def events_of(response):
    """Разбирает SSE так же, как это делает браузер."""
    out, buffer = [], ''
    for raw in response.response:
        buffer += raw.decode('utf-8')
        while '\n\n' in buffer:
            part, buffer = buffer.split('\n\n', 1)
            part = part.strip()
            if part.startswith('data:'):
                out.append(json.loads(part[5:].strip()))
    return out


# ── заглушки провайдеров ─────────────────────────────────────────────────────

class FakeStream:
    """Ответ httpx.stream: SSE с кусками аудио от Vertex."""

    def __init__(self, status=200, chunks=(), rate=24000, boom_after=None):
        self.status_code = status
        self._chunks = chunks
        self._rate = rate
        self._boom_after = boom_after

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"error": "no"}'

    def iter_lines(self):
        for index, chunk in enumerate(self._chunks):
            if self._boom_after is not None and index == self._boom_after:
                raise RuntimeError('связь оборвалась на середине')
            yield 'data: ' + json.dumps({'candidates': [{'content': {'parts': [
                {'inlineData': {'mimeType': f'audio/l16; rate={self._rate}; channels=1',
                                'data': chunk}}]}}]})


def vertex_returning(*args, **kwargs):
    """Подмена httpx.Client.stream — раздел ходит к Vertex через постоянный клиент."""
    stream = FakeStream(*args, **kwargs)
    return lambda *a, **k: stream


class FakeCreds:
    """Сервисный аккаунт без сети: токен уже есть и не протухает."""

    valid = True
    token = 'ya29.fake'
    project_id = 'test-project'

    @classmethod
    def from_service_account_info(cls, _info, **_kwargs):
        return cls()


@contextlib.contextmanager
def vertex_signed_in():
    """Подпись сервисного аккаунта заглушена: проверяем цепочку, а не google-auth."""
    with mock.patch('google.oauth2.service_account.Credentials.from_service_account_info',
                    FakeCreds.from_service_account_info):
        yield


class Frame:
    def __init__(self, data):
        self.data = data


class FakeSocket:
    """Сокет Live API: отдаёт кадры по списку."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def send(self, _payload):
        return None

    def recv_data_frame(self):
        if not self._frames:
            return websocket.ABNF.OPCODE_TEXT, Frame('')
        return self._frames.pop(0)

    def close(self):
        self.closed = True


def live_frames(*frames):
    return lambda *a, **k: FakeSocket(frames)


TEXT = websocket.ABNF.OPCODE_TEXT
CLOSE = websocket.ABNF.OPCODE_CLOSE

LIVE_SPEAKS = (
    (TEXT, Frame(json.dumps({'setupComplete': {}}))),
    (TEXT, Frame(json.dumps({'serverContent': {'modelTurn': {'parts': [
        {'inlineData': {'data': AUDIO}}]}, 'turnComplete': True}}))),
)
LIVE_NO_CREDITS = (
    (CLOSE, Frame(b'\x03\xf3Your prepayment credits are depleted.')),
)


def speak(client, body=None):
    return client.post('/api/trainer/speak',
                       json={'text': 'Алло, слушаю вас.', **(body or {})})


# ── тесты ────────────────────────────────────────────────────────────────────

class VoiceChainTest(unittest.TestCase):

    def test_vertex_speaks_and_declares_its_own_rate(self):
        """Частота берётся из mimeType провайдера, а не зашита в браузер."""
        client, _ = build()
        with vertex_signed_in(), \
                mock.patch('httpx.Client.stream', vertex_returning(chunks=[AUDIO, AUDIO], rate=16000)):
            events = events_of(speak(client))
        kinds = [e['t'] for e in events]
        self.assertEqual(['start', 'audio', 'audio', 'done'], kinds)
        self.assertEqual('vertex', events[0]['provider'])
        self.assertEqual(16000, events[0]['rate'])
        self.assertEqual(16000, events[-1]['rate'])
        self.assertGreater(events[-1]['bytes'], 0)
        self.assertIsNone(events[-1]['error'])

    def test_silence_arrives_as_an_error_before_done(self):
        """Ноль байт — это отказ с текстом причины, а не тихий успех.

        Ровно этого не хватало: Live закрывал сокет с «prepayment credits are
        depleted», раздел досылал 'done' с нулём байт, и человек видел текст
        реплики при полной тишине.
        """
        client, db = build({'TRAINER_TTS_CHAIN': 'live'})
        with mock.patch('websocket.create_connection', live_frames(*LIVE_NO_CREDITS)):
            events = events_of(speak(client, {'session_id': 5}))
        kinds = [e['t'] for e in events]
        self.assertIn('error', kinds)
        self.assertLess(kinds.index('error'), kinds.index('done'), 'error обязан идти до done')
        self.assertNotIn('audio', kinds)
        message = events[kinds.index('error')]['message']
        self.assertIn('1011', message)
        self.assertIn('credits are depleted', message)
        self.assertEqual(0, events[-1]['bytes'])
        # …и то же самое остаётся в журнале сессии, а не только на экране.
        self.assertTrue(any('trainer_events' in sql and 'tts_failed' in str(params)
                            for sql, params in db.cursor.executed))

    def test_second_provider_picks_up_while_nothing_has_sounded(self):
        client, _ = build()
        with vertex_signed_in(), \
                mock.patch('httpx.Client.stream', vertex_returning(status=500)), \
                mock.patch('websocket.create_connection', live_frames(*LIVE_SPEAKS)):
            events = events_of(speak(client))
        starts = [e for e in events if e['t'] == 'start']
        self.assertEqual(1, len(starts))
        self.assertEqual('live', starts[0]['provider'])
        self.assertTrue(any(e['t'] == 'audio' for e in events))
        self.assertEqual('live', events[-1]['provider'])

    def test_no_switch_once_the_voice_has_started(self):
        """Обрыв на середине — конец реплики, а не повод прочитать её заново.

        Второй провайдер начал бы ту же фразу с начала поверх уже сказанного.
        """
        client, _ = build()
        with vertex_signed_in(), \
                mock.patch('httpx.Client.stream', vertex_returning(chunks=[AUDIO, AUDIO], boom_after=1)), \
                mock.patch('websocket.create_connection', live_frames(*LIVE_SPEAKS)):
            events = events_of(speak(client))
        self.assertEqual(1, len([e for e in events if e['t'] == 'start']))
        self.assertEqual(1, len([e for e in events if e['t'] == 'audio']))
        self.assertEqual('vertex', events[-1]['provider'])
        # Прозвучавшее засчитано — замеры реплики не должны обнуляться из-за
        # того, что провайдер оборвался на втором куске.
        self.assertGreater(events[-1]['bytes'], 0)
        self.assertIn('оборвалась', events[-1]['error'])

    def test_chain_order_comes_from_the_environment(self):
        client, _ = build({'TRAINER_TTS_CHAIN': 'live, vertex'})
        with mock.patch('websocket.create_connection', live_frames(*LIVE_SPEAKS)):
            events = events_of(speak(client))
        self.assertEqual('live', events[0]['provider'])

    def test_ping_shows_chains_and_names_a_link_with_nothing_to_run_on(self):
        client, _ = build()
        links = client.get('/api/trainer/ping').get_json()['links']
        self.assertEqual(['vertex', 'live'], links['tts']['chain'])
        self.assertEqual(['vertex', 'gemini', 'claude'], links['llm']['chain'])
        self.assertEqual([], links['tts']['missing'])

        # Ни сервисного аккаунта, ни ключа — озвучивать нечем, и это видно
        # ДО начала разговора, а не по его тишине.
        blind, _ = build({'GOOGLE_APPLICATION_CREDENTIALS_CONTENT': '',
                          'GEMINI_API_KEY': ''})
        ping = blind.get('/api/trainer/ping').get_json()
        self.assertIn('tts', ping['dead_links'])
        self.assertEqual([], ping['links']['tts']['ready'])

    def test_old_single_switch_puts_a_provider_first_without_dropping_the_rest(self):
        """TRAINER_LLM остаётся рабочим, но больше не отменяет цепочку.

        Раньше «первый» значил «единственный плюс жёсткий резерв», и когда у
        AI Studio кончились кредиты, менять было нечего.
        """
        client, _ = build({'TRAINER_LLM': 'claude'})
        chain = client.get('/api/trainer/ping').get_json()['links']['llm']['chain']
        self.assertEqual(['claude', 'vertex', 'gemini'], chain)

    def test_metrics_are_written_even_when_the_browser_drops_the_stream(self):
        """Браузер перебил реплику и закрыл поток — замеры всё равно в базе.

        Это главный дефект, найденный на проде 22.08.2026: UPDATE стоял ПОСЛЕ
        цикла yield, а при закрытии генератор получает GeneratorExit — это
        BaseException, обычный except его не ловит, и хвост просто не
        выполнялся. В итоге из 59 реплик собеседника 24 не имели ни одного
        замера озвучки — ровно все оборванные.
        """
        client, db = build()
        with vertex_signed_in(), mock.patch(
                'httpx.Client.stream', vertex_returning(chunks=[AUDIO, AUDIO, AUDIO])):
            response = speak(client, {'turn_id': 42, 'session_id': 1})
            stream = iter(response.response)
            # Читаем до первого куска ЗВУКА и бросаем поток, как делает браузер
            # при перебивании. До первого куска записывать нечего — там и
            # прозвучать ещё ничего не успело.
            heard = False
            while not heard:
                for event in events_from(next(stream)):
                    heard = heard or event['t'] == 'audio'
            response.response.close()

        written = [sql for sql, _ in db.cursor.executed if 'tts_ttfb_ms' in sql]
        self.assertTrue(written, 'замеры озвучки не записались при обрыве потока')

    def test_speak_stream_format_did_not_change(self):
        """Формат SSE обязан остаться прежним.

        Фронт едет на GitHub Pages отдельным деплоем, который срывается по
        таймауту и оставляет старый бандл. Старый бандл обязан продолжать
        работать с новым сервером, поэтому в поток озвучки не добавлено ни
        одного поля.
        """
        client, _ = build()
        with vertex_signed_in(), mock.patch(
                'httpx.Client.stream', vertex_returning(chunks=[AUDIO])):
            events = events_of(speak(client))
        self.assertEqual(['start', 'audio', 'done'], [e['t'] for e in events])
        self.assertEqual({'t', 'provider', 'model', 'rate'}, set(events[0]))

    def test_scenario_style_reaches_the_synthesizer(self):
        """Манера персонажа доезжает до промпта озвучки.

        Замер 22.08.2026: указание стиля исполняется моделью (темп 1,60 против
        2,62 слов/с) и ничего не стоит по задержке. Но подставляется оно из
        СЕССИИ, а не из тела запроса: иначе манеру можно было бы подменить из
        браузера.
        """
        from voice_trainer import scenarios

        seen = {}

        def catching(*_args, **kwargs):
            seen['prompt'] = kwargs['json']['contents'][0]['parts'][0]['text']
            return FakeStream(chunks=[AUDIO])

        client, db = build()
        db.cursor.scenario_key = 'payout_angry'
        with vertex_signed_in(), mock.patch('httpx.Client.stream', catching):
            speak(client, {'session_id': 1})
        self.assertIn(scenarios.TTS_STYLE['payout_angry'][:40], seen['prompt'])
        # Команда «произнеси ровно» обязана стоять ПОСЛЕДНЕЙ строкой перед
        # текстом, иначе модель зачитывает вслух сам стиль.
        self.assertLess(seen['prompt'].index(scenarios.SAY_EXACTLY.strip()[:20]),
                        seen['prompt'].index('Алло, слушаю вас.'))


if __name__ == '__main__':
    unittest.main()
