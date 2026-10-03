"""``--selftest``: проверка сборки без окна, сети и токена.

Проверяет импорты, встроенный ffmpeg (декодирование и предпрослушивание сгенерированного WAV),
Silero VAD, torch/MPS, импорт pyannote/speechbrain/sklearn, бэкенд keyring, пользовательские
каталоги кэшей (не внутри сборки) и GigaAM на CPU/MPS, если модель уже скачана.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, TextIO
from collections.abc import Callable

from . import __version__, paths

logger = logging.getLogger(__name__)

REQUIRED_MODULES = [
    "numpy", "torch", "torchaudio", "gigaam", "silero_vad", "sentencepiece", "omegaconf", "hydra",
    "huggingface_hub", "platformdirs", "webview", "bottle", "docx", "keyring",
]
# В исходниках диаризация — опциональный extra; во frozen-сборке она обязана быть внутри.
DIARIZATION_MODULES = ["pyannote.audio", "speechbrain", "sklearn"]

_SAMPLE_RATE = 16000


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


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _ffmpeg() -> dict[str, Any]:
    exe = _find_ffmpeg()
    bundled = _is_inside(Path(exe), paths.bin_dir())
    if paths.is_frozen() and not bundled:
        raise RuntimeError(f"используется не встроенный ffmpeg: {exe}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, timeout=20, creationflags=flags)
    first = out.stdout.decode("utf-8", "replace").splitlines()[:1]
    return {"path": exe, "bundled": bundled, "version": first[0] if first else "?"}


def _write_test_wav(path: Path, seconds: float = 1.0) -> None:
    """1 с тона 440 Гц, 16 кГц, моно, int16 — без внешних файлов."""
    import numpy as np

    t = np.arange(int(_SAMPLE_RATE * seconds)) / _SAMPLE_RATE
    pcm = (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())


def _decode() -> str:
    from gigaam_transcriber import audio_io

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tone.wav"
        _write_test_wav(wav)
        audio = audio_io.decode(wav)
        if abs(len(audio.pcm) - _SAMPLE_RATE) > _SAMPLE_RATE // 100:
            raise RuntimeError(f"ожидалось ~{_SAMPLE_RATE} сэмплов, получено {len(audio.pcm)}")
        preview = audio_io.encode_preview(audio, Path(tmp) / "preview.m4a")
        size = preview.stat().st_size
        if size < 1000:
            raise RuntimeError(f"предпрослушивание слишком мало: {size} Б")
    return f"WAV 1 с → {len(audio.pcm)} сэмплов; AAC-превью {size} Б"


def _vad() -> str:
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    speech = get_speech_timestamps(torch.zeros(_SAMPLE_RATE), model, sampling_rate=_SAMPLE_RATE)
    if speech:
        raise RuntimeError(f"VAD нашёл речь в тишине: {speech}")
    return "1 с тишины → 0 фрагментов речи"


def _mps_available() -> bool:
    import torch

    mps = getattr(torch.backends, "mps", None)
    return bool(mps and mps.is_available())


def _torch() -> dict[str, Any]:
    import torch

    info: dict[str, Any] = {"torch": torch.__version__, "threads": torch.get_num_threads()}
    x = torch.arange(6, dtype=torch.float32).reshape(2, 3)
    info["cpu_matmul"] = float((x @ x.T).sum())
    info["mps"] = _mps_available()
    if info["mps"]:
        y = x.to("mps")
        info["mps_matmul"] = float((y @ y.T).sum().cpu())
        if info["mps_matmul"] != info["cpu_matmul"]:
            raise RuntimeError(f"MPS посчитал иначе: {info['mps_matmul']} != {info['cpu_matmul']}")
    return info


def _device() -> Any:
    try:
        from gigaam_transcriber.device import available_options

        return available_options()
    except ImportError:
        return {"mps": _mps_available()}


def _pyannote() -> str:
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    from pyannote.audio import Pipeline  # noqa: F401
    from pyannote.audio.pipelines import SpeakerDiarization

    return f"{SpeakerDiarization.__module__}.{SpeakerDiarization.__name__}"


def _pyannote_pipeline() -> str | None:
    """Если модели pyannote уже в HF-кэше — загрузить пайплайн офлайн и прогнать 4 с тона."""
    from huggingface_hub import snapshot_download

    try:
        local = snapshot_download("pyannote/speaker-diarization-community-1", local_files_only=True)
    except Exception:  # noqa: BLE001 — нет в кэше
        return None
    import numpy as np
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(local)
    t = np.arange(_SAMPLE_RATE * 4) / _SAMPLE_RATE
    waveform = torch.from_numpy((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)).unsqueeze(0)
    output = pipeline({"waveform": waveform, "sample_rate": _SAMPLE_RATE})
    annotation = getattr(output, "speaker_diarization", output)
    return f"community-1 из кэша: {type(pipeline).__name__}, спикеров на тоне: {len(annotation.labels())}"


def _speechbrain() -> str:
    """Импорт EncoderClassifier и сборка ECAPA-TDNN + Fbank без весов (как в hparams spkrec-ecapa)."""
    import torch
    import speechbrain
    from speechbrain.inference.speaker import EncoderClassifier

    # Ленивые подмодули speechbrain (lazy_export_all) — частая поломка во frozen-сборках.
    features = speechbrain.lobes.features
    ecapa_module = importlib.import_module("speechbrain.lobes.models.ECAPA_TDNN")
    fbank = features.Fbank(n_mels=80)
    model = ecapa_module.ECAPA_TDNN(input_size=80, lin_neurons=192).eval()
    with torch.no_grad():
        emb = model(fbank(torch.zeros(1, _SAMPLE_RATE)))
    if tuple(emb.shape) != (1, 1, 192):
        raise RuntimeError(f"неожиданная форма эмбеддинга: {tuple(emb.shape)}")
    return f"{EncoderClassifier.__module__}.{EncoderClassifier.__name__}; ECAPA-TDNN → {tuple(emb.shape)}"


def _sklearn() -> str:
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    points = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 5.0], [5.1, 5.0]])
    labels = AgglomerativeClustering(n_clusters=2).fit_predict(points)
    if len(set(labels.tolist())) != 2:
        raise RuntimeError(f"кластеризация вернула {labels}")
    return "AgglomerativeClustering: 2 кластера"


def _keyring() -> str:
    import keyring

    backend = keyring.get_keyring()
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    if "fail" in name.lower() or "null" in name.lower():
        raise RuntimeError(f"нет рабочего хранилища секретов: {name}")
    return name


def _user_dirs() -> dict[str, str]:
    """Кэши моделей и пользовательские каталоги должны быть вне сборки."""
    found: dict[str, str] = {
        "config": str(paths.config_dir()),
        "data": str(paths.data_dir()),
        "cache": str(paths.cache_dir()),
        "logs": str(paths.log_dir()),
    }
    try:
        from huggingface_hub import constants

        found["hf_hub_cache"] = str(constants.HF_HUB_CACHE)
    except ImportError:
        pass
    try:
        import torch

        found["torch_hub"] = str(torch.hub.get_dir())
    except ImportError:
        pass
    try:
        from gigaam_transcriber import model_store

        found["gigaam"] = str(model_store.models_root())
    except ImportError:
        pass
    if paths.is_frozen():
        bundle = paths.resource_dir()
        inside = [k for k, v in found.items() if _is_inside(Path(v), bundle)]
        if inside:
            raise RuntimeError(f"каталоги внутри сборки: {inside}")
    return found


def _gigaam_root() -> tuple[Path, bool]:
    try:
        from gigaam_transcriber import model_store

        return Path(model_store.models_root()), bool(model_store.is_model_available())
    except ImportError:
        root = Path.home() / ".cache" / "gigaam"
        return root, (root / "v3_e2e_rnnt.ckpt").is_file()


def _gigaam(root: Path, device: str) -> str:
    import gigaam

    model = gigaam.load_model("v3_e2e_rnnt", device=device, download_root=str(root))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            _write_test_wav(wav)
            started = time.monotonic()
            text = model.transcribe(str(wav))
            took = time.monotonic() - started
    finally:
        del model
    return f"{device}: модель загружена, распознавание 1 с тона за {took:.2f} с: {str(getattr(text, 'text', text))!r}"


def run(stdout: TextIO | None) -> int:
    checks: list[dict[str, Any]] = []
    frozen = paths.is_frozen()
    for module in REQUIRED_MODULES:
        _check(checks, f"import:{module}", lambda m=module: getattr(importlib.import_module(m), "__version__", None))
    for module in DIARIZATION_MODULES:
        _check(checks, f"import:{module}", lambda m=module: getattr(importlib.import_module(m), "__version__", None),
               required=frozen)
    _check(checks, "ffmpeg", _ffmpeg)
    _check(checks, "decode", _decode)
    _check(checks, "silero_vad", _vad)
    _check(checks, "torch", _torch)
    _check(checks, "device", _device)
    _check(checks, "pyannote", _pyannote, required=frozen)
    _check(checks, "speechbrain", _speechbrain, required=frozen)
    _check(checks, "pyannote:pipeline", _pyannote_pipeline, required=False)
    _check(checks, "sklearn", _sklearn, required=frozen)
    _check(checks, "keyring", _keyring)
    _check(checks, "user_dirs", _user_dirs)
    root, available = _gigaam_root()
    if available:
        _check(checks, "gigaam:cpu", lambda: _gigaam(root, "cpu"), required=False)
        if _mps_available():
            _check(checks, "gigaam:mps", lambda: _gigaam(root, "mps"), required=False)
    else:
        checks.append({"name": "gigaam", "required": False, "ok": None, "detail": "модель не скачана — пропущено"})

    ok = all(c["ok"] for c in checks if c["required"])
    report = {
        "app": "BP Transcriber",
        "version": __version__,
        "frozen": frozen,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
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


def run_transcribe(path: str, diarization: str, stdout: TextIO | None) -> int:
    """Расшифровать файл настоящим пайплайном без окна (проверка собранного приложения).

    Токен HF берётся из настроек приложения (keyring), как при обычной работе.
    В JSON попадают только сводка и первые реплики — токен никогда не выводится.
    """
    from gigaam_transcriber.pipeline import PipelineOptions, TranscriptionPipeline

    from .settings import SettingsStore

    settings = SettingsStore()
    started = time.monotonic()
    report: dict[str, Any] = {"file": path, "diarization": diarization}
    pipeline = TranscriptionPipeline(device=settings.get().device, hf_token=settings.tokens.get())
    try:
        result = pipeline.run(path, PipelineOptions(diarization=diarization))
        report.update(
            ok=True,
            duration=round(result.duration, 2),
            wall_s=round(time.monotonic() - started, 2),
            device=result.metadata.get("device"),
            diarization_used=result.metadata.get("diarization"),
            speakers=result.get_speakers(),
            segments=len(result.segments),
            words=sum(len(s.words or []) for s in result.segments),
            sample=[f"[{s.start:.1f}] {s.speaker or '—'}: {s.text[:100]}" for s in result.segments[:5]],
            warnings=result.metadata.get("warnings", []),
        )
    except Exception as exc:  # noqa: BLE001 — итог проверки должен попасть в отчёт
        logger.exception("transcribe selftest failed")
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        pipeline.close()
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    logger.info("transcribe:\n%s", text)
    if stdout is not None:
        try:
            print(text, file=stdout, flush=True)
        except (OSError, ValueError):
            pass
    return 0 if report["ok"] else 1
