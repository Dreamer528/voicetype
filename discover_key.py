#!/usr/bin/env python3
"""Run this on your Mac to discover the key code for your microphone key.

Press the mic key (with and without Fn) and this script will show
what events macOS sends. Use the printed key_id to configure VoiceType
if the default doesn't work.

Usage: python3 discover_key.py
Press Ctrl+C to stop.
"""

import Quartz
from AppKit import NSEvent

NX_SYSDEFINED = 14
MEDIA_KEY_SUBTYPE = 8


def callback(proxy, event_type, event, refcon):
    if event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
        key_code = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        action = "DOWN" if event_type == Quartz.kCGEventKeyDown else "UP"
        print(f"[KEY {action}] keyCode={key_code}")

    elif event_type == NX_SYSDEFINED:
        ns_event = NSEvent.eventWithCGEvent_(event)
        if ns_event and ns_event.subtype() == MEDIA_KEY_SUBTYPE:
            data1 = ns_event.data1()
            key_id = (data1 >> 16) & 0xFFFF
            flags = (data1 >> 8) & 0xFF
            is_down = (flags & 0x01) == 0
            action = "DOWN" if is_down else "UP"
            print(f"[MEDIA KEY {action}] key_id={key_id}  flags=0x{flags:02X}")

    return event


def main():
    print("=== VoiceType Key Discovery ===")
    print("Нажимайте клавишу микрофона (с Fn и без Fn)")
    print("Запишите значение key_id — его нужно указать в конфиге если дефолтный не работает")
    print("Ctrl+C для выхода\n")

    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        | Quartz.CGEventMaskBit(NX_SYSDEFINED)
    )

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )

    if tap is None:
        print("Ошибка: не удалось создать event tap.")
        print("Добавьте Terminal в:")
        print("  System Settings → Privacy & Security → Accessibility")
        return

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(
        Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
    )
    Quartz.CGEventTapEnable(tap, True)

    try:
        Quartz.CFRunLoopRun()
    except KeyboardInterrupt:
        print("\nГотово.")


if __name__ == "__main__":
    main()
