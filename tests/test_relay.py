"""The built-in relay, driven by the real client over a real socket.

These are integration tests on purpose. The relay is the one place with wire
protocol in it - chunked encoding, held-open connections - and that is exactly
the kind of code that passes a unit test and fails against an actual client.
"""

import threading
import time

import pytest

from hsduper import relay
from hsduper.notify import NtfyNotifier


@pytest.fixture
def base():
    """A relay on its own port, running for the test."""
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    relay.Handler.hub = relay.Hub()
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def client(base, topic="t"):
    return NtfyNotifier(topic, base=base, timeout=5.0)


def listening_for(notifier, text, timeout=10.0):
    """Start waiting in the background, and hand back a way to collect it."""
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(got=notifier.wait_for(lambda t: t == text, timeout)),
        daemon=True,
    )
    thread.start()
    time.sleep(0.4)  # let the connection be established before publishing
    return thread, result


def test_a_message_reaches_a_waiting_listener(base):
    sender, receiver = client(base), client(base)
    thread, result = listening_for(receiver, "hsd-ready")
    sender.announce("hsd-ready")
    thread.join(timeout=10)
    assert result.get("got") == "hsd-ready"


def test_an_empty_backlog_does_not_end_the_stream(base):
    """A zero-length chunk is the terminator in chunked encoding. Writing one
    for an empty backlog closed the response before any message went out - the
    relay accepted publishes and delivered nothing."""
    sender, receiver = client(base), client(base)
    thread, result = listening_for(receiver, "late")
    time.sleep(0.5)  # the listener sits on an empty topic for a while first
    sender.announce("late")
    thread.join(timeout=10)
    assert result.get("got") == "late"


def test_a_message_published_while_nobody_listens_is_replayed(base):
    """The receiver is off the wire for part of every cycle, using items."""
    sender, receiver = client(base), client(base)
    thread, result = listening_for(receiver, "first")
    sender.announce("first")
    thread.join(timeout=10)
    assert result.get("got") == "first"

    sender.announce("second")
    assert receiver.wait_for(lambda t: t == "second", timeout=5) == "second"


def test_a_replayed_message_does_not_fire_twice(base):
    sender, receiver = client(base), client(base)
    thread, result = listening_for(receiver, "once")
    sender.announce("once")
    thread.join(timeout=10)
    assert receiver.wait_for(lambda t: t == "once", timeout=1.5, reconnect_s=0.2) is None


def test_topics_do_not_leak_into_each_other(base):
    """Both sides of a pact share a topic; two pacts on one relay must not see
    each other's signals."""
    ours, theirs = client(base, "ours"), client(base, "theirs")
    theirs.announce("not for us")
    assert ours.wait_for(lambda t: True, timeout=1.5, reconnect_s=0.2) is None


def test_poll_mode_returns_at_once(base):
    """`ping` polls rather than streams, to start from a known point."""
    sender, reader = client(base), client(base)
    reader.poll()
    sender.announce("hsd-ready")
    assert reader.poll() == ["hsd-ready"]


def test_the_hub_forgets_messages_older_than_the_retention_window(monkeypatch):
    """Held in memory, so it must not grow without bound over a long run."""
    monkeypatch.setattr(relay, "RETAIN_S", 0)
    hub = relay.Hub()
    hub.publish("t", "old")
    hub.publish("t", "new")
    assert [m["message"] for m in hub._topics["t"]] == ["new"]


def test_pruning_never_drops_the_message_being_published(monkeypatch):
    """Timestamps are whole seconds, so a message published now can already
    read as older than a cutoff of now. Pruning it is not retention, it is a
    lost go signal."""
    monkeypatch.setattr(relay, "RETAIN_S", 0)
    hub = relay.Hub()
    hub.publish("t", "the only one")
    assert [m["message"] for m in hub._topics["t"]] == ["the only one"]


def test_a_duration_since_means_a_window_not_nothing():
    """A client asks for `since=10s` before it has seen any id. Treating that
    as an unknown id told it there was never anything there - so a poller
    would never receive a thing, forever."""
    hub = relay.Hub()
    hub.publish("t", "just now")
    fresh, _ = hub.after("t", "10s")
    assert [m["message"] for m in fresh] == ["just now"]


def test_an_unknown_id_starts_from_the_end_not_the_beginning():
    """Replaying history to a client resuming from a dropped id would have it
    act on a stale go signal."""
    hub = relay.Hub()
    hub.publish("t", "ancient")
    fresh, index = hub.after("t", "m999")
    assert fresh == []
    assert index == 1


def test_duration_parsing():
    assert relay._duration("10s") == 10
    assert relay._duration("5m") == 300
    assert relay._duration("2h") == 7200
    for junk in ("m123", "abc", "", "s", "10x", "m5"):
        assert relay._duration(junk) is None, junk
