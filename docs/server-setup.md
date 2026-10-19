# Windows Server Installation

The code directory and the knowledge base data directory are separate. For first-time installation, you only need to specify `DataRoot`; the script automatically installs pinned versions of Python, PostgreSQL/pgvector, application dependencies, and local models, initializes the database and access credentials, and registers three Windows services that start automatically.

## First-Time Installation

Requirements: Windows x64, administrator PowerShell 5.1, Git, network access to GitHub/PyPI/conda-forge and model download sites, and sufficient disk space. The service wrapper uses the Windows .NET Framework runtime. Place the code in a path using English characters only; the data directory supports Chinese characters and spaces, must be an empty directory on a local NTFS disk, and cannot be located inside the code directory. An existing test library cannot be used directly as a fresh installation directory.

Run the following in an **administrator PowerShell**:

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\EnterpriseKnowledgeBase"
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

After installation completes, the administrator reads the initial administrator's MCP address, token, and expiration time from `config/initial-access.json`. The token is valid for 30 days and carries initial administrator privileges. Open "Admin · Access Code Management" at the bottom of the web page; after logging in with this token for the first time, you can issue regular access codes for employees or teams. The administrator code must not be used as a company-wide shared account. Do not paste configuration contents into logs, chats, or Git.

Visiting `/health/ready` in a browser checks readiness status; if opening `/mcp` directly returns `UNAUTHENTICATED`, it means credentials were not provided, not that installation failed. MCP clients need a Bearer token configured and must make calls using the MCP protocol.

By default, the API only allows local access. To let other computers, existing web pages, or DingTalk adapter services connect, you must separately configure a trusted domain, an HTTPS reverse proxy and firewall, and caller identity. Merely selecting a folder cannot infer this network and identity information; the knowledge base itself does not build a duplicate employee chat entry point.

## Upgrading Access Code Management for Existing Installations (One-Time Operations Task)

Back up first, stop the API and worker, then update the program and dependencies. Using the Python environment that runs this installation, load the protected configuration and then run `python -m agico_kb.admin migrate` (this round adds migration 003). Then explicitly designate a dedicated administrative identity, for example `python -m agico_kb.admin set-admin bootstrap-admin true`; that identity must already exist and must not have shared its token with employees. If no suitable identity exists, create a dedicated one via `set-identity`, then run `set-admin` and `issue-token`.

All existing identities from old installations and old backups default to having no new administrator privileges; the upgrade does not automatically promote publishers, nor does it re-enable administrators who were previously demoted. Only a brand-new first-time installation automatically authorizes bootstrap-admin. After an administrative identity's token expires, local operations staff must still issue a new administrator token (`issue-token`) to restore web administration; the web page can only issue regular member access codes. To revoke administrator privileges, use `set-admin <identity> false`.

Example of loading the configuration (using a protected configuration file accessible to the current operator):

```powershell
. .\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile "D:\EnterpriseKnowledgeBase\config\runtime.json" -Account ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
# The following is the setup installation path; for a local development environment, use .venv\Scripts\python.exe.
$PythonExe = Join-Path $PWD.Path ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Please verify the current directory and the Python runtime path." }
& $PythonExe -m agico_kb.admin migrate
& $PythonExe -m agico_kb.admin set-admin bootstrap-admin true
```
Proceed to the next step only after each step succeeds, and start the services at the end. Both existing backups in the 001/002 format and new backups containing 003 can be restored; when restoring an older backup, the missing database structure is filled in automatically, while administrator authorization still needs to be specified explicitly.

## Service Management

The default service names are `AgicoKbDatabase`, `AgicoKbApi`, and `AgicoKbWorker`, each using its own dedicated `NT SERVICE\ServiceName` virtual account and set to start automatically. The database, API, and background parsing process each run independently; the application connects to the database using a non-superuser role.

```powershell
# Run in the code directory from an administrator PowerShell; replace Status with Start or Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "D:\EnterpriseKnowledgeBase" -Action Status
```

For installation errors, see `logs/setup.log` and `logs/setup-errors.log` in the data directory; service logs are located in `logs/database`, `logs/api`, and `logs/worker`. Logs should also be treated as internal operations material. `-Action Uninstall` stops and removes the service registrations for this installation while keeping data and credentials; use `-Action Install` to re-register.

## Updates and Backups

The data directory is not tracked by Git, and `git pull` will not migrate or delete the knowledge base. Before updating, back up first and read the release notes, stop the API and worker, update the code, then synchronize dependencies and migrate the database as required by that version, and finally start the services and check readiness. The current installation script is not an automatic upgrade tool; do not re-sync the Python environment while the services are running.

Backups must cover both the database and the original files, and the configuration and model identity must be properly safeguarded. Do not casually copy a running `postgres` directory and treat it as a reliable backup. Use the existing [operations and recovery process](validation/operations.md); migrating to a new server should follow the recovery process, rather than rewriting the installation ownership files.

Dependency installation follows the [official uv documentation](https://docs.astral.sh/uv/getting-started/installation/) and the [official micromamba documentation](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html); the service wrapper is pinned to [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0). The actual versions and download checksums are stored in `deploy/windows/bootstrap-lock.json` and `deploy/windows/postgres-explicit.txt`.
