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

import datetime
import os
import unittest
from unittest import mock

from wiki.ai import answer as ai_answer
from wiki.ai import currency
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

    def test_exact_term_never_clarifies(self):
        """Названную своим именем вещь переспрашивать не надо.

        Замер на проде: «что за акция Лимонопад» — три слова, близость невысокая,
        попадания в двух статьях, и гейт переспрашивал. Но «Лимонопад» встречается
        ровно в одной статье: это не двусмысленность, а точный термин.
        """
        rows = [chunk(similarity=0.72, article_id=10),
                chunk(chunk_id=2, similarity=0.71, article_id=11)]
        rows[0]['strict_hit'] = True
        need, _ = ai_answer.should_clarify('что за акция Лимонопад', rows)
        self.assertFalse(need)

    def test_named_thing_in_one_article_never_clarifies(self):
        """Тот же смысл, что у strict_hit, но там, где strict_hit не срабатывает.

        strict_hit требует, чтобы в куске нашлись ВСЕ слова вопроса разом.
        «Что за акция 7 Казына?»: слово «казына» лежит ровно в одном куске на всю
        вику, а слова «акция» в этом куске НЕТ — он табличная строка. Гейт
        переспрашивал про вещь, названную своим именем (замер на проде
        22.08.2026, тот же вопрос в помощнике вики и в наставнике).
        """
        rows = [chunk(article_id=33, similarity=0.68,
                      text='№: 6; Город: Все города; Название: 7 Қазына;',
                      heading='Розыгрыши Такси.Про 2026'),
                chunk(chunk_id=2, article_id=11, similarity=0.67,
                      text='Акция «Байга»: условия участия.', heading='Акции')]
        need, _ = ai_answer.should_clarify('Что за акция 7 Казына?', rows)
        self.assertFalse(need)

    def test_named_thing_matches_across_kazakh_spelling(self):
        """В вике «Қазына», спрашивают «казына» — буквы различаться не должны."""
        rows = [chunk(article_id=33, similarity=0.68,
                      text='Название: 7 Қазына; Даты: 01.07.2026'),
                chunk(chunk_id=2, article_id=11, similarity=0.67, text='Другая акция.')]
        self.assertTrue(ai_answer.named_term('Акция «Семь казына».', rows))

    def test_common_word_is_not_a_name(self):
        """Слово, встречающееся в одной статье случайно, именем не является:
        короткие и частые слова не должны отключать гейт."""
        rows = [chunk(article_id=10, text='Минимальный срок аренды — 14 дней.'),
                chunk(chunk_id=2, article_id=11, text='Стоимость аренды в Алматы.')]
        self.assertFalse(ai_answer.named_term('что с машиной', rows))

    def test_short_low_similarity_across_articles_clarifies(self):
        rows = [chunk(similarity=0.72, article_id=10),
                chunk(chunk_id=2, similarity=0.71, article_id=11)]
        need, reason = ai_answer.should_clarify('что с машиной', rows)
        self.assertTrue(need)
        self.assertIn('близость', reason)


class FollowUpQueryTest(unittest.TestCase):
    """Обогащение запроса продолжения. Нить рвалась на втором шаге."""

    HISTORY = [
        {'role': 'user', 'text': 'что за акция Лимонопад'},
        {'role': 'assistant', 'text': 'Лимонопад — розыгрыш…'},
        {'role': 'user', 'text': 'по подробнее, как участвовать'},
        {'role': 'assistant', 'text': 'Участвуют все парки…'},
    ]

    def test_topic_survives_two_hops(self):
        """Главный случай из живого диалога: на третьей реплике тема ещё нужна."""
        query = ai_answer.enrich_query('отправь ссылку для регистрации', self.HISTORY)
        self.assertIn('Лимонопад', query)
        self.assertIn('отправь ссылку для регистрации', query)

    def test_long_question_is_left_alone(self):
        question = 'какие акции для новых водителей действуют в августе в Алматы'
        self.assertEqual(question, ai_answer.enrich_query(question, self.HISTORY))

    def test_five_word_followup_is_enriched(self):
        """«отправь мне ссылку для регистрации» — пять слов и всё ещё отсылка."""
        query = ai_answer.enrich_query('отправь мне ссылку для регистрации', self.HISTORY)
        self.assertNotEqual('отправь мне ссылку для регистрации', query)

    def test_first_question_has_nothing_to_enrich(self):
        self.assertEqual('термопакет', ai_answer.enrich_query('термопакет', []))

    def test_assistant_replies_are_not_glued_in(self):
        """Подмешиваем СВОИ реплики человека, а не ответы помощника.

        Ответ помощника длинный и уводит поиск в свою же формулировку.
        """
        query = ai_answer.enrich_query('а ссылка?', self.HISTORY)
        self.assertNotIn('розыгрыш', query)


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

    def test_inline_marker_is_stripped(self):
        """Маркер бывает на одной строке с текстом, а не отдельным блоком.

        Замер: gemini-3-flash закончил отказ фразой «…обращаться к руководителю
        отдела. ИСТОЧНИКИ: [1]». Привязка к началу строки такое не ловила, и
        служебный маркер уезжал оператору в ответ.
        """
        body, cited = ai_answer.split_sources(
            'Обратитесь к руководителю отдела. ИСТОЧНИКИ: [1]')
        self.assertEqual('Обратитесь к руководителю отдела.', body)
        self.assertEqual([1], cited)

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

    def test_digits_inside_a_url_are_not_facts(self):
        """Замер на проде: обломок идентификатора формы Google выдавался за выдумку.

        «…1FAIpQLSeO-wSvqAZ992Z_LWPbo…» давал предупреждение «числа, которых нет
        в документе: 992». Ложное предупреждение обесценивает все остальные.
        """
        rows = [chunk(text='Заполните заявку по форме.')]
        self.assertEqual([], ai_answer.ungrounded_numbers(
            'Заявка: https://docs.google.com/forms/d/e/1FAIpQLSeO-wSvqAZ992Z_LWPbo/viewform',
            rows))

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

    def test_prompt_puts_helping_first(self):
        """Требование владельца 22.08.2026: помощники обязаны СТАРАТЬСЯ помочь.

        Раньше правило звучало «если во фрагментах ответа нет — ответь одной
        фразой и не пытайся ответить приблизительно», и модель отказывала при
        первой заминке. Отказ вместо ответа, который во фрагментах есть, стоит
        доверия ко всему помощнику.
        """
        self.assertIn('ТВОЯ ЗАДАЧА — ПОМОЧЬ', ai_answer.SYSTEM_PROMPT)
        self.assertIn('ПРОЧИТАЙ ФРАГМЕНТЫ ЦЕЛИКОМ', ai_answer.SYSTEM_PROMPT)
        self.assertIn('дай то, что есть', ai_answer.SYSTEM_PROMPT)
        # …но не ценой выдумки: запрет и дословность чисел остаются.
        self.assertIn('Ничего не додумывай', ai_answer.SYSTEM_PROMPT)
        self.assertIn('ДОСЛОВНО', ai_answer.SYSTEM_PROMPT)

    def test_prompt_warns_about_spelling_of_names(self):
        """Из-за расхождения написания помощник отказывал: в вике «7 Қазына»,
        спрашивают «Жетіқазына» или «Семь казына»."""
        self.assertIn('НАЗВАНИЕ В ВОПРОСЕ МОЖЕТ ОТЛИЧАТЬСЯ', ai_answer.SYSTEM_PROMPT)

    def test_language_rule_permits_translating_context(self):
        """Без явного разрешения перевода модель отвечала по-русски на казахский."""
        self.assertIn('переводи их содержание', ai_answer.SYSTEM_PROMPT)

    def test_prompt_demands_exact_numbers(self):
        self.assertIn('ДОСЛОВНО', ai_answer.SYSTEM_PROMPT)


class DetectLanguageTest(unittest.TestCase):
    """Язык вопроса определяет КОД, поэтому ошибка здесь стоит целого ответа.

    Раньше хватало одной казахской буквы где угодно в строке. Русский вопрос
    «Расскажи всё про акцию «Жетіқазына»» из-за названия акции объявлялся
    казахским, и помощник честно отвечал по-казахски человеку, спросившему
    по-русски (замер на проде 22.08.2026). Название на другом языке — это ИМЯ,
    а не язык вопроса.
    """

    def test_kazakh_name_does_not_make_a_russian_question_kazakh(self):
        for question in ('Расскажи всё про акцию «Жетіқазына».',
                         'Что за акция 7 Қазына?',
                         'Расскажи про Қазына',
                         'Какие документы нужны водителю?'):
            self.assertEqual('ru', ai_answer.detect_language(question), question)

    def test_kazakh_question_is_still_kazakh(self):
        for question in ('Сәлеметсіз бе, жүргізушіге тіркелу үшін қандай құжаттар керек?',
                         'Сәлеметсіз бе',
                         'Мен қалай тіркелемін?',
                         'Тіркелу үшін не керек?',
                         # без специфических букв — ловят маркеры
                         'Бонус бар ма?'):
            self.assertEqual('kk', ai_answer.detect_language(question), question)

    def test_short_reply_takes_the_language_of_the_conversation(self):
        """«Жеті қазына» посреди русского разговора — уточнение про акцию, а не
        переход на казахский. В голосовом режиме такие реплики — норма."""
        russian = [{'role': 'user', 'text': 'Расскажи всё про акцию Жетіқазына'}]
        kazakh = [{'role': 'user', 'text': 'Сәлеметсіз бе, қандай құжаттар керек?'}]
        self.assertEqual('ru', ai_answer.detect_language('Жеті қазына.', russian))
        self.assertEqual('kk', ai_answer.detect_language('Жеті қазына.', kazakh))
        # Без разговора судим по самой реплике.
        self.assertEqual('kk', ai_answer.detect_language('Жеті қазына.'))

    def test_long_question_ignores_the_conversation(self):
        """Развёрнутый вопрос свой язык показывает сам — историю не спрашиваем."""
        kazakh = [{'role': 'user', 'text': 'Сәлеметсіз бе, қандай құжаттар керек?'}]
        self.assertEqual(
            'ru', ai_answer.detect_language('Какие документы нужны водителю?', kazakh))


class ComposeTest(unittest.TestCase):
    def test_no_chunks_means_refusal_without_calling_model(self):
        def explode(*args, **kwargs):
            raise AssertionError('модель звать не нужно')

        result = ai_answer.compose('вопрос', [], explode)
        self.assertEqual('no_answer', result['kind'])

    def test_clarify_does_not_call_model(self):
        def explode(*args, **kwargs):
            raise AssertionError('модель звать не нужно')

        # Как в боевом замере: куски пришли из ЛЕКСИКИ, поэтому порог близости
        # они обходят, и до гейта уточнения доходят все четыре. Если бы они шли
        # только от вектора, их отсёк бы STRICT_FLOOR раньше гейта.
        rows = [chunk(chunk_id=i, article_id=10 + i, similarity=0.716,
                      found_by=(0, 1)) for i in range(4)]
        result = ai_answer.compose('что с машиной', rows, explode)
        self.assertEqual('clarify', result['kind'])

    def test_answer_with_source(self):
        def fake(system, user, history=()):
            return 'Минимальный срок — 14 дней.\nИСТОЧНИКИ: [1]', {'provider': 'test'}

        result = ai_answer.compose('какой минимальный срок аренды', [chunk()], fake)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(result['sources'])
        self.assertIn('14 дней', result['sources'][0]['quote'])

    def test_answer_survives_missing_sources_block(self):
        """Забытый блок ИСТОЧНИКИ больше не повод придержать верный ответ."""
        def fake(system, user, history=()):
            return 'Минимальный срок аренды — 14 дней.', {'provider': 'test'}

        result = ai_answer.compose('какой минимальный срок аренды', [chunk()], fake)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(result['sources'])

    def test_invented_number_is_withheld(self):
        def fake(system, user, history=()):
            return 'Залог составляет 250000 тенге.\nИСТОЧНИКИ: [1]', {'provider': 'test'}

        result = ai_answer.compose('какой залог', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertIn('числа не найдены', result['meta']['rejected'])
        self.assertEqual([], result['sources'])

    def test_model_refusal_is_kept_as_refusal(self):
        def fake(system, user, history=()):
            return 'В доступных вам статьях этого нет. Спросите у СВ.', {}

        result = ai_answer.compose('сколько отпускных', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertIn('Спросите у СВ', result['text'])

    def test_partial_answer_is_not_demoted_to_a_refusal(self):
        """Ответ вида «точного нет, но есть вот это» — это ОТВЕТ.

        Признаком отказа служило одно вхождение фразы «в доступных вам статьях
        этого нет» где угодно в тексте. Полезный ответ из-за этого объявлялся
        отказом и ЛИШАЛСЯ ИСТОЧНИКОВ — помощник наказывал себя ровно за то
        поведение, которого от него и хотят.
        """
        def fake(system, user, history=()):
            return ('В доступных статьях нет общей инструкции на этот случай. '
                    'Известно, что минимальный срок аренды — 14 дней.\nИСТОЧНИКИ: [1]'), {}

        result = ai_answer.compose('что делать с арендой', [chunk()], fake)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(result['sources'], 'у ответа с оговоркой источники остаются')

    def test_refusal_is_recognised_in_paraphrase(self):
        """Модели перефразируют отказ: «в доступных мне материалах нет…»."""
        for text in ('В доступных статьях нет информации о сумме выплат. '
                     'Обратитесь к руководителю отдела.',
                     'В доступных мне материалах нет прогноза погоды. Я могу подсказать '
                     'только данные из базы знаний таксопарка про офисы и аренду.'):
            self.assertTrue(ai_answer.is_refusal(text), text[:50])

    def test_refusal_carries_no_sources(self):
        """Список статей под фразой «этого нет» читается как противоречие."""
        def fake(system, user, history=()):
            return 'В доступных вам статьях этого нет. Спросите у СВ.', {}

        result = ai_answer.compose('сколько отпускных', [chunk()], fake)
        self.assertEqual('no_answer', result['kind'])
        self.assertEqual([], result['sources'])

    def test_ack_note_added_for_required_chunk(self):
        def fake(system, user, history=()):
            return 'Проговорить чек-лист.\nИСТОЧНИКИ: [1]', {}

        result = ai_answer.compose('что проговорить водителю',
                                   [chunk(requires_ack=True)], fake)
        self.assertTrue(result['notes'])
        self.assertIn('обязательное ознакомление', result['notes'][0])


class DocumentProviderTest(unittest.TestCase):
    """Файл целиком: отдельная цепочка, потому что текстовые провайдеры его не примут."""

    def test_only_file_capable_providers_are_used(self):
        chain = (('groq', 'llama'), ('cloudflare', 'mistral'), ('vertex', 'gemini-3-flash'),
                 ('gemini', 'gemini-3.5-flash-lite'))
        self.assertEqual((('vertex', 'gemini-3-flash'),
                          ('gemini', 'gemini-3.5-flash-lite')),
                         ai_providers.file_capable_chain(chain))

    def test_unsupported_mime_fails_fast(self):
        """DOCX моделью не читается — отказ должен быть внятным, а не 400 от API."""
        with self.assertRaises(ai_providers.ProviderError) as caught:
            ai_providers.generate_document(
                'sys', 'user', blob=b'PK', mime='application/vnd.openxmlformats-'
                'officedocument.wordprocessingml.document')
        self.assertFalse(caught.exception.retryable)

    def test_text_only_chain_fails_fast(self):
        with self.assertRaises(ai_providers.ProviderError) as caught:
            ai_providers.generate_document('sys', 'user', blob=b'%PDF',
                                           mime='application/pdf',
                                           chain=(('groq', 'llama'),))
        self.assertIn('умеющего читать файл', str(caught.exception))


class ProvidersTest(unittest.TestCase):
    def test_strips_think_block(self):
        self.assertEqual('Ответ', ai_providers.normalize_answer(
            '<think>рассуждения</think>Ответ'))

    def test_strips_unclosed_think(self):
        self.assertEqual('Ответ', ai_providers.normalize_answer(
            'Ответ<thinking>обрыв на лимите'))

    def test_strips_classifier_artifact(self):
        self.assertEqual('', ai_providers.normalize_answer('User Safety: safe'))

    def test_markdown_is_preserved(self):
        """Разметка НЕ сглаживается: её рендерит интерфейс, включая таблицы.

        Сначала я её вырезал — рендерера в проекте не было, и звёздочки доходили
        до оператора символами. Теперь ответ рисуется через marked + DOMPurify, а
        таблица это главный формат справочных данных вики (город, цена, срок,
        парк): сглаживание разрушало бы её ровно там, где она нужнее всего.
        """
        table = ('| Город | Комиссия |\n'
                 '|---|---|\n'
                 '| Астана | 5% |')
        kept = ai_providers.normalize_answer('**Тарифы**\n' + table)
        self.assertIn('**Тарифы**', kept)
        self.assertIn('| Астана | 5% |', kept)
        self.assertIn('|---|---|', kept)

    def test_service_blocks_still_stripped(self):
        """Служебное вырезается по-прежнему — разметка тут ни при чём."""
        self.assertEqual('Ответ', ai_providers.normalize_answer(
            '<think>рассуждения</think>Ответ'))

    def test_chain_falls_through_on_error_and_empty(self):
        calls = []

        def failing(model, system, user, history=(), max_tokens=None):
            calls.append(('fail', model))
            raise ai_providers.ProviderError('нет связи')

        def empty(model, system, user, history=(), max_tokens=None):
            calls.append(('empty', model))
            return {'text': '<think>только мысли</think>', 'finish': 'length'}

        def good(model, system, user, history=(), max_tokens=None):
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
        def failing(model, system, user, history=(), max_tokens=None):
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

    def test_retired_models_do_not_come_back(self):
        """Модели, снятые вендорами, обратно в цепочку не возвращаем.

        Прогон боевыми ключами 01.09.2026: groq:llama-3.3-70b-versatile отвечает
        404 «does not exist or you do not have access to it» (в /v1/models Groq
        её больше нет), gemini:gemini-2.5-flash — 404 «no longer available to new
        users». Обе стояли в цепочке резервом и молча съедали по попытке, пока
        редактор ждал ответа.
        """
        chain = ' '.join(f'{p}:{m}' for p, m in ai_providers._DEFAULT_CHAIN)
        self.assertNotIn('llama-3.3-70b-versatile', chain)
        self.assertNotIn('gemini-2.5-flash', chain)

    def test_chain_exhaustion_is_logged_in_full(self):
        """Отказ цепочки обязан попасть в лог, и целиком.

        01.09.2026 редактор получил 503 на документе Word, а разобрать инцидент
        было нечем: log_action пишется только после успеха, обработчик превращает
        ProviderError в 503, и в логах Render по всем приметам отказа было ноль
        строк. Человеку сообщение режется (роут отдаёт detail[:300]), в лог
        уходит весь перечень попыток — иначе причина последнего звена теряется.
        """
        def failing(model, system, user, history=(), max_tokens=None):
            raise ai_providers.ProviderError('HTTP 429: Resource exhausted')

        original = dict(ai_providers._ADAPTERS)
        ai_providers._ADAPTERS['z'] = failing
        try:
            with self.assertLogs('root', level='WARNING') as caught:
                with self.assertRaises(ai_providers.ProviderError):
                    ai_providers.generate('sys', 'user', chain=(('z', 'm'),))
        finally:
            ai_providers._ADAPTERS.clear()
            ai_providers._ADAPTERS.update(original)

        written = '\n'.join(caught.output)
        self.assertIn('все провайдеры цепочки отказали', written)
        self.assertIn('Resource exhausted', written)


class ZaiProviderTest(unittest.TestCase):
    """GLM вторым в цепочке: платный резерв вместо истёкшего бесплатного.

    Почему вообще: 01.09.2026 сборка статьи из документа Word отказала целиком —
    Vertex отдал 429, а весь бесплатный резерв за три недели умер сам (404 снятых
    моделей, 429 кончившейся предоплаты, 413 по потолку токенов Groq, отсутствие
    ключей Cloudflare на проде). Разбор целиком — в шапке wiki/ai/providers.py.

    Сеть тут не нужна: подменяется _post, проверяется ровно то, что уходит в
    запрос. Каждое утверждение ниже — замер того дня, а не осторожность.
    """

    def setUp(self):
        self.sent = []

        def fake_post(url, payload, headers, params=None, timeout=None):
            self.sent.append({'url': url, 'payload': payload, 'timeout': timeout})
            return ({'choices': [{'message': {'content': 'НАЗВАНИЕ: Т\nОтвет'},
                                  'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 9107, 'completion_tokens': 5415,
                               'completion_tokens_details': {'reasoning_tokens': 0},
                               'prompt_tokens_details': {'cached_tokens': 1664}}},
                    0.5)

        self._real_post = ai_providers._post
        ai_providers._post = fake_post
        self.addCleanup(setattr, ai_providers, '_post', self._real_post)

        # Ключи обоих платных провайдеров: без них _with_keys режет цепочку, и
        # тест про порядок звеньев проверял бы не порядок, а наличие ключей.
        patch = mock.patch.dict(os.environ, {
            'ZAI_API_KEY': 'тест',
            'GOOGLE_APPLICATION_CREDENTIALS_CONTENT': '{}',
        })
        patch.start()
        self.addCleanup(patch.stop)

    def test_zai_is_second_in_chain_right_after_vertex(self):
        """Резерв обязан быть ПЛАТНЫМ и стоять сразу за Vertex.

        Бесплатные модели снимают и обнуляют без предупреждения, и узнаём мы об
        этом от редактора, у которого упала кнопка. Из всей цепочки сборку статьи
        (документ 45 КБ, 9 000 токенов вывода) вывозит только GLM — 55,3 с.
        """
        self.assertEqual(('vertex', 'gemini-3-flash-preview'),
                         ai_providers._DEFAULT_CHAIN[0])
        self.assertEqual(('zai', 'glm-5.3-flash'), ai_providers._DEFAULT_CHAIN[1])

    def test_available_chain_follows_the_key(self):
        """Новых секретов на прод завозить не нужно: ZAI_API_KEY там уже есть."""
        self.assertIn(('zai', 'glm-5.3-flash'), ai_providers.available_chain())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ZAI_API_KEY', None)
            self.assertNotIn('zai', [p for p, _ in ai_providers.available_chain()])

    def test_reasoning_effort_is_always_sent_and_low_by_default(self):
        """«Мышление» у GLM не отключается, и не поставить поле — НЕ нейтрально.

        Замер: без поля включается умолчание вендора — 23,0 с и 846 токенов
        мышления против 8,3 с и нуля на 'low'. thinking={'type':'disabled'} и
        reasoning_effort='medium' отвергаются с HTTP 400, код 1210: ступеней
        ровно три.
        """
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user')
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user', max_tokens=9000)
        for sent in self.sent:
            self.assertEqual('low', sent['payload']['reasoning_effort'])

    def test_request_carries_nothing_extra(self):
        """response_format и thinking в запросе вики — шум.

        Вика ждёт не JSON, а конверт «НАЗВАНИЕ: / КРАТКО: / СТАТЬЯ:» (его
        разбирает authoring._envelope), а json_object вендор всё равно
        игнорирует: проверено, ответ приходит конвертом в обоих случаях.
        """
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user', max_tokens=9000)
        self.assertEqual(
            ['max_tokens', 'messages', 'model', 'reasoning_effort', 'temperature'],
            sorted(self.sent[0]['payload']))

    def test_effort_does_not_leak_into_other_openai_providers(self):
        """extra_payload добавлен в общую _openai_shape — чужие запросы не трогает.

        Иначе reasoning_effort уехал бы в Groq и OpenRouter, где такого поля нет.
        """
        os.environ.setdefault('GROQ_API_KEY', 'тест')
        ai_providers._call_groq('openai/gpt-oss-20b', 'sys', 'user')
        self.assertNotIn('reasoning_effort', self.sent[0]['payload'])

    def test_timeout_is_a_floor_and_depends_on_the_task(self):
        """Общая минута убивает сборку статьи, а пять минут в чате держать нельзя.

        Замеры GLM: чат 1,3-2,8 с; сборка статьи 55,3 с на 45 КБ и 196,5 с на
        90 КБ. Порог берётся по max_tokens: сборка просит 9 000
        (authoring.MAX_OUTPUT_TOKENS), чат — 2 500. Срок вызывающего при этом
        уважается: наставник тренажёра просит 12 с и не должен получить 300.
        """
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user')
        self.assertEqual(ai_providers.TIMEOUT, self.sent[-1]['timeout'])
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user', max_tokens=9000)
        self.assertEqual(ai_providers.ZAI_TIMEOUT, self.sent[-1]['timeout'])
        ai_providers._call_zai('glm-5.3-flash', 'sys', 'user', max_tokens=9000,
                               timeout=400)
        self.assertEqual(400.0, self.sent[-1]['timeout'])

    def test_usage_is_unpacked_without_double_counting(self):
        """Токены мышления УЖЕ входят в completion_tokens — прибавлять нельзя.

        Иначе расход в журнале чатов удвоится: routes_ai пишет output_tokens
        прямо из этого поля.
        """
        result = ai_providers._call_zai('glm-5.3-flash', 'sys', 'user')
        self.assertEqual({'prompt_tokens': 9107, 'completion_tokens': 5415,
                          'thoughts_tokens': 0, 'cached_tokens': 1664},
                         result['usage'])

    def test_pdf_goes_as_a_file_block_and_pictures_as_image(self):
        """PDF в image_url не проходит — Z.ai отвечает 400 об ошибке разбора."""
        ai_providers._call_zai_file('glm-5.3-flash', 'sys', 'user',
                                    blob=b'%PDF', mime='application/pdf')
        block = self.sent[-1]['payload']['messages'][-1]['content'][0]
        self.assertEqual('file_url', block['type'])
        self.assertTrue(block['file_url']['url'].startswith('data:application/pdf;base64,'))

        ai_providers._call_zai_file('glm-5.3-flash', 'sys', 'user',
                                    blob=b'\x89PNG', mime='image/png')
        block = self.sent[-1]['payload']['messages'][-1]['content'][0]
        self.assertEqual('image_url', block['type'])

    def test_editor_chain_head_is_the_owners_call(self):
        """Первое звено выбирает владелец, и сейчас это 3-flash-preview.

        ВРЕМЕННО: 01.09.2026 он попросил вернуть preview, пока идёт замер порога.
        Числа ниже при этом в силе — на документах ~8 тыс. знаков preview
        переносит половину, и редактор увидит предупреждение о сжатии.

        16 прогонов 01.09.2026, два настоящих документа, один и тот же промпт:

          модель                        перенос (4 прогона)  блоков  сек  $/статью
          vertex:gemini-3.5-flash       0,93 0,99 1,01 1,03  4-6     10   0,0291
          vertex:gemini-3.6-flash       0,97 1,00 1,01 1,02  4       13   0,0255
          vertex:gemini-3-flash-preview 0,51 0,54 0,58 0,67  6       15   0,0067
          zai:glm-5.3-flash             0,99 0,99 0,99 1,00  2       33   0,0009

        3.5-flash единственный переносит документ целиком и при этом дважды из
        четырёх прогонов дотянулся до тех же ШЕСТИ видов блоков, что и
        gemini-3-flash-preview; GLM брал два вида блоков из девяти, и владелец
        забраковал его глазами. Открытый вопрос: preview выдаёт 4 247-5 116
        знаков независимо от размера входа, то есть на документе меньше ~4 500
        знаков сжимать ему нечего. Замер порога идёт.
        """
        self.assertEqual(('vertex', 'gemini-3-flash-preview'),
                         ai_providers.editor_chain()[0])

    def test_editor_fallbacks_go_across_vendors(self):
        """Второе звено — замена по качеству, третье — другой поставщик.

        3.5-flash вторым: он переносит документ целиком там, где preview
        пересказывает. GLM третьим — единственный не от Google, страхует
        недоступность Vertex целиком.
        """
        self.assertEqual([('vertex', 'gemini-3-flash-preview'),
                          ('vertex', 'gemini-3.5-flash'),
                          ('zai', 'glm-5.3-flash')],
                         list(ai_providers.editor_chain()))

    def test_editor_chain_drops_links_that_cannot_build_an_article(self):
        """Бесплатные звенья чата в цепочку редактора не попадают.

        Groq на сборке отдаёт 413 (потолок 8 000 токенов/мин против 9 000
        забронированных), Cloudflare отваливается по таймауту на документе. В
        цепочке редактора они были бы не резервом, а задержкой перед отказом.
        """
        providers = [p for p, _ in ai_providers.editor_chain()]
        self.assertNotIn('groq', providers)
        self.assertNotIn('cloudflare', providers)

    def test_chat_chain_is_untouched(self):
        """Чат помощника остаётся на Vertex: там он выбран замером на КАЧЕСТВЕ
        ответа, и короткие ответы сжатием не страдают."""
        self.assertEqual(('vertex', 'gemini-3-flash-preview'),
                         ai_providers.available_chain()[0])

    def test_editor_chain_is_overridable_and_key_gated(self):
        with mock.patch.dict(os.environ,
                             {'WIKI_AI_EDITOR_CHAIN': 'zai:glm-иная'}):
            self.assertEqual((('zai', 'glm-иная'),), ai_providers.editor_chain())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ZAI_API_KEY', None)
            self.assertNotIn('zai', [p for p, _ in ai_providers.editor_chain()])

    def test_article_doors_use_the_editor_chain(self):
        """Обёртки существуют затем, чтобы вызывающий не помнил про цепочку.

        Забыть про неё — значит собрать статью не той моделью и заметить это
        только по виду готовой статьи: бедное оформление или сжатый текст,
        смотря в какую сторону ошибка.
        """
        seen = []

        def adapter(model, system, user, history=(), max_tokens=None):
            seen.append(model)
            return {'text': 'Ответ', 'elapsed': 0.1, 'usage': {}}

        original = dict(ai_providers._ADAPTERS)
        ai_providers._ADAPTERS['vertex'] = adapter
        try:
            ai_providers.generate_article('sys', 'user')
        finally:
            ai_providers._ADAPTERS.clear()
            ai_providers._ADAPTERS.update(original)
        self.assertEqual(['gemini-3-flash-preview'], seen)

    def test_zai_reads_files_too(self):
        """У PDF и картинок резерва не было вовсе: цепочка файла — vertex+gemini,
        а на проде это Vertex плюс две мёртвые записи Gemini."""
        self.assertIn('zai', ai_providers._FILE_CAPABLE)
        self.assertIn(('zai', 'glm-5.3-flash'),
                      ai_providers.file_capable_chain(ai_providers._DEFAULT_CHAIN))



class HistoryTest(unittest.TestCase):
    """Диалог передаётся модели. Без этого уточняющий вопрос был тупиком.

    Замер на проде: помощник спрашивал «какой именно офис — Ipartner, Global,
    Taxi24?», а на ответ «Taxi24» отвечал «в доступных вам статьях этого нет» —
    хотя поиск по одному слову «Taxi24» отдавал «Адреса Офисов Taxi24» ПЕРВЫМ
    результатом. Не помнил именно модель: реплика приходила одна, без диалога.
    """

    def test_history_reaches_the_model(self):
        seen = {}

        def fake(system, user, history=()):
            seen['history'] = list(history)
            return 'Адрес: Проспект Сарыарка, 31.\nИСТОЧНИКИ: [1]', {}

        turns = [{'role': 'user', 'kind': 'question', 'text': 'Офис Астана'},
                 {'role': 'assistant', 'kind': 'clarify', 'text': 'Какой именно офис?'}]
        ai_answer.compose('Taxi24', [chunk(text='Адрес: Проспект Сарыарка, 31')],
                          fake, history=turns)
        self.assertEqual(2, len(seen['history']))
        self.assertEqual('Офис Астана', seen['history'][0]['text'])

    def test_clarify_can_be_forbidden(self):
        """После своего же уточнения переспрашивать нельзя — иначе круг."""
        rows = [chunk(chunk_id=i, article_id=10 + i, similarity=0.716,
                      found_by=(0, 1)) for i in range(4)]

        def fake(system, user, history=()):
            return 'Ответ по сути.\nИСТОЧНИКИ: [1]', {}

        loop = ai_answer.compose('Taxi24', rows, fake, allow_clarify=True)
        self.assertEqual('clarify', loop['kind'])
        answered = ai_answer.compose('Taxi24', rows, fake, allow_clarify=False)
        self.assertNotEqual('clarify', answered['kind'])

    def test_provider_messages_carry_roles(self):
        built = ai_providers._messages('sys', 'вопрос', [
            {'role': 'user', 'text': 'первый вопрос'},
            {'role': 'assistant', 'text': 'уточните'},
            {'role': 'assistant', 'text': '   '},          # пустое не шлём
        ])
        self.assertEqual(['system', 'user', 'assistant', 'user'],
                         [item['role'] for item in built])
        self.assertEqual('вопрос', built[-1]['content'])

    def test_provider_chain_passes_history(self):
        seen = {}

        def adapter(model, system, user, history=(), max_tokens=None):
            seen['history'] = list(history)
            return {'text': 'Ответ', 'elapsed': 0.1, 'usage': {}}

        original = dict(ai_providers._ADAPTERS)
        ai_providers._ADAPTERS['h'] = adapter
        try:
            ai_providers.generate('sys', 'user', chain=(('h', 'm'),),
                                  history=[{'role': 'user', 'text': 'раньше'}])
        finally:
            ai_providers._ADAPTERS.clear()
            ai_providers._ADAPTERS.update(original)
        self.assertEqual('раньше', seen['history'][0]['text'])



class StaleContextTest(unittest.TestCase):
    """Актуальность в ответе: подпись фрагмента, оговорка и отбор источников.

    Воспроизводится инцидент 27.08.2026: на вопрос «актуальные акции» помощник
    выдал таблицу «Актуальные акции для водителей», и строкой в ней стояла акция
    «Приведи друга» из статьи «Архивные акции TEZ» со сроком, истёкшим
    шестнадцатью месяцами раньше. Разбор целиком — в шапке wiki/ai/currency.py.
    """

    TODAY = datetime.date(2026, 8, 28)

    def archive(self, chunk_id=1):
        """Кусок 6007 боевой базы, текстом как есть."""
        return chunk(
            chunk_id=chunk_id, article_id=731, title='Архивные акции TEZ',
            heading='Акция "Приведи друга".',
            text='Акция "Приведи друга".\n'
                 'Водитель получает за него 10.000 тенге на баланс аккаунта.\n'
                 'дата запуска: 01.03.2025г дата окончания: 30.04.2025г')

    def live(self, chunk_id=1):
        return chunk(
            chunk_id=chunk_id, article_id=730,
            title='Актуальные акции и спецпредложения для водителей Tez Taxi',
            heading='Спецоффер для водителей (Туркестан)',
            text='Выполнить 50 поездок за 14 дней. Размер бонуса: 10 000 тг.')

    def test_today_is_given_to_the_model(self):
        """Без сегодняшней даты правило про сроки было бы пустым.

        В инциденте «дата окончания: 30.04.2025» была для модели просто числом
        из справочника, а правило 8 велит переносить числа дословно. Сравнить
        его оказалось не с чем.
        """
        prompt = ai_answer.build_user_prompt('вопрос', [chunk()], self.TODAY)
        self.assertTrue(prompt.startswith('СЕГОДНЯ: 28.08.2026'))

    def test_archive_fragment_is_labelled_for_the_model(self):
        marked = currency.mark_chunks([self.archive()], self.TODAY)
        prompt = ai_answer.build_user_prompt('актуальные акции', marked, self.TODAY)
        self.assertIn('АРХИВ — сведения уже не действуют', prompt)
        # Пометка стоит ПЕРЕД названием статьи: подпись читается первой, и
        # решение «брать или не брать» модель принимает до содержимого.
        self.assertLess(prompt.index('АРХИВ'), prompt.index('Архивные акции TEZ'))

    def test_clean_fragment_has_no_label(self):
        marked = currency.mark_chunks([self.live()], self.TODAY)
        prompt = ai_answer.build_user_prompt('вопрос', marked, self.TODAY)
        self.assertNotIn('⚠', prompt)

    def test_prompt_has_the_currency_rule(self):
        self.assertIn('АКТУАЛЬНОСТЬ', ai_answer.SYSTEM_PROMPT)
        self.assertIn('сведения уже не действуют', ai_answer.SYSTEM_PROMPT)

    def test_caveat_is_added_by_the_server(self):
        """Оговорку ставит СЕРВЕР, а не модель, и это не перестраховка.

        Правило про уточняющий вопрос модели игнорировали целиком — поэтому
        неоднозначность считает код (см. пункт 3 шапки модуля). У правила про
        архив цена ошибки та же: оператор пообещает водителю то, чего нет.
        """
        def fake(system, user, history=()):
            return ('Акция «Приведи друга»: 10.000 тенге за 100 заказов.\n'
                    'ИСТОЧНИКИ: [1]'), {'provider': 'test'}

        result = ai_answer.compose('была ли акция приведи друга', [self.archive()],
                                   fake, on_date=self.TODAY)
        self.assertEqual('answer', result['kind'])
        self.assertTrue(any('архивных материалов' in note
                            for note in result['notes']))
        self.assertTrue(result['sources'][0]['stale'])

    def test_clean_answer_gets_no_caveat(self):
        """Ложная тревога дороже молчания: её перестают читать вместе с настоящей."""
        def fake(system, user, history=()):
            return 'Бонус — 10 000 тг.\nИСТОЧНИКИ: [1]', {'provider': 'test'}

        result = ai_answer.compose('спецоффер', [self.live()], fake,
                                   on_date=self.TODAY)
        self.assertEqual([], result['notes'])

    def test_archive_is_not_attached_by_number_coincidence(self):
        """Несвежий кусок идёт в источники, только если его назвала САМА модель.

        Замер на боевых данных 28.08.2026: модель ответила по трём действующим
        акциям и архив не тронула, но архивный кусок подшился в источники — в нём
        «10.000 тенге» совпало с «10 000 тг» в ответе, — и под чистым ответом
        встала оговорка про архив.
        """
        def fake(system, user, history=()):
            return ('Спецоффер: 10 000 тг за 50 поездок за 14 дней.\n'
                    'ИСТОЧНИКИ: [1]'), {'provider': 'test'}

        result = ai_answer.compose('актуальные акции',
                                   [self.live(1), self.archive(2)],
                                   fake, on_date=self.TODAY)
        self.assertEqual('answer', result['kind'])
        self.assertEqual([], result['notes'])
        self.assertEqual([730], [item['article_id'] for item in result['sources']])

    def test_reformatted_date_is_not_an_invention(self):
        """Статья пишет «28 июля 2025», модель — «28.07.2025». Это одна дата.

        Замер на проде 28.08.2026: цифр 28072025 в тексте статьи нет (там 282025),
        и ВЕСЬ верный ответ про акции превращался в «в доступных вам статьях
        этого нет».
        """
        rows = [chunk(text='С 28 июля 2025 года бонусные программы приостановлены.')]
        self.assertEqual([], ai_answer.ungrounded_numbers(
            'Программы приостановлены с 28.07.2025.', rows, on_date=self.TODAY))

    def test_todays_date_in_the_answer_is_grounded(self):
        """Число, которое мы сами модели и сообщили, выдумкой быть не может.

        Модель повторяет строку «СЕГОДНЯ:» из запроса — на вопросе про акции
        парка Qazaq ответ начинался «на 28.08.2026 действуют следующие акции»
        и отбрасывался целиком.
        """
        self.assertEqual([], ai_answer.ungrounded_numbers(
            'На 28.08.2026 действуют следующие акции.', [self.live()],
            on_date=self.TODAY))

    def test_invented_number_is_still_caught(self):
        """Послабление для дат не должно открыть дверь выдумке."""
        self.assertEqual(['99 999'], ai_answer.ungrounded_numbers(
            'Бонус 99 999 тг.', [self.live()], on_date=self.TODAY))


if __name__ == '__main__':
    unittest.main()
