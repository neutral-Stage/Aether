"""Spike 2 — dump the Accessibility tree of the frontmost app.

Run it, then within 4 seconds click the app/window you want to inspect.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.perception import accessibility as ax  # noqa: E402

if __name__ == "__main__":
    if not ax.available():
        raise SystemExit("pyobjc not available — run on macOS with deps installed.")
    print("Focus the app you want to inspect…")
    for n in (3, 2, 1):
        print(f"  {n}…")
        time.sleep(1)
    ctx = ax.screen_context(max_elements=120)
    print(f"\nFrontmost: {ctx['frontmost_app']}  ({ctx['element_count']} elements)\n")
    print(ctx["rendered"])
