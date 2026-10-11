"""Request-bound exact generation counting protocol; no tokenizer estimates.

Implementations must own verified rendering, special tokens and runtime identity.
This module deliberately supplies no provider-specific tokenizer implementation.
"""
from dataclasses import dataclass
import hashlib
import re
from typing import Protocol

from pipeline.model_transport import model_json


@dataclass(frozen=True)
class GenerationTokenIdentity:
    model: str
    model_digest: str
    tokenizer_digest: str
    renderer_digest: str
    runtime_version: str

    def __post_init__(self):
        if (not isinstance(self.model, str) or not self.model
                or not isinstance(self.runtime_version, str) or not self.runtime_version
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for value in (self.model_digest, self.tokenizer_digest, self.renderer_digest))):
            raise ValueError("invalid_generation_token_identity")


def request_digest(body):
    return hashlib.sha256(model_json(body).encode()).hexdigest()


@dataclass(frozen=True)
class GenerationTokenCount:
    identity: GenerationTokenIdentity
    request_sha256: str
    input_tokens: int
    context_tokens: int = 8192
    output_reserve: int = 1200

    def fits(self, body, identity):
        # Count is the complete native input including special tokens, not just
        # the payload. Both fixed budgets must match the submitted request.
        if (self.identity != identity or body['model'] != identity.model
                or self.request_sha256 != request_digest(body)
                or type(self.input_tokens) is not int or self.input_tokens <= 0
                or type(self.context_tokens) is not int or self.context_tokens != 8192
                or type(self.output_reserve) is not int or self.output_reserve != 1200
                or body['options'] != {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1200}):
            raise ValueError("generation_token_proof_mismatch")
        return self.input_tokens + self.output_reserve <= self.context_tokens


class GenerationTokenCounter(Protocol):
    """Trusted pinned adapter, initialized once outside candidate planning.

    count must perform no generation or per-candidate metadata discovery.
    It verifies the effective runtime identity and counts native rendering of
    the complete immutable request, with every special token. Cancellation
    must propagate. A stale identity is an error, never an oversized result.
    """

    @property
    def identity(self) -> GenerationTokenIdentity: ...

    async def count(self, request_bytes: bytes) -> GenerationTokenCount: ...
