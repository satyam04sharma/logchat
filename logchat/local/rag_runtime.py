"""Native dispatch adapter around the source-independent semantic core.

Configuration is explicit and immutable for an index. Queries never collect logs.
Prepared work follows the explicit content policy; embeddings run independently.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
import json
import os
from pathlib import Path
import threading

from logchat.rag.contracts import EmbeddingSpec, SourceIdentity, TimeWindow
from logchat.rag.scheduler import SQLiteSchedulerStore


CONFIG_FILE = "rag.json"
# One in-memory original-event intake at a time across sync/async entrypoints.
# Busy callers retry; the executor never accumulates an unbounded raw work queue.
_INTAKE_SLOT = threading.BoundedSemaphore(1)
# Short raw admission must keep working while the model consumes another batch.
_CAPTURE_SLOT = threading.BoundedSemaphore(1)
_INTAKE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="logchat-intake")
MAX_INTAKE_EVENTS = 5000
MAX_INTAKE_BYTES = 8 * 1024 * 1024


async def configure(state_dir: Path, *, base_url="http://127.0.0.1:11434",
                    model="nomic-embed-text", dimensions=768, chat_model="mistral:7b",
                    content_policy="redacted_templates") -> dict:
    if content_policy not in {"redacted_templates","local_model_preserved","local_model_compact"}:
        raise ValueError("Unknown log content policy.")
    preserve = content_policy in {"local_model_preserved","local_model_compact"}
    from .model_profile import ModelProfile
    ModelProfile({"generation_model":chat_model,"content_policy":content_policy})
    from logchat.rag.embeddings import OllamaEmbeddingProvider
    provider = await OllamaEmbeddingProvider.create(base_url=base_url, model=model, dimensions=dimensions,preserve_content=preserve)
    await provider.embed(("Logchat embedding configuration check.",),purpose="document")
    state_dir = Path(state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = {"version": 2, "base_url": base_url, "embedding": asdict(provider.spec), "chat_model": chat_model,
             "generation_model":chat_model,"content_policy":content_policy}
    destination = state_dir / CONFIG_FILE
    if destination.exists():
        previous = json.loads(destination.read_text())
        if previous["embedding"] != value["embedding"]:
            raise ValueError("Embedding index configuration differs; use a separate state directory or explicit reindex migration.")
    # Initialize and validate the real extension before reporting configuration success.
    from logchat.rag.storage import SQLiteVectorStore
    SQLiteVectorStore(state_dir / "rag.db", provider.spec)
    temporary = destination.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return value


class NativeRAGRuntime:
    def __init__(self, store):
        from .raw_capture import CaptureSpool
        self.store = store
        self.config = {"content_policy": "local_model_compact"}
        self.preserve_content = True
        self.model_profile = self.spec = self.backend = self.scheduler = None
        self.provider = None
        self.task = None
        self.state, self.error = "awaiting_model", "model_not_configured"
        self._configuration_lock = threading.Lock()
        self.raw_capture = CaptureSpool(store.state_dir)
        self._refresh_configuration()

    def _refresh_configuration(self):
        """A capture-only runtime can acquire its explicitly configured profile later."""
        if self.model_profile is not None or not (self.store.state_dir / CONFIG_FILE).exists():
            return
        with self._configuration_lock:
            if self.model_profile is not None:
                return
            from .model_profile import ModelProfile
            from logchat.rag.storage import SQLiteVectorStore
            config = json.loads((self.store.state_dir / CONFIG_FILE).read_text())
            profile = ModelProfile(config)
            spec = EmbeddingSpec(**config["embedding"])
            backend = SQLiteVectorStore(self.store.state_dir / "rag.db", spec)
            scheduler = SQLiteSchedulerStore(self.store.state_dir / "rag.db", connect=backend.connect)
            self.config, self.spec, self.backend, self.scheduler = config, spec, backend, scheduler
            self.preserve_content = config.get("content_policy") in {"local_model_preserved", "local_model_compact"}
            self.model_profile = profile
            self.state, self.error = "starting", None

    def _capture(self, project_id, source_id, events):
        from .raw_capture import content_digest
        from logchat.rag.builder import event_key
        from pipeline.models import ModelUnavailable
        if not _CAPTURE_SLOT.acquire(blocking=False):
            raise ModelUnavailable("intake_busy_retry_required")
        try:
            identity = self.identity(project_id, source_id)
            if len(events) > MAX_INTAKE_EVENTS:
                raise ValueError("capture_batch_bound_exceeded")
            pending, deduplicated = events, 0
            if self.scheduler is not None:
                digests = {}
                for event in events:
                    key, digest = event_key(identity, event.event_id), content_digest(event)
                    if key in digests and digests[key] != digest:
                        raise ValueError("event_identity_content_conflict")
                    digests[key] = digest
                unseen = set(self.scheduler.check_event_digests(identity, digests))
                pending = [event for event in events if event_key(identity, event.event_id) in unseen]
                deduplicated = len(events) - len(pending)
            receipt = self.raw_capture.enqueue(identity, pending)
            receipt["accepted"] = len(events)
            receipt["deduplicated"] += deduplicated
            return receipt
        except Exception as error:
            category = str(error) if str(error) in {
                "event_identity_content_conflict", "legacy_replay_digest_unavailable",
                "capture_capacity_exceeded", "capture_batch_bound_exceeded", "capture_invalid_or_conflicting_batch",
                "raw_capture_not_enabled", "capture_storage_unavailable",
            } else "raw_capture_unavailable_retry_required"
            raise ModelUnavailable(category) from None
        finally:
            _CAPTURE_SLOT.release()

    async def drain_raw_once(self) -> int:
        """Delete originals only after compact enqueue commits; retries use core digests."""
        self._refresh_configuration()
        self.raw_capture.expire()
        if self.model_profile is None:
            return 0
        if not _INTAKE_SLOT.acquire(blocking=False):
            return 0
        try:
            claim = self.raw_capture.claim()
        except BaseException:
            _INTAKE_SLOT.release()
            raise
        if claim is None:
            _INTAKE_SLOT.release()
            return 0
        if not self.preserve_content:
            _INTAKE_SLOT.release()
            self.raw_capture.fail(claim, "compact_model_profile_required_for_raw_capture")
            self.state, self.error = "unavailable", "compact_model_profile_required_for_raw_capture"
            return 0
        try:
            current = self.identity(claim.identity.project_id, claim.identity.source_id)
            if current != claim.identity:
                raise ValueError("capture_source_unavailable")
        except Exception:
            _INTAKE_SLOT.release()
            self.raw_capture.fail(claim, "capture_source_unavailable")
            return 0
        try:
            # This entrypoint bypasses the capture toggle, and releases its slot.
            await self._ingest_reserved(claim.identity.project_id, claim.identity.source_id, claim.events)
        except asyncio.CancelledError:
            self.raw_capture.fail(claim)
            raise
        except Exception as error:
            self.raw_capture.fail(claim, getattr(error, "category", "compact_processing_unavailable"))
            return 0
        return self.raw_capture.complete(claim)

    def identity(self, project_id: str, source_id: str) -> SourceIdentity:
        with self.store.connection() as connection:
            source = connection.execute("SELECT environment_id FROM sources WHERE id=? AND project_id=?",
                                        (source_id, project_id)).fetchone()
        if source is None:
            raise ValueError("Source is not part of this project.")
        return SourceIdentity("local-os-user", project_id, source["environment_id"], source_id)

    def ingest(self, project_id: str, source_id: str, events) -> dict:
        """Synchronous adapters use a bounded bridge; async adapters should await ingest_async."""
        from .raw_capture import load_capture_policy
        from pipeline.models import ModelUnavailable
        self._refresh_configuration()
        if load_capture_policy(self.store.state_dir)["mode"] == "retain_until_summarized":
            return self._capture(project_id, source_id, events)
        if self.model_profile is None:
            raise ModelUnavailable("model_not_configured")
        if not self.preserve_content:
            return self._ingest_redacted(project_id, source_id, events)
        if not _INTAKE_SLOT.acquire(blocking=False):
            raise ModelUnavailable("intake_busy_retry_required")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._ingest_reserved(project_id, source_id, events))
        try:
            future = _INTAKE_EXECUTOR.submit(lambda: asyncio.run(self._ingest_reserved(project_id, source_id, events)))
        except Exception:
            _INTAKE_SLOT.release()
            raise ModelUnavailable("intake_bridge_unavailable") from None
        # This compatibility bridge blocks its synchronous caller, not a second
        # service loop. HTTP def routes and file polls run in worker threads.
        return future.result()

    async def ingest_async(self, project_id: str, source_id: str, events) -> dict:
        from .raw_capture import load_capture_policy
        from pipeline.models import ModelUnavailable
        self._refresh_configuration()
        if load_capture_policy(self.store.state_dir)["mode"] == "retain_until_summarized":
            return await asyncio.to_thread(self._capture, project_id, source_id, events)
        if self.model_profile is None:
            raise ModelUnavailable("model_not_configured")
        if not self.preserve_content:
            return self._ingest_redacted(project_id, source_id, events)
        if not _INTAKE_SLOT.acquire(blocking=False):
            raise ModelUnavailable("intake_busy_retry_required")
        return await self._ingest_reserved(project_id, source_id, events)

    async def _ingest_reserved(self, project_id, source_id, events):
        from pipeline.models import ModelUnavailable
        try:
            from logchat.rag.builder import event_key, prepare_preserved_batch
            from logchat.rag.sections import section_batch, compact_summary_batch, validate_compact_batch
            if len(events) > MAX_INTAKE_EVENTS:
                raise ValueError("intake_event_bound_exceeded")
            total = 0
            for event in events:
                total += sum(len(str(getattr(event, name) or "").encode("utf-8"))
                             for name in ("event_id", "source", "service", "level", "release", "fingerprint", "message"))
                if total > MAX_INTAKE_BYTES:
                    raise ValueError("intake_byte_bound_exceeded")
            identity = self.identity(project_id, source_id)
            keys = tuple(event_key(identity, event.event_id) for event in events)
            digests = {}
            for event, key in zip(events, keys):
                # Capture timestamps can change when a file line is retried; its
                # source-scoped ID already binds file generation/position/bytes.
                from .raw_capture import content_digest
                digest = content_digest(event)
                if key in digests and digests[key] != digest:
                    raise ValueError("event_identity_content_conflict")
                digests[key] = digest
            unseen = set(self.scheduler.check_event_digests(identity, digests))
            pending = [event for event, key in zip(events, keys) if key in unseen]
            self.scheduler.register_source(identity, interval_seconds=5)
            jobs = []
            unfinished = sum(count for state, count in self.scheduler.status(identity)["jobs"].items()
                             if state != "completed")
            if pending and unfinished >= 64:
                raise ModelUnavailable("intake_backpressure_retry_required")
            async with asyncio.timeout(300):
                for offset in range(0, len(pending), 400):
                    if unfinished + len(jobs) >= 64:
                        raise ModelUnavailable("intake_backpressure_retry_required")
                    group = pending[offset:offset + 400]
                    window = TimeWindow(min(e.ts for e in group), max(e.ts for e in group) + timedelta(microseconds=1))
                    # Originals exist only in this bounded call's memory. Nothing
                    # enters SQLite before both local generation stages finish.
                    transient = prepare_preserved_batch(identity, group, window)
                    sectioned = await section_batch(transient, self.model_profile.generation("chunking"))
                    compact = await compact_summary_batch(sectioned, self.model_profile.generation("summary"))
                    validate_compact_batch(compact)
                    jobs.append(self.scheduler.enqueue(compact,
                        event_digests={key: digests[key] for key in compact.event_keys}))
            return {"accepted": len(events), "deduplicated": len(events) - len(pending), "jobs": jobs,
                    "state": "durably_prepared", "retrieval": "semantic_hybrid", "indexed": False,
                    "original_events_stored": False}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            category = str(error) if str(error) in {
                "event_identity_content_conflict", "legacy_replay_digest_unavailable",
                "intake_backpressure_retry_required", "intake_event_bound_exceeded", "intake_byte_bound_exceeded",
            } else "compact_intake_unavailable_retry_required"
            from .raw_capture import SAFE_PROCESSING_FAILURES
            stage_category = getattr(error, "category", None) or str(error)
            if stage_category not in SAFE_PROCESSING_FAILURES:
                stage_category = category
            self.state, self.error = "unavailable", stage_category
            unavailable = ModelUnavailable(category)
            unavailable.category = stage_category
            unavailable.phase = getattr(error, "phase", "intake")
            raise unavailable from None
        finally:
            _INTAKE_SLOT.release()

    def _ingest_redacted(self, project_id: str, source_id: str, events) -> dict:
        from logchat.rag.builder import prepare_batch
        identity = self.identity(project_id, source_id)
        self.scheduler.register_source(identity, interval_seconds=5)
        jobs = []
        # Bounds input cardinality without selecting or discarding rare patterns.
        # Exact batch replay is idempotent. Partial replay is rejected by the
        # queue so metrics cannot be silently discarded or partly duplicated.
        pending = events
        for offset in range(0, len(pending), 400):
            group = pending[offset:offset + 400]
            if not group:
                continue
            window = TimeWindow(min(e.ts for e in group), max(e.ts for e in group) + timedelta(microseconds=1))
            batch = prepare_batch(identity, group, window)
            jobs.append(self.scheduler.enqueue(batch))
        return {"accepted": len(pending), "jobs": jobs, "state": "durably_prepared",
                "retrieval": "semantic_hybrid", "indexed": False}

    async def connect_provider(self):
        self._refresh_configuration()
        if self.model_profile is None:
            from pipeline.models import ModelUnavailable
            raise ModelUnavailable("model_not_configured")
        profile = self.model_profile
        provider = await profile.embeddings()
        if self.model_profile is not profile:
            from pipeline.models import ModelUnavailable
            raise ModelUnavailable("model_configuration_changed_retry_required")
        if provider.spec != self.spec:
            from pipeline.models import ModelUnavailable
            raise ModelUnavailable("embedding_revision_mismatch")
        self.provider = provider

    async def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._work())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(30)
            if not self.scheduler.renew(job, lease_seconds=300):
                return

    async def _work(self):
        from logchat.rag.builder import embed_batch
        while True:
            try:
                self._refresh_configuration()
                self.raw_capture.expire()
                if self.model_profile is None:
                    self.state, self.error = "awaiting_model", "model_not_configured"
                    await asyncio.sleep(5)
                    continue
                await self.drain_raw_once()
                if self.provider is None:
                    await self.connect_provider()
                capture_status = self.raw_capture.status()
                capture_failed = capture_status["pending_failed_events"]
                self.state, self.error = (("unavailable", capture_status["pending_failure_categories"][0])
                                         if capture_failed else ("ready", None))
                self.scheduler.dispatch()
                for job in self.scheduler.claim_jobs(limit=1):
                    heartbeat = asyncio.create_task(self._heartbeat(job))
                    failure_category = "embedding_unavailable"
                    try:
                        if self.preserve_content:
                            from logchat.rag.sections import COMPACT_VERSION, validate_compact_batch
                            if any(chunk.compression_version != COMPACT_VERSION for chunk in job.batch.chunks):
                                failure_category = "legacy_raw_work_requires_explicit_migration"
                                raise ValueError(failure_category)
                            validate_compact_batch(job.batch)
                        failure_category = "embedding_unavailable"
                        built = await embed_batch(job.batch, self.provider)
                        self.scheduler.complete(job, built, writer=self.backend.write_result)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        category = getattr(error,"category",None)
                        if category in {"section_timeout","section_provider_unavailable","section_invalid_partition",
                                        "section_invalid_model_output","section_output_budget_exceeded",
                                        "summary_timeout","summary_provider_unavailable","summary_invalid_model_output","summary_invalid_output","summary_input_budget_exceeded",
                                        "summary_local_model_required","summary_model_identity_required",
                                        "summary_output_budget_exceeded","summary_ungrounded_observation"}:
                            failure_category = category
                        self.scheduler.fail(job, failure_category)
                        self.provider = None
                        self.state, self.error = "unavailable", failure_category
                    finally:
                        heartbeat.cancel()
                        try:
                            await heartbeat
                        except asyncio.CancelledError:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                self.provider = None
                self.state, self.error = "unavailable", "embedding_setup_unavailable"
            await asyncio.sleep(1 if self.state == "ready" else 5)

    def status(self, project_id: str) -> dict:
        capture = self.raw_capture.status(project_id)
        if self.model_profile is None:
            return {"mode": "raw_capture", "state": "awaiting_model", "connection_state": "awaiting_model",
                    "processing_state": "awaiting_model", "failed_jobs": 0, "error_category": "model_not_configured",
                    "content_policy": "local_model_compact", "chunk_model": None, "model_profile": None,
                    "embedding": None, "sources": [], "capture": capture}
        sources = self.store.list_sources(project_id)
        stages = []
        for source in sources:
            identity = self.identity(project_id, source["id"])
            try:
                stages.append({"source_id": source["id"], **self.scheduler.status(identity)})
            except ValueError:
                stages.append({"source_id": source["id"], "state": "not_registered"})
        failed_jobs = sum(stage.get("jobs",{}).get("failed",0) for stage in stages)
        capture_failed = capture["pending_failed_events"]
        processing_state = ("failed_jobs_retained" if failed_jobs else
                            "raw_compaction_pending_retry" if capture_failed else self.state)
        return {"mode": "semantic_hybrid", "state": "unavailable" if failed_jobs or capture_failed else self.state,
                "connection_state":self.state,"processing_state":processing_state,
                "failed_jobs":failed_jobs,"error_category":(capture["pending_failure_categories"][0] if capture_failed else
                    self.error or ("failed_jobs_retained" if failed_jobs else None)),
                "content_policy":self.config.get("content_policy","redacted_templates"),
                "chunk_model":self.model_profile.generation_model if self.preserve_content else None,
                "model_profile":self.model_profile.public(),
                "embedding": asdict(self.spec), "sources": stages, "capture": capture,
                "legacy": "Existing lexical aggregates remain separate; they are not vector memories."}

    async def context(self, project_id: str, question, *, user_context=None):
        self._refresh_configuration()
        if self.model_profile is None:
            from pipeline.models import ModelUnavailable
            raise ModelUnavailable("model_not_configured")
        from logchat.rag.retriever import retrieve, plan_cells
        from logchat.rag.remapper import remap
        from logchat.rag.answering import assemble_context
        from pipeline.models import LocalModels
        from pipeline.retrieval import resolve_windows
        windows, assumptions = resolve_windows(question,max_days=366)
        ids = [str(value) for value in question.environment_ids]
        with self.store.connection() as connection:
            self.store.project(connection, project_id)
            environments = connection.execute("SELECT id,name FROM environments WHERE project_id=?", (project_id,)).fetchall()
            valid = {row["id"]:row["name"] for row in environments}
            if any(identifier not in valid for identifier in ids):
                raise ValueError("Environment is not part of this project.")
            sources = connection.execute("SELECT id,environment_id,name FROM sources WHERE project_id=?", (project_id,)).fetchall()
        inventory = {identifier:tuple(row["id"] for row in sources if row["environment_id"]==identifier) for identifier in ids}
        current = next(item for item in windows if item["label"]=="current")
        previous = next((item for item in windows if item["label"]=="previous"), None)
        cells = plan_cells("local-os-user",project_id,ids,TimeWindow(current["start"],current["end"]),
                           source_ids=inventory, service=question.service,
                           comparison_window=TimeWindow(previous["start"],previous["end"]) if previous else None)
        if self.provider is None:
            await self.connect_provider()
        prior_topics = []
        if isinstance(user_context,dict):
            from pipeline.redaction import redact_text
            prior_topics = [(value if self.preserve_content else redact_text(value))[:500] for value in user_context.get("recalled",[])[:3] if isinstance(value,str)]
        retrieval_question = question.question
        if prior_topics:
            retrieval_question += "\nPrior user topics for resolving this follow-up only; not log evidence: " + " | ".join(prior_topics)
        retrieval = await retrieve(retrieval_question,cells,self.backend,self.provider,preserve_content=self.preserve_content)
        from dataclasses import replace
        from logchat.rag.contracts import StageProvenance
        retrieval = replace(retrieval,
            gaps=(*retrieval.gaps,"intake_observations_do_not_prove_continuous_source_capture"),
            provenance=(*retrieval.provenance,StageProvenance("intake_coverage","partial","native-adapter-v1",
                detail="saved_input_windows_not_continuous_source_coverage")))
        model = self.model_profile.generation("relevance")
        from logchat.rag.remapper import RemapConfig
        remap_config = RemapConfig(input_character_budget=24000,input_byte_budget=48000) if self.preserve_content else None
        checked = await remap(retrieval,model,config=remap_config,preserve_content=self.preserve_content)
        result = assemble_context(checked,preserve_content=self.preserve_content)
        capture = self.raw_capture.status(project_id)
        result["pending_capture"] = capture
        if capture["pending_events"]:
            result["gaps"].append("pending_original_logs_not_yet_searchable")
        if capture["expired_events"]:
            result["gaps"].append("temporary_original_logs_expired_before_compaction")
        result["output_kind"] = "retrieved_context"
        result["context_text"] = result["answer"]
        result["solution_generated"] = False
        result["content_policy"] = self.config.get("content_policy","redacted_templates")
        names = {row["id"]:row["name"] for row in sources}
        for cell in result["plan"]["cells"]:
            cell["environment"] = valid[cell["environment_id"]]
        for row in result["evidence"]:
            row["environment"] = valid[row["environment_id"]]
            row["source"] = names.get(row["source_id"],row["source_id"])
        for identifier,name in valid.items():
            result["answer"] = result["answer"].replace(identifier+" / ",name+" / ")
        result["context_text"] = result["answer"]
        result["assumptions"] = assumptions
        result["user_context"] = user_context
        result["user_context_is_log_evidence"] = False
        result["query_context"] = {"original_question":question.question,"prior_user_topics":prior_topics,
                                   "prior_topics_are_log_evidence":False}
        return result
