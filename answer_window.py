"""Floating answer window for AI Q&A responses.

Displays LLM answers in a styled floating panel that appears
on all Spaces, similar to the recording overlay.
"""

import objc
from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSMakeRange,
    NSScreen,
    NSScrollView,
    NSTextView,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSString

WINDOW_W = 420
WINDOW_H = 300
PADDING = 16


class AnswerContentView(NSView):
    """Dark rounded background for the answer window."""

    def drawRect_(self, rect):
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.1, 0.12, 0.95).setFill()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 14, 14
        )
        path.fill()


class AnswerWindow:
    """Floating window that displays AI answers."""

    def __init__(self):
        self.window = None
        self.text_view = None

    def _create_window(self):
        screen = NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - WINDOW_W) / 2
        y = screen.origin.y + (screen.size.height - WINDOW_H) / 2

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (WINDOW_W, WINDOW_H)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(25)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(True)
        # Visible on all Spaces
        self.window.setCollectionBehavior_(1 | 256)
        self.window.setMovableByWindowBackground_(True)

        bg = AnswerContentView.alloc().initWithFrame_(
            ((0, 0), (WINDOW_W, WINDOW_H))
        )
        self.window.setContentView_(bg)

        # Header label "AI Ответ"
        header_y = WINDOW_H - PADDING - 20
        # We'll draw header via the text view content

        # Scrollable text view for the answer
        scroll_frame = ((PADDING, PADDING), (WINDOW_W - 2 * PADDING, WINDOW_H - 2 * PADDING))
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)

        text_frame = ((0, 0), (WINDOW_W - 2 * PADDING - 4, WINDOW_H - 2 * PADDING))
        self.text_view = NSTextView.alloc().initWithFrame_(text_frame)
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextColor_(NSColor.whiteColor())
        self.text_view.setFont_(NSFont.systemFontOfSize_(14))
        # Allow text wrapping
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.setVerticallyResizable_(True)
        text_container = self.text_view.textContainer()
        text_container.setWidthTracksTextView_(True)
        text_container.setContainerSize_((WINDOW_W - 2 * PADDING - 4, 1e7))

        scroll.setDocumentView_(self.text_view)
        bg.addSubview_(scroll)

    def show(self, question, answer):
        """Show the answer window with question and response."""
        if not self.window:
            self._create_window()

        # Center on current main screen
        screen = NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - WINDOW_W) / 2
        y = screen.origin.y + (screen.size.height - WINDOW_H) / 2
        self.window.setFrameOrigin_((x, y))

        # Build styled text
        from AppKit import (
            NSMutableAttributedString,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
        )

        styled = NSMutableAttributedString.alloc().init()

        # Question header
        q_header = NSMutableAttributedString.alloc().initWithString_("🎤 Вопрос:\n")
        q_header.addAttribute_value_range_(
            NSFontAttributeName,
            NSFont.boldSystemFontOfSize_(12),
            NSMakeRange(0, q_header.length()),
        )
        q_header.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.6, 0.65, 1.0),
            NSMakeRange(0, q_header.length()),
        )
        styled.appendAttributedString_(q_header)

        # Question text
        q_text = NSMutableAttributedString.alloc().initWithString_(question + "\n\n")
        q_text.addAttribute_value_range_(
            NSFontAttributeName,
            NSFont.systemFontOfSize_(13),
            NSMakeRange(0, q_text.length()),
        )
        q_text.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.7, 0.75, 1.0),
            NSMakeRange(0, q_text.length()),
        )
        styled.appendAttributedString_(q_text)

        # Answer header
        a_header = NSMutableAttributedString.alloc().initWithString_("💬 Ответ:\n")
        a_header.addAttribute_value_range_(
            NSFontAttributeName,
            NSFont.boldSystemFontOfSize_(12),
            NSMakeRange(0, a_header.length()),
        )
        a_header.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.7, 1.0, 1.0),
            NSMakeRange(0, a_header.length()),
        )
        styled.appendAttributedString_(a_header)

        # Answer text
        a_text = NSMutableAttributedString.alloc().initWithString_(answer)
        a_text.addAttribute_value_range_(
            NSFontAttributeName,
            NSFont.systemFontOfSize_(14),
            NSMakeRange(0, a_text.length()),
        )
        a_text.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            NSColor.whiteColor(),
            NSMakeRange(0, a_text.length()),
        )
        styled.appendAttributedString_(a_text)

        self.text_view.textStorage().setAttributedString_(styled)

        # Fade in
        self.window.setAlphaValue_(0.0)
        self.window.orderFront_(None)
        self.window.makeKeyWindow()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.3)
        self.window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

    def show_loading(self, question):
        """Show window with loading state."""
        self.show(question, "Думаю...")

    def hide(self):
        """Hide the answer window."""
        if not self.window:
            return
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        self.window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()

        from Foundation import NSTimer
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25, self.window, "orderOut:", None, False
        )

    def is_visible(self):
        return self.window and self.window.isVisible() and self.window.alphaValue() > 0
