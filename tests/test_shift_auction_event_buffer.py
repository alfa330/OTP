import ast
import collections
import threading
import unittest
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"
HELPERS = {
    "_publish_shift_auction_events",
    "_read_shift_auction_events_from_buffer",
    "_drain_shift_auction_events",
    "_initialize_shift_auction_event_buffer",
}


def _buffer_namespace(maxlen=3, fetch_limit=2):
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    functions = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    namespace = {
        "SHIFT_AUCTION_EVENT_BUFFER_MAXLEN": maxlen,
        "SHIFT_AUCTION_EVENT_FETCH_LIMIT": fetch_limit,
        "shift_auction_event_condition": threading.Condition(),
        "shift_auction_event_signal_id": 0,
        "shift_auction_event_buffer": collections.deque(maxlen=maxlen),
        "shift_auction_event_buffer_max_id": 0,
        "shift_auction_event_buffer_ready": False,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def _events(start, end):
    return [
        {"id": event_id, "event_type": "lot_claimed", "payload": {"n": event_id}}
        for event_id in range(start, end + 1)
    ]


class ShiftAuctionEventBufferTests(unittest.TestCase):
    def test_recent_cursor_reads_from_memory_and_old_cursor_requires_catchup(self):
        namespace = _buffer_namespace()
        namespace["_publish_shift_auction_events"](_events(10, 12))

        events, covered, _, floor_id = namespace["_read_shift_auction_events_from_buffer"](11)
        self.assertTrue(covered)
        self.assertEqual([event["id"] for event in events], [12])
        self.assertEqual(floor_id, 10)

        events, covered, _, floor_id = namespace["_read_shift_auction_events_from_buffer"](8)
        self.assertFalse(covered)
        self.assertEqual([event["id"] for event in events], [10, 11, 12])
        self.assertEqual(floor_id, 10)

    def test_drain_pages_to_exhaustion_and_keeps_latest_window(self):
        namespace = _buffer_namespace(maxlen=3, fetch_limit=2)
        source_events = _events(1, 5)
        calls = []

        def fetch_events(_cursor, after_id, limit=2):
            calls.append(after_id)
            return [event for event in source_events if event["id"] > after_id][:limit]

        namespace["_fetch_shift_auction_events_with_cursor"] = fetch_events
        namespace["_drain_shift_auction_events"](object())

        self.assertEqual(calls, [0, 2, 4])
        self.assertEqual(namespace["shift_auction_event_buffer_max_id"], 5)
        self.assertEqual(
            [event["id"] for event in namespace["shift_auction_event_buffer"]],
            [3, 4, 5],
        )

    def test_empty_warmup_marks_buffer_ready_and_wakes_streams(self):
        namespace = _buffer_namespace()
        namespace["_fetch_recent_shift_auction_events_with_cursor"] = lambda _cursor: []

        namespace["_initialize_shift_auction_event_buffer"](object())

        self.assertTrue(namespace["shift_auction_event_buffer_ready"])
        self.assertEqual(namespace["shift_auction_event_signal_id"], 1)


if __name__ == "__main__":
    unittest.main()
