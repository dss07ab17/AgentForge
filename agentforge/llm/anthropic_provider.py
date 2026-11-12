"""Claude provider using the Messages API over plain HTTPS (no SDK dependency)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .base import LLMError, LLMProvider

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("AGENTFORGE_MODEL", "claude-sonnet-5-5")


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    is_live = True

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, max_calls: int = 120,
                 timeout_s: int = 180, transport_retries: int = 3) -> None:
        super().__init__(max_calls=max_calls)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.timeout_s = timeout_s
        self.transport_retries = transport_retries

    def _complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        last: Exception | None = None
        for attempt in range(self.transport_retries):
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - fixed https URL
                    payload = json.loads(resp.read())
                return "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 529):
                    raise LLMError(f"Anthropic API error {exc.code}: {exc.read()[:300]!r}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
            time.sleep(2 ** attempt)
        raise LLMError(f"Anthropic API unavailable after {self.transport_retries} attempts: {last}")
