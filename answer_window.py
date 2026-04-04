"""Floating answer window for AI Q&A responses.

Displays LLM answers in a styled floating panel with basic
markdown rendering (bold, lists, headers, code).
"""

import re
import objc
from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontManager,
    NSMakeRange,
    NSMutableAttributedString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSParagraphStyleAttributeName,
    NSMutableParagraphStyle,
    NSScreen,
    NSScrollView,
    NSTextView,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSString

WINDOW_W = 440
WINDOW_H = 320
PADDING = 18


def _clean_markdown(text):
    """Strip markdown to clean text, preserving structure."""
    # Remove <br> tags
    text = re.sub(r'<br\s*/?>', '\n', text)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove markdown tables (pipe-based)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip table separator lines (|---|---|)
        if re.match(r'^[\|\-\s:]+$', stripped) and '|' in stripped:
            continue
        # Convert table rows to plain text
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            cells = [c for c in cells if c]
            line = '  '.join(cells)
        cleaned.append(line)
    return '\n'.join(cleaned)


def _markdown_to_attributed(text, base_size=13.5):
    """Convert markdown text to NSMutableAttributedString with styling."""
    text = _clean_markdown(text)

    result = NSMutableAttributedString.alloc().init()
    base_font = NSFont.systemFontOfSize_(base_size)
    bold_font = NSFont.boldSystemFontOfSize_(base_size)
    heading_font = NSFont.boldSystemFontOfSize_(base_size + 2)
    code_font = NSFont.userFixedPitchFontOfSize_(base_size - 1)
    white = NSColor.whiteColor()
    gray = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.75, 0.75, 0.8, 1.0)
    accent = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.8, 1.0, 1.0)
    code_bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.2, 0.22, 1.0)

    lines = text.split('\n')
    in_code_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Code block toggle
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            _append(result, line + '\n', code_font, gray)
            continue

        # Empty line
        if not stripped:
            _append(result, '\n', base_font, white)
            continue

        # Headers (## Title)
        m = re.match(r'^#{1,3}\s+(.+)', stripped)
        if m:
            _append(result, m.group(1) + '\n', heading_font, accent)
            continue

        # Bullet points
        m = re.match(r'^[\-\*•]\s+(.+)', stripped)
        if m:
            _append_inline(result, '  • ' + m.group(1) + '\n', base_font, bold_font, white)
            continue

        # Numbered lists
        m = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
        if m:
            _append_inline(result, f'  {m.group(1)}. ' + m.group(2) + '\n',
                           base_font, bold_font, white)
            continue

        # Regular line — process inline bold/code
        _append_inline(result, stripped + '\n', base_font, bold_font, white)

    return result


def _append(result, text, font, color):
    """Append plain text segment."""
    s = NSMutableAttributedString.alloc().initWithString_(text)
    r = NSMakeRange(0, s.length())
    s.addAttribute_value_range_(NSFontAttributeName, font, r)
    s.addAttribute_value_range_(NSForegroundColorAttributeName, color, r)
    result.appendAttributedString_(s)


def _append_inline(result, text, base_font, bold_font, color):
    """Append text with inline **bold** and `code` rendering."""
    # Split by **bold** and `code` patterns
    parts = re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            _append(result, part[2:-2], bold_font, color)
        elif part.startswith('`') and part.endswith('`'):
            code_font = NSFont.userFixedPitchFontOfSize_(12)
            code_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.9, 0.6, 1.0)
            _append(result, part[1:-1], code_font, code_color)
        else:
            _append(result, part, base_font, color)


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
        self.window.setCollectionBehavior_(1 | 256)
        self.window.setMovableByWindowBackground_(True)

        bg = AnswerContentView.alloc().initWithFrame_(
            ((0, 0), (WINDOW_W, WINDOW_H))
        )
        self.window.setContentView_(bg)

        scroll_frame = ((PADDING, PADDING), (WINDOW_W - 2 * PADDING, WINDOW_H - 2 * PADDING))
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)

        content_w = WINDOW_W - 2 * PADDING - 4
        text_frame = ((0, 0), (content_w, WINDOW_H - 2 * PADDING))
        self.text_view = NSTextView.alloc().initWithFrame_(text_frame)
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextColor_(NSColor.whiteColor())
        self.text_view.setFont_(NSFont.systemFontOfSize_(13.5))
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.setVerticallyResizable_(True)
        text_container = self.text_view.textContainer()
        text_container.setWidthTracksTextView_(True)
        text_container.setContainerSize_((content_w, 1e7))

        scroll.setDocumentView_(self.text_view)
        bg.addSubview_(scroll)

    def show(self, question, answer):
        """Show the answer window with question and response."""
        if not self.window:
            self._create_window()

        screen = NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - WINDOW_W) / 2
        y = screen.origin.y + (screen.size.height - WINDOW_H) / 2
        self.window.setFrameOrigin_((x, y))

        styled = NSMutableAttributedString.alloc().init()

        # Question
        gray = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.6, 1.0)
        label_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.45, 0.5, 1.0)
        _append(styled, "Вопрос: ", NSFont.boldSystemFontOfSize_(11), label_color)
        _append(styled, question + "\n\n",
                NSFont.systemFontOfSize_(12.5), gray)

        # Separator
        _append(styled, "─" * 40 + "\n\n",
                NSFont.systemFontOfSize_(8),
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.3, 0.35, 1.0))

        # Answer with markdown rendering
        answer_styled = _markdown_to_attributed(answer)
        styled.appendAttributedString_(answer_styled)

        self.text_view.textStorage().setAttributedString_(styled)

        # Scroll to top
        self.text_view.scrollRangeToVisible_(NSMakeRange(0, 0))

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
