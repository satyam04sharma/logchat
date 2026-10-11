"""Exact Mach-O loader closure admission and private staging.

This is a mechanism for a reviewed provider policy, not asset discovery. The
caller supplies independently approved digests and loader names. No fresh hash
of an unknown file establishes approval. Only thin Mach-O dylibs are admitted;
other platforms require their own reviewed loader policy.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct


class ClosureError(ValueError):
    """Fixed categories only; never expose paths or native metadata."""


# These two absolute install names resolve from Apple's protected dyld shared
# cache on the supported macOS host. No filesystem or fallback resolution is
# permitted for other system-looking names, frameworks, or arbitrary paths.
SYSTEM_LIBRARIES = frozenset({"/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib"})
MAX_COMMAND_BYTES = 1024 * 1024


@dataclass(frozen=True)
class LoaderMember:
    # Index in the owner's verified asset tuple; index 1 is always the GGUF.
    asset_index: int
    name: str

    def __post_init__(self):
        if (type(self.asset_index) is not int or not 0 <= self.asset_index < 34
                or self.asset_index == 1 or not isinstance(self.name, str)
                or not re.fullmatch(r"lib[A-Za-z0-9_.-]{1,100}\.dylib", self.name)):
            raise ClosureError("vocabulary_loader_policy_invalid")


def load_commands(path):
    """Bounded metadata only: return CPU, install ID, imports, and runpaths."""
    try:
        with open(path, "rb") as source:
            header = source.read(32)
            if len(header) != 32:
                raise ValueError()
            magic, cpu, _, kind, count, size, _, _ = struct.unpack("<8I", header)
            if (magic != 0xFEEDFACF or cpu not in (0x01000007, 0x0100000C)
                    or kind != 6 or not 1 <= count <= 4096
                    or not 8 * count <= size <= MAX_COMMAND_BYTES):
                raise ValueError()
            data = source.read(size)
            if len(data) != size:
                raise ValueError()
        offset, install_id, imports, runpaths = 0, None, [], []
        for _ in range(count):
            if offset + 8 > len(data):
                raise ValueError()
            command, length = struct.unpack_from("<II", data, offset)
            if length < 8 or length % 8 or offset + length > len(data):
                raise ValueError()
            # Reject alternate dynamic loader commands, weak/reexport/upward
            # dependencies, environment commands and obsolete dylib mechanisms.
            if command in {0x6, 0x7, 0xE, 0xF, 0x10, 0x18 | 0x80000000,
                           0x1F | 0x80000000, 0x20, 0x23 | 0x80000000, 0x27}:
                raise ValueError()
            if command in (0xC, 0xD, 0x1C | 0x80000000):
                minimum = 12 if command == 0x1C | 0x80000000 else 24
                if length < minimum:
                    raise ValueError()
                start = struct.unpack_from("<I", data, offset + 8)[0]
                if not minimum <= start < length:
                    raise ValueError()
                raw = data[offset + start:offset + length]
                end = raw.find(b"\0")
                if end < 0 or end > 255 or any(raw[end:]):
                    raise ValueError()
                name = raw[:end].decode("ascii")
                if not name or any(ord(c) < 32 for c in name):
                    raise ValueError()
                if command == 0xD:
                    if install_id is not None:
                        raise ValueError()
                    install_id = name
                elif command == 0xC:
                    imports.append(name)
                else:
                    runpaths.append(name)
            offset += length
        if offset != size or install_id is None or len(imports) != len(set(imports)):
            raise ValueError()
        return cpu, install_id, tuple(imports), tuple(runpaths)
    except (OSError, ValueError, struct.error):
        raise ClosureError("vocabulary_loader_metadata_invalid") from None


def validate_closure(assets, members):
    """Require exactly the reachable approved files and unambiguous resolution.

    All non-system imports must use @rpath, resolved by exactly @loader_path in
    each importing dylib. The private worker has no rpaths or inherited loader
    environment. Source aliases/symlinks never participate in resolution.
    """
    if (not 1 <= len(members) <= 32 or any(not isinstance(m, LoaderMember) for m in members)
            or members[0].asset_index != 0
            or len({m.name for m in members}) != len(members)
            or len({m.asset_index for m in members}) != len(members)
            or any(m.asset_index >= len(assets) for m in members)):
        raise ClosureError("vocabulary_loader_policy_invalid")
    names = {"@rpath/" + m.name: m for m in members}
    graph, cpus = {}, set()
    for member in members:
        cpu, install_id, imports, runpaths = load_commands(assets[member.asset_index].path)
        cpus.add(cpu)
        if install_id != "@rpath/" + member.name or runpaths != ("@loader_path",):
            raise ClosureError("vocabulary_loader_resolution_unverified")
        if any(name not in SYSTEM_LIBRARIES and name not in names for name in imports):
            raise ClosureError("vocabulary_loader_resolution_unverified")
        graph[install_id] = set(imports) - SYSTEM_LIBRARIES
    if len(cpus) != 1:
        raise ClosureError("vocabulary_loader_cpu_mismatch")
    pending, reached = ["@rpath/" + members[0].name], set()
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(graph[name])
    if reached != set(names):
        raise ClosureError("vocabulary_loader_policy_invalid")


def stage_closure(assets, verified, members, directory):
    """Copy once from pinned regular files; verify the exact bytes to be loaded.

    The owned bootstrap calls this after hashing approved assets, before any
    dlopen. Model bytes are never copied. Exclusive creation and a private
    directory prevent source loader aliases from influencing the closure.
    """
    validate_closure(assets, members)
    destination = Path(directory) / "loader"
    destination.mkdir(mode=0o700)
    for member in members:
        asset, pinned = assets[member.asset_index], verified[member.asset_index]
        pinned.check()
        digest = hashlib.sha256()
        with open(asset.path, "rb") as source, (destination / member.name).open("xb") as target:
            while block := source.read(1024 * 1024):
                digest.update(block)
                target.write(block)
        pinned.check()
        if digest.hexdigest() != asset.sha256:
            raise ClosureError("vocabulary_loader_copy_mismatch")
    # Reinspect the actual copied bytes, not just the supplied file presence.
    staged = list(assets)
    for member in members:
        from types import SimpleNamespace
        staged[member.asset_index] = SimpleNamespace(path=str(destination / member.name))
    validate_closure(staged, members)
    return destination / members[0].name
