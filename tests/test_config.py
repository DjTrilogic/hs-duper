"""Panel anchors - the guard that decides whether clicking is safe at all."""

import base64

import numpy as np
import pytest

from hsduper import capture
from hsduper.config import Config


@pytest.fixture
def flat_screen(monkeypatch):
    """Screen capture returning one solid colour, so anchor colours are exact."""
    holder = {"rgb": (120, 40, 40)}

    def grab(region):
        left, top, width, height = region
        return np.full((height, width, 3), holder["rgb"], dtype=np.uint8)

    monkeypatch.setattr(capture, "grab", grab)
    return holder


def cfg_with_anchor(colour=(120, 40, 40), tolerance=26):
    return Config({
        "anchors": {"stash": {"rect": [10, 10, 80, 20], "color": list(colour)}},
        "anchor_tolerance": tolerance,
    })


def cfg_with_template(frame, correlation=0.65):
    luminance = capture.luminance(frame)
    encoded = base64.b64encode(np.rint(luminance).astype(np.uint8).tobytes()).decode()
    height, width = frame.shape[:2]
    return Config({
        "anchors": {
            "stash": {
                "rect": [10, 10, width, height],
                "color": [0, 0, 0],
                "luminance_template": encoded,
            }
        },
        "anchor_correlation": correlation,
    })


def test_an_anchor_that_still_matches_is_ok(flat_screen):
    assert cfg_with_anchor().anchor_ok("stash")


def test_an_anchor_that_has_changed_colour_is_not(flat_screen):
    flat_screen["rgb"] = (10, 10, 10)
    assert not cfg_with_anchor().anchor_ok("stash")


def test_small_drift_is_tolerated(flat_screen):
    """The panel is drawn over a moving scene and picks up a little of it."""
    flat_screen["rgb"] = (128, 46, 44)
    assert cfg_with_anchor().anchor_ok("stash")


def test_template_anchor_tolerates_a_large_brightness_change(monkeypatch):
    template = np.zeros((20, 80, 3), dtype=np.uint8)
    template[5:15, 18:62] = 120
    current = np.clip(template.astype(float) * 1.4 + 35, 0, 255).astype(np.uint8)
    monkeypatch.setattr(capture, "grab", lambda _: current)

    assert cfg_with_template(template).anchor_ok("stash")


def test_template_anchor_rejects_a_different_shape(monkeypatch):
    template = np.zeros((20, 80, 3), dtype=np.uint8)
    template[5:15, 18:62] = 180
    current = np.zeros_like(template)
    current[:, :12] = 180
    monkeypatch.setattr(capture, "grab", lambda _: current)

    assert not cfg_with_template(template).anchor_ok("stash")


def test_malformed_template_fails_closed(flat_screen):
    cfg = cfg_with_anchor()
    cfg.data["anchors"]["stash"]["luminance_template"] = "not base64!"

    assert not cfg.anchor_ok("stash")


def test_an_uncalibrated_anchor_fails_closed(flat_screen):
    """CTRL+LMB moves an item with the stash open and USES it with the stash
    closed, so 'I was never told what to look for' must not mean 'go ahead'.
    A dropped item can be picked back up; a used one cannot."""
    cfg = Config({"anchors": {}})
    assert not cfg.anchor_ok("stash")
    assert cfg.missing_anchors(["inventory", "stash"]) == ["inventory", "stash"]


def test_missing_anchors_names_only_the_bad_ones(flat_screen):
    cfg = Config({
        "anchors": {
            "stash": {"rect": [10, 10, 80, 20], "color": [120, 40, 40]},
            "inventory": {"rect": [10, 40, 80, 20], "color": [0, 0, 0]},
        }
    })
    assert cfg.missing_anchors(["stash", "inventory"]) == ["inventory"]


def test_a_configured_timing_wins_even_if_it_is_not_a_known_default():
    """`.get(key, DEFAULT[key])` evaluates the fallback first, so an unknown key
    raised KeyError even when config.json set it."""
    cfg = Config({"timing": {"made_up_ms": 7}})
    assert cfg.timing("made_up_ms") == 7


def test_an_unset_timing_falls_back_to_the_default():
    from hsduper.config import DEFAULT_TIMING

    assert Config({}).timing("click_delay_ms") == DEFAULT_TIMING["click_delay_ms"]


def test_a_genuinely_unknown_timing_still_raises():
    with pytest.raises(KeyError):
        Config({}).timing("no_such_setting_ms")
