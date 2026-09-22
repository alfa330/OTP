# -*- coding: utf-8 -*-
"""Скачивание дистрибутивов идёт в этом же окне и одной механикой.

Замечание владельца 22.09.2026: «Скачать iCore Phone» и «Скачать Oktell»
открывали ссылку в новой вкладке — вкладка начинала загрузку и оставалась
висеть пустой с адресом хранилища, что выглядело как сбой. Ссылку на файл
получают три места, и все три теперь отдают её startFileDownload из
src/utils/fileDownload.js (скрытый iframe; почему не <a download> и не переход
окна — в шапке того файла). Тест держит все три на одной механике: четвёртая
копия window.open(url, '_blank') разъехалась бы молча. Поведение самой
механики проверяет tests/file_download.test.mjs.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HELPER = ROOT / 'src' / 'utils' / 'fileDownload.js'

# (файл, начало функции, её конец, что она спрашивает у сервера)
DOWNLOAD_SITES = (
    (ROOT / 'src' / 'App.jsx',
     'const downloadIcorePhone = async () => {', '\n};',
     '/api/phone/download'),
    (ROOT / 'src' / 'components' / 'sip' / 'SipSettingsView.jsx',
     'const downloadPhone = async () => {', '\n    };',
     '/api/phone/download'),
    (ROOT / 'src' / 'components' / 'oktell_guard' / 'OktellGuardView.jsx',
     'const downloadAgent = useCallback(async () => {', '}, [request]);',
     "request('/download')"),
)


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _function_body(source, head, tail):
    start = source.index(head)
    end = source.index(tail, start)
    return source[start:end]


class FileDownloadSameWindowTests(unittest.TestCase):
    def test_helper_downloads_through_a_hidden_frame_and_never_opens_a_tab(self):
        # Шапка файла как раз объясняет, чем плох window.open, — проверяем код,
        # а не комментарий: он начинается с первого объявления.
        helper = _read(HELPER)
        code = helper[helper.index('const FRAME_ID'):]
        self.assertIn("document.createElement('iframe')", code)
        self.assertIn("frame.style.display = 'none'", code)
        for forbidden in ('window.open', 'location.assign', 'location.href', "'_blank'"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_every_download_site_hands_the_link_to_the_helper(self):
        for path, head, tail, api in DOWNLOAD_SITES:
            with self.subTest(file=path.name):
                source = _read(path)
                self.assertIn('import { startFileDownload }', source.split(head)[0],
                              'startFileDownload должен быть импортирован до функции')
                body = _function_body(source, head, tail)
                self.assertIn(api, body)
                self.assertIn('startFileDownload(data?.url)', body)
                self.assertNotIn('window.open', body)
                self.assertNotIn('_blank', body)

    def test_no_download_site_opens_a_new_tab(self):
        """Ловим и обходную копию: window.open(data.url ...) ни в одном из трёх
        файлов, как бы функция ни называлась."""
        for path, _head, _tail, _api in DOWNLOAD_SITES:
            with self.subTest(file=path.name):
                source = _read(path)
                self.assertNotIn('window.open(data.url', source)
                self.assertNotIn('window.open(data?.url', source)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
