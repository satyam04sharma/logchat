"""Optional standalone demo app. Synthetic data only; emits JSON to stdout."""
import argparse
from datetime import datetime, timezone
import json
from uuid import uuid4

from flask import Flask, jsonify
from werkzeug.serving import make_server

app = Flask(__name__)
PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Logchat demo web app</title><style>body{font:18px/1.6 system-ui;max-width:650px;margin:60px auto;padding:24px;color:#24362b;background:#f7f8f5}button{font:inherit;padding:12px 18px;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>A separate application</h1><p>This demo runs independently from Logchat. Click to emit one synthetic session-renewal error into its server terminal.</p>
<button id="emit">Emit session-renewal error</button><pre id="result" aria-live="polite">No event emitted yet.</pre>
<script>document.getElementById('emit').onclick=async()=>{const response=await fetch('/simulate',{method:'POST'});document.getElementById('result').textContent=JSON.stringify(await response.json(),null,2);};</script></html>'''

@app.get('/')
def index():
    return PAGE

@app.get('/health')
def health():
    return jsonify(status='ready', synthetic=True)

@app.post('/simulate')
def simulate():
    event = dict(event_id='QUICKSTART-' + uuid4().hex, timestamp=datetime.now(timezone.utc).isoformat(),
                 service='demo-web', level='error', duration_ms=18,
                 message='Session renewal rejected for demo-user@example.test with DEMO_SESSION_401',
                 email='demo-user@example.test', error_code='DEMO_SESSION_401', status_code=401)
    print(json.dumps(event), flush=True)
    return jsonify(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    # Loopback only, no development reloader; one process keeps capture reproducible.
    server = make_server('127.0.0.1', args.port, app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
