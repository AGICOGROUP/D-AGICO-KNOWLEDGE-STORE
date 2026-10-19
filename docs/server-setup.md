# Windows Server Installation

The code directory and the knowledge base data directory are kept separate. For a first-time installation you only need to specify `DataRoot`; the script automatically installs pinned versions of Python, PostgreSQL/pgvector, application dependencies, and local models, initializes the database and the protected runtime configuration, and registers three Windows services that start automatically.

## First-Time Installation

Requirements: Windows x64, an administrator PowerShell 5.1, Git, network access to GitHub/PyPI/conda-forge and model download sites, and sufficient disk space. The service wrapper uses the Windows .NET Framework runtime. Place the code at an English path; the data directory supports Chinese characters and spaces, must be an empty directory on a local NTFS disk, and cannot be located inside the code directory. An existing test library cannot be used directly as a fresh installation directory.

Run the following in an **administrator PowerShell**. Paths in this guide are examples or placeholders; replace them with the actual directories used on your server before running commands.

The API configuration for this project is saved in `.deployment-private/providers.json`; this private directory is not committed to Git and is not part of the task output. Migrate using the **private deployment package** prepared on the source machine; after extraction, that directory is preserved along with the source code. The first setup automatically imports the same keys, endpoints, vision model pool, and named model pool into the server's protected configuration, and automatically converts them to the server paths, so there is no need to re-enter the API settings. Installing only from a Git clone or a public source package will not include the keys. The installation protects the private directory; the private deployment package is only intended for transfer between trusted servers and must not be distributed as a public download.

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\Enterprise Knowledge Base"
```

The first download takes time. If it fails due to network issues or similar causes, fix them and retry with the same command; the script verifies installation ownership and reuses the saved credentials and database. Re-running on a completed installation checks and starts the services, does not rebuild the knowledge base, and does not automatically upgrade dependencies. Do not move code or data directories that have already been bound.

The default API is `http://127.0.0.1:8765`, MCP is `http://127.0.0.1:8765/mcp`, and the database listens only on the local machine at `15432`. If a port is occupied by another program, the first-time installation can explicitly specify `-ApiPort` and `-DatabasePort`; multiple instances on the same machine also require a different `-ServicePrefix` and separate code and data directories.

## Data and Access

| Location | Contents |
| --- | --- |
| Code directory `.runtime` | Automatically downloaded programs, Python environment, installation cache; not committed to Git |
| Data directory `originals` | Original files |
| Data directory `postgres` | Metadata, permissions, versions, text and vector indexes |
| Data directory `models` | Local vector and OCR models |
| Data directory `config` | Installation ownership, protected credentials, and service configuration |
| Data directories `logs`, `temp`, `services` | Runtime logs, temporary files, and service wrappers |
After installation completes, `config/accounts-setup.json` provides first-time account initialization instructions and does not generate a shared employee access code. First load the protected runtime configuration, then create the first developer:

```powershell
. .\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile "<local-data-root>\config\runtime.json" -Account ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
# The setup installation environment uses .runtime; the local development environment uses .venv.
$PythonExe = Join-Path $PWD.Path ".runtime\venv\Scripts\python.exe"
& $PythonExe -m agico_kb.accounts bootstrap --email administrator@example.com
```

The tool prompts interactively for the initial password; the password never enters command arguments. Re-initialization is refused once any account exists. The first account is a developer and must change its password on first login; the new password is set by the user. Subsequent users can only join when an authorized account registers their email, role, and business division and shares a one-time invitation link. Administrators may invite administrators and regular users within the business divisions where they hold administrative rights, but cannot invite developers; regular users cannot send invitations. For details, see [Account Permissions and Invitations](design/account-login-and-invitations.md).

Visit `/health/ready` in a browser to check readiness. Opening `/mcp` directly returns 401 along with an OAuth discovery address — this is the normal unauthorized state. For MCP clients, configure only the service URL, then start account authorization; the user must log in and confirm on the knowledge base web page in person, and the password must never be sent to the AI. Codex can use `codex mcp login agico-kb`; other clients must support OAuth.

By default the API listens only on the local machine. When used from other computers, configure a trusted domain, an HTTPS reverse proxy, and a firewall; `AGICO_KB_PUBLIC_URL` is the default address for invitation links, and `AGICO_KB_ALLOWED_HOSTS` must include the actual MCP entry domain. Temporary tunnel URLs may change and must not be used as the formal, long-term invitation entry point.

## Upgrading the Account System on an Existing Installation (One-Time Operations Task)

First back up the database and the original files, stop the API and worker, update the program and dependencies, load the protected configuration in the current installation environment, and then run `python -m agico_kb.admin migrate`. Migration 005 only adds account, invitation, session, and OAuth tables; it does not modify original files, parsed content, uploaders, or the historical audit trail. The old authentication mode remains in effect until the first account is created; once creation succeeds, account mode is enabled and the old shared codes are rejected.

Use the account initialization command above to create one explicitly designated developer, then start the services and verify login, first-login password change, invitations, and MCP discovery. Old historical identities are not automatically converted into accounts that can log in. The static Authorization request headers from the old shared configuration should be removed, and clients should re-authorize through accounts. When an administrator deactivates an account, changes its password, or adjusts its permissions, that account's existing sessions and AI authorizations are revoked.
## Current Production Accounts Migrating to the Server (Starting 2026-10-14)

The current local instance already has the production account system enabled, and the registered accounts are production operating data. The deployed code itself does not carry a database; you must migrate the latest database and the originals backup. Do not treat the server's empty database as the existing project, and do not re-create the first administrator in place of the migration.

1. Before the switch, run `agico_kb.operations backup` on the current actual instance to generate a backup containing the complete logical database and the committed originals. The database includes account_settings, account_users, principals, memberships, account_invitations, account_audit, and OAuth records. The backup is stored in the protected data directory and does not enter Git; this baseline backup is only a snapshot of the current point in time, and a fresh backup must be taken again before the production cutover.
2. Prepare the server runtime environment, and **do not create any new accounts**. Use the service script's `-Action Stop` to stop the three target services, then start only the target database service. The restore must create a new database; service management requires the originals to remain at `DataRoot\originals`. After confirming that the brand-new target originals directory is a normal directory and completely empty, move this empty directory to a non-existent temporary name under the same DataRoot so that originals no longer exists. If the directory is not empty or contains reparse points, stop the operation; existing business data must not be cleared, overwritten, or moved. Restore using the command below; the source local machine data remains unchanged.
3. Keep the `agico_app` user and password generated by the server setup; change only the database name in the protected runtime.json to the restored new database and the schema to the backup schema; keep the originals root directory at `DataRoot\originals`. Configure the production HTTPS entry point and allowed domains, then run the service script with `-Action Install` to sync the API/worker configuration, re-apply the ACL, and start the services. `-Action Start` does not sync the modified main configuration. Do not run accounts bootstrap; the initialization tool will also refuse to rebuild when accounts already exist.
4. Before cutting over traffic, verify the number of registered accounts and their identity associations; confirm that activated administrators and staff can log in with their original passwords, that permission scopes are consistent, that deactivated accounts are still rejected, and that unactivated accounts and invitation records still exist; also verify the originals and their version relationships. If verification fails, keep the local instance as the original valid entry point and do not replace it with the new empty database.
5. After the address changes, users log in again, and AI clients re-authorize at the new MCP address; account passwords do not need to be changed because of the relocation. Old invitation links must be replaced with the production entry point or regenerated; the original validity period, single-use, and deactivation rules remain in effect.

Only a brand-new standalone knowledge base with no accounts applies the above "first-time initialization" steps. Project optimization, test data cleanup, code updates, and Git synchronization must not clear or overwrite production accounts. The date and version status of the account system do not mean that the entire knowledge base has completed production server deployment acceptance.

Restore example (Administrator PowerShell, with the target application stopped and only the target database started; replace Archive with the controlled backup path):

```powershell
. .\deploy\windows\config-loader.ps1
$Operator = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Maintenance = Read-ProtectedKbJson -ConfigFile "<local-data-root>\config\maintenance.json" -Account $Operator
$env:AGICO_KB_MAINTENANCE_URL = $Maintenance.database_url
try {
    .\scripts\restore.ps1 -PythonExe "$PWD\.runtime\venv\Scripts\python.exe" -PgBin "$PWD\.runtime\postgres\Library\bin" -Archive "<local-backup-root>\cutover" -NewDatabase agico_restored -NewStorage "<local-data-root>\originals" -RuntimeRole agico_app
} finally {
    Remove-Item Env:\AGICO_KB_MAINTENANCE_URL -ErrorAction SilentlyContinue
    $Maintenance = $null
}
```
`-RuntimeRole` must be an existing non-superuser login role on the target; the tool grants run permissions on the business tables and sequences of the new database. Subsequent schema upgrades are executed by the maintenance identity running migrations, and newly added tables also need the appropriate permissions granted to the runtime role. Official accounts and business authorizations are data within tables and are kept separate from this database runtime role.

## Optional Parsing and Preview Dependencies

The base installation does not automatically install LibreOffice, ODA File Converter, FFmpeg, or Blender. Office full-text preview and legacy format conversion depend on LibreOffice, DWG text and thumbnails depend on ODA, video thumbnails depend on FFmpeg, and some 3D thumbnails depend on Blender. Fill in the absolute executable file paths for `AGICO_KB_OFFICE_CONVERTER`, `AGICO_KB_CAD_CONVERTER`, and `AGICO_KB_BLENDER`, and add FFmpeg to a PATH visible to the services; grant the API/worker service identities the necessary read/execute permissions.

The official GLM and Cephalon Cloud keys are automatically imported by the project's private configuration into separate runtime files under the target `DataRoot\config`; the main configuration automatically generates the target paths for `AGICO_KB_VISION_CONFIG_FILE` and `AGICO_KB_VISION_FALLBACK_CONFIG_FILE`, so there is no need to fill in development machine paths. The Cephalon Cloud fallback address is `https://cephalon.cloud/user-center/v1/model/chat/completions`; the configuration only contains vision models that have passed the current evaluation, separated by `/`, with the first item corresponding to `AGICO_KB_VISION_FALLBACK_MODEL`. The service installation protects the key files, so API/worker can only read them. Public source packages, Git, and the command line contain no keys; the private deployment package carries the same API configuration and must be kept under controlled custody. Verify the primary/fallback connections after deployment. API/worker point to the same AGICO_KB_STORAGE_ROOT, and naming and vision requests are serialized across processes using .vision file locks; other instances or external applications sharing the keys may still trigger vendor rate limiting.

## Service Management

The default service names are `AgicoKbDatabase`, `AgicoKbApi`, and `AgicoKbWorker`, each using a separate `NT SERVICE\ServiceName` virtual account and set to start automatically. The database, API, and background parsing processes each run independently, and the application connects to the database using a non-superuser role.

```powershell
# Run from the code directory in an administrator PowerShell; replace Status with Start or Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "<local-data-root>" -Action Status
```

Installation errors are detailed in the data directory's `logs/setup.log` and `logs/setup-errors.log`; service logs are in `logs/database`, `logs/api`, and `logs/worker`. Logs should also be treated as internal operations material. `-Action Uninstall` stops and deletes the service registrations for this installation while preserving data and credentials; re-register with `-Action Install`.
Modify runtime.json using **Stop → Modify → Install**. Install refuses to refresh configuration while the API/worker is running; after completion, the configuration of both services matches the main configuration. The maintenance configuration is read only by administrators and is not written into the service configuration. Re-running setup on an installed instance only checks and starts services, and does not replace the configuration synchronization step.

Naming models and vision models are kept separate: `AGICO_KB_NAMING_FALLBACK_MODELS` in the runtime configuration can be set to `glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash`; DeepSeek is used only as a text-naming fallback, and image requests are forbidden. The model list in the vision credential file should contain only the two GLM models; HY4 is no longer allowed to be configured.

## Updates and Backups

The data directory does not go into Git, and `git pull` will not migrate or delete the knowledge base. Before updating, back up first and read the release notes, stop the API and worker, update the code, then synchronize dependencies and migrate the database as required by that version, and finally start the services and check their readiness status. The current installation script is not an automatic upgrade tool; do not re-synchronize the Python environment while the services are running.

A backup must cover both the database and the original files, and the configuration and model identities must be properly safeguarded. Do not casually copy the running `postgres` directory and treat it as a reliable backup. Use the existing [operations and recovery process](validation/operations.md); migrating to a new server should follow the recovery process, not rewriting the installation ownership files.

Dependency installation follows the [official uv documentation](https://docs.astral.sh/uv/getting-started/installation/) and the [official micromamba documentation](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html); the service wrapper is fixed at [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0). Actual versions and download checksums are saved in `deploy/windows/bootstrap-lock.json` and `deploy/windows/postgres-explicit.txt`.
