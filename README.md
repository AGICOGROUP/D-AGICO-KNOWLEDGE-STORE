# AGICO Enterprise Knowledge Store

This project adds enterprise knowledge and file capabilities to existing agents such as Codex and Accio while reusing the current web and DingTalk entry points. It includes file storage, identity and version management, parsing and indexing, hybrid search, business MCP tools, publication review, original-file reconciliation, backup and restore, and Windows deployment support. Codex has been validated with synthetic materials in ordinary tasks.

**Current test release:** Code and runtime data are separate. Basic workflows have been checked with an initial five real files and nine additional real files; see the [catalog and operating notes](docs/validation/local-preview.md) and [small-file results](docs/validation/small-files.md). The Windows single-directory installer has passed an isolated local service check; see [installation validation](docs/validation/server-setup.md). Overall quality on company materials, Accio and employee access, the target server environment, and capacity still require acceptance testing. This is not yet a production deployment.

## Install on a new Windows server

In an administrator PowerShell session, clone the code and choose a separate, empty data directory:

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\KnowledgeBaseData"
```

The installer downloads dependencies, prepares the database and local models, generates protected credentials, and registers auto-start services. Initial installation requires Windows x64, administrator access, Git, and network access for downloads. Keep the code path in English and use a separate, empty data directory. Source code is separate from uploaded files, the database, models, and credentials; GitHub contains only project files. See [server installation and management](docs/server-setup.md). Configure HTTPS, a domain, and employee permissions for access from other computers.

## Development and the current local test environment

The project uses Python 3.12; dependencies are pinned in `uv.lock`. On this machine, PostgreSQL binaries are under `.local`. The database data lives in a separate data root and starts through the English-path mapping specified in `.local/test-layout.json`; it listens only on `127.0.0.1:15432`. Real samples are in the separate `agico_preview` database; development tests use `agico_test`. This version has no system service or formal employee accounts.

Start the local test API and worker with `./scripts/test-runtime.ps1 start`; use `status` and `stop` to inspect or stop them. The local service is at `http://127.0.0.1:8765`, with MCP at `/mcp`. Local paths and credentials are not distributed with this repository; configure them on each new machine following the operations guide.

```powershell
uv sync --locked --group load
./scripts/dev-db.ps1 start
uv run python scripts/prepare_dev_db.py
uv run pytest -q
```

Full development tests need the `load` dependency group, which includes load-testing and resource-sampling tools; the production service does not. Tests use the dedicated `agico_test` database. Each test creates a random schema and new synthetic files, then removes only that schema and its temporary directory. The current test fixtures read the local `.local/pg-password.txt` and must never point at the production database. On a new machine, install PostgreSQL, initialize the cluster, and create that credential file as described in the [component record](docs/validation/component-check.md). The `.local` directory is not distributed with the code.

Before starting the API and worker, operations staff must set `AGICO_KB_DATABASE_URL` (use a non-superuser for production) and `AGICO_KB_STORAGE_ROOT` (an absolute writable NTFS path shared by both processes) in a protected environment. Do not place credentials in command-line arguments, the repository, or logs. `AGICO_KB_MODEL_CACHE` defaults to `.local/models`; set it to a shared absolute path for deployment. `AGICO_KB_ALLOWED_HOSTS` specifies allowed MCP hostnames and defaults to local access only. Database migrations require pgvector.

```powershell
uv run python -m agico_kb.admin migrate
uv run python -m agico_kb.admin set-identity employee-id "Employee Name" baiste member
uv run python -m agico_kb.admin issue-token employee-id
uv run uvicorn agico_kb.main:app_factory --factory --host 127.0.0.1 --port 8000
```

`issue-token` prints a secret only once in the operations terminal. Give it to a trusted client without recording the output. `revoke-tokens employee-id` revokes all tokens for that identity. The responsible manager's role is `publisher`, configured separately for each division. The identity-management CLI requires database-administration permissions and is unavailable to ordinary employees.

Run `uv run python -m agico_kb.worker` in another terminal. Development mode needs access to a public model source for the first download; document parsing, OCR, and vector inference run locally. For formal deployment, provision models separately, enable offline mode, and pin the expected model identity as described in [operations](docs/validation/operations.md). Model fingerprint, vector dimension, FastEmbed version, and parser version jointly identify an index so incompatible vector spaces are not mixed.

## Agent and file access

### Employee web portal

Open the service root (locally `http://127.0.0.1:8765/`) and enter a member access code to submit or find files. Select a batch of files, then explicitly choose its division; the portal does not choose one automatically. Material type and model are optional. Submitted files enter the review queue. The portal shows processing status, published-file search, division and type filters, and original-file downloads. See [portal usage and validation](docs/validation/employee-portal.md).

The business MCP endpoint is `/mcp` on the API and uses a separate bearer token. `kb_context` fetches background on demand; `kb_search` returns five relevant materials by default; `kb_read` fetches only the needed source content. Upload, submission, status, and file tools share the same backend. Managers also have publish, withdraw, restore, and history tools.

Place the [short agent-use guidance](client/agent-usage.md) in the client's existing instruction mechanism so agents consult company knowledge when needed, reuse evidence already found in a task, and avoid reading the entire library. Synthetic Codex tasks have shown that background, facts, standards, and experience can affect output; see [results and usage](docs/validation/codex.md). Accio, web, and DingTalk still need live integration checks.

Set `AGICO_KB_URL` and `AGICO_KB_TOKEN` where the files reside, then run the included `client/file_transfer.py`:

```powershell
uv run python client/file_transfer.py upload ./document.docx --organization baiste --title "Product Information"
uv run python client/file_transfer.py download VERSION_ID ./downloaded-document.docx
```

The script checks size and SHA-256. It sends the credential only to the configured service and does not follow redirects. A download is finalized only after integrity checks and never overwrites an existing file. The original file is not transferred through model context. This script ships with the repository and is not packaged as a separate employee application.

Stop the development database with `./scripts/dev-db.ps1 stop`. API routes are documented at `/docs` when the service is running.

## Current API workflow

1. `POST /v1/uploads` submits filename, size, and optional SHA-256 with a bearer token and an `Idempotency-Key`.
2. `PUT /v1/uploads/{upload_id}/content` streams the original file. Submission is allowed only after size and hash checks pass.
3. `POST /v1/submissions` binds division, purpose, title, and optional product, model, project, and business date. The default purpose is "other material." The original file remains independent of the parser process and can be downloaded by the draft author.
4. The worker handles parsing and indexing. Managers see drafts at `/v1/submissions` and publish through `/v1/versions/{version_id}/publish`. Optional `category_id` corrects the purpose in the publication transaction without re-uploading; MCP `kb_publish` accepts the same parameter. Incomplete body content requires explicit `accept_incomplete=true` to publish, while processing state and warnings remain visible.
5. `/v1/documents` lists only current published versions the requester may access. `/v1/versions/{version_id}` and its `/content` endpoint return state and the original file. Historical versions require `history=true` and manager permission for the owning division.

Replacement drafts carry `document_id` and `base_revision` and inherit the current file metadata. Management operations supply `expected_revision`. Publication also checks the draft's actual `base_revision`; changing the expected revision in a request cannot bypass an old-draft conflict. Conflicting drafts are preserved for review and may be submitted again.

Withdraw: `POST /v1/documents/{id}/withdraw`; restore a previously published version: `POST /v1/versions/{id}/restore`; withdraw your own draft: `POST /v1/submissions/{id}/withdraw`; manager sharing: `PUT /v1/documents/{id}/grants`; audit: `GET /v1/documents/{id}/audit`.

Managers use `GET /v1/documents?managed=true` to find all files in their scope, including withdrawn items, then `GET /v1/documents/{id}/versions` to choose a historical version to restore. Ordinary employees cannot view withdrawn materials through these management endpoints.

Every new request is authenticated again. Sharing grants read access without copying the file. Private documents are readable by their author and responsible manager by default. New download requests depend on current authorization and publication status; already delivered copies cannot be recalled.

## Consistency and current limits

- A temporary file is written, checked, and renamed on the same volume before its complete state is registered. Interrupted transfers clean up temporary files. A process exit between renaming and database commit may leave an orphan; `python -m agico_kb.operations reconcile` reports missing, damaged, unreferenced, and stale temporary files without deleting them.
- File submission, job registration, publication switching, revisions, and audit records use database transactions. Concurrent publication locks the same file record so an old draft cannot overwrite a newer one.
- Parser subprocesses have timeouts and background failures have bounded retries. A completed index generation is switched atomically; an old process cannot overwrite newer results. If vector indexing fails, parsed text remains searchable.
- DOCX, XLSX, PPTX, native PDFs, scanned PDFs with OCR, and text materials are supported. Uncovered text boxes, graphics, formula caches, and similar content are reported honestly. Legacy binary Office files, CAD files, and model packages are stored as originals and may have explanatory attachments. See the batch reports for the precise coverage boundary.
- Candidates are permission-filtered before keyword and exact-vector ranking are combined. Short excerpts are returned by default; full units are read on demand. Long units may exceed the vector model's length limit, but source text and keywords remain available with a notice.
- Query vectors share one model and wait briefly in arrival order when it is busy. Queue length is bounded; waiting and inference share one timeout budget to prevent unbounded backlogs.
- Tests perform real PostgreSQL and NTFS operations but do not prove capacity for 50 or 200 concurrent agents.
- Backup briefly pauses writes while saving the database and completed originals; reads continue. Restore refuses an occupied database or directory, verifies hashes first, and then restores permissions, published versions, and pending jobs. Restore is rehearsed only on an isolated new target.
- The Windows installer creates configuration by default. Formal installation needs an explicit target and dedicated account. TLS, ACLs, unattended startup, and independent disaster recovery still require verification on the target server. Do not expose the development service directly to many company users.

Development [plan](docs/superpowers/plans/2026-09-15-enterprise-knowledge-store.md). Current validation: [overall review](docs/validation/final-review.md), [50-identity REST/MCP smoke test](docs/validation/load-50.md), [process restart and 32 MiB file](docs/validation/process-restarts.md), [Codex](docs/validation/codex.md), [operations and restore](docs/validation/operations.md), [integration contract](docs/validation/integration-contract.md), and [progress](docs/validation/progress.md).

Project collaboration: [contributors](CONTRIBUTORS.md).
