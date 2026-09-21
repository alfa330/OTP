# -*- coding: utf-8 -*-
"""«Вопросы операторов» (задача #321): отказ помощника → супервайзер → статья, новость, тест.

Тесты чистые — без базы и сети, приёмом раздела «Новости»: правило, выраженное
функцией, проверяется вызовом; то, что живёт в SQL и JSX, — чтением исходника
(как тесты колокола и вики).
"""

import os
import re
import unittest

from news import access as news_access
from news import schema as news_schema
from notifications import sources
from wiki import access as wiki_access
from wiki import questions as wiki_questions
from wiki.ai import answer as ai_answer
from wiki.ai import knowledge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


def _code_only(source):
    """Исходник без комментариев и docstring'ов: объяснение правила в шапке
    модуля не должно само проходить за его код."""
    without_blocks = re.sub(r'"""[\s\S]*?"""', '', source)
    return re.sub(r'(?m)#.*$', '', without_blocks)


def _jsx_code_only(source):
    return re.sub(r'/\*[\s\S]*?\*/', '', source)


def _sql_and_code(body):
    """Тело функции без её docstring и без #-комментариев, но С SQL.

    _code_only для тел с запросами не годится: SQL тут живёт в тройных кавычках
    и вырезался бы вместе с docstring'ом — а проверяется именно он.
    """
    without_docstring = re.sub(r'^\s*"""[\s\S]*?"""', '', body, count=1)
    return re.sub(r'(?m)^\s*#.*$', '', without_docstring)


def _function(source, name):
    """Тело функции верхнего уровня или вложенной — до следующего def того же отступа."""
    match = re.search(r'(?m)^(\s*)def %s\(' % re.escape(name), source)
    assert match, 'не нашёл def %s' % name
    indent = match.group(1)
    rest = source[match.end():]
    end = re.search(r'(?m)^%sdef |^%s@' % (re.escape(indent), re.escape(indent)), rest)
    return rest[:end.start()] if end else rest


class EscalationRuleTests(unittest.TestCase):
    """Кто и когда передаёт вопрос супервайзеру."""

    def test_only_operator_and_trainee_escalate(self):
        for role in ('operator', 'trainee'):
            self.assertTrue(wiki_questions.should_escalate(role, 'no_answer'), role)
        for role in ('sv', 'supervisor', 'trainer', 'admin', 'super_admin',
                     'hr_manager', 'accounting_manager', '', None):
            self.assertFalse(wiki_questions.should_escalate(role, 'no_answer'), role)

    def test_a_plain_answer_does_not_escalate(self):
        for kind in ('answer', 'clarify', 'supervisor', None):
            self.assertFalse(wiki_questions.should_escalate(
                'operator', kind, 'Минимальный срок аренды — 14 дней.'), kind)

    def test_an_answer_admitting_the_gap_escalates_for_operators_only(self):
        """Прод 15.09.2026: на «комиссию Яндекса» — таблица комиссий парков, без передачи."""
        text = ('В доступных мне фрагментах статей нет информации о размере комиссии '
                'сервиса Яндекс. Есть данные только по комиссиям таксопарков:\n'
                '| Парк | Комиссия |\n| Ноль такси | 0% |')
        self.assertTrue(wiki_questions.should_escalate('operator', 'answer', text))
        self.assertTrue(wiki_questions.should_escalate('trainee', 'answer', text))
        self.assertFalse(wiki_questions.should_escalate('sv', 'answer', text))
        self.assertFalse(wiki_questions.should_escalate('operator', 'clarify', text))

    def test_the_route_hands_over_the_answer_text(self):
        ask = _function(_code_only(_read('wiki', 'routes_ai.py')), 'wiki_ai_ask')
        self.assertIn("should_escalate(ctx['otp_role'], result['kind'], result['text'])", ask)


class AdmitsMissingTests(unittest.TestCase):
    """Признание «нет информации о …» в начале ответа. Формулировки — с прода."""

    ADMITS = (
        'В доступных вам статьях нет информации о конкретных моделях и годах выпуска '
        'автомобилей. Для проверки обратитесь к «Классификатору авто».',
        'В предоставленных фрагментах статей информации об адресе офиса в Туркестане нет. '
        'Для связи доступны контакты: * **Поддержка:** +77003000770',
        'В предоставленных фрагментах статей информация о таксопарке «Достойный» отсутствует. '
        'В материалах содержатся данные только по следующим паркам:',
        'В доступных мне фрагментах статей нет точной информации о размере комиссии '
        'таксопарка **2донгелек**.',
        '**В доступных вам статьях нет информации о комиссии Яндекса.**',
        'Қолжетімді мақала үзінділерінде Kaspi аударымдары туралы ақпарат жоқ. '
        'Бұл сұрақ бойынша бухгалтерге жүгініңіз.',
    )
    ANSWERS = (
        'Минимальный срок аренды — 14 дней.',
        # «нет» — сам ответ, а не признание.
        'Штрафа за опоздание нет. В статье «Аренда» сказано, что залог возвращается.',
        'В статьях указано, что комиссии за вывод нет.',
        # Признание не первой фразой — на вопрос уже ответили.
        'Комиссия парка «Ноль такси» — 0%. В доступных статьях нет информации о других парках.',
        'В статье «Аренда» указано: депозит 5000 ₸, минимальный срок 14 дней.',
    )

    def test_admissions_are_recognised(self):
        for text in self.ADMITS:
            self.assertTrue(ai_answer.admits_missing(text), text[:60])

    def test_answers_are_not_mistaken_for_admissions(self):
        for text in self.ANSWERS:
            self.assertFalse(ai_answer.admits_missing(text), text[:60])

    def test_admission_with_data_stays_an_answer_with_sources(self):
        """Передача не отнимает у оператора смежное: вид ответа не меняется."""
        text = self.ADMITS[1]
        self.assertTrue(ai_answer.admits_missing(text))
        self.assertFalse(ai_answer.is_refusal(text))

    def test_escalation_never_costs_the_operator_the_answer(self):
        """Передача — продолжение ответа, а не его условие: сбой очереди под
        савпоинтом, и сам ответ уже записан к этому моменту."""
        ask = _function(_code_only(_read('wiki', 'routes_ai.py')), 'wiki_ai_ask')
        self.assertIn("SAVEPOINT wiki_question_escalate", ask)
        self.assertIn("ROLLBACK TO SAVEPOINT wiki_question_escalate", ask)
        self.assertLess(ask.index("role='assistant'"), ask.index('wiki_questions.escalate('))
        self.assertIn("'escalation': escalation", ask)

    def test_refusal_text_is_left_as_the_answer_gave_it(self):
        """Аналитика вики различает виды отказа по точному тексту — подменять
        его при передаче нельзя."""
        ask = _function(_code_only(_read('wiki', 'routes_ai.py')), 'wiki_ai_ask')
        self.assertNotIn("result['text'] =", ask)
        self.assertNotIn('ESCALATED_TEXT', _code_only(_read('wiki', 'questions.py')))

    def test_opening_the_chat_marks_the_answer_seen(self):
        read = _function(_code_only(_read('wiki', 'routes_ai.py')), 'wiki_ai_chat_read')
        self.assertIn('decorate_messages', read)
        self.assertIn('mark_answers_seen', read)


class ReviewerLadderTests(unittest.TestCase):
    """Разбирает тот, кто вправе адресовать отделу новость, — ровно он."""

    @staticmethod
    def _ctx(role, **extra):
        ctx = {'otp_role': role, 'department_id': 3, 'headed_department_ids': [],
               'wiki_roles': [], 'capabilities': {}}
        ctx.update(extra)
        return ctx

    def test_reviewers_are_exactly_the_news_publishers(self):
        for role in list(wiki_access.ROLE_LEVELS) + ['supervisor', '', None]:
            allowed, departments = wiki_questions.reviewer_scope(self._ctx(role))
            self.assertEqual(allowed, news_access.publish_ceiling(role) is not None, role)
            if allowed:
                self.assertEqual(departments,
                                 news_access.publish_departments(role, department_id=3), role)

    def test_supervisor_is_bounded_by_own_department(self):
        self.assertEqual(wiki_questions.reviewer_scope(self._ctx('sv', department_id=7)),
                         (True, [7]))

    def test_operator_does_not_review(self):
        self.assertEqual(wiki_questions.reviewer_scope(self._ctx('operator')), (False, []))

    def test_wiki_admin_has_no_boundary(self):
        ctx = self._ctx('operator', wiki_roles=[{'code': 'wiki_admin'}],
                        capabilities={'can_manage_access': True})
        self.assertEqual(wiki_questions.reviewer_scope(ctx), (True, None))

    def test_every_route_by_id_passes_the_department_boundary(self):
        """Точечные пути, не повторившие правило списка, уже открывали чужое в
        «Новостях» — здесь каждая дверь с id ходит через _load."""
        source = _code_only(_read('wiki', 'routes_questions.py'))
        routes = re.findall(r"@wiki_route\('(/questions[^']*)'[^\n]*\n\s*def (\w+)", source)
        self.assertGreaterEqual(len(routes), 9)
        for rule, handler in routes:
            body = _function(source, handler)
            if '<int:question_id>' in rule:
                self.assertIn('_load(cursor, ctx, question_id', body, handler)
            else:
                self.assertIn('_reviewer(cursor, ctx)', body, handler)

    def test_scope_is_in_every_query_of_the_list(self):
        source = _code_only(_read('wiki', 'questions.py'))
        for name in ('list_questions', 'get_question'):
            self.assertIn('_scope(departments)', _function(source, name), name)


class ChatMarksTests(unittest.TestCase):
    def test_refusal_gets_the_state_and_answer_gets_the_name(self):
        messages = [{'id': 1}, {'id': 2}, {'id': 3}]
        marks = [{'id': 9, 'status': 'answered', 'refusal_message_id': 1,
                  'answer_message_id': 3, 'resolved_by_name': 'Анна'}]
        wiki_questions.decorate_messages(messages, marks)
        self.assertEqual(messages[0]['escalation'], {'id': 9, 'status': 'answered'})
        self.assertNotIn('escalation', messages[1])
        self.assertEqual(messages[2]['supervisor_name'], 'Анна')

    def test_only_one_supervisor_answers(self):
        source = _read('wiki', 'questions.py')
        for name in ('answer_question', 'dismiss_question'):
            self.assertIn("WHERE id = %(id)s AND status = 'open'",
                          _sql_and_code(_function(source, name)), name)
        self.assertIn("AND status = 'answered' AND kb_status IS NULL",
                      _sql_and_code(_function(source, 'finish_knowledge')))


class BellRulesTests(unittest.TestCase):
    """Кого будит вопрос — одно правило в двух копиях (триггер и источник)."""

    DATABASE = _read('database.py')

    def _trigger_block(self):
        start = self.DATABASE.index('def _init_bell_notify_schema_tx(self, cursor):')
        return self.DATABASE[start:self.DATABASE.index('def _init_amo_leads_schema_tx', start)]

    def test_source_is_registered(self):
        self.assertIn('wiki_questions', sources.SOURCES)
        self.assertIs(sources._HANDLERS['wiki_questions'], sources.wiki_questions)

    def test_trigger_and_source_share_the_department_rule(self):
        block = self._trigger_block()
        branch = block[block.index("TG_TABLE_NAME = 'wiki_operator_questions'"):]
        branch = branch[:branch.index('ELSIF')]
        source = _sql_and_code(_function(_read('notifications', 'sources.py'), 'wiki_questions'))
        # Супервайзеры отдела вопроса…
        self.assertIn("lower(u.role) IN ('sv', 'supervisor')", branch)
        self.assertIn("lower(me.role) IN ('sv', 'supervisor')", source)
        # …а глава — только когда их нет.
        self.assertIn('head_user_id', branch)
        self.assertIn('array_length(targets, 1) IS NULL', branch)
        self.assertIn('d.head_user_id = me.id', source)
        self.assertIn('NOT EXISTS', source)

    def test_knowledge_writes_do_not_wake_the_department(self):
        self.assertIn("'AFTER UPDATE OF status, asker_seen_at'", self._trigger_block())

    def test_targets_lead_to_the_tabs_the_app_understands(self):
        source = _read('notifications', 'sources.py')
        self.assertIn("'questions:%d'", source)
        self.assertIn("'assistant:%d'", source)
        self.assertIn('(questions|assistant):(\\d+)', _read('src', 'App.jsx'))


class QuizRulesTests(unittest.TestCase):
    """Тест в окне новости: проверка при выпуске и сверка ответов."""

    @staticmethod
    def _good():
        return [{'prompt': 'Срок акции?', 'options': ['7 дней', '14 дней', '30 дней'],
                 'correct': 1},
                {'prompt': 'Кому?', 'options': ['Новичкам', 'Всем'], 'correct': 0}]

    def test_good_quiz_passes_and_is_squashed(self):
        quiz = self._good()
        quiz[0]['prompt'] = '  Срок   акции? '
        clean, problem = news_access.normalize_quiz(quiz)
        self.assertIsNone(problem)
        self.assertEqual(clean[0]['prompt'], 'Срок акции?')

    def test_refusals_name_the_question(self):
        cases = [
            # Один вопрос — законный тест (ТЗ #300, п.4: число вопросов решает
            # автор). Границы теперь стережёт только потолок.
            (lambda q: q * 6, 'В тесте должно быть от 1 до 10 вопросов'),
            (lambda q: [dict(q[0], correct=None), q[1]], 'В вопросе 1 не отмечен верный вариант'),
            (lambda q: [q[0], dict(q[1], correct=True)], 'В вопросе 2 не отмечен верный вариант'),
            (lambda q: [q[0], dict(q[1], options=['Всем', 'всем'])], 'В вопросе 2 варианты повторяются'),
            (lambda q: [dict(q[0], options=['а', '']), q[1]], 'В вопросе 1 есть пустой вариант ответа'),
            (lambda q: [dict(q[0], options=['а']), q[1]], 'В вопросе 1 должно быть от 2 до 4 вариантов'),
            (lambda q: [dict(q[0], prompt=' '), q[1]], 'В вопросе 1 нет текста'),
        ]
        for mutate, expected in cases:
            self.assertEqual(news_access.normalize_quiz(mutate(self._good()))[1], expected)
        self.assertEqual(news_access.normalize_quiz(None)[1], 'Тест не заполнен')

    def test_mistakes_are_counted_on_the_server(self):
        key = [(11, 1), (12, 0)]
        self.assertEqual(news_access.quiz_mistakes(key, {'11': 1, '12': 0}), [])
        self.assertEqual(news_access.quiz_mistakes(key, {11: 1, 12: '0'}), [])
        self.assertEqual(news_access.quiz_mistakes(key, {'11': 2}), [11, 12])
        # True — не «вариант 1»: bool не проходит за число.
        self.assertEqual(news_access.quiz_mistakes(key, {'11': True, '12': 0}), [11])
        self.assertEqual(news_access.quiz_mistakes(key, None), [11, 12])

    def test_window_never_receives_the_right_answer(self):
        source = _read('news', 'queries.py')
        pending = source[source.index('def pending_for_user('):source.index('def mark_shown(')]
        self.assertIn('news_quiz_questions', pending)
        self.assertNotIn('correct_index', pending)

    def test_quiz_news_is_mandatory_whatever_the_record_says(self):
        confirm = _sql_and_code(_function(_read('news', 'queries.py'), 'confirm_read'))
        self.assertLess(confirm.index('is_mandatory = True'), confirm.index('if not is_mandatory:'))
        self.assertLess(confirm.index("'quiz_wrong'"), confirm.rindex('SET confirmed_at'))
        self.assertLess(confirm.index("'too_early'"), confirm.index("'quiz_wrong'"))

    def test_mandatory_cannot_be_dropped_from_a_quiz_news(self):
        self.assertIn('NEWS_QUIZ_MANDATORY', _read('news', 'routes.py'))

    def test_form_limits_agree_with_the_server(self):
        module = _read('src', 'components', 'wiki', 'questionQuiz.js')
        for name in ('QUIZ_MIN_QUESTIONS', 'QUIZ_MAX_QUESTIONS', 'QUIZ_MIN_OPTIONS',
                     'QUIZ_MAX_OPTIONS'):
            match = re.search(r'export const %s = (\d+);' % name, module)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match.group(1)), getattr(news_schema, name), name)

    def test_window_sends_answers_and_starts_over_on_a_mistake(self):
        """Ответы уходят подтверждением, а неверные сбрасывают весь тест
        (решение владельца 21.09.2026, см. useQuizAttempt в NewsPasses)."""
        modal = _jsx_code_only(_read('src', 'components', 'news', 'NewsOfDayModal.jsx'))
        self.assertIn('NEWS_QUIZ_WRONG', modal)
        self.assertIn('{ answers: attempt.answers }', modal)
        # Итог попытки уходит в сброс вместе с отказом: при мягком проходном
        # балле окно говорит «верных 2 из 3, нужно 3» (ТЗ #300, п.4).
        self.assertIn('attempt.fail(e.response.data);', modal)
        self.assertIn("'Подтвердить'", modal)


class KnowledgeDraftTests(unittest.TestCase):
    """Разбор конверта новости и выбор, куда записать ответ."""

    REPLY = """```
**ЗАГОЛОВОК:** Акция для новичков продлена
НОВОСТЬ:
Акция «50 поездок» теперь действует 20 дней.
Условия прежние.
ТЕСТ:
1. Сколько дней действует акция
«50 поездок»?
- 14 дней
+ 20 дней
- 30 дней
2) Изменились ли условия?
+ Нет
- Да
```"""

    def test_reply_is_parsed_into_a_valid_quiz(self):
        parsed = knowledge.parse_news_reply(self.REPLY)
        self.assertEqual(parsed['title'], 'Акция для новичков продлена')
        self.assertIn('20 дней', parsed['body'])
        self.assertEqual(len(parsed['quiz']), 2)
        self.assertEqual(parsed['quiz'][0]['prompt'],
                         'Сколько дней действует акция «50 поездок»?')
        self.assertEqual(parsed['quiz'][0]['correct'], 1)
        self.assertEqual(parsed['quiz'][1]['correct'], 0)
        self.assertIsNone(news_access.normalize_quiz(parsed['quiz'])[1])

    LABEL_REPLY = ('НОВЫЕ СВЕДЕНИЯ\nНОВОСТЬ:\nПарк выдаёт автокресло под залог 5000 ₸.\nТЕСТ:\n'
                   '1. Какой залог?\n- 3000 ₸\n+ 5000 ₸\n2. Какой срок?\n+ 14 дней\n- 7 дней')

    def test_echoed_label_is_not_taken_for_a_title(self):
        """Замер 15.09.2026: llama-3.3 писала «НОВЫЕ СВЕДЕНИЯ» вместо «ЗАГОЛОВОК:»."""
        parsed = knowledge.parse_news_reply(self.LABEL_REPLY)
        self.assertEqual(parsed['title'], '')
        self.assertEqual(parsed['body'], 'Парк выдаёт автокресло под залог 5000 ₸.')
        self.assertEqual(len(parsed['quiz']), 2)

    def test_title_on_the_next_line_and_body_without_its_marker(self):
        parsed = knowledge.parse_news_reply(
            'ЗАГОЛОВОК:\nАвтокресло под залог\nПарк выдаёт автокресло.\nТЕСТ:\n'
            '1. В?\n+ да\n- нет\n2. Г?\n+ да\n- нет')
        self.assertEqual(parsed['title'], 'Автокресло под залог')
        self.assertEqual(parsed['body'], 'Парк выдаёт автокресло.')

    def test_missing_title_comes_from_the_article_without_a_second_call(self):
        calls = []

        def generate(system, user, **kwargs):
            calls.append(user)
            return self.LABEL_REPLY, {}

        result = knowledge.draft_news(question='Автокресло?', answer='Под залог 5000 ₸ на 14 дней',
                                      article_title='Детское автокресло', changes=[],
                                      generate_fn=generate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['title'], 'Новое в статье «Детское автокресло»')
        self.assertEqual(result['warnings'], [])

    def test_prompt_does_not_invite_an_echoed_label(self):
        prompt = knowledge.build_news_prompt(question='В?', answer='О', article_title='', changes=[])
        self.assertNotIn('без названия', prompt)
        self.assertNotIn('новые сведения', prompt.lower())

    def test_two_right_answers_are_not_guessed(self):
        parsed = knowledge.parse_news_reply(
            'ЗАГОЛОВОК: А\nНОВОСТЬ:\nБ\nТЕСТ:\n1. В?\n+ да\n+ нет\n2. Г?\n+ да\n- нет')
        self.assertIsNone(parsed['quiz'][0]['correct'])
        self.assertEqual(news_access.normalize_quiz(parsed['quiz'])[1],
                         'В вопросе 1 не отмечен верный вариант')

    def test_broken_quiz_is_salvaged_for_manual_editing(self):
        kept = knowledge.salvage_quiz([{'prompt': 'В?', 'options': ['а', '', 'б'], 'correct': 7}])
        self.assertEqual(kept, [{'prompt': 'В?', 'options': ['а', 'б'], 'correct': None}])

    def test_draft_news_retries_once_and_then_hands_over(self):
        calls = []

        def generate(system, user, **kwargs):
            calls.append(user)
            return 'ЗАГОЛОВОК: Только заголовок', {'model': 'test'}

        result = knowledge.draft_news(question='Вопрос', answer='Ответ', article_title='Статья',
                                      changes=[], generate_fn=generate)
        self.assertEqual(len(calls), 2)
        self.assertIn('Предыдущий ответ не принят', calls[1])
        self.assertTrue(result['warnings'])

    def test_draft_news_flags_invented_numbers_but_not_distractors(self):
        """Проверка чисел — от трёх цифр (answer.ungrounded_numbers). Неверный
        вариант теста обязан отличаться от ответа, поэтому его числа не выдумка."""
        def generate(system, user, **kwargs):
            return ('ЗАГОЛОВОК: Штраф\nНОВОСТЬ:\nШтраф теперь 1500 ₸.\nТЕСТ:\n'
                    '1. Какой штраф?\n+ 500 ₸\n- 700 ₸\n2. Кому?\n+ всем\n- никому'), {}

        result = knowledge.draft_news(question='Какой штраф?', answer='Штраф 500 ₸',
                                      article_title='Штрафы', changes=[], generate_fn=generate)
        numbers = ' '.join(result['warnings'])
        self.assertIn('1500', numbers)
        self.assertNotIn('700', numbers)

    def test_suggestion_needs_both_confidence_and_the_right_to_edit(self):
        floor = knowledge.STRICT_FLOOR
        editable = {'article_id': 5, 'can_edit': True, 'similarity': floor + 0.05}
        self.assertEqual(knowledge.suggest_target([editable], default_section_id=9),
                         {'action': 'update', 'article_id': 5})
        for candidate in (dict(editable, similarity=floor - 0.1),
                          dict(editable, similarity=None),
                          dict(editable, can_edit=False)):
            self.assertEqual(knowledge.suggest_target([candidate], default_section_id=9),
                             {'action': 'create', 'section_id': 9})

    def test_targets_are_one_row_per_article(self):
        rows = [{'article_id': 1, 'title': 'А'}, {'article_id': 1, 'title': 'А'},
                {'article_id': 2, 'title': 'Б'}]
        self.assertEqual([t['article_id'] for t in knowledge.pick_targets(rows)], [1, 2])

    def test_news_body_escapes_what_looks_like_markup(self):
        self.assertEqual(knowledge.news_body_html('Ответ <5 минут\n\nВторой абзац'),
                         '<p>Ответ &lt;5 минут</p><p>Второй абзац</p>')

    def test_update_instruction_carries_both_question_and_answer(self):
        text = knowledge.update_instruction('Где  взять\nтермокороб?', 'В офисе на Абая')
        self.assertIn('«Где взять термокороб?»', text)
        self.assertIn('«В офисе на Абая»', text)

    def test_new_article_document_is_the_answer_and_the_question_is_context(self):
        """Вопрос в документе модель переносила в статью дословно (стенд 15.09.2026)."""
        prompts = []

        def generate(system, user, **kwargs):
            prompts.append(user)
            return 'НАЗВАНИЕ: Автокресло\nОПИСАНИЕ: Условия\nСТАТЬЯ:\n<h1>Условия</h1><p>Залог 5000 ₸.</p>', {}

        knowledge.draft_create(question='Выдаёт ли парк автокресло?',
                               answer='Автокресло выдаём под залог 5000 ₸.', generate_fn=generate)
        document, _, instruction = prompts[0].partition('УКАЗАНИЕ РЕДАКТОРА')
        self.assertIn('Автокресло выдаём под залог 5000 ₸.', document)
        self.assertNotIn('Выдаёт ли парк автокресло?', document)
        self.assertIn('«Выдаёт ли парк автокресло?»', instruction)

    def test_answer_numbers_are_a_legitimate_source_for_the_edit(self):
        self.assertIn('extra_sources=answer', _code_only(_read('wiki', 'ai', 'knowledge.py')))


class PublishTransactionTests(unittest.TestCase):
    """Статья, новость с тестом и отметка — вместе или никак."""

    PUBLISH = _function(_code_only(_read('wiki', 'routes_questions.py')), 'wiki_questions_publish')

    def test_rights_are_checked_before_the_first_write(self):
        savepoint = self.PUBLISH.index("SAVEPOINT wiki_question_publish")
        for check in ('audience_refusal(', '_article_refusal(', '_section_allowed(',
                      'normalize_quiz('):
            self.assertLess(self.PUBLISH.index(check), savepoint, check)

    def test_quiz_is_attached_before_the_news_goes_out(self):
        self.assertLess(self.PUBLISH.index('set_quiz('), self.PUBLISH.index('publish_post('))

    def test_a_colleague_race_rolls_everything_back(self):
        race = self.PUBLISH[self.PUBLISH.index('finish_knowledge('):]
        self.assertIn('ROLLBACK TO SAVEPOINT wiki_question_publish', race)

    def test_the_news_is_addressed_to_the_askers_department_only(self):
        self.assertIn("'subject_type': 'department', 'subject_id': item['department_id']",
                      self.PUBLISH)
        self.assertIn('is_mandatory=True', self.PUBLISH)


class FrontendTests(unittest.TestCase):
    """Интерфейсные решения, которые сборка не проверит."""

    def test_tab_is_gated_like_the_news(self):
        view = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn("key: 'questions'", view)
        self.assertIn('features.assistant && canPublishNews', view)

    def test_supervisor_answer_is_signed_and_not_rated(self):
        thread = _jsx_code_only(_read('src', 'components', 'assistant', 'assistantThread.jsx'))
        self.assertIn('Ответ супервайзера', thread)
        self.assertIn('!fromSupervisor', thread)

    def test_waiting_uses_the_bell_poke_not_a_timer(self):
        hook = _jsx_code_only(_read('src', 'components', 'assistant', 'useSupervisorReply.js'))
        self.assertIn('subscribeNewsPoke', hook)
        self.assertNotIn('setInterval', hook)
        for owner in (('assistant', 'useAssistantChat.js'), ('wiki', 'WikiAssistant.jsx')):
            self.assertIn('useSupervisorReply(messages, refreshChat)',
                          _read('src', 'components', *owner), owner)

    def test_section_picker_is_the_canonical_one(self):
        tab = _read('src', 'components', 'wiki', 'WikiQuestions.jsx')
        self.assertIn('variant="ios"', tab)
        self.assertNotIn('<select', tab)

    def test_analytics_does_not_count_supervisor_answers(self):
        self.assertIn("m.kind <> 'supervisor'", _read('wiki', 'analytics.py'))

    def test_bell_has_a_label_for_the_source(self):
        bell = _read('src', 'components', 'notifications', 'NotificationsBell.jsx')
        self.assertIn("wiki_questions: { label: 'Вопросы операторов'", bell)

    def test_tab_lists_the_space_on_screen_and_follows_the_bell_into_its_space(self):
        tab = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiQuestions.jsx'))
        self.assertIn('params: { bucket, space_id: spaceId }', tab)
        self.assertIn('[base, headers, bucket, spaceId, toast]', tab)
        self.assertIn('changeSpace(item.space_id)', tab)
        view = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn('onSpaceChange={setSpaceId}', view)


class SpaceBoundaryTests(unittest.TestCase):
    """Вопрос виден в той вике, где его задали.

    Жалоба исполнителя 15.09.2026: вопрос оператора Тез КЦ, заданный в «Тез»,
    показывался в «Таксопарках» — граница отдела отвечает на «чей вопрос», а не
    на «в какой вике он живёт».
    """

    def test_list_is_bounded_by_the_space_on_screen(self):
        where, params = wiki_questions._space_scope(12, [11, 12])
        self.assertIn('q.space_id = %(space)s', where)
        self.assertEqual(params, {'space': 12, 'reachable_spaces': [11, 12]})

    def test_questions_that_would_be_lost_stay_visible(self):
        where, _params = wiki_questions._space_scope(11, [11])
        self.assertIn('q.space_id IS NULL', where)
        self.assertIn('NOT (q.space_id = ANY(%(reachable_spaces)s))', where)

    def test_nothing_reachable_does_not_break_the_array(self):
        self.assertEqual(wiki_questions._space_scope(11, [])[1]['reachable_spaces'], [-1])

    def test_no_space_means_no_boundary(self):
        self.assertEqual(wiki_questions._space_scope(None, [11]), ('TRUE', {}))

    def test_list_and_counters_share_the_boundary_but_the_card_does_not(self):
        """Карточку открывают из колокола, находясь в другой вике, — по id граница
        только отдела, а вику вкладка переключает сама."""
        source = _code_only(_read('wiki', 'questions.py'))
        self.assertIn('_space_scope(space_id, reachable_spaces)',
                      _function(source, 'list_questions'))
        self.assertNotIn('_space_scope', _function(source, 'get_question'))

    def test_route_takes_only_a_space_the_reviewer_can_open(self):
        source = _code_only(_read('wiki', 'routes_questions.py'))
        helper = _function(source, '_space')
        self.assertIn('queries.spaces_for_user(cursor, ctx)', helper)
        self.assertIn("request.args.get('space_id')", helper)
        self.assertIn('space_id=space_id, reachable_spaces=reachable',
                      _function(source, 'wiki_questions_list'))


class AskerEscalationTests(unittest.TestCase):
    """«Отправить супервайзеру»: оператор передаёт вопрос сам (15.09.2026)."""

    def test_the_button_follows_the_escalation_ladder(self):
        for role in ('operator', 'trainee'):
            self.assertTrue(wiki_questions.may_escalate_by_hand(role), role)
        for role in ('sv', 'supervisor', 'trainer', 'admin', 'super_admin', '', None):
            self.assertFalse(wiki_questions.may_escalate_by_hand(role), role)

    def test_front_and_server_agree_on_what_can_be_sent(self):
        thread = _read('src', 'components', 'assistant', 'assistantThread.jsx')
        kinds = re.search(r"ESCALATABLE_KINDS = \[([^\]]*)\]", thread).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", kinds)),
                         wiki_questions.ASKER_ESCALATION_KINDS)
        self.assertNotIn(wiki_questions.SUPERVISOR_KIND, wiki_questions.ASKER_ESCALATION_KINDS)

    def test_only_own_live_chat_and_one_question_per_answer(self):
        body = _sql_and_code(_function(_read('wiki', 'questions.py'), 'escalate_by_asker'))
        self.assertIn('c.user_id = %(asker)s AND c.deleted_at IS NULL', body)
        self.assertIn("role != 'assistant'", body)
        self.assertIn('WHERE refusal_message_id = %s', body)
        self.assertIn("role = 'user' AND seq < %s", body)
        self.assertIn('requested_by_asker=True', body)

    def test_route_checks_role_table_and_department_before_writing(self):
        source = _code_only(_read('wiki', 'routes_ai.py'))
        route = _function(source, 'wiki_ai_escalate')
        checks = [route.index('may_escalate_by_hand'), route.index('table_ready'),
                  route.index("ctx.get('department_id')"), route.index('escalate_by_asker(')]
        self.assertEqual(checks, sorted(checks))
        self.assertIn("'can_escalate'", _function(source, 'wiki_ai_status'))

    def test_both_assistant_surfaces_offer_the_button_only_when_the_server_allows(self):
        self.assertIn('onEscalate={chat.canEscalate ? escalate : null}',
                      _read('src', 'components', 'assistant', 'AssistantPanel.jsx'))
        self.assertIn('canEscalate: !!status?.can_escalate',
                      _read('src', 'components', 'assistant', 'useAssistantChat.js'))
        self.assertIn('onEscalate={status?.can_escalate ? escalate : null}',
                      _read('src', 'components', 'wiki', 'WikiAssistant.jsx'))
        thread = _jsx_code_only(_read('src', 'components', 'assistant', 'assistantThread.jsx'))
        self.assertIn('!!onEscalate && !message.escalation', thread)

    def test_supervisor_sees_what_the_assistant_answered(self):
        source = _read('wiki', 'questions.py')
        self.assertIn('LEFT JOIN wiki_ai_messages reply ON reply.id = q.refusal_message_id', source)
        self.assertIn("'requested_by_asker', 'assistant_text'", source)
        self.assertIn('<AssistantReply text={item.assistant_text} requested={item.requested_by_asker} />',
                      _read('src', 'components', 'wiki', 'WikiQuestions.jsx'))


class NewsOnlyTests(unittest.TestCase):
    """«Опубликовать как новость»: ответ уходит отделу новостью без статьи (15.09.2026)."""

    def test_news_is_a_known_outcome_in_code_and_schema(self):
        self.assertIn('news', wiki_questions.KB_STATUSES)
        schema = _read('wiki', 'schema.py')
        self.assertIn("CHECK (kb_status IN ('published', 'skipped', 'news'))", schema)
        # Проверка переписывается только при нужде — не блокировкой на каждом старте.
        self.assertIn("position('news' in pg_get_constraintdef(oid)) > 0", schema)
        self.assertIn('requested_by_asker BOOLEAN NOT NULL DEFAULT FALSE', schema)

    def test_mark_requires_own_published_news(self):
        route = _function(_code_only(_read('wiki', 'routes_questions.py')),
                          'wiki_questions_news_post')
        self.assertIn("stage='knowledge'", route)
        self.assertIn("post['status'] != 'published'", route)
        self.assertIn("post['author_id'] != ctx['user_id']", route)
        self.assertIn("status='news'", route)

    def test_button_opens_the_news_form_and_returns_to_the_question(self):
        tab = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiQuestions.jsx'))
        self.assertIn('Опубликовать как новость', tab)
        self.assertIn('draft: newsDraftFromQuestion(item)', tab)
        view = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn('compose={newsCompose}', view)
        self.assertIn('/knowledge/news-post', view)
        self.assertIn('canComposeNews={features.news && canPublishNews}', view)
        news = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('closeCompose(payload.publish ? response?.data?.id : null)', news)


if __name__ == '__main__':
    unittest.main()
