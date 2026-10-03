from __future__ import annotations

import inspect
import json

import pytest

from bp_transcriber import api as api_mod
from bp_transcriber.api import Api, ApiError, check_token_fallback
from bp_transcriber.history import HistoryStore
from bp_transcriber.jobs import JobQueue, ModelDownloads
from bp_transcriber.settings import SettingsStore, TokenStore

CONTRACT_METHODS = {
    "get_state", "complete_onboarding", "pick_files", "choose_folder", "enqueue", "list_jobs", "cancel_job", "remove_job",
    "get_settings", "save_settings", "set_hf_token", "import_hf_token", "clear_hf_token", "check_hf_token",
    "models_status", "download_models", "list_history", "load_transcript", "rename_speaker", "edit_segment",
    "delete_transcript", "export", "copy_text", "reveal", "open_url",
}
SECRET = "hf_ApiTestSecretToken0123456789"


class FakeWindow:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append((dialog_type, kwargs))
        return self.result


@pytest.fixture
def api(tmp_path, bus, result, monkeypatch):
    monkeypatch.setattr(api_mod, "check_token", lambda token: check_token_fallback(None) if not token else {
        "ok": True, "state": "ok", "user": "tester", "repos": [], "message": "ok"})
    settings = SettingsStore(tmp_path / "s.json", TokenStore(tmp_path / "tok", use_keyring=False))
    history = HistoryStore(tmp_path / "h")
    downloads = ModelDownloads(bus)
    jobs = JobQueue(settings, history, bus, downloads=downloads)
    instance = Api(settings, history, jobs, downloads, server=None, window=FakeWindow())
    instance._tid = history.save_result(result, str(tmp_path / "Встреча.mp3"))
    yield instance
    jobs.shutdown()


def test_public_surface_matches_contract(api):
    public = {name for name in dir(api) if not name.startswith("_")}
    assert public == CONTRACT_METHODS
    # pywebview читает параметры через getfullargspec → имена из контракта, а не *args
    assert inspect.getfullargspec(api.rename_speaker).args == ["self", "id", "speaker_id", "name"]
    assert inspect.getfullargspec(api.export).args == ["self", "id", "format", "path"]


def test_get_state_shape(api):
    state = api.get_state()
    json.dumps(state)
    assert set(state) == {"version", "os", "settings", "models", "devices", "token_import", "first_run"}
    assert state["first_run"] is True and state["token_import"] is None
    assert state["settings"]["hf_token_set"] is False
    assert {"available", "size_bytes", "downloading", "progress"} <= set(state["models"]["gigaam"])
    assert {d["id"] for d in state["devices"]} >= {"auto", "cpu"}


def test_token_flow_never_leaks(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"HF_TOKEN={SECRET}\n", encoding="utf-8")
    state = api.get_state()
    assert state["token_import"] == {"source": ".env"}
    assert SECRET not in json.dumps(state)
    check = api.import_hf_token()
    assert check["ok"] and SECRET not in json.dumps(check)
    assert api.get_settings()["hf_token_set"] is True
    assert api.get_state()["token_import"] is None
    assert api.models_status()["pyannote"]["token_ok"] is True
    settings = api.clear_hf_token()
    assert settings["hf_token_set"] is False
    assert api.check_hf_token()["state"] == "missing"
    with pytest.raises(ApiError):
        api.set_hf_token("   ")


def test_settings_errors_are_api_errors(api):
    with pytest.raises(ApiError, match="Недопустимое значение"):
        api.save_settings({"theme": "pink"})
    assert api.save_settings({"theme": "dark"})["theme"] == "dark"
    assert api.get_state()["first_run"] is True  # обычные настройки онбординг не завершают


def test_complete_onboarding(api, tmp_path):
    state = api.complete_onboarding()
    assert state["first_run"] is False and set(state) >= {"version", "settings"}
    reloaded = SettingsStore(api._settings.path, TokenStore(tmp_path / "tok", use_keyring=False))
    assert reloaded.first_run is False


def test_history_methods(api):
    tid = api._tid
    assert api.list_history()[0]["id"] == tid
    assert api.list_history("встреча")[0]["id"] == tid
    t = api.rename_speaker(tid, "S2", "Мария")
    assert t["speakers"][1]["name"] == "Мария" and t["media_url"] is None
    assert api.edit_segment(tid, 0, "Привет")["segments"][0]["words"] == []
    with pytest.raises(ApiError, match="не найден"):
        api.load_transcript("000000000000")
    assert api.delete_transcript(tid) is True


def test_export_with_path_and_dialog(api, tmp_path):
    tid = api._tid
    out = api.export(tid, "md", str(tmp_path / "exp" / "file"))
    assert out == {"path": str(tmp_path / "exp" / "file.md")}
    assert (tmp_path / "exp" / "file.md").read_text(encoding="utf-8").startswith("# Встреча.mp3")

    api._window.result = None  # пользователь отменил диалог
    assert api.export(tid, "docx") is None
    dialog_type, kwargs = api._window.calls[-1]
    assert kwargs["save_filename"] == "Встреча.docx"
    assert kwargs["directory"] == str(tmp_path / "exp")  # последняя папка экспорта

    api._window.result = str(tmp_path / "picked.docx")
    assert api.export(tid, "docx")["path"] == str(tmp_path / "picked.docx")
    with pytest.raises(ApiError):
        api.export(tid, "pdf", str(tmp_path / "x.pdf"))


def test_copy_text(api, monkeypatch):
    copied = []
    monkeypatch.setattr(api_mod.clipboard, "copy", lambda text: copied.append(text) or True)
    assert api.copy_text(api._tid, False) is True
    assert copied[0].startswith("Спикер 1: Добрый день")
    api.copy_text(api._tid, True)
    assert copied[1].startswith("[00:00:00] Спикер 1:")


def test_dialogs(api):
    api._window.result = ("/a/x.mp3", "/a/y.wav")
    assert api.pick_files() == ["/a/x.mp3", "/a/y.wav"]
    _, kwargs = api._window.calls[-1]
    assert kwargs["allow_multiple"] is True
    assert kwargs["file_types"][0].startswith("Аудио и видео (*.")
    api._window.result = None
    assert api.pick_files() == []
    assert api.choose_folder() is None
    api._window.result = ("/a/folder",)
    assert api.choose_folder() == "/a/folder"


def test_file_types_valid_for_pywebview():
    from webview.util import parse_file_type

    for ft in api_mod._file_types():
        parse_file_type(ft)


def test_open_url_and_reveal(api, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(api_mod.webbrowser, "open", lambda url: opened.append(url) or True)
    assert api.open_url("https://huggingface.co/settings/tokens") is True
    assert api.open_url("file:///etc/passwd") is False
    assert api.open_url("javascript:alert(1)") is False
    assert opened == ["https://huggingface.co/settings/tokens"]
    with pytest.raises(ApiError):
        api.reveal(str(tmp_path / "nope"))


def test_unexpected_errors_wrapped(api, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad")

    monkeypatch.setattr(api._history, "list", boom)
    with pytest.raises(ApiError, match="Непредвиденная ошибка: bad"):
        api.list_history()


def test_check_token_fallback_missing():
    result = check_token_fallback(None)
    assert result["state"] == "missing" and result["ok"] is False
    assert all(r["ok"] is None for r in result["repos"])  # не проверялись
    assert [r["id"] for r in result["repos"]] == api_mod.PYANNOTE_REPOS
