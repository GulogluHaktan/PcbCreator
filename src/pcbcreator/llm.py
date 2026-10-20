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


# Output budgets per call type: 5-10x what a normal answer needs. A runaway "reasoning" loop (seen: 3,000+ tokens
# for a 300-token Requirements record) would otherwise burn up to 25 minutes per call at ~6 tokens/s.
TOKEN_BUDGET = {"Requirements": 2500, "Architecture": 3500, "PartScoutPlan": 1500, "PartSelection": 2500,
                "PartCard": 3000, "Review": 4000, "BlockPatch": 6000, "RepairPatch": 6000}


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
        budget = min(self.max_tokens, TOKEN_BUDGET.get(schema.__name__, self.max_tokens))
        for attempt in range(self.retries + 1):
            body = {
                "model": self.model, "messages": messages,
                "temperature": min(1.0, self.temperature + 0.2 * attempt), "max_tokens": budget,
                "chat_template_kwargs": {"enable_thinking": self.thinking},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}},
            }
            resp = httpx.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout)
            if resp.status_code >= 400:
                raise LLMError(f"llama-server {resp.status_code}: {resp.text[:300]}")
            choice = resp.json()["choices"][0]
            last = choice["message"]["content"] or ""
            if choice.get("finish_reason") == "length":  # cut off at the budget: start over, a bit hotter, and be brief
                messages = messages[:2] + [{"role": "user", "content": "Your previous answer was cut off because it "
                                            "was too long. Answer again with a much shorter 'reasoning'."}]
                continue
            try:
                return schema.model_validate_json(last)
            except ValidationError as err:
                messages += [{"role": "assistant", "content": last},
                             {"role": "user", "content": f"That JSON was invalid: {err}. Return corrected JSON only."}]
        raise LLMError(f"model did not return valid {schema.__name__} JSON: {last[:300]}")
