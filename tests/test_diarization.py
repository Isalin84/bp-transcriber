"""
Тесты диаризации: сопоставление слов со спикерами, пересборка сегментов,
гибридная кластеризация, pyannote-обвязка и проверка токена (без сети).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gigaam_transcriber import diarization as d
from gigaam_transcriber.asr_engine import AsrSegment
from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
from gigaam_transcriber.data_models import WordSegment
from gigaam_transcriber.diarization import (
    SpeakerTurn,
    assign_words,
    flatten_words,
    regroup,
)
from gigaam_transcriber.progress import Cancelled, CancelToken


def W(word: str, start: float, end: float) -> WordSegment:
    return WordSegment(word=word, start=start, end=end)


def T(start: float, end: float, speaker: str) -> SpeakerTurn:
    return SpeakerTurn(start=start, end=end, speaker=speaker)


def seg(words: list[WordSegment], start: float | None = None, end: float | None = None) -> AsrSegment:
    return AsrSegment(
        start=words[0].start if start is None else start,
        end=words[-1].end if end is None else end,
        text=" ".join(w.word for w in words),
        words=words,
    )


def audio_of(seconds: float) -> DecodedAudio:
    rng = np.random.default_rng(0)
    pcm = (rng.standard_normal(int(seconds * SAMPLE_RATE)) * 1000).astype(np.int16)
    return DecodedAudio(pcm=pcm, source=Path("test.wav"))


# =============================================================================
# assign_words
# =============================================================================


class TestAssignWords:
    def test_max_overlap(self):
        words = [W("a", 0.0, 1.0), W("b", 1.8, 2.6)]
        turns = [T(0.0, 2.0, "S0"), T(2.0, 4.0, "S1")]
        # "b": 0.2 с с S0, 0.6 с с S1
        assert assign_words(words, turns) == ["S0", "S1"]

    def test_speaker_change_mid_chunk(self):
        words = [W("раз", 0.0, 0.4), W("два", 0.5, 0.9), W("три", 1.2, 1.6), W("четыре", 1.7, 2.2)]
        turns = [T(0.0, 1.0, "S0"), T(1.1, 3.0, "S1")]
        assert assign_words(words, turns) == ["S0", "S0", "S1", "S1"]

    def test_short_insertion_is_smoothed(self):
        words = [W("Очки", 0.0, 0.4), W("были", 0.45, 0.8), W("на", 0.85, 0.9), W("складе", 0.95, 1.4)]
        turns = [T(0.0, 0.84, "S0"), T(0.84, 0.92, "S1"), T(0.92, 2.0, "S0")]
        assert assign_words(words, turns) == ["S0", "S0", "S0", "S0"]

    def test_real_short_reply_after_pause_is_kept(self):
        words = [W("Вопрос?", 0.0, 0.8), W("Да.", 1.5, 1.8), W("Продолжим", 2.6, 3.2)]
        turns = [T(0.0, 0.9, "S0"), T(1.4, 1.9, "S1"), T(2.5, 3.5, "S0")]
        assert assign_words(words, turns) == ["S0", "S1", "S0"]


# =============================================================================
# regroup / relabel
# =============================================================================


class TestRegroup:
    def test_speaker_change_splits(self):
        words = [W("Как", 0.0, 0.3), W("дела?", 0.4, 0.8), W("Хорошо.", 0.9, 1.4)]
        out = regroup([seg(words)], ["S0", "S0", "S1"])
        assert [(s.text, s.speaker) for s in out] == [("Как дела?", "S0"), ("Хорошо.", "S1")]

    def test_soft_max_splits_at_sentence_end(self):
        words = [W(f"слово{i}" + ("." if i == 5 else ""), i * 1.0, i * 1.0 + 0.8) for i in range(10)]
        out = regroup([seg(words)], None, soft_max=3.0, hard_max=100.0)
        # первая точка после 3 с — на слове 5
        assert out[0].words[-1].word == "слово5."
        assert len(out) == 2

    def test_hard_max(self):
        words = [W(f"w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(10)]
        out = regroup([seg(words)], None, soft_max=100.0, hard_max=4.0)
        assert all(s.end - s.start <= 4.0 for s in out)
        assert sum(len(s.words) for s in out) == 10

    def test_segment_without_words_kept_whole(self):
        asr = [
            seg([W("до", 0.0, 0.5)]),
            AsrSegment(start=1.0, end=3.0, text="текст без слов", words=[]),
            seg([W("после", 3.2, 3.6)]),
        ]
        units = flatten_words(asr)
        assert [u.word for u in units] == ["до", "текст без слов", "после"]
        speakers = assign_words(units, [T(0.0, 0.6, "S0"), T(0.8, 2.9, "S1"), T(2.9, 4.0, "S0")])
        assert speakers == ["S0", "S1", "S0"]
        out = regroup(asr, speakers)
        assert [(s.text, s.speaker, s.words) for s in out[1:2]] == [("текст без слов", "S1", [])]
        assert [s.text for s in out] == ["до", "текст без слов", "после"]

    def test_join_no_space_before_punctuation(self):
        words = [W("Итак", 0.0, 0.3), W(",", 0.3, 0.35), W("начнём", 0.4, 0.8), W("—", 0.85, 0.9),
                 W("сейчас", 0.9, 1.2), W("!", 1.2, 1.25)]
        out = regroup([seg(words)], None)
        assert out[0].text == "Итак, начнём — сейчас!"

# =============================================================================
# Гибридная диаризация
# =============================================================================


class _FakeEmbedder(d._Embedder):
    """Эмбеддинг = знак средней амплитуды окна: левая половина файла «S0», правая «S1»."""

    kind = "fake"

    def __init__(self) -> None:
        import torch

        self.device = torch.device("cpu")
        self.batches: list[int] = []

    def embed(self, batch):
        self.batches.append(len(batch))
        values = batch.numpy().mean(axis=1)
        return np.stack([[1.0, 0.0] if v > 0 else [0.0, 1.0] for v in values])


class TestHybridDiarizer:
    def test_turns_follow_embeddings(self, monkeypatch):
        pcm = np.concatenate([np.full(6 * SAMPLE_RATE, 3000), np.full(6 * SAMPLE_RATE, -3000)]).astype(np.int16)
        audio = DecodedAudio(pcm=pcm, source=Path("x.wav"))
        fake = _FakeEmbedder()
        monkeypatch.setattr(d, "_get_embedder", lambda device: fake)
        progress: list[float] = []
        turns = d.HybridDiarizer(device="cpu").diarize(audio, [(0.0, 5.5), (6.5, 12.0)], on_progress=progress.append)
        assert [(t.speaker, t.start, t.end) for t in turns] == [("SPEAKER_00", 0.0, 5.5), ("SPEAKER_01", 6.5, 12.0)]
        assert progress[-1] == 1.0
        assert max(fake.batches) <= d._HYBRID_BATCH["cpu"]

    def test_gpu_failure_retries_on_cpu(self, monkeypatch):
        """Виртуальный Mac: MPS «доступен», но падает на выделении памяти — повтор на CPU."""
        import torch

        class _BrokenGpu(_FakeEmbedder):
            def __init__(self) -> None:
                super().__init__()
                self.device = torch.device("mps")

            def embed(self, batch):
                raise RuntimeError("MPS backend out of memory")

        cpu = _FakeEmbedder()
        monkeypatch.setattr(d, "pick_device", lambda pref: torch.device("mps"))
        monkeypatch.setattr(d, "_get_embedder", lambda device: cpu if device.type == "cpu" else _BrokenGpu())
        pcm = np.concatenate([np.full(6 * SAMPLE_RATE, 3000), np.full(6 * SAMPLE_RATE, -3000)]).astype(np.int16)
        turns = d.HybridDiarizer().diarize(DecodedAudio(pcm=pcm, source=Path("x.wav")), [(0.0, 5.5), (6.5, 12.0)])
        assert [t.speaker for t in turns] == ["SPEAKER_00", "SPEAKER_01"]
        assert cpu.batches


# =============================================================================
# pyannote
# =============================================================================


class _FakePipeline:
    """Имитация pyannote-пайплайна: вызывает hook как настоящий."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.devices: list[str] = []

    def to(self, device):
        self.devices.append(device.type)
        return self

    def __call__(self, file, hook=None, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.calls <= self.fail_times:
            raise RuntimeError("MPS kernel failure")
        hook("segmentation", None, file=file, total=4, completed=2)
        hook("segmentation", None, file=file, total=4, completed=4)
        hook("speaker_counting", None, file=file)
        hook("embeddings", None, file=file, total=10, completed=5)
        hook("discrete_diarization", None, file=file)
        segment = lambda s, e: SimpleNamespace(start=s, end=e)  # noqa: E731
        annotation = MagicMock()
        annotation.itertracks.return_value = [(segment(2.0, 3.0), None, "SPEAKER_01"), (segment(0.0, 2.0), None, "SPEAKER_00")]
        return SimpleNamespace(exclusive_speaker_diarization=annotation, speaker_diarization=None)


class TestPyannoteDiarizer:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        d.clear_caches()
        yield
        d.clear_caches()

    def _patch(self, monkeypatch, pipeline):
        monkeypatch.setattr(d, "_load_pipeline", lambda token, device: ("pyannote/speaker-diarization-community-1", pipeline))

    def test_cancel_in_hook_and_reuse(self, monkeypatch):
        pipeline = _FakePipeline()
        self._patch(monkeypatch, pipeline)
        diarizer = d.PyannoteDiarizer(token=None, device="cpu")
        token = CancelToken()

        def on_progress(value: float) -> None:
            if value >= 0.3:
                token.cancel()

        with pytest.raises(Cancelled):
            diarizer.diarize(audio_of(1.0), on_progress=on_progress, cancel=token)
        # пайплайн остаётся в кэше и работает дальше
        assert len(diarizer.diarize(audio_of(1.0))) == 2
        assert pipeline.calls == 2

    def test_gpu_failure_retries_on_cpu(self, monkeypatch):
        import torch

        pipeline = _FakePipeline(fail_times=1)
        self._patch(monkeypatch, pipeline)
        monkeypatch.setattr(d, "pick_device", lambda pref: torch.device("mps"))
        diarizer = d.PyannoteDiarizer(token=None, device="gpu")
        turns = diarizer.diarize(audio_of(1.0))
        assert len(turns) == 2
        assert diarizer.device.type == "cpu"
        assert pipeline.devices[-1] == "cpu"

class TestLoadPipeline:
    def test_no_cache_no_token(self, monkeypatch):
        monkeypatch.setattr(d, "_local_pipeline_config", lambda repo: None)
        fake_module = SimpleNamespace(Pipeline=MagicMock())
        with patch.dict("sys.modules", {"pyannote.audio": fake_module}):
            with pytest.raises(d.PyannoteUnavailableError) as info:
                d._load_pipeline(None, None)
        assert info.value.reason == "token"
        fake_module.Pipeline.from_pretrained.assert_not_called()

    def test_local_config_used_first(self, monkeypatch):
        config = {"pipeline": {"name": "x", "params": {}}}
        monkeypatch.setattr(d, "_local_pipeline_config", lambda repo: config)
        fake_module = SimpleNamespace(Pipeline=MagicMock())
        fake_module.Pipeline.from_pretrained.return_value = "PIPE"
        with patch.dict("sys.modules", {"pyannote.audio": fake_module}):
            repo, pipe = d._load_pipeline("hf_x", None)
        assert (repo, pipe) == ("pyannote/speaker-diarization-community-1", "PIPE")
        fake_module.Pipeline.from_pretrained.assert_called_once_with(config)

class TestLocalConfig:
    def test_resolves_model_refs(self, tmp_path, monkeypatch):
        snapshot = tmp_path / "community"
        for sub, name in (("segmentation", "pytorch_model.bin"), ("embedding", "pytorch_model.bin"), ("plda", "plda.npz")):
            (snapshot / sub).mkdir(parents=True)
            (snapshot / sub / name).write_bytes(b"x")
        (snapshot / "config.yaml").write_text(
            "pipeline:\n  name: SpeakerDiarization\n  params:\n    clustering: VBxClustering\n"
            "    segmentation: $model/segmentation\n    embedding: $model/embedding\n    plda: $model/plda\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(d, "_local_snapshot", lambda repo: snapshot if "community" in repo else None)
        config = d._local_pipeline_config("pyannote/speaker-diarization-community-1")
        params = config["pipeline"]["params"]
        assert params["segmentation"] == str(snapshot / "segmentation")
        assert params["plda"] == str(snapshot / "plda")
        assert params["clustering"] == "VBxClustering"
        assert d.pyannote_cached() is True

    def test_incomplete_snapshot(self, tmp_path, monkeypatch):
        snapshot = tmp_path / "community"
        (snapshot / "plda").mkdir(parents=True)
        (snapshot / "plda" / "plda.npz").write_bytes(b"x")
        monkeypatch.setattr(d, "_local_snapshot", lambda repo: snapshot)
        assert d._local_pipeline_config("pyannote/speaker-diarization-community-1") is None

# =============================================================================
# check_token (без сети)
# =============================================================================


def _http_error(cls, status: int):
    response = MagicMock(status_code=status)
    return cls("error", response=response)


class TestCheckToken:
    def _run(self, whoami=None, auth=None):
        api = MagicMock()
        if isinstance(whoami, Exception):
            api.whoami.side_effect = whoami
        else:
            api.whoami.return_value = whoami or {"name": "user"}
        if auth is not None:
            api.auth_check.side_effect = auth
        with patch("huggingface_hub.HfApi", return_value=api):
            return d.check_token("hf_token")

    def test_ok(self):
        result = self._run()
        assert (result.ok, result.state, result.user) == (True, "ok", "user")
        assert all(r["ok"] for r in result.repos)
        assert result.to_dict()["repos"][0]["url"].startswith("https://huggingface.co/pyannote/")

    def test_invalid_token(self):
        from huggingface_hub.errors import HfHubHTTPError

        result = self._run(whoami=_http_error(HfHubHTTPError, 401))
        assert (result.ok, result.state) == (False, "invalid")

    def test_terms_not_accepted(self):
        from huggingface_hub.errors import GatedRepoError

        result = self._run(auth=lambda repo: (_ for _ in ()).throw(_http_error(GatedRepoError, 403)))
        assert (result.ok, result.state) == (False, "terms_not_accepted")
        assert "huggingface.co/pyannote" in result.message

    def test_network(self):
        result = self._run(whoami=ConnectionError("offline"))
        assert (result.ok, result.state) == (False, "network")
        assert all(r["ok"] is None for r in result.repos)

    def test_token_never_in_result(self):
        result = self._run()
        assert "hf_token" not in repr(result.to_dict())


