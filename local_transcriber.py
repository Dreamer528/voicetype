"""Local AI transcription using MLX-Whisper (Apple Silicon only).

Architecture: persistent worker subprocess with auto-shutdown.
- Worker stays alive for IDLE_TIMEOUT seconds after last transcription
- Model loads once, reused for subsequent requests
- Preload triggered when recording STARTS (model ready by recording end)
- After IDLE_TIMEOUT of no requests, worker exits → RAM freed

Communication: stdin (JSON commands) → stdout (JSON responses).
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

log = logging.getLogger("VoiceType")

WHISPER_MODELS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}

WHISPER_SIZES = {
    "tiny": 75, "base": 140, "small": 460, "medium": 1500, "large-v3": 3000,
}

# Worker auto-shutdown after 5 minutes of no requests
IDLE_TIMEOUT = 300

# Worker script: runs in system Python, reads JSON commands from stdin
_WORKER_SCRIPT = '''
import warnings, os, sys, json, time
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

IDLE_TIMEOUT = {idle_timeout}
model_path = sys.argv[1]
model = None

def ensure_model():
    global model
    if model is not None:
        return
    import mlx_whisper
    # Warm up: transcribe silence to force model load + Metal compile
    import tempfile, numpy as np
    from scipy.io import wavfile
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wavfile.write(tmp.name, 16000, np.zeros(1600, dtype=np.int16))
    tmp.close()
    mlx_whisper.transcribe(tmp.name, path_or_hf_repo=model_path)
    os.unlink(tmp.name)
    model = model_path  # mark as loaded

def transcribe(audio_path, language):
    ensure_model()
    import mlx_whisper
    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=model_path, language=language)
    return result.get("text", "")

# Signal ready
sys.stdout.write(json.dumps({{"status": "ready"}}) + "\\n")
sys.stdout.flush()

import select
last_activity = time.time()

while True:
    # Check for input with 1-second timeout
    ready, _, _ = select.select([sys.stdin], [], [], 1.0)
    if ready:
        line = sys.stdin.readline()
        if not line:
            break  # stdin closed
        try:
            cmd = json.loads(line.strip())
            action = cmd.get("action")
            if action == "preload":
                ensure_model()
                sys.stdout.write(json.dumps({{"status": "loaded"}}) + "\\n")
                sys.stdout.flush()
            elif action == "transcribe":
                text = transcribe(cmd["audio_path"], cmd.get("language", "ru"))
                sys.stdout.write(json.dumps({{"status": "ok", "text": text}}, ensure_ascii=False) + "\\n")
                sys.stdout.flush()
            elif action == "quit":
                break
            last_activity = time.time()
        except Exception as e:
            sys.stdout.write(json.dumps({{"status": "error", "error": str(e)}}) + "\\n")
            sys.stdout.flush()
    else:
        # Check idle timeout
        if time.time() - last_activity > IDLE_TIMEOUT:
            break
'''


def _find_system_python():
    """Find a system Python 3 that has mlx-whisper installed."""
    candidates = [
        "/Library/Developer/CommandLineTools/usr/bin/python3",
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
        shutil.which("python3"),
    ]
    for py in candidates:
        if not py or not os.path.exists(py):
            continue
        try:
            clean_env = {k: v for k, v in os.environ.items()
                         if k not in ("PYTHONPATH", "PYTHONHOME", "RESOURCEPATH")}
            clean_env["LANG"] = "en_US.UTF-8"
            clean_env["PYTHONIOENCODING"] = "utf-8"
            clean_env["PYTHONWARNINGS"] = "ignore"
            result = subprocess.run(
                [py, "-c", "import mlx_whisper; print('ok')"],
                capture_output=True, text=True, timeout=30, env=clean_env,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                log.info("Системный Python с MLX-Whisper: %s", py)
                return py
            else:
                log.warning("Python %s: returncode=%s stderr=%s",
                            py, result.returncode, result.stderr[:200])
        except Exception as e:
            log.warning("Python %s не подходит: %s", py, e)
    return None


_system_python = None


class LocalTranscriber:
    """Speech-to-text using MLX-Whisper with persistent worker subprocess."""

    def __init__(self, model_name="base", language="ru"):
        self._model_name = model_name
        self.language = language
        self._model_path = WHISPER_MODELS.get(model_name, WHISPER_MODELS["base"])
        self._worker = None
        self._worker_lock = threading.Lock()

    @staticmethod
    def is_available():
        """Check if mlx-whisper works on this machine."""
        global _system_python
        if platform.machine() != "arm64":
            return False
        try:
            import mlx_whisper
            _system_python = sys.executable
            return True
        except Exception:
            pass
        _system_python = _find_system_python()
        if _system_python:
            return True
        log.warning("MLX-Whisper не найден ни в бандле, ни в системном Python")
        return False

    def _get_clean_env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME", "RESOURCEPATH")}
        env["LANG"] = "en_US.UTF-8"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONWARNINGS"] = "ignore"
        return env

    def _ensure_worker(self):
        """Start worker subprocess if not running."""
        if self._worker and self._worker.poll() is None:
            return True  # already running

        global _system_python
        py = _system_python
        if not py:
            return False

        script = _WORKER_SCRIPT.format(idle_timeout=IDLE_TIMEOUT)
        try:
            self._worker = subprocess.Popen(
                [py, "-c", script, self._model_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._get_clean_env(),
            )
            # Wait for "ready" signal
            line = self._worker.stdout.readline()
            if not line:
                log.error("Worker не запустился")
                return False
            resp = json.loads(line.decode("utf-8", errors="replace"))
            if resp.get("status") == "ready":
                log.info("Worker запущен (PID %s)", self._worker.pid)
                return True
            log.error("Worker ответил: %s", resp)
            return False
        except Exception as e:
            log.error("Ошибка запуска worker: %s", e)
            return False

    def _send_command(self, cmd, timeout=300):
        """Send a JSON command to the worker and get response."""
        with self._worker_lock:
            if not self._ensure_worker():
                raise RuntimeError("Worker не запущен")
            try:
                data = json.dumps(cmd, ensure_ascii=False) + "\n"
                self._worker.stdin.write(data.encode("utf-8"))
                self._worker.stdin.flush()
                line = self._worker.stdout.readline()
                if not line:
                    self._worker = None
                    raise RuntimeError("Worker завершился неожиданно")
                return json.loads(line.decode("utf-8", errors="replace"))
            except (BrokenPipeError, OSError):
                self._worker = None
                raise RuntimeError("Потеряна связь с worker")

    def preload(self):
        """Preload model in background (call when recording starts)."""
        def _do_preload():
            try:
                resp = self._send_command({"action": "preload"})
                if resp.get("status") == "loaded":
                    log.info("Модель предзагружена")
                elif resp.get("status") == "error":
                    log.warning("Ошибка предзагрузки: %s", resp.get("error"))
            except Exception as e:
                log.warning("Предзагрузка не удалась: %s", e)
        threading.Thread(target=_do_preload, daemon=True).start()

    def transcribe(self, audio_path):
        """Transcribe audio file using persistent worker."""
        log.info("Локальная транскрипция: модель=%s, язык=%s",
                 self._model_name, self.language)

        # Try direct import first (fastest, works in script mode)
        try:
            import mlx_whisper
            result = mlx_whisper.transcribe(
                audio_path,
                path_or_hf_repo=self._model_path,
                language=self.language,
            )
            text = result.get("text", "").strip()
            if not text:
                raise RuntimeError("Пустой результат транскрипции")
            return text
        except ImportError:
            pass

        # Use persistent worker (for .app bundle)
        resp = self._send_command({
            "action": "transcribe",
            "audio_path": audio_path,
            "language": self.language,
        })
        if resp.get("status") == "ok":
            text = resp.get("text", "").strip()
            if not text:
                raise RuntimeError("Пустой результат транскрипции")
            return text
        raise RuntimeError(resp.get("error", "Неизвестная ошибка worker"))

    def shutdown(self):
        """Gracefully stop the worker."""
        with self._worker_lock:
            if self._worker and self._worker.poll() is None:
                try:
                    data = json.dumps({"action": "quit"}) + "\n"
                    self._worker.stdin.write(data.encode("utf-8"))
                    self._worker.stdin.flush()
                    self._worker.wait(timeout=5)
                except Exception:
                    self._worker.kill()
                log.info("Worker остановлен")
                self._worker = None

    def cleanup(self, audio_path):
        try:
            os.unlink(audio_path)
        except OSError:
            pass
