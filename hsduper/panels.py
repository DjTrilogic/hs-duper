"""Opening and closing the stash, and using what came out of it."""

import time

from . import capture, winput
from .config import Config
from .transfer import transfer


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
    for attempt in (1, 2):
        winput.move_to(*point)
        time.sleep(cfg.timing("move_settle_ms") / 1000)
        winput.left_click()
        time.sleep(cfg.timing("panel_settle_ms") / 1000)
        if cfg.anchor_ok("stash"):
            log(f"  stash open (attempt {attempt})")
            return True
    log("  stash did not open")
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
