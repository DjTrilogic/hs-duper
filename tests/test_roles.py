"""The cycle sequences, with the game replaced by recorded calls."""

import pytest

from hsduper import control
from hsduper.config import Config
from hsduper.roles import CycleSignals, Stopped, ready_matcher, run_receiver, run_sender
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
        "timing": {},
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

    def wait_signal(self, token):
        self.calls.append(f"wait:{token}")
        return token


SESSION = "run-abc"
SIGNALS = CycleSignals("hsd-ready", SESSION, 1)


def test_cycle_signals_are_distinct_and_parse_the_deposit(cfg):
    signals = CycleSignals("hsd-ready", SESSION, 3)
    assert len({signals.deposited, signals.visible, signals.done}) == 3
    assert CycleSignals.from_deposited("hsd-ready", signals.deposited) == signals
    assert CycleSignals.from_deposited("hsd-ready", signals.visible) is None
    assert CycleSignals.from_deposited("hsd-ready", "old unrelated message") is None


def test_sender_waits_for_visible_then_done(cfg):
    side = Sender([report(60)], [report(60)])
    signals = CycleSignals("hsd-ready", SESSION, 1)
    cycles = run_sender(
        cfg, 1, session=SESSION, deposit=side.deposit, withdraw=side.withdraw,
        announce=side.announce, wait_signal=side.wait_signal, log=lambda *_: None,
    )
    assert side.calls == [
        "deposit",
        f"announce:{signals.deposited}",
        f"wait:{signals.visible}",
        "withdraw",
        f"wait:{signals.done}",
    ]
    assert str(cycles[0]) == "cycle 1: deposited 60, withdrew 60"


def test_sender_repeats(cfg):
    side = Sender([report(60), report(60)], [report(60), report(60)])
    run_sender(
        cfg, 2, session=SESSION, deposit=side.deposit, withdraw=side.withdraw,
        announce=side.announce, wait_signal=side.wait_signal, log=lambda *_: None,
    )
    first_done = f"wait:{CycleSignals('hsd-ready', SESSION, 1).done}"
    second_deposit = f"announce:{CycleSignals('hsd-ready', SESSION, 2).deposited}"
    assert side.calls.index(first_done) < side.calls.index(second_deposit)


def test_sender_stops_when_a_deposit_moves_nothing(cfg):
    """A stash that will not take anything must not be announced as ready."""
    side = Sender([report(0, Result.STALLED)], [report(60)])
    with pytest.raises(Stopped, match="deposit moved nothing"):
        run_sender(
            cfg, 3, session=SESSION, deposit=side.deposit, withdraw=side.withdraw,
            announce=side.announce, wait_signal=side.wait_signal, log=lambda *_: None,
        )
    assert all(not call.startswith("announce:") for call in side.calls)


def test_sender_honours_the_abort_before_announcing(cfg):
    side = Sender([report(60)], [report(60)])

    def deposit():
        side.calls.append("deposit")
        control.request_abort()
        return report(60)

    with pytest.raises(control.Aborted):
        run_sender(
            cfg, 1, session=SESSION, deposit=deposit, withdraw=side.withdraw,
            announce=side.announce, wait_signal=side.wait_signal, log=lambda *_: None,
        )
    assert side.calls == ["deposit"]


def test_sender_stops_without_the_visible_confirmation(cfg):
    side = Sender([report(60)], [report(60)])
    with pytest.raises(Stopped, match="never confirmed.*visible"):
        run_sender(
            cfg, 1, session=SESSION, deposit=side.deposit, withdraw=side.withdraw,
            announce=side.announce, wait_signal=lambda _: None, log=lambda *_: None,
        )
    assert "withdraw" not in side.calls


def test_sender_does_not_start_the_next_cycle_without_done(cfg):
    side = Sender([report(60), report(60)], [report(60), report(60)])
    answers = iter([SIGNALS.visible, None])
    with pytest.raises(Stopped, match="never confirmed.*used"):
        run_sender(
            cfg, 2, session=SESSION, deposit=side.deposit, withdraw=side.withdraw,
            announce=side.announce, wait_signal=lambda _: next(answers),
            log=lambda *_: None,
        )
    assert side.calls.count("deposit") == 1


class Receiver:
    def __init__(self, event, opens=True, visible=60, used=None):
        self.event, self.opens, self.visible = event, opens, visible
        self.used = used or report(60)
        self.calls = []

    def wait_deposited(self):
        self.calls.append("wait")
        return self.event

    def announce(self, token):
        self.calls.append(f"announce:{token}")

    def withdraw(self):
        self.calls.append("withdraw")
        return report(60)

    def close_stash(self):
        self.calls.append("close")
        return True

    def stash_item_count(self):
        self.calls.append("scan-stash")
        return self.visible

    def use_all(self):
        self.calls.append("use")
        return self.used

    def open_stash(self):
        self.calls.append("open-stash")
        return self.opens

    def open_inventory(self):
        self.calls.append("open-inventory")
        return True


def run_receiver_side(cfg, side, cycles=1):
    return run_receiver(
        cfg, cycles, wait_deposited=side.wait_deposited, announce=side.announce,
        open_stash=side.open_stash, stash_item_count=side.stash_item_count,
        withdraw=side.withdraw, close_stash=side.close_stash,
        open_inventory=side.open_inventory, use_all=side.use_all,
        log=lambda *_: None,
    )


def test_receiver_sequence(cfg):
    side = Receiver(SIGNALS.deposited)
    run_receiver_side(cfg, side)
    assert side.calls == [
        "close",
        "open-inventory",
        "wait",
        "open-stash",
        "scan-stash",
        f"announce:{SIGNALS.visible}",
        "withdraw",
        "close",
        "open-inventory",
        "use",
        f"announce:{SIGNALS.done}",
    ]


def test_receiver_stops_if_the_stash_does_not_open(cfg):
    side = Receiver(SIGNALS.deposited, opens=False)
    with pytest.raises(Stopped, match="did not open"):
        run_receiver_side(cfg, side, cycles=2)


def test_receiver_does_not_acknowledge_an_empty_stash(cfg):
    side = Receiver(SIGNALS.deposited, visible=0)
    with pytest.raises(Stopped, match="no deposited items"):
        run_receiver_side(cfg, side)
    assert all(not call.startswith("announce:") for call in side.calls)


def test_receiver_does_not_announce_done_until_every_item_was_used(cfg):
    incomplete = Report(Result.MAX_PASSES, moved=59, passes=6, left=1)
    side = Receiver(SIGNALS.deposited, used=incomplete)
    with pytest.raises(Stopped, match="using the items did not finish"):
        run_receiver_side(cfg, side)
    assert f"announce:{SIGNALS.visible}" in side.calls
    assert f"announce:{SIGNALS.done}" not in side.calls


def test_receiver_stops_when_no_one_announces(cfg):
    side = Receiver(None)
    with pytest.raises(Stopped, match="never announced"):
        run_receiver_side(cfg, side)


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
