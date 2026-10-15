# -*- coding: utf-8 -*-
"""Озвучка (Soniox TTS) и ИИ для текста рассылки — без сети: запросы подменены."""
import json
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from informer import ai, tts  # noqa: E402


def _wav(seconds=1.0, rate=24000):
    data = b'\x00\x00' * int(rate * seconds)
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    return (b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)


@pytest.fixture(autouse=True)
def soniox_key(monkeypatch):
    monkeypatch.setenv('SONIOX_API_KEY', 'test-key')


def test_tts_request_is_full_quality_wav_with_soniox_language_code():
    sent = {}

    def post(url, headers, body):
        sent.update(url=url, headers=headers, body=body)
        return 200, _wav()

    audio = tts.synthesize('  Сәлеметсіз   бе!  ', 'kz', 'Iris', 'fast', post=post)
    assert audio.startswith(b'RIFF')
    body = sent['body']
    assert body['language'] == 'kk'                     # часть «kz» — у Soniox код kk
    # Полное качество (владелец, 15.10.2026): 24 кГц — родная частота голоса, телефонную сводит сервер.
    assert body['sample_rate'] == 24000 and body['audio_format'] == 'wav'
    assert body['model'] == 'tts-rt-v2' and body['voice'] == 'Iris' and body['speed'] == 1.1
    assert body['text'] == 'Сәлеметсіз бе!'             # пробелы схлопнуты
    assert sent['headers']['Authorization'] == 'Bearer test-key'


def test_tts_unknown_voice_falls_back_to_default():
    sent = {}
    tts.synthesize('Привет', 'ru', 'Nobody', post=lambda u, h, b: (sent.update(b), (200, _wav()))[1])
    assert sent['voice'] == tts.DEFAULT_VOICE and sent['speed'] == 1.0


def test_tts_retries_busy_then_gives_clear_refusal():
    calls = []

    def busy(url, headers, body):
        calls.append(1)
        return 429, b'{"error_type": "rate_limited"}'

    with pytest.raises(tts.TTSError) as err:
        tts.synthesize('Привет', 'ru', post=busy, sleep=lambda s: None)
    assert len(calls) == tts.BUSY_RETRIES + 1
    assert err.value.status == 503 and 'занят' in str(err.value)

    answers = iter([(429, b''), (200, _wav())])
    assert tts.synthesize('Привет', 'ru', post=lambda u, h, b: next(answers), sleep=lambda s: None).startswith(b'RIFF')


@pytest.mark.parametrize('answer, words', [
    ((500, b'oops'), 'не озвучил'),
    ((200, b'ID3notawav'), 'не в том формате'),
    ((200, _wav(rate=8000)), 'не в том формате'),      # 8 кГц — не тот звук, что просили: глухой
])
def test_tts_bad_answers(answer, words):
    with pytest.raises(tts.TTSError) as err:
        tts.synthesize('Привет', 'ru', post=lambda u, h, b: answer)
    assert words in str(err.value)


def test_tts_input_checks(monkeypatch):
    with pytest.raises(tts.TTSError):
        tts.synthesize('Привет', 'en')
    with pytest.raises(tts.TTSError):
        tts.synthesize(' ', 'ru')
    with pytest.raises(tts.TTSError):
        tts.synthesize('x' * (tts.TEXT_MAX + 1), 'ru')
    monkeypatch.delenv('SONIOX_API_KEY')
    with pytest.raises(tts.TTSError) as err:
        tts.synthesize('Привет', 'ru')
    assert err.value.status == 503


def _gemini(payload):
    """Ответ Gemini в форме, которую разбирает dial_list.ai._ask."""
    return 200, {"candidates": [{"content": {"parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}}]}


def _load_ai_service():
    """ai_feedback.service тянет database (time.tzset — только Linux, плюс пул к базе): подменяем
    зависимость заглушкой, как в tests/test_dial_list.py (_load_ai_service)."""
    import importlib
    import types
    import unittest.mock
    if 'ai_feedback.service' in sys.modules:
        return sys.modules['ai_feedback.service']
    stub = types.ModuleType('database')
    stub.db = unittest.mock.MagicMock()
    stub.IT_TICKET_CATALOG = {}
    with unittest.mock.patch.dict(sys.modules, {'database': stub}):
        module = importlib.import_module('ai_feedback.service')
    sys.modules['ai_feedback.service'] = module
    import ai_feedback
    ai_feedback.service = module
    return module


@pytest.fixture
def gemini(monkeypatch):
    ai_service = _load_ai_service()
    monkeypatch.setattr(ai_service, 'GEMINI_API_KEY', 'k', raising=False)
    monkeypatch.setattr(ai_service, '_gemini_model_chain', lambda: ['gemini-test'])
    monkeypatch.setattr(ai_service, 'gemini_endpoint', lambda model: ('https://example.test', {}))
    prompts = []

    def post_factory(answer):
        def post(url, headers, payload):
            prompts.append(payload['contents'][0]['parts'][0]['text'])
            return _gemini(answer)
        return post
    return prompts, post_factory


def test_ai_generate_both_languages(gemini):
    prompts, post = gemini
    result = ai.generate('Акция: 7 заказов — бонус 5000 тенге', post=post({'kz': ' Сәлем! ', 'ru': 'Здравствуйте!'}))
    assert result == {'kz': 'Сәлем!', 'ru': 'Здравствуйте!'}
    assert '5000 тенге' in prompts[0] and 'казахском' in prompts[0]
    with pytest.raises(ai.ScriptAIError):
        ai.generate('коротко', post=post({'kz': 'a', 'ru': 'b'}))


def test_ai_polish_and_translate(gemini):
    prompts, post = gemini
    assert ai.polish('Здравствуйте водители', 'ru', post=post({'text': 'Здравствуйте, водители!'})) == \
        {'text': 'Здравствуйте, водители!'}
    assert ai.translate('Здравствуйте, водители!', 'ru', 'kz', post=post({'text': 'Сәлеметсіз бе!'})) == \
        {'text': 'Сәлеметсіз бе!'}
    assert 'с языка «русский» на язык «казахский»' in prompts[-1]
    with pytest.raises(ai.ScriptAIError):
        ai.translate('Текст', 'ru', 'ru', post=post({'text': 'x'}))
    with pytest.raises(ai.ScriptAIError):
        ai.polish('Текст подлиннее', 'en', post=post({'text': 'x'}))
