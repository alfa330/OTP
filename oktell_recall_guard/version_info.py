"""Ресурс версии для exe: номер, издатель, название.

Зачем он нужен:

* машинная копия (MSI, групповая политика) при входе пользователя сравнивает
  свою версию с версией его копии и не перезаписывает ту, что уже обновилась
  дальше. Читать версию, не запуская чужой exe, можно только из ресурса;
* издатель и название видны в свойствах файла, в диспетчере задач и в
  инвентаризации IT, а MSI записывает версию файла в свою таблицу.

Запуск (обычно из build_exe.bat):

    python version_info.py            -> version_info.txt рядом
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from publish_release import agent_version  # noqa: E402

COMPANY_ENV = "OKTELL_GUARD_COMPANY"
DEFAULT_COMPANY = "iCORE"
PRODUCT_NAME = "Oktell Recall Guard"
OUTPUT = HERE / "version_info.txt"


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in version.split(".")]
    if not 1 < len(parts) <= 4:
        raise ValueError(f"Номер версии должен быть вида 1.2.3: {version!r}")
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def render(version: str, company: str) -> str:
    """Текст в формате, который понимает `PyInstaller --version-file`."""
    numbers = version_tuple(version)
    strings = {
        "CompanyName": company,
        # Это имя процесса в диспетчере задач.
        "FileDescription": PRODUCT_NAME,
        "FileVersion": version,
        "InternalName": "OktellRecallGuard",
        "LegalCopyright": company,
        "OriginalFilename": "OktellRecallGuard.exe",
        "ProductName": PRODUCT_NAME,
        "ProductVersion": version,
    }
    table = ",\n        ".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('041904B0', [
        {table}
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
"""


def main() -> int:
    company = (os.getenv(COMPANY_ENV) or "").strip() or DEFAULT_COMPANY
    version = agent_version()
    OUTPUT.write_text(render(version, company), encoding="utf-8")
    print(f"[ok] Ресурс версии {version} ({company}): {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
