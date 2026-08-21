import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_op_calculator_render.test.mjs"


class SalaryOpCalculatorRenderRuntimeTests(unittest.TestCase):
    """Настоящие компоненты калькуляторов ОП «Верификатор» и «Яндекс Регистрация»
    рисуются через react-dom/server: проверяем, что суммы из формул доезжают до
    карточки результата, а не теряются в опечатке поля."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
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
