"""Panel hotkeys and their idempotent anchor checks."""

from hsduper import panels


class PanelConfig:
    def __init__(self, *, stash=False, inventory=False):
        self.data = {}
        self.state = {"stash": stash, "inventory": inventory}

    def anchor_ok(self, name):
        return self.state[name]

    def timing(self, _):
        return 0


def test_close_stash_does_not_press_escape_when_it_is_already_closed(monkeypatch):
    cfg = PanelConfig(stash=False, inventory=True)
    pressed = []
    monkeypatch.setattr(panels.winput, "press_escape", lambda: pressed.append("esc"))

    assert panels.close_stash(cfg, log=lambda *_: None)
    assert pressed == []


def test_open_stash_uses_f_and_confirms_the_anchor(monkeypatch):
    cfg = PanelConfig(stash=False, inventory=True)
    pressed = []

    def press_f():
        pressed.append("f")
        cfg.state["stash"] = True

    monkeypatch.setattr(panels.winput, "press_interact", press_f)
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert panels.open_stash(cfg, log=lambda *_: None)
    assert pressed == ["f"]


def test_open_inventory_uses_i_and_confirms_the_anchor(monkeypatch):
    cfg = PanelConfig(stash=False, inventory=False)
    pressed = []

    def press_i():
        pressed.append("i")
        cfg.state["inventory"] = True

    monkeypatch.setattr(panels.winput, "press_inventory", press_i)
    monkeypatch.setattr(panels.time, "sleep", lambda *_: None)

    assert panels.open_inventory(cfg, log=lambda *_: None)
    assert pressed == ["i"]
