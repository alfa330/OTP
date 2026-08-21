import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_op_calculator_render.test.mjs"
# Сценарий рисует настоящие компоненты через react-dom/server, то есть требует
# установленных зависимостей фронта. В прогоне CI их ставит только job «node»,
# а pytest идёт на чистом чекауте — без этой проверки шаг падал с
# MODULE_NOT_FOUND на каждом коммите. Тот же принцип, что у тестов боевой базы:
# нет окружения — тест пропускается, красный прогон означает регресс в коде.
# Сам сценарий при этом не теряется: его гоняет job «node» после npm ci.
REACT_DOM = ROOT / "node_modules" / "react-dom"


class SalaryOpCalculatorRenderRuntimeTests(unittest.TestCase):
    """Настоящие компоненты калькуляторов ОП «Верификатор» и «Яндекс Регистрация»
    рисуются через react-dom/server: проверяем, что суммы из формул доезжают до
    карточки результата, а не теряются в опечатке поля."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    @unittest.skipUnless(REACT_DOM.is_dir(), "нужен npm ci: сценарий рисует react-dom/server")
    def test_op_calculator_render_runtime(self):
        completed = subprocess.run(
            [shutil.which("node") or "node", str(NODE_TEST)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
