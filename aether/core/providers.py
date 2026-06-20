"""LLM provider factory — resolves router.yaml roles to concrete clients."""
from __future__ import annotations

import logging
import os
from typing import Any

from .llm import GoogleGeminiClient, LLM, LLMBackend, LocalHTTPClient, OpenAICompatibleClient

log = logging.getLogger(__name__)

# Env var names used by provider entries in configs/router.yaml
KNOWN_API_KEY_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "FIREWORKS_API_KEY",
    "KILO_API_KEY",
    "KIE_API_KEY",
    "ZAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


def collect_api_keys() -> dict[str, str | None]:
    """Load provider API keys from the environment."""
    keys: dict[str, str | None] = {}
    for env_name in KNOWN_API_KEY_ENVS:
        keys[env_name] = os.getenv(env_name)
    # Allow GEMINI_API_KEY as alias for GOOGLE_API_KEY
    if not keys.get("GOOGLE_API_KEY") and keys.get("GEMINI_API_KEY"):
        keys["GOOGLE_API_KEY"] = keys["GEMINI_API_KEY"]
    return keys


def resolve_api_key(role_cfg: dict[str, Any], api_keys: dict[str, str | None]) -> str | None:
    env_name = str(role_cfg.get("api_key_env") or "").strip()
    if not env_name:
        return None
    return api_keys.get(env_name) or os.getenv(env_name)


def merge_role_config(router_raw: dict[str, Any], role: str) -> dict[str, Any]:
    """Merge a role block with its referenced provider template."""
    roles = router_raw.get("roles") or {}
    role_cfg = dict(roles.get(role) or {})
    provider_name = role_cfg.pop("provider", None)
    if provider_name:
        providers = router_raw.get("providers") or {}
        base = dict(providers.get(provider_name) or {})
        if not base:
            log.warning("Role %s references unknown provider %r", role, provider_name)
        base.update(role_cfg)
        role_cfg = base
    return role_cfg


def create_client(
    role_cfg: dict[str, Any],
    *,
    api_keys: dict[str, str | None] | None = None,
    role_name: str = "unknown",
) -> LLMBackend | None:
    """Instantiate an LLM backend from a merged role/provider config."""
    keys = api_keys or collect_api_keys()
    backend = str(role_cfg.get("backend", "anthropic")).lower()
    model = str(role_cfg.get("model", ""))
    max_tokens = int(role_cfg.get("max_tokens", 1024))
    temperature = float(role_cfg.get("temperature", 0))

    if backend == "http":
        return LocalHTTPClient(
            endpoint=str(role_cfg.get("endpoint", "http://localhost:11434/api/chat")),
            model=model or "llama3.2:3b",
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=float(role_cfg.get("timeout_seconds", 30)),
        )

    api_key = resolve_api_key(role_cfg, keys)
    if backend == "anthropic":
        if not api_key:
            log.warning("Skipping role %s: %s not set", role_name, role_cfg.get("api_key_env"))
            return None
        return LLM(
            api_key=api_key,
            model=model or "claude-sonnet-4-6",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    if backend in {"openai", "openai_compatible"}:
        if not api_key:
            log.warning("Skipping role %s: %s not set", role_name, role_cfg.get("api_key_env"))
            return None
        base_url = str(role_cfg.get("base_url") or "https://api.openai.com/v1")
        extra_headers = dict(role_cfg.get("extra_headers") or {})
        provider_label = str(role_cfg.get("provider_label") or role_name)
        return OpenAICompatibleClient(
            api_key=api_key,
            model=model or "gpt-4o-mini",
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            backend_name=provider_label,
            extra_headers=extra_headers or None,
        )

    if backend == "google":
        if not api_key:
            log.warning("Skipping role %s: %s not set", role_name, role_cfg.get("api_key_env"))
            return None
        return GoogleGeminiClient(
            api_key=api_key,
            model=model or "gemini-2.0-flash",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    log.warning("Unknown backend %r for role %s", backend, role_name)
    return None


def has_cloud_backend(router_raw: dict[str, Any], api_keys: dict[str, str | None] | None = None) -> bool:
    """True if any configured cloud role has a usable API key."""
    keys = api_keys or collect_api_keys()
    for role in ("cloud_frontier", "vision"):
        cfg = merge_role_config(router_raw, role)
        backend = str(cfg.get("backend", "anthropic")).lower()
        if backend == "http":
            continue
        if backend == "ocr_only":
            continue
        if resolve_api_key(cfg, keys):
            return True
    return False
