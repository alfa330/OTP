# -*- coding: utf-8 -*-
"""Наставник: форма ответа и то, из чего складывается пауза перед голосом.

Раздел «Тренажёр» переиспользует движок чат-помощника вики целиком — и это
правильно: второй RAG рядом с существующим означал бы вторую точку правды.
Но помощник вики пишет для ЧТЕНИЯ, а наставника СЛУШАЮТ, и разница стоит
секунд: замер 22.08.2026 на вопросе про фотоконтроль — 1047 знаков ответа
(76 секунд монолога, со звёздочками Markdown вслух) против 353 знаков.

Здесь закреплено три вещи, каждая из которых легко потеряется при правке:
  1. к системному промпту вики добавляется голосовое правило, а не заменяет его;
  2. вектор считается ПАРАЛЛЕЛЬНО с запросами в базу, а не после них;
  3. периметр статей за разговор считается один раз, но новый разговор его
     пересчитывает — иначе выданный доступ ждал бы истечения кеша.
"""

import contextlib
import sys
import threading
import time
import types
import unittest
from unittest import mock


class Cursor:
    def __init__(self):
        self.executed = []
        self._last = ''
        self.turn_id = 100
        # (role, text, kind, speech_cut, spoken_chars) — ровно то, что отдаёт
        # _load_history. Две последние колонки говорят, сколько реплики
        # собеседника человек реально услышал.
        self.history = []

    def execute(self, sql, params=None):
        self._last = ' '.join(str(sql).split())
        self.executed.append((self._last, params))

    def fetchone(self):
        if 'FROM users' in self._last:
            return (7, 'Тест', 'super_admin')
        if 'INSERT INTO trainer_sessions' in self._last:
            return (55, None)
        if 'INSERT INTO trainer_turns' in self._last:
            self.turn_id += 1
            return (self.turn_id,)
        if 'FROM trainer_sessions WHERE id' in self._last:
            return (55, 'mentor', None, 'active', 7)
        return None

    def fetchall(self):
        if 'FROM trainer_turns WHERE session_id' in self._last and 'role, text, kind' in self._last:
            return self.history
        return []


class Db:
    def __init__(self):
        self.cursor = Cursor()

    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield self.cursor
        return cm()


class Spy:
    """Считает вызовы движка вики и запоминает, что именно ему передали."""

    def __init__(self, *, embed_ms=0.05):
        self.scope_calls = 0
        self.embed_calls = 0
        self.systems = []
        self.max_tokens = []
        self.chains = []
        self.timeouts = []
        self.histories = []
        self.allow_clarify = []
        self.enriched = False
        self.kind = 'answer'
        self.embed_ms = embed_ms
        self.embed_started = None
        self.search_started = None
        self.lock = threading.Lock()

    # --- wiki.queries / access / perimeter ---
    def load_access_context(self, _cursor, user_id):
        self.scope_calls += 1
        return {'user_id': user_id, 'otp_role': 'super_admin', 'department_id': 1,
                'headed_department_ids': [], 'direction_id': None, 'group_ids': [],
                'wiki_roles': []}

    def collect_subjects(self, **_kwargs):
        return ['u:7']

    def load_capabilities(self, _cursor, ctx, _subjects):
        ctx['capabilities'] = {}

    def assistant_perimeter(self, _cursor, _ctx, _space):
        return {'article_ids': frozenset({1, 2, 3})}

    # --- wiki.ai ---
    def enrich_query(self, question, history):
        self.enrich_seen = list(history)
        # Обогащение имитируем: движок делает это, когда в истории есть реплики
        # человека, а вопрос короткий.
        return f'{history[-1]["text"]} {question}' if (self.enriched and history) else question

    def embed_query(self, _text):
        with self.lock:
            self.embed_started = time.perf_counter()
            self.embed_calls += 1
        time.sleep(self.embed_ms)
        return [0.1] * 8

    def search_hybrid(self, _cursor, **_kwargs):
        with self.lock:
            self.search_started = time.perf_counter()
        return {'rows': [{'chunk_id': 1, 'text': 'кусок'}],
                'branches': {'lexical': 1, 'dense': 1}, 'degraded': False}

    def compose(self, _question, _chunks, generate_fn, *, history=(), allow_clarify=True):
        self.histories.append(list(history))
        self.allow_clarify.append(allow_clarify)
        text, meta = generate_fn('СИСТЕМНЫЙ ПРОМПТ ВИКИ', 'вопрос', history=history)
        return {'kind': self.kind, 'text': text, 'sources': [],
                'meta': dict(meta, provider='vertex', model='gemini-3-flash-preview')}

    def generate(self, system, _prompt, *, history=(), max_tokens=None, chain=None,
                 timeout=None):
        self.systems.append(system)
        self.max_tokens.append(max_tokens)
        self.chains.append(chain)
        self.timeouts.append(timeout)
        return 'Коротко и по делу.', {'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


def fake_wiki(spy):
    """Подменённое дерево модулей вики: движок здесь не проверяется, он свой."""
    queries = types.SimpleNamespace(load_access_context=spy.load_access_context,
                                    load_capabilities=spy.load_capabilities)
    access = types.SimpleNamespace(collect_subjects=spy.collect_subjects)
    perimeter = types.SimpleNamespace(assistant_perimeter=spy.assistant_perimeter)
    answer = types.SimpleNamespace(enrich_query=spy.enrich_query, compose=spy.compose)
    embed = types.SimpleNamespace(embed_query=spy.embed_query)
    retrieve = types.SimpleNamespace(search_hybrid=spy.search_hybrid)
    providers = types.SimpleNamespace(generate=spy.generate)

    ai = types.ModuleType('wiki.ai')
    ai.answer, ai.embed, ai.retrieve, ai.providers = answer, embed, retrieve, providers
    root = types.ModuleType('wiki')
    root.queries, root.access, root.perimeter, root.ai = queries, access, perimeter, ai
    return {'wiki': root, 'wiki.ai': ai}


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
        env=lambda key, default=None: base.get(key, default)))
    return app.test_client(), db


def ask(client, text='Какие документы нужны водителю?'):
    return client.post('/api/trainer/sessions/55/turn', json={'text': text})


class MentorSpeedTest(unittest.TestCase):

    def test_voice_rules_are_added_to_the_wiki_prompt_not_instead_of_it(self):
        """Правила вики остаются, голосовое правило приписывается к ним.

        Заменить промпт целиком означало бы потерять и защиту от выдумки, и
        правило языка, и обязанность признаваться «этого нет в статьях».
        """
        from voice_trainer import scenarios

        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            response = ask(client)
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual(1, len(spy.systems))
        system = spy.systems[0]
        self.assertTrue(system.startswith('СИСТЕМНЫЙ ПРОМПТ ВИКИ'))
        self.assertIn(scenarios.MENTOR_VOICE_RULES, system)
        self.assertIn('НИКАКОЙ РАЗМЕТКИ', system)
        # Краткость не должна покупаться ни честностью, ни готовностью помочь.
        self.assertIn('если ответа во фрагментах', system)
        self.assertIn('отказ вместо имеющегося ответа', system)
        self.assertEqual([400], spy.max_tokens)

    def test_mentor_asks_for_a_faster_model_than_the_text_assistant(self):
        """У наставника своя цепочка: слушателю секунда паузы весит иначе.

        Замер 22.08.2026 на пяти случаях: gemini-3.5-flash — 1174 мс медиана
        против 2071 мс у gemini-3-flash-preview, при той же честности и тех же
        числах. Резерв остаётся моделью помощника вики.
        """
        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertEqual([(('vertex', 'gemini-3.5-flash'),
                           ('vertex', 'gemini-3-flash-preview'))], spy.chains)

    def test_empty_chain_returns_the_mentor_to_the_wiki_chain(self):
        """Пустая переменная — возврат к цепочке помощника вики без правки кода."""
        spy = Spy()
        client, _ = build({'TRAINER_MENTOR_CHAIN': ''})
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertEqual([None], spy.chains)

    def test_mentor_will_not_wait_a_minute_for_a_model(self):
        """Общий потолок вики — минута; в голосовом разговоре столько не ждут.

        22.08.2026 один ответ шёл 15,5 секунды. По истечении своего срока
        цепочка уходит на следующую модель, а не сидит до общего потолка.
        """
        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertEqual([12.0], spy.timeouts)

        spy = Spy()
        client, _ = build({'TRAINER_MENTOR_TIMEOUT': '5'})
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertEqual([5.0], spy.timeouts)

    def test_max_tokens_is_a_knob(self):
        spy = Spy()
        client, _ = build({'TRAINER_MENTOR_MAX_TOKENS': '150'})
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertEqual([150], spy.max_tokens)

    def test_vector_is_counted_alongside_the_database_not_after_it(self):
        """Эмбеддинг стартует ДО запросов в базу: это два разных конца света."""
        spy = Spy(embed_ms=0.20)
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        self.assertIsNotNone(spy.embed_started)
        self.assertIsNotNone(spy.search_started)
        self.assertLess(spy.embed_started, spy.search_started,
                        'вектор обязан считаться параллельно с базой, а не после неё')

    def test_perimeter_is_computed_once_per_conversation(self):
        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
            ask(client)
            ask(client)
        self.assertEqual(1, spy.scope_calls, 'права пересчитывались на каждой реплике')
        self.assertEqual(3, spy.embed_calls, 'вектор обязан считаться на каждый вопрос')

    def test_a_new_conversation_rereads_the_rights(self):
        """Иначе выданный доступ ждал бы истечения кеша, а не начала разговора."""
        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
            client.post('/api/trainer/sessions', json={'mode': 'mentor'})
            ask(client)
        self.assertEqual(2, spy.scope_calls)

    def test_every_stage_of_the_pause_is_recorded(self):
        """Пауза перед голосом складывается из четырёх источников — различать их
        можно только по замерам, и они должны доезжать до реплики."""
        spy = Spy()
        client, db = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        raw = next((params for sql, params in db.cursor.executed
                    if 'INSERT INTO trainer_turns' in sql and params
                    and any('stages' in str(v) for v in params)), None)
        self.assertIsNotNone(raw, 'замеры шагов не попали в реплику')
        blob = next(str(v) for v in raw if 'stages' in str(v))
        for stage in ('scope', 'embed_wait', 'search', 'generate'):
            self.assertIn(stage, blob, f'нет замера шага {stage}')


class MentorMemoryTest(unittest.TestCase):
    """Наставник обязан помнить разговор — и в том виде, в каком движок вики его
    понимает.

    До 22.08.2026 история собиралась с одним ключом kind, без role. Движок этого
    не узнавал: enrich_query ищет реплики с role='user' и не находил ни одной,
    то есть короткое уточнение искалось БЕЗ темы; а сборка сообщений для модели
    считала ответы самого наставника репликами человека. На проде это выглядело
    так: на «Жеті қазына» после вопроса про ту же акцию наставник дважды
    переспросил «уточните вопрос» и так и не ответил.
    """

    def test_history_reaches_the_engine_with_roles(self):
        spy = Spy()
        client, db = build()
        db.cursor.history = [('asker', 'Расскажи про акцию 7 Қазына', None, False, None),
                             ('mentor', 'Акция для курьеров.', 'answer', False, None)]
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client, 'А кому она положена?')
        self.assertEqual(
            [{'role': 'user', 'kind': 'question', 'text': 'Расскажи про акцию 7 Қазына'},
             {'role': 'assistant', 'kind': 'answer', 'text': 'Акция для курьеров.'}],
            spy.histories[0])
        # Тот же список уходит и в поиск: иначе короткая реплика ищется без темы.
        self.assertEqual(spy.histories[0], spy.enrich_seen)

    def test_no_clarify_twice_in_a_row(self):
        """Иначе разговор ходит по кругу — на проде наставник переспросил дважды."""
        spy = Spy()
        client, db = build()
        db.cursor.history = [('asker', 'Жетіқазына', None, False, None),
                             ('mentor', 'Уточните вопрос…', 'clarify', False, None)]
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client, 'Жеті қазына')
        self.assertEqual([False], spy.allow_clarify)

    def test_no_clarify_when_the_query_was_enriched_by_the_conversation(self):
        """Короткий вопрос — не значит двусмысленный, если тему задали раньше."""
        spy = Spy()
        spy.enriched = True
        client, db = build()
        db.cursor.history = [('asker', 'Расскажи про акцию 7 Қазына', None, False, None),
                             ('mentor', 'Акция для курьеров.', 'answer', False, None)]
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client, 'Жеті қазына')
        self.assertEqual([False], spy.allow_clarify)

    def test_clarify_stays_allowed_for_a_cold_question(self):
        spy = Spy()
        client, _ = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client, 'Жеті қазына')
        self.assertEqual([True], spy.allow_clarify)

    def test_kind_of_the_reply_is_stored(self):
        """Без вида реплики в базе нельзя ни объяснить уточнение в журнале, ни
        запретить второе подряд."""
        spy = Spy()
        spy.kind = 'clarify'
        client, db = build()
        with mock.patch.dict(sys.modules, fake_wiki(spy)):
            ask(client)
        insert = next(params for sql, params in db.cursor.executed
                      if 'INSERT INTO trainer_turns' in sql and 'kind' in sql)
        self.assertIn('clarify', [str(v) for v in insert])


if __name__ == '__main__':
    unittest.main()
