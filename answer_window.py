"""Floating answer window for AI Q&A responses.

Displays LLM answers in a styled floating panel with basic
markdown rendering (bold, lists, headers, code).
Includes "Подробнее" button to request a longer answer.
"""

import re
import objc
from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRange,
    NSMutableAttributedString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSObject,
    NSScreen,
    NSScrollView,
    NSTextView,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSString

WINDOW_W = 520
WINDOW_H = 420
PADDING = 18
BUTTON_H = 32


def _clean_markdown(text):
    """Strip markdown to clean text, preserving structure."""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[\|\-\s:]+$', stripped) and '|' in stripped:
            continue
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
    white = NSColor.whiteColor()
    accent = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.8, 1.0, 1.0)
    gray = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.75, 0.75, 0.8, 1.0)

    lines = text.split('\n')
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            code_font = NSFont.userFixedPitchFontOfSize_(base_size - 1)
            _append(result, line + '\n', code_font, gray)
            continue

        if not stripped:
            _append(result, '\n', base_font, white)
            continue

        m = re.match(r'^#{1,3}\s+(.+)', stripped)
        if m:
            _append(result, m.group(1) + '\n', heading_font, accent)
            continue

        m = re.match(r'^[\-\*•]\s+(.+)', stripped)
        if m:
            _append_inline(result, '  • ' + m.group(1) + '\n', base_font, bold_font, white)
            continue

        m = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
        if m:
            _append_inline(result, f'  {m.group(1)}. ' + m.group(2) + '\n',
                           base_font, bold_font, white)
            continue

        _append_inline(result, stripped + '\n', base_font, bold_font, white)

    return result


def _append(result, text, font, color):
    s = NSMutableAttributedString.alloc().initWithString_(text)
    r = NSMakeRange(0, s.length())
    s.addAttribute_value_range_(NSFontAttributeName, font, r)
    s.addAttribute_value_range_(NSForegroundColorAttributeName, color, r)
    result.appendAttributedString_(s)


def _append_inline(result, text, base_font, bold_font, color):
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


class _ButtonHandler(NSObject):
    """NSObject subclass to handle button actions."""

    _callback = None

    def initWithCallback_(self, callback):
        self = objc.super(_ButtonHandler, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    @objc.typedSelector(b"v@:@")
    def onClick_(self, sender):
        if self._callback:
            self._callback()


class AnswerWindow:
    """Floating window that displays AI answers."""

    def __init__(self):
        self.window = None
        self.text_view = None
        self._detail_btn = None
        self._btn_handler = None
        self._last_question = None
        self._on_detail = None  # callback for "Подробнее"

    def set_on_detail(self, callback):
        """Set callback for detail button: callback(question)."""
        self._on_detail = callback

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

        # "Подробнее" button at the bottom
        btn_w = 110
        btn_x = WINDOW_W - PADDING - btn_w
        btn_y = PADDING - 2

        self._btn_handler = _ButtonHandler.alloc().initWithCallback_(self._on_detail_click)

        self._detail_btn = NSButton.alloc().initWithFrame_(
            ((btn_x, btn_y), (btn_w, BUTTON_H))
        )
        self._detail_btn.setTitle_("Подробнее")
        self._detail_btn.setBezelStyle_(1)
        self._detail_btn.setTarget_(self._btn_handler)
        self._detail_btn.setAction_(objc.selector(self._btn_handler.onClick_, signature=b"v@:@"))
        self._detail_btn.setWantsLayer_(True)
        bg.addSubview_(self._detail_btn)

        # "Закрыть" button
        close_btn_w = 80
        close_x = PADDING

        self._close_handler = _ButtonHandler.alloc().initWithCallback_(self.hide)

        self._close_btn = NSButton.alloc().initWithFrame_(
            ((close_x, btn_y), (close_btn_w, BUTTON_H))
        )
        self._close_btn.setTitle_("Закрыть")
        self._close_btn.setBezelStyle_(1)
        self._close_btn.setTarget_(self._close_handler)
        self._close_btn.setAction_(objc.selector(self._close_handler.onClick_, signature=b"v@:@"))
        bg.addSubview_(self._close_btn)

        # Scrollable text view (above buttons)
        text_top = PADDING + BUTTON_H + 8
        scroll_h = WINDOW_H - text_top - PADDING
        scroll_frame = ((PADDING, text_top), (WINDOW_W - 2 * PADDING, scroll_h))
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)

        content_w = WINDOW_W - 2 * PADDING - 4
        text_frame = ((0, 0), (content_w, scroll_h))
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

    def _on_detail_click(self):
        if self._on_detail and self._last_question:
            self._on_detail(self._last_question)

    def show(self, question, answer):
        """Show the answer window with question and response."""
        if not self.window:
            self._create_window()

        self._last_question = question

        screen = NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - WINDOW_W) / 2
        y = screen.origin.y + (screen.size.height - WINDOW_H) / 2
        self.window.setFrameOrigin_((x, y))

        styled = NSMutableAttributedString.alloc().init()

        gray = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.6, 1.0)
        label_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.45, 0.5, 1.0)
        _append(styled, "Вопрос: ", NSFont.boldSystemFontOfSize_(11), label_color)
        _append(styled, question + "\n\n",
                NSFont.systemFontOfSize_(12.5), gray)

        sep_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.3, 0.35, 1.0)
        _append(styled, "─" * 45 + "\n\n", NSFont.systemFontOfSize_(8), sep_color)

        answer_styled = _markdown_to_attributed(answer)
        styled.appendAttributedString_(answer_styled)

        self.text_view.textStorage().setAttributedString_(styled)
        self.text_view.scrollRangeToVisible_(NSMakeRange(0, 0))

        # Show/enable detail button
        if self._detail_btn:
            self._detail_btn.setEnabled_(True)
            self._detail_btn.setTitle_("Подробнее")

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
        if self._detail_btn:
            self._detail_btn.setEnabled_(False)

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
