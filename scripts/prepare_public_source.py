"""Create a source export without local state, Git history or historical reports.

This is an explicit allowlist for the first release, not a certification of every
file's contents. Retains original repository and historical evidence unchanged.
"""
import argparse
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=['README.md','SKILL.md','CONTRIBUTING.md','LICENSE','pyproject.toml','.gitignore']
DIRECTORIES=['src','tests','examples','.github']
DOCS=['docs/architecture.md','docs/connectors.md','docs/settings.md','docs/release-readiness.md',
      'docs/hosting.md','docs/cleanup-2026-10-08.md','docs/maintainers/PRODUCT.md','docs/maintainers/CHANGELOG.md',
      'docs/rag-rebuild/rag-explained.html','docs/qa/README.md','docs/qa/requirements.md',
      'docs/qa/release-2026-10-07.md','docs/screenshots/release-reader-desktop.png','docs/screenshots/release-memory-mobile.png']
SCRIPTS=['scripts/prepare_hosting_site.py','scripts/check_release_wheel.py','scripts/check_release_artifacts.py',
         'scripts/prepare_public_source.py','scripts/qa_native.py','scripts/qa_browser.py']
EXCLUDE={'.git','.venv','__pycache__','node_modules','.next','.logchat','.gnhf','_stack','.pytest_cache','build','dist'}


def export(destination):
    destination=destination.expanduser().resolve()
    if destination.exists():raise ValueError('Choose a new output directory to preserve existing files.')
    if destination==ROOT or ROOT in destination.parents:raise ValueError('Use an output directory outside the source checkout.')
    destination.mkdir(parents=True)
    selected=[ROOT/path for path in FILES+DOCS+SCRIPTS]
    for directory in DIRECTORIES:
        selected.extend(path for path in (ROOT/directory).rglob('*') if path.is_file())
    copied=[]
    for path in selected:
        relative=path.relative_to(ROOT)
        if set(relative.parts)&EXCLUDE or any(part.endswith('.egg-info') for part in relative.parts) or path.is_symlink() or path.name.startswith('.env') or path.suffix in {'.pyc','.db','.sqlite','.sqlite3','.log','.key','.pem'}:continue
        if not path.is_file():raise ValueError('Missing release source: '+str(relative))
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        if relative.as_posix()=='docs/rag-rebuild/rag-explained.html':
            target.write_text(target.read_text().replace('architecture-review-2026-10-04.md','../architecture.md').replace('Read the architecture review','Read the native architecture'))
        copied.append(relative)
    archive=destination.parent/(destination.name+'.zip')
    if archive.exists():raise ValueError('Choose a new output archive path.')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in sorted(set(copied)):bundle.write(destination/relative,(Path(destination.name)/relative).as_posix())
    print(f'Exported {len(set(copied))} source files to {destination}\nArchive: {archive}\nHistorical reports and screenshots, local state and Git history excluded. Only allowlisted synthetic release screenshots included. Review contents before publication.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    export(parser.parse_args().output)
