# Security

## Secrets and private data

Do not commit `.env`, `.env.production`, `.local`, database dumps, uploaded papers, generated artifacts, exported traces, passwords, API keys, or session cookies. Use `.env.example` and `.env.production.example` only as key-name templates.

Run `python scripts/product_check_staged.py` before publishing a commit. The check rejects private runtime paths and scans staged blobs for currently configured secret values without printing those values.

## Deployment boundary

Production requires HTTPS at the reverse proxy, Secure cookies, a concrete Host allowlist, and a disabled web setup endpoint. Create users through `python -m product.manage_user`; do not pass passwords as command-line arguments.

GitHub Actions stores only a dedicated deployment SSH key, a verified `known_hosts` entry, and the SSH endpoint. Application API keys and the database password remain in the server-only `/opt/research-copilot/.env.production`. Release workflows deploy an immutable GHCR digest and remove the short-lived registry credential after each pull.

Trace export is owner-scoped and redacted, but exported files can still contain research questions, filenames, source excerpts, model metadata, and operational timestamps. Treat them as private data.

## Reporting

Until a public security contact is configured, report vulnerabilities privately to the repository owner. Do not include live credentials, private papers, or user data in an issue.
