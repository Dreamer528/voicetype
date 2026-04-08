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

import collections

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

log = logging.getLogger("VoiceType")

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.int16

# Number of amplitude samples to keep for waveform visualization
AMPLITUDE_HISTORY_SIZE = 64


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
        self._amplitude_history = collections.deque(
            [0.0] * AMPLITUDE_HISTORY_SIZE, maxlen=AMPLITUDE_HISTORY_SIZE
        )

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Статус аудио-callback: %s", status)
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
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
            self._current_rms = rms
            self._amplitude_history.append(min(rms / 5000.0, 1.0))
        except Exception:
            self._current_rms = 0.0

    def start_recording(self):
        """Start recording from the default microphone.

        Buffer setup runs synchronously (instant). The actual sd.InputStream
        creation runs in a background thread because PortAudio can hang for
        seconds (Bluetooth, device switching) and would freeze the main thread.
        """
        with self._lock:
            if self._recording:
                return
            max_samples = self.max_seconds * SAMPLE_RATE
            self._buffer = np.zeros((max_samples, CHANNELS), dtype=DTYPE)
            self._write_pos = 0
            self._current_rms = 0.0
            self._amplitude_history = collections.deque(
                [0.0] * AMPLITUDE_HISTORY_SIZE, maxlen=AMPLITUDE_HISTORY_SIZE
            )
            self._recording = True

        # Open the audio stream off the main thread.
        def _try_open():
            return sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=self._callback,
            )

        def _open_stream():
            stream = None
            try:
                try:
                    stream = _try_open()
                    stream.start()
                except Exception as e1:
                    # PortAudio is in a bad state (PaErrorCode -9986 etc.)
                    # Often happens after audio device switch (Bluetooth).
                    # Try to recover by re-initializing PortAudio.
                    log.warning("Микрофон не открылся (%s), перезапускаю PortAudio...", e1)
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception as reinit_err:
                        log.warning("PortAudio re-init: %s", reinit_err)
                    stream = _try_open()
                    stream.start()
                    log.info("Микрофон восстановлен после re-init")

                with self._lock:
                    if self._recording:
                        self._stream = stream
                    else:
                        # User already stopped — discard
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
            except Exception as e:
                with self._lock:
                    self._recording = False
                    self._buffer = None
                log.error("Ошибка микрофона: %s", e)
                if self._on_error:
                    self._on_error(f"Микрофон недоступен. Попробуйте переключить аудио-устройство.")

        threading.Thread(target=_open_stream, daemon=True).start()

    def stop_recording(self):
        """Stop recording and save to a temporary WAV file. Returns file path."""
        stream_to_close = None
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            stream_to_close = self._stream
            self._stream = None

        # Close stream OUTSIDE the lock in a fire-and-forget daemon thread.
        # Never join/wait — PortAudio stream.stop() can hang (Bluetooth, device
        # switch) and would block the main thread, freezing the entire UI.
        # Audio data is already in self._buffer, so we don't need the stream anymore.
        if stream_to_close:
            def _close():
                try:
                    stream_to_close.stop()
                    stream_to_close.close()
                except Exception as e:
                    log.warning("Ошибка при остановке записи: %s", e)
            threading.Thread(target=_close, daemon=True).start()

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
        return min(self._current_rms / 5000.0, 1.0)

    def get_amplitude_history(self):
        """Return list of recent amplitude values (0.0-1.0) for waveform."""
        return list(self._amplitude_history)

    @staticmethod
    def cleanup_file(path):
        """Remove a temporary audio file."""
        try:
            if path:
                os.unlink(path)
        except OSError:
            pass
