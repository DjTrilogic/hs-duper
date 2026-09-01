"""Reading Blood Pact chat out of HS Tracker's packet log.

HS Tracker (the sibling repo) captures the game's traffic and, when its packet
log is switched on, appends every parsed message to debug-capture.jsonl. Chat
travels in that stream in the clear, so the coordination signal needs no changes
to HS Tracker at all - this tails a file it already writes.

A chat record looks like:

    {"message": "yes tel aviv", "name": "johnpaladin", "chatRoom": "0",
     "msgType": "0", "uid": "5788306", "time": "1787747845336", ...}

The same `message` field also carries server chatter - "ok", "No new mail",
"Quest updated succesfully" - but only real chat carries `name`, which is what
tells the two apart.
"""

import json
import time
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChatEvent:
    name: str
    room: int
    text: str
    uid: str
    at_ms: int

    def __str__(self) -> str:
        return f"[room {self.room}] {self.name}: {self.text}"


def _as_int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def chat_from(obj) -> ChatEvent | None:
    """A chat event out of one parsed record, or None.

    `name` is the discriminator: the server's own messages travel in the same
    field but have no sender, so requiring one drops them without needing a
    list of their wordings.
    """
    if not isinstance(obj, dict):
        return None
    text, name = obj.get("message"), obj.get("name")
    if not isinstance(text, str) or not isinstance(name, str) or not name:
        return None
    return ChatEvent(
        name=name,
        room=_as_int(obj.get("chatRoom")),
        text=text,
        uid=str(obj.get("uid", "")),
        at_ms=_as_int(obj.get("time"), 0),
    )


def chats_in(value) -> list[ChatEvent]:
    """Every chat event anywhere in a record, however deeply nested."""
    found = []
    if isinstance(value, dict):
        event = chat_from(value)
        if event is not None:
            found.append(event)
        for sub in value.values():
            found.extend(chats_in(sub))
    elif isinstance(value, list):
        for sub in value:
            found.extend(chats_in(sub))
    return found


class JsonlSignal:
    """Tails debug-capture.jsonl and hands back the chat in it.

    The file is opened and closed on every poll rather than held open. On
    Windows an open handle blocks a rename, and HS Tracker rolls this log at
    64 MB by renaming it - with a reader holding it, that rename fails into an
    ignored `let _ =` and the log grows without limit instead. Reading by
    offset costs one open per poll and cannot interfere with the writer.
    """

    def __init__(self, path, from_end: bool = True):
        self.path = Path(path)
        self.from_end = from_end
        self._pos: int | None = None
        self._identity: tuple[int, int] | None = None

    def close(self) -> None:
        """Nothing is held open, so there is nothing to release."""

    def poll(self) -> list[ChatEvent]:
        """Every chat event appended since the last poll."""
        try:
            info = self.path.stat()
        except OSError:
            return []
        size = info.st_size
        identity = (info.st_dev, info.st_ino)

        if self._pos is None:
            self._pos = size if self.from_end else 0
            self._identity = identity
        elif identity != self._identity:
            # A different file at the same path: HS Tracker rolled the log by
            # renaming it and starting a new one. Comparing sizes is not enough
            # - a fresh log can pass the old one's length between two polls, and
            # the roll then goes unnoticed and everything after it is skipped.
            self._identity = identity
            self._pos = 0
        elif size < self._pos:
            self._pos = 0
        if size == self._pos:
            return []

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._pos)
                data = handle.read()
        except OSError:
            return []

        # Stop at the last complete line. A tail without a newline is a record
        # still being written, and parsing it early loses it for good.
        cut = data.rfind(b'\n')
        if cut == -1:
            return []
        self._pos += cut + 1

        events = []
        for line in data[: cut + 1].decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                events.extend(chats_in(json.loads(line)))
            except json.JSONDecodeError:
                pass
        return events

    def wait_for(self, match, timeout: float = 300.0, poll_s: float = 0.05):
        """Block until a chat event satisfies `match`, or give up."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.poll():
                if match(event):
                    return event
            time.sleep(poll_s)
        return None


#: Where HS Tracker writes its packet log. On Windows it writes beside its own
#: executable, so a build run from source logs into the cargo target directory.
KNOWN_LOG_PATHS = (
    "C:/Personal/Dev/hs-tracker/src-tauri/target/debug/debug-capture.jsonl",
    "C:/Personal/Dev/hs-tracker/src-tauri/target/release/debug-capture.jsonl",
)


def default_log_path() -> Path | None:
    """The most recently written packet log we can find."""
    found = [Path(p) for p in KNOWN_LOG_PATHS if Path(p).exists()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def log_age_seconds(path) -> float:
    return time.time() - os.stat(path).st_mtime
