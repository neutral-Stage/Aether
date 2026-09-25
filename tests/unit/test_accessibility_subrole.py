"""aether/perception/accessibility.py: subrole plumbing and the AX messaging timeout.

These run without pyobjc (this suite runs on Linux), so they only exercise the
parts that don't need a live AX tree: the dataclass shape and the guarded,
never-raising entry points.
"""
from __future__ import annotations

from aether.perception import accessibility as ax


def test_element_has_a_subrole_field_defaulting_to_empty() -> None:
    el = ax.Element(0, "AXTextField", "Password", "", True, 0, 0, 10, 10)
    assert el.subrole == ""
    el2 = ax.Element(0, "AXTextField", "Password", "", True, 0, 0, 10, 10, subrole="AXSecureTextField")
    assert el2.subrole == "AXSecureTextField"


def test_focused_summary_reports_a_subrole_key() -> None:
    summary = ax.focused_summary()
    assert set(summary) == {"role", "subrole", "title", "value"}


def test_set_messaging_timeout_never_raises_without_pyobjc() -> None:
    assert ax.set_messaging_timeout(1.0) is ax.available()
