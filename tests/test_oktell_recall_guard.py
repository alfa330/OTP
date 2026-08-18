"""Тесты агента «Oktell Recall Guard» (oktell_recall_guard/agent.py).

Проверяем только то, что не требует Windows и браузера: разбор конфига,
идемпотентность команд, выбор вкладки, формируемый JS и полезную нагрузку
heartbeat. Сам разлогин проверяется стендом dev_harness/mock_server.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
# Личный токен из имени файла
# --------------------------------------------------------------------------- #

def test_token_extracted_from_download_filename():
    """Скачанный файл называется OktellRecallGuard.<токен>.exe — так присланное
    оказывается подписано конкретным человеком, хотя сам exe один на всех."""
    assert agent.token_from_filename("OktellRecallGuard.abc123XYZ789.exe") == "abc123XYZ789"


def test_plain_filename_has_no_token():
    assert agent.token_from_filename("OktellRecallGuard.exe") == ""


def test_browser_suffix_is_not_a_token():
    """Второй скачанный файл браузер называет «...(1).exe» — это не токен."""
    assert agent.token_from_filename("OktellRecallGuard.abc123XYZ789 (1).exe") == ""
    assert agent.token_from_filename("OktellRecallGuard.short.exe") == ""


def test_stored_token_survives_install(tmp_path, monkeypatch):
    """Установка переименовывает файл, и токен из имени исчезает: если его не
    сохранить, агент после первого же запуска станет анонимным."""
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    agent.save_personal_token("tokenFromFilename1")
    monkeypatch.setattr(agent, "program_path", lambda: Path("C:/x/OktellRecallGuard.exe"))
    assert agent.resolve_agent_token() == "tokenFromFilename1"


def test_filename_token_wins_over_build_token(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "build_token", lambda: "baked-token")
    monkeypatch.setattr(agent, "program_path", lambda: Path("C:/x/OktellRecallGuard.person123456789.exe"))
    assert agent.resolve_agent_token() == "person123456789"
    # И сохранился, иначе установка его потеряет.
    assert agent.load_personal_token() == "person123456789"


def test_build_token_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "build_token", lambda: "baked-token")
    monkeypatch.setattr(agent, "program_path", lambda: Path("C:/x/OktellRecallGuard.exe"))
    assert agent.resolve_agent_token() == "baked-token"


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


def test_token_from_new_filename_shape():
    """Имя должно читаться человеком: случайный хвост рядом с предупреждением
    Windows выглядит как вирус."""
    assert agent.token_from_filename("Oktell-Perezvon-Setup-n8oZgJIxZBaYMhoXwULf.exe") == "n8oZgJIxZBaYMhoXwULf"


def test_old_filename_shape_still_understood():
    """Уже скачанные копии не должны стать анонимными после обновления."""
    assert agent.token_from_filename("OktellRecallGuard.n8oZgJIxZBaYMhoXwULf.exe") == "n8oZgJIxZBaYMhoXwULf"


def test_plain_new_name_has_no_token():
    assert agent.token_from_filename("Oktell-Perezvon-Setup.exe") == ""


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
