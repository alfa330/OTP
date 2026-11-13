# -*- coding: utf-8 -*-
"""«Дресс-код» (#418): разбор ответа ИИ и проход по цепочке моделей — без сети и базы.

Главное правило ТЗ п. 4 — «при сомнении — ручная проверка»: любой ответ модели,
который противоречит сам себе или не укладывается в закрытый набор значений,
становится «не удалось определить», а не «соответствует» или «нарушение».
"""

import base64
import json
import unittest
from unittest import mock

from dress_code import AI_OK, AI_UNCLEAR, AI_VIOLATION, QUALITY_HINTS, ai
from dress_code.rules import DEFAULT_RULES, FIXED_LIMITS, clean_body


def _raw(**over):
    base = {'quality': 'ok', 'verdict': 'ok', 'visible': ['белая рубашка'], 'violations': [], 'remarks': []}
    base.update(over)
    return base


class NormalizeTests(unittest.TestCase):
    def test_clean_ok(self):
        result = ai.normalize(_raw())
        self.assertEqual((result['quality'], result['verdict']), ('ok', AI_OK))
        self.assertEqual(result['visible'], ['белая рубашка'])

    def test_violation_with_sign(self):
        result = ai.normalize(_raw(verdict='violation', violations=[{'sign': 'обычное худи', 'rule': 'п. 1'}]))
        self.assertEqual(result['verdict'], AI_VIOLATION)
        self.assertEqual(result['violations'], [{'sign': 'обычное худи', 'rule': 'п. 1'}])

    def test_violation_without_sign_goes_to_people(self):
        result = ai.normalize(_raw(verdict='violation', violations=[]))
        self.assertEqual(result['verdict'], AI_UNCLEAR)
        self.assertTrue(result['note'])

    def test_ok_with_violations_goes_to_people_and_keeps_signs(self):
        result = ai.normalize(_raw(verdict='ok', violations=[{'sign': 'майка'}]))
        self.assertEqual(result['verdict'], AI_UNCLEAR)
        self.assertEqual(result['violations'][0]['sign'], 'майка')

    def test_unknown_verdict_goes_to_people(self):
        self.assertEqual(ai.normalize(_raw(verdict='maybe'))['verdict'], AI_UNCLEAR)

    def test_bad_frame_is_not_a_verdict(self):
        result = ai.normalize(_raw(quality='too_dark', verdict='violation', violations=[{'sign': 'майка'}]))
        self.assertEqual(result['quality'], 'too_dark')
        self.assertEqual(result['verdict'], AI_UNCLEAR)
        self.assertEqual(result['violations'], [])
        self.assertEqual(result['note'], QUALITY_HINTS['too_dark'])

    def test_unknown_quality_is_not_a_good_frame_verdict(self):
        result = ai.normalize(_raw(quality='sideways', verdict='ok'))
        self.assertEqual(result['verdict'], AI_UNCLEAR)
        self.assertEqual(result['quality'], 'ok')

    def test_garbage_answer(self):
        for raw in (None, [], 'текст', 42):
            self.assertEqual(ai.normalize(raw)['verdict'], AI_UNCLEAR)

    def test_lists_are_trimmed_and_deduplicated(self):
        result = ai.normalize(_raw(visible=['  пиджак  ', 'пиджак', '', None] + ['x%d' % i for i in range(20)],
                                   remarks='не список'))
        self.assertEqual(result['visible'][0], 'пиджак')
        self.assertEqual(len(result['visible']), ai.LIST_MAX)
        self.assertEqual(result['remarks'], [])

    def test_string_violation_items_are_accepted(self):
        result = ai.normalize(_raw(verdict='violation', violations=['топ на бретелях']))
        self.assertEqual(result['violations'], [{'sign': 'топ на бретелях', 'rule': ''}])


class _Resp:
    pass


def _answer(payload):
    return 200, {'candidates': [{'content': {'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}}]}


def _load_ai_service():
    """ai_feedback.service тянет database (time.tzset — только Linux, плюс пул к базе):
    подменяем зависимость заглушкой, как в test_dial_list."""
    import importlib
    import sys
    import types
    if 'ai_feedback.service' in sys.modules:
        return sys.modules['ai_feedback.service']
    stub = types.ModuleType('database')
    stub.db = mock.MagicMock()
    stub.IT_TICKET_CATALOG = {}
    with mock.patch.dict(sys.modules, {'database': stub}):
        module = importlib.import_module('ai_feedback.service')
    sys.modules['ai_feedback.service'] = module
    import ai_feedback
    ai_feedback.service = module
    return module


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict('os.environ', {'GOOGLE_APPLICATION_CREDENTIALS_CONTENT': '{}',
                                                  'DRESS_CODE_MODEL_CHAIN': 'm-first,m-second'})
        self.env.start()
        feedback = _load_ai_service()
        self.endpoint = mock.patch.object(feedback, 'gemini_endpoint',
                                          side_effect=lambda model: ('https://x/%s' % model, {}))
        self.endpoint.start()

    def tearDown(self):
        self.endpoint.stop()
        self.env.stop()

    def test_overloaded_model_falls_through_to_the_next(self):
        calls = []

        def post(url, headers, payload):
            calls.append(url)
            if url.endswith('m-first'):
                return 429, {}
            return _answer(_raw())

        result = ai.analyze(b'\xff\xd8\xffjpeg', 'правила', post=post)
        self.assertEqual(result['model'], 'm-second')
        self.assertEqual(len(calls), 2)

    def test_all_models_down_is_an_error(self):
        with self.assertRaises(ai.DressCodeAIError):
            ai.analyze(b'x', 'правила', post=lambda *a: (503, {}))

    def test_non_json_answer_tries_next_model(self):
        replies = iter([(200, {'candidates': [{'content': {'parts': [{'text': 'не json'}]}}]}), _answer(_raw())])
        result = ai.analyze(b'x', 'правила', post=lambda *a: next(replies))
        self.assertEqual(result['verdict'], AI_OK)

    def test_payload_carries_photo_rules_and_limits(self):
        seen = {}

        def post(url, headers, payload):
            seen['payload'] = payload
            return _answer(_raw())

        ai.analyze(b'JPEGBYTES', 'Мои правила: без худи', post=post)
        parts = seen['payload']['contents'][0]['parts']
        self.assertIn('Мои правила: без худи', parts[0]['text'])
        self.assertIn(FIXED_LIMITS.splitlines()[0], parts[0]['text'])
        self.assertEqual(base64.b64decode(parts[1]['inlineData']['data']), b'JPEGBYTES')
        self.assertEqual(seen['payload']['generationConfig']['responseMimeType'], 'application/json')

    def test_spent_budget_stops_the_chain(self):
        with self.assertRaises(ai.DressCodeAIError):
            ai.analyze(b'x', 'правила', post=lambda *a: (429, {}), budget_sec=-1)


class RulesTests(unittest.TestCase):
    def test_default_rules_name_the_branded_hoodie_exception(self):
        self.assertIn('фирменные худи', DEFAULT_RULES)

    def test_fixed_limits_forbid_identification_and_religious_headwear_violation(self):
        self.assertIn('Не определяй личность', FIXED_LIMITS)
        self.assertIn('религиозным', FIXED_LIMITS)

    def test_body_validation(self):
        with self.assertRaises(ValueError):
            clean_body('   ')
        with self.assertRaises(ValueError):
            clean_body('x' * 7000)
        self.assertEqual(clean_body(' правила \r\n'), 'правила')


if __name__ == '__main__':
    unittest.main()
