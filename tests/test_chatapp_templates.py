# -*- coding: utf-8 -*-
"""Шаблоны WhatsApp в клиенте ChatApp (автообзвон Тез КЦ, 15.10.2026): список шаблонов лицензии и
отправка шаблона — без сети, на подделке requests.Session. Главное правило отправки: повтора при
сбое нет (сообщение могло уйти), кроме протухшего токена — тогда ChatApp запрос не принял."""
import os
import sys
import time
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tez import chatapp_client  # noqa: E402


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self.body


class _Session:
    """Ответы — по очереди из posts/gets; исключение в очереди — бросается."""

    def __init__(self, posts=(), gets=()):
        self.posts, self.gets, self.sent = list(posts), list(gets), []

    def _next(self, queue):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, json=None, headers=None, timeout=None, data=None):
        self.sent.append(('POST', url, json, dict(headers or {})))
        return self._next(self.posts)

    def get(self, url, params=None, headers=None, timeout=None):
        self.sent.append(('GET', url, params, dict(headers or {})))
        return self._next(self.gets)


class _Store:
    def __init__(self, data):
        self.data = data

    def load(self):
        return dict(self.data)

    def save(self, data):
        self.data = dict(data)


def _client(session, access='tok-1'):
    future = int(time.time()) + 3600
    store = _Store({"accessToken": access, "accessTokenEndTime": future, "refreshToken": "ref-1",
                    "refreshTokenEndTime": future + 86400})
    return chatapp_client.ChatAppClient('a@b.c', 'pw', 'app', api_url='https://api.example', token_store=store,
                                        session=session), store


class ChatAppTemplatesTests(unittest.TestCase):
    def test_send_template_posts_once_and_returns_message_id(self):
        session = _Session(posts=[_Resp({"success": True, "data": {"id": "msg-77"}})])
        client, _store = _client(session)
        self.assertEqual(client.send_template(70651, '77010000001', 'tpl-bonus', ['Иван'], tracking='out-1'),
                         'msg-77')
        method, url, payload, headers = session.sent[0]
        self.assertEqual((method, url), ('POST', 'https://api.example/v1/licenses/70651/messengers/caWhatsApp'
                                                 '/chats/77010000001/messages/template'))
        self.assertEqual(payload, {"template": {"id": "tpl-bonus", "params": ["Иван"]}, "tracking": "out-1"})
        self.assertEqual(headers["Authorization"], 'tok-1')

    def test_network_failure_is_not_retried(self):
        session = _Session(posts=[requests.ConnectionError('reset'), _Resp({"success": True, "data": {"id": "x"}})])
        client, _store = _client(session)
        with self.assertRaises(chatapp_client.ChatAppError):
            client.send_template(70651, '+7 701 000 00 01', 'tpl')
        self.assertEqual(len(session.sent), 1)

    def test_api_error_is_not_retried_but_stale_token_is(self):
        session = _Session(posts=[_Resp({"success": False, "error": {"code": "ApiTemplateError",
                                                                     "message": "шаблон не одобрен"}}, 400)])
        client, _store = _client(session)
        with self.assertRaises(chatapp_client.ChatAppError) as ctx:
            client.send_template(70651, '77010000001', 'tpl')
        self.assertIn('шаблон не одобрен', str(ctx.exception))
        self.assertEqual(len(session.sent), 1)
        # Протухший токен: refresh и один повтор — запрос ChatApp не принял, второго сообщения не будет.
        session = _Session(posts=[
            _Resp({"success": False, "error": {"code": "ApiInvalidTokenError", "message": "token"}}, 401),
            _Resp({"success": True, "data": {"accessToken": "tok-2", "accessTokenEndTime": int(time.time()) + 3600,
                                             "refreshToken": "ref-2"}}),
            _Resp({"success": True, "data": {"id": "msg-2"}})])
        client, store = _client(session)
        self.assertEqual(client.send_template(70651, '77010000001', 'tpl'), 'msg-2')
        self.assertEqual([s[1].rsplit('/', 2)[-2:] for s in session.sent],
                         [['messages', 'template'], ['tokens', 'refresh'], ['messages', 'template']])
        self.assertEqual(session.sent[2][3]["Authorization"], 'tok-2')
        self.assertEqual(store.data["accessToken"], 'tok-2')

    def test_empty_number_is_refused_without_a_request(self):
        session = _Session()
        client, _store = _client(session)
        with self.assertRaises(chatapp_client.ChatAppError):
            client.send_template(70651, '', 'tpl')
        self.assertEqual(session.sent, [])

    def test_list_templates(self):
        items = [{"id": "t1", "elementName": "bonus", "data": "Привет, {{1}}", "status": "APPROVED",
                  "templateType": "TEXT"}, "мусор"]
        session = _Session(gets=[_Resp({"success": True, "data": {"items": items}})])
        client, _store = _client(session)
        self.assertEqual(client.list_templates(70651), [items[0]])
        self.assertEqual(session.sent[0][1], 'https://api.example/v1/licenses/70651/messengers/caWhatsApp/templates')


if __name__ == '__main__':
    unittest.main()
