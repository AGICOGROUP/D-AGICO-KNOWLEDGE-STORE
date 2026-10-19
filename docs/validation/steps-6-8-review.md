# Third Batch Development and Review

This file preserves the verification results as they stood during the third batch; the later wrap-up has since resolved the vector concurrency bottleneck described below, and current results are in the [overall report](final-review.md). This batch corresponds to planned steps 6, 7, and 8; branch `dev/steps-6-8`, starting point `65ea2da`, with local implementation, verification, and independent review completed on 2026-09-16. Real business use, client entry points, production servers, and capacity acceptance still require the actual environment.

## Implemented and Verified Parts

### Agent Enterprise Knowledge Extension

- The shared brief usage convention requires proactively retrieving company background, facts, standards, and experience per task and applying them to work outputs, avoiding meaningless full-corpus reads.
- The real Codex CLI completed a routine selection task using four newly uploaded, parsed, and published synthetic documents: identifying over-temperature, adopting the prescribed structure, escalating the technical assessment, and distinguishing historical experience from current commitments.
- The unknown authentication question was not given a fabricated number; the generic multiplication made zero MCP calls. The global Codex configuration was not modified; an independent restricted member identity was used.
- The business example had 8,283 characters of tool text over 11 calls. Cumulative input includes client instructions and multi-turn history, so the full token count cannot be treated as knowledge-base body consumption; there were two avoidable empty searches, left for optimization in the real-material pilot.

Evidence: [Codex routine task](codex.md), [usage convention](../../client/agent-usage.md). Real enterprise quality, Accio, web/DingTalk are still not integration-tested.

### Deployment, Backup, and Recovery

- Database transactions uniformly enter the write-pause mechanism; backup waits for old transactions to end, new writes quickly return a retryable notice, while reads and authentication continue.
- The database snapshot, the corresponding original files, and a checksum manifest are saved; an interrupted lock connection, missing file, or hash error does not produce a successful backup. Recovery only creates a brand-new database and directory.
- Checked recovery of the default `public` and isolated schemas, original files, private permissions, official versions, retrieval, and pending tasks.
- The Windows script generates the configuration by default, supporting an ordinary dedicated account and optional gMSA; it includes offline model identity, logging, health-check, and failure-restart configuration. No system service was installed and no firewall was modified.
- Stopping and restarting the actual development database exposed a stale-connection problem, fixed by validation at connection-pool checkout. The first authenticated read after recovery, file download, cross-department denial, retrieval, and version/job counts all passed.

Evidence: [operations and recovery](operations.md). Same-disk recovery drills do not count as independent disaster recovery; a formal Windows service and no-login startup still await verification on the target machine.

### Business and Concurrency Testing Tools

- Added a Locust tool with frozen configuration, independent credentials, and correctness checks; each run re-uploads and processes the synthetic files and cleans up its own run resources after testing.
- On the local machine: 50 identities, about 75 seconds of measurement, 12,382 complete search/read cycles, an average of 331.5 requests/second, p95 230 ms. This is a short REST backend test, not 50 real MCP sessions or full Agent tasks.
- Complete-cycle correctness was 93.216%; 840 semantic cycles failed due to vector-busy degradation, for an overall search degradation rate of 28.323%. Precise-fact, file, current-revision, unknown-file, and permission samples passed; non-degraded semantic queries found the target source in 1,225/1,225 cases.
- On the first semantic query the target ranked 2nd; after takedown that version was not searchable/readable, the original file checksum matched, and it became queryable 2.129 seconds after the concurrent import.
- Records of the initial probe failure and the criteria correction are retained; the core search was not modified to chase throughput numbers. The next round prioritizes the already-identified query-vector concurrency bottleneck before doing real-scale acceptance.

Evidence: [business quality](business-quality.md), [50-user probe](load-50.md), [200 scaling path](scale-200.md). The conditions for claiming that formal capacity acceptance has passed are currently not met.

## Review Record

The source distribution and wheel were built with `uv build`; the wheel contains database migrations, classification configuration, and the backup/recovery modules. Deployment scripts, client adaptation scripts, and verification instructions are delivered with the repository; formal operations use source/locked-dependency deployment.

After the fixes, the full parent-task regression: `pytest -q --tb=short` gave **97 passed, 1 skipped** (the independent database restart drill, which requires explicit opt-in), 9 pre-existing dependency warnings, 31.42 seconds. The actual development database restart drill had already passed separately; 7 PowerShell scripts parsed successfully. The Ruff full-tree check, format check on 63 Python files, and the source-distribution and wheel builds all passed.

| Scope | Result |
|---|---|
| Step 6 | Independent review found no Critical/Important issues; subprocess environment inheritance and the scope of evidence excerpts were clarified, and retrieval-efficiency improvement records were retained |
| Step 7 | Independent review found no Critical/Important issues; the minor issue of the standard-account installation lacking an event log source has been addressed, and no system installation was performed |
| Step 8 / this batch's final review | Local implementation and integration passed; 1 Important cleanup issue and 1 Minor wording issue were fixed, a scoped re-review confirmed closure, and no Critical/Important issues remain unresolved |

The final review covered `65ea2da..cb03b37` plus the status documents; the fix is `fa903d4` and the documentation sync is `7190db9`. Load-test cleanup now has database connection/lock/statement timeouts and independent deletion credentials; delayed imports can be canceled, and when activity has not stopped, the directory and schema are retained and a sanitized recovery record is generated to avoid cleanup races. Three new tests cover the failure, cancellation, and still-active cases. The re-review conclusion approves only local code and integration, not production rollout or capacity acceptance.
During development testing, one traceback once displayed the local test database connection information; the credentials for that local development account have been rotated and the new connection verified. No credentials are stored in source code, reports, or commits. Production credentials were not involved.

## Actual Inputs That Must Be Completed Before Go-Live

1. Company details and the business owner for the approved pilot, to confirm how the facts, specifications, and cases apply to real tasks.
2. The actual Accio version and how it runs, the web and DingTalk entry-point code or configuration, test employee identities, and the session delivery chain.
3. Target Windows server, service account, TLS / network access scope, and an independent backup location.
4. Real data volume, representative tasks, and response / accuracy requirements; the formal capacity acceptance configuration is frozen based on these.

It is currently not claimed that the full end-to-end flow with real employees, formal go-live, or 50/200 Agent capacity has been accepted.
