"""Explicit semantic-test double; not a native fit or publication proof.

These suites use fake 8-dimensional models. Physical admission has separate
controlled proof, default-owner IPC, and runtime-before-enqueue tests.
"""
from types import SimpleNamespace
from logchat.rag.embedding_subdivision import validate_subdivision


def install(monkeypatch):
    async def plan(self, batch, *, deadline):
        return SimpleNamespace(batch=batch, lineage=validate_subdivision(batch, batch))
    monkeypatch.setattr('logchat.local.model_profile.ModelProfile.plan_embedding_batch', plan)
