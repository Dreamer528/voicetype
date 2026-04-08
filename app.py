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

# In .app bundle, __boot__.py removes RESOURCEPATH from sys.path so that
# python39.zip takes priority. We put RESOURCEPATH back at front so our
# patched .py files in Resources/ override the old compiled .pyc in the zip.
_rp = os.environ.get("RESOURCEPATH", "")
if _rp and _rp not in sys.path:
    sys.path.insert(0, _rp)

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
    load_config, save_config, init_config, APP_NAME,
    install_autostart, uninstall_autostart, is_autostart_installed,
)
from recorder import AudioRecorder
from transcriber import Transcriber
from inserter import insert_text, check_accessibility, get_app_context
from hotkey import HotkeyManager, format_hotkey_name
from overlay import RecordingOverlay
from history import add_entry, load_history, truncate_text, clear_history
from settings_window import SettingsWindowController
from local_transcriber import LocalTranscriber
from answer_window import AnswerWindow
from agent import _build_system_prompt, execute_action, parse_llm_response

IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
LEARNING = "learning"

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")


def _play_sound(name):
    """Play a system sound asynchronously."""
    path = f"/System/Library/Sounds/{name}.aiff"
    if os.path.exists(path):
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_haptic_warned = False


def _haptic():
    """Trigger haptic feedback on Macs with Force Touch trackpad.

    NOTE: only works when the user has a Force Touch trackpad as input.
    External keyboards/mice cannot trigger haptic — this is a hardware
    limitation, not a bug.
    """
    global _haptic_warned
    try:
        from AppKit import NSHapticFeedbackManager
        performer = NSHapticFeedbackManager.defaultPerformer()
        if performer is None:
            if not _haptic_warned:
                log.info("Haptic недоступен (нет Force Touch trackpad)")
                _haptic_warned = True
            return
        # Pattern 2 = LevelChange — most noticeable
        # Performance 0 = default
        performer.performFeedbackPattern_performanceTime_(2, 0)
    except Exception as e:
        if not _haptic_warned:
            log.info("Haptic ошибка: %s", e)
            _haptic_warned = True


def _get_clipboard():
    """Get current clipboard text."""
    try:
        r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip()
    except Exception:
        return ""


def _get_frontmost_app_name():
    """Get name of frontmost application."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else ""
    except Exception:
        return ""

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
                lambda: self._on_mic_error(msg)
            ),
        )
        self.overlay = RecordingOverlay()
        self.answer_window = AnswerWindow()
        self.answer_window.set_on_detail(self._on_qa_detail)
        self.transcriber = None
        self._cloud_transcriber = None  # kept for LLM formatting when using local whisper
        self.hotkey_manager = None
        self._generation = 0  # tracks current recording/processing cycle
        self._qa_mode = False  # True when recording a Q&A question
        self._agent_mode = False  # True when recording an agent command
        self._qa_history = []  # conversation history for Q&A mode
        self._processing_timer = None
        self._processing_gen = 0

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

        # History submenu
        self.history_menu = rumps.MenuItem("История")
        self._rebuild_history_menu()

        # Rate limit display
        self.rate_limit_item = rumps.MenuItem("LLM лимит: ожидание данных...")
        self.rate_limit_item.set_callback(None)

        self.settings_item = rumps.MenuItem(
            "Настройки...", callback=self._open_settings
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
            self.rate_limit_item,
            self.history_menu,
            None,
            self.settings_item,
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
                log.error("Ошибка в главном потоке: %s", e)

    def _run_on_main(self, fn):
        """Schedule fn to run on the main thread."""
        self._pending_calls.put(fn)

    # --- Transcriber ---

    def _init_transcriber(self):
        """Initialize transcribers for each mode."""
        language = self.config.get("language", "ru")

        # Always try to init cloud transcriber (needed for LLM formatting + cloud modes)
        api_key = self.config.get("groq_api_key", "")
        if api_key:
            self._cloud_transcriber = Transcriber(
                api_key=api_key,
                base_url=self.config.get("base_url", ""),
                language=language,
                model=self.config.get("model", "whisper-large-v3"),
                llm_model=self.config.get("llm_model", "llama-3.3-70b-versatile"),
            )

        # Init local transcriber if any mode needs it
        self._local_transcriber = None
        local_ok = LocalTranscriber.is_available()
        if local_ok:
            self._local_transcriber = LocalTranscriber(
                model_name=self.config.get("local_whisper_model", "base"),
                language=language,
            )

        # Set default transcriber (for dictation mode)
        mode = self.config.get("dictation_transcription",
                               self.config.get("transcription_mode", "cloud"))
        self.transcriber = self._get_transcriber_for_mode(mode)

        if not self.transcriber and not api_key:
            return False
        log.info("Используется облачный транскрайбер (Groq)")
        return True


    def _get_transcriber_for_mode(self, mode):
        """Get transcriber for a given mode (cloud/local/auto)."""
        if mode == "local" and self._local_transcriber:
            return self._local_transcriber
        elif mode == "auto" and self._local_transcriber:
            return self._local_transcriber
        elif self._cloud_transcriber:
            return self._cloud_transcriber
        return self._local_transcriber or self._cloud_transcriber

    def _get_active_transcriber(self):
        """Get the right transcriber for current mode (dictation/qa/agent)."""
        if self._agent_mode:
            mode = self.config.get("agent_transcription", "local")
        elif self._qa_mode:
            mode = self.config.get("qa_transcription", "local")
        else:
            mode = self.config.get("dictation_transcription",
                                   self.config.get("transcription_mode", "cloud"))
        return self._get_transcriber_for_mode(mode) or self.transcriber

    # --- State ---

    def _set_state(self, state):
        prev = self.state
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

        # Sound feedback
        if state == RECORDING:
            _play_sound("Tink")
            _haptic()
        elif state == IDLE and prev != IDLE:
            _play_sound("Pop")
            _haptic()

        # Stop processing timeout when leaving PROCESSING
        if prev == PROCESSING and state != PROCESSING:
            self._stop_processing_timeout()

        # Animated overlay
        if state == RECORDING:
            if self._agent_mode:
                rec_label = "Команда..." if lang == "ru" else "Command..."
                color_mode = "agent"
            elif self._qa_mode:
                rec_label = "Спрашивайте..." if lang == "ru" else "Ask..."
                color_mode = "qa"
            else:
                rec_label = labels["recording"]
                color_mode = "dictation"
            self.overlay.show_recording(rec_label, color_mode=color_mode)
            self._start_duration_timer()
        elif state == PROCESSING:
            self._stop_duration_timer()
            self._start_processing_timeout()
            if self._agent_mode:
                proc_label = "Выполняю..." if lang == "ru" else "Executing..."
            elif self._qa_mode:
                proc_label = "AI думает..." if lang == "ru" else "AI thinking..."
            else:
                proc_label = labels["processing"]
            self.overlay.show_processing(proc_label)
        else:
            self._stop_duration_timer()
            self.overlay.hide()

    # --- Duration timer ---

    def _start_duration_timer(self):
        self._rec_start_time = time.time()
        self.overlay.set_duration("0:00")
        self._duration_timer = rumps.Timer(self._update_duration, 0.25)
        self._duration_timer.start()

    def _stop_duration_timer(self):
        if hasattr(self, '_duration_timer') and self._duration_timer:
            self._duration_timer.stop()
            self._duration_timer = None

    def _update_duration(self, _):
        elapsed = int(time.time() - self._rec_start_time)
        mins, secs = divmod(elapsed, 60)
        self.overlay.set_duration(f"{mins}:{secs:02d}")
        # Feed amplitude history to waveform
        self.overlay.set_amplitudes(self.recorder.get_amplitude_history())

    # --- Processing timeout ---

    def _on_mic_error(self, msg):
        """Called on the main thread when the microphone fails to open."""
        log.warning("Microphone error: %s", msg)
        # Reset state immediately so the next press works
        self._qa_mode = False
        self._agent_mode = False
        if self.state != IDLE:
            self._set_state(IDLE)
        rumps.notification(APP_NAME, "Микрофон недоступен", msg)

    def _start_processing_timeout(self):
        """Start a 15-second timeout for processing state."""
        self._stop_processing_timeout()
        self._processing_gen = self._generation
        gen = self._generation
        # Use threading.Timer instead of rumps.Timer (which fires immediately).
        # The callback dispatches back to the main thread via _run_on_main.
        def _on_timeout():
            self._run_on_main(lambda: self._processing_timed_out(gen))
        self._processing_timer = threading.Timer(15.0, _on_timeout)
        self._processing_timer.daemon = True
        self._processing_timer.start()

    def _stop_processing_timeout(self):
        if self._processing_timer:
            try:
                self._processing_timer.cancel()
            except Exception:
                pass
            self._processing_timer = None

    def _processing_timed_out(self, gen):
        """Called when processing takes too long (runs on main thread)."""
        self._stop_processing_timeout()
        # Skip if generation changed (processing already completed/cancelled)
        if gen != self._generation:
            return
        if self.state == PROCESSING:
            log.warning("Обработка превысила таймаут (15с)")
            self.overlay.show_processing("Таймаут... Esc — отмена")
            rumps.notification(APP_NAME, "Долгая обработка",
                              "Транскрипция занимает больше 15 секунд. Нажмите Esc для отмены.")

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
            log.info("Таймаут выбора хоткея")
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

    # --- History ---

    def _update_rate_limit_display(self):
        """Update rate limit menu item from transcriber data."""
        source = self._cloud_transcriber or self.transcriber
        if not source or not hasattr(source, 'rate_limit'):
            return
        rl = source.rate_limit
        if rl.get("remaining") is not None and rl.get("limit"):
            remaining = rl["remaining"]
            limit = rl["limit"]
            pct = int(remaining / limit * 100)
            label = f"LLM лимит: {remaining:,} / {limit:,} ({pct}%)"
            if remaining == 0 and rl.get("reset"):
                label += f"  ⏳ {rl['reset']}"
            self.rate_limit_item.title = label
        elif rl.get("remaining") == 0 and rl.get("reset"):
            self.rate_limit_item.title = f"LLM лимит исчерпан ⏳ {rl['reset']}"

    def _rebuild_history_menu(self):
        """Rebuild the history submenu from saved entries."""
        # clear() fails if menu not yet attached to NSMenu (first call in __init__)
        try:
            self.history_menu.clear()
        except AttributeError:
            pass
        entries = load_history()
        if not entries:
            empty = rumps.MenuItem("(пусто)")
            empty.set_callback(None)
            self.history_menu.add(empty)
            return
        for i, entry in enumerate(entries[:15]):  # Show last 15 in menu
            title = f"{entry['timestamp']}  {truncate_text(entry['text'])}"
            item = rumps.MenuItem(title)
            text = entry["text"]
            item.set_callback(lambda _, t=text: self._copy_history_entry(t))
            self.history_menu.add(item)
        self.history_menu.add(rumps.separator)
        clear_item = rumps.MenuItem("Очистить историю", callback=self._clear_history)
        self.history_menu.add(clear_item)

    def _copy_history_entry(self, text):
        """Copy a history entry to clipboard."""
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        log.info("Запись из истории скопирована в буфер")

    def _clear_history(self, _):
        clear_history()
        self._rebuild_history_menu()

    def _open_settings(self, _):
        """Open native settings window."""
        hotkey_name = format_hotkey_name(self.config.get("hotkey", DEFAULT_HOTKEY))
        self._settings_controller = SettingsWindowController.alloc().initWithConfig_onSave_onLearnHotkey_hotkeyName_(
            self.config,
            self._on_settings_saved,
            self._start_learning,
            hotkey_name,
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
        log.info("_do_activate: state=%s agent=%s qa=%s", self.state, self._agent_mode, self._qa_mode)
        if self.state == LEARNING:
            return
        if self.state == RECORDING:
            # Already recording (toggle mode: stop)
            mode = self.config.get("mode", "push_to_talk")
            if mode == "toggle":
                self._stop_and_process()
                if self.hotkey_manager:
                    self.hotkey_manager._active = False
            return

        # Allow new recording even if previous transcription is still running
        # (PROCESSING state). The old _process_audio thread runs independently.
        if not self.transcriber:
            rumps.notification(APP_NAME, "API ключ не задан",
                               "Откройте настройки и укажите Groq API ключ")
            return

        self._generation += 1
        self._set_state(RECORDING)
        self.recorder.start_recording()
        if hasattr(self.transcriber, 'preload'):
            self.transcriber.preload()
        mode = self.config.get("mode", "push_to_talk")
        if mode == "toggle" and self.hotkey_manager:
            self.hotkey_manager._active = True

    def _on_deactivate(self):
        """Hotkey released (called from CGEventTap thread)."""
        self._run_on_main(self._do_deactivate)

    def _do_deactivate(self):
        """Actual deactivation logic, runs on main thread."""
        log.info("_do_deactivate: state=%s recording=%s", self.state, self.recorder.is_recording())
        mode = self.config.get("mode", "push_to_talk")
        if mode == "push_to_talk":
            if self.state == RECORDING or self.recorder.is_recording():
                self._stop_and_process()

    def _on_cancel(self):
        """Esc pressed (called from CGEventTap thread)."""
        self._run_on_main(self._do_cancel)

    def _do_cancel(self):
        """Cancel current recording without transcribing, or hide answer window."""
        if self.state == RECORDING:
            audio_path = self.recorder.stop_recording()
            if audio_path:
                self.recorder.cleanup_file(audio_path)
            log.info("Запись отменена (Esc)")
            self._qa_mode = False
            self._agent_mode = False
            self._set_state(IDLE)
        elif self.state == PROCESSING:
            log.info("Обработка отменена (Esc)")
            self._generation += 1  # invalidate current processing
            self._qa_mode = False
            self._agent_mode = False
            self._set_state(IDLE)
        elif self.answer_window.is_visible():
            self.answer_window.hide()
        elif self.state == LEARNING:
            if hasattr(self, '_learn_timer') and self._learn_timer:
                self._learn_timer.stop()
            self.hotkey_manager.cancel_learning()
            self._set_state(IDLE)

    # --- Q&A mode ---

    def _on_qa_activate(self):
        self._run_on_main(self._do_qa_activate)

    def _do_qa_activate(self):
        if self.state == RECORDING or self.state == LEARNING:
            return
        # Hide previous answer if visible
        if self.answer_window.is_visible():
            self.answer_window.hide()
        self._qa_mode = True
        self._generation += 1
        self._set_state(RECORDING)
        self.recorder.start_recording()
        if hasattr(self.transcriber, 'preload'):
            self.transcriber.preload()

    def _on_qa_deactivate(self):
        self._run_on_main(self._do_qa_deactivate)

    def _do_qa_deactivate(self):
        if self._qa_mode and (self.state == RECORDING or self.recorder.is_recording()):
            self._stop_and_process_qa()

    def _stop_and_process_qa(self):
        duration = self.recorder.get_duration()
        audio_path = self.recorder.stop_recording()
        if not audio_path or duration < MIN_RECORDING_SECONDS:
            if audio_path:
                self.recorder.cleanup_file(audio_path)
            self._qa_mode = False
            self._set_state(IDLE)
            return
        log.info("QA аудио: %s (%.1fs)", audio_path, duration)
        gen = self._generation
        self._set_state(PROCESSING)
        thread = threading.Thread(
            target=self._process_qa,
            args=(audio_path, gen),
            daemon=True,
        )
        thread.start()

    def _process_qa(self, audio_path, gen):
        """Transcribe question, send to LLM, show answer in window."""
        try:
            t = self._get_active_transcriber()
            if not t:
                return
            log.info("QA: транскрибирую вопрос...")
            question = t.transcribe(audio_path)
            log.info("QA вопрос: %s", question[:100])

            # Add clipboard context if referenced
            if any(w in question.lower() for w in ("буфер", "скопиро", "clipboard", "вставь",
                                                     "переведи это", "что я скопировал")):
                clip = _get_clipboard()
                if clip:
                    question += f"\n\n[Содержимое буфера обмена: {clip[:500]}]"

            # Show loading state
            self._run_on_main(lambda: self.answer_window.show_loading(question))

            # Send to LLM based on configured backend (with streaming)
            def _on_stream_chunk(text_so_far):
                self._run_on_main(lambda: self.answer_window.update_answer(question, text_so_far))

            answer = self._ask_qa_llm(question, stream_callback=_on_stream_chunk)
            log.info("QA ответ: %s", answer[:100])

            # Save to conversation history (keep last 10 exchanges)
            self._qa_history.append({"role": "user", "content": question})
            self._qa_history.append({"role": "assistant", "content": answer})
            if len(self._qa_history) > 20:
                self._qa_history = self._qa_history[-20:]

            self._run_on_main(lambda: self.answer_window.show(question, answer))
        except Exception as e:
            log.error("QA ошибка: %s", e)
            error_msg = str(e)[:200]
            self._run_on_main(lambda: self.answer_window.show(
                "Ошибка", error_msg
            ))
        finally:
            if self.transcriber:
                self.transcriber.cleanup(audio_path)
            else:
                AudioRecorder.cleanup_file(audio_path)
            self._qa_mode = False
            def _reset():
                if self._generation == gen:
                    self._set_state(IDLE)
            self._run_on_main(_reset)

    # Free models ranked by quality/speed (auto-rotation on rate limit)
    _QA_MODELS = [
        "nvidia/nemotron-3-super-120b-a12b:free",   # 120B, fast, excellent
        "qwen/qwen3.6-plus:free",                   # great quality, slower
        "nvidia/nemotron-3-nano-30b-a3b:free",       # 30B, very fast
        "meta-llama/llama-3.3-70b-instruct:free",   # good fallback
        "z-ai/glm-4.5-air:free",                    # decent, slow
        "arcee-ai/trinity-large-preview:free",       # backup
        "stepfun/step-3.5-flash:free",              # backup
    ]

    # Answer length presets: (system_prompt_extra, max_tokens)
    _QA_LENGTHS = {
        "short":  ("Отвечай максимально коротко — 2-4 предложения. Без таблиц. Только суть.", 300),
        "medium": ("Отвечай средней длины — 5-10 предложений. Можно использовать списки.", 600),
        "long":   ("Отвечай развёрнуто и подробно. Используй списки, примеры.", 1500),
    }

    def _ask_qa_llm(self, question, detailed=False, stream_callback=None):
        """Route Q&A to the configured LLM backend."""
        backend = self.config.get("qa_llm_backend", "auto")
        if backend == "groq":
            return self._ask_groq_qa(question, detailed=detailed, stream_callback=stream_callback)
        elif backend == "openrouter":
            return self._ask_openrouter(question, detailed=detailed, stream_callback=stream_callback)
        else:  # auto: try groq first, fallback to openrouter
            if self._cloud_transcriber:
                try:
                    return self._ask_groq_qa(question, detailed=detailed, stream_callback=stream_callback)
                except Exception as e:
                    log.warning("QA Groq failed: %s, trying OpenRouter", e)
            return self._ask_openrouter(question, detailed=detailed, stream_callback=stream_callback)

    def _ask_groq_qa(self, question, detailed=False, stream_callback=None):
        """Send Q&A question to Groq LLM."""
        if not self._cloud_transcriber:
            raise RuntimeError("Groq API ключ не задан")
        lang = self.config.get("language", "ru")
        if detailed:
            length_key = "long"
        else:
            length_key = self.config.get("qa_answer_length", "short")
        length_prompt, max_tokens = self._QA_LENGTHS.get(length_key, self._QA_LENGTHS["short"])

        system_msg = (
            "Ты полезный AI-ассистент. "
            f"{length_prompt} "
            f"Отвечай на {'русском' if lang == 'ru' else 'английском'} языке."
        )
        messages = [{"role": "system", "content": system_msg}]
        if self._qa_history:
            messages.extend(self._qa_history[-10:])
        messages.append({"role": "user", "content": question})

        if stream_callback:
            stream = self._cloud_transcriber.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7,
                stream=True,
            )
            full_text = ""
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_text += delta
                    stream_callback(full_text)
            return full_text
        else:
            response = self._cloud_transcriber.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            return response.choices[0].message.content

    def _ask_openrouter(self, question, detailed=False, stream_callback=None):
        """Send question to OpenRouter with automatic model rotation."""
        import httpx
        import json as _json
        api_key = self.config.get("openrouter_api_key", "")
        if not api_key:
            raise RuntimeError("OpenRouter API ключ не задан (настройки)")
        lang = self.config.get("language", "ru")

        if detailed:
            length_key = "long"
        else:
            length_key = self.config.get("qa_answer_length", "short")
        length_prompt, max_tokens = self._QA_LENGTHS.get(length_key, self._QA_LENGTHS["short"])

        system_msg = (
            "Ты полезный AI-ассистент. "
            f"{length_prompt} "
            f"Отвечай на {'русском' if lang == 'ru' else 'английском'} языке."
        )
        messages = [{"role": "system", "content": system_msg}]
        # Add conversation history for context
        if self._qa_history:
            messages.extend(self._qa_history[-10:])  # last 5 exchanges
        messages.append({"role": "user", "content": question})

        # Try preferred model first, then rotate through fallbacks
        preferred = self.config.get("qa_model", self._QA_MODELS[0])
        models_to_try = [preferred] + [m for m in self._QA_MODELS if m != preferred]

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error = None
        for model in models_to_try:
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            try:
                if stream_callback:
                    body["stream"] = True
                    with httpx.stream("POST", url, headers=headers, json=body, timeout=30) as response:
                        if response.status_code == 200:
                            full_text = ""
                            for line in response.iter_lines():
                                if line.startswith("data: "):
                                    data = line[6:]
                                    if data == "[DONE]":
                                        break
                                    try:
                                        chunk = _json.loads(data)
                                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                                        if delta:
                                            full_text += delta
                                            stream_callback(full_text)
                                    except (_json.JSONDecodeError, KeyError, IndexError):
                                        continue
                            if model != preferred:
                                log.info("QA: использована запасная модель %s", model)
                            return full_text
                        elif response.status_code in (429, 503):
                            log.warning("QA: %s → %s, пробую следующую", model, response.status_code)
                            last_error = f"{model}: HTTP {response.status_code}"
                            continue
                        elif response.status_code == 404:
                            log.warning("QA: %s недоступна (404), пробую следующую", model)
                            last_error = f"{model}: недоступна"
                            continue
                        else:
                            last_error = f"{model}: HTTP {response.status_code}"
                            continue
                else:
                    resp = httpx.post(url, headers=headers, json=body, timeout=30)
                    if resp.status_code == 200:
                        data = resp.json()
                        answer = data["choices"][0]["message"]["content"]
                        if model != preferred:
                            log.info("QA: использована запасная модель %s", model)
                        return answer
                    elif resp.status_code in (429, 503):
                        log.warning("QA: %s → %s, пробую следующую", model, resp.status_code)
                        last_error = f"{model}: HTTP {resp.status_code}"
                        continue
                    elif resp.status_code == 404:
                        log.warning("QA: %s недоступна (404), пробую следующую", model)
                        last_error = f"{model}: недоступна"
                        continue
                    else:
                        last_error = f"{model}: HTTP {resp.status_code}"
                        continue
            except httpx.TimeoutException:
                log.warning("QA: %s таймаут, пробую следующую", model)
                last_error = f"{model}: таймаут"
                continue
            except Exception as e:
                last_error = f"{model}: {e}"
                continue

        raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error}")

    def _on_qa_detail(self, question):
        """'Подробнее' button pressed — re-ask with longer answer."""
        self._run_on_main(lambda: self.answer_window.show_loading(question))

        def _do():
            try:
                def _on_stream_chunk(text_so_far):
                    self._run_on_main(lambda: self.answer_window.update_answer(question, text_so_far))

                answer = self._ask_qa_llm(question, detailed=True, stream_callback=_on_stream_chunk)
                log.info("QA подробный ответ: %s", answer[:100])
                self._run_on_main(lambda: self.answer_window.show(question, answer))
            except Exception as e:
                log.error("QA detail ошибка: %s", e)
                self._run_on_main(lambda: self.answer_window.show(
                    question, f"Ошибка: {str(e)[:200]}"
                ))

        threading.Thread(target=_do, daemon=True).start()

    # --- Agent mode (Opt+Cmd+Space) ---

    def _on_agent_activate(self):
        self._run_on_main(self._do_agent_activate)

    def _do_agent_activate(self):
        if self.state == RECORDING or self.state == LEARNING:
            return
        if self.answer_window.is_visible():
            self.answer_window.hide()
        self._agent_mode = True
        self._qa_mode = False
        self._generation += 1
        self._set_state(RECORDING)
        self.recorder.start_recording()
        if hasattr(self.transcriber, 'preload'):
            self.transcriber.preload()

    def _on_agent_deactivate(self):
        self._run_on_main(self._do_agent_deactivate)

    def _do_agent_deactivate(self):
        if self._agent_mode and (self.state == RECORDING or self.recorder.is_recording()):
            self._stop_and_process_agent()

    def _stop_and_process_agent(self):
        duration = self.recorder.get_duration()
        audio_path = self.recorder.stop_recording()
        if not audio_path or duration < MIN_RECORDING_SECONDS:
            if audio_path:
                self.recorder.cleanup_file(audio_path)
            self._agent_mode = False
            self._set_state(IDLE)
            return
        log.info("Agent аудио: %s (%.1fs)", audio_path, duration)
        gen = self._generation
        self._set_state(PROCESSING)
        threading.Thread(
            target=self._process_agent, args=(audio_path, gen), daemon=True
        ).start()

    def _process_agent(self, audio_path, gen):
        """Transcribe command, send to LLM with tools, execute action."""
        try:
            t = self._get_active_transcriber()
            if not t:
                return
            log.info("Agent: транскрибирую команду...")
            command = t.transcribe(audio_path)
            log.info("Agent команда: %s", command[:100])

            # Check custom commands first (instant, no LLM needed)
            custom = self.config.get("custom_commands", {})
            cmd_lower = command.lower().strip().rstrip('.')
            for trigger, actions in custom.items():
                if trigger.lower() in cmd_lower:
                    log.info("Agent: кастомная команда '%s'", trigger)
                    for act in actions:
                        result = execute_action(act)
                        log.info("Agent custom: %s → %s", act.get("action"), result[:50])
                    subprocess.Popen(["say", "-r", "250", trigger],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return  # skip LLM

            # Add context: active app + clipboard snippet
            # NOTE: active app context is only added when the command explicitly
            # references it (e.g. "close current app", "what's open"), otherwise
            # it confuses the LLM (it picks switch_app to active app instead of
            # open_app to the requested target).
            context = _get_frontmost_app_name()
            clipboard = _get_clipboard()
            enriched = command
            cmd_lower = command.lower()
            wants_context = any(kw in cmd_lower for kw in (
                "это приложение", "текущее", "активное", "это окно",
                "current app", "this app", "active app", "this window",
                "закрой это", "сверни это",
            ))
            if context and wants_context:
                enriched += f" [активное приложение: {context}]"
            if clipboard and ("буфер" in command.lower() or "скопиро" in command.lower()
                              or "clipboard" in command.lower() or "вставь" in command.lower()):
                enriched += f" [буфер обмена: {clipboard[:200]}]"

            # Add screen context for commands that reference what's on screen
            screen_keywords = ("экран", "вижу", "покажи", "что на", "что здесь", "что тут",
                               "screen", "see", "показано", "написано", "текст на экране",
                               "прочитай", "переведи с экрана")
            if any(kw in command.lower() for kw in screen_keywords):
                from agent import get_screen_context
                screen_text = get_screen_context()
                if screen_text:
                    enriched += f" [текст на экране: {screen_text}]"
                    log.info("Agent: OCR контекст добавлен (%d символов)", len(screen_text))

            # Send to LLM with tool-calling system prompt
            action_data = self._agent_ask(enriched)
            action_name = action_data.get("action", "none")
            reply = action_data.get("reply", "")
            log.info("Agent действие: %s — %s", action_name, reply)

            # Execute the action(s)
            result = ""
            if isinstance(action_data.get("actions"), list):
                # Chain of actions
                results = []
                for act in action_data["actions"]:
                    r = execute_action(act)
                    log.info("Agent chain: %s → %s", act.get("action"), r[:50])
                    results.append(r)
                result = "\n".join(results)
            else:
                result = execute_action(action_data)
                log.info("Agent результат: %s", result[:100])

            # For informational actions (list, search, etc.) show result
            # in answer window instead of just voice
            _INFO_ACTIONS = {"ticktick_list", "get_system_info", "search_web"}
            if action_name in _INFO_ACTIONS and result:
                self._run_on_main(lambda: self.answer_window.show(
                    reply or action_name, result
                ))
            # Voice feedback for agent (optional)
            elif self.config.get("agent_voice_feedback", True) and action_name != "say":
                feedback = reply or result
                if feedback:
                    subprocess.Popen(["say", "-r", "250", feedback[:200]],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        except Exception as e:
            log.error("Agent ошибка: %s", e)
        finally:
            if self.transcriber:
                self.transcriber.cleanup(audio_path)
            else:
                AudioRecorder.cleanup_file(audio_path)
            self._agent_mode = False
            def _reset():
                if self._generation == gen:
                    self._set_state(IDLE)
            self._run_on_main(_reset)

    def _agent_ask(self, command):
        """Send command to LLM based on configured agent backend."""
        import httpx
        lang = self.config.get("language", "ru")
        system_prompt = _build_system_prompt(lang)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": command},
        ]
        backend = self.config.get("agent_llm_backend", "auto")

        # Try Groq
        def _try_groq():
            if not self._cloud_transcriber:
                raise RuntimeError("Groq API ключ не задан")
            response = self._cloud_transcriber.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.05,
                max_tokens=300,
            )
            text = response.choices[0].message.content
            return parse_llm_response(text)

        # Try OpenRouter
        def _try_openrouter():
            api_key = self.config.get("openrouter_api_key", "")
            if not api_key:
                raise RuntimeError("OpenRouter API ключ не задан")
            for model in self._QA_MODELS:
                try:
                    resp = httpx.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": 300,
                            "temperature": 0.05,
                        },
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        text = resp.json()["choices"][0]["message"]["content"]
                        return parse_llm_response(text)
                    if resp.status_code in (429, 503, 404):
                        continue
                except Exception:
                    continue
            raise RuntimeError("Все модели OpenRouter недоступны")

        if backend == "groq":
            return _try_groq()
        elif backend == "openrouter":
            return _try_openrouter()
        else:  # auto: groq first, fallback openrouter
            if self._cloud_transcriber:
                try:
                    return _try_groq()
                except Exception as e:
                    log.warning("Agent Groq failed: %s, trying OpenRouter", e)
            return _try_openrouter()

    # --- Dictation processing ---

    def _stop_and_process(self):
        """Stop recording and start transcription."""
        if not self.recorder.is_recording() and self.state != RECORDING:
            log.warning("_stop_and_process вызван но запись не активна (state=%s)", self.state)
            self._set_state(IDLE)
            return
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
        gen = self._generation
        self._set_state(PROCESSING)
        thread = threading.Thread(
            target=self._process_audio,
            args=(audio_path, gen),
            daemon=True,
        )
        thread.start()

    def _process_audio(self, audio_path, gen):
        """Runs on a background thread — must not touch UI directly."""
        try:
            if not self.transcriber:
                log.error("Transcriber не инициализирован (нет API ключа?)")
                return

            # Step 1: Transcribe (local or cloud)
            log.info("Отправляю на транскрипцию...")
            raw_text = self.transcriber.transcribe(audio_path)

            # Step 2: Format (always cloud — local LLMs don't handle Russian well)
            text = raw_text
            if self.config.get("format_with_llm", True):
                app_ctx = get_app_context()
                if app_ctx:
                    log.info("Контекст приложения: %s", app_ctx)
                try:
                    if hasattr(self.transcriber, 'format_text'):
                        text = self.transcriber.format_text(raw_text, app_context=app_ctx)
                    elif self._cloud_transcriber:
                        text = self._cloud_transcriber.format_text(raw_text, app_context=app_ctx)
                except Exception as fmt_err:
                    log.warning("Форматирование не удалось, вставляю как есть: %s", fmt_err)
                    text = raw_text
                # Update rate limit display
                self._run_on_main(self._update_rate_limit_display)

            log.info("Результат: %s", text[:100])
            insert_text(text)
            log.info("Текст вставлен")
            # Save to history & update menu
            add_entry(text)
            self._run_on_main(self._rebuild_history_menu)
        except Exception as e:
            log.error("Ошибка транскрипции: %s", e)
            error_msg = str(e)[:100]
            self._run_on_main(lambda: rumps.notification(
                APP_NAME, "Ошибка транскрипции", error_msg
            ))
        finally:
            if self.transcriber:
                self.transcriber.cleanup(audio_path)
            else:
                AudioRecorder.cleanup_file(audio_path)
            def _reset():
                # Only reset to IDLE if no new recording started since this one
                if self._generation == gen:
                    self._set_state(IDLE)
            self._run_on_main(_reset)

    # --- Lifecycle ---

    def _quit(self, _):
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        if hasattr(self.transcriber, 'shutdown'):
            self.transcriber.shutdown()
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
            log.warning("Не удалось проверить разрешение микрофона: %s", e)

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
            on_qa_activate=self._on_qa_activate,
            on_qa_deactivate=self._on_qa_deactivate,
            on_agent_activate=self._on_agent_activate,
            on_agent_deactivate=self._on_agent_deactivate,
        )
        self.hotkey_manager.start()

        super().run(**kwargs)


def main():
    app = VoiceTypeApp()
    app.run()


if __name__ == "__main__":
    main()
