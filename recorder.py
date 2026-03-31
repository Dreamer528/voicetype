import ctypes
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

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.int16


class AudioRecorder:
    def __init__(self, max_seconds=120):
        self.max_seconds = max_seconds
        self._frames = []
        self._stream = None
        self._recording = False
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.append(indata.copy())

    def start_recording(self):
        """Start recording from the default microphone."""
        with self._lock:
            if self._recording:
                return
            self._frames = []
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=self._callback,
            )
            self._stream.start()

    def stop_recording(self):
        """Stop recording and save to a temporary WAV file. Returns file path."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

        if not self._frames:
            return None

        audio_data = np.concatenate(self._frames, axis=0)

        # Trim to max duration
        max_samples = self.max_seconds * SAMPLE_RATE
        if len(audio_data) > max_samples:
            audio_data = audio_data[:max_samples]

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, SAMPLE_RATE, audio_data)
        tmp.close()
        return tmp.name

    def is_recording(self):
        return self._recording

    def get_duration(self):
        """Return current recording duration in seconds."""
        if not self._frames:
            return 0.0
        total_samples = sum(len(f) for f in self._frames)
        return total_samples / SAMPLE_RATE
