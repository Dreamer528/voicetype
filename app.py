#!/usr/bin/env python3
"""VoiceType — fully background macOS menu bar app for AI voice dictation.

Configurable hotkey, push-to-talk or toggle mode.
Routes through proxy to avoid geo-blocks.
"""

import logging
import os
import queue
import subprocess
import sys
import threading
import time

# Fix encoding for .app bundle (no terminal = defaults to ASCII)
os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import rumps

# Log to file
LOG_FILE = os.path.expanduser("~/Library/Logs/VoiceType.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("VoiceType")

# Suppress verbose httpx/httpcore debug logs that flood the log file
for _noisy in ("httpx", "httpcore", "groq", "openai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from config import (
    load_config, save_config, init_config, APP_NAME, CONFIG_FILE,
    install_autostart, uninstall_autostart, is_autostart_installed,
)
from recorder import AudioRecorder
from transcriber import Transcriber
from inserter import insert_text, check_accessibility
from hotkey import HotkeyManager, format_hotkey_name
from overlay import RecordingOverlay
from settings_window import SettingsWindowController
from local_transcriber import LocalTranscriber, LocalFormatter

IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
LEARNING = "learning"

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")

DEFAULT_HOTKEY = {"source": "key", "key_code": 2, "modifiers": 0x60000}

# Minimum recording duration to avoid sending near-empty audio (400 errors)
MIN_RECORDING_SECONDS = 0.3

# Localized UI strings
_LABELS = {
    "ru": {"recording": "Говорите...", "processing": "Обработка..."},
    "en": {"recording": "Listening...", "processing": "Processing..."},
}


class VoiceTypeApp(rumps.App):
    def __init__(self):
        super().__init__(
            APP_NAME,
            icon=os.path.join(RESOURCES_DIR, "mic_idle.png"),
            quit_button=None,
        )

        self.config = init_config()
        self.state = IDLE
        self.recorder = AudioRecorder(
            max_seconds=self.config["max_recording_seconds"],
            on_error=lambda msg: self._run_on_main(
                lambda: rumps.notification(APP_NAME, "Ошибка", msg)
            ),
        )
        self.overlay = RecordingOverlay()
        self.transcriber = None
        self.hotkey_manager = None

        # Thread-safe queue for dispatching work to the main thread.
        # AppKit/rumps UI must only be touched from the main thread;
        # hotkey callbacks and transcription run on background threads.
        self._pending_calls = queue.Queue()
        self._main_timer = rumps.Timer(self._drain_pending, 0.05)
        self._main_timer.start()

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

        self._settings_controller = None

        self.settings_item = rumps.MenuItem(
            "Настройки...", callback=self._open_settings
        )
        self.config_path_item = rumps.MenuItem(
            "Открыть конфиг (JSON)", callback=self._open_config
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
            self.settings_item,
            self.config_path_item,
            self.quit_item,
        ]

    # --- Main-thread dispatch ---

    def _drain_pending(self, _):
        """Process queued callbacks on the main thread (called by timer)."""
        while True:
            try:
                fn = self._pending_calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                log.error("Main-thread callback error: %s", e)

    def _run_on_main(self, fn):
        """Schedule fn to run on the main thread."""
        self._pending_calls.put(fn)

    # --- Transcriber ---

    def _init_transcriber(self):
        """Initialize transcriber based on transcription_mode config."""
        mode = self.config.get("transcription_mode", "cloud")
        language = self.config.get("language", "ru")

        if mode == "local" and LocalTranscriber.is_available():
            self.transcriber = LocalTranscriber(
                model_name=self.config.get("local_whisper_model", "base"),
                language=language,
            )
            log.info("Using local transcriber (MLX-Whisper)")
            return True
        elif mode == "auto" and LocalTranscriber.is_available():
            self.transcriber = LocalTranscriber(
                model_name=self.config.get("local_whisper_model", "base"),
                language=language,
            )
            log.info("Auto mode: using local transcriber")
            return True

        # Cloud mode (default)
        api_key = self.config.get("groq_api_key", "")
        if not api_key:
            return False
        self.transcriber = Transcriber(
            api_key=api_key,
            base_url=self.config.get("base_url", ""),
            language=language,
            model=self.config.get("model", "whisper-large-v3"),
            llm_model=self.config.get("llm_model", "llama-3.3-70b-versatile"),
        )
        log.info("Using cloud transcriber (Groq)")
        return True

    def _init_local_formatter(self):
        """Initialize local LLM formatter if configured."""
        fmt_mode = self.config.get("formatting_mode", "cloud")
        if fmt_mode == "local" and LocalFormatter.is_available():
            if not hasattr(self, '_local_formatter') or self._local_formatter is None:
                self._local_formatter = LocalFormatter(
                    model_name=self.config.get("local_llm_model",
                                                "mlx-community/Phi-4-mini-instruct-4bit"),
                )
            return True
        return False

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

        lang = self.config.get("language", "ru")
        labels = _LABELS.get(lang, _LABELS["ru"])

        # Animated overlay
        if state == RECORDING:
            self.overlay.show_recording(labels["recording"])
            self._start_duration_timer()
        elif state == PROCESSING:
            self._stop_duration_timer()
            self.overlay.show_processing(labels["processing"])
        else:
            self._stop_duration_timer()
            self.overlay.hide()

    # --- Duration timer ---

    def _start_duration_timer(self):
        self._rec_start_time = time.time()
        self.overlay.set_duration("0:00")
        self._duration_timer = rumps.Timer(self._update_duration, 1.0)
        self._duration_timer.start()

    def _stop_duration_timer(self):
        if hasattr(self, '_duration_timer') and self._duration_timer:
            self._duration_timer.stop()
            self._duration_timer = None

    def _update_duration(self, _):
        elapsed = int(time.time() - self._rec_start_time)
        mins, secs = divmod(elapsed, 60)
        self.overlay.set_duration(f"{mins}:{secs:02d}")
        # Update amplitude from recorder
        amp = self.recorder.get_current_amplitude()
        self.overlay.set_amplitude(amp)

    # --- Hotkey learning ---

    def _start_learning(self, _):
        """User clicked 'Set hotkey' — wait for next key press."""
        if self.state == RECORDING:
            return
        self._set_state(LEARNING)
        self.hotkey_manager.start_learning()
        self._learn_start_time = time.time()
        # Poll from main thread every 0.2s
        self._learn_timer = rumps.Timer(self._poll_learned, 0.2)
        self._learn_timer.start()

    def _poll_learned(self, timer):
        """Check if a key was pressed in learn mode (runs on main thread)."""
        # Auto-cancel after 10 seconds
        if time.time() - self._learn_start_time > 10.0:
            timer.stop()
            self.hotkey_manager.cancel_learning()
            log.info("Learn mode timed out")
            self._set_state(IDLE)
            return

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

    def _open_settings(self, _):
        """Open native settings window."""
        hotkey_name = format_hotkey_name(self.config.get("hotkey", DEFAULT_HOTKEY))
        self._settings_controller = SettingsWindowController(
            config=self.config,
            on_save=self._on_settings_saved,
            on_learn_hotkey=self._start_learning,
            hotkey_name=hotkey_name,
        )
        self._settings_controller.show()

    def _on_settings_saved(self, new_config):
        """Called when user clicks Save in settings window."""
        self.config = new_config
        save_config(self.config)
        self.recorder.max_seconds = self.config["max_recording_seconds"]
        self._init_transcriber()
        self._update_mode_menu(self.config.get("mode", "push_to_talk"))
        self._update_lang_menu()
        self.format_toggle.state = self.config.get("format_with_llm", True)
        hotkey_cfg = self.config.get("hotkey", DEFAULT_HOTKEY)
        self.hotkey_manager.set_hotkey(hotkey_cfg)
        name = format_hotkey_name(hotkey_cfg)
        self.set_hotkey_item.title = f"Хоткей: {name}  [изменить]"
        self._set_state(IDLE)
        log.info("Настройки сохранены")

    def _open_config(self, _):
        subprocess.run(["open", "-a", "TextEdit", CONFIG_FILE])

    def _reload_config(self, _):
        """Reload config from disk and apply changes."""
        self.config = load_config()
        self.recorder.max_seconds = self.config["max_recording_seconds"]
        self._init_transcriber()
        self._update_mode_menu(self.config.get("mode", "push_to_talk"))
        self._update_lang_menu()
        self.format_toggle.state = self.config.get("format_with_llm", True)
        hotkey_cfg = self.config.get("hotkey", DEFAULT_HOTKEY)
        self.hotkey_manager.set_hotkey(hotkey_cfg)
        name = format_hotkey_name(hotkey_cfg)
        self.set_hotkey_item.title = f"Хоткей: {name}  [изменить]"
        self._set_state(IDLE)
        log.info("Конфиг перезагружен")

    # --- Push-to-talk / Toggle callbacks ---

    def _on_activate(self):
        """Hotkey pressed (called from CGEventTap thread)."""
        # Dispatch to main thread — UI calls are not thread-safe
        self._run_on_main(self._do_activate)

    def _do_activate(self):
        """Actual activation logic, runs on main thread."""
        if self.state == LEARNING:
            return

        mode = self.config.get("mode", "push_to_talk")

        if mode == "toggle":
            if self.state == RECORDING:
                self._stop_and_process()
                return
            elif self.state == PROCESSING:
                return
            if not self.transcriber:
                rumps.notification(APP_NAME, "API ключ не задан",
                                   "Откройте настройки и укажите Groq API ключ")
                return
            self._set_state(RECORDING)
            self.recorder.start_recording()
        else:
            if self.state != IDLE:
                return
            if not self.transcriber:
                rumps.notification(APP_NAME, "API ключ не задан",
                                   "Откройте настройки и укажите Groq API ключ")
                return
            self._set_state(RECORDING)
            self.recorder.start_recording()

    def _on_deactivate(self):
        """Hotkey released (called from CGEventTap thread)."""
        self._run_on_main(self._do_deactivate)

    def _do_deactivate(self):
        """Actual deactivation logic, runs on main thread."""
        mode = self.config.get("mode", "push_to_talk")
        if mode == "push_to_talk" and self.state == RECORDING:
            self._stop_and_process()

    def _on_cancel(self):
        """Esc pressed (called from CGEventTap thread)."""
        self._run_on_main(self._do_cancel)

    def _do_cancel(self):
        """Cancel current recording without transcribing."""
        if self.state == RECORDING:
            audio_path = self.recorder.stop_recording()
            if audio_path:
                self.recorder.cleanup_file(audio_path)
            log.info("Запись отменена (Esc)")
            self._set_state(IDLE)
        elif self.state == LEARNING:
            if hasattr(self, '_learn_timer') and self._learn_timer:
                self._learn_timer.stop()
            self.hotkey_manager.cancel_learning()
            self._set_state(IDLE)

    def _stop_and_process(self):
        """Stop recording and start transcription."""
        duration = self.recorder.get_duration()
        audio_path = self.recorder.stop_recording()
        if not audio_path:
            log.info("Нет аудио данных")
            self._set_state(IDLE)
            return
        if duration < MIN_RECORDING_SECONDS:
            log.info("Запись слишком короткая (%.2fs), пропускаю", duration)
            self.recorder.cleanup_file(audio_path)
            self._set_state(IDLE)
            return
        log.info("Аудио сохранено: %s (%.1fs)", audio_path, duration)
        self._set_state(PROCESSING)
        thread = threading.Thread(
            target=self._process_audio,
            args=(audio_path,),
            daemon=True,
        )
        thread.start()

    def _process_audio(self, audio_path):
        """Runs on a background thread — must not touch UI directly."""
        try:
            if not self.transcriber:
                log.error("Transcriber не инициализирован (нет API ключа?)")
                return

            # Step 1: Transcribe
            log.info("Отправляю на транскрипцию...")
            raw_text = self.transcriber.transcribe(audio_path)

            # Step 2: Format
            fmt_mode = self.config.get("formatting_mode", "cloud")
            if not self.config.get("format_with_llm", True) or fmt_mode == "off":
                text = raw_text
            elif fmt_mode == "local" and LocalFormatter.is_available():
                if not hasattr(self, '_local_formatter') or self._local_formatter is None:
                    self._init_local_formatter()
                if self._local_formatter:
                    text = self._local_formatter.format_text(
                        raw_text, self.config.get("language", "ru"))
                else:
                    text = raw_text
            elif hasattr(self.transcriber, 'format_text'):
                text = self.transcriber.format_text(raw_text)
            else:
                text = raw_text

            log.info("Результат: %s", text[:100])
            insert_text(text)
            log.info("Текст вставлен")
        except Exception as e:
            log.error("Ошибка транскрипции: %s", e)
            error_msg = str(e)[:100]
            self._run_on_main(lambda: rumps.notification(
                APP_NAME, "Ошибка транскрипции", error_msg
            ))
        finally:
            self.transcriber.cleanup(audio_path)
            self._run_on_main(lambda: self._set_state(IDLE))

    # --- Lifecycle ---

    def _quit(self, _):
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        rumps.quit_application()

    @staticmethod
    def _check_microphone_permission():
        """Check microphone permission on macOS."""
        try:
            import AVFoundation
            status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
                AVFoundation.AVMediaTypeAudio
            )
            if status == 0:  # AVAuthorizationStatusNotDetermined
                AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVFoundation.AVMediaTypeAudio, lambda granted: None
                )
            elif status == 2:  # AVAuthorizationStatusDenied
                rumps.notification(
                    APP_NAME, "Нет доступа к микрофону",
                    "Откройте Системные настройки → Конфиденциальность → Микрофон",
                )
        except Exception as e:
            log.warning("Could not check microphone permission: %s", e)

    def run(self, **kwargs):
        # Check Accessibility — opens settings page if not granted
        if not check_accessibility():
            log.warning("Нет разрешения 'Универсальный доступ'")
            rumps.notification(
                APP_NAME, "Нужны разрешения",
                "Добавьте VoiceType в 'Универсальный доступ' и 'Мониторинг ввода', затем перезапустите",
            )

        # Check microphone permission
        self._check_microphone_permission()

        self.config = load_config()

        if not self.config.get("groq_api_key"):
            self.status_item.title = "API ключ не задан → Открыть конфиг"
        else:
            self._init_transcriber()

        hotkey_cfg = self.config.get("hotkey", DEFAULT_HOTKEY)
        self.hotkey_manager = HotkeyManager(
            on_activate=self._on_activate,
            on_deactivate=self._on_deactivate,
            on_cancel=self._on_cancel,
            hotkey_cfg=hotkey_cfg,
        )
        self.hotkey_manager.start()

        super().run(**kwargs)


def main():
    app = VoiceTypeApp()
    app.run()


if __name__ == "__main__":
    main()
