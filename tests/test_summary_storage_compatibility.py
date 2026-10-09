"""Backward replay and exact search when embeddings use compact text."""
import asyncio
import json
from dataclasses import asdict, replace
from logchat.rag.contracts import BuildResult, EmbeddedChunk, RetrievalCell
from logchat.rag.scheduler import _encode
from logchat.rag.storage import SQLiteVectorStore, _encode_chunk
from tests.test_rag_builder import IDENTITY, SPEC, WINDOW, event
from logchat.rag.builder import prepare_batch


def test_additive_summary_fields_do_not_change_old_immutable_encoding(tmp_path):
    batch = prepare_batch(IDENTITY, [event(1)], WINDOW)
    encoded = json.loads(_encode(batch))
    assert 'supporting_records' not in encoded['chunks'][0]
    assert 'summary_model' not in encoded['chunks'][0]
    chunk = batch.chunks[0]
    payload = json.loads(_encode_chunk(chunk))
    assert 'supporting_records' not in payload and 'summary_model' not in payload
    store = SQLiteVectorStore(tmp_path/'rag.db', SPEC)
    built = BuildResult(batch.batch_id, (EmbeddedChunk(chunk, (1.,0.,0.), SPEC),), batch.coverage)
    connection = store.connect()
    try:
        connection.execute('BEGIN IMMEDIATE');store.write_result(connection,built);connection.commit()
        connection.execute('BEGIN IMMEDIATE');store.write_result(connection,built);connection.commit()
        assert store.statistics()['vectors'] == 1
    finally: connection.close()


def test_compact_summary_lexical_search_retains_selected_exact_identifiers(tmp_path):
    from logchat.rag.sections import section_batch, compact_summary_batch
    from logchat.rag.builder import prepare_preserved_batch
    original = prepare_preserved_batch(IDENTITY, [event(1, message='Session renewal rejected user=ExactUserXYZ raw_body_canary')], WINDOW)
    class Model:
        local_only=True;preserve_content=True;chat_model='fixture-local'
        async def generate(self,instruction,context,schema):
            if 'items' in context:return {'sections':[[row['ref'] for row in context['items']]]}
            return {'summary':'A visitor experienced a session renewal rejection.',
                    'important_fields':[f['field_ref'] for f in context['field_catalog'] if f['key']=='user'], 'uncertainties':[]}
    async def compact():
        model=Model()
        return await compact_summary_batch(await section_batch(original,model),model)
    summary = asyncio.run(compact()).chunks[0]
    store = SQLiteVectorStore(tmp_path/'rag.db', SPEC)
    built = BuildResult(original.batch_id, (EmbeddedChunk(summary, (1.,0.,0.),SPEC),), original.coverage)
    connection = store.connect()
    try:
        connection.execute('BEGIN IMMEDIATE');store.write_result(connection,built);connection.commit()
    finally: connection.close()
    cell=RetrievalCell('current',IDENTITY.owner_id,IDENTITY.project_id,IDENTITY.environment_id,WINDOW)
    hits=store.lexical_search(cell,'ExactUserXYZ',limit=5)
    assert len(hits)==1 and hits[0].chunk==summary
    assert 'ExactUserXYZ' not in hits[0].chunk.summary
