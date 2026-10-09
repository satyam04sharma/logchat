"""Verify release metadata and bundled runtime bytes without installing a wheel."""
import argparse
import tomllib
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def check(wheel: Path):
    root = Path(__file__).resolve().parents[1]
    release = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert wheel.name == f"logchat-{release}-py3-none-any.whl", wheel.name
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            assert not any(part.lower() in {
                ".gnhf", ".logchat", "credentials", "private_fixtures",
                "private-fixtures", "fixtures", "tests", "__pycache__",
                "node_modules", ".next", ".venv",
            } for part in parts), name
            assert not any(part.lower().startswith(".env") for part in parts), name
            assert not name.endswith((".pyc", ".pem", ".key")), name
        metadata = BytesParser().parsebytes(archive.read(f"logchat-{release}.dist-info/METADATA"))
        assert metadata["Name"] == "logchat"
        assert metadata["Version"] == release
        # Check every shipped Python module, plus native assets and integrations,
        # against both standalone and embedded-stack copies where applicable.
        checked = 0
        for directory in ("api", "cli", "pipeline", "connectors", "logchat"):
            for source in sorted((root / directory).rglob("*.py")):
                if "_stack" in source.relative_to(root).parts:
                    continue
                relative = source.relative_to(root).as_posix()
                for bundled in (relative,):
                    assert archive.read(bundled) == source.read_bytes(), bundled
                    checked += 1
        assets = [*sorted((root / "logchat/local/static").glob("*")),
                  root / "logchat/resources/integrations/node.mjs",
                  root / "logchat/resources/logchat-knowledge/SKILL.md"]
        for source in assets:
            relative = source.relative_to(root).as_posix()
            for bundled in (relative,):
                assert archive.read(bundled) == source.read_bytes(), bundled
                checked += 1
        assert not any(name.startswith("logchat/_stack/") for name in archive.namelist())
        entrypoints = archive.read(f"logchat-{release}.dist-info/entry_points.txt").decode()
        assert "logchat = cli.native:app" in entrypoints
    print(f"Release {release}: metadata and {checked} bundled runtime files match candidate bytes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    check(parser.parse_args().wheel)
