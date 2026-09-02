"""A relay of your own.

The public ntfy service caps anonymous use at a couple of hundred messages a
day, which a long run passes in an afternoon. That cap is what keeps a free
service affordable, so the answer is not to get around it but to stop needing
it: this is the small piece of it hs-duper actually uses, so one of the two
machines can host the signal and neither depends on anyone else.

It speaks the same shapes as ntfy, so `notify.base` is the only setting that
changes:

    POST /<topic>                     publish, body is the message
    GET  /<topic>/json?since=<id>     newline-delimited JSON, held open
    GET  /<topic>/json?poll=1         the same, returned at once

Messages live in memory only. That is the right trade for a go signal, which is
worthless a minute later anyway - and it means nothing to clean up.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8737

#: How long a message stays available for replay. A receiver reconnects after
#: its panel work, asking for everything since the last id it saw, so the
#: window has to comfortably outlast a cycle.
RETAIN_S = 3600

#: Sent to held-open connections so a dead peer is noticed rather than leaving
#: a thread parked on a socket forever.
KEEPALIVE_S = 30


#: Seconds per unit in ntfy's duration form, as used by `since=10s`.
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _duration(value: str) -> int | None:
    """Seconds for "10s", "5m" and friends, or None if it is not one."""
    if len(value) < 2 or value[-1] not in _UNITS or not value[:-1].isdigit():
        return None
    return int(value[:-1]) * _UNITS[value[-1]]


class Hub:
    """Topics and their messages, with subscribers waiting on new ones."""

    def __init__(self):
        self._new = threading.Condition()
        self._topics: dict[str, list[dict]] = {}
        self._next_id = 0

    def publish(self, topic: str, text: str) -> dict:
        with self._new:
            self._next_id += 1
            message = {
                "id": f"m{self._next_id}",
                "time": int(time.time()),
                "event": "message",
                "topic": topic,
                "message": text,
            }
            messages = self._topics.setdefault(topic, [])
            messages.append(message)
            # The newest always survives, whatever the window says. Timestamps
            # are whole seconds for ntfy's sake, so a message published now can
            # already read as older than a cutoff of now - and pruning the
            # message being published is not a retention policy, it is a loss.
            cutoff = time.time() - RETAIN_S
            self._topics[topic] = [m for m in messages if m["time"] >= cutoff] or [message]
            self._new.notify_all()
            return message

    def after(self, topic: str, since: str | None) -> tuple[list[dict], int]:
        """Messages following `since`, and the index to continue from.

        `since` takes two forms, and both have to work. An id continues exactly
        where a client left off. A duration like "10s" is what a client uses
        before it has seen anything - and it means the last ten seconds, not
        "nothing", which is what treating it as an unknown id amounted to: a
        poller asking for a window would be told there was never anything in
        it, forever.

        An id that is no longer held starts from the end. Replaying an hour of
        history to a client that asked to resume would have it act on a stale
        go signal.
        """
        with self._new:
            messages = self._topics.get(topic, [])
            if since:
                seconds = _duration(since)
                if seconds is not None:
                    cutoff = time.time() - seconds
                    return [m for m in messages if m["time"] >= cutoff], len(messages)
                for index, message in enumerate(messages):
                    if message["id"] == since:
                        return messages[index + 1:], len(messages)
            return [], len(messages)

    def wait(self, topic: str, index: int, timeout: float) -> tuple[list[dict], int]:
        with self._new:
            self._new.wait_for(
                lambda: len(self._topics.get(topic, [])) > index, timeout=timeout
            )
            messages = self._topics.get(topic, [])
            return messages[index:], len(messages)


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None  # set by serve()
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # the relay prints what matters itself

    def _topic(self, suffix: str = "") -> str | None:
        path = urlparse(self.path).path.strip("/")
        if suffix:
            if not path.endswith(suffix):
                return None
            path = path[: -len(suffix)].strip("/")
        return path or None

    def do_POST(self):
        topic = self._topic()
        if not topic:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(length).decode("utf-8", "replace").strip()
        message = self.hub.publish(topic, text)
        print(f"  [{time.strftime('%H:%M:%S')}] {topic}: {text}")
        body = json.dumps(message).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        topic = self._topic("/json")
        if not topic:
            self.send_error(404)
            return
        query = parse_qs(urlparse(self.path).query)
        since = (query.get("since") or [None])[0]
        polling = (query.get("poll") or ["0"])[0] not in ("0", "")

        backlog, index = self.hub.after(topic, since)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        if polling:
            body = "".join(json.dumps(m) + "\n" for m in backlog).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            self._write(backlog)
            while True:
                fresh, index = self.hub.wait(topic, index, KEEPALIVE_S)
                # A keepalive when nothing arrived: writing is how a peer that
                # has gone away is discovered, and the thread freed.
                self._write(fresh or [{"event": "keepalive", "topic": topic}])
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _write(self, messages) -> None:
        # Never write an empty chunk: a zero-length chunk is the terminator in
        # chunked encoding, so sending one for an empty backlog ends the
        # response before a single message has gone out.
        if not messages:
            return
        payload = "".join(json.dumps(m) + "\n" for m in messages).encode()
        self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
        self.wfile.flush()


def serve(port: int = DEFAULT_PORT, log=print) -> None:
    Handler.hub = Hub()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log(f"  relay listening on 0.0.0.0:{port}")
    log("  point both machines at it:  notify.base = http://<this-machine>:%d" % port)
    log("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n  stopped")
    finally:
        server.server_close()
