# -*- coding: utf-8 -*-
"""Гейтинг прав на уровне HTTP раздела «Ограничитель Перезвона».

Права раздела держит ОДИН декоратор — section_route, — и до сих пор его не
проверял ни один тест: расстановка manage= по роутам жила на глазах ревьюера.
С 31.08.2026 просмотр и правка разошлись (СВ СЗоВ читает, но не правит), и цена
ошибки в одном декораторе выросла: забытый manage=True отдаёт супервайзеру
общий порог и версию exe на весь отдел.

Базы здесь нет и быть не должно: пакет ничего не импортирует из database, а
запросы подменяются заглушками. Проверяется ровно гейт, а не SQL.
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
    from flask import Flask
    from oktell_guard.routes import build_oktell_guard_blueprint
except ImportError:  # pragma: no cover
    Flask = None
    build_oktell_guard_blueprint = None


def context(role, *, department_code='szov', is_department_head=False):
    """То, что отдаёт queries.access_context. Поля — как в его SELECT.

    department_code у главы отдела уже подменён возглавляемым отделом (это
    делает сам access_context), поэтому здесь он передаётся готовым.
    """
    return {
        'id': 42,
        'name': 'Тест',
        'role': role,
        'department_code': department_code,
        'is_department_head': is_department_head,
        'headed_department_code': department_code if is_department_head else '',
    }


class _Db:
    """Курсор нужен только как объект: все запросы раздела подменены."""

    @contextmanager
    def _get_cursor(self):
        yield object()


@unittest.skipIf(Flask is None, 'flask не установлен')
class OktellGuardSectionGateTest(unittest.TestCase):
    READ_ROUTES = (
        ('get', '/api/oktell_guard/settings'),
        ('get', '/api/oktell_guard/employees'),
        ('get', '/api/oktell_guard/report'),
    )
    WRITE_ROUTES = (
        ('put', '/api/oktell_guard/settings'),
        ('post', '/api/oktell_guard/settings'),
        ('post', '/api/oktell_guard/employees/bulk'),
        ('post', '/api/oktell_guard/release'),
    )

    def client(self, requester):
        for name, replacement in (
            ('access_context', lambda _cursor, _uid: dict(requester)),
            ('get_settings', lambda _cursor: {'enabled': True, 'dry_run': False, 'threshold_s': 180}),
            ('current_release', lambda _cursor: None),
            ('list_employees', lambda _cursor, **_kw: []),
            ('report', lambda _cursor, *_a, **_kw: []),
            ('rejected_count', lambda _cursor, *_a, **_kw: 0),
            # Пишущие обязаны остаться нетронутыми: если гейт их пропустит,
            # тест упадёт на неожиданном вызове, а не молча позеленеет.
            ('save_settings', self._must_not_be_called('save_settings')),
            ('bulk_set_rules', self._must_not_be_called('bulk_set_rules')),
        ):
            patcher = patch.object(queries, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = Flask(__name__)
        app.register_blueprint(build_oktell_guard_blueprint(
            db=_Db(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (requester['id'], None, None),
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def _must_not_be_called(self, name):
        def fail(*_a, **_kw):
            self.fail('гейт пропустил запись: вызван queries.%s' % name)
        return fail

    # ── кто читает ──────────────────────────────────────────────────────────
    def test_supervisor_of_szov_reads_all_three_tabs(self):
        """Решение владельца 31.08.2026: СВ СЗоВ раздел открыт на просмотр."""
        client = self.client(context('sv'))
        for method, url in self.READ_ROUTES:
            self.assertEqual(getattr(client, method)(url).status_code, 200, url)

    def test_admin_and_head_read_all_three_tabs(self):
        for requester in (context('super_admin', department_code=''),
                          context('admin', department_code=''),
                          context('admin', is_department_head=True)):
            client = self.client(requester)
            for method, url in self.READ_ROUTES:
                self.assertEqual(getattr(client, method)(url).status_code, 200,
                                 '%s %s' % (requester['role'], url))

    def test_operator_and_foreign_supervisor_see_nothing(self):
        for requester in (context('operator'),
                          context('trainer'),
                          context('sv', department_code='op')):
            client = self.client(requester)
            for method, url in self.READ_ROUTES + self.WRITE_ROUTES:
                response = getattr(client, method)(url, json={})
                self.assertEqual(response.status_code, 403, '%s %s' % (requester['role'], url))
                # Формулировка важна: «раздел не открыт» и «недостаточно прав» —
                # разные новости, и человек по ним понимает, куда идти.
                self.assertEqual(response.get_json().get('error'), 'Раздел вам не открыт')

    # ── кто правит ──────────────────────────────────────────────────────────
    def test_supervisor_cannot_write_anything(self):
        client = self.client(context('sv'))
        for method, url in self.WRITE_ROUTES:
            response = getattr(client, method)(url, json={'enabled': False, 'user_ids': [1]})
            self.assertEqual(response.status_code, 403, url)
            self.assertEqual(response.get_json().get('error'), 'Недостаточно прав')

    def test_read_only_flag_reaches_the_frontend(self):
        """По этому флагу интерфейс гасит все правящие поля. Разойдись он с
        гейтом — СВ увидел бы живые тумблеры и получил 403 на нажатие."""
        self.assertIs(self.client(context('sv')).get(
            '/api/oktell_guard/settings').get_json()['can_manage'], False)
        self.assertIs(self.client(context('admin', is_department_head=True)).get(
            '/api/oktell_guard/settings').get_json()['can_manage'], True)

    # ── исключение, сделанное осознанно ─────────────────────────────────────
    def test_supervisor_may_still_download_the_agent(self):
        """«Скачать агента» осталось на уровне просмотра, хотя ручка пишет:
        установщик операторам раздаёт как раз СВ, а сам файл и так отдаёт
        публичная /version. Тест держит это решение видимым — если ручку решат
        закрыть, он упадёт и заставит объяснить, чем СВ теперь раздаёт агента."""
        issued = []
        # Клиент строится ДО подмены: self.client сам подменяет current_release
        # на «версии нет», и начатый позже патч перебил бы наш.
        client = self.client(context('sv'))
        with patch.object(queries, 'current_release',
                          lambda _cursor: {'version': '1.0.13', 'gcs_bucket': '', 'gcs_path': ''}), \
             patch.object(queries, 'issue_token',
                          lambda _cursor, user_id, digest, note=None: issued.append(user_id)):
            response = client.get('/api/oktell_guard/download')
        self.assertNotEqual(response.status_code, 403)
        self.assertEqual(issued, [42], 'личный токен должен выписываться на самого СВ')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
