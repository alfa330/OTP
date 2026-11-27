# -*- coding: utf-8 -*-
"""Имя загруженного файла (common/file_names.py) — таким, каким его назвал человек.

До 27.11.2026 вложения задач шли через werkzeug.secure_filename, и от
«Отчёт за ноябрь.xlsx» в базе оставалось «xlsx» (владелец: «названия файлов,
которые загружаются в задачах, теряются»).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import file_names  # noqa: E402


class DisplayNameTest(unittest.TestCase):
    def test_letters_of_any_alphabet_stay(self):
        for name in ('Отчёт за ноябрь.xlsx', 'Есеп қазан 2026.docx', 'ТЗ (финал) v2.pdf',
                     'Акт №5 от 27.11.2026.pdf', 'report-final_v2.csv'):
            self.assertEqual(file_names.display_name(name), name)

    def test_path_is_dropped(self):
        self.assertEqual(file_names.display_name('C:\\fakepath\\Акт.pdf'), 'Акт.pdf')
        self.assertEqual(file_names.display_name('../../etc/Счёт.pdf'), 'Счёт.pdf')
        self.assertEqual(file_names.display_name('/home/u/план.txt'), 'план.txt')

    def test_invisible_and_flipping_characters_go(self):
        # U+202E переворачивает хвост: «акт‮fdp.doc» на экране — «актcod.pdf».
        self.assertEqual(file_names.display_name('акт\u202efdp.doc'), 'актfdp.doc')
        self.assertEqual(file_names.display_name('сч\u200bёт.pdf'), 'счёт.pdf')
        self.assertEqual(file_names.display_name('a\x00b\x07.txt'), 'ab.txt')
        self.assertEqual(file_names.display_name('\u3164.pdf'), 'pdf')

    def test_spaces_are_plain_and_single(self):
        self.assertEqual(file_names.display_name('План\u00a0на   декабрь.xlsx'), 'План на декабрь.xlsx')
        self.assertEqual(file_names.display_name('  отчёт.pdf  '), 'отчёт.pdf')

    def test_mac_decomposed_letters_are_composed(self):
        decomposed = 'и\u0306ога.pdf'  # «йога» так, как её присылает macOS
        self.assertEqual(file_names.display_name(decomposed), 'йога.pdf')

    def test_characters_windows_refuses_are_removed(self):
        self.assertEqual(file_names.display_name('a<b>:c"d|e?f*.txt'), 'abcdef.txt')

    def test_empty_name_takes_the_fallback(self):
        for raw in (None, '', '   ', '...', '\u202e', 'C:\\dir\\'):
            self.assertEqual(file_names.display_name(raw, fallback='attachment_3'), 'attachment_3')

    def test_long_name_keeps_its_extension(self):
        name = file_names.display_name('Я' * 300 + '.xlsx')
        self.assertEqual(len(name), file_names.NAME_MAX)
        self.assertTrue(name.endswith('Я.xlsx'))


class StorageNameTest(unittest.TestCase):
    def test_path_part_is_latin_with_the_extension(self):
        self.assertEqual(file_names.storage_name('Отчёт за ноябрь.xlsx'), 'file.xlsx')
        self.assertEqual(file_names.storage_name('IMG_1234.jpg'), 'IMG_1234.jpg')
        self.assertEqual(file_names.storage_name('План v2.pdf'), 'v2.pdf')
        self.assertEqual(file_names.storage_name('договор', fallback='attachment_1'), 'attachment_1')
        self.assertEqual(file_names.storage_name('', fallback='attachment_1'), 'attachment_1')

    def test_path_part_never_carries_a_path_or_non_ascii(self):
        for raw in ('../../x/../Счёт.pdf', 'C:\\a\\б.в.г.docx', 'a/b\\c.тхт', '\u202egpj.exe'):
            name = file_names.storage_name(raw)
            self.assertTrue(name.isascii(), name)
            self.assertNotIn('/', name)
            self.assertNotIn('\\', name)
            self.assertNotIn('..', name)


if __name__ == '__main__':
    unittest.main()
