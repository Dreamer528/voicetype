#!/usr/bin/env python3
"""VoiceType — fully background macOS menu bar app for AI voice dictation.

Configurable hotkey, push-to-talk or toggle mode.
Routes through proxy to avoid geo-blocks.
"""

import logging
import os
import queue
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
        self.answer_window = AnswerWindow()
        self.answer_window.set_on_detail(self._on_qa_detail)
        self.transcriber = None
        self._cloud_transcriber = None  # kept for LLM formatting when using local whisper
        self.hotkey_manager = None
        self._generation = 0  # tracks current recording/processing cycle
        self._qa_mode = False  # True when recording a Q&A question
        self._agent_mode = False  # True when recording an agent command

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
        """Initialize transcriber based on transcription_mode config."""
        mode = self.config.get("transcription_mode", "cloud")
        language = self.config.get("language", "ru")

        # Always try to init cloud transcriber (needed for LLM formatting)
        api_key = self.config.get("groq_api_key", "")
        if api_key:
            self._cloud_transcriber = Transcriber(
                api_key=api_key,
                base_url=self.config.get("base_url", ""),
                language=language,
                model=self.config.get("model", "whisper-large-v3"),
                llm_model=self.config.get("llm_model", "llama-3.3-70b-versatile"),
            )

        local_ok = LocalTranscriber.is_available()
        if mode in ("local", "auto") and not local_ok:
            log.warning("Локальный режим запрошен, но MLX-Whisper недоступен — fallback на облако")

        if mode == "local" and local_ok:
            self.transcriber = LocalTranscriber(
                model_name=self.config.get("local_whisper_model", "base"),
                language=language,
            )
            log.info("Используется локальный транскрайбер (MLX-Whisper)")
            return True
        elif mode == "auto" and local_ok:
            self.transcriber = LocalTranscriber(
                model_name=self.config.get("local_whisper_model", "base"),
                language=language,
            )
            log.info("Авто-режим: используется локальный транскрайбер")
            return True

        # Cloud mode (default)
        if not api_key:
            return False
        self.transcriber = self._cloud_transcriber
        log.info("Используется облачный транскрайбер (Groq)")
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

        lang = self.config.get("language", "ru")
        labels = _LABELS.get(lang, _LABELS["ru"])

        # Animated overlay
        if state == RECORDING:
            if self._agent_mode:
                rec_label = "Команда..." if lang == "ru" else "Command..."
            elif self._qa_mode:
                rec_label = "Спрашивайте..." if lang == "ru" else "Ask..."
            else:
                rec_label = labels["recording"]
            self.overlay.show_recording(rec_label)
            self._start_duration_timer()
        elif state == PROCESSING:
            self._stop_duration_timer()
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
        if self.state == RECORDING and self._qa_mode:
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
            if not self.transcriber:
                return
            log.info("QA: транскрибирую вопрос...")
            question = self.transcriber.transcribe(audio_path)
            log.info("QA вопрос: %s", question[:100])

            # Show loading state
            self._run_on_main(lambda: self.answer_window.show_loading(question))

            # Send to OpenRouter
            answer = self._ask_openrouter(question)
            log.info("QA ответ: %s", answer[:100])

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

    def _ask_openrouter(self, question, detailed=False):
        """Send question to OpenRouter with automatic model rotation."""
        import httpx
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
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question},
        ]

        # Try preferred model first, then rotate through fallbacks
        preferred = self.config.get("qa_model", self._QA_MODELS[0])
        models_to_try = [preferred] + [m for m in self._QA_MODELS if m != preferred]

        last_error = None
        for model in models_to_try:
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
                        "max_tokens": max_tokens,
                    },
                    timeout=30,
                )
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
                answer = self._ask_openrouter(question, detailed=True)
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
        if self.state == RECORDING and self._agent_mode:
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
            if not self.transcriber:
                return
            log.info("Agent: транскрибирую команду...")
            command = self.transcriber.transcribe(audio_path)
            log.info("Agent команда: %s", command[:100])

            self._run_on_main(lambda: self.answer_window.show_loading(command))

            # Send to LLM with tool-calling system prompt
            action_data = self._agent_ask(command)
            log.info("Agent действие: %s", action_data.get("action", "none"))

            # Execute the action
            result = execute_action(action_data)
            log.info("Agent результат: %s", result[:100])

            action_name = action_data.get("action", "none")
            if action_name == "none":
                display = result
            else:
                display = f"✅ {action_data.get('reply', action_name)}\n\n{result}" if result != action_data.get('reply', '') else f"✅ {result}"

            self._run_on_main(lambda: self.answer_window.show(command, display))
        except Exception as e:
            log.error("Agent ошибка: %s", e)
            error_msg = str(e)[:200]
            self._run_on_main(lambda: self.answer_window.show(
                "Ошибка", error_msg
            ))
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
        """Send command to LLM with tool-calling prompt, return parsed action."""
        import httpx
        api_key = self.config.get("openrouter_api_key", "")
        if not api_key:
            raise RuntimeError("OpenRouter API ключ не задан")
        lang = self.config.get("language", "ru")
        system_prompt = _build_system_prompt(lang)

        models = self._QA_MODELS  # reuse the same model rotation
        last_error = None

        for model in models:
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": command},
                        ],
                        "max_tokens": 300,
                        "temperature": 0.1,
                    },
                    timeout=20,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    return parse_llm_response(text)
                elif resp.status_code in (429, 503, 404):
                    last_error = f"{model}: HTTP {resp.status_code}"
                    continue
                else:
                    last_error = f"{model}: HTTP {resp.status_code}"
                    continue
            except httpx.TimeoutException:
                last_error = f"{model}: таймаут"
                continue
            except Exception as e:
                last_error = str(e)
                continue

        raise RuntimeError(f"Все модели недоступны: {last_error}")

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
