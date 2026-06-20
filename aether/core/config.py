"""Configuration + secrets loading."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging

import yaml
from dotenv import load_dotenv

from .providers import collect_api_keys, has_cloud_backend

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "configs" / "config.schema.json"


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    # secrets
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    api_keys: dict[str, str | None] = field(default_factory=dict)

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # convenience accessors
    @property
    def model(self) -> str:
        return self.get("llm", "model", default="claude-sonnet-4-6")

    @property
    def provider(self) -> str:
        return self.get("llm", "provider", default="anthropic")

    @property
    def max_tokens(self) -> int:
        return int(self.get("llm", "max_tokens", default=1024))

    @property
    def temperature(self) -> float:
        return float(self.get("llm", "temperature", default=0))

    @property
    def max_steps(self) -> int:
        return int(self.get("agent", "max_steps", default=12))

    @property
    def careful(self) -> bool:
        return bool(self.get("agent", "careful", default=False))

    @property
    def narrate(self) -> bool:
        return bool(self.get("agent", "narrate", default=True))

    @property
    def tts(self) -> str:
        return self.get("voice", "tts", default="macos")

    @property
    def tts_voice(self) -> str:
        return self.get("voice", "tts_voice", default="Samantha")

    @property
    def tts_model(self) -> str:
        return self.get("voice", "tts_model", default="canopylabs/orpheus-v1-english")

    @property
    def stt(self) -> str:
        return self.get("voice", "stt", default="none")

    @property
    def stt_model(self) -> str:
        return self.get("voice", "stt_model", default="whisper-large-v3-turbo")

    @property
    def groq_api_key(self) -> str | None:
        return self.api_keys.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")

    @property
    def record_seconds(self) -> int:
        return int(self.get("voice", "record_seconds", default=8))

    @property
    def hud_enabled(self) -> bool:
        return bool(self.get("hud", "enabled", default=True))

    @property
    def stop_hotkey(self) -> str:
        return str(self.get("stop", "hotkey", default="ctrl+shift+s"))

    @property
    def stop_escape_double_tap(self) -> bool:
        return bool(self.get("stop", "escape_double_tap", default=True))

    @property
    def knowledge_enabled(self) -> bool:
        return bool(self.get("knowledge", "enabled", default=True))

    @property
    def router_enabled(self) -> bool:
        return bool(self.get("router", "enabled", default=True))

    @property
    def memory_enabled(self) -> bool:
        return bool(self.get("memory", "enabled", default=True))

    @property
    def memory_db_path(self) -> str | None:
        return self.get("memory", "db_path")

    @property
    def vision_enabled(self) -> bool:
        return bool(self.get("vision", "enabled", default=True))

    @property
    def browser_headless(self) -> bool:
        return bool(self.get("browser", "headless", default=True))

    @property
    def local_only(self) -> bool:
        return bool(self.get("agent", "local_only", default=False))

    def has_cloud_llm(self) -> bool:
        """True if any configured cloud provider has an API key."""
        if self.anthropic_api_key:
            return True
        router_raw: dict[str, Any] = {}
        router_path = self.get("router", "config_path")
        if router_path:
            p = ROOT / str(router_path)
            if p.exists():
                router_raw = yaml.safe_load(p.read_text()) or {}
        return has_cloud_backend(router_raw, self.api_keys)


def validate_config(raw: dict[str, Any], *, strict: bool = False) -> list[str]:
    """Validate config against JSON Schema. Returns warning messages."""
    if not SCHEMA_PATH.exists():
        return []
    try:
        import jsonschema
    except ImportError:
        if strict:
            raise RuntimeError(
                "jsonschema is required for strict config validation "
                "(pip install jsonschema)"
            ) from None
        return []

    import json

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
    if not errors:
        return []
    messages = [
        f"config{''.join(f'[{p!r}]' if isinstance(p, int) else f'.{p}' for p in err.path)}: {err.message}"
        for err in errors
    ]
    if strict:
        raise ValueError("Invalid config.yaml:\n" + "\n".join(messages))
    for msg in messages:
        log.warning("Config schema: %s", msg)
    return messages


def load_config(path: str | Path | None = None, *, validate: bool = True) -> Config:
    """Load .env + config.yaml into a Config object."""
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    if validate and raw:
        validate_config(raw)
    return Config(
        raw=raw,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        api_keys=collect_api_keys(),
    )
