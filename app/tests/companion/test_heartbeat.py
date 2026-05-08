#!/usr/bin/env python3
from __future__ import annotations

import time

from tests.companion.conftest import companion_session


# slow: waits for a live heartbeat from the real backend

def test_heartbeat_fires() -> None:
    config_patch = {
        "heartbeat": {
            "enabled": True,
            "interval": 5,
            "only_when_idle": False,
        }
    }
    with companion_session(config_patch=config_patch, timeout=180) as companion:
        signal = companion.wait_for_heartbeat(timeout=20)
        assert signal, "heartbeat did not fire"


# slow: keeps the conversation active long enough to verify suppression

def test_heartbeat_suppressed_during_conversation() -> None:
    config_patch = {
        "heartbeat": {
            "enabled": True,
            "interval": 5,
            "only_when_idle": False,
        }
    }
    with companion_session(config_patch=config_patch, timeout=180) as companion:
        for _ in range(4):
            reply = companion.say("Stay with me in the conversation for a moment.", timeout=180)
            assert reply.strip(), "companion returned an empty reply while heartbeat suppression was being tested"
            unexpected = companion.wait_for_heartbeat(timeout=2)
            assert unexpected is None, f"heartbeat should stay quiet during active conversation: {unexpected}"
            time.sleep(1)


# slow: waits through a no-heartbeat window to ensure disabled mode stays quiet

def test_heartbeat_disabled() -> None:
    config_patch = {
        "heartbeat": {"enabled": False}
    }
    with companion_session(config_patch=config_patch, timeout=180) as companion:
        signal = companion.wait_for_heartbeat(timeout=10)
        assert signal is None, f"heartbeat emitted despite being disabled: {signal}"
