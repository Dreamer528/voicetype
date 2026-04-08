"""Floating answer window for AI Q&A responses.

Displays LLM answers in a styled floating panel with
markdown rendering (bold, italic, lists, headers, code blocks).
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
    NSFontManager,
    NSMakeRange,
    NSMutableAttributedString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSParagraphStyleAttributeName,
    NSMutableParagraphStyle,
    NSObject,
    NSScreen,
    NSScrollView,
    NSTextView,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSString

WINDOW_W = 620
WINDOW_H = 500
PADDING = 22
BUTTON_H = 34

# Colors
_WHITE = None
_GRAY = None
_DIM = None
_ACCENT = None
_CODE_COLOR = None
_CODE_BG = None
_HEADING_COLOR = None
_SEPARATOR_COLOR = None
_BULLET_COLOR = None


def _init_colors():
    global _WHITE, _GRAY, _DIM, _ACCENT, _CODE_COLOR, _HEADING_COLOR
    global _SEPARATOR_COLOR, _BULLET_COLOR
    _WHITE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.93, 0.93, 0.95, 1.0)
    _GRAY = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.65, 0.65, 0.7, 1.0)
    _DIM = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.45, 0.5, 1.0)
    _ACCENT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.75, 1.0, 1.0)
    _CODE_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.9, 0.55, 1.0)
    _HEADING_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.8, 1.0, 1.0)
    _SEPARATOR_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.25, 0.3, 1.0)
    _BULLET_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.7, 0.95, 1.0)


def _clean_markdown(text):
    """Strip problematic markdown, preserving structure."""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    lines = text.split('\n')
    cleaned = []
    table_rows = []

    for line in lines:
        stripped = line.strip()
        # Table separator line (|---|---|)
        if re.match(r'^[\|\-\s:]+$', stripped) and '|' in stripped:
            continue
        # Table row
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            table_rows.append(cells)
            continue
        # Flush table if we had one
        if table_rows:
            col_widths = []
            for row in table_rows:
                for j, cell in enumerate(row):
                    if j >= len(col_widths):
                        col_widths.append(0)
                    col_widths[j] = max(col_widths[j], len(cell))
            for row in table_rows:
                formatted = "  ".join(
                    cell.ljust(col_widths[j]) if j < len(col_widths) else cell
                    for j, cell in enumerate(row)
                )
                cleaned.append("  " + formatted)
            table_rows = []
        cleaned.append(line)

    # Flush remaining table
    if table_rows:
        col_widths = []
        for row in table_rows:
            for j, cell in enumerate(row):
                if j >= len(col_widths):
                    col_widths.append(0)
                col_widths[j] = max(col_widths[j], len(cell))
        for row in table_rows:
            formatted = "  ".join(
                cell.ljust(col_widths[j]) if j < len(col_widths) else cell
                for j, cell in enumerate(row)
            )
            cleaned.append("  " + formatted)

    return '\n'.join(cleaned)


def _make_paragraph_style(indent=0, spacing=3):
    style = NSMutableParagraphStyle.alloc().init()
    style.setLineSpacing_(spacing)
    style.setParagraphSpacing_(4)
    if indent:
        style.setFirstLineHeadIndent_(indent)
        style.setHeadIndent_(indent)
    return style


def _markdown_to_attributed(text, base_size=14):
    """Convert markdown text to NSMutableAttributedString with rich styling."""
    if not _WHITE:
        _init_colors()

    text = _clean_markdown(text)
    result = NSMutableAttributedString.alloc().init()

    base_font = NSFont.systemFontOfSize_(base_size)
    bold_font = NSFont.boldSystemFontOfSize_(base_size)
    italic_font = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
        base_font, 1)  # NSItalicFontMask = 1
    h1_font = NSFont.boldSystemFontOfSize_(base_size + 4)
    h2_font = NSFont.boldSystemFontOfSize_(base_size + 2)
    h3_font = NSFont.boldSystemFontOfSize_(base_size + 1)
    code_inline_font = NSFont.userFixedPitchFontOfSize_(base_size - 1)
    code_block_font = NSFont.userFixedPitchFontOfSize_(base_size - 2)

    lines = text.split('\n')
    in_code_block = False
    prev_empty = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Code block toggle
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            if in_code_block:
                _append(result, '\n', base_font, _WHITE, _make_paragraph_style())
            continue

        if in_code_block:
            _append(result, '  ' + line + '\n', code_block_font, _CODE_COLOR,
                    _make_paragraph_style(indent=12, spacing=1))
            continue

        # Empty line
        if not stripped:
            if not prev_empty:
                _append(result, '\n', base_font, _WHITE, _make_paragraph_style(spacing=2))
            prev_empty = True
            continue
        prev_empty = False

        # H1 (# Title)
        m = re.match(r'^#\s+(.+)', stripped)
        if m:
            _append(result, m.group(1) + '\n', h1_font, _HEADING_COLOR,
                    _make_paragraph_style(spacing=6))
            continue

        # H2 (## Title)
        m = re.match(r'^##\s+(.+)', stripped)
        if m:
            _append(result, m.group(1) + '\n', h2_font, _HEADING_COLOR,
                    _make_paragraph_style(spacing=5))
            continue

        # H3 (### Title)
        m = re.match(r'^###\s+(.+)', stripped)
        if m:
            _append(result, m.group(1) + '\n', h3_font, _HEADING_COLOR,
                    _make_paragraph_style(spacing=4))
            continue

        # Bullet points
        m = re.match(r'^[\-\*•]\s+(.+)', stripped)
        if m:
            style = _make_paragraph_style(indent=20, spacing=2)
            _append(result, '  •  ', bold_font, _BULLET_COLOR, style)
            _append_inline(result, m.group(1) + '\n', base_font, bold_font,
                           italic_font, code_inline_font, _WHITE, style)
            continue

        # Numbered lists
        m = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
        if m:
            style = _make_paragraph_style(indent=20, spacing=2)
            _append(result, f'  {m.group(1)}.  ', bold_font, _BULLET_COLOR, style)
            _append_inline(result, m.group(2) + '\n', base_font, bold_font,
                           italic_font, code_inline_font, _WHITE, style)
            continue

        # Horizontal rule
        if re.match(r'^[\-\*_]{3,}$', stripped):
            _append(result, '─' * 50 + '\n', NSFont.systemFontOfSize_(8),
                    _SEPARATOR_COLOR, _make_paragraph_style())
            continue

        # Regular paragraph
        style = _make_paragraph_style(spacing=3)
        _append_inline(result, stripped + '\n', base_font, bold_font,
                       italic_font, code_inline_font, _WHITE, style)

    return result


def _append(result, text, font, color, para_style=None):
    """Append plain text segment with optional paragraph style."""
    s = NSMutableAttributedString.alloc().initWithString_(text)
    r = NSMakeRange(0, s.length())
    s.addAttribute_value_range_(NSFontAttributeName, font, r)
    s.addAttribute_value_range_(NSForegroundColorAttributeName, color, r)
    if para_style:
        s.addAttribute_value_range_(NSParagraphStyleAttributeName, para_style, r)
    result.appendAttributedString_(s)


def _append_inline(result, text, base_font, bold_font, italic_font,
                   code_font, color, para_style=None):
    """Append text with inline **bold**, *italic*, and `code` rendering."""
    # Split by **bold**, *italic*, `code`
    parts = re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            _append(result, part[2:-2], bold_font, color, para_style)
        elif part.startswith('*') and part.endswith('*'):
            _append(result, part[1:-1], italic_font,
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.8, 0.8, 0.85, 1.0),
                    para_style)
        elif part.startswith('`') and part.endswith('`'):
            _append(result, part[1:-1], code_font, _CODE_COLOR, para_style)
        else:
            _append(result, part, base_font, color, para_style)


class AnswerContentView(NSView):
    """Dark rounded background for the answer window."""

    def drawRect_(self, rect):
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.08, 0.1, 0.96).setFill()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 16, 16
        )
        path.fill()

        # Subtle inner border
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.2, 0.25, 0.5).setStroke()
        inner = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 16, 16
        )
        inner.setLineWidth_(0.5)
        inner.stroke()


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
        self._copy_btn = None
        self._copy_handler = None
        self._last_question = None
        self._on_detail = None

    def set_on_detail(self, callback):
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

        # Bottom buttons
        btn_y = PADDING - 2

        # "Закрыть" button (left)
        self._close_handler = _ButtonHandler.alloc().initWithCallback_(self.hide)
        self._close_btn = NSButton.alloc().initWithFrame_(
            ((PADDING, btn_y), (90, BUTTON_H))
        )
        self._close_btn.setTitle_("Закрыть")
        self._close_btn.setBezelStyle_(1)
        self._close_btn.setTarget_(self._close_handler)
        self._close_btn.setAction_(objc.selector(self._close_handler.onClick_, signature=b"v@:@"))
        bg.addSubview_(self._close_btn)

        # "Копировать" button (center)
        self._copy_handler = _ButtonHandler.alloc().initWithCallback_(self._copy_answer)
        self._copy_btn = NSButton.alloc().initWithFrame_(
            ((WINDOW_W / 2 - 50, btn_y), (100, BUTTON_H))
        )
        self._copy_btn.setTitle_("Копировать")
        self._copy_btn.setBezelStyle_(1)
        self._copy_btn.setTarget_(self._copy_handler)
        self._copy_btn.setAction_(objc.selector(self._copy_handler.onClick_, signature=b"v@:@"))
        bg.addSubview_(self._copy_btn)

        # "Подробнее" button (right)
        btn_w = 120
        self._btn_handler = _ButtonHandler.alloc().initWithCallback_(self._on_detail_click)
        self._detail_btn = NSButton.alloc().initWithFrame_(
            ((WINDOW_W - PADDING - btn_w, btn_y), (btn_w, BUTTON_H))
        )
        self._detail_btn.setTitle_("Подробнее ›")
        self._detail_btn.setBezelStyle_(1)
        self._detail_btn.setTarget_(self._btn_handler)
        self._detail_btn.setAction_(objc.selector(self._btn_handler.onClick_, signature=b"v@:@"))
        bg.addSubview_(self._detail_btn)

        # Scrollable text view (above buttons)
        text_top = PADDING + BUTTON_H + 10
        scroll_h = WINDOW_H - text_top - PADDING
        scroll_frame = ((PADDING, text_top), (WINDOW_W - 2 * PADDING, scroll_h))
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)

        content_w = WINDOW_W - 2 * PADDING - 8
        text_frame = ((0, 0), (content_w, scroll_h))
        self.text_view = NSTextView.alloc().initWithFrame_(text_frame)
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextColor_(NSColor.whiteColor())
        self.text_view.setFont_(NSFont.systemFontOfSize_(14))
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

    def _copy_answer(self):
        """Copy the answer text to clipboard."""
        if self.text_view:
            text = self.text_view.textStorage().string()
            parts = text.split('─' * 55, 1)
            answer_text = parts[1].strip() if len(parts) > 1 else text
            from AppKit import NSPasteboard, NSPasteboardTypeString
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(answer_text, NSPasteboardTypeString)
            if self._copy_btn:
                self._copy_btn.setTitle_("✓ Скопировано")

    def update_answer(self, question, answer):
        """Update the answer text progressively (for streaming)."""
        if not self.window or not self.text_view:
            self.show(question, answer)
            return
        self._last_question = question
        styled = NSMutableAttributedString.alloc().init()
        _append(styled, "Вопрос:  ", NSFont.boldSystemFontOfSize_(11), _DIM)
        _append(styled, question + "\n\n", NSFont.systemFontOfSize_(12.5), _GRAY)
        _append(styled, '─' * 55 + '\n\n', NSFont.systemFontOfSize_(6), _SEPARATOR_COLOR)
        answer_styled = _markdown_to_attributed(answer)
        styled.appendAttributedString_(answer_styled)
        self.text_view.textStorage().setAttributedString_(styled)
        # Auto-scroll to bottom as new content arrives
        length = self.text_view.textStorage().length()
        self.text_view.scrollRangeToVisible_(NSMakeRange(length, 0))

    def show(self, question, answer):
        """Show the answer window with question and response."""
        if not _WHITE:
            _init_colors()
        if not self.window:
            self._create_window()

        self._last_question = question

        screen = NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - WINDOW_W) / 2
        y = screen.origin.y + (screen.size.height - WINDOW_H) / 2
        self.window.setFrameOrigin_((x, y))

        styled = NSMutableAttributedString.alloc().init()

        # Question (compact, dimmed)
        _append(styled, "Вопрос:  ", NSFont.boldSystemFontOfSize_(11), _DIM)
        _append(styled, question + "\n\n", NSFont.systemFontOfSize_(12.5), _GRAY)

        # Thin separator
        _append(styled, '─' * 55 + '\n\n', NSFont.systemFontOfSize_(6), _SEPARATOR_COLOR)

        # Answer with full markdown rendering
        answer_styled = _markdown_to_attributed(answer)
        styled.appendAttributedString_(answer_styled)

        self.text_view.textStorage().setAttributedString_(styled)
        self.text_view.scrollRangeToVisible_(NSMakeRange(0, 0))

        if self._detail_btn:
            self._detail_btn.setEnabled_(True)
            self._detail_btn.setTitle_("Подробнее ›")
        if self._copy_btn:
            self._copy_btn.setTitle_("Копировать")

        self.window.setAlphaValue_(0.0)
        self.window.orderFront_(None)
        self.window.makeKeyWindow()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.3)
        self.window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

    def show_loading(self, question):
        """Show window with loading state."""
        self.show(question, "⏳ Думаю...")
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
