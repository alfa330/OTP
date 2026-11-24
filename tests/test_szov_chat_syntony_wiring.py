# -*- coding: utf-8 -*-
"""Проводка чатов СЗоВ на Syntony в монолите (SZOV-12) — по исходнику, без импорта модуля.

Сами правила счёта проверяют tests/test_syntony_chats_metrics.py и *_postgres.py; здесь — что
монолит их действительно зовёт: переключатель источника в табло, выгрузке и почасовом отчёте,
своей второй догрузки нет, и имя модуля не перекрыто одноимённой функцией (на этом стенд упал
20.11.2026: функция перекрыла модуль с тем же именем).
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import source_cache  # noqa: E402

SOURCE = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
TREE = source_cache.parse(SOURCE)


def _function(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError('нет функции %s' % name)


class ImportAliasTests(unittest.TestCase):
    def test_szov_module_alias_is_not_shadowed(self):
        aliases = set()
        for node in TREE.body:
            if isinstance(node, ast.ImportFrom) and node.module == 'syntony_chats':
                aliases.update(alias.asname or alias.name for alias in node.names)
        # Биллинг чата по Syntony (24.11.2026) — свой модуль рядом с табло.
        self.assertEqual(aliases, {'syntony_chats_szov', 'syntony_chats_billing'})
        defined = set()
        for node in TREE.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.assertEqual(aliases & defined, set())


class WiringTests(unittest.TestCase):
    def test_no_second_puller_in_the_monolith(self):
        # Чаты в базу приносят вебхук и сторож API истории (syntony_chats/sync.py, свой поток).
        # Своей догрузки у табло нет: второй опрос делил бы с ним предел ленты 10 в минуту.
        self.assertNotIn('syntony_chats_pool', SOURCE)
        self.assertNotIn("id='syntony_chats_sync'", SOURCE)
        self.assertIn('syntony_chats_sync.start_worker(db)', SOURCE)

    def test_wallboard_report_and_export_switch_to_syntony(self):
        self.assertIn('syntony_chats_szov.wallboard_snapshot(', _function('_szov_chat_wallboard_fetch_snapshot'))
        self.assertIn('syntony_chats_szov.hourly_report(', _function('_chat_hourly_collect'))
        self.assertIn('syntony_chats_szov.day_block(', _function('_szov_chat_syntony_export_day'))
        routes = (ROOT / 'szov_wallboard' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("monolith_namespace.get('_szov_chat_syntony_export_day')", routes)

    def test_routes_do_not_demand_the_chat2desk_token_for_syntony(self):
        routes = (ROOT / 'szov_wallboard' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("monolith_namespace.get('_szov_chat_source_is_syntony')", routes)
        self.assertIn('if not syntony and not _chat2desk_authorization_header():', routes)
        self.assertIn('if not _szov_chat_syntony_source() and not _chat2desk_authorization_header():',
                      routes)
        # Своё состояние Syntony (первая загрузка, догрузка) экран показывает словами из снимка.
        self.assertIn("if syntony and reason.startswith('Syntony'):", routes)

    def test_status_based_break_scan_is_off_for_syntony(self):
        self.assertIn('if _szov_chat_source_is_syntony():\n',
                      _function('_szov_chat_break_violations_scan'))
        self.assertIn('if _szov_chat_source_is_syntony():\n',
                      _function('_szov_chat_broadcast_break_violations'))

    def test_context_uses_the_chat2desk_people_rules(self):
        body = _function('_szov_syntony_context')
        self.assertIn('_szov_chat_wallboard_operator_lookup()', body)
        self.assertIn('_szov_chat_wallboard_resolve(name, lookup)', body)
        self.assertIn('test_keys.sql_is_test', body)


if __name__ == '__main__':
    unittest.main()


class BillingWiringTests(unittest.TestCase):
    def test_billing_days_are_built_by_the_scheduler(self):
        # Сутки биллинга сворачиваются в «чаты дня» планировщиком раз в 10 минут, и даже при
        # откате табло на Chat2Desk: биллинг с 18.11 читает только их.
        job = _function('syntony_chat_billing_refresh_job')
        self.assertIn('syntony_chats_szov.data_enabled()', job)
        self.assertIn('syntony_chats_billing.refresh(', job)
        self.assertIn('first_day=chat_billing_syntony_from()', job)
        self.assertIn("id='syntony_chat_billing_refresh'", SOURCE)

