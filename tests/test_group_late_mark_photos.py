# -*- coding: utf-8 -*-
"""Фото отметок Workpace в окне «Отметки за день» (13.11.2026).

Проверяется модуль group_late.mark_photos на подделках Workpace и базы:
как строка окна находит свою отметку (по номеру и, у старых дней, по секунде),
что глава отдела не получает чужие отметки ни поиском, ни номером, что ответ
карточки запоминается, а наружу уходит только настоящая картинка.
"""

import unittest
from datetime import date

from group_late import attendance, mark_photos
from group_late.workpace import WorkpaceError, _uuid

DAY = date(2026, 11, 13)
ID_A = "11111111-1111-4111-8111-111111111111"
ID_B = "22222222-2222-4222-8222-222222222222"
ID_C = "33333333-3333-4333-8333-333333333333"
FILE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _mark(mark_id, at, employee_id="card-1", name="Иванов Иван", department="Регионы", origin=1):
    return {"id": mark_id, "markDate": at, "employeeId": employee_id, "employeeName": name,
            "departmentName": department, "origin": origin, "markType": 0, "status": 1}


class FakeClient:
    def __init__(self, marks, cards=None, photos=None):
        self.marks = marks
        self.cards = cards or {}
        self.photos = photos or {}
        self.card_calls = []
        self.photo_calls = []
        self.list_calls = 0

    def get_marks(self, start, end):
        self.list_calls += 1
        return list(self.marks)

    def get_mark_card(self, mark_id):
        self.card_calls.append(mark_id)
        card = self.cards.get(mark_id)
        if isinstance(card, Exception):
            raise card
        return card or {}

    def download_photo(self, file_id):
        self.photo_calls.append(file_id)
        return self.photos.get(file_id, (b"", ""))


class FakeDb:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.stored = []

    def glb_mark_photo_rows(self, mark_ids):
        return {key: dict(self.rows[key]) for key in mark_ids if key in self.rows}

    def glb_store_mark_photos(self, rows):
        for row in rows:
            self.stored.append(row)
            self.rows[row["mark_id"]] = {"file_id": row["file_id"], "department_name": row["department_name"]}
        return len(rows)


class LookupTests(unittest.TestCase):
    def setUp(self):
        mark_photos._day_lists.clear()
        mark_photos._photo_cache.clear()

    def test_mark_found_by_id_and_photo_remembered(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12")], cards={ID_A: {"fileId": FILE_A}})
        db = FakeDb()
        result = mark_photos.lookup(db, "2026-11-13", [{"key": "0", "id": ID_A}], client=client)
        self.assertEqual(result["items"], [{"key": "0", "mark_id": ID_A, "has_photo": True}])
        self.assertEqual(db.stored[0]["file_id"], FILE_A)
        self.assertEqual(db.stored[0]["department_name"], "Регионы")

    def test_mark_without_photo_is_answered_and_remembered(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12", origin=2)], cards={ID_A: {"fileId": None}})
        db = FakeDb()
        result = mark_photos.lookup(db, DAY, [{"key": "0", "id": ID_A}], client=client)
        self.assertEqual(result["items"], [{"key": "0", "mark_id": ID_A, "has_photo": False}])
        self.assertIsNone(db.stored[0]["file_id"])

    def test_known_mark_does_not_ask_the_card_again(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12")])
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Регионы"}})
        result = mark_photos.lookup(db, DAY, [{"key": "0", "id": ID_A}], client=client)
        self.assertEqual(client.card_calls, [])
        self.assertTrue(result["items"][0]["has_photo"])

    def test_old_day_without_id_matches_by_second(self):
        # Дни, собранные до 13.11.2026, лежат в кэше без номера отметки.
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12.517"), _mark(ID_B, "2026-11-13T18:01:00")],
                            cards={ID_A: {"fileId": FILE_A}, ID_B: {}})
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "at": "2026-11-13T09:03:12"}], client=client)
        self.assertEqual(result["items"], [{"key": "0", "mark_id": ID_A, "has_photo": True}])
        self.assertEqual(client.card_calls, [ID_A])

    def test_second_card_of_the_same_person_still_matches_by_second(self):
        # План встал на первую карточку, отметка лежит на второй (две карточки Workpace).
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12", employee_id="card-2", name="Иванов И.")],
                            cards={ID_A: {"fileId": FILE_A}})
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "at": "2026-11-13T09:03:12",
                                                     "employee_id": "card-1", "employee": "Иванов Иван"}],
                                    client=client)
        self.assertEqual(result["items"][0]["mark_id"], ID_A)

    def test_same_second_two_people_resolved_by_person(self):
        client = FakeClient([
            _mark(ID_A, "2026-11-13T09:03:12", employee_id="card-1", name="Иванов Иван"),
            _mark(ID_B, "2026-11-13T09:03:12", employee_id="card-9", name="Петров Пётр"),
        ], cards={ID_A: {}, ID_B: {"fileId": FILE_A}})
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "at": "2026-11-13T09:03:12",
                                                     "employee": "петров пётр"}], client=client)
        self.assertEqual(result["items"][0]["mark_id"], ID_B)

    def test_same_second_unresolved_gives_nothing(self):
        client = FakeClient([
            _mark(ID_A, "2026-11-13T09:03:12", employee_id="card-1"),
            _mark(ID_B, "2026-11-13T09:03:12", employee_id="card-9"),
        ])
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "at": "2026-11-13T09:03:12",
                                                     "employee_id": "card-5"}], client=client)
        self.assertEqual(result["items"], [])
        self.assertEqual(client.card_calls, [])

    def test_head_does_not_get_marks_of_another_department(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12", department="Основной отдел"),
                             _mark(ID_B, "2026-11-13T09:05:00", department="Регионы")],
                            cards={ID_A: {"fileId": FILE_A}, ID_B: {"fileId": FILE_A}})
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "id": ID_A}, {"key": "1", "id": ID_B}],
                                    scope="Регионы", client=client)
        self.assertEqual([item["mark_id"] for item in result["items"]], [ID_B])
        self.assertEqual(client.card_calls, [ID_B])

    def test_card_error_is_skipped_and_not_remembered(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12")], cards={ID_A: WorkpaceError("timeout")})
        db = FakeDb()
        result = mark_photos.lookup(db, DAY, [{"key": "0", "id": ID_A}], client=client)
        self.assertEqual(result["items"], [])
        self.assertEqual(db.stored, [])

    def test_unknown_mark_and_garbage_items_are_ignored(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12")])
        result = mark_photos.lookup(FakeDb(), DAY, [{"key": "0", "id": ID_C}, "мусор", None,
                                                    {"key": "1", "at": "не дата"}], client=client)
        self.assertEqual(result["items"], [])

    def test_items_are_capped(self):
        marks = [_mark(f"{index:08d}-1111-4111-8111-111111111111", f"2026-11-13T09:{index % 60:02d}:00")
                 for index in range(60)]
        client = FakeClient(marks)
        items = [{"key": str(index), "id": mark["id"]} for index, mark in enumerate(marks)]
        mark_photos.lookup(FakeDb(), DAY, items, client=client)
        self.assertEqual(len(client.card_calls), mark_photos.MAX_ITEMS)

    def test_day_list_is_reused_within_a_minute(self):
        client = FakeClient([_mark(ID_A, "2026-11-13T09:03:12")], cards={ID_A: {}})
        db = FakeDb()
        mark_photos.lookup(db, DAY, [{"key": "0", "id": ID_A}], client=client)
        mark_photos.lookup(db, DAY, [{"key": "0", "id": ID_A}], client=client)
        self.assertEqual(client.list_calls, 1)

    def test_bad_date_is_rejected(self):
        with self.assertRaises(ValueError):
            mark_photos.lookup(FakeDb(), "13.11.2026", [{"key": "0", "id": ID_A}], client=FakeClient([]))


class PhotoTests(unittest.TestCase):
    def setUp(self):
        mark_photos._photo_cache.clear()

    def test_photo_of_a_found_mark(self):
        client = FakeClient([], photos={FILE_A: (JPEG, "image/jpeg")})
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Регионы"}})
        data, content_type = mark_photos.photo(db, ID_A, client=client)
        self.assertEqual((data, content_type), (JPEG, "image/jpeg"))

    def test_photo_is_kept_in_memory(self):
        client = FakeClient([], photos={FILE_A: (JPEG, "image/jpeg")})
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Регионы"}})
        mark_photos.photo(db, ID_A, client=client)
        mark_photos.photo(db, ID_A, client=client)
        self.assertEqual(client.photo_calls, [FILE_A])

    def test_mark_never_looked_up_has_no_photo(self):
        # Номер с экрана сам по себе доступа не даёт: только строка, записанная поиском.
        with self.assertRaises(LookupError):
            mark_photos.photo(FakeDb(), ID_A, client=FakeClient([]))

    def test_mark_without_photo(self):
        db = FakeDb({ID_A: {"file_id": None, "department_name": "Регионы"}})
        client = FakeClient([])
        with self.assertRaises(LookupError):
            mark_photos.photo(db, ID_A, client=client)
        # «Фото нет» — сразу 404, без похода в Workpace (там это была бы ошибка 502).
        self.assertEqual(client.photo_calls, [])

    def test_head_cannot_open_photo_of_another_department(self):
        client = FakeClient([], photos={FILE_A: (JPEG, "image/jpeg")})
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Основной отдел"}})
        with self.assertRaises(LookupError):
            mark_photos.photo(db, ID_A, scope="Регионы", client=client)
        self.assertEqual(client.photo_calls, [])

    def test_not_an_image_is_not_served(self):
        client = FakeClient([], photos={FILE_A: (b"<html>oops</html>", "image/jpeg")})
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Регионы"}})
        with self.assertRaises(LookupError):
            mark_photos.photo(db, ID_A, client=client)

    def test_type_is_taken_from_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        client = FakeClient([], photos={FILE_A: (png, "application/octet-stream")})
        db = FakeDb({ID_A: {"file_id": FILE_A, "department_name": "Регионы"}})
        self.assertEqual(mark_photos.photo(db, ID_A, client=client)[1], "image/png")


class MarkOutTests(unittest.TestCase):
    def test_workpace_mark_carries_its_number(self):
        out = attendance._mark_out({"id": ID_A, "markDate": "2026-11-13T09:03:12", "markType": 0, "status": 1})
        self.assertEqual(out["id"], ID_A)

    def test_clockster_mark_has_no_number(self):
        out = attendance._mark_out({"markDate": "2026-11-13T09:03:12", "markType": 0, "markRole": 0,
                                    "status": 1, "markSystem": "clockster", "id": "x"})
        self.assertNotIn("id", out)


class UuidTests(unittest.TestCase):
    def test_only_real_uuid_goes_into_the_path(self):
        self.assertEqual(_uuid(ID_A.upper()), ID_A)
        for bad in ("../../api/auth", "", None, "123"):
            with self.assertRaises(WorkpaceError):
                _uuid(bad)


if __name__ == "__main__":
    unittest.main()
