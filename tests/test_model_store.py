"""
Тесты для модуля model_store (без сети: urlopen подменяется).
"""

import hashlib
import io
import urllib.error

import pytest

from gigaam_transcriber import model_store
from gigaam_transcriber.exceptions import ModelLoadError
from gigaam_transcriber.progress import Cancelled, CancelToken

NAME = "tiny_e2e"
PRIMARY = "http://primary.test/GigaAM"
CKPT = b"checkpoint-bytes" * 300
TOKENIZER = b"tokenizer-bytes"
CKPT_MD5 = hashlib.md5(CKPT).hexdigest()


class FakeResponse(io.BytesIO):
    """Минимальный ответ urlopen: контекстный менеджер, read(n), headers."""

    def __init__(self, data: bytes, content_length: int | None = None) -> None:
        super().__init__(data)
        length = len(data) if content_length is None else content_length
        self.headers = {"Content-Length": str(length)}


class FakeServer:
    """Таблица url -> тело ответа или исключение; запоминает запросы."""

    def __init__(self) -> None:
        self.routes: dict[str, bytes | Exception | FakeResponse] = {}
        self.requests: list[str] = []
        self.user_agents: list[str | None] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        self.user_agents.append(request.get_header("User-agent"))
        answer = self.routes.get(url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)


@pytest.fixture
def server(monkeypatch, tmp_path) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(model_store.urllib.request, "urlopen", fake)
    monkeypatch.setattr(model_store, "MIRROR_URL", "http://mirror.test/models")
    monkeypatch.setattr(model_store, "_gigaam_tables", lambda: (PRIMARY, {NAME: CKPT_MD5}))
    monkeypatch.setattr(model_store, "_LEGACY_ROOT", tmp_path / "legacy")
    monkeypatch.setattr(
        model_store.platformdirs, "user_cache_dir", lambda *a, **k: str(tmp_path / "cache")
    )
    fake.routes[f"{PRIMARY}/{NAME}.ckpt"] = CKPT
    fake.routes[f"{PRIMARY}/{NAME}_tokenizer.model"] = TOKENIZER
    return fake


@pytest.fixture
def root(tmp_path):
    return tmp_path / "cache" / "gigaam"


class TestModelsRoot:
    def test_default_is_app_cache(self, server, root):
        assert model_store.models_root(NAME) == root

    def test_legacy_cache_is_reused_when_checkpoint_exists(self, server, tmp_path):
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        assert model_store.models_root(NAME) != legacy
        (legacy / f"{NAME}.ckpt").write_bytes(b"x")
        assert model_store.models_root(NAME) == legacy


class TestIsModelAvailable:
    def test_missing(self, server):
        assert model_store.is_model_available(NAME) is False

    def test_checkpoint_without_tokenizer(self, server, root):
        root.mkdir(parents=True)
        (root / f"{NAME}.ckpt").write_bytes(CKPT)
        assert model_store.is_model_available(NAME) is False

    def test_checkpoint_and_tokenizer(self, server, root):
        root.mkdir(parents=True)
        (root / f"{NAME}.ckpt").write_bytes(CKPT)
        (root / f"{NAME}_tokenizer.model").write_bytes(TOKENIZER)
        assert model_store.is_model_available(NAME) is True

    def test_model_without_tokenizer_needs_only_checkpoint(self, server, root):
        root.mkdir(parents=True)
        (root / "v3_ctc.ckpt").write_bytes(CKPT)
        assert model_store.is_model_available("v3_ctc") is True


class TestEnsureModel:
    def test_download_success(self, server, root):
        progress: list[tuple[int, int]] = []
        result = model_store.ensure_model(NAME, on_progress=lambda d, t: progress.append((d, t)))
        assert result == root
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT
        assert (root / f"{NAME}_tokenizer.model").read_bytes() == TOKENIZER
        assert (root / f"{NAME}.verified").read_text() == CKPT_MD5
        assert not list(root.glob("*.part"))
        assert progress[-1] == (len(CKPT), len(CKPT))
        assert [d for d, _ in progress] == sorted(d for d, _ in progress)
        assert model_store.is_model_available(NAME)

    def test_concurrent_callers_download_once(self, server, root, monkeypatch):
        import threading

        monkeypatch.setattr(model_store, "_DOWNLOAD_BLOCK", 64)  # длинная загрузка — потоки пересекаются
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                model_store.ensure_model(NAME)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not errors
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT
        assert server.requests.count(f"{PRIMARY}/{NAME}.ckpt") == 1

    def test_sends_user_agent(self, server):
        model_store.ensure_model(NAME)
        assert all(agent and "BP-Transcriber" in agent for agent in server.user_agents)

    def test_verified_marker_skips_rehash_and_download(self, server, monkeypatch):
        model_store.ensure_model(NAME)
        server.requests.clear()
        monkeypatch.setattr(
            model_store, "_file_md5", lambda *a: pytest.fail("md5 пересчитан при наличии маркера")
        )
        model_store.ensure_model(NAME)
        assert server.requests == []

    def test_existing_file_is_verified_once(self, server, root):
        root.mkdir(parents=True)
        (root / f"{NAME}.ckpt").write_bytes(CKPT)
        (root / f"{NAME}_tokenizer.model").write_bytes(TOKENIZER)
        model_store.ensure_model(NAME)
        assert server.requests == []
        assert (root / f"{NAME}.verified").read_text() == CKPT_MD5

    def test_corrupted_existing_file_is_redownloaded(self, server, root):
        root.mkdir(parents=True)
        (root / f"{NAME}.ckpt").write_bytes(b"corrupted")
        (root / f"{NAME}.verified").write_text("stale")
        model_store.ensure_model(NAME)
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT
        assert (root / f"{NAME}.verified").read_text() == CKPT_MD5

    def test_md5_mismatch_removes_part_and_raises(self, server, root):
        server.routes[f"{PRIMARY}/{NAME}.ckpt"] = b"wrong" * 100
        server.routes[f"http://mirror.test/models/{NAME}.ckpt"] = b"also wrong"
        with pytest.raises(ModelLoadError, match="контрольная сумма"):
            model_store.ensure_model(NAME)
        assert not list(root.glob("*.part"))
        assert not (root / f"{NAME}.ckpt").exists()
        assert not (root / f"{NAME}.verified").exists()

    def test_primary_failure_falls_back_to_mirror(self, server, root):
        server.routes[f"{PRIMARY}/{NAME}.ckpt"] = urllib.error.URLError("offline")
        server.routes[f"http://mirror.test/models/{NAME}.ckpt"] = CKPT
        model_store.ensure_model(NAME)
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT
        ckpt_requests = [url for url in server.requests if url.endswith(".ckpt")]
        assert ckpt_requests == [f"{PRIMARY}/{NAME}.ckpt", f"http://mirror.test/models/{NAME}.ckpt"]

    def test_primary_bad_checksum_falls_back_to_mirror(self, server, root):
        server.routes[f"{PRIMARY}/{NAME}.ckpt"] = b"wrong" * 100
        server.routes[f"http://mirror.test/models/{NAME}.ckpt"] = CKPT
        model_store.ensure_model(NAME)
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT
        assert not list(root.glob("*.part"))

    def test_truncated_download_is_rejected(self, server, root):
        server.routes[f"{PRIMARY}/{NAME}.ckpt"] = FakeResponse(CKPT[:100], len(CKPT))
        server.routes[f"http://mirror.test/models/{NAME}.ckpt"] = CKPT
        model_store.ensure_model(NAME)
        assert (root / f"{NAME}.ckpt").read_bytes() == CKPT

    def test_all_sources_down(self, server, root):
        server.routes.clear()
        with pytest.raises(ModelLoadError, match="нет соединения"):
            model_store.ensure_model(NAME)
        assert not list(root.glob("*.part"))

    def test_cancel_removes_part(self, server, root, monkeypatch):
        monkeypatch.setattr(model_store, "_DOWNLOAD_BLOCK", 256)
        token = CancelToken()
        with pytest.raises(Cancelled):
            model_store.ensure_model(NAME, on_progress=lambda d, t: token.cancel(), cancel=token)
        assert not list(root.glob("*.part"))
        assert not (root / f"{NAME}.ckpt").exists()
        assert server.requests[-1] == f"{PRIMARY}/{NAME}.ckpt"  # зеркало при отмене не трогали

    def test_cancel_during_md5_verification(self, server, root):
        root.mkdir(parents=True)
        (root / f"{NAME}.ckpt").write_bytes(CKPT)
        (root / f"{NAME}_tokenizer.model").write_bytes(TOKENIZER)
        token = CancelToken()
        token.cancel()
        with pytest.raises(Cancelled):
            model_store.ensure_model(NAME, cancel=token)
        assert not (root / f"{NAME}.verified").exists()

    def test_unknown_model(self, server):
        with pytest.raises(ModelLoadError, match="неизвестная модель"):
            model_store.ensure_model("nope")
