# DingTalk Identity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for native implementation task by task. Steps use checkbox (`- [ ]`) syntax. Do not modify the live production instance or publish the DingTalk app before isolated acceptance.

**Goal:** Use DingTalk identities and departments in the existing knowledge base, prove operation in the existing Windows Server test VM, then prepare configuration-only migration to the formal server.

**Architecture:** Keep the existing principals, document ownership, memberships, cookie sessions and MCP OAuth. Add an enterprise-scoped DingTalk API client, identity bindings, short-lived login challenges, bounded directory freshness checks, and a mode-aware portal. New identity infrastructure must not create an alternative document permission model.

**Tech Stack:** Existing Python/FastAPI/PostgreSQL, httpx, vanilla JavaScript, Windows/Hyper-V and DingTalk H5 JSAPI.

**Spec:** `docs/design/dingtalk-identity.md`

## Global Constraints

- Formal account IDs, password hashes, roles, memberships, history and existing source files must survive. No production bootstrap, account cleanup or direct document changes.
- CorpId plus userId is the identity key. No matching by name, or taking over accounts solely by email.
- Employees use DingTalk; only explicitly configured local highest-privilege maintenance identities may retain local login after mode switch.
- Default public reading: general-group and company-common. New staff default to user; department management is an explicit grant, not inferred from DingTalk administrator/title.
- Maximum identity freshness: five minutes, including MCP. Provider failures cannot extend stale identity indefinitely or trigger bulk disabling.
- API credentials remain protected configuration, never source, frontend, reports, query logs or Git. Service URLs and trusted hosts are configuration.
- Tests use fresh synthetic records in isolated schemas. A simulated API pass is not real DingTalk login acceptance.
- Existing worktree has extensive unrelated uncommitted work. Preserve it; make no broad staging, cleanup, reset or migration of the host's live DB. Use a dedicated development branch with current user changes preserved if implementation proceeds here.

## Review Focus

- A foreign-company auth code or forged client-side department list must not create an identity in this company.
- A same-name/same-email employee must not inherit an existing developer account.
- An employee who never logs in again must lose MCP and browser access after the permitted freshness window following removal.
- A directory timeout or incomplete page must neither grant permissions nor mass-disable staff.
- A changed public URL must preserve identity bindings and accounts while requiring correct DingTalk entry/domain setup and renewed sessions.

## Task 1: Protocol client and protected configuration

**Files:** Create `src/agico_kb/dingtalk_client.py`, `tests/unit/test_dingtalk_client.py`; modify `src/agico_kb/config.py`, Windows runtime configuration allowlists and their tests.

**Interfaces:** `DingTalkClient(config, transport=None).identity(code) -> Employee`; `.employee(user_id) -> Employee`; `.departments() -> list[Department]`. Employee includes stable user_id, optional union_id, display_name, department_ids, and explicit active/access status. Network failures raise sanitized KBError; callers cannot set the API base URL from request data.

- [x] Write fresh MockTransport tests for token acquisition/cache, auth-code exchange, employee detail, invalid-code handling, forbidden scope, non-JSON/malformed output, timeouts and sanitized diagnostics; run and observe failure before client code exists.
- [x] Implement only documented official endpoints, fixed HTTPS origins, bounded timeouts, expiry-aware app-token caching and strict expected response validation. Keep external requests outside DB transactions.
- [x] Validate configuration types, company/application identity, credential file and mapping IDs. Extend service loader allowlists consistently so upgrades and backup/restore do not silently discard fields.
- [x] Run `pytest tests/unit/test_dingtalk_client.py tests/unit/test_deployment_runtime.py tests/unit/test_server_setup.py -q` and record exact results.

## Task 2: Durable identity binding and effective scope

**Files:** Create `src/agico_kb/migrations/007_dingtalk.sql`, `src/agico_kb/dingtalk_identity.py`, `tests/integration/test_dingtalk_identity.py`; modify `db.py`, `accounts.py`, `auth.py`.

**Interfaces:** `bind_identity(conn, principal_id, employee, corp_id)` preserves principal ID; `provision_identity(conn, employee, config)` creates only default staff if no explicit binding; `effective_memberships(conn, principal_id, config)` applies current department mapping and explicit management grants. Migration adds bindings, login challenges and synchronization freshness/status without overwriting legacy accounts.

- [ ] Write failing integration tests for binding with no password, immutable CorpId/userId uniqueness, existing email collision, disabled legacy account, history preservation, default public reading, multi-department mapping and unmapped departments.
- [x] Implement verified binding: authenticated current account plus fresh DingTalk proof, with one identity per principal and unique corporate employee key. New employee display identity must work without an email and must not pretend to be a real company mailbox.
- [x] Preserve raw historical local memberships; derive mode-specific effective permissions from verified department membership and explicit KB grants. A department move cannot retain its former file or approval access.
- [x] Run `pytest tests/integration/test_dingtalk_identity.py tests/integration/test_account_hierarchy.py tests/integration/test_manager_department_scope.py -q` and check migration rerun/restore behavior.

## Task 3: Login, continuous validity and MCP

**Files:** Create `src/agico_kb/dingtalk_login.py`, `tests/integration/test_dingtalk_login.py`, `tests/integration/test_dingtalk_access.py`; modify `accounts.py`, `account_oauth.py`, `auth.py`, `main.py`.

**Interfaces:** Public GET auth settings advertises mode and public company ID; POST challenge and POST DingTalk login consume a short-lived cookie-bound single-use challenge plus code. Protected identity binding uses current CSRF session. `refresh_identity(db, principal_id)` validates a linked employee outside long transactions and persists status/department freshness. Every cookie and OAuth credential consumer shares the effective identity check.

- [ ] Write failing tests for challenge expiry, cross-client replay, wrong origin/company, credential throttling, same-email takeover rejection, and session authentication for passwordless linked accounts.
- [x] Implement H5 proof exchange and reuse `session_response`; keep user-supplied identity or role fields forbidden. Use stable session/identity lookups to prevent simultaneous login races.
- [x] On interval-expired identity fetch, update current departments; reject stale identity if provider unavailable. On definitive removal, revoke sessions, pending codes and OAuth grants. Do not auto-enable administratively disabled principals.
- [x] In DingTalk mode enforce employee password/invitation/reset rejection server-side, including unbound old employees; retain only explicitly selected existing superadmin maintenance login.
- [x] Test logout, role scope, department changes, no-new-login MCP removal, refresh-credential handling, temporary provider failure, invalid provider response and recovery. Run all account/OAuth/access tests before proceeding.

## Task 4: Embedded portal login

**Files:** Create `src/agico_kb/portal/dingtalk.js`, `tests/unit/test_dingtalk_portal.py`; modify `portal/account.js`, `portal/index.html`, necessary `portal/styles.css`, `main.py` asset/CSP configuration.

**Interfaces:** Existing account initialization first loads auth settings; the DingTalk adapter obtains official JSAPI code and POSTs it with the one-use challenge, then passes the existing profile to account initialization. No app credential in HTML or JavaScript.

- [x] Write failing UI-contract checks for mode-based staff login, safe SDK origin/CSP, an explicit retry on SDK/network failure, and no employee invitation/password controls in DingTalk mode.
- [x] Add only the required official H5 SDK and minimum CSP allowance. Use actual DingTalk environment detection for UX, while keeping proof verification on the server. Plain browser visits show the supported DingTalk-entry guidance and a distinct controlled maintenance option.
- [x] Keep document links/navigation in the app, and avoid introducing an external browser redirect for ordinary library actions. Existing file previews, CSRF requests and OAuth consent reuse their current flows.
- [ ] Validate the real portal in a browser and in DingTalk on PC/mobile. Mark client-only opening behavior and real auth as unverified until checked in those clients.

## Task 5: Directory configuration, VM and formal migration

**Files:** Create `scripts/dingtalk-setup.py`, `docs/validation/dingtalk-identity-20261019.md`; update `docs/design/account-login-and-invitations.md`, `docs/server-setup.md` and relevant runtime examples.

**Interfaces:** Setup supports validate/status and an explicit isolated deployment mode. It reads application credentials from protected storage, lists only necessary authorized departments, and accepts an explicit dept-ID-to-KB-ID mapping. It must not silently grant roles or bind formal identities based on names.

- [ ] Add fresh tests for configuration validation, invalid department maps, incomplete directory responses, credential absence, root portability and unchanged existing account fingerprints after additive migration.
- [ ] Read actual directory scope after credential authorization. Present the real department mapping and selected pilot scope; do not modify company personnel or guess business divisions.
- [ ] Verify the owned VM, current IP, remote administrator access and existing deployment layout; back up guest state before applying code. Obtain remote credentials through a secure interactive mechanism if no valid session exists, never through chat or report files.
- [ ] Deploy to a separate VM data directory and test DB with synthetic test data. Confirm health, restart, exact running build and migrations, file permissions, outbound DingTalk TLS, and isolation from the host production instance.
- [ ] Set actual reachable test HTTPS URL/hosts and prepare exact DingTalk URL/domain/pilot settings for required preview confirmation. Do not publish an untested full-company application.
- [ ] Complete real H5 login and end-to-end synthetic document upload/parse/preview/search/approval plus MCP authorization. Record protocol simulation, real provider calls, real client login and VM checks separately.
- [x] Replace superseded employee-password design sections with mode-aware behavior. Document final-domain migration: current DB backup/restore, protected config, PUBLIC_URL, trusted hosts, DingTalk entries/domains/callbacks and final-domain login smoke test.

## Delivery and review

- [ ] Run relevant regression suite and a fresh review of this change, with particular attention to identity binding, removal, provider failure and unchanged formal account records.
- [ ] Report code implemented, isolated tests, VM deployment, real DingTalk-client acceptance and remaining external blockers independently. Do not mark complete while required real acceptance remains untested.

## Current evidence

2026-10-19: Four read-only DingTalk permissions verified authed=true, including explicitly approved qyapi_get_microapp_list. Protected application credentials imported; actual employee, department tree and employee-usable app-list reads succeed on the host and VM. Target app absent from the employee-visible list while unpublished. Real response uses appList/agentId; mock tests match that observed schema.

Host final related suite: 71 passed. Fresh VM identity/client suite: 35 passed. Backup/restore suite: 17 passed, 1 intentionally skipped isolated cluster-restart exercise. Review findings about app visibility, definitive removal, emergency maintenance, invalid-token recovery were fixed; additional regression covers session revocation during binding, secret-free HTTPX logs, cached directory evidence expiry and stale cross-process responses.

VM AGICO-WS2012R2-Practice running at 172.28.221.66. Pre-DingTalk checkpoint created. Isolated C:/AGICO-DingTalk-Test-20261019 deployment on port 8766, separate agico_dingtalk_test_20261019 database, migrations 1 through 7. Existing guest accounts unchanged by fingerprint check. Fresh synthetic PDF completed real HTTP upload, project worker parsing, publication, search, full preview and original download. API process restart and ready/live checks succeed; full guest reboot not tested this turn. No migration applied to host production.

User-approved PC homepage http://172.28.221.66:8766/ was written and read back exactly. After exact user approvals, version 1.0.0 (56135dfa-b71b-4dda-b3ea-98826f9d0549) was created and published. Readback confirms RELEASE and the employee-usable app list now contains this AgentId. The user confirmed visibility limited to themselves before publish. Actual DingTalk PC login passed: user confirmed automatic entry; DB confirms the configured corporate user binding plus an unexpired dingtalk session, default user role. Mobile and real multi-role cutover still unverified. Attempted external quick tunnel returned Cloudflare DNS-prohibited-IP/timeout; that task-owned tunnel was stopped, no mobile URL claimed working. Browser UI automation timed out; no visual acceptance claimed.

Five exact-name department roots mapped in the isolated VM only. 建机/爱吉克 correspondence remains unconfirmed and unmapped. Formal account binding/cutover remains a deployment acceptance gate, never inferred from email/name.
