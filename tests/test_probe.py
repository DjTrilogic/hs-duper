"""The pitch probe, against grids whose spacing is known by construction."""

import numpy as np
import pytest

from hsduper import capture
from hsduper.calibrate import pitch_from
from tests.test_grid import a_grid, frame_for


@pytest.mark.parametrize(
    "pitch_x,pitch_y,rows,cols",
    [
        (97.0, 62.0, 6, 10),   # the inventory's shape: wide cells, short rows
        (57.0, 48.0, 18, 15),  # the stash: many small cells
        (64.9, 61.7, 6, 15),   # the mis-entered geometry, to prove it is separable
    ],
)
def test_probe_recovers_the_pitch(pitch_x, pitch_y, rows, cols):
    grid = a_grid(rows=rows, cols=cols, pitch_x=pitch_x, pitch_y=pitch_y)
    mask = np.ones((rows, cols), bool)
    lum = capture.luminance(frame_for(grid, mask))
    got_x, conf_x = pitch_from(lum.mean(axis=0))
    got_y, conf_y = pitch_from(lum.mean(axis=1))
    assert abs(got_x - pitch_x) <= 1.5, f"x: got {got_x}, wanted {pitch_x}"
    assert abs(got_y - pitch_y) <= 1.5, f"y: got {got_y}, wanted {pitch_y}"
    assert min(conf_x, conf_y) > 0.25


def test_implied_counts_come_out_right():
    """What the probe actually tells you: how many rows and columns to type."""
    grid = a_grid(rows=6, cols=10, pitch_x=97.0, pitch_y=62.0)
    lum = capture.luminance(frame_for(grid, np.ones((6, 10), bool)))
    height, width = lum.shape
    pitch_x, _ = pitch_from(lum.mean(axis=0))
    pitch_y, _ = pitch_from(lum.mean(axis=1))
    assert round(width / pitch_x) == 10
    assert round(height / pitch_y) == 6


def test_a_flat_profile_reports_no_confidence():
    flat = np.full(600, 30.0)
    assert pitch_from(flat) == (0, 0.0)


def test_a_config_from_the_three_grid_version_still_loads():
    """The inventory was calibrated twice back when the panel was thought to
    move with the stash. Either of those names still answers to `inventory`."""
    from hsduper.config import Config

    stored = {"name": "inventory_stash_open", "x0": 1576.0, "y0": 844.0,
              "pitch_x": 97.0, "pitch_y": 62.0, "rows": 6, "cols": 10, "threshold": 50.0}
    cfg = Config({"grids": {"inventory_stash_open": stored}})
    assert cfg.has_grid("inventory")
    grid = cfg.grid("inventory")
    assert grid.name == "inventory"
    assert (grid.pitch_x, grid.rows, grid.cols) == (97.0, 6, 10)


def test_an_uncalibrated_grid_says_so():
    from hsduper.config import Config

    cfg = Config({"grids": {}})
    assert not cfg.has_grid("inventory")
    with pytest.raises(KeyError, match="not calibrated"):
        cfg.grid("inventory")
