from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time

import brain
import memory
from model_family import strip_gemma_thought_blocks, strip_legacy_think_blocks

logger = logging.getLogger(__name__)

_heartbeat_active = False
_heartbeat_thread: threading.Thread | None = None
_heartbeat_silent_streak = 0
_last_seen_interaction_ts = 0.0
_dnd_until: float = 0.0
_dnd_lock = threading.Lock()


def get_heartbeat_config(config: dict) -> dict:
    heartbeat_cfg = config.get("heartbeat", {}) if isinstance(config, dict) else {}
    legacy_vision = config.get("vision", {}) if isinstance(config, dict) else {}
    heartbeat_cfg = heartbeat_cfg if isinstance(heartbeat_cfg, dict) else {}
    legacy_vision = legacy_vision if isinstance(legacy_vision, dict) else {}
    return {
        "enabled": bool(heartbeat_cfg.get("enabled", legacy_vision.get("heartbeat_enabled", False))),
        "interval": max(1, int(heartbeat_cfg.get("interval", legacy_vision.get("heartbeat_interval", 1800)) or 1800)),
        "only_when_idle": bool(heartbeat_cfg.get("only_when_idle", legacy_vision.get("only_when_idle", False))),
        "idle_threshold_minutes": max(
            1,
            int(heartbeat_cfg.get("idle_threshold_minutes", legacy_vision.get("idle_threshold_minutes", 5)) or 5),
        ),
    }


def start_heartbeat(config, emit_fn, wrapper_state):
    global _heartbeat_active, _heartbeat_thread
    global _heartbeat_silent_streak, _last_seen_interaction_ts
    if _heartbeat_active:
        return

    _heartbeat_active = True
    _heartbeat_silent_streak = 0
    _last_seen_interaction_ts = float(wrapper_state.get("last_interaction_ts", 0) or 0)
    wrapper_state.setdefault("session_started_ts", time.time())
    logger.info(
        "Starting heartbeat loop: interval=%ss",
        get_heartbeat_config(config).get("interval", 1800),
    )
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_thread_fn,
        args=(config, emit_fn, wrapper_state),
        daemon=True,
        name="heartbeat-loop",
    )
    _heartbeat_thread.start()


def stop_heartbeat():
    global _heartbeat_active, _heartbeat_thread
    _heartbeat_active = False
    thread = _heartbeat_thread
    _heartbeat_thread = None
    logger.info("Stopping heartbeat loop")
    if thread and thread.is_alive():
        thread.join(timeout=2)


def restart_heartbeat(config, emit_fn, wrapper_state):
    stop_heartbeat()
    time.sleep(1)
    start_heartbeat(config, emit_fn, wrapper_state)


def set_dnd(until: float) -> None:
    global _dnd_until
    with _dnd_lock:
        _dnd_until = float(until)


def cancel_dnd() -> None:
    global _dnd_until
    with _dnd_lock:
        _dnd_until = 0.0


def is_dnd_active() -> bool:
    with _dnd_lock:
        return _dnd_until > 0 and time.time() < _dnd_until


def _heartbeat_thread_fn(config, emit_fn, wrapper_state):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_heartbeat_async_loop(config, emit_fn, wrapper_state))
    finally:
        asyncio.set_event_loop(None)
        loop.close()


async def _heartbeat_async_loop(config, emit_fn, wrapper_state):
    while _heartbeat_active:
        heartbeat_cfg = get_heartbeat_config(config)
        interval = heartbeat_cfg["interval"]
        await asyncio.sleep(interval)
        if not _heartbeat_active:
            break

        if heartbeat_cfg["only_when_idle"]:
            idle_threshold = heartbeat_cfg["idle_threshold_minutes"] * 60
            if float(wrapper_state.get("last_interaction_ts", 0) or 0) + idle_threshold > time.time():
                continue

        try:
            await _run_tick(config, emit_fn, wrapper_state)
        except Exception:
            logger.exception("Heartbeat tick failed unexpectedly")


async def _run_tick(config, emit_fn, wrapper_state):
    now = time.time()
    if is_dnd_active():
        print("[HEARTBEAT] tick skipped - DND active", file=sys.stderr, flush=True)
        return

    last_interaction = float(wrapper_state.get("last_interaction_ts", 0) or 0)
    interval = get_heartbeat_config(config)["interval"]
    seconds_since_last_interaction = max(0, int(now - last_interaction)) if last_interaction else 0

    if last_interaction and seconds_since_last_interaction <= interval:
        print(
            f"[HEARTBEAT] tick skipped - user active {seconds_since_last_interaction}s ago",
            file=sys.stderr,
            flush=True,
        )
        return

    runtime_state = str(wrapper_state.get("runtime_state") or "idle").strip().lower()
    runtime_state_updated_ts = float(wrapper_state.get("runtime_state_updated_ts", 0) or 0)
    runtime_state_age = max(0, int(now - runtime_state_updated_ts)) if runtime_state_updated_ts else 0
    if runtime_state != "idle" and runtime_state_age <= interval:
        print(
            f"[HEARTBEAT] tick skipped - runtime state {runtime_state}",
            file=sys.stderr,
            flush=True,
        )
        return

    print(
        f"[HEARTBEAT] tick fired - {seconds_since_last_interaction}s since last interaction",
        file=sys.stderr,
        flush=True,
    )
    description = None

    await _heartbeat_inference(description, config, emit_fn, wrapper_state)


async def _heartbeat_inference(description, config, emit_fn, wrapper_state):
    global _heartbeat_silent_streak, _last_seen_interaction_ts

    screen_context = (
        f"Current screen: {description}"
        if description
        else "You cannot currently see the user's screen."
    )
    current_time = time.strftime("%H:%M")
    current_day = time.strftime("%A")
    last_interaction_ts = float(wrapper_state.get("last_interaction_ts", 0) or 0)
    seconds_since_last_interaction = max(0, int(time.time() - last_interaction_ts)) if last_interaction_ts else 0
    inactivity_minutes = seconds_since_last_interaction // 60
    if last_interaction_ts and last_interaction_ts != _last_seen_interaction_ts:
        _last_seen_interaction_ts = last_interaction_ts
        _heartbeat_silent_streak = 0
    memories = await asyncio.to_thread(memory.load_all_memories)
    should_push_to_speak = _heartbeat_silent_streak >= 2
    silence_guidance = ""
    if should_push_to_speak:
        silence_guidance = (
            f"\nYou have already answered SILENT for {_heartbeat_silent_streak} heartbeat tick(s) in a row.\n"
            "You should say something brief and natural this time unless speaking would be clearly inappropriate.\n"
        )

    prompt = f"""[HEARTBEAT - internal context, not shown to user]

Current time: {current_time}
Current day: {current_day}
Last heard from the user: {inactivity_minutes} minute(s) ago
Seconds since the user last interacted: {seconds_since_last_interaction}
Current consecutive SILENT heartbeat count: {_heartbeat_silent_streak}

{screen_context}

What you remember about this user:
{memories}

---
Decide: do you want to say something to the user right now?

If yes: respond naturally in character. One or two sentences maximum.
If no: respond with exactly the word: SILENT

{silence_guidance}

Good reasons to speak include:
- the user has been quiet for a while and a gentle check-in feels natural
- the time of day suggests a small comment or wellbeing nudge
- something in recent screen context or memory makes this a natural moment

Only speak if something is genuinely worth noting - a change, something interesting,
or a natural moment to check in. Prefer silence. Do not comment on every tick."""

    system_prompt = str(wrapper_state.get("companion_system_prompt") or "").strip()
    if not system_prompt:
        return

    try:
        client = await asyncio.to_thread(brain.create_client, config, "companion")
        response_message = await asyncio.to_thread(
            brain.chat,
            client,
            config,
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
            None,
            "companion",
        )
    except Exception:
        logger.exception("Heartbeat inference brain call failed")
        return

    if getattr(response_message, "tool_calls", None):
        logger.info("Heartbeat inference returned tool calls; dropping them.")

    response_text = str(getattr(response_message, "content", "") or "").strip()
    print(f"[HEARTBEAT] LLM response: {response_text[:100]}", file=sys.stderr, flush=True)
    visible_text = _strip_display_artifacts(response_text)
    if not visible_text or visible_text.upper() == "SILENT":
        _heartbeat_silent_streak += 1
        logger.info("Heartbeat stayed silent; streak is now %s", _heartbeat_silent_streak)
        return

    clean_reply = _strip_legacy_json_directives(visible_text)
    if not clean_reply or clean_reply.upper() == "SILENT":
        _heartbeat_silent_streak += 1
        logger.info("Heartbeat produced no usable reply; streak is now %s", _heartbeat_silent_streak)
        return

    payload = {
        "type": "assistant_message",
        "content": clean_reply,
        "state": "idle",
        "layer": "companion",
    }
    _heartbeat_silent_streak = 0
    logger.info("Heartbeat emitted a companion reply after %s minute(s) of inactivity", inactivity_minutes)
    emit_fn(payload)


def _strip_gemma_token_bleed(s: str) -> str:
    """Strip all Gemma 4 special token bleed variants. Keep in sync with wrapper.py."""
    s = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"call:\w+\{[^}]*\}<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool_response>.*?<tool_response\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool>.*?<tool\|>", "", s, flags=re.DOTALL)
    s = re.sub(r'<\|tool_call>|<tool_call\|>|<\|tool_response>|<tool_response\|>|<\|tool>|<tool\|>|<\|"\|>', "", s)
    return s


def _strip_display_artifacts(text: str) -> str:
    s = str(text or "")
    # 1. Gemma 4 special token bleed variants
    s = _strip_gemma_token_bleed(s)
    # 2. Thought/reasoning blocks
    s = strip_gemma_thought_blocks(s)
    s = strip_legacy_think_blocks(s)
    s = re.sub(r"\[calls\s+[^\]]*?\]", "", s)
    s = re.sub(r"(?<!\*)\*\*([^*\n]+?)\*\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<!_)__([^_\n]+?)__(?!_)", r"\1", s)
    s = re.sub(r"(?<!\*)\*[^*\n]+?\*(?!\*)", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_legacy_json_directives(reply_text: str) -> str:
    remaining_lines = []

    for line in str(reply_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("type") == "animation":
                continue

        remaining_lines.append(line)

    return "\n".join(remaining_lines).strip()
