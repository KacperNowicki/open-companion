#!/usr/bin/env python3
"""Provider integration tests for OpenCompanion.

Standalone runner style: no pytest, stdlib mocks only.
"""

from __future__ import annotations

import json
import os
import sys
import types
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _install_keyring_stub() -> None:
    if "keyring" in sys.modules:
        return
    module = types.ModuleType("keyring")
    module.get_password = lambda service, account: None
    module.set_password = lambda service, account, password: None
    sys.modules["keyring"] = module


def _install_openai_stub() -> None:
    if "openai" in sys.modules:
        return

    module = types.ModuleType("openai")

    class _StubOpenAI:
        next_response = None
        next_error = None
        next_models = None

        def __init__(self, base_url: str | None = None, api_key: str | None = None):
            self.base_url = base_url
            self.api_key = api_key
            self.calls: list[dict] = []
            self.response_calls: list[dict] = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
            self.responses = SimpleNamespace(create=self._responses_create)
            self.models = SimpleNamespace(list=self._models_list)

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            if type(self).next_error is not None:
                raise type(self).next_error
            response = type(self).next_response
            if response is None:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK", tool_calls=[]))])
            if callable(response):
                return response(kwargs)
            return response

        def _responses_create(self, **kwargs):
            self.response_calls.append(kwargs)
            if type(self).next_error is not None:
                raise type(self).next_error
            response = type(self).next_response
            if response is None:
                return SimpleNamespace(output_text="OK", output=[])
            if callable(response):
                return response(kwargs)
            return response

        def _models_list(self):
            models = type(self).next_models
            if models is None:
                models = [SimpleNamespace(id="model-a"), SimpleNamespace(id="model-b")]
            return SimpleNamespace(data=models)

    module.OpenAI = _StubOpenAI
    sys.modules["openai"] = module


_install_keyring_stub()
_install_openai_stub()

import brain  # noqa: E402
from anthropic_models import get_anthropic_model_card, get_anthropic_model_capabilities  # noqa: E402
from chatgpt_oauth_models import (  # noqa: E402
    get_chatgpt_oauth_model_capabilities,
    get_chatgpt_oauth_model_ids,
    normalize_chatgpt_oauth_model_name,
)
from providers.anthropic import AnthropicProvider  # noqa: E402
from providers.chatgpt_oauth import ChatGPTOAuthProvider, _require_chatgpt_oauth_client_id, _sanitize_bearer_token  # noqa: E402
from providers.base import (  # noqa: E402
    BrainProviderAuthError,
    BrainProviderConnectionError,
    BrainProviderRateLimitError,
    read_keyring_secret,
)

from tests.helpers.setup import fail, ok, summary  # noqa: E402


class RecordingOpenAI:
    instances: list["RecordingOpenAI"] = []
    next_response = None
    next_error = None
    next_models = None

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key
        self.calls: list[dict] = []
        self.response_calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.responses = SimpleNamespace(create=self._responses_create)
        self.models = SimpleNamespace(list=self._models_list)
        RecordingOpenAI.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if type(self).next_error is not None:
            raise type(self).next_error
        response = type(self).next_response
        if response is None:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK", tool_calls=[]))])
        if callable(response):
            return response(kwargs)
        return response

    def _responses_create(self, **kwargs):
        self.response_calls.append(kwargs)
        if type(self).next_error is not None:
            raise type(self).next_error
        response = type(self).next_response
        if response is None:
            return SimpleNamespace(output_text="OK", output=[])
        if callable(response):
            return response(kwargs)
        return response

    def _models_list(self):
        models = type(self).next_models
        if models is None:
            models = [SimpleNamespace(id="model-a"), SimpleNamespace(id="model-b")]
        return SimpleNamespace(data=models)


class FakeAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def close(self) -> None:
        return None

    def __iter__(self):
        return iter(())


class FakeSSEResponse:
    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def close(self) -> None:
        self.closed = True


class FakeBody:
    def __init__(self, text: str):
        self.text = text

    def read(self) -> bytes:
        return self.text.encode("utf-8")

    def close(self) -> None:
        return None


def _reset_openai_stub() -> None:
    RecordingOpenAI.instances.clear()
    RecordingOpenAI.next_response = None
    RecordingOpenAI.next_error = None
    RecordingOpenAI.next_models = None




def _render_gemma_docs_prompt(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "tool":
            tool_name = str(message.get("tool_name") or message.get("name") or message.get("tool_call_id") or "tool_result")
            if isinstance(content, str):
                response_blob = f'<|"|>{content}<|"|>'
            else:
                response_blob = json.dumps(content, ensure_ascii=False)
            parts.append(f'<|turn>tool\n<|tool_response>response:{tool_name}' + '{response:' + response_blob + '}<tool_response|><turn|>')
            continue
        if role == "assistant":
            body = content
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                call = tool_calls[0]
                fn = call.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if args:
                    arg_parts = []
                    for key, value in args.items():
                        if isinstance(value, str):
                            arg_parts.append(f'{key}:<|"|>{value}<|"|>')
                        else:
                            arg_parts.append(f'{key}:{json.dumps(value, ensure_ascii=False)}')
                    arg_blob = '{' + ','.join(arg_parts) + '}'
                else:
                    arg_blob = '{}'
                body = f'<|tool_call>call:{fn.get("name", "")}{arg_blob}<tool_call|>' + body
            parts.append(f'<|turn>model\n{body}<turn|>')
            continue
        parts.append(f'<|turn>{role}\n{content}<turn|>')
    return '\n'.join(parts)

def _build_config(provider: str, model: str = "test-model") -> dict:
    return {
        "brain": {
            "provider": provider,
            "model": model,
            "temperature": 0.25,
            "max_tokens": 64,
            "stream": False,
            "layers": {"companion": {"provider": provider, "model": model}},
        }
    }


def test_keyring_lookup_behavior() -> None:
    name = "keyring lookup resolves provider secrets"
    calls: list[tuple[str, str]] = []

    def fake_get_password(service: str, account: str) -> str | None:
        calls.append((service, account))
        if (service, account) == ("open-companion", "openai_api_key"):
            return "sk-openai"
        if (service, account) == ("open-companion", "gemini_api_key"):
            return "sk-gemini"
        return None

    with mock.patch("providers.base.keyring.get_password", side_effect=fake_get_password):
        assert read_keyring_secret(["openai_api_key", "openai"]) == "sk-openai"
        assert read_keyring_secret(["gemini_api_key"]) == "sk-gemini"
        openai_cfg = brain.get_layer_brain_config(_build_config("openai"), "companion")
        gemini_cfg = brain.get_layer_brain_config(_build_config("gemini"), "companion")

    assert openai_cfg["api_key"] == "sk-openai"
    assert gemini_cfg["api_key"] == "sk-gemini"
    assert calls[0] == ("open-companion", "openai_api_key")
    ok(name)


def test_openai_provider_request_and_errors() -> None:
    name = "openai chat-completions path builds requests and translates errors"
    _reset_openai_stub()

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-openai" if account == "openai_api_key" else None,
        ):
            client = brain.create_client(_build_config("openai", "gpt-4.1"), layer_name="companion")
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "System rules."},
                    {"role": "user", "content": "Hello"},
                ],
                tools=[{
                    "type": "function",
                    "function": {"name": "demo_tool", "description": "demo", "parameters": {"type": "object", "properties": {}}},
                }],
            )

    assert response.choices[0].message.content == "OK"
    instance = RecordingOpenAI.instances[-1]
    assert instance.base_url == "https://api.openai.com/v1"
    assert instance.api_key == "sk-openai"
    assert instance.response_calls == []
    call = instance.calls[-1]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert call["tools"][0]["function"]["name"] == "demo_tool"

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-openai" if account == "openai_api_key" else None,
        ):
            client = brain.create_client(_build_config("openai"), layer_name="companion")
            RecordingOpenAI.next_error = FakeAPIError("authentication failed", status_code=401)
            try:
                client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
                raise AssertionError("expected auth error")
            except BrainProviderAuthError as exc:
                assert "openai" in str(exc).lower()
            RecordingOpenAI.next_error = FakeAPIError("rate limited", status_code=429)
            try:
                client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
                raise AssertionError("expected rate limit error")
            except BrainProviderRateLimitError as exc:
                assert "rate limit" in str(exc).lower()
            RecordingOpenAI.next_error = OSError("network unreachable")
            try:
                client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
                raise AssertionError("expected connection error")
            except BrainProviderConnectionError as exc:
                assert "connection" in str(exc).lower()

    RecordingOpenAI.next_error = None
    ok(name)


def test_openai_responses_path_and_tool_normalization() -> None:
    name = "openai reasoning models use Responses API and normalize tool calls"
    _reset_openai_stub()

    response_payload = SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Need to inspect the file.")],
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_123",
                name="demo_tool",
                arguments='{"path":"Projects/notes.txt"}',
            ),
        ],
    )

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        RecordingOpenAI.next_response = response_payload
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-openai" if account == "openai_api_key" else None,
        ):
            client = brain.create_client(_build_config("openai", "gpt-5.4"), layer_name="companion")
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "System rules."},
                    {"role": "user", "content": "Hello"},
                ],
                tools=[{
                    "type": "function",
                    "function": {"name": "demo_tool", "description": "demo", "parameters": {"type": "object", "properties": {}}},
                }],
            )

    instance = RecordingOpenAI.instances[-1]
    assert instance.calls == []
    request = instance.response_calls[-1]
    assert request["model"] == "gpt-5.4"
    assert request["instructions"] == "System rules."
    assert request["input"] == [{"role": "user", "content": "Hello"}]
    assert request["tools"][0]["name"] == "demo_tool"
    assert request["reasoning"] == {"effort": "none", "summary": "auto"}
    message = response.choices[0].message
    assert message.content == "Need to inspect the file."
    assert message.tool_calls[0].id == "call_123"
    assert message.tool_calls[0].function.name == "demo_tool"
    assert message.tool_calls[0].function.arguments == '{"path":"Projects/notes.txt"}'

    RecordingOpenAI.next_response = None
    ok(name)


def test_openai_responses_path_serializes_tool_history() -> None:
    name = "openai responses path adapts assistant tool history into input items"
    _reset_openai_stub()

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-openai" if account == "openai_api_key" else None,
        ):
            client = brain.create_client(_build_config("openai", "gpt-5.4"), layer_name="companion")
            response = client.chat.completions.create(
                messages=[
                    {"role": "user", "content": "Read the file."},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "demo_tool", "arguments": '{"path":"Projects/notes.txt"}'},
                        }],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
                ],
            )

    instance = RecordingOpenAI.instances[-1]
    request = instance.response_calls[-1]
    assert request["input"][0] == {"role": "user", "content": "Read the file."}
    assert request["input"][1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "demo_tool",
        "arguments": '{"path":"Projects/notes.txt"}',
    }
    assert request["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "file body",
    }
    assert response.choices[0].message.content == "OK"
    ok(name)


def test_openai_unknown_model_falls_back_to_chat_completions() -> None:
    name = "unknown openai models keep the conservative chat-completions fallback"
    _reset_openai_stub()

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-openai" if account == "openai_api_key" else None,
        ):
            cfg = _build_config("openai", "gpt-future-1")
            client = brain.create_client(cfg, layer_name="companion")
            response = client.chat.completions.create(messages=[{"role": "user", "content": "ping"}])

    instance = RecordingOpenAI.instances[-1]
    assert instance.response_calls == []
    assert instance.calls[-1]["model"] == "gpt-future-1"
    assert client.provider.model_family == "openai_chat"
    assert response.choices[0].message.content == "OK"
    ok(name)


def test_openai_model_registry_and_family_defaults() -> None:
    name = "openai model registry exposes capabilities and family defaults"
    gpt54_cfg = brain.get_layer_brain_config(_build_config("openai", "gpt-5.4"), "companion")
    caps = brain.resolve_openai_model_capabilities(gpt54_cfg)
    assert caps is not None
    assert caps["family"] == "openai_reasoning"
    assert caps["preferred_api"] == "responses"
    assert caps["default_reasoning_effort"] == "none"
    assert caps["registry_match"] is True

    unknown_cfg = brain.get_layer_brain_config(_build_config("openai", "gpt-future-1"), "companion")
    unknown_caps = brain.resolve_openai_model_capabilities(unknown_cfg)
    assert unknown_caps is not None
    assert unknown_caps["family"] == "openai_chat"
    assert unknown_caps["preferred_api"] == "chat_completions"
    assert unknown_caps["registry_match"] is False
    ok(name)


def test_provider_auto_context_window_uses_registry_metadata() -> None:
    name = "provider auto context window uses OpenAI and Anthropic registry metadata"
    openai_cfg = _build_config("openai", "gpt-5.4")
    anthropic_cfg = _build_config("anthropic", "claude-opus-4-6")
    explicit_cfg = _build_config("anthropic", "claude-opus-4-6")
    explicit_cfg["brain"]["context_window"] = 65536

    assert brain.get_context_window(openai_cfg, "companion") == 1_050_000
    assert brain.get_context_window(anthropic_cfg, "companion") == 200_000
    assert brain.get_context_window(explicit_cfg, "companion") == 65536
    ok(name)


def test_chatgpt_oauth_build_input_uses_openclaw_message_shape() -> None:
    name = "chatgpt oauth uses OpenClaw-style response input items"
    provider = ChatGPTOAuthProvider(
        settings={"provider": "chatgpt_oauth", "model": "gpt-5.4"},
        layer_name="assistant",
    )

    items = provider._build_input(
        [
            {"role": "user", "content": "Read the file."},
            {
                "role": "assistant",
                "content": "Let me inspect it.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_terminal", "arguments": '{"command":"cat Projects/notes.txt"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
        ],
        system="# Assistant Identity\n\nStay concise.",
    )

    assert items[0] == {
        "role": "developer",
        "content": "# Assistant Identity\n\nStay concise.\n\nUse the provided tools when they are available and necessary.\n\nKeep replies in plain prose with no markdown or bullet points.",
    }
    assert items[1] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "Read the file."}],
    }
    assert items[2] == {
        "type": "message",
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "text": "Let me inspect it.",
            "annotations": [],
        }],
        "status": "completed",
        "id": "msg_2",
    }
    assert items[3] == {
        "type": "function_call",
        "id": "call_1",
        "call_id": "call_1",
        "name": "run_terminal",
        "arguments": '{"command":"cat Projects/notes.txt"}',
    }
    assert items[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "file contents",
    }
    ok(name)


def test_chatgpt_oauth_plans_incremental_tool_result_turns() -> None:
    name = "chatgpt oauth reuses previous response id for incremental tool results"
    provider = ChatGPTOAuthProvider(
        settings={"provider": "chatgpt_oauth", "model": "gpt-5.4"},
        layer_name="assistant",
    )
    provider._previous_response_id = "resp_123"
    provider._last_context_length = 2

    planned = provider._plan_turn_input([
        {"role": "user", "content": "Read the file."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_terminal", "arguments": '{"command":"cat Projects/notes.txt"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "note body"},
    ])

    assert planned["previous_response_id"] == "resp_123"
    assert planned["input"] == [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "note body",
    }]
    ok(name)


def test_chatgpt_oauth_sanitizes_bearer_tokens() -> None:
    name = "chatgpt oauth sanitizes control characters from bearer tokens"
    dirty = " \r\nabc\x00.def\tghi\x7f \n"
    clean = _sanitize_bearer_token(dirty)
    assert clean == "abc.defghi"
    ok(name)


def test_chatgpt_oauth_has_public_client_id_fallback() -> None:
    name = "chatgpt oauth falls back to the built-in public client id"

    with mock.patch.dict(os.environ, {"CHATGPT_OAUTH_CLIENT_ID": ""}, clear=False):
        with mock.patch(
            "providers.chatgpt_oauth._read_repo_env_value",
            return_value="",
        ):
            assert _require_chatgpt_oauth_client_id() == "app_EMoamEEZ73f0CkXaXp7hrann"

    ok(name)

def test_gemini_auth_header_registry_and_tool_calls() -> None:
    name = "gemini native REST uses x-goog-api-key, registry metadata, and tool-call normalization"
    from gemini_models import get_gemini_model_capabilities

    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=300):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeHTTPResponse({
            "candidates": [{
                "content": {
                    "parts": [
                        {"thought": True, "text": "private reasoning"},
                        {"functionCall": {"name": "read_file", "args": {"path": "Notes/a.md"}}},
                        {"text": "Visible text."},
                    ]
                }
            }]
        })

    with mock.patch(
        "providers.base.keyring.get_password",
        side_effect=lambda service, account: "sk-gemini" if account == "gemini_api_key" else None,
    ):
        client = brain.create_client(_build_config("gemini", "gemini-2.5-flash"), layer_name="companion")
        with mock.patch("providers.gemini.urllib_request.urlopen", side_effect=fake_urlopen):
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": "Read it"}],
                tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {}}}}],
                extra_body={"enable_thinking": True, "thinking_budget": 1024},
            )

    assert captured["url"].endswith("/models/gemini-2.5-flash:generateContent")
    headers = {str(k).lower(): v for k, v in captured["headers"].items()}
    assert headers["x-goog-api-key"] == "sk-gemini"
    body = captured["body"]
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 1024
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert response.choices[0].message.content == "Visible text."
    assert response.choices[0].message.tool_calls[0].function.name == "read_file"
    assert json.loads(response.choices[0].message.tool_calls[0].function.arguments) == {"path": "Notes/a.md"}
    caps = get_gemini_model_capabilities("gemini-2.5-pro")
    assert caps["supports_thinking"] is True
    assert caps["supports_tools"] is True
    ok(name)


def test_gemini_3_uses_thinking_level() -> None:
    name = "gemini 3 uses thinkingLevel instead of thinkingBudget"
    from providers.gemini import GeminiProvider

    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=300):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeHTTPResponse({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})

    provider = GeminiProvider({"provider": "gemini", "model": "gemini-3-pro-preview", "api_key": "sk", "reasoning_effort": "low"})
    with mock.patch("providers.gemini.urllib_request.urlopen", side_effect=fake_urlopen):
        provider.chat(messages=[{"role": "user", "content": "hi"}])

    thinking = captured["body"]["generationConfig"]["thinkingConfig"]
    assert thinking == {"thinkingLevel": "low"}
    ok(name)


def test_gemma_family_does_not_infer_raw_content_by_default() -> None:
    name = "gemma provider does not infer raw content tool calls by default"
    from providers.gemma import GemmaProvider

    provider = GemmaProvider({"provider": "gemma", "model": "gemma4:e4b"})
    raw_message = SimpleNamespace(
        role="assistant",
        content=(
            "<|channel>thought\nNeed to inspect the note file.\n<channel|>"
            "<|tool_call>call:read_vault_note{}<tool_call|><|tool_response>"
        ),
        tool_calls=[],
    )

    parsed = provider._parse_message(raw_message, "gemma4:e4b")
    assert provider.model_family == "google_gemma"
    assert parsed.tool_calls == []
    assert "<|tool_call>" in parsed.content
    assert "<|tool_response>" in parsed.content
    assert "<|channel>thought" in parsed.content
    ok(name)




def test_tool_call_argument_dicts_normalize_to_json_strings() -> None:
    name = "native tool-call argument objects normalize to JSON strings"
    from providers.base import normalize_tool_calls

    calls = normalize_tool_calls([
        {
            "id": "call_1",
            "function": {
                "name": "read_file",
                "arguments": {"path": "Projects/notes.txt"},
            },
        }
    ])
    assert calls[0].function.arguments == '{"path": "Projects/notes.txt"}'
    ok(name)



def test_ollama_gemma_native_request_matches_doc_shape() -> None:
    name = "ollama gemma native chat uses doc-shaped tool history"
    from providers.gemma import GemmaProvider

    requests: list[dict[str, object]] = []

    def fake_urlopen(req, timeout=300):
        payload = json.loads(req.data.decode("utf-8"))
        requests.append({"url": req.full_url, "body": payload})
        return FakeHTTPResponse({
            "model": "gemma4:26b",
            "message": {
                "role": "assistant",
                "content": "File summary.",
                "thinking": "Checked the tool output first.",
            },
            "done": True,
            "done_reason": "stop",
        })

    provider = GemmaProvider({"provider": "gemma", "model": "gemma4:26b", "temperature": 1.0, "max_tokens": 128, "context_window": 32768})

    with mock.patch("providers.gemma.urllib_request.urlopen", side_effect=fake_urlopen):
        response = provider.chat(
            messages=[
                {"role": "user", "content": "Read the project note."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_vault_note",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "name": "read_vault_note", "content": "This file contains project notes."},
            ],
            system="You are a helpful assistant.",
            stream=False,
            tools=[{
                "type": "function",
                "function": {
                    "name": "read_vault_note",
                    "description": "Read the project note.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            model="gemma4:26b",
            temperature=1.0,
            max_tokens=128,
            extra_body={"options": {"num_ctx": 32768, "top_p": 0.95, "top_k": 64}},
        )

    request = requests[-1]
    assert request["url"].endswith("/api/chat")
    body = request["body"]
    assert body["options"]["num_ctx"] == 32768
    assert body["options"]["temperature"] == 1.0
    assert body["options"]["top_p"] == 0.95
    assert body["options"]["top_k"] == 64
    assistant_turn = body["messages"][2]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"] == ""
    assert assistant_turn["tool_calls"][0]["function"]["name"] == "read_vault_note"
    assert assistant_turn["tool_calls"][0]["function"]["arguments"] == {}
    tool_turn = body["messages"][3]
    assert tool_turn["role"] == "tool"
    assert tool_turn["tool_name"] == "read_vault_note"
    assert "tool_call_id" not in tool_turn
    assert tool_turn["content"] == "This file contains project notes."
    assert response.content == (
        "<|channel>thought\n"
        "Checked the tool output first.\n"
        "<channel|>File summary."
    )
    ok(name)



def test_ollama_gemma_native_request_matches_docs_transformers_lifecycle() -> None:
    name = "ollama gemma native request matches docs-style transformers lifecycle"
    from providers.gemma import GemmaProvider

    requests: list[dict[str, object]] = []

    def fake_urlopen(req, timeout=300):
        payload = json.loads(req.data.decode("utf-8"))
        requests.append(payload)
        return FakeHTTPResponse({
            "model": "gemma4:26b",
            "message": {
                "role": "assistant",
                "content": "Done.",
                "thinking": "Checked the tool output.",
            },
            "done": True,
            "done_reason": "stop",
        })

    provider = GemmaProvider({"provider": "gemma", "model": "gemma4:26b", "temperature": 1.0, "max_tokens": 128})

    with mock.patch("providers.gemma.urllib_request.urlopen", side_effect=fake_urlopen):
        provider.chat(
            messages=[
                {"role": "user", "content": "Read the project note."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_vault_note",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "name": "read_vault_note", "content": "This file contains project notes."},
            ],
            system="<|think|>You are a helpful assistant.",
            stream=False,
            tools=[{
                "type": "function",
                "function": {
                    "name": "read_vault_note",
                    "description": "Read the project note.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            model="gemma4:26b",
            temperature=1.0,
            max_tokens=128,
            extra_body={"options": {"num_ctx": 32768, "top_p": 0.95, "top_k": 64}},
        )

    rendered = _render_gemma_docs_prompt(requests[-1]["messages"])
    assert '<|turn>system\n<|think|>You are a helpful assistant.<turn|>' in rendered
    assert '<|turn>user\nRead the project note.<turn|>' in rendered
    assert '<|turn>model\n<|tool_call>call:read_vault_note{}<tool_call|>' in rendered
    assert '<|turn>tool\n<|tool_response>response:read_vault_note{response:<|"|>This file contains project notes.<|"|>}<tool_response|><turn|>' in rendered
    ok(name)


def test_ollama_gemma_native_retries_with_smaller_num_ctx_on_memory_error() -> None:
    name = "ollama gemma native chat retries with smaller num_ctx on memory error"
    from providers.gemma import GemmaProvider

    requests: list[dict[str, object]] = []

    def fake_urlopen(req, timeout=300):
        payload = json.loads(req.data.decode("utf-8"))
        requests.append(payload)
        num_ctx = payload.get("options", {}).get("num_ctx")
        if num_ctx == 262144:
            raise urllib.error.HTTPError(
                req.full_url,
                500,
                "Internal Server Error",
                hdrs=None,
                fp=FakeBody(json.dumps({"error": "model requires more system memory (27.9 GiB) than is available (27.7 GiB)"})),
            )
        return FakeHTTPResponse({
            "model": "gemma4:26b",
            "message": {
                "role": "assistant",
                "content": "ok",
                "thinking": "",
            },
            "done": True,
            "done_reason": "stop",
        })

    provider = GemmaProvider({"provider": "gemma", "model": "gemma4:26b", "temperature": 1.0, "max_tokens": 32, "context_window": 262144})

    with mock.patch("providers.gemma.urllib_request.urlopen", side_effect=fake_urlopen):
        response = provider.chat(
            messages=[{"role": "user", "content": "Say only ok."}],
            system="You are a helpful assistant.",
            stream=False,
            tools=[],
            model="gemma4:26b",
            temperature=1.0,
            max_tokens=32,
            extra_body={"options": {"num_ctx": 262144, "top_p": 0.95, "top_k": 64}},
        )

    assert len(requests) >= 2
    assert requests[0]["options"]["num_ctx"] == 262144
    assert requests[1]["options"]["num_ctx"] == 131072
    assert response.content == "ok"
    ok(name)


def test_ollama_generic_health_and_cloud_auth_hint() -> None:
    name = "generic ollama provider pings tags, lists cloud hints, and explains cloud auth"
    from providers.ollama_generic import OllamaGenericProvider, is_ollama_cloud_model

    def fake_tags(req_or_url, timeout=30):
        return FakeHTTPResponse({"models": [{"name": "llama3.2:3b"}]})

    provider = OllamaGenericProvider({"provider": "ollama", "model": "kimi-k2.6:cloud"})
    with mock.patch("providers.ollama_generic.urllib_request.urlopen", side_effect=fake_tags):
        assert provider.health_check() is True
        models = provider.list_models()

    assert "llama3.2:3b" in models
    assert "kimi-k2.6:cloud" in models
    assert is_ollama_cloud_model("glm-4.6:cloud") is True
    assert is_ollama_cloud_model("deepseek-v3.1:671-cloud") is True

    class AuthError(Exception):
        status_code = 401

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        RecordingOpenAI.next_error = AuthError("unauthorized")
        try:
            provider = OllamaGenericProvider({"provider": "ollama", "model": "kimi-k2.6:cloud"})
            provider.chat(messages=[{"role": "user", "content": "hi"}])
            raise AssertionError("expected auth error")
        except BrainProviderAuthError as exc:
            assert "ollama login" in str(exc).lower()
        finally:
            RecordingOpenAI.next_error = None

    ok(name)

def test_custom_provider_uses_supplied_base_url() -> None:
    name = "custom provider uses user-supplied base URL"
    _reset_openai_stub()

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        cfg = {
            "brain": {
                "provider": "custom",
                "base_url": "https://example.invalid/v1",
                "custom_api_key": "custom-secret",
                "model": "custom-model",
                "layers": {"companion": {"provider": "custom", "model": "custom-model"}},
            }
        }
        resolved = brain.get_layer_brain_config(cfg, "companion")
        assert resolved["base_url"] == "https://example.invalid/v1"
        assert resolved["api_key"] == "custom-secret"
        client = brain.create_client(cfg, layer_name="companion")
        client.chat.completions.create(messages=[{"role": "user", "content": "ping"}])

    instance = RecordingOpenAI.instances[-1]
    assert instance.base_url == "https://example.invalid/v1"
    assert instance.api_key == "custom-secret"
    ok(name)


def test_custom_provider_health_degrades_when_models_missing() -> None:
    name = "custom provider health check degrades instead of failing when /models is absent"
    from providers.custom import CustomProvider

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        RecordingOpenAI.next_error = FakeAPIError("not found", status_code=404)
        provider = CustomProvider({"provider": "custom", "base_url": "https://example.invalid/v1", "model": "custom-model"})
        provider._client.models.list = mock.Mock(side_effect=FakeAPIError("not found", status_code=404))
        assert provider.health_check() is False
        assert provider.list_models() == []
        RecordingOpenAI.next_error = None

    ok(name)


def test_openrouter_fallback_and_tool_normalization() -> None:
    name = "openrouter uses OpenAI-compatible tools and model-list fallback"
    from providers.openrouter import OpenRouterProvider

    _reset_openai_stub()
    tool_call = SimpleNamespace(
        id="call_or",
        type="function",
        function=SimpleNamespace(name="read_file", arguments='{"path":"Notes/a.md"}'),
    )
    RecordingOpenAI.next_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(role="assistant", content="", tool_calls=[tool_call]))]
    )

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-or" if account == "openrouter_api_key" else None,
        ):
            client = brain.create_client(_build_config("openrouter", "moonshotai/kimi-k2.6"), layer_name="companion")
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": "Read"}],
                tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {}}}}],
            )
            provider = client.provider

    instance = RecordingOpenAI.instances[-1]
    assert instance.base_url == "https://openrouter.ai/api/v1"
    assert instance.api_key == "sk-or"
    assert instance.calls[-1]["tools"][0]["function"]["name"] == "read_file"
    assert response.choices[0].message.tool_calls[0].function.name == "read_file"

    with mock.patch("providers.openrouter.urllib_request.urlopen", side_effect=OSError("network down")):
        models = provider.list_models()
    assert "moonshotai/kimi-k2.6" in models
    ok(name)


def test_anthropic_request_construction_and_stream_parsing() -> None:
    name = "anthropic native messages and SSE parsing"
    requests: list[dict[str, object]] = []

    def fake_urlopen(req, timeout=60):
        headers = {key.lower(): value for key, value in req.header_items()}
        payload = json.loads(req.data.decode("utf-8"))
        record = {"url": req.full_url, "headers": headers, "body": payload, "stream": headers.get("accept") == "text/event-stream"}
        requests.append(record)
        if record["stream"]:
            return FakeSSEResponse(
                [
                    b"event: message_start\n",
                    b'data: {"message":{"role":"assistant","content":[]}}\n',
                    b"\n",
                    b"event: content_block_start\n",
                    b'data: {"index":0,"content_block":{"type":"text","text":""}}\n',
                    b"\n",
                    b"event: content_block_delta\n",
                    b'data: {"index":0,"delta":{"type":"text_delta","text":"Hel"}}\n',
                    b"\n",
                    b"event: content_block_delta\n",
                    b'data: {"index":0,"delta":{"type":"text_delta","text":"lo"}}\n',
                    b"\n",
                    b"event: content_block_start\n",
                    b'data: {"index":1,"content_block":{"type":"tool_use","id":"tool-1","name":"demo_tool","input":{}}}\n',
                    b"\n",
                    b"event: content_block_delta\n",
                    b'data: {"index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":1}"}}\n',
                    b"\n",
                ]
            )
        return FakeHTTPResponse({"content": [{"type": "text", "text": "Hello world"}]})

    provider = AnthropicProvider(
        {
            "provider": "anthropic",
            "api_key": "anthropic-secret",
            "model": "claude-sonnet-4-5",
            "temperature": 0.2,
            "max_tokens": 32,
        }
    )

    with mock.patch("providers.anthropic.request.urlopen", side_effect=fake_urlopen):
        response = provider.chat(
            messages=[{"role": "system", "content": "System prompt"}, {"role": "user", "content": "Say hi"}],
            system="Top-level system",
            tools=[{
                "type": "function",
                "function": {
                    "name": "demo_tool",
                    "description": "Demo",
                    "parameters": {"type": "object", "properties": {"a": {"type": "number"}}},
                },
            }],
            stream=False,
        )
        stream_response = provider.chat(
            messages=[{"role": "user", "content": "Say hi"}],
            system="Top-level system",
            stream=True,
        )

    non_stream = next(item for item in requests if not item["stream"])
    stream = next(item for item in requests if item["stream"])

    assert non_stream["url"] == "https://api.anthropic.com/v1/messages"
    assert non_stream["headers"]["x-api-key"] == "anthropic-secret"
    assert non_stream["headers"]["anthropic-version"] == "2023-06-01"
    assert non_stream["body"]["system"] == "Top-level system"
    assert non_stream["body"]["messages"][0]["role"] == "user"
    assert non_stream["body"]["tools"][0]["name"] == "demo_tool"
    assert response.content == "Hello world"
    assert response.tool_calls == []
    assert stream["headers"]["accept"] == "text/event-stream"
    assert stream_response.content == "Hello"
    assert stream_response.tool_calls[0].id == "tool-1"
    assert stream_response.tool_calls[0].function.name == "demo_tool"
    assert stream_response.tool_calls[0].function.arguments == "{\"a\":1}"
    ok(name)

def test_anthropic_user_friendly_errors() -> None:
    name = "anthropic errors are translated into user-friendly messages"
    provider = AnthropicProvider({"provider": "anthropic", "api_key": "secret", "model": "claude-sonnet-4"})

    auth_error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=FakeBody('{"error":{"message":"bad key"}}'),
    )
    with mock.patch("providers.anthropic.request.urlopen", side_effect=auth_error):
        try:
            provider.chat(messages=[{"role": "user", "content": "hi"}], system="s")
            raise AssertionError("expected auth error")
        except BrainProviderAuthError as exc:
            assert "bad key" in str(exc)

    rate_error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=FakeBody('{"error":{"message":"slow down"}}'),
    )
    with mock.patch("providers.anthropic.request.urlopen", side_effect=rate_error):
        try:
            provider.chat(messages=[{"role": "user", "content": "hi"}], system="s")
            raise AssertionError("expected rate limit error")
        except BrainProviderRateLimitError as exc:
            assert "slow down" in str(exc)

    with mock.patch("providers.anthropic.request.urlopen", side_effect=urllib.error.URLError("offline")):
        try:
            provider.chat(messages=[{"role": "user", "content": "hi"}], system="s")
            raise AssertionError("expected connection error")
        except BrainProviderConnectionError as exc:
            assert "connection" in str(exc).lower()

    ok(name)


def test_anthropic_model_registry_resolution() -> None:
    name = "anthropic model registry resolves known and unknown models"
    opus = get_anthropic_model_card("claude-opus-4-6")
    assert opus.id == "claude-opus-4"
    assert opus.family == "anthropic_claude_reasoning"
    assert opus.preferred_api == "messages"
    assert "low" in opus.reasoning_efforts
    assert "high" in opus.reasoning_efforts
    assert opus.default_reasoning_effort == "medium"
    assert opus.supports_tools is True
    assert opus.supports_image_input is True
    assert opus.context_window == 200_000

    sonnet = get_anthropic_model_card("claude-sonnet-4-5-20250514")
    assert sonnet.id == "claude-sonnet-4"
    assert sonnet.family == "anthropic_claude_reasoning"

    haiku = get_anthropic_model_card("claude-haiku-4-5")
    assert haiku.id == "claude-haiku-4"
    assert haiku.family == "anthropic_claude_chat"
    assert haiku.reasoning_efforts == ()

    unknown = get_anthropic_model_card("claude-future-99")
    assert unknown.id == "anthropic-unknown"
    assert unknown.family == "anthropic_claude_chat"
    assert unknown.reasoning_efforts == ()
    ok(name)


def test_anthropic_model_capabilities_metadata() -> None:
    name = "anthropic model capabilities returns full metadata dict"
    caps = get_anthropic_model_capabilities("claude-opus-4-6")
    assert caps["family"] == "anthropic_claude_reasoning"
    assert caps["preferred_api"] == "messages"
    assert caps["registry_match"] is True
    assert caps["model"] == "claude-opus-4-6"
    assert caps["default_reasoning_effort"] == "medium"

    unknown_caps = get_anthropic_model_capabilities("claude-future-99")
    assert unknown_caps["family"] == "anthropic_claude_chat"
    assert unknown_caps["registry_match"] is False
    ok(name)


def test_anthropic_family_resolution_through_model_family() -> None:
    name = "anthropic models resolve family through model_family module"
    from model_family import resolve_model_family

    assert resolve_model_family({"provider": "anthropic", "model": "claude-opus-4-6"}) == "anthropic_claude_reasoning"
    assert resolve_model_family({"provider": "anthropic", "model": "claude-haiku-4-5"}) == "anthropic_claude_chat"
    assert resolve_model_family({"provider": "anthropic", "model": "claude-future-99"}) == "anthropic_claude_chat"
    assert resolve_model_family({"provider": "gemma", "model": "gemma4:e4b"}) == "google_gemma"
    ok(name)


def test_anthropic_thinking_budget_wiring() -> None:
    name = "anthropic provider wires thinking budget from reasoning_effort config"
    requests: list[dict[str, object]] = []

    def fake_urlopen(req, timeout=60):
        payload = json.loads(req.data.decode("utf-8"))
        requests.append(payload)
        return FakeHTTPResponse({"content": [{"type": "text", "text": "Thought about it."}]})

    provider_with_thinking = AnthropicProvider({
        "provider": "anthropic",
        "api_key": "secret",
        "model": "claude-opus-4-6",
        "reasoning_effort": "high",
        "max_tokens": 4096,
    })
    with mock.patch("providers.anthropic.request.urlopen", side_effect=fake_urlopen):
        response = provider_with_thinking.chat(
            messages=[{"role": "user", "content": "Think hard"}],
            system="System",
        )

    assert requests[-1].get("thinking") == {"type": "enabled", "budget_tokens": 4095}
    assert requests[-1]["temperature"] == 1
    assert response.content == "Thought about it."

    requests.clear()
    provider_no_thinking = AnthropicProvider({
        "provider": "anthropic",
        "api_key": "secret",
        "model": "claude-haiku-4-5",
        "reasoning_effort": "high",
        "temperature": 0.5,
        "max_tokens": 1024,
    })
    with mock.patch("providers.anthropic.request.urlopen", side_effect=fake_urlopen):
        provider_no_thinking.chat(
            messages=[{"role": "user", "content": "Quick answer"}],
            system="System",
        )

    assert "thinking" not in requests[-1]
    assert requests[-1]["temperature"] == 0.5
    ok(name)


def test_anthropic_tool_call_normalization() -> None:
    name = "anthropic tool_use blocks normalize to standard BrainMessage tool_calls"

    def fake_urlopen(req, timeout=60):
        return FakeHTTPResponse({
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
            ]
        })

    provider = AnthropicProvider({
        "provider": "anthropic",
        "api_key": "secret",
        "model": "claude-sonnet-4-6",
    })
    with mock.patch("providers.anthropic.request.urlopen", side_effect=fake_urlopen):
        response = provider.chat(
            messages=[{"role": "user", "content": "Read the file"}],
            system="System",
        )

    assert response.content == "Let me check."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "toolu_123"
    assert response.tool_calls[0].function.name == "read_file"
    assert json.loads(response.tool_calls[0].function.arguments) == {"path": "README.md"}
    ok(name)


def test_chatgpt_oauth_provider_registry() -> None:
    name = "chatgpt_oauth provider is registered and instantiates without credentials"
    from providers import PROVIDER_CLASSES, create_provider
    from providers.chatgpt_oauth import (
        ChatGPTOAuthProvider,
        CHATGPT_OAUTH_MODELS,
        _decode_jwt_exp,
        _token_is_fresh,
    )

    # Registry presence
    assert "chatgpt_oauth" in PROVIDER_CLASSES
    assert PROVIDER_CLASSES["chatgpt_oauth"] is ChatGPTOAuthProvider

    # Instantiation
    provider = create_provider({"provider": "chatgpt_oauth", "model": "gpt-5.4"}, "companion")
    assert isinstance(provider, ChatGPTOAuthProvider)

    # Static model list
    models = provider.list_models()
    assert "gpt-5.4" in models
    assert "gpt-5.3-codex" in models
    assert "gpt-5.1-codex-max" in models
    assert len(models) >= 6

    # Shared catalog drives the exported constant and list_models()
    assert set(CHATGPT_OAUTH_MODELS) == set(get_chatgpt_oauth_model_ids())
    assert set(CHATGPT_OAUTH_MODELS) == set(models)
    capabilities = get_chatgpt_oauth_model_capabilities("gpt-5.4")
    assert capabilities["registry_match"] is True
    assert int(capabilities["context_window"] or 0) >= 1_000_000
    assert normalize_chatgpt_oauth_model_name("gpt-5.3") == "gpt-5.3-codex"

    # JWT exp decoder — valid payload
    import base64, json, time
    payload = {"sub": "user-123", "exp": int(time.time()) + 3600}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    fake_jwt = f"header.{b64}.sig"
    assert _decode_jwt_exp(fake_jwt) == payload["exp"]

    # JWT exp decoder — malformed token
    assert _decode_jwt_exp("not.a.jwt") is None
    assert _decode_jwt_exp("") is None

    # Fresh token detection
    assert _token_is_fresh(fake_jwt) is True

    # Expired token detection
    expired_payload = {"sub": "user-123", "exp": int(time.time()) - 60}
    b64_exp = base64.urlsafe_b64encode(json.dumps(expired_payload).encode()).rstrip(b"=").decode()
    expired_jwt = f"header.{b64_exp}.sig"
    assert _token_is_fresh(expired_jwt) is False

    # health_check raises auth error when no keyring entries (keyring stub returns None)
    from providers.base import BrainProviderAuthError
    try:
        provider.health_check()
        raise AssertionError("expected BrainProviderAuthError")
    except BrainProviderAuthError as exc:
        assert "no credentials" in str(exc).lower() or "connect" in str(exc).lower()

    # brain.py context window returns large value for chatgpt_oauth
    ctx = brain.get_context_window(
        {"brain": {"provider": "chatgpt_oauth", "model": "gpt-5.4", "context_window": "auto", "layers": {}}},
        "companion",
    )
    assert ctx >= 1_000_000

    ok(name)


def test_chatgpt_oauth_reads_keytar_compatible_store() -> None:
    name = "chatgpt_oauth reads tokens through shared keyring helper"
    from providers.chatgpt_oauth import _read_token_store

    values = {
        "chatgpt_oauth_access": "access-token",
        "chatgpt_oauth_refresh": "refresh-token",
        "chatgpt_oauth_expires": "1234567890",
        "chatgpt_oauth_account_id": "acct-123",
    }

    with mock.patch(
        "providers.chatgpt_oauth.read_keyring_secret",
        side_effect=lambda accounts, service_names=None: values.get(accounts[0], ""),
    ):
        store = _read_token_store()

    assert store == {
        "access": "access-token",
        "refresh": "refresh-token",
        "expires": "1234567890",
        "account_id": "acct-123",
    }

    ok(name)


def test_chatgpt_oauth_truncates_oversized_instructions() -> None:
    name = "chatgpt_oauth truncates oversized instructions before request"
    from providers.chatgpt_oauth import ChatGPTOAuthProvider, _MAX_INSTRUCTIONS_CHARS

    captured: dict[str, object] = {}

    class FakeResponse:
        def __iter__(self):
            return iter([
                b'data: {"type":"response.output_text.delta","delta":"OK"}\n',
                b"data: [DONE]\n",
            ])

        def close(self) -> None:
            return None

    def fake_urlopen(req, timeout=120):
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    long_system = "A" * (_MAX_INSTRUCTIONS_CHARS + 5000)
    provider = ChatGPTOAuthProvider({"provider": "chatgpt_oauth", "model": "gpt-5.4"}, "assistant")

    with mock.patch("providers.chatgpt_oauth._read_token_store", return_value={"access": "header.eyJleHAiOjQ3NDQ0NDQ0NDR9.sig", "refresh": "", "expires": "", "account_id": ""}):
        with mock.patch("providers.chatgpt_oauth._token_is_fresh", return_value=True):
            with mock.patch("providers.chatgpt_oauth.urllib.request.urlopen", side_effect=fake_urlopen):
                with mock.patch(
                    "providers.chatgpt_oauth.ChatGPTOAuthProvider._run_ws_bridge",
                    side_effect=BrainProviderConnectionError("bridge unavailable"),
                ):
                    response = provider.chat(messages=[{"role": "user", "content": "hi"}], system=long_system, tools=None)

    assert response.content == "OK"
    body = captured["body"]
    assert isinstance(body, dict)
    assert len(body["instructions"]) <= _MAX_INSTRUCTIONS_CHARS
    assert "truncated extra instruction text" in body["instructions"]
    assert body["input"][0]["role"] == "user"
    assert "temperature" not in body
    assert "max_output_tokens" not in body
    assert "tool_choice" not in body

    ok(name)


def test_chatgpt_oauth_sse_retry_removes_unsupported_parameter() -> None:
    name = "chatgpt_oauth SSE fallback retries without unsupported parameters"
    from providers.chatgpt_oauth import ChatGPTOAuthProvider

    requests: list[dict[str, object]] = []

    class FakeResponse:
        def __iter__(self):
            return iter([
                b'data: {"type":"response.output_text.delta","delta":"OK"}\n',
                b"data: [DONE]\n",
            ])

        def close(self) -> None:
            return None

    def fake_urlopen(req, timeout=120):
        body = json.loads(req.data.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                req.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=FakeBody('{"detail":"Unsupported parameter: tools"}'),
            )
        return FakeResponse()

    provider = ChatGPTOAuthProvider({"provider": "chatgpt_oauth", "model": "gpt-5.4"}, "assistant")

    with mock.patch("providers.chatgpt_oauth._read_token_store", return_value={"access": "header.eyJleHAiOjQ3NDQ0NDQ0NDR9.sig", "refresh": "", "expires": "", "account_id": ""}):
        with mock.patch("providers.chatgpt_oauth._token_is_fresh", return_value=True):
            with mock.patch("providers.chatgpt_oauth.urllib.request.urlopen", side_effect=fake_urlopen):
                with mock.patch(
                    "providers.chatgpt_oauth.ChatGPTOAuthProvider._run_ws_bridge",
                    side_effect=BrainProviderConnectionError("bridge unavailable"),
                ):
                    response = provider.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        system="Stay concise.",
                        tools=[{
                            "type": "function",
                            "function": {
                                "name": "demo_tool",
                                "description": "A demo tool.",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }],
                        temperature=0.9,
                        max_tokens=1024,
                    )

    assert response.content == "OK"
    assert len(requests) == 2
    assert "tools" in requests[0]
    assert "tools" not in requests[1]
    assert "temperature" not in requests[0]
    assert "tool_choice" not in requests[0]
    assert "max_output_tokens" not in requests[0]

    ok(name)


def test_chatgpt_oauth_disables_websocket_after_401() -> None:
    name = "chatgpt_oauth disables websocket after a 401 and reuses SSE fallback"
    from providers.chatgpt_oauth import ChatGPTOAuthProvider

    request_bodies: list[dict[str, object]] = []

    class FakeResponse:
        def __iter__(self):
            return iter([
                b'data: {"type":"response.output_text.delta","delta":"OK"}\n',
                b"data: [DONE]\n",
            ])

        def close(self) -> None:
            return None

    def fake_urlopen(req, timeout=120):
        request_bodies.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse()

    provider = ChatGPTOAuthProvider({"provider": "chatgpt_oauth", "model": "gpt-5.4"}, "assistant")

    with mock.patch("providers.chatgpt_oauth._read_token_store", return_value={"access": "header.eyJleHAiOjQ3NDQ0NDQ0NDR9.sig", "refresh": "", "expires": "", "account_id": ""}):
        with mock.patch("providers.chatgpt_oauth._token_is_fresh", return_value=True):
            with mock.patch("providers.chatgpt_oauth.urllib.request.urlopen", side_effect=fake_urlopen):
                with mock.patch(
                    "providers.chatgpt_oauth.ChatGPTOAuthProvider._run_ws_bridge",
                    side_effect=BrainProviderConnectionError("Unexpected server response: 401"),
                ) as ws_mock:
                    first = provider.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        system="Stay concise.",
                    )
                    second = provider.chat(
                        messages=[{"role": "user", "content": "hi again"}],
                        system="Stay concise.",
                    )

    assert first.content == "OK"
    assert second.content == "OK"
    assert ws_mock.call_count == 1
    assert provider._ws_disabled is True
    assert len(request_bodies) == 2
    ok(name)


def test_chatgpt_oauth_sse_replays_full_tool_history_without_previous_response_id() -> None:
    name = "chatgpt_oauth SSE fallback replays full tool history for tool follow-ups"
    from providers.chatgpt_oauth import ChatGPTOAuthProvider

    captured: dict[str, object] = {}

    class FakeResponse:
        def __iter__(self):
            return iter([
                b'data: {"type":"response.output_text.delta","delta":"OK"}\n',
                b"data: [DONE]\n",
            ])

        def close(self) -> None:
            return None

    def fake_urlopen(req, timeout=120):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    provider = ChatGPTOAuthProvider({"provider": "chatgpt_oauth", "model": "gpt-5.4"}, "assistant")
    provider._previous_response_id = "resp_123"
    provider._last_context_length = 2

    messages = [
        {"role": "user", "content": "Read the file."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "fc_demo_call",
                "type": "function",
                "function": {"name": "write_memory", "arguments": '{"entry":"hello","topic":"reminder"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "fc_demo_call", "content": "Saved memory: hello"},
    ]

    with mock.patch("providers.chatgpt_oauth._read_token_store", return_value={"access": "header.eyJleHAiOjQ3NDQ0NDQ0NDR9.sig", "refresh": "", "expires": "", "account_id": ""}):
        with mock.patch("providers.chatgpt_oauth._token_is_fresh", return_value=True):
            with mock.patch("providers.chatgpt_oauth.urllib.request.urlopen", side_effect=fake_urlopen):
                with mock.patch(
                    "providers.chatgpt_oauth.ChatGPTOAuthProvider._run_ws_bridge",
                    side_effect=BrainProviderConnectionError("bridge unavailable"),
                ):
                    response = provider.chat(
                        messages=messages,
                        system="Stay concise.",
                        tools=[{
                            "type": "function",
                            "function": {
                                "name": "write_memory",
                                "description": "Save a memory.",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }],
                    )

    assert response.content == "OK"
    body = captured["body"]
    assert isinstance(body, dict)
    assert "previous_response_id" not in body
    assert body["input"][0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "Read the file."}],
    }
    assert body["input"][1]["type"] == "function_call"
    assert body["input"][1]["call_id"] == "fc_demo_call"
    assert body["input"][2] == {
        "type": "function_call_output",
        "call_id": "fc_demo_call",
        "output": "Saved memory: hello",
    }

    ok(name)


def test_chatgpt_oauth_compacts_local_runtime_sections() -> None:
    name = "chatgpt_oauth compacts local runtime sections from instructions"
    from providers.chatgpt_oauth import _compact_instructions

    source = """# Assistant Identity

You are the assistant execution layer.

## Runtime Skills

Very long local skills section.

=== Long-term Memories ===

Very long memories section.

## Vault

Very long vault section.
"""

    compact = _compact_instructions(source)
    assert "# Assistant Identity" in compact
    assert "assistant execution layer" in compact.lower()
    assert "## Runtime Skills" not in compact
    assert "Long-term Memories" not in compact
    assert "## Vault" not in compact
    assert "Use the provided tools" in compact

    ok(name)


def test_anthropic_brain_capabilities_resolver() -> None:
    name = "brain.resolve_anthropic_model_capabilities returns metadata for anthropic"
    with mock.patch(
        "providers.base.keyring.get_password",
        side_effect=lambda service, account: "secret" if account == "anthropic_api_key" else None,
    ):
        cfg = brain.get_layer_brain_config(_build_config("anthropic", "claude-opus-4-6"), "companion")
    caps = brain.resolve_anthropic_model_capabilities(cfg)
    assert caps is not None
    assert caps["family"] == "anthropic_claude_reasoning"
    assert caps["preferred_api"] == "messages"
    assert caps["registry_match"] is True

    non_anthropic = brain.resolve_anthropic_model_capabilities({"provider": "openai", "model": "gpt-5.4"})
    assert non_anthropic is None
    ok(name)


def test_qwen_provider_registry_and_capabilities() -> None:
    name = "qwen providers are registered and expose model capabilities"
    from providers import PROVIDER_CLASSES, create_provider
    from providers.qwen import QwenProvider
    from qwen_models import get_qwen_model_capabilities, get_qwen_model_ids
    from model_family import resolve_model_family

    assert PROVIDER_CLASSES["qwen"] is QwenProvider
    assert PROVIDER_CLASSES["qwen_cloud"] is QwenProvider
    assert isinstance(create_provider({"provider": "qwen", "model": "qwen3:8b"}), QwenProvider)
    assert isinstance(create_provider({"provider": "qwen_cloud", "model": "qwen3-max"}), QwenProvider)
    assert "qwen3:8b" in get_qwen_model_ids("local")
    assert "qwen3-max" in get_qwen_model_ids("cloud")
    caps = get_qwen_model_capabilities("qwen3-max")
    assert caps["family"] == "qwen"
    assert caps["supports_thinking"] is True
    assert caps["supports_tools"] is True
    assert caps["path"] == "cloud"
    assert brain.resolve_qwen_model_capabilities({"provider": "qwen_cloud", "model": "qwen3-max"})["registry_match"] is True
    assert resolve_model_family({"provider": "qwen", "model": "qwen3:8b"}) == "qwen"
    ok(name)


def test_qwen_local_request_shape_and_reasoning_strip() -> None:
    name = "qwen local uses Ollama native chat without default raw-content tool recovery"
    from providers.qwen import QwenProvider
    from model_family import get_family_adapter

    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=300):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeHTTPResponse({
            "message": {
                "role": "assistant",
                "reasoning_content": "private thought",
                "content": "Visible response.",
            }
        })

    provider = QwenProvider({"provider": "qwen", "model": "qwen3:8b", "thinking_enabled": True})
    with mock.patch("providers.qwen.urllib_request.urlopen", side_effect=fake_urlopen):
        response = provider.chat(messages=[{"role": "user", "content": "Read it"}], tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}])

    assert captured["url"].endswith("/api/chat")
    body = captured["body"]
    assert body["model"] == "qwen3:8b"
    assert body["messages"][-1]["content"].endswith("/think")
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert response.tool_calls == []
    adapter = get_family_adapter({"provider": "qwen", "model": "qwen3:8b"})
    assert adapter.strip_final_reply(response.content) == "Visible response."
    ok(name)


def test_qwen_cloud_dashscope_key_and_thinking_body() -> None:
    name = "qwen cloud uses DashScope OpenAI-compatible endpoint and enable_thinking"
    _reset_openai_stub()

    with mock.patch("providers.openai.OpenAI", RecordingOpenAI):
        with mock.patch(
            "providers.base.keyring.get_password",
            side_effect=lambda service, account: "sk-qwen" if service == "OpenCompanion" and account == "qwen_api_key" else None,
        ):
            client = brain.create_client(_build_config("qwen_cloud", "qwen3-max"), layer_name="companion")
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": "Hi"}],
                extra_body={"enable_thinking": True},
            )

    instance = RecordingOpenAI.instances[-1]
    assert instance.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert instance.api_key == "sk-qwen"
    call = instance.calls[-1]
    assert call["model"] == "qwen3-max"
    assert call["extra_body"]["enable_thinking"] is True
    assert response.choices[0].message.content == "OK"
    ok(name)


def run_all() -> bool:
    tests = [
        test_keyring_lookup_behavior,
        test_chatgpt_oauth_provider_registry,
        test_openai_provider_request_and_errors,
        test_openai_responses_path_and_tool_normalization,
        test_openai_responses_path_serializes_tool_history,
        test_openai_unknown_model_falls_back_to_chat_completions,
        test_openai_model_registry_and_family_defaults,
        test_provider_auto_context_window_uses_registry_metadata,
        test_chatgpt_oauth_build_input_uses_openclaw_message_shape,
        test_chatgpt_oauth_plans_incremental_tool_result_turns,
        test_chatgpt_oauth_sanitizes_bearer_tokens,
        test_chatgpt_oauth_has_public_client_id_fallback,
        test_gemini_auth_header_registry_and_tool_calls,
        test_gemini_3_uses_thinking_level,
        test_gemma_family_does_not_infer_raw_content_by_default,
        test_tool_call_argument_dicts_normalize_to_json_strings,
        test_ollama_gemma_native_request_matches_doc_shape,
        test_ollama_gemma_native_request_matches_docs_transformers_lifecycle,
        test_ollama_gemma_native_retries_with_smaller_num_ctx_on_memory_error,
        test_ollama_generic_health_and_cloud_auth_hint,
        test_custom_provider_uses_supplied_base_url,
        test_custom_provider_health_degrades_when_models_missing,
        test_openrouter_fallback_and_tool_normalization,
        test_anthropic_request_construction_and_stream_parsing,
        test_anthropic_user_friendly_errors,
        test_anthropic_model_registry_resolution,
        test_anthropic_model_capabilities_metadata,
        test_anthropic_family_resolution_through_model_family,
        test_anthropic_thinking_budget_wiring,
        test_anthropic_tool_call_normalization,
        test_anthropic_brain_capabilities_resolver,
        test_qwen_provider_registry_and_capabilities,
        test_qwen_local_request_shape_and_reasoning_strip,
        test_qwen_cloud_dashscope_key_and_thinking_body,
        test_chatgpt_oauth_reads_keytar_compatible_store,
        test_chatgpt_oauth_truncates_oversized_instructions,
        test_chatgpt_oauth_sse_retry_removes_unsupported_parameter,
        test_chatgpt_oauth_disables_websocket_after_401,
        test_chatgpt_oauth_sse_replays_full_tool_history_without_previous_response_id,
        test_chatgpt_oauth_compacts_local_runtime_sections,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            fail(test.__name__, str(exc))
    return summary()


def main() -> int:
    return 0 if run_all() else 1


if __name__ == "__main__":
    sys.exit(main())
