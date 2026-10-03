from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from bp_transcriber import settings as settings_mod
from bp_transcriber.settings import (
    Settings,
    SettingsError,
    SettingsStore,
    TokenStore,
    find_token_candidate,
    find_token_import_candidate,
)

SECRET = "hf_TestTokenValue1234567890abcdef"


class FailingKeyring:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError("no keyring backend")

        return fail


class MemoryKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        del self.store[(service, user)]


def test_round_trip(tmp_path):
    path = tmp_path / "cfg" / "settings.json"
    store = SettingsStore(path, TokenStore(tmp_path / "tok", use_keyring=False))
    assert store.first_run
    updated = store.update({"theme": "dark", "num_speakers": 3, "autosave": True,
                            "autosave_formats": ["md", "docx", "md"], "autosave_dir": "  "})
    assert updated.theme == "dark" and updated.num_speakers == 3
    assert updated.autosave_formats == ["md", "docx"]
    assert updated.autosave_dir is None
    assert store.first_run  # онбординг не пройден
    store.update({"onboarding_done": True})
    assert not store.first_run

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == settings_mod.SCHEMA_VERSION
    reloaded = SettingsStore(path, TokenStore(tmp_path / "tok", use_keyring=False))
    assert reloaded.get().theme == "dark" and reloaded.get().onboarding_done
    assert not reloaded.first_run
    public = reloaded.public_dict()
    assert public["hf_token_set"] is False and public["theme"] == "dark"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path, TokenStore(tmp_path / "tok", use_keyring=False))
    for i in range(5):
        store.update({"num_speakers": i + 1})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_atomic_write_failure_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    store = SettingsStore(path, TokenStore(tmp_path / "tok", use_keyring=False))
    store.update({"theme": "light"})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(settings_mod.os, "replace", boom)
    with pytest.raises(OSError):
        store.update({"theme": "dark"})
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8"))["theme"] == "light"
    assert store.get().theme == "light"  # состояние в памяти не изменилось
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


@pytest.mark.parametrize(
    "partial",
    [
        {"theme": "blue"},
        {"device": "tpu"},
        {"diarization": "magic"},
        {"num_speakers": 0},
        {"num_speakers": 2.5},
        {"num_speakers": True},
        {"autosave": "yes"},
        {"autosave_formats": ["pdf"]},
        {"autosave_formats": "txt"},
    ],
)
def test_invalid_values_rejected(tmp_path, partial):
    store = SettingsStore(tmp_path / "s.json", TokenStore(tmp_path / "tok", use_keyring=False))
    with pytest.raises(SettingsError):
        store.update(partial)
    assert not (tmp_path / "s.json").exists()


def test_unknown_keys_ignored_and_bad_file_values_reset(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 1, "theme": "neon", "device": "cpu", "future_key": 1}), encoding="utf-8")
    store = SettingsStore(path, TokenStore(tmp_path / "tok", use_keyring=False))
    assert store.get().theme == "system"
    assert store.get().device == "cpu"
    assert store.update({"hf_token_set": True, "whatever": 1}).device == "cpu"


def test_corrupted_file_uses_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert SettingsStore(path).get() == Settings()


def test_token_fallback_file_when_keyring_fails(tmp_path, monkeypatch, caplog):
    import keyring

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(FailingKeyring(), name))
    token_file = tmp_path / "cfg" / "hf_token"
    tokens = TokenStore(token_file)
    assert tokens.get() is None
    tokens.set(f"  {SECRET}\n")
    assert token_file.read_text(encoding="utf-8") == SECRET
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600
    assert TokenStore(token_file).get() == SECRET  # новый экземпляр читает файл
    tokens.clear()
    assert not token_file.exists() and tokens.get() is None
    assert SECRET not in caplog.text


def test_token_keyring_preferred(tmp_path, monkeypatch):
    import keyring

    mem = MemoryKeyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(mem, name))
    token_file = tmp_path / "hf_token"
    token_file.write_text("hf_old_file_token_value", encoding="utf-8")
    tokens = TokenStore(token_file)
    tokens.set(SECRET)
    assert mem.store[("BP Transcriber", "hf_token")] == SECRET
    assert not token_file.exists()
    assert TokenStore(token_file).get() == SECRET
    tokens.clear()
    assert mem.store == {}


def test_import_candidate_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        f"# comment\nOTHER=1\nexport HUGGINGFACE_TOKEN='{SECRET}'\n", encoding="utf-8"
    )
    candidate = find_token_import_candidate([tmp_path])
    assert candidate == {"source": ".env"}
    assert SECRET not in json.dumps(candidate)
    assert find_token_candidate([tmp_path]) == (".env", SECRET)


def test_import_candidate_priority_env_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("HF_TOKEN=hf_from_dotenv_file_value\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN", SECRET)
    assert find_token_import_candidate([tmp_path]) == {"source": "env"}
    assert find_token_candidate([tmp_path])[1] == SECRET


def test_import_candidate_hf_cache(tmp_path, monkeypatch):
    hf_home = tmp_path / "hfhome"
    hf_home.mkdir()
    (hf_home / "token").write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(hf_home))
    assert find_token_import_candidate([tmp_path / "empty"]) == {"source": "hf_cache"}


def test_import_candidate_none(tmp_path):
    (tmp_path / ".env").write_text("HF_TOKEN=\nOTHER=x\n", encoding="utf-8")
    assert find_token_import_candidate([tmp_path]) is None
