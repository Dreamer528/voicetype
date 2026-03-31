import time
import os
from groq import Groq


class Transcriber:
    def __init__(self, api_key, base_url, language="ru", model="whisper-large-v3",
                 llm_model="llama-3.3-70b-versatile"):
        self.client = Groq(api_key=api_key, base_url=base_url)
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
                raise RuntimeError("Empty response from Whisper API")
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                if "timed out" in error_msg or "rate" in error_msg or "429" in error_msg:
                    if attempt < max_attempts:
                        time.sleep(delay * attempt)
                        continue
                raise

        raise last_error or RuntimeError(f"Transcription failed after {max_attempts} attempts")

    def format_text(self, raw_text):
        """Format raw transcription with LLM: paragraphs, punctuation, typo fixes."""
        # Short texts don't need LLM formatting
        if len(raw_text.split()) < 5:
            return raw_text

        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты получаешь текст, транскрибированный с аудио. "
                        "Твоя задача — ТОЛЬКО отформатировать его:\n"
                        "1. Расставь знаки препинания\n"
                        "2. Исправь опечатки и ошибки транскрипции\n"
                        "3. Разбей на абзацы если текст длинный\n"
                        "4. Верни ТОЛЬКО исправленный текст\n\n"
                        "ЗАПРЕЩЕНО: добавлять свои слова, комментарии, "
                        "заголовки, эмодзи, фразы вроде 'продолжение следует'. "
                        "Если текст короткий — верни его как есть с исправленной пунктуацией."
                    ),
                },
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
