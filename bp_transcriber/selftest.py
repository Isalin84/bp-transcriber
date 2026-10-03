"""``--selftest``: проверка сборки без окна (импорты, ffmpeg, Silero VAD, GigaAM при наличии)."""

from __future__ import annotations

import importlib
import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, TextIO
from collections.abc import Callable

from . import __version__, paths

logger = logging.getLogger(__name__)

REQUIRED_MODULES = [
    "numpy", "torch", "torchaudio", "gigaam", "silero_vad", "sentencepiece", "omegaconf", "hydra",
    "huggingface_hub", "platformdirs", "webview", "bottle", "docx", "keyring",
]
OPTIONAL_MODULES = ["pyannote.audio", "speechbrain", "sklearn"]


def _check(checks: list[dict[str, Any]], name: str, func: Callable[[], Any], required: bool = True) -> Any:
    started = time.monotonic()
    entry: dict[str, Any] = {"name": name, "required": required}
    try:
        detail = func()
        entry["ok"] = True
        if detail is not None:
            entry["detail"] = detail
    except Exception as exc:  # noqa: BLE001
        entry["ok"] = False
        entry["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("selftest %s: %s", name, entry["error"])
    entry["time_s"] = round(time.monotonic() - started, 3)
    checks.append(entry)
    return entry["ok"]


def _find_ffmpeg() -> str:
    try:
        from gigaam_transcriber.audio_io import find_ffmpeg

        return find_ffmpeg()
    except ImportError:
        pass
    for name in ("ffmpeg.exe", "ffmpeg"):
        candidate = paths.bin_dir() / name
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError("ffmpeg не найден")
    return found


def _ffmpeg() -> str:
    exe = _find_ffmpeg()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, timeout=20, creationflags=flags)
    first = out.stdout.decode("utf-8", "replace").splitlines()[:1]
    return f"{exe}: {first[0] if first else 'ok'}"


def _vad() -> str:
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    speech = get_speech_timestamps(torch.zeros(16000), model, sampling_rate=16000)
    if speech:
        raise RuntimeError(f"VAD нашёл речь в тишине: {speech}")
    return "1 с тишины → 0 фрагментов речи"


def _device() -> Any:
    try:
        from gigaam_transcriber.device import available_options

        return available_options()
    except ImportError:
        import torch

        return {
            "cuda": torch.cuda.is_available(),
            "mps": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
        }


def _gigaam_root() -> tuple[Path, bool]:
    try:
        from gigaam_transcriber import model_store

        return Path(model_store.models_root()), bool(model_store.is_model_available())
    except ImportError:
        root = Path.home() / ".cache" / "gigaam"
        return root, (root / "v3_e2e_rnnt.ckpt").is_file()


def _gigaam(root: Path) -> str:
    import numpy as np
    import soundfile as sf

    import gigaam

    model = gigaam.load_model("v3_e2e_rnnt", device="cpu", download_root=str(root))
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "silence.wav"
        sf.write(wav, np.zeros(16000, dtype=np.float32), 16000)
        text = model.transcribe(str(wav))
    return f"модель загружена, распознавание 1 с тишины: {str(getattr(text, 'text', text))!r}"


def run(stdout: TextIO | None) -> int:
    checks: list[dict[str, Any]] = []
    for module in REQUIRED_MODULES:
        _check(checks, f"import:{module}", lambda m=module: getattr(importlib.import_module(m), "__version__", None))
    for module in OPTIONAL_MODULES:
        _check(checks, f"import:{module}", lambda m=module: getattr(importlib.import_module(m), "__version__", None),
               required=False)
    _check(checks, "ffmpeg", _ffmpeg)
    _check(checks, "silero_vad", _vad)
    _check(checks, "device", _device)
    root, available = _gigaam_root()
    if available:
        _check(checks, "gigaam", lambda: _gigaam(root), required=False)
    else:
        checks.append({"name": "gigaam", "required": False, "ok": None, "detail": "модель не скачана — пропущено"})

    ok = all(c["ok"] for c in checks if c["required"])
    report = {
        "app": "BP Transcriber",
        "version": __version__,
        "frozen": paths.is_frozen(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "ok": ok,
        "checks": checks,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    logger.info("selftest:\n%s", text)
    if stdout is not None:
        try:
            print(text, file=stdout, flush=True)
        except (OSError, ValueError):
            pass
    return 0 if ok else 1
