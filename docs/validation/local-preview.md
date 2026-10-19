# Local Test Knowledge Base: Directory and Real-File Verification

2026-09-16. Code, test materials, and runtime data have been separated. This is currently a local-machine test service: no Windows auto-start service has been installed, and LAN access is not open.

## Directories

| Path | Purpose |
|---|---|
| `D:\AGICO-KNOWLEDGE-STORE` | Source code, virtual environment, PostgreSQL executables, test tools |
| `D:\TestFiles` | Licensed materials; source files unmodified |
| `D:\EnterpriseKnowledgeBase\originals` | Original files managed by the system; do not manually rename or overwrite them |
| `D:\EnterpriseKnowledgeBase\postgres` | Current database data directory, containing the development test database and the separate `agico_preview` preview database |
| `D:\EnterpriseKnowledgeBase\models` | Offline vector models |
| `D:\EnterpriseKnowledgeBase\logs` | Database, API, and worker logs |
| `D:\EnterpriseKnowledgeBase\config` | Runtime configuration and test tokens protected by NTFS permissions; not committed to Git, not printed |
| `D:\EnterpriseKnowledgeBase\validation` | Migration verification, real-file test records, and download-verification copies |

During the copy performed with services stopped, all 2,027 database files and 14 model files were compared one by one via SHA-256. The old `.local/pgdata` was kept as a pre-cutover copy and is no longer in use; it is not a continuously updated backup. The old `.local/models` is temporarily retained for the existing automated tests.

When the local PostgreSQL is started from a Chinese path, the path encoding it returns is inconsistent with the UTF-8 client. For this reason, `.local/runtime-postgres` is set up as an NTFS Junction (path mapping) pointing to `D:\EnterpriseKnowledgeBase\postgres`; the physical data remains in the user-specified directory. When migrating to another machine, the mapping and configuration must be recreated — copying only the code repository is not enough.

Non-sensitive path configuration is located in `.local/test-layout.json`. Materials and runtime data are not placed in Git. The data directory and credentials are accessible only to the current Windows user, SYSTEM, and Administrators. The service runs under the currently logged-in account; the preview database uses a separate non-superuser, `agico_preview`.
## Start/Stop and Access

Run from the development directory (requires the already-installed `load` dependency group):

```powershell
./scripts/test-runtime.ps1 status
./scripts/test-runtime.ps1 stop
./scripts/test-runtime.ps1 start
```

`start` automatically starts the database and then launches the API and worker in the background; it refuses duplicate starts or the use of an already-occupied port 8765. `stop` only stops process trees whose saved PID, start time, and launch script pass verification; the database continues serving development testing. For a full shutdown, stop the API/worker first, then run `./scripts/dev-db.ps1 stop`.

- API: `http://127.0.0.1:8765`
- MCP: `http://127.0.0.1:8765/mcp`
- Health check: `http://127.0.0.1:8765/health/ready`
- API debug docs: `http://127.0.0.1:8765/docs`; this is not the employee web portal.

Business requests still require separate tokens. The test admin and test reader tokens are located in the protected `config/test-tokens.json` and are valid for 30 days; they are not official employee accounts and have not yet been written into the Codex, Accio, or DingTalk configuration. After the computer restarts, manual startup is required. Each startup overwrites the current API/worker logs; long-term log rotation is handled by the production deployment configuration.

## Real Sample Results

The source material directory contains 349 files in total, including temporary lock files, scripts, and archives. This round selected one file each of Word, Excel, PPT, PDF, and CAD; no full import was performed. The owning business unit has not been confirmed, so the **private test materials** under the "Company Public Knowledge" category are used, which are not open to all employees.

| Type | Result |
|---|---|
| DOCX | Body text is searchable and readable; unresolved warnings are retained for headers/footers, footnotes, etc. |
| XLSX | Parsed normally, retaining worksheets and cell ranges |
| PPTX | Text is searchable and readable; unexplained warnings for images/graphics are retained |
| PDF | Text is searchable and readable; warnings regarding images and long-content vector representation limits are retained |
| DWG | Original file only; no claim of being able to understand drawing contents |
All 5 samples passed the upload/download SHA-256 consistency checks against the source files, library-wide filename lookup, MCP file lookup, and private-material unauthorized-access rejection checks. The 4 samples with body content passed library-wide content retrieval (target within the top 5 results), reads with source attribution, and MCP/HTTP body content consistency checks. These results validate the workflow and basic retrieval; they do not constitute full acceptance of semantic, translation, or engineering content quality.

Final fresh upload batch: `23730fa1ca6b45c4bf90b008c60c4679`, with details at `D:\EnterpriseKnowledgeBase\validation\23730fa1ca6b45c4bf90b008c60c4679\results.json`. The 10 versions from the first two rounds have been taken down via the admin interface, with audit records and original files retained; this batch keeps 5 items. Later, 3 files each of Office/PDF/CAD were added, 9 in total, bringing the current valid versions to 14 — see [Small File Ingestion Test](small-files.md).

Real XLSX files exposed two issues, which have been fixed: missing optional worksheet range metadata, and internal `.blob` filenames being rejected by openpyxl's extension check. Files are now read as file streams, and the 1,000,000-cell protection over the actual traversal range is retained. Six regression tests were added covering normal, empty, and oversized ranges under both `.xlsx` and `.blob` storage names; the old implementation was first confirmed to fail before the fix.

The startup scripts, path switching, and XLSX fixes in this round have passed an independent read-only targeted review, with no unresolved Important/Critical issues.

The full test suite for this batch at the time was **134 passed / 2 skipped** (136 passed after the later small-file test fix); the two explicit failure-and-restart tests were not repeated this round, having passed separately in the previous batch. PowerShell syntax checks, service start/stop/restart, offline models, new-directory read/write, and Ruff checks all passed. The original-file reconciliation checked 15 references; missing, corrupted, unreferenced, and stale temporary files were all 0.

## Follow-up Boundaries

Do not directly copy the running PostgreSQL folder to serve as a consistency backup. Continue using the existing database plus original-file backup tools; place formal backups on a different disk or another device. This round did not designate an independent backup location, and no scheduled backups have been established.

Still needed: real business quality evaluation, confirmation of material ownership, Accio / existing web and DingTalk integration, and formal acceptance of the Windows service, network, TLS, and capacity.
