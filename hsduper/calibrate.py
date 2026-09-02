"""Interactive calibration.

Nothing about the layout is hardcoded. You hover a thing in the game and press
F8; the wizard reads the cursor and works the geometry out from that. Numbers
typed at the console are asked for up front, while you are still looking at the
console, so that every step needing the game in front of you is a keypress.
"""

import base64
import math
import threading
import time

import numpy as np
from pynput import keyboard

from . import capture, winput
from .config import GRID_NAMES, PATH, Config
from .grid import INNER, Grid

ANCHOR_W, ANCHOR_H = 80, 20
SAMPLE_PARK_POINT = (0, 0)
SAMPLE_SETTLE_SECONDS = 0.12
GRID_SIZE_DEFAULTS = {
    "inventory": (6, 15),
    "stash": (18, 17),
}


class Cancelled(RuntimeError):
    pass


def wait_for_mark() -> tuple[int, int]:
    """Block until F8, and report where the cursor was at that instant."""
    done = threading.Event()
    where: list[tuple[int, int]] = []

    def on_press(key):
        if key == keyboard.Key.f8:
            where.append(winput.get_cursor_pos())
            done.set()
            return False
        if key == keyboard.Key.esc:
            done.set()
            return False
        return None

    with keyboard.Listener(on_press=on_press) as listener:
        done.wait()
        listener.join()
    if not where:
        raise Cancelled("calibration cancelled")
    return where[0]


def mark(prompt: str) -> tuple[int, int]:
    print(f"  {prompt}\n     ...hover it and press F8 (ESC cancels)", flush=True)
    point = wait_for_mark()
    print(f"     got {point}", flush=True)
    return point


def sample_metric(point: tuple[int, int], pitch_x: float, pitch_y: float) -> float:
    """The occupancy metric for the cell under the cursor.

    The game paints a very bright highlight over every hovered slot. Move the
    cursor away and let that highlight clear before grabbing the pixels;
    otherwise an empty and an occupied slot both measure near-white.

    The patch remains centred on the point the user marked rather than on a
    derived cell index, so this does not hide bad grid geometry.
    """
    winput.move_to(*SAMPLE_PARK_POINT)
    time.sleep(SAMPLE_SETTLE_SECONDS)
    w = max(int(pitch_x * INNER), 4)
    h = max(int(pitch_y * INNER), 4)
    frame = capture.grab((point[0] - w // 2, point[1] - h // 2, w, h))
    return float(np.percentile(capture.luminance(frame), 95))


def ask_grid_size(name: str) -> tuple[int, int]:
    defaults = GRID_SIZE_DEFAULTS.get(name)
    if defaults is None:
        return int(input(f"  rows in {name}: ").strip()), int(
            input(f"  columns in {name}: ").strip()
        )

    default_rows, default_cols = defaults
    rows = int(input(f"  rows in {name} [{default_rows}]: ").strip() or default_rows)
    cols = int(input(f"  columns in {name} [{default_cols}]: ").strip() or default_cols)
    return rows, cols


def calibrate_grid(name: str) -> Grid:
    print(f"\n=== {name} ===")
    rows, cols = ask_grid_size(name)
    print("  Now switch to Hero Siege with the right panels open.")

    x0, y0 = mark("The CENTRE of the TOP-LEFT cell.")
    x1, y1 = mark("The CENTRE of the BOTTOM-RIGHT cell.")

    pitch_x = (x1 - x0) / (cols - 1) if cols > 1 else 0.0
    pitch_y = (y1 - y0) / (rows - 1) if rows > 1 else 0.0
    if pitch_x <= 0 or pitch_y <= 0:
        raise Cancelled(
            f"that gives a pitch of {pitch_x:.1f} x {pitch_y:.1f} - the two marks were probably "
            "the wrong way round, or the same cell twice"
        )
    print(f"     pitch {pitch_x:.2f} x {pitch_y:.2f} px")

    empty = sample_metric(mark("Any EMPTY cell in this grid."), pitch_x, pitch_y)
    full = sample_metric(mark("Any cell HOLDING AN ITEM in this grid."), pitch_x, pitch_y)
    print(f"     empty reads {empty:.1f}, occupied reads {full:.1f}")
    if full <= empty * 1.15:
        raise Cancelled(
            f"those two are too close ({empty:.1f} vs {full:.1f}) to tell apart. Pick a cell with "
            "a brighter item in it, or check the empty one really was empty."
        )
    threshold = math.sqrt(empty * full) if empty > 0 else full / 2
    print(f"     threshold {threshold:.1f}")

    return Grid(name, x0, y0, pitch_x, pitch_y, rows, cols, threshold)


def calibrate_anchor(cfg: Config, name: str, what: str) -> None:
    point = mark(f"The {what} title text.")
    rect = (point[0] - ANCHOR_W // 2, point[1] - ANCHOR_H // 2, ANCHOR_W, ANCHOR_H)
    # Hero Siege draws its own cursor into the captured frame. Keeping it over
    # the title would bake the cursor shape into the anchor template, which is
    # absent during later checks and makes an open panel look closed.
    winput.move_to(*SAMPLE_PARK_POINT)
    time.sleep(SAMPLE_SETTLE_SECONDS)
    frame = capture.grab(rect)
    colour = frame.reshape(-1, 3).mean(axis=0)
    luminance = np.rint(capture.luminance(frame)).astype(np.uint8)
    cfg.data.setdefault("anchors", {})[name] = {
        "rect": list(rect),
        "color": [round(float(v), 2) for v in colour],
        "luminance_template": base64.b64encode(luminance.tobytes()).decode("ascii"),
    }
    print(f"     anchor colour {[round(float(v)) for v in colour]} and text template stored")


def run(parts: list[str]) -> None:
    cfg = Config.load() if Config.exists() else Config.blank()
    wanted = parts or [*GRID_NAMES, "park", "anchors"]

    for name in GRID_NAMES:
        if name in wanted:
            grid = calibrate_grid(name)
            cfg.data.setdefault("grids", {})[name] = grid.to_dict()
            cfg.save()

    if "park" in wanted:
        print("\n=== park point ===")
        print("  Somewhere the cursor can rest without covering a slot or raising a tooltip -")
        print("  the dark area above the stash panel does nicely.")
        cfg.data["park"] = list(mark("An EMPTY area of screen."))
        cfg.save()

    if "chat" in wanted:
        print()
        print("=== chat ===")
        print("  Press ENTER in game to open the chat, then mark two things.")
        cfg.data["chat_tab_point"] = list(mark("The BLOOD PACT tab."))
        cfg.data["chat_input_point"] = list(mark("The chat INPUT FIELD."))
        cfg.save()

    if "pact" in wanted:
        print()
        print("=== pact ===")
        print("  Both roles need this: the chat cannot be reached while the stash is")
        print("  open, so every cycle shuts it and clicks it open again.")
        print("  ESC closes it, so only reopening needs a position.")
        print("  Stand where you farm, with the stash CLOSED.")
        cfg.data["stash_object_point"] = list(mark("The STASH OBJECT in the world."))
        cfg.save()

    if "anchors" in wanted:
        print("\n=== panel anchors ===")
        print("  These are what stops a pass running with a panel closed, which would drop")
        print("  your items on the floor instead of moving them. Both panels open, please.")
        calibrate_anchor(cfg, "inventory", "INVENTORY")
        calibrate_anchor(cfg, "stash", "BLOOD PACT STASH")
        cfg.save()

    print(f"\nWritten to {PATH}. Check it with `python -m hsduper scan`.")


def pitch_from(profile: np.ndarray) -> tuple[int, float]:
    """The dominant spacing in a 1-D profile, by autocorrelation.

    Slot borders repeat, so the luminance profile across a grid is periodic at
    exactly the cell pitch. Reading that off the pixels is a good deal more
    reliable than counting icons in a screenshot.
    """
    centred = profile - profile.mean()
    if not np.any(centred):
        return 0, 0.0
    auto = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
    auto = auto / auto[0]
    low, high = 20, min(len(centred) // 2, 260)
    if high <= low:
        return 0, 0.0
    lag = int(np.argmax(auto[low:high])) + low
    return lag, float(auto[lag])


def probe(cfg: Config, name: str | None) -> None:
    """Measure a grid's pitch off the screen, without being told the counts."""
    if name:
        region = cfg.grid(name).region
        print(f"probing the calibrated region of {name}: {region}")
    else:
        print("\n=== probe ===")
        print("  Mark the two opposite corners of the grid AREA - the outer edge of the")
        print("  slots, not their centres.")
        x0, y0 = mark("The TOP-LEFT corner of the grid area.")
        x1, y1 = mark("The BOTTOM-RIGHT corner of the grid area.")
        region = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        print(f"     region {region}")

    frame = capture.grab(region)
    if capture.looks_blank(frame):
        print("  capture came back blank - is the game in exclusive fullscreen?")
        return
    lum = capture.luminance(frame)
    pitch_x, conf_x = pitch_from(lum.mean(axis=0))
    pitch_y, conf_y = pitch_from(lum.mean(axis=1))
    width, height = region[2], region[3]

    print(f"\n  pitch  x {pitch_x} px (confidence {conf_x:.2f})")
    print(f"         y {pitch_y} px (confidence {conf_y:.2f})")
    if pitch_x and pitch_y:
        print(f"  which over {width}x{height} is about "
              f"{width / pitch_x:.1f} columns and {height / pitch_y:.1f} rows")
        print(f"\n  so: {round(height / pitch_y)} rows, {round(width / pitch_x)} columns")
    if min(conf_x, conf_y) < 0.25:
        print("\n  low confidence - the region probably included something other than the")
        print("  grid. Mark it again, tighter to the slots.")
