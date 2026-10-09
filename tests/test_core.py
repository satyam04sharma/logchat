import asyncio
import json
import struct
import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from dataclasses import replace

import httpx
from pipeline.types import LogEvent
from pipeline.redaction import redact_event,redact_text
from pipeline.models import LocalModels,ModelUnavailable
from pipeline.retrieval import full_coverage,resolve_windows
from connectors.docker import DockerConnector
from connectors.base import ConnectorError
from cli.secrets import write_credential,read_credential
from unittest.mock import patch
import tempfile

class PrivacyTests(unittest.TestCase):
    def test_all_text_fields_scrub_configured_credentials(self):
        secret='CANARY_UNLABELLED_987654321'
        event=LogEvent(secret,datetime.now(timezone.utc),secret,secret,secret,'email person@example.com password=letmein '+secret,secret,secret)
        redacted=redact_event(event,secrets=(secret,))
        for field in ('event_id','source','service','level','message','fingerprint','release'):
            value=getattr(redacted,field)
            self.assertNotIn(secret,value);self.assertNotIn('person@example.com',value);self.assertNotIn('letmein',value)

    def test_secret_scope_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'LOGCHAT_SECRETS_DIR':folder}):
            ref=write_credential('CANARY',owner_id='owner',environment_id='dev')
            self.assertEqual(read_credential(ref,owner_id='owner',environment_id='dev'),'CANARY')
            with self.assertRaises(RuntimeError):read_credential(ref,owner_id='owner',environment_id='prod')
            with self.assertRaises(RuntimeError):read_credential('../not-a-reference')

class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_summary_redacted_without_request(self):
        bodies=[]
        def handler(request):
            bodies.append(request.content.decode())
            return httpx.Response(200,json={'response':json.dumps({'selected_terms':[]})})
        models=LocalModels(base_url='http://localhost:11434',transport=httpx.MockTransport(handler))
        event=LogEvent('id',datetime.now(timezone.utc),'docker','api','error','person@example.com api_key=abcSECRETvalue','error')
        result=await models.summarize([event])
        self.assertEqual(bodies,[])
        self.assertNotIn('person@example.com',result);self.assertNotIn('abcSECRETvalue',result)

    async def test_no_silent_context_truncation(self):
        requests=[]
        models=LocalModels(base_url='http://localhost:11434',transport=httpx.MockTransport(lambda r:requests.append(r)))
        with self.assertRaises(ModelUnavailable):await models.generate('instruction',{'chunks':['a'*10000]*3,'coverage':'critical gap'}, {})
        self.assertEqual(requests,[])

    async def test_rejects_wrong_embedding_dimensions(self):
        models=LocalModels(base_url='http://localhost:11434',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'embeddings':[[.1]*3]})))
        with self.assertRaises(ModelUnavailable):await models.embed('redacted summary')

class DockerTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiplexed_half_open_window_and_distinct_identical_events(self):
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        lines=(now.isoformat()+' {"message":"timeout", "duration_ms":120, "status":503}\n')*2
        lines+=(now+timedelta(seconds=1)).isoformat()+' outside\n'
        payload=lines.encode();data=b'\x01\0\0\0'+struct.pack('>I',len(payload))+payload
        connector=DockerConnector({'container':'my-api'},transport=httpx.MockTransport(lambda r:httpx.Response(200,content=data)))
        events=[event async for event in connector.fetch(now,now+timedelta(seconds=1))]
        self.assertEqual(len(events),2);self.assertNotEqual(events[0].event_id,events[1].event_id)
        self.assertEqual(events[0].duration_ms,120);self.assertEqual(events[0].request_status,503)

    async def test_oversized_response_is_explicit_failure(self):
        connector=DockerConnector({'container':'my-api'},transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*(4*1024*1024+1))))
        now=datetime.now(timezone.utc)
        with self.assertRaises(ConnectorError):events=[e async for e in connector.fetch(now,now+timedelta(seconds=1))]

class QueryTests(unittest.TestCase):
    def test_gaps_are_not_empty_success(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=start+timedelta(hours=1)
        self.assertFalse(full_coverage(start,end,[{'window_start':start,'window_end':end,'status':'gap'}]))
        self.assertTrue(full_coverage(start,end,[{'window_start':start,'window_end':end,'status':'empty'}]))
        self.assertFalse(full_coverage(start,end,[{'window_start':start+timedelta(seconds=1),'window_end':end,'status':'complete'}]))

    def test_calendar_comparison_is_timezone_aware_and_equal_length(self):
        body=SimpleNamespace(timezone='America/New_York',start=datetime(2026,3,31,tzinfo=timezone.utc),end=datetime(2026,4,7,tzinfo=timezone.utc),compare_start=None,compare_end=None,question='Compare with last month')
        windows,assumptions=resolve_windows(body)
        self.assertEqual(windows[0]['end']-windows[0]['start'],windows[1]['end']-windows[1]['start'])
        self.assertTrue(assumptions)

    def test_comparison_preserves_elapsed_duration_across_daylight_saving(self):
        from zoneinfo import ZoneInfo
        tz=ZoneInfo('America/New_York')
        body=SimpleNamespace(timezone='America/New_York',start=datetime(2026,3,6,12,tzinfo=tz),end=datetime(2026,3,13,12,tzinfo=tz),compare_start=None,compare_end=None,question='Compare with last month')
        windows,_=resolve_windows(body)
        self.assertEqual(windows[0]['end']-windows[0]['start'],timedelta(hours=167))
        self.assertEqual(windows[1]['end']-windows[1]['start'],timedelta(hours=167))

if __name__=='__main__':unittest.main()
