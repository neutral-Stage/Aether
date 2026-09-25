"""Polite proactive hints: rare, short, explained, and easy to silence. Off by default.

See docs/HINTS.md.
"""
from .core import CATEGORIES, HINT_SYSTEM, Hint, HintSettings, HintThrottle, parse_hint

__all__ = ["CATEGORIES", "HINT_SYSTEM", "Hint", "HintSettings", "HintThrottle", "parse_hint"]
