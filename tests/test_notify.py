"""The out-of-band go signal, with HTTP replaced by recorded calls."""

import json
import threading
import time

import pytest

from hsduper.notify import NtfyNotifier, new_topic


def message(ident, text, event="message"):
    return json.dumps({"id": ident, "event": event, "topic": "t", "message": text})


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.posted = []
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        if not self.responses:
            return ""
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def post(self, url, body, timeout):
        self.posted.append((url, body.decode()))


def notifier(http, **kw):
    return NtfyNotifier("t", base="https://example.test", get=http.get, post=http.post, **kw)


def test_topics_are_unguessable_and_unique():
    a, b = new_topic(), new_topic()
    assert a != b and len(a) > 24 and a.startswith("hsduper-")


def test_announce_posts_the_token():
    http = FakeHttp()
    notifier(http).announce("hsd-ready")
    assert http.posted == [("https://example.test/t", "hsd-ready")]


def test_poll_returns_messages():
    http = FakeHttp(message("1", "hsd-ready"))
    assert notifier(http).poll() == ["hsd-ready"]


def test_the_same_message_is_never_returned_twice():
    """ntfy is asked for a window of time, so a slow poll can see a message it
    has already reported. Acting on it twice means a second withdraw nobody
    asked for."""
    http = FakeHttp(message("1", "hsd-ready"), message("1", "hsd-ready"))
    n = notifier(http)
    assert n.poll() == ["hsd-ready"]
    assert n.poll() == []


def test_polling_resumes_from_the_last_message():
    http = FakeHttp(message("abc", "one"), message("def", "two"))
    n = notifier(http)
    n.poll()
    n.poll()
    assert "since=abc" in http.urls[1], http.urls


def test_non_message_events_are_ignored():
    """ntfy sends keepalives and open events down the same channel."""
    http = FakeHttp(message("1", "", event="open") + "\n" + message("2", "hsd-ready"))
    assert notifier(http).poll() == ["hsd-ready"]


def test_a_failed_poll_is_a_lost_second_not_a_lost_signal():
    http = FakeHttp(OSError("network down"), message("1", "hsd-ready"))
    n = notifier(http)
    assert n.poll() == []
    assert n.poll() == ["hsd-ready"]


def test_junk_lines_do_not_stop_the_good_ones():
    http = FakeHttp("not json\n" + message("1", "hsd-ready"))
    assert notifier(http).poll() == ["hsd-ready"]


def test_poll_for_retries_until_the_matching_message_is_cached():
    http = FakeHttp("", message("1", "noise"), message("2", "hsd-ready#123"))
    got = notifier(http).poll_for(
        lambda text: text == "hsd-ready#123", timeout=1.0, interval_s=0.001
    )
    assert got == "hsd-ready#123"
    assert len(http.urls) == 3


class FakeStream:
    """A held-open connection: each entry is one connection's worth of lines.

    An entry may be an exception, standing for the connection dropping.
    """

    def __init__(self, *connections):
        self.connections = list(connections)
        self.opened = []

    def open(self, url, timeout):
        self.opened.append(url)
        if not self.connections:
            raise TimeoutError("nothing more")
        nxt = self.connections.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return iter(nxt)


def streaming(stream, **kw):
    return NtfyNotifier("t", base="https://example.test",
                        get=lambda u, t: "", post=lambda u, b, t: None,
                        stream=stream.open, **kw)


def test_wait_for_returns_the_matching_message():
    stream = FakeStream([message("1", "noise"), message("2", "hsd-ready")])
    assert streaming(stream).wait_for(lambda t: t == "hsd-ready", timeout=5) == "hsd-ready"


def test_wait_for_gives_up_at_the_deadline():
    assert streaming(FakeStream()).wait_for(lambda t: True, timeout=0.3,
                                            reconnect_s=0.01) is None


def test_one_wait_is_one_connection_not_a_request_per_second():
    """Polling would be thousands of requests across a wait, and the public
    relay rate-limits anonymous callers on a burst bucket."""
    stream = FakeStream([message("1", "hsd-ready")])
    streaming(stream).wait_for(lambda t: t == "hsd-ready", timeout=5)
    assert len(stream.opened) == 1


def test_a_dropped_connection_is_reconnected():
    stream = FakeStream(OSError("dropped"), [message("1", "hsd-ready")])
    got = streaming(stream).wait_for(lambda t: t == "hsd-ready", timeout=5, reconnect_s=0.01)
    assert got == "hsd-ready"
    assert len(stream.opened) == 2


def test_reconnecting_resumes_after_the_last_message_seen():
    """Otherwise the signal sent during the gap is missed entirely."""
    stream = FakeStream([message("abc", "noise")], [message("def", "hsd-ready")])
    streaming(stream).wait_for(lambda t: t == "hsd-ready", timeout=5, reconnect_s=0.01)
    assert "since=abc" in stream.opened[1]


def test_a_repeat_after_reconnect_is_not_acted_on_twice():
    stream = FakeStream([message("1", "hsd-ready")], [message("1", "hsd-ready")])
    n = streaming(stream)
    assert n.wait_for(lambda t: t == "hsd-ready", timeout=5) == "hsd-ready"
    assert n.wait_for(lambda t: t == "hsd-ready", timeout=0.3, reconnect_s=0.01) is None


class BlockingConnection:
    def __init__(self):
        self.reading = threading.Event()
        self.released = threading.Event()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        self.reading.set()
        self.released.wait(5)
        raise StopIteration

    def close(self):
        self.closed = True
        self.released.set()


def test_wait_for_can_be_cancelled_while_the_network_read_is_blocked():
    connection = BlockingConnection()
    cancelled = threading.Event()
    n = streaming(type("Stream", (), {"open": lambda self, url, timeout: connection})())
    threading.Timer(0.05, cancelled.set).start()
    started = time.monotonic()

    assert n.wait_for(
        lambda _: False, timeout=5, cancelled=cancelled.is_set, cancel_poll_s=0.01
    ) is None

    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert connection.released.wait(0.5)
    assert connection.closed
