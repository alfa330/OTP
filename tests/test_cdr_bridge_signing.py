# -*- coding: utf-8 -*-
"""Мост: подпись запросов, недоверие порталу, явный выход наружу.

Три вещи, которые проще всего потерять при правке:

  * мост подписывает РОВНО те байты, что уходят в сеть — если сериализацию
    тела снова доверить requests, портал перестанет сходиться по хешу;
  * задание от портала проверяется до похода на станцию: широкое окно или
    сдвинутое начало — отказ без единого запроса к ней;
  * сессия к порталу не читает HTTPS_PROXY из окружения и ходит только через
    прокси, названный в настройках.
"""

import os
import stat
import tempfile
import unittest

from cdr import agent_auth
from cdr_bridge import agent as agent_mod, signing


class _CapturingSession:
    """Двойник requests.Session: запоминает, что мост отправил."""

    def __init__(self, status=200, body=b'{"jobs": []}'):
        self.calls = []
        self.status = status
        self.body = body

    def post(self, url, data=None, timeout=None, headers=None):
        self.calls.append({'url': url, 'data': data, 'headers': headers})

        class _Response:
            status_code = self.status
            text = self.body.decode('utf-8')

            def json(inner):
                import json as _json
                return _json.loads(self.body)
        return _Response()


class _StationThatMustNotBeCalled:
    def iter_cdr(self, *args, **kwargs):
        raise AssertionError('станцию позвали для задания, которое надо было отвергнуть')

    def agents_map(self):
        return {}


def _config(**over):
    base = {'portal': 'https://portal.invalid', 'token': '', 'station': 'http://127.0.0.1:9',
            'login': '', 'password': '', 'key_file': '', 'private_key': '',
            'proxy': '', 'ca_bundle': '', 'heartbeat_file': ''}
    base.update(over)
    return base


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.private_b64, self.public_b64, self.kid = signing.generate()

    def test_post_is_signed_over_the_exact_bytes_sent(self):
        session = _CapturingSession()
        bridge = agent_mod.Bridge(_config(private_key=self.private_b64), session=session,
                                  station=_StationThatMustNotBeCalled())
        bridge.poll()
        call = session.calls[0]
        self.assertEqual(call['url'], 'https://portal.invalid/api/cdr/agent/poll')
        self.assertNotIn('X-Agent-Token', call['headers'])
        keys = agent_auth.parse_public_keys(self.public_b64)
        kid, reason = agent_auth.verify(call['headers'], 'POST', '/api/cdr/agent/poll',
                                        call['data'], keys, nonces=agent_auth.NonceCache())
        self.assertEqual((kid, reason), (self.kid, None))

    def test_without_a_key_the_token_is_sent(self):
        session = _CapturingSession()
        bridge = agent_mod.Bridge(_config(token='legacy'), session=session,
                                  station=_StationThatMustNotBeCalled())
        bridge.poll()
        headers = session.calls[0]['headers']
        self.assertEqual(headers['X-Agent-Token'], 'legacy')
        self.assertNotIn(agent_auth.HEADER_SIGNATURE, headers)

    def test_key_file_is_read_and_perms_are_tight(self):
        path = os.path.join(tempfile.mkdtemp(), 'agent.key')
        signing.write_key_file(path, self.private_b64)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        bridge = agent_mod.Bridge(_config(key_file=path), session=_CapturingSession(),
                                  station=_StationThatMustNotBeCalled())
        self.assertEqual(bridge.signer.key_id, self.kid)
        self.assertEqual(bridge.auth_label, 'ключ %s' % self.kid)

    def test_bad_key_is_a_value_error_not_a_traceback(self):
        with self.assertRaises(ValueError):
            agent_mod.Bridge(_config(private_key='not base64!!'), session=_CapturingSession(),
                             station=_StationThatMustNotBeCalled())
        with self.assertRaises(ValueError):
            signing.load_private_key('AAAA')

    def test_401_message_names_the_key(self):
        session = _CapturingSession(status=401, body=b'{"error":"x"}')
        bridge = agent_mod.Bridge(_config(private_key=self.private_b64), session=session,
                                  station=_StationThatMustNotBeCalled())
        with self.assertRaises(RuntimeError) as caught:
            bridge.poll()
        self.assertIn(self.kid, str(caught.exception))


class SessionTests(unittest.TestCase):
    def test_env_proxies_are_ignored_and_explicit_proxy_is_used(self):
        session = agent_mod.Bridge._build_session(_config(proxy='http://127.0.0.1:3128'))
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies['https'], 'http://127.0.0.1:3128')

    def test_ca_bundle_narrows_trust(self):
        session = agent_mod.Bridge._build_session(_config(ca_bundle='/etc/otp-gateway/portal-ca.pem'))
        self.assertEqual(session.verify, '/etc/otp-gateway/portal-ca.pem')

    def test_defaults_keep_system_trust(self):
        session = agent_mod.Bridge._build_session(_config())
        self.assertIs(session.verify, True)
        self.assertEqual(session.proxies, {})


class JobWindowTests(unittest.TestCase):
    def test_a_day_with_an_hour_of_tail_passes(self):
        start, end = agent_mod.job_window({'day': '2026-09-01', 'from_dt': '2026-09-01T00:00:00',
                                           'to_dt': '2026-09-02T01:00:00'})
        self.assertEqual((end - start).total_seconds(), 25 * 3600)

    def test_a_wide_window_is_refused(self):
        with self.assertRaises(ValueError):
            agent_mod.job_window({'day': '2026-09-01', 'from_dt': '2026-09-01T00:00:00',
                                  'to_dt': '2026-12-31T00:00:00'})

    def test_a_shifted_start_is_refused(self):
        with self.assertRaises(ValueError):
            agent_mod.job_window({'day': '2026-09-01', 'from_dt': '2026-08-31T00:00:00',
                                  'to_dt': '2026-09-01T01:00:00'})
        with self.assertRaises(ValueError):
            agent_mod.job_window({'day': '2026-09-01', 'from_dt': '2026-09-01T12:00:00',
                                  'to_dt': '2026-09-02T01:00:00'})

    def test_garbage_is_refused(self):
        with self.assertRaises(ValueError):
            agent_mod.job_window({'day': 'сегодня', 'from_dt': None, 'to_dt': None})

    def test_bad_job_never_reaches_the_station(self):
        session = _CapturingSession()
        bridge = agent_mod.Bridge(_config(token='t'), session=session,
                                  station=_StationThatMustNotBeCalled())
        ok = bridge.do_day({'day': '2026-09-01', 'from_dt': '2026-01-01T00:00:00',
                            'to_dt': '2026-12-31T00:00:00'})
        self.assertFalse(ok)
        # Портал получил отказ по суткам — и больше ничего.
        self.assertEqual(len(session.calls), 1)
        self.assertIn(b'error', session.calls[0]['data'])


class KeygenTests(unittest.TestCase):
    def test_keygen_prints_public_part_and_writes_private(self):
        import io
        from contextlib import redirect_stdout
        path = os.path.join(tempfile.mkdtemp(), 'agent.key')
        out = io.StringIO()
        with redirect_stdout(out):
            code = agent_mod.keygen(path)
        self.assertEqual(code, 0)
        text = out.getvalue()
        private_b64 = signing.read_key_file(path)
        signer = signing.Signer(signing.load_private_key(private_b64))
        self.assertIn(signer.public_b64, text)
        self.assertIn(signer.key_id, text)
        self.assertNotIn(private_b64, text, 'закрытый ключ напечатан, хотя просили файл')

    def test_keygen_refuses_to_write_inside_a_git_checkout(self):
        # Репозиторий публичный: ключ, созданный внутри клона, уезжает в него
        # первым же `git add -A`. Команда обязана отказаться, а не предупредить.
        import io
        from contextlib import redirect_stdout
        root = tempfile.mkdtemp()
        os.mkdir(os.path.join(root, '.git'))
        target = os.path.join(root, 'cdr_bridge', 'agent.key')
        os.makedirs(os.path.dirname(target))
        out = io.StringIO()
        with redirect_stdout(out):
            code = agent_mod.keygen(target)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(target), 'ключ записан внутрь репозитория')
        self.assertIn('репозитория', out.getvalue())

    def test_inside_git_checkout_detects_parents_and_stops_at_root(self):
        root = tempfile.mkdtemp()
        self.assertFalse(agent_mod.inside_git_checkout(os.path.join(root, 'x', 'agent.key')))
        os.mkdir(os.path.join(root, '.git'))
        self.assertTrue(agent_mod.inside_git_checkout(os.path.join(root, 'x', 'y', 'agent.key')))


if __name__ == '__main__':
    unittest.main()
