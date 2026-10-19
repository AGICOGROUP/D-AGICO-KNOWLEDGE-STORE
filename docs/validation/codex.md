# Codex Regular Task Validation

On 2026-09-15, Codex CLI `0.154.0-alpha.6.2`: **Regular task validation with synthetic data passed; the real enterprise pilot has not been accepted**.

## Reproducible Method

Run `.venv/Scripts/python scripts/probe_agent_usage.py`. The script requires a logged-in Codex CLI, the local `127.0.0.1:15432/agico_test` database, the development password file, and a local model cache. Each run creates a random schema, four newly uploaded / newly parsed / newly published documents, and independent member and publisher identities; the script only explicitly adds the current run's knowledge base member credentials, the subprocess still inherits the original CLI environment, and the current run's publisher credentials are not exported. At the end it stops the temporary HTTP service, deletes the current run's schema (confirmed absent via query), and deletes temporary files and the Agent working directory. Reports are kept in `.local/agent-probes/<run_id>/`. `finally` also executes on exceptions; force-killing the process or a power outage cannot guarantee `finally`, so leftovers should be checked against the exact schema in the report, and other data must not be bulk-cleaned.

Each case uses a fresh Codex session with `--ignore-user-config --ephemeral --skip-git-repo-check -s read-only`; the `AGENTS.md` in the temporary working directory has content taken from [Shared Conventions](../../client/agent-usage.md). A single `-c` sets the actual temporary loopback address and `bearer_token_env_var="AGICO_KB_TOKEN"`; no global configuration is written. Official documentation states support for URL-based MCP and environment-variable Bearer tokens; the local parameters were additionally cross-checked against `codex exec --help`. [Official MCP Documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

The independent test member has department-scoped read and draft-submission capability, without publish permission; it is not an administrator, nor a product-level read-only role. The temporary experiment only performs reads.

## 2026-09-15 Measured Results

run_id: `agent_probe_bcc92848d64a499fa89c0c10584ea82b`. The four documents cover a synthetic department background, product parameters, the current R2 specification, and 2024 historical cases; they do not represent real company facts. The original regular tasks, final answers, tool parameters, and usage excerpts are in [Structured Evidence](codex-synthetic-evidence.json). The committed excerpts do not include raw tool result bodies; the complete JSONL is kept only in the local run directory mentioned above. The script saves the synthetic seed text so the experiment can be re-run, but the excerpts in the repository are not a self-contained archive of the complete responses from this run.

| Scenario | MCP calls | Tool text chars | Seconds | Cumulative input tokens | Of which cached input | Output tokens |
|---|---:|---:|---:|---:|---:|---:|
| 88℃ AX-210 customer selection reply | 11 (7 searches, 4 reads) | 8,283 | 85.58 | 257,614 | 235,136 | 1,124 |
| AX-999 unknown certification number | 2 searches | 1,712 | 36.94 | 96,164 | 80,128 | 169 |
| 17 × 23 | 0 | 0 | 28.31 | 16,567 | 11,648 | 5 |

All CLI exit codes were 0, and schema deletion confirmed `cleanup=true`. The character count only accumulates MCP `result.content[].text`, without counting the duplicated `structured_content` a second time; character count is not tokens. The CLI cumulative input includes client instructions, tool definitions, historical context, and each round's tool returns; cached input is a part of that and is not added on top of the input. There is no separate token accounting for tool body text, so cumulative input cannot be attributed to the knowledge base, and no savings ratio is claimed. Elapsed time is the CLI end-to-end time, not background retrieval latency.

Business outputs verified item by item:

- Background: used "industrial circulating water application support", without fabricating a drinking-water certification service.
- Facts: kept 80℃, 12 m³/h, and the non-explosion-proof condition; computed an 8℃ over-limit and refused to recommend the standard AX-210.
- Specification: used the three headings "working-condition confirmation / applicability judgment / risks and items pending confirmation"; required written confirmation from the technical lead before committing to a model change.
- Experience: cited the 2024 high-temperature case, using it only for upgrade technical evaluation; made no commitment that alternative models, prices, or lead times exist, and did not override the current specification.
- Grounding: read all four source documents and cited paragraph 2 of the `synthetic-1` version. The customer question did not request a search; the Agent invoked MCP autonomously.
- Missing knowledge: AX-999 had no verifiable certification number, and it explicitly stated that none was found; generic multiplication was answered directly as 391 with zero MCP calls.

## File and Permission Corroborating Evidence and Boundaries

This round re-ran `pytest tests/integration/test_mcp.py tests/integration/test_file_transfer.py -q`: **11 passed, 4.92 seconds**. It covers real HTTP MCP, search read-back, member-management permission denial, forged fields, upload / submission / status, file hashing, and unauthorized download attempts; this is protocol-client evidence, and is not presented as acceptance of Codex autonomous file handoff. The three upstream deprecation / model notices are retained.
Currently, there are no real company pilot materials, business owner evaluations, or an actual Accio or web/DingTalk entry point. This small sample only demonstrates the synthetic ordinary task behavior described above; conflicting materials, actual client-side file handoffs, delegation under real personnel identities, and long-term quality still require acceptance verification. Task 6 has not been declared complete as a whole.
