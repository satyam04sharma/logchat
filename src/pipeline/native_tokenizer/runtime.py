"""Internal process owner, not a provider capability or asset discovery policy.

Only a reviewed provider binding may supply Asset identities. Never construct
these from request paths or from freshly calculated hashes of unknown binaries.
Import has no native side effects. Use an async context with an absolute deadline.
"""
import asyncio
import base64
import ctypes
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile

MAX_BYTES = 1048576
MAX_TOKENS = 1048576
MAX_WIRE = 1500000
SOURCE_HASHES = {
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


def _build(directory):
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
    output = directory / "bridge.so"
    if sys.platform not in {"darwin", "linux"}:
        raise VocabularyRuntimeError("vocabulary_platform_unsupported")
    args = ["/usr/bin/cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC",
            "-dynamiclib" if sys.platform == "darwin" else "-shared",
            "-I", str(directory / "vendor"), str(directory / "bridge.c"),
            "-o", str(output)]
    if sys.platform == "linux":
        args.append("-ldl")
    try:
        subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=_environment(), timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise VocabularyRuntimeError("vocabulary_compiler_unavailable_or_failed") from None
    return output


def _environment():
    # No loader injection, compiler flags, credentials, user-site or PYTHONPATH.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}


def _api(path):
    api = ctypes.CDLL(str(path))
    api.lc_vocab_abi.argtypes = []
    api.lc_vocab_abi.restype = ctypes.c_uint32
    if api.lc_vocab_abi() != 1:
        raise VocabularyRuntimeError("vocabulary_bridge_abi_mismatch")
    api.lc_vocab_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    api.lc_vocab_open.restype = ctypes.c_int
    api.lc_vocab_count.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32)]
    api.lc_vocab_count.restype = ctypes.c_int
    api.lc_vocab_close.argtypes = [ctypes.c_void_p]
    api.lc_vocab_close.restype = None
    return api


def _worker():
    # Keep a dedicated protocol descriptor. Native stdout/stderr are discarded.
    output = os.fdopen(os.dup(1), "w", buffering=1)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    handle = ctypes.c_void_p()
    api = None
    try:
        config = json.loads(sys.stdin.buffer.readline(65537))
        assets = [_VerifiedFile(Asset(**item)) for item in config["assets"]]
        if not 2 <= len(assets) <= 34:
            raise VocabularyRuntimeError("vocabulary_asset_identity_invalid")
        directory = Path(config["directory"])
        api = _api(_build(directory))
        for asset in assets:
            asset.check()
        status = api.lc_vocab_open(assets[0].path.encode(), assets[1].path.encode(), ctypes.byref(handle))
        if status:
            raise VocabularyRuntimeError("vocabulary_native_open_failed")
        for asset in assets:
            asset.check()
        output.write('{"ready":true}\n')
        while line := sys.stdin.buffer.readline(MAX_WIRE + 1):
            if len(line) > MAX_WIRE:
                raise VocabularyRuntimeError("vocabulary_input_limit")
            request = json.loads(line)
            if request == {"close": True}:
                break
            data = base64.b64decode(request["data"], validate=True)
            add, parse, capacity = request["add"], request["parse"], request["capacity"]
            if (len(data) > MAX_BYTES or type(add) is not bool or type(parse) is not bool
                    or type(capacity) is not int or not 0 <= capacity <= MAX_TOKENS):
                raise VocabularyRuntimeError("vocabulary_input_limit")
            for asset in assets:
                asset.check()
            count = ctypes.c_uint32()
            status = api.lc_vocab_count(handle, data, len(data), add, parse, capacity, ctypes.byref(count))
            for asset in assets:
                asset.check()
            if status:
                raise VocabularyRuntimeError("vocabulary_native_count_failed")
            output.write(json.dumps({"count": count.value}) + "\n")
    except VocabularyRuntimeError as error:
        output.write(json.dumps({"error": str(error)}) + "\n")
    except Exception:
        output.write('{"error":"vocabulary_worker_failed"}\n')
    finally:
        if api is not None:
            api.lc_vocab_close(handle)
        output.close()


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
                result = await self._exchange({"assets": [a.__dict__ for a in self._assets],
                                               "directory": directory})
                if result != {"ready": True}:
                    raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
            self._watchdog = asyncio.create_task(self._expire())
            return self
        except BaseException:
            await self._stop(force=True)
            raise

    async def _expire(self):
        await asyncio.sleep(max(0, self._deadline - asyncio.get_running_loop().time()))
        await self._stop(force=True)

    async def _exchange(self, value):
        self._process.stdin.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        await self._process.stdin.drain()
        line = await self._process.stdout.readline()
        try:
            result = json.loads(line)
        except (ValueError, TypeError):
            raise VocabularyRuntimeError("vocabulary_worker_protocol_failed") from None
        if isinstance(result, dict) and "error" in result:
            # Worker emits fixed categories only; constrain even those at boundary.
            error = result["error"]
            if not isinstance(error, str) or not re.fullmatch(r"vocabulary_[a-z_]{1,80}", error):
                error = "vocabulary_worker_failed"
            raise VocabularyRuntimeError(error)
        return result

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
                    result = await self._exchange({"data": base64.b64encode(data).decode("ascii"),
                                                   "add": add_special, "parse": parse_special,
                                                   "capacity": capacity})
                    if (not isinstance(result, dict) or set(result) != {"count"}
                            or type(result["count"]) is not int or not 0 <= result["count"] <= capacity):
                        raise VocabularyRuntimeError("vocabulary_worker_protocol_failed")
                    return result["count"]
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
                            process.stdin.write(b'{"close":true}\n')
                            await process.stdin.drain()
                            await asyncio.wait_for(process.wait(), timeout=1)
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
