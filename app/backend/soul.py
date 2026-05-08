"""
Soul System
===========
Manages companion/soul/defaults/ (shipped templates, never modified) and companion/soul/active/
(user-owned runtime files, gitignored). On startup, missing active files are
copied from defaults exactly once and then left alone.

Call ensure_soul_active(config) from wrapper.py init before LayerSession
construction so that brain.load_identity always finds populated active files.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("soul")
from runtime_paths import SOUL_ACTIVE_DIR, SOUL_DEFAULTS_DIR, SOUL_DIR, ensure_runtime_dirs

# All files that should exist in companion/soul/active/.
SOUL_FILES = [
    "shared_runtime_contract.md",
    "soul_companion.md",
    "soul_assistant.md",
]

DEPRECATED_SOUL_FILES = [
    "system.md",
    "system_worker.md",
    "companion.md",
    "assistant.md",
    "soul_pc_doctor.md",
    "soul_default.md",
    "pc_doctor.md",
]

ROOT_ONLY_DEPRECATED_SOUL_FILES = [
    "soul_companion.md",
    "soul_assistant.md",
]

def _copy_default_soul_file(filename: str) -> bool:
    default_path = SOUL_DEFAULTS_DIR / filename
    active_path = SOUL_ACTIVE_DIR / filename
    if not default_path.exists():
        logger.warning("[soul] Default template missing: %s - skipping", filename)
        return False
    if active_path.exists():
        return False
    active_path.write_text(default_path.read_text(encoding="utf-8"), encoding="utf-8")
    logger.debug("[soul] Copied default soul file %s", filename)
    return True


def _cleanup_deprecated_soul_files() -> None:
    for filename in DEPRECATED_SOUL_FILES:
        for directory in (SOUL_ACTIVE_DIR, SOUL_DIR):
            try:
                deprecated_path = directory / filename
                if deprecated_path.exists():
                    deprecated_path.unlink()
            except Exception:
                logger.exception("[soul] Failed to remove deprecated file %s", deprecated_path)
    for filename in ROOT_ONLY_DEPRECATED_SOUL_FILES:
        try:
            deprecated_path = SOUL_DIR / filename
            if deprecated_path.exists():
                deprecated_path.unlink()
        except Exception:
            logger.exception("[soul] Failed to remove deprecated root file %s", deprecated_path)


def generate_soul_files(
    config: dict,
    skip_existing: set[str] | list[str] | tuple[str, ...] | None = None,
) -> None:
    """
    Copy missing defaults from companion/soul/defaults/ into companion/soul/active/.
    Existing active files are always preserved because they are user-owned.
    The config argument is retained for API compatibility with older callers.
    """
    ensure_runtime_dirs()
    skip_existing = set(skip_existing or set())
    _cleanup_deprecated_soul_files()

    for filename in SOUL_FILES:
        try:
            active_path = SOUL_ACTIVE_DIR / filename
            if filename in skip_existing and active_path.exists():
                logger.debug("[soul] Preserving existing active file: %s", filename)
                continue
            _copy_default_soul_file(filename)
        except Exception:
            logger.exception("[soul] Failed to generate %s", filename)


def ensure_soul_active(config: dict) -> None:
    """
    Called at startup. Copy only the missing active soul files from defaults.
    Never overwrite an existing file in companion/soul/active/.
    """
    ensure_runtime_dirs()
    _cleanup_deprecated_soul_files()

    missing = [filename for filename in SOUL_FILES if not (SOUL_ACTIVE_DIR / filename).exists()]

    if missing:
        logger.info("[soul] Missing active soul files: %s - copying defaults", missing)
        generate_soul_files(config)
    else:
        logger.debug("[soul] companion/soul/active/ is complete - skipping generation")
