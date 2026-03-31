#!/usr/bin/env python3
"""VoiceType — fully background macOS menu bar app for AI voice dictation.

Configurable hotkey, push-to-talk or toggle mode.
Routes through proxy to avoid geo-blocks.
"""

import logging
import os
import sys
import threading

# Fix encoding for .app bundle (no terminal = defaults to ASCII)
os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import rumps

# Log to file
LOG_FILE = os.path.expanduser("~/Library/Logs/VoiceType.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("VoiceType")

from config import (
    load_config, save_config, init_config, APP_NAME, CONFIG_FILE,
    install_autostart, uninstall_autostart, is_autostart_installed,
)
from recorder import AudioRecorder
from transcriber import Transcriber
from inserter import insert_text, check_accessibility
from hotkey import HotkeyManager, format_hotkey_name
from overlay import RecordingOverlay

IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
LEARNING = "learning"

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")

DEFAULT_HOTKEY = {"source": "key", "key_code": 2, "modifiers": 0x60000}


class VoiceTypeApp(rumps.App):
    def __init__(self):
        super().__init__(
            APP_NAME,
            icon=os.path.join(RESOURCES_DIR, "mic_idle.png"),
            quit_button=None,
        )

        self.config = init_config()
        self.state = IDLE
        self.recorder = AudioRecorder(max_seconds=self.config["max_recording_seconds"])
        self.overlay = RecordingOverlay()
        self.transcriber = None
        self.hotkey_manager = None

        # Hotkey display name
        hotkey_cfg = self.config.get("hotkey", DEFAULT_HOTKEY)
        hotkey_name = format_hotkey_name(hotkey_cfg)

        # Build menu
        self.status_item = rumps.MenuItem(f"Готов  |  {hotkey_name}")
        self.status_item.set_callback(None)

        # Hotkey settings
        self.set_hotkey_item = rumps.MenuItem(
            f"Хоткей: {hotkey_name}  [изменить]", callback=self._start_learning
        )

        # Mode: push-to-talk vs toggle
        mode = self.config.get("mode", "push_to_talk")
        self.mode_hold = rumps.MenuItem("Зажать и говорить", callback=self._set_mode_hold)
        self.mode_toggle = rumps.MenuItem(
            "Нажать → говорить → нажать", callback=self._set_mode_toggle
        )
        self._update_mode_menu(mode)

        # Language
        self.lang_ru = rumps.MenuItem("Русский", callback=self._set_lang_ru)
        self.lang_en = rumps.MenuItem("English", callback=self._set_lang_en)
        self._update_lang_menu()

        # LLM formatting
        self.format_toggle = rumps.MenuItem(
            "LLM форматирование", callback=self._toggle_format
        )
        self.format_toggle.state = self.config.get("format_with_llm", True)

        # Autostart
        self.autostart_toggle = rumps.MenuItem(
            "Запуск при входе", callback=self._toggle_autostart
        )
        self.autostart_toggle.state = is_autostart_installed()

        self.config_path_item = rumps.MenuItem(
            "Открыть конфиг", callback=self._open_config
        )
        self.quit_item = rumps.MenuItem("Выход", callback=self._quit)

        self.menu = [
            self.status_item,
            None,
            self.set_hotkey_item,
            [rumps.MenuItem("Режим"), [self.mode_hold, self.mode_toggle]],
            [rumps.MenuItem("Язык"), [self.lang_ru, self.lang_en]],
            self.format_toggle,
            self.autostart_toggle,
            None,
            self.config_path_item,
            self.quit_item,
        ]

    # --- Transcriber ---

    def _init_transcriber(self):
        api_key = self.config.get("groq_api_key", "")
        if not api_key:
            return False
        self.transcriber = Transcriber(
            api_key=api_key,
            base_url=self.config.get("base_url", ""),
            language=self.config.get("language", "ru"),
            model=self.config.get("model", "whisper-large-v3"),
            llm_model=self.config.get("llm_model", "llama-3.3-70b-versatile"),
        )
        return True

    # --- State ---

    def _set_state(self, state):
        self.state = state
        icon_map = {
            IDLE: "mic_idle.png",
            RECORDING: "mic_recording.png",
            PROCESSING: "mic_processing.png",
            LEARNING: "mic_processing.png",
        }
        hotkey_name = format_hotkey_name(self.config.get("hotkey", DEFAULT_HOTKEY))
        status_map = {
            IDLE: f"Готов  |  {hotkey_name}",
            RECORDING: "Запись...",
            PROCESSING: "Обработка...",
            LEARNING: "Нажмите нужную клавишу...",
        }
        icon_path = os.path.join(RESOURCES_DIR, icon_map[state])
        if os.path.exists(icon_path):
            self.icon = icon_path
        self.status_item.title = status_map[state]

        # Animated overlay
        if state == RECORDING:
            self.overlay.show_recording()
        elif state == PROCESSING:
            self.overlay.show_processing()
        else:
            self.overlay.hide()

    # --- Hotkey learning ---

    def _start_learning(self, _):
        """User clicked 'Set hotkey' — wait for next key press."""
        if self.state == RECORDING:
            return
        self._set_state(LEARNING)
        self.hotkey_manager.start_learning()
        # Poll from main thread every 0.2s
        self._learn_timer = rumps.Timer(self._poll_learned, 0.2)
        self._learn_timer.start()

    def _poll_learned(self, timer):
        """Check if a key was pressed in learn mode (runs on main thread)."""
        cfg = self.hotkey_manager.get_learned_result()
        if cfg is not None:
            timer.stop()
            self.config["hotkey"] = cfg
            save_config(self.config)
            self.hotkey_manager.set_hotkey(cfg)

            name = format_hotkey_name(cfg)
            self.set_hotkey_item.title = f"Хоткей: {name}  [изменить]"
            self._set_state(IDLE)

    # --- Mode ---

    def _set_mode_hold(self, _):
        self.config["mode"] = "push_to_talk"
        save_config(self.config)
        self._update_mode_menu("push_to_talk")

    def _set_mode_toggle(self, _):
        self.config["mode"] = "toggle"
        save_config(self.config)
        self._update_mode_menu("toggle")

    def _update_mode_menu(self, mode):
        self.mode_hold.state = mode == "push_to_talk"
        self.mode_toggle.state = mode == "toggle"

    # --- Language ---

    def _set_lang_ru(self, _):
        self.config["language"] = "ru"
        save_config(self.config)
        self._update_lang_menu()
        if self.transcriber:
            self.transcriber.language = "ru"

    def _set_lang_en(self, _):
        self.config["language"] = "en"
        save_config(self.config)
        self._update_lang_menu()
        if self.transcriber:
            self.transcriber.language = "en"

    def _update_lang_menu(self):
        lang = self.config.get("language", "ru")
        self.lang_ru.state = lang == "ru"
        self.lang_en.state = lang == "en"

    # --- Other settings ---

    def _toggle_format(self, sender):
        sender.state = not sender.state
        self.config["format_with_llm"] = bool(sender.state)
        save_config(self.config)

    def _toggle_autostart(self, sender):
        if is_autostart_installed():
            uninstall_autostart()
            sender.state = False
        else:
            install_autostart()
            sender.state = True

    def _open_config(self, _):
        os.system(f'open -a TextEdit "{CONFIG_FILE}"')

    # --- Push-to-talk / Toggle callbacks ---

    def _on_activate(self):
        """Hotkey pressed."""
        if self.state == LEARNING:
            return

        mode = self.config.get("mode", "push_to_talk")

        if mode == "toggle":
            # Toggle: if recording — stop; if idle — start
            if self.state == RECORDING:
                self._stop_and_process()
                return
            elif self.state == PROCESSING:
                return
            # Start recording
            if not self.transcriber:
                return
            self._set_state(RECORDING)
            self.recorder.start_recording()
        else:
            # Push-to-talk: start on press
            if self.state != IDLE:
                return
            if not self.transcriber:
                return
            self._set_state(RECORDING)
            self.recorder.start_recording()

    def _on_deactivate(self):
        """Hotkey released (only matters in push-to-talk mode)."""
        mode = self.config.get("mode", "push_to_talk")
        if mode == "push_to_talk" and self.state == RECORDING:
            self._stop_and_process()

    def _stop_and_process(self):
        """Stop recording and start transcription."""
        audio_path = self.recorder.stop_recording()
        if not audio_path:
            print("[VoiceType] Нет аудио данных")
            self._set_state(IDLE)
            return
        print(f"[VoiceType] Аудио сохранено: {audio_path}")
        self._set_state(PROCESSING)
        thread = threading.Thread(
            target=self._process_audio,
            args=(audio_path,),
            daemon=True,
        )
        thread.start()

    def _process_audio(self, audio_path):
        try:
            if not self.transcriber:
                print("[VoiceType] ОШИБКА: transcriber не инициализирован (нет API ключа?)")
                return
            print("[VoiceType] Отправляю на транскрипцию...")
            if self.config.get("format_with_llm", True):
                text = self.transcriber.transcribe_and_format(audio_path)
            else:
                text = self.transcriber.transcribe(audio_path)
            print(f"[VoiceType] Результат: {text[:100]}...")
            insert_text(text)
            print("[VoiceType] Текст вставлен")
        except Exception as e:
            print(f"[VoiceType] ОШИБКА: {e}")
        finally:
            self.transcriber.cleanup(audio_path)
            self._set_state(IDLE)

    # --- Lifecycle ---

    def _quit(self, _):
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        rumps.quit_application()

    def run(self, **kwargs):
        # Check Accessibility — opens settings page if not granted
        if not check_accessibility():
            log.warning("Нет разрешения 'Универсальный доступ'")
            rumps.notification(
                APP_NAME, "Нужны разрешения",
                "Добавьте VoiceType в 'Универсальный доступ' и 'Мониторинг ввода', затем перезапустите",
            )

        self.config = load_config()

        if not self.config.get("groq_api_key"):
            self.status_item.title = "API ключ не задан → Открыть конфиг"
        else:
            self._init_transcriber()

        hotkey_cfg = self.config.get("hotkey", DEFAULT_HOTKEY)
        self.hotkey_manager = HotkeyManager(
            on_activate=self._on_activate,
            on_deactivate=self._on_deactivate,
            hotkey_cfg=hotkey_cfg,
        )
        self.hotkey_manager.start()

        super().run(**kwargs)


def main():
    app = VoiceTypeApp()
    app.run()


if __name__ == "__main__":
    main()
