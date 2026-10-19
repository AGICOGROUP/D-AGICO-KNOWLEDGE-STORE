# Windows Operations and Isolated Recovery

## Current Evidence and Boundaries

This round performed live tests of logical backup, new-database, and new-directory restoration on the local machine with PostgreSQL 16.15 / pgvector 0.8.6; both a random business schema and the production default `public` schema were covered. Each test created fresh synthetic source artifacts and isolated objects. Tests covered source-artifact bytes, private permissions, official active versions, keyword/vector retrieval, queued/running jobs, and lock failures. The test directories are on the same disk; this is only restore verification, not independent disaster recovery.

The local real-database stop/start drill passed: while the database was stopped, live=200 and ready=503; after startup, ready=200, and the first ordinary authenticated read, search, source artifacts, and publication/job counts were all correct. The drill discovered and fixed stale connections in the idle connection pool: a psycopg_pool health check is executed every time a connection is checked out, automatically discarding connections that have been disconnected. The test control subprocess uses DEVNULL on Windows to prevent the new database process from inheriting the capture pipe, which would cause the test to wait without ever finishing. This test requires explicitly setting `AGICO_TEST_RESTART_DB=1` and may only be run when no one is using that local development cluster.

The service scripts have generated XML and passed PowerShell syntax checks. The service has not yet been installed on the designated server, nor have the firewall been modified, accounts created, or Windows restarts performed. Unattended start with no one logged in, restart after real service failure, log rotation after reaching the threshold, the TLS proxy, and cross-disk/cross-machine disaster recovery still require acceptance testing on the target server. Do not claim on this basis that production long-term operation acceptance has been completed.

## Installation Directory, Accounts, and Dependencies

1. Place this repository and `uv.lock` in a fixed absolute directory (example `D:\Agico\App`) and run `uv sync --frozen --no-dev`. Python is pinned to 3.12; the deployment retains source code, migrations, and lock files. For upgrades, create a new application version directory and keep the previous version; do not update the virtual environment while the service is running.
2. Have an administrator prepare a dedicated regular local or domain account (e.g. `SERVER\agico_kb`), grant it "Log on as a service", and do not add it to the Administrators group. Using an existing gMSA is optional; the system does not require Active Directory. The API and worker use the same dedicated account; PostgreSQL uses a separate database account. The runtime account may only read the application and models and write source artifacts and logs; it must not be able to modify the program. Store the maintenance account separately, with permissions to create the restore database / install the vector extension; do not put it into the service configuration.
3. PostgreSQL 16 / pgvector uses a verified package and is deployed separately according to its official service procedure. This script only manages the API/worker; the actual PostgreSQL Windows service, disks, memory, and backup network target must be confirmed by operations. The database connection pool allows at most 8 connections per process, the parsing subprocess is separate from the API, and vector queries run in only one task at a time.
4. Obtain the exe from the official WinSW **v2.12.0** release and independently record the real SHA-256. The scripts do not download the wrapper; before installation, verify the exe path, SHA-256, and FileVersion. Do not treat the example hash as a real signature.
5. Create a protected local JSON from `deploy/windows/runtime.example.json`; fill in the real DSN, absolute source-artifact/model directories, and the model identity. Do not commit this file, print the configuration in a terminal, or put the DSN on a command line. Configure the DACL to disable inheritance, allowing only the dedicated account (read), SYSTEM, and Administrators; the parent directory should be modifiable only by administrators to prevent replacement of the configuration. `config-loader.ps1` checks the DACL on every load and refuses to start if the requirements are not met.

The ACL example below should be executed by an administrator after replacing the real path and account:

```powershell
icacls D:\Agico\Secrets\runtime.json /inheritance:r
icacls D:\Agico\Secrets\runtime.json /grant:r 'SERVER\agico_kb:R' '*S-1-5-18:F' '*S-1-5-32-544:F'
```

Check for and delete any other pre-existing explicit grants; `/grant:r` does not automatically remove ACEs belonging to other accounts. The application/script/service XML directories should likewise deny write access to business accounts. Grant permissions separately for source artifacts, backups, and credentials; backups contain access token hashes and the original enterprise text, and must be stored encrypted under controlled conditions, with the key kept separately.

## Model Preparation and Offline Operation

The first-time online preparation should be executed separately in an administrator maintenance window:

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.embeddings --cache D:\AgicoModels
```

Write the complete model identity from the output into the protected configuration as `AGICO_KB_EXPECTED_MODEL_IDENTITY`, and set `AGICO_KB_MODEL_OFFLINE` to the string `true`. The model identity includes the model name, local model file hash, dimensions, fastembed version, and implementation identifier. Service startup requires offline mode and identity pinning; if the fingerprint does not match, semantic retrieval/vector ingestion fails, while keyword retrieval and the original text remain available. By default, the code tolerates network access in development mode; production forces offline mode via the startup script. The model cache requires a separate, controlled archive restored to an explicit directory; this logical backup does not embed the large model files. Upstream downloads do not guarantee that the same model can be obtained in the future, so keep the model archive and lock files, and after restoration verify the identity before reopening the service.
## Generating and Installing Services

By default, only `AgicoKbApi.xml` / `AgicoKbWorker.xml` are generated under a new directory, without modifying services or accounts:

```powershell
$install = @{
  PythonExe = 'D:\Agico\App\.venv\Scripts\python.exe'
  AppRoot = 'D:\Agico\App'
  ConfigFile = 'D:\Agico\Secrets\runtime.json'
  OutputDirectory = 'D:\Agico\Services-v1'
  ServiceAccount = 'SERVER\agico_kb'
}
D:\Agico\App\scripts\install-services.ps1 @install
```

When actually installing after review, use another output directory that does not yet exist, and additionally pass: `-Install -TargetComputer SERVER -AccountProvisioned -WinSWExe <verified absolute path> -WinSWSha256 <actual 64-character hash> -Credential (Get-Credential 'SERVER\agico_kb')`. Regular account credentials are handed to SCM's `New-Service` via a PowerShell `PSCredential` and are not written into XML/argv; gMSA needs no Credential. Turning passwords into script strings is forbidden. The install script requires an explicitly elevated administrator session, refuses services that already exist, does not automatically start services, and does not automatically delete artifacts on failure. The explicit install branch for regular accounts also registers an Application event source for the service ID via .NET `System.Diagnostics.EventLog` (if it does not already exist), consistent with [WinSW 2.12.0's native installation behavior](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/src/WinSW/Program.cs); there is no need for the `New-EventLog` cmdlet that PowerShell 7 lacks. The default generation mode does not access or create event sources; actual event log writing still requires acceptance testing on the target server.

The generated configuration specifies absolute paths, Automatic delayed start, restart after 10 seconds (never restarts Windows), a 30-second stop wait, a 10 MB threshold per stdout/stderr log, and 8 rotated files. The wrapper's own logs should also be included in disk monitoring. Use the official [WinSW 2.12.0 XML configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/xmlConfigFile.md) and [logging configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/loggingAndErrorReporting.md); actual SCM installation and rotation remain to be tested on the target machine.

After configuration validation, model preparation, and database migration are complete, an administrator runs `Start-Service AgicoKbApi,AgicoKbWorker`. The startup script does not bypass the organization's execution policy; sign it / remove the trusted-file block as required by the organization. The service console will not pop up an interactive desktop window.

## Health, Network, and Routine Checks

- The API binds to `127.0.0.1:8765` by default; the local `/health/live` endpoint only reports on the process; `/health/ready` uses a separate connection to perform a 2-second connection/query time-limited check, returns 503 on failure, and does not occupy application pool wait slots. ready does not mean the model is loaded, the queue is drained, or full business permission acceptance has passed.
- Enterprise access uses explicit hostnames and a trusted TLS reverse proxy; actual hostnames are configured in the allow list, and the proxy preserves authorization and streaming MCP behavior. Only enterprise sources are allowed to reach the proxy over HTTPS; PostgreSQL and application ports are restricted to localhost/necessary sources. Specific certificates, proxy, addresses, and firewall are not yet configured; there are no invented public entry points.
- Check failed/backlogged jobs, free disk space, originals/backup capacity, log growth, the most recent successful backup, and restore times. Model unavailability will not damage originals; after processing repeatedly fails and reaches the limit, retry requires personnel with the necessary permissions.
- Re-authentication is required after the API restarts; workers use PostgreSQL leases and generation fencing, and expired workers must not commit stale results. During a service interruption, a wait for lease expiration may occur; a normal recovery does not forcibly reset the existing lease.

## Read-Only Reconciliation of Original Files

After loading the protected configuration above, you can perform a read-only reconciliation of the current database against the original files directory:

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.operations reconcile --stale-part-age-seconds 86400 --timeout 30
```

The command returns JSON, listing missing originals, originals whose size or SHA-256 does not match, `.blob` files without complete/submitted upload references, and `.part` candidates whose last modification time exceeds the specified number of seconds; the default age is 24 hours. Only files named by the application in the current flat originals directory are scanned; the parsing temporary directory is out of scope. Paths are returned only as relative file names, and no database credentials are returned; the command does not delete, rename, or repair any files.
The scan reuses the backup write-pause lock: it waits for in-flight upload transactions to complete before obtaining a stable database reference; new uploads may keep streaming into temporary files, but final renaming and registration must wait until this reconciliation finishes and be retried. Reads remain available. Stale `.part` files may still belong to uploads paused by the lock, so they are only candidates for manual verification and must not be deleted on that basis alone; database/disk modifications that bypass the application are not protected by this lock. The results correspond to the time window of this scan; after the lock is released, the live state may change.

By default it waits up to 30 seconds for write transactions, and the scan budget after acquiring the lock is also 30 seconds; there is checking time per file and per 1 MiB hash block, and an underlying single disk read operation cannot be forcibly interrupted, so this is not a hard guarantee on total elapsed time. On timeout, unreadable files, or interruption of the lock connection, it returns failure, does not output a complete report, and releases the write pause. For large databases, `--timeout` can be adjusted explicitly during a maintenance window, with frequency scheduled according to the reported `pause_seconds`. When anomalies are found, operations staff verify the originals and the backup; no automatic cleanup is performed.

## Backup

Configure in advance a new target directory on an **independent disk, a controlled network location, or another host**. The task does not automatically select a disk or claim that a copy on the same disk constitutes disaster recovery. Use the protected configuration to load the DSN into the current process environment; avoid exposing the configuration via PowerShell Transcript/debug echo. The backup maintenance configuration is loaded through the same `Import-ProtectedKbConfig`; the service configuration does not contain maintenance credentials.

```powershell
. D:\Agico\App\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile D:\Agico\Secrets\runtime.json -Account 'SERVER\agico_kb'
D:\Agico\App\scripts\backup.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915
```

The backup proceeds by queuing to acquire the schema-dedicated exclusive advisory lock, waiting for write transactions already underway, copying and verifying the complete/submitted originals referenced by the database, running a schema-scoped custom-format `pg_dump`, recording the migration version/category/model identity/dependency locks and hashes, and then releasing the lock. New writes — including final upload rename, commit, publish/unpublish/grant access, file associations, identity tokens, and worker writes — all quickly return a retryable `MAINTENANCE` 503; reads and authentication continue, temporary upload streams may proceed, but final submission must be retried. Write transactions wait at most 30 seconds, and pg tools default to 300 seconds; on timeout, writes are resumed and the incomplete state is preserved. If background parsing encounters the pause, it will be recovered through the retained leases and retries.

The `manifest.json` manifest becomes a completion marker only after the original lock connection has been released successfully; check `pause_seconds` in the manifest. A terminated lock connection cannot produce a completed backup. Missing originals or hash errors cause failure. `.part` files, parse directories, and unreferenced orphan files do not count as backups of completed originals; orphans are not deleted automatically. Direct database/file modifications that do not pass through the application write gate are not protected, and such maintenance writes are prohibited during backup. With a large number of originals, the pause includes the full copy time; monitor the actual duration and, if necessary, design storage snapshots as well.

## Restore to a New Target and Switchover

Only trusted backups from controlled sources are accepted; SHA-256 can detect accidental corruption but is not a substitute for signature-based authenticity verification, and a PostgreSQL archive contains executable SQL. The restore account has only the `AGICO_KB_MAINTENANCE_URL` environment variable injected (loaded from a separate protected configuration); there is no DSN/password on the command line.

```powershell
D:\Agico\App\scripts\restore.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915 -NewDatabase agico_recovery_20260915 -NewStorage D:\AgicoRecovery\run-20260915
```

Restore first verifies the manifest, all hashes, and paths, then creates the new database using template0; an existing database or directory is always rejected. It pre-installs public.vector and uses `pg_restore --no-owner --no-acl`; the CREATE SCHEMA entry for public is skipped precisely, avoiding a conflict with the public schema bundled with template0. Restore checks the original files referenced by the database against the files. Business permissions/identity/active versions are preserved along with the table data; database roles and filesystem ACLs are established separately by operations, and the source database's owner/ACL are not restored.

Only within the newly isolated database are running jobs re-queued, generation/lease cleared, and attempt counts reset; published versions and the existing active generation are preserved. queued jobs remain in the queue, and failed/cancelled statuses are preserved. A failed restore leaves `RESTORE_INCOMPLETE` and the new database/directory for inspection, and the startup script refuses to use that directory; it never automatically DROPs or overwrites production.
After starting the new configuration in isolation, verify `/health/ready`, the specified original bytes, normal/cross-business-unit/private permissions, official versions, and search references; start the worker and confirm that legacy jobs are processed correctly. Verify the model archives and identity, and re-issue or revoke any necessary tokens. After manual review, stop the old writes and cut over the traffic; until verification is complete, the old service continues to use the original database. If new writes occur after the cutover, a rollback will involve the newly added data, so the old snapshot cannot simply be restored. For a pure code upgrade, the data may stay in place and you can revert to the previous application directory, but only do so when the migration is backward compatible and verification has passed.

## Final Acceptance Checklist for the Target Servers

Record the actual servers, service identities, software hashes, independent backup locations, and execution times; perform a Windows reboot with no one logged in, API/worker process exit and restart, ready recovery after temporary database unavailability, log rotation, TLS/firewall, and isolated recovery. Without this target-machine evidence, do not check off the full production run pass criteria in Task 7.
