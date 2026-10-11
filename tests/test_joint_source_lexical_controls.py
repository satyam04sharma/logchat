"""Offline lexical-route integration controls; every model is an in-process mock."""
import asyncio
from collections import Counter
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from logchat.rag.builder import prepare_preserved_batch
from logchat.rag.remapper import remap
from logchat.rag.retriever import RetrievalConfig, retrieve
from logchat.rag.sections import compact_summary_batch, section_batch
from pipeline.models import ModelUnavailable
from tests.test_compact_query_scope import CONFIG, cell, immutable
from tests.test_compact_v3_activation import Selector
from tests.test_joint_source_lexical_admission import QUESTION
from tests.test_rag_compact_summary_context import Backend, Model, Provider
from tests.test_rag_independent_classification import POSITIVE, SequenceModel
from tests.test_rag_sections import IDENTITY, WINDOW, LocalPartitionModel, event


async def source_chunk(index, *, service='gateway', source='console', fields=''):
    phrase = 'delayed parcel dispatch'
    identity = replace(IDENTITY, source_id=source)
    rows = [event(index, phrase + ' private synthetic tail ' + fields, service=service,
                  source=source, duration_ms=None, request_status=None)]
    batch = prepare_preserved_batch(identity, rows, WINDOW)
    sections = await section_batch(batch, LocalPartitionModel())
    compact = await compact_summary_batch(sections, Selector([phrase], fields=bool(fields)))
    return compact.chunks[0]


async def find(chunks, *, question=QUESTION, cells=None, lexical_limit=24, candidate_limit=6):
    return await retrieve(question, cells or (replace(cell(chunks[0], 0, 60), source_ids=()),),
        Backend(*chunks, similarity=.499999, lexical=True), Provider(),
        config=RetrievalConfig(candidate_limit=candidate_limit, lexical_limit=lexical_limit),
        preserve_content=True)


def detail(result):
    return json.loads(result.provenance[0].detail)


@pytest.mark.asyncio
@pytest.mark.parametrize('dimension', ['service', 'source'])
async def test_joint_lexical_fairness_survives_six_candidate_and_three_selection_caps(dimension):
    chunks = [await source_chunk(i, **{dimension: 'gateway'}) for i in range(8)]
    chunks += [await source_chunk(i + 8, **{dimension: name})
               for i, name in enumerate(('worker', 'auth'))]
    before = [immutable(chunk) for chunk in chunks]
    found = await find(chunks)
    assert len(found.candidates) == 6
    names = lambda items: {getattr(c.chunk, 'service') if dimension == 'service'
                          else c.chunk.identity.source_id for c in items}
    assert names(found.candidates[:3]) == {'gateway', 'worker', 'auth'}
    assert all(c.vector_score == .499999 and c.lexical_rank for c in found.candidates)
    model = Model()
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    assert len(checked.selected) == 3
    assert names([s.candidate for s in checked.selected]) == {'gateway', 'worker', 'auth'}
    assert len(model.calls) <= 6
    assert [immutable(chunk) for chunk in chunks] == before


@pytest.mark.asyncio
async def test_bounded_backend_lexical_pool_does_not_admit_later_vector_only_hits():
    chunks = [await source_chunk(i) for i in range(8)]
    class BoundedBackend(Backend):
        def lexical_search(self, cell, question, *, limit):
            return super().lexical_search(cell, question, limit=limit)[:limit]

    found = await retrieve(QUESTION, (cell(chunks[0], 0, 60),),
        BoundedBackend(*chunks, similarity=.499999, lexical=True), Provider(),
        config=RetrievalConfig(candidate_limit=6, lexical_limit=2), preserve_content=True)
    assert [c.chunk.chunk_id for c in found.candidates] == [c.chunk_id for c in chunks[:2]]
    assert all(c.lexical_rank <= 2 for c in found.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize('question,expected', [
    ('Compare SDK-420 and SDK-421', {0, 7}),
    ('Inspect SDK-420 and CASE-73', {7}),
    ('Inspect SDK-999 delayed parcel dispatch', set()),
])
async def test_literal_witnesses_keep_or_and_and_absent_id_before_lexical_caps(question, expected):
    chunks = [await source_chunk(i, fields='code=SDK-420') for i in range(7)]
    fields = 'code=SDK-421' if 'Compare' in question else 'code=SDK-420 incident=CASE-73'
    chunks.append(await source_chunk(7, fields=fields))
    found = await find(chunks, question=question, candidate_limit=2)
    expected_ids = {chunks[i].chunk_id for i in expected}
    if expected == {7}:
        # Retrieval retains a bounded nonmatching neighbor; literal eligibility
        # reserves the joint witness first and rejects the neighbor before calls.
        assert found.candidates[0].chunk.chunk_id == chunks[7].chunk_id
    else:
        assert {c.chunk.chunk_id for c in found.candidates} == expected_ids
    model = Model()
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    assert len(model.calls) == len(checked.selected) == len(expected)
    assert {s.candidate.chunk.chunk_id for s in checked.selected} == expected_ids


@pytest.mark.asyncio
async def test_shared_call_and_selection_caps_are_applied_to_lexical_cells():
    chunks = [await source_chunk(i) for i in range(3)]
    cells = tuple(cell(chunks[0], 0, 60, name=f'cell{i}') for i in range(3))
    found = await find(chunks, cells=cells)
    assert len(found.candidates) == 9
    model = Model()
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    ledger = detail(checked)
    # Complete v3 requests exhaust the unchanged aggregate input cap before
    # eight calls. Do not shrink evidence or raise budgets to fill the quota.
    assert 3 <= len(model.calls) < 8
    assert [p['cell']['cell_id'] for _, p, _ in model.calls] == (['cell0', 'cell1', 'cell2'] * 3)[:len(model.calls)]
    assert len(checked.selected) == len(model.calls) <= 8
    assert max(Counter(s.candidate.cell_id for s in checked.selected).values()) <= 3
    assert ledger['omitted_by_input_budget'] == 9 - len(model.calls)
    assert ledger['input_characters'] <= CONFIG.input_character_budget
    assert ledger['input_utf8_bytes'] <= CONFIG.input_byte_budget


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', ['input_character_budget', 'input_byte_budget'])
async def test_joint_lexical_input_budget_never_forces_a_call(budget):
    found = await find([await source_chunk(i) for i in range(3)])
    model = Model()
    checked = await remap(found, model, config=replace(CONFIG, **{budget: 3000}), preserve_content=True)
    ledger = detail(checked)
    assert ledger['omitted_by_input_budget'] > 0
    assert len(model.calls) < len(found.candidates)
    assert len(checked.selected) == len(model.calls)
    assert ledger['input_characters' if budget == 'input_character_budget' else 'input_utf8_bytes'] <= 3000


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [{}, {'label': 'topic_match', 'confidence': True},
                                 ModelUnavailable('private synthetic failure')])
async def test_joint_lexical_late_failure_discards_prior_positive_and_stops(bad):
    found = await find([await source_chunk(i) for i in range(3)])
    model = SequenceModel([POSITIVE, bad, POSITIVE])
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    assert not checked.selected and len(model.calls) == 2
    ledger = detail(checked)
    assert ledger['validated_classifications'] == 1
    assert not ledger['classification_calls'][2]['attempted']
    assert 'private synthetic failure' not in json.dumps(ledger)


@pytest.mark.asyncio
async def test_joint_lexical_shared_deadline_discards_prior_positive():
    found = await find([await source_chunk(i) for i in range(3)])

    class ExpireAfterPositive(SequenceModel):
        async def generate(self, *args):
            if self.calls:
                raise TimeoutError()
            return await super().generate(*args)

    model = ExpireAfterPositive([POSITIVE] * 3)
    from logchat.rag.remapper import _execute_classification_plan
    with patch('logchat.rag.remapper._execute_classification_plan', wraps=_execute_classification_plan) as execute:
        start = asyncio.get_running_loop().time()
        checked = await remap(found, model, config=replace(CONFIG, timeout_seconds=180), preserve_content=True)
    assert start < execute.call_args.kwargs['deadline'] <= start + 60.01
    assert not checked.selected
    assert detail(checked)['reason'] == 'model_timeout'
    assert detail(checked)['validated_classifications'] == 1


@pytest.mark.asyncio
async def test_joint_lexical_external_cancellation_stops_pending_classifier():
    found = await find([await source_chunk(i) for i in range(3)])
    entered = asyncio.Event()

    class Waiting(Model):
        cancelled = False

        async def generate(self, *args):
            self.calls.append(args)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    model = Waiting()
    task = asyncio.create_task(remap(found, model, config=CONFIG, preserve_content=True))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert model.cancelled and len(model.calls) == 1


@pytest.mark.asyncio
async def test_joint_lexical_expiry_cancels_late_call_without_positive_fallback():
    found = await find([await source_chunk(i) for i in range(3)])

    class WaitAfterPositive(Model):
        cancelled = False

        async def generate(self, *args):
            self.calls.append(args)
            if len(self.calls) == 1:
                return POSITIVE
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    model = WaitAfterPositive()
    checked = await remap(found, model, config=replace(CONFIG, timeout_seconds=.1), preserve_content=True)
    assert not checked.selected and len(model.calls) == 2 and model.cancelled
    assert detail(checked)['reason'] == 'model_timeout'
    assert detail(checked)['validated_classifications'] == 1


@pytest.mark.asyncio
async def test_joint_lexical_fairness_cannot_turn_negative_source_into_selection():
    chunks = [await source_chunk(i) for i in range(7)]
    chunks.append(await source_chunk(8, service='worker'))
    found = await find(chunks)
    assert found.candidates[1].chunk.service == 'worker'

    class RejectWorker(Model):
        async def generate(self, instruction, payload, schema):
            self.calls.append((instruction, payload, schema))
            return {'label': 'not_relevant' if payload['candidate']['service'] == 'worker'
                    else 'topic_match', 'confidence': .99}

    model = RejectWorker()
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    assert model.calls[1][1]['candidate']['service'] == 'worker'
    assert checked.selected and all(s.candidate.chunk.service == 'gateway' for s in checked.selected)
    assert detail(checked)['rejected_labels'] == 1
