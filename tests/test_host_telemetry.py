"""Synthetic host telemetry tests; these never subscribe to live OS logs."""
from __future__ import annotations

import json
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

from logchat.local.store import LocalStore
from logchat.local.telemetry import HostCapture


def capture(tmp_path, interval=5):
    value = HostCapture(LocalStore(tmp_path / "state"), interval=interval)
    value._username = "current-user"
    return value


def test_schema_status_and_events_exist_before_start(tmp_path):
    host = capture(tmp_path)
    status = host.status()
    assert status["running"] is False
    assert status["capabilities"]["system"]["status"] == "stopped"
    assert status["capabilities"]["gpu"]["status"] == "unavailable"
    assert status["capabilities"]["os_logs"]["mode"] == "stream"
    assert host.events() == []


def test_system_and_network_snapshots_are_numeric_and_delta_bounded(tmp_path):
    host = capture(tmp_path)
    memory = SimpleNamespace(percent=62.5, used=1_000, available=500)
    first = {"en0": SimpleNamespace(bytes_sent=100, bytes_recv=200)}
    second = {"en0": SimpleNamespace(bytes_sent=140, bytes_recv=275)}
    stats = {"en0": SimpleNamespace(isup=True)}
    with patch("logchat.local.telemetry.psutil.cpu_percent", return_value=12.25), \
         patch("logchat.local.telemetry.psutil.virtual_memory", return_value=memory), \
         patch("logchat.local.telemetry.psutil.net_io_counters", side_effect=[first, second]), \
         patch("logchat.local.telemetry.psutil.net_if_stats", return_value=stats):
        host._collect_system()
        host._collect_network()
        host._collect_network()
    system = host.events("system", 1)[0]
    network = host.events("network", 1)[0]
    assert system["metrics"]["cpu_percent"] == 12.25
    assert network["metrics"]["bytes_sent_delta"] == 40
    assert network["metrics"]["bytes_recv_delta"] == 75
    assert "en0" not in json.dumps(host.events())
    assert host.status()["capabilities"]["network"]["status"] == "sampled"


class FakeProcess:
    def __init__(self, pid, name, username="current-user", created=1.0, connections=(), denied=False):
        self.info = {"pid": pid, "name": name, "username": username, "create_time": created}
        self._connections = connections
        self._denied = denied

    def net_connections(self, kind):
        assert kind == "inet"
        if self._denied:
            raise psutil.AccessDenied(pid=self.info["pid"])
        return list(self._connections)


def listener(port):
    return SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=port))


def test_process_scan_current_user_only_without_argv_or_endpoints(tmp_path):
    host = capture(tmp_path)
    owned = FakeProcess(10, "worker token=secret", connections=[listener(43210)])
    foreign = FakeProcess(11, "foreign", username="other-user", connections=[listener(54321)])
    with patch("logchat.local.telemetry.psutil.process_iter", side_effect=[[], [owned, foreign]]) as scan:
        host._collect_processes()
        host._collect_processes()
    assert scan.call_args.kwargs["attrs"] == ("pid", "name", "username", "create_time")
    rows = host.events("process", 10)
    assert any(row["pid"] == 10 and "[REDACTED_SECRET]" in row["process"] for row in rows)
    persisted = json.dumps(rows)
    assert "foreign" not in persisted
    assert "43210" not in persisted and "54321" not in persisted


def test_permission_limited_process_scan_does_not_abort_snapshot(tmp_path):
    host = capture(tmp_path)
    denied = FakeProcess(10, "private", denied=True)
    allowed = FakeProcess(20, "public")
    with patch("logchat.local.telemetry.psutil.process_iter", return_value=[denied, allowed]):
        host._collect_processes()
    status = host.status()["capabilities"]["process"]
    assert status["status"] == "partial" and status["reason"] == "permission_limited"
    assert any(row["metrics"].get("skipped") == 1 for row in host.events("process", 10))


def test_permission_gap_does_not_claim_process_or_listener_stopped(tmp_path):
    host = capture(tmp_path)
    visible = FakeProcess(10, "worker", connections=[listener(43210)])
    inaccessible = FakeProcess(10, "worker")
    inaccessible.info["username"] = None
    with patch("logchat.local.telemetry.psutil.process_iter", side_effect=[[visible], [inaccessible]]):
        host._collect_processes()
        host._collect_processes()
    aggregate = next(row for row in host.events("process", 10) if row["metrics"].get("skipped") == 1)
    assert aggregate["metrics"]["stopped"] == 0
    assert aggregate["metrics"]["listeners_closed"] == 0
    assert host.status()["capabilities"]["process"]["reason"] == "permission_limited"


def test_process_scan_cap_is_partial_and_does_not_seed_false_deltas(tmp_path):
    host = capture(tmp_path)
    one = FakeProcess(10, "one")
    two = FakeProcess(20, "two")
    with patch("logchat.local.telemetry.MAX_PROCESSES", 1), \
         patch("logchat.local.telemetry.psutil.process_iter", side_effect=[[one, two], [two]]):
        host._collect_processes()
        first = next(row for row in host.events("process", 10)
                     if row["metrics"].get("scan_limited") == 1)
        host._collect_processes()
    assert first["metrics"]["started"] == 0 and first["metrics"]["stopped"] == 0
    assert host.status()["capabilities"]["process"]["status"] == "sampled"
    aggregates = [row for row in host.events("process", 10) if "process_count" in row["metrics"]]
    assert aggregates[0]["metrics"]["started"] == 0
    assert aggregates[0]["metrics"]["stopped"] == 0


def test_windows_domain_is_part_of_user_identity():
    assert HostCapture._same_user("DOMAIN\\sam", "DOMAIN\\sam")
    assert HostCapture._same_user("DOMAIN/sam", "DOMAIN\\sam")
    assert not HostCapture._same_user("OTHER\\sam", "DOMAIN\\sam")


def test_os_log_raw_canary_never_crosses_persistence_boundary(tmp_path):
    host = capture(tmp_path)
    canary = "RAW-CANARY-user@example.com-token=topsecret"
    record = {"eventMessage": f"database timeout error {canary}",
              "processImagePath": "/private/bin/server", "processID": 99}
    host._log_queue.put_nowait(json.dumps(record).encode())
    host._drain_os_errors()
    row = host.events("system", 1)[0]
    assert row["process"] == "server" and row["pid"] == 99
    assert "timeout" in row["summary"] and canary not in json.dumps(row)
    with host.store.connection() as connection:
        raw_database_text = " ".join(str(value) for dbrow in connection.execute(
            "SELECT * FROM host_events"
        ).fetchall() for value in tuple(dbrow))
    assert canary not in raw_database_text


def test_os_log_metadata_without_message_is_not_an_error_and_header_is_ignored(tmp_path):
    host = capture(tmp_path)
    host._log_queue.put_nowait(b"Filtering the log data using predicate\n")
    host._log_queue.put_nowait(json.dumps({"timestamp": "synthetic"}).encode())
    host._drain_os_errors()
    assert host.events("system") == []
    assert host.status()["capabilities"]["os_logs"]["parse_failures"] == 1


def test_host_uncategorized_summary_does_not_claim_application_event(tmp_path):
    host = capture(tmp_path)
    host._persist("system", "info", "synthetic vocabulary miss", {})
    summary = host.events("system", 1)[0]["summary"]
    assert "uncategorized event" in summary
    assert "application event" not in summary


def test_stream_queue_line_and_drain_bounds_report_loss(tmp_path):
    host = capture(tmp_path)
    valid = json.dumps({"MESSAGE": "error", "_PID": "1"}).encode() + b"\n"
    child = SimpleNamespace(stdout=io.BytesIO(b"x" * (16 * 1024 + 1) + b"\n" + valid * 201))
    host._read_stream(child)
    assert host._log_queue.qsize() == 200
    assert host._oversize_lines == 1 and host._dropped_lines == 1
    persisted = []
    host._persist = lambda *args, **kwargs: persisted.append((args, kwargs))
    host._drain_os_errors()
    assert len(persisted) == 100 and host._log_queue.qsize() == 100
    assert host.status()["dropped_events"] == 2


def test_retention_and_maximum_event_bound(tmp_path):
    host = capture(tmp_path)
    base = datetime(2026, 10, 2, tzinfo=timezone.utc)
    with patch("logchat.local.telemetry.MAX_EVENTS", 3):
        host._persist("system", "info", "cpu", {"cpu_percent": 1},
                      timestamp=base - timedelta(days=8))
        for offset in range(5):
            host._persist("system", "info", "cpu", {"cpu_percent": offset},
                          timestamp=base + timedelta(seconds=offset))
    rows = host.events(limit=100)
    assert len(rows) == 3
    assert {row["metrics"]["cpu_percent"] for row in rows} == {2.0, 3.0, 4.0}
    assert all(row["timestamp"] >= base.isoformat() for row in rows)


def test_gpu_csv_parsing_and_unsupported_status(tmp_path):
    parsed = HostCapture._parse_gpu(b"0, 75, 1024, 8192, 66\n1, N/A, 0, 8192, 40\n")
    assert parsed == [{
        "gpu_index": 0,
        "gpu_utilization_percent": 75.0,
        "memory_used_mib": 1024,
        "memory_total_mib": 8192,
        "temperature_c": 66.0,
    }]
    host = capture(tmp_path)
    with patch("logchat.local.telemetry.shutil.which", return_value=None):
        host._collect_gpu()
    assert host.status()["capabilities"]["gpu"] == {
        "status": "unavailable", "mode": "snapshot", "last_sample_at": None,
        "reason": "nvidia_smi_not_found",
    }


def test_macos_stream_uses_current_user_error_predicate_and_no_stderr(tmp_path):
    host = capture(tmp_path)
    child = Mock()
    child.stdout = Mock()
    with patch("logchat.local.telemetry.platform.system", return_value="Darwin"), \
         patch("logchat.local.telemetry.os.getuid", return_value=501), \
         patch("logchat.local.telemetry.subprocess.Popen", return_value=child) as spawn, \
         patch("logchat.local.telemetry.threading.Thread") as thread:
        host._start_os_error_stream()
    args = spawn.call_args.args[0]
    assert args == ("/usr/bin/log", "stream", "--style", "ndjson", "--user", "501",
                    "--predicate", '(logType="fault" OR logType="error")')
    assert spawn.call_args.kwargs["stderr"] is not None
    thread.return_value.start.assert_called_once()


class FakeChild:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if not self.terminated and not self.killed else -15

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -15


class OutputChild(FakeChild):
    def __init__(self, output):
        super().__init__()
        self.stdout = io.BytesIO(output)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_nvidia_query_output_is_hard_capped(tmp_path):
    host = capture(tmp_path)
    child = OutputChild(b"x" * (16 * 1024 + 1))
    with patch("logchat.local.telemetry.subprocess.Popen", return_value=child):
        output, reason = host._run_bounded(("nvidia-smi",))
    assert output is None and reason == "output_too_large"
    assert child not in host._children


def test_stop_terminates_only_registered_children(tmp_path):
    host = capture(tmp_path)
    owned = FakeChild()
    unrelated = FakeChild()
    host._add_child(owned)
    host.stop()
    assert owned.terminated is True
    assert unrelated.terminated is False


def test_collector_failure_isolated_from_other_collectors(tmp_path):
    host = capture(tmp_path)
    calls = []

    def broken():
        raise RuntimeError("raw exception must not escape")

    def network():
        calls.append("network")

    def finish():
        calls.append("process")
        host._stop.set()

    host._collect_system = broken
    host._collect_network = network
    host._collect_processes = finish
    host._run()
    assert calls == ["network", "process"]
    assert host.status()["capabilities"]["system"]["status"] == "partial"
    assert host.status()["capabilities"]["system"]["reason"] == "collector_failed"


def test_events_filters_time_category_and_caps_limit(tmp_path):
    host = capture(tmp_path)
    base = datetime(2026, 10, 2, tzinfo=timezone.utc)
    host._persist("system", "info", "cpu", {"cpu_percent": 1}, timestamp=base)
    host._persist("network", "info", "network", {"bytes_recv": 2},
                  timestamp=base + timedelta(seconds=1))
    host._persist("system", "info", "memory", {"memory_percent": 3},
                  timestamp=base + timedelta(seconds=2))
    rows = host.events("system", 9999, start=(base + timedelta(seconds=1)).isoformat(),
                       end=(base + timedelta(seconds=3)).isoformat())
    assert len(rows) == 1 and rows[0]["metrics"] == {"memory_percent": 3.0}


def test_gpu_query_reads_fragmented_pipe_to_eof(tmp_path):
    host=capture(tmp_path)
    class Fragmented:
        def __init__(self):self.parts=iter([b"0, 75, ",b"1024, 8192, 66\n",b""])
        def read(self,size):return next(self.parts)
    child=OutputChild(b"");child.stdout=Fragmented()
    with patch("logchat.local.telemetry.subprocess.Popen",return_value=child):
        output,reason=host._run_bounded(("nvidia-smi",))
    assert reason is None
    assert HostCapture._parse_gpu(output)[0]["memory_used_mib"]==1024


def test_dead_os_collector_stays_unavailable_while_backlog_drains(tmp_path):
    host=capture(tmp_path)
    dead=OutputChild(b"");host._os_child=dead;host._add_child(dead)
    for _ in range(101):host._log_queue.put_nowait(b'{"MESSAGE":"timeout error"}')
    host._drain_os_errors();host._check_os_error_stream()
    assert host.status()["capabilities"]["os_logs"]["status"]=="unavailable"
    host._drain_os_errors();host._check_os_error_stream()
    assert host.status()["capabilities"]["os_logs"]["status"]=="unavailable"
    assert len(host.events(limit=200))==101
