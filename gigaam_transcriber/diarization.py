"""
Диаризация спикеров и пословное сопоставление со спикерами.

Обеспечивает:
- pyannote: пайплайн speaker-diarization-community-1 (или 3.1), офлайн из
  HF-кэша в первую очередь, затем онлайн с токеном;
- hybrid: без токена — окна 1.5 с внутри речи, эмбеддинги WeSpeaker
  (или ECAPA SpeechBrain) и агломеративная кластеризация;
- проверку HF-токена и доступа к моделям;
- назначение спикера каждому слову, пересборку сегментов и переименование
  спикеров в «Спикер N».

Тяжёлые зависимости (torch, pyannote, speechbrain, sklearn) импортируются лениво.
"""

from __future__ import annotations

import bisect
import logging
import os
import re
import threading
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

import numpy as np

from .audio_io import SAMPLE_RATE, DecodedAudio
from .data_models import TranscriptionSegment, WordSegment
from .device import pick_device
from .exceptions import DiarizationError
from .progress import Cancelled, CancelToken

if TYPE_CHECKING:
    import torch

    from .asr_engine import AsrSegment

# До импорта pyannote / huggingface_hub: без телеметрии.
os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

logger = logging.getLogger(__name__)

PYANNOTE_REPOS = [
    "pyannote/speaker-diarization-community-1",
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
]
# Пайплайны в порядке предпочтения и репозитории, нужные каждому из них.
_PIPELINE_REPOS: dict[str, tuple[str, ...]] = {
    "pyannote/speaker-diarization-community-1": ("pyannote/speaker-diarization-community-1",),
    "pyannote/speaker-diarization-3.1": (
        "pyannote/speaker-diarization-3.1",
        "pyannote/segmentation-3.0",
    ),
}
_HF_URL = "https://huggingface.co"
_TOKENS_URL = f"{_HF_URL}/settings/tokens"

_WESPEAKER_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"
_ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
_APP_NAME = "BP Transcriber"
_APP_AUTHOR = "BestPractice"

_PYANNOTE_CPU_BATCH = 8
_PYANNOTE_GPU_BATCH = 32

# Доли прогресса шагов пайплайна pyannote.
_SEGMENTATION_SHARE = 0.30
_EMBEDDINGS_SHARE = 0.65

# Гибридная диаризация.
_HYBRID_WINDOW = 1.5
_HYBRID_HOP = 0.75
_HYBRID_MIN_WINDOW = 0.4  # более короткие речевые участки не эмбеддятся
# Пакет окон для эмбеддингов: на CPU крупные пакеты в разы медленнее (замер M4 Max,
# torch 2.11: WeSpeaker 7 мс/окно при 8 против 32 мс/окно при 32; ECAPA 8 окон
# 0.07 с, 32 окна 6.5 с), на GPU выгодны крупные.
_HYBRID_BATCH = {"cpu": 8, "gpu": 32}
_HYBRID_MAX_SPEAKERS = 8
# Порог косинусного расстояния для average linkage. Замер WeSpeaker на синтетических
# диалогах (scripts/bench.py, окна 1.5 с): один голос — p50 0.30 / p95 0.44,
# разные голоса — p5 0.59 / p50 0.71. Порог 0.6 — посередине с запасом в обе
# стороны; реальные разные голоса обычно ещё дальше (0.8+).
_HYBRID_THRESHOLD = {"wespeaker": 0.60, "ecapa": 0.60}
_HYBRID_MIN_CLUSTER_S = 3.0  # кластеры с меньшей суммарной речью присоединяются к ближайшему

# Сопоставление слов со спикерами.
_NEAREST_TURN_S = 0.5
# Сглаживание: короткая вставка другого спикера внутри фразы без пауз — почти
# всегда ошибка диаризации (у pyannote встречаются реплики по 40 мс).
_SMOOTH_MAX_RUN_S = 0.5
_SMOOTH_MAX_GAP_S = 0.25
_MIN_WORD_S = 0.01
_SENTENCE_END = ("." , "!", "?", "…")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?…:;)\]»])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[«])\s+")
_SPACES = re.compile(r"\s{2,}")

_PIPELINE_CACHE: dict[tuple[str, str], Any] = {}
_PIPELINE_LOCK = threading.Lock()
_EMBEDDER_CACHE: dict[str, "_Embedder"] = {}
_EMBEDDER_LOCK = threading.Lock()


# =============================================================================
# Типы
# =============================================================================


@dataclass
class SpeakerTurn:
    """Реплика спикера (секунды)."""

    start: float
    end: float
    speaker: str  # "SPEAKER_00", "SPEAKER_01", ...


@dataclass
class TokenCheck:
    """Результат проверки HF-токена."""

    ok: bool
    state: str  # "ok" | "missing" | "invalid" | "terms_not_accepted" | "network"
    user: str | None
    repos: list[dict[str, Any]] = field(default_factory=list)  # [{"id", "url", "ok": bool | None}], None = не проверялся
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Словарь для JSON (без токена)."""
        return asdict(self)


class PyannoteUnavailableError(DiarizationError):
    """Пайплайн pyannote не удалось загрузить (нет токена, условий, сети или моделей)."""

    def __init__(self, message: str, reason: str, cause: Exception | None = None) -> None:
        self.reason = reason  # "token" | "terms" | "network" | "other"
        super().__init__(message, cause)


# =============================================================================
# Hugging Face: токен и кэш
# =============================================================================


def _repo_url(repo: str) -> str:
    return f"{_HF_URL}/{repo}"


def _http_status(exc: Exception) -> int | None:
    """HTTP-код из исключения huggingface_hub, если он есть."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def check_token(token: str | None) -> TokenCheck:
    """
    Проверить HF-токен и доступ к моделям pyannote.

    Состояния: ok — пайплайн доступен (community-1 или 3.1 вместе с
    segmentation-3.0); terms_not_accepted — токен верный, но условия моделей
    не приняты; invalid — токен отклонён; network — нет связи; missing — токена нет.
    """
    repos: list[dict[str, Any]] = [{"id": repo, "url": _repo_url(repo), "ok": None} for repo in PYANNOTE_REPOS]
    if not token or not token.strip():
        return TokenCheck(False, "missing", None, repos, "Токен Hugging Face не задан.")

    from huggingface_hub import HfApi
    from huggingface_hub.errors import (
        GatedRepoError,
        HfHubHTTPError,
        RepositoryNotFoundError,
    )

    api = HfApi(token=token.strip())
    network_msg = "Нет соединения с huggingface.co. Проверьте интернет и повторите попытку."
    invalid_msg = f"Токен недействителен. Создайте новый токен с правом чтения: {_TOKENS_URL}"

    try:
        info = api.whoami()
    except HfHubHTTPError as exc:
        if _http_status(exc) in (401, 403):
            return TokenCheck(False, "invalid", None, repos, invalid_msg)
        logger.warning("Проверка токена: ошибка Hugging Face (HTTP %s)", _http_status(exc))
        return TokenCheck(False, "network", None, repos, network_msg)
    except Exception as exc:  # noqa: BLE001 - сеть/прокси/таймаут
        logger.warning("Проверка токена: нет связи (%s)", type(exc).__name__)
        return TokenCheck(False, "network", None, repos, network_msg)
    user = info.get("name") if isinstance(info, dict) else None

    for entry in repos:
        try:
            api.auth_check(entry["id"])
            entry["ok"] = True
        except GatedRepoError:
            entry["ok"] = False
        except RepositoryNotFoundError as exc:
            if _http_status(exc) == 401:
                return TokenCheck(False, "invalid", user, repos, invalid_msg)
            entry["ok"] = False
        except HfHubHTTPError as exc:
            if _http_status(exc) == 401:
                return TokenCheck(False, "invalid", user, repos, invalid_msg)
            if _http_status(exc) == 403:
                entry["ok"] = False
                continue
            logger.warning("Проверка доступа к %s: HTTP %s", entry["id"], _http_status(exc))
            return TokenCheck(False, "network", user, repos, network_msg)
        except Exception as exc:  # noqa: BLE001 - сеть
            logger.warning("Проверка доступа к %s: нет связи (%s)", entry["id"], type(exc).__name__)
            return TokenCheck(False, "network", user, repos, network_msg)

    accessible = {entry["id"] for entry in repos if entry["ok"]}
    if any(set(needed) <= accessible for needed in _PIPELINE_REPOS.values()):
        who = f" ({user})" if user else ""
        return TokenCheck(True, "ok", user, repos, f"Токен действителен{who}, модели диаризации доступны.")
    missing = ", ".join(entry["url"] for entry in repos if entry["ok"] is False)
    return TokenCheck(
        False,
        "terms_not_accepted",
        user,
        repos,
        "Токен действителен, но условия использования моделей не приняты. "
        f"Откройте страницы и нажмите «Agree and access repository»: {missing}",
    )


def _local_snapshot(repo: str) -> Path | None:
    """Каталог снапшота репозитория в локальном HF-кэше (без сети) или None."""
    from huggingface_hub import snapshot_download

    try:
        return Path(snapshot_download(repo, local_files_only=True))
    except Exception:  # noqa: BLE001 - нет в кэше / кэш повреждён
        return None


def _has_model_files(path: Path) -> bool:
    """Есть ли в каталоге веса модели pyannote или PLDA."""
    return (path / "pytorch_model.bin").is_file() or (path / "plda.npz").is_file()


def _resolve_reference(value: str, snapshot: Path) -> str | None:
    """
    Превратить ссылку из config.yaml (``$model/sub`` или ``org/repo``) в локальный путь.

    Returns:
        Путь к каталогу в кэше или None, если чего-то не хватает.
    """
    if value.startswith("$model/"):
        sub = value[len("$model/") :].split("@", 1)[0]
        path = snapshot / sub
        return str(path) if path.is_dir() and _has_model_files(path) else None
    if os.path.isdir(value):
        return value
    if "/" in value:
        local = _local_snapshot(value.split("@", 1)[0])
        if local is not None and _has_model_files(local):
            return str(local)
    return None


def _local_pipeline_config(repo: str) -> dict[str, Any] | None:
    """
    Конфиг пайплайна, у которого все модели лежат в локальном кэше.

    Ссылки на подмодели заменяются локальными путями, поэтому загрузка
    из такого конфига не обращается к сети.
    """
    import yaml

    snapshot = _local_snapshot(repo)
    if snapshot is None or not (snapshot / "config.yaml").is_file():
        return None
    try:
        config = yaml.safe_load((snapshot / "config.yaml").read_text(encoding="utf-8"))
        params = config["pipeline"]["params"]
    except Exception:  # noqa: BLE001 - битый конфиг = нет в кэше
        logger.warning("Некорректный config.yaml в кэше %s", repo)
        return None
    for key, value in list(params.items()):
        if isinstance(value, str) and (value.startswith("$model/") or key in ("segmentation", "embedding", "plda")):
            local = _resolve_reference(value, snapshot)
            if local is None:
                return None
            params[key] = local
    return config


def pyannote_cached() -> bool:
    """Лежат ли модели диаризации pyannote в локальном HF-кэше."""
    try:
        return any(_local_pipeline_config(repo) is not None for repo in _PIPELINE_REPOS)
    except Exception:  # noqa: BLE001 - нет huggingface_hub / yaml
        logger.debug("pyannote_cached: проверка кэша не удалась", exc_info=True)
        return False


# =============================================================================
# pyannote
# =============================================================================


def _classify_hub_error(exc: BaseException) -> str:
    """Причина сбоя загрузки: "token" | "terms" | "network" | "other"."""
    try:
        from huggingface_hub.errors import (
            GatedRepoError,
            HfHubHTTPError,
            LocalEntryNotFoundError,
            RepositoryNotFoundError,
        )
    except ImportError:
        return "other"
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, GatedRepoError):
            return "terms"
        if isinstance(current, RepositoryNotFoundError):
            return "token"
        if isinstance(current, HfHubHTTPError):
            status = _http_status(current)
            if status == 401:
                return "token"
            if status == 403:
                return "terms"
            return "network"
        if isinstance(current, LocalEntryNotFoundError):
            return "network"
        if isinstance(current, (ConnectionError, TimeoutError)):
            return "network"
        current = current.__cause__ or current.__context__
    return "other"


def _tune_batches(pipeline: Any, device: "torch.device") -> None:
    """
    Размеры пакетов под устройство.

    На CPU пакеты по 32 из config.yaml в разы медленнее (замер M4 Max, torch 2.11,
    диалог 163 с, 10 потоков: весь пайплайн 32 -> 176 с, 8 -> 17 с).
    На GPU оставляем значения из конфига.
    """
    size = _PYANNOTE_CPU_BATCH if device.type == "cpu" else _PYANNOTE_GPU_BATCH
    for attr in ("segmentation_batch_size", "embedding_batch_size"):
        if hasattr(pipeline, attr):
            try:
                setattr(pipeline, attr, size)
            except Exception:  # noqa: BLE001 - необязательная оптимизация
                logger.debug("pyannote: не удалось задать %s", attr, exc_info=True)


def _move_pipeline(pipeline: Any, device: "torch.device") -> "torch.device":
    """Перенести пайплайн на устройство; при ошибке — на CPU."""
    import torch

    actual = device
    if device.type != "cpu":
        try:
            pipeline.to(device)
        except Exception as exc:  # noqa: BLE001 - устройство не поддерживается
            logger.warning("pyannote не удалось перенести на %s (%s), используется CPU", device, exc)
            actual = torch.device("cpu")
    if actual.type == "cpu":
        pipeline.to(actual)
    _tune_batches(pipeline, actual)
    return actual


def _load_pipeline(token: str | None, device: "torch.device") -> tuple[str, Any]:
    """
    Загрузить пайплайн: для каждого репозитория сначала из кэша, затем онлайн.

    Raises:
        PyannoteUnavailableError: ни один вариант не загрузился
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from pyannote.audio import Pipeline

    reasons: list[str] = []
    last_exc: Exception | None = None
    for repo in _PIPELINE_REPOS:
        config = _local_pipeline_config(repo)
        if config is not None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    pipeline = Pipeline.from_pretrained(config)
                if pipeline is not None:
                    logger.info("pyannote: %s загружен из локального кэша", repo)
                    return repo, pipeline
            except Exception as exc:  # noqa: BLE001 - пробуем дальше
                logger.warning("pyannote: не удалось загрузить %s из кэша: %s", repo, exc)
                last_exc = exc
        if not token:
            reasons.append("token")
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pipeline = Pipeline.from_pretrained(repo, token=token)
            if pipeline is not None:
                logger.info("pyannote: %s загружен с Hugging Face", repo)
                return repo, pipeline
            reasons.append("terms")
        except Exception as exc:  # noqa: BLE001 - классифицируем ниже
            reason = _classify_hub_error(exc)
            logger.warning("pyannote: не удалось загрузить %s (%s): %s", repo, reason, type(exc).__name__)
            reasons.append(reason)
            last_exc = exc

    if "network" in reasons:
        reason, message = "network", "нет соединения с huggingface.co, а моделей pyannote нет в кэше"
    elif "terms" in reasons:
        reason, message = "terms", "не приняты условия использования моделей pyannote на Hugging Face"
    elif reasons and all(r == "token" for r in reasons):
        reason, message = "token", "нужен действительный токен Hugging Face для загрузки моделей pyannote"
    else:
        reason, message = "other", "не удалось загрузить модели pyannote"
    raise PyannoteUnavailableError(message, reason, last_exc)


def _get_pipeline(token: str | None, device: "torch.device") -> tuple[str, Any, "torch.device"]:
    """Пайплайн из кэша процесса или загруженный заново."""
    with _PIPELINE_LOCK:
        for repo in _PIPELINE_REPOS:
            cached = _PIPELINE_CACHE.get((repo, str(device)))
            if cached is not None:
                return repo, cached, device
        repo, pipeline = _load_pipeline(token, device)
        actual = _move_pipeline(pipeline, device)
        _PIPELINE_CACHE[(repo, str(actual))] = pipeline
        if actual != device:
            _PIPELINE_CACHE[(repo, str(device))] = pipeline
        return repo, pipeline, actual


def _annotation_to_turns(annotation: Any) -> list[SpeakerTurn]:
    """pyannote.core.Annotation -> отсортированные реплики."""
    turns = [
        SpeakerTurn(start=float(segment.start), end=float(segment.end), speaker=str(label))
        for segment, _, label in annotation.itertracks(yield_label=True)
        if segment.end > segment.start
    ]
    turns.sort(key=lambda t: (t.start, t.end))
    return turns


class PyannoteDiarizer:
    """Диаризация пайплайном pyannote (community-1, иначе 3.1)."""

    def __init__(self, token: str | None, device: str = "auto") -> None:
        """
        Args:
            token: HF-токен (нужен только для первой загрузки моделей)
            device: "auto" | "cpu" | "gpu"
        """
        self.token = token.strip() if token and token.strip() else None
        self.device_pref = device
        self.model_id: str | None = None
        self.device: "torch.device | None" = None

    def load(self) -> None:
        """Загрузить пайплайн заранее (иначе — при первом diarize)."""
        self.model_id, _, self.device = _get_pipeline(self.token, self._target_device())

    def _target_device(self) -> "torch.device":
        return pick_device(self.device_pref)

    def diarize(
        self,
        audio: DecodedAudio,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> list[SpeakerTurn]:
        """
        Разметить аудио по спикерам.

        Raises:
            PyannoteUnavailableError: пайплайн не загрузился
            DiarizationError: сбой во время диаризации
            Cancelled: отменено пользователем
        """
        if cancel is not None:
            cancel.raise_if_cancelled()
        repo, pipeline, device = _get_pipeline(self.token, self._target_device())
        self.model_id, self.device = repo, device

        def hook(step_name: str, step_artefact: Any = None, file: Any = None,
                 total: int | None = None, completed: int | None = None) -> None:
            if cancel is not None:
                cancel.raise_if_cancelled()
            if on_progress is None:
                return
            ratio = min(completed / total, 1.0) if total and completed is not None else 1.0
            if step_name == "segmentation":
                on_progress(_SEGMENTATION_SHARE * ratio)
            elif step_name == "speaker_counting":
                on_progress(_SEGMENTATION_SHARE)
            elif step_name == "embeddings":
                on_progress(_SEGMENTATION_SHARE + _EMBEDDINGS_SHARE * ratio)
            elif step_name == "discrete_diarization":
                on_progress(_SEGMENTATION_SHARE + _EMBEDDINGS_SHARE)

        kwargs: dict[str, Any] = {}
        if num_speakers:
            kwargs["num_speakers"] = num_speakers
        else:
            if min_speakers:
                kwargs["min_speakers"] = min_speakers
            if max_speakers:
                kwargs["max_speakers"] = max_speakers
        file = {"waveform": audio.torch_waveform(), "sample_rate": SAMPLE_RATE}

        try:
            output = self._apply(pipeline, file, hook, kwargs)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - пробуем CPU, затем ошибка
            if device.type == "cpu":
                logger.exception("Сбой диаризации pyannote")
                raise DiarizationError("сбой pyannote во время диаризации", exc) from exc
            logger.warning("pyannote упал на %s (%s), повтор на CPU", device, exc)
            import torch

            cpu = torch.device("cpu")
            with _PIPELINE_LOCK:
                _move_pipeline(pipeline, cpu)
                _PIPELINE_CACHE[(repo, str(device))] = pipeline
            self.device = cpu
            try:
                output = self._apply(pipeline, file, hook, kwargs)
            except Cancelled:
                raise
            except Exception as exc2:  # noqa: BLE001
                logger.exception("Сбой диаризации pyannote на CPU")
                raise DiarizationError("сбой pyannote во время диаризации", exc2) from exc2

        annotation = getattr(output, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(output, "speaker_diarization", output)
        turns = _annotation_to_turns(annotation)
        if on_progress:
            on_progress(1.0)
        return turns

    @staticmethod
    def _apply(pipeline: Any, file: dict[str, Any], hook: Callable[..., None], kwargs: dict[str, Any]) -> Any:
        """Вызов пайплайна без лишних предупреждений pyannote."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pipeline(file, hook=hook, **kwargs)


# =============================================================================
# Гибридная диаризация
# =============================================================================


class _Embedder:
    """Эмбеддинги голоса для окон фиксированной длины."""

    kind: str = ""
    device: "torch.device"

    def embed(self, batch: "torch.Tensor") -> np.ndarray:  # (B, n) -> (B, D)
        raise NotImplementedError


class _WeSpeakerEmbedder(_Embedder):
    """pyannote/wespeaker-voxceleb-resnet34-LM (без токена: репозиторий открыт)."""

    kind = "wespeaker"

    def __init__(self, device: "torch.device") -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pyannote.audio.pipelines.speaker_verification import (
                PyannoteAudioPretrainedSpeakerEmbedding,
            )

        local = _local_snapshot(_WESPEAKER_REPO)
        if local is not None and _has_model_files(local):
            source: str = str(local)
        else:
            from huggingface_hub import snapshot_download

            # Репозиторий не закрыт условиями: скачиваем анонимно.
            source = snapshot_download(_WESPEAKER_REPO, token=False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model = PyannoteAudioPretrainedSpeakerEmbedding(source, device=device)
        self.device = device

    def embed(self, batch: "torch.Tensor") -> np.ndarray:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.asarray(self._model(batch.unsqueeze(1)))


class _EcapaEmbedder(_Embedder):
    """speechbrain/spkrec-ecapa-voxceleb (запасной вариант)."""

    kind = "ecapa"

    def __init__(self, device: "torch.device") -> None:
        import platformdirs
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy

        import torch

        if device.type not in ("cpu", "cuda"):
            # speechbrain 1.1 не поддерживает run_opts device="mps" (нет device_type)
            device = torch.device("cpu")
        savedir = Path(platformdirs.user_cache_dir(_APP_NAME, _APP_AUTHOR)) / "speechbrain" / "spkrec-ecapa-voxceleb"
        self._model = EncoderClassifier.from_hparams(
            source=_ECAPA_REPO,
            savedir=str(savedir),
            run_opts={"device": str(device)},
            local_strategy=LocalStrategy.COPY,
        )
        self.device = device

    def embed(self, batch: "torch.Tensor") -> np.ndarray:
        import torch

        with torch.inference_mode():
            out = self._model.encode_batch(batch.to(self.device))
        return out.squeeze(1).cpu().numpy()


def _get_embedder(device: "torch.device") -> _Embedder:
    """Модель эмбеддингов (кэш на процесс): WeSpeaker, иначе ECAPA."""
    key = str(device)
    with _EMBEDDER_LOCK:
        cached = _EMBEDDER_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            embedder: _Embedder = _WeSpeakerEmbedder(device)
        except Exception as exc:  # noqa: BLE001 - запасной вариант
            logger.warning("WeSpeaker недоступен (%s), используется ECAPA SpeechBrain", exc)
            try:
                embedder = _EcapaEmbedder(device)
            except Exception as exc2:  # noqa: BLE001
                raise DiarizationError("не удалось загрузить модель голосовых эмбеддингов", exc2) from exc2
        _EMBEDDER_CACHE[key] = embedder
        return embedder


def _hybrid_windows(speech: Sequence[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    """
    Окна 1.5 с с шагом 0.75 с внутри речевых областей.

    Returns:
        (win_start, win_end, core_start, core_end): ядро окна — участок,
        за который оно «отвечает» (границы — середины между центрами соседних окон).
    """
    result: list[tuple[float, float, float, float]] = []
    for region_start, region_end in speech:
        length = region_end - region_start
        if length < _HYBRID_MIN_WINDOW:
            continue
        if length <= _HYBRID_WINDOW:
            result.append((region_start, region_end, region_start, region_end))
            continue
        starts: list[float] = []
        start = region_start
        while start + _HYBRID_WINDOW < region_end - 1e-6:
            starts.append(start)
            start += _HYBRID_HOP
        starts.append(region_end - _HYBRID_WINDOW)
        centers = [s + _HYBRID_WINDOW / 2 for s in starts]
        for k, win_start in enumerate(starts):
            core_start = region_start if k == 0 else (centers[k - 1] + centers[k]) / 2
            core_end = region_end if k == len(starts) - 1 else (centers[k] + centers[k + 1]) / 2
            result.append((win_start, win_start + _HYBRID_WINDOW, core_start, core_end))
    return result


def _cluster(
    embeddings: np.ndarray,
    durations: np.ndarray,
    num_speakers: int | None,
    threshold: float,
) -> np.ndarray:
    """
    Агломеративная кластеризация (cosine, average).

    Без num_speakers число кластеров определяется порогом и ограничивается
    1..8; кластеры с малой суммарной речью присоединяются к ближайшему.
    """
    from sklearn.cluster import AgglomerativeClustering

    count = len(embeddings)
    if count == 1:
        return np.zeros(1, dtype=int)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.maximum(norms, 1e-8)

    if num_speakers:
        clusters = min(num_speakers, count)
        return AgglomerativeClustering(n_clusters=clusters, metric="cosine", linkage="average").fit_predict(normalized)

    labels = AgglomerativeClustering(
        n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average"
    ).fit_predict(normalized)
    labels = _absorb_small_clusters(normalized, labels, durations)
    if len(set(labels.tolist())) > _HYBRID_MAX_SPEAKERS:
        labels = AgglomerativeClustering(
            n_clusters=_HYBRID_MAX_SPEAKERS, metric="cosine", linkage="average"
        ).fit_predict(normalized)
    return labels


def _absorb_small_clusters(normalized: np.ndarray, labels: np.ndarray, durations: np.ndarray) -> np.ndarray:
    """Присоединить кластеры с речью короче порога к ближайшему крупному центроиду."""
    unique = sorted(set(labels.tolist()))
    totals = {c: float(durations[labels == c].sum()) for c in unique}
    big = [c for c in unique if totals[c] >= _HYBRID_MIN_CLUSTER_S]
    if not big or len(big) == len(unique):
        return labels
    centroids = np.stack([normalized[labels == c].mean(axis=0) for c in big])
    centroids /= np.maximum(np.linalg.norm(centroids, axis=1, keepdims=True), 1e-8)
    result = labels.copy()
    for i in np.where(~np.isin(labels, big))[0]:
        result[i] = big[int(np.argmax(centroids @ normalized[i]))]
    return result


class HybridDiarizer:
    """Диаризация без токена: окна внутри речи + эмбеддинги + кластеризация."""

    def __init__(self, device: str = "auto") -> None:
        """
        Args:
            device: "auto" | "cpu" | "gpu"
        """
        self.device_pref = device
        self.model_kind: str | None = None

    def load(self) -> None:
        """Загрузить модель эмбеддингов заранее."""
        self.model_kind = _get_embedder(pick_device(self.device_pref)).kind

    def diarize(
        self,
        audio: DecodedAudio,
        speech: list[tuple[float, float]],
        *,
        num_speakers: int | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> list[SpeakerTurn]:
        """
        Разметить речевые области по спикерам.

        Args:
            audio: Декодированное аудио
            speech: Области речи из vad.detect_speech
            num_speakers: Число спикеров, если известно

        Raises:
            DiarizationError: не удалось загрузить модель эмбеддингов
            Cancelled: отменено пользователем
        """
        import torch

        windows = _hybrid_windows(speech)
        if not windows:
            return []
        device = pick_device(self.device_pref)
        embedder = _get_embedder(device)
        self.model_kind = embedder.kind

        # Окна одинаковой длины считаются пакетами; индексы сохраняются явно.
        # Границы окон выравниваются по отсчётам, чтобы окна 1.5 с были строго одной длины.
        by_length: dict[int, list[int]] = {}
        clips: list[np.ndarray] = []
        for i, (win_start, win_end, _, _) in enumerate(windows):
            first = int(round(win_start * SAMPLE_RATE))
            count = int(round((win_end - win_start) * SAMPLE_RATE))
            clip = audio.float32(first / SAMPLE_RATE, (first + count) / SAMPLE_RATE)
            clips.append(clip)
            by_length.setdefault(len(clip), []).append(i)
        batch_size = _HYBRID_BATCH["cpu" if embedder.device.type == "cpu" else "gpu"]

        embeddings: list[np.ndarray | None] = [None] * len(windows)
        done = 0
        for indices in by_length.values():
            for offset in range(0, len(indices), batch_size):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                part = indices[offset : offset + batch_size]
                batch = torch.from_numpy(np.stack([clips[i] for i in part]))
                vectors = embedder.embed(batch)
                for i, vector in zip(part, vectors):
                    embeddings[i] = vector
                done += len(part)
                if on_progress:
                    on_progress(0.95 * done / len(windows))

        matrix = np.stack([e for e in embeddings if e is not None])
        assert len(matrix) == len(windows)
        valid = np.isfinite(matrix).all(axis=1)
        if not valid.any():
            return []
        durations = np.array([w[3] - w[2] for w in windows])
        labels = np.full(len(windows), -1, dtype=int)
        labels[valid] = _cluster(
            matrix[valid], durations[valid], num_speakers, _HYBRID_THRESHOLD.get(embedder.kind, _HYBRID_THRESHOLD["wespeaker"])
        )
        # окна с невалидным эмбеддингом наследуют метку соседа
        for i in range(len(labels)):
            if labels[i] < 0:
                labels[i] = labels[i - 1] if i > 0 and labels[i - 1] >= 0 else int(labels[valid][0])

        # номера спикеров — в порядке первого появления
        order: dict[int, int] = {}
        for label in labels.tolist():
            order.setdefault(label, len(order))

        turns: list[SpeakerTurn] = []
        for (_, _, core_start, core_end), label in zip(windows, labels.tolist()):
            speaker = f"SPEAKER_{order[label]:02d}"
            if turns and turns[-1].speaker == speaker and core_start - turns[-1].end < 1e-6:
                turns[-1].end = core_end
            else:
                turns.append(SpeakerTurn(start=core_start, end=core_end, speaker=speaker))
        if on_progress:
            on_progress(1.0)
        return turns


def clear_caches() -> None:
    """Выгрузить закэшированные пайплайны pyannote и модели эмбеддингов."""
    with _PIPELINE_LOCK:
        _PIPELINE_CACHE.clear()
    with _EMBEDDER_LOCK:
        _EMBEDDER_CACHE.clear()


# =============================================================================
# Слова, сегменты, имена спикеров
# =============================================================================


def flatten_words(asr: Sequence["AsrSegment"]) -> list[WordSegment]:
    """
    Слова всех сегментов по порядку.

    Сегмент без слов (фолбэк модели) представлен одним псевдо-словом с его
    текстом и границами — так ``regroup`` и ``assign_words`` работают с ним как с единицей.
    """
    units: list[WordSegment] = []
    for segment in asr:
        if segment.words:
            units.extend(segment.words)
        else:
            units.append(WordSegment(word=segment.text, start=segment.start, end=segment.end))
    return units


def assign_words(words: Sequence[WordSegment], turns: Sequence[SpeakerTurn]) -> list[str | None]:
    """
    Спикер для каждого слова.

    Правило: реплика с максимальным перекрытием; без перекрытия — ближайшая
    реплика в пределах 0.5 с; иначе спикер предыдущего слова (для первых
    слов — следующего). Без реплик — все None.

    Затем сглаживание: серия слов другого спикера короче 0.5 с, вплотную
    (паузы < 0.25 с) окружённая словами одного спикера, получает его метку.
    """
    if not turns:
        return [None] * len(words)
    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    starts = [t.start for t in ordered]
    # префиксный максимум концов: позволяет остановить поиск назад
    max_end: list[float] = []
    argmax_end: list[int] = []
    for i, turn in enumerate(ordered):
        if not max_end or turn.end > max_end[-1]:
            max_end.append(turn.end)
            argmax_end.append(i)
        else:
            max_end.append(max_end[-1])
            argmax_end.append(argmax_end[-1])

    result: list[str | None] = []
    for word in words:
        w_start = word.start
        w_end = max(word.end, w_start + _MIN_WORD_S)
        hi = bisect.bisect_left(starts, w_end)  # реплики [0, hi) начинаются до конца слова
        best: str | None = None
        best_overlap = 0.0
        i = hi - 1
        while i >= 0 and max_end[i] > w_start:
            turn = ordered[i]
            overlap = min(turn.end, w_end) - max(turn.start, w_start)
            if overlap > best_overlap:
                best_overlap, best = overlap, turn.speaker
            i -= 1
        if best is None:
            candidates: list[tuple[float, str]] = []
            if hi < len(ordered):
                candidates.append((ordered[hi].start - w_end, ordered[hi].speaker))
            if hi > 0:
                prev = ordered[argmax_end[hi - 1]]
                candidates.append((w_start - prev.end, prev.speaker))
            near = [c for c in candidates if c[0] <= _NEAREST_TURN_S]
            if near:
                best = min(near, key=lambda c: c[0])[1]
        result.append(best)

    # слова без реплики рядом: спикер предыдущего (или следующего) слова
    last: str | None = None
    for i, speaker in enumerate(result):
        if speaker is None:
            result[i] = last
        else:
            last = speaker
    first_known = next((s for s in result if s is not None), None)
    for i, speaker in enumerate(result):
        if speaker is not None:
            break
        result[i] = first_known
    _smooth_runs(words, result)
    return result


def _smooth_runs(words: Sequence[WordSegment], speakers: list[str | None]) -> None:
    """Убрать короткие вставки другого спикера внутри непрерывной фразы (на месте)."""
    count = len(speakers)
    i = 0
    while i < count:
        j = i
        while j + 1 < count and speakers[j + 1] == speakers[i]:
            j += 1
        if 0 < i and j < count - 1:
            around = speakers[i - 1]
            if (
                around is not None
                and around == speakers[j + 1]
                and speakers[i] != around
                and words[j].end - words[i].start < _SMOOTH_MAX_RUN_S
                and words[i].start - words[i - 1].end < _SMOOTH_MAX_GAP_S
                and words[j + 1].start - words[j].end < _SMOOTH_MAX_GAP_S
            ):
                for k in range(i, j + 1):
                    speakers[k] = around
        i = j + 1


def _join_words(words: Sequence[str]) -> str:
    """Склеить слова пробелами без пробелов перед знаками препинания."""
    text = " ".join(w.strip() for w in words if w and w.strip())
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    return _SPACES.sub(" ", text).strip()


def regroup(
    asr: Sequence["AsrSegment"],
    speakers_per_word: Sequence[str | None] | None,
    *,
    max_gap: float = 1.0,
    soft_max: float = 20.0,
    hard_max: float = 40.0,
) -> list[TranscriptionSegment]:
    """
    Пересобрать сегменты из слов.

    Новый сегмент начинается при смене спикера, паузе больше max_gap,
    после слова с . ! ? … если сегмент длиннее soft_max, и принудительно —
    если сегмент стал бы длиннее hard_max. Сегмент ASR без слов остаётся
    отдельным сегментом.

    Args:
        asr: Результат GigaAMEngine.transcribe
        speakers_per_word: Спикеры для ``flatten_words(asr)`` или None (без диаризации)
    """
    units: list[tuple[WordSegment, bool]] = []
    for segment in asr:
        if segment.words:
            units.extend((w, True) for w in segment.words)
        else:
            units.append((WordSegment(word=segment.text, start=segment.start, end=segment.end), False))
    if speakers_per_word is not None and len(speakers_per_word) != len(units):
        raise ValueError(
            f"speakers_per_word: ожидалось {len(units)} значений, получено {len(speakers_per_word)}"
        )

    segments: list[TranscriptionSegment] = []
    current: list[WordSegment] = []
    current_speaker: str | None = None

    def close() -> None:
        nonlocal current
        if current:
            text = _join_words([w.word for w in current])
            if text:
                segments.append(
                    TranscriptionSegment(
                        text=text,
                        start=current[0].start,
                        end=current[-1].end,
                        speaker=current_speaker,
                        words=list(current),
                    )
                )
        current = []

    for index, (word, is_word) in enumerate(units):
        speaker = speakers_per_word[index] if speakers_per_word is not None else None
        if not is_word:
            close()
            text = word.word.strip()
            if text:
                segments.append(
                    TranscriptionSegment(text=text, start=word.start, end=word.end, speaker=speaker, words=[])
                )
            current_speaker = speaker
            continue
        if current and (
            speaker != current_speaker
            or word.start - current[-1].end > max_gap
            or word.end - current[0].start > hard_max
        ):
            close()
        if not current:
            current_speaker = speaker
        current.append(word)
        if word.end - current[0].start > soft_max and word.word.rstrip().endswith(_SENTENCE_END):
            close()
    close()
    return segments


def relabel(segments: Sequence[TranscriptionSegment]) -> None:
    """Переименовать спикеров в «Спикер 1», «Спикер 2»... по порядку первого появления."""
    mapping: dict[str, str] = {}
    for segment in segments:
        if segment.speaker is None:
            continue
        if segment.speaker not in mapping:
            mapping[segment.speaker] = f"Спикер {len(mapping) + 1}"
        segment.speaker = mapping[segment.speaker]
