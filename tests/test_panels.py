"""Opening panels whose animation can outlast one fixed delay."""

from hsduper import panels
from hsduper.transfer import Report, Result


class PanelConfig:
    def __init__(self, anchor_reads, *, attempts=3, timeout_ms=1000):
        self.data = {
            "panel_open_attempts": attempts,
        }
        self.anchor_reads = iter(anchor_reads)
        self.timings = {
            "button_hold_ms": 70,
            "move_settle_ms": 0,
            "panel_open_timeout_ms": timeout_ms,
            "panel_poll_ms": 10,
            "use_click_delay_ms": 250,
        }

    def anchor_ok(self, name):
        assert name == "stash"
        return next(self.anchor_reads)

    def timing(self, name):
        return self.timings[name]


class InventoryConfig(PanelConfig):
    def anchor_ok(self, name):
        assert name == "inventory_standalone"
        return next(self.anchor_reads)


def test_press_interact_uses_the_f_key(monkeypatch):
    taps = []
    monkeypatch.setattr(panels.winput, "tap", lambda scan, vk: taps.append((scan, vk)))

    panels.winput.press_interact()

    assert taps == [(panels.winput.SC_F, panels.winput.VK_F)]


def test_open_stash_polls_until_a_late_anchor_appears(monkeypatch):
    cfg = PanelConfig([False, False, True])
    presses = []
    sleeps = []
    monkeypatch.setattr(panels.winput, "press_interact", lambda: presses.append("f"))
    monkeypatch.setattr(panels.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert panels.open_stash(cfg, log=lambda *_: None)
    assert presses == ["f"]
    assert len(sleeps) >= 2


def test_open_stash_retries_after_a_full_detection_timeout(monkeypatch):
    cfg = PanelConfig([False, True], attempts=2, timeout_ms=0)
    presses = []
    logs = []
    monkeypatch.setattr(panels.winput, "press_interact", lambda: presses.append("f"))
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert panels.open_stash(cfg, log=logs.append)
    assert presses == ["f", "f"]
    assert any("retrying" in line for line in logs)


def test_open_stash_stops_after_the_configured_attempts(monkeypatch):
    cfg = PanelConfig([False, False, False], attempts=3, timeout_ms=0)
    presses = []
    monkeypatch.setattr(panels.winput, "press_interact", lambda: presses.append("f"))
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert not panels.open_stash(cfg, log=lambda *_: None)
    assert presses == ["f", "f", "f"]


def test_open_inventory_presses_i_and_waits_for_its_anchor(monkeypatch):
    cfg = InventoryConfig([False, False, True])
    presses = []
    monkeypatch.setattr(panels.winput, "press_inventory", lambda: presses.append("i"))
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert panels.open_inventory(cfg, log=lambda *_: None)
    assert presses == ["i"]


def test_open_inventory_stops_after_the_configured_attempts(monkeypatch):
    cfg = InventoryConfig([False, False, False, False], attempts=3, timeout_ms=0)
    presses = []
    monkeypatch.setattr(panels.winput, "press_inventory", lambda: presses.append("i"))

    assert not panels.open_inventory(cfg, log=lambda *_: None)
    assert presses == ["i", "i", "i"]


def test_use_all_right_clicks_without_ctrl(monkeypatch):
    cfg = PanelConfig([], timeout_ms=0)
    cfg.timings["button_hold_ms"] = 73
    clicks = []
    transfer_args = {}

    monkeypatch.setattr(
        panels.winput, "right_click", lambda hold_ms: clicks.append(("right", hold_ms))
    )
    monkeypatch.setattr(
        panels.winput, "ctrl_right_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CTRL must stay up")),
    )

    def fake_transfer(grid, config, **kwargs):
        transfer_args.update(kwargs)
        kwargs["click"]()
        return Report(Result.DONE, 1, 1, 0)

    monkeypatch.setattr(panels, "transfer", fake_transfer)

    result = panels.use_all(cfg, object(), log=lambda *_: None)

    assert result.result is Result.DONE
    assert clicks == [("right", 73)]
    assert transfer_args["anchors"] == ("inventory_standalone",)
    assert transfer_args["forbidden"] == ("stash",)
    assert transfer_args["click_delay_ms"] == 250
