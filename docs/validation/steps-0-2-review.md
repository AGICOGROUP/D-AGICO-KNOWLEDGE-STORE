# Steps 0–2: Delivery and Review

Date: 2026-09-15. Branch: `dev/steps-0-2`. Stopped upon completion of this batch; Step 3 has not been started.

## Completed Work

- Step 0: Verification of native Windows local components; PostgreSQL 16.15 / pgvector 0.8.6, Python 3.12.14, synthetic DOCX extraction, local Chinese vector matching, real Codex MCP read-only invocation, and rejection of unauthenticated calls.
- Step 1: Base configuration and migrations, 8 organization partitions and 6 purpose categories, independent tokens and trusted-organization roles, streamed save with validation, idempotent upload and submission, draft and original-file retrieval.
- Step 2: Publication by the owning department's head, explicit file-only publication, concurrency and stale-draft conflicts, take-down, managed history listing and restore, cross-department read-only sharing, auditing, identity initialization, and token issuance/revocation CLI.

## Verification

`pytest -q`: **19 passed**. Every database test uses a random schema, a temporary directory, and fresh synthetic files; explicit idempotent retries reuse only that test case's own inputs.

Coverage: consistent hash for Chinese original-file downloads, cleanup after interrupted transfers, rejection by size/hash/expiry, originals cannot be overwritten, deduplication of concurrent duplicate submissions, rejection of unauthorized access by anonymous/cross-department/private-draft attempts, rejection of revoked and expired tokens, publication permissions, unprocessed files faithfully flagged, only one of concurrent drafts publishable, forged expected revision numbers cannot bypass stale-draft detection, conflicting drafts and retries preserved, new reads stop after take-down, the department head discovers history and restores in a new session, sharing revoked immediately, audit records exist, replacement-draft metadata returned in full.

`ruff check src tests scripts` and `ruff format --check src tests scripts` pass. `uv build --wheel` passes, and the wheel was checked to contain the migration SQL and the category JSON; the identity CLI help entry works.

The test environment has one upstream Starlette DeprecationWarning about a deprecated AnyIO alias; it did not affect the tests. No passing results were manufactured by suppressing warnings or loosening assertions.

## Independent Review and Fixes

Review scope: baseline `deb4b97` through the Step 2 commit `b35cd04`, plus the corresponding fixes. The reviewer examined code, plans, and tests in read-only fashion; tests were actually run by the primary executor.

| Issue | Fix and Verification |
|---|---|
| Major: after take-down, materials could not be discovered in a new session for restore | Added a department-head-restricted `managed=true` file list and version history endpoints; tests for new-session discovery, restore, and rejection of ordinary employees pass; reviewer re-checked and confirmed |
| Minor: updates to category display names in configuration did not take effect | Display names are updated by stable identifier during migration; reviewer re-checked and confirmed |
| Self-check: metadata that a replacement draft must carry was not returned in full | Status/submission results now include organization, purpose, title, product, model, project, business date, etc.; test passes where another employee reads and then submits a replacement draft |
| Self-check: retrying after a stale-draft conflict returned success, giving inconsistent messaging | Retrying the same draft still returns the conflict and the original version identifier, without generating a duplicate version; test passes |

Conclusion: **Review of this batch is complete, the major issue has been fixed, and no unresolved problems blocking development of the next batch were found.** This is not production go-live acceptance.

## Explicitly Deferred to Later

Original-file and version management are currently testable; there is no production parsing worker, content retrieval, production business MCP, multi-user entry-point delegation integration testing, deployment backup/restore, or capacity conclusions. The native DOCX / lightweight vector approach is only a route validation; Docling, OCR, complex tables, multilingual models, and Accio will be tested in practice at their corresponding steps.

No concrete configuration for the target server or the trusted-employee entry point is currently provided. `.local` is for the development environment only and is not the production database deployment package; test probes do not write to global client configuration. Reconciling crash-orphaned files between original-file renaming and database commit is later operational-recovery work; nothing is automatically deleted at present.

Next batch: Steps 3, 4, and 5. Per the user's request, implementation will proceed only after confirmation to continue.
