import asyncio

import pytest

from agent_llm import LLMPlanner


def test_needs_clipboard_detects_complex_text():
    assert LLMPlanner._needs_clipboard("line1\nline2") is True
    assert LLMPlanner._needs_clipboard("a" * 400) is True
    assert LLMPlanner._needs_clipboard("café") is True
    assert LLMPlanner._needs_clipboard("simple") is False


def test_tool_schema_exposes_expected_tools():
    planner = LLMPlanner(base_url="http://localhost", token="")
    names = {item["function"]["name"] for item in planner.tool_schema()}
    assert {
        "run_app",
        "focus_window",
        "open_url",
        "type_text",
        "paste_text",
        "hotkey",
        "screenshot",
    }.issubset(names)


def test_tool_dispatch_type_text(monkeypatch):
    planner = LLMPlanner(base_url="http://localhost", token="")
    calls = []

    async def fake_call(path, params=None):
        calls.append((path, dict(params)))
        return {"status": "ok"}

    monkeypatch.setattr(planner, "_call_local", fake_call)
    result = asyncio.run(planner.tool_dispatch("type_text", {"text": "hello"}))
    assert result["status"] == "ok"
    assert calls == [("/os/keyboard/type", {"text": "hello"})]


def test_tool_dispatch_type_text_uses_clipboard(monkeypatch):
    planner = LLMPlanner(base_url="http://localhost", token="")
    calls = []

    async def fake_call(path, params=None):
        calls.append((path, dict(params)))
        return {"status": "ok"}

    monkeypatch.setattr(planner, "_call_local", fake_call)
    asyncio.run(planner.tool_dispatch("type_text", {"text": "é"}))
    assert calls[0][0] == "/os/clipboard/set"
    assert calls[1][0] == "/os/keyboard/paste"


def test_tool_dispatch_missing_text(monkeypatch):
    planner = LLMPlanner(base_url="http://localhost", token="")
    result = asyncio.run(planner.tool_dispatch("type_text", {"text": ""}))
    assert result["ok"] is False
    assert result["error"] == "missing_text_argument"


def test_tool_dispatch_unknown_tool(monkeypatch):
    planner = LLMPlanner(base_url="http://localhost", token="")
    result = asyncio.run(planner.tool_dispatch("unknown", {}))
    assert "unknown tool" in result["error"]
