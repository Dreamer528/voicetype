import re
import time
import os
import httpx
from groq import Groq

# Filler words to strip from raw transcription before LLM formatting.
# Pattern matches whole words only (word boundaries), case-insensitive.
_FILLER_WORDS_RU = [
    r"э+[мм]+", r"м+м+", r"а+м+", r"хм+",       # эм, ммм, ам, хм
    r"ну+", r"вот", r"короче", r"типа",           # ну, вот, короче, типа
    r"как\s*бы", r"значит", r"ладно",             # как бы, значит, ладно
    r"так\s*сказать", r"в\s*общем",               # так сказать, в общем
    r"слушай",                                     # слушай
]
_FILLER_WORDS_EN = [
    r"u+[hm]+", r"u+m+", r"e+r+", r"a+h+",       # uh, um, er, ah
    r"like", r"you\s*know", r"i\s*mean",           # like, you know, I mean
    r"basically", r"literally", r"actually",       # basically, literally
    r"so+", r"well",                               # so, well
]
_FILLER_RE = re.compile(
    r"\b(?:" + "|".join(_FILLER_WORDS_RU + _FILLER_WORDS_EN) + r")\b[,.]?\s*",
    re.IGNORECASE,
)


def strip_fillers(text):
    """Remove filler words from transcribed text."""
    if not text:
        return ""
    cleaned = _FILLER_RE.sub(" ", text)
    # Collapse multiple spaces
    cleaned = re.sub(r"  +", " ", cleaned).strip()
    return cleaned


class Transcriber:
    def __init__(self, api_key, base_url, language="ru", model="whisper-large-v3",
                 llm_model="llama-3.3-70b-versatile"):
        self.client = Groq(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self.language = language
        self.model = model
        self.llm_model = llm_model

    def transcribe(self, audio_path, max_attempts=3):
        """Transcribe audio file via Groq Whisper through proxy. Returns raw text."""
        delay = 2
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                with open(audio_path, "rb") as f:
                    result = self.client.audio.transcriptions.create(
                        file=("audio.wav", f),
                        model=self.model,
                        language=self.language,
                        response_format="text",
                    )
                text = result.strip() if isinstance(result, str) else str(result).strip()
                if text:
                    return text
                if attempt < max_attempts:
                    time.sleep(delay)
                    continue
                raise RuntimeError("Пустой ответ от Whisper API")
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                if "timed out" in error_msg or "rate" in error_msg or "429" in error_msg:
                    if attempt < max_attempts:
                        time.sleep(delay * attempt)
                        continue
                raise

        raise last_error or RuntimeError(f"Транскрипция не удалась после {max_attempts} попыток")

    @staticmethod
    def _basic_format(text):
        """Basic formatting for short texts: capitalize + punctuate."""
        text = text.strip()
        if not text:
            return text
        text = text[0].upper() + text[1:]
        if text[-1] not in '.!?':
            text += '.'
        return text

    def format_text(self, raw_text, app_context=""):
        """Format raw transcription with LLM: paragraphs, punctuation, typo fixes."""
        # Strip filler words before processing
        raw_text = strip_fillers(raw_text)

        # Short texts: basic formatting only (no LLM call needed)
        if len(raw_text.split()) < 10:
            return self._basic_format(raw_text)

        system_prompt = (
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
        if app_context:
            system_prompt += (
                f"\n\nКОНТЕКСТ: текст вводится в {app_context}. "
                "Адаптируй стиль и длину под этот контекст."
            )

        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text},
            ],
            temperature=0.3,
            max_tokens=7000,
        )
        return response.choices[0].message.content

    def transcribe_and_format(self, audio_path):
        """Full pipeline: transcribe audio, then format with LLM."""
        raw_text = self.transcribe(audio_path)
        formatted = self.format_text(raw_text)
        return formatted

    def cleanup(self, audio_path):
        """Remove temporary audio file."""
        try:
            os.unlink(audio_path)
        except OSError:
            pass
