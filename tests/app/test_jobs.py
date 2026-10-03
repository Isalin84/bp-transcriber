from __future__ import annotations

import time
from pathlib import Path

import pytest

from bp_transcriber import fake_pipeline
from bp_transcriber.history import HistoryStore
from bp_transcriber.jobs import JobQueue, ModelDownloads, PipelineBackend, load_backend
from bp_transcriber.settings import SettingsStore, TokenStore


class RecordingPipeline(fake_pipeline.FakePipeline):
    """FakePipeline с журналом вызовов set_device/set_token."""

    instances: list[RecordingPipeline] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple] = []
        RecordingPipeline.instances.append(self)

    def set_device(self, device):
        self.calls.append(("device", device))
        super().set_device(device)

    def set_token(self, token):
        self.calls.append(("token", bool(token)))
        super().set_token(token)


def fake_backend() -> PipelineBackend:
    return PipelineBackend(
        RecordingPipeline, fake_pipeline.PipelineOptions, fake_pipeline.CancelToken, fake_pipeline.Cancelled, True
    )


@pytest.fixture
def env(tmp_path, bus):
    RecordingPipeline.instances.clear()
    settings = SettingsStore(tmp_path / "settings.json", TokenStore(tmp_path / "tok", use_keyring=False))
    history = HistoryStore(tmp_path / "history")
    queue = JobQueue(
        settings, history, bus, backend_loader=fake_backend,
        pipeline_kwargs={"step_delay": 0.004, "make_preview": False},
        probe=lambda path: 0.0 if "one" in path else 42.5,  # 0.0 → «неизвестно»
    )
    media = tmp_path / "media"
    media.mkdir()
    files = []
    for name in ("one.wav", "two.mp3", "three.m4a"):
        p = media / name
        p.write_bytes(b"\x00" * 64)
        files.append(str(p))
    yield settings, history, queue, files
    queue.shutdown(timeout=5)


def terminal(bus, job_id):
    return bus.wait_for(
        lambda e: e["type"] == "job_update" and e["job"]["id"] == job_id
        and e["job"]["status"] in ("done", "error", "cancelled")
    )


def test_enqueue_three_cancel_second(env, bus, tmp_path):
    settings, history, queue, files = env
    jobs = queue.enqueue(files, {"diarization": "hybrid", "num_speakers": 2})
    assert [j["status"] for j in jobs] == ["queued"] * 3
    assert jobs[0]["options"] == {"diarization": "hybrid", "num_speakers": 2}
    assert queue.cancel(jobs[1]["id"]) is True  # ещё в очереди

    done1 = terminal(bus, jobs[0]["id"])
    done3 = terminal(bus, jobs[2]["id"])
    cancelled = terminal(bus, jobs[1]["id"])
    assert done1["job"]["status"] == "done" and done3["job"]["status"] == "done"
    assert cancelled["job"]["status"] == "cancelled"

    done_events = bus.of_type("job_done")
    assert {e["job"]["id"] for e in done_events} == {jobs[0]["id"], jobs[2]["id"]}
    tid = done_events[0]["transcript_id"]
    transcript = history.load(tid)
    assert [s["name"] for s in transcript["speakers"]] == ["Спикер 1", "Спикер 2"]
    assert transcript["segments"] and transcript["segments"][0]["words"]
    assert len(history.list()) == 2

    # прогресс шёл через этапы, промежуточные события были
    stages = {e["job"]["stage"] for e in bus.of_type("job_update") if e["job"]["status"] == "running"}
    assert {"load", "decode", "asr", "diarize"} <= stages
    # модель «загружена» один раз и переиспользуется
    assert len(RecordingPipeline.instances) == 1 and RecordingPipeline.instances[0].runs == 2
    assert queue.remove(jobs[1]["id"]) is True
    assert [j["id"] for j in queue.list()] == [jobs[0]["id"], jobs[2]["id"]]


def test_cancel_running_leaves_no_history(env, bus, tmp_path):
    settings, history, queue, files = env
    RecordingPipeline_delay = 0.03
    queue._pipeline_kwargs["step_delay"] = RecordingPipeline_delay
    job = queue.enqueue(files[:1])[0]
    bus.wait_for(lambda e: e["type"] == "job_update" and e["job"]["stage"] == "asr")
    assert queue.remove(job["id"]) is False  # running — удалять нельзя
    assert queue.cancel(job["id"]) is True
    ev = terminal(bus, job["id"])
    assert ev["job"]["status"] == "cancelled" and ev["job"]["message"] == "Отменено"
    assert history.list() == []
    assert [p.name for p in (tmp_path / "history").iterdir() if p.name != "index.json"] == []
    assert queue.cancel(job["id"]) is False


def test_error_path(env, bus, tmp_path):
    settings, history, queue, files = env
    bad = Path(files[0]).with_name("fail_broken.wav")
    bad.write_bytes(b"x")
    job = queue.enqueue([str(bad), files[1]])
    ev = terminal(bus, job[0]["id"])
    assert ev["job"]["status"] == "error"
    assert "имитация" in ev["job"]["error"] and ev["job"]["error"] == ev["job"]["message"]
    assert terminal(bus, job[1]["id"])["job"]["status"] == "done"  # очередь продолжает работу
    assert len(history.list()) == 1


def test_unexpected_exception_message(env, bus, monkeypatch):
    settings, history, queue, files = env

    def boom(self, *a, **k):
        raise ValueError("сломалось")

    monkeypatch.setattr(RecordingPipeline, "run", boom)
    job = queue.enqueue(files[:1])[0]
    ev = terminal(bus, job["id"])
    assert ev["job"]["error"] == "Непредвиденная ошибка: сломалось"


def test_skips_invalid_inputs(env, bus, tmp_path):
    settings, history, queue, files = env
    (tmp_path / "notes.txt").write_text("x")
    jobs = queue.enqueue([files[0], files[0], str(tmp_path / "missing.wav"), str(tmp_path / "notes.txt")])
    assert len(jobs) == 1
    toast = bus.wait_for(lambda e: e["type"] == "toast")
    assert "missing.wav (файл не найден)" in toast["message"]
    assert "notes.txt (неподдерживаемый формат)" in toast["message"]


def test_directory_expanded(env, bus, tmp_path):
    settings, history, queue, files = env
    jobs = queue.enqueue([str(Path(files[0]).parent)])
    assert sorted(j["file_name"] for j in jobs) == ["one.wav", "three.m4a", "two.mp3"]


def test_settings_applied_between_jobs(env, bus):
    settings, history, queue, files = env
    first = queue.enqueue(files[:1])[0]
    terminal(bus, first["id"])
    settings.update({"device": "cpu"})
    settings.tokens.set("hf_some_test_token_value")
    second = queue.enqueue(files[1:2])[0]
    terminal(bus, second["id"])
    third = queue.enqueue(files[2:3])[0]
    terminal(bus, third["id"])
    pipe = RecordingPipeline.instances[0]
    assert pipe.calls == [("device", "cpu"), ("token", True)]  # только при изменении


def test_autosave(env, bus, tmp_path):
    settings, history, queue, files = env
    out = tmp_path / "out"
    settings.update({"autosave": True, "autosave_dir": str(out), "autosave_formats": ["txt", "docx"]})
    (out).mkdir()
    (out / "one.txt").write_text("existing")
    job = queue.enqueue(files[:1])[0]
    bus.wait_for(lambda e: e["type"] == "job_done")
    assert sorted(p.name for p in out.iterdir()) == ["one (2).txt", "one.docx", "one.txt"]
    assert (out / "one.txt").read_text() == "existing"
    toast = bus.wait_for(lambda e: e["type"] == "toast" and e["level"] == "success")
    assert "one (2).txt" in toast["message"]
    assert queue.get(job["id"])["status"] == "done"


def test_shutdown_cancels(env, bus):
    settings, history, queue, files = env
    queue._pipeline_kwargs["step_delay"] = 0.05
    queue.enqueue(files)
    bus.wait_for(lambda e: e["type"] == "job_update" and e["job"]["status"] == "running")
    t0 = time.monotonic()
    queue.shutdown(timeout=5)
    assert time.monotonic() - t0 < 3
    statuses = {j["id"]: j["status"] for j in queue.list()}
    assert set(statuses.values()) == {"cancelled"}
    assert history.list() == []
    assert queue.enqueue(files) == []


def test_load_backend_fake_by_env(monkeypatch):
    monkeypatch.setenv("BP_FAKE_PIPELINE", "1")
    backend = load_backend()
    assert backend.is_fake and backend.pipeline_cls is fake_pipeline.FakePipeline


def test_model_download_without_model_store(bus, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gigaam_transcriber" and args and args[2] and "model_store" in args[2]:
            raise ImportError("нет model_store")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    downloads = ModelDownloads(bus)
    assert downloads.start() is False
    ev = bus.wait_for(lambda e: e["type"] == "model_download")
    assert ev["status"] == "error" and "недоступен" in ev["message"]


def test_model_download_events_and_double_start(bus, monkeypatch):
    import sys
    import types

    import threading

    release = threading.Event()

    def ensure_model(name, *, on_progress=None, cancel=None):
        for i in range(1, 5):
            on_progress(i * 25, 100)
        release.wait(5)
        return Path("/tmp")

    module = types.ModuleType("gigaam_transcriber.model_store")
    module.ensure_model = ensure_model
    monkeypatch.setitem(sys.modules, "gigaam_transcriber.model_store", module)
    import gigaam_transcriber

    monkeypatch.setattr(gigaam_transcriber, "model_store", module, raising=False)

    downloads = ModelDownloads(bus)
    assert downloads.start() is True
    assert downloads.start() is False  # защита от двойного старта
    bus.wait_for(lambda e: e["type"] == "model_download" and e["progress"] == 1.0 and e["status"] == "downloading")
    assert downloads.status()["downloading"] is True
    release.set()
    done = bus.wait_for(lambda e: e["type"] == "model_download" and e["status"] == "done")
    assert done["downloaded"] == 100 and done["total"] == 100
    assert downloads.wait_idle(2) and downloads.status()["downloading"] is False


def test_duration_probed_in_background(env, bus):
    settings, history, queue, files = env
    queue._pipeline_kwargs["step_delay"] = 0.03
    jobs = queue.enqueue(files)
    assert all(j["duration"] is None for j in jobs)  # enqueue не ждёт ffmpeg
    ev = bus.wait_for(lambda e: e["type"] == "job_update" and e["job"]["id"] == jobs[1]["id"]
                      and e["job"]["duration"] == 42.5)
    assert ev["job"]["status"] in ("queued", "running", "done")
    # для файла без известной длительности она берётся из результата
    done = terminal(bus, jobs[0]["id"])
    assert done["job"]["duration"] == 95.0  # FakePipeline без ffmpeg-пробы → 95 с


def test_probe_duration_helper(tmp_path):
    from bp_transcriber.jobs import probe_duration

    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not audio")
    assert probe_duration(str(bad)) is None
