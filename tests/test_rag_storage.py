"""Real sqlite-vec index and transaction tests with explicitly synthetic vectors."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from logchat.rag.builder import embed_batch, prepare_batch
from logchat.rag.contracts import BuildResult, EmbeddedChunk, RetrievalCell, TimeWindow
from logchat.rag.scheduler import SQLiteSchedulerStore
from logchat.rag.storage import SQLiteVectorStore
from tests.test_rag_builder import START, WINDOW, IDENTITY, SPEC, FixtureProvider, event


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "semantic.sqlite"
        self.store = SQLiteVectorStore(self.path, SPEC)
        self.cell = RetrievalCell("current", IDENTITY.owner_id, IDENTITY.project_id, IDENTITY.environment_id, WINDOW)

    def tearDown(self):
        self.temp.cleanup()

    def built(self, *, identity=IDENTITY, window=WINDOW, events=None, vector=(1., .2, .3)):
        batch = prepare_batch(identity, events if events is not None else [event()], window)
        return BuildResult(batch.batch_id, tuple(EmbeddedChunk(c, vector, SPEC) for c in batch.chunks), batch.coverage)

    def publish(self, result):
        connection = self.store.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.store.write_result(connection, result)
            connection.commit()
        finally:
            connection.close()

    def test_real_vector_restart_and_idempotent_publication(self):
        result = self.built()
        self.publish(result)
        self.publish(result)
        store = SQLiteVectorStore(self.path, SPEC)
        hits = store.vector_search(self.cell, [1, .2, .3], limit=5)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].score, 1, places=5)
        self.assertEqual(hits[0].chunk, result.chunks[0].chunk)
        self.assertEqual(store.statistics()["vectors"], 1)
        self.assertEqual(store.coverage_for(self.cell), result.coverage)

    def test_scopes_apply_before_top_k_including_multi_source_and_service(self):
        # Excluded rows have the best possible similarity; in-scope rows must
        # still fill top k, proving predicates apply before candidate limiting.
        variants = [replace(IDENTITY, owner_id="other-owner"), replace(IDENTITY, project_id="other-project"),
                    replace(IDENTITY, environment_id="prod"), replace(IDENTITY, source_id="other-source")]
        for identity in variants:
            self.publish(self.built(identity=identity, vector=(1, 0, 0)))
        self.publish(self.built(vector=(.8, .6, 0)))
        self.publish(self.built(events=[event(1, service="worker")], vector=(1, 0, 0)))
        cell = replace(self.cell, source_ids=(IDENTITY.source_id,), service="gateway")
        hits = self.store.vector_search(cell, [1, 0, 0], limit=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk.identity, IDENTITY)
        self.assertEqual(hits[0].chunk.service, "gateway")
        self.assertAlmostEqual(hits[0].score, .8, places=5)
        hits = self.store.vector_search(replace(cell, source_ids=(IDENTITY.source_id, "other-source")), [1, 0, 0], limit=2)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].chunk.identity.source_id, "other-source")

    def test_lexical_scope_and_literal_fts_injection(self):
        self.publish(self.built())
        self.publish(self.built(identity=replace(IDENTITY, project_id="other")))
        hits = self.store.lexical_search(self.cell, 'session " OR project:*', limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk.identity, IDENTITY)
        self.assertEqual(self.store.lexical_search(self.cell, '"**', limit=5), ())
        self.assertEqual(self.store.lexical_search(self.cell, "unrelated_astronomy", limit=5), ())

    def test_half_open_window_filters(self):
        self.publish(self.built())
        next_window = TimeWindow(WINDOW.end, WINDOW.end + timedelta(minutes=15))
        outside = replace(self.cell, window=next_window)
        self.assertEqual(self.store.vector_search(outside, [1, 0, 0], limit=5), ())
        self.assertEqual(self.store.lexical_search(outside, "session", limit=5), ())
        self.assertEqual(self.store.coverage_for(outside), ())

    def test_atomic_rollback_leaves_no_chunk_vector_fts_or_coverage(self):
        connection = self.store.connect()
        connection.execute("BEGIN IMMEDIATE")
        self.store.write_result(connection, self.built())
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        connection.close()
        self.assertEqual(self.store.statistics()["chunks"], 0)
        self.assertEqual(self.store.statistics()["vectors"], 0)
        self.assertEqual(self.store.statistics()["coverage_rows"], 0)
        self.assertEqual(self.store.lexical_search(self.cell, "session", limit=5), ())

    def test_requires_caller_transaction_and_rejects_model_mismatch(self):
        connection = self.store.connect()
        with self.assertRaisesRegex(ValueError, "transaction"):
            self.store.write_result(connection, self.built())
        connection.close()
        with self.assertRaisesRegex(ValueError, "model_mismatch"):
            SQLiteVectorStore(self.path, replace(SPEC, revision="changed"))

    def test_changed_vector_replay_rejected(self):
        self.publish(self.built())
        with self.assertRaisesRegex(ValueError, "vector_conflict"):
            self.publish(self.built(vector=(0, 1, 0)))
        self.assertEqual(self.store.statistics()["vectors"], 1)

    def test_query_bounds_and_nonfinite_vectors_rejected(self):
        for vector in ([0, 0, 0], [1, 2], [1, float("nan"), 0], [True, 1, 0]):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                self.store.vector_search(self.cell, vector, limit=5)
        with self.assertRaises(ValueError): self.store.vector_search(self.cell, [1, 0, 0], limit=101)
        with self.assertRaises(ValueError): self.store.coverage_for(self.cell, limit=10001)

    def test_scoped_inspector(self):
        result = self.built()
        self.publish(result)
        chunk_id = result.chunks[0].chunk.chunk_id
        self.assertIsNone(self.store.get_chunk(chunk_id, owner_id=IDENTITY.owner_id, project_id="wrong"))
        self.assertEqual(self.store.get_chunk(chunk_id, owner_id=IDENTITY.owner_id, project_id=IDENTITY.project_id), result.chunks[0].chunk)

    def test_scheduler_restart_failure_retry_and_atomic_success(self):
        queue = SQLiteSchedulerStore(self.path, connect=self.store.connect)
        batch = prepare_batch(IDENTITY, [event(message="session credential expired token=SECRET_CANARY")], WINDOW)
        queue.register_source(IDENTITY, now=START)
        queue.enqueue(batch, now=START)
        queue.dispatch(now=START + timedelta(seconds=30))
        job = queue.claim_jobs(now=START + timedelta(seconds=30))[0]
        result = asyncio.run(embed_batch(job.batch, FixtureProvider()))
        def failed_writer(connection, value):
            self.store.write_result(connection, value)
            raise RuntimeError("fixture_failure")
        with self.assertRaises(RuntimeError):
            queue.complete(job, result, failed_writer, now=START + timedelta(seconds=31))
        self.assertEqual(self.store.statistics()["chunks"], 0)
        self.assertIsNone(queue.status(IDENTITY)["cursor"])
        queue.fail(job, "embedding_unavailable", now=START + timedelta(seconds=31))
        store = SQLiteVectorStore(self.path, SPEC)
        queue = SQLiteSchedulerStore(self.path, connect=store.connect)
        job = queue.claim_jobs(now=START + timedelta(seconds=40))[0]
        result = asyncio.run(embed_batch(job.batch, FixtureProvider()))
        self.assertTrue(queue.complete(job, result, store.write_result, now=START + timedelta(seconds=41)))
        self.assertIsNotNone(queue.status(IDENTITY)["cursor"])
        self.assertEqual(queue.enqueue(batch, now=START + timedelta(seconds=42)), job.job_id)
        self.assertEqual(store.statistics()["chunks"], 1)
        self.assertNotIn(b"SECRET_CANARY", self.path.read_bytes())
        self.assertEqual(len(store.vector_search(self.cell, [1, .2, .3], limit=5)), 1)


if __name__ == "__main__": unittest.main()
