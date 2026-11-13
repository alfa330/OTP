# -*- coding: utf-8 -*-
"""Файлы задачи открываются в окне просмотра, а не скачиваются.

Просьба владельца 13.11.2026 по разделу «Задачи»: файлы читать, не скачивая —
содержимое открывается в модальном окне в стиле macOS (FilePreviewSheet).
Разбор самих файлов (Word, Excel, PowerPoint, CSV, кодировки) проверяет
tests/file_preview.test.mjs; здесь — проводка раздела, которая ломается молча:
новая группа файлов без onOpenFile снова начала бы скачивать, Esc закрывал бы
карточку под просмотром, а окно с низким слоем уходило бы под карточку.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
SHEET = (ROOT / 'src' / 'components' / 'file_preview' / 'FilePreviewSheet.jsx').read_text(encoding='utf-8')
CSS = (ROOT / 'src' / 'components' / 'file_preview' / 'file-preview.css').read_text(encoding='utf-8')
MOCKUP = (ROOT / 'src' / 'components' / 'file_preview' / 'htmlMockup.js').read_text(encoding='utf-8')


def _code(source):
    """Исходник без комментариев: в шапках модулей перечислены как раз запретные флаги."""
    return re.sub(r'(?m)^\s*//.*$', '', re.sub(r'/\*[\s\S]*?\*/', '', source))


def _between(source, head, tail):
    start = source.index(head)
    return source[start:source.index(tail, start + len(head))]


class TaskFilesOpenInViewerTests(unittest.TestCase):
    def test_every_file_group_of_the_card_opens_the_viewer(self):
        groups = re.findall(r'<TaskFileGroup\b[^>]*?/>', VIEW, flags=re.S)
        self.assertEqual(len(groups), 3, 'файлы задачи, результата и уточнений')
        for group in groups:
            self.assertIn('onOpenFile=', group)
        clarifications = _between(VIEW, '<TaskClarificationsBlock', '/>')
        self.assertIn('onOpenFile={canPreviewFiles ? openFile : undefined}', clarifications)

    def test_file_button_opens_the_viewer_and_only_falls_back_to_download(self):
        group = _between(VIEW, 'const TaskFileGroup = ', 'const RESULT_FILE_BTN_STYLE')
        self.assertIn('onOpenFile ? onOpenFile(files, files.indexOf(att)) : onDownload?.(att)', group)

    def test_view_and_download_share_one_request(self):
        self.assertIn('loadAttachmentFile={loadAttachmentFile}', VIEW)
        download = _between(VIEW, 'const downloadAttachment = useCallback', '}, [loadAttachmentFile, notify]);')
        self.assertIn('previewCache.get(key)', download)
        self.assertIn('previewCache.put(key, blob)', download)
        self.assertIn('cacheKey={taskFileKey}', VIEW)

    def test_pinned_widget_in_picture_in_picture_still_downloads(self):
        # В окне «картинка в картинке» просмотр не помещается, а в окне портала
        # его не видно, пока тот свёрнут.
        opener = _between(VIEW, 'const openPinnedFile = ', '\n  };')
        self.assertIn('if (pipWindow)', opener)
        self.assertIn('onDownloadAttachment?.(att)', opener)
        self.assertNotIn('onClick={() => onDownloadAttachment?.(att)}', VIEW)

    def test_escape_closes_only_the_viewer(self):
        # Карточка задачи закрывается по Esc своим обработчиком на window; просмотр
        # ловит клавишу раньше — в фазе захвата — и дальше её не пускает.
        self.assertIn("window.addEventListener('keydown', onKey, true)", SHEET)
        escape = _between(SHEET, "if (event.key === 'Escape') {", 'return;')
        self.assertIn('event.stopImmediatePropagation()', escape)

    def test_viewer_layer_is_above_the_card_its_windows_and_the_widget(self):
        layer = re.search(r'\.tfv-layer\s*\{[^}]*z-index:\s*(\d+)', CSS)
        self.assertIsNotNone(layer)
        # Карточка — 111 (на телефоне 125), окна действий над задачей и
        # закреплённый виджет — 130.
        self.assertGreater(int(layer.group(1)), 130)

    def test_documents_are_never_inserted_as_raw_html(self):
        # Word, PowerPoint и Excel рисуются React-элементами из разобранных данных;
        # HTML вставляется только из Markdown, прошедшего DOMPurify.
        self.assertEqual(SHEET.count('dangerouslySetInnerHTML'), 1)
        self.assertIn('html: renderMarkdown(text)', SHEET)

    def test_heavy_readers_load_only_when_a_file_needs_them(self):
        for module in ("import('./docxParse.js')", "import('./xlsxParse.js')", "import('./pptxParse.js')",
                       "import('../wazzup/pdfRuntime')", "import('jszip')", "import('papaparse')"):
            self.assertIn(module, SHEET)
        self.assertNotRegex(SHEET, r"(?m)^import .* from '(jszip|papaparse|\./(docx|xlsx|pptx)Parse\.js)'")


class HtmlMockupIsolationTests(unittest.TestCase):
    """HTML-вложение рисуется страницей (просьба 13.11.2026), и его скрипты работают.

    Граница — iframe-клетка: без allow-same-origin у страницы непрозрачное
    происхождение, и до куки, localStorage и DOM портала ей не дотянуться.
    Любое из разрешений ниже эту границу снимает или открывает обход.
    """

    def test_sandbox_allows_scripts_and_nothing_else(self):
        sandbox = re.search(r"export const MOCKUP_SANDBOX = '([^']*)'", MOCKUP)
        self.assertEqual(sandbox.group(1), 'allow-scripts')
        frame = _between(SHEET, '<iframe', '/>')
        self.assertIn("sandbox={scriptsOff ? '' : MOCKUP_SANDBOX}", frame)
        for loophole in ('allow-same-origin', 'allow-top-navigation', 'allow-popups', 'allow-forms',
                         'allow-modals', 'allow-downloads', 'allow-storage-access'):
            self.assertNotIn(loophole, _code(SHEET) + _code(MOCKUP), loophole)

    def test_frame_sends_no_cookies_and_no_portal_address(self):
        frame = _between(SHEET, '<iframe', '/>')
        self.assertIn('credentialless=""', frame)
        self.assertIn('referrerPolicy="no-referrer"', frame)
        self.assertIn('allow=""', frame)

    def test_mockup_is_srcdoc_never_a_url_of_the_portal_origin(self):
        # blob:-адрес принадлежит происхождению портала: «Открыть фрейм в новой
        # вкладке» запускало файл с правами портала (разбор 13.11, H1).
        frame = _between(SHEET, '<iframe', '/>')
        self.assertIn('srcDoc={preview.srcDoc}', frame)
        self.assertNotRegex(frame, r'\bsrc=')
        build = _between(SHEET, "case 'html': {", 'default: {')
        self.assertIn('buildHtmlPreview(', build)
        self.assertNotIn('createObjectURL', build)

    def test_only_its_own_focused_frame_can_close_the_viewer(self):
        listener = _between(SHEET, 'const onMessage = (event) => {', 'window.addEventListener')
        self.assertIn('acceptMockupEscape(event, frame.current, document.activeElement)', listener)

    def test_page_that_leaves_is_brought_back_then_loses_scripts(self):
        # Правила счёта — createLeaveWatch (проверены в node); здесь — что окно им следует.
        on_load = _between(SHEET, 'const onLoad = (event) => {', '    return (')
        self.assertIn('watch.current.load(event.currentTarget)', on_load)
        self.assertIn("if (verdict === 'shown') return;", on_load)
        self.assertIn('turnOff?.()', on_load)
        self.assertIn('setFrameKey', on_load)

    def test_code_view_keeps_the_mockup_alive(self):
        # «Код» прячет сцену, а не снимает iframe: прототип не перезагружается и
        # не теряет состояние, а возврат не выглядит как уход со страницы.
        view = _between(SHEET, 'function HtmlView(', 'function ImageView(')
        self.assertIn("hidden={mode === 'code'}", view)
        self.assertNotIn("mode === 'code' ? <TextView", view)

    def test_viewer_is_a_full_screen_layer_over_the_same_page(self):
        # 13.11.2026: «окно смотрится не очень… на весь экран и оставаться на той
        # же странице». Слой поверх портала, а не окно посреди страницы и не переход.
        self.assertNotIn('IosModal', _code(SHEET))
        self.assertIn('className={`tfv-screen otp-modal-root', SHEET)
        self.assertIn('createPortal(', SHEET)
        screen = re.search(r'\.tfv-screen\s*\{[^}]*\}', CSS).group(0)
        self.assertIn('position: fixed;', screen)
        self.assertIn('inset: 0;', screen)
        self.assertIn('useScreenBackGesture(isNarrow && open, close)', SHEET)


if __name__ == '__main__':
    unittest.main()
