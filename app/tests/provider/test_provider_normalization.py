from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from provider_normalization import filter_visible_output, recover_gemma_ollama_tool_calls
from tool_registry import get_tool_schemas_by_name


def test_generic_prose_does_not_recover_tool_call() -> None:
    schemas = get_tool_schemas_by_name({"read_file"}, "assistant", config={})
    cleaned, calls, diagnostics = recover_gemma_ollama_tool_calls(
        "I will call read_file now.",
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    assert calls == []
    assert cleaned == "I will call read_file now."


def test_generic_json_example_in_prose_does_not_recover_tool_call() -> None:
    schemas = get_tool_schemas_by_name({"read_file"}, "assistant", config={})
    cleaned, calls, _diagnostics = recover_gemma_ollama_tool_calls(
        'Here is an example:\n{"tool_name":"read_file","arguments":{"path":"secret.txt"}}',
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    assert calls == []
    assert "secret.txt" in cleaned


def test_gemma_fixture_recovers_allowed_raw_call() -> None:
    raw_path = ROOT / "app" / "tests" / "fixtures" / "provider-responses" / "gemma_raw_tool_call.raw.json"
    expected_path = ROOT / "app" / "tests" / "fixtures" / "provider-responses" / "gemma_raw_tool_call.normalized.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    schemas = get_tool_schemas_by_name({"read_file"}, "assistant", config={})
    cleaned, calls, diagnostics = recover_gemma_ollama_tool_calls(
        raw["message"]["content"],
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    actual = {
        "contentForUser": cleaned,
        "hiddenReasoning": None,
        "toolCalls": [call.__dict__ for call in calls],
    }
    assert actual == expected
    assert any("accepted" in item for item in diagnostics)


def test_recovered_call_to_non_exposed_tool_is_blocked() -> None:
    schemas = get_tool_schemas_by_name({"read_file"}, "assistant", config={})
    cleaned, calls, diagnostics = recover_gemma_ollama_tool_calls(
        '<|tool_call>call:write_memory{"entry":"x"}<tool_call|>',
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    assert calls == []
    assert cleaned == ""
    assert any("not exposed" in item for item in diagnostics)


def test_recovered_call_with_invalid_args_is_blocked() -> None:
    schemas = get_tool_schemas_by_name({"read_file"}, "assistant", config={})
    _cleaned, calls, diagnostics = recover_gemma_ollama_tool_calls(
        '<|tool_call>call:read_file{}<tool_call|>',
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    assert calls == []
    assert any("missing required argument: path" in item for item in diagnostics)


def test_hidden_channel_and_tool_junk_stripped_from_visible_output() -> None:
    text = "<channel>\nthought\nsecret\n<channel|><tool_call>{}</tool_call>tool_code: nope\nVisible"
    result = filter_visible_output(text)
    assert "<channel" not in result
    assert "<tool_call" not in result
    assert "tool_code" not in result
    assert "Visible" in result
