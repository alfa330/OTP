"""Тесты агента «Oktell Recall Guard» (oktell_recall_guard/agent.py).

Проверяем только то, что не требует Windows и браузера: разбор конфига,
идемпотентность команд, выбор вкладки, формируемый JS и полезную нагрузку
heartbeat. Сам разлогин проверяется стендом dev_harness/mock_server.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "oktell_recall_guard" / "agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("oktell_recall_guard_agent", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = pytest.importorskip("requests") and _load_agent()


# --------------------------------------------------------------------------- #
# Конфиг
# --------------------------------------------------------------------------- #

def test_config_defaults_are_not_shared_between_loads(tmp_path):
    """Дефолты копируются, а не отдаются ссылкой: правка одного конфига не
    должна протекать в следующий загруженный."""
    first = agent.load_config(tmp_path / "нет-такого.json")
    first["session_keys"].append("подмена")
    second = agent.load_config(tmp_path / "нет-такого.json")
    assert second["session_keys"] == ["___oktellsessionid"]


def test_config_file_overrides_and_normalization(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "server_url": "https://icore.example.com/",
                "poll_interval_s": 0,          # ниже границы
                "request_timeout_s": 999,      # выше границы
                "session_keys": ["", "  "],    # мусор
                "browser": {"cdp_port": 70000},
            }
        ),
        encoding="utf-8",
    )
    cfg = agent.load_config(path)
    assert cfg["server_url"] == "https://icore.example.com"  # хвостовой слеш срезан
    assert cfg["poll_interval_s"] == 2
    assert cfg["request_timeout_s"] == 60
    assert cfg["session_keys"] == ["___oktellsessionid"]     # пустые ключи отброшены
    assert cfg["browser"]["cdp_port"] == 65535
    # Ключи, которых нет в файле, берутся из дефолтов, а не теряются.
    assert cfg["browser"]["app_mode"] is True


def test_broken_config_does_not_crash(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{это не json", encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg["poll_interval_s"] == agent.DEFAULT_CONFIG["poll_interval_s"]


def test_offline_backoff_never_below_poll_interval(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"poll_interval_s": 30, "offline_max_backoff_s": 5}), encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg["offline_max_backoff_s"] >= cfg["poll_interval_s"]


# --------------------------------------------------------------------------- #
# Идемпотентность команд
# --------------------------------------------------------------------------- #

def test_command_ledger_blocks_repeat(tmp_path):
    ledger = agent.CommandLedger(tmp_path / "commands.json")
    assert ledger.seen("cmd-1") is False
    ledger.mark("cmd-1", "done")
    assert ledger.seen("cmd-1") is True


def test_command_ledger_survives_restart(tmp_path):
    path = tmp_path / "commands.json"
    agent.CommandLedger(path).mark("cmd-7", "done")
    # Перезапуск агента не должен превращать один «перезвон» в два разлогина.
    assert agent.CommandLedger(path).seen("cmd-7") is True


def test_command_ledger_forgets_after_ttl(tmp_path):
    ledger = agent.CommandLedger(tmp_path / "commands.json", ttl_s=60)
    ledger.mark("cmd-2", "done", now=1_000_000.0)
    assert ledger.seen("cmd-2", now=1_000_030.0) is True
    assert ledger.seen("cmd-2", now=1_000_100.0) is False


def test_command_ledger_trims_to_limit(tmp_path):
    ledger = agent.CommandLedger(tmp_path / "commands.json", limit=3)
    for i in range(10):
        ledger.mark(f"cmd-{i}", "done", now=1_000_000.0 + i)
    assert len(ledger._items) == 3
    assert ledger.seen("cmd-9", now=1_000_010.0) is True
    assert ledger.seen("cmd-0", now=1_000_010.0) is False


def test_broken_ledger_file_is_ignored(tmp_path):
    path = tmp_path / "commands.json"
    path.write_text("не json", encoding="utf-8")
    ledger = agent.CommandLedger(path)
    assert ledger.seen("cmd-1") is False
    ledger.mark("cmd-1", "done")
    assert ledger.seen("cmd-1") is True


def test_backoff_grows_and_is_capped():
    assert agent.backoff_delay(0, 5, 60) == 5
    assert agent.backoff_delay(1, 5, 60) == 10
    assert agent.backoff_delay(2, 5, 60) == 20
    assert agent.backoff_delay(20, 5, 60) == 60


# --------------------------------------------------------------------------- #
# Выбор вкладки Oktell
# --------------------------------------------------------------------------- #

ORIGIN = "https://oktell.example.local"


def _target(url, **kwargs):
    base = {"type": "page", "url": url, "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/1", "id": "T1"}
    base.update(kwargs)
    return base


def test_pick_target_matches_origin():
    targets = [
        _target("devtools://devtools/bundled/inspector.html"),
        _target("chrome://newtab/"),
        _target("https://mail.example.com/"),
        _target(f"{ORIGIN}/#/phone", id="T9"),
    ]
    picked = agent.pick_oktell_target(targets, ORIGIN)
    assert picked and picked["id"] == "T9"


def test_pick_target_ignores_service_workers_and_missing_ws():
    targets = [
        _target(f"{ORIGIN}/sw.js", type="service_worker"),
        _target(f"{ORIGIN}/", webSocketDebuggerUrl=""),
    ]
    assert agent.pick_oktell_target(targets, ORIGIN) is None


def test_pick_target_requires_origin_prefix_not_substring():
    """Домен-двойник (oktell.example.local.evil.com) не должен считаться нашим."""
    targets = [_target("https://oktell.example.local.evil.com/")]
    assert agent.pick_oktell_target(targets, ORIGIN) is None


def test_pick_target_accepts_bare_origin_url():
    targets = [_target(f"{ORIGIN}/")]
    assert agent.pick_oktell_target(targets, ORIGIN) is not None


def test_origin_of():
    assert agent.origin_of("https://oktell.example.local/#/phone") == "https://oktell.example.local"
    assert agent.origin_of("http://127.0.0.1:8799/fake-oktell/") == "http://127.0.0.1:8799"
    assert agent.origin_of("не url") == ""


# --------------------------------------------------------------------------- #
# JS-нагрузка
# --------------------------------------------------------------------------- #

def test_logout_js_contains_session_key_and_ws_logout():
    js = agent.build_logout_js(["___oktellsessionid"])
    assert '"___oktellsessionid"' in js
    assert "['logout', {}]" in js
    assert "localStorage.removeItem" in js
    assert "Max-Age=0" in js
    # location.reload() внутри страницы не зовём: перезагрузку делает CDP,
    # иначе скрипт может не успеть доработать.
    assert "location.reload" not in js


def test_logout_js_escapes_custom_keys():
    js = agent.build_logout_js(['ключ"с кавычкой'])
    assert json.dumps(['ключ"с кавычкой']) in js


def test_probe_js_reads_only_session_facts():
    js = agent.build_probe_js(["___oktellsessionid"])
    assert "loginForm" in js and "input[type=\"password\"]" in js
    assert "localStorage.getItem" in js
    # Никакого сбора содержимого страницы: только факт сессии, форма входа и логин.
    assert "innerText" not in js and "document.body.innerHTML" not in js


def test_banner_js_clamps_seconds_and_escapes_text():
    js = agent.build_banner_js('Верни<script>"кавычки"', 100000)
    assert "var total = 600;" in js
    assert json.dumps('Верни<script>"кавычки"') in js
    # Текст вставляется через textContent, а не innerHTML — иначе это XSS в своей же странице.
    assert "line.textContent = text;" in js


def test_hook_js_is_idempotent_and_keeps_prototype():
    js = agent.build_hook_js()
    assert "__oktellGuardHooked" in js
    assert "Guarded.prototype = Native.prototype;" in js


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #

def _state(**browser):
    identity = agent.AgentIdentity(hostname="WKS-12", windows_user="operator1", operator_login="")
    state = agent.AgentState(identity=identity)
    state.browser = browser
    return state


def test_heartbeat_payload_shape():
    cfg = agent.load_config(Path("нет-такого.json"))
    payload = agent.build_heartbeat_payload(
        _state(window=True, session=True, login_form=False, login="6612", url="https://oktell.example.local/"),
        cfg,
        "2026-08-17T19:00:00+0500",
    )
    assert payload["agent_id"] == "wks-12|operator1"
    assert payload["browser"] == {
        "managed_window": True,
        "session_present": True,
        "login_form": False,
        "url": "https://oktell.example.local/",
    }
    assert payload["operator_login"] == "6612"
    assert payload["version"] == agent.VERSION


def test_heartbeat_prefers_configured_operator_login():
    cfg = agent.load_config(Path("нет-такого.json"))
    state = _state(login="из-страницы")
    state.identity.operator_login = "6612"
    payload = agent.build_heartbeat_payload(state, cfg, "2026-08-17T19:00:00+0500")
    assert payload["operator_login"] == "6612"


def test_heartbeat_without_browser_marks_no_window():
    cfg = agent.load_config(Path("нет-такого.json"))
    payload = agent.build_heartbeat_payload(_state(), cfg, "2026-08-17T19:00:00+0500")
    assert payload["browser"]["managed_window"] is False
    assert payload["browser"]["session_present"] is False


# --------------------------------------------------------------------------- #
# Обход через неуправляемый браузер
# --------------------------------------------------------------------------- #

def test_unmanaged_titles_matched_case_insensitively():
    titles = ["Oktell — Google Chrome", "Почта", "OKTELL (рабочее место)"]
    found = agent.match_unmanaged_titles(titles, ["oktell"])
    assert len(found) == 2


def test_unmanaged_detection_off_without_patterns():
    assert agent.match_unmanaged_titles(["Oktell"], []) == []


# --------------------------------------------------------------------------- #
# dry-run и разбор команд
# --------------------------------------------------------------------------- #

class _FakeBrowser:
    def __init__(self):
        self.logout_calls = 0
        self.banners = []

    def logout(self):
        self.logout_calls += 1
        return {"status": "done"}

    def show_banner(self, message, seconds):
        self.banners.append((message, seconds))
        return True


def test_dry_run_does_not_touch_browser():
    browser = _FakeBrowser()
    report = agent.execute_command({"id": "c1", "type": "logout"}, browser, {"dry_run": True})
    assert report["status"] == "dry_run"
    assert browser.logout_calls == 0


def test_logout_command_executes():
    browser = _FakeBrowser()
    report = agent.execute_command({"id": "c1", "type": "logout"}, browser, {"dry_run": False})
    assert report["status"] == "done"
    assert browser.logout_calls == 1


def test_warn_command_shows_banner():
    browser = _FakeBrowser()
    report = agent.execute_command({"id": "c2", "type": "warn", "seconds": 20}, browser, {"warn_banner": True})
    assert report["status"] == "done"
    assert browser.banners and browser.banners[0][1] == 20


def test_unknown_command_is_reported_not_executed():
    browser = _FakeBrowser()
    report = agent.execute_command({"id": "c3", "type": "перезагрузи-компьютер"}, browser, {})
    assert report["status"] == "unknown_command"
    assert browser.logout_calls == 0


# --------------------------------------------------------------------------- #
# Аргументы запуска Chrome
# --------------------------------------------------------------------------- #

def test_chrome_launch_args(tmp_path):
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["oktell_url"] = "https://oktell.example.local/"
    cfg["browser"]["profile_dir"] = str(tmp_path / "profile")
    cfg["browser"]["cdp_port"] = 9333
    browser = agent.ManagedBrowser(cfg)
    args = browser.launch_args(Path("C:/chrome.exe"))
    assert "--remote-debugging-port=9333" in args
    # Порт отладки обязан слушать только петлю.
    assert "--remote-debugging-address=127.0.0.1" in args
    assert f"--user-data-dir={tmp_path / 'profile'}" in args
    assert "--app=https://oktell.example.local/" in args


def test_chrome_launch_args_without_app_mode(tmp_path):
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["oktell_url"] = "https://oktell.example.local/"
    cfg["browser"]["profile_dir"] = str(tmp_path / "profile")
    cfg["browser"]["app_mode"] = False
    browser = agent.ManagedBrowser(cfg)
    args = browser.launch_args(Path("C:/chrome.exe"))
    assert "https://oktell.example.local/" in args
    assert not any(a.startswith("--app=") for a in args)


def test_profile_dir_expands_env_vars(monkeypatch, tmp_path):
    """Профиль задаётся через %LOCALAPPDATA%: иначе в конфиг пришлось бы
    вписывать имя пользователя Windows, и один файл не подошёл бы всем машинам."""
    monkeypatch.setenv("DEMO_BASE", str(tmp_path))
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["browser"]["profile_dir"] = "%DEMO_BASE%/chrome-profile-real"
    browser = agent.ManagedBrowser(cfg)
    assert str(tmp_path) in str(browser.profile_dir)
    assert "%DEMO_BASE%" not in str(browser.profile_dir)


def test_profile_dir_defaults_to_app_dir():
    cfg = agent.load_config(Path("нет-такого.json"))
    browser = agent.ManagedBrowser(cfg)
    assert browser.profile_dir.name == "chrome-profile"


def test_broken_config_is_flagged_not_silently_defaulted(tmp_path):
    """Битый конфиг не должен молча превращаться в дефолты: с ними агент
    открыл бы чужой oktell_url и стучался бы на чужой server_url."""
    path = tmp_path / "config.json"
    path.write_text('{"oktell_url": "https://oktell.example.local/" ', encoding="utf-8")  # оборван
    cfg = agent.load_config(path)
    assert cfg.get("_config_error"), "ошибка чтения конфига обязана быть отмечена"
    assert cfg["_config_path"] == str(path)


def test_valid_config_has_no_error_flag(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"oktell_url": "https://oktell.example.local/"}), encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg.get("_config_error") is None
    assert cfg["oktell_url"] == "https://oktell.example.local/"


def test_wait_for_valid_config_returns_when_file_fixed(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{битый", encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg.get("_config_error")

    # Чиним файл «во время сна», чтобы проверить самоподхват без перезапуска агента.
    def fake_sleep(_seconds):
        path.write_text(json.dumps({"oktell_url": "https://oktell.example.local/"}), encoding="utf-8")

    monkeypatch.setattr(agent.time, "sleep", fake_sleep)
    fixed = agent.wait_for_valid_config(cfg, retry_s=0)
    assert fixed.get("_config_error") is None
    assert fixed["oktell_url"] == "https://oktell.example.local/"


# --------------------------------------------------------------------------- #
# Правило внутри окна
# --------------------------------------------------------------------------- #

def test_in_window_rule_params_are_injected():
    js = agent.build_hook_js({"threshold_s": 240, "warn_before_s": 45, "recall_lunch_reason_id": 2})
    assert '"thresholdS": 240' in js
    assert '"warnBeforeS": 45' in js
    assert '"recallReasonId": 2' in js
    # Статус берётся из события клиента, а не из опроса базы.
    assert "lunchreasonid" in js and "onlunch" in js


def test_in_window_rule_can_be_disabled():
    js = agent.build_hook_js({"enabled": False})
    assert '"enabled": false' in js


def test_in_window_rule_message_is_escaped():
    js = agent.build_hook_js({"message": 'кавычка " и <script>'})
    assert json.dumps('кавычка " и <script>', ensure_ascii=False) in js
    # Текст ставится через textContent — иначе это XSS в собственной странице.
    assert "line.textContent = text;" in js


def test_in_window_rule_records_violation_after_wipe():
    """Запись о нарушении должна переживать очистку хранилища: если писать
    её до localStorage.clear(), она сотрётся собственным же разлогином."""
    js = agent.build_hook_js({})
    body = js[js.index("function logout("):]      # смотрим сам разлогин, а не объявление функции
    wipe = body.index("sessionStorage.clear()")
    record = body.index("recordViolation(seconds, false)")   # у вызова появился флаг обкатки
    assert wipe < record


def test_in_window_rule_hook_captures_only_status_frames():
    js = agent.build_hook_js({})
    assert "raw.indexOf('userstate')" in js
    # Кадры разговоров и данные клиентов не копим.
    assert "__oktellGuardStateFrames.push" in js
    assert "slice(0, 800)" in js


def test_rule_version_changes_with_params():
    """Отпечаток правила обязан меняться вместе с настройками: по нему агент
    понимает, что в открытой вкладке крутится устаревшее правило."""
    a = agent.rule_version({"threshold_s": 180})
    b = agent.rule_version({"threshold_s": 60})
    assert a != b
    assert agent.rule_version({"threshold_s": 180}) == a
    assert a.startswith(agent.VERSION)


def test_rule_version_is_injected_into_hook():
    js = agent.build_hook_js({"threshold_s": 180})
    assert agent.rule_version({"threshold_s": 180}) in js


# --------------------------------------------------------------------------- #
# Настройки с сервера (сотрудник ничего не настраивает руками)
# --------------------------------------------------------------------------- #

def test_server_config_overrides_only_shared_keys():
    """Сервер задаёт общее (адрес клиента, правило), но не трогает локальное:
    пути, логи и токен машины остаются своими."""
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["agent_token"] = "local-token"
    cfg["log_level"] = "DEBUG"
    merged = agent.apply_server_config(cfg, {
        "oktell_url": "https://oktell.example.local/",
        "in_window_rule": {"threshold_s": 240},
        "agent_token": "server-token",
        "log_level": "CRITICAL",
        "browser": {"extra_args": ["--pin"], "chrome_path": "C:/подмена.exe"},
    })
    assert merged["oktell_url"] == "https://oktell.example.local/"
    assert merged["in_window_rule"]["threshold_s"] == 240
    assert merged["agent_token"] == "local-token"
    assert merged["log_level"] == "DEBUG"
    assert merged["browser"]["extra_args"] == ["--pin"]
    assert merged["browser"]["chrome_path"] == ""


def test_server_config_keeps_untouched_rule_fields():
    cfg = agent.load_config(Path("нет-такого.json"))
    merged = agent.apply_server_config(cfg, {"in_window_rule": {"threshold_s": 90}})
    assert merged["in_window_rule"]["threshold_s"] == 90
    # Остальные поля правила не должны исчезнуть вместе с подменой одного.
    assert merged["in_window_rule"]["recall_lunch_reason_id"] == 2
    assert merged["in_window_rule"]["warn_before_s"] == 30


def test_server_config_is_normalized():
    cfg = agent.load_config(Path("нет-такого.json"))
    merged = agent.apply_server_config(cfg, {"poll_interval_s": 0})
    assert merged["poll_interval_s"] == 2


def test_empty_url_is_not_a_configuration():
    """Пустой адрес в дефолтах — не настройка: иначе агент откроет сотруднику
    окно на несуществующий адрес, пока сервер недоступен."""
    cfg = agent.load_config(Path("нет-такого.json"))
    assert agent.is_configured(cfg) is False


def test_real_url_counts_as_configuration(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"oktell_url": "https://oktell.example.local/"}), encoding="utf-8")
    assert agent.is_configured(agent.load_config(path)) is True


def test_broken_url_is_not_a_configuration(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"oktell_url": "не адрес"}), encoding="utf-8")
    assert agent.is_configured(agent.load_config(path)) is False


def test_browser_lookup_prefers_chrome_then_edge(tmp_path, monkeypatch):
    """Chrome есть не на каждой машине, Edge есть всегда — иначе «без
    зависимостей» упирается в установку браузера."""
    program_files = tmp_path / "Program Files"
    edge = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("", encoding="utf-8")
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(program_files))

    cfg = agent.load_config(Path("нет-такого.json"))
    found = agent.ManagedBrowser(cfg).chrome_path()
    assert found is not None and found.name == "msedge.exe"

    chrome = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")
    assert agent.ManagedBrowser(cfg).chrome_path().name == "chrome.exe"


# --------------------------------------------------------------------------- #
# Автообновление
# --------------------------------------------------------------------------- #

def test_version_comparison():
    assert agent.parse_version("1.2.10") > agent.parse_version("1.2.9")
    assert agent.parse_version("1.10.0") > agent.parse_version("1.9.9")
    assert agent.parse_version("1.0.0-beta") == (1, 0, 0)


def test_update_only_goes_up():
    """Downgrade недопустим: опечатка в манифесте иначе откатила бы всем
    машинам рабочую версию."""
    assert agent.should_update("1.0.0", "1.0.1") is True
    assert agent.should_update("1.0.0", "1.0.0") is False
    assert agent.should_update("1.2.0", "1.1.9") is False
    assert agent.should_update("1.0.0", "") is False


def test_update_requires_matching_hash(tmp_path, monkeypatch):
    """Без совпадения sha256 обновление не ставится и файл удаляется."""
    payload = "это не наш файл".encode("utf-8")
    served = tmp_path / "served.exe"
    served.write_bytes(payload)

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=0): yield payload
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)

    result = agent.download_update({}, {"url": "http://x/f.exe", "sha256": "0" * 64})
    assert result is None
    assert not (tmp_path / "update.download").exists()


def test_update_accepts_matching_hash(tmp_path, monkeypatch):
    payload = "настоящий файл".encode("utf-8")
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=0): yield payload
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)

    result = agent.download_update({}, {"url": "http://x/f.exe", "sha256": digest})
    assert result is not None and result.exists()
    assert agent.sha256_of(result) == digest


def test_update_without_hash_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    assert agent.download_update({}, {"url": "http://x/f.exe"}) is None


# --------------------------------------------------------------------------- #
# Токен агента
# --------------------------------------------------------------------------- #

def test_build_token_empty_without_module():
    """Сборка без токена — законный случай: пока на сервере переменная пуста,
    агентские ручки открыты."""
    assert agent.build_token() == ""


def test_config_token_wins_over_build_token(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "build_token", lambda: "baked-token")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent_token": "from-config"}), encoding="utf-8")
    assert agent.load_config(path)["agent_token"] == "from-config"


def test_build_token_used_when_config_has_none(tmp_path, monkeypatch):
    """Именно этот случай у сотрудника: конфига нет, работает вшитый токен.
    Без него включение токена на сервере оставило бы все машины с 401."""
    # app_dir подменяем обязательно: иначе тест читает личный токен реальной
    # машины и падает от чужого состояния, а не от кода.
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "program_path", lambda: Path("C:/x/OktellRecallGuard.exe"))
    monkeypatch.setattr(agent, "build_token", lambda: "baked-token")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"oktell_url": "https://oktell.example.local/"}), encoding="utf-8")
    assert agent.load_config(path)["agent_token"] == "baked-token"


def test_non_ascii_token_is_rejected_loudly(tmp_path):
    """Кириллический токен рушил КАЖДЫЙ запрос к серверу невнятной ошибкой
    кодировки — снаружи это выглядело как «агент не работает»."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent_token": "токен-по-русски"}), encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg["agent_token"] == ""
    assert "latin-1" in cfg["_token_error"]


def test_ascii_token_passes(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent_token": "abc-123_XYZ"}), encoding="utf-8")
    cfg = agent.load_config(path)
    assert cfg["agent_token"] == "abc-123_XYZ"
    assert cfg.get("_token_error") is None


def test_header_safe_helper():
    assert agent.header_safe("plain-token") is True
    assert agent.header_safe("кириллица") is False


# --------------------------------------------------------------------------- #
# Сессия оператора в iCORE (вход окном, как в iCORE Phone)
# --------------------------------------------------------------------------- #

def test_session_survives_a_save_and_load(tmp_path, monkeypatch):
    """На Windows файл ложится под DPAPI, на прочих — открытым текстом; читаться
    он должен одинаково, иначе вход пришлось бы повторять каждый запуск."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    agent.save_session({"access_token": "a", "refresh_token": "r", "login": "6612"})
    assert agent.load_session()["login"] == "6612"
    agent.clear_session()
    assert agent.load_session() == {}


def test_session_goes_stale_after_twelve_hours():
    """Порог тот же, что в iCORE Phone: вошёл утром — смену не трогаем, назавтра
    окно снова."""
    fresh = {"refresh_token": "r", "saved_at": 1000.0}
    assert agent.session_is_fresh(fresh, now=1000.0 + 11 * 3600) is True
    assert agent.session_is_fresh(fresh, now=1000.0 + 13 * 3600) is False


def test_session_without_refresh_token_is_not_a_session():
    assert agent.session_is_fresh({"saved_at": 1000.0}, now=1000.0) is False
    assert agent.session_is_fresh({}, now=1000.0) is False


def test_machine_token_goes_with_every_request_and_bearer_only_with_a_session():
    """Два разных слоя: вшитый токен — замок машины, Bearer — имя человека.
    Свести их в один заголовок значило бы отдавать пароль от АТС любому."""
    cfg = {"agent_token": "machine-token"}
    without = agent.agent_headers(cfg)
    assert without["X-Agent-Token"] == "machine-token"
    assert "Authorization" not in without

    with_session = agent.agent_headers(cfg, {"access_token": "jwt-value"})
    assert with_session["Authorization"] == "Bearer jwt-value"
    assert with_session["X-Agent-Token"] == "machine-token"


def test_expired_session_is_not_refreshed_into_immortality(tmp_path, monkeypatch):
    """Refresh живёт 30 дней, порог — 12 часов. Обнови мы просроченную сессию,
    порог не наступил бы никогда и вход перестал бы что-либо значить."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    agent.save_session({"refresh_token": "r", "login": "6612", "saved_at": 0.0})
    calls = []
    monkeypatch.setattr(agent, "icore_refresh", lambda *a, **k: calls.append(a) or {})
    assert agent.ensure_session({"server_url": "http://x"}, ask=False) == {}
    assert calls == [], "просроченную сессию обновлять нельзя"


def test_silent_mode_never_opens_the_login_window(tmp_path, monkeypatch):
    """Фоновый агент окно не показывает: форма, всплывшая посреди разговора, —
    ровно то, чего программа делать не должна."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "run_login_window",
                        lambda *a, **k: pytest.fail("окно не должно открываться"))
    assert agent.ensure_session({"server_url": "http://x"}, ask=False) == {}


def test_the_access_token_is_refreshed_before_it_dies():
    """Access живёт полчаса, а обновлялись мы только по 401. Наши ручки на
    неузнанного 401 не отдают: `/news` говорит «объявлений нет», `/config` —
    настройки без учётки кабинета. Через полчаса после входа агент тихо
    становился анонимным до следующего входа, то есть на двенадцать часов.
    Так 21.09.2026 потерялось объявление: токен истёк в 10:09, объявление
    вышло в 10:31."""
    import base64
    import json as _json

    def token(exp):
        head = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
        body = base64.urlsafe_b64encode(_json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"{head}.{body}.signature"

    now = time.time()
    assert agent.access_is_expiring({"access_token": token(now + 30)}) is True
    assert agent.access_is_expiring({"access_token": token(now + 3600)}) is False
    # Срок не прочитался — не выдумываем: обновление по 401 всё равно сработает.
    assert agent.access_is_expiring({"access_token": "не-jwt"}) is False
    assert agent.access_is_expiring({}) is False

    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("    def _request(self"):source.index("    def _url(self")]
    assert "access_is_expiring(self.operator_session)" in body


def test_an_unknown_operator_is_treated_as_a_stale_token():
    """«Не узнаю тебя» при живой у нас сессии и «объявлений нет» — снаружи один
    и тот же ответ. Различить их может только агент, у которого сессия есть."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("    def news(self)"):source.index("    def news_read(self")]
    assert 'not data.get("known_operator") and self.operator_session' in body
    assert "self._refresh_operator_session()" in body
    # И жалуемся один раз: круг минутный, иначе лог станет стеной.
    assert "self._unknown_reported" in body


def test_a_network_hiccup_does_not_throw_the_operator_out(tmp_path, monkeypatch):
    """Сервер не ответил — это не «сессия истекла». Иначе одна потеря связи
    заставляла бы всю смену вводить пароль заново."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    session = {"refresh_token": "r", "access_token": "a", "saved_at": 1e12}
    monkeypatch.setattr(agent, "session_is_fresh", lambda *a, **k: True)
    monkeypatch.setattr(agent, "icore_refresh",
                        lambda *a, **k: {"error": "Нет связи", "keep": True})
    agent.save_session(session)
    assert agent.ensure_session({"server_url": "http://x"}, ask=False)["access_token"] == "a"


def test_cabinet_password_never_lands_in_the_plain_cache(tmp_path, monkeypatch):
    """Кэш настроек — обычный файл в профиле, а кабинет это пароль от АТС.
    Его место в session.json, который под DPAPI."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"oktell_url": "https://oktell/", "cabinet": {"login": "6612", "password": "секрет"}}

    monkeypatch.setattr(agent, "load_session", lambda: {"login": "6612"})
    saved = {}
    monkeypatch.setattr(agent, "save_session", lambda data: saved.update(data))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Response())

    cfg = agent.load_config(tmp_path / "нет-такого.json")
    cfg["server_url"] = "https://icore"
    result = agent.fetch_server_config(cfg, session={"access_token": "jwt"})

    assert result["cabinet"]["password"] == "секрет", "в памяти агента кабинет нужен"
    cached = json.loads((tmp_path / "server_config.json").read_text(encoding="utf-8"))
    assert "cabinet" not in cached
    assert "секрет" not in (tmp_path / "server_config.json").read_text(encoding="utf-8")
    assert saved["cabinet"]["password"] == "секрет", "кабинет должен уехать в сессию"


def test_the_shortcut_refuses_to_open_oktell_without_a_login():
    """Закрыл окно входа — Oktell не открываем: без учётки кабинета оператор
    всё равно увидел бы форму с паролем, которого не знает. Так же ведёт себя
    iCORE Phone (LoginDlg::OnCancel закрывает главное окно)."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_open("):source.index("def run_status(")]
    login = body.index("ensure_session(cfg)")
    browser = body.index("ManagedBrowser(")
    assert login < browser, "вход должен идти до открытия окна Oktell"
    assert "return 3" in body[login:browser]


def test_placeholder_server_url_is_replaced_by_build_value(tmp_path, monkeypatch):
    """Скачанный exe не знает адрес сервера ниоткуда, кроме сборки: с
    плейсхолдером он стучится в несуществующий домен и ждёт настроек вечно."""
    monkeypatch.setattr(agent, "build_server_url", lambda: "https://icore.example.org")
    cfg = agent.load_config(tmp_path / "нет-такого.json")
    assert cfg["server_url"] == "https://icore.example.org"


def test_config_server_url_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "build_server_url", lambda: "https://из-сборки")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server_url": "http://127.0.0.1:8799"}), encoding="utf-8")
    assert agent.load_config(path)["server_url"] == "http://127.0.0.1:8799"


def test_build_server_url_empty_without_module():
    assert agent.build_server_url() == ""


def test_the_name_in_the_login_window_is_the_one_on_the_shortcut():
    """Внутреннее имя оператор не видит нигде: он пришёл сюда по ярлыку
    «Oktell», и в шапке окна должно стоять то же слово."""
    assert agent.APP_NAME_SHORT == "Oktell"
    assert agent.LOGIN_WINDOW_TITLE.startswith("Oktell ")
    assert "Вход в Oktell" in agent.build_login_html()


def test_the_login_page_never_sends_the_password_anywhere():
    """Пароль со страницы забирает агент через CDP и сам идёт на https. Появись
    здесь fetch — пара уходила бы в сеть из file://-страницы, мимо всего, что мы
    об этом запросе знаем."""
    html = agent.build_login_html()
    for forbidden in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon", "form action"):
        assert forbidden not in html, forbidden
    assert "window.__guardLogin" in html


def test_the_remembered_login_is_escaped_before_it_goes_into_the_page():
    """Логин приезжает из прошлой сессии, то есть с сервера. Подставлять его в
    разметку как есть нельзя ни при каких «да кто туда напишет кавычку»."""
    html = agent.build_login_html('6612" autofocus onfocus="alert(1)')
    assert 'onfocus="alert(1)' not in html
    assert "&quot;" in html


def test_the_login_page_carries_the_version_like_the_phone_does():
    """«Какая у тебя версия» спрашивают как раз когда человек ещё не вошёл."""
    assert f"версия {agent.VERSION}" in agent.build_login_html()
    # И в заголовке окна: карточку человек опишет словами, а заголовок прочитает.
    assert agent.LOGIN_WINDOW_TITLE == f"Oktell {agent.VERSION}"
    assert f"<title>{agent.LOGIN_WINDOW_TITLE}</title>" in agent.build_login_html()


def test_the_operator_can_update_himself():
    """Само обновление приходит при старте и раз в 6 часов. Не хватает этого
    ровно тогда, когда версия сломана: ждать полдня оператору нечем."""
    html = agent.build_login_html()
    assert 'id="update"' in html
    assert "window.__guardLogin.update = true" in html
    # Страница на сервер не ходит — нажатие забирает агент, как и вход.
    assert "fetch(" not in html

    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert "def run_update_now(" in source
    assert '"--update"' in source
    # И отвечает словами: «обновились», «уже последняя», «не вышло».
    body = source[source.index("def _run_update_from_window("):source.index("def run_login_window(")]
    for text in ("Обновились", "последняя версия", "не вышло"):
        assert text in body, text


def test_the_operator_on_the_line_can_reach_the_update():
    """Тот, кто уже на линии, окна входа не видит ВООБЩЕ: сессия живёт 12 часов.
    До метки в углу клиента обновиться ему было негде — оставались ярлык и
    командная строка, то есть для человека на смене ничего."""
    js = agent.build_version_badge_js("1.2.3")
    assert "Oktell ' + data.version" in js
    assert "position:fixed" in js
    assert "s.update = true" in agent.build_version_badge_js("1.2.3").replace("state.update = true", "s.update = true")
    # Флаг снимается сразу: иначе обновление запускалось бы по кругу.
    assert "s.update = false" in agent.build_badge_request_js()
    # И ответ приходит в ту же метку, по которой человек нажал.
    assert "__oktell_guard_badge" in agent.build_badge_answer_js("готово")


def test_the_badge_is_there_from_the_first_second():
    """Круг агента — минута. Пока метку ставил только он, оператор всё это время
    смотрел на окно без номера версии и без кнопки обновления — ровно на это и
    пожаловался владелец. По ярлыку она появляется сразу."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_open("):source.index("def fill_oktell_login(")]
    assert "browser.show_version_badge()" in body
    loop = source.split("def run_agent", 1)[1]
    assert "browser.show_version_badge()" in loop, "и на кругах тоже: страницу перезагружают"


def test_the_badge_does_not_flicker():
    """Метку ставим раз, дальше только меняем подпись: пересоздание на каждом
    круге агента давало бы мигание поверх рабочего окна."""
    js = agent.build_version_badge_js("1.2.3")
    assert "if (!box) {" in js
    assert "if (!state.busy) {" in js


def test_status_never_touches_the_page():
    """`--status` — диагностика. С правом чинить она перезагружала страницу
    оператора, и правило теряло перехваченные сокеты ровно на том опросе,
    которым его проверяли: десять вызовов подряд показали «сокетов 0», а прямое
    чтение той же страницы — один."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_status("):source.index("def run_sign_out(")]
    assert "ManagedBrowser(cfg, heal=False)" in body


def test_a_manual_update_stops_the_old_copies():
    """Подменить файл мало: старые агент и сторож держат в памяти прежний код и
    работают дальше. На экране версия новая, работает старая — ровно так
    21.09.2026 ручное обновление до 1.0.29 не починило ничего.

    Гасим именно ПЕРЕИМЕНОВАННЫЙ файл: из него работают только старые копии, а
    свежая уже запущена из нового и трогать её нельзя."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_update_now("):source.index("def run_sign_out(")]
    assert "_stop_installed_copies(installed_path())" in body
    # Порядок ОБРАТНЫЙ и это важно: погасить надо ДО подмены. Иначе старые копии
    # держат переименованный *.old.exe, и следующее обновление не может его
    # удалить — «Отказано в доступе».
    assert body.index("_stop_installed_copies(") < body.index("apply_update(downloaded)")


def test_the_agent_leaves_after_updating_itself():
    """Обновившийся по нажатию агент держит в памяти ПРЕЖНИЙ код: на диске новая
    версия, работает старая. Он обязан уйти, дальше сторож поднимет новую."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    loop = source.split("def run_agent", 1)[1]
    assert "stop_after_update" in loop
    assert "Завершаюсь после ручного обновления" in loop


def test_the_update_result_goes_into_the_same_window():
    """Своё окно с сообщением вылезло бы ПОВЕРХ окна входа, которое и так на
    экране: два окна там, где хватает строки."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def _run_update_from_window("):source.index("def run_login_window(")]
    assert "quiet=True" in body
    assert "__guardUpdateDone" in body


def test_the_server_speaks_english_and_the_operator_should_not_read_it():
    """`/api/login` отвечает «Invalid credentials». Портал показывает это как
    есть, но окно входа человек видит раньше всего остального — первым
    сообщением чужой язык быть не должен."""
    assert agent.login_error_text("Invalid credentials") == "Неправильный логин или пароль"
    assert agent.login_error_text("User account is inactive").startswith("Учётная запись отключена")
    assert "попыток входа" in agent.login_error_text("Too many login attempts. Please try again later.")


def test_an_unknown_answer_is_shown_as_it_came():
    """Соврать про причину хуже, чем показать строку, которую можно переслать
    в IT."""
    assert agent.login_error_text("Something new") == "Something new"
    assert agent.login_error_text("", 503) == "Ошибка входа (код 503)"


# --------------------------------------------------------------------------- #
# Отправка нарушений на сервер
# --------------------------------------------------------------------------- #

def test_dry_run_reaches_the_in_window_rule():
    """Обкатка задаётся в общих настройках, а решает правило в окне: без
    передачи флага «безопасный» режим выкидывал людей по-настоящему."""
    assert '"dryRun": true' in agent.build_hook_js({'dry_run': True})
    assert '"dryRun": false' in agent.build_hook_js({'dry_run': False})


def test_clear_js_removes_only_sent_records():
    """Пока идёт отправка, правило могло записать новое нарушение — очистка
    целиком его бы потеряла."""
    js = agent.build_clear_violations_js(['6612|2026-08-18T10:00:00.000Z'])
    assert '6612|2026-08-18T10:00:00.000Z' in js
    assert 'sent.indexOf(key) < 0' in js
    assert 'localStorage.removeItem' not in js


def test_collect_js_reads_the_page_store():
    js = agent.build_collect_violations_js()
    assert '__oktell_guard_violations' in js


def test_violations_path_is_configurable():
    cfg = agent.load_config(Path("нет-такого.json"))
    assert cfg['violations_path'] == '/api/oktell_guard/violations'


class _FormBrowser:
    """Вкладка Oktell: сначала её нет, потом появляется форма входа."""

    def __init__(self, states):
        self.states = list(states)
        self.filled = []

    def probe(self):
        return self.states.pop(0) if self.states else {}

    def autologin(self, login, password):
        self.filled.append((login, password))
        return {"ok": True}


def test_the_shortcut_fills_the_oktell_form_itself(monkeypatch):
    """Подстановку делает процесс ярлыка, а не долгоживущий агент.

    18.09.2026 на живой учётке она не сработала ни разу: агент читает сессию при
    своём старте, то есть ДО входа, а вход идёт в процессе ярлыка. Даже научив
    агента замечать вход, оставлять подстановку только ему нельзя — круг у него
    минута, и всё это время человек смотрит на пустую форму.
    """
    monkeypatch.setattr(agent.time, "sleep", lambda _s: None)
    browser = _FormBrowser([
        {"window": False},
        {"window": True, "login_form": True, "session": False},
    ])
    cfg = {"cabinet": {"login": "6554", "password": "секрет"}}
    assert agent.fill_oktell_login(browser, cfg) is True
    assert browser.filled == [("6554", "секрет")]


def test_nothing_is_typed_into_a_session_that_is_already_open(monkeypatch):
    """Chrome помнит прошлую сессию Oktell — формы нет, и лезть в неё нечего."""
    monkeypatch.setattr(agent.time, "sleep", lambda _s: None)
    browser = _FormBrowser([{"window": True, "session": True, "login_form": False}])
    assert agent.fill_oktell_login(browser, {"cabinet": {"login": "a", "password": "b"}}) is True
    assert browser.filled == []


def test_without_a_cabinet_the_operator_fills_the_form_himself(monkeypatch):
    """Учётка кабинета заведена у одного человека из 92: для остальных это
    обычный путь, а не сбой."""
    monkeypatch.setattr(agent.time, "sleep", lambda _s: None)
    browser = _FormBrowser([{"window": True, "login_form": True}])
    assert agent.fill_oktell_login(browser, {}) is False
    assert browser.filled == []


def test_the_running_agent_notices_a_login_that_happened_later():
    """Сторож поднимает агента при входе в Windows — раньше, чем человек нажмёт
    ярлык. Не замечай агент чужого входа, он навсегда оставался бы «без сессии»:
    ходил бы за настройками без Bearer и не получал ни кабинета, ни объявлений.
    """
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_agent("):source.index("def run_watchdog(")]
    assert "def adopt_new_login()" in body
    assert "adopt_new_login()" in body.split("while True:", 1)[1], \
        "проверять вход надо каждый круг, а не один раз на старте"
    # И сразу перечитывать настройки: именно в них приезжает учётка кабинета.
    assert "next_config_refresh = time.time()" in body


def test_sign_out_takes_everything_down(tmp_path, monkeypatch):
    """«Выход» — это человек ушёл, а не «переключить учётку».

    Оставь мы хоть что-то, и каждое оставшееся — своя дыра: не разлогинить
    Oktell — следующий за машиной попадёт в чужую сессию АТС (cookie живёт в
    профиле Chrome); не закрыть окно — останется окно, за которым уже никто не
    следит; не погасить процессы — сторож поднимет агента обратно.
    """
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(agent, "installed_path", lambda: tmp_path / "OktellRecallGuard.exe")
    steps = []

    class _Browser:
        def __init__(self, *a, **k):
            pass

        def is_debug_port_alive(self):
            return True

        def oktell_target(self):
            return {"id": "T1"}

        def logout(self):
            steps.append("разлогин в Oktell")
            return {"status": "done"}

        def close_page(self):
            pass

    monkeypatch.setattr(agent, "ManagedBrowser", _Browser)
    monkeypatch.setattr(agent, "_close_target", lambda _b, tid: steps.append(f"закрыто окно {tid}"))
    monkeypatch.setattr(agent, "_stop_installed_copies", lambda _t: steps.append("программа остановлена"))

    agent.save_session({"refresh_token": "r", "login": "6612"})
    assert agent.run_sign_out({}) == 0
    assert agent.load_session() == {}
    assert steps == ["разлогин в Oktell", "закрыто окно T1", "программа остановлена"], steps


def test_the_announcement_lives_in_its_own_window_above_everything():
    """В странице Oktell объявление исчезало вместе со свёрнутым клиентом АТС:
    свернул, ушёл в Excel — и обязательного объявления нет. Теперь у него своё
    окно, накрывающее монитор и поднятое поверх всех (HWND_TOPMOST)."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert "class NewsOverlay:" in source
    # HWND_TOPMOST именно указателем: как 32-битное число −1 доезжает искажённым,
    # окно послушно меняет размер, а поверх всех НЕ встаёт — и увидеть это можно
    # только проверкой WS_EX_TOPMOST, глазами никак.
    assert "HWND_TOPMOST = ctypes.c_void_p(-1)" in source
    # И ни следа старой дороги: две копии правил показа разъехались бы молча.
    assert "def show_news(" not in source
    assert "def close_news(" not in source


def test_both_windows_share_one_copy_of_the_announcement_code():
    """Оболочка страницы пустая, а весь код объявления — тот же build_news_js.
    Вторая копия правил задержки кнопки и разбора теста разъехалась бы с первой."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    page = source[source.index("def build_news_page("):source.index("def _window_by_title(")]
    assert "build_news_js(item)" in page
    assert "__NEWS_JS__" in page


def test_a_closed_announcement_comes_back():
    """«Закрыл крестиком» не может быть способом не читать обязательное."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def handle_news_press()"):source.index("def wait_for_next_round(")]
    assert "if not news_overlay.alive():" in body
    assert "news_overlay.show(active_news)" in body


def test_the_window_has_no_frame_and_lets_the_desktop_through():
    """Снятия WS_CAPTION мало: заголовок у окна `--app` рисует сам Chrome внутри
    клиентской области, и системные стили о нём ничего не знают — на снимке
    экрана рамка оставалась. Отсюда kiosk. Полупрозрачность — всему окну
    целиком: попиксельной у окна Chrome не бывает, её умеет только своя оболочка
    вроде Electron, а это +100 МБ на оператора."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    show = source[source.index("    def show(self, item: dict) -> bool:"):source.index("    # ---------- общение ----------")]
    # Флага --kiosk НЕДОСТАТОЧНО, и это стоило живого прогона: он действует,
    # только когда Chrome стартует ХОЛОДНЫМ. У оператора он уже запущен — в том
    # же профиле открыт клиент Oktell, — и новый вызов просто передаёт окно
    # живому процессу, молча выбрасывая все флаги. Полноэкранный режим обязан
    # ставиться по отладочному соединению, ему всё равно, кто запустил окно.
    assert "_go_fullscreen" in show
    assert '"windowState": "fullscreen"' in source
    # Прозрачность — снимком экрана под затемнением, а не LWA_ALPHA: тот гасит
    # вместе с фоном и карточку, а её как раз надо читать.
    assert "def capture_backdrop(" in source
    assert "SetLayeredWindowAttributes" not in source


def test_the_window_is_pushed_back_on_top_while_it_is_shown():
    """Другие программы тоже ставят себе TOPMOST, и объявление уезжает вниз."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def handle_news_press()"):source.index("def wait_for_next_round(")]
    assert "news_overlay.hold_on_top()" in body


def test_the_window_cannot_be_put_away():
    """Увести объявление с экрана можно тремя штатными способами, и все три
    надо возвращать: крестик, который Chrome сам показывает в полноэкранном
    режиме у верхнего края (убрать его нельзя — рисует браузер), сворачивание с
    панели задач и чужой TOPMOST. Обязательное объявление не может уходить по
    движению мышью к верхнему краю."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    hold = source[source.index("    def hold_on_top(self) -> None:"):source.index("    def result(self)")]
    assert "_restore_if_minimized" in hold
    assert "self._go_fullscreen()" in hold
    assert "_keep_on_top" in hold
    # Фокус при возврате не забираем: выдернутая из-под рук клавиатура — это уже
    # не контроль.
    restore = source[source.index("def _restore_if_minimized("):source.index("def _keep_on_top(")]
    assert "SW_SHOWNOACTIVATE = 4" in restore


def test_the_press_is_picked_up_in_half_a_second_not_in_a_minute():
    """Круг агента — минута, и пока разбор нажатия жил в нём, человек после
    «Подтвердить» до минуты смотрел на «Отправляем…»: ни ответа, ни признака,
    что его услышали. Ровно на это владелец и пожаловался."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_agent("):source.index("def run_watchdog(")]
    assert "def handle_news_press()" in body
    assert "wait_for_next_round(poll_s)" in body, "спать целую минуту с открытым окном нельзя"
    wait = body[body.index("def wait_for_next_round("):body.index("def adopt_new_login(")]
    assert "handle_news_press()" in wait
    assert "0.5" in wait


def test_the_window_does_not_leave_the_operator_guessing():
    """Забрать нажатие может оказаться некому: программу перезапустил сторож,
    вкладка потеряла связь, сеть легла. Тогда окно обязано сказать об этом само,
    а не висеть с «Отправляем…» до конца смены."""
    html = agent.NEWS_JS_TEMPLATE
    assert "Ответ не ушёл — нажмите ещё раз" in html
    assert "state.waitTimer" in html
    # И снимать страховку, когда ответ всё же пришёл.
    assert "clearTimeout(state.waitTimer)" in html.split("state.feedback = function")[1]


def test_being_kicked_out_does_not_kill_the_program():
    """Выброс по правилу — не выход. Оператор остаётся за машиной, и программа
    обязана продолжать считать: иначе один выброс снимал бы ограничитель до
    конца смены, то есть работал бы ровно наоборот задуманному."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    logout = source[source.index("    def logout(self)"):source.index("    def probe(self)")]
    assert "_stop_installed_copies" not in logout
    assert "clear_session" not in logout


def test_the_program_wears_the_icore_phone_icon():
    """Ярлык берёт значок из самого exe, поэтому он один на всё: рабочий стол,
    панель задач, «Программы и компоненты». Разные значки у двух наших программ
    человек читает как «это что-то чужое»."""
    here = Path(agent.__file__).parent
    assert (here / "icore.ico").exists()
    assert "--icon icore.ico" in (here / "build_exe.bat").read_text(encoding="utf-8")
    # Тот же значок — в шапке окна входа, иначе Chrome рисует глобус.
    assert "data:image/png;base64," + agent.LOGIN_ICON_B64 in agent.build_login_html()


def test_install_hands_over_to_the_installed_copy():
    """Скачанная копия не должна жить долго: пока она ждёт нажатия OK, её
    временную папку успевает занять антивирус, и упаковщик показывает
    «Failed to remove temporary directory» — пугающее окно на ровном месте."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    handover = source.index('subprocess.Popen([str(target), "--install"')
    message = source.index('"Программа установлена и уже работает')
    assert handover < message, "передача установки должна идти до показа окна"


def test_child_env_drops_packer_variables(monkeypatch):
    """Установленный агент падал на КАЖДОМ запросе: он унаследовал от
    установщика путь к сертификатам во временную папку, а та удалялась вместе
    с установщиком. Ни heartbeat, ни нарушения после этого не уходили."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", r"C:\Temp\_MEI329922\certifi\cacert.pem")
    monkeypatch.setenv("_MEIPASS", r"C:\Temp\_MEI329922")
    monkeypatch.setenv("SSL_CERT_FILE", r"C:\Temp\_MEI329922\cacert.pem")
    monkeypatch.setenv("PATH", "нужное-сохраняем")

    env = agent.child_env()
    for poisoned in ("REQUESTS_CA_BUNDLE", "_MEIPASS", "SSL_CERT_FILE"):
        assert poisoned not in env, poisoned
    assert env["PATH"] == "нужное-сохраняем"


def test_child_env_keeps_launcher_variable(monkeypatch):
    """А эту переменную трогать нельзя: её ставит первая ступень загрузчика для
    второй, и без неё запуск падает окном «_PYI_APPLICATION_HOME_DIR is not
    defined». Вычистил её один раз — получил два окна с ошибкой у пользователя.
    """
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI1")
    assert agent.child_env().get("_PYI_APPLICATION_HOME_DIR") == r"C:\Temp\_MEI1"


def test_all_child_launches_use_clean_env():
    """Чтобы правка не рассыпалась при следующем запуске процесса."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    launches = source.count("subprocess.Popen(")
    cleaned = source.count("env=child_env()")
    assert cleaned == launches, f"запусков {launches}, с чистым окружением {cleaned}"


def test_installer_becomes_the_watchdog_instead_of_exiting():
    """Короткоживущий процесс после модального окна упирался в антивирус,
    который уже держал его папку распаковки, — отсюда «Failed to remove
    temporary directory». Долгоживущий сторож такой гонки не создаёт."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_install("):source.index("def run_uninstall(")]
    assert "return run_watchdog(cfg)" in body


def test_no_periodic_relaunch_task():
    """Задачи-подстраховки больше нет.

    Возвращать программу по расписанию бессмысленно: если оператор закрыл её и
    ушёл в Oktell через свой браузер, воскресшая копия всё равно бессильна —
    она умеет работать только со своим окном. Обход ловится сервером (агента
    нет, а Oktell показывает человека в «Перезвоне»), а не борьбой с машиной.
    Само удаление задачи оставлено: у прежних установок её надо снять.
    """
    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert "_register_task" not in source
    assert "def _remove_task" in source
    body = source[source.index("def run_install("):source.index("def run_uninstall(")]
    assert "_remove_task()" in body, "установка должна снимать прежнюю задачу"


# --------------------------------------------------------------------------- #
# Слепое правило: код в окне есть, а кадры до него не доходят
# --------------------------------------------------------------------------- #
#
# 04.09.2026 оператор просидел в «Перезвоне» 334 с при пороге 180 и остался на
# месте. Правило слышит статусы только через сокеты, созданные уже подменённым
# window.WebSocket. Документ, загрузившийся раньше нашей регистрации, отдаёт
# клиенту родной сокет — кадры идут мимо правила навсегда.
#
# Смертельной дыру делало не это, а то, что install_hook ВПРЫСКИВАЛ код в такой
# документ. Впрыск ставил флаг и печать нужной версии, тем самым закрывая
# единственную проверку, по которой назначалась перезагрузка. Ограничитель
# выглядел исправным при пустом отчёте.


class _FakePage:
    """Вкладка на CDP: помнит вызовы и отвечает заранее заданным здоровьем."""

    def __init__(self, health, on_reload=None):
        self.health = dict(health)
        self.calls = []
        self.evaluated = []
        self.on_reload = on_reload

    def call(self, method, params=None, timeout=None):
        self.calls.append(method)
        if method == "Page.reload" and self.on_reload:
            self.health = dict(self.on_reload)
        return {}

    def evaluate(self, expression, timeout=None):
        self.evaluated.append(expression)
        if "__oktellGuardSockets" in expression and "hooked" in expression:
            return dict(self.health)
        return None


def _browser(**over):
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["oktell_url"] = "https://oktell.example.local/"
    cfg["in_window_rule"] = {"enabled": True, "threshold_s": 180}
    cfg.update(over)
    browser = agent.ManagedBrowser(cfg)
    browser._page_target_id = "T1"
    return browser


BLIND = {"hooked": True, "ruleVersion": None, "enabled": True, "sockets": 0,
         "frames": 0, "session": True, "login": "6612", "seconds": 0}
HEALTHY = {"hooked": True, "ruleVersion": None, "enabled": True, "sockets": 1,
           "frames": 3, "session": True, "login": "6612", "seconds": 0}
LOGIN_SCREEN = {"hooked": False, "ruleVersion": None, "enabled": False, "sockets": 0,
                "frames": 0, "session": False, "login": None, "seconds": 0}


def test_blind_rule_is_healed_by_reload_not_masked_by_injection():
    browser = _browser()
    page = _FakePage(BLIND)
    browser.install_hook(page)
    assert "Page.reload" in page.calls, (
        "страницу с живой сессией и нулём перехваченных сокетов обязаны перезагрузить"
    )


def test_blind_rule_is_not_papered_over_by_evaluating_the_hook():
    """Впрыск в такую страницу — та самая маскировка: он ставит печать нужной
    версии и навсегда закрывает единственный признак поломки."""
    browser = _browser()
    page = _FakePage(BLIND)
    browser.install_hook(page)
    injected = [e for e in page.evaluated if "__oktellGuardHooked = true" in e]
    assert not injected, "в документ с живой сессией хук впрыскивать нельзя"


def test_login_screen_gets_the_hook_without_reload():
    """До входа сокета ещё нет: обычного впрыска достаточно, и он перехватит
    сокет, который клиент создаст после входа. Перезагружать экран входа — зря
    злить человека."""
    browser = _browser()
    page = _FakePage(LOGIN_SCREEN)
    browser.install_hook(page)
    assert "Page.reload" not in page.calls
    assert any("__oktellGuardHooked = true" in e for e in page.evaluated)


def test_working_rule_is_left_alone():
    browser = _browser()
    page = _FakePage(HEALTHY)
    browser.install_hook(page)
    assert "Page.reload" not in page.calls
    assert not any("__oktellGuardHooked = true" in e for e in page.evaluated)


def test_reload_happens_once_then_the_agent_shouts():
    """Перезагрузка ровно одна: если и она не помогла, надо не крутить вкладку
    в цикле, а сказать об этом — человек сейчас без ограничителя."""
    browser = _browser()
    page = _FakePage(BLIND)
    browser.install_hook(page)
    page.calls.clear()
    browser.install_hook(page)
    assert "Page.reload" not in page.calls


def test_healed_page_earns_the_right_to_be_fixed_again():
    """Ослепла второй раз (клиент сам перезагрузил страницу) — снова чиним.
    Иначе одна успешная починка за смену исчерпывала бы лимит."""
    browser = _browser()
    page = _FakePage(BLIND, on_reload=HEALTHY)
    browser.install_hook(page)
    assert "Page.reload" in page.calls
    browser.install_hook(page)          # теперь здорова — отметка снимается
    page.health = dict(BLIND)
    page.calls.clear()
    browser.install_hook(page)
    assert "Page.reload" in page.calls


def test_short_lived_open_does_not_reload():
    """Процесс `--open` завершится сразу, унеся регистрацию скрипта. Перезагрузи
    он страницу — новый документ остался бы вовсе без правила."""
    cfg = agent.load_config(Path("нет-такого.json"))
    cfg["oktell_url"] = "https://oktell.example.local/"
    browser = agent.ManagedBrowser(cfg, heal=False)
    browser._page_target_id = "T1"
    page = _FakePage(BLIND)
    browser.install_hook(page)
    assert "Page.reload" not in page.calls


def test_run_open_creates_browser_without_healing():
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_open("):source.index("def run_status(")]
    assert "ManagedBrowser(cfg, heal=False)" in body


def test_script_registered_once_per_connection():
    """addScriptToEvaluateOnNewDocument не заменяет прежний скрипт, а добавляет
    ещё один: вызов на каждом опросе копил бы их сотнями за смену."""
    browser = _browser()
    page = _FakePage(HEALTHY)
    browser.install_hook(page)
    browser.install_hook(page)
    assert page.calls.count("Page.addScriptToEvaluateOnNewDocument") == 1


def test_page_disable_is_never_called():
    """В Chromium Page.disable очищает список добавленных скриптов — вызвать
    его значит стереть хук. Упоминание в комментарии как раз объясняет запрет,
    поэтому ищем именно ВЫЗОВ."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert 'call("Page.disable' not in source
    assert "call('Page.disable" not in source


# --- как это выглядит снаружи ------------------------------------------------

def test_rule_alive_needs_a_captured_socket():
    assert agent.rule_alive({"window": True, "session": True,
                             "rule": {"enabled": True, "hooked": True, "sockets": 0}}) is False
    assert agent.rule_alive({"window": True, "session": True,
                             "rule": {"enabled": True, "hooked": True, "sockets": 2}}) is True


def test_rule_alive_is_unknown_without_session():
    """Нет сессии — сказать нечего. Ложное «сломано» на пустом окне заставило бы
    искать поломку там, где человек просто не вошёл."""
    assert agent.rule_alive({"window": True, "session": False, "rule": {}}) is None
    assert agent.rule_alive({}) is None


def test_rule_alive_false_when_rule_switched_off():
    assert agent.rule_alive({"window": True, "session": True,
                             "rule": {"enabled": False, "hooked": True, "sockets": 1}}) is False


def test_heartbeat_reports_rule_health_separately_from_session():
    """Раздел обязан различать «человек в Oktell» и «правило его считает»:
    именно неразличимость двух недель выдавала слепое правило за рабочее."""
    cfg = agent.load_config(Path("нет-такого.json"))
    payload = agent.build_heartbeat_payload(
        _state(window=True, session=True, login="6612",
               rule={"hooked": True, "enabled": True, "sockets": 0, "seconds": 0,
                     "thresholdS": 180, "ruleVersion": "1.0.14-abc"}),
        cfg, "2026-09-04T15:30:00+0500",
    )
    assert payload["browser"]["session_present"] is True
    assert payload["rule"]["alive"] is False
    assert payload["rule"]["threshold_s"] == 180


def test_heartbeat_rule_alive_unknown_for_old_agents_state():
    cfg = agent.load_config(Path("нет-такого.json"))
    payload = agent.build_heartbeat_payload(_state(), cfg, "2026-09-04T15:30:00+0500")
    assert payload["rule"]["alive"] is None


# --------------------------------------------------------------------------- #
# Шторм запусков и лишнее в сборке (07.09.2026)
# --------------------------------------------------------------------------- #
#
# Сборка 1.0.14 выросла на 2,2 МБ: PyInstaller сам подтянул setuptools, потому
# что они стоят в окружении сборки. Вместе с ними приехал хук pyi_rth_pkgres,
# который на старте читает вшитый файл setuptools\_vendor\...\Lorem ipsum.txt.
# Имя временной папки onefile считается от PID и повторяется, а после убитых
# копий такие папки остаются недораспакованными — процесс попал на чужую, файла
# там не оказалось, и он умер с модальным окном «Unhandled exception in script».
#
# Смертельным это сделал сторож: он дёргал агента каждые ~5 с без остановки, а
# упавшая копия без консоли ВИСИТ с этим окном и мьютекс не берёт. За восемь
# минут набежало больше сотни окон и 47 КБ одинаковых строк в логе.


def test_build_does_not_bundle_setuptools():
    """Ни одна строка агента их не импортирует — в сборке им делать нечего."""
    bat = (Path(agent.__file__).parent / "build_exe.bat").read_text(encoding="utf-8")
    assert "--exclude-module pkg_resources" in bat
    assert "--exclude-module setuptools" in bat


def test_agent_really_does_not_need_setuptools():
    """Если однажды понадобятся — исключение придётся снять осознанно, а не
    обнаружить падение на машинах операторов."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("REM"):
            continue
        assert "import pkg_resources" not in stripped
        assert "import setuptools" not in stripped


def test_watchdog_backs_off_instead_of_storming():
    """Пауза между неудачными попытками обязана расти. Постоянные пять секунд
    превращают одно падение в стопку модальных окон."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_watchdog("):source.index("def run_open(")]
    assert "retry_s = min(max_retry_s, retry_s * 2)" in body, "нет удвоения паузы"
    assert "watchdog_max_retry_s" in body, "нет потолка паузы"
    # И обнуление после успеха: иначе первая же осечка за смену навсегда
    # оставила бы сторожа медленным.
    assert body.count("retry_s = check_s") >= 2, "пауза не сбрасывается после успеха"


def test_watchdog_complains_once_not_every_attempt():
    """47 КБ одинаковых строк вытеснили из лога всё остальное."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_watchdog("):source.index("def run_open(")]
    assert "complained" in body, "нет отметки «уже пожаловался»"
    assert "if not complained and failures >= 3:" in body


def test_agent_backs_off_when_watchdog_will_not_start():
    """Та же мина с другой стороны: минутный цикл агента поднимал сторожа
    каждый круг и копил такие же окна."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def run_agent("):source.index("def run_watchdog(")]
    assert "watchdog_skip" in body
    assert "watchdog_failures" in body
    assert "if watchdog_skip > 0:" in body


# --------------------------------------------------------------------------- #
# Установка на весь компьютер (MSI, групповая политика)
# --------------------------------------------------------------------------- #

def test_machine_copy_installs_when_user_has_none_or_older():
    assert agent.machine_logon_plan("1.0.16", "", False) == "install"
    assert agent.machine_logon_plan("1.0.16", "1.0.15.0", True) == "install"
    # Сборки до 1.0.16 без ресурса версии: пусто значит «старее».
    assert agent.machine_logon_plan("1.0.16", "", True) == "install"


def test_machine_copy_never_rolls_back_updated_user_copy():
    """Копия пользователя обновляется сама и уходит вперёд машинной. Перезапись
    откатывала бы её при каждом входе, а автообновление качало бы заново."""
    assert agent.machine_logon_plan("1.0.16", "1.0.17.0", True) == "keep"
    assert agent.machine_logon_plan("1.0.16", "1.0.16.0", True) == "keep"


def test_orphan_only_when_marker_names_missing_machine_copy(tmp_path):
    present = tmp_path / "OktellRecallGuard.exe"
    present.write_bytes(b"MZ")
    assert agent.deployment_orphaned({}) is False, "ручная установка без метки не снимается"
    assert agent.deployment_orphaned({"machine_exe": str(present)}) is False
    assert agent.deployment_orphaned({"machine_exe": str(tmp_path / "нет.exe")}) is True


def _machine_logon_stage(monkeypatch, tmp_path, *, user_version, watchdog_running=False):
    machine = tmp_path / "Program Files" / "OktellRecallGuard.exe"
    machine.parent.mkdir()
    machine.write_bytes(b"MZ new")
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    calls = {"popen": [], "stopped": [], "shortcut": [], "autostart_removed": 0}
    monkeypatch.setattr(agent.sys, "frozen", True, raising=False)
    monkeypatch.setattr(agent, "setup_logging", lambda cfg, name: None)
    monkeypatch.setattr(agent, "program_path", lambda: machine)
    monkeypatch.setattr(agent, "app_dir", lambda: user_dir)
    monkeypatch.setattr(agent, "installed_path", lambda: user_dir / "OktellRecallGuard.exe")
    monkeypatch.setattr(agent, "file_version", lambda path: user_version)
    monkeypatch.setattr(agent, "is_network_path", lambda path: False)
    monkeypatch.setattr(agent, "_stop_installed_copies", lambda target: calls["stopped"].append(target))
    monkeypatch.setattr(agent, "_remove_task", lambda: None)
    monkeypatch.setattr(agent, "_remove_autostart",
                        lambda: calls.__setitem__("autostart_removed", calls["autostart_removed"] + 1))
    monkeypatch.setattr(agent, "shortcut_path", lambda: tmp_path / "Desktop" / "Oktell.lnk")
    monkeypatch.setattr(agent, "_create_shortcut", lambda target: calls["shortcut"].append(target) or True)
    monkeypatch.setattr(agent, "is_running_by_mutex", lambda name: watchdog_running)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent.subprocess, "Popen", lambda args, **kw: calls["popen"].append(args))
    return machine, user_dir, calls


def test_machine_logon_puts_copy_to_user_and_starts_it(monkeypatch, tmp_path):
    machine, user_dir, calls = _machine_logon_stage(monkeypatch, tmp_path, user_version="")

    assert agent.run_machine_logon({}) == 0

    target = user_dir / "OktellRecallGuard.exe"
    assert target.read_bytes() == b"MZ new"
    assert calls["stopped"] == [target], "перед заменой файла копии пользователя гасятся"
    marker = json.loads((user_dir / "deployment.json").read_text(encoding="utf-8"))
    assert marker["machine_exe"] == str(machine)
    assert calls["autostart_removed"] == 1, "личный Run поднимал бы вторую копию в ту же секунду"
    assert calls["shortcut"] == [target]
    assert calls["popen"] == [[str(target)]]


def test_machine_logon_keeps_newer_user_copy_and_running_watchdog(monkeypatch, tmp_path):
    _machine, user_dir, calls = _machine_logon_stage(
        monkeypatch, tmp_path, user_version="1.0.99.0", watchdog_running=True)
    target = user_dir / "OktellRecallGuard.exe"
    target.write_bytes(b"MZ updated by itself")
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "Oktell.lnk").write_bytes(b"lnk")

    assert agent.run_machine_logon({}) == 0

    assert target.read_bytes() == b"MZ updated by itself"
    assert calls["stopped"] == [], "работающую новую копию не трогаем"
    assert calls["shortcut"] == [], "ярлык на месте — PowerShell не запускаем"
    assert calls["popen"] == [], "сторож уже работает"
    assert (user_dir / "deployment.json").exists()


def test_machine_logon_from_network_share_is_plain_quiet_install(monkeypatch, tmp_path):
    """Метка с сетевым путём сняла бы программу при первом же обрыве сети."""
    _machine, user_dir, calls = _machine_logon_stage(monkeypatch, tmp_path, user_version="")
    monkeypatch.setattr(agent, "is_network_path", lambda path: True)
    seen = {}
    monkeypatch.setattr(agent, "run_install", lambda cfg, **kw: seen.update(kw) or 0)

    assert agent.run_machine_logon({}) == 0

    assert seen == {"quiet": True}
    assert not (user_dir / "deployment.json").exists()


def test_orphaned_user_copy_removes_itself_instead_of_opening(monkeypatch, tmp_path):
    """IT сняло пакет, а ярлык «Oktell» остался и поднимал бы контроль снова."""
    (tmp_path / "deployment.json").write_text(
        json.dumps({"machine_exe": str(tmp_path / "снятый.exe")}), encoding="utf-8")
    monkeypatch.setattr(agent.sys, "frozen", True, raising=False)
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "is_installed_copy", lambda: True)
    monkeypatch.setattr(agent, "run_orphan_cleanup", lambda cfg: 7)
    monkeypatch.setattr(agent, "run_open", lambda cfg: pytest.fail("снятая программа открыла Oktell"))
    monkeypatch.setattr(agent, "run_watchdog", lambda cfg: pytest.fail("снятая программа подняла сторожа"))

    assert agent.main(["--open"]) == 7
    assert agent.main([]) == 7
    monkeypatch.setattr(agent, "run_status", lambda cfg: 0)
    assert agent.main(["--status"]) == 0, "диагностика работает и у снятой копии"


def test_orphan_cleanup_disables_but_keeps_logs(monkeypatch, tmp_path):
    (tmp_path / "deployment.json").write_text(json.dumps({"machine_exe": "C:/нет.exe"}), encoding="utf-8")
    (tmp_path / "agent.log").write_text("история", encoding="utf-8")
    done = []
    monkeypatch.setattr(agent, "setup_logging", lambda cfg, name: None)
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "_remove_autostart", lambda: done.append("run"))
    monkeypatch.setattr(agent, "_remove_shortcut", lambda: done.append("lnk"))
    monkeypatch.setattr(agent, "_stop_installed_copies", lambda target: done.append("stop"))

    assert agent.run_orphan_cleanup({}) == 0

    assert done == ["run", "lnk", "stop"]
    assert not (tmp_path / "deployment.json").exists()
    assert (tmp_path / "agent.log").exists()


def _install_stage(monkeypatch, tmp_path, *, same_path):
    source = tmp_path / "Downloads" / "Oktell-Perezvon-Setup.exe"
    source.parent.mkdir()
    source.write_bytes(b"MZ")
    target = source if same_path else tmp_path / "user" / "OktellRecallGuard.exe"
    messages, popen = [], []
    monkeypatch.setattr(agent.sys, "frozen", True, raising=False)
    monkeypatch.setattr(agent, "setup_logging", lambda cfg, name: None)
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "program_path", lambda: source)
    monkeypatch.setattr(agent, "installed_path", lambda: target)
    monkeypatch.setattr(agent, "_stop_installed_copies", lambda target: None)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "_register_autostart", lambda target: True)
    monkeypatch.setattr(agent, "_remove_task", lambda: None)
    monkeypatch.setattr(agent, "_create_shortcut", lambda target: True)
    monkeypatch.setattr(agent, "show_message", lambda text, **kw: messages.append(text))
    monkeypatch.setattr(agent.subprocess, "Popen", lambda args, **kw: popen.append(args))
    return messages, popen


def test_quiet_install_shows_no_windows(monkeypatch, tmp_path):
    """Скрипт IT окно не закроет — процесс висел бы на нём до перезагрузки."""
    messages, _ = _install_stage(monkeypatch, tmp_path, same_path=True)
    assert agent.run_install({}, start=False, quiet=True) == 0
    assert messages == []
    assert agent.run_install({}, start=False) == 0
    assert len(messages) == 1, "ручная установка по-прежнему говорит, что всё готово"


def test_quiet_is_handed_over_to_installed_copy(monkeypatch, tmp_path):
    _, popen = _install_stage(monkeypatch, tmp_path, same_path=False)
    monkeypatch.setattr(agent, "file_version", lambda path: "")
    assert agent.run_install({}, quiet=True) == 0
    assert popen and popen[0][1:] == ["--install", "--quiet"]


def test_install_never_rolls_back_updated_copy(monkeypatch, tmp_path):
    """Давно скачанный файл, запущенный повторно, не откатывает программу,
    которая уже обновилась сама."""
    _, popen = _install_stage(monkeypatch, tmp_path, same_path=False)
    target = tmp_path / "user" / "OktellRecallGuard.exe"
    target.parent.mkdir()
    target.write_bytes(b"MZ newer")
    monkeypatch.setattr(agent, "file_version", lambda path: "9.0.0.0")
    monkeypatch.setattr(agent, "_stop_installed_copies", lambda t: pytest.fail("новую копию погасили"))

    assert agent.run_install({}) == 0

    assert target.read_bytes() == b"MZ newer"
    assert popen and popen[0][0] == str(target), "установку всё равно доводит установленная копия"


def test_stop_copies_spares_own_loader_and_lets_loaders_clean_up():
    """Копия — это загрузчик упаковщика плюс интерпретатор. Убитый загрузчик
    оставляет в %TEMP% папку _MEI на 14 МБ; свой загрузчик убивать нельзя вовсе."""
    source = Path(agent.__file__).read_text(encoding="utf-8")
    body = source[source.index("def _stop_installed_copies("):source.index("def _ps_quote(")]
    assert "os.getppid()" in body
    assert "Wait-Process" in body
    assert body.index("foreach($i in $py)") < body.index("foreach($i in $boot)")


def test_ps_quote_survives_apostrophe_in_user_name():
    assert agent._ps_quote(r"C:\Users\O'Neil\Desktop") == r"'C:\Users\O''Neil\Desktop'"


@pytest.mark.skipif(sys.platform != "win32", reason="ресурс версии читается через version.dll")
def test_file_version_reads_resource_without_running_file(tmp_path):
    assert agent.file_version(Path(sys.executable)).startswith(f"{sys.version_info.major}.")
    plain = tmp_path / "plain.exe"
    plain.write_bytes(b"MZ")
    assert agent.file_version(plain) == ""
