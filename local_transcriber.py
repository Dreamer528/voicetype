"""Local AI transcription and formatting using MLX (Apple Silicon only).

Graceful degradation: if mlx-whisper or mlx-lm are not installed,
is_available() returns False and the app falls back to cloud mode.
"""

import logging
import os
import platform

log = logging.getLogger("VoiceType")

# Model name → HuggingFace repo mapping for MLX-Whisper
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

# Default local LLM for text formatting
DEFAULT_LLM_MODEL = "mlx-community/Phi-4-mini-instruct-4bit"

# Formatting prompt (same logic as cloud version)
_FORMAT_SYSTEM_PROMPT = (
    "Ты получаешь текст, транскрибированный с аудио. "
    "Твоя задача — ТОЛЬКО отформатировать его:\n"
    "1. Расставь знаки препинания\n"
    "2. Исправь опечатки и ошибки транскрипции\n"
    "3. Разбей на абзацы если текст длинный\n"
    "4. Верни ТОЛЬКО исправленный текст\n\n"
    "ЗАПРЕЩЕНО: добавлять свои слова, комментарии, "
    "заголовки, эмодзи, фразы вроде 'продолжение следует'. "
    "Если текст короткий — верни его как есть с исправленной пунктуацией."
)


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

        log.info("Local transcription: model=%s, lang=%s", self._model_name, self.language)
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=self._model_path,
            language=self.language,
        )
        text = result.get("text", "").strip()
        if not text:
            raise RuntimeError("Empty transcription result from local model")
        return text

    def is_model_downloaded(self):
        """Check if the model weights are cached locally."""
        try:
            from huggingface_hub import try_to_load_from_cache
            # Check for a key file that indicates the model is cached
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


class LocalFormatter:
    """Text formatting using a local LLM via MLX-LM (Apple Silicon only)."""

    def __init__(self, model_name=DEFAULT_LLM_MODEL):
        self._model_name = model_name
        self._model = None
        self._tokenizer = None

    @staticmethod
    def is_available():
        """Check if mlx-lm is installed."""
        if platform.machine() != "arm64":
            return False
        try:
            import mlx_lm
            return True
        except ImportError:
            return False

    def _ensure_model(self):
        """Lazy-load the model on first use."""
        if self._model is not None:
            return
        log.info("Loading local LLM: %s", self._model_name)
        from mlx_lm import load
        self._model, self._tokenizer = load(self._model_name)
        log.info("Local LLM loaded")

    def format_text(self, raw_text, language="ru"):
        """Format transcribed text using local LLM."""
        # Short texts: basic formatting only
        words = raw_text.split()
        if len(words) < 10:
            return self._basic_format(raw_text)

        self._ensure_model()
        from mlx_lm import generate

        # Build chat-style prompt
        prompt = self._build_prompt(raw_text)
        result = generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=min(len(words) * 3 + 100, 7000),
        )
        return result.strip() if result else raw_text

    def _build_prompt(self, raw_text):
        """Build a prompt for the LLM."""
        return (
            f"<|system|>\n{_FORMAT_SYSTEM_PROMPT}\n"
            f"<|user|>\n{raw_text}\n"
            f"<|assistant|>\n"
        )

    @staticmethod
    def _basic_format(text):
        """Basic formatting for short texts."""
        text = text.strip()
        if not text:
            return text
        text = text[0].upper() + text[1:]
        if text[-1] not in '.!?':
            text += '.'
        return text

    def is_model_downloaded(self):
        """Check if the LLM model weights are cached locally."""
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(self._model_name, "config.json")
            return cached is not None and isinstance(cached, str)
        except Exception:
            return False
