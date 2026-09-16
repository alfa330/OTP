# -*- coding: utf-8 -*-
"""Выдача доступа сразу нескольким адресатам — POST /access/section-rules/bulk.

Просьба владельца 16.09.2026 дословно: «чтобы я мог добавлять права сразу к
группам людей, и по их должности тоже». До этого адресат был один на запрос, и
открыть раздел четырём группам значило четыре раза пройти форму.

Главное свойство пачки — ВСЁ ИЛИ НИЧЕГО, и проверяется оно здесь первым.
Курсор роута (database._get_cursor) коммитит транзакцию, когда обработчик
вернулся штатно, — в том числе с ответом 403. Значит отказ, случившийся ПОСЛЕ
первой записи, оставил бы половину выдачи выписанной, а человек прочитал бы
«не удалось» и ушёл, глядя на наполовину открытый раздел. Тот же класс
молчаливого расхождения, от которого этот раздел лечили трижды.

Тесты герметичные: боевая база не читается, курсор подменён (см.
tests/test_wiki_routes._RouteHarness).
"""

import unittest
from unittest.mock import patch

from tests.test_wiki_routes import ADMIN_ROLE, _RouteHarness, make_context
from wiki import structure

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

URL = '/api/wiki/access/section-rules/bulk'
READ_ONLY = {'can_read': True}

GROUP_DEPARTMENTS = {10: 7, 11: 7, 99: 9}


def group(subject_id):
    return {'subject_type': 'group', 'subject_id': subject_id}


@unittest.skipIf(Flask is None, 'flask не установлен')
class SectionRulesBulkTest(_RouteHarness, unittest.TestCase):

    def _client(self, context=None):
        """Клиент с подменёнными дверями в базу; self.written — что записано."""
        client, _cursor = self.build(
            context or make_context('super_admin', wiki_roles=[ADMIN_ROLE]))
        self.written = []

        def _upsert(_cursor_, **kwargs):
            self.written.append(kwargs)
            return 100 + len(self.written)

        patches = [
            patch.object(structure, 'section_exists', return_value=1),
            # Отдел адресата: граница отдела считается по нему, и без заглушки
            # MagicMock-курсор вернул бы None — «адресат без отдела».
            patch.object(structure, 'subject_department',
                         side_effect=lambda _c, _t, sid: GROUP_DEPARTMENTS.get(sid)),
            patch.object(structure, 'section_branch_department', return_value=7),
            patch.object(structure, 'section_role_levels', return_value={}),
            patch.object(structure, 'upsert_section_rule', side_effect=_upsert),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return client

    def _post(self, client, **body):
        payload = {'section_id': 1, **body}
        return client.post(URL, json=payload)

    # ── Ради чего всё затевалось ─────────────────────────────────────────
    def test_one_save_writes_a_rule_per_subject(self):
        client = self._client()
        response = self._post(client, subjects=[group(10), group(11)], **READ_ONLY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['count'], 2)
        self.assertEqual([w['subject_id'] for w in self.written], [10, 11])
        self.assertTrue(all(w['permissions']['can_read'] for w in self.written))

    def test_positions_carry_job_title_and_threshold(self):
        """Должность — третье измерение правила, и она обязана доехать целиком.

        Строка «Видеограф» в форме — это правило на ОТДЕЛ, суженное должностью.
        Потеряв job_title, выдача открыла бы раздел всему «Маркетингу».
        """
        client = self._client()
        response = self._post(client, subjects=[
            {'subject_type': 'department', 'subject_id': 7, 'job_title': 'Видеограф'},
        ], **READ_ONLY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.written[0]['job_title'], 'Видеограф')
        # Порог «и все, кто выше» держится сравнением уровня, а сравнение с NULL
        # истины не даёт — роут обязан подставить уровень оператора.
        self.assertEqual(self.written[0]['min_role_level'], 10)

    def test_same_subject_twice_is_one_rule(self):
        """Ключ уникальности склеил бы их молча, а счётчик в ответе соврал бы."""
        client = self._client()
        response = self._post(client, subjects=[group(10), group(10)], **READ_ONLY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['count'], 1)
        self.assertEqual(len(self.written), 1)

    # ── Всё или ничего ───────────────────────────────────────────────────
    def test_foreign_subject_cancels_the_whole_batch(self):
        """Отказ по одному адресату НЕ оставляет выписанными остальных."""
        client = self._client(make_context('admin', headed=[7], department_id=7))
        response = self._post(client, subjects=[group(10), group(99)], **READ_ONLY)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_DEPARTMENT_SCOPE')
        self.assertEqual(self.written, [], 'первое правило всё-таки записали')

    def test_permission_beyond_self_writes_nothing(self):
        client = self._client(make_context('sv', department_id=7))
        response = self._post(client, subjects=[group(10)],
                              can_read=True, can_delete=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_GRANT_BEYOND_SELF')
        self.assertEqual(self.written, [])

    # ── Что нельзя отправить вовсе ───────────────────────────────────────
    def test_empty_permissions_are_refused(self):
        """Правило без единого права — мусор: оно ничего не откроет."""
        client = self._client()
        response = self._post(client, subjects=[group(10)])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.written, [])

    def test_no_subjects_is_refused(self):
        client = self._client()
        for subjects in ([], None, 'group'):
            response = self._post(client, subjects=subjects, **READ_ONLY)
            self.assertEqual(response.status_code, 400, repr(subjects))
        self.assertEqual(self.written, [])

    def test_batch_size_is_capped(self):
        """Сотня правил одним нажатием — выдача, которую никто не перечитает."""
        from wiki.routes_structure import MAX_BULK_SUBJECTS
        client = self._client()
        subjects = [group(10) for _ in range(MAX_BULK_SUBJECTS + 1)]
        response = self._post(client, subjects=subjects, **READ_ONLY)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.written, [])

    # ── Дверь закрыта тем же ключом, что и одиночная ─────────────────────
    def test_operator_cannot_grant_in_bulk(self):
        client = self._client(make_context('operator'))
        response = self._post(client, subjects=[group(10)], **READ_ONLY)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.written, [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
