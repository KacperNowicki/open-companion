"""Ollama model lifecycle helpers used by wrapper.py."""

from __future__ import annotations

import json
import logging
import re
import subprocess

from wrapper_text import sanitize_text_for_utf8

logger = logging.getLogger("wrapper.ollama")


def set_model_keepalive(model: str, value) -> None:
    """Send a keep_alive heartbeat to Ollama for the given model. Best-effort."""
    model_name = str(model or "").strip()
    if not model_name:
        return
    try:
        from urllib import request as _request
        payload = json.dumps(
            {"model": model_name, "keep_alive": value, "prompt": ""},
            ensure_ascii=False,
        ).encode("utf-8")
        req = _request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass


def _validate_ollama_model_name(model: str) -> str:
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model name is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model_name):
        raise ValueError("model name contains unsupported characters")
    if ".." in model_name:
        raise ValueError("model name must not contain path traversal")
    return model_name


def _ollama_chunk_value(chunk, key: str, default=None):
    if isinstance(chunk, dict):
        return chunk.get(key, default)
    return getattr(chunk, key, default)


def _ollama_model_entry_name(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("model") or entry.get("name") or "").strip()
    return str(getattr(entry, "model", None) or getattr(entry, "name", None) or "").strip()


def _normalize_ollama_model_key(model: str) -> str:
    clean = str(model or "").strip().lower()
    if clean.endswith(":latest"):
        clean = clean[: -len(":latest")]
    return clean


def ollama_present_flags(requested_models: list[str], available_models: list[str]) -> dict[str, bool]:
    available_keys = {_normalize_ollama_model_key(model) for model in available_models if model}
    return {
        model: _normalize_ollama_model_key(model) in available_keys
        for model in requested_models
    }


def list_ollama_models() -> list[str]:
    """Return installed Ollama model names using the SDK when present, then HTTP."""
    try:
        import ollama as _ollama

        response = _ollama.list()
        models = getattr(response, "models", None)
        if models is None and isinstance(response, dict):
            models = response.get("models")
        names = [_ollama_model_entry_name(item) for item in (models or [])]
        return [name for name in names if name]
    except ImportError:
        pass
    except Exception:
        logger.debug("Ollama SDK model listing failed; falling back to /api/tags", exc_info=True)

    try:
        from urllib import request as _request

        with _request.urlopen("http://localhost:11434/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        names = [_ollama_model_entry_name(item) for item in (payload.get("models") or [])]
        return [name for name in names if name]
    except Exception:
        logger.exception("Failed to list Ollama models")
        return []


def ollama_pull_progress_payload(model: str, chunk, request_id: str | None = None) -> dict:
    status = str(_ollama_chunk_value(chunk, "status", "") or "").strip()
    digest = _ollama_chunk_value(chunk, "digest", None)
    total = _ollama_chunk_value(chunk, "total", None)
    completed = _ollama_chunk_value(chunk, "completed", None)

    try:
        total_number = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_number = 0
    try:
        completed_number = int(completed) if completed is not None else 0
    except (TypeError, ValueError):
        completed_number = 0

    percent = round((completed_number / total_number) * 100) if total_number > 0 else None
    payload = {
        "type": "pull_progress",
        "tag": model,
        "model": model,
        "status": status or "Pulling model...",
        "percent": percent,
        "completed": completed_number or None,
        "total": total_number or None,
        "completed_gb": f"{completed_number / 1e9:.1f}" if completed_number else None,
        "total_gb": f"{total_number / 1e9:.1f}" if total_number else None,
        "state": "pulling",
    }
    if digest:
        payload["digest"] = str(digest)
    if request_id:
        payload["request_id"] = request_id
    return payload


def pull_ollama_model(model: str, emit_progress, request_id: str | None = None) -> None:
    """Pull an Ollama model, preferring SDK streaming and falling back to CLI."""
    model_name = _validate_ollama_model_name(model)
    try:
        import ollama as _ollama
    except ImportError:
        _pull_ollama_model_subprocess(model_name, emit_progress, request_id=request_id)
        return

    for chunk in _ollama.pull(model_name, stream=True):
        emit_progress(ollama_pull_progress_payload(model_name, chunk, request_id=request_id))


def _pull_ollama_model_subprocess(model: str, emit_progress, request_id: str | None = None) -> None:
    emit_progress({
        "type": "pull_progress",
        "tag": model,
        "model": model,
        "status": "Starting Ollama pull...",
        "percent": None,
        "state": "pulling",
        **({"request_id": request_id} if request_id else {}),
    })
    process = subprocess.Popen(
        ["ollama", "pull", model, "--insecure"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    last_status = ""
    assert process.stdout is not None
    buffer = ""
    while True:
        char = process.stdout.read(1)
        if char == "" and process.poll() is not None:
            break
        if not char:
            continue
        if char not in {"\r", "\n"}:
            buffer += char
            continue
        status = sanitize_text_for_utf8(buffer).strip()
        buffer = ""
        if not status or status == last_status:
            continue
        last_status = status
        emit_progress({
            "type": "pull_progress",
            "tag": model,
            "model": model,
            "status": status,
            "percent": None,
            "state": "pulling",
            **({"request_id": request_id} if request_id else {}),
        })
    status = sanitize_text_for_utf8(buffer).strip()
    if status and status != last_status:
        emit_progress({
            "type": "pull_progress",
            "tag": model,
            "model": model,
            "status": status,
            "percent": None,
            "state": "pulling",
            **({"request_id": request_id} if request_id else {}),
        })
    exit_code = process.wait()
    if exit_code != 0:
        raise RuntimeError(f"ollama pull exited with code {exit_code}")
