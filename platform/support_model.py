"""Explicit, bounded support-model boundary; no ambient credentials or fallback.

The real provider adapter must be wired by the operator after entitlement review.
The deterministic adapter is for tests only and never presented as a live LLM.
"""
from dataclasses import dataclass
from typing import Protocol, Sequence


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    simulated: bool = False


class Model(Protocol):
    def reply(self, turns: Sequence[Turn], *, max_output_tokens: int) -> Reply: ...


class DisabledModel:
    def reply(self, turns, *, max_output_tokens):
        raise ModelUnavailable('Platform support model is not configured; use the setup checklist.')


class FakeModel:
    """Explicitly injected deterministic fixture; never selected by environment."""
    def reply(self, turns, *, max_output_tokens):
        validate_request(turns, max_output_tokens)
        return Reply('TEST ONLY: open Settings to configure your own model; do not paste secrets in chat.',
                     'test/fake', 20, 24, True)


def validate_request(turns, max_output_tokens):
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 2048:
        raise ValueError('Invalid support output bound')
    if not 1 <= len(turns) <= 40:
        raise ValueError('Invalid support context length')
    if any(t.role not in ('system', 'user', 'assistant') or not isinstance(t.content, str) for t in turns):
        raise ValueError('Invalid support turn')
    if sum(len(t.content) for t in turns) > 32000:
        raise ValueError('Support context is too large')


def checked_reply(model, turns, *, max_output_tokens=512):
    validate_request(turns, max_output_tokens)
    response = model.reply(tuple(turns), max_output_tokens=max_output_tokens)
    if not isinstance(response, Reply) or not response.text or len(response.text) > 16000:
        raise ModelUnavailable('Invalid support model response')
    for value in (response.input_tokens, response.output_tokens):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ModelUnavailable('Invalid provider usage')
    return response
