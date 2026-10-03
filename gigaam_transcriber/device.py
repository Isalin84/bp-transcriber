"""
Выбор устройства для вычислений (CUDA > MPS > CPU).

torch импортируется лениво, чтобы быстро запускать окно приложения.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)


def _import_torch() -> Any:
    """Ленивый импорт torch с включением CPU-фолбэка для MPS."""
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import torch

    return torch


def _gpu_kind(torch: Any) -> str | None:
    """Вернуть тип доступного ускорителя: ``"cuda"``, ``"mps"`` или None."""
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return None


def pick_device(pref: str = "auto") -> "torch.device":
    """
    Выбрать устройство.

    Args:
        pref: "auto" | "cpu" | "gpu". Для "auto" и "gpu": cuda > mps > cpu.

    Returns:
        torch.device
    """
    torch = _import_torch()
    if pref == "cpu":
        return torch.device("cpu")
    kind = _gpu_kind(torch)
    if kind is None:
        if pref == "gpu":
            logger.warning("GPU недоступен, используется CPU")
        return torch.device("cpu")
    return torch.device(kind)


def device_label(dev: "torch.device | str") -> str:
    """Человекочитаемое название устройства."""
    device_type = getattr(dev, "type", str(dev)).split(":")[0]
    if device_type == "cuda":
        torch = _import_torch()
        index = getattr(dev, "index", None)
        try:
            name = torch.cuda.get_device_name(index if index is not None else 0)
        except Exception:  # noqa: BLE001 - название не критично
            return "NVIDIA GPU"
        return f"NVIDIA {name.removeprefix('NVIDIA ').strip()}"
    if device_type == "mps":
        return "Apple GPU (MPS)"
    return "CPU"


def available_options() -> list[dict[str, Any]]:
    """
    Варианты выбора устройства для настроек UI.

    Returns:
        Список ``{"id", "label", "available"}`` для "auto", "cpu", "gpu".
    """
    torch = _import_torch()
    kind = _gpu_kind(torch)
    gpu_label = device_label(torch.device(kind)) if kind else "GPU недоступен"
    auto_label = f"Авто ({gpu_label if kind else 'CPU'})"
    return [
        {"id": "auto", "label": auto_label, "available": True},
        {"id": "cpu", "label": "CPU", "available": True},
        {"id": "gpu", "label": gpu_label, "available": kind is not None},
    ]
