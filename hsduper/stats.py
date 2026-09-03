"""Persistent receiver item-opening statistics, grouped by run and globally."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .transfer import Report, Result

PATH = Path(__file__).resolve().parent.parent / "stats.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary(cycles: list[dict]) -> dict:
    return {
        "cycles_completed": sum(cycle["status"] == "completed" for cycle in cycles),
        "cycles_incomplete": sum(cycle["status"] != "completed" for cycle in cycles),
        "items_confirmed_opened": sum(int(cycle["confirmed_opened"]) for cycle in cycles),
        "opening_passes": sum(int(cycle["opening_passes"]) for cycle in cycles),
        "items_remaining": sum(int(cycle["remaining"]) for cycle in cycles),
    }


def _totals(sessions: list[dict]) -> dict:
    cycles = [cycle for session in sessions for cycle in session.get("cycles", [])]
    summary = _summary(cycles)
    return {
        "sessions": len(sessions),
        "sessions_completed": sum(session.get("status") == "completed" for session in sessions),
        "sessions_stopped": sum(
            session.get("status") in ("stopped", "aborted", "interrupted")
            for session in sessions
        ),
        **summary,
    }


def load(path: Path | None = None) -> dict:
    path = path or PATH
    if not path.exists():
        return {"version": 1, "totals": _totals([]), "sessions": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("sessions"), list):
        raise ValueError(f"{path} does not contain a valid sessions list")
    data["version"] = 1
    data["totals"] = _totals(data["sessions"])
    return data


class OpeningSession:
    """One receiver invocation, persisted after every meaningful event."""

    def __init__(self, data: dict, session: dict, path: Path, now=_now):
        self.data = data
        self.session = session
        self.path = path
        self.now = now
        self.finished = False

    @classmethod
    def start(cls, requested_cycles: int, path: Path | None = None, now=_now):
        path = path or PATH
        data = load(path)
        timestamp = now()
        for previous in data["sessions"]:
            if previous.get("status") == "running":
                previous["status"] = "interrupted"
                previous["ended_at"] = timestamp
                previous["stop_reason"] = "a newer receiver session started"
                previous["summary"] = _summary(previous.get("cycles", []))

        session = {
            "id": f"{timestamp}-{uuid.uuid4().hex[:8]}",
            "started_at": timestamp,
            "ended_at": None,
            "status": "running",
            "requested_cycles": int(requested_cycles),
            "cycles": [],
            "summary": _summary([]),
        }
        data["sessions"].append(session)
        tracker = cls(data, session, path, now=now)
        tracker._save()
        return tracker

    def _save(self) -> None:
        self.session["summary"] = _summary(self.session["cycles"])
        self.data["totals"] = _totals(self.data["sessions"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def record_cycle(self, number: int, withdrawn: int, report: Report) -> None:
        complete = report.result is Result.DONE and report.left == 0
        self.session["cycles"].append({
            "number": int(number),
            "status": "completed" if complete else "incomplete",
            "withdrawn": int(withdrawn),
            "confirmed_opened": int(report.moved),
            "opening_passes": int(report.passes),
            "remaining": int(report.left),
        })
        self._save()

    def finish(self, status: str, reason: str | None = None) -> None:
        if self.finished:
            return
        self.session["status"] = status
        self.session["ended_at"] = self.now()
        if reason:
            self.session["stop_reason"] = reason
        self.finished = True
        self._save()

    def summary_line(self) -> str:
        current = self.session["summary"]
        total = self.data["totals"]
        return (
            f"session: {current['items_confirmed_opened']} confirmed opened in "
            f"{current['cycles_completed']} completed cycle(s); all time: "
            f"{total['items_confirmed_opened']} in {total['cycles_completed']} cycle(s)"
        )


def print_report(path: Path | None = None, log=print) -> None:
    path = path or PATH
    data = load(path)
    total = data["totals"]
    log(f"opening stats: {path}")
    log(
        f"  all time: {total['items_confirmed_opened']} confirmed opened, "
        f"{total['cycles_completed']} completed cycle(s), {total['sessions']} session(s)"
    )
    if not data["sessions"]:
        log("  no receiver sessions recorded yet")
        return
    latest = data["sessions"][-1]
    summary = latest["summary"]
    log(
        f"  latest session ({latest['status']}): "
        f"{summary['items_confirmed_opened']} confirmed opened, "
        f"{summary['cycles_completed']} completed cycle(s), "
        f"{summary['items_remaining']} remaining"
    )
