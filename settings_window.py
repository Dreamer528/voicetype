"""Native macOS settings window for VoiceType with tabbed interface."""

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
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
    NSTabView,
    NSTabViewItem,
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

def _label(text, width=140):
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
    stack = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 30))
    stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    stack.setSpacing_(spacing)
    stack.setAlignment_(0x200)  # CenterY
    for v in views:
        stack.addView_inGravity_(v, 1)
    return stack


def _vstack(spacing=10):
    stack = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, 400))
    stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
    stack.setSpacing_(spacing)
    stack.setAlignment_(0x100)  # Leading
    stack.setEdgeInsets_((16, 16, 16, 16))
    return stack


def _separator(width=420):
    sep = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 1))
    sep.setWantsLayer_(True)
    sep.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
    return sep


def _section_header(text):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 20))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.boldSystemFontOfSize_(13))
    field.setTextColor_(NSColor.secondaryLabelColor())
    return field


def _description(text):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 16))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.systemFontOfSize_(11))
    field.setTextColor_(NSColor.tertiaryLabelColor())
    field.setLineBreakMode_(0)
    return field


# Constants
LANGUAGES = ["Русский", "English"]
LANG_CODES = ["ru", "en"]
MODES = ["Зажать и говорить", "Нажать → говорить → нажать"]
MODE_CODES = ["push_to_talk", "toggle"]
TRANSCRIPTION_MODES = ["Облако (Groq)", "Локальный (MLX)", "Авто"]
TRANSCRIPTION_CODES = ["cloud", "local", "auto"]
LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
QA_LENGTHS = ["Короткий", "Средний", "Развёрнутый"]
QA_LENGTH_CODES = ["short", "medium", "long"]


class SettingsWindowController(NSObject):

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
        self._or_key_visible = False
        self._build_window()
        self._populate_from_config()
        return self

    @objc.python_method
    def _build_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 520, 520), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("VoiceType — Настройки")
        self.window.center()
        self.window.setReleasedWhenClosed_(False)

        # Tab view
        tab_view = NSTabView.alloc().initWithFrame_(NSMakeRect(0, 50, 520, 470))

        # Tab 1: General
        tab1 = NSTabViewItem.alloc().initWithIdentifier_("general")
        tab1.setLabel_("Основные")
        tab1.setView_(self._build_general_tab())
        tab_view.addTabViewItem_(tab1)

        # Tab 2: Modes
        tab2 = NSTabViewItem.alloc().initWithIdentifier_("modes")
        tab2.setLabel_("Режимы")
        tab2.setView_(self._build_modes_tab())
        tab_view.addTabViewItem_(tab2)

        # Tab 3: Commands
        tab3 = NSTabViewItem.alloc().initWithIdentifier_("commands")
        tab3.setLabel_("Команды")
        tab3.setView_(self._build_commands_tab())
        tab_view.addTabViewItem_(tab3)

        # Bottom buttons
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 520))
        content.addSubview_(tab_view)

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(310, 12, 90, 30))
        cancel_btn.setTitle_("Отмена")
        cancel_btn.setBezelStyle_(NSBezelStyleRounded)
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancelSettings:")
        content.addSubview_(cancel_btn)

        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(410, 12, 90, 30))
        save_btn.setTitle_("Сохранить")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("saveSettings:")
        content.addSubview_(save_btn)

        self.window.setContentView_(content)

    @objc.python_method
    def _build_general_tab(self):
        stack = _vstack(spacing=8)

        # API key
        stack.addView_inGravity_(_section_header("Groq API"), 1)
        self.api_key_field = _secure_field("gsk_...", width=230)
        show_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 30, 22))
        show_btn.setTitle_("👁")
        show_btn.setBezelStyle_(NSBezelStyleRounded)
        show_btn.setTarget_(self)
        show_btn.setAction_("toggleApiKeyVisibility:")
        self._api_key_plain = _text_field("gsk_...", width=230)
        self._api_key_plain.setHidden_(True)
        stack.addView_inGravity_(
            _hstack(_label("API ключ:"), self.api_key_field, self._api_key_plain, show_btn), 1
        )

        self.base_url_field = _text_field("https://...", width=250)
        stack.addView_inGravity_(_hstack(_label("Base URL:"), self.base_url_field), 1)

        stack.addView_inGravity_(_separator(), 1)

        # Language & mode
        stack.addView_inGravity_(_section_header("Общие"), 1)

        self.lang_popup = _popup(LANGUAGES)
        stack.addView_inGravity_(_hstack(_label("Язык:"), self.lang_popup), 1)

        self.mode_popup = _popup(MODES)
        stack.addView_inGravity_(_hstack(_label("Режим записи:"), self.mode_popup), 1)

        # Hotkey
        self.hotkey_label = _text_field("", width=150)
        self.hotkey_label.setEditable_(False)
        self.hotkey_label.setSelectable_(False)
        change_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 90, 24))
        change_btn.setTitle_("Изменить")
        change_btn.setBezelStyle_(NSBezelStyleRounded)
        change_btn.setTarget_(self)
        change_btn.setAction_("changeHotkey:")
        stack.addView_inGravity_(_hstack(_label("Хоткей:"), self.hotkey_label, change_btn), 1)

        # Max recording
        self.max_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(0, 0, 180, 22))
        self.max_slider.setMinValue_(10)
        self.max_slider.setMaxValue_(300)
        self.max_slider.setContinuous_(True)
        self.max_slider.setTarget_(self)
        self.max_slider.setAction_("sliderChanged:")
        self.max_value_label = _label("120 сек", width=60)
        self.max_value_label.setAlignment_(0)
        stack.addView_inGravity_(
            _hstack(_label("Макс. запись:"), self.max_slider, self.max_value_label), 1
        )

        # LLM formatting
        self.format_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 280, 22))
        self.format_checkbox.setButtonType_(NSButtonTypeSwitch)
        self.format_checkbox.setTitle_("LLM форматирование диктовки")
        self.format_checkbox.setFont_(NSFont.systemFontOfSize_(13))
        stack.addView_inGravity_(_hstack(_label(""), self.format_checkbox), 1)

        return stack

    @objc.python_method
    def _build_modes_tab(self):
        stack = _vstack(spacing=8)

        # --- Dictation ---
        stack.addView_inGravity_(_section_header("🎤  Диктовка  (Opt+Space)"), 1)

        self.dict_trans_popup = _popup(TRANSCRIPTION_MODES)
        stack.addView_inGravity_(_hstack(_label("Транскрипция:"), self.dict_trans_popup), 1)

        self.local_model_popup = _popup(LOCAL_MODELS)
        stack.addView_inGravity_(_hstack(_label("Модель (Local):"), self.local_model_popup), 1)

        stack.addView_inGravity_(_separator(), 1)

        # --- Q&A ---
        stack.addView_inGravity_(_section_header("💬  Q&A  (Ctrl+Opt+Space)"), 1)

        self.qa_trans_popup = _popup(TRANSCRIPTION_MODES)
        stack.addView_inGravity_(_hstack(_label("Транскрипция:"), self.qa_trans_popup), 1)

        self.openrouter_key_field = _secure_field("sk-or-v1-...", width=220)
        or_show_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 30, 22))
        or_show_btn.setTitle_("👁")
        or_show_btn.setBezelStyle_(NSBezelStyleRounded)
        or_show_btn.setTarget_(self)
        or_show_btn.setAction_("toggleOrKeyVisibility:")
        self._or_key_plain = _text_field("sk-or-v1-...", width=220)
        self._or_key_plain.setHidden_(True)
        stack.addView_inGravity_(
            _hstack(_label("OpenRouter:"), self.openrouter_key_field, self._or_key_plain, or_show_btn), 1
        )

        self.qa_length_popup = _popup(QA_LENGTHS)
        stack.addView_inGravity_(_hstack(_label("Длина ответа:"), self.qa_length_popup), 1)

        stack.addView_inGravity_(_separator(), 1)

        # --- Agent ---
        stack.addView_inGravity_(_section_header("🤖  Агент  (Opt+Cmd+Space)"), 1)

        self.agent_trans_popup = _popup(TRANSCRIPTION_MODES)
        stack.addView_inGravity_(_hstack(_label("Транскрипция:"), self.agent_trans_popup), 1)

        self.agent_voice_cb = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 280, 22))
        self.agent_voice_cb.setButtonType_(NSButtonTypeSwitch)
        self.agent_voice_cb.setTitle_("Голосовой ответ")
        self.agent_voice_cb.setFont_(NSFont.systemFontOfSize_(13))
        stack.addView_inGravity_(_hstack(_label(""), self.agent_voice_cb), 1)

        stack.addView_inGravity_(_description(
            "Хоткеи: Opt+Space диктовка, Ctrl+Opt+Space вопрос, Opt+Cmd+Space агент"
        ), 1)

        return stack

    @objc.python_method
    def _build_commands_tab(self):
        stack = _vstack(spacing=8)

        stack.addView_inGravity_(_section_header("Голосовые команды агента"), 1)
        stack.addView_inGravity_(_description(
            "Кастомные команды выполняются мгновенно без LLM."
        ), 1)

        # Add command
        self._commands_field = _text_field("Триггер: рабочий режим", width=280)
        add_cmd_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 30, 22))
        add_cmd_btn.setTitle_("+")
        add_cmd_btn.setBezelStyle_(NSBezelStyleRounded)
        add_cmd_btn.setTarget_(self)
        add_cmd_btn.setAction_("addCommand:")
        stack.addView_inGravity_(_hstack(_label("Триггер:"), self._commands_field, add_cmd_btn), 1)

        self._commands_actions = _text_field("open_app:Telegram, toggle_dnd", width=280)
        stack.addView_inGravity_(_hstack(_label("Действия:"), self._commands_actions), 1)

        stack.addView_inGravity_(_description(
            "Формат: action:param, action:param  |  Пример: open_app:Safari, set_volume:30"
        ), 1)

        stack.addView_inGravity_(_separator(), 1)

        # Existing commands
        stack.addView_inGravity_(_section_header("Текущие команды"), 1)

        self._commands_list_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 80))
        self._commands_list_label.setBezeled_(False)
        self._commands_list_label.setDrawsBackground_(False)
        self._commands_list_label.setEditable_(False)
        self._commands_list_label.setSelectable_(True)
        self._commands_list_label.setFont_(NSFont.systemFontOfSize_(12))
        self._commands_list_label.setTextColor_(NSColor.secondaryLabelColor())
        self._commands_list_label.setLineBreakMode_(0)
        stack.addView_inGravity_(self._commands_list_label, 1)

        del_cmd_btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 150, 24))
        del_cmd_btn.setTitle_("Удалить все")
        del_cmd_btn.setBezelStyle_(NSBezelStyleRounded)
        del_cmd_btn.setTarget_(self)
        del_cmd_btn.setAction_("clearCommands:")
        stack.addView_inGravity_(_hstack(_label(""), del_cmd_btn), 1)

        stack.addView_inGravity_(_separator(), 1)

        stack.addView_inGravity_(_description(
            "Доступные действия: open_app, close_app, set_volume, mute, "
            "search_web, search_youtube, open_url, toggle_dark_mode, "
            "toggle_dnd, toggle_wifi, toggle_bluetooth, screenshot, "
            "lock_screen, music_control, say, create_reminder, "
            "show_downloads, empty_trash, open_settings, ticktick_add"
        ), 1)

        return stack

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

        self.base_url_field.setStringValue_(self._config.get("base_url", ""))

        # Per-mode transcription
        dict_trans = self._config.get("dictation_transcription",
                                       self._config.get("transcription_mode", "cloud"))
        self.dict_trans_popup.selectItemAtIndex_(
            TRANSCRIPTION_CODES.index(dict_trans) if dict_trans in TRANSCRIPTION_CODES else 0
        )

        local_model = self._config.get("local_whisper_model", "base")
        self.local_model_popup.selectItemAtIndex_(
            LOCAL_MODELS.index(local_model) if local_model in LOCAL_MODELS else 1
        )

        qa_trans = self._config.get("qa_transcription", "local")
        self.qa_trans_popup.selectItemAtIndex_(
            TRANSCRIPTION_CODES.index(qa_trans) if qa_trans in TRANSCRIPTION_CODES else 0
        )

        or_key = self._config.get("openrouter_api_key", "")
        self.openrouter_key_field.setStringValue_(or_key)
        self._or_key_plain.setStringValue_(or_key)

        qa_len = self._config.get("qa_answer_length", "short")
        self.qa_length_popup.selectItemAtIndex_(
            QA_LENGTH_CODES.index(qa_len) if qa_len in QA_LENGTH_CODES else 0
        )

        agent_trans = self._config.get("agent_transcription", "local")
        self.agent_trans_popup.selectItemAtIndex_(
            TRANSCRIPTION_CODES.index(agent_trans) if agent_trans in TRANSCRIPTION_CODES else 0
        )

        self.agent_voice_cb.setState_(
            1 if self._config.get("agent_voice_feedback", True) else 0
        )

        self._update_commands_display()

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
        config["base_url"] = self.base_url_field.stringValue()

        # Per-mode transcription
        dict_idx = self.dict_trans_popup.indexOfSelectedItem()
        if 0 <= dict_idx < len(TRANSCRIPTION_CODES):
            config["dictation_transcription"] = TRANSCRIPTION_CODES[dict_idx]
            config["transcription_mode"] = TRANSCRIPTION_CODES[dict_idx]

        model_idx = self.local_model_popup.indexOfSelectedItem()
        if 0 <= model_idx < len(LOCAL_MODELS):
            config["local_whisper_model"] = LOCAL_MODELS[model_idx]

        qa_idx = self.qa_trans_popup.indexOfSelectedItem()
        if 0 <= qa_idx < len(TRANSCRIPTION_CODES):
            config["qa_transcription"] = TRANSCRIPTION_CODES[qa_idx]

        if self._or_key_visible:
            config["openrouter_api_key"] = self._or_key_plain.stringValue()
        else:
            config["openrouter_api_key"] = self.openrouter_key_field.stringValue()

        qa_len_idx = self.qa_length_popup.indexOfSelectedItem()
        if 0 <= qa_len_idx < len(QA_LENGTH_CODES):
            config["qa_answer_length"] = QA_LENGTH_CODES[qa_len_idx]

        agent_idx = self.agent_trans_popup.indexOfSelectedItem()
        if 0 <= agent_idx < len(TRANSCRIPTION_CODES):
            config["agent_transcription"] = TRANSCRIPTION_CODES[agent_idx]

        config["agent_voice_feedback"] = bool(self.agent_voice_cb.state())
        return config

    # --- ObjC actions ---

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

    def toggleOrKeyVisibility_(self, sender):
        self._or_key_visible = not self._or_key_visible
        if self._or_key_visible:
            self._or_key_plain.setStringValue_(self.openrouter_key_field.stringValue())
            self.openrouter_key_field.setHidden_(True)
            self._or_key_plain.setHidden_(False)
        else:
            self.openrouter_key_field.setStringValue_(self._or_key_plain.stringValue())
            self._or_key_plain.setHidden_(True)
            self.openrouter_key_field.setHidden_(False)

    def addCommand_(self, sender):
        trigger = self._commands_field.stringValue().strip()
        actions_str = self._commands_actions.stringValue().strip()
        if not trigger or not actions_str:
            return
        actions = []
        for part in actions_str.split(","):
            part = part.strip()
            if ":" in part:
                name, param = part.split(":", 1)
                name, param = name.strip(), param.strip()
                param_key = {
                    "open_app": "app_name", "close_app": "app_name",
                    "switch_app": "app_name", "set_volume": "level",
                    "search_web": "query", "search_youtube": "query",
                    "open_url": "url", "open_folder": "path",
                    "say": "text", "timer": "seconds",
                    "create_reminder": "title", "toggle_wifi": "state",
                    "toggle_bluetooth": "state", "music_control": "action",
                    "open_settings": "section", "system_info": "info_type",
                    "ticktick_add": "title",
                }.get(name, "value")
                try:
                    param = int(param)
                except ValueError:
                    pass
                actions.append({"action": name, "params": {param_key: param}})
            else:
                actions.append({"action": part, "params": {}})
        if actions:
            cmds = self._config.get("custom_commands", {})
            cmds[trigger] = actions
            self._config["custom_commands"] = cmds
            self._commands_field.setStringValue_("")
            self._commands_actions.setStringValue_("")
            self._update_commands_display()

    def clearCommands_(self, sender):
        self._config["custom_commands"] = {}
        self._update_commands_display()

    @objc.python_method
    def _update_commands_display(self):
        cmds = self._config.get("custom_commands", {})
        if not cmds:
            self._commands_list_label.setStringValue_("Нет кастомных команд")
            return
        lines = []
        for trigger, actions in cmds.items():
            acts = ", ".join(
                f"{a['action']}:{list(a.get('params',{}).values())[0]}"
                if a.get("params") else a["action"]
                for a in actions
            )
            lines.append(f'  "{trigger}"  →  {acts}')
        self._commands_list_label.setStringValue_("\n".join(lines))

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

    # --- Public API ---

    @objc.python_method
    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def update_hotkey_name(self, name):
        self._hotkey_name = name
        self.hotkey_label.setStringValue_(name)
