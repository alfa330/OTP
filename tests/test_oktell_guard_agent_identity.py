# -*- coding: utf-8 -*-
"""Кто за машиной: сессия iCORE, а не токен в имени файла (с 1.0.17).

Ставка этого теста — пароль от АТС. `/config` отдаёт учётку кабинета, и отдать
её не тому значит выдать чужой доступ к телефонии. До 1.0.17 адресата называл
личный токен в имени скачанного exe; файл ходил по рукам, и из 26 живых машин
токен носили 7. Теперь оператор входит окном, как в iCORE Phone, и сервер узнаёт
его по `Authorization: Bearer` — ровно так же, как узнаёт любого в портале.

Базы здесь нет: запросы подменены, проверяется только развязка «кто спросил».
"""

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
    from flask import Flask, g
    from oktell_guard.routes import build_oktell_guard_blueprint
except ImportError:  # pragma: no cover
    Flask = None
    build_oktell_guard_blueprint = None

CABINET = {'cabinet_login': '6612', 'cabinet_password': 'пароль-АТС'}


class _Db:
    """Курсор нужен только как объект: все запросы подменены."""

    @contextmanager
    def _get_cursor(self):
        yield object()

    @staticmethod
    def get_oktell_account(user_id):
        return dict(CABINET) if user_id == 42 else None


@unittest.skipIf(Flask is None, 'flask не установлен')
class AgentIdentityTest(unittest.TestCase):
    def client(self, logged_in_user_id=None):
        for name, replacement in (
            ('get_settings', lambda _cursor: {'enabled': True, 'dry_run': False, 'threshold_s': 180}),
            ('personal_rule_by_sip', lambda _cursor, _sip: None),
            ('agent_config_payload', lambda _settings, _personal: {'oktell_url': 'https://oktell/'}),
            ('user_brief', lambda _cursor, uid: {'user_id': int(uid), 'name': 'Оператор',
                                                 'sip_number': '6612'}),
        ):
            patcher = patch.object(queries, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = Flask(__name__)

        @app.before_request
        def _pretend_the_portal_read_the_bearer():
            # Настоящий разбор Bearer делает общий before_request портала
            # (hydrate_user_context_from_jwt): он кладёт id в g, а раздел его
            # только читает. Своей проверки токена у раздела нет намеренно —
            # вторая копия однажды разъехалась бы с первой.
            if logged_in_user_id is not None:
                g.user_id = logged_in_user_id

        app.register_blueprint(build_oktell_guard_blueprint(
            db=_Db(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (None, None, None),
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def test_the_logged_in_operator_gets_the_cabinet(self):
        with patch.dict('os.environ', {'OKTELL_GUARD_AGENT_TOKEN': 'machine-token'}):
            response = self.client(logged_in_user_id=42).get(
                '/api/oktell_guard/config', headers={'X-Agent-Token': 'machine-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['cabinet'],
                         {'login': '6612', 'password': 'пароль-АТС'})

    def test_a_machine_without_a_login_gets_settings_but_no_password(self):
        """Ограничитель работает и без входа — это осознанно. А вот пароль от
        АТС по общему токену сборки не отдаётся: он один на всех и человека не
        называет."""
        with patch.dict('os.environ', {'OKTELL_GUARD_AGENT_TOKEN': 'machine-token'}):
            response = self.client(logged_in_user_id=None).get(
                '/api/oktell_guard/config', headers={'X-Agent-Token': 'machine-token'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['oktell_url'], 'https://oktell/')
        self.assertNotIn('cabinet', payload)

    def test_a_stranger_without_the_machine_token_is_not_let_in(self):
        with patch.dict('os.environ', {'OKTELL_GUARD_AGENT_TOKEN': 'machine-token'}):
            response = self.client(logged_in_user_id=42).get('/api/oktell_guard/config')
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
