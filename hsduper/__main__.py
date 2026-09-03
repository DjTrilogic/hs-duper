"""hs-duper - bulk item transfer between the inventory and the Blood Pact stash."""

import json
import sys
import threading
import time

from . import winput

# Before anything reads or writes a screen coordinate. On a scaled display
# Windows otherwise reports coordinates in virtual pixels and every click lands
# somewhere other than where it was aimed.
winput.set_dpi_aware()

from pynput import keyboard  # noqa: E402

import numpy as np  # noqa: E402
from . import (calibrate, capture, chat, control, doctor, notify, panels, relay,  # noqa: E402
               roles, signal, stats)  # noqa: E402
from .config import GRID_NAMES, Config  # noqa: E402
from .grid import BlankCapture  # noqa: E402
from .transfer import (NotFocused, PanelClosed, Report, Result, park,  # noqa: E402
                       return_cursor_item, transfer, wait_until_occupied)  # noqa: E402

USAGE = """hs-duper

  python -m hsduper calibrate [part ...]   measure the grids (part: a grid name, park, anchors)
  python -m hsduper scan [grid ...]        print what it sees, click nothing
  python -m hsduper probe [grid]           measure a grid's pitch off the screen
  python -m hsduper click [what]           ctrl | left | plain | ctrlright | sweep
  python -m hsduper hover                  does the game react to the cursor at all?
  python -m hsduper doctor                 why input is not landing
  python -m hsduper deposit                inventory -> stash, once
  python -m hsduper withdraw               stash -> inventory, once
  python -m hsduper listen [seconds]       show Blood Pact chat as it arrives
  python -m hsduper say <text>             send a Blood Pact line, and confirm it
  python -m hsduper link [topic]           make or set the shared signal topic
  python -m hsduper ping [text]            publish a go signal
  python -m hsduper await [seconds]        wait for one
  python -m hsduper watch [seconds]        print everything on the topic
  python -m hsduper stats                  show receiver opening statistics
  python -m hsduper relay [port]           host the signal yourself
  python -m hsduper pact sender|receiver [n] [--dry-run] [--no-use]
  python -m hsduper run                    arm the hotkeys and wait

hotkeys in `run`:  F9 deposit   F10 withdraw   F12 abort   ctrl-c quit
"""


def cmd_scan(names: list[str]) -> int:
    cfg = Config.load()
    wanted = names or [n for n in GRID_NAMES if cfg.has_grid(n)]
    if not wanted:
        print("nothing calibrated yet")
        return 1
    # The cursor is an item as far as the scan is concerned, and the tooltip it
    # raises covers the neighbouring cells - so park it first, exactly as a
    # transfer pass does before every capture.
    if cfg.data.get("park"):
        park(cfg)
    else:
        print("no park point calibrated - move the mouse off the grids yourself")

    for name in wanted:
        grid = cfg.grid(name)
        print(f"\n=== {name} === {grid.rows}x{grid.cols} at {grid.region}")
        frame = capture.grab(grid.region)
        if capture.looks_blank(frame):
            print("  capture came back blank - is the game in exclusive fullscreen?")
            continue
        metrics = grid.metrics(frame)
        mask = metrics >= grid.threshold
        print(grid.render(mask))
        full, empty = metrics[mask], metrics[~mask]
        print(f"  {int(mask.sum())}/{mask.size} occupied, threshold {grid.threshold:.1f}")
        if full.size:
            print(f"  occupied cells read {full.min():.1f}..{full.max():.1f}")
        if empty.size:
            print(f"  empty cells read    {empty.min():.1f}..{empty.max():.1f}")
        if full.size and empty.size:
            margin = full.min() - empty.max()
            verdict = "comfortable" if margin > 12 else "TIGHT - recalibrate this grid"
            print(f"  margin {margin:+.1f} ({verdict})")
        if mask.all() or not mask.any():
            print("  (all-full or all-empty says nothing about whether the geometry is")
            print("   right - check this grid half full)")
    print()
    return 0


def _require_game(cfg) -> bool:
    """Nothing is sent until the game is genuinely the window receiving input."""
    expected = cfg.data.get("game_exe", doctor.DEFAULT_GAME_EXE)
    if doctor.wait_for_game(expected):
        return True
    print(f"  gave up waiting for {expected} to be focused. Nothing was sent.")
    return False


def _try_click(cfg, grid, label: str, fire, restore=None) -> bool:
    """Aim at a real item, fire, and check the grid rather than asking you.

    Aiming matters: you have to click into the game to give it focus, which
    leaves the cursor wherever you clicked - so a test that fires at the current
    cursor position is usually testing an empty patch of stash. And checking
    matters because "did anything happen?" is a question this can answer itself.
    """
    park(cfg)
    time.sleep(cfg.timing("tooltip_ms") / 1000)
    before = grid.scan(capture.grab(grid.region))
    cells = grid.cells(before)
    if not cells:
        print("  the inventory scans as empty - put an item in it first")
        return False

    row, col = cells[0]
    x, y = grid.cell_center(row, col)
    print(f"  {label}: aiming at cell {(row, col)} at ({x}, {y})", flush=True)
    winput.move_to(x, y)
    time.sleep(cfg.timing("move_settle_ms") / 1000)
    fire(x, y)
    time.sleep(0.45)

    park(cfg)
    time.sleep(cfg.timing("tooltip_ms") / 1000)
    after = grid.scan(capture.grab(grid.region))
    delta = int(before.sum()) - int(after.sum())
    if delta > 0:
        print(f"    WORKED - {delta} item(s) left the inventory")
        if restore is not None:
            winput.move_to(x, y)
            time.sleep(cfg.timing("move_settle_ms") / 1000)
            restore(x, y)
            time.sleep(0.4)
            park(cfg)
            print("    (put it back)")
        return True
    print("    nothing moved")
    return False


def cmd_click(args: list[str]) -> int:
    """Test a click and say objectively whether it landed."""
    cfg = Config.load()
    grid = cfg.grid("inventory")
    what = args[0] if args else "ctrl"

    print("put some items in the inventory, with the stash open.")
    if not _require_game(cfg):
        return 1
    doctor.report()
    print()

    hold = int(cfg.timing("button_hold_ms"))
    settle = int(cfg.timing("ctrl_settle_ms"))
    mode = cfg.data.get("ctrl_mode", winput.DEFAULT_CTRL_MODE)

    variants = {
        "left": ("plain LEFT (pick up)", lambda x, y: winput.left_click(hold_ms=hold)),
        "ctrlsweep": ("every CTRL arrangement", None),
        "plain": ("plain RIGHT (equip/use)", lambda x, y: winput.right_click(hold_ms=hold)),
        "ctrl": (f"ctrl+LEFT mode={mode} hold={hold}ms  <- the real binding",
                 lambda x, y: winput.ctrl_left_click(hold, settle, mode)),
        "ctrlright": (f"ctrl+RIGHT mode={mode} (this is Drop, not move)",
                      lambda x, y: winput.ctrl_right_click(hold, settle, mode)),
        "batch": ("ctrl+RIGHT as one SendInput batch",
                  lambda x, y: winput.batched_ctrl_right_click(x, y, mode)),
    }

    if what == "ctrlsweep":
        print("  plain clicks already land, so this is only about CTRL.")
        print("  Trying every arrangement of it. Hands off the mouse.")
        for ctrl_first in (False, True):
            for mode in winput.CTRL_MODES:
                for hold in (60, 140):
                    order = "ctrl-then-move" if ctrl_first else "move-then-ctrl"
                    label = f"[{order} mode={mode} hold={hold}ms]"

                    def fire(x, y, m=mode, h=hold, cf=ctrl_first):
                        winput.ctrl_right_click_at(x, y, m, h, 60, cf)

                    if _try_click(cfg, grid, label, fire):
                        print()
                        print("  >>> that one works. Put it in config.json:")
                        print(f'      "ctrl_mode": "{mode}",')
                        print(f'      "ctrl_first": {str(ctrl_first).lower()},')
                        print(f'      "timing": {{ "button_hold_ms": {hold} }}')
                        return 0
                    time.sleep(1.0)
        print()
        print("  No arrangement of CTRL lands, while a plain left click does. So the game")
        print("  is getting our buttons and not our modifier keys - the keyboard half of")
        print("  the injection is what is being dropped, not the mouse half.")
        print("  Next: try `click ctrl` with CTRL held down physically on your keyboard.")
        print("  If that works, we know it is only the synthetic CTRL that is refused.")
        return 1

    if what == "sweep":
        print("  trying every variant and reporting all of them - no early exit, because")
        print("  stopping at the first success is how `plain` went untested for so long.")
        print("  NOTE: `plain` is right-click = Equip/Use, so it may consume one item.")
        results = {}
        for key in ("left", "ctrl", "plain", "ctrlright"):
            label, fire = variants[key]
            restore = variants["left"][1] if key == "left" else None
            results[key] = _try_click(cfg, grid, f"[{key}] {label}", fire, restore)
            time.sleep(1.2)

        print()
        print("  " + "-" * 46)
        for key, ok in results.items():
            print(f"  {key:<8} {'LANDED' if ok else 'nothing'}")
        print("  " + "-" * 46)
        print()

        if results["left"] and not results["plain"]:
            print("  Left lands, right does not. It is the RIGHT BUTTON being dropped, not")
            print("  CTRL - which is why no arrangement of the modifier ever helped. The")
            print("  game takes our left clicks and ignores our right ones.")
        elif not results["left"] and not results["plain"]:
            print("  Neither button lands. Input is not reaching the game at all.")
        elif results["plain"] and not (results["ctrl"] or results["batch"]):
            print("  Both plain buttons land but neither CTRL form does, so the modifier is")
            print("  genuinely what is lost.")
        elif results["ctrl"] or results["batch"]:
            winner = "ctrl" if results["ctrl"] else "batch"
            print(f"  '{winner}' works - that is the one to use for deposit.")
        return 0

    if what not in variants:
        print(f"  unknown variant {what!r}: pick one of left, plain, ctrl, batch, sweep")
        return 1
    label, fire = variants[what]
    _try_click(cfg, grid, label, fire)
    return 0


def cmd_hover() -> int:
    """Does the game react to a synthetic cursor at all?

    This is the question underneath everything else. Hovering a slot makes the
    game draw something - a highlight, a tooltip - so if the screen changes when
    the cursor is moved onto an item, the game is seeing our mouse and only the
    button is being lost. If nothing changes, the game is not seeing the cursor
    either, and no amount of tuning the click will ever help.
    """
    cfg = Config.load()
    grid = cfg.grid("inventory")
    print("focus Hero Siege and hover an item. Do not touch the mouse.")
    if not _require_game(cfg):
        return 1
    doctor.report()
    print()

    # Watch the grid region and nothing else. It is static UI. A box merely
    # centred on the cell spills onto the town behind the panel, where torches
    # and wandering NPCs change tens of percent of the pixels every frame - and
    # that reads as a reaction no matter what the cursor does.
    box = grid.region

    park(cfg)
    time.sleep(0.5)
    occupied = grid.scan(capture.grab(box))
    cells = grid.cells(occupied)
    target = cells[0] if cells else (grid.rows // 2, grid.cols // 2)
    x, y = grid.cell_center(*target)

    def changed(a, b):
        return int((np.abs(a - b).sum(axis=2) > 24).sum())

    # A control first: two captures with the cursor parked and still. Whatever
    # changes between those is the noise floor, and the hover has to beat it.
    first = capture.grab(box).astype(int)
    time.sleep(0.6)
    second = capture.grab(box).astype(int)
    noise = changed(first, second)

    print(f"  watching {box} (the grid only)")
    print(f"  cursor parked, nothing moving: {noise} px changed")

    winput.move_to(x, y)
    time.sleep(0.6)
    third = capture.grab(box).astype(int)
    signal = changed(second, third)
    area = box[2] * box[3]
    print(f"  cursor moved onto cell {target} at ({x}, {y}): {signal} px changed "
          f"({100 * signal / area:.2f}%)")
    print()

    if signal > max(noise * 5, area * 0.002):
        print("  The game reacted to the cursor - well clear of the noise floor. It sees")
        print("  our mouse move, so movement is fine and only the button is being lost.")
        print("  Try:  python -m hsduper click batch")
    elif signal > noise * 2:
        print("  Something changed, but not far above the noise floor. Inconclusive - run")
        print("  it again, and keep your hands off the mouse while it works.")
    else:
        print("  The game did NOT react. It is not seeing our cursor at all, which means")
        print("  no click variant can ever work: the game does not believe the mouse is")
        print("  over that slot. The cursor you see moving is the Windows one; the game")
        print("  is tracking its own, from a source we are not feeding.")
        print("  Most likely Steam Input. Steam > Hero Siege > Properties > Controller >")
        print("  disable Steam Input, then run this again.")
    return 0


def cmd_doctor() -> int:
    print("focus Hero Siege and hover an item.")
    cfg = Config.load() if Config.exists() else Config.blank()
    _require_game(cfg)
    doctor.report()
    return 0


def cmd_probe(names: list[str]) -> int:
    cfg = Config.load() if Config.exists() else Config.blank()
    calibrate.probe(cfg, names[0] if names else None)
    return 0


def _move(cfg: Config, source_name: str, label: str) -> int:
    grid = cfg.grid(source_name)
    print(f"{label}: draining {source_name}")
    try:
        report = transfer(grid, cfg)
    except PanelClosed as exc:
        print(f"  stopped: {exc}")
        return 1
    except BlankCapture as exc:
        print(f"  stopped: {exc}")
        return 1
    except NotFocused as exc:
        print(f"  stopped: {exc}")
        return 1
    except control.Aborted as exc:
        print(f"  aborted: {exc}")
        return 1
    print(f"  {report}")
    return 0


def cmd_once(which: str) -> int:
    cfg = Config.load()
    source = "inventory" if which == "deposit" else "stash"
    if not _require_game(cfg):
        return 1
    control.clear()
    return _move(cfg, source, which)


def cmd_run() -> int:
    cfg = Config.load()
    busy = threading.Lock()

    def start(which: str) -> None:
        if not busy.acquire(blocking=False):
            print("  already running - F12 to stop it")
            return

        def work():
            try:
                control.clear()
                source = "inventory" if which == "deposit" else "stash"
                _move(cfg, source, which)
            finally:
                busy.release()

        threading.Thread(target=work, daemon=True).start()

    def on_press(key):
        if key == keyboard.Key.f9:
            start("deposit")
        elif key == keyboard.Key.f10:
            start("withdraw")
        elif key == keyboard.Key.f12:
            print("  abort")
            control.request_abort()

    print("armed: F9 deposit, F10 withdraw, F12 abort. ctrl-c to quit.")
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        control.request_abort()
        listener.stop()
    return 0


def cmd_listen(args: list[str]) -> int:
    """Print chat as it arrives, with the room number each line came in on.

    This is how the Blood Pact room id gets found: there is no list of them
    anywhere, so you send a message on the tab and read off the number it
    carries.
    """
    cfg = Config.load() if Config.exists() else Config.blank()
    seconds = float(args[0]) if args else 120.0

    path = cfg.data.get("chat_log") or signal.default_log_path()
    if not path:
        print("no debug-capture.jsonl found. HS Tracker has to be running with its")
        print("packet log switched on in Settings - that is what writes this file.")
        return 1

    age = signal.log_age_seconds(path)
    print(f"reading {path}")
    if age > 120:
        print(f"  WARNING: last written {age / 60:.0f} minutes ago. If HS Tracker is not")
        print("  running with the packet log on, nothing will arrive.")

    print(f"  listening for {seconds:.0f}s - send a message on the Blood Pact tab.")
    print()
    watcher = signal.JsonlSignal(path, from_end=True)
    rooms: dict[int, int] = {}
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            for event in watcher.poll():
                rooms[event.room] = rooms.get(event.room, 0) + 1
                print(f"  room {event.room:>3}  {event.name}: {event.text}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
        print("  stopped")

    print()
    if not rooms:
        print("  nothing arrived. Either no one is talking, or the packet log is off.")
        return 1
    print("  rooms seen: " + ", ".join(f"{r} ({n} msg)" for r, n in sorted(rooms.items())))
    print('  Put the Blood Pact one in config.json as  "blood_pact_room": <number>')
    return 0


def cmd_say(args: list[str]) -> int:
    """Send a line, then confirm it by watching the capture for it coming back.

    Sending is blind otherwise: the tab could be wrong, the field unfocused,
    the text swallowed - and every one of those looks identical from here. The
    packet log is the only honest witness to what actually went out.
    """
    cfg = Config.load()
    text = " ".join(args) or cfg.data.get("ready_token", "hsd-ready")
    room = cfg.data.get("blood_pact_room")

    path = cfg.data.get("chat_log") or signal.default_log_path()
    watcher = signal.JsonlSignal(path, from_end=True) if path else None
    if watcher is None:
        print("  no packet log found - sending without confirmation")

    if not _require_game(cfg):
        return 1

    chat.send(cfg, text)

    if watcher is None:
        return 0
    print("  waiting for it to come back through the capture...")
    seen = watcher.wait_for(lambda e: e.text == text, timeout=15.0)
    if seen is None:
        print("  it never arrived. The tab may be wrong, or the field never took focus.")
        print("  Run `listen` while sending by hand to see what a good one looks like.")
        return 1
    print(f"  confirmed: {seen}")
    if room is not None and seen.room != room:
        print(f"  BUT it went to room {seen.room}, not the Blood Pact room {room}.")
        print("  The tab point is wrong - recalibrate with `calibrate chat`.")
        return 1
    if room is None:
        print(f'  set  "blood_pact_room": {seen.room}  in config.json')
    return 0


def cmd_link(args: list[str]) -> int:
    """Make or set the topic the two machines share."""
    cfg = Config.load() if Config.exists() else Config.blank()
    settings = cfg.data.setdefault("notify", {})
    settings["topic"] = args[0] if args else notify.new_topic()
    settings.setdefault("base", notify.DEFAULT_BASE)
    cfg.save()
    topic, base = settings["topic"], settings["base"].rstrip("/")
    print(f"  topic: {topic}")
    print(f"  relay: {base}")
    print()
    print("  Watch it:")
    print(f"    browser    {base}/app     (subscribe to the topic above)")
    print(f"    raw feed   {base}/{topic}/raw")
    print("    terminal   python -m hsduper watch")
    print()
    print("  Give the receiver the SAME topic:  python -m hsduper link <topic>")
    print()
    print("  What travels over it is one short token and nothing else - no account,")
    print("  no character, nothing about the game. Anyone who knows the topic can read")
    print("  and publish to it, which is why it is a long random string. Change it with")
    print("  `link` again at any time; both sides have to match.")
    return 0


def cmd_ping(args: list[str]) -> int:
    """Publish, then wait for it to come back.

    A publish that returns 200 proves only that something accepted it. Reading
    the message back is what proves the topic, the polling and the relay all
    agree - and it is one command instead of two machines.
    """
    cfg = Config.load()
    base = " ".join(args) or cfg.data.get("ready_token", "hsd-ready")
    token = f"{base}#{int(time.time())}"
    link = notify.from_config(cfg)

    link.poll()  # start from now, so an older ping cannot answer for this one
    link.announce(token)
    print(f"  published {token!r}, waiting for it to come back...")
    # A short diagnostic should replay from ntfy's cache. Opening a live stream
    # only after publishing leaves a small race where another subscriber sees
    # the message but this command begins listening too late.
    got = link.poll_for(lambda t: t == token, timeout=20.0)
    if got is None:
        print("  it never came back. The relay may be unreachable, or the topic wrong.")
        return 1
    print(f"  round trip ok: {got!r}")
    print("  the link works. Give the receiver the same topic and run `await` there.")
    return 0


def cmd_watch(args: list[str]) -> int:
    """Print everything on the topic as it arrives.

    Uses the same held-open connection the receiver does, so watching also
    exercises the path the receiver depends on.
    """
    cfg = Config.load()
    seconds = float(args[0]) if args else 300.0
    settings = cfg.data.get("notify") or {}
    print(f"  watching {settings.get('topic')} for {seconds:.0f}s - ctrl-c to stop")
    seen = 0

    def show(text):
        nonlocal seen
        seen += 1
        print(f"  [{time.strftime('%H:%M:%S')}] {text}")
        return False  # never matches, so it keeps listening

    try:
        notify.from_config(cfg).wait_for(show, timeout=seconds)
    except KeyboardInterrupt:
        pass
    print(f"  {seen} message(s)")
    return 0


def cmd_await(args: list[str]) -> int:
    cfg = Config.load()
    seconds = float(args[0]) if args else 60.0
    token = cfg.data.get("ready_token", "hsd-ready")
    print(f"  waiting up to {seconds:.0f}s for {token!r}")
    got = notify.from_config(cfg).wait_for(lambda t: token in t, timeout=seconds)
    if got is None:
        print("  nothing arrived. Check both sides have the same topic (`link`).")
        return 1
    print(f"  got {got!r}")
    return 0


def cmd_relay(args: list[str]) -> int:
    """Host the signal on this machine instead of a public service."""
    port = int(args[0]) if args else relay.DEFAULT_PORT
    relay.serve(port)
    return 0


def cmd_pact(args: list[str]) -> int:
    flags = {a for a in args if a.startswith("--")}
    rest = [a for a in args if not a.startswith("--")]
    dry = "--dry-run" in flags
    no_use = "--no-use" in flags or dry

    cfg = Config.load()
    role = rest[0] if rest else ""
    cycles = int(rest[1]) if len(rest) > 1 else 1
    if role not in ("sender", "receiver"):
        print("  usage: pact sender|receiver [cycles] [--dry-run] [--no-use]")
        print("    --dry-run  narrate every step, click nothing, publish nothing")
        print("    --no-use   receiver: skip using the items, so nothing is consumed")
        return 1

    inventory, stash = cfg.grid("inventory"), cfg.grid("stash")
    if not _require_game(cfg):
        return 1
    control.clear()
    print(f"  {role}, {cycles} cycle(s). F12 aborts.")

    stats_session = None
    if role == "receiver" and not no_use:
        try:
            stats_session = stats.OpeningSession.start(cycles)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  cannot start opening statistics: {exc}")
            return 1

    def finish_stats(status: str, reason: str | None = None) -> None:
        if stats_session is None:
            return
        try:
            stats_session.finish(status, reason)
            print(f"  stats: {stats_session.summary_line()}")
        except OSError as exc:
            print(f"  warning: could not finish opening statistics: {exc}")

    listener = keyboard.Listener(
        on_press=lambda k: control.request_abort() if k == keyboard.Key.f12 else None)
    listener.start()
    try:
        link = notify.from_config(cfg)
        token = cfg.data.get("ready_token", "hsd-ready")
        seen = cfg.data.get("seen_token", "hsd-seen")
        confirm_wait = cfg.timing("confirm_timeout_ms") / 1000

        def wait_for_signal(match, timeout):
            value = link.wait_for(match, timeout=timeout, cancelled=control.aborted)
            control.check()
            return value

        def nothing_happened(what):
            def act(*_):
                print(f"    [dry run] would {what}")
                return Report(Result.DONE, 0, 0, 0)
            return act

        keep_open = (lambda: True) if dry else (lambda: panels.ensure_stash_open(cfg))

        def move(grid):
            return nothing_happened(f"drain {grid.name}") if dry else (
                lambda: transfer(grid, cfg))

        if role == "sender":
            roles.run_sender(
                cfg, cycles,
                ensure_stash=keep_open,
                have_items=(lambda: True) if dry else
                (lambda: wait_until_occupied(
                    inventory, cfg, timeout=cfg.timing("inventory_wait_ms") / 1000) > 0),
                deposit=move(inventory),
                announce=(lambda t: print(f"    [dry run] would publish {t!r}"))
                if dry else link.announce,
                wait_seen=(lambda: True) if dry else
                (lambda: wait_for_signal(lambda t: seen in t, confirm_wait) is not None),
                withdraw=move(stash),
            )
        else:
            roles.run_receiver(
                cfg, cycles,
                wait_ready=lambda: wait_for_signal(lambda t: token in t, 600.0),
                ensure_stash=keep_open,
                see_items=(lambda: True) if dry else
                (lambda: wait_until_occupied(stash, cfg, timeout=confirm_wait) > 0),
                confirm=(lambda t: print(f"    [dry run] would publish {t!r}"))
                if dry else link.announce,
                withdraw=move(stash),
                close_stash=(lambda: True) if dry else lambda: panels.close_stash(cfg),
                recover_cursor=None if dry else
                lambda: return_cursor_item(stash, inventory, cfg),
                open_inventory=(lambda: True) if dry else lambda: panels.open_inventory(cfg),
                use_all=nothing_happened("use every item in the inventory")
                if no_use else lambda: panels.use_all(cfg, inventory),
                record_opening=None if stats_session is None else stats_session.record_cycle,
            )
        finish_stats("completed")
    except (roles.Stopped, PanelClosed, NotFocused, BlankCapture) as exc:
        finish_stats("stopped", str(exc))
        print(f"  stopped: {exc}")
        return 1
    except control.Aborted as exc:
        finish_stats("aborted", str(exc))
        print(f"  aborted: {exc}")
        return 1
    finally:
        control.request_abort()
        listener.stop()
    print("  done.")
    return 0


def cmd_stats() -> int:
    try:
        stats.print_report()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read opening statistics: {exc}")
        return 1
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = argv[0], argv[1:]
    try:
        if command == "calibrate":
            calibrate.run(rest)
            return 0
        if command == "scan":
            return cmd_scan(rest)
        if command == "probe":
            return cmd_probe(rest)
        if command == "click":
            return cmd_click(rest)
        if command == "hover":
            return cmd_hover()
        if command == "doctor":
            return cmd_doctor()
        if command in ("deposit", "withdraw"):
            return cmd_once(command)
        if command == "listen":
            return cmd_listen(rest)
        if command == "say":
            return cmd_say(rest)
        if command == "link":
            return cmd_link(rest)
        if command == "ping":
            return cmd_ping(rest)
        if command == "await":
            return cmd_await(rest)
        if command == "watch":
            return cmd_watch(rest)
        if command == "stats":
            return cmd_stats()
        if command == "relay":
            return cmd_relay(rest)
        if command == "pact":
            return cmd_pact(rest)
        if command == "run":
            return cmd_run()
    except (FileNotFoundError, KeyError, calibrate.Cancelled) as exc:
        print(f"error: {exc}")
        return 1
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
