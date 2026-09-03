"""The two sides of a Blood Pact cycle.

The sender never touches the panels: it deposits, announces, withdraws, and
goes round again with the stash open throughout. The receiver has to shut the
stash, open the inventory, and use what it took. It opens the stash again on
the next cycle, which is the fragile half: pressing F only works while the
character is still standing in front of it.

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


DEFAULT_WITHDRAW_RECOVERY_ATTEMPTS = 3


def _moved(report: Report, what: str) -> int:
    """The count, or a stop.

    Zero is a failure whatever the result says. A transfer whose source scans
    as empty reports DONE - correctly, there was nothing to move - and that
    used to pass for a successful step: the sender would announce a deposit it
    had not made, and the receiver would be sent looking for items nobody put
    there.
    """
    if report.result is Result.CURSOR_PICKUP:
        raise Stopped(f"{what} stopped after an item was found on the cursor - {report}")
    if report.moved == 0:
        raise Stopped(f"{what} moved nothing at all - {report}")
    return report.moved


def _withdraw_and_close(
    cfg: Config, withdraw, close_stash, recover_cursor=None, log=print
) -> int:
    """Withdraw, recovering if a missed CTRL leaves an item on the cursor.

    A failed close is evidence that an item may still be attached. The recovery
    callback places it into a screen-confirmed empty inventory slot, then the
    remaining stash is retried with another CTRL delivery mode. This also covers
    the last visible item: a grid can look empty while it is still on the cursor.
    """
    attempts = max(
        int(cfg.data.get("withdraw_recovery_attempts", DEFAULT_WITHDRAW_RECOVERY_ATTEMPTS)),
        1,
    )
    configured_mode = cfg.data.get("ctrl_mode")
    fallback_modes = ("both", "vk", "scancode")

    def abort_cleanup() -> None:
        # CTRL is already released by transfer's finally block. Place any
        # carried item into inventory, then close the still-open stash. This
        # deliberately ignores the abort flag: it is the safe work remaining.
        log("  abort cleanup: returning any cursor item and closing the stash")
        try:
            if recover_cursor is not None:
                recover_cursor()
            if not close_stash():
                close_stash()
        except Exception as exc:
            log(f"  abort cleanup could not verify the stash: {exc}")

    def run_withdraw(attempt: int):
        mode = configured_mode or fallback_modes[min(attempt - 1, len(fallback_modes) - 1)]
        marker = object()
        previous = cfg.data.get("ctrl_mode", marker)
        cfg.data["ctrl_mode"] = mode
        log(f"  withdraw CTRL mode: {mode}")
        try:
            try:
                return withdraw()
            except control.Aborted:
                abort_cleanup()
                raise
        finally:
            if previous is marker:
                cfg.data.pop("ctrl_mode", None)
            else:
                cfg.data["ctrl_mode"] = previous

    report = run_withdraw(1)
    withdrawn = report.moved

    for attempt in range(1, attempts + 1):
        control.check()
        label = "closing the stash" if attempt == 1 else "closing the stash again"
        log(f"  {label} (attempt {attempt}/{attempts})")
        if close_stash():
            _moved(report, "the withdraw")
            return max(withdrawn, report.moved)

        log("  stash stayed open; placing any cursor item into an empty inventory cell")
        if recover_cursor is not None:
            recover_cursor()

        if attempt >= attempts:
            # Leave the UI and cursor in a safe state even though this cycle is
            # about to stop. If the recovery placed an item, this Escape should
            # now close the stash instead of merely cancelling the cursor.
            log("  final cleanup: closing the stash before stopping")
            close_stash()
            break

        log("  rescanning and retrying the withdraw with the next CTRL mode")
        control.check()
        report = run_withdraw(attempt + 1)
        withdrawn = max(withdrawn, report.moved)

    raise Stopped(
        "the stash would not close after recovery attempts, and using an item needs "
        "it shut - with the stash open the same gesture moves the item instead of "
        "using it."
    )


def run_sender(cfg: Config, cycles: int, *, ensure_stash, have_items, deposit,
               announce, wait_seen, withdraw, log=print):
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

        # The items land in the inventory at the end of the previous cycle, and
        # the panel does not necessarily show them the instant the withdraw
        # returns. Depositing straight away can scan an inventory that is only
        # briefly empty, which is not the same thing as having nothing to send.
        control.check()
        if not have_items():
            raise Stopped(
                "the inventory is empty - there is nothing to deposit. If the last "
                "withdraw did bring items back, raise timing.inventory_wait_ms; the "
                "panel may just not have caught up."
            )

        control.check()
        log("  depositing")
        deposited = _moved(deposit(), "the deposit")

        control.check()
        log(f"  announcing {token!r}")
        announce(token)

        control.check()
        log("  waiting for the receiver to confirm it can see them")
        started = time.monotonic()
        confirmed = wait_seen()
        waited = time.monotonic() - started
        if not confirmed:
            raise Stopped(
                f"the receiver never confirmed, after {waited:.0f}s. The items are in "
                "the stash - it is safe to withdraw them by hand, but the cycle did not "
                "happen. If the receiver was simply still busy using the last batch, "
                "raise timing.confirm_timeout_ms."
            )
        # Printed every cycle because it is the number that decides how patient
        # the sender has to be: the receiver spends the gap withdrawing and
        # using the previous batch, and how long that takes depends on how many
        # items there are.
        log(f"  confirmed after {waited:.1f}s")
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
                 confirm, withdraw, close_stash, open_inventory, use_all,
                 recover_cursor=None, record_opening=None, log=print):
    """wait -> open stash -> see -> confirm -> withdraw -> shut -> open inventory -> use.

    A cycle ends with the stash shut and does not reopen it. Reopening only
    when the next go signal arrives is what makes `see_items` trustworthy: a
    panel left open across cycles can be showing the previous cycle's contents,
    so watching it would be watching a stale view and confirming against items
    that are not the ones just deposited. Opened fresh, what it shows is what is
    actually there.

    `see_items` is what makes the confirmation worth anything at all - the
    sender withdraws on the strength of it, so it has to be evidence off the
    screen rather than an assumption. Nothing is confirmed if the items never
    appear.
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
        log("  opening the stash")
        if not ensure_stash():
            raise Stopped(
                "the stash would not open - the character has probably drifted away "
                "from it. Stopping rather than running a cycle with no stash."
            )

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
        withdrawn = _withdraw_and_close(
            cfg, withdraw, close_stash, recover_cursor=recover_cursor, log=log
        )

        control.check()
        log("  opening the inventory")
        if not open_inventory():
            raise Stopped(
                "the inventory would not open. Stopping before trying to use any items."
            )

        control.check()
        use_report = use_all()
        if record_opening is not None:
            record_opening(n, withdrawn, use_report)
        used = use_report.moved
        if use_report.result is not Result.DONE or use_report.left:
            raise Stopped(
                f"item opening is incomplete: {used} confirmed opened, "
                f"{use_report.left} still visible after all retries. Leaving the "
                "inventory open and stopping before the next stash cycle."
            )

        cycle = Cycle(n, 0, withdrawn, used)
        log(f"  {cycle} (stash left shut until the next signal)")
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
