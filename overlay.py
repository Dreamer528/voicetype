"""Premium floating overlay with flowing waveform visualization.

Recording: live audio waveform with smooth bezier curves, gradient fills,
glow effects, and mirror reflection. Responds to actual microphone input.

Processing: calm pulsing wave with purple/blue gradient.
"""

import math
import objc
from AppKit import (
    NSAnimationContext,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
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

# Window dimensions — wide pill shape
WINDOW_W = 280
WINDOW_H = 120
WAVE_H = 32        # height of waveform area
WAVE_Y_CENTER = 50  # vertical center of waveform
NUM_BARS = 40       # number of waveform bars

# UserDefaults key for saved position
_POS_KEY = "VoiceTypeOverlayPosition"


class WaveformView(NSView):
    """Custom view rendering a premium audio waveform visualization."""

    def initWithFrame_(self, frame):
        self = objc.super(WaveformView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._phase = 0.0
        self._state = "idle"
        self._timer = None
        self._amplitudes = [0.0] * NUM_BARS
        self._smoothed = [0.0] * NUM_BARS
        self._duration_text = ""
        self._label_text = ""
        self._hint_text = ""
        return self

    def isOpaque(self):
        return False

    @objc.python_method
    def set_state(self, state):
        self._state = state
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_amplitudes(self, values):
        """Set raw amplitude values from recorder history."""
        # Resample to NUM_BARS if needed
        n = len(values)
        if n == 0:
            return
        step = n / NUM_BARS
        self._amplitudes = [
            values[min(int(i * step), n - 1)] for i in range(NUM_BARS)
        ]

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
        self._phase += 0.06
        if self._phase > 628.0:
            self._phase = 0.0

        # Smooth amplitudes with exponential decay (lerp towards target)
        for i in range(NUM_BARS):
            target = self._amplitudes[i]
            self._smoothed[i] += (target - self._smoothed[i]) * 0.25

        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        w = rect.size.width
        h = rect.size.height

        # Clear
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(rect)

        if self._state == "idle":
            return

        # Draw pill background
        self._draw_pill_bg(w, h)

        # Draw waveform
        if self._state == "recording":
            self._draw_recording_waveform(w, h)
        elif self._state == "processing":
            self._draw_processing_wave(w, h)

        # Duration (top left)
        if self._duration_text:
            self._draw_text(self._duration_text, 20, h - 28,
                            size=16, weight=0.2, mono=True, alpha=0.95)

        # Label (top right)
        if self._label_text:
            self._draw_text_right(self._label_text, w - 20, h - 26,
                                  size=12, weight=0.3, alpha=0.7)

        # Hint at bottom center
        if self._hint_text:
            self._draw_text_center(self._hint_text, w, 8,
                                   size=9, weight=0.0, alpha=0.35)

    @objc.python_method
    def _draw_pill_bg(self, w, h):
        """Draw rounded pill background with subtle blur effect."""
        radius = 20
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), radius, radius
        )
        # Dark semi-transparent background
        bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.08, 0.1, 0.85)
        bg.set()
        pill.fill()

        # Subtle border
        border_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.3, 0.35, 0.3)
        border_color.set()
        pill.setLineWidth_(0.5)
        pill.stroke()

    @objc.python_method
    def _draw_recording_waveform(self, w, h):
        """Draw premium flowing waveform bars with glow and reflection."""
        phase = self._phase
        cx = w / 2
        cy = WAVE_Y_CENTER

        bar_total_w = w - 100  # leave space for duration & label
        bar_start_x = 60
        bar_w = bar_total_w / NUM_BARS * 0.65
        bar_gap = bar_total_w / NUM_BARS

        for i in range(NUM_BARS):
            amp = self._smoothed[i]
            # Add subtle idle animation even when silent
            idle_wave = 0.03 + 0.02 * math.sin(phase * 1.5 + i * 0.15)
            bar_amp = max(amp, idle_wave)

            bar_h = bar_amp * WAVE_H
            x = bar_start_x + i * bar_gap

            # Color: warm gradient based on amplitude
            # Low amplitude: soft teal/cyan → High amplitude: vibrant orange/red
            t = min(bar_amp * 2.5, 1.0)
            r = 0.2 + 0.8 * t
            g = 0.7 - 0.3 * t
            b = 0.9 - 0.7 * t

            # Main bar (upper half)
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.9)
            color.set()
            bar_rect = NSMakeRect(x, cy, bar_w, bar_h)
            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, bar_w / 2, bar_w / 2
            )
            bar_path.fill()

            # Mirror bar (lower half) — softer
            mirror_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                r, g, b, 0.35
            )
            mirror_color.set()
            mirror_rect = NSMakeRect(x, cy - bar_h * 0.7, bar_w, bar_h * 0.7)
            mirror_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                mirror_rect, bar_w / 2, bar_w / 2
            )
            mirror_path.fill()

            # Glow on loud bars
            if bar_amp > 0.3:
                glow_alpha = (bar_amp - 0.3) * 0.4
                glow_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, glow_alpha
                )
                glow_color.set()
                glow_r = bar_w * 2.5
                glow_rect = NSMakeRect(
                    x - glow_r / 2 + bar_w / 2,
                    cy - glow_r / 4,
                    glow_r,
                    bar_h + glow_r / 2,
                )
                glow_path = NSBezierPath.bezierPathWithOvalInRect_(glow_rect)
                glow_path.fill()

        # Center line — subtle
        line_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.7, 0.9, 0.15)
        line_color.set()
        line = NSBezierPath.bezierPath()
        line.moveToPoint_((bar_start_x, cy))
        line.lineToPoint_((bar_start_x + bar_total_w, cy))
        line.setLineWidth_(0.5)
        line.stroke()

    @objc.python_method
    def _draw_processing_wave(self, w, h):
        """Draw calm purple/blue flowing sine wave for processing state."""
        phase = self._phase
        cy = WAVE_Y_CENTER
        wave_start = 20
        wave_end = w - 20
        wave_w = wave_end - wave_start
        num_points = 80

        for layer in range(3):
            path = NSBezierPath.bezierPath()
            freq = 2.5 + layer * 0.7
            amp = (WAVE_H * 0.4) * (1.0 - layer * 0.25)
            speed = phase * (1.5 + layer * 0.3)
            alpha = 0.5 - layer * 0.15

            r = 0.4 + layer * 0.1
            g = 0.3 + layer * 0.05
            b = 0.9 - layer * 0.1

            # Upper wave
            for j in range(num_points + 1):
                t = j / num_points
                x = wave_start + t * wave_w
                y = cy + amp * math.sin(t * freq * math.pi + speed)
                # Taper at edges
                edge_fade = math.sin(t * math.pi) ** 0.5
                y = cy + (y - cy) * edge_fade

                if j == 0:
                    path.moveToPoint_((x, y))
                else:
                    path.lineToPoint_((x, y))

            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)
            color.set()
            path.setLineWidth_(2.0 - layer * 0.5)
            path.stroke()

            # Fill under the wave with very subtle gradient
            fill_path = NSBezierPath.bezierPath()
            for j in range(num_points + 1):
                t = j / num_points
                x = wave_start + t * wave_w
                y_val = cy + amp * math.sin(t * freq * math.pi + speed)
                edge_fade = math.sin(t * math.pi) ** 0.5
                y_val = cy + (y_val - cy) * edge_fade
                if j == 0:
                    fill_path.moveToPoint_((x, y_val))
                else:
                    fill_path.lineToPoint_((x, y_val))
            fill_path.lineToPoint_((wave_end, cy))
            fill_path.lineToPoint_((wave_start, cy))
            fill_path.closePath()

            fill_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                r, g, b, alpha * 0.15
            )
            fill_color.set()
            fill_path.fill()

        # Pulsing dots
        num_dots = 5
        for d in range(num_dots):
            t = (d + 0.5) / num_dots
            x = wave_start + t * wave_w
            y = cy + (WAVE_H * 0.3) * math.sin(t * 3 * math.pi + phase * 2)
            dot_r = 2.5 + 1.0 * math.sin(phase * 3 + d)
            dot_alpha = 0.5 + 0.3 * math.sin(phase * 2 + d * 1.5)
            dot_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.6, 0.4, 1.0, dot_alpha
            )
            dot_color.set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - dot_r, y - dot_r, dot_r * 2, dot_r * 2)
            ).fill()

    @objc.python_method
    def _draw_text(self, text, x, y, size=14, weight=0.0, mono=False, alpha=1.0):
        """Draw left-aligned text at position."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(0)  # Left
        if mono:
            font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight)
        else:
            font = NSFont.systemFontOfSize_weight_(size, weight)
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha),
            NSParagraphStyleAttributeName: style,
        }
        ns_text = NSString.stringWithString_(text)
        ns_text.drawAtPoint_withAttributes_((x, y), attrs)

    @objc.python_method
    def _draw_text_right(self, text, x, y, size=14, weight=0.0, alpha=1.0):
        """Draw right-aligned text ending at x."""
        font = NSFont.systemFontOfSize_weight_(size, weight)
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha),
        }
        ns_text = NSString.stringWithString_(text)
        text_size = ns_text.sizeWithAttributes_(attrs)
        if text_size:
            ns_text.drawAtPoint_withAttributes_((x - text_size.width, y), attrs)

    @objc.python_method
    def _draw_text_center(self, text, w, y, size=10, weight=0.0, alpha=0.5):
        """Draw center-aligned text."""
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)  # Center
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha),
            NSParagraphStyleAttributeName: style,
        }
        ns_text = NSString.stringWithString_(text)
        text_rect = NSMakeRect(0, y, w, 14)
        ns_text.drawInRect_withAttributes_(text_rect, attrs)


class RecordingOverlay:
    """Floating pill-shaped overlay with premium waveform visualization."""

    def __init__(self):
        screen = NSScreen.mainScreen().frame()

        # Restore saved position or center-bottom
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
        self.window.setCollectionBehavior_(1 << 0 | 1 << 4)
        self.window.setMovableByWindowBackground_(True)

        self.orb_view = WaveformView.alloc().initWithFrame_(
            ((0, 0), (WINDOW_W, WINDOW_H))
        )
        self.window.setContentView_(self.orb_view)

    def _save_position(self):
        frame = self.window.frame()
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.setObject_forKey_(
            {"x": float(frame.origin.x), "y": float(frame.origin.y)},
            _POS_KEY,
        )

    def show_recording(self, label="Говорите..."):
        self.orb_view.set_state("recording")
        self.orb_view.set_label(label)
        self.orb_view.set_hint("Esc — отмена")
        self.orb_view.set_duration("")
        self.orb_view.start_animation()
        self.window.setIgnoresMouseEvents_(False)

        self.window.setAlphaValue_(0.0)
        self.window.orderFront_(None)
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.3)
        self.window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

    def show_processing(self, label="Обработка..."):
        self.orb_view.set_state("processing")
        self.orb_view.set_label(label)
        self.orb_view.set_hint("")
        self.orb_view.set_duration("")
        self.window.setIgnoresMouseEvents_(True)

    def hide(self):
        self.orb_view.stop_animation()
        self.orb_view.set_state("idle")
        self._save_position()

        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        self.window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()

        from Foundation import NSTimer
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25, self.window, "orderOut:", None, False
        )

    def set_duration(self, text):
        self.orb_view.set_duration(text)

    def set_amplitude(self, value):
        """Legacy single-value amplitude (unused with waveform, kept for compat)."""
        pass

    def set_amplitudes(self, values):
        """Set amplitude history for waveform bars."""
        self.orb_view.set_amplitudes(values)
