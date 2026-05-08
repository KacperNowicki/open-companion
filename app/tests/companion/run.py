#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
for candidate in (str(APP_ROOT), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

# Parse --provider <name> before importing tests so the env var is visible at
# import time (test_llm_behavior reads it to pick the right config patch).
_raw_args = list(sys.argv[1:])
_provider_override = "openai"
if "--provider" in _raw_args:
    idx = _raw_args.index("--provider")
    if idx + 1 < len(_raw_args):
        _provider_override = _raw_args[idx + 1].strip().lower() or "openai"
        del _raw_args[idx:idx + 2]
    else:
        del _raw_args[idx]
os.environ["OPEN_COMPANION_ASSISTANT_PROVIDER"] = _provider_override
_gemma_behavioral_requested = "--gemma-behavioral" in _raw_args
if "--main-profile" in _raw_args:
    _raw_args.remove("--main-profile")
    os.environ["OPEN_COMPANION_TEST_USE_MAIN_PROFILE"] = "1"
elif _gemma_behavioral_requested:
    os.environ["OPEN_COMPANION_TEST_USE_MAIN_PROFILE"] = "1"
_behavioral_assignment_selector = ""
for _selector_flag in ("--behavioral-assignment", "--behavioral-only"):
    if _selector_flag in _raw_args:
        idx = _raw_args.index(_selector_flag)
        if idx + 1 < len(_raw_args):
            _behavioral_assignment_selector = _raw_args[idx + 1].strip()
            del _raw_args[idx:idx + 2]
        else:
            del _raw_args[idx]
        break

from tests.companion.conftest import BOLD, RESET, SuiteSkip, ok, resolve_chat_model, run_test, section, skip, summary
from tests.companion.test_llm_behavior import (
    test_companion_does_not_parse_generic_raw_json_search_memories_tool_calls,
    test_model_comparison_benchmark_records_gemma_variants,
    test_note_workflow_does_not_touch_unrelated_project_file,
    test_prompt_noise_benchmark_records_memory_variants,
    test_report_task_does_not_repeat_identical_source_read,
    test_report_workflow_creates_report_file,
    test_base_tool_payload_hides_skill_tools,
    test_tool_workflow_benchmark_records_memory_variants,
    test_tool_failure_is_reported_honestly,
    OPENAI_ASSISTANT_MODEL,
    ANTHROPIC_ASSISTANT_MODEL,
    CHATGPT_OAUTH_ASSISTANT_MODEL,
    _ASSISTANT_PROVIDER,
)
from tests.companion.test_heartbeat import (
    test_heartbeat_disabled,
    test_heartbeat_fires,
    test_heartbeat_suppressed_during_conversation,
)
from tests.companion.test_memory import (
    test_corrects_memory,
    test_memory_not_hallucinated,
    test_memory_persists_across_sessions,
    test_remembers_name,
    test_remembers_preference,
)

ARGS = set(_raw_args)
FAST_MODE = "--fast" in ARGS
ASSISTANT_WORKFLOWS_ONLY = "--assistant-workflows" in ARGS
GEMMA_BEHAVIORAL_ONLY = "--gemma-behavioral" in ARGS
GEMMA_BEHAVIORAL_LIST = "--behavioral-list" in ARGS
SLOW_TESTS = {
    "heartbeat: emits an unprompted reply",
    "heartbeat: stays suppressed during active conversation",
    "heartbeat: remains disabled when configured off",
    "llm behavior: report workflow creates a vault report",
    "llm behavior: report task avoids repeated source reads",
    "llm behavior: note workflow does not touch unrelated files",
    "llm behavior: base tool payload hides skill tools",
    "benchmarks: tool workflow records memory variants",
    "benchmarks: prompt noise records memory variants",
    "benchmarks: model comparison records Gemma variants",
}

ASSISTANT_WORKFLOW_TESTS = {
    "llm behavior: report workflow creates a vault report",
    "llm behavior: report task avoids repeated source reads",
    "llm behavior: note workflow does not touch unrelated files",
    "llm behavior: tool failure is reported honestly",
    "llm behavior: base tool payload hides skill tools",
}

TEST_GROUPS = [
    (
        "Memory",
        [
            ("memory: remembers the user's name", test_remembers_name),
            ("memory: remembers stated preferences", test_remembers_preference),
            ("memory: persists across wrapper restarts", test_memory_persists_across_sessions),
            ("memory: corrections override stale facts", test_corrects_memory),
            ("memory: fresh profile does not hallucinate personal facts", test_memory_not_hallucinated),
        ],
    ),
    (
        "LLM Behavior",
        [
            ("llm behavior: report workflow creates a vault report", test_report_workflow_creates_report_file),
            ("llm behavior: report task avoids repeated source reads", test_report_task_does_not_repeat_identical_source_read),
            ("llm behavior: companion does not parse generic raw memory tool JSON", test_companion_does_not_parse_generic_raw_json_search_memories_tool_calls),
            ("llm behavior: note workflow does not touch unrelated files", test_note_workflow_does_not_touch_unrelated_project_file),
            ("llm behavior: tool failure is reported honestly", test_tool_failure_is_reported_honestly),
            ("llm behavior: base tool payload hides skill tools", test_base_tool_payload_hides_skill_tools),
        ],
    ),
    (
        "Benchmarks",
        [
            ("benchmarks: tool workflow records memory variants", test_tool_workflow_benchmark_records_memory_variants),
            ("benchmarks: prompt noise records memory variants", test_prompt_noise_benchmark_records_memory_variants),
            ("benchmarks: model comparison records Gemma variants", test_model_comparison_benchmark_records_gemma_variants),
        ],
    ),
    (
        "Heartbeat",
        [
            ("heartbeat: emits an unprompted reply", test_heartbeat_fires),
            ("heartbeat: stays suppressed during active conversation", test_heartbeat_suppressed_during_conversation),
            ("heartbeat: remains disabled when configured off", test_heartbeat_disabled),
        ],
    ),
]


def _emit_line(file_handle, text: str = "") -> None:
    print(text, flush=True)
    if file_handle is not None:
        file_handle.write(f"{text}\n")
        file_handle.flush()


def main() -> None:
    if GEMMA_BEHAVIORAL_ONLY:
        from tests.companion.test_gemma_behavioral import run_suite as run_gemma_behavioral

        logs_dir = ROOT / "app" / "tests" / "companion" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = logs_dir / f"gemma-behavioral-{timestamp}.log"
        relative_log_path = log_path.relative_to(ROOT).as_posix()
        with log_path.open("w", encoding="utf-8") as log_file:
            _emit_line(log_file, f"LOG     -> {relative_log_path}")
            success = run_gemma_behavioral(
                log_file=log_file,
                selector=_behavioral_assignment_selector,
                list_only=GEMMA_BEHAVIORAL_LIST,
            )
        sys.exit(0 if success else 1)

    print(f"{BOLD}OpenCompanion Live Companion Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    if ASSISTANT_WORKFLOWS_ONLY:
        print("Mode: assistant-workflows (report/note/tool assistant tests only)")
    elif FAST_MODE:
        print("Mode: fast (slow heartbeat tests skipped)")
    print()

    section("Prerequisites")
    if ASSISTANT_WORKFLOWS_ONLY:
        if _ASSISTANT_PROVIDER == "anthropic":
            _model = ANTHROPIC_ASSISTANT_MODEL
        elif _ASSISTANT_PROVIDER in {"chatgpt_oauth", "oauth", "chatgpt"}:
            _model = CHATGPT_OAUTH_ASSISTANT_MODEL
        else:
            _model = OPENAI_ASSISTANT_MODEL
        ok(f"assistant workflows: using {_ASSISTANT_PROVIDER} assistant model {_model}")
    else:
        try:
            model = resolve_chat_model()
            ok(f"ollama: reachable with chat model {model}")
        except SuiteSkip as exc:
            skip("ollama: companion suite", str(exc))
            summary()
            sys.exit(0)

    for title, tests in TEST_GROUPS:
        selected = tests
        if ASSISTANT_WORKFLOWS_ONLY:
            selected = [(name, fn) for name, fn in tests if name in ASSISTANT_WORKFLOW_TESTS]
            if not selected:
                continue
        section(title)
        for name, fn in selected:
            if FAST_MODE and not ASSISTANT_WORKFLOWS_ONLY and name in SLOW_TESTS:
                skip(name, "fast mode")
                continue
            run_test(name, fn)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
