#!/usr/bin/env python3
from __future__ import annotations

import re

from tests.companion.conftest import Companion, companion_profile_root, companion_session, memory_snapshot, wait_until


def _ask(companion: Companion, prompt: str) -> str:
    reply = companion.say(prompt, timeout=300)
    assert reply.strip(), f"empty reply for: {prompt}"
    return reply


def test_remembers_name() -> None:
    with companion_session(timeout=240) as companion:
        _ask(companion, "Please remember exactly this: my name is Alex.")
        _ask(companion, "We can talk about something else for a moment. What is a nice coffee order?")
        reply = _ask(companion, "What is my name?")
        assert "alex" in reply.lower(), reply


def test_remembers_preference() -> None:
    with companion_session(timeout=240) as companion:
        _ask(companion, "Remember that I hate mornings and I love coffee.")
        _ask(companion, "Tell me a short fun fact.")
        reply = _ask(companion, "What do you remember about my preferences?")
        lowered = reply.lower()
        assert "coffee" in lowered or "morning" in lowered, reply


def test_memory_persists_across_sessions() -> None:
    with companion_session(timeout=240) as companion:
        _ask(companion, "Remember that my favorite color is green.")
        profile_root = companion_profile_root(companion)
        persisted = wait_until(lambda: "green" in memory_snapshot(profile_root).lower(), timeout=20, interval=0.5)
        assert persisted, memory_snapshot(profile_root)
        companion.reset()
        reply = _ask(companion, "What is my favorite color?")
        assert "green" in reply.lower(), reply


def test_corrects_memory() -> None:
    with companion_session(timeout=240) as companion:
        _ask(companion, "Remember this: I work at Google.")
        _ask(companion, "Actually, correction: I work at Microsoft now.")
        reply = _ask(companion, "Where do I work?")
        lowered = reply.lower()
        assert "microsoft" in lowered, reply
        assert "you work at google" not in lowered and "currently at google" not in lowered, reply


def test_memory_not_hallucinated() -> None:
    with companion_session(timeout=240) as companion:
        reply = _ask(companion, "Without guessing or making one up, what is my name? If you do not know, say you do not know.")
        lowered = reply.lower()
        uncertainty_markers = ["don't know", "do not know", "not sure", "haven't told", "have not told", "you haven't"]
        if any(marker in lowered for marker in uncertainty_markers):
            return
        match = re.search(r"your name is\s+([a-z]+)", lowered)
        assert not match, reply
