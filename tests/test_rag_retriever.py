"""Deterministic transport tests plus opt-in actual Ollama/sqlite-vec evaluation."""
import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from logchat.rag.contracts import (
    BuildResult, Coverage, EmbeddedChunk, EmbeddingSpec, ExactMetrics,
    RetrievalCell, SearchHit, SemanticChunk, SourceIdentity, TimeWindow,
)
from logchat.rag.retriever import (
    RetrievalConfig, plan_cells, previous_month_window, previous_period, retrieve,
)

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
WINDOW = TimeWindow(START, START + timedelta(hours=1))
IDENTITY = SourceIdentity("owner", "project", "prod", "stdout")
SPEC = EmbeddingSpec("deterministic-test", "fixture", "fake-vectors-v1", 3)
CELL = RetrievalCell("prod-current", "owner", "project", "prod", WINDOW,
                     source_ids=("stdout",))


def chunk(identifier="auth", summary="SessionExpired login credential rejected", **changes):
    value = SemanticChunk(identifier, IDENTITY, WINDOW, "api", "error", summary,
                          ExactMetrics(event_count=2, status_counts={"401": 2}),
                          pattern_id="pattern-" + identifier,
                          first_event_at=START, last_event_at=START + timedelta(minutes=1),
                          loss_notes=("template_values_removed",))
    return replace(value, **changes)


def hit(value=None, score=.8, rank=1, method="vector"):
    return SearchHit(value or chunk(), score, rank, method)


class Provider:
    """Explicitly fake vectors: these tests do not establish semantic recall."""
    def __init__(self, spec=SPEC, vectors=((1., 0., 0.),), failure=None):
        self.spec, self.vectors, self.failure = spec, vectors, failure
        self.calls = []

    async def embed(self, texts, *, purpose):
        self.calls.append((texts, purpose))
        if self.failure:
            raise self.failure
        return self.vectors


class Backend:
    embedding_spec = SPEC

    def __init__(self, vectors=(hit(),), lexical=(), coverage=None):
        self.vectors, self.lexical = vectors, lexical
        self.coverage = (Coverage(IDENTITY, WINDOW, "complete", ExactMetrics(event_count=2)),) if coverage is None else coverage
        self.calls = []

    def vector_search(self, cell, vector, *, limit):
        self.calls.append(("vector", cell, limit, vector))
        return self.vectors.get(cell.cell_id, ()) if isinstance(self.vectors, dict) else self.vectors

    def lexical_search(self, cell, query, *, limit):
        self.calls.append(("lexical", cell, limit, query))
        return self.lexical.get(cell.cell_id, ()) if isinstance(self.lexical, dict) else self.lexical

    def coverage_for(self, cell, *, limit):
        self.calls.append(("coverage", cell, limit))
        return self.coverage.get(cell.cell_id, ()) if isinstance(self.coverage, dict) else self.coverage


class RetrieverTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_query_call_contract_and_immutable_provenance(self):
        backend, provider = Backend(), Provider()
        result = await retrieve("Why must users sign in again?", (CELL,), backend, provider)
        self.assertEqual(provider.calls, [(("Why must users sign in again?",), "query")])
        self.assertEqual(result.candidates[0].chunk, chunk())
        self.assertEqual(result.candidates[0].vector_score, .8)
        self.assertEqual(result.candidates[0].vector_rank, 1)
        self.assertIsNone(result.candidates[0].lexical_rank)
        self.assertEqual(result.gaps, ())
        detail = json.loads(next(p.detail for p in result.provenance if p.stage == "retrieval_cell"))
        self.assertEqual(detail["vector_returned"], 1)
        self.assertTrue(detail["revalidation_required"])

    async def test_rrf_fuses_independent_ranks(self):
        first, second = chunk("first"), chunk("second")
        backend = Backend(vectors=(hit(first, .9, 1), hit(second, .8, 2)),
                          lexical=(hit(second, .01, 1, "lexical"),))
        result = await retrieve("credential denied", (CELL,), backend, Provider())
        self.assertEqual([c.chunk.chunk_id for c in result.candidates], ["second", "first"])
        self.assertAlmostEqual(result.candidates[0].score, 1 / 62 + 1 / 61)

    async def test_rare_exact_identifier_can_survive_low_similarity(self):
        rare = chunk("rare", "NebulaShardLeaseLost ownership fence rejected")
        backend = Backend(vectors=(hit(rare, .2),), lexical=(hit(rare, .01, method="lexical"),))
        result = await retrieve("NebulaShardLeaseLost", (CELL,), backend, Provider())
        self.assertEqual([c.chunk.chunk_id for c in result.candidates], ["rare"])

    async def test_plain_lexical_match_does_not_bypass_semantic_gate(self):
        backend = Backend(vectors=(hit(score=.2),), lexical=(hit(method="lexical"),))
        result = await retrieve("login", (CELL,), backend, Provider())
        self.assertEqual(result.candidates, ())
        self.assertIn("prod-current:no_relevant_candidates", result.gaps)

    async def test_redaction_placeholder_is_not_an_exact_identifier(self):
        redacted = chunk(summary="credential [REDACTED_SECRET] rejected")
        backend = Backend(vectors=(hit(redacted, .2),), lexical=(hit(redacted, method="lexical"),))
        result = await retrieve("[REDACTED_SECRET]", (CELL,), backend, Provider())
        self.assertFalse(result.candidates)

    async def test_exact_identifier_can_be_outside_bounded_vector_pool(self):
        rare = chunk("rare", "error E_EDGE_987 repeated")
        backend = Backend(vectors=(), lexical=(hit(rare, .1, method="lexical"),))
        result = await retrieve("E_EDGE_987", (CELL,), backend, Provider())
        self.assertEqual(result.candidates[0].chunk, rare)
        self.assertIsNone(result.candidates[0].vector_score)

    async def test_candidate_limit_distinct_from_search_pool(self):
        backend = Backend(vectors=tuple(hit(chunk(str(i)), .9 - i * .01, i + 1) for i in range(5)))
        result = await retrieve("login rejected", (CELL,), backend, Provider(),
                                config=RetrievalConfig(candidate_limit=2, vector_limit=5, lexical_limit=3))
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual([call[2] for call in backend.calls if call[0] != "coverage"], [5, 3])
        self.assertTrue(json.loads(next(p.detail for p in result.provenance if p.stage == "retrieval_cell"))["candidate_cap_reached"])

    async def test_all_comparison_cells_searched_independently(self):
        cells = plan_cells("owner", "project", ("prod", "dev"), WINDOW,
                           source_ids=("stdout",), comparison_window=previous_period(WINDOW))
        first = replace(chunk(), identity=SourceIdentity("owner", "project", "prod", "stdout"))
        backend = Backend(vectors={cells[0].cell_id: (hit(first),)}, coverage={cells[0].cell_id: (Coverage(IDENTITY, WINDOW, "complete"),)})
        provider = Provider()
        result = await retrieve("Compare login failures", cells, backend, provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len([c for c in backend.calls if c[0] == "vector"]), 4)
        self.assertEqual(len(result.candidates), 1)
        for cell in cells[1:]:
            self.assertIn(cell.cell_id + ":no_relevant_candidates", result.gaps)
            self.assertTrue(any(gap.startswith(cell.cell_id) and "coverage_missing" in gap for gap in result.gaps))

    async def test_model_mismatch_outage_and_bad_vector_never_call_lexical(self):
        providers = [Provider(spec=replace(SPEC, revision="different")),
                     Provider(failure=RuntimeError("SECRET provider payload")),
                     Provider(vectors=((0., 0., 0.),)), Provider(vectors=((1., 2.),)),
                     Provider(vectors=((float("nan"), 0., 0.),)), Provider(vectors=((True, 0., 0.),))]
        for provider in providers:
            with self.subTest(provider=provider):
                backend = Backend()
                result = await retrieve("login", (CELL,), backend, provider)
                self.assertEqual(result.candidates, ())
                self.assertEqual([call[0] for call in backend.calls], ["coverage"])
                self.assertEqual(result.provenance[-1].status, "unavailable")
                self.assertNotIn("SECRET", repr(result))
                self.assertTrue(result.coverage)

    async def test_spec_change_during_embedding_is_refused(self):
        class ChangingProvider(Provider):
            async def embed(self, texts, *, purpose):
                self.spec = replace(self.spec, revision="new")
                return self.vectors
        result = await retrieve("login", (CELL,), Backend(), ChangingProvider())
        self.assertEqual(result.candidates, ())
        self.assertIn("query_embedding_unavailable_or_invalid", result.gaps)

    async def test_cross_scope_results_fail_closed(self):
        cases = [replace(IDENTITY, owner_id="other"), replace(IDENTITY, project_id="other"),
                 replace(IDENTITY, environment_id="dev"), replace(IDENTITY, source_id="other")]
        for identity in cases:
            with self.subTest(identity=identity):
                result = await retrieve("login", (CELL,), Backend(vectors=(hit(chunk(identity=identity)),)), Provider())
                self.assertEqual(result.candidates, ())
                self.assertNotIn("other", repr(result))

    async def test_service_denial_and_half_open_boundaries(self):
        after = TimeWindow(WINDOW.end, WINDOW.end + timedelta(hours=1))
        before = TimeWindow(WINDOW.start - timedelta(hours=1), WINDOW.start)
        for wrong in [chunk(service="other"), chunk(window=after, first_event_at=None, last_event_at=None),
                      chunk(window=before, first_event_at=None, last_event_at=None)]:
            result = await retrieve("login", (replace(CELL, service="api"),), Backend(vectors=(hit(wrong),)), Provider())
            self.assertFalse(result.candidates)

    async def test_boundary_overlap_is_evidence_with_nonexact_window_metrics(self):
        narrow = replace(CELL, window=TimeWindow(START + timedelta(minutes=30), WINDOW.end))
        result = await retrieve("login", (narrow,), Backend(), Provider())
        self.assertEqual(len(result.candidates), 1)
        self.assertIn(narrow.cell_id + ":boundary_chunk_metrics_not_window_exact", result.gaps)
        self.assertEqual(result.candidates[0].chunk.metrics.event_count, 2)

    async def test_coverage_is_source_specific_not_union_across_sources(self):
        cell = replace(CELL, source_ids=("stdout", "browser"))
        result = await retrieve("login", (cell,), Backend(), Provider())
        self.assertTrue(any("coverage_missing" in gap for gap in result.gaps))

    async def test_coverage_merge_empty_gap_failure_and_sentinel_paging(self):
        middle = START + timedelta(minutes=30)
        rows = (Coverage(IDENTITY, TimeWindow(START, middle), "complete"),
                Coverage(IDENTITY, TimeWindow(middle, WINDOW.end), "empty"))
        result = await retrieve("login", (CELL,), Backend(coverage=rows), Provider())
        self.assertFalse(result.gaps)
        result = await retrieve("login", (CELL,), Backend(coverage=rows), Provider(), config=RetrievalConfig(coverage_limit=1))
        self.assertEqual(len(result.coverage), 1)
        self.assertIn(CELL.cell_id + ":coverage_limit_reached_completeness_unknown", result.gaps)
        for status in ("gap", "failed"):
            result = await retrieve("login", (CELL,), Backend(coverage=(Coverage(IDENTITY, WINDOW, status),)), Provider())
            self.assertTrue(any("coverage_incomplete" in gap for gap in result.gaps))

    async def test_exact_coverage_cap_does_not_falsely_report_truncation(self):
        result = await retrieve("login", (CELL,), Backend(), Provider(), config=RetrievalConfig(coverage_limit=1))
        self.assertFalse(result.gaps)

    async def test_unknown_source_inventory_remains_unknown(self):
        result = await retrieve("login", (replace(CELL, source_ids=()),), Backend(), Provider())
        self.assertIn(CELL.cell_id + ":source_inventory_unknown", result.gaps)

    async def test_unsafe_backend_content_rejected_and_query_redacted(self):
        provider, backend = Provider(), Backend(vectors=(hit(chunk(summary="password=hunter2")),))
        result = await retrieve("credential password=hunter2", (CELL,), backend, provider)
        self.assertNotIn("hunter2", repr(provider.calls))
        self.assertNotIn("hunter2", repr(result))
        self.assertFalse(result.candidates)

    async def test_cross_scope_coverage_is_not_returned(self):
        result = await retrieve("login", (CELL,), Backend(coverage=(Coverage(replace(IDENTITY, owner_id="other"), WINDOW, "complete"),)), Provider())
        self.assertFalse(result.coverage)
        self.assertIn(CELL.cell_id + ":coverage_unavailable_or_invalid", result.gaps)

    async def test_invalid_backend_ranks_scores_bounds_and_conflicting_chunks(self):
        failures = [(hit(score=float("inf")),), (hit(score=2),), (hit(rank=0),),
                    (hit(), hit(chunk("second"), rank=1)), (hit(), hit(rank=2))]
        for rows in failures:
            result = await retrieve("login", (CELL,), Backend(vectors=rows), Provider())
            self.assertFalse(result.candidates)
        backend = Backend(vectors=(hit(),), lexical=(hit(chunk(summary="different immutable text"), method="lexical"),))
        result = await retrieve("login", (CELL,), backend, Provider())
        self.assertFalse(result.candidates)
        result = await retrieve("login", (CELL,), Backend(vectors=(hit(), hit(chunk("b"), rank=2))), Provider(), config=RetrievalConfig(vector_limit=1))
        self.assertFalse(result.candidates)

    async def test_backend_failures_do_not_expose_raw_errors(self):
        class Broken(Backend):
            def vector_search(self, *args, **kwargs):
                raise RuntimeError("secret-canary")
        result = await retrieve("login", (CELL,), Broken(), Provider())
        self.assertFalse(result.candidates)
        self.assertNotIn("secret-canary", repr(result))

    async def test_invalid_input_rejected_before_calls(self):
        backend, provider = Backend(), Provider()
        bad_cells = [(), (CELL, CELL), (CELL, replace(CELL, cell_id="second", owner_id="other")),
                     (replace(CELL, source_ids=("stdout", "stdout")),)]
        for cells in bad_cells:
            with self.assertRaises(ValueError):
                await retrieve("login", cells, backend, provider)
        for question in ("", " ", "x" * 4001):
            with self.assertRaises(ValueError):
                await retrieve(question, (CELL,), backend, provider)
        self.assertFalse(backend.calls)
        self.assertFalse(provider.calls)


class PlanningTests(unittest.TestCase):
    def test_cell_product_and_effective_scope_stable(self):
        cells = plan_cells("owner", "project", ("prod", "dev"), WINDOW,
                           source_ids={"prod": ("stdout",), "dev": ("browser",)},
                           service="api", comparison_window=previous_period(WINDOW))
        self.assertEqual(len(cells), 4)
        self.assertEqual(len({c.cell_id for c in cells}), 4)
        self.assertEqual([c.source_ids for c in cells], [("stdout",), ("stdout",), ("browser",), ("browser",)])
        self.assertEqual(cells, plan_cells("owner", "project", ("prod", "dev"), WINDOW,
                           source_ids={"prod": ("stdout",), "dev": ("browser",)},
                           service="api", comparison_window=previous_period(WINDOW)))

    def test_equivalent_elapsed_duration_through_dst_and_month_end(self):
        tz = ZoneInfo("America/New_York")
        window = TimeWindow(datetime(2026, 3, 6, 12, tzinfo=tz), datetime(2026, 3, 13, 12, tzinfo=tz))
        prior = previous_month_window(window, timezone_name="America/New_York")
        self.assertEqual(window.end - window.start, timedelta(hours=167))
        self.assertEqual(prior.end - prior.start, window.end - window.start)
        self.assertEqual(prior.start.astimezone(tz).day, 6)
        month_end = TimeWindow(datetime(2026, 3, 31, 12, tzinfo=tz), datetime(2026, 4, 1, 12, tzinfo=tz))
        prior = previous_month_window(month_end, timezone_name="America/New_York")
        self.assertEqual(prior.start.astimezone(tz).day, 28)
        self.assertEqual(prior.end - prior.start, timedelta(days=1))

    def test_nonexistent_calendar_anchor_preserves_utc_duration(self):
        tz = ZoneInfo("America/New_York")
        window = TimeWindow(datetime(2026, 4, 8, 2, 30, tzinfo=tz), datetime(2026, 4, 8, 3, tzinfo=tz))
        prior = previous_month_window(window, timezone_name="America/New_York")
        self.assertEqual(prior.end - prior.start, timedelta(minutes=30))
        self.assertEqual(prior.start.astimezone(tz).hour, 3)

    def test_invalid_config_and_naive_dates(self):
        for kwargs in ({"candidate_limit": 0}, {"max_cells": 33}, {"vector_limit": 101},
                       {"coverage_limit": 10000}, {"min_vector_similarity": float("nan")},
                       {"candidate_limit": 20, "vector_limit": 1, "lexical_limit": 1}):
            with self.assertRaises(ValueError):
                RetrievalConfig(**kwargs)
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 1, 1), datetime(2026, 1, 2))


class SQLiteScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_scope_is_applied_before_top_k_in_actual_index(self):
        from logchat.rag.storage import SQLiteVectorStore
        good = chunk("expected", "refresh credentials rejected")
        records = [EmbeddedChunk(good, (.7, .7, 0.), SPEC)]
        for index, identity in enumerate((replace(IDENTITY, owner_id="other"),
                replace(IDENTITY, project_id="other"), replace(IDENTITY, environment_id="dev"),
                replace(IDENTITY, source_id="browser"))):
            records.append(EmbeddedChunk(chunk("denied-" + str(index), identity=identity), (1., 0., 0.), SPEC))
        after = TimeWindow(WINDOW.end, WINDOW.end + timedelta(hours=1))
        records.append(EmbeddedChunk(chunk("after-boundary", window=after, first_event_at=None, last_event_at=None), (1., 0., 0.), SPEC))
        records.append(EmbeddedChunk(chunk("other-service", service="worker"), (1., 0., 0.), SPEC))
        with tempfile.TemporaryDirectory() as directory:
            backend = SQLiteVectorStore(Path(directory) / "scope.sqlite3", SPEC)
            with closing(backend.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                backend.write_result(connection, BuildResult("scope-fixture", tuple(records), (Coverage(IDENTITY, WINDOW, "complete"),)))
                connection.commit()
            result = await retrieve("different wording", (replace(CELL, service="api"),), backend,
                                    Provider(), config=RetrievalConfig(candidate_limit=1, vector_limit=1, lexical_limit=1))
        self.assertEqual([c.chunk.chunk_id for c in result.candidates], ["expected"])


ACTUAL_DOCUMENTS = (
    ("auth", "SessionExpired login refresh credential rejected user signed out"),
    ("storage", "Object storage upload failed exhausted retry budget"),
    ("audio", "Audio synthesis completed song playback ready"),
    ("database", "Database DeadlockDetected transaction rolled back"),
    ("normal", "HTTP request completed successfully health check"),
    ("rare", "NebulaShardLeaseLost partition ownership token rejected"),
)
ACTUAL_QUERIES = (
    ("Why do people have to authenticate again?", ("auth",)),
    ("Are file transfers repeatedly failing?", ("storage",)),
    ("What caused the gravitational waves from colliding black holes?", ()),
    ("How to bake a sourdough loaf?", ()),
    ("NebulaShardLeaseLost", ("rare",)),
    ("Are songs ready to listen to?", ("audio",)),
    ("Do database writers interfere with each other?", ("database",)),
)


async def actual_model_evaluation(directory):
    """Host hook: real provider, persisted SQLite vectors and labelled metrics."""
    from logchat.rag.embeddings import OllamaEmbeddingProvider
    from logchat.rag.storage import SQLiteVectorStore
    provider = await OllamaEmbeddingProvider.create()
    backend = SQLiteVectorStore(Path(directory) / "retrieval-eval.sqlite3", provider.spec)
    vectors = await provider.embed([summary for _, summary in ACTUAL_DOCUMENTS], purpose="document")
    documents = [chunk(identifier, summary) for identifier, summary in ACTUAL_DOCUMENTS]
    result = BuildResult("actual-retrieval-fixture", tuple(EmbeddedChunk(doc, tuple(vector), provider.spec)
                         for doc, vector in zip(documents, vectors)),
                         (Coverage(IDENTITY, WINDOW, "complete", ExactMetrics(event_count=12)),))
    with closing(backend.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        backend.write_result(connection, result)
        connection.commit()
    # Reopen to demonstrate durable retrieval, independently of provider caches.
    backend = SQLiteVectorStore(Path(directory) / "retrieval-eval.sqlite3", provider.spec)
    reports = []
    for question, expected in ACTUAL_QUERIES:
        result = await retrieve(question, (CELL,), backend, provider)
        actual = [candidate.chunk.chunk_id for candidate in result.candidates]
        matched = set(actual).intersection(expected)
        reports.append({"question": question, "expected": list(expected), "retrieved": actual,
                        "precision": len(matched) / len(actual) if actual else (1.0 if not expected else 0.0),
                        "recall": len(matched) / len(expected) if expected else None,
                        "hit_rank": next((rank for rank, value in enumerate(actual, 1) if value in expected), None),
                        "scores": {c.chunk.chunk_id: round(c.vector_score, 6) if c.vector_score is not None else None for c in result.candidates}})
    return {"embedding_model": provider.spec.model, "revision": provider.spec.revision,
            "dimensions": provider.spec.dimensions, "similarity_floor": .5,
            "document_count": len(documents), "reports": reports}


@unittest.skipUnless(os.getenv("LOGCHAT_RUN_ACTUAL_EMBEDDINGS") == "1", "actual Ollama evaluation opt-in")
class ActualModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_labelled_paraphrases_unrelated_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            report = await actual_model_evaluation(directory)
        print("ACTUAL_RETRIEVAL_EVALUATION=" + json.dumps(report, sort_keys=True))
        for row in report["reports"]:
            if row["expected"]:
                self.assertEqual(row["recall"], 1., row)
                self.assertEqual(row["hit_rank"], 1, row)
            else:
                self.assertEqual(row["retrieved"], [], row)


if __name__ == "__main__":
    unittest.main()
