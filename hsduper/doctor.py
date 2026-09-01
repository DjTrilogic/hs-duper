"""Why input is not landing.

When the cursor moves and clicks do nothing, the cause is almost never the
click's shape - it is that Windows is refusing to deliver it. The usual reason
is integrity level: a process cannot inject input into a window belonging to a
more privileged process, and it fails silently, which looks exactly like a game
ignoring you.
"""

import ctypes
import time
from ctypes import wintypes

from . import winput

user32 = winput.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

TOKEN_QUERY = 0x0008
TokenElevation = 20
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5


def we_are_elevated() -> bool:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return False
    try:
        elevated = wintypes.DWORD()
        size = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(
            token, TokenElevation, ctypes.byref(elevated),
            ctypes.sizeof(elevated), ctypes.byref(size),
        )
        return bool(ok and elevated.value)
    finally:
        kernel32.CloseHandle(token)


def window_process(hwnd) -> tuple[int, str | None, int]:
    """(pid, executable path or None, last error).

    A `None` path with access denied is the finding: the process is running at a
    higher integrity level than this one, so every click aimed at its window is
    being dropped before it arrives.
    """
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return pid.value, None, ctypes.get_last_error()
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return pid.value, buf.value, 0
        return pid.value, None, ctypes.get_last_error()
    finally:
        kernel32.CloseHandle(handle)


def report() -> None:
    print(f"  this process elevated: {we_are_elevated()}")

    hwnd = user32.GetForegroundWindow()
    title = winput.foreground_window_title()
    print(f"  foreground window:     {title!r}")

    lowered = title.lower()
    if any(k in lowered for k in ("powershell", "cmd.exe", "terminal", "hsduper", "code")):
        print("  ^ that is not the game. Click into Hero Siege first - nothing else matters")
        print("    until the game is the window receiving input.")

    pid, path, err = window_process(hwnd)
    print(f"  its pid:               {pid}")
    if path:
        print(f"  its executable:        {path}")
    elif err == ERROR_ACCESS_DENIED:
        print("  its executable:        ACCESS DENIED")
        print()
        print("  That is the answer. The foreground window belongs to a process this one is")
        print("  not allowed to touch, which means Hero Siege is running elevated (as")
        print("  administrator) and hs-duper is not. Windows blocks injected input from a")
        print("  lower-privileged process to a higher-privileged window, silently - the")
        print("  cursor still moves, because moving the cursor is desktop-wide, but every")
        print("  button event is dropped on arrival.")
        print()
        print("  Fix: run this terminal as administrator, or start Hero Siege without it.")
    else:
        print(f"  its executable:        unavailable (error {err})")

    # If Windows has the buttons swapped, an injected RIGHTDOWN is delivered to
    # the game as a left click and vice versa - which turns every result upside
    # down without anything looking wrong.
    if user32.GetSystemMetrics(23):
        print("  mouse buttons:         SWAPPED system-wide (SM_SWAPBUTTON is set)")
        print("  ^ injected right and left are delivered the other way round.")
    else:
        print("  mouse buttons:         normal (not swapped)")

    x, y = winput.get_cursor_pos()
    print(f"  cursor at:             ({x}, {y})")
    under = user32.WindowFromPoint(wintypes.POINT(x, y))
    if under and under != hwnd:
        upid, upath, _ = window_process(under)
        name = upath.rsplit("\\", 1)[-1] if upath else f"pid {upid}"
        print(f"  window under cursor:   a different window ({name})")
        print("  ^ clicks land on whatever is under the cursor, not on the focused window.")


DEFAULT_GAME_EXE = "Hero_Siege.exe"


def foreground_exe() -> str | None:
    _, path, _ = window_process(user32.GetForegroundWindow())
    return path


def game_is_foreground(expected: str = DEFAULT_GAME_EXE) -> bool:
    path = foreground_exe()
    return bool(path) and path.rsplit(chr(92), 1)[-1].lower() == expected.lower()


def wait_for_game(expected: str = DEFAULT_GAME_EXE, timeout: float = 30.0) -> bool:
    """Block until the game is the window receiving input.

    This replaces a countdown, which only ever hoped you had switched across in
    time. A test that runs against the wrong window does not fail visibly - it
    produces a plausible number about something else entirely, which is worse
    than not running at all.
    """
    if game_is_foreground(expected):
        time.sleep(0.4)
        return True
    print(f"  waiting for {expected} - click into the game (30s)", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if game_is_foreground(expected):
            print("  got it.", flush=True)
            time.sleep(0.6)
            return True
        time.sleep(0.25)
    return False
