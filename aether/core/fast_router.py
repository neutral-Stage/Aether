"""Answer simple, unambiguous requests without a model call.

"Open Safari", "quit Mail", "show my Downloads", "volume 30", "mute", "what
app is this": a model round trip adds a second or more and costs money for a
request whose meaning is certain. These are matched here with strict
patterns and run through the same registry tools (and the same policy check)
as the agent would use.

Deliberately narrow: an app must be installed under exactly the spoken name
or a known alias ("chrome" → Google Chrome), so "open my notes about the
trip" is left to the agent. Anything that fails falls through to the full
agent loop.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", "/System/Library/CoreServices",
            "~/Applications")

# Spoken name → app bundle name. Only used when that app is installed.
ALIASES = {
    "chrome": "Google Chrome", "vs code": "Visual Studio Code", "vscode": "Visual Studio Code",
    "code": "Visual Studio Code", "settings": "System Settings",
    "system preferences": "System Settings", "preferences": "System Settings",
    "word": "Microsoft Word", "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook", "teams": "Microsoft Teams", "iterm": "iTerm",
    "iterm2": "iTerm", "app store": "App Store", "activity monitor": "Activity Monitor",
    "quicktime": "QuickTime Player", "facetime": "FaceTime", "whatsapp": "WhatsApp",
    "zoom": "zoom.us", "firefox": "Firefox", "brave": "Brave Browser", "edge": "Microsoft Edge",
}

FOLDERS = {
    "downloads": "~/Downloads", "documents": "~/Documents", "desktop": "~/Desktop",
    "applications": "/Applications", "home": "~", "pictures": "~/Pictures",
    "music": "~/Music", "movies": "~/Movies", "home folder": "~",
}

_LEAD_RE = re.compile(r"^(?:(?:hey|ok|okay)\s+aether[,!]?\s*|please\s+|can you\s+|could you\s+|"
                      r"would you\s+|will you\s+)+", re.I)
_TAIL_RE = re.compile(r"(?:\s+(?:please|for me|now|thanks|thank you))+$", re.I)
_OPEN_RE = re.compile(r"^(?:open|launch|start|switch to|go to|bring up|show)\s+"
                      r"(?:the\s+|up\s+)?(?P<app>.+?)(?:\s+app(?:lication)?)?$", re.I)
_QUIT_RE = re.compile(r"^(?:quit|close|exit)\s+(?:the\s+|out of\s+)?(?P<app>.+?)"
                      r"(?:\s+app(?:lication)?)?$", re.I)
_FOLDER_RE = re.compile(r"^(?:open|show|go to|show me)\s+(?:my\s+|the\s+)?(?P<folder>"
                        + "|".join(sorted(map(re.escape, FOLDERS), key=len, reverse=True))
                        + r")(?:\s+folder)?$", re.I)
_VOLUME_SET_RE = re.compile(r"^(?:set\s+)?(?:the\s+)?(?:volume|sound)\s+(?:to\s+|at\s+)?"
                            r"(?P<n>\d{1,3})\s*(?:%|percent)?$", re.I)
_VOLUME_STEP_RE = re.compile(r"^(?:turn\s+(?:the\s+)?(?:volume|sound|it)\s+(?P<d1>up|down)|"
                             r"(?:volume|sound)\s+(?P<d2>up|down)|(?P<d3>louder|quieter))$", re.I)
_MUTE_RE = re.compile(r"^(?P<un>un)?mute(?:\s+(?:the\s+)?(?:sound|audio|volume))?$", re.I)
_FRONT_RE = re.compile(r"^(?:what(?:'s| is)\s+(?:this\s+app|the\s+(?:front(?:most)?|current|"
                       r"active)\s+app)|(?:what|which)\s+app\s+is\s+(?:this|open|in\s+front|"
                       r"frontmost|active))$", re.I)


@dataclass
class FastIntent:
    kind: str                     # open_app | quit_app | open_folder | volume | frontmost
    tool: str | None              # registry tool to run (None: answer-only)
    args: dict = field(default_factory=dict)


def normalize(goal: str) -> str:
    text = " ".join(str(goal or "").strip().split())
    text = text.rstrip(".!?").strip()
    text = _LEAD_RE.sub("", text)
    return _TAIL_RE.sub("", text).strip()


@lru_cache(maxsize=1)
def installed_apps() -> dict[str, str]:
    """{lowercased name: bundle name} for apps in the standard folders."""
    found: dict[str, str] = {}
    for d in APP_DIRS:
        base = Path(os.path.expanduser(d))
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".app":
                found.setdefault(entry.stem.lower(), entry.stem)
    return found


def resolve_app(phrase: str) -> str | None:
    """Installed app for a spoken name, exact or via ALIASES; None otherwise."""
    p = " ".join(phrase.strip().strip("\"'").split()).lower()
    p = re.sub(r"^(?:my|the)\s+", "", p)
    apps = installed_apps()
    if p in apps:
        return apps[p]
    alias = ALIASES.get(p)
    if alias and alias.lower() in apps:
        return apps[alias.lower()]
    return None


def match(goal: str) -> FastIntent | None:
    text = normalize(goal)
    if not text or len(text) > 60:
        return None
    if _FRONT_RE.match(text):
        return FastIntent("frontmost", None)
    m = _FOLDER_RE.match(text)
    if m:
        return FastIntent("open_folder", "finder_go_to",
                          {"path": FOLDERS[m.group("folder").lower()]})
    m = _VOLUME_SET_RE.match(text)
    if m:
        return FastIntent("volume", "set_volume", {"level": min(100, int(m.group("n")))})
    m = _VOLUME_STEP_RE.match(text)
    if m:
        d = (m.group("d1") or m.group("d2") or m.group("d3") or "").lower()
        return FastIntent("volume", "set_volume",
                          {"change": 10 if d in ("up", "louder") else -10})
    m = _MUTE_RE.match(text)
    if m:
        return FastIntent("volume", "set_volume", {"muted": not bool(m.group("un"))})
    m = _QUIT_RE.match(text)
    if m:
        app = resolve_app(m.group("app"))
        return FastIntent("quit_app", "quit_app", {"name": app}) if app else None
    m = _OPEN_RE.match(text)
    if m:
        app = resolve_app(m.group("app"))
        return FastIntent("open_app", "open_app", {"name": app}) if app else None
    return None
