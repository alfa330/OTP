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


class FaIconMappingTests(unittest.TestCase):
    def test_every_static_fontawesome_token_has_a_mapping(self):
        fa_icon_source = FA_ICON_PATH.read_text(encoding="utf-8-sig")
        mapped_tokens = set(re.findall(r"'(fa-[a-z0-9-]+)'\s*:", fa_icon_source))

        used_tokens = set()
        for pattern in ("*.js", "*.jsx"):
            for source_path in (ROOT / "src").rglob(pattern):
                if source_path == FA_ICON_PATH:
                    continue
                source = source_path.read_text(encoding="utf-8-sig")
                used_tokens.update(re.findall(r"\bfa-[a-z][a-z0-9-]*\b", source))

        missing_tokens = sorted(used_tokens - mapped_tokens - META_TOKENS)
        self.assertEqual([], missing_tokens, f"Missing FaIcon mappings: {missing_tokens}")


if __name__ == "__main__":
    unittest.main()
