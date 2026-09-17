"""Раскатка «Ограничителя Перезвона» групповой политикой: MSI, подпись, ресурс версии.

Сам пакет собирается и проверяется ICE только на Windows (msilib, msi.dll).
Здесь — то, что ломается тихо и видно лишь на машинах операторов: куда пакет
пишет запуск, как снимает прежнюю версию, что подпись не выкладывается пустой.
"""

import sys
from pathlib import Path

import pytest

GUARD_DIR = Path(__file__).resolve().parents[1] / "oktell_recall_guard"
sys.path.insert(0, str(GUARD_DIR))

import agent  # noqa: E402
import build_msi  # noqa: E402
import publish_release  # noqa: E402
import sign_file  # noqa: E402
import version_info  # noqa: E402


def _tables(**overrides):
    params = dict(version="1.0.16", product_code="{00000000-0000-0000-0000-000000000001}",
                  exe_size=14_000_000, exe_file_version="1.0.16.0", manufacturer="iCORE")
    params.update(overrides)
    return build_msi.package_tables(**params)


# --------------------------------------------------------------------------- #
# MSI
# --------------------------------------------------------------------------- #

def test_upgrade_code_never_changes():
    """По нему новая версия пакета находит прежнюю. Сменить — получить на
    машинах две установки и два запуска при входе."""
    assert build_msi.UPGRADE_CODE == "{CE9C8D1C-E546-4F35-A6CC-9618697BBBA4}"


def test_package_starts_machine_logon_mode_from_hklm():
    rows = _tables()["Registry"]
    assert len(rows) == 1
    _key, root, path, name, value, component = rows[0]
    assert root == build_msi.ROOT_HKLM
    assert path == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert value == f'"[#{build_msi.FILE_KEY}]" --deployed'
    assert build_msi.DEPLOYED_ARG == agent.DEPLOYED_ARG
    # Личный запуск агент снимает, когда стартует из машинного: одно имя на
    # оба значения запутало бы IT и того, кто будет разбирать реестр.
    assert name != agent.RUN_VALUE
    assert component == "MainExecutable", "запуск живёт вместе со своим exe (ICE69)"


def test_per_user_test_build_is_the_only_one_writing_hkcu():
    assert _tables(run_root=build_msi.ROOT_HKCU)["Registry"][0][1] == build_msi.ROOT_HKCU


def test_package_is_per_machine_without_modify_or_repair():
    properties = dict(_tables()["Property"])
    assert properties["ALLUSERS"] == "1"
    assert properties["ARPNOMODIFY"] == "1"
    assert properties["ARPNOREPAIR"] == "1"
    assert properties["UpgradeCode"] == build_msi.UPGRADE_CODE
    assert set(properties["SecureCustomProperties"].split(";")) == {"NEWERPRODUCTFOUND", "OLDPRODUCTFOUND"}


def test_same_version_rebuild_replaces_previous_and_older_never_goes_over_newer():
    """Пересборка той же версии (например, когда появилась подпись) заменяет
    прежнюю, а более старый пакет поверх нового не ставится."""
    tables = _tables()
    old, newer = tables["Upgrade"]
    assert old[1] is None and old[2] == "1.0.16"
    assert old[4] & build_msi.UPGRADE_VERSION_MAX_INCLUSIVE
    assert old[6] == "OLDPRODUCTFOUND"
    assert newer[1] == "1.0.16" and newer[2] is None
    assert newer[4] & build_msi.UPGRADE_ONLY_DETECT
    assert ("NOT NEWERPRODUCTFOUND", "A newer version of [ProductName] is already installed.") in tables["LaunchCondition"]


def test_previous_version_is_removed_before_new_files_are_laid():
    sequence = {action: number for action, _cond, number in _tables()["InstallExecuteSequence"]}
    assert sequence["FindRelatedProducts"] < sequence["LaunchConditions"], "условие про новую версию иначе пустое"
    assert sequence["InstallValidate"] < sequence["RemoveExistingProducts"] < sequence["InstallInitialize"]


def test_package_has_no_ui_tables():
    """Пустые таблицы окон Windows Installer считает недоделанным интерфейсом
    (ICE20, ICE31). Окон у пакета нет намеренно: его ставит групповая политика."""
    assert not {"Dialog", "Control", "TextStyle"} & set(_tables())


def test_file_row_describes_versioned_64bit_exe():
    tables = _tables()
    file_key, component, name, size, version, language, _attrs, sequence = tables["File"][0]
    assert (file_key, component, size, version, sequence) == (
        build_msi.FILE_KEY, "MainExecutable", 14_000_000, "1.0.16.0", 1)
    assert name.endswith("|OktellRecallGuard.exe") and language == "1049"
    assert tables["Component"][0][3] & build_msi.COMPONENT_64BIT
    assert tables["Directory"][1][0] == "ProgramFiles64Folder"


# --------------------------------------------------------------------------- #
# Подпись
# --------------------------------------------------------------------------- #

def test_thumbprint_copied_from_certificate_snap_in_is_cleaned():
    """Из оснастки отпечаток приезжает с пробелами и невидимым U+200E."""
    env = {sign_file.THUMBPRINT_ENV: "\u200e01 23 45 67 89 ab cd ef 01 23 45 67 89 ab cd ef 01 23 45 67"}
    assert sign_file.signing_settings(env) == {"thumbprint": "0123456789ABCDEF0123456789ABCDEF01234567"}


def test_pfx_settings_and_no_settings():
    assert sign_file.signing_settings({sign_file.PFX_ENV: r"C:\keys\code.pfx", sign_file.PFX_PASSWORD_ENV: "p"}) == {
        "pfx": r"C:\keys\code.pfx", "password": "p"}
    assert sign_file.signing_settings({}) is None


def test_signtool_command_timestamps_with_sha256():
    command = sign_file.signtool_command(Path("signtool.exe"), Path("a.exe"), {"thumbprint": "AB"},
                                         "http://timestamp.example")
    assert command[:2] == ["signtool.exe", "sign"]
    for flag, value in (("/fd", "SHA256"), ("/td", "SHA256"), ("/tr", "http://timestamp.example"), ("/sha1", "AB")):
        assert command[command.index(flag) + 1] == value
    assert command[-1] == "a.exe"


def test_pfx_password_never_printed():
    command = sign_file.signtool_command(Path("signtool.exe"), Path("a.exe"),
                                         {"pfx": "c.pfx", "password": "секрет"}, "http://ts")
    assert "секрет" in command
    assert "секрет" not in sign_file.printable(command)


@pytest.mark.parametrize("state, expected", [
    ({"status": "NotSigned", "signer": "", "timestamped": False}, "подписи нет"),
    ({"status": "HashMismatch", "signer": "CN=X", "timestamped": True}, "подписи нет"),
    ({"status": "Valid", "signer": "CN=X", "thumbprint": "FF", "timestamped": True}, "не тем сертификатом"),
    ({"status": "Valid", "signer": "CN=X", "thumbprint": "AB", "timestamped": False}, "нет метки времени"),
])
def test_signature_problems(state, expected):
    assert expected in sign_file.signature_problem(state, "AB")


def test_untrusted_internal_root_on_build_machine_is_not_a_problem():
    """Корень внутреннего УЦ не доверен на машине сборки вне домена — на
    машинах домена цепочка сойдётся, выкладывать можно."""
    state = {"status": "UnknownError", "signer": "CN=X", "thumbprint": "AB", "timestamped": True}
    assert sign_file.signature_problem(state, "AB") == ""


def test_publish_refuses_unsigned_file_when_signing_is_configured(monkeypatch, tmp_path):
    exe = tmp_path / "OktellRecallGuard.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(publish_release.TOKEN_ENV, "token")
    monkeypatch.setenv(publish_release.SERVER_ENV, "https://icore.example.com")
    monkeypatch.setenv(sign_file.THUMBPRINT_ENV, "AB")
    monkeypatch.setattr(sign_file, "signature_status",
                        lambda file: {"status": "NotSigned", "signer": "", "timestamped": False})
    monkeypatch.setattr(sys, "argv", ["publish_release.py", "--exe", str(exe)])

    with pytest.raises(SystemExit) as stop:
        publish_release.main()
    assert "не публикую" in str(stop.value)


def test_build_signs_before_publishing_and_packaging():
    """Иначе на машины уедет неподписанный exe, а в MSI ляжет он же."""
    script = (GUARD_DIR / "build_exe.bat").read_text(encoding="utf-8")
    assert "--version-file version_info.txt" in script
    assert script.index("sign_file.py") < script.index("publish_release.py") < script.index("build_msi.py")


# --------------------------------------------------------------------------- #
# Ресурс версии
# --------------------------------------------------------------------------- #

def test_version_resource_matches_agent_version():
    text = version_info.render(publish_release.agent_version(), "iCORE")
    numbers = version_info.version_tuple(publish_release.agent_version())
    assert f"filevers={numbers}" in text
    assert "StringStruct('CompanyName', 'iCORE')" in text
    assert "StringStruct('OriginalFilename', 'OktellRecallGuard.exe')" in text


def test_version_tuple_is_padded_and_validated():
    assert version_info.version_tuple("1.0.16") == (1, 0, 16, 0)
    with pytest.raises(ValueError):
        version_info.version_tuple("16")
