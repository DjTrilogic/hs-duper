"""The multi-pass move loop, shared by deposit and withdraw."""

import random
import time
from dataclasses import dataclass
from enum import Enum

from . import control, doctor, winput
from .config import Config
from .grid import Grid


class Result(Enum):
    DONE = "done"
    STALLED = "stalled"
    MAX_PASSES = "max_passes"
    CURSOR_PICKUP = "cursor_pickup"


@dataclass
class Report:
    result: Result
    moved: int
    passes: int
    left: int

    def __str__(self) -> str:
        note = {
            Result.DONE: "source empty",
            Result.STALLED: "a whole pass moved nothing - destination full, most likely",
            Result.MAX_PASSES: "hit the pass limit with items still there",
            Result.CURSOR_PICKUP: "an item left the source but never appeared in the destination",
        }[self.result]
        return (
            f"{self.result.value}: moved {self.moved} in {self.passes} pass(es), "
            f"{self.left} left ({note})"
        )


class PanelClosed(RuntimeError):
    pass


class NotFocused(RuntimeError):
    pass


def make_click(cfg: Config, button: str | None = None):
    """The mouse half of the configured transfer gesture.

    CTRL+LMB is what moves an item. CTRL+RMB is Drop, per the game's own legend,
    which is why aiming the right button at a slot with the stash open does
    nothing at all - the game receives it and has nothing to do with it. The
    modifier itself is held once around the complete pass by :func:`transfer`.
    """
    button = button or cfg.data.get("click_button", "left")
    hold = int(cfg.timing("button_hold_ms"))
    settle = int(cfg.timing("ctrl_settle_ms"))
    mode = cfg.data.get("ctrl_mode", winput.DEFAULT_CTRL_MODE)
    if button == "right":
        # Retained for the old diagnostic override. Normal transfers use LMB.
        return lambda: winput.right_click_with_ctrl_held(hold, settle, mode)
    return lambda: winput.left_click_with_ctrl_held(hold, settle, mode)


def park(cfg: Config) -> None:
    """Get the cursor off the grid before looking at it.

    Hovering a slot raises a tooltip that covers its neighbours, and those
    neighbours then scan as occupied - so every capture is taken with the
    cursor parked somewhere harmless.
    """
    winput.move_to(*cfg.park)
    time.sleep(cfg.timing("tooltip_ms") / 1000)


def return_cursor_item(source: Grid, destination: Grid, cfg: Config, log=print) -> bool:
    """Place a possibly carried item into a known empty inventory slot.

    Escape is not a reliable way to clear Hero Siege's item cursor. A plain
    click on a slot that the screen has just confirmed empty is deterministic:
    it places a carried item, and is harmless when the cursor is already empty.
    The destination inventory is preferred because that completes the missed
    withdrawal. An empty source slot is used only when the inventory is full.
    This helper deliberately does not call ``control.check`` so abort cleanup
    can still put an item somewhere safe after F12 has been requested.
    """
    for grid, label in ((destination, "inventory"), (source, "stash")):
        park(cfg)
        cells = grid.cells(~grid.occupied())
        if not cells:
            continue

        row, col = cells[0]
        x, y = grid.cell_center(row, col)
        winput.move_to(x, y)
        time.sleep(cfg.timing("move_settle_ms") / 1000)
        winput.left_click(hold_ms=int(cfg.timing("button_hold_ms")))
        park(cfg)
        time.sleep(cfg.timing("pass_settle_ms") / 1000)

        if bool(grid.occupied()[row, col]):
            log(f"  cursor recovery: item placed safely in {label} cell ({row}, {col})")
            return True

    log("  cursor recovery: cursor was already empty (or placement was not detected)")
    return False


def transfer(
    source: Grid,
    cfg: Config,
    anchors: tuple[str, ...] = ("inventory_stash_open", "stash"),
    forbidden: tuple[str, ...] = (),
    click=None,
    click_delay_ms: float | None = None,
    max_passes: int | None = None,
    destination: Grid | None = None,
    recover_cursor=None,
    log=print,
) -> Report:
    """Drain `source` a pass at a time until nothing moves.

    Deposits only need to empty their source. Withdrawals may also provide the
    inventory as ``destination``; that enables per-click verification and
    immediate recovery when an item is picked up onto the cursor.

    Multi-cell items need no special case either. Clicking any one of the cells
    an item covers moves the whole item, and its other cells simply read empty
    on the next pass.
    """
    hold_modifier = click is None
    if hold_modifier:
        click = make_click(cfg)
    if max_passes is None:
        max_passes = int(cfg.timing("max_passes"))
    max_passes = max(int(max_passes), 1)
    move_settle = cfg.timing("move_settle_ms") / 1000
    if click_delay_ms is None:
        click_delay_ms = cfg.timing("click_delay_ms")
    click_delay = max(click_delay_ms, 0) / 1000
    jitter = cfg.timing("jitter_ms") / 1000
    pass_settle = cfg.timing("pass_settle_ms") / 1000

    moved = 0
    remaining = 0
    cursor_recoveries = 0
    max_cursor_recoveries = max(
        int(cfg.data.get("withdraw_recovery_attempts", 3)), 1
    )
    for attempt in range(1, max_passes + 1):
        # A cursor recovery restarts this same pass. No other source item is
        # clicked until the carried one has been put somewhere safe.
        while True:
            control.check()
            park(cfg)

            expected = cfg.data.get("game_exe", doctor.DEFAULT_GAME_EXE)
            if not doctor.game_is_foreground(expected):
                raise NotFocused(
                    f"{expected} is not the foreground window any more. Stopping rather than "
                    "clicking into whatever is."
                )

            # Some passes require a panel to be shut, not open. Using an item is
            # the case: plain RMB consumes/opens it with the stash closed, and with
            # the stash open that gesture has another meaning. "The panel I need
            # gone is still there" has to stop the run just as firmly as a missing
            # one.
            present = [name for name in forbidden if cfg.anchor_ok(name)]
            if present:
                raise PanelClosed(
                    f"{', '.join(present)} is still open, and this pass needs it shut. "
                    "Refusing to click."
                )

            missing = cfg.missing_anchors(list(anchors))
            if missing:
                raise PanelClosed(
                    f"{', '.join(missing)} is not open. Refusing to click because the required "
                    "panel state cannot be verified."
                )

            before = source.occupied()
            count = int(before.sum())
            if count == 0:
                # Say so. Returning in silence made "there was nothing to move" and
                # "this step never ran" look identical in the log, which is exactly
                # the ambiguity you hit when a cycle announced a deposit it had not
                # made.
                log(f"  {source.name} scans as empty"
                    + (" - nothing to move" if attempt == 1 else " - drained"))
                return Report(Result.DONE, moved, attempt - 1, 0)

            log(f"  pass {attempt}: {count} occupied cell(s) in {source.name}")
            # Where the cursor was last put. Checked at the top of the next
            # iteration rather than straight after the move, because the moment a
            # hand on the mouse can show up is the delay after a click - checking
            # right after `move_to` only ever compares the cursor against the
            # position just given to it, and so can never fire.
            commanded: tuple[int, int] | None = None
            cursor_pickup = False
            observed_after = before

            def click_cells() -> None:
                nonlocal commanded, cursor_pickup, observed_after
                current_source = before
                current_destination = (
                    destination.occupied() if destination is not None else None
                )
                # The dangerous, directly observable case is the first stash
                # item leaving while an empty inventory remains empty. Check
                # that transition once; rescanning both grids after every item
                # makes a full withdrawal needlessly slow.
                verify_empty_destination = (
                    current_destination is not None
                    and not bool(current_destination.any())
                )
                for row, col in source.cells(before):
                    # A multi-cell item may have disappeared when an earlier one
                    # of its cells was clicked. The verification scan gives us a
                    # fresh mask, so never click a now-empty stale coordinate.
                    if destination is not None and not bool(current_source[row, col]):
                        continue
                    control.check(commanded)
                    x, y = source.cell_center(row, col)
                    winput.move_to(x, y)
                    time.sleep(move_settle)
                    click()
                    commanded = (x, y)
                    time.sleep(click_delay + random.uniform(0, jitter))

                    if not verify_empty_destination:
                        continue

                    # Verify the first withdrawal into an empty inventory before
                    # touching the next stash item. Once an inventory item is
                    # visible, the rest of the pass keeps its normal fast cadence.
                    control.check(commanded)
                    park(cfg)
                    commanded = tuple(cfg.park)
                    time.sleep(max(cfg.timing("transfer_verify_ms"), 0) / 1000)
                    next_source = source.occupied()
                    next_destination = destination.occupied()
                    source_lost = not bool(next_source[row, col])
                    destination_gained = bool(
                        (next_destination & ~current_destination).any()
                    )
                    current_source = next_source
                    current_destination = next_destination
                    observed_after = next_source
                    verify_empty_destination = False
                    if source_lost and not destination_gained:
                        cursor_pickup = True
                        return

            if hold_modifier:
                settle = int(cfg.timing("ctrl_settle_ms"))
                configured_mode = cfg.data.get("ctrl_mode")
                fallback_modes = ("both", "vk", "scancode")
                mode = configured_mode or fallback_modes[
                    min(cursor_recoveries, len(fallback_modes) - 1)
                ]
                with winput.hold_ctrl(settle_ms=settle, mode=mode):
                    click_cells()
            else:
                # Custom clicks are used for item opening, where CTRL must stay up.
                click_cells()
            control.check(commanded)

            if not cursor_pickup:
                break

            remaining = int(observed_after.sum())
            gone = max(count - remaining, 0)
            moved += gone
            log(
                "    item left the stash but did not appear in the inventory; "
                "stopping this pass before the next click"
            )
            cursor_recoveries += 1
            recovery = recover_cursor
            if recovery is None and destination is not None:
                recovery = lambda: return_cursor_item(source, destination, cfg, log=log)
            if recovery is None or not recovery():
                return Report(Result.CURSOR_PICKUP, moved, attempt, remaining)
            if cursor_recoveries >= max_cursor_recoveries:
                log("    cursor recovery limit reached; not clicking another stash item")
                return Report(Result.CURSOR_PICKUP, moved, attempt, remaining)
            log("    cursor item secured; restarting with a freshly checked CTRL state")
            continue

        park(cfg)
        time.sleep(pass_settle)
        after = source.occupied()
        remaining = int(after.sum())
        gone = max(count - remaining, 0)
        moved += gone
        log(f"    {gone} left {source.name} this pass, {remaining} still there")
        if remaining == 0:
            return Report(Result.DONE, moved, attempt, 0)
        if remaining >= count:
            return Report(Result.STALLED, moved, attempt, remaining)

    return Report(Result.MAX_PASSES, moved, max_passes, remaining)


def wait_until_occupied(grid, cfg: Config, timeout: float = 60.0,
                        poll_s: float = 0.25, log=print) -> int:
    """Block until the grid actually shows items, and say how many.

    Looking at the screen is a stronger claim than a message saying "ready":
    it is the difference between the other side believing the items arrived and
    this side having seen them.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        control.check()
        count = int(grid.occupied().sum())
        if count:
            log(f"  {count} item(s) visible in {grid.name}")
            return count
        time.sleep(poll_s)
    return 0
