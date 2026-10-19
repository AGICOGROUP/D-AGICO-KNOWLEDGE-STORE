# Windows Operations and Isolation Recovery

## Current Evidence and Boundaries

In this round, logical backup and restore into a new database and a new directory were tested for real on a local machine running PostgreSQL 16.15 / pgvector 0.8.6; both a random business schema and the production default `public` were covered. Each test created fresh synthetic originals and isolated objects. Tests covered original bytes, private permissions, the official active version, keyword/vector retrieval, queued/running jobs, and lock failures. The test directory is on the same disk; this is restore validation only, not independent disaster recovery.

The stop/start drill on the real local database passed: with the database stopped, live=200 and ready=503; after startup, ready=200; the first ordinary authenticated read, search, and the counts of originals and publications/jobs were all correct. The drill found and fixed stale connections in the idle connection pool: every checked-out connection runs a psycopg_pool health check, and broken connections are discarded automatically. On Windows, the test control subprocess uses DEVNULL so that the new database process does not inherit a captured pipe, which would leave the test waiting indefinitely. This test requires explicitly setting `AGICO_TEST_RESTART_DB=1` and may only be run when nobody is using that local development cluster.

The service scripts have generated XML and passed PowerShell syntax checks. The service has not yet been installed on the designated server; no firewall modifications, account creation, or Windows restarts have been performed. Startup with no user logged in, restart after real service failure, log rotation after reaching thresholds, TLS proxying, and cross-disk/cross-machine disaster recovery still require acceptance on the target server. Do not claim, on this basis, that long-term production operation acceptance has been completed.

## Installation Directory, Accounts, and Dependencies

1. Place this repository and `uv.lock` in a fixed absolute directory (example `D:\Agico\App`), and run `uv sync --frozen --no-dev`. Python is pinned to 3.12; the deployment keeps source code, migrations, and the lock file. For upgrades, create a new application version directory and keep the previous version; do not update the running service's virtual environment while the service is running.
2. Have an administrator prepare a dedicated, regular local or domain account (e.g. `SERVER\agico_kb`), grant it "Log on as a service", and do not add it to the Administrators group. Using an existing gMSA is optional; the system does not require Active Directory. The API and the worker use the same dedicated account; PostgreSQL uses a separate database account. The runtime account may only read the application and models and write originals and logs; it must not modify the program. Keep the maintenance account stored separately, with permission to create the restore database / install vector; do not put it into the service configuration.
3. PostgreSQL 16 / pgvector uses verified packages and is deployed separately following its official service procedures. These scripts only manage the API/worker; the actual PostgreSQL Windows service, disks, memory, and backup network target must be confirmed by operations. The database connection pool holds at most 8 connections per process; parsing runs in subprocesses separate from the API, and only one task queries vectors at a time.
4. Obtain the exe from the official WinSW **v2.12.0** release and independently record its real SHA-256. The scripts do not download the wrapper; verify the exe path, SHA-256, and FileVersion before installation. Do not treat the example hash as the real signature.
5. Create a protected local JSON from `deploy/windows/runtime.example.json`; fill in the real DSN, the absolute originals/models directories, and the model identity. Do not commit this file, print the configuration in a terminal, or put the DSN on the command line. Configure the DACL to disable inheritance and allow only the dedicated account (read), SYSTEM, and Administrators; the parent directory may be modified only by administrators to prevent the configuration from being replaced. `config-loader.ps1` checks the DACL on every load and refuses to start if it is not satisfied.
The ACL example is executed by the administrator after replacing the real paths and accounts:

```powershell
icacls D:\Agico\Secrets\runtime.json /inheritance:r
icacls D:\Agico\Secrets\runtime.json /grant:r 'SERVER\agico_kb:R' '*S-1-5-18:F' '*S-1-5-32-544:F'
```

Check for and remove any other existing explicit grants; `/grant:r` does not automatically remove ACEs belonging to other accounts. The application/scripts/service XML directories should likewise deny write access to business accounts. Grant permissions separately for originals, backups, and credentials; backups contain access token hashes and the original enterprise text, and must be stored under controlled encryption with the keys kept elsewhere.

## Model Preparation and Offline Operation

First-time online preparation is performed separately during an administrator maintenance window:

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.embeddings --cache D:\AgicoModels
```

Write the full model identity output into the protected configuration `AGICO_KB_EXPECTED_MODEL_IDENTITY`, and set `AGICO_KB_MODEL_OFFLINE` to the string `true`. The model identity includes the model name, local model file hash, dimensions, fastembed version, and implementation identifier. Service startup requires offline mode and identity pinning; when the fingerprint does not match, semantic retrieval/vector ingestion fails, while keyword retrieval and the original text remain available. By default, the code is compatible with networking in development mode; in production, the startup script forces offline mode. The model cache requires a separately controlled archive restored to an explicit directory; this logical backup does not embed the large model files. Upstream downloads cannot guarantee the same model will be obtainable in the future, so keep the model archive and lock file, verify the identity after restoration, and only then open the service.

## Generating and Installing Services

By default, only `AgicoKbApi.xml` / `AgicoKbWorker.xml` are generated into a new directory, without modifying services or accounts:

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

For the actual installation after review, use another output directory that does not yet exist and additionally pass in: `-Install -TargetComputer SERVER -AccountProvisioned -WinSWExe <verified absolute path> -WinSWSha256 <real 64-character hash> -Credential (Get-Credential 'SERVER\agico_kb')`. Credentials for a regular account are handed to SCM's `New-Service` via a PowerShell `PSCredential`, not written into XML/argv; gMSA requires no Credential. Turning the password into a script string is prohibited. The installation script requires an explicitly elevated administrator session, rejects pre-existing services, does not automatically start the service, and does not automatically clean up artifacts on failure. The explicit installation branch for a regular account also registers an Application event source for the service ID via .NET `System.Diagnostics.EventLog` (if it does not already exist), consistent with [the native installation behavior of WinSW 2.12.0](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/src/WinSW/Program.cs); it does not need the `New-EventLog` cmdlet that is missing from PowerShell 7. The default generation mode neither accesses nor creates event sources; actual event log writing must still pass acceptance on the target server.
The generated configuration specifies absolute paths, Automatic (delayed) start, restart after 10 seconds (never reboot Windows), a 30-second stop wait, a 10 MB threshold for each stdout/stderr log with 8 rotated files. The wrapper's own logs should also be included in disk monitoring. Use the official [WinSW 2.12.0 XML configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/xmlConfigFile.md) and [logging configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/loggingAndErrorReporting.md); actual SCM installation and rotation remain to be verified through testing on the target machine.

After configuration validation, model preparation, and database migration are complete, an administrator runs `Start-Service AgicoKbApi,AgicoKbWorker`. The startup script does not bypass the organization's execution policy; sign or unblock trusted files as required by the organization. The service console will not pop up an interactive desktop window.

## Health, Network, and Routine Checks

- The API binds to `127.0.0.1:8765` by default; the local `/health/live` endpoint only reports on the process; `/health/ready` uses a separate connection to perform a time-limited 2-second connection/query check, returning 503 on failure, without occupying application pool wait slots. ready does not mean the model is loaded, the queue is drained, or full business-permission acceptance has passed.
- Enterprise access uses explicit hostnames and a trusted TLS reverse proxy; actual hostnames are configured in the allowlist, and the proxy preserves authorization and streaming MCP behavior. Only enterprise sources are allowed to reach the proxy over HTTPS; PostgreSQL and application ports are restricted to localhost/necessary sources. Specific certificates, proxy, addresses, and firewall have not yet been configured; there is no fictitious public entry point.
- Check failed/backlogged jobs, free disk space, originals/backup capacity, log growth, the most recent successful backup, and restore time. Model unavailability will not damage originals; after processing repeatedly fails up to the limit, retry requires personnel with permission.
- Re-authenticate after API restart; workers use PostgreSQL leases and generation fencing, and expired workers must not submit stale results. During a service interruption, the system may wait for leases to expire; normal recovery does not forcibly reset in-place leases.

## Read-Only Reconciliation of Original Files

After loading the protected configuration above, a read-only check can be performed against the current database and the originals directory:

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.operations reconcile --stale-part-age-seconds 86400 --timeout 30
```

The command returns JSON listing missing originals, originals whose size or SHA-256 does not match, `.blob` files with no complete/submitted upload references, and `.part` candidates whose last modification time exceeds the specified number of seconds; the default age is 24 hours. Only application-named files in the current flat originals directory are scanned; the parsing temp directory is out of scope. Paths return only relative file names, not database credentials; the command does not delete, rename, or repair any files.
The scan reuses the backup write-pause lock: it waits for in-flight upload transactions to complete before acquiring a stable database reference; new uploads may continue streaming into temporary files, but the final rename and registration must wait until this reconciliation ends and be retried afterward. Reads remain available. Stale `.part` files may still belong to uploads paused mid-flight, so they are only candidates for manual verification and must not be deleted on that basis alone. Database/disk modifications that bypass the application are not protected by this lock. The result corresponds to this scan window; once the lock is released, the state may change.

By default it waits up to 30 seconds for write transactions, and the scan budget after acquiring the lock is also 30 seconds; check time is accounted per file and per 1 MiB hash block, and a single underlying disk read operation cannot be forcibly interrupted, so this is not a hard guarantee of total elapsed time. On timeout, unreadable files, or interruption of the lock connection, it returns failure, does not output a complete report, and releases the write pause. For large databases, `--timeout` can be explicitly adjusted during a maintenance window, and the frequency can be scheduled based on the reported `pause_seconds`. When an anomaly is found, operations staff verify the originals and backups; the site is not cleaned up automatically.

## Backup

Configure the new target directory in advance on an **independent disk, a controlled network location, or another host**. The task does not automatically select a disk or claim that a same-disk copy is disaster recovery. Load the DSN into the current process environment via the protected configuration; avoid PowerShell Transcript/debug echoing of the configuration. The backup maintenance configuration can be loaded through the same `Import-ProtectedKbConfig`; the service configuration does not contain maintenance credentials.

```powershell
. D:\Agico\App\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile D:\Agico\Secrets\runtime.json -Account 'SERVER\agico_kb'
D:\Agico\App\scripts\backup.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915
```

The backup queues in turn to acquire the schema-specific exclusive advisory lock, waits for already-started write transactions, copies the complete/submitted originals referenced by the database and verifies them, runs a custom-format schema-scoped `pg_dump`, records migration version/classification/model identity/dependency locks and hashes, and then releases the lock. New writes—including upload final rename, submission, publish/unpublish/authorization, file associations, identity tokens, and worker writes—all quickly return a retryable `MAINTENANCE` 503; reads and authentication continue, temporary upload streams may continue, but final submission must be retried. Write transactions wait at most 30 seconds, and pg tools default to 300 seconds; on timeout, writes are resumed and the incomplete state is preserved. If background parsing encounters the pause, it will be recovered by the retained lease and retries.

The `manifest.json` only becomes a completion marker after the original lock connection has been successfully released; check the `pause_seconds` in the manifest. A terminated lock connection cannot produce a completed backup. Missing originals or hash errors cause failure. `.part` files, parsing directories, and unreferenced orphan files do not count as completed-original backups; orphans are not deleted automatically. Direct database/file modifications that do not pass through the application write gate are unprotected, and such maintenance writes are forbidden during backup. With a large number of originals, the pause includes the full copy time; monitor the actual duration, and design storage snapshots if necessary.
## Restore and Cutover to the New Target

Only trusted backups from controlled sources are accepted; SHA-256 can detect accidental corruption but cannot substitute for signature verification, since a PostgreSQL archive contains executable SQL. The restore account only has the `AGICO_KB_MAINTENANCE_URL` environment variable injected (loaded from a separate protected configuration); no DSN/password appears on the command line.

```powershell
D:\Agico\App\scripts\restore.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915 -NewDatabase agico_recovery_20260915 -NewStorage D:\AgicoRecovery\run-20260915
```

The restore first validates the manifest, all hashes, and paths, then creates the new database from template0; any pre-existing database or directory is rejected outright. The public.vector extension is pre-installed, `pg_restore --no-owner --no-acl` is used, and the CREATE SCHEMA entry for the default public schema is precisely skipped. Business permissions, official accounts, and the active version are retained along with the table data. You may pass `-RuntimeRole agico_app` to grant an existing non-superuser role on the target DML privileges on the new database's business tables and sequences; source database roles and ACLs are not restored. For Windows automated deployment, the originals path must remain DataRoot\originals; see [Server Installation](../server-setup.md) for the specific steps of preparing an empty directory, reading the maintenance configuration, and refreshing the service configuration. File ACLs are reapplied by the target service registration script.

Only running jobs inside the new isolated database are re-queued, with generation/lease cleared and attempt counts reset; published versions and the existing active generation are preserved. queued jobs remain in the queue, and failed/cancelled statuses are retained. On restore failure, a `RESTORE_INCOMPLETE` marker plus the new database/directory are left behind for inspection, and the startup script refuses to use that directory; never automatically DROP or overwrite the live environment.

After starting the new configuration in isolation, verify `/health/ready`, the bytes of designated originals, normal/cross-division/private permissions, official versions, and search citations; start the worker and confirm that leftover jobs are handled correctly. Validate the model archive and identity, and re-issue/revoke tokens as necessary. After manual review, stop the old writes and cut over traffic; until verification is complete, the old service keeps using the original database. If new writes are produced after the cutover, a rollback will involve that newly added data, so simply restoring the old snapshot is not possible. A pure code upgrade may keep the data in its original location and revert to the previous application directory, but only do so when migrations are backward-compatible and validation has passed.

## Final Acceptance Checklist for the Target Server

Record the actual server, service identity, software hashes, independent backup location, and execution times; perform a Windows restart with no one logged in, a restart after API/worker process exit, ready recovery after temporary database unavailability, log rotation, TLS/firewall checks, and an isolated restore. Without this evidence from the target machine, do not check off the full production run pass condition of Task 7.
