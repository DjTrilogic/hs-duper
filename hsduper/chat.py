"""Sending a line on the Blood Pact tab.

The gesture is: ENTER opens the chat, the tab has to be picked because the game
remembers whichever one was last used, the text goes in, and ENTER sends it.
Sending closes the chat by itself - so there is deliberately no ESC afterwards.
An ESC with the chat already gone reaches the game instead, where it closes the
stash, and the next pass then finds no panel. Every position here is calibrated
rather than assumed - see `calibrate chat`.
"""

import time

from . import winput
from .config import Config


def send(cfg: Config, text: str, log=print) -> None:
    """Type one line into the Blood Pact tab.

    Picking the tab every time is deliberate. The game keeps whichever tab was
    last used, so a message that assumes Blood Pact is still selected will
    cheerfully go to Trade the first time you glance at another channel - and
    the failure is invisible from here.
    """
    tab = cfg.data.get("chat_tab_point")
    field = cfg.data.get("chat_input_point")
    if not tab or not field:
        raise KeyError("chat points are not calibrated - run `python -m hsduper calibrate chat`")

    beat = cfg.timing("chat_step_ms") / 1000

    log("  opening chat")
    winput.press_enter()
    time.sleep(beat)

    log("  selecting the Blood Pact tab")
    winput.move_to(*tab)
    time.sleep(beat)
    winput.left_click()
    time.sleep(beat)

    winput.move_to(*field)
    time.sleep(beat)
    winput.left_click()
    time.sleep(beat)

    log(f"  typing {text!r}")
    winput.type_text(text)
    time.sleep(beat)
    winput.press_enter()
