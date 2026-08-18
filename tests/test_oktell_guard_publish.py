"""Публикация версии сборкой: номер берётся из agent.py, а не из головы."""

import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "oktell_recall_guard" / "publish_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("publish_release", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publish = _load()


def test_version_comes_from_agent_source():
    """Один источник номера версии: два места однажды разойдутся, и агенты
    либо зациклятся на обновлении, либо не увидят его вовсе."""
    agent_source = (Path(__file__).resolve().parents[1] / "oktell_recall_guard" / "agent.py").read_text(encoding="utf-8")
    assert f'VERSION = "{publish.agent_version()}"' in agent_source


def test_publish_skipped_without_token(monkeypatch, capsys):
    """Без токена сборка не падает: exe собран и лежит рядом, публикация —
    отдельный необязательный шаг."""
    monkeypatch.delenv(publish.TOKEN_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["publish_release.py"])   # иначе argparse съест аргументы pytest
    assert publish.main() == 0
    assert "публикацию пропускаю" in capsys.readouterr().out


def test_publish_skipped_without_server(monkeypatch, capsys):
    monkeypatch.setenv(publish.TOKEN_ENV, "token")
    monkeypatch.setenv(publish.SERVER_ENV, "")
    monkeypatch.setattr(sys, "argv", ["publish_release.py"])
    assert publish.main() == 0
    assert "адрес сервера" in capsys.readouterr().out
