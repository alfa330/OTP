# -*- coding: utf-8 -*-
"""Сколько раз агент Ограничителя стучится к серверу (27.10.2026).

Пульс раз в минуту с каждой машины давал 27% всех запросов к API — 1,2 млн за
12 дней, три четверти с машин без открытого Oktell. С 1.0.34 круг агента
остаётся минутным (на нём ставится правило во вкладку), а heartbeat уходит при
перемене состояния и раз в `report_every_s`. Объявления — раз в `news_poll_s`.
Оба числа называет сервер, и здесь проверяется, что он их называет и что они
сходятся с границами агента.

Базы нет: запросы подменены.
"""

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oktell_guard import queries  # noqa: E402

try:
    from flask import Flask
    from oktell_guard.routes import build_oktell_guard_blueprint
except ImportError:  # pragma: no cover
    Flask = None
    build_oktell_guard_blueprint = None


def _load_agent():
    spec = importlib.util.spec_from_file_location(
        "oktell_recall_guard_agent_cadence", ROOT / "oktell_recall_guard" / "agent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Db:
    @contextmanager
    def _get_cursor(self):
        yield object()


@unittest.skipIf(Flask is None, 'flask не установлен')
class HeartbeatReplyTest(unittest.TestCase):
    def setUp(self):
        self.upserts = []
        for name, replacement in (
            ('get_settings', lambda _cursor: {'enabled': True, 'heartbeat_interval_s': 60}),
            ('personal_rule_by_sip', lambda _cursor, _sip: None),
            ('upsert_agent', lambda _cursor, row: self.upserts.append(row)),
            ('mark_managed_day', lambda _cursor, _user_id: None),
        ):
            patcher = patch.object(queries, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(build_oktell_guard_blueprint(
            db=_Db(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (None, None, None),
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_reply_keeps_the_minute_round_and_asks_for_sparse_reports(self):
        response = self.client.post('/api/oktell_guard/heartbeat', json={
            'agent_id': 'wks-12|operator1', 'operator_login': '6612', 'version': '1.0.34',
            'browser': {'managed_window': True, 'session_present': True},
            'rule': {'alive': True, 'sockets': 1, 'seconds': 0},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        # Круг — прежняя минута из настроек: на нём агент ставит правило во вкладку.
        self.assertEqual(body['poll_interval_s'], 60)
        self.assertEqual(body['report_every_s'], queries.HEARTBEAT_REPORT_EVERY_S)
        self.assertEqual(len(self.upserts), 1)


class CadenceContractTest(unittest.TestCase):
    def test_config_names_the_news_period(self):
        payload = queries.agent_config_payload({'heartbeat_interval_s': 60}, None)
        self.assertEqual(payload['news_poll_s'], queries.AGENT_NEWS_POLL_S)
        self.assertEqual(payload['poll_interval_s'], 60)

    def test_server_numbers_fit_the_agent_limits(self):
        """Агент зажимает report_every_s в [круг; REPORT_EVERY_MAX_S], а news_poll_s
        не ниже 30 с. Число сервера вне границ молча превратилось бы в другое."""
        try:
            agent = _load_agent()
        except ImportError as exc:  # pragma: no cover — нет requests
            self.skipTest(f'агент не грузится: {exc}')
        self.assertEqual(agent.report_interval({'report_every_s': queries.HEARTBEAT_REPORT_EVERY_S}, 60.0),
                         float(queries.HEARTBEAT_REPORT_EVERY_S))
        self.assertGreaterEqual(queries.AGENT_NEWS_POLL_S, 30)
        merged = agent.apply_server_config(agent.load_config(Path('нет-такого.json')),
                                           queries.agent_config_payload({'heartbeat_interval_s': 60}, None))
        self.assertEqual(merged['news_poll_s'], queries.AGENT_NEWS_POLL_S)


if __name__ == '__main__':
    unittest.main()
