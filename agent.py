"""Voice agent for macOS control via natural language.

Receives a voice command, sends it to LLM with available tools,
then executes the returned action on the system.
"""

import json
import logging
import os
import subprocess

log = logging.getLogger("VoiceType")

# Available tools the LLM can call
TOOLS = [
    {
        "name": "open_app",
        "description": "Открыть приложение по названию",
        "params": {"app_name": "Название приложения (Safari, Telegram, и т.д.)"},
    },
    {
        "name": "close_app",
        "description": "Закрыть приложение",
        "params": {"app_name": "Название приложения"},
    },
    {
        "name": "set_volume",
        "description": "Установить громкость звука",
        "params": {"level": "Число от 0 до 100"},
    },
    {
        "name": "set_brightness",
        "description": "Установить яркость экрана",
        "params": {"level": "Число от 0 до 100"},
    },
    {
        "name": "search_web",
        "description": "Поиск в браузере (Google)",
        "params": {"query": "Поисковый запрос"},
    },
    {
        "name": "open_url",
        "description": "Открыть URL в браузере",
        "params": {"url": "URL адрес"},
    },
    {
        "name": "open_folder",
        "description": "Открыть папку в Finder",
        "params": {"path": "Путь к папке (~/Documents, ~/Desktop и т.д.)"},
    },
    {
        "name": "system_info",
        "description": "Получить информацию о системе: батарея, диск, память, время",
        "params": {"info_type": "battery | disk | memory | time | all"},
    },
    {
        "name": "toggle_dark_mode",
        "description": "Переключить тёмную/светлую тему macOS",
        "params": {},
    },
    {
        "name": "screenshot",
        "description": "Сделать скриншот экрана и сохранить на рабочий стол",
        "params": {},
    },
    {
        "name": "toggle_wifi",
        "description": "Включить/выключить Wi-Fi",
        "params": {"state": "on | off"},
    },
    {
        "name": "toggle_bluetooth",
        "description": "Включить/выключить Bluetooth",
        "params": {"state": "on | off"},
    },
    {
        "name": "empty_trash",
        "description": "Очистить корзину",
        "params": {},
    },
    {
        "name": "say",
        "description": "Произнести текст вслух (Text-to-Speech)",
        "params": {"text": "Текст для произнесения"},
    },
    {
        "name": "create_note",
        "description": "Создать заметку в приложении Notes",
        "params": {"title": "Заголовок заметки", "body": "Текст заметки"},
    },
    {
        "name": "music_control",
        "description": "Управление музыкой (play, pause, next, previous)",
        "params": {"action": "play | pause | next | previous"},
    },
    {
        "name": "sleep_display",
        "description": "Выключить дисплей (сон экрана)",
        "params": {},
    },
]


def _build_system_prompt(lang="ru"):
    """Build system prompt with available tools for the LLM."""
    tools_desc = json.dumps(TOOLS, ensure_ascii=False, indent=2)
    lang_name = "русском" if lang == "ru" else "английском"

    return f"""Ты — голосовой ассистент для управления macOS. Пользователь даёт команду голосом.

ТВОЯ ЗАДАЧА: определить, какое действие выполнить, и вернуть JSON.

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{tools_desc}

ФОРМАТ ОТВЕТА — строго JSON, без другого текста:
{{"action": "имя_инструмента", "params": {{"ключ": "значение"}}, "reply": "Краткий ответ пользователю"}}

Если команда не требует действия (просто вопрос), верни:
{{"action": "none", "params": {{}}, "reply": "Ответ на вопрос"}}

ПРИМЕРЫ:
- "Открой сафари" → {{"action": "open_app", "params": {{"app_name": "Safari"}}, "reply": "Открываю Safari"}}
- "Сделай громкость 50" → {{"action": "set_volume", "params": {{"level": 50}}, "reply": "Громкость 50%"}}
- "Найди в гугле рецепт борща" → {{"action": "search_web", "params": {{"query": "рецепт борща"}}, "reply": "Ищу в Google"}}
- "Какой заряд батареи?" → {{"action": "system_info", "params": {{"info_type": "battery"}}, "reply": "Проверяю батарею..."}}
- "Сколько будет 2+2?" → {{"action": "none", "params": {{}}, "reply": "4"}}

ВАЖНО:
- Верни ТОЛЬКО JSON, ничего больше
- Отвечай на {lang_name} языке
- Если не уверен какой инструмент — используй "none" и ответь текстом
- Параметры app_name используй на английском (Safari, Telegram, Terminal)"""


def execute_action(action_data):
    """Execute an action returned by the LLM. Returns result string."""
    action = action_data.get("action", "none")
    params = action_data.get("params", {})
    reply = action_data.get("reply", "")

    try:
        if action == "none":
            return reply

        elif action == "open_app":
            app = params.get("app_name", "")
            subprocess.Popen(["open", "-a", app])
            return reply or f"Открываю {app}"

        elif action == "close_app":
            app = params.get("app_name", "")
            _applescript(f'tell application "{app}" to quit')
            return reply or f"Закрываю {app}"

        elif action == "set_volume":
            level = int(params.get("level", 50))
            # macOS volume is 0-100 in AppleScript
            _applescript(f"set volume output volume {level}")
            return reply or f"Громкость: {level}%"

        elif action == "set_brightness":
            level = int(params.get("level", 50))
            # brightness is 0.0-1.0
            val = level / 100.0
            _applescript(f'tell application "System Events" to tell appearance preferences to set dark mode to false')
            subprocess.run(["brightness", str(val)], capture_output=True, timeout=5)
            return reply or f"Яркость: {level}%"

        elif action == "search_web":
            query = params.get("query", "")
            import urllib.parse
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            subprocess.Popen(["open", url])
            return reply or f"Ищу: {query}"

        elif action == "open_url":
            url = params.get("url", "")
            subprocess.Popen(["open", url])
            return reply or f"Открываю {url}"

        elif action == "open_folder":
            path = os.path.expanduser(params.get("path", "~/Desktop"))
            subprocess.Popen(["open", path])
            return reply or f"Открываю {path}"

        elif action == "system_info":
            info_type = params.get("info_type", "all")
            info = _get_system_info(info_type)
            return f"{reply}\n\n{info}" if reply else info

        elif action == "toggle_dark_mode":
            _applescript(
                'tell application "System Events" to tell appearance preferences '
                'to set dark mode to not dark mode'
            )
            return reply or "Тема переключена"

        elif action == "screenshot":
            desktop = os.path.expanduser("~/Desktop")
            subprocess.run(["screencapture", "-x", f"{desktop}/screenshot.png"],
                           timeout=5)
            return reply or "Скриншот сохранён на рабочий стол"

        elif action == "toggle_wifi":
            state = params.get("state", "on")
            device = _get_wifi_device()
            if device:
                subprocess.run(["networksetup", f"-setairportpower", device, state],
                               timeout=5)
            return reply or f"Wi-Fi: {state}"

        elif action == "toggle_bluetooth":
            state = params.get("state", "on")
            cmd = "PowerOn" if state == "on" else "PowerOff"
            _applescript(f'tell application "System Events" to do shell script "blueutil --{state}"')
            return reply or f"Bluetooth: {state}"

        elif action == "empty_trash":
            _applescript(
                'tell application "Finder" to empty the trash'
            )
            return reply or "Корзина очищена"

        elif action == "say":
            text = params.get("text", "")
            subprocess.Popen(["say", text])
            return reply or f"Говорю: {text}"

        elif action == "create_note":
            title = params.get("title", "Заметка")
            body = params.get("body", "")
            _applescript(
                f'tell application "Notes"\n'
                f'  tell account "iCloud"\n'
                f'    make new note at folder "Notes" '
                f'with properties {{name:"{title}", body:"{body}"}}\n'
                f'  end tell\n'
                f'end tell'
            )
            return reply or f"Заметка создана: {title}"

        elif action == "music_control":
            act = params.get("action", "pause")
            _applescript(f'tell application "Music" to {act}')
            return reply or f"Музыка: {act}"

        elif action == "sleep_display":
            subprocess.Popen(["pmset", "displaysleepnow"])
            return reply or "Экран выключен"

        else:
            return reply or f"Неизвестное действие: {action}"

    except Exception as e:
        log.error("Agent action error: %s", e)
        return f"Ошибка выполнения: {e}"


def _applescript(script):
    """Run an AppleScript command."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0 and result.stderr:
        log.warning("AppleScript error: %s", result.stderr[:200])
    return result.stdout.strip()


def _get_wifi_device():
    """Get Wi-Fi network device name."""
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.split('\n')
        for i, line in enumerate(lines):
            if 'Wi-Fi' in line and i + 1 < len(lines):
                device_line = lines[i + 1]
                if 'Device:' in device_line:
                    return device_line.split('Device:')[1].strip()
    except Exception:
        pass
    return "en0"  # default


def _get_system_info(info_type):
    """Gather system information."""
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
            parts.append("💾 Не удалось получить")

    if info_type in ("memory", "all"):
        try:
            import psutil
            mem = psutil.virtual_memory()
            used_gb = mem.used / (1024**3)
            total_gb = mem.total / (1024**3)
            parts.append(f"🧠 RAM: {used_gb:.1f} / {total_gb:.1f} GB ({mem.percent}%)")
        except ImportError:
            try:
                r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
                parts.append(f"🧠 {r.stdout[:100]}")
            except Exception:
                parts.append("🧠 Не удалось получить")

    if info_type in ("time", "all"):
        from datetime import datetime
        now = datetime.now()
        parts.append(f"🕐 {now.strftime('%d.%m.%Y %H:%M')}")

    return '\n'.join(parts) if parts else "Нет данных"


def parse_llm_response(text):
    """Parse LLM response — extract JSON action from text."""
    text = text.strip()

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in markdown code block
    import re
    m = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object in text
    m = re.search(r'\{[^{}]*"action"[^{}]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: treat entire response as plain text reply
    return {"action": "none", "params": {}, "reply": text}
