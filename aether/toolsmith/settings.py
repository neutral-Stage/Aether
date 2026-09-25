"""Toolsmith settings from config.yaml (``toolsmith:``, plus the sandbox and policy bits it needs)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Settings:
    enabled: bool = True
    timeout_s: int = 60
    max_repairs: int = 2
    # A tool with internet access reads only the folders its manifest names.
    restrict_reads_with_network: bool = True
    extra_read_roots: list[str] = field(default_factory=list)
    # Tests only: run tools without the macOS sandbox (never on a real Mac).
    allow_unsandboxed: bool = False
    approved_roots: list[str] = field(default_factory=lambda: ["~"])
    network_cap: bool = True
    shell_cap: bool = True
    sandbox: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> Settings:
        raw = raw or {}
        t = raw.get("toolsmith") or {}
        caps = raw.get("capabilities") or {}
        roots = list((raw.get("policy") or {}).get("approved_file_roots") or ["~"])
        return cls(
            enabled=bool(t.get("enabled", True)),
            timeout_s=max(1, min(int(t.get("timeout_s", 60) or 60), 300)),
            max_repairs=max(0, min(int(t["max_repairs"] if t.get("max_repairs") is not None
                                       else 2), 2)),
            restrict_reads_with_network=bool(t.get("restrict_reads_with_network", True)),
            extra_read_roots=[str(p) for p in t.get("extra_read_roots") or []],
            allow_unsandboxed=bool(t.get("allow_unsandboxed", False)),
            approved_roots=[str(r) for r in roots if r],
            network_cap=bool(caps.get("network", True)),
            shell_cap=bool(caps.get("shell", True)),
            sandbox=dict(raw.get("sandbox") or {}),
        )

    @classmethod
    def load(cls) -> Settings:
        try:
            from ..core.config import load_config

            return cls.from_raw(load_config(validate=False).raw)
        except Exception:  # noqa: BLE001 — defaults without config
            return cls()
