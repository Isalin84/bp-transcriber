"""
Тесты для модуля device.
"""

import torch

from gigaam_transcriber.device import available_options, device_label, pick_device


class TestPickDevice:
    def test_cpu(self):
        assert pick_device("cpu") == torch.device("cpu")

    def test_auto_returns_known_type(self):
        assert pick_device("auto").type in {"cuda", "mps", "cpu"}

    def test_gpu_returns_known_type(self):
        assert pick_device("gpu").type in {"cuda", "mps", "cpu"}

    def test_auto_matches_gpu(self):
        assert pick_device("auto") == pick_device("gpu")


class TestDeviceLabel:
    def test_cpu(self):
        assert device_label(torch.device("cpu")) == "CPU"

    def test_mps(self):
        assert device_label(torch.device("mps")) == "Apple GPU (MPS)"

    def test_accepts_string(self):
        assert device_label("cpu") == "CPU"


class TestAvailableOptions:
    def test_shape(self):
        options = available_options()
        assert [o["id"] for o in options] == ["auto", "cpu", "gpu"]
        for option in options:
            assert set(option) == {"id", "label", "available"}
            assert isinstance(option["label"], str) and option["label"]
            assert isinstance(option["available"], bool)

    def test_auto_and_cpu_always_available(self):
        by_id = {o["id"]: o for o in available_options()}
        assert by_id["auto"]["available"] and by_id["cpu"]["available"]

    def test_gpu_availability_matches_pick_device(self):
        by_id = {o["id"]: o for o in available_options()}
        assert by_id["gpu"]["available"] == (pick_device("gpu").type != "cpu")
