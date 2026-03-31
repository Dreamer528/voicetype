#!/usr/bin/env python3
"""Generate simple menu bar icons for VoiceType (18x18 PNG template images)."""

import struct
import zlib
import os


def create_png(width, height, pixels):
    """Create a minimal PNG file from RGBA pixel data."""
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = make_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))

    raw_data = b""
    for y in range(height):
        raw_data += b"\x00"  # filter byte
        for x in range(width):
            raw_data += bytes(pixels[y][x])

    idat = make_chunk(b"IDAT", zlib.compress(raw_data))
    iend = make_chunk(b"IEND", b"")

    return header + ihdr + idat + iend


def draw_mic(color, size=18):
    """Draw a simple microphone icon."""
    pixels = [[(0, 0, 0, 0)] * size for _ in range(size)]
    r, g, b = color

    # Mic head (oval) - rows 2-8, cols 6-11
    for y in range(2, 9):
        for x in range(6, 12):
            # Oval shape
            cx, cy = 8.5, 5.0
            dx = (x - cx) / 3.0
            dy = (y - cy) / 3.5
            if dx * dx + dy * dy <= 1.0:
                pixels[y][x] = (r, g, b, 255)

    # Mic stem - rows 9-11, cols 8-9
    for y in range(9, 12):
        for x in range(8, 10):
            pixels[y][x] = (r, g, b, 255)

    # Mic arc (U shape) - rows 7-11
    for y in range(7, 12):
        cx, cy = 8.5, 7.0
        for x in range(4, 14):
            dx = (x - cx) / 5.0
            dy = (y - cy) / 4.5
            dist = dx * dx + dy * dy
            if 0.7 < dist < 1.1 and y >= 7:
                pixels[y][x] = (r, g, b, 255)

    # Stand base - row 13-14, cols 6-11
    for y in range(13, 15):
        for x in range(6, 12):
            pixels[y][x] = (r, g, b, 255)

    # Stand pole - rows 12-13, cols 8-9
    for y in range(12, 14):
        for x in range(8, 10):
            pixels[y][x] = (r, g, b, 255)

    return pixels


def main():
    resources_dir = os.path.join(os.path.dirname(__file__), "resources")
    os.makedirs(resources_dir, exist_ok=True)

    # Idle: black mic (template image for macOS menu bar)
    idle_pixels = draw_mic((0, 0, 0), 18)
    with open(os.path.join(resources_dir, "mic_idle.png"), "wb") as f:
        f.write(create_png(18, 18, idle_pixels))

    # Recording: red mic
    rec_pixels = draw_mic((255, 59, 48), 18)
    with open(os.path.join(resources_dir, "mic_recording.png"), "wb") as f:
        f.write(create_png(18, 18, rec_pixels))

    # Processing: orange mic
    proc_pixels = draw_mic((255, 149, 0), 18)
    with open(os.path.join(resources_dir, "mic_processing.png"), "wb") as f:
        f.write(create_png(18, 18, proc_pixels))

    print("Icons created in", resources_dir)


if __name__ == "__main__":
    main()
