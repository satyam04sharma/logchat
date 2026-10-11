"""Internal process owner, not a provider capability or asset discovery policy.

Only a reviewed provider binding may supply Asset identities. Never construct
these from request paths or from freshly calculated hashes of unknown binaries.
Import has no native side effects. Use an async context with an absolute deadline.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import tempfile

MAX_BYTES = 1048576
MAX_TOKENS = 1048576
MAX_WIRE = 65536
RESPONSE = struct.Struct(">4sII")
REQUEST = struct.Struct(">4sIIII")

if __package__:
    from .architecture import ArchitectureError, architectures, compiler_target
else:
    # Isolated bootstrap: only load the sibling installed module, no sys.path.
    import runpy
    _architecture = runpy.run_path(str(Path(__file__).with_name("architecture.py")))
    ArchitectureError = _architecture["ArchitectureError"]
    architectures = _architecture["architectures"]
    compiler_target = _architecture["compiler_target"]
SOURCE_HASHES = {
    "worker.c": "c01f9efe8e4ba5abd66dbe5c5fbdff8a64495c7c45dde33b6834e70e2f5afeae",
    "architecture.py": "82ce01f8c6e0c471cd4e9f716548d81e0e580a466a1d269d0cbcdc7e04073126",
    "bridge.c": "9930d9f8f054ca2f87ce477d9dfec1bb235a5c6c7d5a0290d0ec41bb904c13e0",
    "bridge.h": "dbd1b1283e4bf9ecd44c8da587d1c0e0eabef356a21a91c8ecb6361f1104dc73",
    "sources.json": "5f83d081c8ec69572137e118f9b66cce4d54503ae9c70cc23612ec4ca47a68a5",
}


class VocabularyRuntimeError(RuntimeError):
    """Fixed diagnostics; never include native output, paths or input content."""


@dataclass(frozen=True)
class Asset:
    path: str
    sha256: str

    def __post_init__(self):
        if (not isinstance(self.path, str) or not self.path.startswith("/")
                or "\0" in self.path or len(self.path) > 4096
                or not isinstance(self.sha256, str)
                or not re.fullmatch("[a-f0-9]{64}", self.sha256)):
            raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")


def _revision(path):
    try:
        return _file_revision(path)
    except OSError:
        raise VocabularyRuntimeError("vocabulary_asset_identity_invalid") from None


def _file_revision(path):
    target = Path(path)
    # Reject symlinks in every component, including an otherwise safe leaf.
    for component in (target, *target.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise VocabularyRuntimeError("vocabulary_asset_symlink")
    info = target.stat()
    if not stat.S_ISREG(info.st_mode):
        raise VocabularyRuntimeError("vocabulary_asset_not_regular")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _VerifiedFile:
    def __init__(self, asset):
        self.path = asset.path
        self.revision = _revision(self.path)
        digest = hashlib.sha256()
        with open(self.path, "rb") as source:
            while block := source.read(MAX_BYTES):
                digest.update(block)
        self.check()
        if digest.hexdigest() != asset.sha256:
            raise VocabularyRuntimeError("vocabulary_asset_digest_mismatch")

    def check(self):
        if _revision(self.path) != self.revision:
            raise VocabularyRuntimeError("vocabulary_asset_revision_changed")


def _build(directory, target_flags=(), cpu=None):
    """Copy verified bytes before compilation; never use a shared binary cache."""
    source = Path(__file__).resolve().parent
    manifest_bytes = (source / "sources.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != SOURCE_HASHES["sources.json"]:
        raise VocabularyRuntimeError("vocabulary_source_identity_mismatch")
    expected = dict(SOURCE_HASHES)
    expected.update({"vendor/" + name: item["sha256"]
                     for name, item in json.loads(manifest_bytes)["files"].items()})
    for name, digest in expected.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise VocabularyRuntimeError("vocabulary_source_identity_mismatch")
        target = directory / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    output = directory / "worker"
    if sys.platform not in {"darwin", "linux"}:
        raise VocabularyRuntimeError("vocabulary_platform_unsupported")
    args = ["/usr/bin/cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
            *target_flags, "-I", str(directory / "vendor"),
            str(directory / "bridge.c"), str(directory / "worker.c"),
            "-o", str(output)]
    if sys.platform == "linux":
        args.append("-ldl")
    try:
        subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=_environment(), timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise VocabularyRuntimeError("vocabulary_compiler_unavailable_or_failed") from None
    if cpu is not None:
        system, cpus = architectures(output, executable=True)
        if system != sys.platform or cpus != {cpu}:
            raise VocabularyRuntimeError("vocabulary_architecture_mismatch")
    return output


def _environment():
    # No loader injection, compiler flags, credentials, user-site or PYTHONPATH.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}


# Private wire codes are deliberately finite; never forward native diagnostics.
BOOT_ERRORS = {
    100: "vocabulary_asset_identity_invalid",
    101: "vocabulary_asset_symlink",
    102: "vocabulary_asset_not_regular",
    103: "vocabulary_asset_digest_mismatch",
    104: "vocabulary_asset_revision_changed",
    105: "vocabulary_source_identity_mismatch",
    106: "vocabulary_compiler_unavailable_or_failed",
    107: "vocabulary_architecture_invalid",
    108: "vocabulary_architecture_unsupported",
    109: "vocabulary_architecture_mismatch",
    110: "vocabulary_worker_failed",
}


def _worker():
    """Hash/compile inside the owned group, then replace Python with native C.

    The interpreter never loads a native library. Even ARM Python can exec a
    reviewed x86_64 worker via the operating system's translation facility.
    """
    try:
        line = sys.stdin.buffer.readline(MAX_WIRE + 1)
        if len(line) > MAX_WIRE or not line.endswith(b"\n"):
            raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")
        config = json.loads(line)
        if not 2 <= len(config["assets"]) <= 34:
            raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")
        assets = [_VerifiedFile(Asset(**item)) for item in config["assets"]]
        cpu, flags = compiler_target(assets[0].path)
        binary = _build(Path(config["directory"]), flags, cpu)
        for asset in assets:
            asset.check()
        os.execve(str(binary), [str(binary), assets[0].path, assets[1].path], _environment())
    except (VocabularyRuntimeError, ArchitectureError) as error:
        code = next((code for code, name in BOOT_ERRORS.items() if name == str(error)), 110)
    except Exception:
        code = 110
    sys.stdout.buffer.write(RESPONSE.pack(b"LCR1", code, 0))
    sys.stdout.buffer.flush()


class VocabularyRuntime:
    """One serialized vocabulary per async context; absolute deadline <=300s.

    Dependencies must be the complete reviewed loader closure. This primitive
    checks supplied files, but cannot certify completeness, GGUF semantics,
    provider identity or model-store trust. The provider adapter owns those gates.
    """
    def __init__(self, library: Asset, model: Asset, *, dependencies=(), deadline: float):
        assets = (library, model, *dependencies)
        if not 2 <= len(assets) <= 34 or any(not isinstance(a, Asset) for a in assets):
            raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise VocabularyRuntimeError("vocabulary_deadline_invalid")
        self._assets = assets
        self._deadline = deadline
        self._process = None
        self._directory = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._spawn_task = None
        self._stop_task = None
        self._watchdog = None
        self._revisions = None
        self.cpu = None

    async def __aenter__(self):
        if self._closed or self._spawn_task is not None:
            raise VocabularyRuntimeError("vocabulary_runtime_closed_or_started")
        remaining = self._deadline - asyncio.get_running_loop().time()
        if not 0 < remaining <= 300:
            raise VocabularyRuntimeError("vocabulary_deadline_invalid")
        try:
            async with asyncio.timeout_at(self._deadline):
                self._directory = tempfile.TemporaryDirectory(prefix="logchat-vocabulary-")
                # Resolve system temp aliases (macOS /var -> /private/var).
                directory = str(Path(self._directory.name).resolve())
                self._spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(Path(__file__).resolve()),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=_environment(),
                    start_new_session=True, limit=4096))
                self._process = await asyncio.shield(self._spawn_task)
                self._revisions = tuple(_revision(a.path) for a in self._assets)
                config = json.dumps({"assets": [a.__dict__ for a in self._assets],
                                     "directory": directory}, separators=(",", ":")).encode() + b"\n"
                if len(config) > MAX_WIRE:
                    raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")
                self._process.stdin.write(config)
                await self._process.stdin.drain()
                cpu = await self._response(startup=True)
                if cpu not in (1, 2):
                    raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
                self.cpu = {1: "x86_64", 2: "arm64"}[cpu]
                self._check_assets()
            self._watchdog = asyncio.create_task(self._expire())
            return self
        except BaseException:
            await self._stop(force=True)
            raise

    async def _expire(self):
        await asyncio.sleep(max(0, self._deadline - asyncio.get_running_loop().time()))
        await self._stop(force=True)

    def _check_assets(self):
        if tuple(_revision(a.path) for a in self._assets) != self._revisions:
            raise VocabularyRuntimeError("vocabulary_asset_revision_changed")

    async def _response(self, *, startup=False):
        try:
            data = await self._process.stdout.readexactly(RESPONSE.size)
            magic, status, count = RESPONSE.unpack(data)
        except (asyncio.IncompleteReadError, ValueError):
            raise VocabularyRuntimeError("vocabulary_worker_protocol_failed") from None
        if magic != b"LCR1" or (status and count):
            raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
        if status:
            if startup and status in BOOT_ERRORS:
                raise VocabularyRuntimeError(BOOT_ERRORS[status])
            if 1 <= status <= 8:
                raise VocabularyRuntimeError("vocabulary_native_open_failed" if startup
                                             else "vocabulary_native_count_failed")
            raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
        return count

    async def count(self, data: bytes, *, add_special: bool, parse_special: bool,
                    capacity: int = MAX_TOKENS) -> int:
        if (type(data) is not bytes or len(data) > MAX_BYTES
                or type(add_special) is not bool or type(parse_special) is not bool
                or type(capacity) is not int or not 0 <= capacity <= MAX_TOKENS):
            raise VocabularyRuntimeError("vocabulary_input_limit")
        try:
            async with asyncio.timeout_at(self._deadline):
                async with self._lock:
                    if self._closed or self._process is None:
                        raise VocabularyRuntimeError("vocabulary_runtime_closed_or_not_started")
                    self._check_assets()
                    self._process.stdin.write(REQUEST.pack(
                        b"LCQ1", 1, len(data), capacity, int(add_special) | (int(parse_special) << 1)))
                    self._process.stdin.write(data)
                    await self._process.stdin.drain()
                    count = await self._response()
                    self._check_assets()
                    if count > capacity:
                        raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
                    return count
        except BaseException:
            await self._stop(force=True)
            raise

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        # Do not wait behind a blocked native count; close has its own bounded
        # grace period and prevents new requests before waiting for the worker.
        await self._stop(force=False)

    async def _stop(self, *, force):
        self._closed = True
        if self._watchdog is not None and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()

        async def reap():
            try:
                if self._process is None and self._spawn_task is not None:
                    try:
                        self._process = await self._spawn_task
                    except Exception:
                        return
                process = self._process
                if process is not None:
                    if not force and process.returncode is None:
                        try:
                            async with asyncio.timeout(1):
                                process.stdin.write(REQUEST.pack(b"LCQ1", 0, 0, 0, 0))
                                await process.stdin.drain()
                                await process.wait()
                        except (OSError, TimeoutError):
                            pass
                    # Kill the owned process group, including any compiler child.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            finally:
                if self._directory is not None:
                    self._directory.cleanup()

        if self._stop_task is None:
            self._stop_task = asyncio.create_task(reap())
        task = self._stop_task
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError


if __name__ == "__main__":
    _worker()
