"""Reading chat out of the packet log."""

import json

import pytest

from hsduper.signal import ChatEvent, JsonlSignal, chat_from, chats_in

CHAT = {
    "_src": "143.42.34.47", "chatRoom": "0", "language": "0",
    "message": "yes tel aviv", "msgColor": "16777215", "msgPlus": "0",
    "msgType": "0", "name": "johnpaladin", "nameColor": "7844807",
    "platform": "0", "platformId": "76561198183740533",
    "platformName": "Lolswagger", "region": "6", "slot": "0",
    "time": "1787747845336", "uid": "5788306",
}


def test_reads_a_real_chat_record():
    event = chat_from(CHAT)
    assert event == ChatEvent("johnpaladin", 0, "yes tel aviv", "5788306", 1787747845336)


@pytest.mark.parametrize("record", [
    {"message": "ok", "status": "1"},
    {"message": "No new mail"},
    {"message": "Quest updated succesfully"},
    {"message": "Success!"},
])
def test_server_chatter_is_not_chat(record):
    """These travel in the same `message` field. Only real chat has a sender,
    which is what tells them apart without listing their wordings."""
    assert chat_from(record) is None


@pytest.mark.parametrize("record", [
    {"name": "someone"},
    {"message": 123, "name": "someone"},
    {"message": "hi", "name": ""},
    "not a dict",
    None,
])
def test_junk_is_not_chat(record):
    assert chat_from(record) is None


def test_a_missing_room_does_not_masquerade_as_room_zero():
    """Room 0 is a real channel, so an absent one must not read as it."""
    assert chat_from({"message": "hi", "name": "x"}).room == -1


def test_finds_chat_nested_anywhere():
    assert len(chats_in({"outer": [{"inner": CHAT}, {"noise": {"message": "ok"}}]})) == 1


def write(path, *records):
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def test_poll_returns_only_what_is_new(tmp_path):
    log = tmp_path / "debug-capture.jsonl"
    write(log, CHAT)
    signal = JsonlSignal(log, from_end=False)
    assert [e.text for e in signal.poll()] == ["yes tel aviv"]
    assert signal.poll() == []

    write(log, {**CHAT, "message": "second"}, {**CHAT, "message": "third"})
    assert [e.text for e in signal.poll()] == ["second", "third"]


def test_from_end_skips_the_backlog(tmp_path):
    """A session's worth of old chat must not fire the loop on startup."""
    log = tmp_path / "debug-capture.jsonl"
    write(log, {**CHAT, "message": "ancient history"})
    signal = JsonlSignal(log, from_end=True)
    assert signal.poll() == []
    write(log, {**CHAT, "message": "live"})
    assert [e.text for e in signal.poll()] == ["live"]


def test_a_half_written_line_is_not_parsed_early(tmp_path):
    log = tmp_path / "debug-capture.jsonl"
    signal = JsonlSignal(log, from_end=False)
    with log.open("w", encoding="utf-8") as f:
        f.write('{"message": "part')
        f.flush()
        assert signal.poll() == []
        f.write('ial", "name": "x"}\n')
        f.flush()
    assert [e.text for e in signal.poll()] == ["partial"]


def test_survives_the_log_being_rolled(tmp_path):
    """HS Tracker renames the log at 64 MB and starts a new one. The handle
    then points at a file nothing writes to any more."""
    log = tmp_path / "debug-capture.jsonl"
    write(log, CHAT)
    signal = JsonlSignal(log, from_end=False)
    assert len(signal.poll()) == 1

    log.rename(tmp_path / "debug-capture.old.jsonl")
    write(log, {**CHAT, "message": "after the roll"})
    assert [e.text for e in signal.poll()] == ["after the roll"]


def test_a_missing_log_is_patience_not_a_crash(tmp_path):
    signal = JsonlSignal(tmp_path / "not-there-yet.jsonl")
    assert signal.poll() == []


def test_bad_json_is_skipped_without_losing_the_next_line(tmp_path):
    log = tmp_path / "debug-capture.jsonl"
    with log.open("w", encoding="utf-8") as f:
        f.write("{ not json at all\n")
        f.write(json.dumps(CHAT) + "\n")
    signal = JsonlSignal(log, from_end=False)
    assert [e.text for e in signal.poll()] == ["yes tel aviv"]


def test_wait_for_matches_and_times_out(tmp_path):
    log = tmp_path / "debug-capture.jsonl"
    write(log, {**CHAT, "message": "GO", "name": "partner"})
    signal = JsonlSignal(log, from_end=False)
    found = signal.wait_for(lambda e: e.text == "GO", timeout=1.0)
    assert found is not None and found.name == "partner"
    assert signal.wait_for(lambda e: e.text == "never", timeout=0.2) is None
