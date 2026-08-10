# -*- coding: utf-8 -*-
"""Слой ответа помощника и цепочка провайдеров. Чистые тесты: ни базы, ни сети.

Закрепляются три вещи, которые дались замерами и легко «упростятся» обратно.

1. Гейт уточнения живёт в КОДЕ. Замер: на «что с машиной» обе проверенные модели
   вместо вопроса выдали ответ про мойку кузова — правило в промпте не сработало
   ни у одной. Поэтому неоднозначность решается до вызова модели.
2. Цитату извлекает СЕРВЕР, а модель называет только номера фрагментов. Так
   стало после проверки на проде: сверка модельной цитаты срабатывала через раз и
   выбрасывала ВЕРНЫЕ ответы — на «Офис Астана» кусок с адресом был найден, но
   модель процитировала строку-метку. От выдумки защищает другая, устойчивая
   проверка: числа ответа обязаны встречаться в переданных фрагментах.
3. Пустой ответ провайдера — ошибка, а не результат. Модели с рассуждениями
   возвращают HTTP 200 с пустым content при finish_reason='length', а авторутер
   OpenRouter однажды вернул строку «User Safety: safe» вместо текста.
"""

import unittest

from wiki.ai import answer as ai_answer
from wiki.ai import providers as ai_providers


def chunk(chunk_id=1, article_id=10, text='Минимальный срок аренды — 14 дней.',
          similarity=0.9, found_by=(1,), heading='Аренда > Условия',
          title='Аренда транспорта', requires_ack=False):
    return {'chunk_id': chunk_id, 'article_id': article_id, 'text': text,
            'similarity': similarity, 'found_by': list(found_by),
            'heading_path': heading, 'title': title, 'slug': 'rent',
            'requires_ack': requires_ack, 'chunk_idx': 0}


class UsableChunksTest(unittest.TestCase):
    def test_floor_applies_to_vector_hits(self):
        self.assertEqual([], ai_answer.usable_chunks([chunk(similarity=0.5)]))
        self.assertEqual(1, len(ai_answer.usable_chunks([chunk(similarity=0.73)])))

    def test_lexical_hit_bypasses_floor(self):
        """Точный термин сам себе доказательство — близости у него может не быть."""
        rows = ai_answer.usable_chunks([chunk(similarity=None, found_by=(0,))])
        self.assertEqual(1, len(rows))


class ClarifyGateTest(unittest.TestCase):
    def test_long_question_never_clarifies(self):
        rows = [chunk(similarity=0.70, article_id=10),
                chunk(chunk_id=2, similarity=0.70, article_id=11)]
        need, _ = ai_answer.should_clarify(
            'сколько стоит арендовать машину на неделю в Алматы', rows)
        self.assertFalse(need)

    def test_short_and_confident_does_not_clarify(self):
        need, _ = ai_answer.should_clarify('термопакет', [chunk(similarity=0.91)])
        self.assertFalse(need)

    def test_short_low_similarity_single_article_does_not_clarify(self):
        rows = [chunk(similarity=0.70), chunk(chunk_id=2, similarity=0.69)]
        need, _ = ai_answer.should_clarify('что с машиной', rows)
        self.assertFalse(need)

    def test_short_low_similarity_across_articles_clarifies(self):
        rows = [chunk(similarity=0.72, article_id=10),
                chunk(chunk_id=2, similarity=0.71, article_id=11)]
        need, reason = ai_answer.should_clarify('что с машиной', rows)
        self.assertTrue(need)
        self.assertIn('близость', reason)


class SourcesTest(unittest.TestCase):
    def test_split_sources_takes_numbers(self):
        body, cited = ai_answer.split_sources(
            'Минимальный срок — 14 дней.\nИСТОЧНИКИ: [1] [3]')
        self.assertEqual('Минимальный срок — 14 дней.', body)
        self.assertEqual([1, 3], cited)

    def test_split_without_block(self):
        body, cited = ai_answer.split_sources('Просто ответ')
        self.assertEqual('Просто ответ', body)
        self.assertEqual([], cited)

    def test_excerpt_comes_from_chunk_not_from_model(self):
        text = ('Город: Астана; Адрес: Проспект Сарыарка, 31\n'
                'Город: Алматы; Адрес: Жамбыла, 172')
        excerpt = ai_answer.pick_excerpt(text, 'Офис в Астане на Сарыарка 31')
        self.assertIn('Астана', excerpt)
        self.assertIn(excerpt.rstrip('…'), text)

    def test_excerpt_falls_back_to_first_line(self):
        self.assertEqual('Единственная строка',
                         ai_answer.pick_excerpt('Единственная строка', 'ничего общего'))

    def test_sources_from_model_numbers(self):
        sources = ai_answer.build_sources([1], [chunk()], 'срок 14 дней')
        self.assertEqual(1, len(sources))
        self.assertTrue(sources[0]['ok'])
        self.assertFalse(sources[0]['attributed'])

    def test_sources_attributed_when_model_named_none(self):
        """Модель иногда не даёт номеров — источник выводится по пересечению."""
        rows = [chunk(chunk_id=1, text='Минимальный срок аренды составляет 14 дней'),
                chunk(chunk_id=2, article_id=11, text='Термопакет выдаётся в офисе')]
        sources = ai_answer.build_sources([], rows, 'минимальный срок аренды 14 дней')
        self.assertTrue(sources)
        self.assertEqual(1, sources[0]['chunk_id'])
        self.assertTrue(sources[0]['attributed'])

    def test_bad_fragment_number_is_ignored(self):
        sources = ai_answer.build_sources([7], [chunk()], 'срок аренды 14 дней')
        for source in sources:
            self.assertLessEqual(source['number'], 1)

    def test_numbers_must_be_grounded(self):
        rows = [chunk(text='Минимальный срок аренды — 14 дней.')]
        self.assertEqual([], ai_answer.ungrounded_numbers('Срок 14 дней', rows))
        self.assertTrue(ai_answer.ungrounded_numbers('Залог 250000 тенге', rows))

    def test_short_numbers_are_not_checked(self):
        """Одиночные цифры — нумерация списка, а не факты."""
        rows = [chunk(text='Порядок действий описан ниже.')]
        self.assertEqual([], ai_answer.ungrounded_numbers(
            '1. Позвонить 2. Уточнить', rows))

    def test_number_from_question_is_not_invented(self):
        rows = [chunk(text='Аренда возможна.')]
        self.assertEqual([], ai_answer.ungrounded_numbers(
            'Для 2026 года условия те же', rows, 'что будет в 2026 году'))

    def test_phone_digits_compared_without_punctuation(self):
        """Модель переставляет пробелы в номере — это не выдумка.

        Сравниваются только цифры, поэтому «+7 700 000 01 10» из статьи и
        «+77000000110» в ответе — одно и то же число.
        """
        rows = [chunk(text='Номер офиса: +7 700 000 01 10')]
        self.assertEqual([], ai_answer.ungrounded_numbers(
            'Звоните по номеру +77000000110', rows))

    def test_wrong_phone_is_caught(self):
        rows = [chunk(text='Номер офиса: +7 700 000 01 10')]
        self.assertTrue(ai_answer.ungrounded_numbers(
            'Звоните по номеру +77012345678', rows))


class PromptTest(unittest.TestCase):
    def test_body_is_separated_from_label(self):
        prompt = ai_answer.build_user_prompt('вопрос', [chunk()])
        self.assertIn('ТЕКСТ:', prompt)
        self.assertIn('Статья «Аренда транспорта», раздел «Аренда > Условия»', prompt)

    def test_prompt_asks_for_numbers_only(self):
        """Дословную цитату у модели больше не просим — она её не удерживает."""
        self.assertIn('Только номера', ai_answer.SYSTEM_PROMPT)
        self.assertNotIn('дословная цитата', ai_answer.SYSTEM_PROMPT)

    def test_language_rule_permits_translating_context(self):
        """Без явного разрешения перевода модель отвечала по-русски на казахский."""
        self.assertIn('переводи их содержание', ai_answer.SYSTEM_PROMPT)

    def test_prompt_demands_exact_numbers(self):
        self.assertIn('ДОСЛОВНО', ai_answer.SYSTEM_PROMPT)


class ComposeTest(unittest.TestCase):
    def test_no_chunks_means_refusal_without_calling_model(self):
        def explode(*args):
            raise AssertionError('модель звать не нужно')

        result = ai_answer.compose('вопрос', [], explode)
        self.assertEqual('no_answer', result['kind'])

    def test_clarify_does_not_call_model(self):
        def explode(*args):
            raise AssertionError('модель звать не нужно')

        # Как в боевом замере: куски пришли из ЛЕКСИКИ, поэтому порог близости
        # они обходят, и до гейта уточнения доходят все четыре. Если бы они шли
        # только от вектора, их отсёк бы STRICT_FLOOR раньше гейта.
        rows = [chunk(chunk_id=i, article_id=10 + i, similarity=0.716,
                      found_by=(0, 1)) for i in range(4)]
        result = ai_answer.compose('что с машиной', rows, explode)
        self.assertEqual('clarify', result['kind'])

    def test_answer_with_source(self):
        def fake(system, user):
            return 'Минимальный срок — 14 дней.\nИСТОЧНИКИ: [1]', {'provider': 'test'}

        result = ai_answer.compose('какой минимальный срок аренды', [chunk()], fake)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(result['sources'])
        self.assertIn('14 дней', result['sources'][0]['quote'])

    def test_answer_survives_missing_sources_block(self):
        """Забытый блок ИСТОЧНИКИ больше не повод придержать верный ответ."""
        def fake(system, user):
            return 'Минимальный срок аренды — 14 дней.', {'provider': 'test'}

        result = ai_answer.compose('какой минимальный срок аренды', [chunk()], fake)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(result['sources'])

    def test_invented_number_is_withheld(self):
        def fake(system, user):
            return 'Залог составляет 250000 тенге.\nИСТОЧНИКИ: [1]', {'provider': 'test'}

        result = ai_answer.compose('какой залог', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertIn('числа не найдены', result['meta']['rejected'])
        self.assertEqual([], result['sources'])

    def test_model_refusal_is_kept_as_refusal(self):
        def fake(system, user):
            return 'В доступных вам статьях этого нет. Спросите у СВ.', {}

        result = ai_answer.compose('сколько отпускных', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertIn('Спросите у СВ', result['text'])

    def test_refusal_carries_no_sources(self):
        """Список статей под фразой «этого нет» читается как противоречие."""
        def fake(system, user):
            return 'В доступных вам статьях этого нет. Спросите у СВ.', {}

        result = ai_answer.compose('сколько отпускных', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertEqual([], result['sources'])

    def test_ack_note_added_for_required_chunk(self):
        def fake(system, user):
            return 'Проговорить чек-лист.\nИСТОЧНИКИ: [1]', {}

        result = ai_answer.compose('что проговорить водителю',
                                   [chunk(requires_ack=True)], fake)
        self.assertTrue(result['notes'])
        self.assertIn('обязательное ознакомление', result['notes'][0])


class ProvidersTest(unittest.TestCase):
    def test_strips_think_block(self):
        self.assertEqual('Ответ', ai_providers.normalize_answer(
            '<think>рассуждения</think>Ответ'))

    def test_strips_unclosed_think(self):
        self.assertEqual('Ответ', ai_providers.normalize_answer(
            'Ответ<thinking>обрыв на лимите'))

    def test_strips_classifier_artifact(self):
        self.assertEqual('', ai_providers.normalize_answer('User Safety: safe'))

    def test_chain_falls_through_on_error_and_empty(self):
        calls = []

        def failing(model, system, user):
            calls.append(('fail', model))
            raise ai_providers.ProviderError('нет связи')

        def empty(model, system, user):
            calls.append(('empty', model))
            return {'text': '<think>только мысли</think>', 'finish': 'length'}

        def good(model, system, user):
            calls.append(('good', model))
            return {'text': 'Ответ', 'elapsed': 0.5, 'usage': {}}

        original = dict(ai_providers._ADAPTERS)
        ai_providers._ADAPTERS.update({'a': failing, 'b': empty, 'c': good})
        try:
            text, meta = ai_providers.generate(
                'sys', 'user', chain=(('a', 'm1'), ('b', 'm2'), ('c', 'm3')))
        finally:
            ai_providers._ADAPTERS.clear()
            ai_providers._ADAPTERS.update(original)

        self.assertEqual('Ответ', text)
        self.assertEqual('c', meta['provider'])
        self.assertEqual(2, len(meta['attempts']))
        self.assertEqual([('fail', 'm1'), ('empty', 'm2'), ('good', 'm3')], calls)

    def test_all_failed_raises(self):
        def failing(model, system, user):
            raise ai_providers.ProviderError('нет связи')

        original = dict(ai_providers._ADAPTERS)
        ai_providers._ADAPTERS['z'] = failing
        try:
            with self.assertRaises(ai_providers.ProviderError):
                ai_providers.generate('sys', 'user', chain=(('z', 'm'),))
        finally:
            ai_providers._ADAPTERS.clear()
            ai_providers._ADAPTERS.update(original)

    def test_excluded_models_are_not_in_default_chain(self):
        """Модели, отвергнутые замером, не должны вернуться в цепочку по умолчанию."""
        chain = ' '.join(f'{p}:{m}' for p, m in ai_providers._DEFAULT_CHAIN)
        for banned in ('qwen', 'gemma', 'openrouter/free', 'nemotron-nano',
                       'gemini-3.6-flash'):
            self.assertNotIn(banned, chain)


if __name__ == '__main__':
    unittest.main()
