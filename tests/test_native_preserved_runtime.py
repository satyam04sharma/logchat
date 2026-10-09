import asyncio,json
from datetime import datetime,timedelta,timezone
import pytest
from fastapi.testclient import TestClient
from logchat.local.app import create_app
from logchat.local.rag_runtime import configure
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.embeddings import OllamaEmbeddingProvider
from logchat.rag.sections import records_for_chunk
from pipeline.types import LogEvent


@pytest.mark.asyncio
async def test_preserved_intake_refinement_embedding_query_inspection_and_chat(tmp_path,monkeypatch):
    texts=[]
    class Provider:
        spec=EmbeddingSpec('ollama','mock','digest:prefix-v1',8)
        preserve_content=True;local_only=True
        async def embed(self,values,**kwargs):
            texts.extend(values);return [(1,.1,.2,.3,.4,.5,.6,.7) for _ in values]
    async def create(**kwargs):return Provider()
    monkeypatch.setattr(OllamaEmbeddingProvider,'create',create)
    class LocalModel:
        preserve_content=True;local_only=True;chat_model='mock-local'
        def __init__(self,**kwargs):pass
        async def generate(self,instruction,context,schema):
            if 'records' in context:
                return {'summary':'A session renewal failed for the affected visitor.',
                    'important_fields':[field['field_ref'] for field in context['field_catalog'] if field['key'] in {'email','phone','error_code'}],
                    'uncertainties':[]}
            if 'items' in context:return {'sections':[[item['ref'] for item in context['items']]]}
            return {'selections':[{'evidence_ref':item['evidence_ref'],'reason':'exact_identifier','confidence':1}
                for cell in context['cells'] for item in cell['candidates']]}
    monkeypatch.setattr('pipeline.models.LocalModels',LocalModel)
    await configure(tmp_path,model='mock',dimensions=8,content_policy='local_model_compact',chat_model='mock-local')
    store=LocalStore(tmp_path);project=store.create_project('preserved')
    source=store.create_source(project['id'],'local file','dev','push',8000)
    now=datetime.now(timezone.utc)
    message='Session failure synthetic_raw_only_marker\nStructured fields: '+json.dumps({'email':'alice@example.test','phone':'+1-202-555-0101','error_code':'LIB-042'})
    event=LogEvent('preserved-event',now,source['id'],'app','error',message,'x',duration_ms=18,request_status=401)
    store.ingest(project['id'],source['id'],[event]);runtime=store.rag_runtime
    runtime.scheduler.dispatch(now=now+timedelta(seconds=10))
    await runtime.start()
    try:
        async with asyncio.timeout(10):
            while runtime.backend.statistics()['vectors']!=1:await asyncio.sleep(.01)
    finally:await runtime.stop()
    chunk=runtime.backend.list_chunks(owner_id='local-os-user',project_id=project['id'])[0]
    assert chunk.compression_version=='local-model-compact-v1'
    assert chunk.summary_model=='mock-local'
    assert 'message' not in records_for_chunk(chunk)[0]
    assert chunk.supporting_records is None
    assert 'synthetic_raw_only_marker' not in chunk.compact_evidence
    assert any('LIB-042' in text for text in texts)
    assert all('synthetic_raw_only_marker' not in text for text in texts)
    application=create_app(tmp_path,8772,capture_host=False);client=TestClient(application,base_url='http://127.0.0.1:8772')
    headers={'Authorization':'Bearer '+store.control_token}
    inspected=client.get(f"/projects/{project['id']}/memories/{chunk.chunk_id}",headers=headers).json()
    assert 'records' not in inspected['memory']
    assert inspected['memory']['original_records_retained'] is False
    assert inspected['memory']['important_fields']
    status=client.get(f"/projects/{project['id']}/status",headers=headers).json()
    assert status['memory']['application']['full_event_text_stored'] is False
    assert status['models']['chat_model']=='mock-local'
    assert status['models']['configuration_source']=='shared_native_profile'
    model_settings=client.get('/settings/models',headers=headers).json()
    assert model_settings['chat_model']=='mock-local'
    assert client.put('/settings/models',headers=headers,json={'provider':'extractive'}).status_code==409

    cid=client.post(f"/projects/{project['id']}/conversations",json={},headers=headers).json()['id']
    environment=store.list_environments(project['id'])[0]['id']
    response=client.post(f"/projects/{project['id']}/conversations/{cid}/messages",headers=headers,
        json={'question':'What happened to alice@example.test with LIB-042?','environment_ids':[environment]})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['user_message']['content']=='What happened to alice@example.test with LIB-042?'
    assert result['result']['evidence'][0]['retained_events'][0]['important_fields']
    assert result['result']['evidence'][0]['original_records_retained'] is False

    assert result['result']['output_kind']=='retrieved_context'
    assert result['result']['solution_generated'] is False
    assert result['result']['context_text']==result['result']['answer']
    assert result['result']['provenance']['answering']['model'] is None

    runtime.state='ready'
    monkeypatch.setattr(runtime.scheduler,'status',lambda identity:{'registered':True,'jobs':{'failed':1}})
    failed=runtime.status(project['id'])
    assert failed['state']=='unavailable'
    assert failed['connection_state']=='ready'
    assert failed['processing_state']=='failed_jobs_retained'
    assert failed['failed_jobs']==1
