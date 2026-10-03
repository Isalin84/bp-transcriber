from __future__ import annotations

import json

import pytest

from bp_transcriber.history import HistoryError, HistoryStore


def test_save_list_load(tmp_path, result):
    store = HistoryStore(tmp_path / "history")
    pending = store.new_entry()
    pending.preview_path.write_bytes(b"\x00" * 100)
    tid = store.save_result(result, "/data/встреча.mp3", {"diarization": "hybrid"}, pending.preview_path, pending=pending)

    assert len(tid) == 12
    assert not any(p.name.startswith(".pending-") for p in (tmp_path / "history").iterdir())
    assert store.preview_file(tid) is not None

    items = store.list()
    assert [i["id"] for i in items] == [tid]
    assert items[0]["file_name"] == "встреча.mp3"
    assert items[0]["speakers"] == 2
    assert items[0]["preview"].startswith("Добрый день")

    t = store.load(tid)
    assert t["speakers"] == [
        {"id": "S1", "name": "Спикер 1", "color": 0},
        {"id": "S2", "name": "Спикер 2", "color": 1},
    ]
    assert [s["index"] for s in t["segments"]] == [0, 1, 2, 3]
    assert t["segments"][2]["speaker"] == "S2"
    assert t["segments"][0]["words"][0] == {"w": "Добрый", "s": 0.5, "e": 1.0}
    assert t["duration"] == 3663.0 and t["media_url"] is None

    raw = json.loads((tmp_path / "history" / tid / "transcript.json").read_text(encoding="utf-8"))
    assert raw["segments"][0]["speaker"] == "S1"  # в файле — id спикера
    assert raw["speakers"][1]["name"] == "Спикер 2"


def test_order_and_search(tmp_path, result, monkeypatch):
    store = HistoryStore(tmp_path / "h")
    import itertools

    clock = itertools.count(1_000_000.0, 10.0)  # монотонные часы: порядок не зависит от разрешения time()
    monkeypatch.setattr("bp_transcriber.history.time.time", lambda: next(clock))
    a = store.save_result(result, "/x/alpha.wav")
    b = store.save_result(result, "/x/Бета отчёт.wav")
    c = store.save_result(result, "/x/gamma.wav")
    assert [i["id"] for i in store.list()] == [c, b, a]
    assert [i["id"] for i in store.list("БЕТА")] == [b]
    assert [i["id"] for i in store.list("отчет")] == [b]  # ё ~ е
    assert len(store.list("елочные")) == 3  # поиск по тексту
    assert store.list("несуществующее") == []
    store.edit_segment(a, 2, "Уникальная фраза")
    assert [i["id"] for i in store.list("уникальная")] == [a]


def test_rename_and_edit(tmp_path, result):
    store = HistoryStore(tmp_path / "h")
    tid = store.save_result(result, "/x/a.wav")
    t = store.rename_speaker(tid, "S1", "  Иван   Петров ")
    assert t["speakers"][0]["name"] == "Иван Петров"
    with pytest.raises(HistoryError):
        store.rename_speaker(tid, "S9", "X")
    with pytest.raises(HistoryError):
        store.rename_speaker(tid, "S1", "   ")

    t = store.edit_segment(tid, 1, "Новый  текст")
    assert t["segments"][1]["text"] == "Новый текст"
    assert t["segments"][1]["words"] == []
    assert t["segments"][0]["words"]  # остальные сегменты не тронуты
    with pytest.raises(HistoryError):
        store.edit_segment(tid, 99, "x")

    reloaded = HistoryStore(tmp_path / "h").load(tid)
    assert reloaded["speakers"][0]["name"] == "Иван Петров"
    assert reloaded["segments"][1]["text"] == "Новый текст"


def test_delete(tmp_path, result):
    store = HistoryStore(tmp_path / "h")
    tid = store.save_result(result, "/x/a.wav")
    assert store.delete(tid) is True
    assert not (tmp_path / "h" / tid).exists()
    assert store.list() == []
    assert store.delete(tid) is False
    assert store.delete("../../etc") is False
    with pytest.raises(HistoryError):
        store.load(tid)


def test_corrupted_entry_skipped_and_index_rebuilt(tmp_path, result, caplog):
    root = tmp_path / "h"
    store = HistoryStore(root)
    good = store.save_result(result, "/x/good.wav")
    bad = store.save_result(result, "/x/bad.wav")
    (root / bad / "meta.json").write_text("{broken", encoding="utf-8")
    (root / "index.json").unlink()
    (root / ".pending-0123456789ab").mkdir()

    store2 = HistoryStore(root)
    assert [i["id"] for i in store2.list()] == [good]
    assert "bad" not in json.dumps(store2.list())
    assert (root / "index.json").exists()
    assert not (root / ".pending-0123456789ab").exists()
    assert "Пропускаю повреждённую запись" in caplog.text


def test_index_reconciled_with_disk(tmp_path, result):
    root = tmp_path / "h"
    store = HistoryStore(root)
    a = store.save_result(result, "/x/a.wav")
    b = store.save_result(result, "/x/b.wav")
    import shutil

    shutil.rmtree(root / a)  # удалено вручную
    assert [i["id"] for i in HistoryStore(root).list()] == [b]


def test_discard_pending(tmp_path):
    store = HistoryStore(tmp_path / "h")
    pending = store.new_entry()
    pending.preview_path.write_bytes(b"x")
    store.discard(pending)
    assert list((tmp_path / "h").iterdir()) == [] or all(
        p.name == "index.json" for p in (tmp_path / "h").iterdir()
    )
    assert store.list() == []
