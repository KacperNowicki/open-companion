"""Scheduler bootstrapping hooks used by wrapper.py."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path

from runtime_paths import PROFILE_ROOT

logger = logging.getLogger("wrapper.scheduler")

SCHEDULE_PATH = PROFILE_ROOT / "companion" / "schedule.md"
SCHEDULE_STARTER_CONTENT = """# Schedule

Add recurring reminders below. The companion can help you add entries.

<!-- Format: - [ ] Description | every PATTERN HH:MM -->
<!-- Examples:
- Weekly review: every Monday 09:00
- Birthday reminder: every year on Apr 21 10:00
- Quarterly tax payment: every 3 months on the 1st 09:00
-->
"""


def ensure_schedule_file() -> Path:
    """Create the minimal scheduler markdown file exactly once if it is missing."""
    try:
        SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not SCHEDULE_PATH.exists():
            SCHEDULE_PATH.write_text(SCHEDULE_STARTER_CONTENT, encoding="utf-8")
    except Exception:
        logger.exception("Failed to ensure starter schedule file at %s", SCHEDULE_PATH)
    return SCHEDULE_PATH


def load_scheduler_module():
    for module_name in ("app.backend.scheduler", "scheduler"):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError, AttributeError):
            spec = None
        if spec is None:
            continue
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.exception("Failed to import scheduler module %s", module_name)
            return None
    return None


def invoke_optional_scheduler_hook(module, method_names: tuple[str, ...], **available_args) -> bool:
    if module is None:
        return False

    for method_name in method_names:
        hook = getattr(module, method_name, None)
        if not callable(hook):
            continue

        try:
            signature = inspect.signature(hook)
            kwargs = {}
            for parameter in signature.parameters.values():
                if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if parameter.name in available_args:
                    kwargs[parameter.name] = available_args[parameter.name]
                    continue
                if parameter.default is inspect._empty:
                    raise TypeError(
                        f"Scheduler hook {method_name} requires unsupported parameter {parameter.name}"
                    )
            hook(**kwargs)
            return True
        except Exception:
            logger.exception("Scheduler hook %s failed", method_name)
            return False

    return False
