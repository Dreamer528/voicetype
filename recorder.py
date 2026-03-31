import ctypes
import logging
import os
import sys
import tempfile
import threading

# Pre-load PortAudio when running as bundled .app
# py2app puts it in Contents/Frameworks/, but sounddevice can't find it there
if getattr(sys, "frozen", False):
    _app_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    _pa = os.path.join(_app_dir, "Frameworks", "libportaudio.dylib")
    if os.path.exists(_pa):
        ctypes.cdll.LoadLibrary(_pa)

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

log = logging.getLogger("VoiceType")

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.int16


class AudioRecorder:
    def __init__(self, max_seconds=120, on_error=None):
        self.max_seconds = max_seconds
        self._on_error = on_error  # callback(error_msg: str)
        self._buffer = None
        self._write_pos = 0
        self._stream = None
        self._recording = False
        self._lock = threading.Lock()
        self._current_rms = 0.0

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Audio callback status: %s", status)
        if not self._recording:
            return
        n = len(indata)
        end = min(self._write_pos + n, len(self._buffer))
        actual = end - self._write_pos
        if actual > 0:
            self._buffer[self._write_pos:end] = indata[:actual]
            self._write_pos = end
        # Compute RMS for amplitude visualization
        try:
            self._current_rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        except Exception:
            self._current_rms = 0.0

    def start_recording(self):
        """Start recording from the default microphone."""
        with self._lock:
            if self._recording:
                return
            max_samples = self.max_seconds * SAMPLE_RATE
            self._buffer = np.zeros((max_samples, CHANNELS), dtype=DTYPE)
            self._write_pos = 0
            self._current_rms = 0.0
            self._recording = True
            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    callback=self._callback,
                )
                self._stream.start()
            except sd.PortAudioError as e:
                self._recording = False
                self._buffer = None
                log.error("Microphone error: %s", e)
                if self._on_error:
                    self._on_error(f"Ошибка микрофона: {e}")

    def stop_recording(self):
        """Stop recording and save to a temporary WAV file. Returns file path."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    log.warning("Error stopping stream: %s", e)
                self._stream = None

        if self._buffer is None or self._write_pos == 0:
            return None

        audio_data = self._buffer[:self._write_pos]
        self._buffer = None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, SAMPLE_RATE, audio_data)
        tmp.close()
        return tmp.name

    def is_recording(self):
        return self._recording

    def get_duration(self):
        """Return current recording duration in seconds."""
        return self._write_pos / SAMPLE_RATE if self._recording else 0.0

    def get_current_amplitude(self):
        """Return current RMS amplitude (0.0-1.0 normalized)."""
        # Normalize: int16 max is 32767, typical speech RMS ~1000-5000
        return min(self._current_rms / 5000.0, 1.0)

    @staticmethod
    def cleanup_file(path):
        """Remove a temporary audio file."""
        try:
            if path:
                os.unlink(path)
        except OSError:
            pass
