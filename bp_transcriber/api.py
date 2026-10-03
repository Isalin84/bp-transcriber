"""JS API (контракт §2.2): ``window.pywebview.api.<method>()``.

pywebview рекурсивно публикует все публичные атрибуты объекта, поэтому всё, что не
является методом API, хранится в атрибутах с ``_``. Каждый метод вызывается в
отдельном потоке pywebview — общий изменяемый state здесь защищён ``_lock``,
остальное потокобезопасно в соответствующих модулях.
Ошибки — ``ApiError`` с русским сообщением (в JS: Promise reject с ``{message}``).
"""

from __future__ import annotations

import functools
import inspect
import logging
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any
from collections.abc import Callable
from urllib.parse import urlparse

from . import __version__, clipboard, exporters
from .history import HistoryError, HistoryStore
from .jobs import AUDIO_EXTENSIONS, DEFAULT_MODEL, VIDEO_EXTENSIONS, JobQueue, ModelDownloads
from .settings import FORMATS, SettingsError, SettingsStore, find_token_candidate, find_token_import_candidate

logger = logging.getLogger(__name__)

PYANNOTE_REPOS = [
    "pyannote/speaker-diarization-community-1",
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
]
GIGAAM_APPROX_BYTES = 449_184_588  # v3_e2e_rnnt: ckpt + tokenizer


class ApiError(Exception):
    """Ошибка для UI; ``str(e)`` показывается пользователю."""


def _api(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Логирует и переводит исключения в ApiError, сохраняя сигнатуру для pywebview."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ApiError:
            raise
        except (SettingsError, HistoryError, exporters.ExportError) as exc:
            raise ApiError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Ошибка в API %s", fn.__name__)
            raise ApiError(f"Непредвиденная ошибка: {exc}") from exc

    # pywebview читает параметры через getfullargspec, который учитывает __signature__.
    wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapper


def os_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def _file_types() -> tuple[str, ...]:
    media = ";".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS))
    audio = ";".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
    video = ";".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
    return (
        f"Аудио и видео ({media})",
        f"Аудио ({audio})",
        f"Видео ({video})",
        "Все файлы (*.*)",
    )


def _first_path(result: Any) -> str | None:
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result)


# --- проверка HF-токена --------------------------------------------------------------


def _token_result(state: str, user: str | None, repos: list[dict[str, Any]], message: str) -> dict[str, Any]:
    return {"ok": state == "ok", "state": state, "user": user, "repos": repos, "message": message}


def check_token_fallback(token: str | None) -> dict[str, Any]:
    """Проверка токена через huggingface_hub, если в ядре ещё нет ``diarization.check_token``."""
    # ok: None — репозиторий не проверялся (нет токена, токен недействителен, сеть)
    repos = [{"id": r, "url": f"https://huggingface.co/{r}", "ok": None} for r in PYANNOTE_REPOS]
    if not token:
        return _token_result("missing", None, repos, "Токен HuggingFace не задан")
    try:
        from huggingface_hub import HfApi, auth_check
        from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
    except ImportError:
        return _token_result("network", None, repos, "Модуль huggingface_hub недоступен")

    network_msg = "Нет соединения с HuggingFace — проверьте интернет и повторите"
    try:
        info = HfApi().whoami(token=token)
        user = info.get("name") if isinstance(info, dict) else None
    except HfHubHTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return _token_result("invalid", None, repos, "Токен недействителен — проверьте, что он скопирован полностью")
        logger.warning("whoami: HTTP %s", status)
        return _token_result("network", None, repos, network_msg)
    except Exception as exc:  # noqa: BLE001 — httpx/requests/OSError
        logger.warning("whoami: %s", type(exc).__name__)
        return _token_result("network", None, repos, network_msg)

    gated = False
    for repo in repos:
        try:
            auth_check(repo["id"], token=token)
            repo["ok"] = True
        except GatedRepoError:
            repo["ok"] = False
            gated = True
        except RepositoryNotFoundError:
            repo["ok"] = False
            gated = True  # для gated-репозиториев без доступа HF иногда отвечает 404
        except HfHubHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                repo["ok"] = False
                gated = True
            else:
                return _token_result("network", user, repos, network_msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auth_check %s: %s", repo["id"], type(exc).__name__)
            return _token_result("network", user, repos, network_msg)
    if gated or not all(r["ok"] is True for r in repos):
        return _token_result(
            "terms_not_accepted",
            user,
            repos,
            "Токен действителен, но нужно принять условия использования моделей pyannote на HuggingFace",
        )
    return _token_result("ok", user, repos, f"Токен действителен ({user})" if user else "Токен действителен")


def check_token(token: str | None) -> dict[str, Any]:
    try:
        from gigaam_transcriber.diarization import check_token as core_check
    except ImportError:
        return check_token_fallback(token)
    result = core_check(token)
    return result.to_dict() if hasattr(result, "to_dict") else dict(result)


# --- API ------------------------------------------------------------------------------


class Api:
    def __init__(
        self,
        settings: SettingsStore,
        history: HistoryStore,
        jobs: JobQueue,
        downloads: ModelDownloads,
        server: Any = None,
        window: Any = None,
    ):
        self._settings = settings
        self._history = history
        self._jobs = jobs
        self._downloads = downloads
        self._server = server
        self._window = window
        self._lock = threading.Lock()
        self._devices: list[dict[str, Any]] | None = None
        self._token_ok: bool | None = None
        self._last_export_dir: str | None = None

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def _warm_up(self) -> None:
        """Прогрев медленных проверок (torch для списка устройств) в фоне при старте."""
        try:
            self._get_devices()
            self._models_status()
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка прогрева")

    # --- состояние ------------------------------------------------------------------

    @_api
    def get_state(self) -> dict[str, Any]:
        settings = self._settings.public_dict()
        return {
            "version": __version__,
            "os": os_name(),
            "settings": settings,
            "models": self._models_status(),
            "devices": self._get_devices(),
            "token_import": None if settings["hf_token_set"] else find_token_import_candidate(),
            "first_run": self._settings.first_run,
        }

    @_api
    def complete_onboarding(self) -> dict[str, Any]:
        self._settings.update({"onboarding_done": True})
        return self.get_state()

    @_api
    def get_settings(self) -> dict[str, Any]:
        return self._settings.public_dict()

    @_api
    def save_settings(self, partial: dict[str, Any]) -> dict[str, Any]:
        self._settings.update(partial or {})
        return self._settings.public_dict()

    # --- файлы ----------------------------------------------------------------------

    @_api
    def pick_files(self) -> list[str]:
        import webview

        window = self._require_window()
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=_file_types()
        )
        return [str(p) for p in result] if result else []

    @_api
    def choose_folder(self) -> str | None:
        import webview

        window = self._require_window()
        current = self._settings.get().autosave_dir or ""
        return _first_path(window.create_file_dialog(webview.FileDialog.FOLDER, directory=current))

    # --- задачи ---------------------------------------------------------------------

    @_api
    def enqueue(self, paths: list[str], options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            raise ApiError("Ожидался список файлов")
        return self._jobs.enqueue([str(p) for p in paths], options or {})

    @_api
    def list_jobs(self) -> list[dict[str, Any]]:
        return self._jobs.list()

    @_api
    def cancel_job(self, id: str) -> bool:  # noqa: A002 — имя из контракта
        return self._jobs.cancel(id)

    @_api
    def remove_job(self, id: str) -> bool:  # noqa: A002
        return self._jobs.remove(id)

    # --- токен и модели -------------------------------------------------------------

    @_api
    def set_hf_token(self, token: str) -> dict[str, Any]:
        token = (token or "").strip() if isinstance(token, str) else ""
        if not token:
            raise ApiError("Вставьте токен HuggingFace")
        self._settings.tokens.set(token)
        return self._check(token)

    @_api
    def import_hf_token(self) -> dict[str, Any]:
        found = find_token_candidate()
        if found is None:
            raise ApiError("Существующий токен HuggingFace не найден")
        source, token = found
        logger.info("Импорт HF-токена из источника %s", source)
        self._settings.tokens.set(token)
        return self._check(token)

    @_api
    def clear_hf_token(self) -> dict[str, Any]:
        self._settings.tokens.clear()
        with self._lock:
            self._token_ok = None
        return self._settings.public_dict()

    @_api
    def check_hf_token(self) -> dict[str, Any]:
        return self._check(self._settings.tokens.get())

    @_api
    def models_status(self) -> dict[str, Any]:
        return self._models_status()

    @_api
    def download_models(self) -> bool:
        return self._downloads.start()

    # --- история --------------------------------------------------------------------

    @_api
    def list_history(self, query: str | None = None) -> list[dict[str, Any]]:
        return self._history.list(query)

    @_api
    def load_transcript(self, id: str) -> dict[str, Any]:  # noqa: A002
        return self._with_media(self._history.load(id))

    @_api
    def rename_speaker(self, id: str, speaker_id: str, name: str) -> dict[str, Any]:  # noqa: A002
        return self._with_media(self._history.rename_speaker(id, speaker_id, name))

    @_api
    def edit_segment(self, id: str, index: int, text: str) -> dict[str, Any]:  # noqa: A002
        return self._with_media(self._history.edit_segment(id, index, text))

    @_api
    def delete_transcript(self, id: str) -> bool:  # noqa: A002
        return self._history.delete(id)

    @_api
    def export(self, id: str, format: str, path: str | None = None) -> dict[str, str] | None:  # noqa: A002
        if format not in FORMATS:
            raise ApiError(f"Неизвестный формат: {format}")
        transcript = self._history.load(id)
        filename = exporters.default_filename(transcript, format)
        if not path:
            import webview

            window = self._require_window()
            with self._lock:
                directory = self._last_export_dir
            if not directory or not Path(directory).is_dir():
                directory = str(Path(transcript.get("source_path") or "").parent) if transcript.get("source_path") else ""
            path = _first_path(
                window.create_file_dialog(webview.FileDialog.SAVE, directory=directory, save_filename=filename)
            )
            if not path:
                return None
        target = Path(path)
        if target.suffix.lower() != f".{format}":
            target = target.with_name(f"{target.name}.{format}")
        include_ts = self._settings.get().include_timestamps
        written = exporters.export(transcript, format, target, include_ts)
        with self._lock:
            self._last_export_dir = str(written.parent)
        return {"path": str(written)}

    @_api
    def copy_text(self, id: str, with_timestamps: bool = True) -> bool:  # noqa: A002
        transcript = self._history.load(id)
        return clipboard.copy(exporters.render_txt(transcript, bool(with_timestamps)))

    # --- система --------------------------------------------------------------------

    @_api
    def reveal(self, path: str) -> bool:
        target = Path(str(path or "")).expanduser()
        if not target.exists():
            raise ApiError("Файл не найден")
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)])
            elif sys.platform == "win32":
                # explorer ожидает именно /select,"<путь>" — строкой, не списком.
                subprocess.Popen(f'explorer /select,"{target}"')
            else:
                subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
        except OSError as exc:
            logger.warning("reveal: %s", exc)
            return False
        return True

    @_api
    def open_url(self, url: str) -> bool:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        return bool(webbrowser.open(url))

    # --- внутреннее -----------------------------------------------------------------

    def _require_window(self) -> Any:
        if self._window is None:
            raise ApiError("Окно приложения ещё не готово")
        return self._window

    def _with_media(self, transcript: dict[str, Any]) -> dict[str, Any]:
        if self._server is not None:
            transcript["media_url"] = self._server.media_url(transcript["id"])
        return transcript

    def _check(self, token: str | None) -> dict[str, Any]:
        result = check_token(token)
        with self._lock:
            self._token_ok = None if result.get("state") == "network" else bool(result.get("ok"))
        return result

    def _get_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._devices is not None:
                return [dict(d) for d in self._devices]
        try:
            from gigaam_transcriber.device import available_options

            devices = [
                {"id": d["id"], "label": d.get("label", d["id"]), "available": bool(d.get("available", True))}
                for d in available_options()
            ]
        except Exception as exc:  # noqa: BLE001
            logger.info("device.available_options недоступен (%s), только CPU", type(exc).__name__)
            devices = [
                {"id": "auto", "label": "Автоматически", "available": True},
                {"id": "cpu", "label": "CPU", "available": True},
            ]
        with self._lock:
            self._devices = devices
        return [dict(d) for d in devices]

    def _models_status(self) -> dict[str, Any]:
        available, size = _gigaam_status()
        dl = self._downloads.status()
        try:
            from gigaam_transcriber.diarization import pyannote_cached

            cached = bool(pyannote_cached())
        except Exception:  # noqa: BLE001
            cached = False
        with self._lock:
            token_ok = self._token_ok
        return {
            "gigaam": {
                "available": available,
                "size_bytes": size,
                "downloading": bool(dl["downloading"]),
                "progress": float(dl["progress"]),
            },
            "pyannote": {"cached": cached, "token_ok": token_ok},
        }


def _gigaam_status() -> tuple[bool, int]:
    try:
        from gigaam_transcriber import model_store

        available = bool(model_store.is_model_available(DEFAULT_MODEL))
        root = Path(model_store.models_root())
    except Exception:  # noqa: BLE001 — модуль A1 ещё не влит: смотрим кэш GigaAM по умолчанию
        root = Path.home() / ".cache" / "gigaam"
        available = (root / f"{DEFAULT_MODEL}.ckpt").is_file()
    if available:
        try:
            size = sum(p.stat().st_size for p in root.glob(f"{DEFAULT_MODEL}*") if p.is_file())
            return True, size or GIGAAM_APPROX_BYTES
        except OSError:
            pass
    return available, GIGAAM_APPROX_BYTES
