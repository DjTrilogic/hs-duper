"""Win32 input via SendInput.

Games routinely ignore the legacy `mouse_event`/`keybd_event` path, and modifier
keys sent as virtual-key codes often do not register in a game that reads raw
scancodes. So: SendInput throughout, absolute coordinates for the mouse, and a
hardware scancode for CTRL.
"""

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes

if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_ulonglong
else:
    ULONG_PTR = ctypes.c_ulong

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

SC_LCONTROL = 0x1D
VK_LCONTROL = 0xA2
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_F = 0x46
VK_I = 0x49
SC_RETURN = 0x1C
SC_ESCAPE = 0x01
SC_F = 0x21
SC_I = 0x17

#: How CTRL is put on the wire. Real hardware delivers both a virtual-key code
#: and a scancode in the same event; "both" reproduces that, and is the default
#: because a game reading either one will see it. The other two exist so a game
#: that only believes one of them can be found by trying.
CTRL_MODES = ("both", "scancode", "vk")
DEFAULT_CTRL_MODE = "scancode"

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def set_dpi_aware() -> None:
    """Must run before anything reads or writes a screen coordinate.

    Without it Windows reports scaled coordinates on a display with a scaling
    factor, and every click lands somewhere other than where it was aimed.
    """
    try:
        # PER_MONITOR_AWARE_V2; the only one correct on a mixed-DPI desktop
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _send(*inputs: INPUT) -> None:
    n = len(inputs)
    array = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, array, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError(f"SendInput sent {sent}/{n}: {ctypes.get_last_error()}")


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx, dy, 0, flags, 0, 0))


def _key(scan: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, 0))


def _ctrl(up: bool, mode: str = "both") -> INPUT:
    up_flag = KEYEVENTF_KEYUP if up else 0
    if mode == "scancode":
        return INPUT(type=INPUT_KEYBOARD,
                     ki=KEYBDINPUT(0, SC_LCONTROL, KEYEVENTF_SCANCODE | up_flag, 0, 0))
    if mode == "vk":
        return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_LCONTROL, 0, up_flag, 0, 0))
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_LCONTROL, SC_LCONTROL, up_flag, 0, 0))


def _ctrl_inputs(up: bool, mode: str = "both") -> tuple[INPUT, ...]:
    """Build CTRL events for the requested delivery path.

    Without ``KEYEVENTF_SCANCODE``, Windows uses ``wVk`` and ignores ``wScan``.
    The old ``both`` event populated both fields but therefore behaved like
    VK-only input. Two events make the default honest: virtual-key and raw
    scancode consumers both receive the transition.
    """
    if mode == "both":
        return (_ctrl(up, "vk"), _ctrl(up, "scancode"))
    return (_ctrl(up, mode),)


def _send_ctrl(up: bool, mode: str = "both") -> None:
    _send(*_ctrl_inputs(up, mode))


def ctrl_is_down() -> bool:
    """Whether Windows currently reports left CTRL as held."""
    return bool(user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)


def ensure_ctrl_down(
    settle_ms: int = 45, mode: str = "both", attempts: int = 3
) -> None:
    """Reassert CTRL and refuse to click unless Windows confirms its state."""
    attempts = max(int(attempts), 1)
    for _ in range(attempts):
        _send_ctrl(up=False, mode=mode)
        time.sleep(settle_ms / 1000)
        if ctrl_is_down():
            return
    raise RuntimeError(
        f"CTRL did not register after {attempts} attempt(s); refusing to send a plain click"
    )


def _click_with_ctrl_held(
    down: int, up: int, hold_ms: int, settle_ms: int, mode: str
) -> None:
    """Reassert CTRL and press a mouse button in one SendInput batch.

    Windows confirming the key state does not guarantee that a game consuming
    raw input has already associated the separate keyboard event with a later
    mouse event. Repeating CTRL-down and LMB-down in the same input array keeps
    their ordering adjacent for both the Windows and raw-input paths. CTRL is
    intentionally not released here; the surrounding pass owns its lifetime.
    """
    ensure_ctrl_down(settle_ms=settle_ms, mode=mode)
    _send(*_ctrl_inputs(up=False, mode=mode), _mouse(down))
    time.sleep(hold_ms / 1000)
    _send(_mouse(up))


def left_click_with_ctrl_held(
    hold_ms: int = 70, settle_ms: int = 45, mode: str = DEFAULT_CTRL_MODE
) -> None:
    _click_with_ctrl_held(
        MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, hold_ms, settle_ms, mode
    )


def right_click_with_ctrl_held(
    hold_ms: int = 70, settle_ms: int = 45, mode: str = DEFAULT_CTRL_MODE
) -> None:
    _click_with_ctrl_held(
        MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, hold_ms, settle_ms, mode
    )


def _unicode_key(char: str, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, ord(char), flags, 0, 0))


def get_cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def move_to(x: int, y: int) -> None:
    """Absolute move over the whole virtual desktop.

    SendInput is what the game sees; the SetCursorPos afterwards only corrects
    the pixel or two lost to the 0..65535 normalisation, so that a later
    GetCursorPos check compares against the position actually asked for.
    """
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = round((x - vx) * 65535 / max(vw - 1, 1))
    ny = round((y - vy) * 65535 / max(vh - 1, 1))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send(_mouse(flags, nx, ny))
    if get_cursor_pos() != (x, y):
        user32.SetCursorPos(int(x), int(y))


def _modified_click(
    down: int, up: int, hold_ms: int = 70, settle_ms: int = 45, mode: str = "both"
) -> None:
    """CTRL + a mouse button, with CTRL released whatever happens.

    `hold_ms` is how long the button stays down, and it has to clear a frame.
    A game samples input once a frame - about 17 ms at 60 fps - so a button
    pressed and released inside one frame can be missed entirely: the game
    polls, sees nothing, polls again, still sees nothing. That is what a run
    where the cursor moves correctly and nothing at all happens looks like.

    The release is in a `finally` because an abort raised mid-click would
    otherwise leave CTRL held down for the whole desktop.
    """
    try:
        ensure_ctrl_down(settle_ms=settle_ms, mode=mode)
        _send(_mouse(down))
        time.sleep(hold_ms / 1000)
        _send(_mouse(up))
        time.sleep(settle_ms / 1000)
    finally:
        _send_ctrl(up=True, mode=mode)


def ctrl_right_click(hold_ms: int = 70, settle_ms: int = 45, mode: str = "both") -> None:
    _modified_click(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, hold_ms, settle_ms, mode)


def ctrl_left_click(hold_ms: int = 70, settle_ms: int = 45, mode: str = "both") -> None:
    _modified_click(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, hold_ms, settle_ms, mode)


@contextmanager
def hold_ctrl(settle_ms: int = 45, mode: str = DEFAULT_CTRL_MODE):
    """Keep CTRL down for a whole group of mouse clicks.

    Bulk transfers are more reliable when they look like the physical gesture:
    press CTRL once, click every item, then release it. Toggling the modifier
    around every individual click gives the game one opportunity per slot to
    miss CTRL-down and interpret that click as a plain pick-up instead.

    The release remains unconditional so F12, a focus failure or any other
    exception cannot leave CTRL held on the desktop.
    """
    try:
        ensure_ctrl_down(settle_ms=settle_ms, mode=mode)
        yield
        time.sleep(settle_ms / 1000)
    finally:
        _send_ctrl(up=True, mode=mode)


def _plain_click(down: int, up: int, hold_ms: int = 70) -> None:
    _send(_mouse(down))
    time.sleep(hold_ms / 1000)
    _send(_mouse(up))


def left_click(hold_ms: int = 70) -> None:
    _plain_click(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, hold_ms)


def right_click(hold_ms: int = 70) -> None:
    _plain_click(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, hold_ms)


def tap(scan: int, vk: int = 0, hold_ms: int = 60) -> None:
    """Press and release a key, carrying both codes where one is known.

    Real hardware delivers a virtual-key code and a scancode in the same event.
    The hold clears a frame, for the same reason the mouse buttons do.
    """
    down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, 0 if vk else KEYEVENTF_SCANCODE, 0, 0))
    up_flags = KEYEVENTF_KEYUP | (0 if vk else KEYEVENTF_SCANCODE)
    up = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, up_flags, 0, 0))
    _send(down)
    time.sleep(hold_ms / 1000)
    _send(up)


def press_enter() -> None:
    tap(SC_RETURN, VK_RETURN)


def press_escape() -> None:
    tap(SC_ESCAPE, VK_ESCAPE)


def press_interact() -> None:
    """Interact with the object in front of the character using F."""
    tap(SC_F, VK_F)


def press_inventory() -> None:
    """Toggle the inventory with the game's default I binding."""
    tap(SC_I, VK_I)


def type_text(text: str, per_char_ms: int = 12) -> None:
    """Unicode input, not scancodes.

    A game text field takes WM_CHAR-style unicode; scancodes are what the
    gameplay keybinds read, and typing a message with them produces whatever
    those keys are bound to instead of the letters.
    """
    for char in text:
        _send(_unicode_key(char))
        _send(_unicode_key(char, up=True))
        time.sleep(per_char_ms / 1000)


def foreground_window_title() -> str:
    """Which window input is going to.

    A run where the cursor moves correctly and nothing happens is very often
    this: the console kept focus, and the game ignored clicks aimed at it.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "(none)"
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or "(untitled)"


def _abs_move_input(x: int, y: int) -> INPUT:
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = round((x - vx) * 65535 / max(vw - 1, 1))
    ny = round((y - vy) * 65535 / max(vh - 1, 1))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    return _mouse(flags, nx, ny)


def batched_ctrl_right_click(x: int, y: int, mode: str = "both") -> None:
    """Move and click as ONE SendInput call.

    Some games drain the input queue in batches and only look at the cursor
    position attached to the event they are handling. Delivered as separate
    calls, the move can be processed, coalesced or discarded independently of
    the button - so the button arrives describing a position the game has not
    caught up to. In one array the whole gesture is unambiguous.
    """
    try:
        _send(
            _abs_move_input(x, y),
            *_ctrl_inputs(up=False, mode=mode),
            _mouse(MOUSEEVENTF_RIGHTDOWN),
            _mouse(MOUSEEVENTF_RIGHTUP),
        )
    finally:
        _send_ctrl(up=True, mode=mode)


def ctrl_right_click_at(
    x: int, y: int, mode: str = "both", hold_ms: int = 70,
    settle_ms: int = 45, ctrl_first: bool = False,
) -> None:
    """CTRL + right click at a point, with the ordering under our control.

    `ctrl_first` presses CTRL before the cursor arrives, rather than after. It
    matters for a game that decides what a slot does at the moment the cursor
    enters it: arriving with the modifier already down is a different gesture
    from arriving and then pressing it, even though the two look identical by
    the time the button falls.
    """
    try:
        if ctrl_first:
            _send_ctrl(up=False, mode=mode)
            time.sleep(settle_ms / 1000)
            _send(_abs_move_input(x, y))
            time.sleep(settle_ms / 1000)
        else:
            _send(_abs_move_input(x, y))
            time.sleep(settle_ms / 1000)
            _send_ctrl(up=False, mode=mode)
            time.sleep(settle_ms / 1000)
        _send(_mouse(MOUSEEVENTF_RIGHTDOWN))
        time.sleep(hold_ms / 1000)
        _send(_mouse(MOUSEEVENTF_RIGHTUP))
        time.sleep(settle_ms / 1000)
    finally:
        _send_ctrl(up=True, mode=mode)
