"""LLM access. The pipeline only needs `complete_json`, so tests use a scripted fake.

Production client talks to a local llama-server (OpenAI-compatible API) and asks for
schema-constrained JSON, so the model cannot emit anything but a valid Module/plan
shape; semantic validity is then the linter's job.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: type[M]) -> M: ...


class LlamaServerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", model: str = "bonsai",
                 temperature: float = 0.3, max_tokens: int = 9000, timeout: float = 3600.0,
                 retries: int = 2, thinking: bool = False):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.retries, self.thinking = retries, thinking

    def complete_json(self, system: str, user: str, schema: type[M]) -> M:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = ""
        for _ in range(self.retries + 1):
            body = {
                "model": self.model, "messages": messages,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "chat_template_kwargs": {"enable_thinking": self.thinking},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}},
            }
            resp = httpx.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout)
            if resp.status_code >= 400:
                raise LLMError(f"llama-server {resp.status_code}: {resp.text[:300]}")
            last = resp.json()["choices"][0]["message"]["content"] or ""
            try:
                return schema.model_validate_json(last)
            except ValidationError as err:
                messages += [{"role": "assistant", "content": last},
                             {"role": "user", "content": f"That JSON was invalid: {err}. Return corrected JSON only."}]
        raise LLMError(f"model did not return valid {schema.__name__} JSON: {last[:300]}")
