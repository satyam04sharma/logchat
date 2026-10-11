"""Offline process ownership tests. No actual model, tokenizer or provider calls."""
import asyncio
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pipeline.native_tokenizer import runtime
from pipeline.native_tokenizer.runtime import Asset, VocabularyRuntime, VocabularyRuntimeError


def identity(path):
    return Asset(str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def assets(tmp_path):
    if sys.platform not in {"darwin", "linux"} or not Path("/usr/bin/cc").exists():
        pytest.skip("offline POSIX compiler unavailable")
    tmp_path = tmp_path.resolve()
    library = tmp_path / "stub.so"
    sources = Path(runtime.__file__).parent
    subprocess.run(["/usr/bin/cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC",
                    "-dynamiclib" if sys.platform == "darwin" else "-shared",
                    "-I", str(sources / "vendor"),
                    str(Path(__file__).parent / "native/runtime_vocab_stub.c"),
                    "-o", str(library)], check=True, capture_output=True, timeout=30)
    model = tmp_path / "not-a-real-model.gguf"
    model.write_bytes(b"inert test identity; not a GGUF")
    dependency = tmp_path / "dependency"
    dependency.write_bytes(b"inert dependency identity")
    return identity(library), identity(model), identity(dependency)


def owner(assets, seconds=15):
    return VocabularyRuntime(*assets[:2], dependencies=assets[2:],
                             deadline=asyncio.get_running_loop().time() + seconds)


def assert_reaped(value):
    assert value._process.returncode is not None
    assert not Path(value._directory.name).exists()


@pytest.mark.asyncio
async def test_real_compiler_process_reuse_explicit_flags_binary_bytes_and_free(assets):
    value = owner(assets)
    async with value:
        assert Path(value._directory.name).stat().st_mode & 0o777 == 0o700
        for data in (b"", "search_document: é雪".encode(), b"search_query: a\0b", b"[INST] x[/INST]"):
            for add, parse in ((False, True), (True, True), (True, False), (False, False)):
                assert await value.count(data, add_special=add, parse_special=parse) == len(data) + 2 * add + parse
        counts = await asyncio.gather(*[
            value.count(b"x", add_special=True, parse_special=True) for _ in range(10)])
        assert counts == [4] * 10  # synthetic process/flag proof, not tokenizer parity
    assert_reaped(value)
    assert Path(assets[1].path + ".audit").read_text() == "open\nfree\n"
    await value.aclose()
    with pytest.raises(VocabularyRuntimeError, match="closed"):
        await value.count(b"x", add_special=True, parse_special=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2])
async def test_bad_library_model_dependency_digest_never_loads(assets, index):
    changed = list(assets)
    changed[index] = Asset(changed[index].path, "0" * 64)
    value = owner(changed)
    with pytest.raises(VocabularyRuntimeError, match="digest_mismatch"):
        async with value:
            pytest.fail("must not load")
    assert_reaped(value)
    assert not Path(assets[1].path + ".audit").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2])
async def test_revision_change_rejected_before_next_count(assets, index):
    value = owner(assets)
    async with value:
        path = Path(assets[index].path)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)  # even identical bytes with a different inode
        with pytest.raises(VocabularyRuntimeError, match="revision_changed"):
            await value.count(b"x", add_special=True, parse_special=True)
    assert_reaped(value)


@pytest.mark.asyncio
async def test_symlink_and_missing_asset_fail_before_loading(assets, tmp_path):
    link = tmp_path.resolve() / "symlink"
    link.symlink_to(assets[1].path)
    for model in (Asset(str(link), assets[1].sha256),
                  Asset(str(tmp_path.resolve() / "missing"), assets[1].sha256)):
        value = owner((assets[0], model))
        with pytest.raises(VocabularyRuntimeError):
            async with value:
                pytest.fail("must not load")
        assert_reaped(value)
    assert not Path(assets[1].path + ".audit").exists()


async def wait_for_hang(assets):
    audit = Path(assets[1].path + ".audit")
    async with asyncio.timeout(5):
        while "hang\n" not in audit.read_text():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_cancel_inflight_native_call_kills_and_reaps_process(assets):
    value = owner(assets)
    async with value:
        task = asyncio.create_task(value.count(b"hang", add_special=True, parse_special=True))
        await wait_for_hang(assets)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_reaped(value)


@pytest.mark.asyncio
async def test_absolute_deadline_kills_blocking_native_call(assets):
    value = owner(assets, seconds=2)
    with pytest.raises(TimeoutError):
        async with value:
            await value.count(b"hang", add_special=True, parse_special=True)
    assert_reaped(value)


@pytest.mark.asyncio
async def test_capacity_failure_closes_owner_and_no_native_diagnostic_escapes(assets):
    value = owner(assets)
    with pytest.raises(VocabularyRuntimeError, match="^vocabulary_native_count_failed$"):
        async with value:
            await value.count(b"private-content", add_special=True, parse_special=True, capacity=1)
    assert_reaped(value)


@pytest.mark.asyncio
async def test_limits_checked_without_starting_process(assets):
    value = owner(assets)
    for kwargs in ({"data": b"x" * (runtime.MAX_BYTES + 1)}, {"add_special": 1},
                   {"parse_special": None}, {"capacity": -1}, {"capacity": True},
                   {"capacity": runtime.MAX_TOKENS + 1}):
        parameters = dict(data=b"x", add_special=True, parse_special=True)
        parameters.update(kwargs)
        with pytest.raises(VocabularyRuntimeError, match="input_limit"):
            await value.count(**parameters)
    assert value._process is None


def test_source_corruption_rejected_before_compile(tmp_path, monkeypatch):
    source = tmp_path / "source"
    shutil.copytree(Path(runtime.__file__).parent, source)
    (source / "bridge.c").write_text("corrupt")
    monkeypatch.setattr(runtime, "__file__", str(source / "runtime.py"))
    build = tmp_path / "build"
    build.mkdir()
    with pytest.raises(VocabularyRuntimeError, match="source_identity_mismatch"):
        runtime._build(build)
    assert not (build / "worker").exists()


def test_runtime_environment_excludes_injection_and_credentials(monkeypatch):
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/untrusted")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted")
    monkeypatch.setenv("API_KEY", "private")
    monkeypatch.setenv("CFLAGS", "-include /untrusted")
    assert set(runtime._environment()) == {"PATH", "LANG", "LC_ALL"}


@pytest.mark.asyncio
async def test_idle_owner_expires_and_cannot_be_reused(assets):
    value = owner(assets, seconds=1)
    async with value:
        await asyncio.wait_for(value._process.wait(), timeout=3)
        await value._watchdog
        assert_reaped(value)
        with pytest.raises((VocabularyRuntimeError, TimeoutError)):
            await value.count(b"x", add_special=True, parse_special=True)


@pytest.mark.asyncio
async def test_cancel_during_subprocess_creation_still_reaps_late_child(assets, monkeypatch):
    create = asyncio.create_subprocess_exec
    created = asyncio.Event()
    release = asyncio.Event()

    async def delayed(*args, **kwargs):
        process = await create(*args, **kwargs)
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    value = owner(assets)
    task = asyncio.create_task(value.__aenter__())
    await asyncio.wait_for(created.wait(), timeout=5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_reaped(value)
    assert not Path(assets[1].path + ".audit").exists()


@pytest.mark.asyncio
async def test_worker_death_fails_closed_and_removes_private_build(assets):
    value = owner(assets)
    with pytest.raises((VocabularyRuntimeError, OSError)):
        async with value:
            value._process.kill()
            await value._process.wait()
            await value.count(b"x", add_special=True, parse_special=True)
    assert_reaped(value)


@pytest.mark.parametrize("name", ["sources.json", "vendor/llama.h", "bridge.h", "worker.c", "architecture.py"])
def test_all_compilation_inputs_are_identity_checked(tmp_path, monkeypatch, name):
    source = tmp_path / "source"
    shutil.copytree(Path(runtime.__file__).parent, source)
    (source / name).write_text("corrupt")
    monkeypatch.setattr(runtime, "__file__", str(source / "runtime.py"))
    build = tmp_path / "build"
    build.mkdir()
    with pytest.raises(VocabularyRuntimeError, match="source_identity_mismatch"):
        runtime._build(build)
    assert not (build / "worker").exists()


@pytest.mark.asyncio
async def test_close_does_not_wait_behind_a_stalled_count(assets):
    value = owner(assets)
    async with value:
        task = asyncio.create_task(value.count(b"hang", add_special=True, parse_special=True))
        await wait_for_hang(assets)
        await asyncio.wait_for(value.aclose(), timeout=3)
        with pytest.raises(VocabularyRuntimeError):
            await task
        assert_reaped(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [
    runtime.REQUEST.pack(b"oops", 1, 0, 0, 0),
    runtime.REQUEST.pack(b"LCQ1", 2, 0, 0, 0),
    runtime.REQUEST.pack(b"LCQ1", 1, runtime.MAX_BYTES + 1, 0, 0),
    runtime.REQUEST.pack(b"LCQ1", 1, 0, runtime.MAX_TOKENS + 1, 0),
    runtime.REQUEST.pack(b"LCQ1", 1, 0, 0, 4),
    runtime.REQUEST.pack(b"LCQ1", 0, 1, 0, 0),
])
async def test_native_worker_rejects_malformed_frames_and_frees(assets, header):
    value = owner(assets)
    async with value:
        value._process.stdin.write(header)
        await value._process.stdin.drain()
        assert await value._process.stdout.readexactly(12) == runtime.RESPONSE.pack(b"LCR1", 1, 0)
        await asyncio.wait_for(value._process.wait(), timeout=2)
        assert await value._process.stdout.read() == b""
    assert_reaped(value)
    assert Path(assets[1].path + ".audit").read_text() == "open\nfree\n"


@pytest.mark.asyncio
async def test_truncated_payload_eof_frees_without_echoing_input(assets):
    value = owner(assets)
    async with value:
        value._process.stdin.write(runtime.REQUEST.pack(b"LCQ1", 1, 100, 100, 3) + b"private")
        await value._process.stdin.drain()
        value._process.stdin.close()
        await asyncio.wait_for(value._process.wait(), timeout=2)
        assert await value._process.stdout.read() == b""
    assert_reaped(value)
    assert Path(assets[1].path + ".audit").read_text() == "open\nfree\n"


@pytest.mark.asyncio
async def test_maximum_frame_preserves_all_bytes(assets):
    async with owner(assets) as value:
        data = (b"\0\xc3\xa9x" * (runtime.MAX_BYTES // 4))
        assert await value.count(data, add_special=False, parse_special=False) == runtime.MAX_BYTES


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="Apple cross-target compiler/translation test")
async def test_real_intel_worker_uses_library_cpu_not_python_cpu(assets):
    # Controlled inert library only; never use the installed Ollama dylib.
    library = Path(assets[0].path)
    sources = Path(runtime.__file__).parent
    subprocess.run(["/usr/bin/cc", "-arch", "x86_64", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-dynamiclib", "-I", str(sources / "vendor"),
                    str(Path(__file__).parent / "native/runtime_vocab_stub.c"),
                    "-o", str(library)], check=True, capture_output=True, timeout=30)
    value = owner((identity(library), *assets[1:]))
    async with value:
        assert value.cpu == "x86_64"
        assert runtime.architectures(Path(value._directory.name) / "worker", executable=True) == ("darwin", {"x86_64"})
        assert await value.count("雪\0[INST]".encode(), add_special=True, parse_special=True) == 13
    assert_reaped(value)


@pytest.mark.asyncio
async def test_close_deadline_includes_stalled_stdin_drain(assets, monkeypatch):
    value = owner(assets)
    async with value:
        async def stalled():
            await asyncio.Future()
        monkeypatch.setattr(value._process.stdin, "drain", stalled)
        await asyncio.wait_for(value.aclose(), timeout=3)
    assert_reaped(value)


@pytest.mark.asyncio
async def test_deleted_asset_diagnostic_contains_no_private_path(assets):
    value = owner(assets)
    async with value:
        Path(assets[2].path).unlink()
        with pytest.raises(VocabularyRuntimeError, match="^vocabulary_asset_identity_invalid$"):
            await value.count(b"private", add_special=True, parse_special=True)
    assert_reaped(value)
