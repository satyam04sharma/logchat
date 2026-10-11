"""Synthetic offline contracts for joint checked-quotation candidate admission."""
from dataclasses import replace

import pytest

from logchat.rag.remapper import remap
from logchat.rag.retriever import RetrievalConfig, retrieve
from logchat.rag.sections import (
    COMPACT_EVIDENCE_V1, _OMISSION_NOTICE, _compact_id, _compact_pattern_id,
    _json, validate_compact_chunk,
)
from tests.test_compact_query_scope import CONFIG, cell
from tests.test_compact_v3_activation import Selector, fresh
from tests.test_rag_compact_summary_context import Backend, Model, Provider
from tests.test_rag_sections import event

QUESTION = 'What context exists about delayed parcel dispatch?'


async def fixture(phrases, messages=None):
    _, batch = await fresh([event(i, text + ' private synthetic tail') for i, text in
                           enumerate(messages or phrases)], Selector(phrases))
    return batch.chunks[0]


async def run(chunk, question=QUESTION, *, lexical=True, score=.499999, scoped=None,
              model=None):
    model = model or Model()
    found = await retrieve(question, (scoped or cell(chunk, 0, 60),),
                           Backend(chunk, similarity=score, lexical=lexical), Provider(),
                           config=RetrievalConfig(candidate_limit=6), preserve_content=True)
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    return found, checked, model


@pytest.mark.asyncio
@pytest.mark.parametrize('score', [.499999, 0.0, -.5])
async def test_joint_quote_admits_to_independent_classifier(score):
    chunk = await fixture(['delayed parcel dispatch'])
    before = chunk.compact_evidence, chunk.chunk_id, chunk.summary
    found, checked, model = await run(chunk, score=score)
    assert len(found.candidates) == len(model.calls) == len(checked.selected) == 1
    assert found.candidates[0].vector_score == score
    assert model.calls[0][1]['question'] == QUESTION
    assert (chunk.compact_evidence, chunk.chunk_id, chunk.summary) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('phrases,messages,question', [
    (['delayed parcel'], None, QUESTION),
    (['delayed parcel dispatch'], None, QUESTION + ' billing'),
    (['delayed parcel', 'dispatch'], None, QUESTION),
    (['delayed parcel', 'dispatch'], ['delayed parcel dispatch'], QUESTION),
    (['retained source observations'], None, 'What retained source observations?'),
    (['event count measurements'], None, 'What event count measurements?'),
    (['parcel'], None, 'What parcel?'),
    (['delayed parcels dispatch'], None, QUESTION),
    (['delayed parcel dispatch'], None, QUESTION + ' xy'),
    (['delayed parcel dispatch'], None, QUESTION + ' SDK-999'),
])
async def test_no_partial_stitched_scaffolding_or_absent_id_admission(phrases, messages, question):
    found, checked, model = await run(await fixture(phrases, messages), question)
    assert not found.candidates and not model.calls and not checked.selected


@pytest.mark.asyncio
async def test_actual_backend_lexical_hit_required_and_vector_floor_unchanged():
    chunk = await fixture(['delayed parcel dispatch'])
    found, _, model = await run(chunk, lexical=False)
    assert not found.candidates and not model.calls
    found, checked, model = await run(chunk, lexical=False, score=.5)
    assert len(found.candidates) == len(model.calls) == len(checked.selected) == 1


@pytest.mark.asyncio
async def test_quote_outside_half_open_cell_cannot_qualify():
    chunk = await fixture(['delayed parcel dispatch', 'routine completion'])
    found, _, model = await run(chunk, scoped=cell(chunk, 1, 2))
    assert not found.candidates and not model.calls


@pytest.mark.asyncio
async def test_context_labels_cannot_supply_meaningful_topic_words():
    chunk = await fixture(['delayed parcel dispatch'])
    labels = ' '.join((chunk.service, chunk.identity.source_id, chunk.identity.environment_id))
    from logchat.rag.retriever import _joint_source_topics
    assert not _joint_source_topics(labels, chunk, [{'text': labels}])


@pytest.mark.asyncio
async def test_historical_free_prose_cannot_qualify():
    chunk = await fixture(['delayed parcel dispatch'])
    evidence = validate_compact_chunk(chunk)
    evidence = {key: evidence[key] for key in ('records', 'important_fields')}
    evidence.update(version=COMPACT_EVIDENCE_V1, uncertainties=[_OMISSION_NOTICE])
    summary = 'delayed parcel dispatch'
    chunk = replace(chunk, summary=summary, compact_evidence=_json(evidence),
                    pattern_id=_compact_pattern_id(evidence, summary))
    chunk = replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, summary, chunk.summary_model))
    found, _, model = await run(chunk)
    assert not found.candidates and not model.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [
    {'label': 'not_relevant', 'confidence': .99},
    {'label': 'topic_match', 'confidence': .1},
    {'label': 'topic_match', 'confidence': '0.99'},
    {'label': 'topic_match', 'confidence': .99, 'extra': True},
])
async def test_lexical_candidate_never_fills_after_negative_or_invalid_classification(result):
    chunk = await fixture(['delayed parcel dispatch'])
    question = 'What context exists about parcel dispatch without delayed?'
    found, checked, model = await run(chunk, question, model=Model(result))
    assert len(found.candidates) == len(model.calls) == 1
    assert model.calls[0][1]['question'] == question
    assert not checked.selected


@pytest.mark.asyncio
async def test_partial_topic_crowd_is_rejected_before_candidate_cap():
    partial = [await fixture([f'delayed parcel distractor{i}']) for i in range(7)]
    joint = await fixture(['delayed parcel dispatch'])
    found = await retrieve(QUESTION, (cell(joint, 0, 60),),
        Backend(*partial, joint, similarity=.499999, lexical=True), Provider(),
        config=RetrievalConfig(candidate_limit=6), preserve_content=True)
    model = Model()
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    assert [c.chunk.chunk_id for c in found.candidates] == [joint.chunk_id]
    assert len(model.calls) == len(checked.selected) == 1


@pytest.mark.asyncio
async def test_punctuated_context_labels_and_identifier_tokens_do_not_count_as_topics():
    from logchat.rag.retriever import _joint_source_topics
    chunk = replace(await fixture(['delayed parcel dispatch']), service='parcel-dispatch')
    assert not _joint_source_topics('parcel dispatch', chunk, [{'text': 'parcel dispatch'}])
    assert not _joint_source_topics('CODE TOKEN', chunk, [{'text': 'CODE TOKEN'}])
