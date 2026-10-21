# Windows Server Installation

The code directory and the knowledge base data directory are separate. For first-time installation, specify `DataRoot`; the script installs pinned Python, PostgreSQL/pgvector, application dependencies and local models, prepares the database and protected runtime configuration, and registers three auto-start Windows services. Git synchronizes project files only, not credentials, accounts, uploaded originals, parsing results or operational backups.

## First-Time Installation

Requirements: Windows x64, administrator PowerShell 5.1, Git, network access to GitHub/PyPI/conda-forge and model download sites, and sufficient disk space. The service wrapper uses the Windows .NET Framework runtime. Place the code in a path using English characters only; the data directory supports Chinese characters and spaces, must be an empty directory on a local NTFS disk, and cannot be located inside the code directory. An existing test library cannot be used directly as a fresh installation directory.

Run the following in an **administrator PowerShell**. Paths are examples or placeholders; replace them with the actual directories on your server before running commands.

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "<local-data-root>"
```

The first download takes time. After a failure, fix the network or other causes and retry with the same command; the script verifies installation ownership and reuses the saved credentials and database. Re-running on a completed installation checks and starts the services; it will not rebuild the knowledge base, nor automatically upgrade dependencies. Do not move code or data directories that have already been bound.

By default, the API is at `http://127.0.0.1:8765`, MCP at `http://127.0.0.1:8765/mcp`, and the database listens only on local port `15432`. If a port is occupied by another program, you can explicitly specify `-ApiPort` and `-DatabasePort` during the first-time installation; multiple instances on the same machine also require a different `-ServicePrefix` and separate code and data directories.

## Data and Access

| Location | Contents |
| --- | --- |
| Code directory `.runtime` | Automatically downloaded programs, Python environment, and installation cache; not committed to Git |
| Data directory `originals` | Original files |
| Data directory `postgres` | Metadata, permissions, versions, and text and vector indexes |
| Data directory `models` | Local vector and OCR models |
| Data directory `config` | Installation ownership, protected credentials, and service configuration |
| Data directory `logs`, `temp`, `services` | Runtime logs, temporary files, and service wrappers |

For a completely new instance, `config/accounts-setup.json` describes account initialization. Load protected runtime configuration and run `python -m agico_kb.accounts bootstrap --email administrator@example.com`; the initial password is entered interactively, never on the command line. The tool refuses to initialize when accounts already exist. The developer account must change its initial password after signing in. In email mode, authorized administrators register each user's email, role and divisions before issuing a single-use invitation. Managers cannot invite developers; ordinary users cannot invite others. See [account and invitation design](design/account-login-and-invitations.md).

Visit `/health/ready` to check readiness. An unauthenticated `/mcp` request returning 401 with OAuth discovery information is expected. MCP clients configure the service URL and use account OAuth authorization; the human signs in and approves in the browser. Do not send employee passwords to an AI assistant or copy static Authorization headers into client configuration. Codex supports `codex mcp login agico-kb`.

The API defaults to local access. Configure a trusted domain, HTTPS reverse proxy, firewall rules, `AGICO_KB_PUBLIC_URL` and `AGICO_KB_ALLOWED_HOSTS` before external access. Temporary tunnel addresses are not permanent production invitation URLs.

## Existing Accounts, Upgrades and Server Migration

Back up the current database and originals, stop API/worker, update code and dependencies, load the protected configuration and run `python -m agico_kb.admin migrate` using the maintenance identity. Migration 005 adds accounts, invitations, sessions and OAuth; migration 007 adds DingTalk bindings and challenges. Migrations must not rewrite original documents, parsing content, existing account identities or business audit.

Accounts already registered in an operational instance are persistent production data. Source deployment does not migrate them. Transfer a fresh complete database and original-file backup through a protected channel; never initialize a replacement administrator or restore a test library over operational accounts. Preserve passwords, identity bindings, roles, division memberships, invitations, disabled state and account audit. Restore into a new database and an unoccupied original-storage target; do not clear existing business directories. Keep the target installation's database login, point its protected runtime configuration to the restored database/schema, grant its non-superuser runtime role access to newly added tables, and synchronize service configuration with Stop then Install. Existing users must pass login and permission checks before cutover. Users sign in again and AI clients reauthorize at the new public address; that does not require changing their existing account passwords.

Example of loading the configuration (using a protected configuration file accessible to the current operator):

```powershell
. .\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile "<local-data-root>\config\runtime.json" -Account ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
# The following is the setup installation path; for a local development environment, use .venv\Scripts\python.exe.
$PythonExe = Join-Path $PWD.Path ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Please verify the current directory and the Python runtime path." }
& $PythonExe -m agico_kb.admin migrate
# Only for a completely new instance with no accounts; never repeat after restore.
& $PythonExe -m agico_kb.accounts bootstrap --email administrator@example.com
```
Proceed only after each step succeeds, and start services at the end. The bootstrap command above is exclusively for a new empty account system, not a routine upgrade. Runtime database credentials are separate from application account and approval roles. Follow the [operations and recovery process](validation/operations.md).

## API Model Configuration

Only developers can manage the API configuration page. Each route keeps one current key, encrypted with Fernet authenticated encryption; the browser receives only its first three and last four characters. Replacing a key opens an empty password field and overwrites the old value without keeping a history. Connection testing uses saved server-side configuration without returning the full key.

- Primary: `bigmodel.cn`, models `glm-5.3-flashx` and `glm-5.3-flash`.
- Fallback: `cephalon.cloud`, those two GLMs plus `deepseek-v4.1-flash-1017` and `deepseek-v4.1-flash`. All four fallback candidates are allowed in image and text tasks; availability does not establish recognition accuracy.
- Low, medium and high reasoning levels map to native `low`, `high` and `max`, with thinking enabled.
- Try primary candidates in preference order. Only after they are unavailable, use fallback for a fixed 1,800 seconds from the route switch. Do not return early when an individual primary model's cooldown expires. Retry the primary preference after the interval; another complete primary failure begins another half-hour fallback period. Later fallback failures do not extend the current route interval. State is shared across API and workers and survives restarts. If all candidates are unavailable, preserve progress and wait without rewriting the preferred models.

Profiles live outside source and original storage at `DataRoot\config\api-keys\primary.txt` and `fallback.txt`. Separate encryption material is generated at `config\.api-key-encryption.key`, with service and maintenance ACLs. Missing material fails closed; existing encrypted stores do not silently receive a replacement. No additional user-maintained encryption password is required. Subsequent requests read new keys without restarting, while an in-flight request may retain its previously read value.

For migration, re-enter keys on the target page or securely transfer encrypted profiles together with their encryption material. Anyone possessing both can decrypt them. Source synchronization and newly generated deployment packages omit API and DingTalk secrets; historical private packages remain sensitive. Git ignore rules do not protect folders uploaded through ordinary cloud-drive synchronization.

## Optional Preview Dependencies

The base installer does not install LibreOffice, ODA File Converter, FFmpeg or Blender. Office full-document previews and legacy conversions use LibreOffice, DWG extraction and previews use ODA, video thumbnails use FFmpeg, and some 3D thumbnails use Blender. Set absolute executable paths in `AGICO_KB_OFFICE_CONVERTER`, `AGICO_KB_CAD_CONVERTER` and `AGICO_KB_BLENDER`, expose FFmpeg through the service PATH, and give API/worker identities the necessary read/execute access.

## DingTalk Identity

See the [H5 integration guide](integrations/dingtalk-h5-integration.md) and [identity design](design/dingtalk-identity.md). Without `AGICO_KB_DINGTALK_CONFIG_FILE`, existing email mode remains enabled. Use an independent database and storage directory for initial acceptance. Protect the application credentials on the server, explicitly map verified department IDs to divisions, and list only existing active developer principal IDs in `maintenance_principals`. Do not infer approval or developer roles from a DingTalk job title.

Before enabling this mode on an operational instance, back up its current accounts and originals, stop API/worker and apply migration 007 with the maintenance identity. Put `dingtalk.json` beside protected `runtime.json`, configure its absolute path and synchronize services with Stop then Install. Configure client-reachable PC/mobile home URLs, actual allowed domains and a restricted pilot audience before publishing the enterprise application. Validate real client login, division visibility and approval boundaries, disablement and MCP authorization independently. A health check or mocked API result does not prove client login, and the H5 flow does not implement external-browser QR login.

At production cutover preserve CorpId/userId bindings and migrate the latest operational database and protected configuration. Update public URL, allowed hosts, DingTalk home URLs and actual domain settings; do not replace operational accounts with a pilot database or rerun bootstrap.

## Service Management

The default service names are `AgicoKbDatabase`, `AgicoKbApi`, and `AgicoKbWorker`, each using its own dedicated `NT SERVICE\ServiceName` virtual account and set to start automatically. The database, API, and background parsing process each run independently; the application connects to the database using a non-superuser role.

```powershell
# Run in the code directory from an administrator PowerShell; replace Status with Start or Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "<local-data-root>" -Action Status
```

For installation errors, see `logs/setup.log` and `logs/setup-errors.log` in the data directory; service logs are located in `logs/database`, `logs/api`, and `logs/worker`. Logs should also be treated as internal operations material. `-Action Uninstall` stops and removes the service registrations for this installation while keeping data and credentials; use `-Action Install` to re-register.

After editing protected runtime configuration, use Stop then Install to synchronize API/worker configuration and ACLs. Start alone does not synchronize revised configuration. Installation refuses to refresh configuration while API/worker are running.

## Updates and Backups

The data directory is not tracked by Git, and `git pull` will not migrate or delete the knowledge base. Before updating, back up first and read the release notes, stop the API and worker, update the code, then synchronize dependencies and migrate the database as required by that version, and finally start the services and check readiness. The current installation script is not an automatic upgrade tool; do not re-sync the Python environment while the services are running.

Backups must cover both the database and the original files, and the configuration and model identity must be properly safeguarded. Do not casually copy a running `postgres` directory and treat it as a reliable backup. Use the existing [operations and recovery process](validation/operations.md); migrating to a new server should follow the recovery process, rather than rewriting the installation ownership files.

Dependency installation follows the [official uv documentation](https://docs.astral.sh/uv/getting-started/installation/) and the [official micromamba documentation](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html); the service wrapper is pinned to [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0). The actual versions and download checksums are stored in `deploy/windows/bootstrap-lock.json` and `deploy/windows/postgres-explicit.txt`.
