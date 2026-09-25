"""What a self-written tool is allowed to do, stated up front and approved.

The manifest is the contract the user approves. The sandbox enforces it on
every run, and a repair can change the code but never the manifest.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PREFIX = "my_"
PARAM_TYPES = frozenset({"string", "number", "integer", "boolean"})
MAX_PARAMS = 8
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_PARAM_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")


def tool_name(raw: str) -> str:
    """'Resize images' → 'my_resize_images'."""
    slug = re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")
    if slug.startswith(PREFIX):
        slug = slug[len(PREFIX):]
    slug = slug[:40].strip("_")
    if slug and not slug[0].isalpha():
        slug = "t_" + slug
    return PREFIX + (slug or "tool")


def _real(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _inside(path: str, roots: list[str]) -> bool:
    real = _real(path)
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)


@dataclass
class ToolManifest:
    name: str
    description: str
    params: dict[str, dict[str, str]] = field(default_factory=dict)   # name → {type, description}
    required: list[str] = field(default_factory=list)
    network: bool = False                 # may it reach the internet?
    write_dirs: list[str] = field(default_factory=list)   # besides its own scratch folder
    # Enforced only for tools with network access: a tool that can reach the
    # internet reads only these folders (plus write_dirs), so it cannot send
    # the rest of your files anywhere.
    read_dirs: list[str] = field(default_factory=list)
    how: str = ""                         # implementation notes, kept for repairs
    version: int = 1
    code_sha256: str = ""
    created_at: float = 0.0

    def validate(self, approved_roots: list[str] | None = None) -> list[str]:
        errors = []
        if not self.name.startswith(PREFIX) or not _NAME_RE.match(self.name[len(PREFIX):]):
            errors.append(f"name must look like {PREFIX}something (lowercase, digits, _)")
        if not self.description.strip() or len(self.description) > 400:
            errors.append("description must be 1-400 characters")
        if len(self.params) > MAX_PARAMS:
            errors.append(f"at most {MAX_PARAMS} parameters")
        for pname, spec in self.params.items():
            if not _PARAM_RE.match(pname):
                errors.append(f"bad parameter name {pname!r}")
            if spec.get("type") not in PARAM_TYPES:
                errors.append(f"parameter {pname!r} needs a type in {sorted(PARAM_TYPES)}")
        for r in self.required:
            if r not in self.params:
                errors.append(f"required parameter {r!r} is not declared")
        roots = [_real(x) for x in approved_roots or []]
        for kind, dirs in (("write_dirs", self.write_dirs), ("read_dirs", self.read_dirs)):
            if len(dirs) > 5:
                errors.append(f"at most 5 {kind}")
            for d in dirs:
                if not str(d).strip() or not Path(str(d)).expanduser().is_absolute():
                    errors.append(f"{kind} entry {d!r} must be an absolute path or start with ~")
                elif _real(d) == _real("~") and kind == "write_dirs":
                    errors.append("write_dirs cannot be your whole home folder")
                elif roots and not _inside(d, roots):
                    errors.append(f"{kind} entry {d!r} is outside the approved folders")
        return errors

    def json_schema(self) -> dict[str, Any]:
        return {"type": "object",
                "properties": {k: {"type": v.get("type", "string"),
                                   "description": v.get("description", "")}
                               for k, v in self.params.items()},
                "required": list(self.required)}

    def capabilities(self) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        """What the sandbox grants; a repair must keep this identical."""
        return (self.network, tuple(sorted(self.write_dirs)), tuple(sorted(self.read_dirs)))

    def capability_lines(self) -> list[str]:
        if self.network:
            reads = ", ".join(self.read_dirs + [d for d in self.write_dirs
                                                if d not in self.read_dirs])
            lines = ["can reach the internet",
                     f"can read only {reads}" if reads else "cannot read your files"]
        else:
            lines = ["no internet",
                     "can read your files, except passwords, keys and browser data"]
        lines.append("writes only its own scratch folder" if not self.write_dirs else
                     "can write in " + ", ".join(self.write_dirs))
        lines.append("cannot start other programs, control apps or change settings")
        return lines

    def capability_text(self) -> str:
        return "; ".join(self.capability_lines())

    def signature(self) -> str:
        """my_x(path: string, width?: integer)."""
        parts = [f"{k}{'' if k in self.required else '?'}: {v.get('type', 'string')}"
                 for k, v in self.params.items()]
        return f"{self.name}({', '.join(parts)})"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolManifest:
        params = data.get("params") or {}
        return cls(name=str(data.get("name", "")), description=str(data.get("description", "")),
                   params={str(k): {"type": str(v.get("type", "string")),
                                    "description": str(v.get("description", ""))}
                           for k, v in params.items() if isinstance(v, dict)}
                   if isinstance(params, dict) else {},
                   required=[str(r) for r in data.get("required") or []],
                   network=bool(data.get("network", False)),
                   write_dirs=[str(d) for d in data.get("write_dirs") or []],
                   read_dirs=[str(d) for d in data.get("read_dirs") or []],
                   how=str(data.get("how", "") or "")[:2000],
                   version=int(data.get("version", 1) or 1),
                   code_sha256=str(data.get("code_sha256", "")),
                   created_at=float(data.get("created_at", 0) or 0))
