"""Check distribution contents and metadata for accidental local runtime artifacts.

This structural audit is not a secret scanner or Git-history privacy certification.
"""
from pathlib import Path, PurePosixPath
import tarfile
import tomllib
import zipfile

FORBIDDEN = {'.git','.venv','node_modules','.next','__pycache__','.pytest_cache',
             '.logchat','.gnhf','credentials','rag-walkthrough-site'}


def validate_name(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe archive path: '+name)
    if set(path.parts) & FORBIDDEN or path.name.startswith('.env') or path.suffix in {'.pyc','.pem','.key','.db','.sqlite','.sqlite3','.log'}:
        raise ValueError('Local or sensitive artifact in distribution: '+name)


def check(directory):
    version=tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())['project']['version']
    files=[directory/f'logchat-{version}-py3-none-any.whl', directory/f'logchat-{version}.tar.gz']
    for artifact in files:
        if artifact.suffix=='.whl':
            with zipfile.ZipFile(artifact) as archive:
                for name in archive.namelist():validate_name(name)
                assert 'README' in archive.read(f'logchat-{version}.dist-info/METADATA').decode() or 'Get started' in archive.read(f'logchat-{version}.dist-info/METADATA').decode()
        else:
            with tarfile.open(artifact) as archive:
                for member in archive:
                    validate_name(member.name)
                    if member.issym() or member.islnk():raise ValueError('Symlink in source distribution: '+member.name)
    print(f'Distribution structure passed for {len(files)} artifacts. Git history and secret contents require separate review.')


if __name__=='__main__':check(Path(__file__).resolve().parents[1]/'dist')
