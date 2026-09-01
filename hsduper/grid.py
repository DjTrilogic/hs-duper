"""A grid of item slots, and how to tell which of them hold something."""

from dataclasses import dataclass, asdict

import numpy as np

from . import capture

#: How much of a cell is looked at, as a fraction of its pitch. The border and
#: the slot highlight live in the outer ~22% on each side and are present
#: whether or not the slot holds an item, so including them narrows the gap
#: between a full cell and an empty one for no gain.
INNER = 0.56


@dataclass
class Grid:
    """Geometry is stored as the centre of the top-left cell plus the pitch.

    Pitch is per-axis on purpose: this game's inventory cells are not square,
    and assuming they were put every click in the lower rows off by a growing
    margin.
    """

    name: str
    x0: float
    y0: float
    pitch_x: float
    pitch_y: float
    rows: int
    cols: int
    threshold: float = 0.0

    def cell_center(self, row: int, col: int) -> tuple[int, int]:
        return (round(self.x0 + col * self.pitch_x), round(self.y0 + row * self.pitch_y))

    @property
    def region(self) -> tuple[int, int, int, int]:
        """The whole grid as (left, top, width, height), one cell's worth of
        margin around the outermost cell centres."""
        left = round(self.x0 - self.pitch_x / 2)
        top = round(self.y0 - self.pitch_y / 2)
        return (left, top, round(self.pitch_x * self.cols), round(self.pitch_y * self.rows))

    def metrics(self, frame: np.ndarray) -> np.ndarray:
        """Per cell, the 95th percentile of luminance over its inner rect.

        An empty slot is near-uniform dark blue, so its bright tail stays low.
        Any item icon puts some bright pixels in the cell, whatever its colour,
        which is why this separates far better than sampling a single pixel.
        """
        lum = capture.luminance(frame)
        height, width = lum.shape
        out = np.zeros((self.rows, self.cols))
        half_x = self.pitch_x * INNER / 2
        half_y = self.pitch_y * INNER / 2
        for row in range(self.rows):
            cy = (row + 0.5) * self.pitch_y
            y1, y2 = max(0, round(cy - half_y)), min(height, round(cy + half_y))
            for col in range(self.cols):
                cx = (col + 0.5) * self.pitch_x
                x1, x2 = max(0, round(cx - half_x)), min(width, round(cx + half_x))
                patch = lum[y1:y2, x1:x2]
                out[row, col] = float(np.percentile(patch, 95)) if patch.size else 0.0
        return out

    def scan(self, frame: np.ndarray) -> np.ndarray:
        return self.metrics(frame) >= self.threshold

    def occupied(self) -> np.ndarray:
        frame = capture.grab(self.region)
        if capture.looks_blank(frame):
            raise BlankCapture(
                f"the capture of {self.name} came back blank - Hero Siege is most likely in "
                "exclusive fullscreen. Switch it to borderless windowed."
            )
        return self.scan(frame)

    def cells(self, mask: np.ndarray) -> list[tuple[int, int]]:
        return [(int(r), int(c)) for r, c in zip(*np.nonzero(mask))]

    def render(self, mask: np.ndarray) -> str:
        head = "    " + "".join(str(c % 10) for c in range(self.cols))
        rows = [f"{r:>3} " + "".join("#" if v else "." for v in mask[r]) for r in range(self.rows)]
        return "\n".join([head, *rows])

    def to_dict(self) -> dict:
        return asdict(self)


class BlankCapture(RuntimeError):
    pass
