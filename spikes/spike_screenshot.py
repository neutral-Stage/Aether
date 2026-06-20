"""Spike 4 — capture the screen (needs Screen Recording permission)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.perception import screen  # noqa: E402

if __name__ == "__main__":
    path = screen.capture_to_file()
    print("Saved screenshot to:", path)
    print("Open it to confirm Screen Recording permission is granted.")
