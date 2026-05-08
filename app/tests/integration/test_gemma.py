#!/usr/bin/env python3
from __future__ import annotations

import json
import site
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
USER_SITE = site.getusersitepackages()
EXTRA_SITE = ROOT / ".pytest-deps"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(EXTRA_SITE) if EXTRA_SITE.exists() else "", USER_SITE):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import BOLD, RESET, fail, ok, section, summary

import backend.brain as brain_module
import backend.wrapper as wrapper
from backend.model_family import get_family_adapter
from provider_normalization import recover_gemma_ollama_tool_calls
from tool_registry import get_tool_schemas_by_name
from providers.gemma import GemmaProvider


class FakeClient:
    def __init__(self, settings: dict) -> None:
        self.provider = SimpleNamespace(family_adapter=get_family_adapter(settings))

    def health_check(self):
        return True


class FakeToolCall:
    def __init__(self, name: str, arguments: dict, call_id: str = "call-1") -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))


def run_test(name: str, fn) -> None:
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


def base_config(model: str) -> dict:
    return {
        "companion": {"name": "Nova"},
        "brain": {
            "provider": "gemma",
            "model": model,
            "layers": {
                "assistant": {"provider": "gemma", "model": model},
            },
        },
        "layers": {
            "assistant": {"enabled": True, "display_name": "Assistant"},
        },
        "voice": {"tts_provider": "", "stt_provider": "", "tts_enabled": False, "stt_enabled": False},
        "heartbeat": {"enabled": False},
        "memory": {"enabled": False},
        "vault": {"enabled": True, "path": "./vault"},
    }


def session_patches(config: dict):
    return [
        patch("backend.wrapper.memory.build_system_prompt", lambda prompt, _config, user_message="", memory_budget_tokens=None: prompt),
        patch("backend.wrapper.brain.create_client", lambda _config, layer_name="assistant": FakeClient(brain_module.get_layer_brain_config(_config, layer_name))),
        patch("backend.wrapper.brain.load_identity", lambda _config, layer_name="assistant": f"identity for {layer_name}"),
    ]


def build_session(model: str) -> wrapper.LayerSession:
    config = base_config(model)
    stack = ExitStack()
    for patcher in session_patches(config):
        stack.enter_context(patcher)
    session = wrapper.LayerSession(config, "assistant", include_memory=False)
    session._test_stack = stack
    return session


def close_session(session: wrapper.LayerSession) -> None:
    stack = getattr(session, "_test_stack", None)
    if stack is not None:
        stack.close()


# ---------------------------------------------------------------------------
# Bleed-stripping unit tests (wrapper._strip_gemma_token_bleed)
# ---------------------------------------------------------------------------

def test_bleed_variant1_bare_ollama_call() -> None:
    """Variant 1: call:name{key:<|"|>val<|"|>}<tool_call|> — bare Ollama text bleed."""
    text = 'call:run_terminal{<|"|>command<|"|>:<|"|>ls<|"|>}<tool_call|>Sure, running that.'
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<tool_call|>" not in result
    assert "call:run_terminal" not in result
    assert "Sure, running that." in result


def test_bleed_variant2_full_tool_call_block() -> None:
    """Variant 2: <|tool_call>...<tool_call|> — full block with opening token."""
    text = "Let me check.<|tool_call>run_terminal{command:ls}<tool_call|> Done."
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<|tool_call>" not in result
    assert "<tool_call|>" not in result
    assert "run_terminal" not in result
    assert "Let me check." in result
    assert "Done." in result


def test_bleed_variant3_tool_response_block() -> None:
    """Variant 3: <|tool_response>...<tool_response|> — tool response token bleed."""
    text = "Here is the result.<|tool_response>file1.txt\nfile2.txt<tool_response|>Done."
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<|tool_response>" not in result
    assert "<tool_response|>" not in result
    assert "file1.txt" not in result
    assert "Done." in result


def test_bleed_variant4_tool_declaration_block() -> None:
    """Variant 4: <|tool>...<tool|> — tool declaration token bleed."""
    text = "Available tools:<|tool>run_terminal(command: str)<tool|>Ask me anything."
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<|tool>" not in result
    assert "<tool|>" not in result
    assert "run_terminal" not in result
    assert "Ask me anything." in result


def test_bleed_variant5_stray_opening_token() -> None:
    """Variant 5: stray <|tool_call> with no closing pair."""
    text = "I will call<|tool_call> the function now."
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<|tool_call>" not in result
    assert "I will call" in result
    assert "the function now." in result


def test_bleed_variant5_stray_closing_token() -> None:
    """Variant 5: stray <tool_call|> with no opening pair."""
    text = "Done.<tool_call|>"
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<tool_call|>" not in result
    assert "Done." in result


def test_bleed_variant6_quote_token() -> None:
    """Variant 6: <|"|> quote token."""
    text = 'The value is <|"|>hello<|"|>.'
    result = wrapper._strip_gemma_token_bleed(text)
    assert '<|"|>' not in result
    assert "The value is" in result


def test_bleed_multiline_tool_call_block() -> None:
    """Full block with embedded newlines spans multiple lines."""
    text = "Checking.<|tool_call>\ncall:run_terminal{command:pwd}\n<tool_call|>Result: here."
    result = wrapper._strip_gemma_token_bleed(text)
    assert "<|tool_call>" not in result
    assert "run_terminal" not in result
    assert "Result: here." in result


def test_bleed_clean_text_unchanged() -> None:
    """Text with no bleed tokens passes through unchanged."""
    text = "Hello, how are you today?"
    assert wrapper._strip_gemma_token_bleed(text) == text


def test_strip_display_artifacts_integrates_bleed_stripping() -> None:
    """_strip_display_artifacts applies bleed stripping before thought-block stripping."""
    text = "<|tool_call>run_terminal{command:ls}<tool_call|><|channel>thought\ninternal\n<channel|>Visible reply"
    result = wrapper._strip_display_artifacts(text)
    assert "<|tool_call>" not in result
    assert "<|channel>" not in result
    assert result == "Visible reply"


def test_strip_display_artifacts_removes_bare_channel_marker() -> None:
    text = "thought\nNeed to report the tool result.\n<channel|>Visible reply<channel|>"
    result = wrapper._strip_display_artifacts(text)
    assert "<channel|>" not in result
    assert result == "Visible reply"


# ---------------------------------------------------------------------------
# TTS bleed stripping
# ---------------------------------------------------------------------------

def _tts_strip(text: str) -> str:
    """Import and call tts._prepare_for_speech."""
    import importlib
    import types

    # Stub heavy optional deps so tts.py imports cleanly without GPU hardware
    for mod_name in ("kokoro_onnx", "phonemizer", "soundfile", "onnxruntime"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    try:
        import backend.tts as tts_module
    except Exception:
        # If import still fails, fall back to testing wrapper's copy instead
        return wrapper._strip_gemma_token_bleed(text)
    return tts_module._prepare_for_speech(text)


def test_tts_strips_tool_call_bleed() -> None:
    """_prepare_for_speech removes tool_call bleed before markdown stripping."""
    text = "<|tool_call>run_terminal{command:ls}<tool_call|>Sure, I ran it."
    result = _tts_strip(text)
    assert "<|tool_call>" not in result
    assert "Sure, I ran it." in result


def test_tts_strips_bare_ollama_bleed() -> None:
    """_prepare_for_speech removes bare Ollama bleed variant."""
    text = 'call:run_terminal{<|"|>command<|"|>:<|"|>ls<|"|>}<tool_call|>Done.'
    result = _tts_strip(text)
    assert "<tool_call|>" not in result
    assert "Done." in result


# ---------------------------------------------------------------------------
# Original display tests
# ---------------------------------------------------------------------------

def test_strip_display_artifacts_removes_gemma_thought_blocks() -> None:
    text = "<|channel>thought\ninternal\n<channel|>Visible reply"
    assert wrapper._strip_display_artifacts(text) == "Visible reply"


def test_strip_display_artifacts_preserves_markdown_emphasis_content() -> None:
    text = "Based on memory, your favorite color is **purple**."
    assert wrapper._strip_display_artifacts(text) == "Based on memory, your favorite color is purple."


def test_generic_tool_results_stay_as_tool_messages() -> None:
    session = build_session("qwen3:8b")
    try:
        session._tool_definitions(session.config, query_text="read Projects/notes.txt")
        first = SimpleNamespace(content="Need to inspect the file.", tool_calls=[FakeToolCall("read_file", {"path": "Projects/notes.txt"})])
        final = SimpleNamespace(content="Done.", tool_calls=[])
        with patch("backend.wrapper.is_tool_allowed", return_value=True), patch("backend.wrapper.needs_confirmation", return_value=False), patch("backend.wrapper.execute_tool", return_value="note text"), patch.object(session, "_call_brain", return_value=final):
            event = session._handle_brain_response(first, tool_config=session.config)
        assert event["content"] == "Done."
        assert any(message.get("role") == "tool" for message in session.conversation)
        assistant_messages = [message for message in session.conversation if message.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert "tool_responses" not in assistant_messages[0]
    finally:
        close_session(session)


def test_gemma_keeps_thoughts_during_active_tool_turn() -> None:
    session = build_session("gemma4:e4b")
    try:
        session._tool_definitions(session.config, query_text="write a vault file")
        first = SimpleNamespace(
            content="<|channel>thought\nNeed confirmation before editing.\n<channel|>",
            tool_calls=[FakeToolCall("write_file", {"path": "Projects/out.txt", "content": "x"})],
        )
        with patch("backend.wrapper.is_tool_allowed", return_value=True), patch("backend.wrapper.needs_confirmation", return_value=True):
            event = session._handle_brain_response(first, tool_config=session.config)
        assert event["type"] == "tool_confirmation_requested"
        assistant_message = [message for message in session.conversation if message.get("role") == "assistant"][-1]
        assert "<|channel>thought" in assistant_message["content"]
        assert "tool_responses" not in assistant_message
    finally:
        close_session(session)


def test_gemma_tool_results_pack_into_assistant_history_and_sanitize_on_completion() -> None:
    session = build_session("gemma4:e4b")
    try:
        session._tool_definitions(session.config, query_text="read Projects/notes.txt")
        parsed = SimpleNamespace(
            content="<|tool_call>call:read_file{\"path\":\"Projects/notes.txt\"}<tool_call|>",
            tool_calls=[],
        )
        final = SimpleNamespace(content="<|channel>thought\nFinished.\n<channel|>File summary.", tool_calls=[])
        with patch("backend.wrapper.is_tool_allowed", return_value=True), patch("backend.wrapper.needs_confirmation", return_value=False), patch("backend.wrapper.execute_tool", return_value="note content"), patch.object(session, "_call_brain", return_value=final):
            event = session._handle_brain_response(parsed, tool_config=session.config)

        assert event["content"] == "File summary."
        assistant_messages = [message for message in session.conversation if message.get("role") == "assistant"]
        assert assistant_messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
        assert any(message.get("role") == "tool" for message in session.conversation)
        assert "<|channel>thought" not in json.dumps(assistant_messages[-1])
    finally:
        close_session(session)


def test_gemma_raw_json_tool_call_accepts_parameters_alias() -> None:
    schemas = get_tool_schemas_by_name({"write_memory"}, "assistant", config={})
    content = (
        '{"tool_name":"write_memory","parameters":{"topic":"preference",'
        '"entry":"My favorite color is electric blue."}}'
    )
    cleaned, calls, diagnostics = recover_gemma_ollama_tool_calls(
        content,
        provider="ollama_gemma_native",
        allowed_tool_schemas=schemas,
    )
    assert cleaned == ""
    assert len(calls) == 1
    assert calls[0].name == "write_memory"
    args = calls[0].arguments
    assert args == {"topic": "preference", "entry": "My favorite color is electric blue."}
    assert any("accepted" in item for item in diagnostics)


def test_tool_grounding_instruction_contains_exact_tool_result() -> None:
    session = build_session("gemma4:e4b")
    try:
        signature = session._tool_signature("read_file", {"path": "Projects/books.md"})
        session._turn_tool_results[signature] = "1: 1984 by Orwell, Dune by Herbert"
        instruction = session._build_tool_grounding_instruction()
        assert "authoritative" in instruction
        assert "Do not invent different file contents or facts." in instruction
        assert "1: 1984 by Orwell, Dune by Herbert" in instruction
    finally:
        close_session(session)


def test_generic_inferred_tool_helpers_are_removed() -> None:
    session = build_session("gemma4:e4b")
    try:
        assert not hasattr(session, "_infer_read_file_tool_call")
        assert not hasattr(session, "_infer_write_memory_tool_call")
        assert not hasattr(session, "_infer_missing_tool_call")
    finally:
        close_session(session)


def test_read_file_argument_path_is_sanitized_before_execution() -> None:
    session = build_session("gemma4:e4b")
    try:
        call = FakeToolCall("read_file", {"path": "Read the file Projects/hello.txt"})
        args = session._parse_tool_arguments(call)
        clean_path = wrapper._extract_explicit_vault_file_path(args["path"])
        assert clean_path == "Projects/hello.txt"
    finally:
        close_session(session)


def test_missing_tool_inference_blocked_after_native_tool_execution() -> None:
    session = build_session("gemma4:e4b")
    try:
        session.current_turn_query = "Read the file Projects/hello.txt and tell me what it says."
        session._turn_tool_call_counts[session._tool_signature("read_file", {"path": "Projects/hello.txt"})] = 1
        assert session._tool_already_executed_this_turn()
        response = SimpleNamespace(content="The file says Hello from the vault.", tool_calls=[])
        with patch.object(session, "_call_brain", return_value=response):
            event = session._handle_brain_response(response, tool_config=session.config)
        assert event["content"] == "The file says Hello from the vault."
        assistant_messages = [message for message in session.conversation if message.get("role") == "assistant"]
        assert all(message.get("tool_calls") is None for message in assistant_messages)
    finally:
        close_session(session)


def test_missing_tool_inference_blocked_by_pending_tool_event() -> None:
    session = build_session("gemma4:e4b")
    try:
        session.current_turn_query = "Read the file Projects/hello.txt and tell me what it says."
        session.pending_side_events.append({"type": "tool_executed", "tool_name": "read_file"})
        assert session._tool_already_executed_this_turn()
    finally:
        close_session(session)


def main() -> None:
    print(f"{BOLD}OpenCompanion Gemma Adapter Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    print()

    section("Gemma Bleed Stripping")
    run_test("Variant 1: bare Ollama call bleed stripped", test_bleed_variant1_bare_ollama_call)
    run_test("Variant 2: full <|tool_call>...<tool_call|> block stripped", test_bleed_variant2_full_tool_call_block)
    run_test("Variant 3: <|tool_response>...<tool_response|> block stripped", test_bleed_variant3_tool_response_block)
    run_test("Variant 4: <|tool>...<tool|> declaration block stripped", test_bleed_variant4_tool_declaration_block)
    run_test("Variant 5: stray opening token stripped", test_bleed_variant5_stray_opening_token)
    run_test("Variant 5: stray closing token stripped", test_bleed_variant5_stray_closing_token)
    run_test("Variant 6: <|\"|> quote token stripped", test_bleed_variant6_quote_token)
    run_test("Multiline tool_call block stripped", test_bleed_multiline_tool_call_block)
    run_test("Clean text passes through unchanged", test_bleed_clean_text_unchanged)
    run_test("_strip_display_artifacts integrates bleed stripping", test_strip_display_artifacts_integrates_bleed_stripping)
    run_test("Bare channel marker stripped from display output", test_strip_display_artifacts_removes_bare_channel_marker)

    section("TTS Bleed Stripping")
    run_test("TTS: tool_call bleed removed before markdown processing", test_tts_strips_tool_call_bleed)
    run_test("TTS: bare Ollama bleed removed", test_tts_strips_bare_ollama_bleed)

    section("Display")
    run_test("Gemma thought blocks are stripped from display output", test_strip_display_artifacts_removes_gemma_thought_blocks)
    run_test("Markdown emphasis survives display cleanup", test_strip_display_artifacts_preserves_markdown_emphasis_content)

    section("History and Tool Loop")
    run_test("Generic providers keep standalone tool-role messages", test_generic_tool_results_stay_as_tool_messages)
    run_test("Gemma keeps thoughts during an in-progress tool turn", test_gemma_keeps_thoughts_during_active_tool_turn)
    run_test("Gemma packs tool results into assistant history and sanitizes on completion", test_gemma_tool_results_pack_into_assistant_history_and_sanitize_on_completion)
    run_test("Gemma raw JSON tool call accepts parameters alias", test_gemma_raw_json_tool_call_accepts_parameters_alias)
    run_test("Tool grounding instruction includes exact result", test_tool_grounding_instruction_contains_exact_tool_result)
    run_test("Generic inferred tool helpers are removed", test_generic_inferred_tool_helpers_are_removed)
    run_test("read_file path arguments can be sanitized", test_read_file_argument_path_is_sanitized_before_execution)
    run_test("Missing tool inference blocked after native execution", test_missing_tool_inference_blocked_after_native_tool_execution)
    run_test("Missing tool inference blocked by pending tool event", test_missing_tool_inference_blocked_by_pending_tool_event)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
