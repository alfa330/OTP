"""Подложка окна не закрывает его, когда выделение текста отпускают за краем.

Браузер шлёт `click` не тому, над чем отпустили кнопку, а ближайшему общему
предку нажатия и отпускания. Если подложка ОБОРАЧИВАЕТ панель, этим предком
оказывается она сама: нажал в поле, протянул выделение за край окна, отпустил —
окно закрылось вместе с набранным. Приём лечения один на весь портал и живёт в
`src/components/common/useOverlayDismiss.js`.

Тестами поведение не проверить (нужен настоящий браузер), поэтому здесь —
страж исходников: сам хук на месте, и ни один из вылеченных потребителей не
вернулся к голому onClick.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "src" / "components" / "common" / "useOverlayDismiss.js"
TASKS_VIEW_PATH = ROOT / "src" / "components" / "tasks" / "TasksView.jsx"
DISPUTE_PATH = ROOT / "src" / "components" / "modals" / "DisputeModal.jsx"
TECHNICAL_PATH = ROOT / "src" / "components" / "technical" / "TechnicalIssuesView.jsx"
APP_JSX_PATH = ROOT / "src" / "App.jsx"

# Файл → как он импортирует хук → сколько подложек на нём сидит.
CONSUMERS = (
    (TASKS_VIEW_PATH, "import useOverlayDismiss from '../common/useOverlayDismiss';", 5),
    (DISPUTE_PATH, "import useOverlayDismiss from '../common/useOverlayDismiss';", 1),
    (TECHNICAL_PATH, "import useOverlayDismiss from '../common/useOverlayDismiss';", 2),
    (APP_JSX_PATH, "import useOverlayDismiss from './components/common/useOverlayDismiss';", 1),
)


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class OverlayDismissHookTests(unittest.TestCase):
    def setUp(self):
        self.hook = _read(HOOK_PATH)

    def test_hook_requires_press_and_release_on_the_backdrop(self):
        """
        Закрываем, только если и нажали, и отпустили ровно по подложке.
        Проверка одного лишь `target === currentTarget` на onClick — ложная
        защита: при отпускании за краем окна target подложки и есть.
        """
        self.assertIn("export default function useOverlayDismiss(onClose)", self.hook)
        self.assertIn("onPointerDown: (event) => {", self.hook)
        self.assertIn("down: event.target === event.currentTarget", self.hook)
        self.assertIn("pressRef.current.up = event.target === event.currentTarget;", self.hook)
        self.assertIn(
            "if (down && up && event.target === event.currentTarget) onClose();",
            self.hook,
        )

    def test_hook_resets_between_gestures(self):
        """
        Без сброса одна удачная пара «нажал-отпустил» закрывала бы окно и на
        следующем, постороннем клике.
        """
        self.assertIn("pressRef.current = { down: false, up: false };", self.hook)
        self.assertIn("pressRef.current = { down: event.target === event.currentTarget, up: false };", self.hook)

    def test_consumers_import_and_spread_the_hook(self):
        for path, import_line, overlays in CONSUMERS:
            src = _read(path)
            with self.subTest(file=path.name):
                self.assertIn(import_line, src)
                self.assertEqual(
                    len(re.findall(r"= useOverlayDismiss\(", src)),
                    overlays,
                    "число подложек разошлось с ожидаемым",
                )

    def test_cured_backdrops_did_not_return_to_bare_onclick(self):
        """
        Ложная защита `onClick={… target === currentTarget …}` на подложке
        выглядит рабочей и потому возвращается при копировании соседнего кода.
        В вылеченных файлах её быть не должно.
        """
        false_guard = re.compile(
            r"onClick=\{\((?:e|event)\)\s*=>\s*\{?\s*if\s*\(\s*(?:e|event)\.target === (?:e|event)\.currentTarget",
        )
        for path, _, _ in CONSUMERS:
            with self.subTest(file=path.name):
                self.assertIsNone(false_guard.search(_read(path)))


if __name__ == "__main__":
    unittest.main()
