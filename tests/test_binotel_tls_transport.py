# -*- coding: utf-8 -*-
"""SIP по TLS для шифрованных линий Binotel (22.10.2026).

В фронт-офисе в Шымкенте (Beeline) порт 5060 режет сеть: UDP и TCP на 5060 молча пропадали
до любых SIP-серверов, а TLS 5061 до Binotel проходил. Binotel пускает по TLS только
шифрованную линию (endpointData.encryptedWithTls = "1"; обычной — 403 на REGISTER с верным
паролем, проверено на линии 926), поэтому сервер ставит транспорт TLS ровно тогда, когда
линия шифрованная и телефон его умеет (3.22.38+). Во всех остальных случаях ответ прежний — UDP.
"""

import ast
import copy
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dial_list import service as dial_service  # noqa: E402
from tests import source_cache  # noqa: E402

BOT_PATH = ROOT / "bot_schedule2.py"


def _employees(*lines):
    """Ответ settings/list-of-employees: {номер: encryptedWithTls}."""
    return {"status": "success", "listOfEmployees": {
        str(i): {"endpointData": {"internalNumber": number, "encryptedWithTls": flag}}
        for i, (number, flag) in enumerate(lines)}}


class _Client:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def _post(self, endpoint, params, max_wait=None, timeout=None):
        self.calls.append((endpoint, max_wait))
        if self.error:
            raise self.error
        return self.payload


class PhoneHasTlsTests(unittest.TestCase):
    def test_only_338_and_newer(self):
        for value, expected in ((None, False), ("", False), ("мусор", False),
                                ("3.22.37", False), ("3.22.38", True), ("3.22.38.0", True),
                                ("3.23", True), ("4.0.0", True)):
            self.assertIs(dial_service.phone_has_tls(value), expected, value)


class LineUsesTlsTests(unittest.TestCase):
    def _svc(self, client, configured=True):
        svc = dial_service.DialListService(object(), client_factory=lambda dep: client)
        svc.department_settings = lambda dep: {"configured": configured, "department_id": dep}
        return svc

    def test_flag_per_line_and_one_request_for_the_company(self):
        client = _Client(_employees(("926", "1"), ("931", "0"), ("905", "")))
        svc = self._svc(client)
        self.assertTrue(svc.line_uses_tls(1954, "926"))
        self.assertFalse(svc.line_uses_tls(1954, "931"))
        self.assertFalse(svc.line_uses_tls(1954, "905"))
        self.assertFalse(svc.line_uses_tls(1954, "999"))
        self.assertTrue(svc.line_uses_tls(1954, " 926 "))
        # Флаги всех линий — одним запросом, дальше из кэша; очередь к Binotel — коротко.
        self.assertEqual([("settings/list-of-employees", dial_service.LINE_TLS_MAX_WAIT)], client.calls)

    def test_cache_expires(self):
        client = _Client(_employees(("926", "1")))
        svc = self._svc(client)
        with mock.patch.object(dial_service.time, "monotonic", return_value=1000.0):
            self.assertTrue(svc.line_uses_tls(1954, "926"))
        client.payload = _employees(("926", "0"))
        with mock.patch.object(dial_service.time, "monotonic",
                               return_value=1000.0 + dial_service.LINE_TLS_TTL_SECONDS + 1):
            self.assertFalse(svc.line_uses_tls(1954, "926"))
        self.assertEqual(2, len(client.calls))

    def test_unconfigured_department_never_asks_binotel(self):
        """Компанию Binotel знают только настройки раздела: чужой одноимённый номер дал бы
        чужой флаг."""
        client = _Client(_employees(("926", "1")))
        self.assertFalse(self._svc(client, configured=False).line_uses_tls(909, "926"))
        self.assertEqual([], client.calls)

    def test_binotel_failure_keeps_last_known_or_udp(self):
        client = _Client(_employees(("926", "1")))
        svc = self._svc(client)
        with mock.patch.object(dial_service.time, "monotonic", return_value=1000.0):
            self.assertTrue(svc.line_uses_tls(1954, "926"))
        client.error = RuntimeError("Binotel status='error' code=106")
        with mock.patch.object(dial_service.time, "monotonic",
                               return_value=1000.0 + dial_service.LINE_TLS_TTL_SECONDS + 1):
            # Отказ Binotel не превращает шифрованную линию в обычную на 2 минуты.
            self.assertTrue(svc.line_uses_tls(1954, "926"))
        fresh = self._svc(_Client(error=RuntimeError("timeout")))
        self.assertFalse(fresh.line_uses_tls(1954, "926"))

    def test_empty_number(self):
        client = _Client(_employees(("926", "1")))
        self.assertFalse(self._svc(client).line_uses_tls(1954, ""))
        self.assertEqual([], client.calls)


class SipTransportHelperTests(unittest.TestCase):
    """_icore_phone_sip_transport (bot_schedule2) — исполненная с заглушками."""

    MAIN = {"username": "8vt7tivn", "password": "p", "server": "sip53.binotel.com",
            "transport": "UDP", "domain": "sip53.binotel.com", "auth_id": "8vt7tivn", "number": "926"}

    def _helper(self, version="3.22.38", member=None, own_department=909, tls_lines=("926",),
                service_error=None):
        node = copy.deepcopy(source_cache.function_node(BOT_PATH, "_icore_phone_sip_transport"))
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        seen = {"line_uses_tls": []}

        class _Service:
            @staticmethod
            def line_uses_tls(department_id, number):
                seen["line_uses_tls"].append((department_id, number))
                if service_error:
                    raise service_error
                return number in tls_lines

        class _Db:
            @staticmethod
            def get_user_department_id(user_id):
                seen["own_department_read"] = True
                return own_department

        headers = {} if version is None else {dial_service.PHONE_VERSION_HEADER: version}
        namespace = {
            "request": mock.Mock(headers=headers),
            "logging": logging,
            "db": _Db,
            "_dial_list_service": _Service,
            "_dial_list_line_member": lambda user_id: member,
        }
        exec(compile(module, "<sip-transport>", "exec"), namespace)
        return namespace["_icore_phone_sip_transport"], seen

    def test_encrypted_line_and_new_phone_get_tls(self):
        helper, seen = self._helper(member={"department_id": 1954, "internal_number": "926"})
        main = helper(427, "binotel", dict(self.MAIN))
        self.assertEqual("TLS", main["transport"])
        # Компания — по отделу ЛИНИИ (удалённый КЦ), а не по отделу человека.
        self.assertEqual([(1954, "926")], seen["line_uses_tls"])
        self.assertNotIn("own_department_read", seen)
        # Остальные поля учётки не меняются.
        self.assertEqual({k: v for k, v in self.MAIN.items() if k != "transport"},
                         {k: v for k, v in main.items() if k != "transport"})

    def test_own_department_operator(self):
        helper, seen = self._helper(member=None, own_department=1954)
        self.assertEqual("TLS", helper(5, "binotel", dict(self.MAIN))["transport"])
        self.assertEqual([(1954, "926")], seen["line_uses_tls"])

    def test_old_or_unknown_phone_keeps_udp(self):
        for version in (None, "3.22.37", "мусор"):
            helper, seen = self._helper(version=version, member={"department_id": 1954})
            self.assertEqual("UDP", helper(427, "binotel", dict(self.MAIN))["transport"], version)
            self.assertEqual([], seen["line_uses_tls"], version)

    def test_plain_line_keeps_udp(self):
        helper, _ = self._helper(member={"department_id": 1954}, tls_lines=())
        self.assertEqual("UDP", helper(427, "binotel", dict(self.MAIN))["transport"])

    def test_local_pbx_is_never_touched(self):
        helper, seen = self._helper()
        main = {"username": "6735", "password": "p", "server": "pbx", "transport": "UDP", "number": "6735"}
        self.assertEqual(main, helper(5, "asterisk", dict(main)))
        self.assertEqual([], seen["line_uses_tls"])

    def test_failure_keeps_udp(self):
        helper, _ = self._helper(member={"department_id": 1954}, service_error=RuntimeError("boom"))
        with self.assertLogs(level="ERROR"):
            self.assertEqual("UDP", helper(427, "binotel", dict(self.MAIN))["transport"])

    def test_no_main_or_number(self):
        helper, seen = self._helper(member={"department_id": 1954})
        self.assertIsNone(helper(427, "binotel", None))
        no_number = dict(self.MAIN, number="")
        self.assertEqual("UDP", helper(427, "binotel", no_number)["transport"])
        self.assertEqual([], seen["line_uses_tls"])


class EndpointWiringTests(unittest.TestCase):
    def test_transport_is_applied_before_the_payload(self):
        node = source_cache.function_node(BOT_PATH, "operator_sip_settings_endpoint")
        body = ast.get_source_segment(source_cache.read(BOT_PATH), node)
        call = "main = _icore_phone_sip_transport(requester_id, provider, main)"
        self.assertIn(call, body)
        self.assertLess(body.index(call), body.index("payload = {"))
        # Транспорт доходит до телефона через плоские поля основного аккаунта.
        self.assertIn("**main", body)


if __name__ == "__main__":
    unittest.main()
