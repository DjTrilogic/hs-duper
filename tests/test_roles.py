"""The cycle sequences, with the game replaced by recorded calls."""

import pytest

from hsduper import control
from hsduper.config import Config
from hsduper.roles import Stopped, ready_matcher, run_receiver, run_sender
from hsduper.signal import ChatEvent
from hsduper.transfer import Report, Result


def report(moved, result=Result.DONE, left=0):
    return Report(result, moved, 1, left)


@pytest.fixture
def cfg():
    return Config({
        "ready_token": "hsd-ready",
        "seen_token": "hsd-seen",
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
    def __init__(self, deposits, withdraws, confirmed=True, stash_opens=True,
                 has_items=True):
        self.deposits, self.withdraws = list(deposits), list(withdraws)
        self.confirmed, self.stash_opens = confirmed, stash_opens
        self.has_items = has_items
        self.calls = []

    def have_items(self):
        self.calls.append("have_items")
        return self.has_items

    def ensure_stash(self):
        self.calls.append("ensure_stash")
        return self.stash_opens

    def deposit(self):
        self.calls.append("deposit")
        return self.deposits.pop(0)

    def announce(self, token):
        self.calls.append("announce:" + token)

    def wait_seen(self):
        self.calls.append("wait_seen")
        return self.confirmed

    def withdraw(self):
        self.calls.append("withdraw")
        return self.withdraws.pop(0)

    def run(self, cfg, cycles=1):
        return run_sender(cfg, cycles, ensure_stash=self.ensure_stash,
                          have_items=self.have_items,
                          deposit=self.deposit, announce=self.announce,
                          wait_seen=self.wait_seen, withdraw=self.withdraw,
                          log=lambda *_: None)


def test_sender_waits_for_confirmation_before_withdrawing(cfg):
    """The bug this fixes: announcing and withdrawing straight away assumes the
    receiver got there, and when it had not the cycle was silently wasted."""
    side = Sender([report(60)], [report(60)])
    cycles = side.run(cfg)
    assert side.calls == ["ensure_stash", "have_items", "deposit",
                          "announce:hsd-ready", "wait_seen", "withdraw"]
    assert str(cycles[0]) == "cycle 1: deposited 60, withdrew 60"


def test_sender_does_not_withdraw_when_the_receiver_never_confirms(cfg):
    side = Sender([report(60)], [report(60)], confirmed=False)
    with pytest.raises(Stopped, match="never confirmed"):
        side.run(cfg)
    assert "withdraw" not in side.calls


def test_sender_repeats(cfg):
    side = Sender([report(60), report(60)], [report(60), report(60)])
    side.run(cfg, 2)
    assert side.calls.count("announce:hsd-ready") == 2


def test_sender_stops_when_a_deposit_moves_nothing(cfg):
    """A stash that will not take anything must not be announced as ready."""
    side = Sender([report(0, Result.STALLED)], [report(60)])
    with pytest.raises(Stopped, match="deposit moved nothing"):
        side.run(cfg, 3)
    assert "announce:hsd-ready" not in side.calls


def test_sender_honours_the_abort_before_announcing(cfg):
    side = Sender([report(60)], [report(60)])
    first = side.deposit

    def deposit():
        result = first()
        control.request_abort()
        return result

    side.deposit = deposit
    with pytest.raises(control.Aborted):
        side.run(cfg)
    assert side.calls == ["ensure_stash", "have_items", "deposit"]


class Receiver:
    def __init__(self, event, sees=True, stash_opens=True, closes=True,
                 inventory_opens=True, use_report=None):
        self.event, self.sees = event, sees
        self.stash_opens, self.closes = stash_opens, closes
        self.inventory_opens = inventory_opens
        self.use_report = use_report or report(60)
        self.opening_records = []
        self.calls = []

    def ensure_stash(self):
        self.calls.append("ensure_stash")
        return self.stash_opens

    def wait_ready(self):
        self.calls.append("wait")
        return self.event

    def see_items(self):
        self.calls.append("see")
        return self.sees

    def confirm(self, token):
        self.calls.append("confirm:" + token)

    def withdraw(self):
        self.calls.append("withdraw")
        return report(60)

    def close_stash(self):
        self.calls.append("close")
        return self.closes

    def open_inventory(self):
        self.calls.append("open_inventory")
        return self.inventory_opens

    def recover_cursor(self):
        self.calls.append("recover_cursor")
        return True

    def use_all(self):
        self.calls.append("use")
        return self.use_report

    def record_opening(self, number, withdrawn, opening_report):
        self.opening_records.append((number, withdrawn, opening_report))

    def run(self, cfg, cycles=1):
        return run_receiver(cfg, cycles, wait_ready=self.wait_ready,
                            ensure_stash=self.ensure_stash,
                            see_items=self.see_items, confirm=self.confirm,
                            withdraw=self.withdraw, close_stash=self.close_stash,
                            open_inventory=self.open_inventory,
                            use_all=self.use_all, recover_cursor=self.recover_cursor,
                            record_opening=self.record_opening,
                            log=lambda *_: None)


GO = ChatEvent("Partner", 7216, "hsd-ready", "999", 1)


def test_receiver_sees_the_items_before_it_confirms(cfg):
    side = Receiver(GO)
    side.run(cfg)
    assert side.calls == ["wait", "ensure_stash", "see", "confirm:hsd-seen",
                          "withdraw", "close", "open_inventory", "use"]


def test_receiver_does_not_confirm_items_it_cannot_see(cfg):
    """The sender withdraws on the strength of this reply, so it has to be
    evidence from the screen rather than an assumption."""
    side = Receiver(GO, sees=False)
    with pytest.raises(Stopped, match="never appeared"):
        side.run(cfg)
    assert not any(call.startswith("confirm") for call in side.calls)
    assert "withdraw" not in side.calls


def test_a_cycle_ends_with_the_stash_shut(cfg):
    """It is reopened only when the next go signal arrives. A panel left open
    across cycles can be showing the previous cycle's contents, so see_items
    would be watching a stale view."""
    side = Receiver(GO)
    side.run(cfg)
    assert side.calls[-1] == "use"
    assert side.calls.count("ensure_stash") == 1


def test_the_stash_is_opened_afresh_on_every_cycle(cfg):
    side = Receiver(GO)
    side.run(cfg, 3)
    assert side.calls.count("ensure_stash") == 3
    assert side.calls.count("close") == 3


def test_receiver_stops_if_the_stash_will_not_close(cfg):
    """Using an item needs the stash shut - with it open the same gesture moves
    the item instead of using it."""
    side = Receiver(GO, closes=False)
    with pytest.raises(Stopped, match="would not close"):
        side.run(cfg)
    assert "use" not in side.calls


def test_receiver_retries_withdraw_when_escape_returns_a_carried_item(cfg):
    side = Receiver(GO)
    reports = iter([
        report(1, Result.STALLED, left=17),
        report(18),
    ])
    closes = iter([False, True])
    logs = []

    def withdraw():
        side.calls.append("withdraw")
        return next(reports)

    def close_stash():
        side.calls.append("close")
        return next(closes)

    side.withdraw = withdraw
    side.close_stash = close_stash
    cycles = run_receiver(
        cfg, 1,
        wait_ready=side.wait_ready,
        ensure_stash=side.ensure_stash,
        see_items=side.see_items,
        confirm=side.confirm,
        withdraw=side.withdraw,
        close_stash=side.close_stash,
        recover_cursor=side.recover_cursor,
        open_inventory=side.open_inventory,
        use_all=side.use_all,
        log=logs.append,
    )

    assert side.calls == [
        "wait", "ensure_stash", "see", "confirm:hsd-seen",
        "withdraw", "close", "recover_cursor", "withdraw", "close",
        "open_inventory", "use",
    ]
    assert cycles[0].withdrew == 18
    assert any("empty inventory cell" in line for line in logs)
    assert [line for line in logs if "withdraw CTRL mode" in line] == [
        "  withdraw CTRL mode: both",
        "  withdraw CTRL mode: vk",
    ]


def test_receiver_rescans_even_when_the_last_item_was_on_the_cursor(cfg):
    side = Receiver(GO)
    closes = iter([False, True])
    reports = iter([report(1), report(1)])
    side.withdraw = lambda: side.calls.append("withdraw") or next(reports)
    side.close_stash = lambda: side.calls.append("close") or next(closes)

    cycles = side.run(cfg)

    assert side.calls.count("withdraw") == 2
    assert side.calls.count("close") == 2
    assert cycles[0].withdrew == 1


def test_receiver_abort_returns_a_cursor_item_and_closes_the_stash(cfg):
    side = Receiver(GO)
    closes = iter([False, True])

    def aborting_withdraw():
        side.calls.append("withdraw")
        control.request_abort()
        raise control.Aborted("abort requested")

    side.withdraw = aborting_withdraw
    side.close_stash = lambda: side.calls.append("close") or next(closes)

    with pytest.raises(control.Aborted, match="abort requested"):
        side.run(cfg)

    assert side.calls[-4:] == ["withdraw", "recover_cursor", "close", "close"]
    assert "open_inventory" not in side.calls


def test_receiver_abort_during_a_retry_also_cleans_the_cursor(cfg):
    side = Receiver(GO)
    withdrawals = 0
    closes = iter([False, True])

    def withdraw():
        nonlocal withdrawals
        side.calls.append("withdraw")
        withdrawals += 1
        if withdrawals == 1:
            return report(1, Result.STALLED, left=17)
        control.request_abort()
        raise control.Aborted("abort requested")

    side.withdraw = withdraw
    side.close_stash = lambda: side.calls.append("close") or next(closes)

    with pytest.raises(control.Aborted, match="abort requested"):
        side.run(cfg)

    assert side.calls[-3:] == ["withdraw", "recover_cursor", "close"]
    assert side.calls.count("recover_cursor") == 2


def test_receiver_opens_inventory_before_using_items(cfg):
    side = Receiver(GO, inventory_opens=False)
    with pytest.raises(Stopped, match="inventory would not open"):
        side.run(cfg)
    assert side.calls[-2:] == ["close", "open_inventory"]
    assert "use" not in side.calls


def test_receiver_stops_before_the_next_cycle_if_items_remain(cfg):
    side = Receiver(GO, use_report=report(55, Result.MAX_PASSES, left=5))

    with pytest.raises(Stopped, match="55 confirmed opened, 5 still visible"):
        side.run(cfg, cycles=2)

    assert side.calls.count("wait") == 1
    assert side.calls[-1] == "use"
    assert side.opening_records == [(1, 60, side.use_report)]


def test_receiver_stops_when_no_one_announces(cfg):
    side = Receiver(None)
    with pytest.raises(Stopped, match="never announced"):
        side.run(cfg)


def test_the_two_tokens_differ_so_neither_side_answers_itself(cfg):
    """Everyone on the topic receives everything, including their own
    publishes. Matching tokens would have each side confirming to itself."""
    assert cfg.data["ready_token"] != cfg.data["seen_token"]


def test_ready_ignores_our_own_announcement(cfg):
    assert not ready_matcher(cfg)(ChatEvent("DjTrilogic", 7216, "hsd-ready", "1", 0))
    assert ready_matcher(cfg)(ChatEvent("Partner", 7216, "hsd-ready", "2", 0))


def test_ready_ignores_other_channels(cfg):
    assert not ready_matcher(cfg)(ChatEvent("Partner", 0, "hsd-ready", "2", 0))


def test_ready_ignores_unrelated_chatter(cfg):
    assert not ready_matcher(cfg)(ChatEvent("Partner", 7216, "hello there", "2", 0))


def test_ready_matches_the_token_inside_a_longer_line(cfg):
    assert ready_matcher(cfg)(ChatEvent("Partner", 7216, "ok hsd-ready go", "2", 0))


def test_sender_opens_the_stash_before_the_first_deposit(cfg):
    """The tool is started with the panel in whatever state the player left it,
    so the first cycle must not depend on how the session happened to begin."""
    side = Sender([report(60)], [report(60)], stash_opens=False)
    with pytest.raises(Stopped, match="would not open"):
        side.run(cfg)
    assert "deposit" not in side.calls


def test_receiver_opens_the_stash_before_looking_for_the_items(cfg):
    """It spends part of every cycle with the stash deliberately shut, so it
    cannot assume the panel is back when the next go signal arrives."""
    side = Receiver(GO, stash_opens=False)
    with pytest.raises(Stopped, match="would not open"):
        side.run(cfg)
    assert "see" not in side.calls


def test_sender_does_not_announce_a_deposit_it_did_not_make(cfg):
    """The bug seen live: cycle 2's deposit found the inventory empty, reported
    DONE because there was nothing to move, and the sender announced anyway -
    sending the receiver to look for items nobody had put there."""
    side = Sender([report(0)], [report(60)])
    with pytest.raises(Stopped, match="moved nothing"):
        side.run(cfg)
    assert not any(call.startswith("announce") for call in side.calls)


def test_sender_waits_for_the_inventory_before_calling_it_empty(cfg):
    """The items arrive at the end of the previous cycle and the panel does not
    necessarily show them the instant the withdraw returns."""
    side = Sender([report(60)], [report(60)], has_items=False)
    with pytest.raises(Stopped, match="inventory is empty"):
        side.run(cfg)
    assert "deposit" not in side.calls


def test_a_receiver_withdraw_that_moves_nothing_stops_the_cycle(cfg):
    side = Receiver(GO)
    side.withdraw = lambda: (side.calls.append("withdraw"), report(0))[1]
    with pytest.raises(Stopped, match="moved nothing"):
        side.run(cfg)
    assert "use" not in side.calls
