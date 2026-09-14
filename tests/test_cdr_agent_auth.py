# -*- coding: utf-8 -*-
"""Подпись запросов моста на портале: /api/cdr/agent/* с CDR_AGENT_KEYS.

Что закреплено:

  * подписанный запрос проходит, и портал запоминает, каким ключом он подписан;
  * как только заданы ключи, токен не принимается даже правильный — два способа
    входа одновременно это два способа ошибиться;
  * подмена тела, чужой ключ, старое время, повтор одноразового номера — всё 401
    с одинаковым текстом снаружи (причина только в журнале);
  * кривая запись ключей закрывает вход (503), а не открывает его;
  * без ключей работает старый путь с токеном; без того и другого — 503.

Ключи настоящие (Ed25519 через cryptography), подпись собирается той же
половиной протокола, что стоит на шлюзе, — cdr_bridge/signing.py. Так тест
проверяет не «портал согласен сам с собой», а совместимость двух сторон.
"""

import json
import unittest
from contextlib import contextmanager
from unittest import mock

from flask import Flask

from cdr import agent_auth, routes as cdr_routes
from cdr_bridge import signing

PATH = '/api/cdr/agent/poll'


class _FakeCursor:
    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeDb:
    def _get_cursor(self):
        @contextmanager
        def scope():
            yield _FakeCursor()
        return scope()


class _Recorder:
    def __init__(self):
        self.seen = []

    def agent_seen(self, cursor, **kwargs):
        self.seen.append(kwargs)

    def claim_days(self, cursor, agent_id, limit=1):
        return []

    def agent_state(self, cursor):
        return {}

    def cleanup_due(self, cursor, hours=24):
        return False


def _make_signer():
    private_b64, public_b64, kid = signing.generate()
    return signing.Signer(signing.load_private_key(private_b64)), public_b64, kid


class AgentSignatureTests(unittest.TestCase):
    def setUp(self):
        self.signer, self.public_b64, self.kid = _make_signer()
        self.recorder = _Recorder()
        self.keys_setting = self.public_b64
        self.token_setting = ''

        for name, getter in (('queries', lambda: self.recorder),):
            patcher = mock.patch.object(cdr_routes, name, getter())
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, getter in (('agent_keys_raw', lambda: self.keys_setting),
                             ('agent_token', lambda: self.token_setting)):
            patcher = mock.patch.object(cdr_routes.config, name, side_effect=getter)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Кэш одноразовых номеров общий на процесс — между тестами он обязан
        # быть чистым, иначе «повтор» из одного теста отравит другой.
        patcher = mock.patch.object(agent_auth, '_default_nonces', agent_auth.NonceCache())
        patcher.start()
        self.addCleanup(patcher.stop)

        app = Flask(__name__)
        app.register_blueprint(cdr_routes.build_cdr_blueprint(
            db=_FakeDb(),
            require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None),
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def _signed(self, payload, signer=None, now=None, nonce=None, tamper=None, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        headers.update((signer or self.signer).headers('POST', PATH, body, now=now, nonce=nonce))
        if extra:
            headers.update(extra)
        if tamper is not None:
            body = tamper
        return self.client.post(PATH, data=body, headers=headers)

    # ── проходит ─────────────────────────────────────────────────────────────

    def test_signed_request_is_accepted_and_key_is_recorded(self):
        response = self._signed({'agent_id': 'gw-1'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.recorder.seen[0]['agent_key'], self.kid)

    def test_key_id_is_derived_from_the_key_not_configured(self):
        self.assertEqual(len(self.kid), 16)
        raw, _ = agent_auth.decode_public_key(self.public_b64)
        self.assertEqual(agent_auth.key_id(raw), self.kid)

    def test_several_keys_may_be_configured_at_once(self):
        other, other_public, other_kid = _make_signer()
        self.keys_setting = '%s, %s\n' % (self.public_b64, other_public)
        self.assertEqual(self._signed({}, signer=other).status_code, 200)
        self.assertEqual(self.recorder.seen[-1]['agent_key'], other_kid)

    # ── не проходит ──────────────────────────────────────────────────────────

    def test_token_is_ignored_once_keys_are_configured(self):
        self.token_setting = 'still-a-valid-token'
        response = self.client.post(PATH, data='{}', headers={
            'Content-Type': 'application/json', 'X-Agent-Token': 'still-a-valid-token'})
        self.assertEqual(response.status_code, 401)

    def test_tampered_body_is_refused(self):
        response = self._signed({'day': '2026-09-01'},
                                tamper=json.dumps({'day': '2026-09-02'}).encode())
        self.assertEqual(response.status_code, 401)

    def test_unknown_key_is_refused(self):
        stranger, _, _ = _make_signer()
        self.assertEqual(self._signed({}, signer=stranger).status_code, 401)

    def test_stale_signature_is_refused(self):
        import time
        response = self._signed({}, now=time.time() - agent_auth.MAX_SKEW_SECONDS - 60)
        self.assertEqual(response.status_code, 401)

    def test_replay_is_refused(self):
        first = self._signed({}, nonce='a' * 32)
        second = self._signed({}, nonce='a' * 32)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 401)

    def test_refusals_look_the_same_from_outside(self):
        stranger, _, _ = _make_signer()
        texts = {
            self._signed({}, signer=stranger).get_data(as_text=True),
            self._signed({}, tamper=b'{"x":1}').get_data(as_text=True),
            self.client.post(PATH, data='{}').get_data(as_text=True),
        }
        self.assertEqual(len(texts), 1, 'причина отказа утекает наружу')

    def test_broken_keys_setting_closes_the_door(self):
        self.keys_setting = 'not-base64-at-all'
        response = self._signed({})
        self.assertEqual(response.status_code, 503)

    # ── запасной путь ────────────────────────────────────────────────────────

    def test_without_keys_the_token_path_still_works(self):
        self.keys_setting = ''
        self.token_setting = 'legacy-token'
        response = self.client.post(PATH, data='{}', headers={
            'Content-Type': 'application/json', 'X-Agent-Token': 'legacy-token'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.recorder.seen[0]['agent_key'])

    def test_nothing_configured_is_503_not_open(self):
        self.keys_setting = ''
        self.token_setting = ''
        self.assertEqual(self._signed({}).status_code, 503)


class VerifyUnitTests(unittest.TestCase):
    """Проверка без Flask: сам модуль agent_auth."""

    def setUp(self):
        self.signer, self.public_b64, self.kid = _make_signer()
        self.keys = agent_auth.parse_public_keys(self.public_b64)
        self.nonces = agent_auth.NonceCache()

    def _verify(self, headers, body=b'{}', now=1_800_000_000):
        return agent_auth.verify(headers, 'POST', PATH, body, self.keys,
                                 now=now, nonces=self.nonces)

    def test_round_trip(self):
        headers = self.signer.headers('POST', PATH, b'{}', now=1_800_000_000)
        kid, reason = self._verify(headers)
        self.assertEqual((kid, reason), (self.kid, None))

    def test_missing_headers(self):
        self.assertEqual(self._verify({})[0], None)

    def test_nonce_must_be_hex_of_sane_length(self):
        headers = self.signer.headers('POST', PATH, b'{}', now=1_800_000_000, nonce='short')
        self.assertIn('номер', self._verify(headers)[1])

    def test_method_and_path_are_part_of_the_signature(self):
        headers = self.signer.headers('POST', '/api/cdr/agent/day', b'{}', now=1_800_000_000)
        self.assertEqual(self._verify(headers)[0], None, 'подпись для другого пути прошла')

    def test_nonce_is_remembered_only_after_a_valid_signature(self):
        # Иначе кто угодно без ключа мог бы «сжечь» номер настоящего моста.
        headers = self.signer.headers('POST', PATH, b'{}', now=1_800_000_000, nonce='b' * 32)
        headers[agent_auth.HEADER_SIGNATURE] = 'A' * 86 + '=='
        self._verify(headers)
        self.assertEqual(len(self.nonces), 0)

    def test_parse_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            agent_auth.parse_public_keys('AAAA')

    def test_nonce_cache_expires(self):
        cache = agent_auth.NonceCache(ttl=10)
        self.assertFalse(cache.seen_or_remember('k', 'n', now=100))
        self.assertTrue(cache.seen_or_remember('k', 'n', now=105))
        self.assertFalse(cache.seen_or_remember('k', 'n', now=111))

    def test_nonce_cache_refuses_when_full(self):
        cache = agent_auth.NonceCache(limit=1)
        self.assertFalse(cache.seen_or_remember('k', 'n1', now=100))
        self.assertTrue(cache.seen_or_remember('k', 'n2', now=100))


if __name__ == '__main__':
    unittest.main()
