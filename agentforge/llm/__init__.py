from __future__ import annotations

import os

from .base import LLMError, LLMProvider, OfflineProvider, parse_json


def build_provider(name: str, max_calls: int) -> LLMProvider:
    if name == "auto":
        name = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "offline"
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(max_calls=max_calls)
    if name == "offline":
        return OfflineProvider(max_calls=max_calls)
    raise ValueError(f"unknown LLM provider '{name}'")


__all__ = ["LLMError", "LLMProvider", "OfflineProvider", "build_provider", "parse_json"]
