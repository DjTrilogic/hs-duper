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


def run_sender(cfg: Config, cycles: int, *, deposit, announce, withdraw, log=print):
    """deposit -> announce -> withdraw, with the stash open throughout.

    Announcing sits between the two transfers on purpose: the receiver can
    start pulling the moment the items are in the stash, so its work overlaps
    the sender's own withdraw instead of queueing behind it.

    `announce` is just a callable. The sender must never close the stash, which
    rules out the in-game chat - so what actually carries the signal is the
    caller's business, not this loop's.
    """
    token = cfg.data.get("ready_token", "hsd-ready")
    gap = cfg.timing("after_ready_ms") / 1000
    done = []

    for n in range(1, cycles + 1):
        control.check()
        log(f"[cycle {n}/{cycles}] depositing")
        deposited = _moved(deposit(), "the deposit")

        control.check()
        log(f"  announcing {token!r}")
        announce(token)
        if gap:
            time.sleep(gap)

        control.check()
        log("  withdrawing")
        withdrawn = _moved(withdraw(), "the withdraw")

        cycle = Cycle(n, deposited, withdrawn)
        log(f"  {cycle}")
        done.append(cycle)
    return done


def run_receiver(cfg: Config, cycles: int, *, wait_ready, withdraw, close_stash,
                 use_all, open_stash, log=print):
    """wait for the sender -> withdraw -> shut the stash -> use -> reopen.

    Reopening is the step that can leave things wedged: it is a click on a
    world object, so if the character has drifted the stash does not come back
    and the next cycle has nowhere to take items from. That is why a failure to
    reopen stops the run rather than pressing on.
    """
    done = []
    for n in range(1, cycles + 1):
        control.check()
        log(f"[cycle {n}/{cycles}] waiting for the sender")
        event = wait_ready()
        if event is None:
            raise Stopped("the sender never announced - nothing to do")
        log(f"  heard {event}")

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
