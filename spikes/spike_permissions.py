"""Spike 1 — verify TCC permissions (Accessibility, Screen Recording)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.core import permissions  # noqa: E402

if __name__ == "__main__":
    st = permissions.report(prompt=True)
    print("\nResult:", "OK" if st.accessibility else "Accessibility needed.")
