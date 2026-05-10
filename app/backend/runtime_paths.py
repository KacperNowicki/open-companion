from __future__ import annotations

import os
import sys
from pathlib import Path

# In a PyInstaller frozen build sys.frozen is set and __file__ resolves inside
# sys._MEIPASS (the temp extraction dir).  Shipped read-only assets live there.
# Persistent user data must go to the OS profile dir, never to %TEMP%.
_FROZEN = getattr(sys, "frozen", False)
_MEIPASS = Path(getattr(sys, "_MEIPASS", "")).resolve() if _FROZEN else None

def _default_project_root() -> str:
    """Return the directory that contains shipped read-only assets."""
    if _FROZEN and _MEIPASS:
        return str(_MEIPASS)
    return str(Path(__file__).resolve().parent.parent.parent)

def _default_profile_root() -> str:
    """Return the persistent per-user profile directory.

    In a frozen (packaged) build the profile must live under %APPDATA% so that
    config, memory and soul files survive across app updates.  In dev/test the
    project root is used as before so that existing tooling is unaffected.
    """
    if _FROZEN:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return str(Path(appdata) / "OpenCompanion")
    return _default_project_root()


PROJECT_ROOT = Path(
    os.environ.get(
        "OPEN_COMPANION_PROJECT_ROOT",
        _default_project_root(),
    )
).resolve()
TEST_MODE = os.environ.get("OPEN_COMPANION_TEST_MODE") == "1"
PROFILE_ROOT = Path(
    os.environ.get(
        "OPEN_COMPANION_PROFILE_DIR",
        os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR", _default_profile_root()),
    )
).resolve()

CONFIG_PATH = PROFILE_ROOT / "config.json"
LOCAL_CONFIG_PATH = PROFILE_ROOT / "config.local.json"
MEMORY_DIR = PROFILE_ROOT / "companion" / "memory"
SESSION_SUMMARIES_DIR = MEMORY_DIR / "session_summaries"
SOUL_DIR = PROFILE_ROOT / "companion" / "soul"
SOUL_ACTIVE_DIR = SOUL_DIR / "active"
RUNTIME_ASSETS_DIR = Path(
    os.environ.get("OPEN_COMPANION_RUNTIME_ASSETS_DIR", str(PROFILE_ROOT / "runtime-assets"))
).resolve()
KOKORO_DIR = RUNTIME_ASSETS_DIR / "kokoro"
LEGACY_MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_KOKORO_MODEL_PATH = KOKORO_DIR / "kokoro-v1.0.onnx"
DEFAULT_KOKORO_VOICES_PATH = KOKORO_DIR / "voices-v1.0.bin"
KOKORO_MODEL_PATH = Path(
    os.environ.get(
        "OPEN_COMPANION_KOKORO_MODEL_PATH",
        str((LEGACY_MODELS_DIR / "kokoro-v1.0.onnx") if (LEGACY_MODELS_DIR / "kokoro-v1.0.onnx").is_file() else DEFAULT_KOKORO_MODEL_PATH),
    )
).resolve()
KOKORO_VOICES_PATH = Path(
    os.environ.get(
        "OPEN_COMPANION_KOKORO_VOICES_PATH",
        str((LEGACY_MODELS_DIR / "voices-v1.0.bin") if (LEGACY_MODELS_DIR / "voices-v1.0.bin").is_file() else DEFAULT_KOKORO_VOICES_PATH),
    )
).resolve()
INSTALL_CACHE_DIR = PROFILE_ROOT / "install-cache"
LOGS_DIR = PROFILE_ROOT / "logs"

SOUL_DEFAULTS_DIR = PROJECT_ROOT / "companion" / "soul" / "defaults"
VAULT_DIR = PROFILE_ROOT / "companion" / "vault"

KEYCHAIN_SERVICE = os.environ.get("OPEN_COMPANION_KEYCHAIN_SERVICE", "open-companion")


def ensure_runtime_dirs() -> None:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    SOUL_DIR.mkdir(parents=True, exist_ok=True)
    SOUL_ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
