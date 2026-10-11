"""Offline loader policy tests; inert Mach-O metadata and C stubs only."""
import asyncio
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from pipeline.native_tokenizer.closure import (
    ClosureError, LoaderMember, load_commands, stage_closure, validate_closure,
)
from pipeline.native_tokenizer.runtime import Asset, VocabularyRuntime, VocabularyRuntimeError, _VerifiedFile
from tests.test_vocab_runtime import assert_reaped, identity


def command(code, name):
    start = 12 if code == 0x8000001C else 24
    raw = name.encode() + b'\0'
    length = (start + len(raw) + 7) // 8 * 8
    return struct.pack('<III', code, length, start) + bytes(start - 12) + raw + bytes(length - start - len(raw))


def macho(path, name, imports=(), *, cpu=0x01000007, runpaths=('@loader_path',), extra=b''):
    rows = [command(0xD, '@rpath/' + name)]
    rows += [command(0xC, item) for item in imports]
    rows += [command(0x8000001C, item) for item in runpaths]
    if extra:
        rows.append(extra)
    data = b''.join(rows)
    path.write_bytes(struct.pack('<8I', 0xFEEDFACF, cpu, 3, 6, len(rows), len(data), 0, 0) + data)
    return identity(path)


@pytest.fixture
def closure(tmp_path):
    tmp_path = tmp_path.resolve()
    main = macho(tmp_path / 'versioned-main', 'libmain.0.dylib',
                 ('@rpath/libdependency.0.dylib', '/usr/lib/libSystem.B.dylib'))
    model = tmp_path / 'inert-model'
    model.write_bytes(b'not a model')
    dependency = macho(tmp_path / 'versioned-dependency', 'libdependency.0.dylib',
                       ('/usr/lib/libc++.1.dylib',))
    assets = (main, identity(model), dependency)
    members = (LoaderMember(0, 'libmain.0.dylib'), LoaderMember(2, 'libdependency.0.dylib'))
    return assets, members


def test_exact_closure_stages_regular_files_without_source_aliases(closure, tmp_path):
    assets, members = closure
    directory = tmp_path / 'owned'
    directory.mkdir(mode=0o700)
    # The original loader alias is malicious. It is never consulted or copied.
    (tmp_path / 'libdependency.0.dylib').symlink_to('/untrusted')
    result = stage_closure(assets, [_VerifiedFile(a) for a in assets], members, directory)
    assert result.name == 'libmain.0.dylib'
    assert result.read_bytes() == Path(assets[0].path).read_bytes()
    loader = result.parent
    assert loader.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in loader.iterdir()) == ['libdependency.0.dylib', 'libmain.0.dylib']
    assert not (loader / 'libdependency.0.dylib').is_symlink()
    assert (loader / 'libdependency.0.dylib').read_bytes() == Path(assets[2].path).read_bytes()


@pytest.mark.parametrize('imports,runpaths', [
    (('@rpath/libmissing.dylib',), ('@loader_path',)),
    (('@loader_path/libdependency.0.dylib',), ('@loader_path',)),
    (('/tmp/libdependency.0.dylib',), ('@loader_path',)),
    (('/usr/lib/libUnreviewed.dylib',), ('@loader_path',)),
    (('@rpath/libdependency.0.dylib',), ('@loader_path', '/untrusted')),
    (('@rpath/libdependency.0.dylib',), ('@executable_path',)),
    (('@rpath/libdependency.0.dylib',), ()),
])
def test_missing_dependency_or_ambiguous_resolution_is_rejected(closure, imports, runpaths):
    assets, members = closure
    macho(Path(assets[0].path), 'libmain.0.dylib', imports, runpaths=runpaths)
    with pytest.raises(ClosureError, match='^vocabulary_loader_resolution_unverified$'):
        validate_closure(assets, members)


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate_name', 'duplicate_index', 'wrong_id', 'cpu'])
def test_exact_reachable_closure_and_cpu_required(closure, change):
    assets, members = closure
    if change == 'missing':
        members = members[:1]
    elif change == 'extra':
        macho(Path(assets[0].path), 'libmain.0.dylib')
    elif change == 'duplicate_name':
        members = (members[0], LoaderMember(2, members[0].name))
    elif change == 'duplicate_index':
        members = (members[0], LoaderMember(0, members[1].name))
    elif change == 'wrong_id':
        macho(Path(assets[2].path), 'libother.dylib')
    else:
        macho(Path(assets[2].path), members[1].name, cpu=0x0100000C)
    with pytest.raises(ClosureError):
        validate_closure(assets, members)


@pytest.mark.parametrize('code', [0x6, 0x7, 0xE, 0xF, 0x10, 0x80000018, 0x8000001F, 0x20, 0x80000023, 0x27])
def test_unsupported_loader_commands_rejected(closure, code):
    assets, _ = closure
    path = Path(assets[0].path)
    macho(path, 'libmain.0.dylib', extra=struct.pack('<II', code, 8))
    with pytest.raises(ClosureError, match='^vocabulary_loader_metadata_invalid$'):
        load_commands(path)


@pytest.mark.parametrize('mutation', ['truncated', 'huge', 'command_count', 'command_length', 'no_nul', 'duplicate_id'])
def test_bounded_malformed_metadata_rejected(closure, mutation):
    assets, _ = closure
    path = Path(assets[0].path)
    data = bytearray(path.read_bytes())
    if mutation == 'truncated':
        data = data[:-1]
    elif mutation == 'huge':
        struct.pack_into('<I', data, 20, 2**32 - 1)
    elif mutation == 'command_count':
        struct.pack_into('<I', data, 16, 4097)
    elif mutation == 'command_length':
        struct.pack_into('<I', data, 36, 2**32 - 1)
    elif mutation == 'no_nul':
        length = struct.unpack_from('<I', data, 36)[0]
        data[32 + 24:32 + length] = b'a' * (length - 24)
    else:
        data.extend(command(0xD, '@rpath/libduplicate.dylib'))
        struct.pack_into('<I', data, 16, struct.unpack_from('<I', data, 16)[0] + 1)
        struct.pack_into('<I', data, 20, len(data) - 32)
    path.write_bytes(data)
    with pytest.raises(ClosureError, match='^vocabulary_loader_metadata_invalid$'):
        load_commands(path)


@pytest.mark.parametrize('index,name', [(True, 'liba.dylib'), (1, 'liba.dylib'), (-1, 'liba.dylib'),
                                       (34, 'liba.dylib'), (0, '../liba.dylib'), (0, '/liba.dylib'),
                                       (0, 'liba\0.dylib'), (0, 'liba/d.dylib')])
def test_loader_policy_rejects_paths_and_invalid_indexes(index, name):
    with pytest.raises(ClosureError, match='^vocabulary_loader_policy_invalid$'):
        LoaderMember(index, name)


def test_revision_or_copy_hash_drift_prevents_staging(closure, tmp_path):
    assets, members = closure
    pinned = [_VerifiedFile(a) for a in assets]
    Path(assets[2].path).write_bytes(Path(assets[2].path).read_bytes())
    directory = tmp_path / 'owned'
    directory.mkdir()
    with pytest.raises(VocabularyRuntimeError, match='revision_changed'):
        stage_closure(assets, pinned, members, directory)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='reviewed Mach-O closure only')
async def test_real_owned_intel_worker_loads_only_private_reviewed_closure(tmp_path):
    from pipeline.native_tokenizer import runtime
    directory = tmp_path.resolve()
    sources = Path(runtime.__file__).parent
    common = ['/usr/bin/cc', '-arch', 'x86_64', '-dynamiclib', '-Wl,-rpath,@loader_path']
    dependency = directory / 'versioned-dependency.dylib'
    c = directory / 'dependency.c'
    c.write_text('int inert_dependency(void) { return 1; }\n')
    subprocess.run([*common, '-Wl,-install_name,@rpath/libdependency.0.dylib', str(c),
                    '-o', str(dependency)], check=True, capture_output=True, timeout=30)
    library = directory / 'versioned-main.dylib'
    subprocess.run([*common, '-Wl,-install_name,@rpath/libmain.0.dylib',
                    '-I', str(sources / 'vendor'), str(Path(__file__).parent / 'native/runtime_vocab_stub.c'),
                    str(dependency), '-o', str(library)], check=True, capture_output=True, timeout=30)
    model = directory / 'inert-model'
    model.write_bytes(b'not a GGUF')
    # Native loading from the source directory would fail. Only staged bytes
    # provide the required import name, without following this symlink.
    (directory / 'libdependency.0.dylib').symlink_to(directory / 'missing')
    members = (LoaderMember(0, 'libmain.0.dylib'), LoaderMember(2, 'libdependency.0.dylib'))
    value = VocabularyRuntime(identity(library), identity(model), dependencies=(identity(dependency),),
                              loader_members=members, deadline=asyncio.get_running_loop().time() + 20)
    async with value:
        assert value.cpu == 'x86_64'
        assert await value.count('é\0雪'.encode(), add_special=True, parse_special=True) == 9
        staged = Path(value._directory.name) / 'loader'
        assert sorted(p.name for p in staged.iterdir()) == [m.name for m in members][::-1]
    assert_reaped(value)
    assert Path(str(model) + '.audit').read_text() == 'open\nfree\n'


def test_staged_bytes_must_match_approved_digest(closure, tmp_path):
    assets, members = closure
    pinned = [_VerifiedFile(a) for a in assets]
    changed = (Asset(assets[0].path, '0' * 64), *assets[1:])
    with pytest.raises(ClosureError, match='^vocabulary_loader_copy_mismatch$'):
        stage_closure(changed, pinned, members, tmp_path)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='Mach-O admission must precede native open')
async def test_invalid_closure_bootstrap_returns_fixed_error_before_native_load(closure):
    assets, members = closure
    # Valid Mach-O metadata is enough for compiler target selection, but an
    # incomplete closure must fail before dlopen of these synthetic files.
    value = VocabularyRuntime(*assets[:2], dependencies=assets[2:], loader_members=members[:1],
                              deadline=asyncio.get_running_loop().time() + 15)
    with pytest.raises(VocabularyRuntimeError, match='^vocabulary_loader_resolution_unverified$'):
        async with value:
            pytest.fail('Unverified closure must never become ready')
    assert_reaped(value)
    assert not Path(assets[1].path + '.audit').exists()
