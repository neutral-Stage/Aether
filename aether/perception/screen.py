"""Screen capture.

Phase 0 uses the built-in `screencapture` CLI for reliability (it also triggers
the Screen Recording permission prompt correctly). ScreenCaptureKit is the
production path (see the spec, section 6.2) and slots in behind this interface.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
import time
from pathlib import Path


def capture_to_file(path: str | None = None) -> str:
    """Capture the full screen to a PNG and return its path."""
    if path is None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = str(Path(tempfile.gettempdir()) / f"aether-screen-{ts}.png")
    # -x = no sound, -C = capture cursor off by default
    subprocess.run(["screencapture", "-x", path], check=True, timeout=15)
    return path


def capture_base64() -> str:
    """Capture the screen and return base64 PNG (for sending to a vision model)."""
    path = capture_to_file()
    data = Path(path).read_bytes()
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
    return base64.b64encode(data).decode("ascii")


if __name__ == "__main__":
    p = capture_to_file()
    print("Saved screenshot:", p)
