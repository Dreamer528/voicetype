"""Local AI transcription using MLX-Whisper (Apple Silicon only).

Graceful degradation: if mlx-whisper is not installed,
is_available() returns False and the app falls back to cloud mode.
Formatting always uses cloud (Groq LLM) — local LLMs don't handle Russian well enough.
"""

import logging
import os
import platform

log = logging.getLogger("VoiceType")

# Model name -> HuggingFace repo mapping for MLX-Whisper
WHISPER_MODELS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}

# Approximate download sizes in MB
WHISPER_SIZES = {
    "tiny": 75, "base": 140, "small": 460, "medium": 1500, "large-v3": 3000,
}


class LocalTranscriber:
    """Speech-to-text using MLX-Whisper (Apple Silicon only)."""

    def __init__(self, model_name="base", language="ru"):
        self._model_name = model_name
        self.language = language
        self._model_path = WHISPER_MODELS.get(model_name, WHISPER_MODELS["base"])

    @staticmethod
    def is_available():
        """Check if mlx-whisper is installed and running on Apple Silicon."""
        if platform.machine() != "arm64":
            return False
        try:
            import mlx_whisper
            return True
        except ImportError:
            return False

    def transcribe(self, audio_path):
        """Transcribe audio file using local MLX-Whisper model."""
        import mlx_whisper

        log.info("Локальная транскрипция: модель=%s, язык=%s", self._model_name, self.language)
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=self._model_path,
            language=self.language,
        )
        text = result.get("text", "").strip()
        if not text:
            raise RuntimeError("Пустой результат транскрипции от локальной модели")
        return text

    def is_model_downloaded(self):
        """Check if the model weights are cached locally."""
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(self._model_path, "config.json")
            return cached is not None and isinstance(cached, str)
        except Exception:
            return False

    def get_model_size_mb(self):
        """Get approximate download size in MB."""
        return WHISPER_SIZES.get(self._model_name, 140)

    def cleanup(self, audio_path):
        """Remove temporary audio file."""
        try:
            os.unlink(audio_path)
        except OSError:
            pass
