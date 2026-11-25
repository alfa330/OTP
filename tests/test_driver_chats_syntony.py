# -*- coding: utf-8 -*-
"""«Чаты водителей» на Syntony (25.11.2026) — без базы: правила ленты, поиск водителя по хвосту,
подпись ссылок на вложения, ручки поиска, «Передан», «открыл», контекста и вложений.

SQL на настоящем Postgres — tests/test_driver_chats_syntony_postgres.py. Номера и люди выдуманы.
"""

import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from driver_chats import chat2desk, queries, syntony  # noqa: E402
from syntony_chats import client as syntony_client  # noqa: E402

if Flask is not None:
    from driver_chats.routes import build_driver_chats_blueprint  # noqa: E402

SZOV = 'b40df588-75cc-4562-be29-b1e73580fd49'      # «Ноль такси», номер СЗоВ (szov.SZOV_CHANNEL_IDS)
SZOV_2 = '39114c57-557e-4549-9af2-7d169524272e'    # «Ноль такси 2»
NOT_SZOV = 'cd824063-38ca-481f-8c54-99b4455d8840'  # чужой номер сети
VIEW_JSX = ROOT / 'src' / 'components' / 'driver_chats' / 'DriverChatsView.jsx'
THREAD_JSX = ROOT / 'src' / 'components' / 'c2d_eval' / 'ChatThread.jsx'


def utc(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)


def row(mid, *, direction='inbound', sender='contact', author=None, content='text', text='текст',
        media=None, created='2026-11-25T05:00:00Z', scope=None, channel=SZOV, conversation='conv-1',
        park='Ноль такси', channel_name='WhatsApp 77000000000'):
    """Строка окна в порядке колонок syntony._WINDOW_SQL."""
    return (mid, conversation, direction, sender, author, content, text, media, utc(created), scope,
            'received', channel, park, channel_name)


def build_media(message_id):
    return '/api/driver_chats/media/%s?s=x' % message_id


class SourceTests(unittest.TestCase):
    def test_explicit_setting_wins(self):
        self.assertEqual(syntony.source({'DRIVER_CHATS_SOURCE': 'chat2desk'}), 'chat2desk')
        self.assertEqual(syntony.source({'DRIVER_CHATS_SOURCE': ' Syntony '}), 'syntony')

    def test_default_follows_whether_syntony_data_flows_into_the_base(self):
        with mock.patch.object(syntony.szov, 'data_enabled', return_value=True):
            self.assertEqual(syntony.source({}), 'syntony')
        with mock.patch.object(syntony.szov, 'data_enabled', return_value=False):
            self.assertEqual(syntony.source({}), 'chat2desk')

    def test_channels_are_the_szov_numbers_of_the_wallboard(self):
        self.assertEqual(set(syntony.channel_ids({})), set(syntony.szov.SZOV_CHANNEL_IDS))
        self.assertEqual(len(syntony.channel_ids({})), 17)

    def test_clean_id_lets_only_safe_ids_into_the_journal(self):
        self.assertEqual(syntony.clean_id(' ' + SZOV + ' '), SZOV)
        for junk in ('', None, '../x', 'a b', 'x' * 81, "1' OR '1"):
            with self.subTest(junk=junk):
                self.assertIsNone(syntony.clean_id(junk))


class MessageShapeTests(unittest.TestCase):
    def test_driver_is_on_the_left_and_time_is_almaty(self):
        item = syntony.message_from_row(row('m1'), build_media)
        self.assertEqual(item['type'], 'from_client')
        # 05:00 UTC → 10:00 по Алматы, без пояса — как время ленты Chat2Desk.
        self.assertEqual(item['created'], '2026-11-25T10:00:00')
        self.assertEqual((item['channelId'], item['dialogId'], item['requestId']),
                         (SZOV, 'conv-1', None))
        self.assertNotIn('author', item)

    def test_staff_reply_is_signed_with_its_author(self):
        item = syntony.message_from_row(
            row('m2', direction='outbound', sender='human', author='Бекова Динара'), build_media)
        self.assertEqual((item['type'], item['author']), ('to_client', 'Бекова Динара'))

    def test_reply_without_author_is_not_signed_with_someone_elses_name(self):
        # Ответ через API (waba_api) или с телефона приходит без автора.
        item = syntony.message_from_row(row('m3', direction='outbound', sender='human'), build_media)
        self.assertEqual(item['type'], 'to_client')
        self.assertIn('author', item)
        self.assertIsNone(item['author'])

    def test_ai_agent_is_an_autoreply_labelled_as_such(self):
        item = syntony.message_from_row(row('m4', direction='outbound', sender='ai'), build_media)
        self.assertEqual((item['type'], item['label']), ('autoreply', 'ИИ-агент'))

    def test_unknown_sender_never_speaks_for_the_driver(self):
        item = syntony.message_from_row(row('m5', direction='outbound', sender='robot'), build_media)
        self.assertEqual(item['type'], 'autoreply')
        self.assertIsNone(item['label'])

    def test_call_line_is_a_system_line(self):
        item = syntony.message_from_row(
            row('m6', direction='outbound', sender='system', content='call', text=''), build_media)
        self.assertEqual((item['type'], item['text']), ('system', 'Звонок'))

    def test_deleted_messages(self):
        photo = {'mimeType': 'image/jpeg', 'fileName': 'a.jpg', 'url': '/api/chats-api/v1/files/m7'}
        by_driver = syntony.message_from_row(row('m7', content='image', media=photo, scope='contact'),
                                             build_media)
        self.assertEqual(by_driver['text'], 'Водитель удалил сообщение')
        self.assertIsNone(by_driver['photo'], 'файл у Syntony стёрт — ссылки нет')
        everyone = syntony.message_from_row(
            row('m8', direction='outbound', sender='human', text='секрет', scope='everyone'), build_media)
        self.assertEqual(everyone['text'], 'Сообщение удалено')
        self.assertIsNone(syntony.message_from_row(row('m9', scope='unsent'), build_media),
                          'не ушедшее сообщение в ленту не идёт')

    def test_media_kinds(self):
        def media(mime, name='f'):
            return {'mimeType': mime, 'fileName': name, 'url': '/api/chats-api/v1/files/x'}
        cases = [
            ('image', media('image/jpeg'), 'photo'),
            ('sticker', media('image/webp'), 'photo'),
            ('document', media('image/jpeg'), 'photo'),
            ('video', media('video/mp4'), 'video'),
            ('audio', media('audio/ogg'), 'audio'),
            ('audio', media('video/mp4'), 'audio'),
        ]
        for content, payload, field in cases:
            with self.subTest(content=content, mime=payload['mimeType']):
                item = syntony.message_from_row(row('x1', content=content, media=payload, text=''),
                                                build_media)
                self.assertEqual(item[field], '/api/driver_chats/media/x1?s=x')

    def test_documents_carry_their_file_name(self):
        pdf = syntony.message_from_row(row('d1', content='document', media={
            'mimeType': 'application/pdf', 'fileName': 'техпаспорт.pdf', 'url': 'u'}), build_media)
        self.assertEqual(pdf['attachments'], [{'name': 'техпаспорт.pdf',
                                               'link': '/api/driver_chats/media/d1?s=x'}])
        unnamed = syntony.message_from_row(row('d2', content='document', media={
            'mimeType': 'application/pdf', 'url': 'u'}), build_media)
        self.assertEqual(unnamed['attachments'][0]['name'], 'PDF')
        docx = syntony.message_from_row(row('d3', content='document', media={
            'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'url': 'u'}), build_media)
        self.assertEqual(docx['attachments'][0]['name'], 'Файл')

    def test_without_a_signing_key_media_is_simply_absent(self):
        item = syntony.message_from_row(
            row('x2', content='image', media={'mimeType': 'image/jpeg', 'url': 'u'}), None)
        self.assertIsNone(item['photo'])
        self.assertEqual(item['attachments'], [])


class ChatGroupingTests(unittest.TestCase):
    def test_chat_is_the_park_and_keys_take_guid_channels(self):
        rows = [row('a1', created='2026-11-25T05:00:00Z'),
                row('b1', created='2026-11-25T06:00:00Z', channel=SZOV_2, conversation='conv-2'),
                row('a2', direction='outbound', sender='human', author='Бекова Динара',
                    created='2026-11-25T05:01:00Z')]
        messages = [syntony.message_from_row(r, build_media) for r in rows]
        chats = chat2desk.group_chats(messages)
        self.assertEqual([chat['key'] for chat in chats], ['c' + SZOV_2, 'c' + SZOV],
                         'свежий сверху; ключ — тот же, что подставляет фронт: c${channel_id}')
        self.assertEqual(chats[1]['authors'], ['Бекова Динара'])
        self.assertEqual(chat2desk.chat_key(SZOV), 'c' + SZOV)
        self.assertEqual(chat2desk.chat_key(3624), 'c3624')
        self.assertEqual(chat2desk.chat_key('3624'), 'c3624', 'Chat2Desk — как раньше')

    def test_same_second_messages_with_string_ids_do_not_break_the_sort(self):
        messages = [dict(syntony.message_from_row(row('b'), build_media)),
                    dict(syntony.message_from_row(row('a'), build_media)),
                    syntony.handoff_message(event_id=5, text='Передан', created='2026-11-25T10:00:00',
                                            channel_id=SZOV)]
        chat, = chat2desk.group_chats(messages)
        self.assertEqual([m['id'] for m in chat['messages']], ['a', 'b', 'dch-5'])

    def test_park_names_follow_the_billing_names(self):
        self.assertEqual(syntony.channel_name(SZOV, 'Ноль такси', 'WhatsApp …'), 'Ноль такси')
        self.assertEqual(syntony.channel_name(SZOV_2), 'Ноль такси 2')
        self.assertEqual(syntony.channel_name('other', 'Таксопарк Global', 'x'), 'Таксопарк Global')
        self.assertEqual(syntony.channel_name('other', None, 'WhatsApp 77000'), 'WhatsApp 77000')


class FindContactTests(unittest.TestCase):
    def lookup(self, rows, phone=None, tail=None):
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        result = syntony.find_contact(cursor, phone, tail, [SZOV])
        return result, cursor

    def test_full_number_picks_its_own_record_among_tail_matches(self):
        found, cursor = self.lookup([('77011234567', 'Асхат'), ('99871234567', None)],
                                    phone='87011234567', tail='011234567')
        self.assertEqual(found, ('77011234567', 'Асхат'))
        params = cursor.execute.call_args[0][1]
        self.assertEqual((params['tail'], params['channels']), ('011234567', [SZOV]))

    def test_single_number_behind_the_tail_is_the_driver(self):
        found, _ = self.lookup([('905317298361', None)], tail='317298361')
        self.assertEqual(found, ('905317298361', None))

    def test_two_drivers_behind_one_tail_is_ambiguous(self):
        found, _ = self.lookup([('905317298361', None), ('995317298361', None)], tail='317298361')
        self.assertEqual(found, ('ambiguous', None))

    def test_nobody(self):
        self.assertIsNone(self.lookup([], tail='317298361')[0])
        self.assertIsNone(syntony.find_contact(MagicMock(), None, None, [SZOV]))


class MediaLinkTests(unittest.TestCase):
    KEY = syntony.media_key('секрет сервера')
    NOW = 1_764_050_000

    def args(self, link):
        query = link.split('?', 1)[1]
        return dict(part.split('=', 1) for part in query.split('&'))

    def test_signed_link_opens_for_its_message_and_person_only(self):
        build = syntony.media_url_builder(self.KEY, 7, now_ts=self.NOW)
        link = build('0f4b7c31-aaaa-bbbb-cccc-0123456789ab')
        self.assertTrue(link.startswith('/api/driver_chats/media/0f4b7c31-aaaa-bbbb-cccc-0123456789ab?'))
        args = self.args(link)
        self.assertEqual(syntony.check_media_link(
            self.KEY, '0f4b7c31-aaaa-bbbb-cccc-0123456789ab', args, now_ts=self.NOW + 60), 7)
        self.assertIsNone(syntony.check_media_link(self.KEY, 'другое-сообщение', args, now_ts=self.NOW))
        self.assertIsNone(syntony.check_media_link(
            self.KEY, '0f4b7c31-aaaa-bbbb-cccc-0123456789ab', dict(args, u='8'), now_ts=self.NOW))
        self.assertIsNone(syntony.check_media_link(
            self.KEY, '0f4b7c31-aaaa-bbbb-cccc-0123456789ab', dict(args, s=args['s'][:-1] + '0'),
            now_ts=self.NOW))
        self.assertIsNone(syntony.check_media_link(
            syntony.media_key('другой секрет'), '0f4b7c31-aaaa-bbbb-cccc-0123456789ab', args,
            now_ts=self.NOW))

    def test_link_lives_two_to_three_hours_and_is_stable_within_the_hour(self):
        build = syntony.media_url_builder(self.KEY, 7, now_ts=self.NOW)
        args = self.args(build('m1'))
        expires = int(args['e'])
        self.assertTrue(2 * 3600 <= expires - self.NOW <= 3 * 3600)
        self.assertEqual(build('m1'), syntony.media_url_builder(self.KEY, 7, now_ts=self.NOW + 30)('m1'))
        self.assertIsNone(syntony.check_media_link(self.KEY, 'm1', args, now_ts=expires + 1))
        # Срок из далёкого будущего — подделка срока, а не «вечная» ссылка.
        far = dict(args, e=str(self.NOW + 30 * 24 * 3600))
        far['s'] = syntony.media_signature(self.KEY, 'm1', 7, int(far['e']))
        self.assertIsNone(syntony.check_media_link(self.KEY, 'm1', far, now_ts=self.NOW))

    def test_without_key_or_with_a_bad_id_there_is_no_link(self):
        self.assertIsNone(syntony.media_key(''))
        self.assertIsNone(syntony.media_url_builder(None, 7))
        build = syntony.media_url_builder(self.KEY, 7, now_ts=self.NOW)
        self.assertIsNone(build('../../files/x'))
        self.assertIsNone(syntony.check_media_link(self.KEY, '../x', {'u': '7', 'e': '1', 's': 'x'}))
        self.assertIsNone(syntony.check_media_link(self.KEY, 'm1', {}))


class FileLinkClientTests(unittest.TestCase):
    CONFIG = syntony_client.Config(url='https://app.syntony.kz/api/chats-api/v1', key='sck_' + 'a' * 32)

    def test_link_must_be_https_on_the_syntony_domain(self):
        for url in ('http://app.syntony.kz/minio/a', 'https://evil.example/minio/a',
                    'https://app.syntony.kz.evil.example/a', 'javascript:alert(1)', ''):
            with self.subTest(url=url), mock.patch.object(syntony_client, 'get',
                                                          return_value={'url': url}):
                with self.assertRaises(syntony_client.SyntonyHistoryError):
                    syntony_client.file_link(self.CONFIG, 'm1')
        with mock.patch.object(syntony_client, 'get', return_value={
                'url': 'https://app.syntony.kz/minio/a?X-Amz-Signature=1', 'mimeType': 'image/jpeg'}) as get:
            link = syntony_client.file_link(self.CONFIG, 'id/with/slash')
        self.assertEqual(link['url'], 'https://app.syntony.kz/minio/a?X-Amz-Signature=1')
        self.assertEqual(get.call_args[0][1], '/files/id%2Fwith%2Fslash')


@unittest.skipIf(Flask is None, 'flask не установлен')
class SyntonyRoutesTests(unittest.TestCase):
    """Ручки раздела целиком при DRIVER_CHATS_SOURCE=syntony."""

    def build(self, *, found=('77011234567', 'Асхат'), rows=(), notes=(), used=0, extra=0,
              role='operator', stale=None, has_channel=True, person_status='working',
              media_ok=True):
        context = {'user_id': 7, 'name': 'Оператор СЗоВ', 'role': role,
                   'department_id': 1, 'department_code': 'szov', 'direction_model': 'operator',
                   'status': person_status,
                   'headed_department_ids': [], 'headed_department_codes': []}
        seen = {'logged': [], 'vendor': [], 'comment': [], 'file_link': []}
        cursor = MagicMock()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _patch(module, name, value):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)

        def _log(_c, _ctx, kind, **kwargs):
            seen['logged'].append(dict(kwargs, kind=kind))
            return {'id': 41, 'created_at': '2026-11-25T10:05:00'}

        _patch(queries, 'load_access_context', lambda _c, _uid: dict(context))
        _patch(queries, 'search_usage_today', lambda _c, _uid: (used, extra))
        _patch(queries, 'log_event', _log)
        _patch(syntony, 'find_contact', lambda *_a, **_k: found)
        _patch(syntony, 'window_rows', lambda *_a, **_k: (list(rows), False))
        _patch(syntony, 'handoff_messages', lambda *_a, **_k: list(notes))
        _patch(syntony, 'stale_minutes', lambda *_a, **_k: stale)
        _patch(syntony, 'contact_has_channel', lambda *_a, **_k: has_channel)
        _patch(syntony, 'media_allowed', lambda *_a, **_k: media_ok)
        _patch(chat2desk, 'find_client', lambda phone: seen['vendor'].append(phone))
        _patch(chat2desk, 'send_internal_comment',
               lambda *a: seen['comment'].append(a) or {'message_id': 1})
        _patch(syntony_client, 'load_config', lambda *_a, **_k: syntony_client.Config(
            url='https://app.syntony.kz/api/chats-api/v1', key='sck_' + 'a' * 32))

        def _file_link(_config, message_id, **_k):
            seen['file_link'].append(message_id)
            return {'url': 'https://app.syntony.kz/minio/%s?X-Amz-Signature=abc' % message_id}

        _patch(syntony_client, 'file_link', _file_link)
        env = mock.patch.dict(os.environ, {'DRIVER_CHATS_SOURCE': 'syntony'})
        env.start()
        self.addCleanup(env.stop)

        app = Flask(__name__)
        app.register_blueprint(build_driver_chats_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _uid: True,
            media_secret='секрет сервера'))
        return app.test_client(), seen

    def test_search_answers_from_the_base_without_any_vendor(self):
        photo = {'mimeType': 'image/jpeg', 'fileName': 'a.jpg', 'url': '/api/chats-api/v1/files/p1'}
        client, seen = self.build(rows=[
            row('p1', content='image', media=photo, text=''),
            row('r1', direction='outbound', sender='human', author='Бекова Динара',
                created='2026-11-25T05:02:00Z'),
        ], notes=[syntony.handoff_message(event_id=40, text='Комментарий от оператора', created=
                                          '2026-11-25T10:03:00', channel_id=SZOV)])
        response = client.get('/api/driver_chats/search?phone=8+701+123+45+67')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual((payload['source'], payload['client_id'], payload['phone'],
                          payload['client_name']), ('syntony', None, '77011234567', 'Асхат'))
        chat, = payload['chats']
        self.assertEqual((chat['key'], chat['channel_name'], chat['operator_name']),
                         ('c' + SZOV, 'Ноль такси', 'Бекова Динара'))
        self.assertEqual([m['type'] for m in chat['messages']], ['from_client', 'to_client', 'comment'])
        link = chat['messages'][0]['photo']
        self.assertTrue(link.startswith('/api/driver_chats/media/p1?u=7&e='))
        self.assertEqual(seen['vendor'], [], 'к Chat2Desk не ходили')
        search, = seen['logged']
        self.assertEqual((search['kind'], search['phone'], search['source'], search['messages_count']),
                         ('search', '77011234567', 'syntony', 2))
        self.assertEqual(payload['searches_left'], 39)

    def test_not_found_and_ambiguous(self):
        client, seen = self.build(found=None)
        payload = client.get('/api/driver_chats/search?phone=87011234567').get_json()
        self.assertTrue(payload['not_found'])
        self.assertEqual(seen['logged'][0]['kind'], 'search')
        client, seen = self.build(found=('ambiguous', None))
        response = client.get('/api/driver_chats/search?phone=531+729+83+61')
        self.assertEqual((response.status_code, response.get_json()['code']), (400, 'PHONE_AMBIGUOUS'))
        self.assertEqual(seen['logged'][0]['kind'], 'search', 'искал — значит в журнале')

    def test_limit_stays_but_does_not_blame_the_chat2desk_quota(self):
        client, seen = self.build(used=40)
        response = client.get('/api/driver_chats/search?phone=87011234567')
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('Chat2Desk', response.get_json()['error'])
        self.assertEqual(seen['logged'], [], 'сверх лимита поиск не делается и не пишется')

    def test_stale_data_is_said_out_loud(self):
        client, _ = self.build(rows=[row('m1')], stale=14)
        self.assertEqual(client.get('/api/driver_chats/search?phone=87011234567')
                         .get_json()['stale_minutes'], 14)

    def test_handoff_is_a_journal_mark_on_the_drivers_own_szov_chat(self):
        client, seen = self.build()
        body = {'phone': '77011234567', 'client_id': None, 'channel_id': SZOV,
                'dialog_id': 'conv-1', 'channel_name': 'Ноль такси', 'note': 'просит перезвонить'}
        response = client.post('/api/driver_chats/handoff', json=body)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['delivered_to_chat'])
        self.assertEqual(payload['message']['id'], 'dch-41')
        self.assertEqual(payload['message']['type'], 'comment')
        self.assertEqual(payload['message']['channelId'], SZOV)
        logged, = seen['logged']
        self.assertEqual((logged['kind'], logged['source'], logged['syntony_channel_id'],
                          logged['syntony_conversation_id'], logged['phone']),
                         ('handoff', 'syntony', SZOV, 'conv-1', '77011234567'))
        self.assertEqual(logged['comment_text'],
                         'Комментарий от оператора Оператор СЗоВ: просит перезвонить')
        self.assertEqual(seen['comment'], [], 'в Chat2Desk заметка не уходит')

    def test_handoff_refuses_foreign_numbers_and_foreign_chats(self):
        client, seen = self.build()
        for channel in (NOT_SZOV, '', '../x'):
            with self.subTest(channel=channel):
                response = client.post('/api/driver_chats/handoff', json={
                    'phone': '77011234567', 'channel_id': channel})
                self.assertEqual(response.get_json()['code'], 'BAD_TARGET')
        client, seen = self.build(has_channel=False)
        response = client.post('/api/driver_chats/handoff', json={
            'phone': '77011234567', 'channel_id': SZOV})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(seen['logged'], [])

    def test_open_keeps_syntony_ids_in_their_own_columns(self):
        client, seen = self.build()
        client.post('/api/driver_chats/open', json={
            'phone': '77011234567', 'channel_id': SZOV, 'dialog_id': 'conv-1',
            'channel_name': 'Ноль такси', 'messages_count': 4})
        logged, = seen['logged']
        self.assertEqual((logged['kind'], logged['source'], logged['syntony_channel_id'],
                          logged['syntony_conversation_id'], logged.get('channel_id')),
                         ('open', 'syntony', SZOV, 'conv-1', None))

    def test_context_tells_the_screen_where_the_mark_goes(self):
        client, _ = self.build()
        payload = client.get('/api/driver_chats/context').get_json()
        self.assertEqual((payload['source'], payload['handoff_to_chat']), ('syntony', False))

    def _signed(self, message_id='p1', user_id=7):
        key = syntony.media_key('секрет сервера')
        return syntony.media_url_builder(key, user_id)(message_id)

    def test_media_redirects_to_a_fresh_syntony_link(self):
        client, seen = self.build()
        response = client.get(self._signed())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'https://app.syntony.kz/minio/p1?X-Amz-Signature=abc')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(seen['file_link'], ['p1'])

    def test_media_refusals(self):
        client, seen = self.build()
        self.assertEqual(client.get('/api/driver_chats/media/p1?u=7&e=1&s=x').status_code, 403)
        forged = self._signed().replace('/p1?', '/p2?')
        self.assertEqual(client.get(forged).status_code, 403)
        self.assertEqual(seen['file_link'], [], 'без подписи к Syntony не ходим')
        client, seen = self.build(person_status='fired')
        self.assertEqual(client.get(self._signed()).status_code, 403)
        client, seen = self.build(media_ok=False)
        self.assertEqual(client.get(self._signed()).status_code, 404)
        self.assertEqual(seen['file_link'], [])

    def test_media_not_ready_asks_to_retry(self):
        client, _ = self.build()

        def _not_ready(*_a, **_k):
            raise syntony_client.SyntonyHistoryError('file_not_ready', 409, 12)

        with mock.patch.object(syntony_client, 'file_link', _not_ready):
            response = client.get(self._signed())
        self.assertEqual((response.status_code, response.headers['Retry-After']), (503, '12'))


class ScreenContractTests(unittest.TestCase):
    """Экран читает поля Syntony; pytest читает .jsx текстом, поведение правил — node --test."""

    @classmethod
    def setUpClass(cls):
        cls.view = VIEW_JSX.read_text(encoding='utf-8')
        cls.thread = THREAD_JSX.read_text(encoding='utf-8')

    def test_media_links_get_the_api_address(self):
        load = self.view.split('const load = useCallback(')[1].split('const runSearch')[0]
        self.assertIn('absolutizeMediaLinks(data.chats || [], apiBaseUrl)', load)
        self.assertIn('staleMinutes', load)

    def test_handoff_texts_follow_the_source(self):
        self.assertIn('subtitle={handoffTexts(context?.handoff_to_chat).subtitle}', self.view)
        steps = self.view.split('const SEARCH_STEPS = [')[1].split('];')[0]
        self.assertIn('журнале раздела', steps)
        self.assertIn('textToChat', steps)
        self.assertIn("handoffToChat === true && step.textToChat", self.view)

    def test_autoreply_label_comes_from_the_message(self):
        self.assertIn("{m.label || 'Автоответ'}", self.thread)


if __name__ == '__main__':
    unittest.main()
