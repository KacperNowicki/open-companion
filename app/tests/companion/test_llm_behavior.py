#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import sys
import uuid
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from tests.companion.conftest import Companion, ROOT, SuiteSkip, _ollama_models, companion_session

BACKEND_ROOT = ROOT / "app" / "backend"
TOOLS_ROOT = BACKEND_ROOT / "tools"
for candidate in (BACKEND_ROOT, TOOLS_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from builtin.sandbox import run_terminal  # noqa: E402
from model_family import get_family_adapter  # noqa: E402

WORKFLOW_SOURCE_REL = "Projects/behavior-source.txt"
WORKFLOW_OUTPUT_REL = "Projects/behavior-report.txt"
SUCCESS_MARKERS = ("success", "succeeded", "completed successfully", "done", "added it", "wrote it")
FAILURE_MARKERS = ("error", "failed", "not found", "did not", "didn't", "could not", "couldn't")
OPENAI_ASSISTANT_MODEL = str(os.environ.get("OPEN_COMPANION_ASSISTANT_OPENAI_MODEL") or "gpt-5.4-mini").strip() or "gpt-5.4-mini"
ANTHROPIC_ASSISTANT_MODEL = str(os.environ.get("OPEN_COMPANION_ASSISTANT_ANTHROPIC_MODEL") or "claude-sonnet-4-5-20250929").strip() or "claude-sonnet-4-5-20250929"
CHATGPT_OAUTH_ASSISTANT_MODEL = str(os.environ.get("OPEN_COMPANION_ASSISTANT_CHATGPT_OAUTH_MODEL") or "gpt-5.4").strip() or "gpt-5.4"

# Selected at import time by run.py via --provider flag (default: openai).
_ASSISTANT_PROVIDER: str = os.environ.get("OPEN_COMPANION_ASSISTANT_PROVIDER", "openai").strip().lower() or "openai"


def _openai_assistant_patch() -> dict:
    env_openai_key = (
        os.environ.get("OPEN_COMPANION_ASSISTANT_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    keyring_openai_key = ""
    try:
        from providers.base import read_keyring_secret  # noqa: WPS433
        keyring_openai_key = read_keyring_secret(["openai_api_key", "openai"])
    except Exception:
        keyring_openai_key = ""
    if not (env_openai_key or keyring_openai_key):
        raise SuiteSkip(
            "OpenAI assistant live tests require OPENAI_API_KEY/OPEN_COMPANION_ASSISTANT_OPENAI_API_KEY or a saved openai_api_key credential."
        )
    return {
        "brain": {
            "provider": "openai",
            "model": OPENAI_ASSISTANT_MODEL,
            "layers": {
                "companion": {
                    "provider": "openai",
                    "model": OPENAI_ASSISTANT_MODEL,
                    "api_key": env_openai_key or keyring_openai_key,
                },
                "assistant": {
                    "provider": "openai",
                    "model": OPENAI_ASSISTANT_MODEL,
                    "api_key": env_openai_key or keyring_openai_key,
                }
            }
        },
        "memory": {
            "enabled": False,
            "write_back_enabled": False,
            "embedding_enabled": False,
        }
    }


def _anthropic_assistant_patch() -> dict:
    env_key = (
        os.environ.get("OPEN_COMPANION_ASSISTANT_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    ).strip()
    keyring_key = ""
    try:
        from providers.base import read_keyring_secret  # noqa: WPS433
        keyring_key = read_keyring_secret(["anthropic_api_key", "anthropic"])
    except Exception:
        keyring_key = ""
    api_key = env_key or keyring_key
    if not api_key:
        raise SuiteSkip(
            "Anthropic assistant live tests require ANTHROPIC_API_KEY/OPEN_COMPANION_ASSISTANT_ANTHROPIC_API_KEY or a saved anthropic_api_key credential."
        )
    return {
        "brain": {
            "provider": "anthropic",
            "model": ANTHROPIC_ASSISTANT_MODEL,
            "layers": {
                "companion": {
                    "provider": "anthropic",
                    "model": ANTHROPIC_ASSISTANT_MODEL,
                    "api_key": api_key,
                },
                "assistant": {
                    "provider": "anthropic",
                    "model": ANTHROPIC_ASSISTANT_MODEL,
                    "api_key": api_key,
                }
            }
        },
        "memory": {
            "enabled": False,
            "write_back_enabled": False,
            "embedding_enabled": False,
        }
    }


def _chatgpt_oauth_assistant_patch() -> dict:
    return {
        "brain": {
            "provider": "chatgpt_oauth",
            "model": CHATGPT_OAUTH_ASSISTANT_MODEL,
            "layers": {
                "companion": {
                    "provider": "chatgpt_oauth",
                    "model": CHATGPT_OAUTH_ASSISTANT_MODEL,
                },
                "assistant": {
                    "provider": "chatgpt_oauth",
                    "model": CHATGPT_OAUTH_ASSISTANT_MODEL,
                },
            },
        },
        "memory": {
            "enabled": False,
            "write_back_enabled": False,
            "embedding_enabled": False,
        },
    }


def _assistant_patch() -> dict:
    if _ASSISTANT_PROVIDER == "anthropic":
        return _anthropic_assistant_patch()
    if _ASSISTANT_PROVIDER in {"chatgpt_oauth", "oauth", "chatgpt"}:
        return _chatgpt_oauth_assistant_patch()
    return _openai_assistant_patch()


def _safe_terminal(command: str, timeout_seconds: int = 20) -> str:
    result = run_terminal(command, timeout_seconds=timeout_seconds)
    lowered = result.lower()
    if lowered.startswith("error: wsl") or "sandbox unavailable" in lowered:
        raise SuiteSkip(result)
    return result


def _assistant_turn(companion: Companion, prompt: str, approve: bool | None = None, timeout: int = 360):
    stderr_mark = companion.mark_stderr()
    events = companion.invoke_layer("assistant", prompt, timeout=timeout)
    combined = list(events)
    if any(event.get("type") == "tool_confirmation_requested" for event in events):
        if approve is None:
            raise AssertionError("Unexpected confirmation request")
        follow_up = companion.resolve_confirmation(approve, timeout=timeout)
        combined.extend(follow_up)
    replies = [event for event in combined if event.get("type") == "assistant_message"]
    reply = str(replies[-1].get("content") or "") if replies else ""
    stderr_lines = companion.stderr_since(stderr_mark)
    return reply, combined, stderr_lines


def _tool_names(events: list[dict]) -> list[str]:
    return [str(event.get("tool_name") or "") for event in events if event.get("type") == "tool_executed"]


def _tool_results(events: list[dict]) -> list[str]:
    return [str(event.get("result") or "") for event in events if event.get("type") == "tool_executed"]


def _maybe_skip_for_sandbox_failure(events: list[dict]) -> None:
    joined = "\n".join(_tool_results(events)).lower()
    if "sandbox unavailable" in joined or "wsl sandbox unavailable" in joined or "wsl/enumeratedistros/service/e_accessdenied" in joined:
        raise SuiteSkip(joined)


def _count_tool_call(stderr_lines: list[str], fragment: str) -> int:
    return sum(1 for line in stderr_lines if "[TOOL CALL]" in line and fragment in line)


def _vault_root(companion: Companion) -> Path:
    return companion.profile_root / "companion" / "vault"


def _shared_skill_root(companion: Companion) -> Path:
    return companion.profile_root / "companion" / "skills" / "shared"


def _report_validation_errors(report_text: str, marker: str) -> list[str]:
    text = str(report_text or "")
    lowered = text.lower()
    errors: list[str] = []
    if not text.strip():
        errors.append("report file is empty")
    if "```" in text:
        errors.append("report file contains markdown code fences")
    if "i need to" in lowered or "can't" in lowered:
        errors.append("report file contains task/prose failure text")
    if marker.lower() not in lowered:
        errors.append(f"report file does not contain marker {marker!r}")
    return errors


@contextmanager
def _preserve_workflow_files(vault_root: Path, source_text: str):
    workflow_source_path = vault_root / WORKFLOW_SOURCE_REL
    workflow_output_path = vault_root / WORKFLOW_OUTPUT_REL
    originals: dict[Path, str | None] = {}
    for path in (workflow_source_path, workflow_output_path):
        originals[path] = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else None
    try:
        workflow_source_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_source_path.write_text(source_text, encoding="utf-8")
        workflow_output_path.unlink(missing_ok=True)
        yield
    finally:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(original, encoding="utf-8")


@contextmanager
def _temporary_skill(skill_root: Path, filename: str, content: str):
    skill_root.mkdir(parents=True, exist_ok=True)
    path = skill_root / filename
    path.write_text(content, encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def _temporary_vault_file(vault_root: Path, relative_path: str):
    target = vault_root / Path(relative_path)
    try:
        yield relative_path
    finally:
        target.unlink(missing_ok=True)


@contextmanager
def _temporary_daily_note_marker(vault_root: Path, marker: str):
    note_path = vault_root / "Daily Notes" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    existing = note_path.read_text(encoding="utf-8", errors="ignore") if note_path.exists() else None
    try:
        note_path.parent.mkdir(parents=True, exist_ok=True)
        yield note_path
    finally:
        if existing is None:
            if note_path.exists():
                kept = [line for line in note_path.read_text(encoding="utf-8", errors="ignore").splitlines() if marker not in line]
                if kept:
                    note_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
                else:
                    note_path.unlink(missing_ok=True)
        else:
            note_path.write_text(existing, encoding="utf-8")


def _report_metrics(config_patch: dict | None = None) -> dict:
    marker = f"REPORT_OK_{uuid.uuid4().hex[:8]}"
    source_text = f"Source marker: {marker}\nWrite this marker into the report file.\n"
    with companion_session(config_patch=config_patch, timeout=420) as companion:
        vault_root = _vault_root(companion)
        with _preserve_workflow_files(vault_root, source_text):
            reply, events, stderr_lines = _assistant_turn(
                companion,
                f"Read {WORKFLOW_SOURCE_REL}, create {WORKFLOW_OUTPUT_REL} containing the source marker, and verify the report file after writing it.",
                timeout=420,
            )
            _maybe_skip_for_sandbox_failure(events)
            report_path = vault_root / WORKFLOW_OUTPUT_REL
            report_text = report_path.read_text(encoding="utf-8", errors="ignore") if report_path.exists() else ""
            validation_errors = _report_validation_errors(report_text, marker)
            return {
                "reply": reply,
                "tool_names": _tool_names(events),
                "report_text": report_text,
                "report_validation_errors": validation_errors,
                "report_is_valid": not validation_errors,
                "source_reads": _count_tool_call(stderr_lines, WORKFLOW_SOURCE_REL),
                "stderr_tail": "\n".join(stderr_lines[-20:]),
            }


def _simple_file_metrics(config_patch: dict | None = None) -> dict:
    marker = f"SIMPLE_READ_{uuid.uuid4().hex[:8]}"
    source_text = f"{marker}\nSecond line\n"
    with companion_session(config_patch=config_patch, timeout=300) as companion:
        vault_root = _vault_root(companion)
        with _preserve_workflow_files(vault_root, source_text):
            reply, events, stderr_lines = _assistant_turn(
                companion,
                f"Read {WORKFLOW_SOURCE_REL} and answer with the first non-empty line exactly.",
                timeout=300,
            )
            _maybe_skip_for_sandbox_failure(events)
            return {
                "reply": reply,
                "tool_names": _tool_names(events),
                "source_reads": _count_tool_call(stderr_lines, WORKFLOW_SOURCE_REL),
                "marker": marker,
            }


def _strict_model_available(model_name: str) -> str:
    available = {name.lower() for name in _ollama_models(timeout=6)}
    clean = model_name.lower()
    if clean not in available:
        raise SuiteSkip(f"Required model not installed for benchmark: {model_name}")
    return model_name


def test_report_workflow_creates_report_file() -> None:
    metrics = _report_metrics(_assistant_patch())
    assert any(name in metrics["tool_names"] for name in ("read_file", "write_file", "run_terminal")), metrics
    assert metrics["report_is_valid"], metrics
    assert metrics["reply"].strip(), metrics


def test_report_task_does_not_repeat_identical_source_read() -> None:
    metrics = _report_metrics(_assistant_patch())
    assert metrics["source_reads"] <= 1, metrics


def test_companion_does_not_parse_generic_raw_json_search_memories_tool_calls() -> None:
    adapter = get_family_adapter({"family": "google_gemma", "model": "gemma4:e4b"})
    cases = [
        '<|channel>thought Search memory first. <channel|>\n{"tool_call":"search_memories","arguments":{"query":"user details"}}',
        '<|channel>thought Search memory first. <channel|>\n{"name":"search_memories","arguments":{"query":"user details"}}',
        '<|channel>thought Search memory first. <channel|>\n{"function":{"name":"search_memories","arguments":{"query":"user details"}}}',
        '<|channel>thought Search memory first. <channel|>\n[{"tool_call":"search_memories","arguments":{"query":"user details"}},{"name":"search_memories","arguments":{"query":"preferences"}}]',
        'I''ve saved that.\n{"name":"search_memories","arguments":{"query":"favorite color"}}',
        '{"name":"search_memories","arguments":{ "query":"favorite color"}}<tool_call|>',
        '{"tool_calls": [{"function": "search_memories", "args": {"query": "favorite color"}}]}',
    ]

    for content in cases:
        cleaned, tool_calls = adapter.parse_tool_calls(content, None)
        assert cleaned == content, {"content": content, "cleaned": cleaned, "tool_calls": tool_calls}
        assert tool_calls == [], {"content": content, "cleaned": cleaned, "tool_calls": tool_calls}

def test_note_workflow_does_not_touch_unrelated_project_file() -> None:
    marker = f"llm-note-{uuid.uuid4().hex[:8]}"
    unrelated_rel = f"Projects/unrelated-{uuid.uuid4().hex[:8]}.txt"
    with companion_session(config_patch=_assistant_patch(), timeout=360) as companion:
        vault_root = _vault_root(companion)
        unrelated_path = vault_root / unrelated_rel
        unrelated_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_path.write_text("do not change me", encoding="utf-8")
        with _temporary_daily_note_marker(vault_root, marker) as note_path, _temporary_vault_file(vault_root, unrelated_rel):
            reply, events, _stderr_lines = _assistant_turn(
                companion,
                f"Append a daily note saying '{marker}' to today's daily note, verify it, and do not touch {unrelated_rel}.",
                timeout=360,
            )
            _maybe_skip_for_sandbox_failure(events)
            note_text = note_path.read_text(encoding="utf-8", errors="ignore") if note_path.exists() else ""
            assert marker in note_text, note_text
            assert unrelated_path.read_text(encoding="utf-8", errors="ignore") == "do not change me"
            assert any(name in _tool_names(events) for name in ("append_file", "write_file", "run_terminal")), events
            assert reply.strip(), reply


def test_tool_failure_is_reported_honestly() -> None:
    bad_command = f"definitely_not_a_real_command_oc_{uuid.uuid4().hex[:8]}"
    with companion_session(config_patch=_assistant_patch(), timeout=240) as companion:
        reply, events, _stderr_lines = _assistant_turn(
            companion,
            f"Use run_terminal to run this exact command: {bad_command}. Then tell me clearly whether it succeeded. Do not pretend success.",
            timeout=240,
        )
        _maybe_skip_for_sandbox_failure(events)
        tool_results = "\n".join(_tool_results(events)).lower()
        lowered_reply = reply.lower()
        assert any(marker in tool_results for marker in ("not found", "error", "stderr", "exit code 1")), tool_results
        assert any(marker in lowered_reply for marker in FAILURE_MARKERS), reply
        assert not any(marker in lowered_reply for marker in SUCCESS_MARKERS), reply


def test_base_tool_payload_hides_skill_tools() -> None:
    token = f"BASE_TOOL_{uuid.uuid4().hex[:8]}"
    target_rel = f"Projects/base-tool-{uuid.uuid4().hex[:8]}.txt"
    with companion_session(config_patch=_assistant_patch(), timeout=300) as companion:
        vault_root = _vault_root(companion)
        with _temporary_vault_file(vault_root, target_rel):
            warmup = companion.say("Say ready.", timeout=180)
            assert warmup.strip(), warmup
            reply, events, stderr_lines = _assistant_turn(
                companion,
                f"Use write_file to create `{target_rel}` containing exactly `{token}`, then use read_file to verify it. Do not use skill tools.",
                timeout=300,
            )
            _maybe_skip_for_sandbox_failure(events)
            created_path = vault_root / Path(target_rel)
            created = created_path.read_text(encoding="utf-8", errors="ignore") if created_path.exists() else ""
            tool_names = _tool_names(events)
            assert token in created, {"created": created, "tools": tool_names, "reply": reply}
            assert "list_skills" not in tool_names, {"tools": tool_names, "reply": reply}
            assert "help_skill" not in tool_names, {"tools": tool_names, "reply": reply}
            assert "write_file" in tool_names, {"tools": tool_names, "reply": reply}
            assert _count_tool_call(stderr_lines, target_rel) >= 1, stderr_lines
            assert reply.strip(), reply


def test_tool_workflow_benchmark_records_memory_variants() -> None:
    with_memory = _report_metrics({"memory": {"enabled": True, "write_back_enabled": True}})
    without_memory = _report_metrics({"memory": {"enabled": False, "write_back_enabled": False}})
    report = {
        "with_memory": with_memory,
        "without_memory": without_memory,
    }
    print(json.dumps(report, indent=2))
    assert with_memory["reply"].strip(), report
    assert without_memory["reply"].strip(), report
    assert with_memory["report_is_valid"] or without_memory["report_is_valid"], report


def test_prompt_noise_benchmark_records_memory_variants() -> None:
    with_memory = _simple_file_metrics({"memory": {"enabled": True, "write_back_enabled": True}})
    without_memory = _simple_file_metrics({"memory": {"enabled": False, "write_back_enabled": False}})
    report = {
        "with_memory": with_memory,
        "without_memory": without_memory,
    }
    print(json.dumps(report, indent=2))
    assert with_memory["reply"].strip(), report
    assert without_memory["reply"].strip(), report
    assert any(name in with_memory["tool_names"] for name in ("read_file", "run_terminal")), report
    assert any(name in without_memory["tool_names"] for name in ("read_file", "run_terminal")), report


def test_model_comparison_benchmark_records_gemma_variants() -> None:
    model_large = _strict_model_available("gemma4:26b")
    model_small = _strict_model_available("gemma4:e4b")
    assistant_large = _simple_file_metrics({
        "brain": {
            "model": model_large,
            "layers": {"assistant": {"provider": "ollama", "model": model_large}},
        }
    })
    assistant_small = _simple_file_metrics({
        "brain": {
            "model": model_small,
            "layers": {"assistant": {"provider": "ollama", "model": model_small}},
        }
    })
    report = {
        model_large: assistant_large,
        model_small: assistant_small,
    }
    print(json.dumps(report, indent=2))
    assert assistant_large["reply"].strip(), report
    assert assistant_small["reply"].strip(), report
    assert any(name in assistant_large["tool_names"] for name in ("read_file", "run_terminal")), report
    assert any(name in assistant_small["tool_names"] for name in ("read_file", "run_terminal")), report
