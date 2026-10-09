"""Generate local-only credentials without displaying them or replacing existing ones."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

root = Path(__file__).resolve().parents[1]
secret_dir = root / ".logchat"
secret_dir.mkdir(mode=0o700, exist_ok=True)
path = secret_dir / ".secrets"
credentials = Path(os.getenv('LOGCHAT_SETUP_CREDENTIALS_DIR', str(Path.home() / '.local/share/logchat/credentials')))
credentials.mkdir(parents=True, exist_ok=True, mode=0o700)
credentials.chmod(0o700)
if path.exists():
    original = path.read_text()
    additions = ""
    additions_settings = [("LOGCHAT_UID", os.getuid()), ("LOGCHAT_GID", os.getgid())]
    if os.getenv('LOGCHAT_HOST_CREDENTIALS_DIR'):
        additions_settings.append(('LOGCHAT_CREDENTIALS_DIR',os.environ['LOGCHAT_HOST_CREDENTIALS_DIR']))
    for name, value in additions_settings:
        if name + "=" not in original:
            additions += f"{name}={value}\n"
    if additions:
        with path.open("a") as out:
            out.write("\n" + additions)
    path.chmod(0o600)
    print("Preserved existing local credentials and prepared runtime settings.")
    raise SystemExit(0)
password = secrets.token_hex(24)
secret = secrets.token_hex(32)
def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
now = int(time.time())
payload = encode({"role": "anon", "iss": "supabase", "iat": now, "exp": now + 10 * 365 * 86400})
unsigned = encode({"alg": "HS256", "typ": "JWT"}) + b"." + payload
anon = (unsigned + b"." + base64.urlsafe_b64encode(hmac.new(secret.encode(), unsigned, hashlib.sha256).digest()).rstrip(b"=")).decode()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(f"POSTGRES_PASSWORD={password}\nJWT_SECRET={secret}\nANON_KEY={anon}\nAPI_PORT=8080\nWEB_PORT=3000\nSUPABASE_PORT=54321\nLOGCHAT_UID={os.getuid()}\nLOGCHAT_GID={os.getgid()}\n")
    if os.getenv('LOGCHAT_HOST_CREDENTIALS_DIR'):
        out.write('LOGCHAT_CREDENTIALS_DIR='+os.environ['LOGCHAT_HOST_CREDENTIALS_DIR']+'\n')
print("Generated local credentials in .logchat/.secrets (owner access only).")

credentials = Path(os.getenv('LOGCHAT_SETUP_CREDENTIALS_DIR', str(Path.home() / '.local/share/logchat/credentials')))
credentials.mkdir(parents=True, exist_ok=True, mode=0o700)
