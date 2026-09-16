# Windows single-data-folder server setup

**Goal:** after cloning, an administrator runs `./setup.ps1 -DataRoot 'D:\EnterpriseKnowledge'`. Dependencies, empty database, offline models, credentials and automatic background services are prepared without company files entering Git.

**Architecture:** preserve native Windows Python/PostgreSQL/pgvector and existing API/worker. `.runtime` holds downloaded executables and virtualenv beside code; the chosen data folder holds originals/postgres/models/logs/config/services. A managed ASCII junction `.runtime/data` points to the data folder. Three passwordless Windows virtual service accounts run PostgreSQL, API and worker; no LocalSystem application fallback. Network defaults remain loopback. Cross-machine HTTPS/domain/firewall and per-employee identity are separate integration settings.

**User authorization:** user requested GitHub delivery and data-folder-only server initialization; target repository `https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git`. Continue existing dedicated development branch. Never replace or migrate the current preview implicitly. Validate on a new isolated deployment folder and different service IDs/ports, then remove only owned verification services.

## Shared implementation contract

- Root `setup.ps1` delegates to `scripts/bootstrap-server.ps1` (Windows PowerShell 5.1 compatible).
- Parameters: mandatory `DataRoot`; optional `ApiPort=8765`, `DatabasePort=15432`, `ServicePrefix=AgicoKb` for isolated validation. AppRoot is repository root, must be ASCII absolute path without quotes; DataRoot may contain Chinese/spaces, must be local absolute directory outside AppRoot and not a drive root.
- Bootstrap uses pinned HTTPS archives and hashes in `deploy/windows/bootstrap-lock.json` plus exact conda package URLs in `deploy/windows/postgres-explicit.txt`. No arbitrary downloaded script execution or dependency on developer paths.
- `.runtime/setup-target.json` records canonical app/data roots, ports and service prefix. A nonempty DataRoot without matching `config/deployment.json` is refused, except a fresh Windows `desktop.ini`. Fresh creation protects DataRoot to operator/SYSTEM/Administrators before secrets are written.
- `DataRoot/config/deployment.json` schema: `schema_version:1, app_root, data_root, api_port, database_port, service_prefix, stage` (`preparing`, `initialized`, `installed`). Existing marker must match requested paths and ports. Do not reset credentials or reinitialize an existing cluster.
- Bootstrap prepares `.runtime/uv/uv.exe`, `.runtime/micromamba.exe`, `.runtime/postgres/Library/bin`, `.runtime/venv/Scripts/python.exe`, `.runtime/winsw.exe`. Python 3.12.14 and `uv.lock` production dependencies; portable Python under `.runtime/python`.
- Then invoke `scripts/initialize-server.py --app-root ... --data-root ... --api-port ... --database-port ... --service-prefix ...`. It initializes owned PostgreSQL via `.runtime/data/postgres`, migrates, preloads vector and OCR models, records model identity. It stops its own temporary cluster before returning.
- Initialization writes operator-only `config/runtime.json`, `config/maintenance.json`, `config/initial-access.json` (bootstrap-admin token, expiry, MCP URL). Non-superuser database role for runtime. Bootstrap admin is a local initialization identity, not a shared employee identity. Secrets never appear in argv/logs/Git.
- Finally invoke `scripts/configure-server-services.ps1 -AppRoot ... -DataRoot ...`. Reads deployment marker; service names `<prefix>Database`, `<prefix>Api`, `<prefix>Worker`. Validates any existing service ownership and refuses unrelated services. Owns protected runtime per-service configs and XML, automatic start/recovery, ACLs, ordered health check. Supports `-Action Install|Start|Stop|Status|Uninstall`; Uninstall removes only validated service registrations, never data.
- New `deploy/windows/launch-server.ps1` runs api/worker from per-service config using actual virtual service identity and marker's port. API binds only 127.0.0.1. PostgreSQL wrapper uses direct postgres executable, graceful pg_ctl stop.

## Tasks

- [x] 1. Pinned bootstrap/download/install entry with guards and dependency validation.
- [x] 2. Database/model/credential initializer and virtual-account service configuration; fresh isolated actual install/start/stop/restart checks, fresh upload/search/MCP test, rollback boundaries.
- [ ] 3. Review, repository hygiene/history scan, clean-clone instructions, GitHub push to empty target without force. Verify remote revision and separately preserve local preview state.

## Acceptance and limits

- Existing preview and original test files stay intact; no permanent verification services or registrations remain after the isolated test.
- Re-running setup on the owned installed root validates/reuses existing installation rather than regenerating identities or overwriting data. Unmanaged/existing data are rejected.
- No claim of production-server certification, LAN/TLS readiness or 200-agent capacity. First install requires supported Windows x64, administrator rights, internet and sufficient disk space; initialization choices beyond DataRoot have defaults.
- Git contains application source, locked dependency metadata, scripts and docs only. No originals, database, models, credentials, runtime logs or personally configured machine paths in active defaults.
