"""
Oktell Recall Guard — агент на машине оператора.

Задача: по команде сервера выкинуть оператора из веб-клиента Oktell
(https://<веб-клиент Oktell>/), когда он просидел в статусе «Перезвон» дольше нормы.

Разделение ответственности:
  * решение принимает СЕРВЕР (правило D3: State=2 + ICode=2 + N секунд + ноль
    исходящих). Агент ничего не измеряет и не решает;
  * агент только (1) говорит серверу, кто за машиной и жив ли он,
    (2) принимает команду, (3) исполняет разлогин в браузере, (4) отчитывается.

Почему разлогин исполняет агент, а не сервер: серверная команда Oktell
`wp_setuserstate` умеет только снять с линии (`oncallcenter=0`) — это НЕ выход
из веб-клиента, оператор остаётся залогинен и возвращается за секунды.
Настоящий разлогин = стереть сессию `___oktellsessionid` и перезагрузить
страницу, то есть действие внутри браузера.

Чтобы иметь доступ внутрь браузера без расширения (расширению нужен GPO
force-install), агент сам запускает Chrome: свой профиль + порт отладки CDP
на loopback. Такой Chrome управляем по DevTools Protocol, а операторский
ярлык «Oktell» указывает на этот же exe.

Режимы (один файл, как в MicroSIP DND Shield):
    OktellRecallGuard.exe             -> watchdog (по умолчанию)
    OktellRecallGuard.exe --agent     -> рабочий цикл агента
    OktellRecallGuard.exe --open      -> открыть управляемое окно Oktell и выйти
    OktellRecallGuard.exe --logout-now-> ручной разлогин (проверка на месте)
    OktellRecallGuard.exe --status    -> состояние в JSON (диагностика)

Не читает клавиатуру, не делает скриншотов, не собирает содержимое страниц:
наружу уходят только имя машины, пользователь Windows, факт наличия сессии
Oktell и результат исполнения команды.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

APP_NAME = "Oktell Recall Guard"
APP_DIR_NAME = "OktellRecallGuard"
VERSION = "1.0.1"

IS_WINDOWS = sys.platform.startswith("win")

# Local\, а не Global\: Global-объекты требуют SeCreateGlobalPrivilege, которой
# у обычного (не-админ) пользователя нет — при per-user автозапуске мьютекс молча
# не создавался бы и взаимный сторож плодил бы копии. Проверено на DND Shield.
AGENT_MUTEX_NAME = "Local\\OktellRecallGuard_AgentInstance"
WATCHDOG_MUTEX_NAME = "Local\\OktellRecallGuard_WatchdogInstance"
ERROR_ALREADY_EXISTS = 183

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# Ключ сессии веб-клиента Oktell: подтверждён по бандлу scripts.js
# ($.cookie("___oktellsessionid", null) + localStorage). Вынесен в конфиг на
# случай, если вендор его переименует в новой версии.
DEFAULT_SESSION_KEYS = ["___oktellsessionid"]

DEFAULT_CONFIG: dict[str, Any] = {
    # --- сервер ---
    "server_url": "https://icore.example.com",
    "heartbeat_path": "/api/oktell_guard/heartbeat",
    "ack_path": "/api/oktell_guard/ack",
    "agent_token": "",
    "verify_tls": True,
    "request_timeout_s": 10,
    # Раз в минуту: решение принимает правило в окне, к серверу агент ходит
    # только отметиться и отдать факты нарушений. Раньше стояло 5 секунд —
    # наследие схемы, где сервер раздавал команды, и это давало тысячи
    # бессмысленных запросов за смену.
    "poll_interval_s": 60,
    "offline_max_backoff_s": 60,

    # --- кто за машиной ---
    # operator_login заполняется при установке, если известен логин Oktell.
    # Пустой — не беда: агент пробует вытащить логин со страницы, а сервер
    # в любом случае знает машину по hostname + пользователю Windows.
    "operator_login": "",

    # --- автообновление ---
    # Сотрудник скачивает файл один раз, поэтому новые версии агент ставит сам.
    "auto_update": True,
    "update_check_hours": 6,

    # --- Oktell ---
    # Пусто — значит «не настроено»: адрес приезжает с сервера (или из
    # config.json на машине разработчика). Плейсхолдер тут ставить нельзя,
    # иначе агент считает себя настроенным и открывает несуществующий сайт.
    "oktell_url": "",
    "session_keys": list(DEFAULT_SESSION_KEYS),

    # --- управляемый браузер ---
    "browser": {
        "chrome_path": "",          # пусто = ищем сами
        "profile_dir": "",          # пусто = %LOCALAPPDATA%\OktellRecallGuard\chrome-profile
        "cdp_port": 0,              # 0 = Chrome выберет сам, порт читаем из DevToolsActivePort
        "app_mode": True,           # окно без адресной строки (--app=)
        "launch_on_start": True,    # открыть Oktell при старте агента
        "focus_on_command": True,   # поднимать окно поверх при разлогине
        "keep_open": False,         # переоткрывать окно, если оператор его закрыл
        "extra_args": [],
    },

    # --- обход через «неуправляемый» браузер ---
    # report  — только сообщить серверу (он применит серверный рычаг);
    # ignore  — не смотреть вообще.
    "unmanaged": {
        "detect": True,
        "window_title_patterns": ["oktell"],
        "action": "report",
    },

    # --- правило прямо в окне: без опроса базы и без нагрузки на сервер ---
    "in_window_rule": {
        "enabled": True,
        "threshold_s": 180,
        "warn_before_s": 30,
        "recall_lunch_reason_id": 2,   # подпричина перерыва «Перезвон»
        # Накопленное время обнуляет только состоявшийся звонок.
        "call_state_strings": ["talk", "dial", "call", "ring"],
        "call_state_ids": [],
        "message": "«Перезвон» дольше 3 минут — сессия будет закрыта",
    },

    # --- поведение ---
    "warn_banner": True,        # показывать предупреждение по команде warn
    "dry_run": False,           # писать в лог «разлогинил бы», но не трогать браузер
    "ensure_watchdog_alive": True,
    "guardian_interval_s": 5,
    "watchdog_check_interval_s": 2,
    "watchdog_spawn_grace_s": 3,
    "log_level": "INFO",
    "log_max_bytes": 1048576,
    "log_backup_count": 3,
    "single_instance": True,
}

COMMAND_TTL_S = 6 * 3600
COMMAND_LEDGER_LIMIT = 500


# --------------------------------------------------------------------------- #
# Конфиг, пути, логи
# --------------------------------------------------------------------------- #

def deep_update(target: dict, source: dict) -> dict:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value
    return target


def program_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    path = Path(base) / APP_DIR_NAME if base else Path.home() / f".{APP_DIR_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    exe_dir = program_path().parent
    candidate = exe_dir / "config.json"
    if candidate.exists():
        return candidate
    installed = app_dir() / "config.json"
    if installed.exists():
        return installed
    return exe_dir / "config.example.json"


def header_safe(value: str) -> bool:
    """Влезет ли значение в HTTP-заголовок.

    Заголовки — latin-1. Токен с кириллицей роняет КАЖДЫЙ запрос к серверу с
    невнятным «'latin-1' codec can't encode», и снаружи это выглядит как
    «агент не работает», а не как «токен неправильный».
    """
    try:
        str(value).encode('latin-1')
        return True
    except (UnicodeEncodeError, AttributeError):
        return False


def build_server_url() -> str:
    """Адрес сервера, вшитый при сборке.

    Без него скачанный exe не знает, куда обращаться: конфига у сотрудника нет,
    а дефолт — плейсхолдер. Первая установка на живой машине именно на это и
    напоролась: агент молча ждал настроек, которые не мог получить.
    """
    try:
        from _build_token import SERVER_URL  # type: ignore
        return str(SERVER_URL or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def build_token() -> str:
    """Токен агента, вшитый при сборке exe.

    У сотрудника нет ни конфига, ни возможности что-то вводить: он скачивает
    один файл. Поэтому пароль к серверным ручкам кладётся в exe на сборке
    (build_exe.bat берёт его из той же переменной окружения, что и сервер).

    Секрет ли это. Не вполне: файл скачивает любой сотрудник, и вытащить строку
    из exe несложно. Он отсекает не своего же сотрудника, а посторонние запросы
    из интернета — ручки пишут в нашу базу, и оставлять их открытыми нельзя.
    Смена токена дешёвая: новая сборка разъезжается автообновлением сама.
    """
    try:
        from _build_token import AGENT_TOKEN  # type: ignore
        return str(AGENT_TOKEN or "").strip()
    except Exception:  # noqa: BLE001 — сборка без токена это законный случай
        return ""


def token_from_filename(name: str) -> str:
    """Достать личный токен из имени файла `OktellRecallGuard.<токен>.exe`.

    Токен именно в имени, а не внутри exe: файл в хранилище один на всех, и
    пересобирать его под каждого сотрудника было бы абсурдом. Имя же задаётся
    на скачивании — сотрудник ничего не делает, а присланное потом подписано им.
    """
    stem = str(name or '')
    if stem.lower().endswith('.exe'):
        stem = stem[:-4]
    parts = stem.split('.')
    if len(parts) < 2:
        return ''
    candidate = parts[-1].strip()
    # Токен — латиница и цифры: всё остальное это часть имени вроде «(1)».
    if len(candidate) < 12 or not candidate.isalnum() or not candidate.isascii():
        return ''
    return candidate


def personal_token_path() -> Path:
    return app_dir() / "token.json"


def load_personal_token() -> str:
    try:
        data = json.loads(personal_token_path().read_text(encoding="utf-8"))
        return str(data.get("token") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def save_personal_token(token: str) -> None:
    """Токен переживает установку: она переименовывает файл, и имя с токеном
    исчезает — если не сохранить, агент станет анонимным после первого же
    запуска."""
    try:
        personal_token_path().write_text(
            json.dumps({"token": token}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        logging.debug("Личный токен не сохранён", exc_info=True)


def resolve_agent_token() -> str:
    """Личный токен важнее вшитого: он называет человека, вшитый — нет."""
    stored = load_personal_token()
    if stored:
        return stored
    from_name = token_from_filename(program_path().name)
    if from_name:
        save_personal_token(from_name)
        return from_name
    return build_token()


def load_config(path: Optional[Path] = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # глубокая копия без побочек
    cfg_path = path or config_path()
    cfg["_config_path"] = str(cfg_path) if cfg_path else ""
    if cfg_path and Path(cfg_path).exists():
        try:
            with Path(cfg_path).open("r", encoding="utf-8-sig") as f:
                deep_update(cfg, json.load(f))
        except Exception as exc:  # noqa: BLE001 — конфиг не должен ронять агента молча
            # Дефолты подставлять НЕЛЬЗЯ: в них чужой server_url и чужой
            # oktell_url, и агент пойдёт открывать не тот Oktell и стучаться
            # не на тот сервер. Отмечаем ошибку — работать с таким конфигом
            # агент откажется (см. wait_for_valid_config).
            cfg["_config_error"] = f"{type(exc).__name__}: {exc}"
            print(f"Config read error ({cfg_path}): {exc}")
    return normalize_config(cfg)


def wait_for_valid_config(cfg: dict, retry_s: float = 60.0) -> dict:
    """Пока конфиг битый — ничего не делаем и громко пишем в лог.

    Молчаливый откат на дефолты уже приводил к тому, что агент открывал окно
    на несуществующий адрес и ломился на чужой сервер. Лучше простаивать
    заметно, чем работать неправильно; исправленный файл подхватываем сами.
    """
    while cfg.get("_config_error"):
        logging.error(
            "Конфиг %s не читается (%s). Агент НЕ работает: с дефолтами он открыл бы "
            "не тот Oktell и стучался бы на чужой сервер. Исправь файл — подхвачу сам.",
            cfg.get("_config_path") or "(не задан)",
            cfg["_config_error"],
        )
        time.sleep(retry_s)
        raw_path = cfg.get("_config_path")
        cfg = load_config(Path(raw_path) if raw_path else None)
    return cfg


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_config(cfg: dict) -> dict:
    """Приводим значения к безопасным границам: кривой конфиг не должен
    превращаться ни в DDoS сервера, ни в мёртвого агента. Ноль и отрицательные
    зажимаются в нижнюю границу, а не подменяются дефолтом молча."""
    cfg["poll_interval_s"] = _clamp_int(cfg.get("poll_interval_s"), 2, 300, 5)
    cfg["request_timeout_s"] = _clamp_int(cfg.get("request_timeout_s"), 2, 60, 10)
    cfg["offline_max_backoff_s"] = max(
        cfg["poll_interval_s"], _clamp_int(cfg.get("offline_max_backoff_s"), 2, 600, 60)
    )
    keys = cfg.get("session_keys") or []
    cfg["session_keys"] = [str(k) for k in keys if str(k).strip()] or list(DEFAULT_SESSION_KEYS)
    cfg["server_url"] = str(cfg.get("server_url", "")).rstrip("/")
    # Плейсхолдер из дефолтов адресом не считается: с ним агент честно стучится
    # в несуществующий домен и ждёт настроек вечно.
    if not cfg["server_url"] or cfg["server_url"] == DEFAULT_CONFIG["server_url"].rstrip("/"):
        cfg["server_url"] = build_server_url() or cfg["server_url"]
    # Конфиг (у разработчика) перекрывает вшитый токен; у сотрудника конфига
    # нет, и работает именно вшитый.
    if not str(cfg.get("agent_token") or "").strip():
        cfg["agent_token"] = resolve_agent_token()
    if cfg.get("agent_token") and not header_safe(cfg["agent_token"]):
        # Дальше он всё равно не уедет: лучше сказать прямо и работать без
        # токена, чем ронять каждый запрос кодировкой.
        cfg["_token_error"] = "токен агента содержит символы вне latin-1 — заголовок с ним не отправить"
        cfg["agent_token"] = ""
    browser = cfg.setdefault("browser", {})
    browser["cdp_port"] = _clamp_int(browser.get("cdp_port"), 0, 65535, 0)
    return cfg


def cached_config_path() -> Path:
    return app_dir() / "server_config.json"


def apply_server_config(cfg: dict, remote: dict) -> dict:
    """Накладываем присланные сервером настройки на локальные.

    Сервер задаёт только то, что должно быть одинаковым у всех: адрес Oktell,
    правило, аргументы браузера, период опроса. Пути, логи и токен остаются
    локальными — иначе один кривой ответ сервера сломал бы всем машинам запуск.
    """
    allowed = ("oktell_url", "session_keys", "in_window_rule", "poll_interval_s", "dry_run", "unmanaged")
    for key in allowed:
        if key in remote:
            if isinstance(remote[key], dict) and isinstance(cfg.get(key), dict):
                deep_update(cfg[key], remote[key])
            else:
                cfg[key] = remote[key]
    browser = remote.get("browser") or {}
    if isinstance(browser, dict):
        for key in ("extra_args", "keep_open", "app_mode", "launch_on_start"):
            if key in browser:
                cfg.setdefault("browser", {})[key] = browser[key]
    return normalize_config(cfg)


def is_configured(cfg: dict) -> bool:
    """Есть ли у агента настоящий адрес Oktell.

    В дефолтах адреса нет (пустая строка), поэтому свежескачанный exe без
    ответа сервера считается ненастроенным. Без этого он открывал окно на
    несуществующий адрес, и сотрудник видел сломанный сайт: лучше заметно
    простаивать, чем уверенно делать не то.
    """
    url = str(cfg.get("oktell_url") or "").strip()
    return bool(url) and bool(origin_of(url))


def wait_for_configuration(cfg: dict, retry_s: float = 60.0) -> dict:
    """Ждём настройки с сервера, ничего не открывая и никого не трогая."""
    while not is_configured(cfg):
        logging.error(
            "Нет настроек: адрес Oktell не задан (%s). Агент ждёт ответа сервера %s "
            "и пока ничего не открывает.",
            cfg.get("oktell_url"),
            cfg.get("server_url"),
        )
        time.sleep(retry_s)
        cfg = fetch_server_config(cfg)
    return cfg


def fetch_server_config(cfg: dict) -> dict:
    """Забрать настройки с сервера; при недоступности — из кэша.

    Смысл: сотрудник скачивает один exe и ничего не настраивает. Порог,
    адрес клиента и пин сертификата приезжают с сервера, а кэш нужен, чтобы
    агент пережил недоступность сервера и не остался без правила.
    """
    url = str(cfg.get("config_url") or "")
    if not url:
        base = str(cfg.get("server_url") or "").rstrip("/")
        url = f"{base}/api/oktell_guard/config" if base else ""
    if url:
        try:
            import requests

            response = requests.get(
                url,
                timeout=float(cfg.get("request_timeout_s", 10)),
                verify=bool(cfg.get("verify_tls", True)),
                headers={"X-Agent-Token": str(cfg.get("agent_token") or ""),
                         "User-Agent": f"OktellRecallGuard/{VERSION}"},
            )
            response.raise_for_status()
            remote = response.json()
            if isinstance(remote, dict):
                try:
                    cached_config_path().write_text(json.dumps(remote, ensure_ascii=False), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    logging.debug("Кэш настроек не записан", exc_info=True)
                logging.info("Настройки получены с сервера")
                return apply_server_config(cfg, remote)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Настройки с сервера не получены (%s) — беру кэш", exc)

    try:
        if cached_config_path().exists():
            remote = json.loads(cached_config_path().read_text(encoding="utf-8"))
            if isinstance(remote, dict):
                logging.info("Применил кэш настроек от %s", cached_config_path())
                return apply_server_config(cfg, remote)
    except Exception:  # noqa: BLE001
        logging.debug("Кэш настроек не прочитан", exc_info=True)
    return cfg


def setup_logging(cfg: dict, log_name: str) -> Path:
    log_path = app_dir() / log_name
    numeric = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=int(cfg.get("log_max_bytes", 1048576)),
            backupCount=int(cfg.get("log_backup_count", 3)),
            encoding="utf-8",
        )
    ]
    # В сборке --noconsole sys.stdout может быть None: тогда только файл.
    if getattr(sys, "stdout", None):
        handlers.append(logging.StreamHandler(sys.stdout))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    logging.basicConfig(level=numeric, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)
    logging.info("%s v%s | %s", APP_NAME, VERSION, log_name)
    logging.info("Лог: %s", log_path)
    return log_path


# --------------------------------------------------------------------------- #
# Windows: мьютексы и запуск копий
# --------------------------------------------------------------------------- #

_held_mutexes: dict[str, Any] = {}


def _kernel32():
    if not IS_WINDOWS:
        return None
    return ctypes.windll.kernel32


def _create_mutex(name: str):
    k32 = _kernel32()
    if not k32:
        return None, False
    try:
        handle = k32.CreateMutexW(None, False, name)
        already = k32.GetLastError() == ERROR_ALREADY_EXISTS
        return handle, already
    except Exception:  # noqa: BLE001
        return None, False


def _close_handle(handle) -> None:
    k32 = _kernel32()
    if not k32 or not handle:
        return
    try:
        k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass


def is_running_by_mutex(name: str) -> bool:
    handle, already = _create_mutex(name)
    _close_handle(handle)
    return bool(already)


def acquire_mutex(name: str) -> bool:
    """False = копия уже запущена."""
    handle, already = _create_mutex(name)
    if already:
        _close_handle(handle)
        return False
    _held_mutexes[name] = handle
    return True


def release_mutex(name: str) -> None:
    _close_handle(_held_mutexes.pop(name, None))


def _self_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(program_path()), *args]
    return [sys.executable, str(program_path()), *args]


def spawn_self(*args: str) -> None:
    cmd = _self_command(*args)
    try:
        flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
        subprocess.Popen(cmd, cwd=str(program_path().parent), creationflags=flags, close_fds=True)
        logging.info("Запущен процесс: %s", " ".join(cmd))
    except Exception:  # noqa: BLE001
        logging.exception("Не удалось запустить %s", " ".join(cmd))


# --------------------------------------------------------------------------- #
# Установка себя (без прав администратора и без .bat)
# --------------------------------------------------------------------------- #

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "OktellRecallGuard"
TASK_NAME = "Oktell Recall Guard"
INSTALLED_NAME = "OktellRecallGuard.exe"


def installed_path() -> Path:
    return app_dir() / INSTALLED_NAME


def is_installed_copy() -> bool:
    try:
        return program_path().resolve() == installed_path().resolve()
    except Exception:  # noqa: BLE001
        return False


def _register_autostart(target: Path) -> bool:
    """Автозапуск через HKCU\...\Run.

    Именно Run, а не задача с триггером ONLOGON: создание такой задачи требует
    прав администратора, а сотрудник их не имеет — это и есть причина, по
    которой раньше всё делал .bat с той же логикой.
    """
    if not IS_WINDOWS:
        return False
    try:
        import winreg  # type: ignore

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{target}"')
        return True
    except Exception:  # noqa: BLE001
        logging.exception("Не удалось прописать автозапуск")
        return False


def _remove_autostart() -> None:
    if not IS_WINDOWS:
        return
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        logging.debug("Автозапуск не удалён", exc_info=True)


def _run_hidden(args: list[str], timeout: int = 30) -> bool:
    try:
        flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
        result = subprocess.run(args, creationflags=flags, timeout=timeout, capture_output=True)
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        logging.debug("Команда не выполнилась: %s", " ".join(args), exc_info=True)
        return False


def _stop_installed_copies(target: Path) -> None:
    """Погасить ТОЛЬКО установленную копию — по пути, а не по имени процесса.

    Скачанный сотрудником файл называется так же, поэтому `taskkill /IM` убивал
    сам установщик, и установка молча не происходила: лог обрывался на первой
    строке. Свой собственный PID исключаем в любом случае.
    """
    if not IS_WINDOWS:
        return
    own = os.getpid()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='" + INSTALLED_NAME + "'\" | "
        "Where-Object { $_.ExecutablePath -eq '" + str(target) + "' -and $_.ProcessId -ne " + str(own) + " } | "
        "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
    )
    _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=25)


def _register_task(target: Path) -> bool:
    """Задача «раз в минуту» — подстраховка, если убиты обе копии.
    Такая задача админских прав не требует."""
    return _run_hidden(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "MINUTE", "/MO", "1", "/TR", f'"{target}"', "/F"]
    )


def _remove_task() -> None:
    _run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])


def _create_shortcut(target: Path) -> bool:
    """Ярлык «Oktell» на рабочем столе: сотрудник открывает Oktell через него,
    и окно сразу управляемое."""
    if not IS_WINDOWS:
        return False
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "[Environment]::GetFolderPath('Desktop')+'\\Oktell.lnk');"
        f"$s.TargetPath='{target}';$s.Arguments='--open';"
        f"$s.IconLocation='{target},0';$s.Description='Oktell';$s.Save()"
    )
    return _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def _remove_shortcut() -> None:
    try:
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Oktell.lnk"
        if desktop.exists():
            desktop.unlink()
    except Exception:  # noqa: BLE001
        pass


def show_message(text: str, title: str = APP_NAME, error: bool = False) -> None:
    """Обычное окно Windows с сообщением.

    Сборка идёт без консоли, поэтому иначе установка проходит МОЛЧА: человек
    кликает по файлу, не видит ничего и жмёт ещё раз. Одно окно снимает вопрос.
    """
    if not IS_WINDOWS:
        print(text)
        return
    try:
        MB_ICONINFORMATION = 0x40
        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(
            None, str(text), str(title), MB_ICONERROR if error else MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001
        logging.debug("Окно с сообщением не показалось", exc_info=True)


def run_install(cfg: dict, start: bool = True) -> int:
    """Разложить себя по местам. Ровно то, что делали install_*.bat."""
    setup_logging(cfg, "install.log")
    target = installed_path()
    source = program_path()

    if not getattr(sys, "frozen", False):
        logging.error("Установка имеет смысл только для собранного exe")
        return 2

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            # Работающую копию нельзя перезаписать — сначала гасим её (только её).
            _stop_installed_copies(target)
            time.sleep(1.5)
            shutil.copy2(source, target)
            logging.info("Скопирован в %s", target)
        # Конфиг рядом со скачанным exe (если его положили) переносим тоже.
        local_cfg = source.parent / "config.json"
        if local_cfg.exists() and local_cfg.resolve() != (target.parent / "config.json").resolve():
            shutil.copy2(local_cfg, target.parent / "config.json")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Не удалось скопировать себя в %s", target)
        show_message(
            f"Не удалось установить программу.\n\n{exc}\n\nПодробности: {app_dir() / 'install.log'}",
            error=True,
        )
        return 1

    ok_run = _register_autostart(target)
    ok_task = _register_task(target)
    ok_link = _create_shortcut(target)
    logging.info("Автозапуск: %s | задача: %s | ярлык: %s", ok_run, ok_task, ok_link)

    if start:
        try:
            flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
            subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=flags, close_fds=True)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Не удалось запустить установленную копию")
            show_message(f"Программа установлена, но не запустилась.\n\n{exc}", error=True)
            return 1

    if ok_run:
        show_message(
            "Программа установлена и уже работает.\n\n"
            "Она будет запускаться сама при входе в Windows.\n"
            "На рабочем столе появился ярлык «Oktell» — открывайте Oktell через него.\n\n"
            "Ничего настраивать не нужно."
        )
    else:
        show_message(
            "Программа скопирована, но не смогла прописаться в автозапуск.\n"
            f"Подробности: {app_dir() / 'install.log'}",
            error=True,
        )
    return 0 if ok_run else 1


def run_uninstall(cfg: dict) -> int:
    setup_logging(cfg, "install.log")
    _remove_autostart()
    _remove_task()
    _remove_shortcut()
    logging.info("Удалено: автозапуск, задача, ярлык")
    # Гасим последними и не себя: иначе не дописали бы лог.
    _stop_installed_copies(installed_path())
    return 0


# --------------------------------------------------------------------------- #
# Автообновление
# --------------------------------------------------------------------------- #

OLD_SUFFIX = ".old.exe"


def parse_version(text: str) -> tuple:
    """1.2.10 -> (1, 2, 10). Нечисловые куски отбрасываем, чтобы «1.2.0-beta»
    не ломал сравнение."""
    parts = []
    for chunk in str(text or "").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts[:4])


def should_update(local: str, remote: str) -> bool:
    """Обновляемся только вверх. Downgrade по ошибке в манифесте недопустим:
    так одна опечатка на сервере откатила бы всем машинам рабочую версию."""
    if not remote:
        return False
    return parse_version(remote) > parse_version(local)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cleanup_old_binary() -> None:
    """Снести хвост прошлого обновления (переименованный exe)."""
    try:
        leftover = installed_path().with_suffix("")
        leftover = Path(str(leftover) + OLD_SUFFIX)
        if leftover.exists():
            leftover.unlink()
            logging.info("Удалён остаток прошлого обновления: %s", leftover)
    except Exception:  # noqa: BLE001
        logging.debug("Остаток прошлого обновления удалить не удалось", exc_info=True)


def fetch_update_manifest(cfg: dict) -> Optional[dict]:
    base = str(cfg.get("server_url") or "").rstrip("/")
    url = str(cfg.get("update_url") or "") or (f"{base}/api/oktell_guard/version" if base else "")
    if not url:
        return None
    try:
        import requests

        response = requests.get(
            url,
            timeout=float(cfg.get("request_timeout_s", 10)),
            verify=bool(cfg.get("verify_tls", True)),
            headers={"X-Agent-Token": str(cfg.get("agent_token") or ""),
                     "User-Agent": f"OktellRecallGuard/{VERSION}"},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logging.debug("Манифест обновления недоступен: %s", exc)
        return None


def download_update(cfg: dict, manifest: dict) -> Optional[Path]:
    """Скачать и проверить новый exe. Без совпадения sha256 не ставим."""
    url = str(manifest.get("url") or "")
    expected = str(manifest.get("sha256") or "").lower().strip()
    if not url:
        logging.warning("В манифесте обновления нет ссылки")
        return None
    if not expected:
        # Подписи у нас нет, поэтому хеш — единственная проверка того, что
        # приехал наш файл, а не что-то по дороге подменённое.
        logging.warning("В манифесте нет sha256 — обновление пропускаю")
        return None
    target = app_dir() / "update.download"
    try:
        import requests

        with requests.get(url, stream=True, timeout=120, verify=bool(cfg.get("verify_tls", True))) as response:
            response.raise_for_status()
            with target.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось скачать обновление: %s", exc)
        return None

    actual = sha256_of(target)
    if actual.lower() != expected:
        logging.error("Хеш обновления не совпал (ждали %s, получили %s) — файл удалён", expected, actual)
        try:
            target.unlink()
        except Exception:  # noqa: BLE001
            pass
        return None
    return target


def apply_update(new_file: Path) -> bool:
    """Подменить себя и перезапуститься.

    Работающий exe нельзя перезаписать, но можно переименовать — на этом и
    держится схема: старый файл уезжает в *.old.exe, новый встаёт на его место,
    запускается, а хвост подчищается при следующем старте.
    """
    target = installed_path()
    backup = Path(str(target.with_suffix("")) + OLD_SUFFIX)
    try:
        if backup.exists():
            backup.unlink()
        if target.exists():
            target.rename(backup)
        shutil.copy2(new_file, target)
        new_file.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logging.exception("Не удалось подменить exe — откатываюсь")
        try:
            if not target.exists() and backup.exists():
                backup.rename(target)
        except Exception:  # noqa: BLE001
            logging.exception("Откат тоже не удался")
        return False

    logging.info("Обновление установлено, перезапускаюсь")
    try:
        flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
        subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=flags, close_fds=True)
    except Exception:  # noqa: BLE001
        logging.exception("Новая копия не запустилась")
        return False
    return True


def check_for_update(cfg: dict) -> bool:
    """True, если обновились и пора завершаться."""
    if not getattr(sys, "frozen", False) or not cfg.get("auto_update", True):
        return False
    manifest = fetch_update_manifest(cfg)
    if not manifest:
        return False
    remote = str(manifest.get("version") or "")
    if not should_update(VERSION, remote):
        return False
    logging.info("Есть версия %s (у меня %s) — обновляюсь", remote, VERSION)
    downloaded = download_update(cfg, manifest)
    if not downloaded:
        return False
    return apply_update(downloaded)


# --------------------------------------------------------------------------- #
# Реестр исполненных команд (идемпотентность)
# --------------------------------------------------------------------------- #

class CommandLedger:
    """Помнит уже исполненные команды.

    Нужен потому, что сервер может отдать одну и ту же команду повторно (ack
    потерялся, агент перезапустился). Без этого один «перезвон» превращался бы
    в серию разлогинов подряд.
    """

    def __init__(self, path: Path, ttl_s: int = COMMAND_TTL_S, limit: int = COMMAND_LEDGER_LIMIT):
        self.path = Path(path)
        self.ttl_s = ttl_s
        self.limit = limit
        self._items: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._items = {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001 — битый файл не повод падать
            self._items = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:  # noqa: BLE001
            logging.debug("Не удалось сохранить реестр команд", exc_info=True)

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._items = {k: v for k, v in self._items.items() if now - float(v.get("ts", 0)) <= self.ttl_s}
        if len(self._items) > self.limit:
            ordered = sorted(self._items.items(), key=lambda kv: float(kv[1].get("ts", 0)), reverse=True)
            self._items = dict(ordered[: self.limit])

    def seen(self, command_id: str, now: Optional[float] = None) -> bool:
        self.prune(now)
        return str(command_id) in self._items

    def mark(self, command_id: str, status: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._items[str(command_id)] = {"ts": now, "status": status}
        self.prune(now)
        self._save()


def backoff_delay(attempt: int, base_s: float, cap_s: float) -> float:
    """Экспоненциальная пауза при недоступном сервере, без джиттера —
    агент один на машину, стадо не набегает."""
    if attempt <= 0:
        return base_s
    return float(min(cap_s, base_s * (2 ** min(attempt, 10))))


# --------------------------------------------------------------------------- #
# JS-полезная нагрузка
# --------------------------------------------------------------------------- #

def origin_of(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


# Шаблон вынесен из функции: внутри много JS с кавычками, и собирать его
# конкатенацией в теле функции — верный способ однажды сломать экранирование.
HOOK_JS_TEMPLATE = r"""
(function () {
  if (window.__oktellGuardHooked) { return; }
  window.__oktellGuardHooked = true;
  window.__oktellGuardSockets = [];
  var cfg = window.__oktellGuardRuleConfig = __RULE_PARAMS__;

  var Native = window.WebSocket;
  if (!Native) { return; }

  // Копим ТОЛЬКО кадры со статусом оператора: разговоры, номера и данные
  // клиентов сюда не попадают и страницу не покидают.
  window.__oktellGuardStateFrames = [];

  var BUDGET_KEY = '__oktell_guard_budget';

  var rule = window.__oktellGuardRule = {
    since: null,        // когда начался текущий заход в «Перезвон»
    budget: 0,          // накопленные секунды «Перезвона» с последнего звонка
    callSeen: false,    // был ли звонок после последнего накопления
    warned: false, fired: false, login: null, seconds: 0,
    lastState: null, lastCallState: null, seenStates: []
  };

  function today() {
    var d = new Date();
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }

  // Счётчик переживает перезагрузку страницы: иначе ограничитель обходится
  // клавишей F5. День в ключе — чтобы вчерашнее не переносилось на сегодня.
  function loadBudget() {
    try {
      var saved = JSON.parse(localStorage.getItem(BUDGET_KEY) || 'null');
      if (saved && saved.day === today() && (!rule.login || saved.login === rule.login)) {
        rule.budget = Number(saved.budget) || 0;
      }
    } catch (e) {}
  }

  function saveBudget() {
    try {
      localStorage.setItem(BUDGET_KEY, JSON.stringify({
        day: today(), login: rule.login, budget: rule.budget, at: Date.now()
      }));
    } catch (e) {}
  }

  function totalSeconds() {
    var live = rule.since === null ? 0 : Math.floor((Date.now() - rule.since) / 1000);
    return rule.budget + live;
  }

  // Звонок — единственное, что обнуляет накопленное. Признак берём из того же
  // события статуса: разговор/набор клиент показывает строкой состояния.
  function looksLikeCall(payload) {
    var text = String(payload.userstatestr || '');
    if (cfg.callStateStrings && cfg.callStateStrings.length) {
      for (var i = 0; i < cfg.callStateStrings.length; i++) {
        if (text.toLowerCase().indexOf(String(cfg.callStateStrings[i]).toLowerCase()) >= 0) { return true; }
      }
    }
    if (cfg.callStateIds && cfg.callStateIds.length) {
      for (var j = 0; j < cfg.callStateIds.length; j++) {
        if (Number(payload.userstate) === Number(cfg.callStateIds[j])) { return true; }
      }
    }
    return false;
  }

  function onCall(payload) {
    rule.callSeen = true;
    rule.lastCallState = { state: payload.userstate, str: payload.userstatestr, at: Date.now() };
    // Разговор идёт прямо сейчас — накопленное списываем сразу, чтобы плашка
    // не выскочила посреди звонка.
    rule.budget = 0;
    rule.since = null;
    rule.warned = false;
    rule.fired = false;
    hideBanner();
    saveBudget();
  }

  function onFrame(raw) {
    if (typeof raw !== 'string') { return; }
    if (raw.indexOf('userstate') < 0 && raw.indexOf('lunchreason') < 0) { return; }
    try {
      window.__oktellGuardStateFrames.push({ at: Date.now(), raw: raw.slice(0, 800) });
      if (window.__oktellGuardStateFrames.length > 40) { window.__oktellGuardStateFrames.shift(); }
    } catch (e) {}
    if (!cfg.enabled) { return; }
    try {
      var parsed = JSON.parse(raw);
      if (Object.prototype.toString.call(parsed) === '[object Array]' && parsed.length > 1) {
        onState(parsed[1]);
      }
    } catch (e) {}
  }

  function onState(payload) {
    if (!payload || typeof payload !== 'object') { return; }
    if (payload.userlogin) { rule.login = String(payload.userlogin); }
    if (payload.userstatestr && rule.seenStates.indexOf(payload.userstatestr) < 0) {
      // Небольшой словарь встреченных состояний: по нему настраивается
      // распознавание звонка, значений разговоров тут нет.
      rule.seenStates.push(String(payload.userstatestr));
      if (rule.seenStates.length > 20) { rule.seenStates.shift(); }
    }
    if (looksLikeCall(payload)) { onCall(payload); return; }
    if (payload.onlunch === undefined && payload.lunchreasonid === undefined) { return; }
    var isRecall = payload.onlunch === true &&
                   Number(payload.lunchreasonid) === Number(cfg.recallReasonId);
    rule.lastState = { onlunch: !!payload.onlunch, reason: payload.lunchreasonid, at: Date.now() };
    if (!isRecall) {
      // Выход из «Перезвона» НЕ обнуляет накопленное: иначе ограничитель
      // обходится переключением статуса туда-обратно на секунду. Обнуляет
      // только состоявшийся звонок (см. onCall).
      if (rule.since !== null) {
        rule.budget += Math.floor((Date.now() - rule.since) / 1000);
        rule.since = null;
        saveBudget();
      }
      rule.seconds = rule.budget;
      hideBanner();
      return;
    }
    if (rule.since === null) {
      if (rule.callSeen) {
        rule.budget = 0;
        rule.callSeen = false;
        rule.warned = false;
        rule.fired = false;
        saveBudget();
      }
      rule.since = Date.now();
    }
  }

  var BANNER_ID = '__oktell_guard_banner';

  function hideBanner() {
    var old = document.getElementById(BANNER_ID);
    if (old && old.parentNode) { old.parentNode.removeChild(old); }
  }

  function banner(text, seconds) {
    hideBanner();
    var box = document.createElement('div');
    box.id = BANNER_ID;
    box.setAttribute('style', ['position:fixed', 'z-index:2147483647', 'left:50%', 'top:24px',
      'transform:translateX(-50%)', 'max-width:min(560px,92vw)', 'padding:14px 18px',
      'border-radius:14px', 'background:rgba(20,20,22,0.92)', 'color:#fff',
      'font:600 15px/1.35 -apple-system,Segoe UI,Roboto,Arial,sans-serif',
      'box-shadow:0 10px 30px rgba(0,0,0,0.35)', 'text-align:center',
      'pointer-events:none'].join(';'));
    var line = document.createElement('div');
    line.textContent = text;
    var timer = document.createElement('div');
    timer.setAttribute('style', 'margin-top:6px;font-weight:500;opacity:.75');
    box.appendChild(line);
    box.appendChild(timer);
    (document.body || document.documentElement).appendChild(box);
    var left = seconds;
    var tick = function () {
      timer.textContent = left > 0 ? ('через ' + left + ' с') : '';
      left--;
      if (left < 0) { clearInterval(handle); hideBanner(); }
    };
    tick();
    var handle = setInterval(tick, 1000);
  }

  function recordViolation(seconds) {
    // Пишем ПОСЛЕ очистки хранилища, иначе стёрли бы собственную запись.
    try {
      var list = [];
      try { list = JSON.parse(localStorage.getItem('__oktell_guard_violations') || '[]'); } catch (e) { list = []; }
      list.push({ at: new Date().toISOString(), login: rule.login, seconds: seconds, reason: 'recall_timeout' });
      if (list.length > 50) { list = list.slice(-50); }
      localStorage.setItem('__oktell_guard_violations', JSON.stringify(list));
    } catch (e) {}
  }

  function logout(seconds) {
    try {
      var socks = window.__oktellGuardSockets || [];
      for (var i = socks.length - 1; i >= 0; i--) {
        if (socks[i] && socks[i].readyState === 1) {
          socks[i].send(JSON.stringify(['logout', {}]));
          break;
        }
      }
    } catch (e) {}
    var keys = cfg.sessionKeys || [];
    for (var j = 0; j < keys.length; j++) {
      try { localStorage.removeItem(keys[j]); } catch (e) {}
      try {
        document.cookie = keys[j] + '=; Max-Age=0; path=/';
        document.cookie = keys[j] + '=; Max-Age=0; path=/; domain=' + location.hostname;
      } catch (e) {}
    }
    try { sessionStorage.clear(); } catch (e) {}
    recordViolation(seconds);
    setTimeout(function () { location.reload(); }, 300);
  }

  function Guarded(url, protocols) {
    var ws = (protocols === undefined) ? new Native(url) : new Native(url, protocols);
    try {
      window.__oktellGuardSockets.push(ws);
      ws.addEventListener('message', function (event) { onFrame(event.data); });
    } catch (e) {}
    return ws;
  }
  Guarded.prototype = Native.prototype;
  try {
    Guarded.CONNECTING = Native.CONNECTING; Guarded.OPEN = Native.OPEN;
    Guarded.CLOSING = Native.CLOSING; Guarded.CLOSED = Native.CLOSED;
  } catch (e) {}
  window.WebSocket = Guarded;

  if (cfg.enabled) {
    loadBudget();
    setInterval(function () {
      if (rule.since === null || rule.fired) { return; }
      var seconds = totalSeconds();
      rule.seconds = seconds;
      if (!rule.warned && seconds >= cfg.thresholdS - cfg.warnBeforeS) {
        rule.warned = true;
        banner(cfg.message, Math.max(1, cfg.thresholdS - seconds));
      }
      if (seconds >= cfg.thresholdS) {
        rule.fired = true;
        rule.budget = 0;
        rule.since = null;
        rule.callSeen = false;
        saveBudget();
        logout(seconds);
      }
    }, 1000);
  }
})();
"""


def rule_version(rule: Optional[dict] = None) -> str:
    """Отпечаток правила: версия агента + сами параметры.

    Нужен, чтобы заметить страницу со старым правилом. Хук идемпотентен и в уже
    открытом документе повторно не выполняется, поэтому без этой сверки
    обновление порога (или закрытие дыры) доезжало бы до оператора только
    после того, как он сам перезагрузит вкладку.
    """
    payload = json.dumps(rule or {}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"{VERSION}-{digest}"


def build_hook_js(rule=None) -> str:
    """Хук ставится на КАЖДЫЙ новый документ (Page.addScriptToEvaluateOnNewDocument).

    Делает два дела:
      1) сохраняет ссылки на живые WebSocket клиента — через них уходит штатный
         кадр `logout`, чтобы серверная сессия умерла сразу, а не по таймауту;
      2) держит правило «Перезвон дольше нормы» ПРЯМО В ОКНЕ.

    Правило живёт здесь, а не на сервере, потому что клиент и так получает от
    Oktell событие статуса вида
        ["getuserstateresult", {"onlunch": true, "lunchreasonid": 2, "userlogin": "6612", ...}]
    — то есть секунды можно считать мгновенно и бесплатно, без опроса базы и без
    нагрузки на SQL-прокси, который под опросом уже дважды отваливался. Наружу
    уходит только факт нарушения, постфактум.
    """
    rule = rule or {}
    params = json.dumps(
        {
            "ruleVersion": rule_version(rule),
            "enabled": bool(rule.get("enabled", True)),
            "thresholdS": int(rule.get("threshold_s", 180)),
            "warnBeforeS": int(rule.get("warn_before_s", 30)),
            "recallReasonId": int(rule.get("recall_lunch_reason_id", 2)),
            # Что считать звонком: строки состояния и/или числовые коды.
            # Обнуляет накопленное ТОЛЬКО это, смена статуса — нет.
            "callStateStrings": list(rule.get("call_state_strings") or ["talk", "dial", "call", "ring"]),
            "callStateIds": list(rule.get("call_state_ids") or []),
            "message": str(rule.get("message") or "«Перезвон» дольше нормы — сессия будет закрыта"),
            "sessionKeys": list(rule.get("session_keys") or DEFAULT_SESSION_KEYS),
        },
        ensure_ascii=False,
    )
    return HOOK_JS_TEMPLATE.replace("__RULE_PARAMS__", params).strip()


def build_probe_js(session_keys: Iterable[str]) -> str:
    """Читает ровно три факта: есть ли сессия, виден ли экран входа, какой логин.

    Содержимое страницы (разговоры, клиенты, номера) не читается и не передаётся.
    """
    keys = json.dumps(list(session_keys))
    return f"""
(function () {{
  var keys = {keys};
  var out = {{ session: false, loginForm: false, login: null, url: location.href, title: document.title }};
  try {{
    for (var i = 0; i < keys.length; i++) {{
      var k = keys[i];
      try {{ if (localStorage.getItem(k)) {{ out.session = true; }} }} catch (e) {{}}
      if (!out.session && document.cookie.indexOf(k + '=') >= 0) {{ out.session = true; }}
    }}
  }} catch (e) {{ out.error = String(e); }}
  try {{ out.loginForm = !!document.querySelector('input[type="password"]'); }} catch (e) {{}}
  try {{
    // Логин лежит в отдельной cookie __oktelllogin — это именно логин, а не
    // токен сессии, поэтому читать и передавать его безопасно. Обход Angular
    // ниже оставлен запасным путём: на живом клиенте он логина не находит.
    var m = document.cookie.match(/(?:^|;\s*)__oktelllogin=([^;]*)/);
    if (m) {{
      var value = decodeURIComponent(m[1]);
      // Страховка: если вендор однажды положит сюда токен — не отправляем его.
      if (value && value.length < 40 && !/^[0-9a-f-]{{30,}}$/i.test(value)) {{ out.login = value; }}
    }}
  }} catch (e) {{}}
  try {{
    if (!out.login && window.angular && document.body) {{
      var scope = window.angular.element(document.body).scope();
      var root = scope && scope.$root ? scope.$root : scope;
      var re = /^(login|username|userlogin|operatorlogin|user_name)$/i;
      var seen = 0;
      var walk = function (obj, depth) {{
        if (!obj || depth > 2 || seen > 400 || out.login) {{ return; }}
        for (var key in obj) {{
          if (!Object.prototype.hasOwnProperty.call(obj, key)) {{ continue; }}
          if (key.charAt(0) === '$') {{ continue; }}
          seen++;
          var val = obj[key];
          if (typeof val === 'string' && val && re.test(key)) {{ out.login = val; return; }}
          if (val && typeof val === 'object' && depth < 2) {{ walk(val, depth + 1); }}
        }}
      }};
      walk(root, 0);
    }}
  }} catch (e) {{}}
  return out;
}})();
""".strip()


def build_logout_js(session_keys: Iterable[str]) -> str:
    """Разлогин внутри страницы: штатный WS-logout + снос сессии.

    Порядок важен: сначала сокет (пока сессия ещё валидна — сервер корректно
    закрывает её сам), потом чистка хранилищ, и только потом перезагрузка,
    которую делает уже CDP (Page.reload) — так надёжнее, чем location.reload()
    из скрипта, который может не успеть выполниться.
    """
    keys = json.dumps(list(session_keys))
    return f"""
(function () {{
  var keys = {keys};
  var out = {{ socket: false, cleared: [], wiped: false }};
  try {{
    var socks = window.__oktellGuardSockets || [];
    for (var i = socks.length - 1; i >= 0; i--) {{
      var s = socks[i];
      if (s && s.readyState === 1) {{
        s.send(JSON.stringify(['logout', {{}}]));
        out.socket = true;
        break;
      }}
    }}
  }} catch (e) {{ out.socketError = String(e); }}
  for (var j = 0; j < keys.length; j++) {{
    var k = keys[j];
    try {{
      if (localStorage.getItem(k) !== null) {{ localStorage.removeItem(k); out.cleared.push('ls:' + k); }}
    }} catch (e) {{}}
    try {{ sessionStorage.removeItem(k); }} catch (e) {{}}
    try {{
      document.cookie = k + '=; Max-Age=0; path=/';
      document.cookie = k + '=; Max-Age=0; path=/; domain=' + location.hostname;
      out.cleared.push('cookie:' + k);
    }} catch (e) {{}}
  }}
  try {{ localStorage.clear(); sessionStorage.clear(); out.wiped = true; }} catch (e) {{}}
  return out;
}})();
""".strip()


def build_banner_js(message: str, seconds: int) -> str:
    """Предупреждение поверх страницы. Ставится в самом документе, потому что
    системный toast оператор в полноэкранном софтфоне не увидит."""
    payload = json.dumps(str(message))
    secs = max(1, min(600, int(seconds or 30)))
    return f"""
(function () {{
  var text = {payload};
  var total = {secs};
  var id = '__oktell_guard_banner';
  var old = document.getElementById(id);
  if (old && old.parentNode) {{ old.parentNode.removeChild(old); }}
  var box = document.createElement('div');
  box.id = id;
  box.setAttribute('style', [
    'position:fixed', 'z-index:2147483647', 'left:50%', 'top:24px',
    'transform:translateX(-50%)', 'max-width:min(560px,92vw)',
    'padding:14px 18px', 'border-radius:14px',
    'background:rgba(20,20,22,0.92)', 'color:#fff',
    'font:600 15px/1.35 -apple-system,Segoe UI,Roboto,Arial,sans-serif',
    'box-shadow:0 10px 30px rgba(0,0,0,0.35)', 'text-align:center',
    'pointer-events:none'
  ].join(';'));
  var line = document.createElement('div');
  line.textContent = text;
  var timer = document.createElement('div');
  timer.setAttribute('style', 'margin-top:6px;font-weight:500;opacity:.75');
  box.appendChild(line);
  box.appendChild(timer);
  (document.body || document.documentElement).appendChild(box);
  var left = total;
  var tick = function () {{
    timer.textContent = left > 0 ? ('через ' + left + ' с') : '';
    left--;
    if (left < 0) {{
      clearInterval(handle);
      if (box.parentNode) {{ box.parentNode.removeChild(box); }}
    }}
  }};
  tick();
  var handle = setInterval(tick, 1000);
  return true;
}})();
""".strip()


# --------------------------------------------------------------------------- #
# Chrome DevTools Protocol
# --------------------------------------------------------------------------- #

class CdpError(RuntimeError):
    pass


def pick_oktell_target(targets: list[dict], origin: str) -> Optional[dict]:
    """Из списка целей выбираем вкладку веб-клиента Oktell.

    Служебные цели (devtools://, chrome://, расширения, воркеры) отбрасываем:
    Runtime.evaluate в них либо запрещён, либо бессмыслен.
    """
    origin = (origin or "").rstrip("/").lower()
    best: Optional[dict] = None
    for target in targets or []:
        if target.get("type") != "page":
            continue
        url = str(target.get("url") or "")
        if url.startswith(("devtools://", "chrome://", "chrome-extension://", "about:")):
            continue
        # Сравниваем именно origin, а не префикс строки: иначе
        # https://oktell.example.local.evil.com сошёл бы за нашу вкладку.
        if origin and origin_of(url).lower() != origin:
            continue
        if not target.get("webSocketDebuggerUrl"):
            continue
        # Первую подходящую и берём: в app-режиме окно одно.
        if best is None:
            best = target
    return best


class CdpPage:
    """Тонкий клиент CDP к одной вкладке. Без зависимостей, кроме websocket-client."""

    def __init__(self, ws_url: str, timeout: float = 10.0):
        try:
            import websocket  # type: ignore
        except ImportError as exc:  # pragma: no cover — на машине оператора есть в exe
            raise CdpError("Нужен пакет websocket-client (pip install websocket-client)") from exc
        # suppress_origin: Chrome >= 111 отклоняет апгрейд, если Origin чужой.
        self._ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self._timeout = timeout
        self._id = 0

    @property
    def connected(self) -> bool:
        return bool(getattr(self._ws, "connected", False))

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "CdpPage":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def call(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        import websocket  # type: ignore

        self._id += 1
        message_id = self._id
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + (timeout or self._timeout)
        while time.time() < deadline:
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            try:
                message = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if message.get("id") != message_id:
                continue  # событие — не наш ответ
            if "error" in message:
                raise CdpError(f"{method}: {message['error']}")
            return message.get("result", {})
        raise CdpError(f"{method}: таймаут ответа CDP")

    def evaluate(self, expression: str, timeout: Optional[float] = None) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        details = result.get("exceptionDetails")
        if details:
            raise CdpError(f"JS-исключение: {details.get('text')}")
        return (result.get("result") or {}).get("value")


class ManagedBrowser:
    """Chrome, которым владеем мы: свой профиль + порт отладки на loopback."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.browser_cfg = cfg.get("browser", {}) or {}
        self.url = str(cfg.get("oktell_url") or "")
        self.origin = origin_of(self.url)
        profile_cfg = str(self.browser_cfg.get("profile_dir") or "").strip()
        self.profile_dir = Path(os.path.expandvars(profile_cfg)) if profile_cfg else app_dir() / "chrome-profile"
        self.process: Optional[subprocess.Popen] = None
        # Постоянное CDP-подключение к вкладке: хук ставится один раз на него.
        self._page: Optional[CdpPage] = None
        self._page_target_id: str = ""
        # Вкладки, которые уже перезагрузили ради обновления правила: страховка
        # от цикла «перезагрузил — снова не совпало — перезагрузил».
        self._reloaded_for_rule: set[str] = set()

    # ---------- запуск ----------

    def chrome_path(self) -> Optional[Path]:
        configured = os.path.expandvars(str(self.browser_cfg.get("chrome_path") or "").strip())
        if configured and Path(configured).exists():
            return Path(configured)
        # Chrome, а если его нет — Edge: он тоже Chromium и понимает и
        # --app, и --remote-debugging-port, но в отличие от Chrome есть на
        # любой Windows. Иначе «программа без зависимостей» упиралась бы в
        # необходимость сначала поставить браузер.
        relative = [
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
        ]
        for tail in relative:
            for env_key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                base = os.environ.get(env_key)
                if not base:
                    continue
                candidate = Path(base) / tail
                if candidate.exists():
                    return candidate
        if IS_WINDOWS:
            try:
                import winreg  # type: ignore

                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(
                            hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                        ) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                            if value and Path(value).exists():
                                return Path(value)
                    except OSError:
                        continue
            except Exception:  # noqa: BLE001
                pass
        found = shutil.which("chrome") or shutil.which("google-chrome")
        return Path(found) if found else None

    def launch_args(self, chrome: Path) -> list[str]:
        port = int(self.browser_cfg.get("cdp_port", 0) or 0)
        args = [
            str(chrome),
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={port}",
            # Слушаем только петлю: порт отладки наружу отдавать нельзя.
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,ChromeWhatsNewUI",
        ]
        args.extend(str(a) for a in (self.browser_cfg.get("extra_args") or []))
        if self.browser_cfg.get("app_mode", True):
            args.append(f"--app={self.url}")
        else:
            args.append(self.url)
        return args

    def devtools_port(self) -> Optional[int]:
        configured = int(self.browser_cfg.get("cdp_port", 0) or 0)
        if configured:
            return configured
        # При --remote-debugging-port=0 Chrome пишет выбранный порт первой
        # строкой DevToolsActivePort в каталоге профиля.
        marker = self.profile_dir / "DevToolsActivePort"
        try:
            first_line = marker.read_text(encoding="utf-8").splitlines()[0].strip()
            return int(first_line)
        except Exception:  # noqa: BLE001
            return None

    def is_debug_port_alive(self) -> bool:
        port = self.devtools_port()
        if not port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    def launch(self) -> bool:
        chrome = self.chrome_path()
        if not chrome:
            logging.error("Не найден ни Chrome, ни Edge. Укажи browser.chrome_path в config.json")
            return False
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = self.launch_args(chrome)
        try:
            flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
            self.process = subprocess.Popen(args, creationflags=flags, close_fds=True)
        except Exception:  # noqa: BLE001
            logging.exception("Не удалось запустить Chrome")
            return False
        logging.info("Запущен управляемый Chrome: %s", " ".join(args[1:]))
        for _ in range(40):  # до ~12 с на холодный старт
            time.sleep(0.3)
            if self.is_debug_port_alive():
                return True
        logging.warning("Chrome запущен, но порт отладки не отвечает")
        return False

    def wait_for_page(self, timeout_s: float = 20.0) -> Optional[dict]:
        """Ждём появления вкладки Oktell.

        Порт отладки отвечает раньше, чем создаётся вкладка, поэтому сразу
        после launch() разворачивать ещё нечего — окна для CDP не существует.
        Из-за этой гонки окно оставалось свёрнутым, и демо выглядело как
        «Chrome не открылся».
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            target = self.oktell_target()
            if target:
                return target
            time.sleep(0.5)
        return None

    def ensure_running(self) -> bool:
        if self.is_debug_port_alive():
            return True
        if not self.launch():
            return False
        # Chrome помнит состояние окна в профиле: если прошлый сеанс закончился
        # свёрнутым окном, новое откроется свёрнутым же. Ждём вкладку и
        # приводим окно в видимое состояние.
        if self.wait_for_page():
            self.ensure_window_visible(bring_to_front=True)
        else:
            logging.warning("Chrome запущен, но вкладка Oktell не появилась за 20 с")
        return True

    # ---------- работа со вкладкой ----------

    def targets(self) -> list[dict]:
        port = self.devtools_port()
        if not port:
            return []
        try:
            import requests

            response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            logging.debug("CDP /json/list недоступен", exc_info=True)
            return []

    def oktell_target(self) -> Optional[dict]:
        return pick_oktell_target(self.targets(), self.origin)

    def page(self, target: Optional[dict] = None) -> Optional[CdpPage]:
        """Постоянное подключение к вкладке Oktell (переиспользуется между опросами).

        Именно постоянное, и это не оптимизация. Chrome снимает скрипты,
        добавленные через `Page.addScriptToEvaluateOnNewDocument`, когда
        отключается добавивший их клиент. При переподключении на каждый опрос
        хук не доживал бы до следующей загрузки страницы — проверено на стенде:
        после reload `window.__oktellGuardSockets` оказывался пустым, и штатный
        WS-logout был бы недоступен ровно тогда, когда он нужен.
        """
        if self._page is not None:
            if getattr(self._page, "connected", False) and (target is None or str(target.get("id") or "") == self._page_target_id):
                return self._page
            self.close_page()

        target = target or self.oktell_target()
        if not target:
            return None
        try:
            page = CdpPage(str(target["webSocketDebuggerUrl"]), timeout=float(self.cfg.get("request_timeout_s", 10)))
        except Exception:  # noqa: BLE001
            logging.debug("Не удалось подключиться к вкладке по CDP", exc_info=True)
            return None
        self._page = page
        self._page_target_id = str(target.get("id") or "")
        self.install_hook(page)
        return page

    def close_page(self) -> None:
        if self._page is not None:
            self._page.close()
        self._page = None
        self._page_target_id = ""

    def install_hook(self, page: CdpPage) -> None:
        """Хук на будущие документы + тот же код в текущий.

        `Page.enable` обязателен: проверено на стенде — без него
        `addScriptToEvaluateOnNewDocument` возвращает identifier, но скрипт
        в новый документ не попадает (после reload хука в странице нет).
        А `Page.disable` звать нельзя: в Chromium он очищает список добавленных
        скриптов, то есть «включить и выключить» стёрло бы хук.
        """
        rule_cfg = dict(self.cfg.get("in_window_rule") or {})
        rule_cfg.setdefault("session_keys", self.cfg.get("session_keys", DEFAULT_SESSION_KEYS))
        source = build_hook_js(rule_cfg)
        expected = rule_version(rule_cfg)
        try:
            page.call("Page.enable")
            page.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        except CdpError:
            logging.debug("addScriptToEvaluateOnNewDocument не прошёл", exc_info=True)
        try:
            page.evaluate(source)
        except CdpError:
            logging.debug("Хук не выполнился в текущем документе", exc_info=True)

        # Если в странице живёт правило прежней версии — обновить его можно
        # только перезагрузкой: повторный запуск хука блокируется его же
        # флагом идемпотентности. Перезагружаем ровно один раз на вкладку.
        try:
            actual = page.evaluate("(window.__oktellGuardRuleConfig || {}).ruleVersion || null")
        except CdpError:
            actual = None
        if actual != expected and self._page_target_id not in self._reloaded_for_rule:
            self._reloaded_for_rule.add(self._page_target_id)
            logging.info("В окне правило версии %s, нужно %s — перезагружаю страницу", actual, expected)
            try:
                page.call("Page.reload", {"ignoreCache": False})
            except CdpError:
                logging.debug("Перезагрузка ради обновления правила не удалась", exc_info=True)

    def probe(self) -> dict:
        """Снимок состояния вкладки Oktell для heartbeat."""
        state = {"window": False, "session": False, "login_form": False, "login": None, "url": None}
        target = self.oktell_target()
        if not target:
            self.close_page()
            return state
        state["window"] = True
        state["url"] = target.get("url")
        page = self.page(target)
        if not page:
            return state
        try:
            data = page.evaluate(build_probe_js(self.cfg.get("session_keys", DEFAULT_SESSION_KEYS)))
        except Exception:  # noqa: BLE001
            logging.debug("Проба страницы не удалась", exc_info=True)
            self.close_page()
            return state
        if isinstance(data, dict):
            state["session"] = bool(data.get("session"))
            state["login_form"] = bool(data.get("loginForm"))
            state["login"] = data.get("login") or None
            state["url"] = data.get("url") or state["url"]
        return state

    def ensure_window_visible(self, bring_to_front: bool = True) -> bool:
        """Развернуть окно Oktell, если оно свёрнуто, и (опционально) поднять поверх.

        Без этого весь ограничитель незаметен: свёрнутое окно не показывает ни
        предупреждение, ни экран входа после разлогина — оператор просто не
        поймёт, что произошло. Chrome к тому же восстанавливает состояние окна
        из профиля, так что «свёрнуто» переживает перезапуск.
        """
        page = self.page()
        if not page:
            return False
        try:
            window = page.call("Browser.getWindowForTarget", {"targetId": self._page_target_id})
            state = (window.get("bounds") or {}).get("windowState")
            if state == "minimized":
                page.call(
                    "Browser.setWindowBounds",
                    {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
                )
                logging.info("Окно Oktell было свёрнуто — развернул")
            if bring_to_front:
                page.call("Page.bringToFront")
            return True
        except Exception:  # noqa: BLE001
            logging.debug("Не удалось показать окно Oktell", exc_info=True)
            return False

    def show_banner(self, message: str, seconds: int) -> bool:
        page = self.page()
        if not page:
            return False
        try:
            # Предупреждение показываем без кражи фокуса: оператор может
            # говорить с клиентом, отбирать у него окно посреди разговора нельзя.
            self.ensure_window_visible(bring_to_front=False)
            page.evaluate(build_banner_js(message, seconds))
            return True
        except Exception:  # noqa: BLE001
            logging.debug("Баннер не показан", exc_info=True)
            self.close_page()
            return False

    def logout(self) -> dict:
        """Настоящий разлогин: WS-logout → снос сессии → чистка origin → reload.

        Возвращает словарь-отчёт, он же уходит в ack.
        """
        report: dict[str, Any] = {"status": "failed", "steps": {}}
        target = self.oktell_target()
        if not target:
            report["status"] = "no_window"
            report["detail"] = "нет управляемого окна Oktell"
            return report
        page = self.page(target)
        if not page:
            report["status"] = "no_cdp"
            report["detail"] = "вкладка найдена, но CDP не отвечает"
            return report

        keys = self.cfg.get("session_keys", DEFAULT_SESSION_KEYS)
        try:
            report["steps"]["page"] = page.evaluate(build_logout_js(keys))
        except Exception as exc:  # noqa: BLE001
            report["steps"]["page_error"] = str(exc)

        # Cookie-сессия может быть HttpOnly — из JS её не стереть, только CDP.
        if self.origin:
            try:
                page.call(
                    "Storage.clearDataForOrigin",
                    {"origin": self.origin, "storageTypes": "cookies,local_storage,session_storage,indexeddb"},
                )
                report["steps"]["storage_cleared"] = True
            except Exception as exc:  # noqa: BLE001
                report["steps"]["storage_error"] = str(exc)

        try:
            page.call("Page.reload", {"ignoreCache": True})
            report["steps"]["reloaded"] = True
        except Exception as exc:  # noqa: BLE001
            report["steps"]["reload_error"] = str(exc)

        # Здесь фокус забираем осознанно: это и есть санкция — оператор обязан
        # увидеть экран входа и ввести пароль заново.
        if self.browser_cfg.get("focus_on_command", True):
            report["steps"]["window_shown"] = self.ensure_window_visible(bring_to_front=True)

        # Проверяем результат: сессии быть не должно, экран входа — должен.
        time.sleep(3.0)
        after = self.probe()
        report["after"] = after
        if after.get("window") and not after.get("session"):
            report["status"] = "done"
        elif not after.get("window"):
            report["status"] = "window_closed"
        else:
            report["status"] = "not_verified"
        return report


# --------------------------------------------------------------------------- #
# Неуправляемые окна Oktell (обход ярлыка)
# --------------------------------------------------------------------------- #

def match_unmanaged_titles(titles: Iterable[str], patterns: Iterable[str]) -> list[str]:
    """Заголовки окон, похожих на Oktell вне нашего профиля.

    Нужно для серверного fail-closed: если оператор в Oktell есть, а
    управляемого окна нет, разлогинить его нечем — это само по себе повод
    применить серверный рычаг.
    """
    compiled = [re.compile(str(p), re.IGNORECASE) for p in patterns if str(p).strip()]
    if not compiled:
        return []
    return [t for t in titles if t and any(rx.search(t) for rx in compiled)]


def list_window_titles() -> list[str]:
    if not IS_WINDOWS:
        return []
    try:
        import win32gui  # type: ignore
    except ImportError:
        return []
    titles: list[str] = []

    def _cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                text = win32gui.GetWindowText(hwnd)
                if text:
                    titles.append(text)
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:  # noqa: BLE001
        pass
    return titles


# --------------------------------------------------------------------------- #
# Обмен с сервером
# --------------------------------------------------------------------------- #

@dataclass
class AgentIdentity:
    hostname: str
    windows_user: str
    operator_login: str = ""

    @property
    def agent_id(self) -> str:
        return f"{self.hostname}|{self.windows_user}".lower()


def current_identity(cfg: dict) -> AgentIdentity:
    hostname = os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown-host"
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"
    return AgentIdentity(hostname=hostname, windows_user=user, operator_login=str(cfg.get("operator_login") or ""))


@dataclass
class AgentState:
    identity: AgentIdentity
    browser: dict = field(default_factory=dict)
    unmanaged: list[str] = field(default_factory=list)
    last_command: Optional[dict] = None


def build_heartbeat_payload(state: AgentState, cfg: dict, now_iso: str) -> dict:
    """Наружу уходит минимум: кто, где, есть ли управляемая сессия, что с командой."""
    browser = state.browser or {}
    return {
        "agent_id": state.identity.agent_id,
        "hostname": state.identity.hostname,
        "windows_user": state.identity.windows_user,
        "operator_login": state.identity.operator_login or browser.get("login") or None,
        "version": VERSION,
        "ts": now_iso,
        "dry_run": bool(cfg.get("dry_run")),
        "browser": {
            "managed_window": bool(browser.get("window")),
            "session_present": bool(browser.get("session")),
            "login_form": bool(browser.get("login_form")),
            "url": browser.get("url"),
        },
        "unmanaged_windows": list(state.unmanaged or []),
        "last_command": state.last_command,
    }


class ServerLink:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base = str(cfg.get("server_url") or "").rstrip("/")
        self.timeout = float(cfg.get("request_timeout_s", 10))
        self.verify = bool(cfg.get("verify_tls", True))
        import requests

        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Agent-Token": str(cfg.get("agent_token") or ""),
                "User-Agent": f"OktellRecallGuard/{VERSION}",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path_key: str, default_path: str) -> str:
        path = str(self.cfg.get(path_key) or default_path)
        if path.startswith("http"):
            return path
        return f"{self.base}{path}"

    def heartbeat(self, payload: dict) -> Optional[dict]:
        url = self._url("heartbeat_path", "/api/oktell_guard/heartbeat")
        response = self._session.post(url, json=payload, timeout=self.timeout, verify=self.verify)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    def ack(self, payload: dict) -> None:
        url = self._url("ack_path", "/api/oktell_guard/ack")
        try:
            self._session.post(url, json=payload, timeout=self.timeout, verify=self.verify)
        except Exception:  # noqa: BLE001 — ack не критичен, повтор придёт с командой
            logging.debug("ack не доставлен", exc_info=True)


# --------------------------------------------------------------------------- #
# Основной цикл
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def execute_command(command: dict, browser: ManagedBrowser, cfg: dict) -> dict:
    kind = str(command.get("type") or "").lower()
    if kind in ("logout", "force_logout"):
        if cfg.get("dry_run"):
            logging.warning("DRY-RUN: разлогинил бы (команда %s, причина %s)", command.get("id"), command.get("reason"))
            return {"status": "dry_run"}
        logging.warning("Исполняю разлогин: команда %s, причина %s", command.get("id"), command.get("reason"))
        return browser.logout()
    if kind == "warn":
        if not cfg.get("warn_banner", True):
            return {"status": "skipped", "detail": "баннеры выключены в конфиге"}
        message = str(command.get("message") or "Долгий «Перезвон». Вернитесь к работе, иначе сессия будет закрыта.")
        seconds = int(command.get("seconds") or 30)
        ok = browser.show_banner(message, seconds)
        return {"status": "done" if ok else "no_window"}
    if kind in ("ping", "noop", ""):
        return {"status": "done"}
    return {"status": "unknown_command", "detail": kind}


def run_agent(cfg: dict) -> int:
    if cfg.get("single_instance", True) and not acquire_mutex(AGENT_MUTEX_NAME):
        return 0
    setup_logging(cfg, "agent.log")
    cleanup_old_binary()
    cfg = wait_for_valid_config(cfg)
    if cfg.get("_token_error"):
        logging.error("Токен агента не применён: %s. Задай токен латиницей и пересобери.",
                      cfg["_token_error"])
    logging.info("Режим AGENT. Сервер: %s | Oktell: %s", cfg.get("server_url"), cfg.get("oktell_url"))
    if cfg.get("dry_run"):
        logging.warning("DRY-RUN включён: команды разлогина будут только логироваться")

    cfg = fetch_server_config(cfg)
    cfg = wait_for_configuration(cfg)
    logging.info("Настройки применены. Oktell: %s | порог: %s с",
                 cfg.get("oktell_url"), (cfg.get("in_window_rule") or {}).get("threshold_s"))
    identity = current_identity(cfg)
    browser = ManagedBrowser(cfg)
    ledger = CommandLedger(app_dir() / "commands.json")
    link = ServerLink(cfg)

    browser_cfg = cfg.get("browser", {}) or {}
    if browser_cfg.get("launch_on_start", True):
        browser.ensure_running()
        # Фокус не забираем: агента мог перезапустить сторож посреди смены,
        # дёргать окно оператору в этот момент незачем — достаточно развернуть.
        browser.ensure_window_visible(bring_to_front=False)

    poll_s = float(cfg.get("poll_interval_s", 60))
    max_backoff = float(cfg.get("offline_max_backoff_s", 60))
    failures = 0
    last_command_report: Optional[dict] = None
    update_every_s = max(600.0, float(cfg.get("update_check_hours", 6)) * 3600.0)
    next_update_check = time.time()

    try:
        while True:
            try:
                if time.time() >= next_update_check:
                    next_update_check = time.time() + update_every_s
                    if check_for_update(cfg):
                        logging.info("Завершаюсь: работу продолжит обновлённая копия")
                        return 0

                # Взаимный сторож: агент поднимает watchdog, watchdog — агента.
                # Убить контроль можно только сняв обе копии в одном узком окне.
                if cfg.get("ensure_watchdog_alive", True) and not is_running_by_mutex(WATCHDOG_MUTEX_NAME):
                    logging.info("Watchdog не обнаружен — поднимаю")
                    spawn_self()

                if browser_cfg.get("keep_open", False):
                    browser.ensure_running()

                state = AgentState(identity=identity)
                state.browser = browser.probe() if browser.is_debug_port_alive() else {}
                unmanaged_cfg = cfg.get("unmanaged", {}) or {}
                if unmanaged_cfg.get("detect", True) and not state.browser.get("window"):
                    # Ищем чужое окно Oktell только когда своего нет: иначе наш
                    # же управляемый Chrome попадал бы в «обход».
                    state.unmanaged = match_unmanaged_titles(
                        list_window_titles(), unmanaged_cfg.get("window_title_patterns", [])
                    )
                state.last_command = last_command_report

                payload = build_heartbeat_payload(state, cfg, now_iso())
                data = link.heartbeat(payload)
                failures = 0

                if data:
                    server_interval = data.get("poll_interval_s")
                    if server_interval:
                        poll_s = max(2.0, min(300.0, float(server_interval)))
                    for command in data.get("commands") or []:
                        command_id = str(command.get("id") or "")
                        if not command_id or ledger.seen(command_id):
                            continue
                        report = execute_command(command, browser, cfg)
                        ledger.mark(command_id, str(report.get("status")))
                        last_command_report = {
                            "id": command_id,
                            "type": command.get("type"),
                            "status": report.get("status"),
                            "at": now_iso(),
                        }
                        link.ack(
                            {
                                "agent_id": identity.agent_id,
                                "command_id": command_id,
                                "status": report.get("status"),
                                "report": report,
                                "ts": now_iso(),
                            }
                        )
                        logging.info("Команда %s (%s) → %s", command_id, command.get("type"), report.get("status"))

                time.sleep(poll_s)
            except KeyboardInterrupt:
                logging.info("Агент остановлен с клавиатуры")
                return 0
            except Exception as exc:  # noqa: BLE001 — сеть/браузер не должны ронять агента
                failures += 1
                delay = backoff_delay(failures, poll_s, max_backoff)
                logging.warning("Цикл агента: ошибка (%s). Пауза %.0f c", exc, delay)
                logging.debug("Подробности", exc_info=True)
                time.sleep(delay)
    finally:
        release_mutex(AGENT_MUTEX_NAME)


def run_watchdog(cfg: dict) -> int:
    """Взаимный сторож: watchdog поднимает агента, агент — watchdog.

    Схема ровно как в MicroSIP DND Shield: чтобы остановить контроль, надо убить
    обе копии в одном узком окне, а полную остановку подстрахует Task Scheduler.
    """
    if not acquire_mutex(WATCHDOG_MUTEX_NAME):
        return 0
    setup_logging(cfg, "watchdog.log")
    logging.info("Режим WATCHDOG")
    check_s = max(0.5, float(cfg.get("watchdog_check_interval_s", 2)))
    grace_s = max(0.5, float(cfg.get("watchdog_spawn_grace_s", 3)))
    try:
        while True:
            try:
                if not is_running_by_mutex(AGENT_MUTEX_NAME):
                    logging.info("Агент не обнаружен — запускаю")
                    spawn_self("--agent")
                    time.sleep(grace_s)
                    if not is_running_by_mutex(AGENT_MUTEX_NAME):
                        logging.warning("Агент не поднялся после запуска")
                time.sleep(check_s)
            except KeyboardInterrupt:
                return 0
            except Exception:  # noqa: BLE001
                logging.exception("Ошибка цикла watchdog")
                time.sleep(check_s)
    finally:
        release_mutex(WATCHDOG_MUTEX_NAME)


def run_open(cfg: dict) -> int:
    """Ярлык «Oktell» на рабочем столе ведёт сюда: открыть управляемое окно."""
    setup_logging(cfg, "agent.log")
    if cfg.get("_config_error"):
        logging.error("Конфиг %s не читается (%s) — окно не открываю", cfg.get("_config_path"), cfg["_config_error"])
        return 2
    cfg = fetch_server_config(cfg)
    if not is_configured(cfg):
        logging.error("Нет настроек (адрес Oktell не задан) — окно не открываю")
        return 2
    browser = ManagedBrowser(cfg)
    ok = browser.ensure_running()
    if ok:
        browser.wait_for_page()
        browser.ensure_window_visible(bring_to_front=True)
    if ok and not browser.oktell_target():
        # Chrome уже был жив, но окна Oktell нет — открываем ещё одно.
        chrome = browser.chrome_path()
        if chrome:
            subprocess.Popen(browser.launch_args(chrome), close_fds=True)
    # Автозапуск watchdog: оператор открыл Oktell — контроль обязан быть поднят.
    if cfg.get("ensure_watchdog_alive", True) and not is_running_by_mutex(WATCHDOG_MUTEX_NAME):
        spawn_self()
    return 0 if ok else 1


def run_status(cfg: dict) -> int:
    browser = ManagedBrowser(cfg)
    identity = current_identity(cfg)
    state = AgentState(identity=identity)
    state.browser = browser.probe() if browser.is_debug_port_alive() else {}
    unmanaged_cfg = cfg.get("unmanaged", {}) or {}
    if unmanaged_cfg.get("detect", True) and not state.browser.get("window"):
        state.unmanaged = match_unmanaged_titles(list_window_titles(), unmanaged_cfg.get("window_title_patterns", []))
    payload = build_heartbeat_payload(state, cfg, now_iso())
    payload["_local"] = {
        "config": cfg.get("_config_path"),
        "config_error": cfg.get("_config_error"),
        "log_dir": str(app_dir()),
        "cdp_port": browser.devtools_port(),
        "agent_running": is_running_by_mutex(AGENT_MUTEX_NAME),
        "watchdog_running": is_running_by_mutex(WATCHDOG_MUTEX_NAME),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # В сборке --noconsole печатать некуда, поэтому дублируем в файл: на него
    # и смотрит check_install.bat.
    try:
        (app_dir() / "status.json").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if getattr(sys, "stdout", None):
        print(text)
    return 0


def run_logout_now(cfg: dict) -> int:
    setup_logging(cfg, "agent.log")
    if cfg.get("_config_error"):
        logging.error("Конфиг %s не читается (%s) — разлогин не выполняю", cfg.get("_config_path"), cfg["_config_error"])
        return 2
    browser = ManagedBrowser(cfg)
    report = browser.logout()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    logging.info("Ручной разлогин: %s", text)
    try:
        (app_dir() / "last_logout.json").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if getattr(sys, "stdout", None):
        print(text)
    return 0 if report.get("status") == "done" else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="OktellRecallGuard", add_help=True)
    parser.add_argument("--agent", action="store_true", help="рабочий цикл агента")
    parser.add_argument("--watchdog", action="store_true", help="сторож (режим по умолчанию)")
    parser.add_argument("--open", action="store_true", help="открыть управляемое окно Oktell")
    parser.add_argument("--logout-now", action="store_true", help="разлогинить прямо сейчас (проверка)")
    parser.add_argument("--status", action="store_true", help="состояние в JSON")
    parser.add_argument("--install", action="store_true", help="установить себя и запустить")
    parser.add_argument("--uninstall", action="store_true", help="удалить автозапуск, задачу и ярлык")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {VERSION}")
        return 0

    cfg = load_config(Path(args.config) if args.config else None)

    if args.install:
        return run_install(cfg)
    if args.uninstall:
        return run_uninstall(cfg)
    if args.agent:
        return run_agent(cfg)
    if args.open:
        return run_open(cfg)
    if args.logout_now:
        return run_logout_now(cfg)
    if args.status:
        return run_status(cfg)
    # Запуск без аргументов из папки «Загрузки» = сотрудник скачал файл с iCORE
    # и кликнул по нему. Ставим себя сами: никаких .bat и распаковок.
    if getattr(sys, "frozen", False) and not args.watchdog and not is_installed_copy():
        return run_install(cfg)
    return run_watchdog(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
