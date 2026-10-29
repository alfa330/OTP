"""Текст голосовых «Чатов ОП» (wazzup/voice_asr.py, wazzup/voice.py) без сети и базы.

Распознавание проверяется на подставном ответе Vertex той формы, что пришла на
замере 29.10.2026: JSON под схемой, usageMetadata с токенами по модальностям.
Очередь и ручка на настоящем SQL — tests/test_wazzup_voice_postgres.py."""
import json
import struct

import pytest

from wazzup import voice, voice_asr
from wazzup.voice_asr import VoiceError


def mp3(seconds, *, id3=True):
    """MPEG-1 Layer III, 128 кбит/с, 44,1 кГц: кадр 417 байт и 1152 сэмпла."""
    frame = struct.pack('>I', 0xFFFB9000) + b'\x00' * 413
    count = round(seconds * 44100 / 1152)
    head = b'ID3\x04\x00\x00\x00\x00\x00\x00' if id3 else b''
    return head + frame * count


def answer(text='Сәлеметсіз бе, заказ қашан келеді?', *, speech=True, language='kk',
           finish='STOP', raw=None, audio_tokens=250):
    body = raw if raw is not None else json.dumps(
        {'speech': speech, 'language': language, 'text': text}, ensure_ascii=False)
    return {'candidates': [{'content': {'parts': [{'text': body}]}, 'finishReason': finish}],
            'modelVersion': 'gemini-3.5-flash-lite-001',
            'usageMetadata': {'promptTokenCount': audio_tokens + 496, 'candidatesTokenCount': 40,
                              'promptTokensDetails': [{'modality': 'AUDIO', 'tokenCount': audio_tokens},
                                                      {'modality': 'TEXT', 'tokenCount': 496}]}}


def test_mp3_duration_walks_frames_after_the_id3_tag():
    assert voice_asr.mp3_duration(mp3(10)) == pytest.approx(10, abs=0.03)
    assert voice_asr.mp3_duration(mp3(2.5, id3=False)) == pytest.approx(2.5, abs=0.03)
    assert voice_asr.mp3_duration(b'not audio at all') is None


def test_mime_comes_from_the_bytes_and_non_audio_is_never_sent():
    assert voice_asr.audio_mime(mp3(1), 'application/octet-stream') == 'audio/mpeg'
    assert voice_asr.audio_mime(b'OggS' + b'\x00' * 20) == 'audio/ogg'
    with pytest.raises(VoiceError) as error:
        voice_asr.audio_mime(b'%PDF-1.7 ...', 'application/pdf')
    assert error.value.kind == 'skipped'


def test_answer_is_parsed_from_plain_and_fenced_json():
    assert voice_asr.parse_answer(answer())['text'] == 'Сәлеметсіз бе, заказ қашан келеді?'
    fenced = answer(raw='```json\n{"speech": true, "language": "ru", "text": "Где  машина?"}\n```')
    assert voice_asr.parse_answer(fenced) == {'speech': True, 'language': 'ru', 'text': 'Где машина?'}
    # Пустой текст при speech=true — это тоже «речи нет», а не пустая расшифровка.
    assert voice_asr.parse_answer(answer(text='  ', speech=True))['speech'] is False


@pytest.mark.parametrize('bad', [answer(finish='MAX_TOKENS'), answer(raw='не JSON'), answer(raw='[1, 2]')])
def test_broken_answer_is_a_retry_not_a_transcript(bad):
    with pytest.raises(VoiceError) as error:
        voice_asr.parse_answer(bad)
    assert error.value.kind == 'retry'


def test_a_transcript_the_recording_cannot_hold_is_refused():
    # Замер: 3.8 Flash на 1 с щелчка написала целую фразу водителя.
    assert voice_asr.implausible('Маған екі адамдық жер бар Астана қаласына қазір бірден керек', 1.0)
    assert voice_asr.implausible('Через сколько подъедет машина', 1.0) is None  # мало слов, чтобы судить
    assert voice_asr.implausible('А потом что будет? Мне сказали нажать это ID.', 4.9) is None
    looped = ' '.join(['мен қазір жолдамын'] * 20)
    assert voice_asr.implausible(looped, 600) == 'ответ зациклен'


_PRICE = voice_asr.PRICES[voice_asr.MODEL]
ONE_LISTEN = (250 * _PRICE[0] + 496 * _PRICE[1] + 40 * _PRICE[2]) / 1e6   # звук, текст, выход


def listens(*texts):
    """generate(), answering the given texts in turn (None — «речи нет»)."""
    queue, bodies = list(texts), []

    def generate(model, body):
        bodies.append((model, body))
        text = queue.pop(0)
        return answer(text=text or '', speech=text is not None, language='kk' if text else 'other')
    generate.bodies = bodies
    return generate


def test_transcribe_keeps_everything_the_paid_calls_returned():
    generate = listens('Сәлеметсіз бе, заказ қашан келеді?', 'Сәлеметсіз бе, заказ қашан келеді?')
    result = voice_asr.transcribe(mp3(10), 'audio/mpeg', generate=generate)
    assert result['status'] == 'ready' and result['language'] == 'kk' and result['error'] is None
    assert result['duration'] == pytest.approx(10, abs=0.03)
    assert result['model_version'] == 'gemini-3.5-flash-lite-001'
    # Короткая запись прослушана дважды: оба счёта и оба сырых блока.
    assert len(generate.bodies) == 2 and len(result['usage']['listens']) == 2
    assert result['tokens'] == (500, 992, 80, 0)
    assert result['cost'] == pytest.approx(2 * ONE_LISTEN)
    model, body = generate.bodies[0]
    assert model == voice_asr.MODEL == 'gemini-3.8-flash'   # выбор замера 29.10.2026
    assert body['generationConfig']['temperature'] == 0
    assert body['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0}
    assert body['contents'][0]['parts'][0]['inlineData']['mimeType'] == 'audio/mpeg'
    # В промпте нет образцов номеров: на тишине их переписывали в «расшифровку».
    assert '777' not in voice_asr.SYSTEM and 'ABC' not in voice_asr.SYSTEM


def test_a_long_recording_is_heard_once():
    generate = listens('Мен жолдамын.')
    result = voice_asr.transcribe(mp3(voice_asr.CHECK_SECONDS + 5), generate=generate)
    assert result['status'] == 'ready' and len(generate.bodies) == 1
    assert result['cost'] == pytest.approx(ONE_LISTEN) and 'listens' not in result['usage']


@pytest.mark.parametrize('first, second', [
    # Замер 29.10.2026: 3.8 Flash на тишине и уличном гуле — разные фразы при повторе.
    ('Машина бос, 836, Тамерлановский шоссе.', '44-тен бар ғой, Орталық жаққа шығайын деп едім.'),
    ('Маған 4-ке көлік тауып беріңізші, Таразға.', None),
    (None, 'Через сколько подъедет машина?'),
])
def test_listens_that_disagree_give_no_text(first, second):
    result = voice_asr.transcribe(mp3(4), generate=listens(first, second))
    assert result['status'] == 'no_speech' and result['text'] is None
    assert result['error'] == 'два прослушивания не совпали'
    assert result['cost'] == pytest.approx(2 * ONE_LISTEN)


def test_close_listens_are_one_text_and_silence_is_an_answer():
    close = voice_asr.transcribe(mp3(6), generate=listens('А потом что будет? Мне сказали, что не нажат этот ID.',
                                                          'А потом что будет? Мне сказали, не нажат этот ID.'))
    assert close['status'] == 'ready' and close['text'].startswith('А потом что будет?')
    silent = voice_asr.transcribe(mp3(4), generate=listens(None, None))
    assert silent['status'] == 'no_speech' and silent['error'] is None


def test_an_invention_is_no_text_when_short_and_a_paid_retry_when_long():
    invented = 'Мақтаарал алты жүз қырық екі бес жүз теңге алған жоқпын жеті жүз теңге берген екі жүз'
    short = voice_asr.transcribe(mp3(1), generate=listens(invented, invented))
    assert short['status'] == 'no_speech'
    looped = ' '.join(['мен қазір жолдамын'] * 30)
    with pytest.raises(VoiceError) as error:
        voice_asr.transcribe(mp3(voice_asr.CHECK_SECONDS + 1), generate=listens(looped))
    assert error.value.kind == 'retry'
    assert error.value.result['cost'] == pytest.approx(ONE_LISTEN)


def test_a_broken_listen_is_a_retry_that_still_counts_both_costs():
    def generate(model, body):
        generate.calls = getattr(generate, 'calls', 0) + 1
        return answer(raw='не JSON') if generate.calls == 1 else answer()
    with pytest.raises(VoiceError) as error:
        voice_asr.transcribe(mp3(5), generate=generate)
    assert error.value.kind == 'retry'
    assert error.value.result['cost'] == pytest.approx(2 * ONE_LISTEN)
    assert error.value.result['tokens'][0] == 500


def test_agreement_tells_two_hearings_of_one_phrase_from_two_inventions():
    assert voice_asr.agreement('Хорошо.', 'хорошо') == 1.0
    assert voice_asr.agreement('', '') == 1.0
    # Замер 29.10.2026: настоящие пары 3.8 Flash — не ниже 0,68, выдуманные — не выше 0,34.
    assert voice_asr.agreement('Ассалаумағалейкум.', 'Ассалаумағалейкүм.') > 0.9
    assert voice_asr.agreement('Сапарға қол қойдым. Сүзгіш түкірдім.',
                               'Сапарға қол қойдым. Снести қылдым.') >= voice_asr.MIN_AGREEMENT
    for invented in (('Машина бос, 836, Тамерлановский шоссе.',
                      '44-тен бар ғой, Орталық жаққа шығайын деп едім. 44-тен Сарыарқаға дейін, Орталыққа қарай.'),
                     ('3 адам бар ма?', 'Маған екі адамдық жер бар, Астана.'),
                     ('Ало, брат, 12-ші микрорайонға заказ бар ма? Мен осы жерде тұрмын, боспын.',
                      'Саламатсыз ба! Жетісайға 1 машин керек еді, қашан болады?')):
        assert voice_asr.agreement(*invented) < voice_asr.MIN_AGREEMENT


def test_overlong_recording_is_skipped_before_any_paid_request():
    frame = struct.pack('>I', 0xFFF31800) + b'\x00' * 32   # MPEG-2, 8 кбит/с, 16 кГц: 36 байт, 0,036 с
    assert voice_asr.mp3_duration(frame * 1000) == pytest.approx(36, abs=0.01)
    raw = frame * int((voice_asr.MAX_SECONDS + 5) / 0.036)
    with pytest.raises(VoiceError) as error:
        voice_asr.transcribe(raw, generate=lambda m, b: pytest.fail('paid request for a skipped file'))
    assert error.value.kind == 'skipped'


class Response:
    def __init__(self, status, body=b'', headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class Session:
    def __init__(self, responses):
        self.responses, self.urls = list(responses), []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        self.urls.append(url)
        return self.responses.pop(0)


def test_download_only_from_wazzup_store_and_redirects_are_checked():
    good = 'https://store.wazzup24.com/abc'
    session = Session([Response(302, headers={'Location': 'https://store.wazzup24.com/def'}),
                       Response(200, mp3(1), {'Content-Type': 'audio/mpeg'})])
    raw, content_type = voice_asr.download(good, session_factory=lambda: session)
    assert content_type == 'audio/mpeg' and raw.startswith(b'ID3')
    hop = Session([Response(302, headers={'Location': 'http://169.254.169.254/latest'})])
    with pytest.raises(VoiceError) as error:
        voice_asr.download(good, session_factory=lambda: hop)
    assert error.value.kind == 'skipped' and hop.urls == [good]
    for url in ('http://store.wazzup24.com/a', 'https://store.wazzup24.com.evil.kz/a',
                'https://user:pw@store.wazzup24.com/a'):
        assert not voice_asr.allowed_url(url)


@pytest.mark.parametrize('status, kind', [(404, 'unavailable'), (410, 'unavailable'), (503, 'retry')])
def test_download_errors_say_whether_to_try_again(status, kind):
    with pytest.raises(VoiceError) as error:
        voice_asr.download('https://store.wazzup24.com/x', session_factory=lambda: Session([Response(status)]))
    assert error.value.kind == kind


def test_download_stops_at_the_size_limit():
    big = Session([Response(200, b'\x00' * (voice_asr.MAX_FILE + 1))])
    with pytest.raises(VoiceError) as error:
        voice_asr.download('https://store.wazzup24.com/x', session_factory=lambda: big)
    assert error.value.kind == 'skipped'


def test_process_turns_every_outcome_into_final_or_retry():
    item = {'content_uri': 'https://store.wazzup24.com/x'}
    assert voice.process({'content_uri': None})[1]['status'] == 'unavailable'
    kind, result = voice.process(item, download=lambda url: (mp3(3), 'audio/mpeg'),
                                 transcribe=lambda raw, ct: {'status': 'ready', 'text': 'Алло', 'model': 'm'})
    assert kind == 'final' and result['audio_bytes'] == len(mp3(3))

    def gone(url):
        raise VoiceError('Wazzup не отдаёт файл (HTTP 404)', 'unavailable')
    assert voice.process(item, download=gone)[1]['status'] == 'unavailable'

    def broken(url):
        raise ConnectionError('https://store.wazzup24.com/secret-path')
    kind, error = voice.process(item, download=broken)
    assert kind == 'retry' and 'secret-path' not in str(error)


def test_webhook_wakes_the_worker_only_for_voice():
    assert voice.payload_has_audio({'messages': [{'type': 'text'}, {'type': 'audio'}]})
    assert not voice.payload_has_audio({'messages': [{'type': 'image'}], 'statuses': []})
    assert not voice.payload_has_audio({'messages': 'oops'})


def test_a_person_queues_a_bounded_number_of_old_voice_messages_an_hour():
    voice._queued_by_user.clear()
    assert voice._allow_queue(7, 250, now=0) == 250
    assert voice._allow_queue(7, 100, now=10) == voice.USER_HOURLY_LIMIT - 250
    assert voice._allow_queue(7, 5, now=20) == 0
    assert voice._allow_queue(7, 5, now=3700) == 5
    assert voice._allow_queue(8, 5, now=20) == 5


def test_a_recording_of_unknown_length_is_heard_twice():
    wav = b'RIFF' + b'\x00' * 400
    result = voice_asr.transcribe(wav, 'audio/wav', generate=listens('Қай жерден аламыз?', 'Машина номері 121'))
    assert result['status'] == 'no_speech' and result['error'] == 'два прослушивания не совпали'
    assert result['duration'] == pytest.approx(10)   # 250 audio tokens at 25 a second


def test_request_limit_grows_with_the_recording():
    assert voice_asr.request_seconds(4) == pytest.approx(22)
    assert voice_asr.request_seconds(80) == pytest.approx(60)
    assert voice_asr.request_seconds(15 * 60) == voice_asr.MAX_REQUEST_SECONDS
    assert voice.LEASE_SECONDS > 30 + 2 * (2 * voice_asr.MAX_REQUEST_SECONDS + 8)


def test_a_model_without_reasoning_switch_is_asked_once_more_without_it(monkeypatch):
    from call_qa import providers
    calls = []

    def vertex_generate(model, body, **kwargs):
        calls.append(dict(body['generationConfig']))
        if 'thinkingConfig' in body['generationConfig'] or len(calls) > 2:
            raise providers.VertexError(400, 'thinking_budget is not supported')
        raise providers.VertexError(400, 'thinking is still mentioned')
    monkeypatch.setattr(providers, 'vertex_generate', vertex_generate)
    with pytest.raises(VoiceError):
        voice_asr._generate('gemini-x', voice_asr.request_body(mp3(1), 'audio/mpeg'))
    assert len(calls) == 2 and 'thinkingConfig' not in calls[1]


def test_control_characters_never_reach_the_database():
    escaped = voice_asr.parse_answer(answer(raw='{"speech": true, "language": "ru", "text": "Алло\\u0000 где\\u0007 машина"}'))
    raw = voice_asr.parse_answer(answer(raw='{"speech": true, "language": "ru", "text": "Алло\x00 где\x07 машина"}'))
    assert escaped['text'] == raw['text'] == 'Алло где машина'


def test_a_long_recording_of_unknown_length_is_heard_once():
    wav = b'RIFF' + b'\x00' * 400
    generate = listens('Мен жолдамын, қазір жетемін.')
    result = voice_asr.transcribe(wav, 'audio/wav', generate=lambda m, b: dict(
        generate(m, b), usageMetadata={'promptTokensDetails': [{'modality': 'AUDIO', 'tokenCount': 25 * 60}]}))
    assert result['status'] == 'ready' and len(generate.bodies) == 1
    assert result['duration'] == pytest.approx(60)


def test_agreement_of_long_answers_is_bounded_in_time():
    import time
    long_a = ' '.join(['сәлеметсіз бе заказ қашан келеді'] * 400)
    long_b = ' '.join(['сәлеметсіз бе заказ қашан келед'] * 400)
    started = time.perf_counter()
    assert voice_asr.agreement(long_a, long_b) > 0.9
    assert time.perf_counter() - started < 3
