"""The move loop, with the game replaced by a script of what the grid shows."""

from contextlib import contextmanager

import numpy as np
import pytest

import hsduper.transfer as transfer_module
from hsduper import control, doctor, winput
from hsduper.config import Config
from hsduper.grid import Grid
from hsduper.transfer import NotFocused, PanelClosed, Result, return_cursor_item, transfer


def full(n=2):
    return np.ones((n, n), bool)


def empty(n=2):
    return np.zeros((n, n), bool)


def one(n=2):
    mask = np.zeros((n, n), bool)
    mask[0, 0] = True
    return mask


class ScriptedGrid(Grid):
    """A grid that reports a scripted sequence instead of looking at the screen."""

    def __init__(self, states):
        super().__init__("src", 100.0, 200.0, 50.0, 40.0, 2, 2, 50.0)
        self.states = list(states)

    def occupied(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class MutableGrid(Grid):
    def __init__(self, name, mask):
        super().__init__(name, 100.0, 200.0, 50.0, 40.0, 2, 2, 50.0)
        self.mask = mask.copy()

    def occupied(self):
        return self.mask.copy()


@pytest.fixture
def cfg():
    """Anchors are stubbed as present. Whether an anchor is satisfied is
    config's business and is tested there; these cover the loop."""
    conf = Config(
        {
            "grids": {},
            "anchors": {},
            "park": [5, 5],
            "timing": {
                "move_settle_ms": 0,
                "click_delay_ms": 0,
                "jitter_ms": 0,
                "tooltip_ms": 0,
                "pass_settle_ms": 0,
                "max_passes": 6,
            },
        }
    )
    conf.missing_anchors = lambda names: []
    return conf


@pytest.fixture(autouse=True)
def fake_mouse(monkeypatch):
    """No real cursor is moved, and control.check still sees a consistent one."""
    state = {"pos": (0, 0), "clicks": []}
    monkeypatch.setattr(winput, "move_to", lambda x, y: state.__setitem__("pos", (x, y)))
    monkeypatch.setattr(winput, "get_cursor_pos", lambda: state["pos"])
    monkeypatch.setattr(doctor, "game_is_foreground", lambda expected=None: True)
    control.clear()
    yield state
    control.clear()


@pytest.fixture
def click(fake_mouse):
    return lambda: fake_mouse["clicks"].append(fake_mouse["pos"])


def test_drains_the_source_and_reports_done(cfg, click, fake_mouse):
    grid = ScriptedGrid([full(), empty(), empty()])
    report = transfer(grid, cfg, click=click, log=lambda *_: None)
    assert report.result is Result.DONE
    assert (report.moved, report.left, report.passes) == (4, 0, 1)
    assert len(fake_mouse["clicks"]) == 4


def test_clicks_land_on_the_cell_centres(cfg, click, fake_mouse):
    grid = ScriptedGrid([one(), empty(), empty()])
    transfer(grid, cfg, click=click, log=lambda *_: None)
    assert fake_mouse["clicks"] == [(100, 200)]


def test_cursor_recovery_prefers_an_empty_inventory_cell(cfg, fake_mouse, monkeypatch):
    source = ScriptedGrid([full()])
    destination = ScriptedGrid([empty(), one()])
    clicks = []
    monkeypatch.setattr(
        winput,
        "left_click",
        lambda hold_ms: clicks.append((fake_mouse["pos"], hold_ms)),
    )

    assert return_cursor_item(source, destination, cfg, log=lambda *_: None)
    assert clicks == [((100, 200), 70)]


def test_cursor_recovery_falls_back_to_an_empty_stash_cell(
    cfg, fake_mouse, monkeypatch
):
    source = ScriptedGrid([empty(), one()])
    destination = ScriptedGrid([full()])
    clicks = []
    monkeypatch.setattr(
        winput,
        "left_click",
        lambda hold_ms: clicks.append((fake_mouse["pos"], hold_ms)),
    )

    assert return_cursor_item(source, destination, cfg, log=lambda *_: None)
    assert clicks == [((100, 200), 70)]


def test_default_transfer_holds_ctrl_once_for_the_whole_pass(
    cfg, fake_mouse, monkeypatch
):
    events = []

    @contextmanager
    def held_ctrl(*, settle_ms, mode):
        events.append(("ctrl-down", settle_ms, mode))
        try:
            yield
        finally:
            events.append(("ctrl-up", settle_ms, mode))

    monkeypatch.setattr(winput, "hold_ctrl", held_ctrl)
    monkeypatch.setattr(
        winput,
        "left_click_with_ctrl_held",
        lambda hold_ms, settle_ms, mode: events.append(
            ("left", fake_mouse["pos"], hold_ms, settle_ms, mode)
        ),
    )
    monkeypatch.setattr(
        winput,
        "ctrl_left_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("CTRL must not be toggled around each slot")
        ),
    )
    grid = ScriptedGrid([full(), empty(), empty()])

    transfer(grid, cfg, log=lambda *_: None)

    assert events[0] == ("ctrl-down", 70, "both")
    assert events[-1] == ("ctrl-up", 70, "both")
    clicks = [event for event in events if event[0] == "left"]
    assert len(clicks) == 4
    assert all(event[3:] == (70, "both") for event in clicks)
    assert not any(event[0] == "ctrl-down" for event in events[1:-1])


def test_a_custom_delay_can_be_used_between_clicks(cfg, click, monkeypatch):
    sleeps = []
    monkeypatch.setattr(transfer_module.time, "sleep", sleeps.append)
    grid = ScriptedGrid([one(), empty(), empty()])

    transfer(grid, cfg, click=click, click_delay_ms=250, log=lambda *_: None)

    assert sleeps.count(0.25) == 1


def test_a_pass_that_moves_nothing_stops_instead_of_clicking_on(cfg, click, fake_mouse):
    """A full destination looks exactly like this, and is the reason the loop is
    progress-based rather than counted."""
    grid = ScriptedGrid([full(), full()])
    report = transfer(grid, cfg, click=click, log=lambda *_: None)
    assert report.result is Result.STALLED
    assert (report.moved, report.left) == (0, 4)
    assert len(fake_mouse["clicks"]) == 4, "it clicked one pass, then gave up"


def test_leftovers_are_picked_up_by_a_later_pass(cfg, click, fake_mouse):
    """What a multi-cell item looks like from here: clicking one of its cells
    moves the item, so the rest read empty next time round."""
    grid = ScriptedGrid([full(), one(), one(), empty(), empty()])
    report = transfer(grid, cfg, click=click, log=lambda *_: None)
    assert report.result is Result.DONE
    assert (report.moved, report.passes) == (4, 2)


def test_gives_up_after_max_passes(cfg, click):
    cfg.data["timing"]["max_passes"] = 3
    # never empties, but always moves one, so it never counts as stalled either
    grid = ScriptedGrid([full(4), full(3), full(3), full(2), full(2), one(2), full(2)])
    report = transfer(grid, cfg, click=click, log=lambda *_: None)
    assert report.result is Result.MAX_PASSES
    assert report.passes == 3


def test_refuses_to_click_when_a_panel_is_closed(cfg, click, fake_mouse):
    """A pass must stop before the first click when its required panel is shut."""
    cfg.missing_anchors = lambda names: ["stash"]
    grid = ScriptedGrid([full(), empty()])
    with pytest.raises(PanelClosed, match="required panel state cannot be verified"):
        transfer(grid, cfg, click=click, log=lambda *_: None)
    assert fake_mouse["clicks"] == []


def test_abort_stops_within_one_click(cfg, fake_mouse):
    clicks = []

    def click():
        clicks.append(fake_mouse["pos"])
        control.request_abort()

    grid = ScriptedGrid([full(), empty()])
    with pytest.raises(control.Aborted):
        transfer(grid, cfg, click=click, log=lambda *_: None)
    assert len(clicks) == 1


def test_a_hand_on_the_mouse_stops_the_run(cfg, fake_mouse):
    def click():
        fake_mouse["pos"] = (fake_mouse["pos"][0] + 400, fake_mouse["pos"][1])

    grid = ScriptedGrid([full(), empty()])
    with pytest.raises(control.Aborted, match="cursor moved on its own"):
        transfer(grid, cfg, click=click, log=lambda *_: None)


def test_ctrl_is_released_even_when_the_click_raises(monkeypatch):
    """Otherwise an abort mid-click leaves CTRL held down for the whole desktop."""
    sent = []

    def fake_send(*inputs):
        for item in inputs:
            if item.type == winput.INPUT_KEYBOARD:
                sent.append(("key", item.ki.wScan, bool(item.ki.dwFlags & winput.KEYEVENTF_KEYUP)))
            else:
                sent.append(("mouse", item.mi.dwFlags))
                if item.mi.dwFlags == winput.MOUSEEVENTF_RIGHTDOWN:
                    raise RuntimeError("boom")

    monkeypatch.setattr(winput, "_send", fake_send)
    monkeypatch.setattr(winput, "ctrl_is_down", lambda: True)
    with pytest.raises(RuntimeError):
        winput.ctrl_right_click()
    assert ("key", winput.SC_LCONTROL, True) in sent, "CTRL was left down"


def test_held_ctrl_is_released_when_a_pass_raises(monkeypatch):
    sent = []

    def fake_send(*inputs):
        for item in inputs:
            sent.append(bool(item.ki.dwFlags & winput.KEYEVENTF_KEYUP))

    monkeypatch.setattr(winput, "_send", fake_send)
    states = iter([False, True])
    monkeypatch.setattr(winput, "ctrl_is_down", lambda: next(states))
    monkeypatch.setattr(winput.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="boom"):
        with winput.hold_ctrl(mode="both"):
            raise RuntimeError("boom")

    assert sent == [True, True, False, False, True, True]


def test_held_ctrl_forces_and_checks_a_fresh_up_down_edge(monkeypatch):
    events = []
    monkeypatch.setattr(
        winput,
        "ensure_ctrl_up",
        lambda **kwargs: events.append(("checked-up", kwargs)),
    )
    monkeypatch.setattr(
        winput,
        "ensure_ctrl_down",
        lambda **kwargs: events.append(("checked-down", kwargs)),
    )
    monkeypatch.setattr(
        winput, "_send_ctrl", lambda up, mode: events.append(("release", up, mode))
    )
    monkeypatch.setattr(winput.time, "sleep", lambda *_: None)

    with winput.hold_ctrl(settle_ms=80, mode="both"):
        events.append(("body",))

    assert events[:2] == [
        ("checked-up", {"settle_ms": 80, "mode": "both", "attempts": 3}),
        ("checked-down", {"settle_ms": 80, "mode": "both", "attempts": 3}),
    ]
    assert events[2:] == [("body",), ("release", True, "both")]


def test_withdraw_recovers_cursor_before_clicking_another_stash_item(
    cfg, fake_mouse, monkeypatch
):
    cfg.data["timing"]["transfer_verify_ms"] = 0
    source = MutableGrid("stash", full())
    destination = MutableGrid("inventory", empty())
    events = []
    click_count = 0

    @contextmanager
    def held_ctrl(*, settle_ms, mode):
        events.append(("ctrl-down", mode))
        try:
            yield
        finally:
            events.append(("ctrl-up", mode))

    def missed_ctrl_click(*_args, **_kwargs):
        nonlocal click_count
        click_count += 1
        row = round((fake_mouse["pos"][1] - source.y0) / source.pitch_y)
        col = round((fake_mouse["pos"][0] - source.x0) / source.pitch_x)
        events.append(("click", row, col))
        source.mask[row, col] = False
        if click_count > 1:
            destination.mask[row, col] = True

    def recover():
        events.append(("recover",))
        destination.mask[0, 0] = True
        return True

    monkeypatch.setattr(winput, "hold_ctrl", held_ctrl)
    monkeypatch.setattr(winput, "left_click_with_ctrl_held", missed_ctrl_click)

    report = transfer(
        source,
        cfg,
        destination=destination,
        recover_cursor=recover,
        log=lambda *_: None,
    )

    first_recovery = events.index(("recover",))
    assert [event for event in events[:first_recovery] if event[0] == "click"] == [
        ("click", 0, 0)
    ]
    assert events[first_recovery - 1] == ("ctrl-up", "both")
    assert ("ctrl-down", "vk") in events[first_recovery + 1:]
    assert report.result is Result.DONE


def test_both_ctrl_mode_really_sends_vk_and_scancode():
    inputs = winput._ctrl_inputs(up=False, mode="both")

    assert len(inputs) == 2
    assert inputs[0].ki.wVk == winput.VK_LCONTROL
    assert not (inputs[0].ki.dwFlags & winput.KEYEVENTF_SCANCODE)
    assert inputs[1].ki.wScan == winput.SC_LCONTROL
    assert inputs[1].ki.dwFlags & winput.KEYEVENTF_SCANCODE


def test_ctrl_must_be_confirmed_before_a_click_can_be_sent(monkeypatch):
    sends = []
    monkeypatch.setattr(winput, "_send_ctrl", lambda up, mode: sends.append((up, mode)))
    monkeypatch.setattr(winput, "ctrl_is_down", lambda: False)
    monkeypatch.setattr(winput.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="refusing to send a plain click"):
        winput.ensure_ctrl_down(settle_ms=55, mode="both")

    assert sends == [(False, "both")] * 3


def test_checked_ctrl_and_mouse_down_are_sent_in_the_same_batch(monkeypatch):
    batches = []
    checks = []
    monkeypatch.setattr(
        winput,
        "ensure_ctrl_down",
        lambda *, settle_ms, mode: checks.append((settle_ms, mode)),
    )
    monkeypatch.setattr(winput, "_send", lambda *inputs: batches.append(inputs))
    monkeypatch.setattr(winput.time, "sleep", lambda *_: None)

    winput.left_click_with_ctrl_held(hold_ms=70, settle_ms=55, mode="both")

    assert checks == [(55, "both")]
    assert len(batches[0]) == 3
    assert batches[0][0].type == winput.INPUT_KEYBOARD
    assert batches[0][1].type == winput.INPUT_KEYBOARD
    assert batches[0][2].mi.dwFlags == winput.MOUSEEVENTF_LEFTDOWN
    assert batches[1][0].mi.dwFlags == winput.MOUSEEVENTF_LEFTUP


def test_stops_when_the_game_stops_being_the_focused_window(cfg, click, fake_mouse, monkeypatch):
    """Alt-tabbing mid-run must stop it, not carry on clicking into whatever
    window came forward."""
    monkeypatch.setattr(doctor, "game_is_foreground", lambda expected=None: False)
    grid = ScriptedGrid([full(), empty()])
    with pytest.raises(NotFocused, match="foreground window"):
        transfer(grid, cfg, click=click, log=lambda *_: None)
    assert fake_mouse["clicks"] == []
