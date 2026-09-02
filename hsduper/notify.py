"""Signalling between the two machines, out of band.

The sender must never close the stash, and the in-game chat cannot be reached
while it is open - so the go signal cannot travel through the game at all. It
goes over the internet instead, on a pub/sub topic both sides know.

What leaves the machine is one short token on a random topic name, and nothing
else: no account name, no character, nothing about the game. Anyone who knows
the topic can read and publish to it, which is why the topic is a long random
string rather than something memorable.
"""

import json
import queue
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://ntfy.sh"
USER_AGENT = "hs-duper"


def new_topic() -> str:
    """A topic nobody will guess. Both machines need the same one."""
    return "hsduper-" + secrets.token_urlsafe(24)


def _http_post(url: str, body: bytes, timeout: float) -> None:
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _http_get(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _http_stream(url: str, timeout: float):
    """A held-open connection, yielding one line per message."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def _interrupt_stream(stream) -> None:
    """Wake a worker blocked in an HTTPResponse SSL read, then close it."""
    try:
        raw = getattr(getattr(stream, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError):
        pass
    closer = getattr(stream, "close", None)
    if closer:
        try:
            closer()
        except (OSError, ValueError):
            pass


class NtfyNotifier:
    """Publish, poll diagnostics, and stream live messages from one topic."""

    def __init__(self, topic: str, base: str = DEFAULT_BASE,
                 get=_http_get, post=_http_post, stream=_http_stream,
                 timeout: float = 10.0):
        self.topic = topic
        self.base = base.rstrip("/")
        self._get = get
        self._post = post
        self._stream = stream
        self.timeout = timeout
        self._seen: set[str] = set()
        self._since = "10s"

    def announce(self, token: str) -> None:
        self._post(f"{self.base}/{self.topic}", token.encode("utf-8"), self.timeout)

    def poll(self) -> list[str]:
        """Messages that have arrived since the last poll."""
        url = f"{self.base}/{self.topic}/json?poll=1&since={self._since}"
        try:
            raw = self._get(url, self.timeout)
        except (urllib.error.URLError, OSError, TimeoutError):
            # A failed poll is a lost second, not a lost signal: the next one
            # asks for the same window again.
            return []

        out = []
        for line in raw.splitlines():
            text = self._text_of(line)
            if text is not None:
                out.append(text)
        return out

    def poll_for(self, match, timeout: float = 20.0, interval_s: float = 0.5):
        """Poll briefly until a matching message is replayed from the cache.

        This is for the one-shot `ping` diagnostic. Unlike a receiver waiting
        for minutes, a short round-trip check benefits from cache replay: it
        cannot miss a message published just before its listening connection
        opens.
        """
        deadline = time.monotonic() + timeout
        while True:
            for text in self.poll():
                if match(text):
                    return text
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(interval_s, remaining))

    def _text_of(self, line) -> str | None:
        """One stream line to a message body, or None if it is not one.

        ntfy sends open events and keepalives down the same channel, and can
        repeat a message that falls in an overlapping window. A repeat acted on
        twice is a second withdraw nobody asked for, so identity is checked
        here rather than by the caller.
        """
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.strip()
        if not line:
            return None
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return None
        if message.get("event") != "message":
            return None
        ident = message.get("id")
        if ident:
            if ident in self._seen:
                return None
            self._seen.add(ident)
            self._since = ident
        text = message.get("message")
        return text if isinstance(text, str) else None

    def wait_for(self, match, timeout: float = 600.0, reconnect_s: float = 2.0,
                 cancelled=None, cancel_poll_s: float = 0.05):
        """Wait on a held-open connection, not by asking over and over.

        Polling once a second would be thousands of requests across a wait, and
        the public relay rate-limits anonymous callers on a burst bucket - the
        loop would start being refused partway through, silently, right when it
        mattered. One streamed connection is what the service is built for; a
        dropped one is reconnected.

        Reading an HTTPS stream can block inside SSL until the next server
        keepalive. Do that read in a daemon thread and let this caller inspect
        `cancelled` frequently, so F12 does not have to wait for network I/O.
        """
        cancelled = cancelled or (lambda: False)
        deadline = time.monotonic() + timeout
        while True:
            if cancelled():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            url = f"{self.base}/{self.topic}/json?since={self._since}"
            events = queue.Queue()
            stop_reading = threading.Event()
            active_stream = {}

            def read_connection():
                stream = None
                try:
                    stream = self._stream(url, min(remaining, 45.0))
                    active_stream["value"] = stream
                    for line in stream:
                        if stop_reading.is_set():
                            break
                        events.put(("line", line))
                except (urllib.error.URLError, OSError, TimeoutError):
                    pass
                except BaseException as exc:
                    events.put(("error", exc))
                finally:
                    if stream is not None:
                        _interrupt_stream(stream)
                    events.put(("done", None))

            threading.Thread(target=read_connection, daemon=True).start()
            try:
                while True:
                    if cancelled():
                        return None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    try:
                        kind, payload = events.get(timeout=min(cancel_poll_s, remaining))
                    except queue.Empty:
                        continue
                    if kind == "line":
                        line = payload
                        text = self._text_of(line)
                        if text is not None and match(text):
                            return text
                    elif kind == "error":
                        raise payload
                    else:
                        break
            finally:
                stop_reading.set()
                stream = active_stream.get("value")
                if stream is not None:
                    threading.Thread(
                        target=_interrupt_stream, args=(stream,), daemon=True
                    ).start()

            reconnect_until = min(time.monotonic() + reconnect_s, deadline)
            while time.monotonic() < reconnect_until:
                if cancelled():
                    return None
                time.sleep(min(cancel_poll_s, reconnect_until - time.monotonic()))


def from_config(cfg):
    settings = cfg.data.get("notify") or {}
    topic = settings.get("topic")
    if not topic:
        raise KeyError(
            "no notify topic set. Run `python -m hsduper link` to make one, and give "
            "the same topic to whoever is receiving."
        )
    return NtfyNotifier(topic, settings.get("base", DEFAULT_BASE))
