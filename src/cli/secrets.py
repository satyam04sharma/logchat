"""Local credentials. Docker-compatible files are protected and strictly scoped."""
import json
import os
import secrets
from pathlib import Path
from uuid import UUID


def secret_dir():
    return Path(os.getenv("LOGCHAT_SECRETS_DIR", str(Path.home() / ".local/share/logchat/credentials"))).expanduser()


def write_credential(value, **scope):
    directory = secret_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    reference = str(UUID(bytes=secrets.token_bytes(16), version=4))
    fd = os.open(directory / (reference + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"value": value, "scope": {k: str(v) for k,v in scope.items()}}, out)
    return reference


def read_credential(reference, **scope):
    if not reference:
        return None
    try:
        ref = str(UUID(reference))
        path = secret_dir() / (ref + ".json")
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError()
        record = json.loads(path.read_text())
        if record.get("scope") != {k: str(v) for k,v in scope.items()}:
            raise ValueError()
        return record["value"]
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Credential unavailable for this scope.") from None


def delete_credential(reference):
    if reference:
        (secret_dir() / (str(UUID(reference)) + ".json")).unlink(missing_ok=True)
