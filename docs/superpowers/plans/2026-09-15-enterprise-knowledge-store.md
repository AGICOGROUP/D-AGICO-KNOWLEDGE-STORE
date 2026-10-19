# Development Plan for the Enterprise Knowledge Extension Library and File Library

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task in the current task. Steps use checkbox tracking. By default, proceed in task-dependency order: first get the minimal usable flow running end-to-end, then fill in the first-version capabilities; verify after each item is completed. Parallel development by multiple Agents is not required.

**Goal:** Build, on a Windows server, an additional enterprise knowledge extension library for Agents such as Codex and Accio, enabling them to learn about the company's business, products, standards, and experience on demand via MCP, and to apply this knowledge to all kinds of answers, judgments, and work tasks, producing results that better fit the company's needs; at the same time, provide employees with the ability to upload, find, and retrieve files through existing entry points.

**Architecture:** An Agent answers a question or performs a task → calls MCP as needed → understands the relevant company background, facts, standards, and experience → applies that knowledge to answering and task execution. A single Python knowledge backend provides both the MCP and file transfer interfaces, with a separate background process responsible for parsing, OCR, and indexing; PostgreSQL stores management data and indexes, while the Windows data directory stores the original files. The existing web/DingTalk frontends reuse the same set of knowledge and file capabilities.

**Tech Stack:** Verified: Python 3.12, FastAPI, the official MCP Python SDK 2.2, PostgreSQL + pgvector, and jieba; native formats are handled with python-docx / openpyxl / python-pptx / PyMuPDF, and scanned documents with RapidOCR; local vectors use FastEmbed's lightweight multilingual MiniLM 384-dimensional model. Dependencies are locked in uv.lock, and model identity includes file fingerprints. Windows service hosting is still to be validated later per the WinSW approach.

**Spec:** [Project Design Baseline](../../project-design-baseline.md), [Knowledge Base Classification Scheme](../../knowledge-base-classification.md). This file, together with these two documents, constitutes the current basis for development.
## Global Constraints

- Simple, efficient, sensible design; no over-engineering.
- The core deliverable is an enterprise knowledge extension package that Agents can invoke on demand: a unified document repository + MCP capabilities + brief usage conventions, so that company knowledge genuinely influences the original Agent's answers, judgments, and execution results.
- Enterprise-related work should fetch company context and evidence on demand, without relying on the user to instruct a search every time; do not repeatedly load all background material or scan the entire repository, and do not harden any illustrative example into a dedicated business system.
- Original files are saved first; parsing and vectorization run in the background; no dedicated ingestion Agent is configured.
- Company materials are stored locally; the data boundary for cloud models is made explicit through the existing Agent access contract.
- Reuse the existing web page, DingTalk, and execution Agents; do not develop a standalone Q&A application or a full employee website.
- Preserve original files, reliable sources, permissions, official versions, conflict checks, takedown and restoration, and independent backups.
- Free submission, authorized release; company public knowledge is not unconditionally public material.
- By default, return relevant content on demand; do not set strict fixed per-task token quotas.
- The initial phase is validated with 50 Agents reading concurrently; expand and validate to 200 in the mid-to-late phases; do not claim the target is met before testing.
- Test materials must be re-uploaded, parsed, and indexed as new files each round; except for explicit failure retries and idempotency scenarios, historical results must not be used in place of new inputs for testing.
- Windows is the target system; Codex and Accio are the primary clients. The current machine is not automatically treated as the company target server.
- Changes to this plan or design are merged directly, overwriting the original content; historical versions and backups of production system materials are retained as usual.

---

## 1. Plan Deliverables and Current Status

The plan is numbered 0–8, nine steps in total. Three batches of local implementation and their stage reviews are complete; on 2026-09-16 the user authorized filling in all local gaps, followed by an overall review. Already added: bounded waiting for query vectors, publishing category review, read-only original reconciliation, MCP concurrency testing and threshold determination, real process restart, and 32 MiB file validation. Real enterprise materials, the Accio/employee entry point, the production server, and formal capacity acceptance still require actual input; local validation does not substitute for these acceptance checks.

The core acceptance goal is "Agents acquire and apply enterprise knowledge so that answers and various task results fit the company better." Select a small number of real questions and tasks from background understanding, fact lookup, specification application, and experience reference for validation; do not define the product by a single example. Historical quotes are just one illustrative case. Employee upload, document lookup, content Q&A, and file retrieval share this document capability and remain in scope for the first version.
The overall framework of this plan and the two categories of requirements are settled. The component combinations, table names, tool names, module files, and test parameters below are actionable starting suggestions, to be finalized when the corresponding features are implemented; when a mature component can achieve the same goal more simply, directly override and adjust the relevant content — do not add work merely to conform to the draft's form. Fundamental requirements such as identity and permissions, original file preservation, version traceability, publishing, and recovery still need to be implemented.

The first usable workflow selects only a small amount of representative material and one workable client, to get "save material → process → permission-based retrieval and reading → Agent applied to a question or task → obtain the original file" working end to end. Use synthetic or approved pilot material and small-scale identity verification, then complete the remaining formats, Codex/Accio, the employee portal, operational safeguards, and first-phase capacity acceptance. A successful initial run-through does not equal full delivery of the first version.

Verified documentation basis: PostgreSQL and Docling provide Windows support; pgvector officially provides Windows build instructions; the local Codex CLI's `mcp add --help` shows support for Streamable HTTP URLs and environment-variable Bearer tokens. Support documentation is no substitute for actual server and client integration testing. [PostgreSQL Windows](https://www.postgresql.org/download/windows/), [Docling installation](https://docling-project.github.io/docling/getting_started/installation/), [pgvector](https://github.com/pgvector/pgvector), [Codex MCP](https://developers.openai.com/codex/mcp/)

Accio is listed as a mandatory test client under the name provided by the user; Task 0 determines its actual version and integration requirements, and its specific MCP, authentication, and attachment transfer capabilities are to be verified in an available environment, no later than Task 5/6. The entire integration must not be declared compatible based solely on advertised MCP support.

## 2. Technical Solution and Reuse Boundaries

### 2.1 Recommended Combination

| Area | First-Version Recommendation | Development Boundary |
|---|---|---|
| Backend and MCP | Python + FastAPI + official MCP SDK | Write enterprise business logic and tool wrappers; reuse the protocol implementation |
| Data and Retrieval | PostgreSQL + pgvector + built-in full-text search | Write data associations, authorization filtering, and retrieval composition; reuse database capabilities |
| Chinese Keywords | jieba tokenization + a small set of business dictionaries + exact-match fields for codes | Ensure effective handling of Chinese text and product codes; do not stand up a separate search cluster |
| Document Processing | Native Office libraries + PyMuPDF + RapidOCR | Write format adaptation and true-source mapping; explicitly degrade unparsed regions; do not develop an OCR engine |
| Vectorization | Local multilingual MiniLM, 384 dimensions, FastEmbed/ONNX | Batch processing and model file fingerprinting; do not train models; decide whether to upgrade the model only after seeing complex business samples |
| Original Files | NTFS data directory; file identifiers generate the storage paths | Write streaming upload, permission checks, and version references |
| Background Jobs | PostgreSQL job table + one worker process | Support claiming, retries, and crash recovery; do not introduce message middleware upfront |
| Windows Operation | PostgreSQL native service; WinSW hosting the API and worker | Write installation, health-check, backup, and recovery scripts |
pgvector supports being combined with PostgreSQL full-text search; Chinese word segmentation uses jieba's search mode, and the original model-number strings are stored separately to keep tokenization from breaking the identifiers. [pgvector hybrid retrieval](https://github.com/pgvector/pgvector#hybrid-search), [jieba](https://github.com/fxsjy/jieba), [PostgreSQL full-text search](https://www.postgresql.org/docs/current/textsearch-controls.html)

The second batch adopts lightweight multilingual MiniLM as the starting point for local embeddings, already validated on Windows with synthetic data; BGE-M3 is kept as a candidate for cases with genuine quality requirements and is not introduced for now. API query vectorization runs at single concurrency with timeout-based degradation, and the worker runs independently; official model distribution and capacity testing take place in the corresponding deployment/acceptance phases.

### 2.2 Why this component combination is adopted

This keeps permissions, releases, versions, and file records in the same database, shortening the consistency-handling chain. However, this still requires developing the enterprise business layer, so this work cannot be called fully out-of-the-box.

A full RAG product can also be reused. Taking RAGFlow as a comparison candidate, its publicly documented features include parsing, retrieval, Q&A, and Agents, and its deployment involves more supporting components. Whether it can directly satisfy this project's versioning, authorization, and file delivery through configuration alone requires actual verification; it cannot simply be assumed to satisfy or not satisfy them. [RAGFlow official repository](https://github.com/infiniflow/ragflow)

Task 0 first checks whether the company already has a reusable platform; if it can pass the same set of baseline acceptance checks while reducing overall development and operations work, it will be adopted and the subsequent plan adjusted accordingly, overriding it as needed. If not, proceed with this combination. Only one route is selected for implementation; two knowledge bases are not maintained at the same time.

### 2.3 Windows-specific arrangements

- Prefer native Windows installation; after recording the actual Windows version, select the PostgreSQL major version from within the officially supported range, then pin the corresponding pgvector and Python dependencies.
- If pgvector needs to be compiled, build the extension on the build machine using the official instructions so that it matches the PostgreSQL major version and architecture, and keep the build records and file checksums; do not download DLLs from unknown sources.
- Native format libraries, RapidOCR, and the local embedding model have been validated on this machine; the target server still requires deployment validation. Legacy .doc/.xls/.ppt files are currently only stored as originals, with submitters prompted to provide the newer Office formats; no unvalidated conversion service is introduced upfront. A restricted conversion process will be integrated only when there is an actual legacy-file backlog.
- Prefer reusing the company's existing HTTPS entry point; if there is none, first use the API service's own TLS capability together with company-approved certificates. Deployment validation must check the trust chains of Codex and Accio.
- The API and the worker are registered as background services using dedicated service accounts; PostgreSQL and the file data directories grant access only to the processes that need them. Reuse service-wrapping components rather than implementing a Windows service framework ourselves. [WinSW](https://github.com/winsw/winsw)
- Docker Desktop, WSL, Linux virtual machines, or a new reverse proxy are not introduced by default. If native components genuinely hit a blocker, compare supported alternatives in Task 0 and then update the plan.
## 3. Data and Interface Conventions

### 3.1 Minimal Data Structure

| Table / Record | Core Fields and Purpose |
|---|---|
| `organizations`, `categories` | Stable identifiers, names, enabled status; initialized with 8 organization partitions and 6 usage categories |
| `principals`, `memberships`, `access_tokens` | User / service identities, organizational relationships, token digests and revocation status; roles are managed by the trusted server side |
| `documents`, `document_grants` | Document identity, ownership, category, product / model / project, optional business date, read permissions, `active_version_id`, `revision` |
| `versions` | Original file location, hash, uploader, source version label, `base_revision`, publication and processing status |
| `file_links` | Associations between versions such as previews / model packages / parameter sheets; sharing does not copy the original files |
| `uploads`, `jobs` | Upload completion and submission status; job attempt count, claim lease, processing generation, next retry time |
| `chunks` | `version_id`, processing generation, chunk original text, true source location, tokenized text, vectors and model identifier |
| `audit_events` | Who performed submission, publication, withdrawal, restoration and authorization changes on which record |

Enterprise knowledge extensions include company and department background, business and product facts, work standards, templates, project materials, and historical experience. The same data structure is reused; background and brief experience entries can use Markdown as the original file. Business dates come from the original text or verified records; upload time does not substitute for the business date. Specific scenarios use the original text and generic association fields; no dedicated business databases are added for the sake of examples.

### 3.2 Status and Consistency

Processing status: `queued / processing / ready / partial / failed / stored_only`. `partial` returns both an explicit capability description and a failure explanation, e.g., the text is readable but vectors are not yet available; `stored_only` means the attachment is registered, which does not equal successful parsing.

Publication status: `draft / published / superseded / withdrawn`. The responsible person may explicitly accept a "file-management-only" or partially parsed version, but the publication interface must state its content retrieval limitations; failures are never silently treated as fully retrievable.
`documents.active_version_id` is the official version pointer used by ordinary queries; default searches and reads must check this pointer, the publication status, and permissions together. Historical versions may be read only through an explicit history request accompanied by the corresponding authorization.

The "historical versions" above refers only to superseded revisions of the same file. Historical records of different projects, times, and business activities may each remain officially searchable, without needing to enable the system revision history, and must not be entirely excluded by a "query only the latest date" rule. Return the original text's scope of applicability, timing, preconditions, and limitations, stating any missing items truthfully; distinguish current norms, facts, and historical references based on the existing purpose classification and the original text, and do not automatically treat historical cases as execution requirements.

- New files start at `revision=0`; the submission record stores the `base_revision` as of that moment.
- Publishing, taking down, restoring, and administrative changes that affect visibility are checked and increment `revision` within the same database transaction.
- Both submission and publishing check versions; at publishing time the `base_revision` saved with the submission is verified against the current `revision`, so a client cannot bypass the stale-draft conflict merely by changing the `expected_revision` it fills in. When a conflict occurs, the modified draft is kept, and `VERSION_CONFLICT` together with current version information is returned to an operator with permission; only after re-verifying the modification basis can it be submitted again.
- New parse results are first written to a separate processing generation; once complete, the processing generation available to that version is switched over; queries never read partial batch results.
- Worker retries must validate the lease and the processing generation; an old process must not overwrite new results, republish, or restore taken-down content.
- Uploads first land in a temporary file; after full validation, the file is atomically renamed on the same disk, and its submittable state is then registered; orphaned files left behind by database registration failures are handled by a reconciliation job. Do not claim upload success first and then save the original file asynchronously.
- The first version has no answer cache; take-downs and permission changes take effect directly through the query and download authorization checks, without building an additional cache-invalidation system.

### 3.3 Ordinary MCP Capabilities

The following is the suggested capability split and initial interface names, to be finalized during the MCP implementation phase together with mature SDKs and actual clients; duplicate tools may be merged, but capabilities must remain clear and output concise. The authenticated identity comes from the request context; freely fillable `employee_name`, `role`, or `allowed_departments` parameters are not provided.

| Tool | Input | Returns |
|---|---|---|
| `kb_context` | Optional `organization_id`, task keywords | Called only when the task needs background; returns relevant official background and sources; not a mandatory step for starting a task |
| `kb_search` | `query`, `mode=files/content`, optional organization/model/category/file/business-date-range filters, `limit`, `offset` | File cards or snippets of relevant background, facts, norms, or experience material, with sources and an identifier for further reading |
| `kb_read` | `version_id`, optional `chunk_id`, `offset`, `limit`, `max_chars` | Original-text paragraphs/sections/tables, locations, and completeness notes |
| `kb_prepare_upload` | File name, size, optional content hash | `upload_id`, controlled upload address, validity period, and transfer method |
| `kb_submit` | `upload_id`, ownership, category, optional `document_id`, `base_revision`, product, and other fields | `document_id`, `version_id`, processing and publication status |
| `kb_status` | `version_id` | Processing, publication, and failure descriptions, plus allowed next-step actions |
| `kb_get_file` | `version_id` | File name, size, hash, and a controlled retrieval reference; does not return the file binary |
| `kb_withdraw_submission` | One's own `version_id` | The result of withdrawing an unpublished submission; does not grant permission to take down official material |
The first version of `kb_search` returns 5 items by default, with a configurable cap and pagination; 5 is an adjustable starting value, not a task budget. Subsequent reads return complete semantic units, without cutting off units or footnotes. Responses explicitly include `has_more`, `next_offset`, and `warnings`.

Administrative capabilities are authorized separately: `kb_review_submissions`, `kb_publish`, `kb_withdraw`, and `kb_restore`. They respectively list pending submissions within the holder's scope of responsibility, publish, withdraw, and restore; all administrative writes carry `expected_revision`. The first version prioritizes letting owners use the same backend administrative capabilities through the existing Agent, rather than simultaneously developing a duplicate business-management CLI; the CLI keeps only necessary operations such as identity initialization and token revocation. Clients without administrative permissions are not shown the management tools, and the backend still independently checks authorization.

Error codes uniformly use `NOT_FOUND` (does not exist or is not visible), `VERSION_CONFLICT`, `UPLOAD_INCOMPLETE`, `PROCESSING_INCOMPLETE`, `INVALID_ARGUMENT`, and `RETRY_LATER`; tools also return a plain-language Chinese explanation. Detailed backend reasons go into permission-restricted logs.

### 3.4 File Transfer and Identity

```text
Prepare upload → the existing entry point or the runtime environment where the Agent resides sends the file to the upload address → verification completes → submit using upload_id
Obtain file reference → an authenticated delivery program downloads → deliver through the original web / DingTalk entry point
```

File transfer uses HTTP streaming endpoints: `PUT /v1/uploads/{upload_id}/content` and `GET /v1/versions/{version_id}/content`. Upload records are bound to identity, size, and completion status; ownership is verified again at submission time.

Download URLs require identity authentication by default; if an ordinary browser cannot attach authentication directly, the trusted delivery program of the original entry point retrieves the file and then delivers it, instead of issuing permanent public bare links by default. New download requests re-check permissions and withdrawal status; file copies already delivered to employees or third-party platforms cannot be recovered by withdrawing them from the knowledge base.

If Codex / Accio cannot directly complete binary transfers, provide the same small client adapter script, which runs in the environment that actually holds the files. The script only transfers files and outputs references; it does not build a local knowledge copy or another backend. MCP tools do not accept arbitrary server-side local paths, nor do they fetch arbitrary URLs by default.
Standalone employee clients should prefer standalone, revocable access tokens or an existing authorization system. When an entry-point Agent acts on behalf of multiple employees, the trusted entry-point/execution scheduling layer must bind the actual user's credentials or delegated identity; identity must not be selected via model parameters. The first version will not build a separate OAuth identity platform; when OAuth is needed, reuse the existing identity service and integrate through the SDK. Until the real delegation chain has passed verification, no employee entry point that mixes identities across multiple people will go live. [MCP Authentication Specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

## 4. Planned File Structure

The following are planned paths; steps 0–5 have been implemented. The migration SQL and taxonomy JSON are actually placed inside src/agico_kb and shipped with the Python package; other not-yet-implemented paths will be created along with their corresponding tasks.

```text
pyproject.toml / uv.lock                 dependencies and locked versions
src/agico_kb/main.py                     API, MCP mounting, and health checks
src/agico_kb/config.py                   configuration and dependency checks
src/agico_kb/contracts.py                tool and business request/response types
src/agico_kb/db.py                       database connections and transactions
src/agico_kb/auth.py                     trusted identity resolution and unified authorization
src/agico_kb/catalog.py                  taxonomy, metadata, and authorization catalog
src/agico_kb/files.py                    upload, submission, controlled download, and file reconciliation
src/agico_kb/lifecycle.py                publishing, conflicts, takedown, restoration, and auditing
src/agico_kb/ingestion.py                parsing, source locations, and chunk construction
src/agico_kb/embeddings.py               local models, model versions, and batching
src/agico_kb/worker.py                   job claiming, leases, and retries
src/agico_kb/search.py                   precise filtering, Chinese retrieval, vector fusion, and reading
src/agico_kb/mcp_server.py               wrappers for regular and admin tools
src/agico_kb/admin.py                    controlled CLI for owners and operations
migrations/001_initial.sql               data structures, constraints, and indexes
config/taxonomy.json                     8 organizations and 6 categories
config/search-dictionary.txt             terms required for sample validation, without fabricated business words
client/file_transfer.py                  upload/download adaptation for the existing runtime environment
client/agent-usage.md                    brief knowledge-usage conventions shared by Codex/Accio
scripts/probe.ps1                        Windows dependency and permission checks
scripts/install-services.ps1             register this project's Windows services
scripts/backup.ps1 / restore.ps1         data and file backup and independent restore
deploy/windows/                          WinSW configuration templates for the API/worker
tests/conftest.py                        isolated database, temporary storage, and test identities
tests/contract/                          business interface, authorization, and state-transition tests
tests/integration/                       file processing, retrieval, and MCP integration tests
tests/fixtures/                          synthetic test source files and source-location answers
tests/load/                              50/200 concurrent-request load definitions
docs/validation/                         component, business, client, load, and recovery reports
```
Keep a small number of modules organized by function; do not create a separate package for each business field, and do not split into multiple deployment projects prematurely. The file table is for describing responsibilities; it does not require pre-creating all empty modules according to the table or splitting code just to increase the file count.

## 5. Execution Tasks

### Task 0: Windows and Client Compatibility Validation, Locking In One Route

**Deliverables:** A reproducible dependency validation record, plus integration contracts for the server, Codex, Accio, and file handoff.

**Files:** Create `scripts/probe.ps1`, `docs/validation/component-check.md`, `docs/validation/integration-contract.md`, `tests/fixtures/manifest.json`; overwrite and update this plan's technical table where necessary.

**Inputs:** Existing Windows environment, available clients, and synthetic or approved samples. **Outputs:** The first viable technical route, verified component versions, identity and file handoff conventions, and the remaining actual integration items; full-format and dual-client detailed validation is completed in the corresponding tasks.

- [x] Record the development Windows version, architecture, CPU, and memory, and provide a GPU/disk configuration check script; it has been made explicit that the current machine is not the confirmed target server. Target system and service account conditions are left for verification during the deployment phase, and the script does not output secrets.
- [x] Use official installation/build methods to validate the database, vector extension, Python, and one document processing route; first use simple native text files to validate parsing and vectorization. Record the currently passing versions, and lock in the corresponding dependencies when OCR and other formats are added later; do not deliver with a floating `latest`.
- [x] Use a small number of newly created synthetic samples to validate Chinese content, model numbers, units, and source locating. Cross-page complex tables, the various Office formats, and scanned documents are scheduled for Task 3, and material applicability for Task 4/6/8; once real samples are in place, re-run a complete import, and do not turn every sample issue into a blocking prerequisite for Task 0.
- [x] Verify whether a platform that already meets the requirements exists; choose between reusing a platform or this plan's component combination based on the same set of validation results. Unless proven to reduce total workload, do not additionally introduce a full RAG product.
- [x] Start from the currently available Codex or Accio to first validate tool listing, test calls, authentication, and network reachability, and map out the file transfer path; for the other client, record existing information and the parts requiring live testing, to be completed in Task 5/6. Codex's `mcp add` is the evidence-based integration method; Accio uses the configuration entry provided by its actual version.
- [x] Write out the integration contract for trusted entry binding of employee credentials and return of the original session; the real web/DingTalk delegation configuration has not yet been provided, so record it for verification in Task 6, and current standalone identity trials do not substitute for multi-person delegation joint testing.
Verification commands used during implementation:

```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_ComputerSystem | Select-Object NumberOfLogicalProcessors, TotalPhysicalMemory
Get-Volume | Select-Object DriveLetter, FileSystem, SizeRemaining, Size
codex mcp add --help
```

**Pass criteria:** Reproducible runtime evidence for the basic components, one representative native text format, and one usable client, on the basis of which the minimal workflow can proceed. Other formats, another client, and real employee delegation are reserved as corresponding follow-up tasks and are not claimed as passed. When a real environment is unavailable, synthetic data may be used to verify the backend, but deployment compatibility and client integration must leave a clear record of what remains incomplete.

### Task 1: Establish backend, identity, taxonomy, and original file storage capabilities

**Files:** Create `pyproject.toml`, `uv.lock`, `main.py`, `config.py`, `contracts.py`, `db.py`, `auth.py`, `catalog.py`, `files.py`, `migrations/001_initial.sql`, `config/taxonomy.json`, `tests/conftest.py`, `tests/contract/test_files.py` (source code paths are all located in the corresponding directories of Section 4).

**Interface:** Takes a trusted `Principal` and a file stream as input; outputs an upload identifier and an immutable reference to the original file. The `Principal` must at least include a server-confirmed `subject_id`, organizational affiliations, and operational permissions; roles are not generated from tool JSON.

- [x] Initialize Git (only if no repository exists yet and implementation is starting), the Python package, database migrations, a health check, and configuration loading; test fixtures establish an isolated database/schema, temporary directories, and synthetic identities for each run, and cleaning production data is forbidden.
- [x] Initialize 8 organizational partitions and 6 purpose categories; stable identifiers are kept separate from display names, and the configuration is adjustable.
- [x] First write tests for streaming upload, size/hash validation, Chinese file names, resumable transfer after failure, unauthorized reads, and duplicate submissions, then run them to confirm the target behavior is not yet implemented.
- [x] Implement controlled upload and submission: a ready-to-submit status is returned only after the file write is complete; original files are stored under random identifiers, with names kept only as metadata; a duplicate idempotency key returns only the original result, while the same key with a different request returns a conflict.
- [x] Implement ownership and file read permissions; cross-department sharing only adds authorization and does not copy content. Uploads may be staged first; a submission without a specified purpose is categorized as "Other materials" and is published after the responsible person reviews the purpose.
- [x] Implement status and file retrieval, ensuring files can still be saved when the parsing service is not running, and that those with permission can retrieve their own submissions.
- [x] Run the tests; once they pass, commit the code and verification records involved in this task.
```powershell
uv run pytest tests/contract/test_files.py -q
```

**Pass criteria:** An upload interrupted mid-transfer produces no complete submission; unauthorized identities can neither read file records nor download; two identical idempotent requests do not create duplicate versions; the hash of the downloaded original file equals the upload hash; the classification matches the name shown in the screenshot.

### Task 2: Publishing, Version Conflicts, Withdrawal, and Restoration

**Files:** Create `src/agico_kb/lifecycle.py`, `src/agico_kb/admin.py`, and `tests/contract/test_lifecycle.py`; modify `files.py`, `auth.py`, `contracts.py`, and the migrations.

**Interfaces:** `publish(version_id, expected_revision)`, `withdraw(document_id, expected_revision)`, and `restore(version_id, expected_revision)` obtain the operator from a trusted context; on success they return the latest `revision`, and on conflict they return `VERSION_CONFLICT`. Also provide withdrawal of one's own unpublished submissions, plus a pending-publication list scoped to one's responsibilities.

- [x] First write the tests: employees cannot publish; a person in charge may publish only within their own responsibility; an old draft cannot overwrite a newer one; ordinary access stops after withdrawal; historical authorized reads; and restoration.
- [x] Within a single transaction, check `expected_revision`, version readiness, and permissions; switch the official pointer, update the status, and write the audit record.
- [x] Return a clear publish result separately for new files, replacement drafts, partially parsed files, and attachment-type files. Publishing a file alone must not pretend that the main body has been parsed.
- [x] Implement the pending list, publish, withdrawal, and restoration as one and the same background business capability, for the Task 5 admin tools to call. The CLI only provides the necessary identity initialization and token revocation; credentials are supplied by protected configuration or the environment, and are not written to logs as command arguments.
- [x] Run the concurrent publish scenario: A and B submit based on the same revision; after A publishes, B's publish returns a conflict and the submission still exists.
- [x] Once verification passes, commit the code and the state transition documentation.

```powershell
uv run pytest tests/contract/test_lifecycle.py -q
```
Key assertions are implemented against the following full scenarios, not merely by checking internal fields:

```text
Employee submits v1 → owner publishes successfully → both submit v2a/v2b based on the same revision
Publishing v2a succeeds → publishing v2b returns VERSION_CONFLICT → the default read is still v2a
Unpublishing succeeds → ordinary search/read/new download requests no longer see it
An owner with permission selects a historical version to restore → the default official version is switched and an audit trail is left
```

### Task 3: Background Parsing, Source Localization, and Recoverable Indexing Tasks

**Files:** Create `ingestion.py`, `embeddings.py`, `worker.py`, `tests/integration/test_ingestion.py`, `tests/integration/test_worker_recovery.py`; modify `files.py`, `contracts.py`, and the migrations.

**Interface:** The job takes an immutable `version_id` and the original file location as input; it produces `chunks`, source locations, capability notes, and processing status. The chunk location format is `{kind, page?, section?, sheet?, range?, slide?, table?}`, with only verifiable fields filled in.

- [x] Prepare new test files with known answers for each representative format; assert on original text, model numbers, numbers, units, table headers, and source locations — not merely that "parsing succeeded".
- [x] Use mature native-format libraries to organize chunks, binding section headings and table context; DOCX uses sections/paragraphs, XLSX uses worksheets/ranges, PPTX uses slide positions, and PDF uses verifiable original page numbers. When a component does not provide sufficient location information, fill it in at the adapter layer; if it still cannot be determined, degrade explicitly — fabrication is forbidden.
- [x] For Excel formulas, preserve the distinction between the formula itself and any existing cached result, and do not claim to have recalculated; report capability limitations when complex graphics are not parsed. Apply OCR to scanned documents only as needed; do not blanket-OCR pages that already contain native text.
- [x] Complete Chinese word segmentation, batched vector processing, and recording of model identifiers; the model identifier, revision, embedding dimension, and chunking version are treated as one group — when any of them changes, rebuild the affected indexes, and queries must not mix embedding spaces from different models.
- [x] Worker claiming uses a database transaction with row locks, after which the file is processed outside the transaction; the lease allows jobs to be re-claimed after a service restart, and the completion write validates the processing generation. The first version runs a single process; parsing subprocesses have timeouts, and on failure the error is retained after a limited number of retries, along with a manual retry entry point.
- [x] When vectorization fails but the text is still valid, keep keyword-based retrieval and mark the status as `partial`; if the entire file fails, the original file is still saved. CAD/3D packages are registered as `stored_only`, preserving associations with dependency packages and auxiliary materials.
- [x] Re-run all parsing scenarios with fresh inputs, simulating mid-processing exits, timeouts, and index write failures; verify that there are no duplicate visible chunks, no half-batch results, and no lost files.
```powershell
uv run pytest tests/integration/test_ingestion.py tests/integration/test_worker_recovery.py -q
```

**Pass criteria:** Regular sample content and sources match the answer checklist; failures are recoverable; unparsed content is truthfully labeled; backend failures do not block reading of published materials.

### Task 4: Precise File Lookup, Hybrid Retrieval, and On-Demand Reading

**Files:** Create `search.py`, `config/search-dictionary.txt`, `tests/integration/test_search.py`, `tests/integration/test_source_read.py`; modify `catalog.py`, `auth.py`, migrations, and contracts.

**Interface:** `SearchRequest` uses the fields from Section 3.3; results uniformly include `document_id`, `version_id`, `title`, `organization_id`, `source_version`, `locator`, `excerpt`, `capabilities`. Reading the original text uses the same `version_id/chunk_id`.

- [x] First write tests for exact model numbers, similar model numbers, Chinese synonymous expressions, in-file search, permission isolation, superseded revisions, and withdrawn documents; separately verify that historical business records are still retrieved, that upload dates are not mistaken for business dates, and that current specifications can be distinguished from historical cases.
- [x] File mode prioritizes exact number/model matching and metadata filtering, then searches file names and descriptions; content mode fuses keyword and vector results. Default Chinese tokenization uses the same rules at ingestion and query time, with business numbers additionally compared exactly. When query vectorization times out or is unavailable, degrade to keyword retrieval and state in the response that the retrieval scope is limited, without dragging down file lookup.
- [x] Use simple rank fusion and snippet deduplication; do not add a separate reranking model for now. Candidates must be constrained by permissions before ranking, and visibility is validated again at final serialization and read time.
- [x] Start with exact vector queries as the correctness baseline; add an HNSW index when data volume requires it. Specifically test the problem of insufficient candidates after department/file permission filtering, using iterative scans or exact retrieval over restricted sets when necessary. [pgvector filtering notes](https://github.com/pgvector/pgvector#filtering)
- [x] Implement pagination and reading of related paragraphs/complete tables, returning `has_more` and continuation locator information; read volume has a protective upper limit, but must not cut off key units and footnotes and then treat the result as complete by default.
- [x] The background tool reuses published background-type documents and is invoked only when the task calls for it; with an empty background, it returns that materials are missing rather than generating company facts. When an explicitly specified scope gets no hits, return no results or a small number of options to choose from. When similar products are requested, allow returning similar candidates while preserving spec differences; when a specific model is requested, do not pass off similar models as exact matches.
- [x] Complete the retrieval benchmark on synthetic representative samples, leaving real enterprise sample quality to Task 6, and confirm that typical questions can find authoritative basis; after passing, commit the code and the retrieval report.
```powershell
uv run pytest tests/integration/test_search.py tests/integration/test_source_read.py -q
```

Minimal SQL-layer retrieval contract (parameters are bound by the program; concatenating user text is forbidden):

```sql
-- document/version/permission filters must be applied to both candidate queries.
-- segmented_text and segmented_query use the same application tokenizer.
to_tsvector('simple', segmented_text)
plainto_tsquery('simple', segmented_query)
-- Exact model/document identifiers are compared as normalized stored fields.
```

**Pass criteria:** no cross-model, cross-version, or cross-permission leakage; references returned by search can be read back to the corresponding source text; when the body text is unavailable, the file can still be located and the restriction explained. The amount of body text returned does not grow directly with the length of the full-library catalog.

### Task 5: MCP and File Transfer Adaptation

**Files:** Create `mcp_server.py`, `client/file_transfer.py`, `tests/integration/test_mcp.py`, `tests/integration/test_file_transfer.py`; modify `main.py`, `contracts.py`, and the identity adaptation.

**Interface:** Register tools according to Section 3.3; MCP and the HTTP file interface call the same business services and authorization checks, and do not maintain state separately.

- [x] Mount Streamable HTTP using the official SDK major version locked in Task 0; verify lifecycle, authentication, concurrent requests, and resource release after the client closes. Avoid mixing SDK 1.x and 2.x examples.
- [x] Write real HTTP MCP session tests first: initialization, listing tools, search, read, submit, status query, and retry of duplicate calls; management tools are shown only to the matching permission and authorized on each call.
- [x] Implement all regular tools from Section 3.3 plus the separately authorized management tools; tool descriptions are short and unambiguous, and errors return actionable next-step information.
- [x] Test prepare upload → upload → submit → retrieve with real binary files. The client transfer script verifies server origin, credential scope, and the download hash, and does not send backend credentials to unknown addresses.
- [x] Verify that passing in an `upload_id` belonging to another identity, withdrawn versions, new download requests, or forged role fields cannot bypass permissions; test that repeated tool calls do not publish or submit twice.
- [x] Check that results do not return Base64 files, long-lived credentials, server-local paths, or a full-library inventory; after passing, commit the adaptation code.
```powershell
uv run pytest tests/integration/test_mcp.py tests/integration/test_file_transfer.py -q
```

The official SDK's current documentation covers 2.x and provides Streamable HTTP; the implementation uses the pinned SDK's API and does not copy directly from older examples. [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

### Task 6: Verify the Agent Uses Enterprise Knowledge in Real Tasks, Then Integration-Test the Employee Entry Points

**Files:** Create new `client/agent-usage.md`, `docs/validation/codex.md`, `docs/validation/accio.md`, `docs/validation/employee-entry.md`; update `integration-contract.md`, and adjust `file_transfer.py` if necessary.

**Input:** The Task 5 services and real clients. **Output:** Reproducible configurations and measured results for each of the two clients, plus evidence of completion for the web and DingTalk paths.

- [ ] Curate a small set of pilot knowledge from the company's existing materials: company/department overviews, representative product or business materials, applicable work standards or templates, and usable experience cases. Prioritize reusing existing files; where necessary, prepare brief background entries and publish them only after confirmation by the responsible business owner. Do not fabricate enterprise knowledge, and do not require each business unit to complete the curation in one go.
- [ ] Configure restricted identities separately for the two clients; verify the standalone terminal and the multi-employee entry delegation separately, and do not use a shared administrator identity to bypass issues.
- [ ] Configure Codex using the official URL-based MCP configuration; provide the token via secure storage or environment variables supported by the client. Use the verified actual service address for configuration parameters, and never write real secrets into the documentation.
- [ ] For Accio, use the MCP configuration and authentication method confirmed on an actual device. If only stdio is supported, prefer a maintainable client-side transport adapter rather than duplicating the backend; if file transfer capability is missing, reuse the existing entry point's file handoff procedure.
- [ ] Have each client run a representative set of enterprise questions and work tasks covering background understanding, fact lookup, application of standards, and experience reference. Issue tasks normally without adding an extra prompt like "search the knowledge base" each time, and check whether the Agent identifies knowledge needs and calls MCP according to the usage convention; manually searching and then pasting results must not substitute for this acceptance check.
- [ ] Check whether the enterprise knowledge actually affects the output: whether company and department positioning is correct, whether product facts are backed by sources, whether work steps comply with applicable standards, whether terminology and templates meet company requirements, and whether experience is applied with current conditions taken into account. Complex judgment, computation, and business processes are handled by the original Agent's existing capabilities.
- [ ] Verify that questions not requiring company knowledge do not trigger meaningless whole-corpus reads, and that insufficient or conflicting knowledge is honestly disclosed. Then verify file content citation, original file retrieval, submission, processing status, and failed unauthorized-access attempts.
- [ ] Verify upload and file retrieval, as well as queries from two employees with different permissions, separately on the web and DingTalk; the backend identity, delivery session, and file recipient must be consistent. A successful MCP call alone does not mean the entry point is complete.
- [x] Write a shared, brief Agent usage convention: when an answer or task involves the company's situation, fetch background, facts, standards, templates, and experience as needed and apply them to the current work; do not wait for the user to instruct a search every time; reuse applicable grounds already present in the current task to avoid repeated loading; distinguish current rules, historical experience, and old revisions; cite key conclusions; state conflicts or gaps; generated experience is submitted only as pending confirmation. Ordinary material content must not change permissions or tool rules. Configure it via each client's existing instruction mechanisms; do not force it to be packaged as a proprietary plugin.
- [x] Record cumulative input/cached-input/output tokens, tool text characters, call counts, and elapsed time for Codex synthetic tasks; do not build a separate context management platform for this step. Independent tokens for tool body text were not metered, and Accio records await verification on an actual device.
**Current evidence:** [Codex general task](../../validation/codex.md) has verified that synthetic background/facts/norms/experience influence output, that unknown questions are not fabricated, and that unrelated computations involve zero knowledge-base lookups. Real enterprise materials, Accio, and the employee entry point have not yet been provided, so Task 6's overall acceptance remains incomplete.

Example Codex implementation configuration command (environment variables are supplied by the actual deployment configuration; when a value is missing, check first — do not write an empty address directly):

```powershell
if (-not $env:AGICO_KB_MCP_URL) { throw 'Missing confirmed MCP address' }
codex mcp add agico-kb --url $env:AGICO_KB_MCP_URL --bearer-token-env-var AGICO_KB_TOKEN
```

Codex business MCP search/read has been verified using a temporary per-run configuration, without modifying the global client configuration; the official address and real identity configuration below still require environment confirmation from step 6. [Codex MCP official documentation](https://developers.openai.com/codex/mcp/)

**Pass criteria:** There is evidence for both Codex and Accio of "acquiring and understanding enterprise knowledge → applying it to answers, judgments, and execution results", covering the four knowledge-use categories above; web pages and DingTalk are verified separately. Merely being able to call MCP, find files, or attach citations does not mean answers and tasks already meet company requirements. If any real entry point is missing, retain the corresponding "not jointly tested" status, continue with other verifiable work, and do not claim the full chain is complete.

### Task 7: Windows Service Operation, Backup, and Restore

**Files:** Create new `deploy/windows/api.xml`, `deploy/windows/worker.xml`, `scripts/install-services.ps1`, `scripts/backup.ps1`, `scripts/restore.ps1`, `docs/validation/operations.md`, `tests/integration/test_restore.py`.

**Interface:** Configuration provides the installation root directory, an independent data root directory, database connections, and protected credential sources; backups output a manifest, database snapshot, files, and checksum values.
- [ ] Register the API and worker with a dedicated service account; verify boot-time startup, restart after failure, log rotation, health status, and a restricted firewall scope; background startup must not pop up interactive windows.
- [x] API online queries and parsing run in separate processes, so CPU-intensive parsing does not block the HTTP event loop; local query vectorization uses a bounded FIFO wait, with waiting and inference sharing a timeout budget, and degradation happens only on timeout or overrun (see Task 8 for the load ratio). The database connection pool is capped at 8 connections per process, rather than directly creating 200 database connections for 200 Agents.
- [ ] Establish an independent backup target covering the database, original files, classification configuration, and any required recovery information. Backup credentials and identity configuration must be kept under controlled storage; backing up only the vector index or treating a copy on the same disk as an independent backup is not acceptable.
- [x] The first-version backup uses an explicit brief write pause: pause writes for new submissions/publications/background completions, wait for in-flight write transactions to finish, back up the database and the corresponding file manifest, then resume writes; reads continue to be served. Record the pause duration and optimize online backup further as actually needed.
- [x] Recovery was first executed against a new isolated database and a new directory; file hashes, official versions, authorization, retrieval, and pending jobs were verified. It rejects an existing database or directory and does not overwrite the active environment.
- [x] In an isolated development environment, actually terminated/restarted the API and worker, and stopped/started the database; verified original files, permissions, versions, and job recovery. Provided a deployment/upgrade/rollback manual with pinned dependencies and models; this does not equate to formal Windows unattended-login startup acceptance — see the process restart record for the specific fault-injection scope.
- [ ] Run recovery tests and a manual Windows restart drill, save evidence, then commit the scripts.

```powershell
uv run pytest tests/integration/test_restore.py -q
```

**Pass criteria:** The service runs normally with no one logged in; backups can be restored to an isolated environment; original files match official query results; the operations record clearly states single-machine recovery capability and the measured recovery time, without claiming high availability.

**Current evidence:** [Operations record](../../validation/operations.md) covers backup recovery, offline model configuration, a Windows service generation script, and actual development database stop/start verification; the stale connection issue on first query has been fixed. Formal service installation, Windows unattended-login restart, TLS, firewall, and an independent backup location remain pending acceptance on the target machine, so Task 7's overall pass criteria are not yet fully met.
### Task 8: Business Trial and 50-Concurrency Acceptance, Recording the Scaling Path to 200

**Files:** Create new `tests/load/locustfile.py`, `tests/load/acceptance-profile.json`, `docs/validation/business-quality.md`, `docs/validation/load-50.md`, `docs/validation/scale-200.md`.

**Inputs:** Approved real materials and trial participants from one business division, a client that has completed integration testing, and fully documented server conditions. **Outputs:** Business acceptance and a reproducible capacity report.

- [ ] First select one business division and fully import its representative materials; each rerun uses newly uploaded / newly processed data, keeping real-sample reports separate from synthetic test reports.
- [ ] Fix a set of representative questions and tasks drawn from actual company work, covering background understanding, factual lookup, applying standards, and referencing prior experience; for example, business introductions, product judgments, producing deliverables according to company requirements, and handling new problems by referencing historical cases, with examples replaced based on actual materials. Also cover file discovery and content reading, identical names across different departments, similar products, historical records and old revisions, no-answer cases, permission denials, and CAD / 3D file access.
- [ ] Have the business owner annotate the relevant enterprise background, factual basis, the standards and templates that should be applied, the conditions under which each case applies, and acceptable answers / deliverables; also evaluate retrieval coverage and how well company requirements are fulfilled, not merely whether materials were found or relying on the model's self-assessment.
- [ ] Before load testing, fill in and freeze `acceptance-profile.json`: servers, material count / total size / chunk count, client version, query mix ratios, response time and success rate targets, test duration, maximum return volume, and parsing concurrency. Thresholds derive from the trial requirements; this plan does not fabricate hardware commitments.
- [ ] By default, prepare 50 closed-loop virtual Agents, each holding one request in flight at a time, split evenly between search / read with no think time, running continuously for 30 minutes after warm-up; these are the starting test configuration and may be adjusted before testing based on real invocation behavior. Report actual throughput; do not treat client connection counts as load.
- [ ] Using varied queries and hard-to-cache data, separately test normal reads, first-time queries, batch import / update in parallel, takedown, and failure recovery; test large file downloads separately, and record model generation time separately.
- [ ] Record p50 / p95 / p99, success rate, vectorization queueing, database and worker backlogs, memory / CPU / disk, and cumulative tokens for successful tasks. Check retrieval quality and permissions both before and after performance testing; do not fake speedups by reducing the number of correct results.
- [ ] Once the 50 load meets the frozen criteria and business quality passes, complete the first-phase acceptance; 200 serves as a mid-to-late-phase target reusing the same test framework. A 200 probe may be performed in the first phase, but do not claim a pass unless the same quality and stability standards are met.
- [ ] Decide scaling based on measured bottlenecks: first adjust indexes / connection pools / query and parsing resources; then consider a standalone model service, additional workers, or retrieval nodes. Do not add deployment components preemptively without bottleneck evidence.
Formal backend load command (first fill in and freeze the configuration, and prepare independent credentials for each virtual user; set `AGICO_KB_LOAD_PROFILE`, `AGICO_KB_LOAD_CREDENTIALS`, `AGICO_KB_LOAD_PROFILE_SHA256`; see the capacity report for the detailed steps). The default total duration is 2 minutes of warm-up plus 30 minutes of statistics, and the CLI parameters must match the frozen configuration:

```powershell
uv run --group load locust -f tests/load/locustfile.py --headless --users 50 --spawn-rate 5 --run-time 32m --host $env:AGICO_KB_TEST_URL --csv .local/load-50
```

This command tests the REST backend and is not equivalent to 50 real MCP sessions or full Agent tasks; formal production capacity must additionally be backed by evidence from the actual client call chain.

**Pass criteria:** Every item must have results and environment records; items that fall short continue to be diagnosed and retested; the first phase must not be delivered as 50-concurrency qualified based solely on functional tests passing. Full-scale 200 acceptance is scheduled separately and is not dragged into unbounded expansion of the first version.

**Current evidence:** [Local 50-identity REST/MCP short test](../../validation/load-50.md) is complete; after fixing the immediate degradation when vector search is busy, both full cycles ran correctly with no degradation. MCP has been verified with 50 actual SDK clients, which does not yet include real Agent reasoning/generation. [Business quality matrix](../../validation/business-quality.md) and [200 scaling path](../../validation/scale-200.md) have been delivered; actual company materials, the target server, complete employee/Agent workflows, and the formal 30-minute acceptance remain outstanding. The formal acceptance checkboxes above are left unchecked.

## 6. Milestones and execution dependencies

| Milestone | Tasks | Checkable results |
|---|---|---|
| M0: Basic pipeline runnable | 0 | Windows base components, one representative format, and one usable client with measured evidence |
| M1: File management usable | 1–2 | Files saved first; classification, permissions, publishing, versioning, and takedown/restore complete |
| M2: Content retrieval usable | 3–4 | Automatic processing, keyword + vector retrieval, reading real sources |
| M3: Enterprise knowledge expansion usable | 5–6 | Codex/Accio apply company background, facts, standards, and experience across various answers and tasks; employee portal integrated separately |
| M4: First-phase delivery | 7–8 | Long-term Windows operation, independent recovery, business quality and 50-concurrency acceptance |
| Mid-to-late phase: Scaling | Task 8's 200 configuration | Scale according to material volume and load and complete the 200 acceptance |
Proceed by dependency, prioritizing the minimal workflow: the necessary save, permission, and publishing capabilities of Tasks 1/2 → the representative format handling and retrieval of Tasks 3/4 → one client fetching and applying enterprise knowledge of Tasks 5/6. Afterwards, fill in the remaining capabilities of each task and all acceptance criteria; file acquisition is also verified as part of the minimal workflow. When a real client environment is temporarily unavailable, work may continue independently, without falsely reporting joint debugging as complete. The schedule is estimated based on actual samples, the extent of entry-point changes, and staffing arrangements; development does not require all details to be fully investigated first.

## 7. Testing and Delivery Rules

- For permissions, versioning, original-file preservation, processing recovery, and retrieval quality, first write meaningful failure scenarios, then implement and run the corresponding tests. Minor changes to configuration documents require only necessary validation; do not write mirror-style tests.
- Each round of testing uses an isolated environment and new inputs; explicitly mark the fault-injection points for idempotency/recovery scenarios. Real files are not uploaded to unconfirmed external models or testing services.
- Each task forms one reviewable commit after passing local verification; if there is no Git yet, initialize it when starting to implement the task, and do not create a code repository merely for writing this plan.
- Completed delivery includes source code with locked dependencies, Windows deployment scripts, classification configuration, client integration instructions, backup and recovery documentation, and testing evidence.
- Record the supported parsing scope and its defects; do not claim that all PDF tables, scanned documents, or CAD/3D files are understood; original-file management remains independently functional at all times.
- If technology choices, interfaces, or permission implementations change, update the design baseline, the affected content in the classification scheme, and this plan synchronously and comprehensively; different clients do not result in different enterprise-knowledge rules.

## 8. Requirement Coverage Self-Check

| Confirmed Requirement | Corresponding Tasks |
|---|---|
| Agent enterprise-knowledge extension: understanding company knowledge and applying it to answers and tasks | 4, 6, 8 |
| Historical business records and cases are searchable, distinguishing business history from old revisions | 0, 2, 4, 6, 8 |
| Employees upload, find materials, ask about content, and fetch files | 1, 3–6 |
| Windows server; Codex/Accio | 0, 5–7 |
| 7 screenshot groups + company public knowledge; usage-based classification | 1, 4 |
| Files are saved first, then parsed and vectorized in the background | 1, 3 |
| Low waste, on-demand reading, not strictly gated by tokens | 4–6, 8 |
| Free submission, authorized publishing, version conflicts | 1–2 |
| Trusted employee identity and cross-department permissions | 0–2, 4–6 |
| CAD/3D full-package preservation, auxiliary-material version association | 1, 3–4 |
| Unpublish and restore, failure retry, independent backup | 2–3, 7 |
| 50 in the initial phase; 200 in the mid-to-late phases | 8 |
| Reuse mature components; avoid rebuilding entry points and over-engineering | 0 and global constraints |
Overall review conclusion: The framework in which the two types of requirements share a common knowledge and file backend remains unchanged; the 9-step local development and wrap-up have been implemented, while the missing items—real client, production server, and business acceptance—are kept in an incomplete state. Checkboxes distinguish local evidence from formal acceptance, and synthetic tests are not used as a substitute for live integration testing.
