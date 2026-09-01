"""Sending a Blood Pact line, with the input layer recorded rather than sent."""

import pytest

from hsduper import chat, winput
from hsduper.config import Config


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(winput, "move_to", lambda x, y: calls.append(("move", x, y)))
    monkeypatch.setattr(winput, "left_click", lambda *a, **k: calls.append(("click",)))
    monkeypatch.setattr(winput, "press_enter", lambda: calls.append(("enter",)))
    monkeypatch.setattr(winput, "press_escape", lambda: calls.append(("escape",)))
    monkeypatch.setattr(winput, "type_text", lambda t, **k: calls.append(("type", t)))
    return calls


@pytest.fixture
def cfg():
    return Config({
        "chat_tab_point": [994, 576],
        "chat_input_point": [700, 900],
        "timing": {"chat_step_ms": 0},
    })


def test_the_tab_is_picked_before_typing(cfg, sent):
    """The game keeps whichever tab was last used, so a line that assumes Blood
    Pact is still selected can go to Trade without anything looking wrong."""
    chat.send(cfg, "hsd-ready", log=lambda *_: None)
    assert sent[0] == ("enter",)
    assert ("move", 994, 576) in sent
    assert sent.index(("move", 994, 576)) < sent.index(("type", "hsd-ready"))


def test_it_sends_with_enter(cfg, sent):
    chat.send(cfg, "hsd-ready", log=lambda *_: None)
    assert sent[-1] == ("enter",)


def test_no_escape_is_ever_sent(cfg, sent):
    """Sending closes the chat by itself. A trailing ESC reaches the game
    instead, closes the stash, and the next pass finds no panel."""
    chat.send(cfg, "hsd-ready", log=lambda *_: None)
    assert ("escape",) not in sent


def test_uncalibrated_chat_points_are_refused(sent):
    with pytest.raises(KeyError, match="calibrate chat"):
        chat.send(Config({}), "hsd-ready", log=lambda *_: None)
    assert sent == []
