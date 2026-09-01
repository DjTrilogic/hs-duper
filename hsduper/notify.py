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
import secrets
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


class NtfyNotifier:
    """Publish and poll one topic.

    Polling rather than holding a stream open: a long-lived connection has to
    be reconnected when it drops, and the drop is silent - the loop would sit
    waiting for a signal that can no longer arrive. At a poll a second, a lost
    request costs one second and fixes itself.
    """

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

    def wait_for(self, match, timeout: float = 600.0, reconnect_s: float = 2.0):
        """Wait on a held-open connection, not by asking over and over.

        Polling once a second would be thousands of requests across a wait, and
        the public relay rate-limits anonymous callers on a burst bucket - the
        loop would start being refused partway through, silently, right when it
        mattered. One streamed connection is what the service is built for; a
        dropped one is reconnected.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            url = f"{self.base}/{self.topic}/json?since={self._since}"
            try:
                stream = self._stream(url, min(remaining, 45.0))
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(min(reconnect_s, max(remaining, 0)))
                continue
            try:
                for line in stream:
                    text = self._text_of(line)
                    if text is not None and match(text):
                        return text
                    if time.monotonic() >= deadline:
                        return None
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            finally:
                closer = getattr(stream, "close", None)
                if closer:
                    closer()


def from_config(cfg):
    settings = cfg.data.get("notify") or {}
    topic = settings.get("topic")
    if not topic:
        raise KeyError(
            "no notify topic set. Run `python -m hsduper link` to make one, and give "
            "the same topic to whoever is receiving."
        )
    return NtfyNotifier(topic, settings.get("base", DEFAULT_BASE))
