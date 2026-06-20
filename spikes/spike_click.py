"""Spike 3 — synthetic input (CGEvent).

Within 4 seconds, focus a text field (e.g. a new TextEdit document or Notes).
The spike will type a line and press Return. Harmless, but type into a scratch doc.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.effectors import input as kbd  # noqa: E402

if __name__ == "__main__":
    if not kbd.available():
        raise SystemExit("Quartz not available — run on macOS with deps installed.")
    print("Focus a SCRATCH text field now (TextEdit/Notes)…")
    for n in (4, 3, 2, 1):
        print(f"  {n}…")
        time.sleep(1)
    kbd.type_text("Aether input spike — hello! ")
    kbd.type_text("(typed via CGEvent)")
    kbd.press_key("return")
    print("Done. If you saw the text appear, synthetic input works.")
