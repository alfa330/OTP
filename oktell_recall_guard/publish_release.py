"""Выложить собранный exe на сервер — без участия человека.

Собрал новую версию → запустил это → агенты на машинах обновились сами.
Никто ничего не загружает руками: раздел лишь показывает, какая версия текущая.

Запуск (обычно из build_exe.bat, но можно и отдельно):

    set OKTELL_GUARD_PUBLISH_TOKEN=...
    python publish_release.py --server https://icore.example.com

Номер версии берётся из самого agent.py: держать его в двух местах — верный
способ однажды выложить файл под чужим номером и получить либо вечное
обновление по кругу, либо, наоборот, никакого.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_EXE = HERE / "dist" / "OktellRecallGuard.exe"
TOKEN_ENV = "OKTELL_GUARD_PUBLISH_TOKEN"
SERVER_ENV = "OKTELL_GUARD_SERVER"


def agent_version() -> str:
    text = (HERE / "agent.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("В agent.py не найден VERSION")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Публикация версии агента")
    parser.add_argument("--server", default=os.getenv(SERVER_ENV, ""), help="адрес iCORE")
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--notes", default="", help="что изменилось")
    parser.add_argument("--version", default="", help="по умолчанию — из agent.py")
    args = parser.parse_args()

    token = (os.getenv(TOKEN_ENV) or "").strip()
    if not token:
        print(f"[!] {TOKEN_ENV} не задан — публикацию пропускаю (exe собран и лежит рядом).")
        return 0
    server = args.server.rstrip("/")
    if not server:
        print(f"[!] Не задан адрес сервера ({SERVER_ENV}) — публикацию пропускаю.")
        return 0

    exe = Path(args.exe)
    if not exe.exists():
        sys.exit(f"Файл не найден: {exe}")

    version = args.version.strip() or agent_version()
    payload = exe.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    try:
        import requests
    except ImportError:
        sys.exit("Нужен requests: python -m pip install requests")

    print(f"Публикую версию {version} ({len(payload) / 1048576:.1f} МБ) на {server}")
    response = requests.post(
        f"{server}/api/oktell_guard/publish",
        headers={"X-Publish-Token": token},
        files={"file": (exe.name, payload, "application/octet-stream")},
        # Отпечаток шлём ЗАРАНЕЕ: сервер сверит его со своим и не примет файл,
        # который доехал не таким, каким его собрали.
        data={"version": version, "notes": args.notes, "sha256": digest},
        timeout=300,
    )
    if response.status_code != 200:
        sys.exit(f"Сервер отказал: {response.status_code} {response.text[:300]}")

    data = response.json()
    if data.get("sha256") != digest:
        sys.exit("Сервер сохранил файл с другим отпечатком — публикация недостоверна")
    print(f"Готово: версия {data['version']} выложена, агенты обновятся сами.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
