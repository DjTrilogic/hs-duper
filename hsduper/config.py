"""Everything machine-specific lives in config.json, written by `calibrate`."""

import json
from pathlib import Path

import numpy as np

from . import capture
from .grid import Grid

PATH = Path(__file__).resolve().parent.parent / "config.json"

#: The bag grid does not move when the stash opens. What changes above it is the
#: equipment panel, which is drawn only while the stash is closed - the slots
#: themselves stay put, so one calibration serves both states.
GRID_NAMES = ("inventory", "stash")

#: Written by an earlier version, which had the inventory calibrated twice.
LEGACY_NAMES = {"inventory": ("inventory_stash_open", "inventory_stash_closed")}

DEFAULT_TIMING = {
    # How long the mouse button stays down. Must clear a frame: a game samples
    # input about once every 17 ms at 60 fps, so a shorter press can fall
    # entirely between two polls and be missed.
    "button_hold_ms": 70,
    # Around the CTRL press, so the game sees the modifier held before the
    # button goes down and still held when it comes up.
    "ctrl_settle_ms": 45,
    "move_settle_ms": 15,
    "click_delay_ms": 60,
    "jitter_ms": 15,
    "tooltip_ms": 150,
    "pass_settle_ms": 250,
    "max_passes": 6,
    # Between the steps of sending a chat line: opening it, picking the tab,
    # focusing the field, sending.
    "chat_step_ms": 180,
    # Extra pause before the sender's own withdraw. The stash reopen already
    # sits between announcing and withdrawing and takes time of its own, so
    # this starts at nothing and is the dial for the overlap.
    "after_ready_ms": 0,
    # After clicking a panel open or shut, before believing the anchor.
    "panel_settle_ms": 450,
    # Opening can animate for longer than the generic settle delay. Poll the
    # anchor throughout this window so a late-opened stash is detected before
    # another interaction clicks it shut again.
    "panel_open_timeout_ms": 2500,
    "panel_poll_ms": 100,
    # How long each side waits on the other: the sender for the confirmation,
    # the receiver for the items to show up in the stash.
    "confirm_timeout_ms": 60000,
}

#: Phase 2. The room is a specific pact's id, not a fixed channel number, so it
#: has to be discovered per pact with `python -m hsduper listen`.
DEFAULT_PHASE2 = {
    "blood_pact_room": None,
    "ready_token": "hsd-ready",
    # The receiver's reply, sent once it can see the items in the stash. The
    # two tokens differ so neither side can be answered by its own echo - the
    # relay hands every subscriber everything, including what it published.
    "seen_token": "hsd-seen",
    "own_name": None,
}

#: How far a captured anchor may drift from the calibrated colour before the
#: panel is treated as not open. Generous, because the panel is drawn over a
#: moving scene and its titles pick up a little of what is behind them.
ANCHOR_TOLERANCE = 26

#: How far the cursor may sit from where it was last commanded before this is
#: read as the user having taken the mouse back.
CURSOR_TOLERANCE = 4


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls) -> "Config":
        if not PATH.exists():
            raise FileNotFoundError(
                f"no {PATH.name} yet - run `python -m hsduper calibrate` first"
            )
        return cls(json.loads(PATH.read_text(encoding="utf-8")))

    @classmethod
    def exists(cls) -> bool:
        return PATH.exists()

    @classmethod
    def blank(cls) -> "Config":
        return cls({"grids": {}, "anchors": {}, "park": None, "timing": dict(DEFAULT_TIMING)})

    def save(self) -> None:
        PATH.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def _stored(self, name: str) -> dict | None:
        grids = self.data.get("grids", {})
        for key in (name, *LEGACY_NAMES.get(name, ())):
            if key in grids:
                return {**grids[key], "name": name}
        return None

    def grid(self, name: str) -> Grid:
        stored = self._stored(name)
        if stored is None:
            raise KeyError(f"{name} is not calibrated - run `python -m hsduper calibrate`")
        return Grid(**stored)

    def has_grid(self, name: str) -> bool:
        return self._stored(name) is not None

    @property
    def park(self) -> tuple[int, int]:
        point = self.data.get("park")
        if not point:
            raise KeyError("no park point calibrated")
        return tuple(point)

    def timing(self, key: str) -> float:
        """A configured value wins; the default is only reached for.

        Written as `.get(key, DEFAULT_TIMING[key])` this raised KeyError for any
        key not in the defaults, even when config.json set it - because the
        fallback is evaluated before `.get` has a chance to not need it.
        """
        configured = self.data.get("timing", {})
        if key in configured:
            return configured[key]
        return DEFAULT_TIMING[key]

    def anchor_ok(self, name: str) -> bool:
        """Is the panel this anchor watches still on screen?

        This matters more than it looks. CTRL+LMB moves an item while the stash
        is open, and USES it while the stash is closed - so a pass that runs
        against a closed panel does not misplace your items, it consumes them.
        A dropped item can be picked back up; a used one cannot.

        Hence fail-closed: an anchor that was never calibrated counts as
        missing. Treating "I was never told what to look for" as "everything is
        fine" is exactly the wrong default when the cost of being wrong is the
        whole inventory.
        """
        anchor = self.data.get("anchors", {}).get(name)
        if not anchor:
            return False
        frame = capture.grab(tuple(anchor["rect"]))
        seen = frame.reshape(-1, 3).mean(axis=0)
        want = np.array(anchor["color"], dtype=float)
        return bool(np.linalg.norm(seen - want) <= self.data.get("anchor_tolerance", ANCHOR_TOLERANCE))

    def missing_anchors(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.anchor_ok(n)]
