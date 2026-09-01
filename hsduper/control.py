"""The stop button, and what counts as pressing it."""

import threading

from . import config, winput

_abort = threading.Event()


class Aborted(RuntimeError):
    pass


def request_abort() -> None:
    _abort.set()


def clear() -> None:
    _abort.clear()


def aborted() -> bool:
    return _abort.is_set()


def check(expected_cursor: tuple[int, int] | None = None) -> None:
    """Raise if the run should stop. Called before every single click.

    Two ways to stop: the abort hotkey, or moving the mouse yourself. The
    second matters because the tool drives the same cursor you do - if you have
    reached for the mouse, whatever the tool does next is landing somewhere it
    did not intend.
    """
    if _abort.is_set():
        raise Aborted("abort requested")
    if expected_cursor is not None:
        x, y = winput.get_cursor_pos()
        ex, ey = expected_cursor
        if abs(x - ex) > config.CURSOR_TOLERANCE or abs(y - ey) > config.CURSOR_TOLERANCE:
            raise Aborted(
                f"the cursor moved on its own (at {x},{y}, expected {ex},{ey}) - "
                "stopping in case that was you"
            )
