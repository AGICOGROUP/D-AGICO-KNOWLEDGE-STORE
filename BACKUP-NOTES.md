# Source backup

## Version

Current DingTalk release, 2026-10-27

Current local source tree, based on acfd44f with subsequent development.

## Repository and branches

Repository: https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE

- `main`: current DingTalk source, including fixed department mapping plus local access extensions.
- `codex/backup-2026-10-19`: the existing company email-login backup from 2026-10-19, before DingTalk identity integration. Its source is preserved, with backup metadata and exclusion rules added.

## Scope

This repository contains source code, API functionality, tests, dependency locks, deployment tools and general documentation. Knowledge documents, parsed content, indexes, account databases, passwords, real API keys, encryption master keys, DingTalk secrets, certificates, logs, models, machine-specific runtime configuration and private migration bundles are excluded. Internal validation evidence is excluded because it may contain document excerpts. Original company Git history is retained.

Access extensions are stored only in the knowledge-base database. They do not change the DingTalk organization directory.

## Restore

Clone this repository, choose the branch, install pinned dependencies and follow `docs/server-setup.md`. Provision protected runtime credentials and data separately. This source backup cannot restore company users or knowledge data by itself. Never bootstrap or overwrite existing formal accounts.

Developer: [Dennis-pan-x](https://github.com/Dennis-pan-x).
