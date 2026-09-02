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
    """The click as configured.

    CTRL+LMB is what moves an item. CTRL+RMB is Drop, per the game's own legend,
    which is why aiming the right button at a slot with the stash open does
    nothing at all - the game receives it and has nothing to do with it.
    """
    button = button or cfg.data.get("click_button", "left")
    fn = winput.ctrl_right_click if button == "right" else winput.ctrl_left_click
    hold = int(cfg.timing("button_hold_ms"))
    settle = int(cfg.timing("ctrl_settle_ms"))
    mode = cfg.data.get("ctrl_mode", "both")
    return lambda: fn(hold_ms=hold, settle_ms=settle, mode=mode)


def park(cfg: Config) -> None:
    """Get the cursor off the grid before looking at it.

    Hovering a slot raises a tooltip that covers its neighbours, and those
    neighbours then scan as occupied - so every capture is taken with the
    cursor parked somewhere harmless.
    """
    winput.move_to(*cfg.park)
    time.sleep(cfg.timing("tooltip_ms") / 1000)


def transfer(
    source: Grid,
    cfg: Config,
    anchors: tuple[str, ...] = ("inventory_stash_open", "stash"),
    forbidden: tuple[str, ...] = (),
    click=None,
    log=print,
) -> Report:
    """Drain `source` a pass at a time until nothing moves.

    The destination is never modelled. Both directions just empty the source
    and let the game decide where things land, which is also why a full
    destination needs no special case - it shows up as a pass that moves
    nothing.

    Multi-cell items need no special case either. Clicking any one of the cells
    an item covers moves the whole item, and its other cells simply read empty
    on the next pass.
    """
    if click is None:
        click = make_click(cfg)
    max_passes = int(cfg.timing("max_passes"))
    move_settle = cfg.timing("move_settle_ms") / 1000
    click_delay = cfg.timing("click_delay_ms") / 1000
    jitter = cfg.timing("jitter_ms") / 1000
    pass_settle = cfg.timing("pass_settle_ms") / 1000

    moved = 0
    remaining = 0
    for attempt in range(1, max_passes + 1):
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
            return Report(Result.DONE, moved, attempt - 1, 0)

        log(f"  pass {attempt}: {count} occupied cell(s) in {source.name}")
        # Where the cursor was last put. Checked at the top of the next
        # iteration rather than straight after the move, because the moment a
        # hand on the mouse can show up is the delay after a click - checking
        # right after `move_to` only ever compares the cursor against the
        # position just given to it, and so can never fire.
        commanded: tuple[int, int] | None = None
        for row, col in source.cells(before):
            control.check(commanded)
            x, y = source.cell_center(row, col)
            winput.move_to(x, y)
            time.sleep(move_settle)
            click()
            commanded = (x, y)
            time.sleep(click_delay + random.uniform(0, jitter))
        control.check(commanded)

        park(cfg)
        time.sleep(pass_settle)
        after = source.occupied()
        remaining = int(after.sum())
        moved += max(count - remaining, 0)
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
