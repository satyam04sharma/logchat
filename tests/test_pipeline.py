import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock

import httpx
from connectors.docker import DockerConnector

from pipeline.builder import build_job
from pipeline.models import LocalModels, OpenAICompatibleModels
from pipeline.scheduler import choose_interval, completed_volume, schedule_once
from pipeline.store import PipelineStore
from pipeline.types import LogEvent


class SchedulerIntervalTests(unittest.TestCase):
    def test_busy_volume_shortens_interval(self):
        interval, reason = choose_interval(4_000, 1_800, 86_400, 1_800)
        self.assertLess(interval, 1_800)
        self.assertIn("target-batch", reason)

    def test_quiet_volume_backs_off(self):
        interval, reason = choose_interval(0, 1_800, 86_400, 1_800)
        self.assertGreater(interval, 1_800)
        self.assertIn("quiet-backoff", reason)

    def test_retention_under_ten_minutes_overrides_normal_floor(self):
        interval, _ = choose_interval(0, 300, 480, 1_800)
        self.assertEqual(interval, 240)

    def test_interval_never_exceeds_half_retention(self):
        for retention in (60, 480, 3_600, 172_800):
            interval, _ = choose_interval(0, 300, retention, 21_600)
            self.assertLessEqual(interval, retention // 2)


class CompletedFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def source(self):
        return dict(source_id="source", owner_id="owner", project_id="project", environment_id="env",
                    lease_token="lease", connector="docker", connector_config={},
                    interval_seconds=1800, retention_seconds=86400)

    def feedback(self, source, now, count=4000, seconds=1800):
        return {**{key: source[key] for key in ("source_id", "owner_id", "project_id", "environment_id")},
                "event_count": count, "window_seconds": seconds, "window_end": now}

    async def test_none_probe_uses_completed_count_and_actual_duration_without_fetch(self):
        source = self.source()
        now = datetime.now(timezone.utc)
        connector = Mock()
        async def probe(**kwargs): return None
        connector.probe = probe
        for count, seconds, direction in ((4000, 1800, "busy"), (0, 1800, "quiet"), (2000, 300, "busy")):
            with self.subTest(count=count, seconds=seconds):
                store = Mock()
                store.claim_due_sources.return_value = [source]
                store.completed_feedback.return_value = self.feedback(source, now, count, seconds)
                store.enqueue_job.return_value = "job"
                with patch("pipeline.scheduler.make_connector", return_value=connector):
                    self.assertEqual(await schedule_once(store), 1)
                store.completed_feedback.assert_called_once_with("source", "lease")
                args = store.enqueue_job.call_args.args
                self.assertEqual(args[3], count)
                self.assertEqual(args[4], choose_interval(count, seconds, 86400, 1800)[0])
                self.assertIn("completed-window;", args[5])
                self.assertGreater(args[4], 1800) if direction == "quiet" else self.assertLess(args[4], 1800)
        connector.fetch.assert_not_called()

    async def test_stale_or_missing_feedback_stays_unknown_and_reason_labelled(self):
        source = self.source()
        now = datetime.now(timezone.utc)
        stale = self.feedback(source, now - timedelta(hours=2), 0)
        connector = Mock()
        async def probe(**kwargs): return None
        connector.probe = probe
        for feedback, reason in ((None, "missing"), (stale, "stale")):
            store = Mock()
            store.claim_due_sources.return_value = [source]
            store.completed_feedback.return_value = feedback
            with patch("pipeline.scheduler.make_connector", return_value=connector):
                await schedule_once(store)
            args = store.enqueue_job.call_args.args
            self.assertIsNone(args[3])
            self.assertEqual(args[4], 1800)
            self.assertIn("completed-window-" + reason, args[5])
            self.assertIn("volume-unknown", args[5])

    def test_scoped_freshness_bounds_reject_invalid_evidence(self):
        source, now = self.source(), datetime.now(timezone.utc)
        for key in ("source_id", "owner_id", "project_id", "environment_id"):
            feedback = self.feedback(source, now)
            feedback[key] = "other"
            self.assertEqual(completed_volume(source, feedback, now)[2], "completed-window-scope-mismatch")
        for override in ({"event_count": None}, {"event_count": -1}, {"event_count": True},
                         {"event_count": 5001}, {"window_seconds": 0}, {"window_seconds": float("nan")},
                         {"window_seconds": 21601}, {"window_end": now.replace(tzinfo=None)}):
            self.assertEqual(completed_volume(source, {**self.feedback(source, now), **override}, now)[2],
                             "completed-window-invalid")
        for end in (now + timedelta(seconds=1), now - timedelta(seconds=3601)):
            self.assertEqual(completed_volume(source, self.feedback(source, end), now)[2], "completed-window-stale")
        source["retention_seconds"] = 480
        self.assertEqual(completed_volume(source, self.feedback(source, now - timedelta(seconds=241)), now)[2],
                         "completed-window-stale")

    async def test_real_probe_takes_priority_over_completed_feedback(self):
        store = Mock()
        store.claim_due_sources.return_value = [self.source()]
        connector = Mock()
        async def probe(**kwargs): return 0
        connector.probe = probe
        with patch("pipeline.scheduler.make_connector", return_value=connector):
            await schedule_once(store)
        store.completed_feedback.assert_not_called()
        self.assertEqual(store.enqueue_job.call_args.args[3], 0)
        self.assertIn("probe;quiet-backoff", store.enqueue_job.call_args.args[5])

    async def test_docker_probe_only_inspects_container_and_uses_completed_feedback(self):
        paths = []
        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={})
        connector = DockerConnector({"container": "synthetic"}, transport=httpx.MockTransport(handler))
        store = Mock()
        source = self.source()
        store.claim_due_sources.return_value = [source]
        store.completed_feedback.return_value = self.feedback(source, datetime.now(timezone.utc))
        with patch("pipeline.scheduler.make_connector", return_value=connector):
            await schedule_once(store)
        self.assertEqual(paths, ["/containers/synthetic/json"])
        self.assertEqual(store.enqueue_job.call_args.args[3], 4000)
        self.assertLess(store.enqueue_job.call_args.args[4], 1800)

    async def test_feedback_database_failure_remains_unknown_without_exception_text(self):
        store = Mock()
        store.claim_due_sources.return_value = [self.source()]
        store.completed_feedback.side_effect = RuntimeError("private database detail")
        connector = Mock()
        async def probe(**kwargs): return None
        connector.probe = probe
        with patch("pipeline.scheduler.make_connector", return_value=connector):
            await schedule_once(store)
        args = store.enqueue_job.call_args.args
        self.assertIsNone(args[3])
        self.assertEqual(args[5], "completed-window-unavailable;volume-unknown;retention-cap=43200s")

    def test_store_preserves_unknown_count_at_sql_boundary(self):
        store = PipelineStore("synthetic-unused")
        with patch.object(store, "_call", return_value=[{"id": None}]) as call:
            store.enqueue_job("source", "lease", datetime.now(timezone.utc), None, 1800, "unknown")
        self.assertIsNone(call.call_args.args[1][3])


class FakeStore:
    def __init__(self):
        self.token = "new-token"
        self.completed = False
        self.cursor = None

    def complete_job(self, job_id, token, cursor_ts, cursor_event_id, chunks, coverage):
        if token != self.token or self.completed:
            return False
        self.completed = True
        self.cursor = (cursor_ts, cursor_event_id)
        return True

    def retry_job(self, job_id, token, error_summary, delay_seconds=30):
        return token == self.token and not self.completed


class LeaseFencingTests(unittest.TestCase):
    def test_stale_completion_cannot_advance_cursor(self):
        store = FakeStore()
        now = datetime.now(timezone.utc)
        self.assertFalse(store.complete_job("job", "old-token", now, "e1", [], []))
        self.assertIsNone(store.cursor)
        self.assertTrue(store.complete_job("job", "new-token", now, "e1", [], []))
        self.assertEqual(store.cursor, (now, "e1"))

    def test_completion_is_idempotent_and_retry_is_fenced(self):
        store = FakeStore()
        now = datetime.now(timezone.utc)
        self.assertTrue(store.retry_job("job", "new-token", "safe_category"))
        self.assertFalse(store.retry_job("job", "old-token", "safe_category"))
        self.assertTrue(store.complete_job("job", "new-token", now, None, [], []))
        self.assertFalse(store.complete_job("job", "new-token", now + timedelta(seconds=1), None, [], []))


class BuilderStore:
    def __init__(self):
        self.completed = None
        self.split = False

    def get_job_source(self, job_id, token):
        return {"connector": "fake", "connector_config": {}, "credential_ref": "ref", "retention_seconds": 3600}

    def renew_job(self, *args): return True
    def complete_job(self, *args): self.completed = args; return True
    def split_job(self, *args): self.split = True; return True
    def retry_job(self, *args): return True


class FakeConnector:
    def __init__(self, events): self.events = events
    async def fetch(self, since, until):
        for event in self.events:
            yield event


class FakeModels:
    def __init__(self, secret): self.secret, self.seen = secret, []
    async def summarize(self, events): self.seen.extend(events); return "model repeated " + self.secret
    async def embed(self, text): self.embedded = text; return [0.0] * 768


class BuilderTests(unittest.IsolatedAsyncioTestCase):
    def job(self):
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
        return {"id": "job", "lease_token": "token", "owner_id": "owner", "project_id": "project",
                "environment_id": "environment", "source_id": "source", "requested_start": start,
                "window_start": start, "window_end": start + timedelta(minutes=3)}

    async def test_redacts_before_models_and_clamps_chunk_to_job(self):
        secret = "standalone-credential-value"
        event = LogEvent("raw-id", self.job()["window_start"] + timedelta(seconds=1), "fake", "api", "error",
                         "message " + secret, "fingerprint", duration_ms=12, request_status=500)
        store, models = BuilderStore(), FakeModels(secret)
        with patch("pipeline.builder.read_credential", return_value=secret), \
             patch("pipeline.builder.make_connector", return_value=FakeConnector([event])):
            self.assertTrue(await build_job(store, models, self.job()))
        self.assertNotIn(secret, models.seen[0].message)
        self.assertNotIn(secret, models.embedded)
        chunks = store.completed[4]
        self.assertEqual(chunks[0]["bucket_start"], self.job()["window_start"].isoformat())
        self.assertNotIn(secret, chunks[0]["summary"])

    async def test_oversized_job_splits_without_completion(self):
        start = self.job()["window_start"]
        events = [LogEvent(str(i), start + timedelta(seconds=i), "fake", "api", "info", "ok", "f") for i in range(3)]
        store = BuilderStore()
        with patch("pipeline.builder.read_credential", return_value="credential"), \
             patch("pipeline.builder.make_connector", return_value=FakeConnector(events)), \
             patch("pipeline.builder.MAX_EVENTS_PER_JOB", 2):
            self.assertTrue(await build_job(store, FakeModels("credential"), self.job()))
        self.assertTrue(store.split)
        self.assertIsNone(store.completed)

    async def test_summary_zero_chat_calls_preserve_all_200_event_metrics(self):
        job = self.job()
        events = [LogEvent(str(i), job["window_start"] + timedelta(seconds=1), "fake", "api", "error",
                           ("cache unavailable " if i == 17 else "database timeout ") + "界" * 4000, "same-pattern", duration_ms=i,
                           request_status=500 if i % 2 else 200) for i in range(200)]
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/api/embed":
                return httpx.Response(200, json={"embeddings": [[0.0] * 768]})
            return httpx.Response(200, json={"response": '{"selected_terms":[]}'})
        store = BuilderStore()
        models = LocalModels(transport=httpx.MockTransport(handler))
        with patch("pipeline.builder.read_credential", return_value="credential"), \
             patch("pipeline.builder.make_connector", return_value=FakeConnector(events)):
            self.assertTrue(await build_job(store, models, job))
        self.assertIsNotNone(store.completed)
        self.assertEqual([request.url.path for request in requests], ["/api/embed"])
        chunk = store.completed[4][0]
        coverage = store.completed[5][0]
        for metrics in (chunk, coverage):
            self.assertEqual(metrics["event_count"], 200)
            self.assertEqual(metrics["duration_count"], 200)
            self.assertEqual(metrics["duration_sum_ms"], sum(range(200)))
            self.assertEqual(metrics["duration_min_ms"], 0)
            self.assertEqual(metrics["duration_max_ms"], 199)
            self.assertEqual(metrics["status_counts"], {"200": 100, "500": 100})
        self.assertEqual(store.completed[2], job["window_end"])
        self.assertEqual(json.loads(requests[0].content)["input"], chunk["summary"])
        summary = json.loads(chunk["summary"])
        self.assertEqual(summary["operational_terms"], ["timeout", "database", "cache", "unavailable"])
        facts = summary["facts"]
        self.assertEqual(facts["event_count"], 200)
        self.assertEqual(facts["duration_sum_ms"], 19900.0)

    async def test_large_status_map_projects_text_without_losing_terms_or_exact_metrics(self):
        job = self.job()
        first = job["window_start"] + timedelta(seconds=1)
        secret = "standalone-credential-value"
        events = [LogEvent(str(i), first, "fake", "api", "error",
                           ("cache unavailable tls " if i == 17 else "database timeout ") + secret,
                           "same-pattern", duration_ms=i, request_status=10 ** 79 + i)
                  for i in range(200)]
        expected_statuses = {str(event.request_status): 1 for event in events}
        self.assertGreater(len(json.dumps(expected_statuses)), 12000)
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/api/embed":
                return httpx.Response(200, json={"embeddings": [[0.0] * 768]})
            return httpx.Response(503)
        transport = httpx.MockTransport(handler)
        outputs = []
        for models in (LocalModels(transport=transport),
                       OpenAICompatibleModels("https://models.example/v1", "chat",
                                              transport=transport, embedding_transport=transport)):
            with self.subTest(provider=type(models).__name__):
                requests.clear()
                store = BuilderStore()
                with patch("pipeline.builder.read_credential", return_value=secret), \
                     patch("pipeline.builder.make_connector", return_value=FakeConnector(events + [events[17]])):
                    self.assertTrue(await build_job(store, models, job))
                self.assertEqual([request.url.path for request in requests], ["/api/embed"])
                chunk, coverage = store.completed[4][0], store.completed[5][0]
                for metrics in (chunk, coverage):
                    self.assertEqual(metrics["event_count"], 200)
                    self.assertEqual(metrics["duration_count"], 200)
                    self.assertEqual(metrics["duration_sum_ms"], 19900.0)
                    self.assertEqual(metrics["duration_min_ms"], 0)
                    self.assertEqual(metrics["duration_max_ms"], 199)
                    self.assertEqual(metrics["status_counts"], expected_statuses)
                text = chunk["summary"]
                outputs.append(text)
                self.assertLessEqual(len(text), 12000)
                self.assertNotIn(secret, text)
                summary = json.loads(text)
                self.assertEqual(summary["operational_terms"], ["timeout", "database", "cache", "unavailable", "tls"])
                self.assertNotIn("status_counts", summary["facts"])
                self.assertEqual(summary["text_projection"]["omitted_fact_maps"],
                                 {"status_counts": {"distinct_values": 200, "event_count": 200}})
                self.assertEqual(summary["text_projection"]["character_limit"], 12000)
                self.assertEqual(summary["facts"]["observed_start"], first.isoformat())
                self.assertEqual(summary["facts"]["observed_end"], first.isoformat())
                self.assertEqual(summary["facts"]["service_counts"], {"api": 200})
                self.assertEqual(summary["facts"]["severity_counts"], {"error": 200})
                self.assertEqual(summary["facts"]["duration_sum_ms"], 19900.0)
                # Existing embedding-input budget is independent of durable text.
                self.assertEqual(json.loads(requests[0].content)["input"], text[:4000])
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
