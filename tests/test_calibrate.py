"""Calibration sampling must ignore Hero Siege's slot hover highlight."""

import numpy as np

from hsduper import calibrate


def test_inventory_size_defaults_to_six_by_fifteen(monkeypatch):
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert calibrate.ask_grid_size("inventory") == (6, 15)


def test_stash_size_defaults_to_eighteen_by_seventeen(monkeypatch):
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    rows, cols = calibrate.ask_grid_size("stash")

    assert (rows, cols) == (18, 17)


def test_inventory_size_defaults_can_be_overridden(monkeypatch):
    answers = iter(["8", "12"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert calibrate.ask_grid_size("inventory") == (8, 12)


def test_sample_metric_parks_cursor_before_capture(monkeypatch):
    events = []
    frame = np.full((22, 33, 3), 40, dtype=np.uint8)

    monkeypatch.setattr(calibrate.winput, "move_to", lambda x, y: events.append(("move", x, y)))
    monkeypatch.setattr(calibrate.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    def grab(region):
        events.append(("grab", region))
        return frame

    monkeypatch.setattr(calibrate.capture, "grab", grab)

    assert calibrate.sample_metric((100, 200), 60.0, 40.0) == 40.0
    assert events == [
        ("move", *calibrate.SAMPLE_PARK_POINT),
        ("sleep", calibrate.SAMPLE_SETTLE_SECONDS),
        ("grab", (84, 189, 33, 22)),
    ]
