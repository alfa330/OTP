# -*- coding: utf-8 -*-
"""Потолок одновременных SSE-потоков аукциона.

Каждый поток занимает нить waitress на всё время соединения, а нитей на весь
портал около 96, и колокол со своим `BELL_STREAM_LIMIT` берёт из того же бюджета.
До этой правки у аукциона потолка не было вовсе: достаточно оставить открытой
вкладку живого аукциона на каждом мониторе, чтобы нити кончились и портал
перестал отвечать целиком — не деградация раздела, а остановка всего.

Здесь закреплены две вещи: сами слоты (берутся, отдаются, не уходят в минус,
переживают гонку) и то, что обработчик их действительно использует. Второе —
статическая проверка исходника: слот, который берут и не отдают, хуже, чем
отсутствие слотов вовсе, — счётчик уползёт вверх, и через несколько суток
раздел перестанет принимать кого-либо.
"""

import ast
import threading
import unittest
from pathlib import Path

from tests import source_cache


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"
HELPERS = {
    "_try_acquire_shift_auction_stream_slot",
    "_release_shift_auction_stream_slot",
    "_shift_auction_active_stream_count",
}
HANDLER = "api_shift_auction_test_events"


def _module():
    return source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))


def _slots_namespace(limit=2):
    functions = [
        node for node in _module().body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    assert len(functions) == len(HELPERS), 'функции работы со слотами не найдены в монолите'
    namespace = {
        "SHIFT_AUCTION_STREAM_LIMIT": limit,
        "shift_auction_stream_lock": threading.Lock(),
        "shift_auction_active_streams": 0,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def _handler_node():
    for node in _module().body:
        if isinstance(node, ast.FunctionDef) and node.name == HANDLER:
            return node
    raise AssertionError('обработчик %s не найден' % HANDLER)


def _calls(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        # call_on_close(_release_...) передаёт функцию БЕЗ вызова — иначе слот
        # освободился бы сразу, а не на закрытии ответа.
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


class SlotsTest(unittest.TestCase):
    def test_limit_is_respected(self):
        ns = _slots_namespace(limit=2)
        self.assertTrue(ns["_try_acquire_shift_auction_stream_slot"]())
        self.assertTrue(ns["_try_acquire_shift_auction_stream_slot"]())
        self.assertFalse(ns["_try_acquire_shift_auction_stream_slot"](),
                         'третий поток получил место при потолке в два')
        self.assertEqual(ns["_shift_auction_active_stream_count"](), 2)

    def test_release_frees_place(self):
        ns = _slots_namespace(limit=1)
        self.assertTrue(ns["_try_acquire_shift_auction_stream_slot"]())
        self.assertFalse(ns["_try_acquire_shift_auction_stream_slot"]())
        ns["_release_shift_auction_stream_slot"]()
        self.assertEqual(ns["_shift_auction_active_stream_count"](), 0)
        self.assertTrue(ns["_try_acquire_shift_auction_stream_slot"](),
                        'место не вернулось после закрытия потока')

    def test_release_never_goes_negative(self):
        # Двойное закрытие ответа не должно «печатать» лишние места.
        ns = _slots_namespace(limit=1)
        ns["_release_shift_auction_stream_slot"]()
        ns["_release_shift_auction_stream_slot"]()
        self.assertEqual(ns["_shift_auction_active_stream_count"](), 0)

    def test_no_overbooking_under_race(self):
        # Проверка на то, ради чего взят замок: waitress раздаёт запросы нитям,
        # и без него два потока проскочили бы мимо потолка одновременно.
        ns = _slots_namespace(limit=10)
        acquire = ns["_try_acquire_shift_auction_stream_slot"]
        granted = []
        lock = threading.Lock()
        start = threading.Event()

        def worker():
            start.wait()
            if acquire():
                with lock:
                    granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()
        self.assertEqual(len(granted), 10)
        self.assertEqual(ns["_shift_auction_active_stream_count"](), 10)


class HandlerUsesSlotsTest(unittest.TestCase):
    def test_handler_acquires_and_releases(self):
        names = _calls(_handler_node())
        self.assertIn("_try_acquire_shift_auction_stream_slot", names,
                      'обработчик перестал занимать место — потолок ничего не значит')
        self.assertIn("call_on_close", names,
                      'нет call_on_close: место не вернётся при обрыве соединения')
        self.assertIn("_release_shift_auction_stream_slot", names,
                      'место занимается, но не освобождается — счётчик уползёт вверх')

    def test_slot_taken_before_response_is_built(self):
        # Порядок важен: занимать место после того, как поток отдан клиенту,
        # поздно — нить waitress уже занята.
        source = ast.unparse(_handler_node())
        acquire_at = source.index("_try_acquire_shift_auction_stream_slot")
        response_at = source.index("Response(generate()")
        self.assertLess(acquire_at, response_at,
                        'место занимается позже, чем создаётся поток')


if __name__ == '__main__':
    unittest.main()
