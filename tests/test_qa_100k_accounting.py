"""Canonical ledger QA rejects corruption in disposable copies; no models."""
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from connectors.local import parse_push_events
from logchat.local.raw_capture import content_digest
from logchat.rag.builder import event_key
from logchat.rag.contracts import SourceIdentity
from logchat.rag.scheduler import _identity_key


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def accounting_fixture(tmp_path):
    qa = load_module("qa_100k_ledger_test", ROOT / "scripts/qa_100k.py")
    producer = load_module("qa_100k_producer_test", ROOT / "examples/loadtest/producer.py")
    state = tmp_path / "original"
    state.mkdir()
    configs = [dict(app=app, dataset=dataset, run_id="ledger-fixture", count=2,
                    project_id="project", environment_id="prod", source_id=app + "-source",
                    timestamp="2026-10-10T12:00:00+00:00",
                    templates=[dict(message="Routine synthetic operation", weight=2)])
               for app, dataset in (("web", "Apache"), ("worker", "Linux"), ("auth", "OpenSSH"))]
    with sqlite3.connect(state / "rag.db") as db:
        db.executescript("""
            CREATE TABLE rag_chunks(source_id TEXT,payload TEXT);
            CREATE TABLE rag_schedule_jobs(job_id TEXT PRIMARY KEY,scope TEXT);
            CREATE TABLE rag_schedule_event_keys(scope TEXT,event_key TEXT,job_id TEXT,
                PRIMARY KEY(scope,event_key));
            CREATE TABLE rag_schedule_event_digests(scope TEXT,event_key TEXT,content_digest TEXT,job_id TEXT,
                PRIMARY KEY(scope,event_key));
        """)
        for config in configs:
            identity = SourceIdentity("local-os-user", config["project_id"], config["environment_id"], config["source_id"])
            scope, job = _identity_key(identity), config["app"] + "-job"
            records = parse_push_events([producer.event(config, index) for index in range(config["count"])],
                                        source=config["source_id"], preserve_fields=True)
            db.execute("INSERT INTO rag_schedule_jobs VALUES(?,?)", (job, scope))
            for record in records:
                key = event_key(identity, record.event_id)
                db.execute("INSERT INTO rag_schedule_event_keys VALUES(?,?,?)", (scope, key, job))
                db.execute("INSERT INTO rag_schedule_event_digests VALUES(?,?,?,?)", (scope, key, content_digest(record), job))
            # Minimal corpus fixture for the QA accounting reader; it does not
            # claim model-generated prose or exercise compact model quality.
            chunk = dict(identity=dict(owner_id=identity.owner_id, project_id=identity.project_id,
                                       environment_id=identity.environment_id, source_id=identity.source_id),
                         metrics=dict(event_count=2, duration_sum_ms=5011, status_counts={"200": 1, "504": 1}),
                         compact_evidence=json.dumps(dict(records=[dict(event_id=record.event_id) for record in records])))
            db.execute("INSERT INTO rag_chunks VALUES(?,?)", (config["source_id"], json.dumps(chunk)))
    return qa, state, configs


@pytest.mark.parametrize("fault", ["missing_digest", "wrong_scope_digest", "changed_digest", "orphan_digest_job", "orphan_key_job", "extra_scope", "mismatched_same_scope_jobs"])
def test_disposable_corruption_rejected_with_original_ledger_untouched(accounting_fixture, tmp_path, fault):
    qa, original, configs = accounting_fixture
    original_hash = hashlib.sha256((original / "rag.db").read_bytes()).hexdigest()
    baseline = qa.reconcile(original, configs)
    assert baseline["passed"]
    assert baseline["ledger"]["event_keys"] == baseline["ledger"]["event_digests"] == 6
    changed = tmp_path / fault
    changed.mkdir()
    shutil.copy2(original / "rag.db", changed / "rag.db")
    with sqlite3.connect(changed / "rag.db") as db:
        scope, key, job = db.execute("SELECT scope,event_key,job_id FROM rag_schedule_event_keys LIMIT 1").fetchone()
        if fault == "missing_digest":
            db.execute("DELETE FROM rag_schedule_event_digests WHERE scope=? AND event_key=?", (scope, key))
        elif fault == "wrong_scope_digest":
            db.execute("UPDATE rag_schedule_event_digests SET scope='wrong-scope' WHERE scope=? AND event_key=?", (scope, key))
        elif fault == "changed_digest":
            db.execute("UPDATE rag_schedule_event_digests SET content_digest=? WHERE scope=? AND event_key=?", ("0" * 64, scope, key))
        elif fault == "orphan_digest_job":
            db.execute("UPDATE rag_schedule_event_digests SET job_id='missing-job' WHERE scope=? AND event_key=?", (scope, key))
        elif fault == "orphan_key_job":
            db.execute("UPDATE rag_schedule_event_keys SET job_id='missing-job' WHERE scope=? AND event_key=?", (scope, key))
        elif fault == "extra_scope":
            db.execute("INSERT INTO rag_schedule_event_keys VALUES('extra-scope','extra-key',?)", (job,))
            db.execute("INSERT INTO rag_schedule_event_digests VALUES('extra-scope','extra-key',?,?)", ("0" * 64, job))
        else:
            db.execute("INSERT INTO rag_schedule_jobs VALUES('second-job',?)", (scope,))
            db.execute("UPDATE rag_schedule_event_digests SET job_id='second-job' WHERE scope=? AND event_key=?", (scope, key))
    rejected = qa.reconcile(changed, configs)
    assert not rejected["passed"] and not rejected["ledger"]["passed"]
    if fault in {"missing_digest", "wrong_scope_digest", "changed_digest"}:
        assert any(not source["canonical_event_digests_match"] for source in rejected["sources"])
    assert hashlib.sha256((original / "rag.db").read_bytes()).hexdigest() == original_hash
    assert qa.reconcile(original, configs)["passed"]
