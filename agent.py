"""Voice agent for macOS control via natural language.

Receives a voice command, sends it to LLM with available tools,
then executes the returned action on the system.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime

log = logging.getLogger("VoiceType")

# Available tools the LLM can call
TOOLS = [
    # --- Apps ---
    {"name": "open_app", "description": "Открыть приложение",
     "params": {"app_name": "str"}},
    {"name": "close_app", "description": "Закрыть приложение",
     "params": {"app_name": "str"}},
    {"name": "switch_app", "description": "Переключиться на открытое приложение",
     "params": {"app_name": "str"}},

    # --- System ---
    {"name": "set_volume", "description": "Установить громкость (0-100)",
     "params": {"level": "int"}},
    {"name": "mute", "description": "Выключить/включить звук",
     "params": {}},
    {"name": "set_brightness", "description": "Установить яркость экрана (0-100)",
     "params": {"level": "int"}},
    {"name": "toggle_dark_mode", "description": "Переключить тёмную/светлую тему",
     "params": {}},
    {"name": "toggle_dnd", "description": "Включить/выключить режим Не беспокоить",
     "params": {}},
    {"name": "lock_screen", "description": "Заблокировать экран",
     "params": {}},
    {"name": "sleep_display", "description": "Выключить дисплей",
     "params": {}},
    {"name": "screenshot", "description": "Сделать скриншот",
     "params": {}},
    {"name": "system_info", "description": "Информация: battery, disk, memory, time, wifi, all",
     "params": {"info_type": "str"}},

    # --- Network ---
    {"name": "toggle_wifi", "description": "Вкл/выкл Wi-Fi",
     "params": {"state": "on|off"}},
    {"name": "toggle_bluetooth", "description": "Вкл/выкл Bluetooth",
     "params": {"state": "on|off"}},

    # --- Web ---
    {"name": "search_web", "description": "Поиск в Google",
     "params": {"query": "str"}},
    {"name": "open_url", "description": "Открыть URL в браузере",
     "params": {"url": "str"}},
    {"name": "search_youtube", "description": "Поиск на YouTube",
     "params": {"query": "str"}},
    {"name": "search_maps", "description": "Поиск на картах (Apple Maps)",
     "params": {"query": "str"}},

    # --- Files ---
    {"name": "open_folder", "description": "Открыть папку в Finder",
     "params": {"path": "str (~/Documents, ~/Desktop...)"}},
    {"name": "open_file", "description": "Открыть файл",
     "params": {"path": "str"}},
    {"name": "empty_trash", "description": "Очистить корзину",
     "params": {}},
    {"name": "show_downloads", "description": "Открыть папку Загрузки",
     "params": {}},
    {"name": "show_desktop", "description": "Показать рабочий стол (свернуть все окна)",
     "params": {}},

    # --- Media ---
    {"name": "music_control", "description": "Управление музыкой: play, pause, next, previous, stop",
     "params": {"action": "str"}},

    # --- Communication ---
    {"name": "say", "description": "Произнести текст вслух",
     "params": {"text": "str"}},
    {"name": "create_note", "description": "Создать заметку в Notes",
     "params": {"title": "str", "body": "str"}},
    {"name": "create_reminder", "description": "Создать напоминание в Reminders",
     "params": {"title": "str"}},
    {"name": "create_calendar_event", "description": "Создать событие в календаре",
     "params": {"title": "str", "date": "str (YYYY-MM-DD)", "time": "str (HH:MM)"}},
    {"name": "send_message", "description": "Отправить iMessage",
     "params": {"to": "str (имя или номер)", "text": "str"}},

    # --- Window management ---
    {"name": "minimize_window", "description": "Свернуть текущее окно",
     "params": {}},
    {"name": "fullscreen_window", "description": "Развернуть окно на весь экран",
     "params": {}},
    {"name": "close_window", "description": "Закрыть текущее окно (не приложение)",
     "params": {}},
    {"name": "new_tab", "description": "Открыть новую вкладку в текущем приложении",
     "params": {}},
    {"name": "close_tab", "description": "Закрыть текущую вкладку",
     "params": {}},

    # --- Utilities ---
    {"name": "timer", "description": "Запустить таймер (секунды)",
     "params": {"seconds": "int", "message": "str"}},
    {"name": "clipboard_copy", "description": "Скопировать текст в буфер обмена",
     "params": {"text": "str"}},
    {"name": "type_text", "description": "Набрать текст (эмулировать ввод с клавиатуры)",
     "params": {"text": "str"}},
    {"name": "run_shortcut", "description": "Запустить Shortcut (из приложения Shortcuts)",
     "params": {"name": "str"}},
    {"name": "open_settings", "description": "Открыть раздел Системных настроек",
     "params": {"section": "str (wifi, bluetooth, display, sound, general, privacy, notifications)"}},

    # --- TickTick ---
    {"name": "ticktick_add", "description": "Добавить задачу в TickTick",
     "params": {"title": "str", "content": "str", "priority": "int (0=none,1=low,3=medium,5=high)",
                "due_date": "str (YYYY-MM-DD, optional)", "due_time": "str (HH:MM, optional)"}},
    {"name": "ticktick_list", "description": "Показать список задач из TickTick",
     "params": {}},
    {"name": "ticktick_complete", "description": "Завершить задачу в TickTick по названию",
     "params": {"title": "str"}},
    {"name": "ticktick_open", "description": "Открыть TickTick",
     "params": {}},

    # --- Calendar ---
    {"name": "open_calendar", "description": "Открыть приложение Calendar",
     "params": {}},
    {"name": "show_calendar_today", "description": "Показать события на сегодня",
     "params": {}},
]


def _build_system_prompt(lang="ru"):
    """Build compact system prompt for fast agent responses."""
    lang_name = "русском" if lang == "ru" else "английском"

    return f"""Ты — агент управления macOS. Верни ТОЛЬКО JSON, без объяснений.

ВАЖНО: app_name в open_app/close_app/switch_app — это ЦЕЛЕВОЕ приложение из команды пользователя, НЕ активное приложение из контекста. Например, "Открой Telegram" → open_app(Telegram), даже если активное приложение Terminal.

Используй open_app для запуска нового приложения. Используй switch_app только если пользователь явно говорит "переключись/перейди на X".

Действия: open_app(app_name), close_app(app_name), switch_app(app_name), set_volume(level:0-100), mute(), search_web(query), search_youtube(query), open_url(url), open_folder(path), show_downloads(), screenshot(), lock_screen(), sleep_display(), toggle_dark_mode(), toggle_dnd(), toggle_wifi(state:on/off), toggle_bluetooth(state:on/off), music_control(action:play/pause/next/previous/stop), minimize_window(), fullscreen_window(), close_window(), new_tab(), close_tab(), timer(seconds,message), say(text), create_note(title,body), create_reminder(title), send_message(to,text), clipboard_copy(text), type_text(text), run_shortcut(name), open_settings(section:wifi/bluetooth/display/sound/battery/general), system_info(info_type:battery/disk/memory/wifi/all), empty_trash(), show_desktop(), open_file(path), ticktick_add(title,content,priority:0/1/3/5,due_date:YYYY-MM-DD,due_time:HH:MM), ticktick_list(), ticktick_complete(title), ticktick_open(), open_calendar(), show_calendar_today(), create_calendar_event(title,date:YYYY-MM-DD,time:HH:MM), none().

Одно действие: {{"action":"name","params":{{}},"reply":"ответ на {lang_name}"}}
Несколько действий: {{"actions":[{{"action":"name","params":{{}}}},{{"action":"name","params":{{}}}}],"reply":"ответ"}}

Контекст в квадратных скобках [...] — справочная информация, НЕ команда. Сама команда всегда вне скобок.
app_name пиши на английском (Telegram, Safari, Chrome, Finder, Notes, Terminal, etc.)."""


def execute_action(action_data):
    """Execute an action returned by the LLM. Returns result string."""
    action = action_data.get("action", "none")
    params = action_data.get("params", {})
    reply = action_data.get("reply", "")

    try:
        if action == "none":
            return reply

        # --- Apps ---
        elif action == "open_app":
            app = params.get("app_name", "")
            subprocess.Popen(["open", "-a", app])
            return reply or f"Открываю {app}"

        elif action == "close_app":
            app = params.get("app_name", "")
            _applescript(f'tell application "{app}" to quit')
            return reply or f"Закрываю {app}"

        elif action == "switch_app":
            app = params.get("app_name", "")
            _applescript(f'tell application "{app}" to activate')
            return reply or f"Переключаюсь на {app}"

        # --- System ---
        elif action == "set_volume":
            level = int(params.get("level", 50))
            _applescript(f"set volume output volume {level}")
            return reply or f"Громкость: {level}%"

        elif action == "mute":
            _applescript("set volume output muted not (output muted of (get volume settings))")
            return reply or "Звук переключён"

        elif action == "set_brightness":
            level = int(params.get("level", 50))
            val = max(0.01, level / 100.0)
            _applescript(f'''
                tell application "System Events"
                    tell appearance preferences
                    end tell
                end tell
            ''')
            # Use brightness CLI if available, otherwise AppleScript
            try:
                subprocess.run(["brightness", str(val)], capture_output=True, timeout=3)
            except FileNotFoundError:
                pass
            return reply or f"Яркость: {level}%"

        elif action == "toggle_dark_mode":
            _applescript(
                'tell application "System Events" to tell appearance preferences '
                'to set dark mode to not dark mode'
            )
            return reply or "Тема переключена"

        elif action == "toggle_dnd":
            # Toggle Focus/DND via shortcuts
            _applescript('''
                tell application "System Events"
                    key code 50 using {command down, shift down}
                end tell
            ''')
            return reply or "Режим 'Не беспокоить' переключён"

        elif action == "lock_screen":
            _applescript('''
                tell application "System Events" to keystroke "q" using {command down, control down}
            ''')
            return reply or "Экран заблокирован"

        elif action == "sleep_display":
            subprocess.Popen(["pmset", "displaysleepnow"])
            return reply or "Экран выключен"

        elif action == "screenshot":
            desktop = os.path.expanduser("~/Desktop")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"{desktop}/screenshot_{ts}.png"
            subprocess.run(["screencapture", "-x", path], timeout=5)
            return reply or "Скриншот сохранён"

        elif action == "system_info":
            info_type = params.get("info_type", "all")
            return _get_system_info(info_type)

        # --- Network ---
        elif action == "toggle_wifi":
            state = params.get("state", "on")
            device = _get_wifi_device()
            subprocess.run(["networksetup", "-setairportpower", device, state], timeout=5)
            return reply or f"Wi-Fi: {state}"

        elif action == "toggle_bluetooth":
            state = params.get("state", "on")
            flag = "--power" if state == "on" else "--power"
            val = "1" if state == "on" else "0"
            try:
                subprocess.run(["blueutil", "--power", val], timeout=5)
            except FileNotFoundError:
                _applescript(f'do shell script "blueutil --power {val}"')
            return reply or f"Bluetooth: {state}"

        # --- Web ---
        elif action == "search_web":
            query = params.get("query", "")
            import urllib.parse
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            subprocess.Popen(["open", url])
            return reply or f"Ищу: {query}"

        elif action == "open_url":
            url = params.get("url", "")
            if not url.startswith("http"):
                url = "https://" + url
            subprocess.Popen(["open", url])
            return reply or f"Открываю {url}"

        elif action == "search_youtube":
            query = params.get("query", "")
            import urllib.parse
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            subprocess.Popen(["open", url])
            return reply or f"Ищу на YouTube: {query}"

        elif action == "search_maps":
            query = params.get("query", "")
            import urllib.parse
            url = f"maps://?q={urllib.parse.quote(query)}"
            subprocess.Popen(["open", url])
            return reply or f"Ищу на карте: {query}"

        # --- Files ---
        elif action == "open_folder":
            path = os.path.expanduser(params.get("path", "~/Desktop"))
            subprocess.Popen(["open", path])
            return reply or f"Открываю {path}"

        elif action == "open_file":
            path = os.path.expanduser(params.get("path", ""))
            subprocess.Popen(["open", path])
            return reply or f"Открываю файл"

        elif action == "empty_trash":
            _applescript('tell application "Finder" to empty the trash')
            return reply or "Корзина очищена"

        elif action == "show_downloads":
            subprocess.Popen(["open", os.path.expanduser("~/Downloads")])
            return reply or "Открываю загрузки"

        elif action == "show_desktop":
            # Mission Control "Show Desktop" via F11 or hot corners
            _applescript('''
                tell application "System Events"
                    key code 111 using {command down}
                end tell
            ''')
            return reply or "Показываю рабочий стол"

        # --- Media ---
        elif action == "music_control":
            act = params.get("action", "pause")
            cmd_map = {"play": "play", "pause": "pause", "next": "next track",
                       "previous": "previous track", "stop": "stop"}
            _applescript(f'tell application "Music" to {cmd_map.get(act, act)}')
            return reply or f"Музыка: {act}"

        # --- Communication ---
        elif action == "say":
            text = params.get("text", "")
            subprocess.Popen(["say", text])
            return reply or "Говорю..."

        elif action == "create_note":
            title = params.get("title", "Заметка")
            body = params.get("body", "")
            _applescript(
                f'tell application "Notes"\n'
                f'  make new note with properties {{name:"{_esc(title)}", body:"{_esc(body)}"}}\n'
                f'end tell'
            )
            return reply or f"Заметка: {title}"

        elif action == "create_reminder":
            title = params.get("title", "")
            _applescript(
                f'tell application "Reminders"\n'
                f'  make new reminder with properties {{name:"{_esc(title)}"}}\n'
                f'end tell'
            )
            return reply or f"Напоминание: {title}"

        elif action == "create_calendar_event":
            title = params.get("title", "Событие")
            date = params.get("date", "")
            time_str = params.get("time", "12:00")
            _applescript(
                f'tell application "Calendar"\n'
                f'  tell calendar "Calendar"\n'
                f'    make new event with properties '
                f'{{summary:"{_esc(title)}", start date:date "{date} {time_str}"}}\n'
                f'  end tell\n'
                f'end tell'
            )
            return reply or f"Событие: {title}"

        elif action == "send_message":
            to = params.get("to", "")
            text = params.get("text", "")
            _applescript(
                f'tell application "Messages"\n'
                f'  send "{_esc(text)}" to buddy "{_esc(to)}"\n'
                f'end tell'
            )
            return reply or f"Сообщение отправлено"

        # --- Window management ---
        elif action == "minimize_window":
            _applescript('''
                tell application "System Events"
                    keystroke "m" using command down
                end tell
            ''')
            return reply or "Окно свёрнуто"

        elif action == "fullscreen_window":
            _applescript('''
                tell application "System Events"
                    keystroke "f" using {command down, control down}
                end tell
            ''')
            return reply or "Полный экран"

        elif action == "close_window":
            _applescript('''
                tell application "System Events"
                    keystroke "w" using command down
                end tell
            ''')
            return reply or "Окно закрыто"

        elif action == "new_tab":
            _applescript('''
                tell application "System Events"
                    keystroke "t" using command down
                end tell
            ''')
            return reply or "Новая вкладка"

        elif action == "close_tab":
            _applescript('''
                tell application "System Events"
                    keystroke "w" using command down
                end tell
            ''')
            return reply or "Вкладка закрыта"

        # --- Utilities ---
        elif action == "timer":
            secs = int(params.get("seconds", 60))
            msg = params.get("message", "Таймер сработал!")
            # Run timer in background
            subprocess.Popen(["bash", "-c",
                f'sleep {secs} && osascript -e \'display notification "{_esc(msg)}" '
                f'with title "VoiceType Timer"\' && say "{_esc(msg)}"'
            ])
            mins = secs // 60
            label = f"{mins} мин" if mins > 0 else f"{secs} сек"
            return reply or f"Таймер: {label}"

        elif action == "clipboard_copy":
            text = params.get("text", "")
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            return reply or "Скопировано в буфер"

        elif action == "type_text":
            text = params.get("text", "")
            _applescript(f'''
                tell application "System Events"
                    keystroke "{_esc(text)}"
                end tell
            ''')
            return reply or "Текст набран"

        elif action == "run_shortcut":
            name = params.get("name", "")
            subprocess.Popen(["shortcuts", "run", name])
            return reply or f"Запускаю Shortcut: {name}"

        elif action == "open_settings":
            section = params.get("section", "general")
            section_map = {
                "wifi": "com.apple.wifi-settings-extension",
                "bluetooth": "com.apple.BluetoothSettings",
                "display": "com.apple.Displays-Settings.extension",
                "sound": "com.apple.Sound-Settings.extension",
                "general": "com.apple.General-Settings.extension",
                "privacy": "com.apple.settings.PrivacySecurity.extension",
                "notifications": "com.apple.Notifications-Settings.extension",
                "battery": "com.apple.Battery-Settings.extension",
                "keyboard": "com.apple.Keyboard-Settings.extension",
            }
            pane = section_map.get(section, section_map["general"])
            subprocess.Popen(["open", f"x-apple.systempreferences:{pane}"])
            return reply or f"Открываю настройки: {section}"

        # --- TickTick ---
        elif action == "ticktick_add":
            try:
                from ticktick import create_task
                title = params.get("title", "")
                content = params.get("content", "")
                priority = int(params.get("priority", 0))
                due_date = params.get("due_date")
                due_time = params.get("due_time")
                result = create_task(title, content=content, priority=priority,
                                     due_date=due_date, due_time=due_time)
                return reply or f"Задача создана: {title}"
            except Exception as e:
                return f"Ошибка TickTick: {e}"

        elif action == "ticktick_list":
            try:
                from ticktick import get_tasks
                tasks = get_tasks()
                if not tasks:
                    return "Нет активных задач"
                lines = []
                for t in tasks[:10]:
                    p = {0: "  ", 1: "🔵", 3: "🟡", 5: "🔴"}.get(t.get("priority", 0), "  ")
                    lines.append(f"{p} {t.get('title', '?')}")
                return "Задачи TickTick:\n" + "\n".join(lines)
            except Exception as e:
                return f"Ошибка TickTick: {e}"

        elif action == "ticktick_complete":
            try:
                from ticktick import get_all_tasks, complete_task
                title_query = params.get("title", "").lower()
                tasks = get_all_tasks()
                matched = None
                for t in tasks:
                    if title_query in t.get("title", "").lower():
                        matched = t
                        break
                if not matched:
                    return f"Задача не найдена: {title_query}"
                complete_task(matched.get("projectId", ""), matched["id"])
                return reply or f"Задача завершена: {matched['title']}"
            except Exception as e:
                return f"Ошибка TickTick: {e}"

        elif action == "ticktick_open":
            subprocess.Popen(["open", "-a", "TickTick"])
            return reply or "Открываю TickTick"

        # --- Calendar ---
        elif action == "open_calendar":
            subprocess.Popen(["open", "-a", "Calendar"])
            return reply or "Открываю календарь"

        elif action == "show_calendar_today":
            events = _applescript('''
                tell application "Calendar"
                    set today to current date
                    set time of today to 0
                    set tomorrow to today + 1 * days
                    set eventList to ""
                    repeat with cal in calendars
                        set evts to (every event of cal whose start date >= today and start date < tomorrow)
                        repeat with e in evts
                            set eventList to eventList & summary of e & " (" & time string of start date of e & ")" & linefeed
                        end repeat
                    end repeat
                    return eventList
                end tell
            ''')
            if events.strip():
                return f"События на сегодня:\n{events.strip()}"
            return "Нет событий на сегодня"

        else:
            return reply or f"Неизвестное действие: {action}"

    except Exception as e:
        log.error("Agent action error: %s — %s", action, e)
        return f"Ошибка: {e}"


def _applescript(script):
    """Run an AppleScript command."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0 and result.stderr:
        log.warning("AppleScript error: %s", result.stderr[:200])
    return result.stdout.strip()


def _esc(text):
    """Escape string for AppleScript."""
    return text.replace('\\', '\\\\').replace('"', '\\"')


def _get_wifi_device():
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5,
        )
        for i, line in enumerate(result.stdout.split('\n')):
            if 'Wi-Fi' in line:
                next_line = result.stdout.split('\n')[i + 1]
                if 'Device:' in next_line:
                    return next_line.split('Device:')[1].strip()
    except Exception:
        pass
    return "en0"


def _get_system_info(info_type):
    parts = []

    if info_type in ("battery", "all"):
        try:
            r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split('\n'):
                if '%' in line:
                    parts.append(f"🔋 {line.strip()}")
        except Exception:
            parts.append("🔋 Не удалось получить")

    if info_type in ("disk", "all"):
        try:
            r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
            lines = r.stdout.strip().split('\n')
            if len(lines) > 1:
                vals = lines[1].split()
                parts.append(f"💾 Диск: {vals[3]} свободно из {vals[1]}")
        except Exception:
            pass

    if info_type in ("memory", "all"):
        try:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            total = int(r.stdout.strip()) / (1024**3)
            parts.append(f"🧠 RAM: {total:.0f} GB")
        except Exception:
            pass

    if info_type in ("time", "all"):
        parts.append(f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if info_type in ("wifi", "all"):
        try:
            r = subprocess.run(
                ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.split('\n'):
                if 'SSID' in line and 'BSSID' not in line:
                    ssid = line.split(':')[1].strip()
                    parts.append(f"📶 Wi-Fi: {ssid}")
                    break
        except Exception:
            pass

    return '\n'.join(parts) if parts else "Нет данных"


# OCR via Swift + Vision framework. We compile a small Swift binary once
# and cache it, since `swift script.swift` recompiles on every invocation
# (10+ seconds, unusable for interactive use).
_OCR_SWIFT = '''import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    exit(1)
}
let req = VNRecognizeTextRequest { req, _ in
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    for o in obs {
        if let c = o.topCandidates(1).first { print(c.string) }
    }
}
req.recognitionLevel = .accurate
req.recognitionLanguages = ["ru-RU", "en-US"]
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try? handler.perform([req])
'''

_OCR_CACHE_DIR = os.path.expanduser("~/Library/Caches/VoiceType")
_OCR_BIN_PATH = os.path.join(_OCR_CACHE_DIR, "voicetype_ocr")
_OCR_COMPILE_FAILED = False


def _ensure_ocr_binary():
    """Compile Swift OCR binary once and cache it. Returns path or None."""
    global _OCR_COMPILE_FAILED
    if _OCR_COMPILE_FAILED:
        return None
    if os.path.exists(_OCR_BIN_PATH):
        return _OCR_BIN_PATH
    try:
        os.makedirs(_OCR_CACHE_DIR, exist_ok=True)
        swift_src = os.path.join(_OCR_CACHE_DIR, "voicetype_ocr.swift")
        with open(swift_src, "w") as f:
            f.write(_OCR_SWIFT)
        log.info("Компилирую OCR бинарь (одноразово)...")
        result = subprocess.run(
            ["swiftc", swift_src, "-O", "-o", _OCR_BIN_PATH],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.warning("OCR компиляция не удалась: %s", result.stderr[:200])
            _OCR_COMPILE_FAILED = True
            return None
        log.info("OCR бинарь готов: %s", _OCR_BIN_PATH)
        return _OCR_BIN_PATH
    except FileNotFoundError:
        log.warning("OCR: swiftc не установлен (нужен Xcode CLT)")
        _OCR_COMPILE_FAILED = True
        return None
    except Exception as e:
        log.warning("OCR компиляция ошибка: %s", e)
        _OCR_COMPILE_FAILED = True
        return None


def get_screen_context():
    """Capture and OCR the frontmost screen for agent context."""
    import tempfile
    tmp_path = None
    try:
        ocr_bin = _ensure_ocr_binary()
        if not ocr_bin:
            return ""

        # Take screenshot of full screen
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp_path = tmp.name
        tmp.close()

        result = subprocess.run(
            ["screencapture", "-x", "-o", tmp_path],
            capture_output=True, timeout=3
        )
        if result.returncode != 0:
            return ""

        # Run cached OCR binary
        result = subprocess.run(
            [ocr_bin, tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        ocr_text = result.stdout.strip()
        return ocr_text[:500] if ocr_text else ""
    except Exception as e:
        log.warning("OCR ошибка: %s", e)
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def parse_llm_response(text):
    """Parse LLM response — extract JSON action from text."""
    text = text.strip()

    # Remove thinking tags if present
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from code block (greedy to handle nested braces)
    m = re.search(r'```(?:json)?\s*(\{.+\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Find the outermost JSON object by brace matching
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    return {"action": "none", "params": {}, "reply": text[:300]}
