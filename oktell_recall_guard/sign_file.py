"""Подпись exe и msi сертификатом организации (Authenticode).

Без подписи Windows пишет «Неизвестный издатель»: в окне SmartScreen у
скачанного файла, в запросе UAC у MSI, в предупреждении «Открыть файл» при
запуске из общей папки. Подпись ставит туда имя организации из сертификата и
даёт IT правило «доверять издателю» для AppLocker/WDAC.

Чего подпись сама НЕ делает: не снимает SmartScreen со скачанного файла.
SmartScreen смотрит репутацию, а её сертификат набирает скачиваниями — у
внутренней программы на десятки машин её может не быть никогда. При раскатке
групповой политикой это неважно: такие файлы SmartScreen не проверяет.

Откуда сертификат (скрипт один на любой вариант):

    set OKTELL_GUARD_SIGN_THUMBPRINT=<отпечаток>   сертификат в личном хранилище
                                                   пользователя сборки (токен и
                                                   HSM тоже видны там)
    set OKTELL_GUARD_SIGN_PFX=C:\\keys\\code.pfx     или файл .pfx
    set OKTELL_GUARD_SIGN_PFX_PASSWORD=...

Запуск:

    python sign_file.py dist\\OktellRecallGuard.exe [ещё файлы]

Код выхода 0 — подписано или подпись не настроена (тогда предупреждение).
Код 1 — подпись настроена, но не получилась: такой файл выкладывать нельзя.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

THUMBPRINT_ENV = "OKTELL_GUARD_SIGN_THUMBPRINT"
PFX_ENV = "OKTELL_GUARD_SIGN_PFX"
PFX_PASSWORD_ENV = "OKTELL_GUARD_SIGN_PFX_PASSWORD"
SIGNTOOL_ENV = "OKTELL_GUARD_SIGNTOOL"
TIMESTAMP_ENV = "OKTELL_GUARD_TIMESTAMP_URL"
DEFAULT_DESCRIPTION = "Oktell Recall Guard"

# Метка времени обязательна: без неё подпись умирает вместе со сроком
# сертификата, и через год все установленные копии стали бы «неподписанными».
# Серверов несколько — любой из них иногда не отвечает.
TIMESTAMP_URLS = (
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "http://timestamp.globalsign.com/tsa/r6advanced1",
)

# Состояния Get-AuthenticodeSignature, при которых подписи по сути нет.
# Остальные «не Valid» (например, корень внутреннего УЦ не доверен на машине
# сборки вне домена) — предупреждение: на машинах домена цепочка сойдётся.
BROKEN_STATUSES = {"NotSigned", "HashMismatch", "NotSupportedFileFormat", "Incompatible"}


def signing_settings(env: Mapping[str, str]) -> Optional[dict]:
    """Какой сертификат брать. None — подпись не настроена."""
    # Отпечаток копируют из оснастки сертификатов, и вместе с ним приезжают
    # пробелы и невидимый знак U+200E — signtool с ними сертификат не находит.
    thumbprint = re.sub(r"[^0-9A-Fa-f]", "", env.get(THUMBPRINT_ENV, "")).upper()
    if thumbprint:
        return {"thumbprint": thumbprint}
    pfx = env.get(PFX_ENV, "").strip()
    if pfx:
        return {"pfx": pfx, "password": env.get(PFX_PASSWORD_ENV, "")}
    return None


def _sdk_version_key(path: Path) -> tuple:
    try:
        return tuple(int(part) for part in path.parent.parent.name.split("."))
    except ValueError:
        return (0,)


def find_signtool(env: Mapping[str, str]) -> Optional[Path]:
    explicit = env.get(SIGNTOOL_ENV, "").strip()
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    found = shutil.which("signtool")
    if found:
        return Path(found)
    kits = Path(env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)") / "Windows Kits" / "10" / "bin"
    candidates = sorted(kits.glob("*/x64/signtool.exe"), key=_sdk_version_key)
    return candidates[-1] if candidates else None


def timestamp_urls(env: Mapping[str, str]) -> list[str]:
    own = env.get(TIMESTAMP_ENV, "").strip()
    return [own] if own else list(TIMESTAMP_URLS)


def signtool_command(signtool: Path, file: Path, settings: dict, timestamp_url: str,
                     description: str = DEFAULT_DESCRIPTION) -> list[str]:
    command = [str(signtool), "sign", "/fd", "SHA256", "/td", "SHA256", "/tr", timestamp_url,
               "/d", description]
    if settings.get("thumbprint"):
        command += ["/sha1", settings["thumbprint"]]
    else:
        command += ["/f", settings["pfx"]]
        if settings.get("password"):
            command += ["/p", settings["password"]]
    command.append(str(file))
    return command


def printable(command: list[str]) -> str:
    """Команда для лога — без пароля от .pfx."""
    shown = list(command)
    for index, item in enumerate(shown[:-1]):
        if item == "/p":
            shown[index + 1] = "***"
    return " ".join(shown)


def _console_text(raw: bytes) -> str:
    """signtool пишет в кодировке консоли (cp866 на русской Windows)."""
    encoding = "utf-8"
    if sys.platform.startswith("win"):
        try:
            import ctypes

            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:  # noqa: BLE001
            pass
    return raw.decode(encoding, errors="replace").strip()


def signature_status(file: Path) -> dict:
    """Что Windows думает о подписи файла: состояние, подписант, метка времени."""
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$s=Get-AuthenticodeSignature -LiteralPath '{str(file).replace(chr(39), chr(39) * 2)}';"
        "[pscustomobject]@{status=[string]$s.Status;message=[string]$s.StatusMessage;"
        "signer=$(if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''});"
        "thumbprint=$(if($s.SignerCertificate){$s.SignerCertificate.Thumbprint}else{''});"
        "timestamped=[bool]$s.TimeStamperCertificate} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=60)
    try:
        return json.loads(result.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return {"status": "Unknown", "message": _console_text(result.stderr), "signer": "",
                "thumbprint": "", "timestamped": False}


def signature_problem(state: dict, expected_thumbprint: str = "") -> str:
    """Почему подписанный файл выкладывать нельзя. Пусто — можно."""
    if state.get("status") in BROKEN_STATUSES or not state.get("signer"):
        return f"подписи нет ({state.get('status')}: {state.get('message')})"
    if expected_thumbprint and str(state.get("thumbprint") or "").upper() != expected_thumbprint:
        return f"подписан не тем сертификатом ({state.get('thumbprint')})"
    if not state.get("timestamped"):
        return "нет метки времени — подпись умрёт вместе со сроком сертификата"
    return ""


def sign(file: Path, settings: dict, signtool: Path, env: Mapping[str, str], description: str) -> bool:
    for url in timestamp_urls(env):
        command = signtool_command(signtool, file, settings, url, description)
        result = subprocess.run(command, capture_output=True, timeout=300)
        if result.returncode == 0:
            return True
        reason = _console_text(result.stderr) or _console_text(result.stdout)
        print(f"[!] Не подписалось с меткой времени {url}: {reason.splitlines()[-1] if reason else result.returncode}")
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Подпись exe и msi сертификатом организации")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION, help="название в окне UAC")
    args = parser.parse_args(argv)

    settings = signing_settings(os.environ)
    if not settings:
        print(f"[!] Подпись не настроена ({THUMBPRINT_ENV} или {PFX_ENV}) — файлы остаются без "
              "подписи, Windows назовёт издателя неизвестным.")
        return 0
    signtool = find_signtool(os.environ)
    if not signtool:
        print(f"[x] Не найден signtool.exe: поставьте Windows SDK или укажите путь в {SIGNTOOL_ENV}.")
        return 1

    for name in args.files:
        file = Path(name)
        if not file.is_file():
            print(f"[x] Нет файла: {file}")
            return 1
        if not sign(file, settings, signtool, os.environ, args.description):
            print(f"[x] {file.name} не подписан — выкладывать его нельзя.")
            return 1
        state = signature_status(file)
        problem = signature_problem(state, settings.get("thumbprint", ""))
        if problem:
            print(f"[x] {file.name}: {problem}")
            return 1
        print(f"[ok] {file.name} подписан: {state['signer']}")
        if state.get("status") != "Valid":
            print(f"[!] Windows на этой машине не доверяет цепочке ({state['status']}: {state['message']}). "
                  "Для внутреннего УЦ это ожидаемо вне домена; на машинах домена проверьте свойства файла.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
