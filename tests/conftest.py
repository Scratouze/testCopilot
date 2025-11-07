import importlib
import sys
import types
from pathlib import Path

import pytest

# --- Stub external dependencies to avoid interacting with the host OS ---
pyautogui_stub = types.ModuleType("pyautogui")
pyautogui_stub.moves = []
pyautogui_stub.clicks = []
pyautogui_stub.hotkeys = []
pyautogui_stub.typed = []


def _record_move(x, y, duration=0.0):
    pyautogui_stub.moves.append((x, y, duration))


def _record_click(*args, **kwargs):
    pyautogui_stub.clicks.append((args, kwargs))


def _record_hotkey(*keys):
    pyautogui_stub.hotkeys.append(tuple(keys))


def _record_type(text):
    pyautogui_stub.typed.append(text)


pyautogui_stub.moveTo = _record_move
pyautogui_stub.click = _record_click
pyautogui_stub.hotkey = _record_hotkey
pyautogui_stub.typewrite = _record_type
sys.modules.setdefault("pyautogui", pyautogui_stub)

pyperclip_stub = types.ModuleType("pyperclip")
pyperclip_stub.copied = []


def _copy(text):
    pyperclip_stub.copied.append(text)


pyperclip_stub.copy = _copy
sys.modules.setdefault("pyperclip", pyperclip_stub)


class _DummyShot:
    def __init__(self):
        self.paths: list[Path] = []

    def shot(self, mon=-1, output=None):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"shot")
        self.paths.append(path)
        return str(path)


class _DummyMSSCtx:
    def __init__(self):
        self.impl = _DummyShot()

    def __enter__(self):
        return self.impl

    def __exit__(self, exc_type, exc, tb):
        return False


mss_stub = types.ModuleType("mss")


def _mss_factory():
    return _DummyMSSCtx()


mss_stub.mss = _mss_factory
sys.modules.setdefault("mss", mss_stub)


class _DummyRectangle:
    left = 0
    top = 0
    right = 100
    bottom = 100


class _DummyWindow:
    def __init__(self, title: str):
        self._title = title
        self.focused = False

    def window_text(self):
        return self._title

    def set_focus(self):
        self.focused = True

    def rectangle(self):
        return _DummyRectangle()


class _DummyDesktop:
    windows_list: list[_DummyWindow] = []

    @classmethod
    def set_windows(cls, titles: list[str]):
        cls.windows_list = [_DummyWindow(t) for t in titles]

    def __init__(self, backend="uia"):
        self.backend = backend

    def windows(self):
        return list(self.__class__.windows_list)


pywinauto_stub = types.ModuleType("pywinauto")
pywinauto_stub.Desktop = _DummyDesktop
sys.modules.setdefault("pywinauto", pywinauto_stub)


DEFAULT_CONFIG = """
[server]
host = \"127.0.0.1\"
port = 9999

[security]
token = \"secret\"
disabled = false

[features]
mouse = true
keyboard = true
window = true
screenshot = true
run_apps = true
browser_open = true
browser_playwright = false

[run.allowlist]
notepad = [\"notepad.exe\"]
chrome = [\"chrome.exe\"]
"""


def _load_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_text: str = DEFAULT_CONFIG, extra_env: dict | None = None):
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text, encoding="utf-8")

    env = {"COPILOTPC_CONFIG": str(config_path), "COPILOTPC_TOKEN": "secret"}
    if extra_env:
        env.update(extra_env)

    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))

    sys.modules.pop("server", None)
    module = importlib.import_module("server")
    module = importlib.reload(module)

    sleep_calls: list[float] = []

    async def _fast_sleep(delay: float):
        sleep_calls.append(delay)

    module.asyncio.sleep = _fast_sleep
    module._test_sleep_calls = sleep_calls
    module.Desktop.set_windows(["Chrome"])
    module._test_config_path = config_path
    return module


@pytest.fixture(autouse=True)
def _reset_stubs():
    pyautogui_stub.moves.clear()
    pyautogui_stub.clicks.clear()
    pyautogui_stub.hotkeys.clear()
    pyautogui_stub.typed.clear()
    pyperclip_stub.copied.clear()
    _DummyDesktop.set_windows(["Chrome"])
    yield


@pytest.fixture
def load_server(monkeypatch):
    def _loader(tmp_path: Path, config_text: str = DEFAULT_CONFIG, extra_env: dict | None = None):
        return _load_server(tmp_path, monkeypatch, config_text=config_text, extra_env=extra_env)

    return _loader


@pytest.fixture
def server_module(load_server, tmp_path):
    return load_server(tmp_path)
