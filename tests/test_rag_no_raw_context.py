"""Context consumers use compact selected facts without original event messages."""
from dataclasses import replace
import json
import pytest
from pipeline.types import LogEvent
from logchat.rag.builder import prepare_preserved_batch
from logchat.rag.sections import section_batch, compact_summary_batch, records_for_chunk
from logchat.rag.retriever import retrieve
from logchat.rag.remapper import remap
from logchat.rag.answering import assemble_context
from tests.test_rag_compact_summary_context import START, WINDOW, SCOPE, CELL, CONFIG, Backend, Provider, Model

class SummaryModel:
    local_only=True;preserve_content=True;chat_model='chosen-local-model'
    async def generate(self,instruction,context,schema):
        if 'items' in context:return {'sections':[[r['ref'] for r in context['items']]]}
        fields=[field['field_ref'] for field in context['field_catalog'] if field['key'] in {'email','phone','error_code'}]
        return {'summary':'Session renewals were rejected for the affected visitors.',
                'important_fields':fields,'uncertainties':['The logs do not establish the underlying cause.']}

async def fixture():
    events=[]
    for n in range(2):
        fields={'email':f'user{n}@example.test','phone':f'+1-202-555-010{n}','error_code':'SDK_AUTH_X_42'}
        events.append(LogEvent(f'e{n}',START,'browser','gateway','error',
            'Session renewal rejected RAW_ORIGINAL_ONLY_MARKER\nStructured fields: '+json.dumps(fields),
            'source-fingerprint',duration_ms=18+n,request_status=401))
    model=SummaryModel()
    prepared=prepare_preserved_batch(SCOPE,events,WINDOW)
    return (await compact_summary_batch(await section_batch(prepared,model),model)).chunks[0]

@pytest.mark.asyncio
async def test_compact_context_keeps_selected_fields_and_declares_no_originals():
    item=await fixture()
    found=await retrieve('What happened to user0@example.test?',(CELL,),Backend(item,similarity=.1,lexical=True),Provider(),preserve_content=True)
    checked=await remap(found,Model(),config=CONFIG,preserve_content=True)
    result=assemble_context(checked,preserve_content=True)
    assert result['evidence']
    row=result['evidence'][0]
    assert 'records' not in row and row['original_records_retained'] is False
    assert all('message' not in record for record in row['retained_events'])
    assert row['important_fields'] and row['uncertainties']
    assert 'RAW_ORIGINAL_ONLY_MARKER' not in json.dumps(result)
    assert 'Original logs are not retained' in row['interpretation']
    assert 'original_logs_not_retained_selected_fields_not_exhaustive' in result['gaps']
    assert 'Supporting original records accompany' not in result['answer']

@pytest.mark.asyncio
async def test_compact_exact_constraints_require_values_in_same_event():
    item=await fixture()
    found=await retrieve('user0@example.test with +1-202-555-0101',(CELL,),Backend(item,similarity=.9,lexical=True),Provider(),preserve_content=True)
    checked=await remap(found,Model(),config=CONFIG,preserve_content=True)
    assert not checked.selected

@pytest.mark.asyncio
async def test_compact_loaded_evidence_tamper_is_not_promoted():
    item=await fixture()
    invalid=replace(item,compact_evidence=item.compact_evidence.replace('user0@example.test','invented@example.test'))
    result=await retrieve('session failures',(CELL,),Backend(invalid),Provider(),preserve_content=True)
    assert not result.candidates
