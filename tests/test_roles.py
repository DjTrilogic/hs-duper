"""The cycle sequences, with the game replaced by recorded calls."""

import pytest

from hsduper import control
from hsduper.config import Config
from hsduper.roles import Stopped, ready_matcher, run_receiver, run_sender
from hsduper.signal import ChatEvent
from hsduper.transfer import Report, Result


def report(moved, result=Result.DONE):
    return Report(result, moved, 1, 0)


@pytest.fixture
def cfg():
    return Config({
        "ready_token": "hsd-ready",
        "blood_pact_room": 7216,
        "own_name": "DjTrilogic",
        "timing": {"after_ready_ms": 0},
    })


@pytest.fixture(autouse=True)
def no_abort():
    control.clear()
    yield
    control.clear()


class Sender:
    def __init__(self, deposits, withdraws):
        self.deposits, self.withdraws = list(deposits), list(withdraws)
        self.calls = []

    def deposit(self):
        self.calls.append("deposit")
        return self.deposits.pop(0)

    def withdraw(self):
        self.calls.append("withdraw")
        return self.withdraws.pop(0)

    def announce(self, token):
        self.calls.append(f"announce:{token}")


def test_sender_runs_deposit_announce_withdraw_in_that_order(cfg):
    side = Sender([report(60)], [report(60)])
    cycles = run_sender(cfg, 1, deposit=side.deposit, withdraw=side.withdraw,
                        announce=side.announce, log=lambda *_: None)
    assert side.calls == ["deposit", "announce:hsd-ready", "withdraw"]
    assert str(cycles[0]) == "cycle 1: deposited 60, withdrew 60"


def test_sender_repeats(cfg):
    side = Sender([report(60), report(60)], [report(60), report(60)])
    run_sender(cfg, 2, deposit=side.deposit, withdraw=side.withdraw,
               announce=side.announce, log=lambda *_: None)
    assert side.calls.count("announce:hsd-ready") == 2


def test_sender_stops_when_a_deposit_moves_nothing(cfg):
    """A stash that will not take anything must not be announced as ready."""
    side = Sender([report(0, Result.STALLED)], [report(60)])
    with pytest.raises(Stopped, match="deposit moved nothing"):
        run_sender(cfg, 3, deposit=side.deposit, withdraw=side.withdraw,
                   announce=side.announce, log=lambda *_: None)
    assert "announce:hsd-ready" not in side.calls


def test_sender_honours_the_abort_before_announcing(cfg):
    side = Sender([report(60)], [report(60)])

    def deposit():
        side.calls.append("deposit")
        control.request_abort()
        return report(60)

    with pytest.raises(control.Aborted):
        run_sender(cfg, 1, deposit=deposit, withdraw=side.withdraw,
                   announce=side.announce, log=lambda *_: None)
    assert side.calls == ["deposit"]


class Receiver:
    def __init__(self, event, reopens=True):
        self.event, self.reopens = event, reopens
        self.calls = []

    def wait_ready(self):
        self.calls.append("wait")
        return self.event

    def withdraw(self):
        self.calls.append("withdraw")
        return report(60)

    def close_stash(self):
        self.calls.append("close")

    def use_all(self):
        self.calls.append("use")
        return report(60)

    def open_stash(self):
        self.calls.append("open")
        return self.reopens


GO = ChatEvent("Partner", 7216, "hsd-ready", "999", 1)


def test_receiver_sequence(cfg):
    side = Receiver(GO)
    run_receiver(cfg, 1, wait_ready=side.wait_ready, withdraw=side.withdraw,
                 close_stash=side.close_stash, use_all=side.use_all,
                 open_stash=side.open_stash, log=lambda *_: None)
    assert side.calls == ["wait", "withdraw", "close", "use", "open"]


def test_receiver_stops_if_the_stash_does_not_reopen(cfg):
    """A click on a world object: if the character drifted, it fails, and the
    next cycle would run with no stash at all."""
    side = Receiver(GO, reopens=False)
    with pytest.raises(Stopped, match="did not reopen"):
        run_receiver(cfg, 2, wait_ready=side.wait_ready, withdraw=side.withdraw,
                     close_stash=side.close_stash, use_all=side.use_all,
                     open_stash=side.open_stash, log=lambda *_: None)


def test_receiver_stops_when_no_one_announces(cfg):
    side = Receiver(None)
    with pytest.raises(Stopped, match="never announced"):
        run_receiver(cfg, 1, wait_ready=side.wait_ready, withdraw=side.withdraw,
                     close_stash=side.close_stash, use_all=side.use_all,
                     open_stash=side.open_stash, log=lambda *_: None)


def test_ready_ignores_our_own_announcement(cfg):
    """The capture sees what we send as well as what we receive. A sender that
    does not exclude itself hears its own go signal and races itself."""
    assert not ready_matcher(cfg)(ChatEvent("DjTrilogic", 7216, "hsd-ready", "1", 0))
    assert ready_matcher(cfg)(ChatEvent("Partner", 7216, "hsd-ready", "2", 0))


def test_ready_ignores_other_channels(cfg):
    assert not ready_matcher(cfg)(ChatEvent("Partner", 0, "hsd-ready", "2", 0))


def test_ready_ignores_unrelated_chatter(cfg):
    assert not ready_matcher(cfg)(ChatEvent("Partner", 7216, "hello there", "2", 0))


def test_ready_matches_the_token_inside_a_longer_line(cfg):
    assert ready_matcher(cfg)(ChatEvent("Partner", 7216, "ok hsd-ready go", "2", 0))
