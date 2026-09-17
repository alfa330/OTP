"""MSI для раскатки групповой политикой.

IT назначает пакет компьютерам: Конфигурация компьютера → Политики →
Конфигурация программ → Установка программ. Пакет ставится при загрузке от
имени системы, без окон и без SmartScreen — у файлов, разложенных групповой
политикой, нет пометки «скачано из интернета», а SmartScreen проверяет только
помеченные.

Что делает пакет, и больше ничего (своих действий внутри нет):

* кладёт OktellRecallGuard.exe в C:\\Program Files\\Oktell Recall Guard;
* пишет в HKLM\\...\\Run запуск с --deployed: при входе каждого пользователя
  программа кладёт ему рабочую копию и запускает её (agent.py,
  run_machine_logon) — так продолжает работать автообновление;
* новая версия пакета сама снимает прежнюю; удаление пакета убирает оба пункта,
  а копии пользователей снимают себя при следующем запуске.

Запуск (после сборки и подписи exe — обычно из build_exe.bat):

    python build_msi.py                 -> dist\\OktellRecallGuard-<версия>.msi

Нужен Python не новее 3.12: модуль msilib из 3.13 убран.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from publish_release import agent_version  # noqa: E402

PRODUCT_NAME = "Oktell Recall Guard"
COMPANY_ENV = "OKTELL_GUARD_COMPANY"
DEFAULT_COMPANY = "iCORE"
DEFAULT_EXE = HERE / "dist" / "OktellRecallGuard.exe"

# Коды ниже НЕ МЕНЯТЬ. По UpgradeCode новая версия пакета находит и снимает
# прежнюю; сменить его — получить на машинах две установки сразу. Код
# компонента по правилам Windows Installer постоянен, пока на месте его файл.
UPGRADE_CODE = "{CE9C8D1C-E546-4F35-A6CC-9618697BBBA4}"
EXE_COMPONENT_ID = "{A236D401-52E0-4883-AF11-F86C11059E18}"

EXE_NAME = "OktellRecallGuard.exe"
FILE_KEY = "OktellRecallGuard.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Не совпадает с личным значением в HKCU (OktellRecallGuard): это разные точки
# входа, и программа снимает личное, когда запускается из машинного.
RUN_VALUE = "OktellRecallGuardMachine"
DEPLOYED_ARG = "--deployed"   # сверяется тестом с agent.DEPLOYED_ARG

COMPONENT_64BIT = 256
UPGRADE_ONLY_DETECT = 2
UPGRADE_VERSION_MAX_INCLUSIVE = 512
ROOT_HKCU = 1
ROOT_HKLM = 2


def package_tables(*, version: str, product_code: str, exe_size: int, exe_file_version: str,
                   manufacturer: str, run_root: int = ROOT_HKLM) -> dict[str, list[tuple]]:
    """Содержимое таблиц пакета — без Windows, чтобы проверять тестами везде.

    run_root — куда писать запуск. Боевой пакет: HKLM. HKCU — только проверочная
    сборка, которую ставят без прав администратора (--per-user-test).
    """
    execute = [
        ("FindRelatedProducts", None, 25),
        ("LaunchConditions", None, 100),
        ("ValidateProductID", None, 700),
        ("CostInitialize", None, 800),
        ("FileCost", None, 900),
        ("CostFinalize", None, 1000),
        ("MigrateFeatureStates", None, 1200),
        ("InstallValidate", None, 1400),
        # Сначала снять прежнюю версию, потом ставить: так пакету не важно,
        # поменялась ли версия файла, а в Program Files не остаётся смеси.
        ("RemoveExistingProducts", None, 1401),
        ("InstallInitialize", None, 1500),
        ("ProcessComponents", None, 1600),
        ("UnpublishFeatures", None, 1800),
        ("RemoveRegistryValues", None, 2600),
        ("RemoveFiles", None, 3500),
        ("InstallFiles", None, 4000),
        ("WriteRegistryValues", None, 5000),
        ("RegisterUser", None, 6000),
        ("RegisterProduct", None, 6100),
        ("PublishFeatures", None, 6300),
        ("PublishProduct", None, 6400),
        ("InstallFinalize", None, 6600),
    ]
    return {
        "Property": [
            ("ProductName", PRODUCT_NAME),
            ("ProductCode", product_code),
            ("ProductVersion", version),
            ("Manufacturer", manufacturer),
            ("ProductLanguage", "1033"),
            ("UpgradeCode", UPGRADE_CODE),
            ("ALLUSERS", "1"),
            # Изменять и чинить тут нечего: рабочая копия у пользователя
            # обновляется сама, а машинную ставит и снимает IT.
            ("ARPNOMODIFY", "1"),
            ("ARPNOREPAIR", "1"),
            ("SecureCustomProperties", "NEWERPRODUCTFOUND;OLDPRODUCTFOUND"),
        ],
        "Directory": [
            ("TARGETDIR", None, "SourceDir"),
            ("ProgramFiles64Folder", "TARGETDIR", "."),
            ("INSTALLDIR", "ProgramFiles64Folder", "OKTREC~1|Oktell Recall Guard"),
        ],
        # Запуск ссылается на сам exe, поэтому живёт с ним в одном компоненте:
        # не бывает ни файла без запуска, ни запуска без файла (ICE69).
        "Component": [
            ("MainExecutable", EXE_COMPONENT_ID, "INSTALLDIR", COMPONENT_64BIT, None, FILE_KEY),
        ],
        "File": [
            (FILE_KEY, "MainExecutable", f"OKTREC~1.EXE|{EXE_NAME}", exe_size,
             exe_file_version or None, "1049" if exe_file_version else None, 0, 1),
        ],
        "Registry": [
            ("MachineRunValue", run_root, RUN_KEY, RUN_VALUE, f'"[#{FILE_KEY}]" {DEPLOYED_ARG}', "MainExecutable"),
        ],
        "Feature": [
            ("Complete", None, PRODUCT_NAME, "Oktell recall status limiter", 1, 1, "INSTALLDIR", 0),
        ],
        "FeatureComponents": [
            ("Complete", "MainExecutable"),
        ],
        "Upgrade": [
            # Всё до этой версии включительно снимается: пересборка той же
            # версии (например, после появления подписи) заменяет прежнюю, а не
            # встаёт второй строкой в «Программах и компонентах».
            (UPGRADE_CODE, None, version, None, UPGRADE_VERSION_MAX_INCLUSIVE, None, "OLDPRODUCTFOUND"),
            (UPGRADE_CODE, version, None, None, UPGRADE_ONLY_DETECT, None, "NEWERPRODUCTFOUND"),
        ],
        "LaunchCondition": [
            ("NOT NEWERPRODUCTFOUND", "A newer version of [ProductName] is already installed."),
            ("VersionNT64", "[ProductName] requires 64-bit Windows."),
        ],
        "InstallExecuteSequence": execute,
        "InstallUISequence": [
            ("FindRelatedProducts", None, 25),
            ("LaunchConditions", None, 100),
            ("ValidateProductID", None, 700),
            ("CostInitialize", None, 800),
            ("FileCost", None, 900),
            ("CostFinalize", None, 1000),
            ("MigrateFeatureStates", None, 1200),
            ("ExecuteAction", None, 1300),
        ],
        "AdminUISequence": [
            ("CostInitialize", None, 800),
            ("FileCost", None, 900),
            ("CostFinalize", None, 1000),
            ("ExecuteAction", None, 1300),
        ],
        "AdminExecuteSequence": [
            ("CostInitialize", None, 800),
            ("FileCost", None, 900),
            ("CostFinalize", None, 1000),
            ("InstallValidate", None, 1400),
            ("InstallInitialize", None, 1500),
            ("InstallAdminPackage", None, 3900),
            ("InstallFiles", None, 4000),
            ("InstallFinalize", None, 6600),
        ],
        "AdvtExecuteSequence": [
            ("CostInitialize", None, 800),
            ("CostFinalize", None, 1000),
            ("InstallValidate", None, 1400),
            ("InstallInitialize", None, 1500),
            ("PublishFeatures", None, 6300),
            ("PublishProduct", None, 6400),
            ("InstallFinalize", None, 6600),
        ],
    }


def build(exe: Path, output: Path, *, manufacturer: str, per_user_test: bool = False) -> Path:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import msilib
            from msilib import schema
    except ImportError:
        raise SystemExit("В этом Python нет msilib (убран в 3.13) — соберите MSI на Python 3.11 или 3.12")
    from agent import file_version

    version = agent_version()
    exe_file_version = file_version(exe)
    # Пакет с exe от другой версии сломал бы сравнение версий у пользователей:
    # машинная копия считала бы себя не той, кем является.
    if not exe_file_version.startswith(f"{version}."):
        raise SystemExit(f"Версия exe ({exe_file_version or 'нет ресурса версии'}) не совпадает с agent.py "
                         f"({version}) — пересоберите exe через build_exe.bat")

    tables = package_tables(
        version=version,
        product_code="{%s}" % str(uuid.uuid4()).upper(),
        exe_size=exe.stat().st_size,
        exe_file_version=exe_file_version,
        manufacturer=manufacturer,
        run_root=ROOT_HKCU if per_user_test else ROOT_HKLM,
    )
    # Только те таблицы, что пакет использует. Пустые таблицы окон (Dialog,
    # Control) Windows Installer считает недоделанным интерфейсом и требует
    # стандартные диалоги (ICE20, ICE31), хотя окон у пакета нет намеренно.
    used = set(tables) | {"Media"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    db = msilib.OpenDatabase(str(output), msilib.MSIDBOPEN_CREATE)
    for table in schema.tables:
        if table.name in used:
            table.create(db)
    schema._Validation.create(db)
    msilib.add_data(db, "_Validation", [row for row in schema._Validation_records if row[0] in used])

    summary = db.GetSummaryInformation(20)
    summary.SetProperty(msilib.PID_CODEPAGE, 1252)
    summary.SetProperty(msilib.PID_TITLE, "Installation Database")
    summary.SetProperty(msilib.PID_SUBJECT, f"{PRODUCT_NAME} {version}")
    summary.SetProperty(msilib.PID_AUTHOR, manufacturer)
    summary.SetProperty(msilib.PID_KEYWORDS, "Installer")
    summary.SetProperty(msilib.PID_TEMPLATE, "x64;1033")
    summary.SetProperty(msilib.PID_REVNUMBER, "{%s}" % str(uuid.uuid4()).upper())
    summary.SetProperty(msilib.PID_PAGECOUNT, 200)
    # 2 — сжатые файлы внутри пакета, длинные имена. +8 — ставить без прав
    # администратора: только проверочная сборка, в боевом пакете этого нет.
    summary.SetProperty(msilib.PID_WORDCOUNT, 10 if per_user_test else 2)
    summary.SetProperty(msilib.PID_APPNAME, "build_msi.py")
    summary.SetProperty(msilib.PID_SECURITY, 2)
    summary.Persist()

    for name, rows in tables.items():
        msilib.add_data(db, name, rows)

    cab = msilib.CAB("product.cab")
    cab.append(str(exe), EXE_NAME, FILE_KEY)
    cab.commit(db)
    db.Commit()
    db.Close()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MSI для раскатки групповой политикой")
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--output", default="", help="по умолчанию dist\\OktellRecallGuard-<версия>.msi")
    parser.add_argument("--manufacturer", default=(os.getenv(COMPANY_ENV) or "").strip() or DEFAULT_COMPANY)
    parser.add_argument("--per-user-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    import sign_file

    exe = Path(args.exe)
    if not exe.is_file():
        print(f"[x] Нет exe: {exe}")
        return 1
    settings = sign_file.signing_settings(os.environ)
    if settings:
        # Внутрь пакета должен попасть уже подписанный exe: подпись MSI не
        # распространяется на файлы в нём, и у пользователей оказалась бы
        # неподписанная программа.
        problem = sign_file.signature_problem(sign_file.signature_status(exe), settings.get("thumbprint", ""))
        if problem:
            print(f"[x] {exe.name}: {problem}. Сначала подпишите exe.")
            return 1

    output = Path(args.output) if args.output else exe.parent / f"OktellRecallGuard-{agent_version()}.msi"
    build(exe, output, manufacturer=args.manufacturer, per_user_test=args.per_user_test)
    print(f"[ok] Пакет: {output} ({output.stat().st_size / 1048576:.1f} МБ)")
    return sign_file.main([str(output), "--description", PRODUCT_NAME])


if __name__ == "__main__":
    raise SystemExit(main())
