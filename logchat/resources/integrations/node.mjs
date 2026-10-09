/** Server-side structured log delivery for an already-running Node/Next.js app.
 * No dependencies. Run `logchat local attach --project PATH` once first.
 * Never import this module into a browser/client bundle.
 */
import { readFileSync, lstatSync } from 'node:fs';
import { resolve } from 'node:path';

export function createLogchat({ project = process.cwd() } = {}) {
  const descriptor = JSON.parse(readFileSync(resolve(project, '.logchat/local.json'), 'utf8'));
  const url = new URL(descriptor.api_url);
  if (url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('Logchat requires a loopback endpoint.');
  }
  const credential = lstatSync(descriptor.credential_file);
  if (credential.isSymbolicLink() || (process.platform !== 'win32' && (credential.mode & 0o077))) {
    throw new Error('Logchat credential permissions are invalid.');
  }
  const record = JSON.parse(readFileSync(descriptor.credential_file, 'utf8'));
  if (record.scope?.purpose !== 'local_ingest' || record.scope?.project_id !== descriptor.project_id ||
      record.scope?.source_id !== descriptor.source_id || record.scope?.endpoint !== descriptor.api_url || typeof record.value !== 'string') {
    throw new Error('Logchat credential does not match this source.');
  }
  const endpoint = new URL(`/projects/${descriptor.project_id}/events`, url);
  return {
    async emit(event) {
      try {
        const body = JSON.stringify({ source_id: descriptor.source_id, events: [event] });
        if (Buffer.byteLength(body) > 240000) return false;
        const response = await fetch(endpoint, {
          method: 'POST', redirect: 'error', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${record.value}` },
          body, signal: AbortSignal.timeout(3000),
        });
        return response.ok;
      } catch { return false; } // Logging failure should not crash the user's request.
    },
  };
}
