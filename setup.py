import os
import sys
from setuptools import setup

# py2app hits recursion limit on large dependency trees (torch, transformers)
sys.setrecursionlimit(10000)

# Find libportaudio.dylib from installed _sounddevice_data
frameworks = []
try:
    import _sounddevice_data
    pa_path = os.path.join(
        _sounddevice_data.__path__[0], "portaudio-binaries", "libportaudio.dylib"
    )
    if os.path.exists(pa_path):
        frameworks.append(pa_path)
        print(f"Found PortAudio: {pa_path}")
    else:
        print(f"WARNING: libportaudio.dylib not found at {pa_path}")
except ImportError:
    print("WARNING: _sounddevice_data not installed, PortAudio won't be bundled")

APP = ["app.py"]
DATA_FILES = [
    ("resources", [
        "resources/mic_idle.png",
        "resources/mic_recording.png",
        "resources/mic_processing.png",
    ]),
]
OPTIONS = {
    "argv_emulation": False,
    "frameworks": frameworks,
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "VoiceType",
        "CFBundleDisplayName": "VoiceType",
        "CFBundleIdentifier": "com.voicetype.app",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSMicrophoneUsageDescription": (
            "VoiceType needs microphone access to record your voice for transcription."
        ),
        "NSAppleEventsUsageDescription": (
            "VoiceType needs to send keystrokes to paste transcribed text."
        ),
    },
    "packages": [
        "rumps", "groq", "scipy", "numpy",
        "objc", "Quartz", "AppKit", "Foundation",
        "httpcore", "httpx", "anyio", "sniffio", "certifi", "h11", "idna",
    ],
    "includes": [
        "settings_window", "local_transcriber", "history",
    ],
    "excludes": [
        "torch", "transformers", "torchaudio", "torchvision",
        "tensorflow", "keras", "sklearn", "matplotlib",
        "PIL", "cv2", "pandas", "jupyter",
    ],
}

setup(
    name="VoiceType",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
