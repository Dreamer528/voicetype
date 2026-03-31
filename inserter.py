import ctypes
import subprocess
import time
import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString, NSWorkspace

V_KEYCODE = 9  # macOS virtual keycode for 'V'


def check_accessibility():
    """Check if app has Accessibility permission. Opens settings if not."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = lib.AXIsProcessTrusted()

        if not trusted:
            # Open the right settings page automatically
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ])
        return trusted
    except Exception:
        return True


def insert_text(text):
    """Insert text into the frontmost application via clipboard + Cmd+V."""
    # Copy to clipboard via native macOS API (handles Unicode correctly)
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)

    time.sleep(0.1)

    # Get frontmost app and send Cmd+V directly to it
    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = front_app.processIdentifier()
    app_name = front_app.localizedName()
    print(f"[VoiceType] Вставляю в: {app_name} (PID: {pid})")

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)

    # Key down: Cmd+V
    key_down = Quartz.CGEventCreateKeyboardEvent(source, V_KEYCODE, True)
    Quartz.CGEventSetFlags(key_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPostToPid(pid, key_down)

    time.sleep(0.05)

    # Key up
    key_up = Quartz.CGEventCreateKeyboardEvent(source, V_KEYCODE, False)
    Quartz.CGEventSetFlags(key_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPostToPid(pid, key_up)
