"""Explicit local-file adapter. Core owns the explicit temporary-retention and semantic policies."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
from uuid import uuid4

from connectors.local import parse_stdout_line
from logchat.rag.builder import event_key

MAX_LINE_BYTES = 12000


class FileCapture:
    def __init__(self, store):
        self.store = store
        self.task = None
        with store.connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS local_file_capture(
                source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL,
                device INTEGER NOT NULL, inode INTEGER NOT NULL, generation TEXT NOT NULL,
                offset INTEGER NOT NULL, anchor TEXT NOT NULL, skipping INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL, gaps INTEGER NOT NULL DEFAULT 0,
                accepted INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(local_file_capture)")}
            for name in ("error_category", "error_phase"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE local_file_capture ADD COLUMN {name} TEXT")

    @staticmethod
    def open_file(path):
        path = Path(path).expanduser().absolute()
        if path.resolve() != path:
            raise ValueError("Select a regular local file without symlink components.")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os,"O_NONBLOCK",0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Select a regular local log file.")
            return os.fdopen(fd, "rb")
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def anchor(handle, offset):
        handle.seek(max(0, offset - 64))
        return hashlib.sha256(handle.read(min(offset,64))).hexdigest()

    def configure(self, project_id, source_id, path, *, from_start=False):
        if self.store.rag_runtime is None:
            raise ValueError("Configure semantic memory before connecting a file.")
        self.store.rag_runtime.identity(project_id,source_id)
        path = Path(path).expanduser().absolute()
        with self.open_file(path) as handle:
            info = os.fstat(handle.fileno())
            offset = 0 if from_start else info.st_size
            anchor = self.anchor(handle,offset)
            handle.seek(max(0,offset-1))
            skipping = int(bool(offset) and handle.read(1)!=b'\n')
        with self.store.connection(write=True) as conn:
            previous = conn.execute("SELECT * FROM local_file_capture WHERE source_id=?",(source_id,)).fetchone()
            if previous:
                if previous['path'] != str(path) or previous['project_id'] != project_id:
                    raise ValueError("Use a separate source for a different file.")
                conn.execute("UPDATE local_file_capture SET enabled=1 WHERE source_id=?",(source_id,))
            else:
                if conn.execute("SELECT count(*) FROM local_file_capture").fetchone()[0]>=64:
                    raise ValueError("This instance supports at most 64 configured file sources.")
                conn.execute("""INSERT INTO local_file_capture
                    (source_id,project_id,path,device,inode,generation,offset,anchor,state,skipping)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",(source_id,project_id,str(path),info.st_dev,info.st_ino,str(uuid4()),offset,anchor,'watching',skipping))
        return self.status(project_id)

    def status(self, project_id):
        with self.store.connection() as conn:
            rows=conn.execute("SELECT source_id,path,offset,state,gaps,accepted,enabled,error_category,error_phase FROM local_file_capture WHERE project_id=?",(project_id,)).fetchall()
        return {'sources':[dict(row) for row in rows], 'capture':'explicit_local_file',
                'limitations':['Port metadata does not expose logs.',
                    'Rotation or truncation can lose unread data; gaps are counted.',
                    'Plain-text timestamps are capture times, not original event times.']}

    def disable(self, project_id, source_id):
        with self.store.connection(write=True) as conn:
            conn.execute("UPDATE local_file_capture SET enabled=0,state='stopped' WHERE project_id=? AND source_id=?",(project_id,source_id))

    def poll(self):
        with self.store.connection() as conn:
            rows=conn.execute("SELECT * FROM local_file_capture WHERE enabled=1 ORDER BY source_id LIMIT 64").fetchall()
        for row in rows:
            try:
                self._read(row)
            except Exception as error:
                # Never publish raw lines, file/model exceptions or credential contents.
                from .raw_capture import processing_failure
                category, phase = processing_failure(error)
                try:
                    with self.store.connection(write=True) as conn:
                        conn.execute("UPDATE local_file_capture SET state='unavailable',error_category=?,error_phase=? WHERE source_id=?",(category,phase,row['source_id']))
                except Exception:
                    # A transient database failure must not terminate other sources.
                    pass

    def _read(self,row):
        runtime=self.store.rag_runtime
        identity=runtime.identity(row['project_id'],row['source_id'])
        jobs=runtime.scheduler.status(identity)['jobs'] if runtime.scheduler is not None else {}
        if sum(count for state,count in jobs.items() if state!='completed')>=64:
            with self.store.connection(write=True) as conn:
                conn.execute("UPDATE local_file_capture SET state='backpressure' WHERE source_id=?",(row['source_id'],))
            return
        with self.open_file(row['path']) as handle:
            info=os.fstat(handle.fileno());offset=row['offset'];generation=row['generation']
            skipping=row['skipping'];gaps=row['gaps'];events=[]
            if (info.st_dev,info.st_ino)!=(row['device'],row['inode']) or info.st_size<offset or self.anchor(handle,offset)!=row['anchor']:
                offset=0;generation=str(uuid4());skipping=0;gaps+=1
                # Persist the new generation before preparing any records. A crash
                # after enqueue must replay with the same IDs, including after rotation.
                with self.store.connection(write=True) as conn:
                    conn.execute("""UPDATE local_file_capture SET device=?,inode=?,generation=?,offset=0,
                        anchor=?,skipping=0,gaps=? WHERE source_id=?""",
                        (info.st_dev,info.st_ino,generation,self.anchor(handle,0),gaps,row['source_id']))
            handle.seek(offset)
            for _ in range(100):
                beginning=handle.tell();raw=handle.readline(MAX_LINE_BYTES+1)
                if not raw:break
                if skipping:
                    skipping=int(not raw.endswith(b'\n'));offset=handle.tell();continue
                if len(raw)>MAX_LINE_BYTES:
                    skipping=int(not raw.endswith(b'\n'));gaps+=1;offset=handle.tell();continue
                if not raw.endswith(b'\n'):
                    # A writer may still be finishing this line. Never index partial text.
                    handle.seek(beginning);break
                offset=handle.tell()
                if not raw.strip():continue
                identifier=hashlib.sha256(f"{generation}:{beginning}:".encode()+raw).hexdigest()
                # A queue commit may have succeeded before the file cursor was saved.
                # This ID binds generation, position and bytes, so skip only exact source replay.
                if runtime.scheduler is not None and not runtime.scheduler.unseen_event_keys(identity,(event_key(identity,identifier),)):
                    continue
                try:
                    event=replace(parse_stdout_line(raw.decode('utf-8',errors='replace'),source=row['source_id'],
                        preserve_fields=runtime.preserve_content),event_id=identifier)
                except (ValueError,OverflowError,TypeError):
                    gaps+=1;continue
                events.append(event)
            if events:
                runtime.ingest(row['project_id'],row['source_id'],events)
            anchor=self.anchor(handle,offset)
        # Prepared jobs are already durable. Crash before this cursor commits replays
        # exactly identified file records against the scheduler's source-scoped ledger.
        with self.store.connection(write=True) as conn:
            conn.execute("""UPDATE local_file_capture SET device=?,inode=?,generation=?,offset=?,anchor=?,
                skipping=?,state='watching',error_category=NULL,error_phase=NULL,gaps=?,accepted=accepted+? WHERE source_id=?""",
                (info.st_dev,info.st_ino,generation,offset,anchor,skipping,gaps,len(events),row['source_id']))

    async def start(self):
        self.task=asyncio.create_task(self._work())

    async def _work(self):
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Retry database/source discovery failures on the next bounded poll.
                pass
            await asyncio.sleep(1)

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
