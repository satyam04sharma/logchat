"""Security and concurrency checks for the portable SQLite backend."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import httpx

from logchat.local.app import _optional_model_answer, create_app


class PortableBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        self.port = 18765
        self.app = create_app(self.state, self.port)
        self.transport = httpx.ASGITransport(app=self.app)
        self.control = self.app.state.store.control_token
        self.client = httpx.AsyncClient(
            transport=self.transport, base_url=f"http://127.0.0.1:{self.port}",
            headers={"Authorization": "Bearer " + self.control},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    async def project(self, name="sample"):
        response = await self.client.post("/projects", json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def source(self, project, name="app", environment="dev"):
        response = await self.client.post(f"/projects/{project['id']}/sources", json={
            "name": name, "environment": environment, "kind": "push",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def emit(self, project, source, events):
        return await self.client.post(
            f"/projects/{project['id']}/events",
            headers={"Authorization": "Bearer " + source["token"]},
            json={"source_id": source["id"], "events": events},
        )

    async def test_health_permissions_and_default_extractive_provider(self):
        live = await self.client.get("/health/live")
        self.assertEqual(live.json(), {"status": "ready", "mode": "local"})
        unauthenticated = httpx.AsyncClient(transport=self.transport, base_url=f"http://127.0.0.1:{self.port}")
        try:
            self.assertEqual((await unauthenticated.get("/projects")).status_code, 401)
            self.assertEqual((await unauthenticated.get("/settings/models")).status_code, 401)
        finally:
            await unauthenticated.aclose()
        settings = (await self.client.get("/settings/models")).json()
        self.assertEqual(settings["provider"], "extractive")
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.state / "local.db").st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.state / "control-token").st_mode & 0o777, 0o600)

    async def test_source_bearer_is_exactly_project_and_source_scoped(self):
        first, second = await self.project("one"), await self.project("two")
        first_source, other_source = await self.source(first, "one"), await self.source(second, "two")
        event = {"message": "request timeout", "timestamp": "2026-10-01T10:01:00Z"}
        wrong_project = await self.client.post(f"/projects/{second['id']}/events",
            headers={"Authorization": "Bearer " + first_source["token"]},
            json={"source_id": first_source["id"], "events": [event]})
        wrong_source = await self.client.post(f"/projects/{first['id']}/events",
            headers={"Authorization": "Bearer " + first_source["token"]},
            json={"source_id": other_source["id"], "events": [event]})
        control_is_not_ingest = await self.client.post(f"/projects/{first['id']}/events",
            json={"source_id": first_source["id"], "events": [event]})
        self.assertEqual((wrong_project.status_code, wrong_source.status_code, control_is_not_ingest.status_code),
                         (401, 401, 401))
        self.assertEqual((await self.emit(first, first_source, [event])).status_code, 200)

    async def test_foreign_host_origin_and_oversized_requests_are_rejected(self):
        foreign = httpx.AsyncClient(transport=self.transport, base_url="http://logs.example.test")
        try:
            self.assertEqual((await foreign.get("/health/live")).status_code, 400)
        finally:
            await foreign.aclose()
        response = await self.client.get("/health/live", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        project = await self.project(); source = await self.source(project)
        huge = "x" * (257 * 1024)
        response = await self.emit(project, source, [{"message": huge}])
        self.assertEqual(response.status_code, 413)

    async def test_only_lossy_redacted_aggregates_are_persisted(self):
        project, canary = await self.project(), "RAW-CANARY-do-not-persist-39157"
        source = await self.source(project)
        response = await self.emit(project, source, [{
            "message": canary + " request timeout for person@example.com token=private-value",
            "service": "api", "level": "error", "timestamp": "2026-10-01T10:01:00Z",
        }])
        self.assertEqual(response.status_code, 200, response.text)
        evidence = (await self.client.get(f"/projects/{project['id']}/search", params={"q": "timeout"})).json()["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertIn("timeout", evidence[0]["summary"])
        payload = b"".join(path.read_bytes() for path in self.state.glob("local.db*"))
        for forbidden in (canary.encode(), b"person@example.com", b"private-value"):
            self.assertNotIn(forbidden, payload)

    async def test_busy_records_retain_late_operational_topics_without_raw_text(self):
        project = await self.project(); source = await self.source(project)
        prefix = ('timeout slow latency duration connection connect disconnected database db sql '
                  'cache redis http request response route endpoint')
        suffix = 'certificate ssl tls heartbeat healthcheck authentication traceback import module'
        canary = 'RAW-CANARY-busy-record-79431'
        base = {'service': 'api', 'level': 'error', 'timestamp': '2026-10-01T10:01:00Z',
                'duration_ms': 120, 'http_status': 503}
        response = await self.emit(project, source, [
            {**base, 'message': prefix + ' ' + suffix + ' ' + canary + ' person@example.com token=private-value'},
            {**base, 'message': prefix},
            {**base, 'message': prefix + ' certificate'},
        ])
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.app.state.store.evidence(project['id'])
        # Distinct retained topic sets must not be coalesced just because their first 12 terms agree.
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row['event_count'] for row in rows), 3)
        complete = next(row for row in rows if 'module' in row['summary'])
        for term in (prefix + ' ' + suffix).split():
            self.assertIn(term, complete['summary'])
        self.assertLess(len(complete['summary']), 2000)
        scope = {'environment_ids': [project['environments'][0]['id']], 'timezone': 'UTC',
                 'start': '2026-10-01T10:00:00Z', 'end': '2026-10-01T10:30:00Z'}
        for query in ('certificate ssl tls', 'heartbeat healthcheck', 'authentication traceback import module'):
            with self.subTest(query=query):
                search = (await self.client.get(f"/projects/{project['id']}/search", params={'q': query})).json()
                self.assertEqual([row['id'] for row in search['evidence']], [complete['id']])
                reply = await self.client.post(f"/projects/{project['id']}/ask", json={**scope, 'question': query})
                self.assertEqual(reply.status_code, 200, reply.text)
                result = reply.json()
                self.assertEqual(result['cited_evidence_ids'], [complete['id']])
                self.assertEqual(result['plan']['cells'][0]['metrics']['events'], 1)
                self.assertIn('do not establish complete traffic totals or a cause', result['findings'][0]['interpretation'])
        self.assertEqual(self.app.state.store.evidence(project['id'], 'certificate unavailable'), [])
        payload = b''.join(path.read_bytes() for path in self.state.glob('local.db*'))
        for forbidden in (canary.encode(), b'person@example.com', b'private-value'):
            self.assertNotIn(forbidden, payload)

    async def test_full_vocabulary_ingest_preserves_legacy_memory_and_saved_answer(self):
        project = await self.project(); source = await self.source(project)
        store = self.app.state.store
        message = ('timeout slow latency duration connection connect disconnected database db sql '
                   'cache redis certificate ssl tls')
        event = {'message': message, 'service': 'api', 'level': 'error',
                 'timestamp': '2026-10-01T10:01:00Z', 'duration_ms': 120}
        legacy_summary = ('Observed 1 error event(s) in api. Aggregate pattern: timeout, slow, latency, '
                          'duration, connection, connect, disconnected, database, db, sql, cache, redis.')
        # Seed the old retained representation without persisting or reconstructing raw text.
        with patch.object(store, '_summary', return_value=legacy_summary):
            self.assertEqual((await self.emit(project, source, [event])).status_code, 200)
        legacy = store.evidence(project['id'])[0]
        conversation = (await self.client.post(f"/projects/{project['id']}/conversations", json={})).json()
        reply = await self.client.post(f"/conversations/{conversation['id']}/messages", json={
            'question': 'database timeout', 'environment_ids': [project['environments'][0]['id']],
            'timezone': 'UTC', 'start': '2026-10-01T10:00:00Z', 'end': '2026-10-01T10:30:00Z'})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()['result']['cited_evidence_ids'], [legacy['id']])
        saved = (await self.client.get(f"/conversations/{conversation['id']}")).json()

        self.assertEqual((await self.emit(project, source, [event])).status_code, 200)
        self.assertEqual(len(store.evidence(project['id'])), 2)
        matching = store.evidence(project['id'], 'certificate ssl tls')
        self.assertEqual(len(matching), 1)
        self.assertNotEqual(matching[0]['id'], legacy['id'])
        self.assertEqual(next(row for row in store.evidence(project['id']) if row['id'] == legacy['id']), legacy)
        inspected = await self.client.get(f"/projects/{project['id']}/memories/{legacy['id']}")
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertIn(legacy_summary, inspected.text)
        self.assertEqual((await self.client.get(f"/conversations/{conversation['id']}")).json(), saved)

    async def test_concurrent_ingest_and_idempotent_conversation_replay(self):
        project = await self.project(); source = await self.source(project)
        when = "2026-10-01T10:01:00Z"
        responses = await asyncio.gather(*[
            self.emit(project, source, [{"message": "database connection timeout", "service": "api",
                                         "level": "error", "timestamp": when, "duration_ms": index + 1}])
            for index in range(12)
        ])
        self.assertTrue(all(item.status_code == 200 for item in responses))
        evidence = (await self.client.get(f"/projects/{project['id']}/search", params={"q": "timeout"})).json()["evidence"]
        self.assertEqual(sum(item["event_count"] for item in evidence), 12)
        conversation = (await self.client.post(f"/projects/{project['id']}/conversations", json={})).json()
        request_id = str(uuid4())
        turn = {"question": "database timeout on October 1", "environment_ids": [project["environments"][0]["id"]],
                "timezone": "UTC", "start": "2026-10-01T10:00:00Z", "end": "2026-10-01T10:30:00Z",
                "request_id": request_id}
        replies = await asyncio.gather(*[
            self.client.post(f"/conversations/{conversation['id']}/messages", json=turn) for _ in range(8)
        ])
        self.assertTrue(all(item.status_code == 200 for item in replies), [item.text for item in replies])
        assistant_ids = {item.json()["assistant_message"]["id"] for item in replies}
        self.assertEqual(len(assistant_ids), 1)
        saved = (await self.client.get(f"/conversations/{conversation['id']}")).json()["messages"]
        self.assertEqual([item["role"] for item in saved], ["user", "assistant"])
        context = (await self.client.get(f"/projects/{project['id']}/conversations/{conversation['id']}/context")).json()
        self.assertEqual(context["turn_count"], 1)
        with self.app.state.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversation_questions WHERE conversation_id=?", (conversation['id'],)).fetchone()[0], 1)

    async def test_retrieval_obeys_environment_and_whole_bucket_time_scope(self):
        project = await self.project()
        await self.client.post(f"/projects/{project['id']}/environments", json={"name": "prod"})
        environments = (await self.client.get(f"/projects/{project['id']}/environments")).json()
        ids = {item["name"]: item["id"] for item in environments}
        dev, prod = await self.source(project, "app", "dev"), await self.source(project, "app", "prod")
        await self.emit(project, dev, [{"message": "database timeout", "service": "api", "level": "error",
                                        "timestamp": "2026-10-01T10:01:00Z", "duration_ms": 90}])
        await self.emit(project, prod, [{"message": "request failed", "service": "api", "level": "error",
                                         "timestamp": "2026-10-01T11:01:00Z", "duration_ms": 20}])
        body = {"question": "timeout", "environment_ids": [ids["dev"]], "timezone": "UTC",
                "start": "2026-10-01T10:00:00Z", "end": "2026-10-01T10:30:00Z"}
        result = (await self.client.post(f"/projects/{project['id']}/ask", json=body)).json()
        self.assertEqual({item["environment"] for item in result["evidence"]}, {"dev"})
        self.assertIn("90.0 ms", result["answer"])
        narrow = {**body, "start": "2026-10-01T10:00:30Z", "end": "2026-10-01T10:02:00Z"}
        result = (await self.client.post(f"/projects/{project['id']}/ask", json=narrow)).json()
        self.assertEqual(result["evidence"], [])
        self.assertTrue(any("boundary bucket" in gap for gap in result["gaps"]))

        current = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.emit(project, dev, [{"message": "request timeout", "service": "api", "level": "error",
                                        "timestamp": current.isoformat()}])
        immediate = (await self.client.post(f"/projects/{project['id']}/ask", json={
            "question": "last hour timeout", "environment_ids": [ids["dev"]], "timezone": "UTC",
        })).json()
        self.assertTrue(immediate["evidence"], "new observations must be queryable before the quarter-hour ends")

    async def test_unmatched_topic_does_not_fall_back_to_unrelated_memories(self):
        project=await self.project();source=await self.source(project)
        await self.emit(project,source,[{'message':'database connection timeout','timestamp':'2026-10-01T10:01:00Z'}])
        body={'question':'GPU overheating','environment_ids':[project['environments'][0]['id']],
              'timezone':'UTC','start':'2026-10-01T10:00:00Z','end':'2026-10-01T11:00:00Z'}
        result=(await self.client.post(f"/projects/{project['id']}/ask",json=body)).json()
        self.assertEqual(result['evidence'],[])
        self.assertTrue(any('no matching' in gap for gap in result['gaps']))

    async def test_compound_topics_require_one_coherent_memory(self):
        project = await self.project(); source = await self.source(project)
        corpus = [
            ('db-timeout', 'database connection timeout', 'error'),
            ('db-healthy', 'database connection', 'info'),
            ('http-timeout', 'http request timeout', 'error'),
            ('cache-unavailable', 'cache redis unavailable', 'error'),
            ('cache-healthy', 'cache redis', 'info'),
            ('http-unavailable', 'http server unavailable', 'error'),
            ('cpu-only', 'cpu', 'info'),
            ('memory-only', 'memory', 'info'),
            ('permission-denied', 'auth permission denied', 'error'),
            ('permission-present', 'auth permission', 'info'),
            ('queue-retry', 'worker queue retry failed', 'error'),
            ('cpu-pressure', 'cpu memory', 'warning'),
        ]
        await self.emit(project, source, [
            {'message': message, 'service': service, 'level': level,
             'timestamp': '2026-10-01T10:01:00Z', 'duration_ms': 100}
            for service, message, level in corpus
        ])
        scope = {'environment_ids': [project['environments'][0]['id']], 'timezone': 'UTC',
                 'start': '2026-10-01T10:00:00Z', 'end': '2026-10-01T10:30:00Z'}
        cases = [
            ('database timeouts', {'db-timeout'}),
            ('cache redis unavailable', {'cache-unavailable'}),
            ('permission denied', {'permission-denied'}),
            ('worker queue retry', {'queue-retry'}),
            ('unknownwidget', set()), ('cpu memory', {'cpu-pressure'}),
            ('requests timeout', {'http-timeout'}),
            ('auth permission denied', {'permission-denied'}),
            ('database errors', {'db-timeout'}),
            ('cache failures', {'cache-unavailable'}),
            ('warnings', {'cpu-pressure'}),
            ('database warnings', set()),
            ('cpu warning', {'cpu-pressure'}), ('cpu warn', {'cpu-pressure'}),
            ('unknownwidget errors', set()),
            ('db-timeout', {'db-timeout'}), ('db_timeout', {'db-timeout'}),
            ('database redis', set()),
            ('database latency', set()), ('cpu duration', set()),
            ('timeout unknownwidget', set()),
            ('Please show me which database timeouts were observed in the logs '
             'and tell us about the supporting evidence for those events on October 1', {'db-timeout'}),
            ('Could you please explain which cache redis unavailable events were '
             'observed and show me their supporting evidence?', {'cache-unavailable'}),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                search = await self.client.get(f"/projects/{project['id']}/search", params={'q': query})
                self.assertEqual(search.status_code, 200, search.text)
                self.assertEqual({row['service'] for row in search.json()['evidence']}, expected)
                reply = await self.client.post(f"/projects/{project['id']}/ask", json={**scope, 'question': query})
                self.assertEqual(reply.status_code, 200, reply.text)
                result = reply.json()
                self.assertEqual({row['service'] for row in result['evidence']}, expected)
                self.assertEqual(set(result['cited_evidence_ids']), {row['id'] for row in result['evidence']})
                self.assertEqual(result['provenance']['provider'], 'extractive')
                if expected:
                    self.assertIn('do not establish complete traffic totals or a cause', result['findings'][0]['interpretation'])
                else:
                    self.assertEqual(result['findings'], [])
        # Generic failures retain an explicit disjunction across failure/failed/error.
        for query in ('failures', 'failure', 'failed', 'error'):
            result = (await self.client.get(f"/projects/{project['id']}/search", params={'q': query})).json()
            self.assertEqual({row['service'] for row in result['evidence']},
                             {name for name, _, level in corpus if level == 'error'})

    async def test_compound_duration_followups_keep_citations_and_scope(self):
        project = await self.project(); source = await self.source(project)
        await self.client.post(f"/projects/{project['id']}/environments", json={'name': 'prod'})
        prod = await self.source(project, 'prod', 'prod')
        foreign = await self.project('foreign'); foreign_source = await self.source(foreign)
        base = {'message': 'database connection timeout', 'service': 'api', 'level': 'error',
                'timestamp': '2026-10-01T10:01:00Z', 'duration_ms': 100}
        await self.emit(project, source, [base, {**base, 'timestamp': '2026-10-01T10:16:00Z', 'duration_ms': 200},
            {**base, 'message': 'database connection', 'duration_ms': 900},
            {**base, 'message': 'http request timeout', 'duration_ms': 900},
            {**base, 'service': 'worker', 'duration_ms': 900},
            {**base, 'timestamp': '2026-10-01T11:01:00Z', 'duration_ms': 900}])
        await self.emit(project, prod, [{**base, 'duration_ms': 900}])
        await self.emit(foreign, foreign_source, [{**base, 'duration_ms': 900}])
        conversation = (await self.client.post(f"/projects/{project['id']}/conversations", json={})).json()
        path = f"/conversations/{conversation['id']}/messages"
        first = await self.client.post(path, json={
            'question': 'Please show me which database timeouts were observed on October 1',
            'environment_ids': [project['environments'][0]['id']], 'timezone': 'UTC', 'service': 'api',
            'start': '2026-10-01T10:00:00Z', 'end': '2026-10-01T10:30:00Z'})
        self.assertEqual(first.status_code, 200, first.text)
        initial = first.json()['result']
        ids = {row['id'] for row in initial['evidence']}
        self.assertEqual(len(ids), 2)
        for question in ['Were duration measurements available for those failures?'] * 3:
            response = await self.client.post(path, json={'question': question})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()['result']
            self.assertEqual({row['id'] for row in result['evidence']}, ids)
            self.assertEqual(set(result['cited_evidence_ids']), ids)
            metrics = result['plan']['cells'][0]['metrics']
            self.assertEqual((metrics['events'], metrics['duration_count'], metrics['duration_mean_ms']), (2, 2, 150))
            self.assertEqual(result['context_metadata']['recalled_count'], 1)
            self.assertEqual(result['provenance']['provider'], 'extractive')
            for row in result['evidence']:
                self.assertEqual((row['project_id'], row['environment'], row['service']), (project['id'], 'dev', 'api'))
            for finding in result['findings']:
                self.assertIn('do not establish complete traffic totals or a cause', finding['interpretation'])

    @staticmethod
    def model_result():
        identifiers = [str(uuid4()), str(uuid4())]
        rows = [{
            "id": identifier, "environment": "dev", "source": "app", "service": "api",
            "level": "error", "summary": "Observed timeout aggregate.", "event_count": 1,
            "bucket_start": f"2026-10-01T1{index}:00:00+00:00",
            "bucket_end": f"2026-10-01T1{index}:01:00+00:00", "duration_count": 1,
            "duration_sum_ms": 20.0, "duration_min_ms": 20.0, "duration_max_ms": 20.0,
            "status_counts": {"500": 1},
        } for index, identifier in enumerate(identifiers)]
        return identifiers, {"answer": f"Computed [{identifiers[0]}] and [{identifiers[1]}].",
                             "evidence": rows, "cited_evidence_ids": identifiers.copy(),
                             "gaps": [], "plan": {"cells": [], "assumptions": []}}

    async def test_optional_model_keeps_computed_evidence_when_model_cites_one_row(self):
        identifiers, result = self.model_result()
        class Store:
            def settings(self): return {"provider": "openai_compatible", "base_url": "https://models.example/v1", "chat_model": "chat"}
            def model_api_key(self): return None
        class Models:
            async def generate(self, *_args):
                return {"answer": f"One useful observation [{identifiers[0]}].",
                        "cited_evidence_ids": [identifiers[0]]}
        with patch("logchat.local.app.OpenAICompatibleModels", return_value=Models()):
            output = await _optional_model_answer(Store(), result, "What happened?")
        self.assertEqual([row["id"] for row in output["evidence"]], identifiers)
        self.assertEqual(output["cited_evidence_ids"], identifiers)
        self.assertIn(identifiers[1], output["answer"])
        self.assertEqual(output["provenance"], {
            "provider": "openai_compatible", "model": "chat", "status": "model",
            "computed_findings_provider": "extractive",
        })

    async def test_optional_model_rejects_invented_inline_evidence_id(self):
        identifiers, result = self.model_result()
        computed = result["answer"]
        invented = str(uuid4())
        class Store:
            def settings(self): return {"provider": "openai_compatible", "base_url": "https://models.example/v1", "chat_model": "chat"}
            def model_api_key(self): return None
        class Models:
            async def generate(self, *_args):
                return {"answer": f"Invented support [{invented}].", "cited_evidence_ids": [identifiers[0]]}
        with patch("logchat.local.app.OpenAICompatibleModels", return_value=Models()):
            output = await _optional_model_answer(Store(), result, "What happened?")
        self.assertEqual(output["answer"], computed)
        self.assertEqual(output["cited_evidence_ids"], identifiers)
        self.assertEqual([row["id"] for row in output["evidence"]], identifiers)
        self.assertNotIn(invented, output["answer"])
        self.assertTrue(any("optional model was unavailable" in gap for gap in output["gaps"]))
        self.assertEqual(output["provenance"]["provider"], "extractive")
        self.assertEqual(output["provenance"]["model"], "deterministic_aggregate")
        self.assertEqual(output["provenance"]["status"], "fallback")

    async def test_findings_cite_every_row_supporting_totals_and_keep_scope(self):
        project = await self.project()
        source = await self.source(project)
        await self.emit(project, source, [
            {"message": "database timeout", "service": "api", "level": "error",
             "timestamp": f"2026-10-01T10:{minute:02d}:00Z", "duration_ms": minute}
            for minute in (1, 16, 31, 46)
        ])
        body = {"question": "database timeout", "environment_ids": [project["environments"][0]["id"]],
                "service": "api", "timezone": "UTC", "start": "2026-10-01T10:00:00Z",
                "end": "2026-10-01T11:00:00Z"}
        response = await self.client.post(f"/projects/{project['id']}/ask", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        ids = {row["id"] for row in result["evidence"]}
        self.assertEqual(len(ids), 4)
        self.assertEqual(set(result["cited_evidence_ids"]), ids)
        finding, = result["findings"]
        self.assertEqual(set(finding["evidence_ids"]), ids)
        for identifier in ids:
            self.assertIn(f"[{identifier}]", result["answer"])
        self.assertIn("4 observed events", finding["observation"])
        self.assertIn("23.5 ms", finding["observation"])
        self.assertIn("do not establish complete traffic totals or a cause", finding["interpretation"])
        self.assertEqual(finding["scope"]["project_id"], project["id"])
        self.assertEqual(finding["scope"]["environment_id"], body["environment_ids"][0])
        self.assertEqual(finding["scope"]["service"], "api")
        self.assertEqual(finding["scope"]["timezone"], "UTC")
        self.assertEqual(finding["scope"]["window"]["start"], "2026-10-01T10:00:00+00:00")
        self.assertEqual(result["provenance"]["provider"], "extractive")

    async def test_empty_evidence_has_no_findings_and_does_not_claim_configured_model(self):
        project = await self.project()
        await self.client.put("/settings/models", json={"provider": "ollama", "chat_model": "unused-model"})
        with patch("logchat.local.app.LocalModels") as model:
            response = await self.client.post(f"/projects/{project['id']}/ask", json={
                "question": "database timeout", "environment_ids": [project["environments"][0]["id"]],
                "timezone": "UTC", "start": "2026-10-01T10:00:00Z", "end": "2026-10-01T11:00:00Z",
            })
            model.assert_not_called()
        result = response.json()
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["cited_evidence_ids"], [])
        self.assertEqual(result["provenance"], {"provider": "extractive", "model": "deterministic_aggregate",
                                                "status": "extractive", "reason": "empty_evidence"})
        self.assertTrue(any("behavior is unknown" in gap for gap in result["gaps"]))

    async def test_model_unavailable_preserves_extractive_answer_and_provenance(self):
        from pipeline.models import ModelUnavailable
        _, result = self.model_result()
        computed = result["answer"]
        class Store:
            def settings(self):
                return {"provider": "ollama", "base_url": None, "chat_model": "configured", "embedding_model": None}
        class Models:
            async def generate(self, *_args):
                raise ModelUnavailable("Synthetic failure")
        with patch("logchat.local.app.LocalModels", return_value=Models()):
            output = await _optional_model_answer(Store(), result, "What happened?")
        self.assertEqual(output["answer"], computed)
        self.assertEqual(output["provenance"]["provider"], "extractive")
        self.assertEqual(output["provenance"]["status"], "fallback")

    async def test_multiple_cells_fit_model_context_without_losing_computed_evidence(self):
        import json
        identifiers, result = self.model_result()
        template=result['evidence'][0]
        result['evidence']=[{**template,'id':str(uuid4()),'summary':'Observed database connection timeout. '*35} for _ in range(40)]
        ids=[row['id'] for row in result['evidence']]
        result['answer']=f'Computed observed metrics [{ids[0]}].'
        result['cited_evidence_ids']=ids.copy()
        result['plan']['cells']=[{'id':'dev','evidence_ids':ids[:20],'metrics':{'events':20}},
                                  {'id':'prod','evidence_ids':ids[20:],'metrics':{'events':20}}]
        class Store:
            def settings(self):return {'provider':'ollama','base_url':'http://127.0.0.1:11434','chat_model':'small','embedding_model':None}
        captured=[]
        class Models:
            async def generate(self,instruction,context,schema):
                captured.append(context)
                return {'answer':'Observed errors.','cited_evidence_ids':[ids[0]]}
        with patch('logchat.local.app.LocalModels',return_value=Models()):
            output=await _optional_model_answer(Store(),result,'Compare the observed failures')
        assert len(captured[0]['evidence'])==4
        assert {row['id'] for row in captured[0]['evidence']}=={*ids[:2],*ids[20:22]}
        assert len(json.dumps(captured[0]))<24000
        assert len(output['evidence'])==40 and output['cited_evidence_ids']==ids
        assert [cell['metrics']['events'] for cell in result['plan']['cells']]==[20,20]


if __name__ == "__main__":
    unittest.main()
