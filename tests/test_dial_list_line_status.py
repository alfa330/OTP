# -*- coding: utf-8 -*-
"""Код 150 от Binotel: текст ошибки говорит, что Binotel думает о линии.

25.09.2026 линия 904 была зарегистрирована (REGISTER 200, ответы на OPTIONS), а
click-to-call обзвона полчаса отвечал «Can't call to the ext»: Binotel пометил линию
«не в сети» при снятии регистрации по событию «сеть изменилась» и вернул в сеть
только своим обходом в 17:17. Подсказка «проверьте регистрацию» отправляла оператора
чинить исправный телефон. Теперь текст берёт статус линии у самого Binotel.
"""

import inspect
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dial_list import service as dial_service  # noqa: E402


def _ts(hh, mm):
    """Unix-время сегодняшних hh:mm по Алматы — как отдаёт Binotel (updatedAt)."""
    now = datetime.now(dial_service.PERIOD_TZ)
    return int(now.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp())


def _line(number, state, updated_at=None, was_online_at=None):
    return {"endpointData": {
        "internalNumber": number, "login": "v6e2r7r9",
        "wasOnlineAt": was_online_at or 0,
        "status": {"sip": {"status": state, "updatedAt": updated_at or 0},
                   "preparedStatus": state},
    }}


class _DB:
    pass


class LineStatusHintTests(unittest.TestCase):
    def _svc(self, lines=None, error=None):
        svc = dial_service.DialListService(_DB())

        def employees(_department_id):
            if error:
                raise error
            return list(lines or [])
        svc._binotel_employees = employees
        return svc

    def test_offline_line_explains_binotel_monitoring(self):
        svc = self._svc([_line("904", "offline", _ts(16, 47), _ts(16, 42))])
        hint = svc._line_status_hint(7, "904")
        self.assertIn("не в сети с 16:47", hint)
        self.assertIn("последний раз в сети 16:42", hint)
        self.assertIn("30–40 минут", hint)
        self.assertIn("подождите и повторите", hint)

    def test_online_line_says_retry(self):
        svc = self._svc([_line("904", "online", _ts(17, 17), _ts(17, 17))])
        hint = svc._line_status_hint(7, "904")
        self.assertIn("видит линию в сети", hint)
        self.assertNotIn("проверьте регистрацию", hint)

    def test_inuse_counts_as_online(self):
        svc = self._svc([_line("903", "inuse", _ts(17, 19))])
        self.assertIn("видит линию в сети", svc._line_status_hint(7, "903"))

    def test_unknown_line_falls_back_to_registration_hint(self):
        svc = self._svc([_line("903", "online", _ts(17, 19))])
        self.assertIn("проверьте регистрацию", svc._line_status_hint(7, "904"))

    def test_binotel_error_never_breaks_the_message(self):
        svc = self._svc(error=RuntimeError("Binotel недоступен"))
        self.assertIn("проверьте регистрацию", svc._line_status_hint(7, "904"))

    def test_unparsable_timestamps_do_not_crash(self):
        svc = self._svc([_line("904", "offline", "не число", None)])
        hint = svc._line_status_hint(7, "904")
        self.assertIn("не в сети с ?", hint)
        self.assertIn("последний раз в сети ?", hint)

    def test_start_call_uses_the_hint(self):
        src = inspect.getsource(dial_service.DialListService.start_call)
        self.assertIn('self._line_status_hint(ctx["department_id"], ctx["internal_number"])', src)
        self.assertIn('не на связи', src)


if __name__ == '__main__':
    unittest.main()
