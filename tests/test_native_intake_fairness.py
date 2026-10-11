"""Admission fairness without queuing any original events."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from logchat.local.rag_runtime import _FairIntakeSlot
from logchat.rag.contracts import SourceIdentity


def test_retry_order_survives_hot_source_reacquisition_and_duplicate_retries():
    slot = _FairIntakeSlot()
    assert slot.acquire(scope="hot")
    assert not slot.acquire(scope="cold")
    assert not slot.acquire(scope="third")
    for _ in range(100):
        assert not slot.acquire(scope="cold")
    slot.release()
    assert not slot.acquire(scope="hot")
    # Settings and raw drain retain exclusion and respect reserved retries.
    assert not slot.acquire()
    assert slot.acquire(scope="cold")
    assert not slot.acquire(scope="cold")
    slot.release()
    assert not slot.acquire(scope="hot")
    assert slot.acquire(scope="third")
    slot.release()
    assert slot.acquire(scope="hot")
    slot.release()
    assert slot.acquire(scope="cold")
    slot.release()
    assert slot.acquire()
    slot.release()
    with pytest.raises(ValueError, match="without_reservation"):
        slot.release()


def test_abandoned_turns_expire_but_retries_renew_without_reordering():
    now = [0.]
    slot = _FairIntakeSlot(clock=lambda: now[0])
    assert slot.acquire()
    assert not slot.acquire(scope="abandoned")
    assert not slot.acquire(scope="retrying")
    now[0] = 9
    assert not slot.acquire(scope="retrying")
    slot.release()
    assert not slot.acquire(scope="retrying")
    now[0] = 10
    assert slot.acquire(scope="retrying")
    slot.release()
    assert slot.acquire()
    slot.release()


def test_waiting_sources_are_bounded_and_overflow_remains_retryable():
    slot = _FairIntakeSlot(max_waiters=2)
    assert slot.acquire()
    assert not slot.acquire(scope="one")
    assert not slot.acquire(scope="two")
    for i in range(1000):
        assert not slot.acquire(scope=f"overflow-{i}")
    assert len(slot._waiting) == 2
    slot.release()
    assert slot.acquire(scope="one")
    assert not slot.acquire(scope="overflow-0")
    slot.release()
    assert slot.acquire(scope="two")
    slot.release()
    assert slot.acquire(scope="overflow-0")
    slot.release()


def test_simultaneous_threads_cannot_overlap_reservations():
    slot = _FairIntakeSlot()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda i: slot.acquire(scope=str(i)), range(16)))
    assert sum(results) == 1
    assert len(slot._waiting) == 15
    slot.release()


def test_runtime_reservations_use_full_scope_and_state_directory(tmp_path, monkeypatch):
    from logchat.local import rag_runtime
    slot = _FairIntakeSlot()
    monkeypatch.setattr(rag_runtime, "_INTAKE_SLOT", slot)
    identities = [SourceIdentity("owner", "project", "env", "source"),
                  SourceIdentity("other-owner", "project", "env", "source"),
                  SourceIdentity("owner", "other-project", "env", "source"),
                  SourceIdentity("owner", "project", "other-env", "source")]
    runtimes = []
    for identity in identities + identities[:1]:
        runtime = object.__new__(rag_runtime.NativeRAGRuntime)
        runtime.store = SimpleNamespace(state_dir=tmp_path / ("other" if len(runtimes) == 4 else "state"))
        runtime.identity = lambda project, source, identity=identity: identity
        runtimes.append(runtime)
    assert slot.acquire()
    for runtime in runtimes:
        assert not runtime._reserve_intake("project", "source")
    assert len(slot._waiting) == 5
    slot.release()
    for runtime in runtimes:
        assert runtime._reserve_intake("project", "source")
        slot.release()
