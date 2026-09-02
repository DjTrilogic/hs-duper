"""The two sides of a Blood Pact cycle.

The sender never touches its panels. The receiver refreshes its stash with F,
confirms that the deposited items are visible, withdraws them, then closes the
stash and opens its inventory with I before using them. Three distinct signals
make those state transitions explicit instead of relying on timing.

Every step is injected rather than reached for, so the sequence can be tested
without a game.
"""

from dataclasses import dataclass

from . import control
from .config import Config
from .transfer import Report, Result

DEPOSITED = "deposited"
VISIBLE = "visible"
DONE = "done"


@dataclass(frozen=True)
class CycleSignals:
    """The three messages belonging to one cycle of one sender run."""

    base: str
    session: str
    cycle: int

    def token(self, kind: str) -> str:
        return f"{self.base}#{self.session}#{self.cycle}#{kind}"

    @property
    def deposited(self) -> str:
        return self.token(DEPOSITED)

    @property
    def visible(self) -> str:
        return self.token(VISIBLE)

    @property
    def done(self) -> str:
        return self.token(DONE)

    @classmethod
    def from_deposited(cls, base: str, text: str):
        """Parse only a deposited message; reject other and stale-looking text."""
        try:
            message_base, session, cycle_text, kind = text.rsplit("#", 3)
            cycle = int(cycle_text)
        except (AttributeError, TypeError, ValueError):
            return None
        if message_base != base or not session or cycle < 1 or kind != DEPOSITED:
            return None
        return cls(base, session, cycle)


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


def _finished(report: Report, what: str) -> int:
    moved = _moved(report, what)
    if report.result is not Result.DONE or report.left:
        raise Stopped(f"{what} did not finish - {report}")
    return moved


def run_sender(
    cfg: Config, cycles: int, *, session: str, deposit, announce, wait_signal,
    withdraw, log=print,
):
    """Deposit, wait until the receiver sees it, withdraw, then await DONE."""
    token = cfg.data.get("ready_token", "hsd-ready")
    done = []

    for n in range(1, cycles + 1):
        signals = CycleSignals(token, session, n)
        control.check()
        log(f"[cycle {n}/{cycles}] depositing")
        deposited = _moved(deposit(), "the deposit")

        control.check()
        log(f"  announcing {signals.deposited!r}")
        announce(signals.deposited)

        control.check()
        log("  waiting until the receiver sees the items")
        if wait_signal(signals.visible) is None:
            raise Stopped("the receiver never confirmed that the items were visible")

        control.check()
        log("  withdrawing")
        withdrawn = _moved(withdraw(), "the withdraw")

        control.check()
        log("  waiting until the receiver has used the items")
        if wait_signal(signals.done) is None:
            raise Stopped("the receiver never confirmed that the items were used")

        cycle = Cycle(n, deposited, withdrawn)
        log(f"  {cycle}")
        done.append(cycle)
    return done


def run_receiver(
    cfg: Config, cycles: int, *, wait_deposited, announce, open_stash,
    stash_item_count, withdraw, close_stash, open_inventory, use_all, log=print,
):
    """Refresh the stash, acknowledge visibility, withdraw, use, acknowledge."""
    token = cfg.data.get("ready_token", "hsd-ready")
    done = []

    # Establish the same state used between cycles: stash shut, inventory open.
    if not close_stash():
        raise Stopped("the receiver could not close the stash before waiting")
    if not open_inventory():
        raise Stopped("the receiver could not open the inventory before waiting")

    for n in range(1, cycles + 1):
        control.check()
        log(f"[cycle {n}/{cycles}] waiting for the sender")
        event = wait_deposited()
        if event is None:
            raise Stopped("the sender never announced a deposit - nothing to do")
        signals = CycleSignals.from_deposited(token, event)
        if signals is None:
            raise Stopped(f"the sender announcement was invalid: {event!r}")
        log(f"  heard {event}")

        control.check()
        log("  opening the stash with F")
        if not open_stash():
            raise Stopped("the stash did not open after the sender deposited")

        control.check()
        visible = stash_item_count()
        if visible <= 0:
            raise Stopped("the stash opened but no deposited items were visible")
        log(f"  {visible} occupied stash cell(s) visible")
        announce(signals.visible)

        control.check()
        withdrawn = _moved(withdraw(), "the withdraw")

        control.check()
        log("  closing the stash")
        if not close_stash():
            raise Stopped("the receiver could not close the stash")

        control.check()
        log("  opening the inventory with I")
        if not open_inventory():
            raise Stopped("the receiver could not open the inventory")

        control.check()
        used = _finished(use_all(), "using the items")

        control.check()
        announce(signals.done)

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
