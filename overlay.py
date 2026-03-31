"""Siri-like pulsing orb overlay for recording/processing states."""

import math
import objc
from AppKit import (
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
import Quartz


# Window size
ORB_SIZE = 120
WINDOW_W = 200
WINDOW_H = 180


class OrbView(NSView):
    """Custom view that draws animated pulsing orb."""

    def initWithFrame_(self, frame):
        self = objc.super(OrbView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._phase = 0.0
        self._state = "idle"  # idle, recording, processing
        self._timer = None
        return self

    def isOpaque(self):
        return False

    @objc.python_method
    def set_state(self, state):
        self._state = state
        self.setNeedsDisplay_(True)

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
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(rect)

        if self._state == "idle":
            return

        cx = rect.size.width / 2
        cy = rect.size.height / 2 + 20  # shift orb up to leave room for label

        phase = self._phase

        if self._state == "recording":
            self._draw_recording_orb(cx, cy, phase)
            self._draw_label("Говорите...", rect)
        elif self._state == "processing":
            self._draw_processing_orb(cx, cy, phase)
            self._draw_label("Обработка...", rect)

    @objc.python_method
    def _draw_recording_orb(self, cx, cy, phase):
        """Red/orange pulsing orb for recording."""
        for i in range(5, 0, -1):
            t = phase + i * 0.3
            scale = 1.0 + 0.15 * math.sin(t * 2.0) * i / 5.0
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

        # Bright center
        center_r = 15 + 5 * math.sin(phase * 3)
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
    def _draw_label(self, text, rect):
        """Draw status label below the orb."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)  # NSTextAlignmentCenter
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(13, 0.3),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSParagraphStyleAttributeName: style,
        }
        from Foundation import NSString, NSMakeRect
        ns_text = NSString.stringWithString_(text)
        text_rect = NSMakeRect(0, 10, rect.size.width, 20)
        ns_text.drawInRect_withAttributes_(text_rect, attrs)


class RecordingOverlay:
    """Floating overlay window with animated orb."""

    def __init__(self):
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - WINDOW_W) / 2
        y = 80  # near bottom of screen

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (WINDOW_W, WINDOW_H)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(25)  # NSStatusWindowLevel — above everything
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setIgnoresMouseEvents_(True)
        self.window.setHasShadow_(False)
        self.window.setCollectionBehavior_(1 << 0 | 1 << 4)  # canJoinAllSpaces + transient

        self.orb_view = OrbView.alloc().initWithFrame_(((0, 0), (WINDOW_W, WINDOW_H)))
        self.window.setContentView_(self.orb_view)

    def show_recording(self):
        """Show recording animation."""
        self.orb_view.set_state("recording")
        self.orb_view.start_animation()
        self.window.setAlphaValue_(0.0)
        self.window.orderFront_(None)
        # Fade in
        self.window.setAlphaValue_(1.0)

    def show_processing(self):
        """Switch to processing animation."""
        self.orb_view.set_state("processing")

    def hide(self):
        """Hide overlay."""
        self.orb_view.stop_animation()
        self.orb_view.set_state("idle")
        self.window.orderOut_(None)
