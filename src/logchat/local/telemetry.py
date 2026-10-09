"""Privacy-bounded native host telemetry for the local runtime.

Collectors retain numeric snapshots and fixed-vocabulary sketches only.  OS log
records are held briefly in a bounded in-memory queue and reduced through
``LocalStore._summary`` before any SQLite write.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

import psutil

from pipeline.redaction import redact_text
from pipeline.types import LogEvent

from .store import LocalStore

MAX_EVENTS = 20_000
RETENTION_DAYS = 7
MAX_LINE_BYTES = 16 * 1024
MAX_QUEUE = 200
MAX_DRAIN = 100
MAX_PROCESSES = 1_000
NVIDIA_TIMEOUT = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    """Return a finite, JSON-safe number without preserving arbitrary text."""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return int(result) if integer else round(result, 3)


class HostCapture:
    """Collect safe host aggregates on a background thread.

    ``start`` and ``stop`` are idempotent.  Each capability fails independently,
    and status reasons come from a fixed vocabulary rather than exception or
    subprocess text.
    """

    def __init__(self, store: LocalStore, interval: float = 5):
        self.store = store
        self.interval = max(float(interval), 0.05)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._os_child: subprocess.Popen[bytes] | None = None
        self._children: set[subprocess.Popen[bytes]] = set()
        self._log_queue: queue.Queue[bytes] = queue.Queue(maxsize=MAX_QUEUE)
        self._dropped_lines = 0
        self._oversize_lines = 0
        self._parse_failures = 0
        self._previous_network: dict[str, tuple[int, int, bool]] = {}
        self._previous_processes: dict[int, tuple[str, float | None]] = {}
        self._previous_listeners: set[tuple[int, int]] = set()
        self._network_baselined = False
        self._process_baselined = False
        self._process_previous_complete = False
        try:
            self._username = psutil.Process(os.getpid()).username()
        except (psutil.Error, OSError):
            self._username = getpass.getuser()
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._capabilities: dict[str, dict[str, Any]] = {
            "system": self._capability("stopped", "snapshot", "not_started"),
            "network": self._capability("stopped", "snapshot", "not_started"),
            "process": self._capability("stopped", "snapshot", "not_started"),
            "gpu": self._capability("unavailable", "snapshot", "not_checked"),
            "os_logs": self._capability("stopped", "stream", "not_started"),
        }
        self._ensure_schema()

    @staticmethod
    def _capability(state: str, mode: str, reason: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"status": state, "mode": mode, "last_sample_at": None}
        if reason:
            result["reason"] = reason
        return result

    def _ensure_schema(self) -> None:
        with self.store.connection(write=True) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS host_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    level TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    process TEXT,
                    pid INTEGER
                );
                CREATE INDEX IF NOT EXISTS host_events_time ON host_events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS host_events_category_time
                    ON host_events(category,timestamp DESC);
            """)

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._started_at = _now().isoformat()
            self._stopped_at = None
            self._start_os_error_stream()
            self._thread = threading.Thread(target=self._run, name="logchat-host-capture", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._stop_owned_children()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(self.interval * 2, 1.0))
        reader = self._reader
        if reader and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        with self._lock:
            self._stopped_at = _now().isoformat()
            for value in self._capabilities.values():
                if value["status"] in {"sampled", "streaming", "partial"}:
                    value["status"] = "stopped"
                    value["reason"] = "stopped"
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive() and not self._stop.is_set())
            capabilities = {name: dict(value) for name, value in self._capabilities.items()}
            capabilities["os_logs"]["dropped_lines"] = self._dropped_lines
            capabilities["os_logs"]["oversize_lines"] = self._oversize_lines
            capabilities["os_logs"]["parse_failures"] = self._parse_failures
            capabilities["os_logs"]["queued_lines"] = self._log_queue.qsize()
            return {
                "running": running,
                "interval_seconds": self.interval,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "dropped_events": self._dropped_lines + self._oversize_lines + self._parse_failures,
                "capabilities": capabilities,
            }

    def events(self, category: str = "", limit: int = 100, *, start: str | None = None,
               end: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if category:
            clauses.append("category=?")
            values.append(category[:30])
        if start:
            clauses.append("timestamp>=?")
            values.append(start)
        if end:
            clauses.append("timestamp<=?")
            values.append(end)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.connection() as connection:
            rows = connection.execute(
                f"SELECT id,timestamp,category,level,summary,metrics,process,pid "
                f"FROM host_events{where} ORDER BY timestamp DESC,id DESC LIMIT ?",
                (*values, min(max(int(limit), 1), 500)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                metrics = json.loads(item["metrics"])
            except (TypeError, ValueError, json.JSONDecodeError):
                metrics = {}
            item["metrics"] = metrics if isinstance(metrics, dict) else {}
            result.append(item)
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            for name, collector in (
                ("system", self._collect_system),
                ("network", self._collect_network),
                ("process", self._collect_processes),
                ("gpu", self._collect_gpu),
                ("os_logs", self._drain_os_errors),
            ):
                if self._stop.is_set():
                    break
                try:
                    collector()
                except Exception:
                    self._set_capability(name, "partial", "collector_failed")
            self._check_os_error_stream()
            self._stop.wait(self.interval)

    def _set_capability(self, name: str, state: str, reason: str | None = None,
                        *, sampled: bool = False) -> None:
        with self._lock:
            value = self._capabilities[name]
            value["status"] = state
            if reason:
                value["reason"] = reason
            else:
                value.pop("reason", None)
            if sampled:
                value["last_sample_at"] = _now().isoformat()

    def _persist(self, category: str, level: str, message: str, metrics: dict[str, Any],
                 *, process: str | None = None, pid: int | None = None,
                 timestamp: datetime | None = None, count: int = 1) -> None:
        timestamp = timestamp or _now()
        safe_process = redact_text(Path(process).name)[:120] if process else None
        safe_level = level if level in {"debug", "info", "warning", "error", "critical"} else "error"
        event = LogEvent(
            event_id="host-event",
            ts=timestamp,
            source="host",
            service=f"host-{category}",
            level=safe_level,
            message=message,
            fingerprint=f"host-{category}",
        )
        summary = self.store._summary(event, max(int(count), 1)).replace(
            "uncategorized application event", "uncategorized event"
        )
        safe_metrics = self._safe_metrics(metrics)
        cutoff = (timestamp - timedelta(days=RETENTION_DAYS)).isoformat()
        with self.store.connection(write=True) as connection:
            connection.execute(
                "INSERT INTO host_events(timestamp,category,level,summary,metrics,process,pid) "
                "VALUES(?,?,?,?,?,?,?)",
                (timestamp.isoformat(), category, safe_level, summary,
                 json.dumps(safe_metrics, sort_keys=True, separators=(",", ":")), safe_process,
                 int(pid) if pid is not None else None),
            )
            connection.execute("DELETE FROM host_events WHERE timestamp < ?", (cutoff,))
            connection.execute("""
                DELETE FROM host_events WHERE id IN (
                    SELECT id FROM host_events ORDER BY timestamp DESC,id DESC LIMIT -1 OFFSET ?
                )
            """, (MAX_EVENTS,))

    @classmethod
    def _safe_metrics(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Allow a small structural/numeric vocabulary, never arbitrary strings."""
        allowed = {
            "cpu_percent", "memory_percent", "memory_used_bytes", "memory_available_bytes",
            "bytes_sent", "bytes_recv", "bytes_sent_delta", "bytes_recv_delta",
            "interface_count", "interfaces_up", "interfaces_down", "interfaces_changed",
            "process_count", "started", "stopped", "listener_count", "listeners_opened",
            "listeners_closed", "scanned", "skipped", "gpu_index", "gpu_utilization_percent",
            "memory_used_mib", "memory_total_mib", "temperature_c", "records", "parse_failures",
            "scan_limited",
        }
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key not in allowed:
                continue
            number = _number(item, integer=key not in {
                "cpu_percent", "memory_percent", "gpu_utilization_percent", "temperature_c"
            })
            if number is not None:
                result[key] = number
        return result

    def _collect_system(self) -> None:
        cpu = _number(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        metrics = {
            "cpu_percent": cpu,
            "memory_percent": _number(memory.percent),
            "memory_used_bytes": _number(memory.used, integer=True),
            "memory_available_bytes": _number(memory.available, integer=True),
        }
        self._persist("system", "info", "cpu memory", metrics)
        self._set_capability("system", "sampled", sampled=True)

    def _collect_network(self) -> None:
        counters = psutil.net_io_counters(pernic=True) or {}
        stats = psutil.net_if_stats() or {}
        current: dict[str, tuple[int, int, bool]] = {}
        for name, item in counters.items():
            current[name] = (max(int(item.bytes_sent), 0), max(int(item.bytes_recv), 0),
                             bool(getattr(stats.get(name), "isup", False)))
        sent = sum(item[0] for item in current.values())
        received = sum(item[1] for item in current.values())
        delta_sent = sum(max(item[0] - self._previous_network.get(name, item)[0], 0)
                         for name, item in current.items())
        delta_received = sum(max(item[1] - self._previous_network.get(name, item)[1], 0)
                             for name, item in current.items())
        changed = 0
        if self._network_baselined:
            changed = sum(1 for name, item in current.items()
                          if name in self._previous_network and item[2] != self._previous_network[name][2])
            changed += len(set(self._previous_network) ^ set(current))
        metrics = {
            "bytes_sent": sent, "bytes_recv": received,
            "bytes_sent_delta": delta_sent, "bytes_recv_delta": delta_received,
            "interface_count": len(current), "interfaces_up": sum(item[2] for item in current.values()),
            "interfaces_down": sum(not item[2] for item in current.values()),
            "interfaces_changed": changed,
        }
        self._previous_network = current
        self._network_baselined = True
        self._persist("network", "info", "network connection", metrics)
        self._set_capability("network", "sampled", sampled=True)

    def _collect_processes(self) -> None:
        current: dict[int, tuple[str, float | None]] = {}
        listeners: set[tuple[int, int]] = set()
        skipped = 0
        scanned = 0
        limited = False
        for index, process in enumerate(
                psutil.process_iter(attrs=("pid", "name", "username", "create_time"), ad_value=None)):
            if index >= MAX_PROCESSES:
                limited = True
                break
            scanned += 1
            try:
                info = process.info
                if info.get("username") is None:
                    skipped += 1
                    continue
                if not self._same_user(info.get("username"), self._username):
                    continue
                pid = int(info["pid"])
                name = redact_text(Path(str(info.get("name") or "process")).name)[:120]
                created = _number(info.get("create_time"))
                current[pid] = (name, float(created) if created is not None else None)
                for connection in process.net_connections(kind="inet"):
                    if connection.status == psutil.CONN_LISTEN:
                        port = int(getattr(connection.laddr, "port", 0) or 0)
                        listeners.add((pid, port))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError, ValueError):
                skipped += 1
        complete = not skipped and not limited
        if self._process_baselined and self._process_previous_complete:
            started = {pid: item for pid, item in current.items()
                       if pid not in self._previous_processes or self._previous_processes[pid][1] != item[1]}
            opened = listeners - self._previous_listeners
            if complete:
                stopped = {pid: item for pid, item in self._previous_processes.items()
                           if pid not in current or current.get(pid, (None, None))[1] != item[1]}
                closed = self._previous_listeners - listeners
            else:
                stopped, closed = {}, set()
        else:
            started, stopped, opened, closed = {}, {}, set(), set()
        metrics = {
            "process_count": len(current), "started": len(started), "stopped": len(stopped),
            "listener_count": len(listeners), "listeners_opened": len(opened),
            "listeners_closed": len(closed), "scanned": scanned, "skipped": skipped,
            "scan_limited": limited,
        }
        self._persist("process", "info", "startup shutdown server", metrics,
                      count=max(len(started) + len(stopped), 1))
        for pid, (name, _) in list(started.items())[:20]:
            self._persist("process", "info", "observed startup", {"started": 1}, process=name, pid=pid)
        for pid, (name, _) in list(stopped.items())[:20]:
            self._persist("process", "info", "shutdown", {"stopped": 1}, process=name, pid=pid)
        self._previous_processes = current
        self._previous_listeners = listeners
        self._process_baselined = True
        self._process_previous_complete = complete
        state = "sampled" if complete else "partial"
        reason = "permission_limited" if skipped else "process_limit_reached" if limited else None
        self._set_capability("process", state, reason, sampled=True)

    @staticmethod
    def _same_user(candidate: Any, current: str) -> bool:
        """Compare psutil usernames across Windows domain and POSIX forms."""
        if not isinstance(candidate, str):
            return False
        normalize = lambda value: value.replace("/", "\\").casefold()
        return normalize(candidate) == normalize(current)

    def _collect_gpu(self) -> None:
        executable = shutil.which("nvidia-smi")
        if not executable:
            self._set_capability("gpu", "unavailable", "nvidia_smi_not_found")
            return
        output, reason = self._run_bounded((
            executable,
            "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ))
        if output is None:
            self._set_capability("gpu", "unavailable", reason or "query_failed")
            return
        parsed = self._parse_gpu(output)
        if not parsed:
            self._set_capability("gpu", "partial", "no_readable_devices", sampled=True)
            return
        for metrics in parsed:
            self._persist("gpu", "info", "memory cpu", metrics)
        self._set_capability("gpu", "sampled", sampled=True)

    @staticmethod
    def _parse_gpu(output: bytes | str) -> list[dict[str, Any]]:
        text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
        result = []
        for line in text.splitlines()[:64]:
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 5:
                continue
            values = [_number(field, integer=index in {0, 2, 3}) for index, field in enumerate(fields)]
            if any(value is None for value in values):
                continue
            result.append(dict(zip(("gpu_index", "gpu_utilization_percent", "memory_used_mib",
                                    "memory_total_mib", "temperature_c"), values)))
        return result

    def _run_bounded(self, args: tuple[str, ...]) -> tuple[bytes | None, str | None]:
        try:
            child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except FileNotFoundError:
            return None, "executable_not_found"
        except PermissionError:
            return None, "permission_denied"
        except OSError:
            return None, "start_failed"
        self._add_child(child)
        holder: list[bytes] = []
        done = threading.Event()

        def read_output() -> None:
            try:
                data=bytearray()
                if child.stdout:
                    while len(data)<=MAX_LINE_BYTES:
                        chunk=child.stdout.read(min(4096,MAX_LINE_BYTES+1-len(data)))
                        if not chunk:break
                        data.extend(chunk)
                holder.append(bytes(data))
            except (OSError,ValueError):
                holder.append(b"")
            finally:
                done.set()

        reader = threading.Thread(target=read_output, name="logchat-nvidia-reader", daemon=True)
        reader.start()
        if not done.wait(NVIDIA_TIMEOUT):
            self._terminate_child(child)
            return None, "query_timeout"
        data = holder[0] if holder else b""
        if len(data) > MAX_LINE_BYTES:
            self._terminate_child(child)
            return None, "output_too_large"
        try:
            code = child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            self._terminate_child(child)
            return None, "query_timeout"
        finally:
            self._remove_child(child)
        return (data, None) if code == 0 else (None, "query_failed")

    def _start_os_error_stream(self) -> None:
        system = platform.system()
        if system == "Darwin":
            args = ("/usr/bin/log", "stream", "--style", "ndjson", "--user", str(os.getuid()),
                    "--predicate", '(logType="fault" OR logType="error")')
        elif system == "Linux":
            args = ("journalctl", "--user", "--follow", "--since", "now", "-p", "err", "-o", "json", "--no-pager")
        else:
            self._set_capability("os_logs", "unavailable", "platform_unsupported")
            return
        try:
            child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except FileNotFoundError:
            self._set_capability("os_logs", "unavailable", "executable_not_found")
            return
        except PermissionError:
            self._set_capability("os_logs", "unavailable", "permission_denied")
            return
        except OSError:
            self._set_capability("os_logs", "unavailable", "start_failed")
            return
        self._add_child(child)
        self._os_child = child
        self._reader = threading.Thread(target=self._read_stream, args=(child,),
                                        name="logchat-os-error-reader", daemon=True)
        self._reader.start()
        self._set_capability("os_logs", "streaming")

    def _read_stream(self, child: subprocess.Popen[bytes]) -> None:
        stream = child.stdout
        if stream is None:
            return
        while not self._stop.is_set():
            try:
                line = stream.readline(MAX_LINE_BYTES + 1)
            except (OSError, ValueError):
                break
            if not line:
                break
            if len(line) > MAX_LINE_BYTES:
                self._oversize_lines += 1
                self._discard_line_tail(stream, line)
                continue
            try:
                self._log_queue.put_nowait(line)
            except queue.Full:
                self._dropped_lines += 1

    @staticmethod
    def _discard_line_tail(stream: BinaryIO, first: bytes) -> None:
        if first.endswith(b"\n"):
            return
        while True:
            try:
                part = stream.readline(MAX_LINE_BYTES + 1)
            except (OSError, ValueError):
                return
            if not part or part.endswith(b"\n"):
                return

    def _drain_os_errors(self) -> None:
        records = 0
        failures = 0
        for _ in range(MAX_DRAIN):
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if line.lstrip().startswith(b"Filtering the log data"):
                continue
            parsed = self._parse_os_record(line)
            if parsed is None:
                failures += 1
                self._parse_failures += 1
                continue
            message, process, pid = parsed
            self._persist("system", "error", message, {"records": 1}, process=process, pid=pid)
            records += 1
        if records or failures:
            # Backlog is observed history, not proof that the collector is alive.
            with self._lock:
                self._capabilities["os_logs"]["last_sample_at"] = _now().isoformat()
            if self._os_child is not None and self._os_child.poll() is None:
                self._set_capability("os_logs", "streaming")

    @staticmethod
    def _parse_os_record(line: bytes) -> tuple[str, str | None, int | None] | None:
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        message = record.get("eventMessage") or record.get("MESSAGE")
        if not isinstance(message, str) or not message.strip():
            return None
        message = message[:MAX_LINE_BYTES]
        process_value = record.get("processImagePath") or record.get("process") or record.get("_COMM")
        process = Path(str(process_value)).name if process_value else None
        raw_pid = record.get("processID") if "processID" in record else record.get("_PID")
        try:
            pid = int(raw_pid) if raw_pid is not None else None
        except (TypeError, ValueError, OverflowError):
            pid = None
        return message, process, pid

    def _check_os_error_stream(self) -> None:
        stream_child = self._os_child
        if stream_child is not None and stream_child.poll() is None:
            return
        if stream_child is not None:
            self._remove_child(stream_child)
            self._os_child = None
            if not self._stop.is_set():
                self._set_capability("os_logs", "unavailable", "collector_exited")

    def _add_child(self, child: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._children.add(child)

    def _remove_child(self, child: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._children.discard(child)

    def _terminate_child(self, child: subprocess.Popen[bytes]) -> None:
        if child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    child.kill()
                    child.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._remove_child(child)

    def _stop_owned_children(self) -> None:
        with self._lock:
            children = list(self._children)
        for child in children:
            self._terminate_child(child)
