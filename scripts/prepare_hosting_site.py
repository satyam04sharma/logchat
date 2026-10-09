"""Prepare a static walkthrough and documentation site without application state."""
import argparse
import html
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError('Use a new output directory.')
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Use an output directory outside the development checkout.')
    destination.mkdir(parents=True)
    walkthrough = (ROOT / 'docs/rag-rebuild/rag-explained.html').read_text()
    walkthrough = walkthrough.replace('architecture-review-2026-10-04.md', 'architecture.html').replace('Read the architecture review', 'Read the native architecture')
    walkthrough = walkthrough.replace('</footer>', '<a href="getting-started.html">Installation guide</a> · <a href="hosting.html">Hosting boundaries</a></footer>')
    (destination / 'index.html').write_text(walkthrough)
    for title, source, target in [('Installation guide', 'README.md', 'getting-started.html'),
                                  ('Native architecture', 'docs/architecture.md', 'architecture.html'),
                                  ('Hosting boundaries', 'docs/hosting.md', 'hosting.html')]:
        body = html.escape((ROOT / source).read_text())
        (destination / target).write_text(f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — Logchat</title><style>body{{max-width:900px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui;background:#fafafa;color:#202020}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#1559a0}}</style><a href="index.html">← RAG walkthrough</a><h1>{title}</h1><p>The following is the source documentation, displayed verbatim.</p><pre>{body}</pre></html>')
    for name in ('release-reader-desktop.png', 'release-memory-mobile.png'):
        target = destination / 'docs/screenshots' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'docs/screenshots' / name, target)
    print(f'Static site prepared at {destination}. No log service, private state or credentials included.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    prepare(parser.parse_args().output)
