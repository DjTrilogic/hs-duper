"""Screen capture, one region at a time."""

import mss
import numpy as np

_sct = None


def _instance():
    global _sct
    if _sct is None:
        _sct = mss.mss()
    return _sct


def grab(region: tuple[int, int, int, int]) -> np.ndarray:
    """Grab (left, top, width, height) as an RGB uint8 array."""
    left, top, width, height = region
    shot = _instance().grab(
        {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    )
    bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return np.ascontiguousarray(bgra[:, :, 2::-1])


def pixel(x: int, y: int) -> tuple[int, int, int]:
    return tuple(int(v) for v in grab((x, y, 1, 1))[0, 0])


def looks_blank(frame: np.ndarray) -> bool:
    """An all-black or flat frame, which is what exclusive fullscreen looks like.

    GDI capture of a game holding the display exclusively returns black rather
    than failing, so a blank frame has to be treated as "capture did not work"
    and not as "every cell is empty" - the latter would send clicks based on
    nothing.
    """
    return bool(frame.max() < 8 or frame.std() < 1.0)


def luminance(frame: np.ndarray) -> np.ndarray:
    rgb = frame.astype(np.float32)
    return 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
