"""
Dream Consolidation
====================
Background process that cleans and deduplicates the memory file.

Trigger conditions (both must be true):
  - 24+ hours since last consolidation
  - 5+ conversations since last consolidation

Runs in a background thread spawned by LayeredRuntime on session start.
Uses a lockfile to prevent double-runs.

Three phases:
  1. Orientation  — read the memory file
  2. Consolidate  — call brain to merge/deduplicate/remove stale entries
  3. Write back   — overwrite the memory file with the cleaned version

Dream state is tracked in companion/memory/.dream_state.json.
Conversation count is incremented by wrapper.py after each companion reply.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
from memory import strip_llm_preamble
from model_family import strip_gemma_thought_blocks
from runtime_paths import MEMORY_DIR, ensure_runtime_dirs

DREAM_STATE_PATH = MEMORY_DIR / ".dream_state.json"
DREAM_LOCK_PATH = MEMORY_DIR / ".dream_lock"

DREAM_INTERVAL_HOURS = 24
DREAM_MIN_CONVERSATIONS = 5
_TIMESTAMP_PREFIX_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*")
_SUBJECT_PATTERNS = (
    re.compile(r"^(?P<subject>.+?)\s+is\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+are\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+was\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+were\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+likes\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+dislikes\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+prefers\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+loves\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+hates\s+.+$", re.IGNORECASE),
)

_CONSOLIDATION_SYSTEM = (
    "You are a memory consolidation assistant. You will receive the current contents of a single "
    "memory file containing bullet-point entries. Your job is to clean it up.\n\n"
    "Rules:\n"
    "1. Merge entries that say the same thing — keep the most specific or most recent version.\n"
    "2. Remove entries that use relative time language that is now stale "
    '   (e.g., "yesterday", "last week", "recently" without a specific date), '
    "   UNLESS the fact itself is still likely true regardless of when it was written.\n"
    "3. Resolve contradictions — keep the most recently dated or most specific entry.\n"
    "4. Keep the file header (lines starting with #) exactly as-is.\n"
    "5. Return the complete cleaned file content. Each entry must remain a bullet point "
    '   starting with "- ". Do not add commentary or explanations.\n'
    "6. If there is nothing to clean, return the file exactly as given.\n"
    "7. Return only the file content — no markdown fences, no extra text."
)


def _load_dream_state() -> dict:
    if DREAM_STATE_PATH.exists():
        try:
            return json.loads(DREAM_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_consolidation_ts": 0.0, "conversations_since": 0}


def _save_dream_state(state: dict) -> None:
    DREAM_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def increment_conversation_count() -> None:
    """Increment the conversations-since-last-dream counter. Called after each companion reply."""
    ensure_runtime_dirs()
    state = _load_dream_state()
    state["conversations_since"] = state.get("conversations_since", 0) + 1
    _save_dream_state(state)


def should_dream() -> bool:
    """Return True if both trigger conditions are met."""
    state = _load_dream_state()
    now = time.time()
    hours_elapsed = (now - state.get("last_consolidation_ts", 0.0)) / 3600
    conversations = state.get("conversations_since", 0)
    return hours_elapsed >= DREAM_INTERVAL_HOURS and conversations >= DREAM_MIN_CONVERSATIONS


def _acquire_lock() -> bool:
    """Try to create the lockfile. Returns True if we got the lock."""
    try:
        # Exclusive creation — fails if file already exists
        DREAM_LOCK_PATH.touch(exist_ok=False)
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    try:
        DREAM_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _split_memory_content(content: str) -> tuple[list[str], list[str]]:
    header_lines: list[str] = []
    entries: list[str] = []
    for line in content.splitlines():
        if line.startswith("- "):
            entries.append(line[2:].strip())
        else:
            header_lines.append(line)
    return header_lines, entries


def _render_memory_content(header_lines: list[str], entries: list[str]) -> str:
    lines = list(header_lines)
    if entries and lines and lines[-1].strip():
        lines.append("")
    lines.extend(f"- {entry}" for entry in entries)
    rendered = "\n".join(lines).rstrip() + "\n"
    return rendered


def _strip_timestamp_prefix(entry: str) -> str:
    return _TIMESTAMP_PREFIX_RE.sub("", str(entry or "").strip(), count=1).strip()


def _parse_timestamp_value(entry: str) -> float:
    match = _TIMESTAMP_PREFIX_RE.match(str(entry or "").strip())
    if not match:
        return 0.0

    raw = match.group("timestamp").strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _entry_subject(entry: str) -> str | None:
    body = _strip_timestamp_prefix(entry).rstrip(".")
    for pattern in _SUBJECT_PATTERNS:
        match = pattern.match(body)
        if match:
            return " ".join(match.group("subject").strip().lower().split())
    return None


def _prefer_candidate(candidate: dict, current: dict | None) -> bool:
    if current is None:
        return True
    if candidate["timestamp"] != current["timestamp"]:
        return candidate["timestamp"] > current["timestamp"]
    return candidate["order"] > current["order"]


def _apply_global_fact_resolution() -> None:
    filepath = MEMORY_DIR / "memory.md"
    if not filepath.exists():
        return

    entries: list[dict] = []
    lines = filepath.read_text(encoding="utf-8").splitlines()
    for line_index, line in enumerate(lines):
        if not line.startswith("- "):
            continue
        entry_text = line[2:].strip()
        entries.append({
            "id": line_index,
            "line_index": line_index,
            "entry": entry_text,
            "body_key": _strip_timestamp_prefix(entry_text).lower(),
            "subject": _entry_subject(entry_text),
            "timestamp": _parse_timestamp_value(entry_text),
            "order": line_index,
        })

    if not entries:
        return

    best_by_body: dict[str, dict] = {}
    best_by_subject: dict[str, dict] = {}
    for record in entries:
        body_key = record["body_key"]
        if body_key and _prefer_candidate(record, best_by_body.get(body_key)):
            best_by_body[body_key] = record

        subject = record["subject"]
        if subject and _prefer_candidate(record, best_by_subject.get(subject)):
            best_by_subject[subject] = record

    keep_ids: set[int] = set()
    for record in entries:
        subject = record["subject"]
        if subject:
            winner = best_by_subject.get(subject)
            if winner is not None:
                keep_ids.add(winner["id"])
            continue

        body_key = record["body_key"]
        winner = best_by_body.get(body_key)
        if winner is not None:
            keep_ids.add(winner["id"])

    updated_lines: list[str] = []
    for line_index, line in enumerate(lines):
        if not line.startswith("- "):
            updated_lines.append(line)
            continue

        matched = next((record for record in entries if record["line_index"] == line_index), None)
        if matched and matched["id"] in keep_ids:
            updated_lines.append(line)

    rendered = "\n".join(updated_lines).rstrip() + "\n"
    original = "\n".join(lines).rstrip() + "\n"
    if rendered != original:
        filepath.write_text(rendered, encoding="utf-8")


def _consolidate_topic_file(filepath: Path, client, model: str) -> None:
    """Run the consolidation LLM call for the memory file and overwrite it if changed."""
    original_content = filepath.read_text(encoding="utf-8")
    # Skip files with no bullet entries — nothing to consolidate
    if not any(line.startswith("- ") for line in original_content.splitlines()):
        return

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CONSOLIDATION_SYSTEM},
                {"role": "user", "content": original_content},
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        raw_response = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("Dream consolidation LLM call failed for %s: %s", filepath.name, exc)
        return

    cleaned = strip_llm_preamble(raw_response).strip()
    cleaned = strip_gemma_thought_blocks(cleaned).strip()

    if not cleaned or cleaned == original_content.strip():
        return

    entry_lines = [line for line in cleaned.splitlines() if line.strip().startswith("-")]
    original_entry_lines = [line for line in original_content.splitlines() if line.strip().startswith("-")]
    if len(original_entry_lines) > 2 and len(entry_lines) < max(1, len(original_entry_lines) // 2):
        logger.warning(
            "[DREAM] consolidation result suspiciously short (%s entries vs %s original), skipping write",
            len(entry_lines),
            len(original_entry_lines),
        )
        return

    # Ensure file ends with a newline
    if not cleaned.endswith("\n"):
        cleaned += "\n"

    filepath.write_text(cleaned, encoding="utf-8")
    logger.info("Dream: consolidated %s", filepath.name)


def run_dream(client, config: dict) -> None:
    """
    Run a full dream consolidation cycle.

    Acquires lockfile, consolidates the memory file, then updates dream state.
    Safe to call from a background thread.
    """
    if not _acquire_lock():
        logger.info("Dream: another consolidation is already running, skipping.")
        return

    try:
        logger.info("Dream: starting consolidation cycle.")
        model = config.get("brain", {}).get("model", "gemma4:e4b")
        _apply_global_fact_resolution()

        filepath = MEMORY_DIR / "memory.md"
        if filepath.exists():
            _consolidate_topic_file(filepath, client, model)

        _apply_global_fact_resolution()

        state = _load_dream_state()
        state["last_consolidation_ts"] = time.time()
        state["conversations_since"] = 0
        state["last_consolidation_iso"] = datetime.now(timezone.utc).isoformat()
        _save_dream_state(state)

        logger.info("Dream: consolidation complete.")
    except Exception as exc:
        logger.error("Dream: consolidation failed: %s", exc)
    finally:
        _release_lock()


def maybe_trigger_dream(client, config: dict) -> None:
    """
    Check trigger conditions and, if met, fire dream consolidation in a background thread.
    Returns immediately — never blocks the caller.
    """
    if not config.get("memory", {}).get("enabled", True):
        return
    if not config.get("memory", {}).get("dream_enabled", True):
        return
    if not should_dream():
        return

    logger.info("Dream: trigger conditions met, starting background consolidation.")
    thread = threading.Thread(
        target=run_dream,
        args=(client, config),
        daemon=True,
        name="dream-consolidation",
    )
    thread.start()
