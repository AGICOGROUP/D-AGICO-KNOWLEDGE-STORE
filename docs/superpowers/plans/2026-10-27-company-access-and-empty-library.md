# Company access and empty library implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, task by task.

**Goal:** Combine fixed DingTalk access with editable extra reading access, then prepare a backed-up company-server document purge.

**Architecture:** Store extensions in a separate table, union them with fresh DingTalk identity on every authorization, and never infer publisher authority from extra reading access. A scoped maintenance operation backs up the database and originals before clearing only document tables and active original storage.

**Tech Stack:** Python 3.12, PostgreSQL, FastAPI, vanilla JavaScript, Windows PowerShell 5.1.

**Spec:** User request in this conversation on 2026-10-27.

## Global constraints

- Preserve accounts, passwords, roles, DingTalk bindings, API keys, organization taxonomy and configuration.
- Company server is remote; local tests must use new isolated schemas and synthetic files.
- Administrators and developers may grant extra reading access; ordinary users may not. Keep existing role-management boundaries.
- DingTalk inactivity/freshness checks still apply. Department changes replace only mapped access.
- No reset, bootstrap, migration reimport, dependency upgrade, or Git publishing.

## Review focus

- Removing extensions must revoke access on the next request, including existing OAuth sessions.
- Managers must not gain publisher access through an extension, nor edit developer roles.
- Upstream errors/inactive identities must not be bypassed by manual grants.
- Purge must refuse incomplete backup or unknown referencing tables; never use TRUNCATE CASCADE.
- Filesystem paths must stay within verified deployed roots and backups must remain protected.

## Task 1: Additive access and personnel editor

Files: accounts.py, dingtalk_identity.py, db.py, migrations/008_access_extensions.sql, portal/admin.js, portal/account.js; integration tests.

- [x] Add fresh integration tests for developer/manager grants, ordinary-user denial, read-only union, persistence through department move, extension removal and list source labels.
- [x] Observe failure, then implement explicit extension storage and PATCH /v1/accounts/{id}/access with strict body validation and audit.
- [x] Expose all division choices only in personnel management; show fixed mapped divisions and editable additions separately.
- [x] Run identity, account hierarchy, file/search access and portal regression tests.

## Task 2: Scoped backup and purge

Files: library_maintenance.py, scripts/update-company-library.ps1, release packaging helper; integration tests; operations.py migration compatibility.

- [x] Write fresh document/job/original fixtures plus protected account/config rows. Test backup refusal, successful empty counts, exact protected-row preservation, repeat guard, and validated restore.
- [x] Implement guarded backup, transactional explicit-table clear and verified original quarantine, with fresh restore evidence.
- [x] Build one self-contained PowerShell 5.1 company maintenance script with owned-service/path checks, backups, source patch hashes and readiness verification.
- [x] Deliver one short server-side command. Report deployment/purge as pending until actual server output arrives.

## Execution decisions

- Work in the current checkout because the unpublished deployed source includes existing local changes. No broad checkout/reset/commit.
- User has explicitly requested the document deletion and manual extension capability. Proceed within this scope without an additional design approval cycle.

## Verification and deployment boundary

- 65 relevant tests passed together; a subsequent fresh adapter/repeat-clear regression passed with the maintenance and release tests (66 unique tests in total).
- Tested actual downloads and an already-issued OAuth token before adding, after adding and after removing access.
- Tested restore of the pre-clear database and original files, exact preservation of non-document rows, SQL failure rollback, and one-time receipt protection for later uploads.
- Native Windows PowerShell 5.1 parsed the script and extracted/verified its real embedded payload in a temporary directory.
- Delivered `update-company-library-20261027.ps1` on the local Desktop. Company-server execution and live readback are PENDING; no company documents have been cleared by this local work.
