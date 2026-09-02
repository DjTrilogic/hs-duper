"""Opening and closing the stash, and using what came out of it."""

import time

from . import capture, winput
from .config import Config
from .transfer import transfer


def close_stash(cfg: Config, log=print) -> bool:
    """Shut the stash and confirm it actually shut."""
    if not cfg.anchor_ok("stash"):
        log("  stash already closed")
        return True
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
    """Press F by the stash and confirm that its panel appeared."""
    if cfg.anchor_ok("stash"):
        log("  stash already open")
        return True
    for attempt in (1, 2):
        winput.press_interact()
        time.sleep(cfg.timing("panel_settle_ms") / 1000)
        if cfg.anchor_ok("stash"):
            log(f"  stash open (attempt {attempt})")
            return True
    log("  stash did not open")
    return False


def open_inventory(cfg: Config, log=print) -> bool:
    """Press I if needed and confirm that the inventory panel appeared."""
    if cfg.anchor_ok("inventory"):
        log("  inventory already open")
        return True
    for attempt in (1, 2):
        winput.press_inventory()
        time.sleep(cfg.timing("panel_settle_ms") / 1000)
        if cfg.anchor_ok("inventory"):
            log(f"  inventory open (attempt {attempt})")
            return True
    log("  inventory did not open")
    return False


def use_all(cfg: Config, grid, log=print):
    """CTRL+RMB every item in the inventory, with the stash shut.

    The forbidden anchor is the safety here: with the stash open this gesture
    is not 'use', so the pass must refuse to run until the panel is really gone.
    """
    return transfer(
        grid, cfg,
        anchors=("inventory",),
        forbidden=("stash",),
        click=lambda: winput.ctrl_right_click(
            int(cfg.timing("button_hold_ms")),
            int(cfg.timing("ctrl_settle_ms")),
            cfg.data.get("ctrl_mode", "both"),
        ),
        log=log,
    )
