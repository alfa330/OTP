# -*- coding: utf-8 -*-
"""Удаление обращения через боевой роут: кто получает отказ и что при этом НЕ
происходит с базой.

Права на удаление проверены в test_crm_access.py на чистых функциях. Здесь
проверяется другое и более дорогое: что отказ доходит до отказа. Обработчик
сначала читает обращение, потом спрашивает права, и только потом удаляет
(crm/routes.py) — порядок, в котором легко ошибиться при любой правке, а цена
ошибки несимметрична: лишний 403 человек увидит и скажет, а удаление, которое
случилось ВОПРЕКИ отказу, не заметит никто и вернуть его нечем.

Поэтому в каждом отрицательном случае проверяется не только код ответа, но и
что в курсор не ушло ни одного DELETE.

Курсор и приложение берутся из test_crm_routing: это тот же самый Blueprint на
подменённом SQL-слое, и второй такой каркас разъехался бы с первым.
"""

import unittest
from unittest import mock

from crm import queries

from tests.test_crm_routing import FakeCursor, admin_ctx, build_client


def ticket_row(ticket_id=14, created_by=202, status='answered'):
    """Обращение в том виде, в каком его отдаёт queries.get_ticket.

    Ровно те поля, которые читают проверка прав и запись в лог: остальные для
    удаления не нужны, а перечислять их значило бы держать здесь второй слепок
    _ticket_row.
    """
    return {
        'id': ticket_id,
        'subject': 'Документы не поступили · ИИН 060606060606',
        'status': status,
        'created_by': created_by,
        'created_by_name': 'Кастек Гаухар',
        'department_id': 1,
        'queue_department_id': None,
        'author_group_ids': [],
    }


class TicketDeleteEndpointTest(unittest.TestCase):
    def setUp(self):
        self.cursor = FakeCursor()
        self.ctx = admin_ctx()
        self.ticket = ticket_row()
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'get_ticket',
                              lambda cursor, ticket_id, viewer_id=None: self.ticket),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _delete(self, ticket_id=14):
        return build_client(self.cursor, self.ctx).delete('/api/crm/tickets/%s' % ticket_id)

    def _deletes(self):
        return [sql for sql, _params in self.cursor.executed
                if sql.startswith('DELETE FROM crm_tickets')]

    def test_admin_deletes_the_ticket(self):
        response = self._delete()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'deleted')
        # id в ответе: лента удаляет строку по нему, не перечитывая себя целиком.
        self.assertEqual(response.get_json()['id'], 14)
        self.assertEqual(len(self._deletes()), 1)
        self.assertEqual(self.cursor.executed[-1][1], (14,))

    def test_operator_is_refused_and_nothing_is_deleted(self):
        """Самое дорогое место теста: отказ, после которого строки уже нет."""
        self.ctx = admin_ctx(role='operator')
        response = self._delete()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._deletes(), [])

    def test_supervisor_is_refused_and_nothing_is_deleted(self):
        """СВ настраивает очереди, но обращения не удаляет."""
        self.ctx = admin_ctx(role='sv')
        response = self._delete()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._deletes(), [])

    def test_department_head_is_refused_and_nothing_is_deleted(self):
        """Назначение главой ЗАМЕНЯЕТ роль админа — в том числе право удалять."""
        self.ctx = admin_ctx(role='admin')
        self.ctx['headed_department_ids'] = [1]
        self.ctx['headed_department_codes'] = ['szov']
        response = self._delete()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._deletes(), [])

    def test_missing_ticket_is_404_and_not_403(self):
        """Порядок проверок: сначала «есть ли», потом «можно ли».

        Наоборот получилось бы, что второй администратор, удаляющий уже
        удалённое, читает «удалять может только администратор» — то есть
        сообщение, прямо противоречащее происходящему.
        """
        self.ticket = None
        response = self._delete(9999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._deletes(), [])

    def test_closed_ticket_is_still_deletable(self):
        """Прогоны раздела как раз закрыты. Запрет на закрытые — правило ОТВЕТА,
        и скопировать его сюда значило бы сделать мусор невыносимым."""
        for status in ('resolved', 'cancelled'):
            self.cursor = FakeCursor()
            self.ticket = ticket_row(status=status)
            self.assertEqual(self._delete().status_code, 200, status)
            self.assertEqual(len(self._deletes()), 1, status)


if __name__ == '__main__':
    unittest.main()
