import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_potok.test.mjs"


class SalaryPotokRuntimeTests(unittest.TestCase):
    """Формулы ОП «Поток» гоняются настоящим Node — сверка с таблицей владельца
    (247 600 ₸ у оператора, 233 600 ₸ у новичка) идёт в .mjs."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_potok_salary_runtime(self):
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
