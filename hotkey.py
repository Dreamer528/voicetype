"""Global hotkey listener using Quartz CGEventTap.

Requires Accessibility permissions on macOS.
"""

import logging
import threading
import Quartz
from AppKit import NSEvent

log = logging.getLogger("VoiceType")

NX_SYSDEFINED = 14
MEDIA_KEY_SUBTYPE = 8

# Modifier flag masks
MOD_CMD = Quartz.kCGEventFlagMaskCommand
MOD_SHIFT = Quartz.kCGEventFlagMaskShift
MOD_OPT = Quartz.kCGEventFlagMaskAlternate
MOD_CTRL = Quartz.kCGEventFlagMaskControl

# macOS virtual key code → display name
KEYCODE_NAMES = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X",
    8: "C", 9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R",
    16: "Y", 17: "T", 31: "O", 32: "U", 34: "I", 35: "P", 37: "L",
    38: "J", 40: "K", 45: "N", 46: "M",
    18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7",
    28: "8", 25: "9", 29: "0",
    49: "Space", 36: "Return", 48: "Tab", 51: "Delete", 53: "Esc",
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6",
    98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12",
    126: "↑", 125: "↓", 123: "←", 124: "→",
}

NX_KEY_NAMES = {
    27: "Mic Key", 29: "Mic Key", 30: "Mic Key", 96: "F5 (media)",
}

_instance = None


def _tap_callback(proxy, event_type, event, refcon):
    # macOS may disable the tap after inactivity — re-enable it
    if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                      Quartz.kCGEventTapDisabledByUserInput):
        log.warning("Event tap disabled (type=%s), re-enabling...", event_type)
        Quartz.CGEventTapEnable(proxy, True)
        return event

    if _instance is None:
        return event
    try:
        consumed = _instance._handle_event(event_type, event)
        if consumed:
            return None  # Swallow the event — don't pass to other apps
    except Exception as e:
        log.error("Hotkey callback error: %s", e)
    return event


def _modifier_names(flags):
    parts = []
    if flags & MOD_CTRL:
        parts.append("Ctrl")
    if flags & MOD_OPT:
        parts.append("Opt")
    if flags & MOD_SHIFT:
        parts.append("Shift")
    if flags & MOD_CMD:
        parts.append("Cmd")
    return parts


def format_hotkey_name(hotkey_cfg):
    parts = []
    mods = hotkey_cfg.get("modifiers", 0)
    parts.extend(_modifier_names(mods))

    source = hotkey_cfg.get("source", "key")
    if source == "modifiers":
        pass  # Only modifiers, no extra key
    elif source == "nx":
        nx_id = hotkey_cfg.get("nx_key_id", 0)
        parts.append(NX_KEY_NAMES.get(nx_id, f"Media({nx_id})"))
    else:
        kc = hotkey_cfg.get("key_code", 0)
        parts.append(KEYCODE_NAMES.get(kc, f"Key({kc})"))

    return "+".join(parts) if parts else "Не задан"


_MODIFIER_KEYCODES = {54, 55, 56, 57, 58, 59, 60, 61, 62, 63}

# Default: Ctrl+Option+D (no conflicts with macOS)
DEFAULT_HOTKEY = {
    "source": "key",
    "key_code": 2,  # D
    "modifiers": MOD_CTRL | MOD_OPT,
}


class HotkeyManager:
    """Configurable push-to-talk hotkey via Quartz CGEventTap."""

    def __init__(self, on_activate, on_deactivate, on_cancel=None, hotkey_cfg=None):
        self.on_activate = on_activate
        self.on_deactivate = on_deactivate
        self.on_cancel = on_cancel
        self._active = False
        self._thread = None
        self._running = False
        self._learn_mode = False
        self._learned_result = None
        self.set_hotkey(hotkey_cfg or DEFAULT_HOTKEY)

    def set_hotkey(self, hotkey_cfg):
        self.hotkey_cfg = hotkey_cfg
        self._source = hotkey_cfg.get("source", "key")
        self._key_code = hotkey_cfg.get("key_code", 2)
        self._nx_key_id = hotkey_cfg.get("nx_key_id", 0)
        self._modifiers = hotkey_cfg.get("modifiers", 0)
        self._mod_mask = MOD_CMD | MOD_SHIFT | MOD_OPT | MOD_CTRL
        name = format_hotkey_name(hotkey_cfg)
        log.info("Hotkey set: %s", name)

    def start_learning(self):
        self._learned_result = None
        self._learn_mode = True
        log.info("Learn mode: press any key...")

    def get_learned_result(self):
        result = self._learned_result
        if result is not None:
            self._learned_result = None
        return result

    def cancel_learning(self):
        self._learn_mode = False
        self._learned_result = None

    def _handle_event(self, event_type, event):
        """Returns True if event should be consumed (swallowed)."""
        # --- Regular key events ---
        if event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            key_code = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if key_code in _MODIFIER_KEYCODES:
                return False

            flags = Quartz.CGEventGetFlags(event) & self._mod_mask

            # Learn mode
            if self._learn_mode and event_type == Quartz.kCGEventKeyDown:
                self._learn_mode = False
                self._learned_result = {
                    "source": "key",
                    "key_code": key_code,
                    "modifiers": flags,
                }
                return True  # consume

            # Esc key cancels recording
            if key_code == 53 and event_type == Quartz.kCGEventKeyDown:
                if self.on_cancel:
                    self._active = False
                    log.info(">>> RECORDING CANCEL (Esc)")
                    self.on_cancel()
                    return True

            # Normal mode — check match
            if self._source == "key" and key_code == self._key_code:
                if flags != self._modifiers:
                    return False
                is_repeat = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventAutorepeat
                )
                if is_repeat:
                    return True  # consume repeats too
                if event_type == Quartz.kCGEventKeyDown and not self._active:
                    self._active = True
                    log.info(">>> RECORDING START")
                    self.on_activate()
                elif event_type == Quartz.kCGEventKeyUp and self._active:
                    self._active = False
                    log.info(">>> RECORDING STOP")
                    self.on_deactivate()
                return True  # consume the hotkey event

        # --- NX system-defined events (media keys) ---
        elif event_type == NX_SYSDEFINED:
            ns_event = NSEvent.eventWithCGEvent_(event)
            if ns_event is None or ns_event.subtype() != MEDIA_KEY_SUBTYPE:
                return
            data1 = ns_event.data1()
            nx_key_id = (data1 >> 16) & 0xFFFF
            nx_flags = (data1 >> 8) & 0xFF
            is_down = (nx_flags & 0x01) == 0
            action = "DOWN" if is_down else "UP"
            name = NX_KEY_NAMES.get(nx_key_id, f"NX({nx_key_id})")
            log.info("Media %s: %s", action, name)

            if self._learn_mode and is_down:
                self._learn_mode = False
                self._learned_result = {
                    "source": "nx",
                    "nx_key_id": nx_key_id,
                    "key_code": nx_key_id,
                    "modifiers": 0,
                }
                return True

            if self._source == "nx" and nx_key_id == self._nx_key_id:
                if is_down and not self._active:
                    self._active = True
                    log.info(">>> RECORDING START")
                    self.on_activate()
                elif not is_down and self._active:
                    self._active = False
                    log.info(">>> RECORDING STOP")
                    self.on_deactivate()
                return True

        return False

    def _run(self):
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(NX_SYSDEFINED)
        )

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # Active tap — can consume events
            mask,
            _tap_callback,
            None,
        )

        if tap is None:
            log.error("Event tap не создан — нет разрешений")
            import subprocess
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ])
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
            ])
            try:
                import rumps
                rumps.notification(
                    "VoiceType",
                    "Нужны разрешения",
                    "Добавьте VoiceType в 'Универсальный доступ' и 'Мониторинг ввода', затем перезапустите",
                )
            except Exception:
                pass
            return

        log.info("Event tap создан, слушаю клавиши...")

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)

        self._running = True
        while self._running:
            result = Quartz.CFRunLoopRunInMode(
                Quartz.kCFRunLoopDefaultMode, 1.0, False
            )
            if result == Quartz.kCFRunLoopRunFinished:
                break
            # Safety check: re-enable tap if macOS disabled it
            if not Quartz.CGEventTapIsEnabled(tap):
                log.warning("Event tap found disabled in run loop, re-enabling...")
                Quartz.CGEventTapEnable(tap, True)

    def start(self):
        global _instance
        _instance = self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        global _instance
        self._running = False
        _instance = None
