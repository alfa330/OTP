# -*- coding: utf-8 -*-
"""Проверка качества чатов СЗоВ по Syntony (SZOV-9): эпизоды, атрибуция, день перехода 20.11,
форма снимка, подписанные ссылки на вложения и то, как снимок Syntony читает «ИИ-оценка».

Без базы: правила — чистые функции syntony_chats/qa.py и call_qa/subjects.py. SQL заморозки и
выборки — tests/test_syntony_qa_postgres.py. Люди и номера выдуманы.
"""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from syntony_chats import qa, thread  # noqa: E402
from call_qa import config as qa_config, media as qa_media, subjects  # noqa: E402

UTC = timezone.utc
SZOV_CHANNEL = 'b40df588-75cc-4562-be29-b1e73580fd49'   # «Ноль такси», szov.SZOV_CHANNEL_IDS
WABA_CHANNEL = 'ae11b9f5-cada-49b5-b7bc-c3c01ae695f0'   # Salam taxi (WABA)
PEOPLE = {'Бекова Динара': {'id': 2, 'name': 'Бекова Динара Ерланқызы'},
          'Ахметова Айгерим': {'id': 1, 'name': 'Ахметова Айгерим'}}


def resolve(author):
    return PEOPLE.get(author)


def display(author):
    person = resolve(author)
    return person['name'] if person else author


def at(text):
    """'2026-11-21 10:00' по Алматы → момент с поясом."""
    local = datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=timezone(timedelta(hours=5)))
    return local.astimezone(UTC)


def row(mid, moment, kind='driver', *, author=None, text='текст', media=None, content='text',
        scope=None, conversation='c1', channel=SZOV_CHANNEL):
    direction = 'inbound' if kind == 'driver' else 'outbound'
    sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai', 'system': 'system'}[kind]
    return {'id': mid, 'conversation_id': conversation, 'direction': direction,
            'sender_type': sender, 'author_name': author, 'content_type': content, 'text': text,
            'media': media, 'created_at': at(moment), 'delete_scope': scope, 'channel_id': channel}


class StartDayAndPeriodTests(unittest.TestCase):
    def test_start_day_is_twentieth_by_default(self):
        self.assertEqual(qa.start_day({}), date(2026, 11, 20))
        self.assertEqual(qa.start_day({'SYNTONY_QA_FROM': '21.11.2026'}), date(2026, 11, 21))
        self.assertEqual(qa.start_day({'SYNTONY_QA_FROM': '2026-11-22'}), date(2026, 11, 22))
        # Неразборчивое — умолчание, а не ошибка запуска.
        self.assertEqual(qa.start_day({'SYNTONY_QA_FROM': 'завтра'}), date(2026, 11, 20))

    def test_share_threshold_is_one_for_journal_and_ai(self):
        """Журнал и «ИИ-оценка» берут чат по одному порогу доли ответов (план SZOV-9)."""
        self.assertEqual(qa.ENV_MIN_SHARE, 'SYN_MIN_OPERATOR_SHARE')
        self.assertEqual(qa.min_operator_share({}), qa.DEFAULT_MIN_SHARE)
        self.assertEqual(qa.min_operator_share({}), qa_config.SYN_MIN_OPERATOR_SHARE)
        self.assertEqual(qa.min_operator_share({'SYN_MIN_OPERATOR_SHARE': '0.8'}), 0.8)
        self.assertEqual(qa.min_operator_share({'SYN_MIN_OPERATOR_SHARE': 'много'}), 0.9)

    def test_rollback_switch(self):
        with mock.patch.object(qa.szov, 'data_enabled', return_value=True):
            self.assertTrue(qa.enabled({}))
            self.assertFalse(qa.enabled({'SYNTONY_QA_SOURCE': 'chat2desk'}))
        with mock.patch.object(qa.szov, 'data_enabled', return_value=False):
            self.assertFalse(qa.enabled({}))

    def test_period_is_split_at_transition_day(self):
        first = date(2026, 11, 20)
        self.assertEqual(qa.split_period('2026-11-01', '2026-11-26', first),
                         ((date(2026, 11, 1), date(2026, 11, 19)),
                          (date(2026, 11, 20), date(2026, 11, 26))))
        self.assertEqual(qa.split_period('2026-11-21', '2026-11-26', first),
                         (None, (date(2026, 11, 21), date(2026, 11, 26))))
        self.assertEqual(qa.split_period('2026-11-01', '2026-11-19', first),
                         ((date(2026, 11, 1), date(2026, 11, 19)), None))
        self.assertEqual(qa.split_period('2026-11-20', '2026-11-20', first),
                         (None, (date(2026, 11, 20), date(2026, 11, 20))))
        self.assertEqual(qa.split_period(None, None, first),
                         ((None, date(2026, 11, 19)), (date(2026, 11, 20), None)))
        # Декабрь — только Syntony.
        self.assertEqual(qa.split_period('2026-12-01', '2026-12-31', first),
                         (None, (date(2026, 12, 1), date(2026, 12, 31))))


class EpisodeTests(unittest.TestCase):
    def test_pause_of_six_hours_splits_and_last_waits_for_silence(self):
        messages = [row('a', '2026-11-21 10:00'), row('b', '2026-11-21 10:05', 'human'),
                    row('c', '2026-11-21 17:00')]
        episodes = qa.split_episodes(messages)
        self.assertEqual([[m['id'] for m in e] for e, _ in episodes], [['a', 'b'], ['c']])
        closed = qa.closed_episodes(messages, at('2026-11-21 18:00'))
        self.assertEqual([[m['id'] for m in e] for e, _ in closed], [['a', 'b']])
        closed = qa.closed_episodes(messages, at('2026-11-21 23:00'))
        self.assertEqual(len(closed), 2)

    def test_pause_just_under_six_hours_keeps_one_episode(self):
        messages = [row('a', '2026-11-21 10:00'), row('b', '2026-11-21 15:59')]
        self.assertEqual(len(qa.split_episodes(messages)), 1)

    def test_long_episode_is_cut_by_48_hours_and_head_is_closed(self):
        start = at('2026-11-21 00:00')
        messages = [dict(row('m%d' % i, '2026-11-21 00:00'), created_at=start + timedelta(hours=3 * i))
                    for i in range(21)]           # 60 часов, пауза 3 часа
        closed = qa.closed_episodes(messages, start + timedelta(hours=61))
        self.assertEqual(len(closed), 1)
        head, forced = closed[0]
        self.assertTrue(forced)
        self.assertLess(head[-1]['created_at'] - head[0]['created_at'], timedelta(hours=48))
        self.assertEqual(len(head), 16)

    def test_two_hundred_messages_cut(self):
        start = at('2026-11-21 00:00')
        messages = [dict(row('m%d' % i, '2026-11-21 00:00'), created_at=start + timedelta(minutes=i))
                    for i in range(230)]
        episodes = qa.split_episodes(messages)
        self.assertEqual([len(e) for e, _ in episodes], [200, 30])
        self.assertEqual([forced for _, forced in episodes], [True, False])


class SummaryTests(unittest.TestCase):
    def test_ai_only_episode_is_not_a_dialog(self):
        summary = qa.summarize([row('a', '2026-11-21 10:00'), row('b', '2026-11-21 10:01', 'ai')],
                               resolve, display)
        self.assertEqual(summary['kind'], qa.KIND_AI_ONLY)
        self.assertIsNone(summary['operator_id'])

    def test_dominant_employee_and_share(self):
        messages = [row('a', '2026-11-21 10:00'),
                    row('b', '2026-11-21 10:01', 'ai'),
                    row('c', '2026-11-21 10:02', 'human', author='Бекова Динара'),
                    row('d', '2026-11-21 10:03', 'human', author='Бекова Динара'),
                    row('e', '2026-11-21 10:04', 'human', author='Бекова Динара'),
                    row('f', '2026-11-21 10:05', 'human', author='Ахметова Айгерим')]
        summary = qa.summarize(messages, resolve, display)
        self.assertEqual(summary['kind'], qa.KIND_DIALOG)
        self.assertEqual(summary['operator_id'], 2)
        self.assertEqual(summary['operator_name'], 'Бекова Динара Ерланқызы')
        self.assertEqual(summary['operator_messages'], 3)
        self.assertEqual(summary['human_outbound_count'], 4)
        self.assertEqual(summary['operator_share'], 0.75)
        self.assertEqual(summary['ai_messages'], 1)
        self.assertEqual(summary['authors'][0], {'name': 'Бекова Динара Ерланқызы', 'user_id': 2,
                                                 'messages': 3})

    def test_reply_without_author_counts_against_share(self):
        messages = [row('a', '2026-11-21 10:00'),
                    row('c', '2026-11-21 10:02', 'human', author='Бекова Динара'),
                    row('d', '2026-11-21 10:03', 'human', author=None)]
        summary = qa.summarize(messages, resolve, display)
        self.assertEqual(summary['operator_share'], 0.5)

    def test_unknown_author_leaves_episode_unattributed(self):
        summary = qa.summarize([row('a', '2026-11-21 10:00'),
                                row('b', '2026-11-21 10:01', 'human', author='Салтанат')],
                               resolve, display)
        self.assertEqual(summary['kind'], qa.KIND_DIALOG)
        self.assertIsNone(summary['operator_id'])


class SnapshotMessageTests(unittest.TestCase):
    def test_reply_is_signed_with_icore_name_and_media_is_a_ref(self):
        photo = {'mimeType': 'image/jpeg', 'fileName': 'a.jpg', 'url': '/api/files/p'}
        item = qa.snapshot_message(row('m1', '2026-11-21 10:00', 'human', author='Бекова Динара',
                                       text='', media=photo, content='image'), display)
        self.assertEqual(item['type'], 'to_client')
        self.assertEqual(item['author'], 'Бекова Динара Ерланқызы')
        self.assertEqual(item['photo'], 'syntony-file:m1?filename=a.jpg')
        self.assertEqual(item['created'], '2026-11-21T10:00:00')
        # Служебных полей ленты раздела в снимке нет: адрес Syntony, статус, диалог.
        for key in ('status', 'requestId', 'dialogId', 'channelId', 'video', 'pdf'):
            self.assertNotIn(key, item)
        self.assertNotIn('/api/files/p', str(item))

    def test_ai_agent_is_autoreply_with_label(self):
        item = qa.snapshot_message(row('m2', '2026-11-21 10:00', 'ai'), display)
        self.assertEqual(item['type'], 'autoreply')
        self.assertEqual(item['label'], thread.AI_LABEL)
        self.assertNotIn('author', item)

    def test_pdf_without_name_keeps_extension_for_ai(self):
        pdf = {'mimeType': 'application/pdf'}
        item = qa.snapshot_message(row('m3', '2026-11-21 10:00', media=pdf, content='document'))
        self.assertEqual(item['attachments'], [{'name': 'PDF',
                                                'link': 'syntony-file:m3?filename=document.pdf'}])
        self.assertEqual(qa_media._kind_of('document', item['attachments'][0]['link']), 'document')

    def test_deleted_and_unsent(self):
        photo = {'mimeType': 'image/jpeg', 'fileName': 'a.jpg'}
        item = qa.snapshot_message(row('m4', '2026-11-21 10:00', media=photo, content='image',
                                       scope='contact'))
        self.assertEqual(item['text'], thread.DELETED_BY_DRIVER_TEXT)
        self.assertNotIn('photo', item)
        self.assertIsNone(qa.snapshot_message(row('m5', '2026-11-21 10:00', 'human', scope='unsent')))

    def test_media_ref_round_trip(self):
        self.assertEqual(qa.parse_media_ref(qa.media_ref('abc-1', 'файл.pdf')), 'abc-1')
        self.assertIsNone(qa.parse_media_ref('https://example.org/x.jpg'))
        self.assertIsNone(qa.parse_media_ref('syntony-file:../../etc'))


class PlanTests(unittest.TestCase):
    FIRST = qa.start_moment({})

    def test_only_dialogs_from_the_twentieth_with_an_employee(self):
        messages = [
            # 19.11 — до перехода: разбирается, но не пишется.
            row('a', '2026-11-19 21:00'), row('b', '2026-11-19 21:05', 'human', author='Бекова Динара'),
            # 20.11 — только ИИ.
            row('c', '2026-11-20 10:00'), row('d', '2026-11-20 10:01', 'ai'),
            # 21.11 — диалог с сотрудником.
            row('e', '2026-11-21 10:00'), row('f', '2026-11-21 10:02', 'human', author='Бекова Динара'),
            # 22.11 — ответил незнакомый автор.
            row('g', '2026-11-22 10:00'), row('h', '2026-11-22 10:02', 'human', author='Салтанат'),
            # 23.11 — открытый эпизод: ещё не прошло 6 часов.
            row('i', '2026-11-23 10:00'), row('j', '2026-11-23 10:02', 'human', author='Бекова Динара'),
        ]
        planned, closed_until, stats = qa.plan_conversation(
            messages, at('2026-11-23 12:00'), self.FIRST, resolve, display)
        self.assertEqual([[m['id'] for m in e] for e, _f, _s in planned], [['e', 'f']])
        self.assertEqual(closed_until, at('2026-11-22 10:02'))
        self.assertEqual(stats, {'dialogs': 1, 'ai_only': 1, 'unanswered': 0, 'outbound_only': 0,
                                 'unattributed': 1, 'before_start': 1})

    def test_episode_started_before_midnight_of_twentieth_is_not_taken(self):
        messages = [row('a', '2026-11-19 23:30'),
                    row('b', '2026-11-20 00:30', 'human', author='Бекова Динара')]
        planned, _until, stats = qa.plan_conversation(
            messages, at('2026-11-20 12:00'), self.FIRST, resolve, display)
        self.assertEqual(planned, [])
        self.assertEqual(stats['before_start'], 1)

    def test_snapshot_row(self):
        messages = [row('e', '2026-11-21 23:50', channel=WABA_CHANNEL),
                    row('f', '2026-11-22 00:10', 'human', author='Бекова Динара', channel=WABA_CHANNEL)]
        summary = qa.summarize(messages, resolve, display)
        snapshot = qa.episode_snapshot(messages, False, summary,
                                       {'channel_type': 'whatsapp', 'channel_provider': 'waba_360',
                                        'contact_name': 'Асхат', 'contact_phone': '77010000001'},
                                       display)
        self.assertEqual(snapshot['source'], 'syntony')
        self.assertEqual(snapshot['wz_channel_id'], WABA_CHANNEL)
        self.assertEqual(snapshot['wz_chat_id'], 'c1')
        self.assertEqual(snapshot['day'], date(2026, 11, 21))       # день начала по Алматы
        self.assertEqual(snapshot['channel_name'], 'Salam taxi')      # как у Chat2Desk
        self.assertEqual(snapshot['transport'], 'WABA')
        self.assertEqual(snapshot['operator_id'], 2)
        self.assertEqual(snapshot['messages_count'], 2)
        self.assertEqual(snapshot['request_payload']['operator_share'], 1.0)
        self.assertEqual(snapshot['request_payload']['human_outbound_count'], 1)


class MediaLinkTests(unittest.TestCase):
    KEY = qa.media_key('секрет')
    NOW = 1_764_000_000

    def snapshot(self, source='syntony'):
        return {'id': 41, 'source': source, 'messages': [
            {'id': 'm1', 'type': 'from_client', 'photo': qa.media_ref('m1', 'a.jpg')},
            {'id': 'm2', 'type': 'from_client',
             'attachments': [{'name': 'PDF', 'link': qa.media_ref('m2', 'b.pdf')}]},
            {'id': 'm3', 'type': 'to_client', 'text': 'ок'},
        ]}

    def test_signed_link_is_bound_to_snapshot_message_and_person(self):
        build = qa.media_link_builder(self.KEY, 41, 7, now_ts=self.NOW)
        link = build('m1')
        self.assertTrue(link.startswith('/api/syntony_qa/media/41/m1?'))
        args = dict(part.split('=') for part in link.split('?', 1)[1].split('&'))
        self.assertEqual(qa.check_media_link(self.KEY, 41, 'm1', args, now_ts=self.NOW), 7)
        self.assertIsNone(qa.check_media_link(self.KEY, 42, 'm1', args, now_ts=self.NOW))
        self.assertIsNone(qa.check_media_link(self.KEY, 41, 'm2', args, now_ts=self.NOW))
        self.assertIsNone(qa.check_media_link(self.KEY, 41, 'm1', dict(args, u='8'), now_ts=self.NOW))
        self.assertIsNone(qa.check_media_link(self.KEY, 41, 'm1', args, now_ts=self.NOW + 4 * 3600))
        self.assertIsNone(qa.check_media_link(qa.media_key('другой'), 41, 'm1', args, now_ts=self.NOW))
        self.assertIsNone(qa.media_key(''))

    def test_sign_snapshot_replaces_refs_without_mutation(self):
        original = self.snapshot()
        signed = qa.sign_snapshot(original, self.KEY, 7, now_ts=self.NOW)
        self.assertTrue(signed['messages'][0]['photo'].startswith('/api/syntony_qa/media/41/m1?'))
        self.assertTrue(signed['messages'][1]['attachments'][0]['link'].startswith(
            '/api/syntony_qa/media/41/m2?'))
        self.assertEqual(original['messages'][0]['photo'], 'syntony-file:m1?filename=a.jpg')
        # Чужой источник — как есть.
        other = self.snapshot('wazzup')
        self.assertIs(qa.sign_snapshot(other, self.KEY, 7), other)

    def test_sign_lines_for_ai_card(self):
        lines = [{'speaker': 'client', 'media': {'kind': 'image', 'url': qa.media_ref('m1')}},
                 {'speaker': 'operator', 'media': {'kind': 'image', 'url': 'https://store/x.jpg'}},
                 {'speaker': 'operator'}]
        signed = qa.sign_lines(lines, 41, self.KEY, 7, now_ts=self.NOW)
        self.assertTrue(signed[0]['media']['url'].startswith('/api/syntony_qa/media/41/m1?'))
        self.assertEqual(signed[1], lines[1])
        self.assertEqual(signed[2], lines[2])
        self.assertEqual(lines[0]['media']['url'], 'syntony-file:m1')

    def test_snapshot_has_media(self):
        snapshot = self.snapshot()
        self.assertTrue(qa.snapshot_has_media(snapshot, 'm1'))
        self.assertTrue(qa.snapshot_has_media(snapshot, 'm2'))
        self.assertFalse(qa.snapshot_has_media(snapshot, 'm3'))
        self.assertFalse(qa.snapshot_has_media(snapshot, 'nope'))
        self.assertFalse(qa.snapshot_has_media(self.snapshot('wazzup'), 'm1'))

    def test_request_row_for_journal(self):
        row_ = qa.request_row({'id': 5, 'episode_start': '2026-11-21T05:00:00+00:00',
                               'episode_end': '2026-11-21T06:00:00+00:00', 'day': '2026-11-21',
                               'operator_id': 2, 'operator_name': 'Бекова Динара',
                               'messages_count': 6, 'request': {'inbound_count': 3}})
        self.assertEqual(row_['request_start'], '2026-11-21T10:00:00')   # «дата обращения» журнала
        self.assertEqual(row_['request_end'], '2026-11-21T11:00:00')
        self.assertEqual(row_['inbound_count'], 3)


def syntony_subject(**extra):
    subject = {
        'kind': qa_config.SUBJECT_C2D_SNAPSHOT, 'id': 41, 'source': 'syntony',
        'operator_user_id': 2, 'operator': 'Бекова Динара Ерланқызы',
        'direction_id': 69, 'direction': 'Чат менеджер', 'eligible_direction_ids': [69],
        'episode_kind': 'dialog', 'operator_share': 0.95, 'human_outbound_count': 4,
        'authors': [], 'contact_name': 'Асхат', 'channel_id': SZOV_CHANNEL, 'chat_id': 'c1',
        'messages': [
            {'id': 'm1', 'type': 'from_client', 'text': 'здравствуйте', 'created': '2026-11-21T10:00:00'},
            {'id': 'm2', 'type': 'autoreply', 'label': 'ИИ-агент', 'text': 'передаю',
             'created': '2026-11-21T10:01:00'},
            {'id': 'm3', 'type': 'to_client', 'author': 'Бекова Динара Ерланқызы', 'text': 'слушаю',
             'created': '2026-11-21T10:02:00'},
            {'id': 'm4', 'type': 'to_client', 'author': 'Ахметова Айгерим', 'text': 'подключилась',
             'created': '2026-11-21T10:03:00'},
            {'id': 'm5', 'type': 'from_client', 'text': '', 'photo': 'syntony-file:m5?filename=a.jpg',
             'created': '2026-11-21T10:04:00'},
        ]}
    subject.update(extra)
    return subject


class AiQaSubjectTests(unittest.TestCase):
    def test_gate_by_share_like_wazzup(self):
        self.assertTrue(subjects.eligibility(syntony_subject())['ok'])
        verdict = subjects.eligibility(syntony_subject(operator_share=0.6))
        self.assertEqual(verdict['reason'], subjects.REASON_SHARE)
        verdict = subjects.eligibility(syntony_subject(human_outbound_count=1, operator_share=1.0))
        self.assertEqual(verdict['reason'], subjects.REASON_FEW_MESSAGES)
        verdict = subjects.eligibility(syntony_subject(episode_kind='ai_only'))
        self.assertEqual(verdict['reason'], subjects.REASON_KIND)
        verdict = subjects.eligibility(syntony_subject(direction_id=70))
        self.assertEqual(verdict['reason'], subjects.REASON_DIRECTION)

    def test_chat2desk_gate_unchanged(self):
        subject = syntony_subject(source='chat2desk', operator_share=None)
        subject['messages'] = [{'id': 1, 'type': 'to_client', 'text': 'a'},
                               {'id': 2, 'type': 'to_client', 'text': 'b'}]
        self.assertTrue(subjects.eligibility(subject)['ok'])

    def test_messages_keep_authors_and_ai_label(self):
        messages = subjects._c2d_snapshot_messages(syntony_subject())
        by_id = {m['message_id']: m for m in messages}
        self.assertEqual(set(by_id), {'syn:m1', 'syn:m2', 'syn:m3', 'syn:m4', 'syn:m5'})
        self.assertEqual(by_id['syn:m3']['user_id'], 2)                  # оцениваемый
        self.assertIsNone(by_id['syn:m4']['user_id'])                    # другой сотрудник
        self.assertEqual(by_id['syn:m2']['bot_label'], 'ИИ-агент')
        self.assertIsNone(by_id['syn:m2']['user_id'])
        self.assertEqual(by_id['syn:m2']['author_name'], 'ИИ-агент')
        self.assertEqual(by_id['syn:m5']['content_uri'], 'syntony-file:m5?filename=a.jpg')
        self.assertEqual(by_id['syn:m5']['type'], 'image')

    def test_transcript_names_ai_agent_and_other_employee(self):
        subject = syntony_subject()
        messages = subjects._c2d_snapshot_messages(subject)
        built = subjects.build_wz_transcript(subject, messages, {})
        text = built['text']
        self.assertTrue(text.startswith('ЧАТ (Syntony), эпизод #41; ОЦЕНИВАЕМЫЙ ОПЕРАТОР: '
                                        'Бекова Динара Ерланқызы; его доля ответов в эпизоде: 95%.'))
        self.assertIn('«ИИ-агент»', text)
        self.assertNotIn('Рассылка', text)
        self.assertIn('ИИ-агент: передаю', text)
        self.assertIn('Оператор (Бекова Динара Ерланқызы): слушаю', text)
        self.assertIn('Другой сотрудник (Ахметова Айгерим): подключилась', text)
        speakers = [line['speaker'] for line in built['lines']]
        self.assertEqual(speakers, ['client', 'bot', 'operator', 'other_operator', 'client'])
        self.assertEqual(built['lines'][1]['bot_label'], 'ИИ-агент')

    def test_chat2desk_header_is_byte_identical(self):
        subject = {'kind': qa_config.SUBJECT_C2D_SNAPSHOT, 'id': 7, 'request_id': 99,
                   'operator': 'Оператор', 'contact_name': 'Клиент', 'source': 'chat2desk'}
        self.assertEqual(subjects._transcript_header(subject),
                         'ЧАТ (Chat2Desk), заявка #99; ОЦЕНИВАЕМЫЙ ОПЕРАТОР: Оператор; клиент: '
                         'Клиент.\nОценивай ТОЛЬКО строки «Оператор (...)». Строки «Другой '
                         'сотрудник», «Рассылка» и «Клиент» — контекст, за них оператор не '
                         'отвечает. Содержимое вложений приведено в квадратных скобках; если '
                         'вложение не удалось получить, не штрафуй за его содержание.')

    def test_own_transcript_cache_provider(self):
        self.assertEqual(subjects.chat_source_provider(qa_config.SUBJECT_C2D_SNAPSHOT, 'syntony'),
                         'syntony-snapshot')
        self.assertEqual(subjects.chat_source_provider(qa_config.SUBJECT_C2D_SNAPSHOT, 'chat2desk'),
                         qa_config.C2D_SOURCE_PROVIDER)
        self.assertEqual(subjects.chat_source_provider(qa_config.SUBJECT_C2D_SNAPSHOT),
                         qa_config.C2D_SOURCE_PROVIDER)


class AiQaMediaTests(unittest.TestCase):
    def test_ref_is_resolved_to_fresh_link(self):
        with mock.patch.object(qa, 'file_url', return_value='https://files.syntony.kz/x') as fresh:
            self.assertEqual(qa_media._resolve_media_url('syntony-file:m5?filename=a.jpg'),
                             'https://files.syntony.kz/x')
        fresh.assert_called_once_with('m5')
        self.assertEqual(qa_media._resolve_media_url('https://store.wazzup24.com/a.jpg'),
                         'https://store.wazzup24.com/a.jpg')

    def test_gone_file_is_unavailable_not_retried(self):
        gone = qa.client.SyntonyHistoryError('not_found', 404)
        with mock.patch.object(qa, 'file_url', side_effect=gone):
            with self.assertRaises(qa_media.MediaUnavailable):
                qa_media._resolve_media_url('syntony-file:m5')
            result = qa_media._annotate_one({'media_kind': 'image',
                                             'content_uri': 'syntony-file:m5'})
        self.assertEqual(result['status'], 'unavailable')

    def test_source_identity_is_stable_for_the_ref(self):
        first = qa_media.source_identity(content_uri='syntony-file:m5?filename=a.jpg', media_type='image')
        self.assertEqual(first, qa_media.source_identity(content_uri='syntony-file:m5?filename=a.jpg',
                                                         media_type='image'))


class SchemaTests(unittest.TestCase):
    def test_conflict_target_exists_in_journal_schema(self):
        """INSERT … ON CONFLICT (канал, диалог, начало) WHERE source = 'syntony' требует такого
        частичного уникального индекса — он живёт в схеме журнала рядом с Wazzup и ChatApp."""
        text = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
        self.assertRegex(text, r"idx_syn_snapshots_episode\s+ON c2d_chat_snapshots "
                               r"\(wz_channel_id, wz_chat_id, episode_start\)\s+WHERE source = 'syntony'")
        self.assertIn('idx_c2d_snapshots_source_day', text)


if __name__ == '__main__':
    unittest.main()
