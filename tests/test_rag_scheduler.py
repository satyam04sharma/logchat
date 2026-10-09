"""Real temporary SQLite queue tests; embeddings here are explicitly synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from logchat.rag.contracts import (
    BuildResult, Coverage, EmbeddedChunk, EmbeddingSpec, ExactMetrics,
    PreparedBatch, SemanticChunk, SourceIdentity, StageProvenance, TimeWindow,
)
from logchat.rag.scheduler import OverlappingReplay, SchedulerConfig, SQLiteSchedulerStore, choose_interval


NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
IDENTITY = SourceIdentity("owner", "project", "dev", "browser")
SPEC = EmbeddingSpec("unit-test", "fake", "test-v1", 3)


def batch(name="batch-1", identity=IDENTITY, count=2, offset=0):
    window = TimeWindow(NOW + timedelta(seconds=offset), NOW + timedelta(seconds=offset + 30))
    metrics = ExactMetrics(event_count=count)
    chunks = (SemanticChunk(name + "-chunk", identity, window, "web", "warn",
                            "Visitor credentials expired before checkout", metrics, "pattern"),) if count else ()
    coverage = (Coverage(identity, window, "complete" if count else "empty", metrics),)
    keys = tuple(hashlib.sha256(f"{identity}:{name}:{i}".encode()).hexdigest() for i in range(count))
    return PreparedBatch(name, identity, window, chunks, coverage, keys)


def result(prepared):
    return BuildResult(prepared.batch_id, tuple(EmbeddedChunk(c, (1., 2., 3.), SPEC) for c in prepared.chunks),
                       prepared.coverage, (StageProvenance("embedding", "ok", "unit_test", model="fake"),))


def writer(connection, built):
    connection.execute("CREATE TABLE IF NOT EXISTS published(batch_id TEXT PRIMARY KEY, count INTEGER)")
    connection.execute("INSERT INTO published VALUES(?,?)", (built.batch_id, len(built.chunks)))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite"
        self.store = SQLiteSchedulerStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def ready_job(self, prepared=None, **register):
        prepared = prepared or batch()
        self.store.register_source(prepared.identity, now=NOW, **register)
        self.store.enqueue(prepared, now=NOW)
        self.store.dispatch(now=NOW + timedelta(seconds=30))
        return self.store.claim_jobs(now=NOW + timedelta(seconds=30))[0]

    def test_no_sources_and_unknown_volume_do_not_invent_empty_observations(self):
        self.assertEqual(self.store.dispatch(now=NOW), ())
        self.assertEqual(self.store.claim_jobs(now=NOW), ())
        interval, reason = choose_interval(None, 0, 86400)
        self.assertEqual((interval, reason), (30, "volume_unknown"))
        self.store.register_source(IDENTITY, now=NOW)
        self.assertIsNone(self.store.status(IDENTITY)["last_observed_count"])
        self.assertIsNone(self.store.status(IDENTITY)["cursor"])

    def test_successful_empty_window_is_distinct_and_advances_after_atomic_publish(self):
        job = self.ready_job(batch(count=0))
        self.assertTrue(self.store.complete(job, result(job.batch), writer, now=NOW + timedelta(seconds=31)))
        status = self.store.status(IDENTITY)
        self.assertEqual(status["last_observed_count"], 0)
        self.assertEqual(status["jobs"], {"completed": 1})
        self.assertEqual(datetime.fromisoformat(status["cursor"]), job.batch.window.end)
        self.assertGreater(status["interval_seconds"], 30)

    def test_burst_threshold_and_retention_cap_dispatch_without_waiting_normal_cadence(self):
        self.store.register_source(IDENTITY, interval_seconds=300, now=NOW)
        self.store.enqueue(batch(count=2000), now=NOW)
        self.assertEqual(len(self.store.dispatch(now=NOW)), 1)
        other = replace(IDENTITY, source_id="short-retention")
        self.store.register_source(other, interval_seconds=300, retention_seconds=6, now=NOW)
        self.store.enqueue(batch("short", other), now=NOW)
        self.assertEqual(self.store.dispatch(now=NOW + timedelta(seconds=2)), ())
        self.assertEqual(len(self.store.dispatch(now=NOW + timedelta(seconds=3))), 1)

    def test_later_due_source_is_not_starved_by_undue_sources_on_bounded_page(self):
        for index in range(3):
            scope = replace(IDENTITY, source_id=str(index))
            self.store.register_source(scope, interval_seconds=300, now=NOW)
            self.store.enqueue(batch(str(index), scope), now=NOW)
        urgent = replace(IDENTITY, source_id="urgent")
        self.store.register_source(urgent, interval_seconds=5, now=NOW)
        expected = self.store.enqueue(batch("urgent", urgent), now=NOW)
        self.assertEqual(self.store.dispatch(now=NOW + timedelta(seconds=5), limit=1), (expected,))

    def test_multiple_sources_get_fair_turns_and_one_active_job_per_source(self):
        second = replace(IDENTITY, source_id="terminal")
        for scope in (IDENTITY, second):
            self.store.register_source(scope, now=NOW)
            self.store.enqueue(batch(scope.source_id, scope), now=NOW)
        self.store.enqueue(batch("next-browser", offset=30), now=NOW)
        first = self.store.dispatch(now=NOW + timedelta(seconds=30), limit=1)
        other = self.store.dispatch(now=NOW + timedelta(seconds=30), limit=1)
        self.assertEqual(len(first + other), 2)
        self.assertNotEqual(first, other)
        self.assertEqual(len(self.store.claim_jobs(now=NOW + timedelta(seconds=30), limit=8)), 2)
        self.assertEqual(self.store.dispatch(now=NOW + timedelta(seconds=100)), ())

    def test_restart_deduplicates_pending_and_completed_batch_without_reinference(self):
        prepared = batch()
        job = self.ready_job(prepared)
        restarted = SQLiteSchedulerStore(self.path)
        self.assertEqual(restarted.enqueue(prepared, now=NOW), job.job_id)
        self.assertTrue(restarted.complete(job, result(prepared), writer, now=NOW + timedelta(seconds=31)))
        self.assertEqual(SQLiteSchedulerStore(self.path).enqueue(prepared, now=NOW), job.job_id)
        self.assertEqual(self.store.claim_jobs(now=NOW + timedelta(hours=1)), ())
        self.assertFalse(self.store.complete(job, result(prepared), writer, now=NOW + timedelta(seconds=32)))

    def test_overlapping_event_replay_rejects_before_count_changes(self):
        prepared = batch()
        self.ready_job(prepared)
        with self.assertRaises(OverlappingReplay):
            self.store.enqueue(replace(prepared, batch_id="changed-envelope"), now=NOW)
        self.assertEqual(self.store.status(IDENTITY)["jobs"], {"running": 1})

    def test_expired_worker_cannot_complete_fail_or_renew_reclaimed_lease(self):
        job = self.ready_job()
        at = NOW + timedelta(seconds=330)
        fresh = self.store.claim_jobs(now=at)[0]
        self.assertNotEqual(job.lease_token, fresh.lease_token)
        self.assertFalse(self.store.complete(job, result(job.batch), writer, now=at))
        self.assertFalse(self.store.fail(job, "embedding_unavailable", now=at))
        self.assertFalse(self.store.renew(job, now=at))
        self.assertTrue(self.store.complete(fresh, result(fresh.batch), writer, now=at))

    def test_model_unavailability_retries_boundedly_preserving_pending_work_and_cursor(self):
        job = self.ready_job()
        self.assertTrue(self.store.fail(job, "embedding_unavailable", now=NOW + timedelta(seconds=31)))
        self.assertEqual(self.store.status(IDENTITY)["jobs"], {"deferred": 1})
        self.assertIsNone(self.store.status(IDENTITY)["cursor"])
        self.assertEqual(self.store.claim_jobs(now=NOW + timedelta(seconds=35)), ())
        retry = SQLiteSchedulerStore(self.path).claim_jobs(now=NOW + timedelta(seconds=36))[0]
        self.assertEqual(retry.batch, job.batch)
        self.store.fail(retry, "embedding_unavailable", now=NOW + timedelta(seconds=36))
        final = self.store.claim_jobs(now=NOW + timedelta(seconds=46))[0]
        self.store.fail(final, "embedding_unavailable", now=NOW + timedelta(seconds=46))
        self.assertEqual(self.store.status(IDENTITY)["jobs"], {"failed": 1})
        self.assertIsNone(self.store.status(IDENTITY)["cursor"])
        self.assertEqual(self.store.claim_jobs(now=NOW + timedelta(days=1)), ())

    def test_backend_failure_rolls_back_published_rows_and_cursor_together(self):
        job = self.ready_job()

        def broken(connection, built):
            writer(connection, built)
            raise RuntimeError("synthetic failure")

        with self.assertRaises(RuntimeError):
            self.store.complete(job, result(job.batch), broken, now=NOW + timedelta(seconds=31))
        self.assertEqual(self.store.status(IDENTITY)["jobs"], {"running": 1})
        self.assertIsNone(self.store.status(IDENTITY)["cursor"])
        self.assertTrue(self.store.complete(job, result(job.batch), writer, now=NOW + timedelta(seconds=31)))

    def test_explicit_retry_restores_terminal_work_without_releasing_other_leases(self):
        job = self.ready_job()
        self.store.fail(job, "embedding_unavailable", now=NOW + timedelta(seconds=31), retryable=False)
        other = replace(IDENTITY, project_id="isolated")
        self.assertEqual(self.store.retry_failed(other, now=NOW + timedelta(seconds=32)), 0)
        self.assertEqual(self.store.retry_failed(IDENTITY, now=NOW + timedelta(seconds=32)), 1)
        recovered = self.store.claim_jobs(now=NOW + timedelta(seconds=32))[0]
        self.assertEqual(recovered.attempt, 1)
        self.assertEqual(recovered.batch, job.batch)
        self.assertEqual(self.store.retry_failed(IDENTITY, now=NOW + timedelta(seconds=33)), 0)
        self.assertTrue(self.store.complete(recovered, result(recovered.batch), writer,
                                            now=NOW + timedelta(seconds=33)))

    def test_bad_scope_metrics_or_unavailable_result_cannot_publish(self):
        job = self.ready_job()
        built = result(job.batch)
        wrong = replace(built.chunks[0], chunk=replace(built.chunks[0].chunk,
                        identity=replace(IDENTITY, project_id="another")))
        for invalid in (replace(built, chunks=(wrong,)), replace(built, chunks=()),
                        replace(built, provenance=(StageProvenance("embedding", "unavailable", "test"),))):
            with self.assertRaises(ValueError):
                self.store.complete(job, invalid, writer, now=NOW + timedelta(seconds=31))
        self.assertIsNone(self.store.status(IDENTITY)["cursor"])

    def test_safe_categories_and_timestamp_bounds(self):
        job = self.ready_job()
        with self.assertRaises(ValueError):
            self.store.fail(job, "provider password=private", now=NOW)
        with self.assertRaises(ValueError):
            self.store.claim_jobs(now=datetime(2026, 10, 4))
        self.assertEqual(choose_interval(None, 0, 1)[0], 1)


if __name__ == "__main__":
    unittest.main()
