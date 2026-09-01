"""Geometry and occupancy, on synthetic frames with a known answer.

The live check is `python -m hsduper scan` against the real game; these cover
the parts that can be wrong without the game being open - the arithmetic, the
non-square pitch, and the threshold.
"""

import math

import numpy as np
import pytest

from hsduper.grid import Grid

DARK, BRIGHT = 14.0, 210.0


def frame_for(grid: Grid, mask: np.ndarray) -> np.ndarray:
    """Draw a grid whose occupied cells carry a bright blob in the middle."""
    height = round(grid.pitch_y * grid.rows)
    width = round(grid.pitch_x * grid.cols)
    rng = np.random.default_rng(0)
    frame = np.full((height, width, 3), DARK, dtype=np.float64)
    frame += rng.normal(0, 1.2, frame.shape)
    for row in range(grid.rows):
        for col in range(grid.cols):
            if not mask[row, col]:
                continue
            cy, cx = (row + 0.5) * grid.pitch_y, (col + 0.5) * grid.pitch_x
            hy, hx = grid.pitch_y * 0.3, grid.pitch_x * 0.3
            frame[round(cy - hy):round(cy + hy), round(cx - hx):round(cx + hx)] = BRIGHT
    return np.clip(frame, 0, 255).astype(np.uint8)


def a_grid(rows=6, cols=10, pitch_x=99.0, pitch_y=71.0) -> Grid:
    return Grid("t", 200.0, 300.0, pitch_x, pitch_y, rows, cols, math.sqrt(DARK * BRIGHT))


def test_cell_centres_step_by_the_pitch():
    grid = a_grid()
    assert grid.cell_center(0, 0) == (200, 300)
    assert grid.cell_center(0, 1) == (299, 300)
    assert grid.cell_center(1, 0) == (200, 371)
    assert grid.cell_center(5, 9) == (1091, 655)


def test_region_wraps_the_outermost_cells_by_half_a_pitch():
    grid = a_grid()
    left, top, width, height = grid.region
    assert (left, top) == (round(200 - 99 / 2), round(300 - 71 / 2))
    assert (width, height) == (990, 426)


@pytest.mark.parametrize(
    "name,build",
    [
        ("full", lambda r, c: np.ones((r, c), bool)),
        ("empty", lambda r, c: np.zeros((r, c), bool)),
        ("checkerboard", lambda r, c: np.indices((r, c)).sum(axis=0) % 2 == 0),
        ("one cell", lambda r, c: np.eye(r, c, dtype=bool) & (np.arange(r)[:, None] == 3)),
        ("last cell", lambda r, c: np.pad(np.ones((1, 1), bool), ((r - 1, 0), (c - 1, 0)))),
    ],
)
def test_scan_reproduces_the_pattern(name, build):
    grid = a_grid()
    mask = build(grid.rows, grid.cols)
    assert np.array_equal(grid.scan(frame_for(grid, mask)), mask), name


def test_non_square_pitch_does_not_drift_in_the_far_corner():
    """The failure this guards: assuming square cells put the lower rows off by
    a margin that grows with distance from the calibration corner."""
    grid = a_grid(rows=19, cols=15, pitch_x=57.0, pitch_y=48.0)
    mask = np.indices((grid.rows, grid.cols)).sum(axis=0) % 3 == 0
    assert np.array_equal(grid.scan(frame_for(grid, mask)), mask)


def test_fractional_pitch_survives_the_whole_grid():
    grid = a_grid(rows=12, cols=11, pitch_x=77.4, pitch_y=55.6)
    mask = np.indices((grid.rows, grid.cols)).sum(axis=0) % 2 == 1
    assert np.array_equal(grid.scan(frame_for(grid, mask)), mask)


def test_cells_and_render_agree_with_the_mask():
    grid = a_grid(rows=3, cols=4)
    mask = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=bool)
    assert grid.cells(mask) == [(0, 0), (1, 2), (2, 3)]
    assert grid.render(mask).splitlines()[1:] == ["  0 #...", "  1 ..#.", "  2 ...#"]
