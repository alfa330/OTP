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
    OktellRecallGuard.exe --install --quiet -> установка без окон (скрипт IT)
    OktellRecallGuard.exe --deployed  -> вход пользователя на компьютере, куда
                                         программу поставил MSI (групповая политика)

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
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

APP_NAME = "Oktell Recall Guard"
# Как программа называется ДЛЯ ОПЕРАТОРА: так подписан ярлык на рабочем столе и
# пункт меню в iCORE. Внутреннее имя он нигде не видит, и в окне входа должно
# стоять то же слово, что на ярлыке, по которому он сюда попал.
APP_NAME_SHORT = "Oktell"
APP_DIR_NAME = "OktellRecallGuard"
VERSION = "1.0.26"

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
    "violations_path": "/api/oktell_guard/violations",
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
    # Как часто перечитывать настройки раздела (порог, обкатка, вкл/выкл).
    # Раньше они читались один раз за запуск процесса, и правка порога могла
    # не доехать до работающего агента за всю смену.
    "config_refresh_minutes": 10,

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
        # Окно открывает человек — ярлыком «Oktell» на рабочем столе. Агент его
        # не навязывает: ни при своём старте, ни после того, как окно закрыли.
        # Иначе программа спорит с оператором, а он этого не просил.
        "launch_on_start": False,
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
        # usFullbusy (числовой код 5) — состояние разговора в Oktell.
        "call_state_strings": ["fullbusy", "talk", "dial", "call", "ring"],
        "call_state_ids": [5],
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


def expand_env(value: str) -> str:
    """Раскрывает переменные окружения в пути из конфига.

    `os.path.expandvars` понимает `%VAR%` только на Windows, а конфиг пишется
    именно в этом синтаксисе (`%LOCALAPPDATA%\\OktellRecallGuard\\...`). На
    Linux и macOS — то есть в тестах и на CI — такой путь возвращался
    нераскрытым. Дораскрываем `%VAR%` сами, чтобы поведение не зависело от
    платформы; неизвестное имя, как и на Windows, остаётся как есть.
    """
    expanded = os.path.expandvars(value)
    return re.sub(r"%([^%\s]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), expanded)


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


# ─────────────────────────────────────────────────────────────────────────────
# Сессия оператора в iCORE
#
# Кто за машиной, программа спрашивает у самого человека — окном входа, как
# iCORE Phone (microsip-src/LoginDlg.cpp, iCoreAuth.cpp). До 1.0.17 это решалось
# личным токеном в ИМЕНИ скачанного файла, и схема держалась на том, что каждый
# скачает файл себе: на деле из 26 живых машин токен носили 7, остальные были
# безымянными — объявления им не показывались, а учётку кабинета отдавать было
# некому. Вход учёткой iCORE снимает это целиком: человек называет себя сам,
# сессию можно отозвать в «Сессиях», а файл в раздаче снова один на всех.
#
# Сессия хранится ТОЛЬКО на машине оператора, в профиле пользователя Windows.
# ─────────────────────────────────────────────────────────────────────────────

# Порог переспроса — тот же, что у телефона (kSessionMaxAgeSec): вошёл утром,
# смену не трогаем, назавтра окно снова. Access живёт ~30 минут и обновляется
# по refresh молча, так что за смену пароль не спрашивается ни разу.
SESSION_MAX_AGE_S = 12 * 60 * 60


def session_path() -> Path:
    return app_dir() / "session.json"


def _dpapi(data: bytes, unprotect: bool) -> Optional[bytes]:
    """CryptProtectData/CryptUnprotectData — привязка блоба к учётке Windows.

    В реестре у телефона токены лежат открытым текстом, но у него и токен уже
    SIP-пароль. Здесь в файле лежит refresh на 30 дней ко ВСЕМУ порталу, и
    отдавать его любому, кто прочитает профиль, незачем. DPAPI ничего не
    спрашивает у пользователя и не требует прав: ключ — сама учётка Windows.
    None — DPAPI недоступна (не Windows), тогда работаем открытым текстом.
    """
    if not IS_WINDOWS:
        return None

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                      ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = (ctypes.windll.crypt32.CryptUnprotectData if unprotect
          else ctypes.windll.crypt32.CryptProtectData)
    # CRYPTPROTECT_UI_FORBIDDEN: сборка идёт --noconsole, и всплывший запрос
    # DPAPI повис бы невидимым окном, а процесс — на нём.
    try:
        if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return None


def load_session() -> dict:
    """Сохранённая сессия оператора. Пустой словарь — входа не было."""
    try:
        raw = session_path().read_bytes()
    except Exception:  # noqa: BLE001
        return {}
    for candidate in (_dpapi(raw, unprotect=True), raw):
        if not candidate:
            continue
        try:
            data = json.loads(candidate.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    return {}


def save_session(session: dict) -> None:
    try:
        payload = json.dumps(session, ensure_ascii=False).encode("utf-8")
        session_path().write_bytes(_dpapi(payload, unprotect=False) or payload)
    except Exception:  # noqa: BLE001
        logging.debug("Сессия не сохранена", exc_info=True)


def clear_session() -> None:
    try:
        session_path().unlink()
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        logging.debug("Сессия не удалена", exc_info=True)


def session_is_fresh(session: dict, now: Optional[float] = None) -> bool:
    """Свежая ли сессия. Считаем от МОМЕНТА ВХОДА, а не от последнего запроса.

    Иначе порог не наступал бы никогда: агент ходит на сервер каждую минуту и
    сам бы продлевал себе сутки за сутками.
    """
    if not session.get("refresh_token"):
        return False
    try:
        saved_at = float(session.get("saved_at") or 0)
    except (TypeError, ValueError):
        return False
    age = (time.time() if now is None else now) - saved_at
    return 0 <= age < SESSION_MAX_AGE_S


# Ответы /api/login приходят по-английски: сам портал показывает их как есть, и
# оператор читает «Invalid credentials». Здесь так нельзя — окно входа человек
# видит раньше всего остального, и первое же сообщение не должно быть чужим
# языком. Незнакомый текст отдаём как есть: соврать про причину хуже, чем
# показать английскую строку, которую можно переслать в IT.
LOGIN_ERROR_TEXTS = {
    "invalid credentials": "Неправильный логин или пароль",
    "missing credentials": "Введите логин и пароль",
    "user account is inactive": "Учётная запись отключена. Обратитесь к руководителю",
    "too many login attempts. please try again later.":
        "Слишком много попыток входа. Подождите несколько минут",
    "internal server error": "Сервер не отвечает. Попробуйте ещё раз",
}


def login_error_text(message: str, status: int = 0) -> str:
    known = LOGIN_ERROR_TEXTS.get(str(message or "").strip().lower())
    if known:
        return known
    if not str(message or "").strip():
        return f"Ошибка входа (код {status})" if status else "Ошибка входа"
    return str(message)


def icore_login(cfg: dict, login: str, password: str) -> dict:
    """POST /api/login. Возвращает сессию либо {"error": "текст для человека"}.

    Синхронно и небыстро: сервер на Render просыпается из холодного старта до
    минуты, поэтому таймаут здесь свой, а не общий request_timeout_s, и звать
    это можно только из рабочего потока.
    """
    base = str(cfg.get("server_url") or "").rstrip("/")
    if not base:
        return {"error": "Не задан адрес сервера iCORE"}
    try:
        import requests

        response = requests.post(
            f"{base}/api/login",
            json={"login": login, "password": password, "auth_transport": "bearer"},
            timeout=float(cfg.get("login_timeout_s", 90)),
            verify=bool(cfg.get("verify_tls", True)),
            headers={"User-Agent": f"OktellRecallGuard/{VERSION}"},
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Вход в iCORE не удался: %s", exc)
        return {"error": "Нет связи с сервером iCORE"}

    try:
        data = response.json() or {}
    except Exception:  # noqa: BLE001
        data = {}
    if response.status_code == 200 and data.get("access_token"):
        user = data.get("user") or {}
        return {
            "access_token": str(data.get("access_token") or ""),
            "refresh_token": str(data.get("refresh_token") or ""),
            "user_id": user.get("id"),
            "user_name": str(user.get("name") or ""),
            "login": login,
            "saved_at": time.time(),
        }
    if data.get("error"):
        return {"error": login_error_text(str(data["error"]), response.status_code)}
    if response.status_code == 401:
        return {"error": "Неправильный логин или пароль"}
    return {"error": f"Ошибка входа (код {response.status_code})"}


def icore_refresh(cfg: dict, session: dict) -> dict:
    """Обновить access по refresh. {"error": ...} — сессию надо спрашивать заново."""
    refresh = str(session.get("refresh_token") or "")
    if not refresh:
        return {"error": "Сессии нет"}
    base = str(cfg.get("server_url") or "").rstrip("/")
    try:
        import requests

        response = requests.post(
            f"{base}/api/auth/refresh",
            json={"auth_transport": "bearer"},
            timeout=float(cfg.get("request_timeout_s", 10)),
            verify=bool(cfg.get("verify_tls", True)),
            headers={
                "X-Refresh-Token": refresh,
                "X-Auth-Transport": "bearer",
                "User-Agent": f"OktellRecallGuard/{VERSION}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Сеть — не истёкшая сессия: старую не трогаем, попробуем в следующий круг.
        logging.debug("Обновление сессии не удалось: %s", exc)
        return {"error": "Нет связи с сервером iCORE", "keep": True}

    try:
        data = response.json() or {}
    except Exception:  # noqa: BLE001
        data = {}
    if response.status_code == 200 and data.get("access_token"):
        updated = dict(session)
        updated["access_token"] = str(data["access_token"])
        # refresh ротируется — новый храним, старый после ротации уже мёртв.
        if data.get("refresh_token"):
            updated["refresh_token"] = str(data["refresh_token"])
        return updated
    if response.status_code == 401:
        return {"error": "Сессия истекла — нужен повторный вход"}
    # 5xx и прочее временное: сессия, скорее всего, жива.
    return {"error": f"Сессия не обновилась (код {response.status_code})", "keep": True}


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
    # нет, и работает именно вшитый. Человека этот токен не называет — кто за
    # машиной, говорит сессия iCORE (см. выше).
    if not str(cfg.get("agent_token") or "").strip():
        cfg["agent_token"] = build_token()
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
    # cabinet — учётка кабинета АТС этого сотрудника. Приходит только по личному
    # токену и только с сервера: в локальном конфиге её нет и быть не должно,
    # поэтому здесь она ПЕРЕЗАПИСЫВАЕТСЯ, а не сливается — отозвали доступ,
    # значит и в памяти агента её остаться не должно.
    allowed = ("oktell_url", "session_keys", "in_window_rule", "poll_interval_s", "dry_run",
               "unmanaged", "training_frame", "restore_frame", "news_poll_s")
    cfg["cabinet"] = remote.get("cabinet") or {}
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


def wait_for_configuration(cfg: dict, retry_s: float = 60.0,
                           session: Optional[dict] = None) -> dict:
    """Ждём настройки с сервера, ничего не открывая и никого не трогая."""
    while not is_configured(cfg):
        logging.error(
            "Нет настроек: адрес Oktell не задан (%s). Агент ждёт ответа сервера %s "
            "и пока ничего не открывает.",
            cfg.get("oktell_url"),
            cfg.get("server_url"),
        )
        time.sleep(retry_s)
        cfg = fetch_server_config(cfg, session=session)
    return cfg


def agent_headers(cfg: dict, session: Optional[dict] = None) -> dict:
    """Заголовки к нашим ручкам: замок машины плюс, если есть, имя человека.

    Два разных слоя, и путать их нельзя. `X-Agent-Token` вшит в сборку и один
    на всех — он только отсекает посторонние запросы из интернета, человека не
    называет. Кто за машиной, говорит `Authorization: Bearer` сессии iCORE.
    """
    headers = {
        "X-Agent-Token": str(cfg.get("agent_token") or ""),
        "User-Agent": f"OktellRecallGuard/{VERSION}",
    }
    access = str((session or {}).get("access_token") or "")
    if access and header_safe(access):
        headers["Authorization"] = f"Bearer {access}"
    return headers


def fetch_server_config(cfg: dict, login: str = "", session: Optional[dict] = None) -> dict:
    """Забрать настройки с сервера; при недоступности — из кэша.

    Смысл: сотрудник скачивает один exe и ничего не настраивает. Порог,
    адрес клиента и пин сертификата приезжают с сервера, а кэш нужен, чтобы
    агент пережил недоступность сервера и не остался без правила.

    `login` — SIP-номер оператора из открытой вкладки. Без него сервер отдаёт
    общее правило, и личный порог из раздела «Сотрудники» не применялся НИКОГДА:
    на старте процесса вкладки ещё нет, а другого места, где спрашивают
    настройки, не было. Поэтому логин передаём, как только он становится известен.

    `session` — сессия оператора в iCORE. Без неё сервер отдаёт только общие
    настройки: учётку кабинета он выдаёт лишь тому, кто назвал себя.
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
                params={"login": login} if login else None,
                timeout=float(cfg.get("request_timeout_s", 10)),
                verify=bool(cfg.get("verify_tls", True)),
                headers=agent_headers(cfg, session),
            )
            response.raise_for_status()
            remote = response.json()
            if isinstance(remote, dict):
                try:
                    # Учётку кабинета в открытый кэш НЕ кладём: это пароль от
                    # АТС, а кэш лежит простым файлом в профиле. Её место —
                    # session.json, он под DPAPI (см. save_session).
                    cached = {k: v for k, v in remote.items() if k != "cabinet"}
                    cached_config_path().write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
                    if session is not None and remote.get("cabinet"):
                        stored = load_session()
                        if stored:
                            stored["cabinet"] = remote["cabinet"]
                            save_session(stored)
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
                # Кабинет доклеиваем из сессии: в кэше его нет намеренно, а без
                # него молчащий сервер означал бы ещё и «войди в Oktell руками».
                saved_cabinet = (session or {}).get("cabinet") or load_session().get("cabinet")
                if saved_cabinet and not remote.get("cabinet"):
                    remote["cabinet"] = saved_cabinet
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


# Переменные, которые упаковщик выставляет ДЛЯ СЕБЯ. Дочернему процессу они
# смертельны: путь к сертификатам указывает во временную папку родителя, а она
# удаляется, когда родитель выходит. Установленный агент после этого падал на
# КАЖДОМ запросе с «Could not find a suitable TLS CA certificate bundle» —
# то есть молча переставал отчитываться.
# ВАЖНО: `_PYI_APPLICATION_HOME_DIR` здесь быть НЕ должно. Её ставит первая
# ступень загрузчика для второй, и если её вычистить, запуск падает с окном
# «_PYI_APPLICATION_HOME_DIR environment variable is not defined!».
# Вычищаем ровно то, из-за чего дочерний процесс брал файлы из чужой временной
# папки: путь распаковки (по нему certifi ищет сертификаты) и явные указания
# на файл сертификатов.
_PYINSTALLER_ENV_KEYS = (
    "_MEIPASS", "_MEIPASS2",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
)


def child_env() -> dict:
    """Окружение для дочернего процесса — без следов упаковщика."""
    env = dict(os.environ)
    for key in _PYINSTALLER_ENV_KEYS:
        env.pop(key, None)
    return env


def _self_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(program_path()), *args]
    return [sys.executable, str(program_path()), *args]


def spawn_self(*args: str) -> None:
    cmd = _self_command(*args)
    try:
        flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
        subprocess.Popen(cmd, cwd=str(program_path().parent), creationflags=flags,
                         close_fds=True, env=child_env())
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
    r"""Автозапуск через HKCU\...\Run.

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

    Каждая копия — ДВА процесса: загрузчик упаковщика и интерпретатор под ним.
    Поэтому исключаем и своего родителя (иначе копия убивала собственный
    загрузчик), а чужие гасим с интерпретаторов: загрузчик, дождавшись конца
    интерпретатора, сам удаляет папку распаковки _MEI и выходит. Убитый первым
    загрузчик оставлял её в %TEMP% — 14 МБ на каждую погашенную копию.
    """
    if not IS_WINDOWS:
        return
    script = (
        "$all=@(Get-CimInstance Win32_Process -Filter \"Name='" + INSTALLED_NAME + "'\" | "
        "Where-Object { $_.ExecutablePath -eq " + _ps_quote(target) + " -and $_.ProcessId -ne " + str(os.getpid()) +
        " -and $_.ProcessId -ne " + str(os.getppid()) + " });"
        # Кто загрузчик, а кто интерпретатор, видно по глубине в цепочке наших
        # процессов: корень — всегда загрузчик, под ним интерпретатор, под тем
        # (сторож запускает агента) снова загрузчик. «Родитель — тоже наша
        # копия» признаком не годится: у загрузчика агента он такой же.
        "$map=@{}; foreach($x in $all){ $map[[int]$x.ProcessId]=[int]$x.ParentProcessId };"
        "$py=@(); $boot=@();"
        "foreach($x in $all){ $d=0; $p=[int]$x.ParentProcessId;"
        " while($map.ContainsKey($p) -and $d -lt 64){ $d++; $p=$map[$p] };"
        " if($d % 2){ $py+=[int]$x.ProcessId } else { $boot+=[int]$x.ProcessId } };"
        "foreach($i in $py){ try { Stop-Process -Id $i -Force -ErrorAction Stop } catch {} };"
        # Загрузчику даём убрать за собой; добиваем только тех, кто не вышел.
        "if($boot.Count){ Wait-Process -Id $boot -Timeout 5 -ErrorAction SilentlyContinue };"
        "foreach($i in $boot){ try { Stop-Process -Id $i -Force -ErrorAction Stop } catch {} }"
    )
    _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=25)


def _ps_quote(value: Any) -> str:
    """Строка для PowerShell в одинарных кавычках.

    Путь идёт через профиль пользователя, а в имени бывает апостроф (O'Neil):
    без удвоения он обрывал команду, и ярлык молча не появлялся.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _remove_task() -> None:
    """Снять задачу-подстраховку.

    Она больше не заводится: возвращать программу по расписанию бессмысленно —
    если оператор закрыл её и ушёл в Oktell через свой браузер, воскресшая
    копия всё равно бессильна. Удаление оставлено, чтобы задача ушла у тех, кому
    её успели поставить.
    """
    _run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])


def desktop_dir() -> Path:
    r"""Рабочий стол так, как его видит Проводник.

    Папку перенаправляют (OneDrive, групповая политика), и тогда
    `%USERPROFILE%\Desktop` указывает мимо: ярлык создавался в одном месте, а
    искался и удалялся в другом.
    """
    if IS_WINDOWS:
        try:
            CSIDL_DESKTOPDIRECTORY = 0x0010
            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer) == 0 and buffer.value:
                return Path(buffer.value)
        except Exception:  # noqa: BLE001
            logging.debug("Рабочий стол не определён через Проводник", exc_info=True)
    return Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"


def shortcut_path() -> Path:
    return desktop_dir() / "Oktell.lnk"


def icon_path() -> Path:
    return app_dir() / "icore.ico"


def ensure_icon_file() -> Optional[Path]:
    """Разложить значок рядом с программой и вернуть путь к нему.

    Значок есть и внутри exe, но ярлык на него ссылаться НЕ должен. Проводник
    кэширует картинку по строке `<путь>,<индекс>`, а она у ярлыка не менялась с
    самой первой установки — значит на всех уже работающих машинах он так и
    показывал бы старую, сколько exe ни обновляй. Отдельный файл даёт новую
    строку, и картинка обновляется у всех разом. Заодно уходит гонка: при
    обновлении exe на секунды исчезает, и Проводник, спросив значок ровно в этот
    момент, запоминает замок «файл недоступен» — именно это и было видно.
    """
    if not IS_WINDOWS:
        return None
    target = icon_path()
    source = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "icore.ico"
    try:
        if not source.exists():
            return target if target.exists() else None
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            logging.info("Значок разложен: %s", target)
        return target
    except Exception:  # noqa: BLE001 — без значка программа работает, ярлык просто серый
        logging.debug("Значок разложить не удалось", exc_info=True)
        return target if target.exists() else None


def _create_shortcut(target: Path) -> bool:
    """Ярлык «Oktell» на рабочем столе: сотрудник открывает Oktell через него,
    и окно сразу управляемое."""
    if not IS_WINDOWS:
        return False
    icon = ensure_icon_file()
    icon_location = f"{icon},0" if icon else f"{target},0"
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(shortcut_path())});"
        f"$s.TargetPath={_ps_quote(target)};$s.Arguments='--open';"
        f"$s.IconLocation={_ps_quote(icon_location)};$s.Description='Oktell';$s.Save()"
    )
    return _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def _remove_shortcut() -> None:
    try:
        link = shortcut_path()
        if link.exists():
            link.unlink()
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


# ─────────────────────────────────────────────────────────────────────────────
# Окно входа
#
# Рисуем его страницей в том же Chromium, которым программа и так управляет, а
# не своим окном на Tk: у экрана входа iCORE есть готовый вид (src/App.jsx,
# ветка `if (!user)`), и человек должен видеть ровно его — он входит в тот же
# iCORE, что и в браузере.
#
# Почему это не опаснее своего окна:
#   * страница лежит ЛОКАЛЬНЫМ файлом в папке программы и открывается как
#     file:// — в неё нечего внедрить по сети, и она сама никуда не ходит;
#   * пароль страница НЕ отправляет: агент забирает его через CDP и сам стучится
#     на https://…/api/login. В сети запроса от страницы нет вовсе, токен в её
#     JS не попадает;
#   * забрав пару, агент тут же стирает её из страницы; в лог и на диск пароль
#     не попадает ни разу;
#   * окно Oktell живёт на https-origin и прочитать file://-страницу не может;
#   * порт отладки Chrome и так слушает петлю (127.0.0.1) и держит сессию
#     Oktell — окно входа новой двери не открывает.
# ─────────────────────────────────────────────────────────────────────────────

# Дольше десяти минут окно висеть не должно: процесс от ярлыка ждёт ответа, и
# забытая форма держала бы его до перезагрузки.
LOGIN_WINDOW_TIMEOUT_S = 600

# Значок окна входа: 32×32 PNG, снятый с icore.ico — того же файла, который
# PyInstaller вшивает в exe (см. build_exe.bat, --icon). Держим байтами, а не
# ссылкой на файл: страница открывается как file:// и не должна зависеть от
# того, что рядом с ней что-то лежит. Менять значок — менять icore.ico и
# пересобирать эту строку тем же способом.
LOGIN_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAHo0lEQVR42q1Xe7BVVRn/fWut/Thn"
    "n/uQuEDgOJYVmZNpE6MMmVRERGZFIsgzXmEUmOZM2kTQxFjOhI0hBgQJhkTonQE1hhShkECM4WHU"
    "xL2RDgSil8vjcM/ZZ++91vr6Y+977rlwcS7anllnztnnW9/3W99r/T7CZTwLNrxVeGn77puL54rD"
    "YmuvZYiAmPMAl4ioqBxnb2N9YcfLS2/f11ud1BuhL933x4+cONkxu1SpjEksrmYoGAYsALAFEUBE"
    "IJKQNkTOUX8tBO4v964Z3/yeAMx9dHP9zt3t8ztCPScmP290BJjk0jsZABFI+nAVIZDRioNPT/s2"
    "EdnLBjB82rqb2s5Gqyqcu07HIWANQDbbQBcbrn3NDCYJ1wsQyPCeA83TfnUpO6Knl8PG/+7OtvZo"
    "e5So61ApwrExHGYoZkhmSLbdF7JV/c1QbECJRhQmk97Jy+rCF58Z99sxpzv0em1A0obdjsYQqZez"
    "UxN6dkRVOVsotqLXAEZNXnfjydPltcZKEiYGVQ1nnwxQBoGEAkiCsv/ZGgAG4FTakoCjfOSQPPtO"
    "AERXiR1yT7d3rGbr5aSOIUGQDCgGJNKlIODKPHzpwYM+4VO8P694j4/ktZzQbb504TsBfCdAoDzk"
    "Udx0/YCmX/TKAzueenUeULiekhIUUXas1M2CASEUHCHgiOSFIOcvveGagX95+OEvnKtWzIObm1qP"
    "tA0JS+VPQEoZ5P29m9fP3LK7N2U49Z7tjS3/bGmJ4TXBRjXpTAAxBDlQwlQacuLuLRunr8H/8VEA"
    "cPLIkfFKFJqMLoNgwRAZCAZBwiEd9Wlwv/LshqlbL9fAAw88f0Vr69kJFhb9+nobly8fd/yiHEgq"
    "8V2wBoothBVQFlA2Lbu8cNHg4P53Y3z2lD8M2n/w+M4zZfnY6ZLz2JE3SvvH3/HEqG4hmDt3c9Oh"
    "fxz9tyanHtZk+U4gWAjpwUX54Itb59xwofLJE9aPKBYr34wTe6XvuX+vaxS/efLJCa/VyowetWxB"
    "zI0Lw/g8GIAUHnIUlgY2FW5c/fuJrQAgjv3r2Kcc8uqFMRDMENVmA7gkkXPUsguNj7ltxYJT7ZUX"
    "yxU5MdLq1o6K+m7bycqucV9bdVs3QWP7ktFQzPDYQJgIoLrgVHvpxzUh4H4SKnV55va06wlQEtp6"
    "IXZ2O/nta0bEFW9hFDFMUgLrEpL4DLRG0FGM186a2Pz+TlmHxRGHGY4FpLVQ1sAkFSTafnXejHX9"
    "AUC4QvqSAUlZvTMgmaGIIGHevuaDA4/WAiiVK1PBCtImcC3gsoRrCaRjSAoazrW339Ep+75C3R4y"
    "FZZgCBAkGNImcODVtb1VHgoAQhKEYkDazPVgCAYcBlxCOPyT18bdksbwB9jqtNdDZCEjSCYQE6DN"
    "4KoHVHKUbRRKIigWkCzSA0JBR3xVCsBwLNlCMaXLpkjJMkhz36c3/62xFoAn5WEfTmaUq/skEzwW"
    "8OG0dspKq5RikspmXs0uMmUBqW0aAqt1mCpAdaXJaOCSV8fnzw+uBVDwaaXiEG4VRNopPfIBUyxe"
    "2f+KDZ2yNoo/6gvPE7brJlWddki4ACDyjnOYTcgClLZcBiQLCMuQkOiI9NhaACuen7m74Nr7c8Ki"
    "IHPwhY/AKSAv4mKfnDP5oae+8WanbBiakZLdTG+N/jR4JwFAjBk86JBifdyFgGKGAqeNiAFrYrDG"
    "5AVjN1xVC2LVC7MW92lQQwMnfjRQleaCG/1s0IBgyLI/zazefItnbOljEzPZmiRNvprTK9YIJB8H"
    "ADV6yehoxqeXbTNwp8ScZHdvdgEbA0m5+hNvn30cQLcaX/LctFcAvHKpLtjy+htLFOebtClDwgJE"
    "1conXdaNfYJ91Vacd/3VkhMIcBZ/VFeSVMDa+/K8z65a27K5xetNC/7eyNWLdexNiJMQMmtuwjKI"
    "GR45UIR9P984taUbubt72NJdGsHQWFcy0lHLdAh5mYejKnv6Nfg/nP/clG09Gf7J19cPaT9dXJRo"
    "d2RJRyCYWqIIQCBQOfhuPOeR7bN+3Q3Ag59bcUuxLHeEVgNsQNS1kUEAE1zlQlACKWi35zp/dpR4"
    "ky1zzGiK42hoounzAq6IdeVikkaAQx4ElY/e/KEPf2zK2i+WLqK3992y9BHohns79HlkrB+cAREW"
    "sCAIElBCQZIEKPUOQDBsoG3URd0428ucxV8gr1wUcmbsom0znumRFU+YdNMPBHdsC2QB0hKUFZCW"
    "IG0qqMAgNrAmQqLLSJJ06aQDrENIayFtFnMAMss9wUC9zMMVlcdrjfc4Fywfu6HhP/9t38S2cGtJ"
    "V0BIAMqygvmijdyDppQ1U5WcFlQeUpaaH9o5+84Lh5QeB5Nd9+7KbdlzaKXR7oTIGCQcdxnMvhD3"
    "TMW7JiSGEi5ywoF0Kk8s3PmtWURkLms0WzRs5fQwpp9KuAMrJoLmBJwxVWJUKXk3uwAkKfjCg6Hw"
    "VN735v/o5WnL3vVw2jypud/h1898J4z1FGG9qwEJwxqWGTYzT1lyShIQMLCI2lxHrhvYv27x9E13"
    "HXvP0zEAHPj+gWDbq/uHh3E8whrz8cTwIGY0AoaIRIcQ8rgSqjXwnJcGD+i3dfQzo9t6o/d/fGK5"
    "X/XLJVoAAAAASUVORK5CYII="
)

LOGIN_PAGE_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<!-- Значок в заголовке окна: тот же, что у iCORE Phone и у самого exe
     (icore.ico). Без него Chrome рисует глобус, и окно входа выглядит как
     случайная страница из интернета, а не как наша программа. Вшит прямо в
     страницу: она открывается как file:// и ходить за файлами не должна. -->
<link rel="icon" href="data:image/png;base64,__ICON__">
<style>
  :root {
    --indigo-700: #4338CA; --indigo-600: #4F46E5; --indigo-500: #6366F1;
    --slate-100: #F1F5F9; --slate-400: #94A3B8; --slate-500: #64748B;
    --slate-900: #0F172A; --red-50: #FEF2F2; --red-600: #DC2626;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; align-items: center; justify-content: center; padding: 16px;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: linear-gradient(to bottom right, #DBEAFE, #F3E8FF);
    -webkit-user-select: none; user-select: none;
  }
  .card {
    width: 100%; max-width: 380px; padding: 28px 24px; border-radius: 28px;
    background: rgba(255, 255, 255, 0.95);
    box-shadow: 0 24px 60px rgba(15, 23, 42, 0.16);
    outline: 1px solid rgba(15, 23, 42, 0.05);
  }
  .brand { display: flex; align-items: center; justify-content: center; margin: 0 0 6px; }
  .brand-word {
    display: flex; align-items: stretch; font-size: 36px; font-weight: 800; line-height: 1;
  }
  .brand-word .word {
    padding: 8px 12px; color: #fff; background: var(--indigo-700);
    border: 1px solid var(--indigo-700); border-radius: 16px 0 0 16px;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
  }
  .brand-word .mark {
    display: grid; place-items: center; padding: 8px; margin-left: -1px; background: #fff;
    border: 1px solid var(--indigo-700); border-left: none; border-radius: 0 16px 16px 0;
  }
  .brand-word .mark svg { width: 44px; height: 44px; color: var(--indigo-600); }
  .subtitle { margin: 0 0 22px; text-align: center; font-size: 13px; color: var(--slate-500); }
  .field { position: relative; display: block; margin-top: 10px; }
  .field:first-of-type { margin-top: 0; }
  .field .glyph {
    position: absolute; left: 16px; top: 50%; transform: translateY(-50%);
    width: 16px; height: 16px; color: var(--slate-400); pointer-events: none;
  }
  .field input {
    width: 100%; height: 48px; padding: 0 16px 0 44px; border: none; outline: none;
    border-radius: 14px; background: var(--slate-100); color: var(--slate-900);
    font-size: 16px; font-family: inherit; transition: background .15s, box-shadow .15s;
    -webkit-user-select: text; user-select: text;
  }
  .field input::placeholder { color: var(--slate-400); }
  .field input:focus { background: #fff; box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.7); }
  .field input:disabled { opacity: .6; }
  #pass { padding-right: 48px; }
  .eye {
    position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
    display: grid; place-items: center; width: 36px; height: 36px; padding: 0;
    border: none; border-radius: 999px; background: transparent; cursor: pointer;
    color: var(--slate-400); transition: background .15s, color .15s;
  }
  .eye:hover { background: rgba(226, 232, 240, 0.7); color: #475569; }
  .eye svg { width: 16px; height: 16px; }
  .slot { display: flex; align-items: center; justify-content: center; min-height: 60px; }
  .slot p {
    width: 100%; margin: 0; padding: 8px 12px; border-radius: 12px;
    background: var(--red-50); color: var(--red-600);
    font-size: 13px; font-weight: 500; text-align: center;
  }
  .slot p.calm { background: transparent; color: var(--slate-500); font-weight: 400; }
  button.submit {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    width: 100%; height: 48px; border: none; border-radius: 14px;
    background: var(--indigo-600); color: #fff; font-size: 15px; font-weight: 600;
    font-family: inherit; cursor: pointer; transition: background .15s, transform .1s;
  }
  button.submit:hover:not(:disabled) { background: var(--indigo-700); }
  button.submit:active:not(:disabled) { transform: scale(.99); }
  button.submit:disabled { opacity: .6; cursor: not-allowed; }
  .spinner {
    width: 15px; height: 15px; border: 2px solid rgba(255, 255, 255, .4);
    border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .version { margin: 14px 0 0; text-align: center; font-size: 12px; color: var(--slate-400); }
  /* «обновить» рядом с версией, а не кнопкой: обновляются раз в жизни, когда
     версия сломана, и полноразмерная кнопка спорила бы за внимание с «Войти». */
  .linkish {
    padding: 0; border: none; background: none; font: inherit; color: var(--indigo-600);
    cursor: pointer; text-decoration: underline; text-underline-offset: 2px;
  }
  .linkish:disabled { color: var(--slate-400); cursor: default; text-decoration: none; }
</style>
</head>
<body>
  <main class="card">
    <h1 class="brand">
      <span class="brand-word">
        <span class="word">iCORE</span>
        <span class="mark">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 389 389" aria-hidden="true">
            <path fill="currentColor" d="M49 193 C49.04 185.71 49.82 178.36 50.92 171.14 C52.02 163.92 53.52 156.69 55.6 149.7 C57.69 142.71 60.32 135.79 63.43 129.18 C66.54 122.58 70.21 116.16 74.27 110.1 C78.33 104.03 82.88 98.18 87.79 92.79 C92.7 87.39 98.15 82.4 103.76 77.72 C109.36 73.05 115.25 68.59 121.43 64.72 C127.61 60.84 134.15 57.41 140.83 54.46 C147.5 51.51 154.44 48.94 161.47 47.01 C168.5 45.08 175.78 43.74 183.03 42.91 C190.27 42.07 197.66 41.74 204.95 42 C212.23 42.26 219.61 43 226.74 44.48 C233.87 45.97 241.1 47.95 247.72 50.92 C254.33 53.9 261.33 57.42 266.43 62.33 C271.53 67.23 276.72 73.77 278.31 80.35 C279.89 86.93 278.88 95.7 275.95 101.83 C273.02 107.96 266.8 113.86 260.73 117.13 C254.66 120.39 246.64 121.51 239.54 121.42 C232.44 121.32 225.33 117.77 218.14 116.56 C210.94 115.35 203.58 113.9 196.35 114.18 C189.13 114.46 181.69 115.99 174.8 118.24 C167.92 120.49 161.04 123.67 155.03 127.68 C149.03 131.69 143.43 136.77 138.77 142.3 C134.12 147.83 130.06 154.24 127.12 160.85 C124.17 167.45 122.12 174.77 121.1 181.93 C120.08 189.08 120.1 196.61 121.02 203.78 C121.93 210.95 123.77 218.31 126.6 224.95 C129.42 231.6 133.38 238.08 137.97 243.66 C142.55 249.23 148.11 254.38 154.1 258.4 C160.08 262.42 166.98 265.68 173.89 267.78 C180.79 269.88 188.32 270.95 195.54 271 C202.77 271.05 210.31 270.07 217.24 268.06 C224.17 266.05 231.12 262.89 237.13 258.91 C243.14 254.94 248.76 249.81 253.3 244.22 C257.84 238.63 261.75 232.07 264.37 225.37 C266.98 218.67 268.48 211.23 269 204.02 C269.52 196.82 268.17 189.41 267.49 182.16 C266.81 174.92 263.41 167.14 264.92 160.56 C266.44 153.97 271.11 146.05 276.57 142.64 C282.03 139.23 291.22 138.39 297.67 140.08 C304.12 141.77 310.49 147.5 315.25 152.77 C320.01 158.03 323.49 164.98 326.25 171.67 C329 178.36 330.66 185.7 331.78 192.89 C332.91 200.08 333.26 207.52 332.99 214.8 C332.73 222.08 331.72 229.43 330.18 236.56 C328.65 243.68 326.49 250.78 323.78 257.54 C321.06 264.31 317.74 270.94 313.91 277.14 C310.07 283.33 305.56 289.21 300.79 294.71 C296.01 300.22 290.83 305.47 285.26 310.17 C279.69 314.86 273.61 319.12 267.36 322.87 C261.1 326.63 254.5 329.99 247.73 332.7 C240.96 335.41 233.87 337.59 226.75 339.14 C219.63 340.69 212.29 341.56 205.01 342 C197.72 342.44 190.3 342.44 183.04 341.79 C175.77 341.14 168.47 339.88 161.39 338.09 C154.32 336.29 147.32 333.87 140.59 331.03 C133.86 328.2 127.27 324.86 121.02 321.09 C114.76 317.33 108.73 313.06 103.06 308.45 C97.4 303.85 91.92 298.87 87.02 293.47 C82.13 288.06 77.7 282.13 73.69 276.03 C69.68 269.94 66.02 263.51 62.95 256.89 C59.88 250.28 57.3 243.35 55.25 236.34 C53.21 229.34 51.7 222.1 50.66 214.88 C49.62 207.65 48.96 200.29 49 193Z"/>
            <ellipse cx="199.15" cy="195.32" rx="39.43" ry="40.41" fill="currentColor"/>
          </svg>
        </span>
      </span>
    </h1>
    <p class="subtitle">__SUBTITLE__</p>

    <form id="form" autocomplete="off">
      <label class="field">
        <svg class="glyph" viewBox="0 0 448 512" aria-hidden="true"><path fill="currentColor" d="M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3 0 498.7 13.3 512 29.7 512H418.3c16.4 0 29.7-13.3 29.7-29.7 0-98.5-79.8-178.3-178.3-178.3H178.3z"/></svg>
        <input id="login" type="text" placeholder="Логин" value="__PREFILL__"
               autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false">
      </label>
      <label class="field">
        <svg class="glyph" viewBox="0 0 448 512" aria-hidden="true"><path fill="currentColor" d="M144 144v48h160v-48c0-44.2-35.8-80-80-80s-80 35.8-80 80zM80 192v-48C80 64.5 144.5 0 224 0s144 64.5 144 144v48h16c35.3 0 64 28.7 64 64v192c0 35.3-28.7 64-64 64H64c-35.3 0-64-28.7-64-64V256c0-35.3 28.7-64 64-64h16z"/></svg>
        <input id="pass" type="password" placeholder="Пароль" autocomplete="off" enterkeyhint="go">
        <button id="eye" class="eye" type="button" tabindex="-1" aria-label="Показать пароль">
          <svg viewBox="0 0 576 512" aria-hidden="true"><path fill="currentColor" d="M288 32c-80.8 0-145.5 36.8-192.6 80.6C48.6 156 17.3 208 2.5 243.7c-3.3 7.9-3.3 16.7 0 24.6C17.3 304 48.6 356 95.4 399.4 142.5 443.2 207.2 480 288 480s145.5-36.8 192.6-80.6c46.8-43.5 78.1-95.4 93-131.1 3.3-7.9 3.3-16.7 0-24.6-14.9-35.7-46.2-87.7-93-131.1C433.5 68.8 368.8 32 288 32zM144 256a144 144 0 1 1 288 0 144 144 0 1 1-288 0zm144-64c0 35.3-28.7 64-64 64-7.1 0-13.9-1.2-20.2-3.3-5.5-1.8-11.9 1.6-11.7 7.4.3 6.9 1.3 13.8 3.2 20.7 13.7 51.2 66.4 81.6 117.6 67.9s81.6-66.4 67.9-117.6c-11.1-41.5-47.8-69.4-88.6-71.1-5.8-.2-9.2 6.1-7.4 11.7 2.1 6.3 3.3 13.1 3.3 20.2z"/></svg>
        </button>
      </label>

      <div class="slot"><p id="status" class="calm" hidden></p></div>

      <button id="submit" class="submit" type="submit">
        <span id="spinner" class="spinner" hidden></span>
        <span id="submit-text">Войти</span>
      </button>
    </form>
    <p class="version">
      версия __VERSION__ · <button id="update" class="linkish" type="button">обновить</button>
    </p>
  </main>

<script>
(function () {
  // Единственный канал наружу. Пароль здесь и остаётся: страница его никуда не
  // шлёт — забирает агент через CDP и стирает эту пару сразу же.
  window.__guardLogin = { pending: null, closing: false, update: false };

  var form = document.getElementById('form');
  var login = document.getElementById('login');
  var pass = document.getElementById('pass');
  var eye = document.getElementById('eye');
  var submit = document.getElementById('submit');
  var submitText = document.getElementById('submit-text');
  var spinner = document.getElementById('spinner');
  var status = document.getElementById('status');

  function say(text, isError) {
    if (!text) { status.hidden = true; status.textContent = ''; return; }
    status.hidden = false;
    status.textContent = text;
    status.className = isError ? '' : 'calm';
  }

  function busy(on) {
    login.disabled = on;
    pass.disabled = on;
    submit.disabled = on;
    spinner.hidden = !on;
    submitText.textContent = on ? 'Вход…' : 'Войти';
  }

  // Агент зовёт это, когда сервер ответил.
  window.__guardSay = function (text, isError) { busy(false); say(text, !!isError); };
  window.__guardBusy = function (text) { busy(true); say(text || '', false); };

  // Обновление по требованию. Само оно приходит при старте и раз в 6 часов —
  // не хватает этого ровно тогда, когда версия сломана и ждать полдня нечем.
  // Забирает нажатие агент, как и вход: страница на сервер не ходит.
  var updateButton = document.getElementById('update');
  updateButton.addEventListener('click', function () {
    if (updateButton.disabled) { return; }
    updateButton.disabled = true;
    updateButton.textContent = 'проверяем…';
    say('Проверяем обновление…', false);
    window.__guardLogin.update = true;
  });
  window.__guardUpdateDone = function (text, isError) {
    updateButton.disabled = false;
    updateButton.textContent = 'обновить';
    say(text || '', !!isError);
  };

  eye.addEventListener('click', function () {
    var shown = pass.type === 'text';
    pass.type = shown ? 'password' : 'text';
    eye.setAttribute('aria-label', shown ? 'Показать пароль' : 'Скрыть пароль');
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (submit.disabled) { return; }
    var user = login.value.trim();
    var secret = pass.value;
    if (!user || !secret) { say('Введите логин и пароль', true); return; }
    busy(true);
    say('Вход…', false);
    window.__guardLogin.pending = { login: user, password: secret };
  });

  (login.value ? pass : login).focus();
})();
</script>
</body>
</html>
"""


def login_page_path() -> Path:
    return app_dir() / "login.html"


def build_login_html(prefill: str = "") -> str:
    """Страница входа с подставленным логином.

    Значение экранируем сами: логин приходит из прошлой сессии, то есть с
    сервера, и вставлять его в разметку как есть нельзя ни при каких «да кто
    туда напишет кавычку».
    """
    safe = (str(prefill or "")
            .replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))
    return (LOGIN_PAGE_HTML
            .replace("__ICON__", LOGIN_ICON_B64)
            .replace("__TITLE__", LOGIN_WINDOW_TITLE)
            .replace("__SUBTITLE__", f"Вход в {APP_NAME_SHORT}")
            .replace("__PREFILL__", safe)
            .replace("__VERSION__", VERSION))


def _login_target(browser: "ManagedBrowser", page_url: str) -> Optional[dict]:
    needle = page_url.rsplit("/", 1)[-1].lower()
    for target in browser.targets():
        if str(target.get("type") or "") != "page":
            continue
        if needle in str(target.get("url") or "").lower():
            return target
    return None


def _close_target(browser: "ManagedBrowser", target_id: str) -> None:
    port = browser.devtools_port()
    if not port or not target_id:
        return
    try:
        import requests

        requests.get(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=5)
    except Exception:  # noqa: BLE001
        logging.debug("Окно входа не закрылось само", exc_info=True)


def _set_window_bounds(page: "CdpPage", bounds: dict) -> None:
    """Задать окну размер/состояние через CDP.

    Флаги вроде --window-size и --kiosk действуют, только когда Chrome стартует
    ХОЛОДНЫМ. У оператора он уже запущен, новый вызов просто передаёт окно
    живому процессу и выбрасывает флаги — молча.
    """
    try:
        window = page.call("Browser.getWindowForTarget")
        window_id = (window or {}).get("windowId")
        if window_id is None:
            return
        page.call("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
    except Exception:  # noqa: BLE001
        logging.debug("Размер окна не задан", exc_info=True)


def _take_update_request(page: "CdpPage") -> bool:
    """Нажали ли «обновить». Флаг снимаем сразу — иначе сработает по кругу."""
    try:
        return bool(page.evaluate(
            "(function(){var s=window.__guardLogin;"
            "if(!s||!s.update){return false;}s.update=false;return true;})()"))
    except Exception:  # noqa: BLE001
        return False


def _run_update_from_window(cfg: dict, page: "CdpPage") -> None:
    """Обновиться по нажатию в окне входа и ответить человеку в это же окно.

    Своего окна с сообщением тут не показываем: оно вылезло бы ПОВЕРХ окна
    входа, которое и так на экране, — человек получил бы два окна там, где
    хватает одной строки.
    """
    code = run_update_now(cfg, quiet=True)
    texts = {
        0: ("Обновились. Закройте это окно и откройте Oktell заново.", False),
        1: (f"У вас последняя версия — {VERSION}.", False),
        2: ("Обновиться не вышло. Проверьте интернет или сообщите в IT.", True),
    }
    text, is_error = texts.get(code, texts[2])
    try:
        page.evaluate("window.__guardUpdateDone(%s, %s)"
                      % (json.dumps(text, ensure_ascii=False),
                         "true" if is_error else "false"))
    except Exception:  # noqa: BLE001 — окно могли закрыть, пока мы качали
        logging.debug("Итог обновления показать некому", exc_info=True)


def run_login_window(cfg: dict, prefill: str = "") -> dict:
    """Спросить учётку iCORE. Возвращает сессию либо {} — человек закрыл окно.

    Закрытие — законный ответ «не буду», а не ошибка: выше по стеку оно значит
    «Oktell не открываем».
    """
    browser = ManagedBrowser(cfg, heal=False)
    chrome = browser.chrome_path()
    if not chrome:
        logging.error("Не найден ни Chrome, ни Edge — окно входа показать нечем")
        show_message("Не найден браузер Chrome. Сообщите в IT.", error=True)
        return {}

    page_file = login_page_path()
    try:
        # Пишем страницу заново каждый раз: файл лежит в профиле пользователя, и
        # опираться на то, что вчерашний остался нашим, незачем.
        page_file.parent.mkdir(parents=True, exist_ok=True)
        page_file.write_text(build_login_html(prefill), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logging.exception("Страница входа не записалась")
        return {}

    page_url = page_file.resolve().as_uri()
    args = [
        str(chrome),
        f"--user-data-dir={browser.profile_dir}",
        f"--remote-debugging-port={int(browser.browser_cfg.get('cdp_port', 0) or 0)}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,ChromeWhatsNewUI",
        "--window-size=420,620",
        f"--app={page_url}",
    ]
    browser.profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(args, creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
                         close_fds=True, env=child_env())
    except Exception:  # noqa: BLE001
        logging.exception("Окно входа не запустилось")
        return {}
    logging.info("Показываю окно входа")

    deadline = time.time() + LOGIN_WINDOW_TIMEOUT_S
    page = None
    target_id = ""
    try:
        while time.time() < deadline:
            target = _login_target(browser, page_url)
            if not target:
                if page is not None:
                    # Окно было и исчезло — человек его закрыл.
                    logging.info("Окно входа закрыто без входа")
                    return {}
                time.sleep(0.4)
                continue
            if page is None or not getattr(page, "connected", False):
                try:
                    page = CdpPage(str(target["webSocketDebuggerUrl"]),
                                   timeout=float(cfg.get("request_timeout_s", 10)))
                    target_id = str(target.get("id") or "")
                except Exception:  # noqa: BLE001
                    time.sleep(0.4)
                    continue
                # Размер задаём по отладочному соединению, а не флагом: живому
                # Chrome (а он у оператора запущен — там открыт клиент Oktell)
                # командная строка не доезжает, и окно выходило во весь экран.
                _set_window_bounds(page, {"width": 420, "height": 620})

            try:
                pending = page.evaluate(
                    "(function(){var s=window.__guardLogin;"
                    "if(!s||!s.pending){return null;}"
                    "var v=s.pending;s.pending=null;return v;})()"
                )
            except Exception:  # noqa: BLE001 — окно могли закрыть прямо сейчас
                page = None
                time.sleep(0.4)
                continue

            if not pending:
                if _take_update_request(page):
                    _run_update_from_window(cfg, page)
                time.sleep(0.3)
                continue

            session = icore_login(cfg, str(pending.get("login") or ""),
                                  str(pending.get("password") or ""))
            # Пара больше не нужна нигде: в странице её уже стёрли, здесь —
            # выходит из области видимости вместе с pending.
            pending = None
            if session.get("error"):
                try:
                    page.evaluate("window.__guardSay(%s, true)"
                                  % json.dumps(str(session["error"]), ensure_ascii=False))
                except Exception:  # noqa: BLE001
                    page = None
                continue
            _close_target(browser, target_id)
            return session
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            page_file.unlink()
        except Exception:  # noqa: BLE001
            pass

    logging.info("Окно входа: ответа не дождались")
    _close_target(browser, target_id)
    return {}


def ensure_session(cfg: dict, ask: bool = True) -> dict:
    """Живая сессия оператора. {} — входа нет (и спросить было нельзя или отказались).

    Порядок важен: сначала свежесть по времени входа, потом обновление access.
    Просроченную по 12 часам сессию обновлять НЕЛЬЗЯ — refresh живёт 30 дней и
    молча продлевал бы вход до бесконечности, а порог для того и заведён.
    """
    session = load_session()
    if session_is_fresh(session):
        refreshed = icore_refresh(cfg, session)
        if not refreshed.get("error"):
            save_session(refreshed)
            return refreshed
        if refreshed.get("keep"):
            # Сервер молчит — со старым access ещё можно попробовать поработать.
            return session
        clear_session()
        session = {}

    if not ask:
        return {}
    fresh = run_login_window(cfg, prefill=str(session.get("login") or load_session().get("login") or ""))
    if not fresh:
        return {}
    save_session(fresh)
    logging.info("Вход выполнен: %s", fresh.get("user_name") or fresh.get("login") or "оператор")
    return fresh


def run_install(cfg: dict, start: bool = True, quiet: bool = False) -> int:
    """Разложить себя по местам. Ровно то, что делали install_*.bat.

    Скачанная копия делает МИНИМУМ: копирует себя и передаёт установку уже
    установленной копии, после чего немедленно завершается. Так её одноразовая
    папка распаковки живёт секунды и успевает удалиться — иначе упаковщик
    показывал пугающее «Failed to remove temporary directory».

    quiet — установка скриптом IT: окно с итогом закрыть некому, и процесс
    висел бы на нём до перезагрузки. Итог в этом случае только в install.log.
    """
    setup_logging(cfg, "install.log")
    target = installed_path()
    source = program_path()

    def tell(text: str, error: bool = False) -> None:
        (logging.error if error else logging.info)("%s", " ".join(text.split()))
        if not quiet:
            show_message(text, error=error)

    if not getattr(sys, "frozen", False):
        logging.error("Установка имеет смысл только для собранного exe")
        return 2

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            installed_version = file_version(target) if target.exists() else ""
            if installed_version and _version_key(installed_version) > _version_key(VERSION):
                # Обновление только вверх и здесь: давно скачанный файл,
                # запущенный повторно, откатил бы обновившуюся программу.
                logging.info("Установлена версия %s новее этой (%s) — файл не заменяю",
                             installed_version, VERSION)
            else:
                # Работающую копию нельзя перезаписать — сначала гасим её (только её).
                _stop_installed_copies(target)
                time.sleep(1.5)
                shutil.copy2(source, target)
                logging.info("Скопирован в %s", target)
            # Дальше всё делает установленная копия: автозапуск, ярлык, окно с
            # результатом. Мы уходим сразу и ничего не держим.
            try:
                flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
                subprocess.Popen([str(target), "--install", *(["--quiet"] if quiet else [])],
                                 cwd=str(target.parent), creationflags=flags, close_fds=True,
                                 env=child_env())
                return 0
            except Exception:  # noqa: BLE001
                logging.exception("Не удалось передать установку установленной копии")
                # Не смогли передать — доделываем сами, окно всё равно нужнее
                # аккуратного выхода.
        # Конфиг рядом со скачанным exe (если его положили) переносим тоже.
        local_cfg = source.parent / "config.json"
        if local_cfg.exists() and local_cfg.resolve() != (target.parent / "config.json").resolve():
            shutil.copy2(local_cfg, target.parent / "config.json")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Не удалось скопировать себя в %s", target)
        tell(
            f"Не удалось установить программу.\n\n{exc}\n\nПодробности: {app_dir() / 'install.log'}",
            error=True,
        )
        return 1

    ok_run = _register_autostart(target)
    _remove_task()   # у прежних установок она есть — снимаем
    ok_link = _create_shortcut(target)
    logging.info("Автозапуск: %s | ярлык: %s", ok_run, ok_link)

    if ok_run:
        tell(
            "Программа установлена и уже работает.\n\n"
            "Она будет запускаться сама при входе в Windows.\n"
            "На рабочем столе появился ярлык «Oktell» — открывайте Oktell через него.\n\n"
            "Ничего настраивать не нужно."
        )
    else:
        tell(
            "Программа скопирована, но не смогла прописаться в автозапуск.\n"
            f"Подробности: {app_dir() / 'install.log'}",
            error=True,
        )

    if not start:
        return 0 if ok_run else 1

    # Дальше НЕ завершаемся, а сами становимся сторожем. Раньше здесь
    # запускалась ещё одна копия, а эта выходила сразу после нажатия OK — и
    # упиралась в антивирус, уже державший её папку распаковки: пользователь
    # видел «Failed to remove temporary directory». Долгоживущий процесс такой
    # гонки не создаёт: он держит папку всё время работы и убирает при выходе.
    logging.info("Установка завершена, продолжаю работу сторожем")
    return run_watchdog(cfg)


def run_uninstall(cfg: dict) -> int:
    setup_logging(cfg, "install.log")
    _remove_autostart()
    _remove_task()
    _remove_shortcut()
    _remove_deployment_marker()
    logging.info("Удалено: автозапуск, задача, ярлык")
    # Гасим последними и не себя: иначе не дописали бы лог.
    _stop_installed_copies(installed_path())
    return 0


# --------------------------------------------------------------------------- #
# Установка на весь компьютер: MSI через групповую политику
# --------------------------------------------------------------------------- #
#
# MSI кладёт программу в Program Files и пишет в HKLM\...\Run запуск с
# --deployed. Работать прямо оттуда агент не может: обычный пользователь не
# пишет в Program Files, и автообновление не заменило бы файл. Поэтому при входе
# каждого пользователя машинная копия кладёт ему рабочую в %LOCALAPPDATA% —
# только если там нет такой же или новее — и запускает её. Дальше всё как после
# ручной установки, включая автообновление. Так устроен и машинный установщик
# Teams.
#
# Файлы, разложенные групповой политикой, не несут пометки «скачано из
# интернета», и SmartScreen такой путь не проверяет вовсе.

DEPLOYED_ARG = "--deployed"


def deployment_marker_path() -> Path:
    return app_dir() / "deployment.json"


def read_deployment_marker() -> dict:
    try:
        data = json.loads(deployment_marker_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — нет метки = ручная установка
        return {}


def _remove_deployment_marker() -> None:
    try:
        deployment_marker_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logging.debug("Метка установки на компьютер не удалена", exc_info=True)


def file_version(path: Path) -> str:
    """Версия из ресурса exe («1.0.16.0»). Пусто — ресурса нет или файл не читается.

    Ресурс версии появился в 1.0.16: у прежних сборок его нет, и пустая строка
    для них честно означает «старее».
    """
    if not IS_WINDOWS:
        return ""

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
            ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32),
        ]

    try:
        api = ctypes.windll.version
        size = api.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        block = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(path), 0, size, block):
            return ""
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not api.VerQueryValueW(block, "\\", ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value:
            return ""
        info = VS_FIXEDFILEINFO.from_address(pointer.value)
        if info.dwSignature != 0xFEEF04BD:
            return ""
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001
        logging.debug("Версия файла %s не прочитана", path, exc_info=True)
        return ""


def _version_key(text: str) -> tuple:
    """«1.0.16» и «1.0.16.0» — одна версия, а кортежи разной длины так не считают."""
    parts = list(parse_version(text))
    return tuple(parts + [0] * (4 - len(parts)))


def machine_logon_plan(own_version: str, installed_version: str, installed_exists: bool) -> str:
    """'install' — положить пользователю свою копию, 'keep' — оставить его.

    Копия пользователя обновляется сама и уходит вперёд машинной, которую IT
    переустанавливает редко. Безусловная перезапись откатывала бы версию при
    каждом входе в Windows, а автообновление тут же качало бы её заново.
    """
    if not installed_exists:
        return "install"
    return "install" if _version_key(own_version) > _version_key(installed_version) else "keep"


def deployment_orphaned(marker: dict) -> bool:
    """Копию пользователю положила установка на компьютер, а её уже сняли."""
    machine_exe = str((marker or {}).get("machine_exe") or "")
    return bool(machine_exe) and not Path(machine_exe).exists()


def is_network_path(path: Path) -> bool:
    """Общая папка или сетевой диск."""
    text = str(path)
    if text.startswith("\\\\"):
        return True
    if not IS_WINDOWS:
        return False
    try:
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(Path(text).anchor) == DRIVE_REMOTE
    except Exception:  # noqa: BLE001
        return False


def run_machine_logon(cfg: dict) -> int:
    """Вход пользователя на компьютере, куда программу поставил MSI.

    Окон не показывает ни при каком исходе: это вход в Windows, а не действие
    человека. Неудача пишется в install.log, следующий вход пробует снова.
    """
    setup_logging(cfg, "install.log")
    source = program_path()
    target = installed_path()

    if not getattr(sys, "frozen", False):
        logging.error("%s имеет смысл только для собранного exe", DEPLOYED_ARG)
        return 2
    if is_network_path(source):
        # Копия пользователя снимает себя, когда машинной копии больше нет.
        # Общая папка «пропадает» при каждом обрыве сети — ноутбук вне офиса
        # удалил бы программу сам. Из сети ставим обычной тихой установкой.
        logging.warning("%s запущен из сети (%s) — ставлю тихой установкой без метки", DEPLOYED_ARG, source)
        return run_install(cfg, quiet=True)
    if source.resolve() == target.resolve():
        logging.error("%s запущен из копии пользователя, а он для машинной копии", DEPLOYED_ARG)
        return 2

    exists = target.exists()
    installed_version = file_version(target) if exists else ""
    plan = machine_logon_plan(VERSION, installed_version, exists)
    logging.info("Вход пользователя: машинная копия %s, у пользователя %s → %s",
                 VERSION, installed_version or ("без версии" if exists else "нет"), plan)
    if plan == "install":
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _stop_installed_copies(target)
            time.sleep(1.5)
            shutil.copy2(source, target)
        except Exception:  # noqa: BLE001
            logging.exception("Не удалось положить копию пользователю в %s", target)
            return 1
        _remove_task()   # у прежних установок она есть — снимаем

    try:
        deployment_marker_path().write_text(
            json.dumps({"machine_exe": str(source)}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        logging.exception("Метка установки на компьютер не записана")
    # Запуск теперь из HKLM\...\Run. Личный от прежней ручной установки поднимал
    # бы вторую копию в ту же секунду и мешал подмене файла.
    _remove_autostart()
    if not shortcut_path().exists():
        logging.info("Ярлык Oktell создан: %s", _create_shortcut(target))

    if not is_running_by_mutex(WATCHDOG_MUTEX_NAME):
        try:
            flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
            subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=flags,
                             close_fds=True, env=child_env())
            logging.info("Копия пользователя запущена: %s", target)
        except Exception:  # noqa: BLE001
            logging.exception("Копия пользователя не запустилась")
            return 1
    return 0


def run_orphan_cleanup(cfg: dict) -> int:
    """Установку на компьютер сняли — снять и копию пользователя.

    Иначе программа, удалённая IT, продолжала бы работать: ярлык «Oktell» ведёт
    в копию пользователя и поднимает контроль снова. Файлы и логи остаются —
    выключает программу не их удаление, а разбирать случившееся без логов нечем.
    """
    setup_logging(cfg, "install.log")
    logging.warning("Установка на компьютер снята (%s нет) — снимаю копию пользователя",
                    read_deployment_marker().get("machine_exe"))
    _remove_autostart()
    _remove_shortcut()
    _remove_deployment_marker()
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
        subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=flags,
                         close_fds=True, env=child_env())
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
    warned: false, fired: false, login: null, userid: null, seconds: 0,
    // Идёт ли разговор ПРЯМО СЕЙЧАС. Нужно не ограничителю, а тому, что стоит
    // поверх него: обязательное объявление обязано дождаться конца звонка.
    inCall: false,
    lastState: null, lastCallState: null, seenStates: []
  };

  // Свой логин известен ДО первого кадра: он лежит в cookie __oktelllogin.
  // Это важно для ключа накопленного — иначе на машине, где смены работают по
  // очереди, loadBudget подхватывал бюджет предыдущего оператора за этот день.
  function ownLogin() {
    try {
      var m = document.cookie.match(/(?:^|;\s*)__oktelllogin=([^;]*)/);
      if (!m) { return null; }
      var value = decodeURIComponent(m[1]);
      // Страховка: если вендор однажды положит сюда токен — не берём.
      if (!value || value.length >= 40 || /^[0-9a-f-]{30,}$/i.test(value)) { return null; }
      return value;
    } catch (e) { return null; }
  }

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
    rule.inCall = true;
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
    // Кадр может быть про КОЛЛЕГУ, а не про нас. Такой кадр раньше правил наш
    // счётчик: разговор соседа обнулял накопленное, его выход из «Перезвона»
    // парковал отсчёт. Свой логин берём из cookie __oktelllogin (это логин, а
    // не токен), а свой GUID узнаём из кадра, где есть и логин, и userid —
    // потому что часть кадров (userstatechanged) логина не несёт вовсе.
    if (payload.userlogin) {
      var who = String(payload.userlogin);
      if (rule.login && who !== rule.login) { return; }
      rule.login = who;
      if (payload.userid) { rule.userid = String(payload.userid); }
    } else if (payload.userid && rule.userid && String(payload.userid) !== rule.userid) {
      return;
    }
    if (payload.userstatestr && rule.seenStates.indexOf(payload.userstatestr) < 0) {
      // Небольшой словарь встреченных состояний: по нему настраивается
      // распознавание звонка, значений разговоров тут нет.
      rule.seenStates.push(String(payload.userstatestr));
      if (rule.seenStates.length > 20) { rule.seenStates.shift(); }
    }
    if (looksLikeCall(payload)) { onCall(payload); return; }
    // Любой НЕ разговорный кадр про нас означает, что разговор кончился.
    rule.inCall = false;
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

  function recordViolation(seconds, dry) {
    // Пишем ПОСЛЕ очистки хранилища, иначе стёрли бы собственную запись.
    try {
      var list = [];
      try { list = JSON.parse(localStorage.getItem('__oktell_guard_violations') || '[]'); } catch (e) { list = []; }
      var at = new Date().toISOString();
      list.push({
        at: at,
        login: rule.login,
        seconds: seconds,
        threshold_s: cfg.thresholdS,
        reason: 'recall_timeout',
        dry_run: !!dry,
        // Ключ, чтобы повторная отправка не удвоила запись в отчёте.
        key: (rule.login || 'нет-логина') + '|' + at
      });
      if (list.length > 50) { list = list.slice(-50); }
      localStorage.setItem('__oktell_guard_violations', JSON.stringify(list));
    } catch (e) {}
  }

  function logout(seconds) {
    if (cfg.dryRun) {
      // Обкатка: фиксируем и уходим. Плашку тоже не показываем — предупреждение,
      // за которым никогда ничего не следует, приучает его игнорировать.
      recordViolation(seconds, true);
      return;
    }
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
    recordViolation(seconds, false);
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
    rule.login = ownLogin();
    loadBudget();
    setInterval(function () {
      if (rule.since === null || rule.fired) { return; }
      var seconds = totalSeconds();
      rule.seconds = seconds;
      if (!rule.warned && seconds >= cfg.thresholdS - cfg.warnBeforeS) {
        rule.warned = true;
        if (!cfg.dryRun) { banner(cfg.message, Math.max(1, cfg.thresholdS - seconds)); }
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
            # Обкатка: считаем и записываем, но не трогаем человека.
            "dryRun": bool(rule.get("dry_run")),
            # Что считать звонком: строки состояния и/или числовые коды.
            # Обнуляет накопленное ТОЛЬКО это, смена статуса — нет.
            "callStateStrings": list(rule.get("call_state_strings") or ["fullbusy", "talk", "dial", "call", "ring"]),
            "callStateIds": list(rule.get("call_state_ids") or [5]),
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
    return rf"""
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


def build_hook_health_js(session_keys: Iterable[str]) -> str:
    """Живо ли правило в странице — или оно есть, но ничего не видит.

    Одного факта «хук стоит» мало. Правило слышит статусы только через сокеты,
    которые создал уже подменённый `window.WebSocket`. Сокет, открытый клиентом
    Oktell РАНЬШЕ подмены, остаётся родным, и кадры по нему мимо правила: код в
    странице есть, счётчик стоит на нуле, человека не выбрасывает никогда.
    Отличить это можно только по одному признаку — сессия есть, а перехваченных
    сокетов ноль. Его и снимаем.
    """
    keys = json.dumps(list(session_keys))
    return f"""
(function () {{
  var keys = {keys};
  var cfg = window.__oktellGuardRuleConfig || {{}};
  var rule = window.__oktellGuardRule || {{}};
  var out = {{
    hooked: !!window.__oktellGuardHooked,
    ruleVersion: cfg.ruleVersion || null,
    enabled: !!cfg.enabled,
    thresholdS: cfg.thresholdS || 0,
    sockets: (window.__oktellGuardSockets || []).length,
    frames: (window.__oktellGuardStateFrames || []).length,
    session: false,
    login: rule.login || null,
    seconds: Number(rule.seconds) || 0,
    inCall: !!rule.inCall,
    counting: rule.since !== undefined && rule.since !== null
  }};
  try {{
    for (var i = 0; i < keys.length; i++) {{
      var k = keys[i];
      try {{ if (localStorage.getItem(k)) {{ out.session = true; }} }} catch (e) {{}}
      if (!out.session && document.cookie.indexOf(k + '=') >= 0) {{ out.session = true; }}
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


def build_collect_violations_js() -> str:
    """Забрать накопленные записи о нарушениях из страницы."""
    return """
(function () {
  try {
    return JSON.parse(localStorage.getItem('__oktell_guard_violations') || '[]');
  } catch (e) {
    return [];
  }
})();
""".strip()


def build_clear_violations_js(keys) -> str:
    """Удалить ровно отправленные записи.

    По ключам, а не «очистить всё»: пока шла отправка, правило могло записать
    новое нарушение, и очистка целиком его бы потеряла.
    """
    payload = json.dumps(list(keys))
    return f"""
(function () {{
  var sent = {payload};
  try {{
    var list = JSON.parse(localStorage.getItem('__oktell_guard_violations') || '[]');
    var left = list.filter(function (item) {{
      var key = item && (item.key || ((item.login || '') + '|' + item.at));
      return sent.indexOf(key) < 0;
    }});
    localStorage.setItem('__oktell_guard_violations', JSON.stringify(left));
    return left.length;
  }} catch (e) {{
    return -1;
  }}
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


NEWS_JS_TEMPLATE = r"""
(function () {
  var data = __NEWS_PAYLOAD__;
  var ID = '__oktell_guard_news';
  var state = window.__oktellGuardNews = window.__oktellGuardNews || {};
  if (state.id === data.id && document.getElementById(ID)) { return true; }
  state.id = data.id; state.result = null; state.step = 'read';
  state.answers = {}; state.failed = false;
  var old = document.getElementById(ID);
  if (old && old.parentNode) { old.parentNode.removeChild(old); }

  var root = document.createElement('div');
  root.id = ID;
  root.setAttribute('style', [
    'position:fixed', 'inset:0', 'z-index:2147483647',
    'background:rgba(10,10,12,0.58)', 'backdrop-filter:blur(6px)',
    'display:flex', 'align-items:center', 'justify-content:center',
    'font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif'
  ].join(';'));
  // Клавиатура и мышь дальше окна не идут: объявление обязательное, и
  // «свернуть, потом прочитаю» у него нет.
  root.addEventListener('keydown', function (e) { e.stopPropagation(); }, true);

  var card = document.createElement('div');
  card.setAttribute('style', [
    'width:min(720px,92vw)', 'max-height:88vh', 'overflow:auto',
    'background:#fff', 'color:#1c1c1e', 'border-radius:18px',
    'padding:28px', 'box-shadow:0 20px 60px rgba(0,0,0,.45)'
  ].join(';'));

  var badge = document.createElement('div');
  badge.textContent = 'ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ';
  badge.setAttribute('style', 'display:inline-block;font:600 11.5px/1 -apple-system,Segoe UI,Arial;'
    + 'letter-spacing:.4px;color:#007aff;background:rgba(0,122,255,.1);padding:6px 10px;border-radius:999px');

  var title = document.createElement('h2');
  title.textContent = data.title || '';
  title.setAttribute('style', 'font:600 22px/1.25 -apple-system,Segoe UI,Arial;margin:12px 0 14px');

  var body = document.createElement('div');
  body.innerHTML = data.body || '';
  body.setAttribute('style', 'line-height:1.55');

  // Кадры объявления. Адреса подписаны сервером на час и ведут прямо в
  // хранилище — окно за ними не ходит никуда, кроме как за картинкой.
  // Складываем в колонку, а не в карусель: у объявления их единицы, а листалка
  // в обязательном окне — лишний повод в нём застрять.
  var gallery = document.createElement('div');
  gallery.setAttribute('style', 'margin-top:14px');
  (data.photos || []).forEach(function (photo) {
    if (!photo || !photo.url) { return; }
    var image = document.createElement('img');
    image.src = photo.url;
    image.alt = '';
    image.setAttribute('style', 'display:block;width:100%;max-height:52vh;object-fit:contain;'
      + 'background:#f2f2f7;border-radius:14px;margin-bottom:10px');
    // Битый кадр убираем совсем: пустая рамка с крестиком в обязательном окне
    // читается как «программа сломалась», а причина может быть в протухшей
    // подписи, и человеку с ней всё равно ничего не сделать.
    image.addEventListener('error', function () {
      if (image.parentNode) { image.parentNode.removeChild(image); }
    });
    gallery.appendChild(image);
  });

  // Уведомление о неверных ответах — ОДНО на весь тест и над вопросами. Это
  // единственное красное в окне: какой именно ответ неверен, оно не говорит.
  var warn = document.createElement('div');
  warn.textContent = 'Ответы неверные — выбор сброшен, пройдите тест заново';
  warn.setAttribute('style', 'display:none;margin-bottom:12px;padding:11px 13px;border-radius:12px;'
    + 'background:#fff1f0;color:#b3120b;font:500 13.5px/1.35 -apple-system,Segoe UI,Arial');

  var quiz = document.createElement('div');
  quiz.style.display = 'none';

  var note = document.createElement('div');
  note.setAttribute('style', 'margin-top:12px;font-size:13px;color:#6e6e73;min-height:18px');

  var button = document.createElement('button');
  button.setAttribute('style', [
    'margin-top:16px', 'width:100%', 'padding:12px 16px', 'border:none',
    'border-radius:12px', 'background:#007aff', 'color:#fff',
    'font:600 15px/1 -apple-system,Segoe UI,Arial', 'cursor:pointer',
    'font-variant-numeric:tabular-nums'
  ].join(';'));

  var back = document.createElement('button');
  back.textContent = 'Перечитать новость';
  back.setAttribute('style', 'margin-top:8px;width:100%;padding:8px;border:none;background:none;'
    + 'color:#007aff;font:500 13px/1 -apple-system,Segoe UI,Arial;cursor:pointer;display:none');

  card.appendChild(badge); card.appendChild(title); card.appendChild(body);
  card.appendChild(gallery);
  card.appendChild(warn); card.appendChild(quiz);
  card.appendChild(button); card.appendChild(back); card.appendChild(note);
  root.appendChild(card);
  (document.body || document.documentElement).appendChild(root);

  var left = Number(data.remaining_seconds || 0);
  var questions = data.quiz || [];

  function answered() {
    for (var i = 0; i < questions.length; i++) {
      if (typeof state.answers[questions[i].id] !== 'number') { return false; }
    }
    return true;
  }

  function paint() {
    if (state.step === 'read') {
      body.style.display = ''; gallery.style.display = '';
      quiz.style.display = 'none'; back.style.display = 'none';
      warn.style.display = 'none';
      button.disabled = left > 0;
      button.textContent = left > 0 ? ('Ознакомлен · ' + left) : 'Ознакомлен';
    } else {
      body.style.display = 'none'; gallery.style.display = 'none';
      quiz.style.display = ''; back.style.display = '';
      warn.style.display = state.failed ? '' : 'none';
      button.disabled = !answered();
      button.textContent = 'Подтвердить';
    }
    button.style.opacity = button.disabled ? '.45' : '1';
  }

  // КАКОЙ вопрос неверен, окно НЕ показывает, и сервер его больше не присылает
  // (решение владельца 21.09.2026). Подсветка превращала тест в перебор: человек
  // менял помеченный ответ, жал снова — и подбирал верный, ни разу не вернувшись
  // к тексту. Ради этого возврата тест и заведён. Поэтому неверная попытка не
  // засчитывается целиком: весь выбор снимается, и тест проходится заново.
  var refreshers = [];

  function drawQuiz() {
    quiz.innerHTML = '';
    refreshers = [];
    questions.forEach(function (item, index) {
      var box = document.createElement('div');
      box.setAttribute('style', 'background:#f2f2f7;border-radius:14px;padding:14px;margin-bottom:10px');
      var prompt = document.createElement('div');
      prompt.textContent = (index + 1) + '. ' + item.prompt;
      prompt.setAttribute('style', 'font-weight:500;margin-bottom:8px');
      box.appendChild(prompt);
      var labels = [], radios = [];
      (item.options || []).forEach(function (option, optionIndex) {
        var label = document.createElement('label');
        label.setAttribute('style', 'display:flex;gap:8px;align-items:center;padding:8px 10px;'
          + 'background:#fff;border:1px solid #d1d1d6;border-radius:10px;margin-bottom:6px;cursor:pointer');
        var radio = document.createElement('input');
        radio.type = 'radio'; radio.name = 'q' + item.id;
        radio.checked = state.answers[item.id] === optionIndex;
        radio.addEventListener('change', function () {
          state.answers[item.id] = optionIndex;
          // Уведомление и отказ сервера убираем: они были про прошлую попытку.
          state.failed = false;
          note.textContent = '';
          refresh();
          paint();
        });
        var text = document.createElement('span');
        text.textContent = option;
        label.appendChild(radio); label.appendChild(text);
        box.appendChild(label);
        labels.push(label); radios.push(radio);
      });
      function refresh() {
        labels.forEach(function (label, optionIndex) {
          radios[optionIndex].checked = state.answers[item.id] === optionIndex;
        });
      }

      refreshers.push({ id: item.id, box: box, refresh: refresh });
      refresh();
      quiz.appendChild(box);
    });
  }

  function refreshQuiz() { refreshers.forEach(function (one) { one.refresh(); }); }

  drawQuiz();
  paint();

  if (left > 0) {
    var handle = setInterval(function () {
      left = Math.max(0, left - 1);
      paint();
      if (left === 0) { clearInterval(handle); }
    }, 1000);
  }

  back.addEventListener('click', function () { state.step = 'read'; paint(); });

  button.addEventListener('click', function () {
    if (button.disabled) { return; }
    // Первый шаг ничего не подтверждает: он открывает тест. Отправлять
    // подтверждение с пустыми ответами значило бы получить отказ сервера.
    if (state.step === 'read' && questions.length) { state.step = 'quiz'; paint(); return; }
    button.disabled = true;
    button.textContent = 'Отправляем…';
    state.result = { id: data.id, answers: state.answers };
    // Страховка на случай, когда забрать нажатие некому: программа перезапущена
    // сторожем, вкладка потеряла связь с ней, сеть легла. Без неё человек
    // остаётся с вечным «Отправляем…» и не понимает, услышали его или нет.
    if (state.waitTimer) { clearTimeout(state.waitTimer); }
    state.waitTimer = setTimeout(function () {
      if (!state.result) { return; }   // уже забрали, ответ просто в пути
      state.result = null;
      note.textContent = 'Ответ не ушёл — нажмите ещё раз';
      note.style.color = '#d70015';
      paint();
    }, 20000);
  });

  // Сервер ответил отказом — показываем его словами, своего мнения не имеем.
  state.feedback = function (payload) {
    if (state.waitTimer) { clearTimeout(state.waitTimer); state.waitTimer = null; }
    note.textContent = payload && payload.error ? payload.error : '';
    note.style.color = '#d70015';
    if (payload && payload.remaining_seconds) { left = Number(payload.remaining_seconds); }
    if (payload && payload.code === 'NEWS_QUIZ_WRONG') {
      // Правило владельца (21.09.2026) дословно: «если один вариант не правилен,
      // ответы сбрасываются и выходит уведомление о том что ответы не правильные
      // и попробовать заново, тест будет завершен если он все ответы выберет
      // корректно». Снимаем ВЕСЬ выбор, показываем уведомление и оставляем
      // человека на тесте: «Перечитать новость» под рукой, а подтвердить он
      // сможет, только ответив заново на все вопросы верно.
      state.answers = {}; state.failed = true;
      drawQuiz();
      // Отказ сервера строкой под кнопкой повторил бы уведомление над тестом:
      // два красных текста об одном и том же — шум.
      note.textContent = '';
      if (warn.scrollIntoView) { warn.scrollIntoView({block: 'start'}); }
    }
    state.result = null;
    paint();
  };
  return true;
})();
""".strip()


def build_news_js(item: dict) -> str:
    """Разметка и поведение окна обязательного объявления.

    Один код на оба места: им наполняется собственное окно поверх всех окон
    (NewsOverlay), а раньше он же впрыскивался в страницу клиента АТС.

    Наружу отдаём ТОЛЬКО то, что окно рисует. Служебные поля объявления (пути в
    хранилище, адресаты, автор) в страницу не уходят вовсе — не по секретности,
    а потому что окно про них ничего не знает и знать не должно.
    """
    payload = json.dumps(
        {
            "id": item.get("id"),
            "title": item.get("title") or "",
            "body": item.get("body") or "",
            "remaining_seconds": int(item.get("remaining_seconds") or 0),
            # Кадры уже подписаны сервером: у окна только адрес и размеры.
            "photos": [
                {"url": photo.get("url") or "",
                 "width": photo.get("width"), "height": photo.get("height")}
                for photo in (item.get("photos") or []) if photo.get("url")
            ],
            "quiz": [
                {"id": q.get("id"), "prompt": q.get("prompt") or "", "options": q.get("options") or []}
                for q in (item.get("quiz") or [])
            ],
        },
        ensure_ascii=False,
    )
    return NEWS_JS_TEMPLATE.replace("__NEWS_PAYLOAD__", payload)


def build_news_result_js() -> str:
    """Забрать нажатие «Ознакомлен» из страницы и очистить его.

    Запрос на сервер делает агент, а не страница: у неё нет ни токена, ни права
    ходить на наш адрес, и выдавать ей то и другое ради одной кнопки незачем.
    """
    return """
(function () {
  var state = window.__oktellGuardNews;
  if (!state || !state.result) { return null; }
  var out = state.result;
  state.result = null;
  return out;
})();
""".strip()


def build_news_feedback_js(payload: dict) -> str:
    data = json.dumps(payload or {}, ensure_ascii=False)
    return f"""
(function () {{
  var state = window.__oktellGuardNews;
  if (state && typeof state.feedback === 'function') {{ state.feedback({data}); return true; }}
  return false;
}})();
""".strip()


def build_news_close_js() -> str:
    return """
(function () {
  var node = document.getElementById('__oktell_guard_news');
  if (node && node.parentNode) { node.parentNode.removeChild(node); }
  if (window.__oktellGuardNews) { window.__oktellGuardNews.id = null; }
  return true;
})();
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Окно объявления поверх ВСЕХ окон
#
# Раньше объявление рисовалось внутри страницы Oktell — и жило только в пределах
# её окна: свернул клиент АТС, ушёл в Excel или MicroSIP — и обязательного
# объявления нет. Теперь у него своё окно: тот же Chromium, отдельная локальная
# страница, размер по монитору и `HWND_TOPMOST` через Win32.
#
# Разметку и поведение берём ТЕ ЖЕ (build_news_js): страница-оболочка пустая, а
# весь код объявления — один на оба места. Вторая копия правил показа, задержки
# кнопки и разбора теста разъехалась бы с первой молча.
#
# Чем это отличается от шарика помощника (icore-orb-overlay): тому нужны
# сквозные клики, а этому наоборот — он обязан перехватывать всё. Поэтому ни
# прозрачности, ни WS_EX_TRANSPARENT здесь нет.
# ─────────────────────────────────────────────────────────────────────────────

NEWS_WINDOW_TITLE = "iCORE · Объявление"
# Заголовок окна входа. Версия прямо в нём: «какая у тебя версия» — первый
# вопрос при разборе, а до меню оператор доберётся только после входа.
LOGIN_WINDOW_TITLE = f"{APP_NAME_SHORT} {VERSION}"

NEWS_PAGE_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="icon" href="data:image/png;base64,__ICON__">
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #0a0a0c; }
  /* Под затемнением — снимок того, что было на экране. Настоящей прозрачности
     у окна Chrome нет, а полупрозрачность всему окну выцветила бы и карточку,
     которую как раз надо читать. */
  body {
    background-image: url("backdrop.bmp");
    background-size: cover;
    background-position: center;
  }

</style>
</head>
<body>
<script>__NEWS_JS__</script>
</body>
</html>
"""


def news_page_path() -> Path:
    return app_dir() / "news.html"


def build_news_page(item: dict) -> str:
    """Страница объявления: пустая оболочка плюс тот же самый код окна."""
    return (NEWS_PAGE_HTML
            .replace("__TITLE__", NEWS_WINDOW_TITLE)
            .replace("__ICON__", LOGIN_ICON_B64)
            .replace("__NEWS_JS__", build_news_js(item)))


def capture_backdrop(rect, path: Path, shrink: int = 2) -> bool:
    """Снять монитор в BMP — он станет фоном окна объявления.

    Зачем это вместо настоящей прозрачности. Прозрачный фон при непрозрачной
    карточке умеет только своя оболочка на Chromium (Electron/WebView2); Electron
    по собственному README тянет около 100 МБ на оператора, а окно Chrome
    попиксельной прозрачности не имеет вовсе. Полупрозрачность всему окну
    (LWA_ALPHA) гасит заодно и карточку — текст объявления выцветает вместе с
    фоном, а его как раз надо читать.

    Снимок решает то же самое честнее: под затемнением стоит ровно то, что было
    на экране, карточка поверх — полностью чёткая. Фон застывший, но окно
    модальное и живёт минуту: за спиной у него всё равно ничего не меняется.

    Пишем BMP руками, а не через библиотеку: PIL в сборку не входит, и тащить
    её ради одного кадра — те же лишние мегабайты, от которых уходим. 24 бита,
    а не 32: BitBlt оставляет четвёртый байт нулевым, и декодер, принявший его
    за прозрачность, показал бы пустоту вместо экрана.
    """
    if not IS_WINDOWS or not rect:
        return False
    left, top, width, height = (int(v) for v in rect)
    if width <= 0 or height <= 0:
        return False
    out_w, out_h = max(1, width // shrink), max(1, height // shrink)

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    screen_dc = mem_dc = bitmap = None
    try:
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, out_w, out_h)
        gdi32.SelectObject(mem_dc, bitmap)
        gdi32.SetStretchBltMode(mem_dc, 4)   # HALFTONE — уменьшение без ступенек
        SRCCOPY = 0x00CC0020
        if not gdi32.StretchBlt(mem_dc, 0, 0, out_w, out_h,
                                screen_dc, left, top, width, height, SRCCOPY):
            return False

        stride = (out_w * 3 + 3) & ~3
        buffer = ctypes.create_string_buffer(stride * out_h)
        header = BitmapInfoHeader()
        header.biSize = ctypes.sizeof(BitmapInfoHeader)
        header.biWidth, header.biHeight = out_w, out_h   # + — строки снизу вверх, как ждёт BMP
        header.biPlanes, header.biBitCount, header.biCompression = 1, 24, 0
        if not gdi32.GetDIBits(mem_dc, bitmap, 0, out_h, buffer,
                               ctypes.byref(header), 0):
            return False

        size = 14 + header.biSize + len(buffer)
        with path.open("wb") as handle:
            handle.write(b"BM")
            handle.write(struct.pack("<IHHI", size, 0, 0, 14 + header.biSize))
            handle.write(bytes(header))
            handle.write(buffer.raw)
        return True
    except Exception:  # noqa: BLE001 — без фона объявление показывается на тёмном
        logging.debug("Снимок экрана для фона не сделан", exc_info=True)
        return False
    finally:
        try:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)
        except Exception:  # noqa: BLE001
            pass


def backdrop_path() -> Path:
    return app_dir() / "backdrop.bmp"


def _window_by_title(title: str):
    """HWND окна Chrome с таким заголовком. 0 — нет такого.

    Ищем И по классу: на машине оператора открыт клиент Oktell, и у него в
    заголовке бывает то же слово — FindWindow без класса попадал в него.
    """
    if not IS_WINDOWS:
        return 0
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _param):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, cls, 256)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        text = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, text, 512)
        if text.value.strip() == title:
            found.append(hwnd)
            return False
        return True

    try:
        ctypes.windll.user32.EnumWindows(check, None)
    except Exception:  # noqa: BLE001
        return 0
    return found[0] if found else 0


def _monitor_rect(hwnd_hint: int = 0):
    """Границы монитора, на котором показывать объявление.

    Берём монитор ОКНА OKTELL, а не основной: у оператора два экрана, клиент АТС
    на одном из них, и объявление обязано накрыть тот, куда он смотрит.
    Подсказки нет — берём монитор с курсором, он ближе к правде, чем основной.
    """
    if not IS_WINDOWS:
        return None

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong),
                    ("rcMonitor", ctypes.c_long * 4),
                    ("rcWork", ctypes.c_long * 4),
                    ("dwFlags", ctypes.c_ulong)]

    MONITOR_DEFAULTTONEAREST = 2
    try:
        if hwnd_hint:
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd_hint, MONITOR_DEFAULTTONEAREST)
        else:
            point = ctypes.c_longlong()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            monitor = ctypes.windll.user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        left, top, right, bottom = info.rcMonitor
        return left, top, right - left, bottom - top
    except Exception:  # noqa: BLE001
        logging.debug("Границы монитора не определились", exc_info=True)
        return None


def _set_window_pos(hwnd: int, left: int, top: int, width: int, height: int, flags: int) -> bool:
    """SetWindowPos с HWND_TOPMOST и ПРАВИЛЬНЫМИ типами.

    Типы здесь не занудство: `HWND_TOPMOST` это −1, и без argtypes ctypes
    отдаёт его 32-битным числом, а параметр — указатель. Окно при этом послушно
    меняло размер, но поверх всех не вставало — и заметить это можно было только
    проверкой флага WS_EX_TOPMOST, а не глазами.
    """
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_bool
    HWND_TOPMOST = ctypes.c_void_p(-1)
    return bool(user32.SetWindowPos(ctypes.c_void_p(hwnd), HWND_TOPMOST,
                                    left, top, width, height, flags))


def _make_overlay(hwnd: int, rect) -> bool:
    """Снять рамку, накрыть монитор, поднять поверх всех окон.

    Поверх ВСЕХ — это и есть постановка: объявление обязательное, и «сверну,
    потом прочитаю» у него нет. Alt+Tab Windows при этом не отнимает ни у кого —
    это потолок для любого приложения, включая Electron.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    GWL_STYLE = -16
    WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
    SWP_SHOWWINDOW, SWP_FRAMECHANGED = 0x0040, 0x0020
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME))
        # Полупрозрачным окно целиком НЕ делаем. LWA_ALPHA гасит вместе с фоном
        # и карточку — текст объявления выцветает, а его как раз надо читать.
        # Прозрачность даёт фон-снимок экрана под затемнением (capture_backdrop):
        # под ним стоит ровно то, что было на экране, карточка поверх чёткая.
        left, top, width, height = rect or (0, 0, 0, 0)
        _set_window_pos(hwnd, int(left), int(top), int(width), int(height),
                        SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        return True
    except Exception:  # noqa: BLE001
        logging.debug("Окно объявления не удалось поднять поверх всех", exc_info=True)
        return False


def _restore_if_minimized(hwnd: int) -> None:
    """Развернуть свёрнутое окно. SW_SHOWNOACTIVATE — без кражи фокуса.

    Фокус не забираем намеренно: оператор мог свернуть окно, чтобы дописать
    строчку в Excel, и выдернутая из-под рук клавиатура — это уже не контроль,
    а вредительство. Окно вернётся на экран и подождёт.
    """
    if not IS_WINDOWS or not hwnd:
        return
    SW_SHOWNOACTIVATE = 4
    try:
        user32 = ctypes.windll.user32
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
    except Exception:  # noqa: BLE001
        pass


def _keep_on_top(hwnd: int) -> None:
    """Вернуть окно наверх, если его перекрыли.

    Нужно: другие программы тоже ставят себе TOPMOST, и объявление уезжает вниз.
    Размер и положение не трогаем — окно уже накрыло монитор.
    """
    if not IS_WINDOWS or not hwnd:
        return
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
    try:
        _set_window_pos(hwnd, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:  # noqa: BLE001
        pass


class NewsOverlay:
    """Окно обязательного объявления поверх всех окон.

    Живёт ровно столько, сколько показывается объявление: подтвердили — закрыли.
    Держать его пустым и прятать было бы дешевле по запуску, но дороже по сути:
    невидимое окно поверх всех — это то, что однажды перехватит клик и никто не
    поймёт почему.
    """

    def __init__(self, cfg: dict, browser: "ManagedBrowser"):
        self.cfg = cfg
        self.browser = browser
        self.page: Optional[CdpPage] = None
        self.hwnd = 0
        self.target_id = ""

    # ---------- показ ----------

    def show(self, item: dict) -> bool:
        self.close()
        chrome = self.browser.chrome_path()
        if not chrome:
            logging.error("Объявление показать нечем: браузер не найден")
            return False
        page_file = news_page_path()
        try:
            page_file.parent.mkdir(parents=True, exist_ok=True)
            page_file.write_text(build_news_page(item), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logging.exception("Страница объявления не записалась")
            return False

        rect = _monitor_rect(_window_by_title_like_oktell(self.browser))
        # Снимок ДО запуска окна: иначе оно снимет само себя.
        capture_backdrop(rect, page_file.parent / "backdrop.bmp")
        args = [
            str(chrome),
            f"--user-data-dir={self.browser.profile_dir}",
            f"--remote-debugging-port={int(self.browser.browser_cfg.get('cdp_port', 0) or 0)}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,ChromeWhatsNewUI",
            # Именно kiosk: снятия WS_CAPTION мало — заголовок у окна --app
            # рисует сам Chrome внутри клиентской области, и системные стили о
            # нём ничего не знают. Проверено снимком экрана: рамка оставалась.
            "--kiosk",
            f"--app={page_file.resolve().as_uri()}",
        ]
        if rect:
            args.insert(-1, f"--window-position={int(rect[0])},{int(rect[1])}")
            args.insert(-1, f"--window-size={int(rect[2])},{int(rect[3])}")
        try:
            subprocess.Popen(args, creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
                             close_fds=True, env=child_env())
        except Exception:  # noqa: BLE001
            logging.exception("Окно объявления не запустилось")
            return False

        deadline = time.time() + 20
        while time.time() < deadline:
            target = _login_target(self.browser, page_file.resolve().as_uri())
            if target:
                try:
                    self.page = CdpPage(str(target["webSocketDebuggerUrl"]),
                                        timeout=float(self.cfg.get("request_timeout_s", 10)))
                    self.target_id = str(target.get("id") or "")
                except Exception:  # noqa: BLE001
                    time.sleep(0.4)
                    continue
                self._go_fullscreen()
                self.hwnd = _window_by_title(NEWS_WINDOW_TITLE)
                _make_overlay(self.hwnd, rect)
                logging.info("Объявление #%s показано поверх всех окон", item.get("id"))
                return True
            time.sleep(0.4)
        logging.warning("Окно объявления не открылось за 20 с")
        return False

    def _go_fullscreen(self) -> None:
        """Убрать рамку через CDP, а не флагом командной строки.

        `--kiosk` работает, только когда Chrome стартует ХОЛОДНЫМ. У оператора он
        уже запущен — в том же профиле открыт клиент Oktell, — и новый вызов
        просто передаёт окно живому процессу, молча выбрасывая все флаги. Окно
        объявления выходило с обычной рамкой Chrome, и заметить это на чистом
        профиле было нельзя: там Chrome стартовал заново и флаг срабатывал.

        `Browser.setWindowBounds` идёт по отладочному соединению к УЖЕ открытому
        окну, поэтому ему всё равно, кто его запустил.
        """
        if self.page is None:
            return
        try:
            window = self.page.call("Browser.getWindowForTarget")
            window_id = (window or {}).get("windowId")
            if window_id is None:
                return
            if str(((window or {}).get("bounds") or {}).get("windowState") or "") == "fullscreen":
                return
            self.page.call("Browser.setWindowBounds",
                           {"windowId": window_id, "bounds": {"windowState": "fullscreen"}})
        except Exception:  # noqa: BLE001 — рамка хуже, чем отсутствие объявления
            logging.debug("Полноэкранный режим не включился", exc_info=True)

    # ---------- общение ----------

    def alive(self) -> bool:
        return self.page is not None and getattr(self.page, "connected", False)

    def hold_on_top(self) -> None:
        """Вернуть окно на место, если его увели. Зовётся каждые полсекунды.

        Увести его можно тремя способами, и все три штатные для Windows:

        * Chrome в полноэкранном режиме сам показывает крестик, стоит подвести
          мышь к верхнему краю, — по нему окно выходит из полноэкранного и
          сворачивается. Убрать эту кнопку нельзя (её рисует браузер), поэтому
          просто возвращаем режим обратно;
        * окно можно свернуть с панели задач или Win+D;
        * другие программы тоже ставят себе TOPMOST и перекрывают наше.

        Объявление обязательное — «свернул и не прочитал» способом быть не
        должно. Само окно при этом никого не держит силой: закрыть его Windows
        по-прежнему позволяет, и тогда агент показывает объявление заново.
        """
        if not self.hwnd:
            self.hwnd = _window_by_title(NEWS_WINDOW_TITLE)
        _restore_if_minimized(self.hwnd)
        self._go_fullscreen()
        _keep_on_top(self.hwnd)

    def result(self) -> Optional[dict]:
        if not self.alive():
            return None
        try:
            data = self.page.evaluate(build_news_result_js())
        except Exception:  # noqa: BLE001
            logging.debug("Ответ объявления не прочитан", exc_info=True)
            return None
        return data if isinstance(data, dict) else None

    def feedback(self, payload: dict) -> None:
        if not self.alive():
            return
        try:
            self.page.evaluate(build_news_feedback_js(payload))
        except Exception:  # noqa: BLE001
            logging.debug("Отказ сервера не показан", exc_info=True)

    def close(self) -> None:
        if self.page is not None:
            try:
                self.page.close()
            except Exception:  # noqa: BLE001
                pass
            self.page = None
        if self.target_id:
            _close_target(self.browser, self.target_id)
            self.target_id = ""
        self.hwnd = 0
        for leftover in (news_page_path(), backdrop_path()):
            try:
                leftover.unlink()
            except FileNotFoundError:
                continue
            except Exception:  # noqa: BLE001
                logging.debug("Файл окна объявления не удалился", exc_info=True)


def _window_by_title_like_oktell(browser: "ManagedBrowser") -> int:
    """HWND окна Oktell — чтобы накрыть ТОТ монитор, где оператор работает."""
    if not IS_WINDOWS:
        return 0
    origin = origin_of(str(browser.cfg.get("oktell_url") or ""))
    host = urlparse(origin).hostname if origin else ""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _param):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, cls, 256)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        text = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, text, 512)
        title = text.value.strip()
        if title and title not in (NEWS_WINDOW_TITLE, LOGIN_WINDOW_TITLE) and (
                "oktell" in title.lower() or (host and host.lower() in title.lower())):
            found.append(hwnd)
            return False
        return True

    try:
        ctypes.windll.user32.EnumWindows(check, None)
    except Exception:  # noqa: BLE001
        return 0
    return found[0] if found else 0


def build_autologin_js(login: str, password: str) -> str:
    """Подставить учётку кабинета в форму входа Oktell и нажать «Войти».

    Оператор вводит только логин и пароль iCORE — при скачивании файла сервер
    уже знает, кто он, поэтому пару от АТС агент получает вместе с настройками
    и вписывает за него.

    Нативный сеттер плюс события input/change: у формы на Angular простое
    присваивание value не будит слушателей, и она отправляла бы пустые поля,
    которые на экране выглядят заполненными.
    """
    payload = json.dumps({"login": str(login or ""), "password": str(password or "")},
                         ensure_ascii=False)
    return rf"""
(function () {{
  var creds = {payload};
  if (!creds.login || !creds.password) {{ return {{ok: false, reason: 'нет учётки'}}; }}
  if (window.__oktellGuardAutologinAt && (Date.now() - window.__oktellGuardAutologinAt) < 15000) {{
    // Форма могла не успеть перерисоваться — второй заход подряд только мешает.
    return {{ok: false, reason: 'только что пробовали'}};
  }}
  function visible(node) {{ return node && node.offsetParent !== null; }}
  var pass = null, list = document.querySelectorAll('input[type="password"]');
  for (var i = 0; i < list.length; i++) {{ if (visible(list[i])) {{ pass = list[i]; break; }} }}
  if (!pass) {{ return {{ok: false, reason: 'формы входа нет'}}; }}
  var form = pass.form || document;
  var user = null, texts = form.querySelectorAll('input');
  for (var j = 0; j < texts.length; j++) {{
    var node = texts[j];
    if (node === pass || !visible(node)) {{ continue; }}
    var type = (node.getAttribute('type') || 'text').toLowerCase();
    if (type === 'text' || type === 'email' || type === 'tel' || !type) {{ user = node; break; }}
  }}
  if (!user) {{ return {{ok: false, reason: 'поля логина нет'}}; }}
  function put(node, value) {{
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(node, value);
    node.dispatchEvent(new Event('input', {{bubbles: true}}));
    node.dispatchEvent(new Event('change', {{bubbles: true}}));
  }}
  try {{
    put(user, creds.login);
    put(pass, creds.password);
  }} catch (e) {{ return {{ok: false, reason: String(e)}}; }}
  window.__oktellGuardAutologinAt = Date.now();
  var submit = null;
  var buttons = (form.querySelectorAll ? form : document).querySelectorAll('button, input[type="submit"]');
  for (var k = 0; k < buttons.length; k++) {{ if (visible(buttons[k])) {{ submit = buttons[k]; break; }} }}
  if (submit) {{ submit.click(); return {{ok: true, how: 'click'}}; }}
  if (form && form.submit) {{ try {{ form.submit(); return {{ok: true, how: 'submit'}}; }} catch (e) {{}} }}
  return {{ok: true, how: 'filled'}};
}})();
""".strip()


def build_read_status_js() -> str:
    """Где оператор сейчас: статус и причина перерыва.

    Причина нужна не для отчётности. Человека, которого мы сняли с линии ради
    объявления, надо вернуть ТУДА ЖЕ: был на «Перерыве» — вернуть на «Перерыв»,
    а не на «Тренинг» и не в «Готов». Один статус этого не описывает: и обед, и
    тренинг, и тех.причина — всё это `break`, различает их только причина.
    """
    return """
(function () {
  var root = window.app && window.app.oktell;
  if (!root || typeof root.getUserStatus !== 'function') { return null; }
  var out = {};
  try { out.status = root.getUserStatus(); } catch (e) { out.status = null; }
  try { out.reason = root.getCurrentBreakReason ? root.getCurrentBreakReason() : null; }
  catch (e) { out.reason = null; }
  try { out.inCallCenter = root.inCallCenter ? !!root.inCallCenter() : null; } catch (e) {}
  return out;
})();
""".strip()


def build_set_status_js(status: str, reason_id: Optional[int] = None) -> str:
    """Сменить статус ЕГО ЖЕ методом — `app.oktell.setUserStatus`.

    Раньше мы собирали кадр сами и слали в перехваченный сокет. Кадр оказался
    неполным, и АТС его молча игнорировала: оператор на время чтения объявления
    так и оставался на линии. Снятый с провода настоящий кадр клиента:

        ["setuserstate",{"userlogin":"6554","userstateid":2,"oncallcenter":true,
                         "onredirect":false,"lunchreasonid":3,"qid":"0.365…"}]

    а наш был `{"onlunch":true,"lunchreasonid":3}` — без логина, без кода
    состояния, без `oncallcenter`. Отсюда правило: формат кадра — внутреннее
    дело вендора, и собирать его руками нельзя. Метод же лежит в той же
    странице, что и кнопка статуса, по которой жмёт оператор.

    Именно `setUserStatus`. Похожий `setStatus` подпричину ТЕРЯЕТ: снято с
    провода — `setStatus('break', 3)` шлёт кадр без `lunchreasonid`, и вместо
    «Тренинга» оператор уходит в перерыв по умолчанию.
    """
    payload = json.dumps({"status": status, "reason": reason_id}, ensure_ascii=False)
    return rf"""
(function () {{
  var want = {payload};
  var root = window.app && window.app.oktell;
  var method = (root && typeof root.setUserStatus === 'function') ? 'setUserStatus'
             : (root && typeof root.setStatus === 'function') ? 'setStatus' : null;
  if (!method) {{
    return {{ok: false, reason: 'в странице нет app.oktell.setUserStatus'}};
  }}
  try {{
    var done = (want.reason === null || want.reason === undefined)
      ? root[method](want.status)
      : root[method](want.status, want.reason);
    // `setUserStatus` возвращает undefined всегда — по нему судить о результате
    // нельзя. Отсюда `done !== false`: это «вызов не отвергнут», а не «АТС
    // применила». Применила или нет, проверяет вызывающий, перечитав статус.
    return {{ok: done !== false, status: want.status, used: method}};
  }} catch (e) {{
    return {{ok: false, reason: String(e)}};
  }}
}})();
""".strip()


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

    def __init__(self, cfg: dict, heal: bool = True):
        self.cfg = cfg
        self.browser_cfg = cfg.get("browser", {}) or {}
        self.url = str(cfg.get("oktell_url") or "")
        self.origin = origin_of(self.url)
        # Чинить слепое правило перезагрузкой имеет право только долгоживущий
        # процесс: регистрация скрипта живёт ровно столько, сколько подключение,
        # и после ухода клиента новый документ остался бы вовсе без правила.
        self.heal = heal
        self._hook_health: dict = {}
        self._script_registered = False
        profile_cfg = str(self.browser_cfg.get("profile_dir") or "").strip()
        self.profile_dir = Path(expand_env(profile_cfg)) if profile_cfg else app_dir() / "chrome-profile"
        self.process: Optional[subprocess.Popen] = None
        # Постоянное CDP-подключение к вкладке: хук ставится один раз на него.
        self._page: Optional[CdpPage] = None
        self._page_target_id: str = ""
        # Вкладки, которые уже перезагрузили ради обновления правила: страховка
        # от цикла «перезагрузил — снова не совпало — перезагрузил».
        self._reloaded_for_rule: set[str] = set()

    # ---------- запуск ----------

    def chrome_path(self) -> Optional[Path]:
        configured = expand_env(str(self.browser_cfg.get("chrome_path") or "").strip())
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
            self.process = subprocess.Popen(args, creationflags=flags, close_fds=True, env=child_env())
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

    def apply_new_rule(self) -> None:
        """Правило изменилось — донести его до уже открытой вкладки.

        Сам хук идемпотентен, поэтому новое правило доходит только вместе с
        перезагрузкой документа: её и назначит install_hook, увидев в странице
        чужой отпечаток версии.
        """
        page = self._page
        if page is None or not getattr(page, "connected", False):
            return
        self.install_hook(page)

    def close_page(self) -> None:
        if self._page is not None:
            self._page.close()
        self._page = None
        self._page_target_id = ""
        # Chrome снимает скрипты ушедшего клиента — значит и наша отметка
        # «зарегистрировано» вместе с подключением обнуляется.
        self._script_registered = False

    def hook_health(self, page: CdpPage) -> dict:
        """Снимок «живо ли правило» из страницы. Пустой словарь — не смогли спросить."""
        try:
            data = page.evaluate(build_hook_health_js(self.cfg.get("session_keys", DEFAULT_SESSION_KEYS)))
        except CdpError:
            logging.debug("Не удалось снять состояние правила", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def install_hook(self, page: CdpPage) -> None:
        """Хук на будущие документы + починка страницы, где правило слепое.

        `Page.enable` обязателен: проверено на стенде — без него
        `addScriptToEvaluateOnNewDocument` возвращает identifier, но скрипт
        в новый документ не попадает (после reload хука в странице нет).
        А `Page.disable` звать нельзя: в Chromium он очищает список добавленных
        скриптов, то есть «включить и выключить» стёрло бы хук.

        Почему нельзя просто впрыснуть код в текущий документ. Правило слышит
        статусы только через сокеты, созданные уже подменённым `window.WebSocket`.
        Если документ загрузился и вошёл в Oktell РАНЬШЕ, чем появилась наша
        регистрация (окно открыто со вчера; короткий процесс `--open` поставил
        скрипт и вышел, унеся регистрацию с собой; клиент сам перезагрузил
        страницу при разлогине), то сокет у клиента уже свой. Впрыск в такой
        документ ставит флаг `__oktellGuardHooked` и печать нужной версии — и
        этим ЗАКРЫВАЕТ единственную проверку, по которой раньше назначалась
        перезагрузка. Снаружи всё выглядит исправным: сессия видна, правило
        «стоит», отчёт пуст. Проверено живьём 04.09.2026: оператор просидел в
        «Перезвоне» 334 с при пороге 180 — ноль кадров, ноль выбросов.
        Поэтому решение о перезагрузке принимается по факту перехвата сокета,
        а не по версии правила.
        """
        rule_cfg = dict(self.cfg.get("in_window_rule") or {})
        rule_cfg.setdefault("session_keys", self.cfg.get("session_keys", DEFAULT_SESSION_KEYS))
        # Обкатка задаётся на верхнем уровне настроек, а решает правило в окне —
        # без этой строки флаг до него не доезжал и «безопасный» режим выкидывал
        # людей по-настоящему.
        rule_cfg.setdefault("dry_run", bool(self.cfg.get("dry_run")))
        source = build_hook_js(rule_cfg)
        expected = rule_version(rule_cfg)
        # Регистрируем один раз на подключение: метод не заменяет прежний скрипт,
        # а добавляет ещё один, и вызов на каждом опросе копил бы их сотнями.
        if not self._script_registered:
            try:
                page.call("Page.enable")
                page.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
                self._script_registered = True
            except CdpError:
                logging.debug("addScriptToEvaluateOnNewDocument не прошёл", exc_info=True)

        health = self.hook_health(page)
        self._hook_health = health
        session = bool(health.get("session"))
        hooked = bool(health.get("hooked"))
        sockets = int(health.get("sockets") or 0)
        stale = hooked and health.get("ruleVersion") not in (None, expected)
        # Сессии ещё нет (экран входа) — сокета тоже нет. Достаточно впрыска:
        # он и перехватит сокет, который клиент создаст после входа. Этот путь
        # проверен живьём: после входа sockets=1 и кадры доходят.
        if not session:
            if not hooked or stale:
                try:
                    page.evaluate(source)
                    self._hook_health = self.hook_health(page)
                except CdpError:
                    logging.debug("Хук не выполнился в текущем документе", exc_info=True)
            return

        if hooked and not stale and sockets > 0:
            # Правило считает — снимаем отметку о перезагрузке, чтобы вкладка,
            # ослепшая ещё раз, снова получила право на одну починку.
            self._reloaded_for_rule.discard(f"{self._page_target_id}|{expected}")
            return

        # Дальше — документ с живой сессией, которому впрыск уже не поможет:
        # либо правила нет, либо оно слепое, либо старой версии. Лечится только
        # перезагрузкой, причём регистрация должна пережить её — то есть чинит
        # долгоживущий агент, а не короткий `--open` (он отключится раньше, чем
        # новый документ стартует, и оставит страницу вовсе без правила).
        reason = "правила нет" if not hooked else ("устарело" if stale else "не видит сокетов клиента")
        if not self.heal:
            logging.warning("В окне правило %s, но перезагружать не мой режим — починит агент", reason)
            return
        marker = f"{self._page_target_id}|{expected}"
        if marker in self._reloaded_for_rule:
            # Перезагружали и не помогло: молчать нельзя — снаружи это выглядит
            # как исправно работающий ограничитель, который просто никого не ловит.
            logging.error(
                "Правило в окне не работает (%s) даже после перезагрузки: сессия есть, "
                "перехваченных сокетов %s. Оператор сейчас без ограничителя.", reason, sockets
            )
            return
        self._reloaded_for_rule.add(marker)
        logging.info("В окне правило %s — перезагружаю страницу, чтобы оно встало до сокета", reason)
        try:
            page.call("Page.reload", {"ignoreCache": False})
        except CdpError:
            logging.debug("Перезагрузка ради обновления правила не удалась", exc_info=True)

    def probe(self) -> dict:
        """Снимок состояния вкладки Oktell для heartbeat.

        Вместе с сессией снимаем и здоровье правила. Раньше heartbeat говорил
        только «окно есть, сессия есть» — и ровно так выглядела машина, где
        правило стояло слепым и не считало ничего. Отличить одно от другого
        снаружи было нечем, поэтому дыра прожила две недели незамеченной.
        """
        state = {"window": False, "session": False, "login_form": False, "login": None,
                 "url": None, "rule": {}}
        target = self.oktell_target()
        if not target:
            self.close_page()
            self._hook_health = {}
            return state
        state["window"] = True
        state["url"] = target.get("url")
        previous = self._page
        page = self.page(target)
        if not page:
            return state
        # Свежее подключение уже прошло через install_hook внутри page():
        # повторять его в этом же проходе незачем — перезагрузка ещё не успела
        # случиться, и вторая попытка лишь напишет в лог пугающую ошибку.
        fresh_connection = page is not previous
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
        # Здоровье снимаем всегда: install_hook его считает только в момент
        # подключения, а вкладка живёт между опросами и может «ослепнуть» после
        # того, как клиент сам перезагрузит страницу.
        health = self.hook_health(page)
        if health:
            self._hook_health = health
            if state["session"] and not int(health.get("sockets") or 0) and not fresh_connection:
                # Правило ослепло уже после подключения — чиним тем же путём.
                self.install_hook(page)
                self._hook_health = self.hook_health(page) or self._hook_health
        state["rule"] = dict(self._hook_health or {})
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

    def collect_violations(self) -> list:
        """Записи о нарушениях, накопленные страницей."""
        page = self.page()
        if not page:
            return []
        try:
            data = page.evaluate(build_collect_violations_js())
        except Exception:  # noqa: BLE001
            logging.debug("Не удалось прочитать нарушения из страницы", exc_info=True)
            return []
        return data if isinstance(data, list) else []

    def drop_violations(self, keys) -> None:
        page = self.page()
        if not page or not keys:
            return
        try:
            page.evaluate(build_clear_violations_js(keys))
        except Exception:  # noqa: BLE001
            logging.debug("Не удалось очистить отправленные нарушения", exc_info=True)

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

    def autologin(self, login: str, password: str) -> dict:
        """Вписать учётку кабинета в форму входа Oktell."""
        page = self.page()
        if not page:
            return {"ok": False, "reason": "страницы нет"}
        try:
            result = page.evaluate(build_autologin_js(login, password))
        except Exception as exc:  # noqa: BLE001
            logging.debug("Подстановка учётки не удалась", exc_info=True)
            self.close_page()
            return {"ok": False, "reason": str(exc)}
        return result if isinstance(result, dict) else {"ok": False, "reason": "нет ответа"}

    def operator_status(self) -> Optional[dict]:
        """Где оператор сейчас: {status, reason}. None — спросить не у кого."""
        page = self.page()
        if not page:
            return None
        try:
            data = page.evaluate(build_read_status_js())
        except Exception:  # noqa: BLE001
            logging.debug("Статус не прочитан", exc_info=True)
            return None
        return data if isinstance(data, dict) else None

    def set_operator_status(self, status: str, reason_id: Optional[int] = None,
                            verify_s: float = 4.0) -> Optional[dict]:
        """Сменить статус и УБЕДИТЬСЯ, что АТС его применила.

        Возвращает состояние ДО смены ({status, reason}) либо None, если не
        вышло. Проверка обязательна: `setUserStatus` возвращает undefined всегда,
        и по вызову судить о результате нельзя. Без неё агент считал успехом сам
        факт вызова — 21.09.2026 объявление показалось человеку, оставшемуся на
        линии, а в логе не было ни строчки о том, что что-то пошло не так.

        Ждём применения, а не отвечаем сразу: смена статуса идёт на сервер АТС и
        возвращается кадром, то есть занимает сотни миллисекунд.
        """
        page = self.page()
        if not page or not status:
            return None
        before = self.operator_status() or {}
        try:
            result = page.evaluate(build_set_status_js(status, reason_id))
        except Exception:  # noqa: BLE001
            logging.debug("Статус не сменился", exc_info=True)
            return None
        if not (isinstance(result, dict) and result.get("ok")):
            logging.warning("Статус «%s» не поставлен: %s", status,
                            (result or {}).get("reason") if isinstance(result, dict) else "нет ответа")
            return None

        deadline = time.time() + max(0.5, verify_s)
        while time.time() < deadline:
            time.sleep(0.4)
            now = self.operator_status() or {}
            if str(now.get("status") or "") != status:
                continue
            if reason_id is not None and now.get("reason") not in (None, reason_id):
                continue
            logging.info("Статус оператора: было %s/%s, стало %s/%s",
                         before.get("status"), before.get("reason"),
                         now.get("status"), now.get("reason"))
            return {"status": before.get("status") or "ready", "reason": before.get("reason")}

        # Самая частая причина — оператор не на линии: из «Без телефона» АТС на
        # перерыв не переводит. Молчать об этом нельзя: снаружи это выглядит как
        # «программа не работает», и искать будут в программе.
        now = self.operator_status() or {}
        logging.warning(
            "АТС не применила статус «%s»: оператор остался %s/%s (на линии: %s). "
            "Объявление покажем, но звонок может прийти во время чтения",
            status, now.get("status"), now.get("reason"), now.get("inCallCenter"))
        return None

    # Показ объявления раньше жил здесь — внутри страницы Oktell. Переехал в
    # собственное окно поверх всех окон (NewsOverlay): в странице он исчезал
    # вместе со свёрнутым клиентом АТС, а объявление обязательное. Держать обе
    # дороги нельзя: правила показа, задержки кнопки и разбора теста разъехались
    # бы молча.

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


def rule_alive(browser: dict) -> Optional[bool]:
    """Считает ли правило прямо сейчас. None — сказать нечего (нет сессии/окна).

    Живым считается только правило, перехватившее хотя бы один сокет клиента:
    без этого кадры статуса до него не доходят, и «стоит» оно лишь на словах.
    """
    browser = browser or {}
    if not browser.get("window") or not browser.get("session"):
        return None
    rule = browser.get("rule") or {}
    if not rule:
        return None
    if not rule.get("enabled"):
        return False
    return bool(rule.get("hooked")) and int(rule.get("sockets") or 0) > 0


def build_heartbeat_payload(state: AgentState, cfg: dict, now_iso: str) -> dict:
    """Наружу уходит минимум: кто, где, есть ли управляемая сессия, что с командой."""
    browser = state.browser or {}
    rule = browser.get("rule") or {}
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
        # Отдельно от browser: «сессия есть» и «правило считает» — разные вещи,
        # и раздел обязан их различать, иначе слепое правило выглядит рабочим.
        "rule": {
            "alive": rule_alive(browser),
            "enabled": bool(rule.get("enabled")) if rule else None,
            "sockets": int(rule.get("sockets") or 0),
            "seconds": int(rule.get("seconds") or 0),
            "threshold_s": int(rule.get("thresholdS") or 0),
            "version": rule.get("ruleVersion"),
        },
        "unmanaged_windows": list(state.unmanaged or []),
        "last_command": state.last_command,
    }


class ServerLink:
    def __init__(self, cfg: dict, session: Optional[dict] = None):
        self.cfg = cfg
        self.base = str(cfg.get("server_url") or "").rstrip("/")
        self.timeout = float(cfg.get("request_timeout_s", 10))
        self.verify = bool(cfg.get("verify_tls", True))
        import requests

        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self.set_operator_session(session or {})

    def set_operator_session(self, session: dict) -> None:
        """Подменить сессию оператора (вход, обновление, выход)."""
        self.operator_session = session or {}
        self._session.headers.pop("Authorization", None)
        self._session.headers.update(agent_headers(self.cfg, self.operator_session))

    def _refresh_operator_session(self) -> bool:
        """Обновить access по refresh. Зовётся ровно на 401 нашей ручки.

        401 на агентской ручке — это не «токен машины неверен» (он вшит в exe и
        не меняется), а истёкший access оператора: он живёт ~30 минут, а агент
        работает сменами. Без этого объявления пропадали бы через полчаса после
        входа, и выглядело бы это как «сервер их не отдаёт».
        """
        if not self.operator_session:
            return False
        updated = icore_refresh(self.cfg, self.operator_session)
        if updated.get("error"):
            if not updated.get("keep"):
                logging.info("Сессия оператора истекла — работаем без неё до следующего входа")
                clear_session()
                self.set_operator_session({})
            return False
        save_session(updated)
        self.set_operator_session(updated)
        return True

    def _request(self, method: str, url: str, **kwargs):
        """Запрос с одной попыткой обновить сессию на 401."""
        response = self._session.request(method, url, timeout=self.timeout,
                                         verify=self.verify, **kwargs)
        if response.status_code == 401 and self._refresh_operator_session():
            response = self._session.request(method, url, timeout=self.timeout,
                                             verify=self.verify, **kwargs)
        return response

    def _url(self, path_key: str, default_path: str) -> str:
        path = str(self.cfg.get(path_key) or default_path)
        if path.startswith("http"):
            return path
        return f"{self.base}{path}"

    def heartbeat(self, payload: dict) -> Optional[dict]:
        url = self._url("heartbeat_path", "/api/oktell_guard/heartbeat")
        response = self._request("POST", url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    def violations(self, payload: dict) -> bool:
        url = self._url("violations_path", "/api/oktell_guard/violations")
        response = self._request("POST", url, json=payload)
        response.raise_for_status()
        return True

    def news(self) -> Optional[dict]:
        """Что показать этому оператору. None — сервер недоступен."""
        url = self._url("news_path", "/api/oktell_guard/news")
        try:
            response = self._request("GET", url)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            logging.debug("Объявления не получены", exc_info=True)
            return None

    def news_read(self, news_id: int, answers: dict) -> dict:
        """Подтверждение. Решение принимает сервер, мы только передаём ответ."""
        url = f"{self.base}/api/oktell_guard/news/{int(news_id)}/read"
        try:
            response = self._request("POST", url, json={"answers": answers or {}})
            payload = {}
            try:
                payload = response.json() or {}
            except Exception:  # noqa: BLE001
                payload = {}
            payload["ok"] = response.status_code == 200
            return payload
        except Exception as exc:  # noqa: BLE001
            logging.warning("Подтверждение не доставлено: %s", exc)
            return {"ok": False, "error": "Нет связи с iCORE — попробуйте ещё раз"}

    def ack(self, payload: dict) -> None:
        url = self._url("ack_path", "/api/oktell_guard/ack")
        try:
            self._request("POST", url, json=payload)
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
    # Обновление меняет только exe, ярлык остаётся прежним — значит после
    # перехода на новую версию его надо один раз переписать, иначе значок у уже
    # работающих машин так и останется старым из кэша Проводника. Признак «ещё
    # не переписывали» — отсутствие файла значка рядом с программой.
    if IS_WINDOWS and is_installed_copy() and not icon_path().exists():
        if ensure_icon_file() and shortcut_path().exists():
            logging.info("Значок ярлыка обновлён: %s", _create_shortcut(installed_path()))
    cfg = wait_for_valid_config(cfg)
    if cfg.get("_token_error"):
        logging.error("Токен агента не применён: %s. Задай токен латиницей и пересобери.",
                      cfg["_token_error"])
    logging.info("Режим AGENT. Сервер: %s | Oktell: %s", cfg.get("server_url"), cfg.get("oktell_url"))
    if cfg.get("dry_run"):
        logging.warning("DRY-RUN включён: команды разлогина будут только логироваться")

    # ask=False: окно входа поднимает только ярлык. Фоновый процесс, ткнувший
    # оператору форму посреди разговора, — это ровно то, чего программа делать
    # не должна; без сессии она работает как ограничитель и молчит.
    session = ensure_session(cfg, ask=False)
    if session:
        logging.info("Сессия оператора: %s", session.get("user_name") or session.get("login") or "есть")
    else:
        logging.info("Сессии нет — работаю ограничителем, объявлений не показываю")

    cfg = fetch_server_config(cfg, session=session)
    cfg = wait_for_configuration(cfg, session=session)
    logging.info("Настройки применены. Oktell: %s | порог: %s с",
                 cfg.get("oktell_url"), (cfg.get("in_window_rule") or {}).get("threshold_s"))
    identity = current_identity(cfg)
    browser = ManagedBrowser(cfg)
    ledger = CommandLedger(app_dir() / "commands.json")
    link = ServerLink(cfg, session)
    # Объявление живёт в СВОЁМ окне поверх всех, а не внутри страницы Oktell:
    # там оно исчезало вместе со свёрнутым клиентом АТС.
    news_overlay = NewsOverlay(cfg, browser)

    browser_cfg = cfg.get("browser", {}) or {}
    if browser_cfg.get("launch_on_start", True):
        browser.ensure_running()
        # Фокус не забираем: агента мог перезапустить сторож посреди смены,
        # дёргать окно оператору в этот момент незачем — достаточно развернуть.
        browser.ensure_window_visible(bring_to_front=False)

    poll_s = float(cfg.get("poll_interval_s", 60))
    max_backoff = float(cfg.get("offline_max_backoff_s", 60))
    failures = 0
    unauthorized_notified = [False]   # окно про отозванный пропуск показываем один раз
    last_command_report: Optional[dict] = None
    update_every_s = max(600.0, float(cfg.get("update_check_hours", 6)) * 3600.0)
    next_update_check = time.time()
    # Настройки раздела (порог, обкатка, вкл/выкл) раньше читались РОВНО один
    # раз — при старте процесса. Правка порога доезжала до работающего агента
    # только после перезахода в Windows или самообновления, то есть могла не
    # доехать вовсе за смену.
    config_every_s = max(60.0, float(cfg.get("config_refresh_minutes", 10)) * 60.0)
    next_config_refresh = time.time() + config_every_s
    last_config_login = ""
    # Сколько кругов подряд не удалось поднять сторожа и сколько ещё пропустить.
    watchdog_failures = 0
    watchdog_skip = 0
    # Надстройка над ограничителем: вход по учётке iCORE и обязательные
    # объявления. Живут в том же цикле — своего процесса им не нужно.
    news_every_s = max(30.0, float(cfg.get("news_poll_s", 60)))
    next_news_check = time.time()
    active_news: Optional[dict] = None     # показанное объявление, ждём подтверждения
    training_set = False                   # это мы сняли оператора с линии
    status_before: dict = {}               # куда возвращать: туда, где он был,
                                           # включая ПРИЧИНУ перерыва: обед и
                                           # тренинг — оба `break`, и один
                                           # статус их не различает

    def rule_print(current: dict) -> str:
        """Отпечаток того, что реально уедет в окно: правило плюс обкатка."""
        return rule_version({**(current.get("in_window_rule") or {}),
                             "dry_run": bool(current.get("dry_run"))})

    def handle_news_press() -> bool:
        """Разобрать нажатие «Подтвердить» в окне объявления. True — разобрали.

        Вынесено из круга НАМЕРЕННО: круг у агента минута, и пока разбор жил в
        нём, человек после нажатия до минуты смотрел на «Отправляем…» — ни
        ответа, ни признака, что его вообще услышали. Теперь этой функции ждут
        каждые полсекунды, пока объявление на экране (см. wait_for_next_round).
        """
        nonlocal active_news, training_set, next_news_check, status_before
        if active_news is None:
            return False
        if not news_overlay.alive():
            # Окно закрыли, не подтвердив. Объявление обязательное — показываем
            # снова: «закрыл крестиком» не может быть способом его не читать.
            logging.info("Окно объявления закрыли — показываю снова")
            if not news_overlay.show(active_news):
                return False
            return True
        news_overlay.hold_on_top()
        pressed = news_overlay.result()
        if not pressed:
            return False
        verdict = link.news_read(pressed.get("id"), pressed.get("answers") or {})
        if verdict.get("ok"):
            news_overlay.close()
            # Возвращаем статус только если сами его и забрали: у того, кто к
            # моменту объявления уже был на перерыве, статус не наш.
            if training_set:
                browser.set_operator_status(status_before.get("status"),
                                            status_before.get("reason"))
                training_set = False
            logging.info("Объявление #%s подтверждено", pressed.get("id"))
            active_news = None
            next_news_check = time.time()   # очередь может быть длиннее одного
        else:
            news_overlay.feedback(verdict)
        return True

    def wait_for_next_round(seconds: float) -> None:
        """Пауза до следующего круга, но с ухом на окне объявления.

        Спать целую минуту можно только когда на экране ничего не ждут ответа.
        Пока объявление показано, режем сон на полсекунды и на каждой проверяем
        нажатие: для человека это «нажал — ответили», а не «нажал — тишина».
        """
        if active_news is None:
            time.sleep(seconds)
            return
        step = 0.5
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(min(step, max(0.0, deadline - time.time())))
            try:
                if handle_news_press() and active_news is None:
                    # Подтвердили — дальше ждать нечего, идём на круг за
                    # следующим объявлением очереди.
                    return
            except Exception:  # noqa: BLE001 — разбор нажатия не должен ронять цикл
                logging.debug("Разбор нажатия не удался", exc_info=True)
                return

    def adopt_new_login() -> bool:
        """Заметить вход, случившийся уже после старта агента.

        Вход происходит в ДРУГОМ процессе — том, что запустил ярлык. Агент же
        читает сессию один раз, при своём старте, а стартует он раньше: сторож
        поднимает его при входе в Windows. Без этой проверки работающий агент
        навсегда оставался «без сессии», ходил за настройками без Bearer и не
        получал ни кабинета, ни объявлений — ровно это и увидели 18.09.2026.

        Читаем только файл, без сети: сессию, которую сами же и обновили, узнаём
        по совпадению access-токена и второй раз не подхватываем.
        """
        stored = load_session()
        if not session_is_fresh(stored):
            stored = {}
        if (stored.get("access_token") or "") == (link.operator_session or {}).get("access_token", ""):
            return False
        link.set_operator_session(stored)
        if stored:
            logging.info("Замечен вход: %s", stored.get("user_name") or stored.get("login") or "оператор")
        else:
            logging.info("Сессия оператора кончилась — объявления не показываю")
        return True

    try:
        while True:
            try:
                if adopt_new_login():
                    # Настройки перечитываем сразу: именно в них приезжает
                    # учётка кабинета, и ждать общего таймера незачем.
                    next_config_refresh = time.time()
                if time.time() >= next_update_check:
                    next_update_check = time.time() + update_every_s
                    if check_for_update(cfg):
                        logging.info("Завершаюсь: работу продолжит обновлённая копия")
                        return 0

                # Взаимный сторож: агент поднимает watchdog, watchdog — агента.
                # Убить контроль можно только сняв обе копии в одном узком окне.
                # Симметрично сторожу: если вторая копия не поднимается, не
                # дёргаем её каждый круг. Упавшая сборка без консоли оставляет
                # висеть модальное окно, и минутный цикл превращался в стопку
                # таких окон за смену.
                if cfg.get("ensure_watchdog_alive", True) and not is_running_by_mutex(WATCHDOG_MUTEX_NAME):
                    if watchdog_skip > 0:
                        watchdog_skip -= 1
                    else:
                        logging.info("Watchdog не обнаружен — поднимаю")
                        spawn_self()
                        time.sleep(1.0)
                        if is_running_by_mutex(WATCHDOG_MUTEX_NAME):
                            watchdog_failures = 0
                        else:
                            watchdog_failures += 1
                            # Пропускаем тем больше кругов, чем дольше не выходит.
                            watchdog_skip = min(30, 2 ** min(watchdog_failures, 5))
                            if watchdog_failures == 3:
                                logging.error(
                                    "Watchdog не поднимается три раза подряд — пробую реже. "
                                    "Похоже, сломана сборка."
                                )
                elif watchdog_failures:
                    watchdog_failures = 0
                    watchdog_skip = 0

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

                # Логин оператора известен только из живой вкладки, а личный
                # порог сервер отдаёт только по логину. Поэтому спрашиваем
                # настройки заново, как только логин появился или сменился
                # (посменная работа на одной машине), и повторяем по таймеру.
                login_now = str(state.browser.get("login") or "")
                if (login_now and login_now != last_config_login) or time.time() >= next_config_refresh:
                    next_config_refresh = time.time() + config_every_s
                    was = rule_print(cfg)
                    cfg = fetch_server_config(cfg, login_now, session=link.operator_session)
                    last_config_login = login_now
                    if rule_print(cfg) != was:
                        rule_now = cfg.get("in_window_rule") or {}
                        logging.info(
                            "Правило изменилось (порог %s с, обкатка %s) — доношу до вкладки",
                            rule_now.get("threshold_s"), bool(cfg.get("dry_run")),
                        )
                        browser.apply_new_rule()

                payload = build_heartbeat_payload(state, cfg, now_iso())
                data = link.heartbeat(payload)
                failures = 0

                # Нарушения, накопленные страницей, уезжают на сервер. Без этого
                # правило срабатывало, а в отчёте не появлялось ничего.
                pending = browser.collect_violations()
                if pending:
                    sent = link.violations({
                        "agent_id": identity.agent_id,
                        "operator_login": state.browser.get("login") or identity.operator_login,
                        "hostname": identity.hostname,
                        "windows_user": identity.windows_user,
                        "version": VERSION,
                        "dry_run": bool(cfg.get("dry_run")),
                        "violations": pending,
                    })
                    if sent:
                        keys = [str(item.get("key") or f"{item.get('login', '')}|{item.get('at', '')}")
                                for item in pending]
                        browser.drop_violations(keys)
                        logging.info("Отправлено нарушений: %d", len(pending))

                # ── Вход по учётке iCORE ──────────────────────────────────
                # Оператор ввёл логин и пароль iCORE ещё при скачивании файла —
                # больше от него ничего не требуется: пару от кабинета АТС
                # сервер отдал вместе с настройками, и вписывает её агент.
                cabinet = cfg.get("cabinet") or {}
                if state.browser.get("login_form") and cabinet.get("login"):
                    filled = browser.autologin(cabinet.get("login"), cabinet.get("password"))
                    if filled.get("ok"):
                        logging.info("Учётка кабинета подставлена в форму входа")
                    else:
                        logging.debug("Подстановка не выполнена: %s", filled.get("reason"))

                # ── Обязательные объявления ───────────────────────────────
                rule_state = state.browser.get("rule") or {}
                in_call = bool(rule_state.get("inCall"))
                if active_news is None and time.time() >= next_news_check:
                    next_news_check = time.time() + news_every_s
                    if state.browser.get("session") and not in_call:
                        answer = link.news() or {}
                        item = answer.get("item")
                        if item:
                            # Порядок именно такой: сначала снять с линии, потом
                            # показать. Иначе между окном и сменой статуса есть
                            # щель, в которую АТС успевает направить звонок.
                            status_before = browser.set_operator_status(
                                "break", int(cfg.get("training_reason_id", 3)))
                            training_set = bool(status_before)
                            if not training_set:
                                logging.warning(
                                    "Снять с линии не вышло — объявление покажем, "
                                    "но звонок может прийти во время чтения")
                            if news_overlay.show(item):
                                active_news = item
                            elif training_set:
                                browser.set_operator_status(status_before.get("status"),
                                                            status_before.get("reason"))
                                training_set = False

                handle_news_press()

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

                wait_for_next_round(poll_s)
            except KeyboardInterrupt:
                logging.info("Агент остановлен с клавиатуры")
                return 0
            except Exception as exc:  # noqa: BLE001 — сеть/браузер не должны ронять агента
                failures += 1
                delay = backoff_delay(failures, poll_s, max_backoff)
                if "401" in str(exc):
                    # Сюда доходят только 401, которые не вылечило обновление
                    # сессии (ServerLink._request пробует его сам). Значит дело
                    # не в истёкшем access, а во вшитом токене машины: его
                    # сменили на сервере, а сборку не разнесли. Молчать нельзя —
                    # агент жив, но сервер его не принимает, и снаружи это
                    # выглядит как «программа не работает».
                    logging.error(
                        "Сервер не принимает пропуск машины (401). Похоже, токен сборки "
                        "сменили — нужна новая версия программы."
                    )
                    if not unauthorized_notified[0]:
                        unauthorized_notified[0] = True
                        show_message(
                            "Программа не может связаться с сервером: пропуск больше не действует. "
                            "Сообщите в IT — нужна новая версия программы.",
                            error=True,
                        )
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
    # Пауза между НЕУДАЧНЫМИ попытками растёт. Без этого один сорвавшийся запуск
    # превращался в шторм: сторож дёргал агента каждые ~5 с, каждая попытка
    # распаковывала 16 МБ во временную папку, а при сборке без консоли упавший
    # процесс ВИСИТ с модальным окном «Unhandled exception in script» и мьютекс
    # не берёт — то есть окна копились, пока их закрывали руками. Ровно это
    # случилось 07.09.2026 при переходе на 1.0.14: с 14:19 до 14:27 сторож сделал
    # больше сотни попыток. Причину запуска чинит сборка, а шторм — этот отсчёт.
    retry_s = check_s
    max_retry_s = max(60.0, float(cfg.get("watchdog_max_retry_s", 300)))
    failures = 0
    complained = False
    try:
        while True:
            try:
                if is_running_by_mutex(AGENT_MUTEX_NAME):
                    if failures:
                        logging.info("Агент поднялся, попыток было %d", failures)
                    failures = 0
                    retry_s = check_s
                    complained = False
                    time.sleep(check_s)
                    continue

                logging.info("Агент не обнаружен — запускаю")
                spawn_self("--agent")
                time.sleep(grace_s)
                if is_running_by_mutex(AGENT_MUTEX_NAME):
                    failures = 0
                    retry_s = check_s
                    complained = False
                    time.sleep(check_s)
                    continue

                failures += 1
                # Жалуемся ОДИН раз: строка в логе на каждую попытку — это тот
                # же шум, только в файле, и он вытесняет из лога всё остальное
                # (за восемь минут набежало 47 КБ).
                if not complained and failures >= 3:
                    complained = True
                    logging.error(
                        "Агент не поднимается (%d попытки подряд). Дальше пробую реже, "
                        "с паузой до %.0f c. Скорее всего сломана сама сборка.",
                        failures, max_retry_s,
                    )
                elif failures < 3:
                    logging.warning("Агент не поднялся после запуска (попытка %d)", failures)
                time.sleep(retry_s)
                retry_s = min(max_retry_s, retry_s * 2)
            except KeyboardInterrupt:
                return 0
            except Exception:  # noqa: BLE001
                logging.exception("Ошибка цикла watchdog")
                time.sleep(check_s)
    finally:
        release_mutex(WATCHDOG_MUTEX_NAME)


def run_open(cfg: dict) -> int:
    """Ярлык «Oktell» на рабочем столе ведёт сюда: окно входа, потом клиент АТС."""
    setup_logging(cfg, "agent.log")
    if cfg.get("_config_error"):
        logging.error("Конфиг %s не читается (%s) — окно не открываю", cfg.get("_config_path"), cfg["_config_error"])
        return 2
    # Вход ДО открытия клиента, а не после: логин с паролем АТС подставляет
    # агент, и взять их он может только у сервера, назвав, кто за машиной.
    # Открыть окно раньше входа значило бы показать оператору пустую форму
    # Oktell с паролем, которого он не знает.
    session = ensure_session(cfg)
    if not session:
        # Отказ от входа — законный ответ, а не сбой: закрыл окно, значит
        # Oktell не открываем (так же ведёт себя iCORE Phone).
        logging.info("Вход не выполнен — окно Oktell не открываю")
        return 3
    cfg = fetch_server_config(cfg, session=session)
    if not is_configured(cfg):
        logging.error("Нет настроек (адрес Oktell не задан) — окно не открываю")
        return 2
    # heal=False: этот процесс сейчас же завершится, а вместе с ним уйдёт и
    # регистрация скрипта. Перезагрузи он страницу — новый документ остался бы
    # вовсе без правила. Слепую вкладку починит долгоживущий агент.
    browser = ManagedBrowser(cfg, heal=False)
    ok = browser.ensure_running()
    if ok:
        browser.wait_for_page()
        browser.ensure_window_visible(bring_to_front=True)
    if ok and not browser.oktell_target():
        # Chrome уже был жив, но окна Oktell нет — открываем ещё одно.
        chrome = browser.chrome_path()
        if chrome:
            subprocess.Popen(browser.launch_args(chrome), close_fds=True, env=child_env())
    if ok:
        fill_oktell_login(browser, cfg)
    # Автозапуск watchdog: оператор открыл Oktell — контроль обязан быть поднят.
    if cfg.get("ensure_watchdog_alive", True) and not is_running_by_mutex(WATCHDOG_MUTEX_NAME):
        spawn_self()
    return 0 if ok else 1


def fill_oktell_login(browser: "ManagedBrowser", cfg: dict, wait_s: float = 20.0) -> bool:
    """Вписать учётку кабинета в только что открытую форму Oktell.

    Делает это САМ процесс ярлыка, а не долгоживущий агент. Сначала подстановка
    жила только в цикле агента — и не срабатывала ни разу: агент читает сессию
    при своём старте, то есть ДО входа, а вход происходит уже в процессе ярлыка.
    Работающий агент о нём не знал, ходил за настройками без Bearer и кабинета
    не получал (проверено живьём 18.09.2026 на учётке Зиноллаева). Даже когда
    агент научился замечать вход, оставлять подстановку только ему нельзя:
    круг у него минута, а человек всё это время смотрит на пустую форму.

    Форма появляется не мгновенно — ждём её, а не пробуем один раз.
    """
    cabinet = cfg.get("cabinet") or {}
    if not (cabinet.get("login") and cabinet.get("password")):
        logging.info("Учётка кабинета не задана — форму Oktell оператор заполняет сам")
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        state = browser.probe()
        if state.get("session"):
            # Уже внутри: Chrome помнит прошлую сессию Oktell, формы нет.
            return True
        if state.get("login_form"):
            result = browser.autologin(cabinet.get("login"), cabinet.get("password"))
            if result.get("ok"):
                logging.info("Учётка кабинета подставлена в форму входа")
                return True
            logging.warning("Подстановка не выполнена: %s", result.get("reason"))
            return False
        time.sleep(1.0)
    logging.info("Форма входа Oktell за %.0f c не появилась — подстановку пропускаю", wait_s)
    return False


def run_status(cfg: dict) -> int:
    browser = ManagedBrowser(cfg)
    identity = current_identity(cfg)
    state = AgentState(identity=identity)
    state.browser = browser.probe() if browser.is_debug_port_alive() else {}
    unmanaged_cfg = cfg.get("unmanaged", {}) or {}
    if unmanaged_cfg.get("detect", True) and not state.browser.get("window"):
        state.unmanaged = match_unmanaged_titles(list_window_titles(), unmanaged_cfg.get("window_title_patterns", []))
    payload = build_heartbeat_payload(state, cfg, now_iso())
    saved_session = load_session()
    payload["_local"] = {
        "config": cfg.get("_config_path"),
        "config_error": cfg.get("_config_error"),
        "log_dir": str(app_dir()),
        "cdp_port": browser.devtools_port(),
        "agent_running": is_running_by_mutex(AGENT_MUTEX_NAME),
        "watchdog_running": is_running_by_mutex(WATCHDOG_MUTEX_NAME),
        # Кто вошёл. Сами токены сюда не кладём: --status печатают в переписке.
        "session": {
            "login": saved_session.get("login") or "",
            "user_name": saved_session.get("user_name") or "",
            "fresh": session_is_fresh(saved_session),
        },
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


def run_update_now(cfg: dict, quiet: bool = False) -> int:
    """«Обновиться сейчас» — то же обновление, но по требованию человека.

    Само оно приходит при старте и раз в 6 часов, и почти всегда этого хватает.
    Не хватает ровно тогда, когда версия сломана: ждать полдня, пока агент
    соберётся проверить, оператору нечем. Отдельный режим отвечает СЛОВАМИ —
    «обновились», «уже последняя», «сервер не ответил», — иначе человек нажимает
    и не понимает, случилось что-нибудь или нет.

    Возврат: 0 — обновились (процесс сейчас же сменится новым), 1 — уже
    последняя, 2 — не вышло.
    """
    setup_logging(cfg, "agent.log")
    if not getattr(sys, "frozen", False):
        logging.info("Обновление из исходников не ставится")
        return 1

    manifest = fetch_update_manifest(cfg)
    if not manifest:
        logging.warning("Обновление: сервер не ответил")
        if not quiet:
            show_message("Не получилось спросить сервер о новой версии. "
                         "Проверьте интернет и попробуйте ещё раз.", error=True)
        return 2

    remote = str(manifest.get("version") or "")
    if not should_update(VERSION, remote):
        logging.info("Обновление: у вас уже %s, на сервере %s", VERSION, remote or "—")
        if not quiet:
            show_message(f"У вас последняя версия — {VERSION}.")
        return 1

    downloaded = download_update(cfg, manifest)
    if not downloaded or not apply_update(downloaded):
        logging.error("Обновление до %s не установилось", remote)
        if not quiet:
            show_message(f"Новая версия {remote} есть, но установить её не вышло. "
                         "Сообщите в IT.", error=True)
        return 2

    logging.info("Обновление до %s установлено вручную", remote)
    if not quiet:
        show_message(f"Обновились до версии {remote}. Программа перезапустилась.")
    return 0


def run_sign_out(cfg: dict) -> int:
    """«Выход»: закончить работу на этой машине целиком.

    Выход — это ЧЕЛОВЕК ушёл, а не «переключить учётку». Поэтому уходит всё
    сразу: сессия Oktell, окно клиента, сессия iCORE и сама программа. Оставь
    мы хоть что-то — получилась бы дыра, и каждая по-своему:

    * не разлогинить Oktell — следующий за этой машиной попадает в чужую
      сессию АТС: cookie живёт в профиле Chrome, а не в нашей сессии;
    * не закрыть окно — остаётся окно Oktell, за которым уже никто не следит:
      ограничитель живёт в процессе, а его мы гасим;
    * не погасить процессы — сторож поднимет агента обратно, и он продолжит
      отмечаться за ушедшего.

    ВЫБРОС ПО ПРАВИЛУ — НЕ ЭТО. Там оператор остаётся за машиной, и программу
    трогать нельзя: она обязана продолжать считать. Выброс делает только
    WS-logout в окне (ManagedBrowser.logout), процессы не задеваются вовсе.
    """
    setup_logging(cfg, "agent.log")
    session = load_session()
    who = session.get("user_name") or session.get("login") or ""

    # Сначала АТС, пока окно и агент ещё живы: после остановки процессов
    # разлогинить будет нечем.
    try:
        browser = ManagedBrowser(cfg, heal=False)
        if browser.is_debug_port_alive():
            target = browser.oktell_target()
            if target:
                report = browser.logout()
                logging.info("Выход: разлогин в Oktell — %s", report.get("status"))
                browser.close_page()
                _close_target(browser, str(target.get("id") or ""))
    except Exception:  # noqa: BLE001 — не закрывшееся окно не повод оставить сессию
        logging.debug("Выход: окно Oktell закрыть не удалось", exc_info=True)

    clear_session()
    logging.info("Выход из учётной записи%s — останавливаю программу", f": {who}" if who else "")
    if getattr(sys, "stdout", None):
        print(json.dumps({"ok": True, "signed_out": who}, ensure_ascii=False))
    # Гасим последними: этот процесс себя и своего родителя не трогает, поэтому
    # строку выше напечатать успеваем.
    _stop_installed_copies(installed_path())
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="OktellRecallGuard", add_help=True)
    parser.add_argument("--agent", action="store_true", help="рабочий цикл агента")
    parser.add_argument("--watchdog", action="store_true", help="сторож (режим по умолчанию)")
    parser.add_argument("--open", action="store_true", help="открыть управляемое окно Oktell")
    parser.add_argument("--sign-out", dest="sign_out", action="store_true",
                        help="забыть учётку iCORE на этой машине")
    parser.add_argument("--update", dest="update_now", action="store_true",
                        help="проверить и поставить новую версию прямо сейчас")
    parser.add_argument("--logout-now", action="store_true", help="разлогинить прямо сейчас (проверка)")
    parser.add_argument("--status", action="store_true", help="состояние в JSON")
    parser.add_argument("--install", action="store_true", help="установить себя и запустить")
    parser.add_argument("--quiet", action="store_true", help="с --install: без окон (скрипт IT)")
    parser.add_argument(DEPLOYED_ARG, dest="deployed", action="store_true",
                        help="вход пользователя на компьютере, куда программу поставил MSI")
    parser.add_argument("--uninstall", action="store_true", help="удалить автозапуск, задачу и ярлык")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {VERSION}")
        return 0

    cfg = load_config(Path(args.config) if args.config else None)

    if args.install:
        return run_install(cfg, quiet=args.quiet)
    if args.deployed:
        return run_machine_logon(cfg)
    if args.uninstall:
        return run_uninstall(cfg)
    if args.logout_now:
        return run_logout_now(cfg)
    if args.sign_out:
        return run_sign_out(cfg)
    if args.update_now:
        return run_update_now(cfg, quiet=args.quiet)
    if args.status:
        return run_status(cfg)
    # Всё ниже поднимает контроль. Копия пользователя от снятой установки на
    # компьютер делать этого не должна — её снимаем вместо запуска.
    if getattr(sys, "frozen", False) and is_installed_copy() and deployment_orphaned(read_deployment_marker()):
        return run_orphan_cleanup(cfg)
    if args.agent:
        return run_agent(cfg)
    if args.open:
        return run_open(cfg)
    # Запуск без аргументов из папки «Загрузки» = сотрудник скачал файл с iCORE
    # и кликнул по нему. Ставим себя сами: никаких .bat и распаковок.
    if getattr(sys, "frozen", False) and not args.watchdog and not is_installed_copy():
        return run_install(cfg)
    return run_watchdog(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
