"""Opening panels whose animation can outlast one fixed delay."""

from hsduper import panels


class PanelConfig:
    def __init__(self, anchor_reads, *, attempts=3, timeout_ms=1000):
        self.data = {
            "stash_object_point": [100, 200],
            "panel_open_attempts": attempts,
        }
        self.anchor_reads = iter(anchor_reads)
        self.timings = {
            "move_settle_ms": 0,
            "panel_open_timeout_ms": timeout_ms,
            "panel_poll_ms": 10,
        }

    def anchor_ok(self, name):
        assert name == "stash"
        return next(self.anchor_reads)

    def timing(self, name):
        return self.timings[name]


def test_open_stash_polls_until_a_late_anchor_appears(monkeypatch):
    cfg = PanelConfig([False, False, True])
    clicks = []
    sleeps = []
    monkeypatch.setattr(panels.winput, "move_to", lambda *_: None)
    monkeypatch.setattr(panels.winput, "left_click", lambda: clicks.append("click"))
    monkeypatch.setattr(panels.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert panels.open_stash(cfg, log=lambda *_: None)
    assert clicks == ["click"]
    assert len(sleeps) >= 2


def test_open_stash_retries_after_a_full_detection_timeout(monkeypatch):
    cfg = PanelConfig([False, True], attempts=2, timeout_ms=0)
    clicks = []
    logs = []
    monkeypatch.setattr(panels.winput, "move_to", lambda *_: None)
    monkeypatch.setattr(panels.winput, "left_click", lambda: clicks.append("click"))
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert panels.open_stash(cfg, log=logs.append)
    assert clicks == ["click", "click"]
    assert any("retrying" in line for line in logs)


def test_open_stash_stops_after_the_configured_attempts(monkeypatch):
    cfg = PanelConfig([False, False, False], attempts=3, timeout_ms=0)
    clicks = []
    monkeypatch.setattr(panels.winput, "move_to", lambda *_: None)
    monkeypatch.setattr(panels.winput, "left_click", lambda: clicks.append("click"))
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert not panels.open_stash(cfg, log=lambda *_: None)
    assert clicks == ["click", "click", "click"]
