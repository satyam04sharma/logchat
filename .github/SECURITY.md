# Security

Native Logchat is intended for one OS user on loopback. It does not provide multi-user hosted isolation. Keep state directories and credentials private; do not expose its port to the internet. Provider CLIs execute with the current user’s permissions. A custom CLI is an explicitly trusted local program, while its output remains untrusted log data.

The compact path does not blanket-redact log identifiers. Selected fields and summaries can retain sensitive input. Summaries-only is the default; temporary raw capture is explicit, bounded and excluded from retrieval. Models run at the configured loopback Ollama endpoint. Provider tokens are stored separately from memory and status.

Do not post a vulnerability containing tokens, logs or personal data in a public issue. Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/satyam04sharma/logchat/security/advisories/new). No separate security inbox or response SLA has been established.

Before publication, review Git history and historical reports/screenshots for private data. A clean wheel or source export does not prove the existing repository history is safe. Rotate any credential that was committed; removing it from the current tree is insufficient.
