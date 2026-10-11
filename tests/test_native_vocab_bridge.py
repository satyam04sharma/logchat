"""Compile only against inert stubs: no installed library/model is opened."""
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SOURCES = Path(__file__).resolve().parents[1] / "src/pipeline/native_tokenizer"


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None or sys.platform not in {"darwin", "linux"}:
        pytest.skip("offline POSIX C compiler fixture unavailable")
    directory = tmp_path_factory.mktemp("vocab-stub")
    flags = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC",
             "-dynamiclib" if sys.platform == "darwin" else "-shared",
             "-I", str(SOURCES / "vendor")]
    stub = Path(__file__).parent / "native/llama_vocab_stub.c"
    library = directory / "stub.so"
    missing = directory / "missing.so"
    bridge = directory / "bridge.so"
    for source, target, extra in [
        (stub, library, []), (stub, missing, ["-DSTUB_MISSING_TOKENIZE"]),
        (SOURCES / "bridge.c", bridge, [] if sys.platform == "darwin" else ["-ldl"]),
    ]:
        subprocess.run([*flags, str(source), *extra, "-o", str(target)], check=True,
                       capture_output=True, timeout=30)
    api = ctypes.CDLL(str(bridge))
    api.lc_vocab_abi.argtypes = []
    api.lc_vocab_abi.restype = ctypes.c_uint32
    api.lc_vocab_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    api.lc_vocab_open.restype = ctypes.c_int
    api.lc_vocab_close.argtypes = [ctypes.c_void_p]
    api.lc_vocab_close.restype = None
    api.lc_vocab_count.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32)]
    api.lc_vocab_count.restype = ctypes.c_int
    control = ctypes.CDLL(str(library))
    control.stub_reset.argtypes = [ctypes.c_int]
    control.stub_reset.restype = None
    for name in ("opens", "frees", "calls", "flags", "bytes"):
        method = getattr(control, "stub_" + name)
        method.argtypes = []
        method.restype = ctypes.c_int
    return api, control, library, missing


def open_stub(native, mode=0):
    api, control, library, _ = native
    control.stub_reset(mode)
    handle = ctypes.c_void_p()
    status = api.lc_vocab_open(str(library).encode(), b"/offline-fixture.gguf", ctypes.byref(handle))
    return status, handle


def test_pinned_sources_are_byte_exact():
    manifest = json.loads((SOURCES / "sources.json").read_text())
    assert manifest["revision"] == "161755f29e415e2c33efe906e91843c068efd664"
    assert manifest["ollama_revision"] == "b2da9e468af2479058ae18c6d908ed29de410684"
    assert set(manifest["files"]) == {p.name for p in (SOURCES / "vendor").iterdir()}
    for name, item in manifest["files"].items():
        assert hashlib.sha256((SOURCES / "vendor" / name).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("add,parse", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_typed_abi_reuses_vocab_preserves_explicit_flags_and_byte_length(native, add, parse):
    api, control, _, _ = native
    assert api.lc_vocab_abi() == 1
    status, handle = open_stub(native)
    assert status == 0 and handle.value
    try:
        for data in (b"", "search_document: é雪".encode(), b"search_query: a\0b", b"<s>[INST] x[/INST]"):
            count = ctypes.c_uint32(999)
            assert api.lc_vocab_count(handle, data, len(data), add, parse, 7, ctypes.byref(count)) == 0
            assert count.value == 7  # explicitly synthetic, never a real token claim
            assert control.stub_flags() == add * 2 + parse
            assert control.stub_bytes() == len(data)
        assert control.stub_opens() == 1
        assert control.stub_calls() == 8
    finally:
        api.lc_vocab_close(handle)
    assert control.stub_frees() == 1


@pytest.mark.parametrize("mode,status,frees", [(1, 5, 0), (2, 6, 1)])
def test_open_failure_releases_resources_and_clears_output(native, mode, status, frees):
    api, control, _, _ = native
    result, handle = open_stub(native, mode)
    assert result == status and handle.value is None
    assert control.stub_frees() == frees
    api.lc_vocab_close(handle)


@pytest.mark.parametrize("mode,status", [(3, 7), (4, 8), (5, 7), (6, 0), (7, 8), (8, 8), (9, 8)])
def test_overflow_and_inconsistent_tokenizer_results_fail_closed(native, mode, status):
    api, control, _, _ = native
    result, handle = open_stub(native, mode)
    assert result == 0
    try:
        count = ctypes.c_uint32(999)
        assert api.lc_vocab_count(handle, b"x", 1, 1, 1, 1048576, ctypes.byref(count)) == status
        assert count.value == 0
    finally:
        api.lc_vocab_close(handle)
    assert control.stub_frees() == 1


@pytest.mark.parametrize("length,add,parse,capacity,status,calls", [
    (1, 1, 1, 6, 7, 1), (1, 1, 1, 0, 7, 1),
    (1048577, 1, 1, 7, 7, 0), (2**32 - 1, 1, 1, 7, 7, 0),
    (1, 1, 1, 1048577, 7, 0), (1, 2, 1, 7, 1, 0), (1, 1, -1, 7, 1, 0),
])
def test_capacity_and_argument_limits_before_allocation(native, length, add, parse, capacity, status, calls):
    api, control, _, _ = native
    result, handle = open_stub(native)
    assert result == 0
    try:
        count = ctypes.c_uint32(999)
        assert api.lc_vocab_count(handle, b"x", length, add, parse, capacity, ctypes.byref(count)) == status
        assert count.value == 0 and control.stub_calls() == calls
    finally:
        api.lc_vocab_close(handle)


def test_loader_rejects_missing_symbols_and_relative_paths(native):
    api, control, library, missing = native
    control.stub_reset(0)
    for path, model, expected in [(str(missing), "/offline-fixture.gguf", 3),
                                  (str(library.parent / "absent.so"), "/offline-fixture.gguf", 2),
                                  ("relative.so", "/offline-fixture.gguf", 1),
                                  (str(library), "relative.gguf", 1)]:
        handle = ctypes.c_void_p(123)
        assert api.lc_vocab_open(path.encode(), model.encode(), ctypes.byref(handle)) == expected
        assert handle.value is None
    assert control.stub_opens() == 0
    count = ctypes.c_uint32(999)
    assert api.lc_vocab_count(None, b"x", 1, 1, 1, 7, ctypes.byref(count)) == 1
    assert count.value == 0
    api.lc_vocab_close(None)
