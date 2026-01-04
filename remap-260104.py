from __future__ import annotations

import os
import time
from dataclasses import dataclass

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
    SC_F: (26, False),   # mirrors keyhac "26"
    SC_J: (22, False),   # mirrors keyhac "22"
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


def main() -> None:
    ctx = create_context()
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
            if not lib.interception_is_keyboard(device):
                passthrough(ctx, device, stroke)
                continue
            if device != 1:
                passthrough(ctx, device, stroke)
                continue

            process_keystroke(ctx, device, stroke, mod)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        lib.interception_destroy_context(ctx)


if __name__ == "__main__":
    main()