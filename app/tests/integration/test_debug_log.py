#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
SCRATCH_ROOT = ROOT / "app" / "tests" / "integration" / ".tmp-debug-log"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import ok, summary

import debug_log


def _temp_dir() -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_ROOT / f"debug-log-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_debug_log_disabled_creates_no_file() -> None:
    name = "debug log: disabled mode does not create files"
    scratch = _temp_dir()
    original_enabled = debug_log.DEBUG_ENABLED
    original_path = debug_log.DEBUG_LOG_PATH
    try:
        debug_log.DEBUG_ENABLED = False
        debug_log.DEBUG_LOG_PATH = scratch / "logs" / "debug.log"
        debug_log.debug_log("PROMPT_SENT", "hello")
        assert not debug_log.DEBUG_LOG_PATH.exists()
        ok(name)
    finally:
        debug_log.DEBUG_ENABLED = original_enabled
        debug_log.DEBUG_LOG_PATH = original_path
        shutil.rmtree(scratch, ignore_errors=True)


def test_debug_log_writes_full_payload_and_rotates() -> None:
    name = "debug log: writes full escaped payload and rotates"
    scratch = _temp_dir()
    original_enabled = debug_log.DEBUG_ENABLED
    original_path = debug_log.DEBUG_LOG_PATH
    original_rotate_bytes = debug_log.DEBUG_ROTATE_BYTES
    try:
        debug_log.DEBUG_ENABLED = True
        debug_log.DEBUG_ROTATE_BYTES = 10
        debug_log.DEBUG_LOG_PATH = scratch / "logs" / "debug.log"
        debug_log.DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        debug_log.DEBUG_LOG_PATH.write_text("x" * 10, encoding="utf-8")

        payload = "line one\nline two\n" + ("z" * 1000)
        debug_log.debug_log("OLLAMA_RAW", payload)

        rotated = debug_log.DEBUG_LOG_PATH.with_name("debug.log.1")
        assert rotated.exists()
        line = debug_log.DEBUG_LOG_PATH.read_text(encoding="utf-8").rstrip("\n")
        entry = json.loads(line)
        assert entry["timestamp"]
        assert entry["event"] == "OLLAMA_RAW"
        assert entry["payload"] == payload
        assert "system" in entry
        assert "message" in entry
        assert "..." not in line
        ok(name)
    finally:
        debug_log.DEBUG_ENABLED = original_enabled
        debug_log.DEBUG_LOG_PATH = original_path
        debug_log.DEBUG_ROTATE_BYTES = original_rotate_bytes
        shutil.rmtree(scratch, ignore_errors=True)


def test_debug_log_skips_streaming_ollama_chunks() -> None:
    name = "debug log: skips streaming ollama chunks"
    scratch = _temp_dir()
    original_enabled = debug_log.DEBUG_ENABLED
    original_path = debug_log.DEBUG_LOG_PATH
    try:
        debug_log.DEBUG_ENABLED = True
        debug_log.DEBUG_LOG_PATH = scratch / "logs" / "debug.log"
        debug_log.debug_log("OLLAMA_RAW", {"message": {"content": "token"}, "done": False})
        assert not debug_log.DEBUG_LOG_PATH.exists()
        ok(name)
    finally:
        debug_log.DEBUG_ENABLED = original_enabled
        debug_log.DEBUG_LOG_PATH = original_path
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    test_debug_log_disabled_creates_no_file()
    test_debug_log_writes_full_payload_and_rotates()
    test_debug_log_skips_streaming_ollama_chunks()
    summary()
