import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "salary_yandex_reg.test.mjs"


class SalaryYandexRegRuntimeTests(unittest.TestCase):
    """Формулы ОП «Яндекс Регистрация» гоняются настоящим Node — шаги примера
    владельца (конверсия 41%, 82% плана, цена успешки 200 ₸) сверяются в .mjs."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_yandex_reg_salary_runtime(self):
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
