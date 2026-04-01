from __future__ import annotations

import argparse
import atexit
import msvcrt
import os
import tempfile
import threading
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional
from PIL import Image, ImageDraw, ImageFont
import pystray

# ---------------------------------------------------------------------------
# Scancodes (Set 1)
# ---------------------------------------------------------------------------
SC_F13 = 0x64
SC_LCTRL = 0x1D
SC_LSHIFT = 0x2A
SC_LALT = 0x38
SC_CAPSLOCK = 0x3A
SC_ENTER = 0x1C
SC_OPEN_BRACKET = 0x1A
SC_SLASH = 0x35
SC_SEMICOLON = 0x27
SC_QUOTE = 0x28
SC_1 = 0x02
SC_2 = 0x03
SC_3 = 0x04
SC_4 = 0x05
SC_5 = 0x06
SC_6 = 0x07
SC_7 = 0x08
SC_8 = 0x09
SC_9 = 0x0A
SC_0 = 0x0B
SC_MINUS = 0x0C
SC_EQUAL = 0x0D
SC_F = 0x21
SC_J = 0x24
SC_K = 0x25
SC_COMMA = 0x33
SC_L = 0x26
SC_PERIOD = 0x34
SC_A = 0x1E

# Targets
SC_F1 = 0x3B
SC_F2 = 0x3C
SC_F3 = 0x3D
SC_F4 = 0x3E
SC_F5 = 0x3F
SC_F6 = 0x40
SC_F7 = 0x41
SC_F8 = 0x42
SC_F9 = 0x43
SC_F10 = 0x44
SC_F11 = 0x57
SC_F12 = 0x58
SC_UP = 0x48
SC_DOWN = 0x50
SC_LEFT = 0x4B
SC_RIGHT = 0x4D
SC_HOME = 0x47
SC_END = 0x4F
SC_PGUP = 0x49
SC_PGDN = 0x51

DEBUG_KEYS = os.environ.get("DEBUG_KEYS", "1") != "0"
REFRESH_INTERVAL_SEC = 0.5
LOCK_PATH = os.path.join(tempfile.gettempdir(), "my-interception.lock")

MODE_BODY = "body"
MODE_EXTERNAL = "external"

F13_MAP = {
    SC_OPEN_BRACKET: (SC_UP, True),
    SC_SLASH: (SC_DOWN, True),
    SC_SEMICOLON: (SC_LEFT, True),
    SC_QUOTE: (SC_RIGHT, True),
    SC_1: (SC_F1, False),
    SC_2: (SC_F2, False),
    SC_3: (SC_F3, False),
    SC_4: (SC_F4, False),
    SC_5: (SC_F5, False),
    SC_6: (SC_F6, False),
    SC_7: (SC_F7, False),
    SC_8: (SC_F8, False),
    SC_9: (SC_F9, False),
    SC_0: (SC_F10, False),
    SC_MINUS: (SC_F11, False),
    SC_EQUAL: (SC_F12, False),
    SC_F: (0x7B, False),
    SC_J: (0x79, False),
    SC_K: (SC_HOME, True),
    SC_COMMA: (SC_END, True),
    SC_L: (SC_PGUP, True),
    SC_PERIOD: (SC_PGDN, True),
}

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008

LLKHF_INJECTED = 0x10
LLKHF_EXTENDED = 0x01

VK_IME_ON = 0x16
VK_IME_OFF = 0x1A
VK_CONVERT = 0x1C
VK_NONCONVERT = 0x1D

# Extra info tag so we can recognise our own injected events
_INJECTED_TAG = 0x4D495243  # "MIRC" in hex-ish

ULONG_PTR = wintypes.WPARAM

# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
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

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
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

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
kernel32.LoadLibraryW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

# Scancode → is_extended lookup for keys we emit
_EXTENDED_SCANCODES = {SC_UP, SC_DOWN, SC_LEFT, SC_RIGHT, SC_HOME, SC_END, SC_PGUP, SC_PGDN}


def _dbg(msg: str) -> None:
    if DEBUG_KEYS:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# SendInput helpers
# ---------------------------------------------------------------------------

def _make_input_scancode(scancode: int, *, up: bool, extended: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scancode
    flags = KEYEVENTF_SCANCODE
    if up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.ki.dwFlags = flags
    inp.ki.dwExtraInfo = _INJECTED_TAG
    return inp


def _make_input_vk(vk: int, *, up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    if inp.ki.wScan == 0:
        if vk == VK_CONVERT:
            inp.ki.wScan = 0x79
        elif vk == VK_NONCONVERT:
            inp.ki.wScan = 0x7B
    inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp.ki.dwExtraInfo = _INJECTED_TAG
    return inp


def _send_inputs(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs) and DEBUG_KEYS:
        err = kernel32.GetLastError()
        _dbg(f"SendInput: wanted {len(inputs)}, sent {sent}, err={err}")


def send_scancode(scancode: int, *, up: bool, extended: bool = False) -> None:
    _send_inputs(_make_input_scancode(scancode, up=up, extended=extended))


def send_vk(vk_code: int, is_up: bool) -> None:
    _send_inputs(_make_input_vk(vk_code, up=is_up))


# ---------------------------------------------------------------------------
# Composite key sequences via SendInput
# ---------------------------------------------------------------------------

def send_word_motion(*, ctrl: bool, shift: bool, direction_left: bool,
                     lctrl_down: bool, lshift_down: bool) -> None:
    inputs: list[INPUT] = []
    if ctrl and not lctrl_down:
        inputs.append(_make_input_scancode(SC_LCTRL, up=False, extended=False))
    if shift and not lshift_down:
        inputs.append(_make_input_scancode(SC_LSHIFT, up=False, extended=False))

    arrow = SC_LEFT if direction_left else SC_RIGHT
    inputs.append(_make_input_scancode(arrow, up=False, extended=True))
    inputs.append(_make_input_scancode(arrow, up=True, extended=True))

    if shift and not lshift_down:
        inputs.append(_make_input_scancode(SC_LSHIFT, up=True, extended=False))
    if ctrl and not lctrl_down:
        inputs.append(_make_input_scancode(SC_LCTRL, up=True, extended=False))

    _send_inputs(*inputs)


def send_alt_shift_enter_combo(*, lctrl_down: bool, lshift_down: bool) -> None:
    inputs: list[INPUT] = []

    def add_combo(ctrl: bool, shift: bool, direction_left: bool) -> None:
        if ctrl and not lctrl_down:
            inputs.append(_make_input_scancode(SC_LCTRL, up=False, extended=False))
        if shift and not lshift_down:
            inputs.append(_make_input_scancode(SC_LSHIFT, up=False, extended=False))
        arrow = SC_LEFT if direction_left else SC_RIGHT
        inputs.append(_make_input_scancode(arrow, up=False, extended=True))
        inputs.append(_make_input_scancode(arrow, up=True, extended=True))
        if shift and not lshift_down:
            inputs.append(_make_input_scancode(SC_LSHIFT, up=True, extended=False))
        if ctrl and not lctrl_down:
            inputs.append(_make_input_scancode(SC_LCTRL, up=True, extended=False))

    add_combo(ctrl=True, shift=False, direction_left=True)
    add_combo(ctrl=True, shift=True, direction_left=False)

    _send_inputs(*inputs)


# ---------------------------------------------------------------------------
# Modifier state tracker
# ---------------------------------------------------------------------------

@dataclass
class ModifierState:
    f13_down: bool = False
    ctrl_down: bool = False
    lshift_down: bool = False
    lalt_down: bool = False

    def update(self, scancode: int, is_up: bool) -> None:
        if scancode == SC_LCTRL:
            self.ctrl_down = not is_up
        elif scancode == SC_LSHIFT:
            self.lshift_down = not is_up
        elif scancode == SC_LALT:
            self.lalt_down = not is_up


# ---------------------------------------------------------------------------
# Low-level keyboard hook processor
# ---------------------------------------------------------------------------

class KeyboardHook:
    """Installs a WH_KEYBOARD_LL hook and remaps keys in the callback."""

    def __init__(self,
                 is_enabled: Optional[Callable[[], bool]] = None,
                 get_mode: Optional[Callable[[], str]] = None) -> None:
        self._hook: Optional[int] = None
        self._thread_id: Optional[int] = None
        self._mod = ModifierState()
        self._is_enabled = is_enabled
        self._get_mode = get_mode
        # Must prevent GC of the callback
        self._proc = HOOKPROC(self._ll_keyboard_proc)

    def install(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        # For WH_KEYBOARD_LL, try loading user32 explicitly to get a valid handle
        hmod = kernel32.LoadLibraryW("user32.dll")
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, hmod, 0
        )
        if not self._hook:
            # Fallback: pass None (works on some Python/Windows combos)
            self._hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._proc, None, 0
            )
        if not self._hook:
            raise RuntimeError(f"SetWindowsHookExW failed: err={kernel32.GetLastError()}")
        _dbg("Keyboard hook installed")

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            _dbg("Keyboard hook uninstalled")

    def pump_messages(self, stop_event: Optional[threading.Event] = None) -> None:
        """Run the message loop. Blocks until WM_QUIT or stop_event is set."""
        msg = wintypes.MSG()
        while True:
            if stop_event and stop_event.is_set():
                break
            # PeekMessage with timeout emulation: use GetMessage with a posted WM_NULL heartbeat
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:  # WM_QUIT or error
                break

    def post_quit(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT

    def reset_modifier_state(self) -> None:
        self._mod = ModifierState()

    # ------------------------------------------------------------------

    def _ll_keyboard_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == HC_ACTION:
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents

            # Skip events we injected ourselves
            if kbd.dwExtraInfo == _INJECTED_TAG:
                return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

            # Bypass when disabled
            if self._is_enabled is not None and not self._is_enabled():
                return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

            scancode = kbd.scanCode & 0xFF
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            is_extended = bool(kbd.flags & LLKHF_EXTENDED)

            result = self._process(scancode, is_up, is_extended)
            if result is not None:
                return result

        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _process(self, scancode: int, is_up: bool, is_extended: bool) -> Optional[int]:
        """Process a single keystroke. Return 1 to suppress, None to pass through."""
        mod = self._mod
        original = scancode
        mode = self._get_mode() if self._get_mode is not None else MODE_BODY

        if DEBUG_KEYS and original in (SC_CAPSLOCK, SC_LCTRL, SC_F13, SC_A):
            _dbg(
                f"t={time.monotonic():.6f} recv sc=0x{original:02X} "
                f"up={is_up} ext={is_extended} "
                f"f13_down={mod.f13_down} ctrl_down={mod.ctrl_down} mode={mode}"
            )

        if mode == MODE_EXTERNAL:
            if original == SC_F13:
                send_scancode(SC_LCTRL, up=is_up, extended=False)
                if DEBUG_KEYS:
                    _dbg(f"t={time.monotonic():.6f} external map F13->LCTRL up={is_up}")
                return 1
            if original == SC_LCTRL:
                send_scancode(SC_F13, up=is_up, extended=False)
                if DEBUG_KEYS:
                    _dbg(f"t={time.monotonic():.6f} external map LCTRL->F13 up={is_up}")
                return 1
            return None

        # CapsLock -> LCTRL
        if original == SC_CAPSLOCK:
            send_scancode(SC_LCTRL, up=is_up, extended=False)
            if DEBUG_KEYS:
                _dbg(f"t={time.monotonic():.6f} map CapsLock->LCTRL up={is_up}")
            return 1  # suppress original

        # Physical F13 acts as modifier (consume)
        if original == SC_F13:
            mod.f13_down = not is_up
            if DEBUG_KEYS:
                _dbg(f"t={time.monotonic():.6f} set f13_down={mod.f13_down} (from physical F13)")
            return 1

        # Update modifier states (after remapping for CapsLock -> LCTRL already emitted above)
        mod.update(scancode, is_up)

        # F13 combos
        if mod.f13_down and scancode in F13_MAP:
            # IME switching via VK
            if scancode == SC_F:
                send_vk(VK_IME_OFF, is_up)
                if DEBUG_KEYS:
                    _dbg(f"t={time.monotonic():.6f} f13combo 0x{scancode:02X} -> VK_IME_OFF up={is_up}")
                return 1
            if scancode == SC_J:
                send_vk(VK_IME_ON, is_up)
                if DEBUG_KEYS:
                    _dbg(f"t={time.monotonic():.6f} f13combo 0x{scancode:02X} -> VK_IME_ON up={is_up}")
                return 1

            target_code, use_e0 = F13_MAP[scancode]
            send_scancode(target_code, up=is_up, extended=use_e0)
            if DEBUG_KEYS:
                _dbg(f"t={time.monotonic():.6f} f13combo 0x{scancode:02X} -> 0x{target_code:02X} up={is_up}")
            return 1

        # Alt-based word-jump shortcuts (key down only)
        if mod.lalt_down and not is_up:
            if mod.lshift_down and scancode == SC_SEMICOLON:
                send_word_motion(ctrl=True, shift=True, direction_left=True,
                                 lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
                return 1
            if mod.lshift_down and scancode == SC_QUOTE:
                send_word_motion(ctrl=True, shift=True, direction_left=False,
                                 lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
                return 1
            if mod.lshift_down and scancode == SC_ENTER:
                send_alt_shift_enter_combo(lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
                return 1
            if scancode == SC_SEMICOLON:
                send_word_motion(ctrl=True, shift=False, direction_left=True,
                                 lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
                return 1
            if scancode == SC_QUOTE:
                send_word_motion(ctrl=True, shift=False, direction_left=False,
                                 lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
                return 1

        return None  # pass through


# ---------------------------------------------------------------------------
# Service: manages hook lifecycle on a dedicated thread
# ---------------------------------------------------------------------------

class RemapService:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._thread: Optional[threading.Thread] = None
        self._enabled_lock = threading.Lock()
        self._enabled = True
        self._mode_lock = threading.Lock()
        self._mode = MODE_BODY
        self._hook: Optional[KeyboardHook] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="remap-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._set_state("running")
        hook = KeyboardHook(is_enabled=self.is_enabled, get_mode=self.mode)
        self._hook = hook
        try:
            hook.install()
            print(f"F13-mode remap active ({self.mode_label()} mode, WH_KEYBOARD_LL hook, Ctrl+C to stop)")
            if DEBUG_KEYS:
                print("DEBUG_KEYS=1: logging CapsLock/Ctrl/F13/A events", flush=True)

            # Heartbeat thread to break out of GetMessage when stop is requested
            def _heartbeat() -> None:
                while not self._stop_event.is_set():
                    self._stop_event.wait(0.5)
                hook.post_quit()

            hb = threading.Thread(target=_heartbeat, name="hook-heartbeat", daemon=True)
            hb.start()

            hook.pump_messages(self._stop_event)
        except KeyboardInterrupt:
            print("Stopping...")
        finally:
            hook.uninstall()
            self._hook = None
            self._set_state("stopped")

    def stop(self) -> None:
        self._stop_event.set()
        if self._hook:
            self._hook.post_quit()
        if self._thread:
            self._thread.join(timeout=3)

    def request_reload(self) -> None:
        # For WH_KEYBOARD_LL there is no stale context issue, so reload is a no-op.
        # The hook survives USB reconnects automatically.
        _dbg("Reload requested — no-op for WH_KEYBOARD_LL (hook survives reconnects)")

    def status(self) -> str:
        with self._state_lock:
            return self._state

    def status_label(self) -> str:
        enabled = self.is_enabled()
        state = self.status()
        if state == "running" and not enabled:
            return f"running (disabled) / {self.mode_label()}"
        return f"{state} / {self.mode_label()}"

    def is_enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    def enable(self) -> None:
        with self._enabled_lock:
            self._enabled = True

    def disable(self) -> None:
        with self._enabled_lock:
            self._enabled = False

    def toggle_enabled(self) -> None:
        with self._enabled_lock:
            self._enabled = not self._enabled

    def mode(self) -> str:
        with self._mode_lock:
            return self._mode

    def mode_label(self) -> str:
        return self.mode()

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_BODY, MODE_EXTERNAL):
            raise ValueError(f"Unknown mode: {mode}")
        with self._mode_lock:
            changed = self._mode != mode
            self._mode = mode
        if changed and self._hook:
            self._hook.reset_modifier_state()

    def toggle_mode(self) -> None:
        next_mode = MODE_EXTERNAL if self.mode() == MODE_BODY else MODE_BODY
        self.set_mode(next_mode)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state


# ---------------------------------------------------------------------------
# Tray UI
# ---------------------------------------------------------------------------

def _load_icon_font(size: int) -> ImageFont.ImageFont:
    font_candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"),
    ]
    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_icon(color: str, label: str = "") -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, size - 8, size - 8), fill=color, outline="#111111")
    if label:
        font = _load_icon_font(28)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        text_x = (size - text_width) / 2 - text_box[0]
        text_y = (size - text_height) / 2 - text_box[1] - 1
        draw.text((text_x, text_y), label, fill="#FFFFFF", font=font)
    return img


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------

_LOCK_FILE_HANDLE: Optional[object] = None


def acquire_single_instance_lock() -> bool:
    """Try to obtain a non-blocking file lock; return False if another instance holds it."""
    global _LOCK_FILE_HANDLE
    if _LOCK_FILE_HANDLE is not None:
        return True

    try:
        fh = open(LOCK_PATH, "a+b")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        _LOCK_FILE_HANDLE = fh
        atexit.register(release_single_instance_lock)
        return True
    except OSError:
        return False


def release_single_instance_lock() -> None:
    global _LOCK_FILE_HANDLE
    fh = _LOCK_FILE_HANDLE
    if fh is None:
        return
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass
    _LOCK_FILE_HANDLE = None


# ---------------------------------------------------------------------------

class TrayApp:
    def __init__(self, service: RemapService) -> None:
        self.service = service
        self._icons = {
            "body": _make_icon("#1565C0", "B"),
            "external": _make_icon("#EF6C00", "E"),
            "body-disabled": _make_icon("#78909C", "B"),
            "external-disabled": _make_icon("#8D6E63", "E"),
            "reloading": _make_icon("#FFC107", "R"),
            "stopped": _make_icon("#C62828", "X"),
            "starting": _make_icon("#546E7A", "..."),
        }
        self.icon = pystray.Icon(
            "my-interception",
            self._icons["starting"],
            "Remap: starting",
            menu=pystray.Menu(
                pystray.MenuItem(lambda _item: self._toggle_mode_label(), self._toggle_mode, default=True),
                pystray.MenuItem(lambda _item: f"Status: {self.service.status_label()}", None, enabled=False),
                pystray.MenuItem(lambda _item: f"Mode: {self.service.mode_label()}", None, enabled=False),
                pystray.MenuItem("💻Body mode", self._set_body_mode,
                                 checked=lambda _item: self.service.mode() == MODE_BODY,
                                 radio=True),
                pystray.MenuItem("⌨External mode", self._set_external_mode,
                                 checked=lambda _item: self.service.mode() == MODE_EXTERNAL,
                                 radio=True),
                pystray.MenuItem(lambda _item: ("Disable mapping" if self.service.is_enabled() else "Enable mapping"), self._toggle_enabled),
                pystray.MenuItem("Reload mapping", self._reload),
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    def run(self) -> None:
        self.service.start()
        threading.Thread(target=self._refresh_ui, name="tray-refresh", daemon=True).start()
        self.icon.run()

    def _reload(self, _icon, _item) -> None:
        self.service.request_reload()

    def _quit(self, _icon, _item) -> None:
        self.service.stop()
        self.icon.visible = False
        self.icon.stop()

    def _toggle_enabled(self, _icon, _item) -> None:
        self.service.toggle_enabled()
        self._update_ui_once()

    def _toggle_mode(self, _icon, _item) -> None:
        self.service.toggle_mode()
        self._update_ui_once()

    def _toggle_mode_label(self) -> str:
        next_mode = MODE_EXTERNAL if self.service.mode() == MODE_BODY else MODE_BODY
        return f"Switch to {next_mode} mode"

    def _set_body_mode(self, _icon, _item) -> None:
        self.service.set_mode(MODE_BODY)
        self._update_ui_once()

    def _set_external_mode(self, _icon, _item) -> None:
        self.service.set_mode(MODE_EXTERNAL)
        self._update_ui_once()

    def _icon_key(self) -> str:
        state = self.service.status()
        if state != "running":
            return state
        mode = self.service.mode()
        if self.service.is_enabled():
            return mode
        return f"{mode}-disabled"

    def _update_ui_once(self) -> tuple[str, str, str]:
        state = self.service.status()
        state_key = self._icon_key()
        status_label = self.service.status_label()
        self.icon.title = f"Remap: {status_label}"
        self.icon.icon = self._icons.get(state_key, self._icons["starting"])
        self.icon.update_menu()
        return state, state_key, status_label

    def _refresh_ui(self) -> None:
        while True:
            state, _, _ = self._update_ui_once()
            if state == "stopped":
                break
            time.sleep(REFRESH_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    if not acquire_single_instance_lock():
        return

    parser = argparse.ArgumentParser(description="F13-mode remapper with tray support (WinAPI)")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray")
    args = parser.parse_args()

    if args.no_tray:
        service = RemapService()
        service._run()
        return

    service = RemapService()
    TrayApp(service).run()


if __name__ == "__main__":
    main()
