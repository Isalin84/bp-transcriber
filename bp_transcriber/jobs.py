"""Очередь транскрипции и загрузка моделей.

Модель потоков JobQueue:
* один поток-воркер обрабатывает задачи строго по очереди (GPU/модель одна);
* API-потоки pywebview вызывают enqueue/cancel/remove/list;
* всё состояние задач (``_jobs``, ``_current``) защищено ``_cond`` (RLock внутри);
* события публикуются в шину ПОД той же блокировкой, сразу после изменения состояния —
  так порядок событий в шине всегда совпадает с порядком переходов состояния
  (``EventBus.post`` не блокируется и не вызывает код очереди, deadlock невозможен);
* тяжёлая работа (пайплайн, экспорт) идёт вне блокировки.
Отмена выполняемой задачи — через CancelToken пайплайна; терминальный статус
выставляет только воркер.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable, Mapping

from .exporters import default_filename, export, unique_path
from .history import HistoryStore, PendingEntry
from .settings import DIARIZATION_MODES, SettingsError, SettingsStore, validate_num_speakers

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "v3_e2e_rnnt"
MAX_FILES_PER_ENQUEUE = 1000

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wma",
    ".aif", ".aiff", ".aifc", ".caf", ".amr", ".3gp", ".3ga", ".webm", ".mka", ".ac3",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts",
    ".m2ts", ".3g2", ".ogv", ".vob",
}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


@dataclass
class Job:
    id: str
    file_name: str
    path: str
    options: dict[str, Any]
    status: str = "queued"
    stage: str | None = None
    progress: float = 0.0
    stage_progress: float = 0.0
    message: str = "В очереди"
    eta_s: float | None = None
    created_at: float = field(default_factory=lambda: round(time.time(), 3))
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    transcript_id: str | None = None
    duration: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineBackend:
    pipeline_cls: Any
    options_cls: Any
    cancel_token_cls: Any
    cancelled_exc: type[BaseException]
    is_fake: bool


def load_backend(force_fake: bool | None = None) -> PipelineBackend:
    """Реальный пайплайн, либо FakePipeline (``BP_FAKE_PIPELINE=1`` или ошибка импорта)."""
    fake = os.environ.get("BP_FAKE_PIPELINE") == "1" if force_fake is None else force_fake
    if not fake:
        try:
            from gigaam_transcriber.pipeline import PipelineOptions, TranscriptionPipeline
            from gigaam_transcriber.progress import CancelToken, Cancelled

            return PipelineBackend(TranscriptionPipeline, PipelineOptions, CancelToken, Cancelled, False)
        except Exception as exc:  # noqa: BLE001 — любой сбой импорта → имитация
            logger.warning("Пайплайн GigaAM недоступен (%s: %s) — используется имитация", type(exc).__name__, exc)
    from . import fake_pipeline as fp

    return PipelineBackend(fp.FakePipeline, fp.PipelineOptions, fp.CancelToken, fp.Cancelled, True)


def probe_duration(path: str) -> float | None:
    """Длительность медиафайла (секунды) через ffmpeg; None, если неизвестна."""
    try:
        from gigaam_transcriber.audio_io import probe_duration as core_probe

        value = core_probe(path)
    except Exception as exc:  # noqa: BLE001 — нет ffmpeg/модуля или битый файл
        logger.debug("probe_duration %s: %s", path, exc)
        return None
    return float(value) if value else None


def error_message(exc: BaseException) -> str:
    """Понятное пользователю сообщение (по-русски)."""
    from gigaam_transcriber.exceptions import TranscriberError

    if isinstance(exc, TranscriberError):
        return str(exc) or "Ошибка транскрипции"
    if isinstance(exc, MemoryError):
        return "Недостаточно памяти для обработки файла"
    if isinstance(exc, FileNotFoundError):
        return f"Файл не найден: {exc.filename or exc}"
    if isinstance(exc, PermissionError):
        return f"Нет доступа к файлу: {exc.filename or exc}"
    return f"Непредвиденная ошибка: {exc}"


def _is_cancel(exc: BaseException, token: Any, cancelled_exc: type[BaseException] | None) -> bool:
    if cancelled_exc is not None and isinstance(exc, cancelled_exc):
        return True
    if type(exc).__name__ == "Cancelled":
        return True
    return bool(token is not None and getattr(token, "cancelled", False))


# --- загрузка моделей -------------------------------------------------------------------


class ModelDownloads:
    """Фоновая загрузка GigaAM через ``model_store.ensure_model`` (не более одной)."""

    def __init__(self, bus: Any, model_name: str = DEFAULT_MODEL):
        self._bus = bus
        self._model_name = model_name
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._thread: threading.Thread | None = None
        self._token: Any = None
        self._state = {"downloading": False, "progress": 0.0, "downloaded": 0, "total": 0}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def is_downloading(self) -> bool:
        return not self._idle.is_set()

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def start(self) -> bool:
        with self._lock:
            if self._state["downloading"]:
                return False
            try:
                from gigaam_transcriber import model_store

                from .fake_pipeline import CancelToken  # реальный из progress, если он есть
            except Exception as exc:  # noqa: BLE001
                logger.error("model_store недоступен: %s", exc)
                self._bus.post(
                    {
                        "type": "model_download",
                        "status": "error",
                        "progress": 0.0,
                        "downloaded": 0,
                        "total": 0,
                        "message": "Модуль загрузки моделей недоступен в этой сборке",
                    }
                )
                return False
            self._token = CancelToken()
            self._state = {"downloading": True, "progress": 0.0, "downloaded": 0, "total": 0}
            self._idle.clear()
            self._thread = threading.Thread(
                target=self._run, args=(model_store, self._token), name="bp-model-download", daemon=True
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        with self._lock:
            if self._token is not None:
                self._token.cancel()

    def _event(self, status: str, message: str | None = None) -> dict[str, Any]:
        event = {"type": "model_download", "status": status, **{k: self._state[k] for k in ("progress", "downloaded", "total")}}
        if message:
            event["message"] = message
        return event

    def _run(self, model_store: Any, token: Any) -> None:
        def on_progress(downloaded: int, total: int) -> None:
            with self._lock:
                self._state.update(
                    downloaded=int(downloaded),
                    total=int(total),
                    progress=(downloaded / total) if total else 0.0,
                )
                self._bus.post(self._event("downloading"))

        try:
            self._bus.post(self._event("downloading"))
            model_store.ensure_model(self._model_name, on_progress=on_progress, cancel=token)
            with self._lock:
                self._state.update(downloading=False, progress=1.0)
                self._bus.post(self._event("done", "Модель загружена"))
        except Exception as exc:  # noqa: BLE001
            cancelled = _is_cancel(exc, token, None)
            if not cancelled:
                logger.exception("Ошибка загрузки модели")
            with self._lock:
                self._state["downloading"] = False
                if not cancelled:
                    self._bus.post(self._event("error", f"Не удалось загрузить модель: {error_message(exc)}"))
        finally:
            with self._lock:
                self._state["downloading"] = False
                self._token = None
            self._idle.set()


# --- очередь задач ----------------------------------------------------------------------


class JobQueue:
    def __init__(
        self,
        settings: SettingsStore,
        history: HistoryStore,
        bus: Any,
        *,
        downloads: ModelDownloads | None = None,
        backend_loader: Callable[[], PipelineBackend] = load_backend,
        pipeline_kwargs: Mapping[str, Any] | None = None,
        probe: Callable[[str], float | None] | None = probe_duration,
    ):
        self._settings = settings
        self._history = history
        self._bus = bus
        self._downloads = downloads
        self._backend_loader = backend_loader
        self._pipeline_kwargs = dict(pipeline_kwargs or {})
        self._probe = probe
        self._cond = threading.Condition(threading.RLock())
        self._jobs: dict[str, Job] = {}
        self._current: tuple[str, Any] | None = None  # (job_id, cancel_token)
        self._stopping = False
        self._worker: threading.Thread | None = None
        # Используются только воркером:
        self._backend: PipelineBackend | None = None
        self._pipeline: Any = None
        self._applied: tuple[str, str | None] | None = None

    @property
    def backend_is_fake(self) -> bool | None:
        return None if self._backend is None else self._backend.is_fake

    # --- публичные операции -------------------------------------------------------------

    def enqueue(self, paths: Iterable[str], options: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        job_options = self._resolve_options(options or {})
        files, skipped = self._expand(paths)
        created: list[dict[str, Any]] = []
        with self._cond:
            if self._stopping:
                return []
            active = {j.path for j in self._jobs.values() if j.status in ("queued", "running")}
            for path in files:
                if str(path) in active:
                    skipped.append((path.name, "уже в очереди"))
                    continue
                job = Job(id=uuid.uuid4().hex[:12], file_name=path.name, path=str(path), options=dict(job_options))
                self._jobs[job.id] = job
                active.add(job.path)
                created.append(job.to_dict())
                self._post_update(job)
            if created:
                self._ensure_worker()
                self._cond.notify_all()
        if created and self._probe is not None:
            # Длительность — в фоне: ffmpeg на каждый файл не должен тормозить enqueue.
            threading.Thread(
                target=self._probe_durations, args=([j["id"] for j in created],),
                name="bp-probe", daemon=True,
            ).start()
        if skipped:
            names = ", ".join(f"{n} ({why})" for n, why in skipped[:3])
            more = f" и ещё {len(skipped) - 3}" if len(skipped) > 3 else ""
            self._bus.post({"type": "toast", "level": "warning", "message": f"Пропущено: {names}{more}"})
        return created

    def list(self) -> list[dict[str, Any]]:
        with self._cond:
            return [j.to_dict() for j in self._jobs.values()]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._cond:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status == "queued":
                self._finish(job, "cancelled")
                return True
            if job.status == "running" and self._current and self._current[0] == job_id:
                self._current[1].cancel()
                job.message = "Отмена…"
                job.eta_s = None
                self._post_update(job)
                return True
            return False

    def remove(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status == "running":
                return False
            del self._jobs[job_id]
            return True

    def cancel_all(self) -> None:
        """Не блокирует: отменяет текущую и все ожидающие задачи (закрытие окна)."""
        with self._cond:
            for job in list(self._jobs.values()):
                if job.status == "queued":
                    self._finish(job, "cancelled")
            if self._current is not None:
                self._current[1].cancel()
        if self._downloads is not None:
            self._downloads.cancel()

    def shutdown(self, timeout: float = 5.0) -> None:
        self.cancel_all()
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)
            if worker.is_alive():
                logger.warning("Воркер задач не завершился за %.1f с", timeout)

    # --- внутреннее: подготовка -----------------------------------------------------

    def _resolve_options(self, options: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(options, Mapping):
            raise SettingsError("Параметры задачи заданы некорректно")
        settings = self._settings.get()
        diarization = options.get("diarization") or settings.diarization
        if diarization not in DIARIZATION_MODES:
            raise SettingsError(f"Неизвестный режим диаризации: {diarization}")
        num = options["num_speakers"] if "num_speakers" in options else settings.num_speakers
        return {"diarization": diarization, "num_speakers": validate_num_speakers(num)}

    @staticmethod
    def _expand(paths: Iterable[str]) -> tuple[list[Path], list[tuple[str, str]]]:
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        files: list[Path] = []
        skipped: list[tuple[str, str]] = []
        for raw in paths:
            if not raw:
                continue
            path = Path(raw).expanduser()
            try:
                path = path.resolve()
            except OSError:
                pass
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and is_media_file(p))
                if not found:
                    skipped.append((path.name, "нет медиафайлов"))
                files.extend(found)
            elif not path.is_file():
                skipped.append((path.name, "файл не найден"))
            elif not is_media_file(path):
                skipped.append((path.name, "неподдерживаемый формат"))
            else:
                files.append(path)
        unique = list(dict.fromkeys(files))
        if len(unique) > MAX_FILES_PER_ENQUEUE:
            skipped.append((f"{len(unique) - MAX_FILES_PER_ENQUEUE} файлов", "слишком много за раз"))
            unique = unique[:MAX_FILES_PER_ENQUEUE]
        return unique, skipped

    def _probe_durations(self, job_ids: list[str]) -> None:
        for job_id in job_ids:
            with self._cond:
                job = self._jobs.get(job_id)
                if self._stopping or job is None or job.duration is not None:
                    continue
                path = job.path
            duration = self._probe(path)
            if not duration or duration <= 0:
                continue
            with self._cond:
                job = self._jobs.get(job_id)
                if job is not None and job.duration is None:
                    job.duration = round(duration, 3)
                    self._post_update(job)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run_worker, name="bp-jobs", daemon=True)
            self._worker.start()

    # --- внутреннее: состояние и события (вызывать под self._cond) -----------------

    def _post_update(self, job: Job) -> None:
        self._bus.post({"type": "job_update", "job": job.to_dict()})

    def _finish(self, job: Job, status: str, *, error: str | None = None, transcript_id: str | None = None) -> None:
        job.status = status
        job.finished_at = round(time.time(), 3)
        job.eta_s = None
        job.error = error
        job.transcript_id = transcript_id
        if status == "done":
            job.progress = 1.0
            job.stage_progress = 1.0
            job.message = "Готово"
        elif status == "cancelled":
            job.message = "Отменено"
        else:
            job.message = error or "Ошибка"
        self._post_update(job)
        if status == "done":
            self._bus.post({"type": "job_done", "job": job.to_dict(), "transcript_id": transcript_id})

    # --- воркер -------------------------------------------------------------------------

    def _run_worker(self) -> None:
        if self._backend is None:
            try:
                self._backend = self._backend_loader()
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось инициализировать пайплайн, используется имитация")
                self._backend = load_backend(force_fake=True)
            logger.info("Пайплайн: %s", "имитация" if self._backend.is_fake else "GigaAM")
        try:
            while True:
                with self._cond:
                    job = None
                    while not self._stopping:
                        job = next((j for j in self._jobs.values() if j.status == "queued"), None)
                        if job is not None:
                            break
                        self._cond.wait()
                    if self._stopping:
                        return
                    token = self._backend.cancel_token_cls()
                    job.status = "running"
                    job.started_at = round(time.time(), 3)
                    job.message = "Подготовка…"
                    self._current = (job.id, token)
                    self._post_update(job)
                try:
                    self._process(job, token)
                finally:
                    with self._cond:
                        self._current = None
        finally:
            self._close_pipeline()

    def _process(self, job: Job, token: Any) -> None:
        pending: PendingEntry | None = None
        backend = self._backend
        try:
            self._wait_for_download(job, token)
            pipeline = self._ensure_pipeline()
            on_event = self._progress_handler(job)
            pipeline.prepare(on_event=on_event, cancel=token)
            token.raise_if_cancelled()
            pending = self._history.new_entry()
            options = backend.options_cls(
                diarization=job.options["diarization"],
                num_speakers=job.options["num_speakers"],
                preview_path=pending.preview_path,
            )
            result = pipeline.run(Path(job.path), options, on_event=on_event, cancel=token)
            token.raise_if_cancelled()
            transcript_id = self._history.save_result(
                result, job.path, job.options, pending.preview_path, pending=pending
            )
            pending = None
        except Exception as exc:  # noqa: BLE001
            if pending is not None:  # убрать запись ДО терминального события
                self._history.discard(pending)
                pending = None
            with self._cond:
                if _is_cancel(exc, token, backend.cancelled_exc if backend else None):
                    logger.info("Задача %s отменена", job.id)
                    self._finish(job, "cancelled")
                else:
                    logger.exception("Ошибка транскрипции %s", job.file_name)
                    self._finish(job, "error", error=error_message(exc))
            return
        finally:
            if pending is not None:
                self._history.discard(pending)

        self._autosave(job, transcript_id)
        with self._cond:
            if job.duration is None and getattr(result, "duration", None):
                job.duration = round(float(result.duration), 3)
            self._finish(job, "done", transcript_id=transcript_id)

    def _wait_for_download(self, job: Job, token: Any) -> None:
        if self._downloads is None or not self._downloads.is_downloading():
            return
        with self._cond:
            job.message = "Ожидание загрузки модели…"
            self._post_update(job)
        while not self._downloads.wait_idle(0.2):
            token.raise_if_cancelled()

    def _ensure_pipeline(self) -> Any:
        settings = self._settings.get()
        token = self._settings.tokens.get()
        if self._pipeline is None:
            kwargs = dict(self._pipeline_kwargs) if self._backend.is_fake else {}
            self._pipeline = self._backend.pipeline_cls(device=settings.device, hf_token=token, **kwargs)
        else:
            device, applied_token = self._applied
            if settings.device != device:
                self._pipeline.set_device(settings.device)
            if token != applied_token:
                self._pipeline.set_token(token)
        self._applied = (settings.device, token)
        return self._pipeline

    def _progress_handler(self, job: Job) -> Callable[[Any], None]:
        def on_event(event: Any) -> None:
            with self._cond:
                if job.status != "running":
                    return
                job.stage = getattr(event, "stage", job.stage)
                job.stage_progress = _clamp(getattr(event, "stage_progress", 0.0))
                job.progress = max(job.progress, _clamp(getattr(event, "progress", 0.0)))
                cancelling = self._current is not None and getattr(self._current[1], "cancelled", False)
                if not cancelling:
                    job.message = getattr(event, "message", "") or job.message
                    job.eta_s = getattr(event, "eta_s", None)
                self._post_update(job)

        return on_event

    def _autosave(self, job: Job, transcript_id: str) -> None:
        settings = self._settings.get()
        if not settings.autosave or not settings.autosave_formats:
            return
        target = Path(settings.autosave_dir).expanduser() if settings.autosave_dir else Path(job.path).parent
        saved: list[Path] = []
        failed: list[str] = []
        try:
            transcript = self._history.load(transcript_id)
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Автосохранение недоступно")
            self._bus.post({"type": "toast", "level": "error", "message": f"Автосохранение не удалось: {exc}"})
            return
        for fmt in settings.autosave_formats:
            try:
                dest = unique_path(target, default_filename(transcript, fmt), saved)
                saved.append(export(transcript, fmt, dest, settings.include_timestamps))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Автосохранение %s не удалось", fmt)
                failed.append(f"{fmt.upper()}: {exc}")
        if saved:
            names = ", ".join(p.name for p in saved)
            self._bus.post({"type": "toast", "level": "success", "message": f"Сохранено в «{target}»: {names}"})
        if failed:
            self._bus.post({"type": "toast", "level": "error", "message": "Не удалось сохранить: " + "; ".join(failed)})

    def _close_pipeline(self) -> None:
        if self._pipeline is not None:
            try:
                self._pipeline.close()
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка при закрытии пайплайна")
            self._pipeline = None


def _clamp(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
