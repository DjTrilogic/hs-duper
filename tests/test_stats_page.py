"""The local statistics dashboard remains standalone and points at stats.json."""

from pathlib import Path


PAGE = Path(__file__).resolve().parent.parent / "stats.html"


def test_stats_dashboard_is_standalone_and_loads_the_stats_file():
    html = PAGE.read_text(encoding="utf-8")

    assert "stats.json" in html
    assert "application/json" in html
    assert "https://" not in html
    assert "<script src=" not in html
