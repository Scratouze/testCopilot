import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_resolve_config_path_prefers_env(load_server, tmp_path):
    module = load_server(tmp_path)
    path, warn = module._resolve_config_path()
    assert path == module.CONFIG_PATH
    assert warn is None


def test_resolve_config_path_missing_falls_back(server_module, monkeypatch):
    monkeypatch.setenv("COPILOTPC_CONFIG", "missing.toml")
    path, warn = server_module._resolve_config_path()
    assert path == server_module.ROOT / "config.toml"
    assert warn is not None


def test_feature_overrides_respect_environment(server_module, monkeypatch):
    monkeypatch.setenv("COPILOTPC_FEATURE_MOUSE", "0")
    monkeypatch.setenv("COPILOTPC_FEATURE_CUSTOM", "1")
    overrides = server_module._feature_overrides({"mouse": True})
    assert overrides["mouse"] is False
    assert overrides["custom"] is True


def test_normalize_allowlist_handles_scalars(server_module):
    normalized = server_module._normalize_allowlist({"one": "cmd", "two": ["a", "b"]})
    assert normalized["one"] == ["cmd"]
    assert normalized["two"] == ["a", "b"]


def test_status_requires_token(server_module):
    client = TestClient(server_module.app)
    response = client.get("/status")
    assert response.status_code == 401


def test_status_reports_configuration(server_module):
    client = TestClient(server_module.app)
    response = client.get("/status", params={"token": server_module.TOKEN})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["config_path"] == str(server_module.CONFIG_PATH)
    assert "features" in data and isinstance(data["features"], dict)
    assert data["allowlist"] == sorted(server_module.ALLOW.keys())


def test_panic_and_enable_toggle(server_module):
    client = TestClient(server_module.app)
    panic = client.get("/panic")
    assert panic.status_code == 200
    assert panic.json()["disabled"] is True

    blocked = client.post(
        "/agent/command",
        params={"token": server_module.TOKEN},
        json={"text": 'tape "Bonjour"'},
    )
    assert blocked.status_code == 423

    enabled = client.get("/enable")
    assert enabled.status_code == 200
    assert enabled.json()["disabled"] is False


def test_agent_command_executes_plan(server_module):
    client = TestClient(server_module.app)
    response = client.post(
        "/agent/command",
        params={"token": server_module.TOKEN},
        json={"text": 'tape "Bonjour" et screenshot'},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert any(step["type"] == "type" for step in data["plan"])
    assert Path(data["results"][-1]["path"]).exists()
    pyautogui_stub = sys.modules["pyautogui"]
    assert pyautogui_stub.typed == ["Bonjour"]
    assert server_module._test_sleep_calls == [pytest.approx(0.4, rel=1e-3)]


def test_keyboard_type_endpoint(server_module):
    client = TestClient(server_module.app)
    response = client.get(
        "/os/keyboard/type",
        params={"token": server_module.TOKEN, "text": "hello"},
    )
    assert response.status_code == 200
    assert sys.modules["pyautogui"].typed == ["hello"]


def test_keyboard_hotkey_endpoint(server_module):
    client = TestClient(server_module.app)
    response = client.get(
        "/os/keyboard/hotkey",
        params={"token": server_module.TOKEN, "keys": "ctrl+enter"},
    )
    assert response.status_code == 200
    assert ("ctrl", "enter") in sys.modules["pyautogui"].hotkeys


def test_clipboard_endpoint(server_module):
    client = TestClient(server_module.app)
    response = client.get(
        "/os/clipboard/set",
        params={"token": server_module.TOKEN, "text": "copied"},
    )
    assert response.status_code == 200
    assert sys.modules["pyperclip"].copied == ["copied"]


def test_window_activate_uses_stubbed_desktop(server_module):
    server_module.Desktop.set_windows(["Bloc-notes", "Chrome"])  # stub windows
    client = TestClient(server_module.app)
    response = client.get(
        "/window/activate",
        params={"token": server_module.TOKEN, "title": "bloc"},
    )
    assert response.status_code == 200
    assert response.json()["window"] == "Bloc-notes"


def test_app_run_respects_allowlist(server_module, monkeypatch):
    monkeypatch.setattr(server_module.shutil, "which", lambda exe: exe)
    launched = []

    def fake_popen(cmd):
        launched.append(cmd)

    monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)

    client = TestClient(server_module.app)
    ok = client.get(
        "/app/run",
        params={"token": server_module.TOKEN, "name": "notepad"},
    )
    assert ok.status_code == 200
    assert launched and launched[0][0] == "notepad.exe"

    forbidden = client.get(
        "/app/run",
        params={"token": server_module.TOKEN, "name": "unknown"},
    )
    assert forbidden.status_code == 403


def test_browser_open(monkeypatch, server_module):
    opened = []
    browser_stub = types.SimpleNamespace(open_new_tab=lambda url: opened.append(url))
    monkeypatch.setitem(sys.modules, "webbrowser", browser_stub)

    client = TestClient(server_module.app)
    response = client.get(
        "/browser/open",
        params={"token": server_module.TOKEN, "url": "example.com"},
    )
    assert response.status_code == 200
    assert opened == ["https://example.com"]


def test_interpret_command_handles_multiple_clauses(server_module):
    plan = server_module.interpret_command('tape "Bonjour" et screenshot')
    assert plan[0] == {"type": "type", "text": "Bonjour"}
    assert any(step.get("type") == "screenshot" for step in plan)


def test_resolve_app_alias(server_module):
    assert server_module.resolve_app("Bloc-notes") == "notepad"


def test_allowlist_normalization_string_values(load_server, tmp_path):
    config_text = """
[server]
host = \"127.0.0.1\"
port = 9999

[security]
token = \"secret\"

[features]
mouse = true
keyboard = true
window = true
screenshot = true
run_apps = true
browser_open = true
browser_playwright = false

[run.allowlist]
solo = \"/usr/bin/solo\"
"""
    module = load_server(tmp_path, config_text=config_text)
    assert module.ALLOW["solo"] == ["/usr/bin/solo"]
