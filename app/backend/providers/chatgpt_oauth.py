"""ChatGPT Plus OAuth provider for OpenCompanion.

Uses the same PKCE OAuth flow as Codex CLI / OpenClaw to authenticate as a
ChatGPT Plus/Pro subscriber and call the Codex responses endpoint without
burning separate API credits.

Critical: access token JWT exp is always checked before attempting a refresh.
Refreshing unconditionally (the OpenClaw bug) burns single-use refresh tokens.

Keyring keys:
    chatgpt_oauth_access    — JWT access token
    chatgpt_oauth_refresh   — refresh token
    chatgpt_oauth_expires   — expiry timestamp (ms, as string)
    chatgpt_oauth_account_id — OpenAI account UUID
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Any

from chatgpt_oauth_models import (
    get_chatgpt_oauth_model_ids,
    get_default_chatgpt_oauth_model_id,
    normalize_chatgpt_oauth_model_name,
)
from debug_log import debug_tool_schema
from .base import (
    BrainMessage,
    BrainProvider,
    BrainProviderAuthError,
    BrainProviderConnectionError,
    BrainProviderError,
    BrainProviderRateLimitError,
    normalize_tool_calls,
    normalize_message_content,
    read_keyring_secret,
)

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_WS_URL = "wss://api.openai.com/v1/responses"
TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Keyring service + account names (must match main.js)
_KR_SERVICE = "OpenCompanion"
_KR_ACCESS = "chatgpt_oauth_access"
_KR_REFRESH = "chatgpt_oauth_refresh"
_KR_EXPIRES = "chatgpt_oauth_expires"
_KR_ACCOUNT = "chatgpt_oauth_account_id"

# Refresh 5 minutes before actual expiry
_REFRESH_BUFFER_SECONDS = 5 * 60

CHATGPT_OAUTH_MODELS = tuple(get_chatgpt_oauth_model_ids())
_MAX_INSTRUCTIONS_CHARS = 6_000
_WS_BRIDGE_PATH = Path(__file__).resolve().parents[1] / "chatgpt_oauth_ws_bridge.js"
_REPO_DOTENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _coerce_message_text_content(text: str) -> str | list[dict[str, str]]:
    content = str(text or "")
    return content if content else ""


def _sanitize_bearer_token(token: Any) -> str:
    """Normalize bearer tokens loaded from keyring or refresh responses.

    The websocket client rejects any header value containing ASCII control
    characters. Access tokens should be plain JWT strings, so we strip all
    control bytes and surrounding whitespace before using them in headers.
    """
    raw = str(token or "")
    if not raw:
        return ""
    sanitized = "".join(ch for ch in raw if ch >= " " and ch != "\x7f").strip()
    if raw != sanitized:
        logger.warning(
            "chatgpt_oauth: sanitized bearer token before transport (raw_len=%s clean_len=%s)",
            len(raw),
            len(sanitized),
        )
    return sanitized


def _read_repo_env_value(name: str) -> str:
    try:
        raw = _REPO_DOTENV_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""
    prefix = f"{name}="
    for raw_line in raw.splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        return value.strip()
    return ""


def _get_chatgpt_oauth_client_id() -> str:
    return (
        str(os.environ.get("CHATGPT_OAUTH_CLIENT_ID") or "").strip()
        or _read_repo_env_value("CHATGPT_OAUTH_CLIENT_ID")
        or DEFAULT_CLIENT_ID
    )


def _require_chatgpt_oauth_client_id() -> str:
    client_id = _get_chatgpt_oauth_client_id()
    if client_id:
        return client_id
    raise BrainProviderAuthError(
        "ChatGPT OAuth client ID is not configured. "
        "Set CHATGPT_OAUTH_CLIENT_ID in your environment or .env and restart OpenCompanion."
    )


def _read_token_store() -> dict:
    """Read all four keyring keys using the shared keytar-aware helper."""
    return {
        "access": read_keyring_secret([_KR_ACCESS], service_names=("open-companion", _KR_SERVICE)),
        "refresh": read_keyring_secret([_KR_REFRESH], service_names=("open-companion", _KR_SERVICE)),
        "expires": read_keyring_secret([_KR_EXPIRES], service_names=("open-companion", _KR_SERVICE)),
        "account_id": read_keyring_secret([_KR_ACCOUNT], service_names=("open-companion", _KR_SERVICE)),
    }


def _write_token_store(access: str, refresh: str, expires_ms: int, account_id: str) -> None:
    try:
        import keyring as kr
    except Exception:
        return
    try:
        kr.set_password(_KR_SERVICE, _KR_ACCESS, access)
        kr.set_password(_KR_SERVICE, _KR_REFRESH, refresh)
        kr.set_password(_KR_SERVICE, _KR_EXPIRES, str(expires_ms))
        kr.set_password(_KR_SERVICE, _KR_ACCOUNT, account_id)
    except Exception as exc:
        logger.warning("chatgpt_oauth: failed to write token store: %s", exc)


def _decode_jwt_exp(token: str) -> int | None:
    """Return the `exp` claim (seconds since epoch) from a JWT, or None."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


def _token_is_fresh(access_token: str) -> bool:
    """Return True if the JWT is valid and not expiring within REFRESH_BUFFER_SECONDS."""
    if not access_token:
        return False
    exp = _decode_jwt_exp(access_token)
    if exp is None:
        # Can't decode — fall back to stored expires field
        return False
    return (exp - _REFRESH_BUFFER_SECONDS) > time.time()


def _refresh_tokens(refresh_token: str) -> dict:
    """POST to token endpoint with refresh_token grant.  Returns new token dict."""
    client_id = _require_chatgpt_oauth_client_id()
    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        _raise_from_http_error(exc.code, body)
    except urllib.error.URLError as exc:
        raise BrainProviderConnectionError(
            f"ChatGPT OAuth: token refresh connection failed: {exc.reason}"
        ) from exc

    access = _sanitize_bearer_token(data.get("access_token") or "")
    refresh = _sanitize_bearer_token(data.get("refresh_token") or refresh_token)
    expires_in = int(data.get("expires_in") or 3600)
    expires_ms = int((time.time() + expires_in) * 1000)

    # Extract account_id from id_token if present
    account_id = ""
    id_token = str(data.get("id_token") or "").strip()
    if id_token:
        exp = _decode_jwt_exp(id_token)
        # Decode payload for sub claim
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                account_id = str(payload.get("sub") or "").strip()
        except Exception:
            pass

    return {
        "access": access,
        "refresh": refresh,
        "expires_ms": expires_ms,
        "account_id": account_id,
    }


def _raise_from_http_error(status: int, body: str) -> None:
    lowered = body.lower()
    if status == 429 or "rate limit" in lowered or "too many" in lowered:
        raise BrainProviderRateLimitError(
            "ChatGPT usage limit reached. Try again later."
        )
    if status in {401, 403}:
        if "unsupported_country" in lowered or "region" in lowered or "territory" in lowered:
            raise BrainProviderAuthError(
                "ChatGPT OAuth is not available in your region."
            )
        raise BrainProviderAuthError(
            f"ChatGPT OAuth: authentication failed (HTTP {status}). "
            "Try disconnecting and reconnecting your account."
        )
    if 400 <= status < 500:
        raise BrainProviderError(f"ChatGPT OAuth: request rejected (HTTP {status}): {body[:200]}")
    raise BrainProviderConnectionError(
        f"ChatGPT OAuth: server error (HTTP {status}): {body[:200]}"
    )


def _redact_for_log(payload: dict[str, Any]) -> str:
    try:
        clone = dict(payload or {})
        if clone.get("input"):
            clone["input"] = f"<{len(clone['input'])} input items>"
        if clone.get("tools"):
            clone["tools"] = f"<{len(clone['tools'])} tools>"
        return json.dumps(clone, ensure_ascii=False)
    except Exception:
        return "<unavailable>"


def _extract_unsupported_parameter(body: str) -> str | None:
    text = str(body or "")
    if not text:
        return None
    try:
        parsed = json.loads(text)
        detail = parsed.get("detail")
        if isinstance(detail, str):
            match = re.search(r"Unsupported parameter:\s*([A-Za-z0-9_.-]+)", detail)
            if match:
                return match.group(1)
    except Exception:
        pass
    match = re.search(r"Unsupported parameter:\s*([A-Za-z0-9_.-]+)", text)
    return match.group(1) if match else None


def _truncate_instructions(text: str, limit: int = _MAX_INSTRUCTIONS_CHARS) -> str:
    clean = str(text or "")
    if len(clean) <= limit:
        return clean
    suffix = "\n\n[System note: OpenCompanion truncated extra instruction text for ChatGPT OAuth transport compatibility.]"
    keep = max(0, limit - len(suffix))
    truncated = clean[:keep].rstrip()
    logger.warning(
        "chatgpt_oauth: instructions too large (%s chars), truncating to %s chars",
        len(clean),
        limit,
    )
    return truncated + suffix


def _compact_instructions(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""

    compact = clean
    for marker in (
        "## Runtime Skills",
        "=== Long-term Memories ===",
        "=== Relevant Memories ===",
        "## Vault",
    ):
        index = compact.find(marker)
        if index != -1:
            compact = compact[:index].rstrip()

    additions = [
        "Use the provided tools when they are available and necessary.",
        "Keep replies in plain prose with no markdown or bullet points.",
    ]
    for addition in additions:
        if addition.lower() not in compact.lower():
            compact = f"{compact}\n\n{addition}".strip()

    return _truncate_instructions(compact)


def _build_openclaw_text_input(text: str) -> list[dict[str, str]]:
    content = str(text or "").strip()
    return [{"type": "input_text", "text": content}] if content else []


def _parse_sse_stream(response_iter, stream_handler=None) -> BrainMessage:
    """Parse OpenAI Responses API SSE stream from the Codex endpoint.

    Events follow the Responses API SSE format:
      data: {"type": "response.output_text.delta", "delta": "...", ...}
      data: {"type": "response.completed", ...}
    """
    text_parts: list[str] = []
    raw_tool_calls: dict[int, dict[str, Any]] = {}
    response_id: str | None = None

    for raw_line in response_iter:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)

        line = line.rstrip("\r\n")
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break

        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        event_type = str(event.get("type") or "").strip()

        if event_type == "response.output_text.delta":
            delta = str(event.get("delta") or "")
            if delta:
                text_parts.append(delta)
                if stream_handler is not None:
                    try:
                        stream_handler(delta)
                    except Exception:
                        pass

        elif event_type in {"response.function_call_arguments.delta"}:
            index = int(event.get("output_index") or 0)
            delta = str(event.get("delta") or "")
            if index not in raw_tool_calls:
                raw_tool_calls[index] = {
                    "id": str(event.get("item_id") or f"tool_call_{index}"),
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            raw_tool_calls[index]["function"]["arguments"] += delta

        elif event_type == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                index = int(event.get("output_index") or 0)
                raw_tool_calls.setdefault(index, {
                    "id": str(item.get("id") or f"tool_call_{index}"),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""), "arguments": ""},
                })

        elif event_type == "error":
            msg = str(event.get("message") or event.get("error") or "unknown error")
            status = event.get("status")
            try:
                status_code = int(status or 0)
            except Exception:
                status_code = 0
            if not status_code:
                try:
                    status_code = int(event.get("code") or 0)
                except Exception:
                    status_code = 0
            _raise_from_http_error(status_code or 500, msg)
        elif event_type == "response.completed":
            response = event.get("response") or {}
            response_id = str(response.get("id") or "").strip() or response_id

    content = "".join(text_parts).strip()
    tool_calls_list = [
        raw_tool_calls[i]
        for i in sorted(raw_tool_calls)
        if raw_tool_calls[i].get("function", {}).get("name")
    ]
    return BrainMessage(
        content=content,
        tool_calls=normalize_tool_calls(tool_calls_list),
        role="assistant",
        model=None,
        raw={"response_id": response_id} if response_id else None,
    )


class ChatGPTOAuthProvider(BrainProvider):
    """Brain provider that uses ChatGPT Plus/Pro OAuth tokens."""

    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        super().__init__(settings=settings, layer_name=layer_name)
        self._ws_session_id = str(uuid.uuid4())
        self._previous_response_id: str | None = None
        self._last_context_length = 0
        self._ws_disabled = False
        self._ws_disable_reason = ""

    def _get_access_token(self) -> str:
        """Return a valid access token, refreshing if needed.

        Critical: always check JWT exp before refreshing (never refresh blindly).
        """
        store = _read_token_store()
        access = _sanitize_bearer_token(store.get("access", ""))
        refresh = _sanitize_bearer_token(store.get("refresh", ""))

        if not access and not refresh:
            raise BrainProviderAuthError(
                "ChatGPT OAuth: no credentials found. "
                "Connect your account in Settings -> Model -> ChatGPT Plus."
            )

        # Check JWT exp directly — most reliable
        if _token_is_fresh(access):
            return access

        # Token missing or expiring — need to refresh
        if not refresh:
            raise BrainProviderAuthError(
                "ChatGPT OAuth: access token expired and no refresh token available. "
                "Please reconnect your account."
            )

        logger.info("chatgpt_oauth: access token expiring, refreshing...")
        try:
            new_tokens = _refresh_tokens(refresh)
        except (BrainProviderAuthError, BrainProviderRateLimitError):
            raise
        except Exception as exc:
            raise BrainProviderConnectionError(
                f"ChatGPT OAuth: token refresh failed: {exc}"
            ) from exc

        _write_token_store(
            access=new_tokens["access"],
            refresh=new_tokens["refresh"],
            expires_ms=new_tokens["expires_ms"],
            account_id=new_tokens.get("account_id") or store.get("account_id", ""),
        )
        return new_tokens["access"]

    def _build_input(self, messages: list[dict], system: str = "", include_system_item: bool = True) -> list[dict]:
        """Convert messages into the OpenClaw-style Responses/WebSocket input shape."""
        items: list[dict] = []
        compact_system = _compact_instructions(system)
        if include_system_item and compact_system:
            items.append({
                "role": "developer",
                "content": compact_system,
            })
        for msg in messages or []:
            role = str(msg.get("role") or "").strip().lower()
            content = normalize_message_content(msg.get("content"))
            tool_calls = normalize_tool_calls(msg.get("tool_calls"))
            if role in {"user", "assistant"} and content:
                if role == "user":
                    items.append({
                        "role": "user",
                        "content": _build_openclaw_text_input(content),
                    })
                else:
                    items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": content,
                            "annotations": [],
                        }],
                        "status": "completed",
                        "id": f"msg_{len(items)}",
                    })
            if role == "assistant":
                for tool_call in tool_calls:
                    if not tool_call.function.name:
                        continue
                    items.append({
                        "type": "function_call",
                        "id": tool_call.id,
                        "call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments or "{}",
                    })
                continue
            elif role == "tool":
                tool_call_id = str(msg.get("tool_call_id") or "").strip()
                if tool_call_id and content:
                    items.append({
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": content,
                    })
        return items

    def _plan_turn_input(
        self,
        messages: list[dict],
        system: str = "",
        include_system_item: bool = True,
    ) -> dict[str, Any]:
        if self._previous_response_id and self._last_context_length > 0:
            new_messages = list(messages or [])[self._last_context_length:]
            tool_results = [
                message
                for message in new_messages
                if str(message.get("role") or "").strip().lower() == "tool"
            ]
            if tool_results:
                return {
                    "input": self._build_input(tool_results, include_system_item=False),
                    "previous_response_id": self._previous_response_id,
                }
        return {
            "input": self._build_input(messages, system=system, include_system_item=include_system_item),
        }

    def _resolve_ws_bridge_command(self) -> list[str]:
        command = str(os.environ.get("OPEN_COMPANION_NODE_PATH") or "").strip() or "node"
        return [command, str(_WS_BRIDGE_PATH)]

    def _disable_websocket_for_session(self, reason: str) -> None:
        if self._ws_disabled:
            return
        self._ws_disabled = True
        self._ws_disable_reason = str(reason or "").strip()
        logger.warning(
            "chatgpt_oauth: disabling websocket transport for the remainder of this session; using SSE fallback instead: %s",
            self._ws_disable_reason or "unknown reason",
        )

    def _run_ws_bridge(
        self,
        payload: dict[str, Any],
        stream_handler=None,
    ) -> BrainMessage:
        env = dict(os.environ)
        node_mode = str(env.get("OPEN_COMPANION_NODE_MODE") or "").strip().lower()
        command = self._resolve_ws_bridge_command()
        if node_mode == "electron":
            env["ELECTRON_RUN_AS_NODE"] = "1"

        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(_WS_BRIDGE_PATH.parent),
            env=env,
        )

        try:
            if proc.stdin is None or proc.stdout is None:
                raise BrainProviderConnectionError("ChatGPT OAuth websocket bridge failed to start.")
            proc.stdin.write(json.dumps(payload, ensure_ascii=False))
            proc.stdin.close()
            message = _parse_sse_stream(proc.stdout, stream_handler=stream_handler)
            stderr_text = proc.stderr.read() if proc.stderr is not None else ""
            return_code = proc.wait(timeout=10)
            if return_code != 0 and not message.content and not message.tool_calls:
                raise BrainProviderConnectionError(
                    f"ChatGPT OAuth websocket bridge exited with code {return_code}: {stderr_text[:300]}"
                )
            if stderr_text.strip():
                logger.warning("chatgpt_oauth: websocket bridge stderr: %s", stderr_text[:500])
            return message
        except FileNotFoundError as exc:
            raise BrainProviderConnectionError(
                f"ChatGPT OAuth websocket bridge runtime not found: {command[0]}"
            ) from exc
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    def _build_tools(self, tools: list[dict] | None) -> list[dict]:
        result: list[dict] = []
        for tool in tools or []:
            fn = tool.get("function") or {}
            name = str(fn.get("name") or tool.get("name") or "").strip()
            if not name:
                continue
            result.append({
                "type": "function",
                "name": name,
                "description": str(fn.get("description") or "").strip(),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return result

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        stream: bool = False,
        tools: list[dict] | None = None,
        stream_handler=None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
    ) -> BrainMessage:
        access_token = self._get_access_token()
        requested_model = str(model or self.model or get_default_chatgpt_oauth_model_id()).strip()
        resolved_model = normalize_chatgpt_oauth_model_name(requested_model)
        if requested_model and requested_model != resolved_model:
            logger.warning(
                "chatgpt_oauth: normalized unsupported/stale model %r -> %r",
                requested_model,
                resolved_model,
            )
            self.model = resolved_model
        ws_turn_input = self._plan_turn_input(messages, system=system, include_system_item=True)
        sse_turn_input = {
            "input": self._build_input(messages, system="", include_system_item=False),
        }
        compact_system = _compact_instructions(system) or "You are a helpful assistant."

        body: dict[str, Any] = {
            "model": resolved_model,
            "input": ws_turn_input["input"],
            "store": False,
            "stream": True,
        }
        built_tools = self._build_tools(tools)
        if built_tools:
            body["tools"] = built_tools
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens:
            body["max_output_tokens"] = max_tokens

        if ws_turn_input.get("previous_response_id"):
            body["previous_response_id"] = ws_turn_input["previous_response_id"]

        if not self._ws_disabled:
            try:
                debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": "chatgpt_oauth", "api": "websocket", "body": body})
                ws_message = self._run_ws_bridge(
                    {
                        **body,
                        "access_token": access_token,
                        "session_id": self._ws_session_id,
                        "request_id": str(uuid.uuid4()),
                        "url": OPENAI_WS_URL,
                    },
                    stream_handler=stream_handler if stream else None,
                )
                response_id = (
                    (ws_message.raw or {}).get("response_id")
                    if isinstance(ws_message.raw, dict)
                    else None
                )
                if response_id:
                    self._previous_response_id = str(response_id)
                self._last_context_length = len(messages or [])
                return ws_message
            except BrainProviderConnectionError as exc:
                message = str(exc)
                if "401" in message or "authentication failed" in message.lower():
                    self._disable_websocket_for_session(message)
                logger.warning(
                    "chatgpt_oauth: websocket transport unavailable, falling back to SSE endpoint: %s",
                    exc,
                )

        sse_body: dict[str, Any] = {
            "model": resolved_model,
            "input": sse_turn_input["input"],
            "store": False,
            "stream": True,
            "instructions": compact_system,
        }
        if built_tools:
            sse_body["tools"] = built_tools

        resp = None
        body_text = ""
        for _attempt in range(4):
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": "chatgpt_oauth", "api": "sse", "body": sse_body})
            payload = json.dumps(sse_body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                CODEX_BASE_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Origin": "https://chatgpt.com",
                    "Referer": "https://chatgpt.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) OpenCompanion/1.0 Chrome/136.0.0.0 Safari/537.36",
                },
                method="POST",
            )
            try:
                resp = urllib.request.urlopen(req, timeout=120)
                break
            except urllib.error.HTTPError as exc:
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                unsupported = _extract_unsupported_parameter(body_text)
                if unsupported and unsupported in sse_body:
                    logger.warning(
                        "chatgpt_oauth: SSE fallback rejected parameter '%s'; retrying without it",
                        unsupported,
                    )
                    sse_body.pop(unsupported, None)
                    continue
                if (
                    "not supported when using Codex with a ChatGPT account" in body_text
                    and resolved_model != get_default_chatgpt_oauth_model_id()
                ):
                    fallback_model = get_default_chatgpt_oauth_model_id()
                    logger.warning(
                        "chatgpt_oauth: model %r rejected for ChatGPT account Codex transport; retrying with %r",
                        resolved_model,
                        fallback_model,
                    )
                    resolved_model = fallback_model
                    self.model = fallback_model
                    sse_body["model"] = fallback_model
                    continue
                logger.error(
                    "chatgpt_oauth: request rejected status=%s payload=%s response=%s",
                    exc.code,
                    _redact_for_log(sse_body),
                    body_text[:500],
                )
                _raise_from_http_error(exc.code, body_text)
            except urllib.error.URLError as exc:
                raise BrainProviderConnectionError(
                    f"ChatGPT OAuth: connection failed: {exc.reason}"
                ) from exc

        if resp is None:
            logger.error(
                "chatgpt_oauth: request rejected status=%s payload=%s response=%s",
                400,
                _redact_for_log(sse_body),
                body_text[:500],
            )
            _raise_from_http_error(400, body_text or "ChatGPT OAuth: no SSE response received.")

        try:
            message = _parse_sse_stream(resp, stream_handler=stream_handler if stream else None)
            response_id = (
                (message.raw or {}).get("response_id")
                if isinstance(message.raw, dict)
                else None
            )
            if response_id:
                self._previous_response_id = str(response_id)
            self._last_context_length = len(messages or [])
            return message
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def list_models(self) -> list[str]:
        return get_chatgpt_oauth_model_ids()

    def health_check(self) -> bool:
        store = _read_token_store()
        access = store.get("access", "")
        refresh = store.get("refresh", "")
        if not access and not refresh:
            raise BrainProviderAuthError(
                "ChatGPT OAuth: no credentials stored. Connect your account first."
            )
        # Validate token is readable without actually refreshing if still fresh
        if _token_is_fresh(access):
            return True
        if refresh:
            return True
        raise BrainProviderAuthError(
            "ChatGPT OAuth: access token expired and no refresh token. Reconnect account."
        )
