"""Opening and closing the stash, and using what came out of it."""

import time

from . import control, winput
from .config import Config
from .transfer import transfer

DEFAULT_OPEN_ATTEMPTS = 3
DEFAULT_USE_BATCH_SIZE = 10


def _wait_for_anchor(cfg: Config, name: str, timeout_ms: float) -> bool:
    """Poll an anchor until it appears or its timeout expires."""
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    poll_s = max(cfg.timing("panel_poll_ms"), 10) / 1000
    while True:
        if cfg.anchor_ok(name):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_s, remaining))


def close_stash(cfg: Config, log=print) -> bool:
    """Shut the stash and confirm it actually shut."""
    point = cfg.data.get("stash_close_point")
    if point:
        winput.move_to(*point)
        time.sleep(cfg.timing("move_settle_ms") / 1000)
        winput.left_click()
    else:
        winput.press_escape()
    time.sleep(cfg.timing("panel_settle_ms") / 1000)
    shut = not cfg.anchor_ok("stash")
    log("  stash closed" if shut else "  stash did NOT close")
    return shut


def open_stash(cfg: Config, log=print) -> bool:
    """Press F in front of the stash, and confirm the panel came back.

    This is the one step that depends on where the character is standing rather
    than on the interface. The return value is checked by the caller so a
    character that moved away from the stash cannot start an unsafe pass.
    """
    attempts = max(int(cfg.data.get("panel_open_attempts", DEFAULT_OPEN_ATTEMPTS)), 1)
    timeout_ms = cfg.timing("panel_open_timeout_ms")
    for attempt in range(1, attempts + 1):
        winput.press_interact()
        if _wait_for_anchor(cfg, "stash", timeout_ms):
            log(f"  stash open (attempt {attempt})")
            return True
        if attempt < attempts:
            log(f"  stash not detected after attempt {attempt}/{attempts} - retrying")
    log(f"  stash did not open after {attempts} attempt(s)")
    return False


def open_inventory(cfg: Config, log=print) -> bool:
    """Open the inventory with I and confirm its anchor appeared."""
    if cfg.anchor_ok("inventory_standalone"):
        log("  inventory already open")
        return True

    attempts = max(int(cfg.data.get("panel_open_attempts", DEFAULT_OPEN_ATTEMPTS)), 1)
    timeout_ms = cfg.timing("panel_open_timeout_ms")
    for attempt in range(1, attempts + 1):
        winput.press_inventory()
        if _wait_for_anchor(cfg, "inventory_standalone", timeout_ms):
            log(f"  inventory open (attempt {attempt})")
            return True
        if attempt < attempts:
            log(f"  inventory not detected after attempt {attempt}/{attempts} - retrying")
    log(f"  inventory did not open after {attempts} attempt(s)")
    return False


def use_all(cfg: Config, grid, log=print):
    """RMB every item in the inventory, with the stash shut.

    The forbidden anchor is the safety here: with the stash open this gesture
    is not 'use', so the pass must refuse to run until the panel is really gone.
    """
    clicks = 0
    hold_ms = int(cfg.timing("button_hold_ms"))
    batch_size = max(int(cfg.data.get("use_batch_size", DEFAULT_USE_BATCH_SIZE)), 1)
    batch_delay = max(cfg.timing("use_batch_delay_ms"), 0) / 1000

    def use_one() -> None:
        nonlocal clicks
        winput.right_click(hold_ms)
        clicks += 1
        if clicks % batch_size == 0 and batch_delay:
            log(f"  {clicks} use click(s) sent - waiting {batch_delay:g}s for the server")
            control.check()
            time.sleep(batch_delay)
            control.check()

    return transfer(
        grid, cfg,
        anchors=("inventory_standalone",),
        forbidden=("stash",),
        click=use_one,
        log=log,
    )


def ensure_stash_open(cfg: Config, log=print) -> bool:
    """Open the stash if it is not already open.

    Both sides start their tool with the panel in whatever state the player
    left it, and the receiver spends part of every cycle with it deliberately
    shut. Assuming it is open makes the first cycle depend on how the session
    happened to begin - so each side checks and opens rather than assumes.
    """
    if cfg.anchor_ok("stash"):
        return True
    log("  stash is closed - opening it")
    return open_stash(cfg, log=log)
