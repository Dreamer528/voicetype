"""Native macOS settings window for VoiceType."""

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSObject,
    NSPopUpButton,
    NSSecureTextField,
    NSSlider,
    NSStackView,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

from config import get_keychain_key


# --- Helpers ---

def _label(text, width=130):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, width, 22))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setAlignment_(2)  # Right
    field.setFont_(NSFont.systemFontOfSize_(13))
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return field


def _text_field(placeholder="", width=250):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, width, 22))
    field.setPlaceholderString_(placeholder)
    field.setFont_(NSFont.systemFontOfSize_(13))
    return field


def _secure_field(placeholder="", width=250):
    field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, width, 22))
    field.setPlaceholderString_(placeholder)
    field.setFont_(NSFont.systemFontOfSize_(13))
    return field


def _popup(items, width=250):
    popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        NSMakeRect(0, 0, width, 24), False
    )
    for title in items:
        popup.addItemWithTitle_(title)
    popup.setFont_(NSFont.systemFontOfSize_(13))
    return popup


def _hstack(*views, spacing=8):
    stack = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 30))
    stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    stack.setSpacing_(spacing)
    stack.setAlignment_(0x200)  # CenterY
    for v in views:
        stack.addView_inGravity_(v, 1)
    return stack


def _separator(width=400):
    sep = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 1))
    sep.setWantsLayer_(True)
    sep.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
    return sep


def _section_header(text):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 20))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.boldSystemFontOfSize_(12))
    field.setTextColor_(NSColor.secondaryLabelColor())
    return field


# Language/mode display values
LANGUAGES = ["Русский", "English"]
LANG_CODES = ["ru", "en"]
MODES = ["Зажать и говорить", "Нажать → говорить → нажать"]
MODE_CODES = ["push_to_talk", "toggle"]
TRANSCRIPTION_MODES = ["Облако (Groq)", "Локальный (MLX)", "Авто"]
TRANSCRIPTION_CODES = ["cloud", "local", "auto"]
LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"]


class SettingsWindowController(NSObject):
    """Native macOS settings window — NSObject subclass so actions work."""

    @objc.python_method
    def initWithConfig_onSave_onLearnHotkey_hotkeyName_(
        self, config, on_save, on_learn_hotkey, hotkey_name
    ):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self._config = config.copy()
        self._on_save = on_save
        self._on_learn_hotkey = on_learn_hotkey
        self._hotkey_name = hotkey_name
        self._api_key_visible = False
        self._build_window()
        self._populate_from_config()
        return self

    @objc.python_method
    def _build_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 480, 560), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("VoiceType — Настройки")
        self.window.center()
        self.window.setReleasedWhenClosed_(False)

        main_stack = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 460, 540))
        main_stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        main_stack.setSpacing_(10)
        main_stack.setAlignment_(0x100)  # Leading
        main_stack.setEdgeInsets_((20, 20, 20, 20))

        # --- ОСНОВНЫЕ ---
        main_stack.addView_inGravity_(_section_header("ОСНОВНЫЕ"), 1)

        # API key
        self.api_key_field = _secure_field("gsk_...", width=230)
        show_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 30, 22))
        show_btn.setTitle_("👁")
        show_btn.setBezelStyle_(NSBezelStyleRounded)
        show_btn.setTarget_(self)
        show_btn.setAction_("toggleApiKeyVisibility:")
        self._api_key_plain = _text_field("gsk_...", width=230)
        self._api_key_plain.setHidden_(True)
        main_stack.addView_inGravity_(
            _hstack(_label("API ключ:"), self.api_key_field, self._api_key_plain, show_btn), 1
        )

        # Language
        self.lang_popup = _popup(LANGUAGES)
        main_stack.addView_inGravity_(_hstack(_label("Язык:"), self.lang_popup), 1)

        # Mode
        self.mode_popup = _popup(MODES)
        main_stack.addView_inGravity_(_hstack(_label("Режим:"), self.mode_popup), 1)

        main_stack.addView_inGravity_(_separator(), 1)

        # --- ЗАПИСЬ ---
        main_stack.addView_inGravity_(_section_header("ЗАПИСЬ"), 1)

        # Hotkey
        self.hotkey_label = _text_field("", width=150)
        self.hotkey_label.setEditable_(False)
        self.hotkey_label.setSelectable_(False)
        change_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 90, 24))
        change_btn.setTitle_("Изменить")
        change_btn.setBezelStyle_(NSBezelStyleRounded)
        change_btn.setTarget_(self)
        change_btn.setAction_("changeHotkey:")
        main_stack.addView_inGravity_(
            _hstack(_label("Хоткей:"), self.hotkey_label, change_btn), 1
        )

        # Max recording
        self.max_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(0, 0, 180, 22))
        self.max_slider.setMinValue_(10)
        self.max_slider.setMaxValue_(300)
        self.max_slider.setContinuous_(True)
        self.max_slider.setTarget_(self)
        self.max_slider.setAction_("sliderChanged:")
        self.max_value_label = _label("120 сек", width=60)
        self.max_value_label.setAlignment_(0)
        main_stack.addView_inGravity_(
            _hstack(_label("Макс. запись:"), self.max_slider, self.max_value_label), 1
        )

        # LLM formatting
        self.format_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 22))
        self.format_checkbox.setButtonType_(NSButtonTypeSwitch)
        self.format_checkbox.setTitle_("LLM форматирование текста")
        self.format_checkbox.setFont_(NSFont.systemFontOfSize_(13))
        main_stack.addView_inGravity_(_hstack(_label(""), self.format_checkbox), 1)

        main_stack.addView_inGravity_(_separator(), 1)

        # --- AI ДВИЖОК ---
        main_stack.addView_inGravity_(_section_header("AI ДВИЖОК"), 1)

        self.trans_popup = _popup(TRANSCRIPTION_MODES)
        main_stack.addView_inGravity_(_hstack(_label("Транскрипция:"), self.trans_popup), 1)

        self.local_model_popup = _popup(LOCAL_MODELS)
        main_stack.addView_inGravity_(_hstack(_label("Локальная модель:"), self.local_model_popup), 1)

        self.base_url_field = _text_field("https://...", width=250)
        main_stack.addView_inGravity_(_hstack(_label("Base URL:"), self.base_url_field), 1)

        main_stack.addView_inGravity_(_separator(), 1)

        # --- Кнопки ---
        spacer = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 90, 30))
        cancel_btn.setTitle_("Отмена")
        cancel_btn.setBezelStyle_(NSBezelStyleRounded)
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancelSettings:")

        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 90, 30))
        save_btn.setTitle_("Сохранить")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("saveSettings:")

        main_stack.addView_inGravity_(_hstack(spacer, cancel_btn, save_btn, spacing=12), 1)

        self.window.setContentView_(main_stack)

    @objc.python_method
    def _populate_from_config(self):
        api_key = self._config.get("groq_api_key", "")
        if not api_key:
            api_key = get_keychain_key() or ""
        self.api_key_field.setStringValue_(api_key)
        self._api_key_plain.setStringValue_(api_key)

        lang = self._config.get("language", "ru")
        self.lang_popup.selectItemAtIndex_(
            LANG_CODES.index(lang) if lang in LANG_CODES else 0
        )

        mode = self._config.get("mode", "push_to_talk")
        self.mode_popup.selectItemAtIndex_(
            MODE_CODES.index(mode) if mode in MODE_CODES else 0
        )

        self.hotkey_label.setStringValue_(self._hotkey_name)

        max_sec = self._config.get("max_recording_seconds", 120)
        self.max_slider.setIntValue_(max_sec)
        self.max_value_label.setStringValue_(f"{max_sec} сек")

        self.format_checkbox.setState_(
            1 if self._config.get("format_with_llm", True) else 0
        )

        trans_mode = self._config.get("transcription_mode", "cloud")
        self.trans_popup.selectItemAtIndex_(
            TRANSCRIPTION_CODES.index(trans_mode) if trans_mode in TRANSCRIPTION_CODES else 0
        )

        local_model = self._config.get("local_whisper_model", "base")
        self.local_model_popup.selectItemAtIndex_(
            LOCAL_MODELS.index(local_model) if local_model in LOCAL_MODELS else 1
        )

        self.base_url_field.setStringValue_(self._config.get("base_url", ""))

    @objc.python_method
    def _build_config(self):
        config = self._config.copy()

        if self._api_key_visible:
            config["groq_api_key"] = self._api_key_plain.stringValue()
        else:
            config["groq_api_key"] = self.api_key_field.stringValue()

        lang_idx = self.lang_popup.indexOfSelectedItem()
        if 0 <= lang_idx < len(LANG_CODES):
            config["language"] = LANG_CODES[lang_idx]

        mode_idx = self.mode_popup.indexOfSelectedItem()
        if 0 <= mode_idx < len(MODE_CODES):
            config["mode"] = MODE_CODES[mode_idx]

        config["max_recording_seconds"] = int(self.max_slider.intValue())
        config["format_with_llm"] = bool(self.format_checkbox.state())

        trans_idx = self.trans_popup.indexOfSelectedItem()
        if 0 <= trans_idx < len(TRANSCRIPTION_CODES):
            config["transcription_mode"] = TRANSCRIPTION_CODES[trans_idx]

        model_idx = self.local_model_popup.indexOfSelectedItem()
        if 0 <= model_idx < len(LOCAL_MODELS):
            config["local_whisper_model"] = LOCAL_MODELS[model_idx]

        config["base_url"] = self.base_url_field.stringValue()
        return config

    # --- ObjC actions (must be real selectors for NSButton targets) ---

    def toggleApiKeyVisibility_(self, sender):
        self._api_key_visible = not self._api_key_visible
        if self._api_key_visible:
            self._api_key_plain.setStringValue_(self.api_key_field.stringValue())
            self.api_key_field.setHidden_(True)
            self._api_key_plain.setHidden_(False)
        else:
            self.api_key_field.setStringValue_(self._api_key_plain.stringValue())
            self._api_key_plain.setHidden_(True)
            self.api_key_field.setHidden_(False)

    def sliderChanged_(self, sender):
        val = int(sender.intValue())
        self.max_value_label.setStringValue_(f"{val} сек")

    def changeHotkey_(self, sender):
        if self._on_learn_hotkey:
            self._on_learn_hotkey(None)

    def cancelSettings_(self, sender):
        self.window.close()

    def saveSettings_(self, sender):
        config = self._build_config()
        if self._on_save:
            self._on_save(config)
        self.window.close()

    # --- Public API (called from app.py) ---

    @objc.python_method
    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def update_hotkey_name(self, name):
        self._hotkey_name = name
        self.hotkey_label.setStringValue_(name)
