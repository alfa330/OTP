import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_osnova.test.mjs"


class SalaryOsnovaRuntimeTests(unittest.TestCase):
    """Формулы ОП «Основа» гоняются настоящим Node — сверка с таблицей владельца
    (416 550 ₸ у оператора со стажем, 135 600 ₸ у новичка) идёт в .mjs."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_osnova_salary_runtime(self):
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
