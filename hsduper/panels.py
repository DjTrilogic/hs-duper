"""Opening and closing the stash, and using what came out of it."""

import time

from . import capture, winput
from .config import Config
from .transfer import transfer

DEFAULT_OPEN_ATTEMPTS = 3


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
    """Click the stash object in the world, and confirm the panel came back.

    This is the one step that depends on where the character is standing rather
    than on the interface, so it is the one that quietly stops working. The
    return value is checked by the caller for exactly that reason.
    """
    point = cfg.data.get("stash_object_point")
    if not point:
        raise KeyError("stash_object_point is not calibrated - run `calibrate pact`")
    attempts = max(int(cfg.data.get("panel_open_attempts", DEFAULT_OPEN_ATTEMPTS)), 1)
    timeout_ms = cfg.timing("panel_open_timeout_ms")
    for attempt in range(1, attempts + 1):
        winput.move_to(*point)
        time.sleep(cfg.timing("move_settle_ms") / 1000)
        winput.left_click()
        if _wait_for_anchor(cfg, "stash", timeout_ms):
            log(f"  stash open (attempt {attempt})")
            return True
        if attempt < attempts:
            log(f"  stash not detected after attempt {attempt}/{attempts} - retrying")
    log(f"  stash did not open after {attempts} attempt(s)")
    return False


def use_all(cfg: Config, grid, log=print):
    """RMB every item in the inventory, with the stash shut.

    The forbidden anchor is the safety here: with the stash open this gesture
    is not 'use', so the pass must refuse to run until the panel is really gone.
    """
    return transfer(
        grid, cfg,
        anchors=("inventory",),
        forbidden=("stash",),
        click=lambda: winput.right_click(int(cfg.timing("button_hold_ms"))),
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
