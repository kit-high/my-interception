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

from PIL import Image, ImageDraw
import pystray
from interception import ffi, lib

# Set 1 scancodes
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


def _dbg(msg: str) -> None:
    if DEBUG_KEYS:
        print(msg, flush=True)


def make_keystroke(code: int, base_state: int, *, extended: bool = False):
    state = base_state | (lib.INTERCEPTION_KEY_E0 if extended else 0)
    return ffi.new("InterceptionKeyStroke*", {"code": code, "state": state, "information": 0})[0]


@dataclass
class ModifierState:
    f13_down: bool = False
    ctrl_down: bool = False
    lshift_down: bool = False
    lalt_down: bool = False

    def update(self, code: int, is_up: bool) -> None:
        if code == SC_LCTRL:
            self.ctrl_down = not is_up
        elif code == SC_LSHIFT:
            self.lshift_down = not is_up
        elif code == SC_LALT:
            self.lalt_down = not is_up


def create_context():
    ctx = lib.interception_create_context()
    if ctx == ffi.NULL:
        raise RuntimeError("Failed to create interception context (driver installed?)")
    lib.interception_set_filter(ctx, lib.interception_is_keyboard, lib.INTERCEPTION_FILTER_KEY_ALL)
    return ctx


def passthrough(ctx, device, stroke) -> None:
    lib.interception_send(ctx, device, ffi.cast("InterceptionStroke*", stroke), 1)


def log_debug(device, stroke, original_code, state, is_up, mod: ModifierState) -> None:
    if DEBUG_KEYS and original_code in (SC_CAPSLOCK, SC_LCTRL, SC_F13, SC_A):
        info = int(stroke[0].information)
        _dbg(
            f"t={time.monotonic():.6f} dev={device} recv code=0x{original_code:02X} "
            f"state=0x{state:02X} info=0x{info:08X} up={is_up} "
            f"f13_down={mod.f13_down} ctrl_down={mod.ctrl_down}"
        )


def send_word_motion(
    ctx,
    device,
    *,
    ctrl: bool,
    shift: bool,
    direction_left: bool,
    lctrl_down: bool,
    lshift_down: bool,
) -> None:
    strokes = []
    if ctrl and not lctrl_down:
        strokes.append(make_keystroke(SC_LCTRL, lib.INTERCEPTION_KEY_DOWN))
    if shift and not lshift_down:
        strokes.append(make_keystroke(SC_LSHIFT, lib.INTERCEPTION_KEY_DOWN))

    arrow_code = SC_LEFT if direction_left else SC_RIGHT
    strokes.append(make_keystroke(arrow_code, lib.INTERCEPTION_KEY_DOWN, extended=True))
    strokes.append(make_keystroke(arrow_code, lib.INTERCEPTION_KEY_UP, extended=True))

    if shift and not lshift_down:
        strokes.append(make_keystroke(SC_LSHIFT, lib.INTERCEPTION_KEY_UP))
    if ctrl and not lctrl_down:
        strokes.append(make_keystroke(SC_LCTRL, lib.INTERCEPTION_KEY_UP))

    lib.interception_send(ctx, device, ffi.new("InterceptionKeyStroke[]", strokes), len(strokes))


def send_alt_shift_enter_combo(ctx, device, *, lctrl_down: bool, lshift_down: bool) -> None:
    strokes = []

    def add_combo(ctrl: bool, shift: bool, direction_left: bool):
        if ctrl and not lctrl_down:
            strokes.append(make_keystroke(SC_LCTRL, lib.INTERCEPTION_KEY_DOWN))
        if shift and not lshift_down:
            strokes.append(make_keystroke(SC_LSHIFT, lib.INTERCEPTION_KEY_DOWN))
        arrow = SC_LEFT if direction_left else SC_RIGHT
        strokes.append(make_keystroke(arrow, lib.INTERCEPTION_KEY_DOWN, extended=True))
        strokes.append(make_keystroke(arrow, lib.INTERCEPTION_KEY_UP, extended=True))
        if shift and not lshift_down:
            strokes.append(make_keystroke(SC_LSHIFT, lib.INTERCEPTION_KEY_UP))
        if ctrl and not lctrl_down:
            strokes.append(make_keystroke(SC_LCTRL, lib.INTERCEPTION_KEY_UP))

    add_combo(ctrl=True, shift=False, direction_left=True)
    add_combo(ctrl=True, shift=True, direction_left=False)

    lib.interception_send(ctx, device, ffi.new("InterceptionKeyStroke[]", strokes), len(strokes))


# WinAPI for SendInput
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008

VK_IME_ON = 0x16
VK_IME_OFF = 0x1A
VK_CONVERT = 0x1C
VK_NONCONVERT = 0x1D

ULONG_PTR = wintypes.WPARAM

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR)
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
        ("_input", _INPUT)
    ]

def send_vk(vk_code: int, is_up: bool) -> None:
    inputs = INPUT()
    inputs.type = INPUT_KEYBOARD
    inputs.ki.wVk = vk_code
    inputs.ki.dwFlags = KEYEVENTF_KEYUP if is_up else 0
    
    # PowerToys logic: Set extended key flag if needed
    # See Helpers::IsExtendedKey in PowerToys
    # VK_CONVERT/VK_NONCONVERT are NOT extended keys usually, but let's be precise if we add others.
    # For now, we just follow the basic structure.
    
    # PowerToys logic: Set wScan using MapVirtualKey
    # MapVirtualKey returns 0 if the key code does not correspond to a physical key.
    # For VK_CONVERT/VK_NONCONVERT on US keyboard, it might return 0.
    # If it returns 0, we might want to force the scancode if we know it.
    scancode = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
    if scancode == 0:
        if vk_code == VK_CONVERT:
            scancode = 0x79
        elif vk_code == VK_NONCONVERT:
            scancode = 0x7B

    inputs.ki.wScan = scancode
    
    # PowerToys logic: Set dwExtraInfo to KEYBOARDMANAGER_SINGLEKEY_FLAG (0x11)
    # This marks the event as injected by Keyboard Manager (or our script mimicking it)
    # PowerToys has two relevant concepts:
    # - CommonSharedConstants::KEYBOARDMANAGER_INJECTED_FLAG (0x1)
    # - KeyboardManagerConstants::KEYBOARDMANAGER_SINGLEKEY_FLAG (0x11)
    # Their SINGLEKEY_FLAG already includes the injected bit.
    inputs.ki.dwExtraInfo = 0x11

    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    if sent != 1 and DEBUG_KEYS:
        err = ctypes.windll.kernel32.GetLastError()
        _dbg(f"SendInput failed: sent={sent} vk=0x{vk_code:02X} up={is_up} cbSize={ctypes.sizeof(INPUT)} err={err}")


def process_keystroke(ctx, device, stroke, mod: ModifierState) -> None:
    code = stroke[0].code
    state = stroke[0].state
    is_up = bool(state & lib.INTERCEPTION_KEY_UP)
    base_state = lib.INTERCEPTION_KEY_UP if is_up else lib.INTERCEPTION_KEY_DOWN
    original_code = code

    log_debug(device, stroke, original_code, state, is_up, mod)

    # CapsLock -> LCTRL (emit remapped stroke)
    if original_code == SC_CAPSLOCK:
        stroke[0].code = SC_LCTRL
        code = SC_LCTRL
        if DEBUG_KEYS:
            _dbg(f"t={time.monotonic():.6f} dev={device} map CapsLock->LCTRL state=0x{state:02X}")

    # Physical LCTRL acts as F13 modifier (consume)
    if original_code == SC_LCTRL:
        mod.f13_down = not is_up
        if DEBUG_KEYS:
            _dbg(f"t={time.monotonic():.6f} dev={device} set f13_down={mod.f13_down} (from physical LCTRL)")
        return

    # Physical F13 acts as modifier (consume)
    if original_code == SC_F13:
        mod.f13_down = not is_up
        if DEBUG_KEYS:
            _dbg(f"t={time.monotonic():.6f} dev={device} set f13_down={mod.f13_down} (from physical F13)")
        return

    # Update modifier states
    mod.update(code, is_up)

    # F13 combos
    if mod.f13_down and code in F13_MAP:
        # Direct IME switching
        if code == SC_F:
            # Match PowerToys target key: "IME Off" (VK_IME_OFF)
            send_vk(VK_IME_OFF, is_up)
            if DEBUG_KEYS:
                _dbg(f"t={time.monotonic():.6f} dev={device} f13combo src=0x{code:02X} -> VK_IME_OFF up={is_up}")
            return
        if code == SC_J:
            # Match PowerToys target key: "IME On" (VK_IME_ON)
            send_vk(VK_IME_ON, is_up)
            if DEBUG_KEYS:
                _dbg(f"t={time.monotonic():.6f} dev={device} f13combo src=0x{code:02X} -> VK_IME_ON up={is_up}")
            return

        target_code, use_e0 = F13_MAP[code]
        remapped = make_keystroke(target_code, base_state, extended=use_e0)
        lib.interception_send(ctx, device, ffi.new("InterceptionKeyStroke[]", [remapped]), 1)
        if DEBUG_KEYS:
            _dbg(f"t={time.monotonic():.6f} dev={device} f13combo src=0x{code:02X} -> dst=0x{target_code:02X} up={is_up}")
        return

    # Alt-based word-jump shortcuts (key down only)
    if mod.lalt_down and not is_up:
        if mod.lshift_down and code == SC_SEMICOLON:
            send_word_motion(ctx, device, ctrl=True, shift=True, direction_left=True,
                             lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
            return
        if mod.lshift_down and code == SC_QUOTE:
            send_word_motion(ctx, device, ctrl=True, shift=True, direction_left=False,
                             lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
            return
        if mod.lshift_down and code == SC_ENTER:
            send_alt_shift_enter_combo(ctx, device, lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
            return
        if code == SC_SEMICOLON:
            send_word_motion(ctx, device, ctrl=True, shift=False, direction_left=True,
                             lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
            return
        if code == SC_QUOTE:
            send_word_motion(ctx, device, ctrl=True, shift=False, direction_left=False,
                             lctrl_down=mod.ctrl_down, lshift_down=mod.lshift_down)
            return

    passthrough(ctx, device, stroke)


def run_loop(
    stop_event: Optional[threading.Event] = None,
    reload_event: Optional[threading.Event] = None,
    ctx_holder: Optional[dict] = None,
    is_enabled: Optional[Callable[[], bool]] = None,
) -> None:
    ctx = create_context()
    if ctx_holder is not None:
        ctx_holder["ctx"] = ctx
    print("F13-mode remap active on keyboard device 1 (Ctrl+C to stop)")
    if DEBUG_KEYS:
        print("DEBUG_KEYS=1: logging CapsLock/Ctrl/F13/A events", flush=True)

    mod = ModifierState()
    stroke = ffi.new("InterceptionKeyStroke*")

    try:
        while True:
            device = lib.interception_wait(ctx)
            read = lib.interception_receive(ctx, device, ffi.cast("InterceptionStroke*", stroke), 1)
            if read <= 0:
                continue
            if stop_event and stop_event.is_set():
                break
            if reload_event and reload_event.is_set():
                reload_event.clear()
                break
            if not lib.interception_is_keyboard(device):
                passthrough(ctx, device, stroke)
                continue
            if device != 1:
                passthrough(ctx, device, stroke)
                continue
            if is_enabled is not None and not is_enabled():
                passthrough(ctx, device, stroke)
                continue

            process_keystroke(ctx, device, stroke, mod)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        if ctx_holder is not None:
            ctx_holder.pop("ctx", None)
        try:
            lib.interception_destroy_context(ctx)
        except Exception:
            pass


class RemapService:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._reload_event = threading.Event()
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._thread: Optional[threading.Thread] = None
        self._ctx_holder: dict = {}
        self._enabled_lock = threading.Lock()
        self._enabled = True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._reload_event.clear()
        self._thread = threading.Thread(target=self._run, name="remap-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._set_state("running")
            run_loop(
                self._stop_event,
                self._reload_event,
                self._ctx_holder,
                self.is_enabled,
            )
            if self._stop_event.is_set():
                break
            if self._reload_event.is_set():
                self._reload_event.clear()
                self._set_state("reloading")
                continue
            break
        self._set_state("stopped")

    def stop(self) -> None:
        self._stop_event.set()
        self._destroy_ctx()
        if self._thread:
            self._thread.join(timeout=3)

    def request_reload(self) -> None:
        self._reload_event.set()
        self._destroy_ctx()

    def status(self) -> str:
        with self._state_lock:
            return self._state

    def status_label(self) -> str:
        enabled = self.is_enabled()
        state = self.status()
        if state == "running" and not enabled:
            return "running (disabled)"
        return state

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

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _destroy_ctx(self) -> None:
        ctx = self._ctx_holder.get("ctx")
        if ctx is None:
            return
        try:
            lib.interception_destroy_context(ctx)
        except Exception:
            pass


def _make_icon(color: str) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, size - 8, size - 8), fill=color, outline="#111111")
    return img


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


class TrayApp:
    def __init__(self, service: RemapService) -> None:
        self.service = service
        self._icons = {
            "running": _make_icon("#4CAF50"),
            "disabled": _make_icon("#9E9E9E"),
            "reloading": _make_icon("#FFC107"),
            "stopped": _make_icon("#F44336"),
            "starting": _make_icon("#2196F3"),
        }
        self.icon = pystray.Icon(
            "my-interception",
            self._icons["starting"],
            "Remap: starting",
            menu=pystray.Menu(
                pystray.MenuItem(lambda _item: f"Status: {self.service.status_label()}", None, enabled=False),
                pystray.MenuItem(lambda _item: ("Disable mapping" if self.service.is_enabled() else "Enable mapping"), self._toggle_enabled),
                pystray.MenuItem("Reload mapping", self._reload),
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    def run(self) -> None:
        self.service.start()
        threading.Thread(target=self._refresh_ui, name="tray-refresh", daemon=True).start()
        self.icon.run()

    def _reload(self, _icon: pystray.Icon, _item) -> None:
        self.service.request_reload()

    def _quit(self, _icon: pystray.Icon, _item) -> None:
        self.service.stop()
        self.icon.visible = False
        self.icon.stop()

    def _toggle_enabled(self, _icon: pystray.Icon, _item) -> None:
        self.service.toggle_enabled()

    def _refresh_ui(self) -> None:
        last_state_key = None
        while True:
            state = self.service.status()
            enabled = self.service.is_enabled()
            state_key = "disabled" if state == "running" and not enabled else state
            if state_key != last_state_key:
                self.icon.title = f"Remap: {self.service.status_label()}"
                self.icon.icon = self._icons.get(state_key, self._icons["starting"])
                last_state_key = state_key
            if state == "stopped":
                break
            time.sleep(REFRESH_INTERVAL_SEC)


def main() -> None:
    if not acquire_single_instance_lock():
        return

    parser = argparse.ArgumentParser(description="F13-mode remapper with tray support")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray")
    args = parser.parse_args()

    if args.no_tray:
        run_loop()
        return

    service = RemapService()
    TrayApp(service).run()


if __name__ == "__main__":
    main()