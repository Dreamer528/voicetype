"""Animated overlay for recording/processing states with amplitude visualization."""

import math
import objc
from AppKit import (
    NSAnimationContext,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSScreen,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
    NSBackingStoreBuffered,
)
from Foundation import NSMakeRect, NSString, NSUserDefaults
import Quartz


# Window size
ORB_SIZE = 120
WINDOW_W = 200
WINDOW_H = 220

# UserDefaults key for saved position
_POS_KEY = "VoiceTypeOverlayPosition"


class OrbView(NSView):
    """Custom view that draws animated pulsing orb with amplitude and duration."""

    def initWithFrame_(self, frame):
        self = objc.super(OrbView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._phase = 0.0
        self._state = "idle"  # idle, recording, processing
        self._timer = None
        self._amplitude = 0.0      # 0.0-1.0 from microphone RMS
        self._duration_text = ""    # "0:05", "1:23"
        self._label_text = ""       # "Говорите..." or "Processing..."
        self._hint_text = ""        # "Esc — отмена"
        return self

    def isOpaque(self):
        return False

    @objc.python_method
    def set_state(self, state):
        self._state = state
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_amplitude(self, value):
        self._amplitude = max(0.0, min(1.0, value))

    @objc.python_method
    def set_duration(self, text):
        self._duration_text = text

    @objc.python_method
    def set_label(self, text):
        self._label_text = text

    @objc.python_method
    def set_hint(self, text):
        self._hint_text = text

    @objc.python_method
    def start_animation(self):
        if self._timer is not None:
            return
        from Foundation import NSTimer
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 30.0, self, "tick:", None, True
        )

    @objc.python_method
    def stop_animation(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def tick_(self, timer):
        self._phase += 0.05
        # Prevent unbounded growth
        if self._phase > 628.0:
            self._phase = 0.0
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(rect)

        if self._state == "idle":
            return

        cx = rect.size.width / 2
        cy = rect.size.height / 2 + 10  # shift orb up for duration + label + hint

        phase = self._phase
        amp = self._amplitude

        if self._state == "recording":
            self._draw_recording_orb(cx, cy, phase, amp)
        elif self._state == "processing":
            self._draw_processing_orb(cx, cy, phase)

        # Duration text above orb
        if self._duration_text:
            self._draw_duration(self._duration_text, rect)

        # Status label below orb
        if self._label_text:
            self._draw_label(self._label_text, rect, y=22)

        # Hint text at bottom
        if self._hint_text:
            self._draw_hint(self._hint_text, rect)

    @objc.python_method
    def _draw_recording_orb(self, cx, cy, phase, amp):
        """Red/orange pulsing orb modulated by microphone amplitude."""
        amp_scale = 1.0 + amp * 0.4  # up to 40% size boost from amplitude

        for i in range(5, 0, -1):
            t = phase + i * 0.3
            scale = amp_scale * (1.0 + 0.15 * math.sin(t * 2.0) * i / 5.0)
            radius = (ORB_SIZE / 2) * scale * (i / 5.0)
            alpha = 0.15 + 0.1 * (5 - i) / 5.0

            r = 1.0
            g = 0.2 + 0.3 * math.sin(t * 1.5) ** 2
            b = 0.1 + 0.2 * math.sin(t * 0.8) ** 2

            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)
            color.set()

            path = NSBezierPath.bezierPathWithOvalInRect_(
                ((cx - radius, cy - radius), (radius * 2, radius * 2))
            )
            path.fill()

        # Amplitude rings
        if amp > 0.05:
            for ring in range(3):
                ring_radius = (ORB_SIZE / 2) * amp_scale + 8 + ring * 12
                ring_alpha = 0.15 * (1.0 - ring / 3.0) * amp
                ring_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.4, 0.2, ring_alpha
                )
                ring_color.set()
                ring_path = NSBezierPath.bezierPathWithOvalInRect_(
                    ((cx - ring_radius, cy - ring_radius),
                     (ring_radius * 2, ring_radius * 2))
                )
                ring_path.setLineWidth_(1.5)
                ring_path.stroke()

        # Bright center
        center_r = 15 + 5 * math.sin(phase * 3) + amp * 8
        bright = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.4, 0.2, 0.9)
        bright.set()
        NSBezierPath.bezierPathWithOvalInRect_(
            ((cx - center_r, cy - center_r), (center_r * 2, center_r * 2))
        ).fill()

    @objc.python_method
    def _draw_processing_orb(self, cx, cy, phase):
        """Purple/blue spinning orb for processing."""
        for i in range(6, 0, -1):
            angle = phase * 2.0 + i * math.pi / 3
            offset_x = math.cos(angle) * 8
            offset_y = math.sin(angle) * 8
            scale = 0.9 + 0.1 * math.sin(phase * 3 + i)
            radius = (ORB_SIZE / 2) * scale * (i / 6.0)
            alpha = 0.12 + 0.08 * (6 - i) / 6.0

            r = 0.4 + 0.3 * math.sin(phase + i * 0.5) ** 2
            g = 0.2 + 0.2 * math.sin(phase * 0.7 + i) ** 2
            b = 0.8 + 0.2 * math.sin(phase * 1.2) ** 2

            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)
            color.set()

            path = NSBezierPath.bezierPathWithOvalInRect_(
                ((cx - radius + offset_x, cy - radius + offset_y), (radius * 2, radius * 2))
            )
            path.fill()

        # Bright spinning center
        for j in range(3):
            a = phase * 4 + j * math.pi * 2 / 3
            dot_x = cx + math.cos(a) * 12
            dot_y = cy + math.sin(a) * 12
            dot_r = 6
            bright = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.3, 1.0, 0.8)
            bright.set()
            NSBezierPath.bezierPathWithOvalInRect_(
                ((dot_x - dot_r, dot_y - dot_r), (dot_r * 2, dot_r * 2))
            ).fill()

    @objc.python_method
    def _draw_duration(self, text, rect):
        """Draw duration timer above the orb."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)  # Center
        attrs = {
            NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(22, 0.2),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSParagraphStyleAttributeName: style,
        }
        ns_text = NSString.stringWithString_(text)
        text_rect = NSMakeRect(0, rect.size.height - 30, rect.size.width, 28)
        ns_text.drawInRect_withAttributes_(text_rect, attrs)

    @objc.python_method
    def _draw_label(self, text, rect, y=22):
        """Draw status label below the orb."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(13, 0.3),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSParagraphStyleAttributeName: style,
        }
        ns_text = NSString.stringWithString_(text)
        text_rect = NSMakeRect(0, y, rect.size.width, 20)
        ns_text.drawInRect_withAttributes_(text_rect, attrs)

    @objc.python_method
    def _draw_hint(self, text, rect):
        """Draw small hint text at the bottom."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(10),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.7, 0.6),
            NSParagraphStyleAttributeName: style,
        }
        ns_text = NSString.stringWithString_(text)
        text_rect = NSMakeRect(0, 4, rect.size.width, 16)
        ns_text.drawInRect_withAttributes_(text_rect, attrs)


class RecordingOverlay:
    """Floating overlay window with animated orb."""

    def __init__(self):
        screen = NSScreen.mainScreen().frame()

        # Try to restore saved position, fallback to center-bottom
        defaults = NSUserDefaults.standardUserDefaults()
        saved = defaults.dictionaryForKey_(_POS_KEY)
        if saved and "x" in saved and "y" in saved:
            x = float(saved["x"])
            y = float(saved["y"])
        else:
            x = (screen.size.width - WINDOW_W) / 2
            y = 80

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (WINDOW_W, WINDOW_H)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(25)  # NSStatusWindowLevel
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setIgnoresMouseEvents_(True)
        self.window.setHasShadow_(True)
        self.window.setCollectionBehavior_(1 << 0 | 1 << 4)  # canJoinAllSpaces + transient
        self.window.setMovableByWindowBackground_(True)

        self.orb_view = OrbView.alloc().initWithFrame_(((0, 0), (WINDOW_W, WINDOW_H)))
        self.window.setContentView_(self.orb_view)

    def _save_position(self):
        """Save current window position to UserDefaults."""
        frame = self.window.frame()
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.setObject_forKey_(
            {"x": float(frame.origin.x), "y": float(frame.origin.y)},
            _POS_KEY,
        )

    def show_recording(self, label="Говорите..."):
        """Show recording animation."""
        self.orb_view.set_state("recording")
        self.orb_view.set_label(label)
        self.orb_view.set_hint("Esc — отмена")
        self.orb_view.set_duration("")
        self.orb_view.start_animation()

        # Allow dragging with Option key held
        self.window.setIgnoresMouseEvents_(False)

        # Fade in
        self.window.setAlphaValue_(0.0)
        self.window.orderFront_(None)
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.25)
        self.window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

    def show_processing(self, label="Обработка..."):
        """Switch to processing animation."""
        self.orb_view.set_state("processing")
        self.orb_view.set_label(label)
        self.orb_view.set_hint("")
        self.orb_view.set_duration("")
        self.window.setIgnoresMouseEvents_(True)

    def hide(self):
        """Hide overlay with fade-out."""
        self.orb_view.stop_animation()
        self.orb_view.set_state("idle")
        self._save_position()

        # Fade out
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        self.window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()

        # Schedule orderOut after fade completes
        from Foundation import NSTimer
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25, self.window, "orderOut:", None, False
        )

    def set_duration(self, text):
        """Update the duration display."""
        self.orb_view.set_duration(text)

    def set_amplitude(self, value):
        """Update the amplitude visualization."""
        self.orb_view.set_amplitude(value)
