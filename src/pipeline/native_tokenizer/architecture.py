"""Bounded executable metadata inspection; never loads or executes an asset.

CPU compatibility is not backend/ABI approval. Discovery must separately verify
an approved dependency closure. Only 64-bit little-endian native targets exist.
"""
import platform
import struct
import sys


class ArchitectureError(ValueError):
    pass


CPUS = {0x01000007: "x86_64", 0x0100000C: "arm64"}


def architectures(path, *, executable=False):
    with open(path, "rb") as file:
        size = file.seek(0, 2)

        def read(offset, length):
            if offset < 0 or offset + length > size:
                raise ArchitectureError("vocabulary_architecture_invalid")
            file.seek(offset)
            data = file.read(length)
            if len(data) != length:
                raise ArchitectureError("vocabulary_architecture_invalid")
            return data

        def macho(offset, extent):
            if extent < 32:
                raise ArchitectureError("vocabulary_architecture_invalid")
            header = read(offset, 32)
            magic, cpu, _, kind, commands, command_bytes, _, _ = struct.unpack("<8I", header)
            if (magic != 0xFEEDFACF or cpu not in CPUS
                    or kind != (2 if executable else 6)
                    or commands > 4096 or command_bytes > extent - 32):
                raise ArchitectureError("vocabulary_architecture_invalid")
            return CPUS[cpu]

        magic = read(0, 4)
        if magic == b"\xcf\xfa\xed\xfe":
            return "darwin", frozenset([macho(0, size)])
        if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            count = struct.unpack(">I", read(4, 4))[0]
            width = 32 if magic[-1] == 0xBF else 20
            if not 1 <= count <= 8:
                raise ArchitectureError("vocabulary_architecture_invalid")
            table = read(8, width * count)
            found, ranges = set(), []
            for index in range(count):
                row = table[index * width:(index + 1) * width]
                if width == 32:
                    cpu, _, offset, extent, alignment, reserved = struct.unpack(">IIQQII", row)
                    if reserved:
                        raise ArchitectureError("vocabulary_architecture_invalid")
                else:
                    cpu, _, offset, extent, alignment = struct.unpack(">IIIII", row)
                if (cpu not in CPUS or CPUS[cpu] in found or alignment > 31
                        or offset < 8 + width * count or offset + extent > size
                        or offset % (1 << alignment)
                        or any(offset < end and start < offset + extent for start, end in ranges)):
                    raise ArchitectureError("vocabulary_architecture_invalid")
                actual = macho(offset, extent)
                if actual != CPUS[cpu]:
                    raise ArchitectureError("vocabulary_architecture_invalid")
                ranges.append((offset, offset + extent))
                found.add(actual)
            return "darwin", frozenset(found)
        if magic == b"\x7fELF":
            header = read(0, 64)
            kind, machine, version = struct.unpack_from("<HHI", header, 16)
            cpu = {62: "x86_64", 183: "arm64"}.get(machine)
            if (header[4:7] != b"\x02\x01\x01" or cpu is None or version != 1
                    or kind not in ((2, 3) if executable else (3,))
                    or struct.unpack_from("<H", header, 52)[0] != 64):
                raise ArchitectureError("vocabulary_architecture_invalid")
            return "linux", frozenset([cpu])
        raise ArchitectureError("vocabulary_architecture_invalid")


def compiler_target(path):
    system, cpus = architectures(path)
    host = {"aarch64": "arm64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    if system != sys.platform or system not in {"darwin", "linux"}:
        raise ArchitectureError("vocabulary_architecture_unsupported")
    cpu = host if host in cpus else sorted(cpus)[0]
    if system == "linux" and cpu != host:
        raise ArchitectureError("vocabulary_architecture_unsupported")
    # Apple cc supports both targets; execution/translation availability is
    # checked by owned process startup, never by loading through Python.
    return cpu, (["-arch", cpu] if system == "darwin" else [])
