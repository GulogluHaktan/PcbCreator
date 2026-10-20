import httpx
import pytest

from pcbcreator.llm import LlamaServerClient, LLMError, TOKEN_BUDGET
from pcbcreator.agents import Requirements


class Resp:
    def __init__(self, content, finish="stop", status=200):
        self.status_code, self._c, self._f, self.text = status, content, finish, content

    def json(self):
        return {"choices": [{"message": {"content": self._c}, "finish_reason": self._f}]}


GOOD = ('{"summary":"s","purpose":"p","supply":"5V","interfaces":[],"constraints":[],"required":[]}')


def test_truncated_answer_is_retried_hotter_with_a_budget(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return Resp("{\"reasoning\": \"blah blah", "length") if len(calls) == 1 else Resp(GOOD)

    monkeypatch.setattr(httpx, "post", fake_post)
    out = LlamaServerClient(temperature=0.3).complete_json("sys", "user", Requirements)
    assert out.summary == "s" and len(calls) == 2
    assert calls[0]["max_tokens"] == TOKEN_BUDGET["Requirements"]  # per-call budget, not the 9000 default
    assert calls[1]["temperature"] > calls[0]["temperature"]
    assert "cut off" in calls[1]["messages"][-1]["content"]


def test_gives_up_after_the_retries(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: Resp("{", "length"))
    with pytest.raises(LLMError):
        LlamaServerClient(retries=1).complete_json("s", "u", Requirements)
