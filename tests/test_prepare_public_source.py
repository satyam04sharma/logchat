"""Public exports retain reproducible QA without copying private run state."""
import importlib.util
from pathlib import Path
import zipfile


def test_export_includes_qa_commands_and_fixtures_without_private_state(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('prepare_public_source', root / 'scripts/prepare_public_source.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    # Use a small source tree to prove private artifacts are excluded even if
    # they appear underneath an otherwise allowed directory.
    source = tmp_path / 'source'
    for name in exporter.FILES + exporter.DOCS + exporter.SCRIPTS:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    for name in ['examples/qa/demo.py', 'examples/qa/console.html']:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    private = ['examples/.env.qa', 'examples/state.sqlite', 'examples/secret.key',
               'examples/.gnhf/notes.md', 'examples/.logchat/control.json',
               'examples/quickstart/build/lib/obsolete.py', 'examples/demo.egg-info/private.txt']
    for name in private:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private-canary')
    monkeypatch.setattr(exporter, 'ROOT', source)
    output = tmp_path / 'public'
    exporter.export(output)
    expected = ['docs/qa/README.md', 'docs/qa/requirements.md', 'scripts/qa_native.py',
                'scripts/qa_browser.py', 'examples/qa/demo.py', 'examples/qa/console.html',
                'docs/screenshots/release-reader-desktop.png', 'docs/screenshots/release-memory-mobile.png',
                'docs/qa/release-2026-10-07.md']
    with zipfile.ZipFile(output.with_suffix('.zip')) as archive:
        for name in expected:
            assert (output / name).read_bytes() == (root / name).read_bytes()
            assert archive.read('public/' + name) == (root / name).read_bytes()
        assert not any(b'private-canary' in archive.read(name) for name in archive.namelist())
    assert not any((output / name).exists() for name in private)
    readme = (output / 'docs/qa/README.md').read_text()
    assert "pip install -e '.[dev,qa]'" in readme
    assert 'scripts/qa_native.py' in readme and '--walkthrough' in readme
