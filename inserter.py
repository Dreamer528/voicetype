import ctypes
import logging
import subprocess
import time
import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString, NSWorkspace

log = logging.getLogger("VoiceType")

V_KEYCODE = 9  # macOS virtual keycode for 'V'

# App bundle ID → formatting context hint for LLM
_APP_CONTEXTS = {
    # Messengers → casual, short
    "ru.keepcoder.Telegram": "мессенджер (короткие неформальные сообщения, без лишней формальности)",
    "com.apple.MobileSMS": "iMessage (короткие неформальные сообщения)",
    "net.whatsapp.WhatsApp": "WhatsApp (короткие неформальные сообщения)",
    "com.tinyspeck.slackmacgap": "Slack (рабочий мессенджер, полуформально)",
    "com.hnc.Discord": "Discord (неформальное общение)",
    # Email → formal
    "com.apple.mail": "почтовый клиент (формальный стиль, полные предложения)",
    "com.google.Chrome": "браузер (может быть email или документ)",
    # Code → technical
    "com.microsoft.VSCode": "редактор кода (технический текст, сохраняй термины и camelCase)",
    "com.apple.dt.Xcode": "Xcode (технический текст, сохраняй термины)",
    "com.jetbrains.intellij": "IntelliJ (технический текст)",
    "com.googlecode.iterm2": "терминал (технический текст, команды)",
    # Notes/docs → clean structured
    "com.apple.Notes": "заметки (чистый структурированный текст)",
    "md.obsidian": "Obsidian (Markdown заметки)",
    "com.notion.Notion": "Notion (структурированный текст)",
}


def get_app_context():
    """Return formatting context hint based on the frontmost application."""
    try:
        front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = front_app.bundleIdentifier()
        return _APP_CONTEXTS.get(bundle_id, "")
    except Exception:
        return ""


def check_accessibility():
    """Check if app has Accessibility permission. Opens settings if not."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = lib.AXIsProcessTrusted()

        if not trusted:
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ])
        return trusted
    except Exception:
        return True


def insert_text(text):
    """Insert text into the frontmost application via clipboard + Cmd+V.

    Saves and restores the original clipboard contents.
    """
    pb = NSPasteboard.generalPasteboard()

    # Save original clipboard content
    original_content = pb.stringForType_(NSPasteboardTypeString)

    # Copy transcribed text to clipboard
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)

    time.sleep(0.1)

    # Get frontmost app and send Cmd+V directly to it
    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front_app is None:
        log.warning("No frontmost application found, cannot paste")
        return
    pid = front_app.processIdentifier()
    app_name = front_app.localizedName()
    log.info("Вставляю в: %s (PID: %s)", app_name, pid)

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

    # Wait for paste to complete, then restore original clipboard
    time.sleep(0.15)
    if original_content is not None:
        pb.clearContents()
        pb.setString_forType_(original_content, NSPasteboardTypeString)
