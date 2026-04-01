"""Local AI transcription using MLX-Whisper (Apple Silicon only).

MLX requires native Metal libraries that can't be bundled with py2app.
Instead, we call the system Python (which has mlx-whisper installed)
via subprocess. This works both in .app bundle and script mode.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys

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

# Script that runs in system Python to do actual transcription
_TRANSCRIBE_SCRIPT = '''
import warnings, os, sys, json
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"
audio_path = sys.argv[1]
model_path = sys.argv[2]
language = sys.argv[3]
import mlx_whisper
result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=model_path, language=language)
print(json.dumps({"text": result.get("text", "")}, ensure_ascii=False))
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
            # Clean env: remove py2app bundle paths so system Python uses its own packages
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
                log.warning("Python %s: returncode=%s stderr=%s", py, result.returncode, result.stderr[:200])
        except Exception as e:
            log.warning("Python %s не подходит: %s", py, e)
            continue
    return None


_system_python = None


class LocalTranscriber:
    """Speech-to-text using MLX-Whisper via system Python subprocess."""

    def __init__(self, model_name="base", language="ru"):
        self._model_name = model_name
        self.language = language
        self._model_path = WHISPER_MODELS.get(model_name, WHISPER_MODELS["base"])

    @staticmethod
    def is_available():
        """Check if mlx-whisper works on this machine."""
        global _system_python
        if platform.machine() != "arm64":
            return False
        # First try direct import (works when running as script, not .app)
        try:
            import mlx_whisper
            _system_python = sys.executable
            return True
        except Exception:
            pass
        # Fallback: find system Python with mlx-whisper
        _system_python = _find_system_python()
        if _system_python:
            log.info("MLX-Whisper найден в системном Python: %s", _system_python)
            return True
        log.warning("MLX-Whisper не найден ни в бандле, ни в системном Python")
        return False

    def transcribe(self, audio_path):
        """Transcribe audio file using MLX-Whisper."""
        global _system_python
        log.info("Локальная транскрипция: модель=%s, язык=%s", self._model_name, self.language)

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
                raise RuntimeError("Пустой результат транскрипции от локальной модели")
            return text
        except ImportError:
            pass

        # Subprocess fallback for .app bundle
        if not _system_python:
            raise RuntimeError("Системный Python с mlx-whisper не найден")

        try:
            clean_env = {k: v for k, v in os.environ.items()
                         if k not in ("PYTHONPATH", "PYTHONHOME", "RESOURCEPATH")}
            clean_env["LANG"] = "en_US.UTF-8"
            clean_env["PYTHONIOENCODING"] = "utf-8"
            clean_env["PYTHONWARNINGS"] = "ignore"
            result = subprocess.run(
                [_system_python, "-c", _TRANSCRIBE_SCRIPT,
                 audio_path, self._model_path, self.language],
                capture_output=True, timeout=300, env=clean_env,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError(f"Ошибка транскрипции: {stderr[:200]}")
            # Find JSON in stdout (may have warnings before it)
            json_start = stdout.find("{")
            if json_start < 0:
                raise RuntimeError(f"Нет JSON в ответе: {stdout[:200]}")
            data = json.loads(stdout[json_start:])
            text = data.get("text", "").strip()
            if not text:
                raise RuntimeError("Пустой результат транскрипции от локальной модели")
            return text
        except subprocess.TimeoutExpired:
            raise RuntimeError("Таймаут локальной транскрипции (>120с)")

    def is_model_downloaded(self):
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(self._model_path, "config.json")
            return cached is not None and isinstance(cached, str)
        except Exception:
            return False

    def get_model_size_mb(self):
        return WHISPER_SIZES.get(self._model_name, 140)

    def cleanup(self, audio_path):
        try:
            os.unlink(audio_path)
        except OSError:
            pass
