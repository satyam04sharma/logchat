"""Optional telemetry cannot change model outcomes or leak intake admission."""
import asyncio
import json
import logging

import pytest
from pipeline.models import ModelUnavailable
from logchat.local import rag_runtime
from tests.test_native_compact_persistence import configured, event


class RaisingHandler(logging.Handler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.levels = []

    def emit(self, record):
        if record.getMessage().startswith('native_intake '):
            value = json.loads(record.getMessage().removeprefix('native_intake '))
            if self.kind == '*' or value['kind'] == self.kind:
                self.levels.append(record.levelno)
                raise RuntimeError('synthetic logging failure')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['admitted', 'stage_completed', 'model_started', 'model_finished', 'finished', '*'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
async def test_raising_info_and_warning_handlers_preserve_outcomes_and_slot(
        configured, monkeypatch, caplog, kind, outcome):
    store, project, source, model = configured
    slot = rag_runtime._FairIntakeSlot()
    monkeypatch.setattr(rag_runtime, '_INTAKE_SLOT', slot)
    logger = logging.getLogger(rag_runtime.__name__)
    caplog.set_level(logging.INFO, logger=rag_runtime.__name__)
    handler = RaisingHandler(kind)
    logger.addHandler(handler)
    original = event(source)
    try:
        if outcome == 'failure':
            model.fail = True
        elif outcome == 'cancel':
            model.started, model.release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(store.rag_runtime.ingest_async(project['id'], source['id'], [original]))
        if outcome == 'cancel':
            await model.started.wait()
            task.cancel()
        if outcome == 'success':
            receipt = await task
            assert receipt['accepted'] == 1 and receipt['state'] == 'durably_prepared'
        elif outcome == 'failure':
            with pytest.raises(ModelUnavailable) as error:
                await task
            assert str(error.value) == 'compact_intake_unavailable_retry_required'
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
        assert handler.levels  # INFO explicitly enabled, faults actually exercised.
        if kind in ('model_finished', 'finished', '*') and outcome != 'success':
            assert logging.WARNING in handler.levels
        assert not slot._held
        with store.rag_runtime.backend.connect() as con:
            assert con.execute('SELECT count(*) FROM rag_schedule_jobs').fetchone()[0] == (outcome == 'success')
        model.fail = False
        model.started = model.release = None
        receipt = await store.rag_runtime.ingest_async(project['id'], source['id'], [original])
        assert receipt['accepted'] == 1 and not slot._held
        assert receipt['deduplicated'] == (outcome == 'success')
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['constructor', 'uuid'])
async def test_diagnostics_initialization_failure_is_nonfatal_and_releases_slot(configured, monkeypatch, fault):
    store, project, source, model = configured
    slot = rag_runtime._FairIntakeSlot()
    monkeypatch.setattr(rag_runtime, '_INTAKE_SLOT', slot)
    def broken():
        raise RuntimeError('synthetic diagnostic initialization failure')
    with monkeypatch.context() as patch:
        if fault == 'constructor':
            patch.setattr(rag_runtime, '_IntakeDiagnostics', broken)
        else:
            patch.setattr(rag_runtime.uuid, 'uuid4', broken)
        receipt = await store.rag_runtime.ingest_async(project['id'], source['id'], [event(source)])
        assert receipt['state'] == 'durably_prepared' and not slot._held
    receipt = await store.rag_runtime.ingest_async(project['id'], source['id'], [event(source, identifier='second')])
    assert receipt['deduplicated'] == 0 and not slot._held
