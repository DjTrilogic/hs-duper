"""The two sides of a Blood Pact cycle.

The sender never touches the panels: it deposits, announces, withdraws, and
goes round again with the stash open throughout. The receiver has to shut the
stash to use what it took and open it again afterwards, which is the fragile
half - opening needs a click on the stash object in the world, so it depends on
the character still standing next to it.

Every step is injected rather than reached for, so the sequence can be tested
without a game.
"""

import time
from dataclasses import dataclass

from . import control
from .config import Config
from .transfer import Report, Result


@dataclass
class Cycle:
    n: int
    deposited: int
    withdrew: int
    used: int = 0

    def __str__(self) -> str:
        bits = [f"cycle {self.n}: deposited {self.deposited}, withdrew {self.withdrew}"]
        if self.used:
            bits.append(f"used {self.used}")
        return ", ".join(bits)


class Stopped(RuntimeError):
    """A cycle could not complete, with the reason as the message."""


def _moved(report: Report, what: str) -> int:
    if report.result is Result.STALLED and report.moved == 0:
        raise Stopped(f"{what} moved nothing at all - {report}")
    return report.moved


def run_sender(cfg: Config, cycles: int, *, ensure_stash, deposit, announce,
               wait_seen, withdraw, log=print):
    """deposit -> announce -> wait for the receiver -> withdraw.

    The wait is the point. Announcing and withdrawing straight away assumes the
    receiver got there, and when it did not the items simply come back and the
    cycle was wasted. The receiver replies only once it can see the items in
    the stash on its own screen, so the reply means they really arrived rather
    than that a message was delivered.
    """
    token = cfg.data.get("ready_token", "hsd-ready")
    gap = cfg.timing("after_ready_ms") / 1000
    done = []

    for n in range(1, cycles + 1):
        control.check()
        log(f"[cycle {n}/{cycles}] making sure the stash is open")
        if not ensure_stash():
            raise Stopped("the stash is not open and would not open - stand next to it")

        control.check()
        log("  depositing")
        deposited = _moved(deposit(), "the deposit")

        control.check()
        log(f"  announcing {token!r}")
        announce(token)

        control.check()
        log("  waiting for the receiver to confirm it can see them")
        if not wait_seen():
            raise Stopped(
                "the receiver never confirmed. The items are in the stash - it is safe "
                "to withdraw them by hand, but the cycle did not happen."
            )
        log("  confirmed")
        if gap:
            time.sleep(gap)

        control.check()
        log("  withdrawing")
        try:
            withdrawn = _moved(withdraw(), "the withdraw")
        except Stopped as exc:
            raise Stopped(
                "the stash was empty when the sender withdrew - the receiver got there "
                "first and took everything. Nothing is lost, but this cycle produced "
                f"nothing. ({exc})"
            ) from exc

        cycle = Cycle(n, deposited, withdrawn)
        log(f"  {cycle}")
        done.append(cycle)
    return done


def run_receiver(cfg: Config, cycles: int, *, wait_ready, ensure_stash, see_items,
                 confirm, withdraw, close_stash, use_all, open_stash, log=print):
    """wait -> see the items -> confirm -> withdraw -> shut, use, reopen.

    `see_items` is what makes the confirmation worth anything: it watches the
    stash until the items are actually on screen, so the reply the sender waits
    for is evidence rather than an assumption. Nothing is confirmed if they
    never appear.
    """
    seen_token = cfg.data.get("seen_token", "hsd-seen")
    done = []

    for n in range(1, cycles + 1):
        control.check()
        log(f"[cycle {n}/{cycles}] waiting for the sender")
        event = wait_ready()
        if event is None:
            raise Stopped("the sender never announced - nothing to do")
        log(f"  heard {event}")

        control.check()
        if not ensure_stash():
            raise Stopped("the stash is not open and would not open - stand next to it")

        control.check()
        log("  watching the stash for the items")
        if not see_items():
            raise Stopped(
                "the items never appeared in the stash. Not confirming, because the "
                "sender would withdraw on the strength of it."
            )

        control.check()
        log(f"  confirming with {seen_token!r}")
        confirm(seen_token)

        control.check()
        withdrawn = _moved(withdraw(), "the withdraw")

        control.check()
        log("  closing the stash")
        close_stash()

        control.check()
        used = _moved(use_all(), "using the items")

        control.check()
        log("  reopening the stash")
        if not open_stash():
            raise Stopped(
                "the stash did not reopen - the character has probably drifted away "
                "from it. Stopping rather than running a cycle with no stash."
            )

        cycle = Cycle(n, 0, withdrawn, used)
        log(f"  {cycle}")
        done.append(cycle)
    return done


def ready_matcher(cfg: Config):
    """What counts as the sender's go signal.

    Three conditions, and the third is the one that matters: our own messages
    come back through the same capture, so a sender that does not exclude
    itself hears its own announcement and races itself.
    """
    token = cfg.data.get("ready_token", "hsd-ready")
    room = cfg.data.get("blood_pact_room")
    own = (cfg.data.get("own_name") or "").lower()

    def matches(event) -> bool:
        if token not in event.text:
            return False
        if room is not None and event.room != room:
            return False
        if own and event.name.lower() == own:
            return False
        return True

    return matches
