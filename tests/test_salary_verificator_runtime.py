import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_verificator.test.mjs"


class SalaryVerificatorRuntimeTests(unittest.TestCase):
    """Формулы ОП «Верификатор» гоняются настоящим Node — сверка с таблицей
    владельца (172 656 ₸ у оператора со стажем, 171 315 ₸ у новичка) идёт в .mjs."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_verificator_salary_runtime(self):
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
