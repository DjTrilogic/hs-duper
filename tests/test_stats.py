"""Persistent item-opening counters, per receiver session and all time."""

from hsduper.stats import OpeningSession, load, print_report
from hsduper.transfer import Report, Result


def clock(*values):
    moments = iter(values)
    return lambda: next(moments)


def test_records_completed_and_incomplete_cycles_in_one_session(tmp_path):
    path = tmp_path / "stats.json"
    session = OpeningSession.start(
        2, path=path, now=clock("2026-09-02T10:00:00Z", "2026-09-02T10:02:00Z")
    )
    session.record_cycle(1, 48, Report(Result.DONE, 48, 3, 0))
    session.record_cycle(2, 48, Report(Result.MAX_PASSES, 47, 5, 1))
    session.finish("stopped", "one item remained")

    saved = load(path)
    recorded = saved["sessions"][0]
    assert recorded["status"] == "stopped"
    assert recorded["stop_reason"] == "one item remained"
    assert recorded["summary"] == {
        "cycles_completed": 1,
        "cycles_incomplete": 1,
        "items_confirmed_opened": 95,
        "opening_passes": 8,
        "items_remaining": 1,
    }
    assert saved["totals"]["items_confirmed_opened"] == 95


def test_totals_accumulate_across_sessions_and_close_abandoned_runs(tmp_path):
    path = tmp_path / "stats.json"
    first = OpeningSession.start(3, path=path, now=lambda: "2026-09-02T10:00:00Z")
    first.record_cycle(1, 40, Report(Result.DONE, 40, 2, 0))

    second = OpeningSession.start(
        1, path=path, now=clock("2026-09-02T11:00:00Z", "2026-09-02T11:01:00Z")
    )
    second.record_cycle(1, 48, Report(Result.DONE, 48, 3, 0))
    second.finish("completed")

    saved = load(path)
    assert saved["sessions"][0]["status"] == "interrupted"
    assert saved["sessions"][1]["summary"]["items_confirmed_opened"] == 48
    assert saved["totals"]["sessions"] == 2
    assert saved["totals"]["sessions_completed"] == 1
    assert saved["totals"]["sessions_stopped"] == 1
    assert saved["totals"]["cycles_completed"] == 2
    assert saved["totals"]["items_confirmed_opened"] == 88


def test_print_report_shows_latest_session_and_all_time(tmp_path):
    path = tmp_path / "stats.json"
    session = OpeningSession.start(
        1, path=path, now=clock("2026-09-02T12:00:00Z", "2026-09-02T12:01:00Z")
    )
    session.record_cycle(1, 48, Report(Result.DONE, 48, 2, 0))
    session.finish("completed")
    lines = []

    print_report(path, log=lines.append)

    assert any("all time: 48 confirmed opened" in line for line in lines)
    assert any("latest session (completed): 48 confirmed opened" in line for line in lines)
