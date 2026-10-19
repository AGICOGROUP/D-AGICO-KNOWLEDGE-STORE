# Windows Single-Directory Installation Verification

Date: 2026-09-16. Goal: after pulling the Git project onto a new server, initialize an independent knowledge base via a single required `DataRoot` parameter; the existing local test library is not migrated or overwritten.

## Verified

- Ran the real installation entry point on a fresh code copy with a fresh Chinese-language data directory, using separate API/database ports.
- Automatically downloaded and verified pinned versions of uv, micromamba, and WinSW; installed Python 3.12.14, locked in the production dependencies, and installed PostgreSQL 16.15 and pgvector 0.8.6.
- Initialized an empty database, permission identities, and protected credentials; downloaded the vector and OCR models; original files, the database, models, and configuration were placed in the data directory.
- Actually registered three Windows auto-start services, each running under its own `NT SERVICE` virtual account; the API readiness check passed.
- Used freshly synthesized Word and image files, uploaded them over actual HTTP, and the background services completed parsing, vector indexing, and OCR; the Word file was ready, the image was partial with the OCR review prompt retained. The SHA-256 of the downloaded original file matched.
- Completed tool discovery, filename search, content search, and reading with source attribution through a real MCP client; access without credentials was denied. The application database role is not a superuser and has no privileges to create databases or roles.
- Re-running the installation in the same directory kept the access credentials unchanged; after the three services were manually stopped and restarted, they continued processing brand-new files.
- Synthetic files generated independently in two rounds both passed verification; four chunks in the database had vectors, and hybrid retrieval after the restart produced no degradation warning. After verification ended, the test service registrations and listening ports were removed while the test data was retained; the original local preview readiness check still passed.
- Fixed and verified the Windows Python version alias and the permission handling of internal links in the Hugging Face model cache; unexpected links and out-of-bounds targets are rejected.
- The installation mutex covers both the code and data directories; running the initializer directly also takes an exclusive lock, preventing concurrent initializations from stopping each other's database.
- The repository release review found no real credentials, original business filenames, personal directories, or binary runtime artifacts. A clean initial commit was adopted, and the original development history is kept locally.

## Automated Checks

Full pytest run: 147 passed and 2 skipped under the default configuration (isolated-process/database-restart tests that require explicit enabling). Among these, the 4 newly added initializer unit tests and the 7 Windows PowerShell 5.1 installation-protection tests all passed; the standalone script tests for service XML/identity/ACL/cache links passed, and the Ruff check passed. Actual Windows service stop/start was separately verified via the real installation flow described above. The final in-scope review has no remaining Important/Critical issues.

## Boundaries

This was an isolated deployment verification on the current Windows host; the entire machine was not rebooted. The auto-start configuration was checked, and the services were actually stopped/started. No claim is made on this basis that the target server, cross-machine HTTPS, the Accio/DingTalk entry point, or the 200-Agent capacity have been accepted. An actual installation still requires administrator privileges, Git, the Windows runtime environment, and a first-time online download. Upgrades and cross-machine recovery follow the operations procedures and are not replaced by re-running initialization.
