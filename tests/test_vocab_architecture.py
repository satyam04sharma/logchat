"""Executable metadata fixtures only: no real installed backend is loaded."""
import struct

import pytest

from pipeline.native_tokenizer import architecture


def macho(cpu=0x01000007, kind=6):
    return struct.pack("<8I", 0xFEEDFACF, cpu, 3, kind, 0, 0, 0, 0)


def elf(machine=62):
    data = bytearray(64)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", data, 16, 3, machine, 1)
    struct.pack_into("<H", data, 52, 64)
    return data


def test_thin_macho_intel_target_is_independent_of_arm_python(tmp_path, monkeypatch):
    library = tmp_path / "intel"
    library.write_bytes(macho())
    monkeypatch.setattr(architecture.sys, "platform", "darwin")
    monkeypatch.setattr(architecture.platform, "machine", lambda: "arm64")
    assert architecture.compiler_target(library) == ("x86_64", ["-arch", "x86_64"])
    assert architecture.architectures(library) == ("darwin", {"x86_64"})
    with pytest.raises(architecture.ArchitectureError):
        architecture.architectures(library, executable=True)


@pytest.mark.parametrize("wide", [False, True])
def test_fat_slices_checked_and_host_preferred(tmp_path, monkeypatch, wide):
    library = tmp_path / "fat"
    width = 32 if wide else 20
    start = 8 + width * 2
    rows = []
    for index, cpu in enumerate((0x01000007, 0x0100000C)):
        values = (cpu, 3, start + index * 32, 32, 0)
        rows.append(struct.pack(">IIQQII" if wide else ">IIIII", *(values + (0,) if wide else values)))
    data = struct.pack(">II", 0xCAFEBABF if wide else 0xCAFEBABE, 2) + b"".join(rows) + macho() + macho(0x0100000C)
    library.write_bytes(data)
    monkeypatch.setattr(architecture.sys, "platform", "darwin")
    monkeypatch.setattr(architecture.platform, "machine", lambda: "arm64")
    assert architecture.compiler_target(library) == ("arm64", ["-arch", "arm64"])
    # Inner CPU identity must agree with the outer fat table.
    library.write_bytes(data[:-32] + macho())
    with pytest.raises(architecture.ArchitectureError):
        architecture.architectures(library)


@pytest.mark.parametrize("data", [b"", b"unknown", macho(7), macho(kind=2),
    macho()[:24], struct.pack(">II", 0xCAFEBABE, 0xFFFFFFFF),
    struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">IIIII", 0x01000007, 3, 0, 32, 0),
    elf(3), bytes(elf()[:63])])
def test_malformed_or_unsupported_metadata_fails_closed(tmp_path, data):
    library = tmp_path / "invalid"
    library.write_bytes(data)
    with pytest.raises(architecture.ArchitectureError, match="architecture_invalid"):
        architecture.architectures(library)


def test_elf_same_cpu_only_and_wrong_platform_rejected(tmp_path, monkeypatch):
    library = tmp_path / "elf"
    library.write_bytes(elf())
    monkeypatch.setattr(architecture.sys, "platform", "linux")
    monkeypatch.setattr(architecture.platform, "machine", lambda: "x86_64")
    assert architecture.compiler_target(library) == ("x86_64", [])
    monkeypatch.setattr(architecture.platform, "machine", lambda: "aarch64")
    with pytest.raises(architecture.ArchitectureError, match="unsupported"):
        architecture.compiler_target(library)
    monkeypatch.setattr(architecture.sys, "platform", "darwin")
    with pytest.raises(architecture.ArchitectureError, match="unsupported"):
        architecture.compiler_target(library)
