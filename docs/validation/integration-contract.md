# First-Batch Integration Contract

The knowledge base is the enterprise knowledge extension library and original-file repository for existing Agents. Agents retrieve, understand, and apply enterprise materials on demand; the backend handles storage, authorization, publishing, and subsequent parsing and indexing, and does not take over business tasks.

## Identity

- Each distinct employee/service identity uses an independent, revocable Bearer token; the database stores only a digest.
- Organization and roles are confirmed by the server-side `memberships`; model input cannot select users, roles, or departments allowed for access.
- For browser/DingTalk, an existing trusted entry point or the task-scheduling layer maps the actual employee to the corresponding credentials, and results are returned to the original session. This real delegation chain is not yet in place; at present only backend verification with independent identities has been completed, so this must not be regarded as a multi-user entry point that has passed joint testing.
- Department members submit drafts; department heads publish. Public knowledge partitions also require explicit authorization. Cross-department sharing grants read access only, does not copy original files, and does not grant publishing rights.

## Files and Network

Prepare upload → the client holding the file performs a streaming PUT → full validation → submit with upload_id.
Obtain a file reference → a trusted delivery program performs a GET with the current identity → deliver via the original entry point; the model is not required to bring binaries into its context.

Uploads, downloads, and MCP share the same backend identity rules going forward. Original files will never be imported through arbitrary local paths or arbitrary URLs. Download references are not permanently public links. Current permissions are checked on every new download; copies already delivered cannot be recalled.

Codex's basic probes and business MCP search/read have passed, and the Step 5 tool and file adaptations are implemented. Step 6 uses the [shared Agent conventions](../../client/agent-usage.md) and four types of synthetic materials newly created each round to validate ordinary tasks; results and limitations are described in [Codex](codex.md). This does not replace business confirmation with real enterprise materials. [Accio](accio.md) and [Web/DingTalk](employee-entry.md) lack a real environment and remain pending joint testing.

Verification on this machine uses loopback HTTP, which does not mean company-network reachability, mobile reachability, or production HTTPS has passed. For the production launch, configure a dedicated service account, TLS, and data directory permissions according to Step 7.
