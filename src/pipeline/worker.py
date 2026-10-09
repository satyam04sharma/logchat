"""Infrastructure worker; connector ingestion will be implemented in Phase 3."""
import signal
import threading
from pathlib import Path

stop = threading.Event()
for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, lambda *_: stop.set())

if __name__ == "__main__":
    print("Pipeline idle: no connectors configured.", flush=True)
    while not stop.is_set():
        Path("/tmp/worker-heartbeat").touch()
        stop.wait(10)
