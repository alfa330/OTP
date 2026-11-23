from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FA_ICON_PATH = ROOT / "src" / "components" / "common" / "FaIcon.jsx"
META_TOKENS = {
    "fa-brands",
    "fa-lg",
    "fa-regular",
    "fa-solid",
    "fa-spin",
}

# Учебная CRM тренажёра «Рабочее место оператора» рисуется во фрейме
# настоящими стилями и шрифтом рабочей CRM (crmFrame.jsx): Font Awesome 4.7
# лежит в public/trainers/crm/fonts, и `fa fa-…` там — родные классы шрифта,
# а не имена для FaIcon. Портал эти файлы не рисует, сверять их со
# справочником FaIcon незачем: значок, которого нет в FaIcon, во фрейме
# всё равно виден.
TRAINER_DIR = ROOT / "src" / "components" / "wiki" / "trainers"
TRAINER_CRM_FILE = re.compile(r"^(crm[A-Za-z]*\.jsx?|screensCrm\.jsx)$")
TRAINER_CRM_FONT = ROOT / "public" / "trainers" / "crm" / "fonts" / "fontawesome-webfont.woff2"


def _renders_in_crm_frame(path):
    return path.parent == TRAINER_DIR and bool(TRAINER_CRM_FILE.match(path.name))


class FaIconMappingTests(unittest.TestCase):
    def test_every_static_fontawesome_token_has_a_mapping(self):
        fa_icon_source = FA_ICON_PATH.read_text(encoding="utf-8-sig")
        mapped_tokens = set(re.findall(r"'(fa-[a-z0-9-]+)'\s*:", fa_icon_source))

        used_tokens = set()
        for pattern in ("*.js", "*.jsx"):
            for source_path in (ROOT / "src").rglob(pattern):
                if source_path == FA_ICON_PATH or _renders_in_crm_frame(source_path):
                    continue
                source = source_path.read_text(encoding="utf-8-sig")
                used_tokens.update(re.findall(r"\bfa-[a-z][a-z0-9-]*\b", source))

        missing_tokens = sorted(used_tokens - mapped_tokens - META_TOKENS)
        self.assertEqual([], missing_tokens, f"Missing FaIcon mappings: {missing_tokens}")

    def test_crm_frame_files_really_bring_their_own_font(self):
        # Исключение выше держится на том, что шрифт учебной CRM на месте:
        # без него значки во фрейме пропали бы молча, и страж бы промолчал.
        self.assertTrue(TRAINER_CRM_FONT.exists(), TRAINER_CRM_FONT)
        excluded = [path.name for path in TRAINER_DIR.glob("*.js*") if _renders_in_crm_frame(path)]
        self.assertIn("crmDrivers.jsx", excluded)
        self.assertNotIn("TrainerPlayer.jsx", excluded)


if __name__ == "__main__":
    unittest.main()
