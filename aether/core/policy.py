"""Safety policy gate — action classification and confirmation (§6.7)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..effectors import shell as shell_fx
from .security import (
    InjectionScan,
    InjectionSeverity,
    redact_secrets_extended,
    scan_injection,
    wrap_untrusted,
)

if TYPE_CHECKING:
    from ..tools.registry import ToolSpec

# AX / text patterns indicating secrets — redact before cloud calls
_SECRET_PATTERNS = [
    re.compile(r"(?i)password"),
    re.compile(r"(?i)secure\s*text"),
    re.compile(r"(?i)ssn|social\s*security"),
    re.compile(r"(?i)credit\s*card"),
    re.compile(r"(?i)api[_-]?key"),
]
_SECRET_VALUE_RE = re.compile(
    r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*\S+"
)

# Shell patterns beyond shell_fx defaults
_EXTRA_DESTRUCTIVE = [
    r"\brm\s+-", r"\btrash\b", r"\bdelete\b", r"\bsend\b.*\bmail\b",
    r"\bchmod\b", r"\bmv\b.*/dev/null", r"\bstripe\b", r"\bpayment\b",
    r"\bcurl\b.*\|\s*(sh|bash)", r"\bwget\b.*\|\s*(sh|bash)",
]
_EXTRA_DESTRUCTIVE_RE = re.compile("|".join(_EXTRA_DESTRUCTIVE), re.IGNORECASE)


def normalize_file_roots(roots: list[str] | None) -> list[str]:
    """Expand ``~`` and narrow legacy ``/Users`` default to the current home."""
    home = str(Path.home())
    if not roots:
        return [home]
    expanded: list[str] = []
    for root in roots:
        raw = str(root).strip()
        if raw in ("~", "$HOME", "/Users", "/Users/"):
            expanded.append(home)
        else:
            expanded.append(str(Path(raw).expanduser()))
    return expanded or [home]


@dataclass
class PolicyConfig:
    careful: bool = False
    capabilities: dict[str, bool] | None = None
    approved_file_roots: list[str] = field(default_factory=lambda: [
        str(Path.home()),
    ])
    network_allowlist: list[str] = field(default_factory=list)
    redact_secrets: bool = True
    block_injection_goals: bool = True
    flag_injection_in_context: bool = True
    wrap_untrusted_context: bool = True

    def allows(self, permission: str) -> bool:
        caps = self.capabilities or {}
        if permission not in caps:
            return True
        return bool(caps[permission])


class Policy:
    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()

    def impact_of(self, spec: "ToolSpec", args: dict) -> str:
        name = spec.name

        if name == "run_shell":
            cmd = args.get("command", "")
            if shell_fx.is_destructive(cmd) or _EXTRA_DESTRUCTIVE_RE.search(cmd):
                return "destructive"
            return "reversible"

        if name == "mail_compose":
            # Composing is reversible; sending would be destructive (not a tool yet)
            return "reversible"

        if name in ("browser_navigate", "browser_click", "browser_fill"):
            url = args.get("url", "")
            if url and not self._network_allowed(url):
                return "destructive"
            return "reversible"

        if name == "remember_fact":
            return "reversible"

        if spec.impact == "destructive":
            return "destructive"

        # AppleScript with send/delete keywords
        if name == "run_applescript":
            src = (args.get("source") or "").lower()
            if any(k in src for k in ("send", "delete", "remove", "trash", "push")):
                return "destructive"

        return spec.impact

    def scan_injection(self, text: str) -> InjectionScan:
        return scan_injection(text)

    def should_block_goal(self, goal: str) -> bool:
        if not self.config.block_injection_goals:
            return False
        return scan_injection(goal).blocked

    def requires_confirm(self, spec: "ToolSpec", args: dict) -> bool:
        if self.config.careful and spec.name not in (
            "get_screen_context", "finish", "analyze_screen", "remember_fact",
        ):
            return True
        impact = self.impact_of(spec, args)
        if impact == "destructive":
            return True
        # Injection in tool arguments (e.g. pasted screen text) → confirm
        if self.config.flag_injection_in_context:
            for val in args.values():
                if isinstance(val, str):
                    scan = scan_injection(val)
                    if scan.severity in (InjectionSeverity.HIGH, InjectionSeverity.MEDIUM):
                        return True
        return False

    def allows_tool(self, spec: "ToolSpec") -> bool:
        return self.config.allows(spec.permission)

    def allows_shell_path(self, command: str) -> bool:
        """Check if shell command touches paths outside approved roots."""
        if not self.config.approved_file_roots:
            return True
        # Heuristic: flag absolute paths outside roots
        for match in re.finditer(r"(/[\w./-]+)", command):
            path = match.group(1)
            if not any(path.startswith(root) for root in self.config.approved_file_roots):
                return False
        return True

    def _network_allowed(self, url: str) -> bool:
        if not self.config.careful or not self.config.network_allowlist:
            return True
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False
        return any(
            host == allowed or host.endswith("." + allowed)
            for allowed in self.config.network_allowlist
        )

    def redact_text(self, text: str) -> str:
        """Redact secrets before sending context to cloud models."""
        if not self.config.redact_secrets or not text:
            return text
        redacted = _SECRET_VALUE_RE.sub(r"\1: [REDACTED]", text)
        lines = []
        for line in redacted.splitlines():
            if any(p.search(line) for p in _SECRET_PATTERNS):
                if ":" in line:
                    key, _, _ = line.partition(":")
                    lines.append(f"{key}: [REDACTED]")
                else:
                    lines.append("[REDACTED SECURE FIELD]")
            else:
                lines.append(line)
        redacted = "\n".join(lines)
        return redact_secrets_extended(redacted)

    def prepare_context_for_model(self, text: str) -> str:
        """Sanitize perceived content: redact secrets, wrap as untrusted data."""
        if not text:
            return ""
        cleaned = self.redact_text(text)
        if self.config.wrap_untrusted_context:
            return wrap_untrusted(cleaned)
        return cleaned

    def confirm(self, description: str) -> bool:
        prompt = f"About to {description}. Proceed? [y/N] "
        try:
            ans = input(f"\n⚠️  {prompt}").strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")
