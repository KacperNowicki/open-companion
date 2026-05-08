from __future__ import annotations

import json
import math
import subprocess
import warnings

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional in stripped subprocess envs
    psutil = None

KV_BYTES_PER_TOKEN = 512  # conservative universal estimate for Q4 models
LAYER_FLOORS = {"companion": 8192, "assistant": 16384}
CEILING = 65536


def get_vram_total_bytes() -> tuple[int, int]:
    # Try NVIDIA via nvidia-ml-py (exposes the pynvml module name).
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")
            import pynvml  # type: ignore  # noqa: PLC0415

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.total), int(info.free)
    except Exception:
        pass

    # Try AMD via rocm-smi.
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                for card in data.values():
                    if not isinstance(card, dict):
                        continue
                    total = int(card.get("VRAM Total Memory (B)", 0) or 0)
                    used = int(card.get("VRAM Total Used Memory (B)", 0) or 0)
                    if total > 0:
                        return total, max(0, total - used)
    except Exception:
        pass

    # No GPU detected.
    return 0, 0


def prev_power_of_2(n: int) -> int:
    if n <= 0:
        return 0
    return 2 ** int(math.log2(n))


def calculate_optimal_ctx(model_vram_bytes: int, layer: str = "assistant") -> int:
    """
    Calculate optimal num_ctx based on available hardware.
    model_vram_bytes: size_vram from /api/ps for the loaded model
    layer: "companion" or "assistant"
    """
    total_vram, free_vram_raw = get_vram_total_bytes()

    # If we got total VRAM, calculate free more accurately.
    if total_vram > 0:
        free_vram = max(0, free_vram_raw - max(0, int(model_vram_bytes or 0)))
        # Use 75% of free VRAM for KV cache, leave 25% headroom for other apps.
        ctx_from_vram = int((free_vram * 0.75) / KV_BYTES_PER_TOKEN)
    else:
        ctx_from_vram = 0

    # RAM spill budget - 20% of free RAM only (PCIe spill kills inference speed).
    try:
        free_ram = int(psutil.virtual_memory().available) if psutil is not None else 0
    except Exception:
        free_ram = 0
    ctx_from_ram = int((free_ram * 0.20) / KV_BYTES_PER_TOKEN)

    ctx_candidate = ctx_from_vram + ctx_from_ram

    floor = LAYER_FLOORS.get(layer, 8192)

    # Clamp between floor and ceiling.
    ctx_clamped = max(floor, min(CEILING, ctx_candidate))

    # Round down to nearest power of 2.
    ctx_final = prev_power_of_2(ctx_clamped)
    ctx_final = max(floor, ctx_final)

    return ctx_final


__all__ = [
    "CEILING",
    "KV_BYTES_PER_TOKEN",
    "LAYER_FLOORS",
    "calculate_optimal_ctx",
    "get_vram_total_bytes",
    "prev_power_of_2",
]
