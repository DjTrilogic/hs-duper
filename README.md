# hs-duper

Automates the Blood Pact stash cycle in Hero Siege: bulk item transfer between
the inventory and the stash, and the signal that coordinates two players.

It reads the screen and moves the mouse. Nothing is injected into the game, no
game memory is read, no game file is touched.

---

## The workflow

One cycle, with both machines running hs-duper:

| | sender | receiver |
| --- | --- | --- |
| 1 | deposits the inventory into the stash | waits with its stash closed and inventory open |
| 2 | publishes `DEPOSITED` | receives it, presses F to open the stash |
| 3 | waits | verifies that item cells are visible, publishes `VISIBLE` |
| 4 | receives `VISIBLE`, withdraws | withdraws the same items |
| 5 | waits | closes the stash, presses I if needed, uses the items |
| 6 | receives `DONE`; the next cycle may start | publishes `DONE` with the inventory left open |

Each message contains a random sender-session id and the cycle number. This
makes the three signals distinct and prevents a cached message from an earlier
run from satisfying the wrong wait. `VISIBLE` is deliberately sent before both
sides withdraw, so neither starts removing items before the receiver has opened
the refreshed stash and seen them. `DONE` prevents the sender from starting the
next deposit while the receiver is still using the previous items.

**The signals do not travel through the game.** The sender must never close the
stash, and the in-game chat cannot be reached while it is open — so the cycle
signals go over a pub/sub topic both machines share.

### Run it

```powershell
# receiver, first
.\.venv\Scripts\python.exe -m hsduper pact receiver 5

# sender
.\.venv\Scripts\python.exe -m hsduper pact sender 5
```

`5` is the number of cycles. **F12 aborts.** Start with `1`.

| flag | |
| --- | --- |
| `--dry-run` | narrate every step, click nothing, publish nothing |
| `--no-use` | receiver: skip using the items, so nothing is consumed |

Start the receiver first. It closes its initially open stash and leaves the
inventory open before it begins waiting. The sender now waits for both receiver
acknowledgements, so a complete cycle requires both processes.

---

## Setup

Once per machine.

### 1. Install

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install mss numpy pynput
```

Hero Siege must run in **borderless windowed**. In exclusive fullscreen the
screen capture comes back black; the tool detects that and refuses to click.

### 2. Calibrate

Everything positional is measured, never assumed — so this is the step that
decides whether anything else works.

```powershell
.\.venv\Scripts\python.exe -m hsduper calibrate
```

You confirm the row and column counts at the console, then hover things in game
and press **F8**. The defaults are 6x15 for the inventory and 18x17 for the
stash; press Enter to accept them or type a different value. ESC cancels.

When the brightness of an empty or occupied slot is sampled, calibration moves
the cursor briefly to the top-left of the screen. This clears the game's bright
hover highlight before the pixels are captured, so it is expected.

| what | why |
| --- | --- |
| `inventory` | the bag grid. It does **not** move when the stash opens — what appears above it is the equipment panel |
| `stash` | the Blood Pact stash grid |
| park point | somewhere the cursor can rest without raising a tooltip over a slot. Every capture is taken with it parked there |
| anchors | the INVENTORY and BLOOD PACT STASH titles. These are what prove a panel is open before any click — see Safety |

Redo one part at a time by naming it: `calibrate stash`, `calibrate park anchors`.

**Don't know the row and column counts?** Counting icons off a screenshot is how
wrong ones get entered, and the pitch is `span / (count - 1)`, so a count that is
out by a few puts every cell in the wrong place. `probe` reads the real cell
pitch off the pixels and tells you the counts to type:

```powershell
.\.venv\Scripts\python.exe -m hsduper probe
```

**Then check it before letting it click:**

```powershell
.\.venv\Scripts\python.exe -m hsduper scan
```

The printed map must match the screen cell for cell. Check it on a **half-full**
grid — an all-`#` map proves nothing, because a grid with the wrong pitch laid
over a solid block of items also reads as full. `scan` clicks nothing, ever.

### 3. Link the two machines

```powershell
.\.venv\Scripts\python.exe -m hsduper link     # prints a topic
.\.venv\Scripts\python.exe -m hsduper ping     # publish, and read it back
```

Give the receiver the same topic with `hsduper link <topic>`. `ping` proves the
whole round trip — a publish returning 200 only proves something accepted it.

The relay defaults to `https://ntfy.sh`, which is free and needs no account.
Only a short token crosses it: no account, no character, nothing about the game.
**The topic name is the only secret**, which is why it is a long random string —
don't paste it anywhere public. ntfy is open source, so `notify.base` can point
at your own instance instead.

---

## Safety

- **`CTRL + LMB` moves an item with the stash open, and USES it with the stash
  closed.** A pass against a closed panel would consume your inventory, and a
  used item cannot be recovered the way a dropped one can. So the panel anchors
  are checked before every pass, and an anchor that was never calibrated counts
  as missing — "I was never told what to look for" must not mean "go ahead".
- **The run stops if Hero Siege loses focus**, rather than clicking into
  whatever window came forward.
- **F12** is checked before every click.
- **Moving the mouse yourself stops the run.** The tool drives the same cursor
  you do.
- CTRL is released in a `finally`, so an abort mid-click cannot leave it stuck.
- A blank capture is treated as a failure, never as an empty grid.

---

## Commands

| | |
| --- | --- |
| `calibrate [part ...]` | measure things (`inventory`, `stash`, `park`, `anchors`, `chat`) |
| `scan [grid ...]` | print what it sees, click nothing |
| `probe [grid]` | measure a grid's cell pitch off the screen |
| `deposit` / `withdraw` | one bulk transfer |
| `pact sender\|receiver [n]` | run the cycle |
| `link [topic]` / `ping` / `watch` / `await` | the signal topic: set it, test it, observe it |
| `click [what]` / `hover` / `doctor` | input diagnostics |
| `listen` / `say` | read and write Blood Pact chat (diagnostics only) |

---

## Tuning

`config.json`. Timings are milliseconds.

| | |
| --- | --- |
| `timing.click_delay_ms` | after each click. If pass 2 regularly does real work, the server is dropping transfers — raise it |
| `timing.button_hold_ms` | how long the button stays down; must clear a frame |
| `timing.max_passes` | give-up limit per transfer |
| `ready_token` | prefix shared by the three cycle signals |
| `notify.topic` / `notify.base` | the shared topic and relay |
| `ctrl_mode` | how CTRL is delivered: `both`, `vk`, `scancode` |

---

## If something goes wrong

| symptom | |
| --- | --- |
| **`scan` map doesn't match the screen** | wrong row/column counts — `probe` reads the real pitch off the pixels |
| **capture comes back blank** | the game is in exclusive fullscreen; switch to borderless windowed |
| **cursor moves, nothing happens** | `hover` tests whether the game sees the cursor at all; `click sweep` tries each button and reports which lands; `doctor` reports focus and privilege |
| **run refuses to start** | an anchor is missing or a panel is closed. It is refusing on purpose — see Safety |
| **`stalled` after moving nothing** | the destination is full, or the tab won't take those items |
| **`ping` never comes back** | the two machines have different topics, or the relay is unreachable |

---

## How it works

**Transfers** are multi-pass: scan the grid, click every occupied cell, rescan,
repeat until the source is empty or a pass moves nothing. Two things fall out of
that shape for free — multi-cell items (clicking any one of an item's cells moves
the whole thing, and the rest read empty next pass) and a full destination (it
shows up as a pass that moved nothing). Occupancy is the 95th percentile of
luminance over the middle of each cell: an empty slot is near-uniform dark, and
any item icon puts bright pixels in it whatever its colour.

**The signals** are three short tokens (`DEPOSITED`, `VISIBLE`, and `DONE`) on a
shared pub/sub topic. Both sides hold a streamed connection open while waiting
rather than polling — polling once a second would be thousands of requests
across a wait, and the public relay rate-limits anonymous callers. A dropped
connection reconnects from the last message seen.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

The tests cover grid geometry and occupancy against synthetic frames, the transfer
loop against a scripted grid, the anchor rules, both role sequences, the chat
reader, the notifier's reconnect and duplicate handling, and the command table.

## Status

Phase 1 (bulk transfer) and Phase 2 (the coordinated cycle) both work. The
sender path is the one exercised in anger; the receiver's panel steps — closing
the stash, reopening it with F, and opening the inventory with I — are tested
but have seen less real use.
