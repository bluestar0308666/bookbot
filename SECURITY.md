# Security policy

BookBot is designed for a private library. Do not expose its service directly to the Internet without authentication and HTTPS.

## Keep private

- `data/`, `reader.db`, `publish-config.json`, tokens and passwords
- PDFs, converted pages, covers and `library.json`
- backups and logs

The repository ignores these paths by default. Before publishing a fork, run a secret scan over both the working tree and Git history.

## Reporting a vulnerability

Please open a private security advisory in the repository instead of a public issue. Include affected versions, reproduction steps and impact. Do not include real tokens, passwords, private PDFs or personal reading data.
