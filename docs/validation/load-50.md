# 50-Identity REST/MCP Local Verification

## Current Conclusion (2026-09-16)

The issue of immediate degradation when the vector component is busy has been changed to a bounded FIFO wait: one model and one execution thread are retained, holding at most 64 active/waiting requests, with waiting and inference sharing a 5-second budget; a timed-out local inference does not release its execution slot until it finishes. No caching, model serving, or deployment components were added.

In both rounds, 104 synthetic documents were regenerated, uploaded, parsed, and published, using 50 independent identities, with two business units performing permission-scoped retrieval. Each caller had only one request in flight at a time, reading the corresponding source after each search. Documents added concurrently were not written back into the frozen snapshot. The results below are from a short test, **not a formal 30-minute capacity test or real business acceptance**.

| Metric | REST Backend | Official MCP SDK Client |
|---|---:|---:|
| Independent identities/clients | 50 | 50, all initialized |
| Measurement time | 74.947 s | 75 s |
| Full search/read cycles | 11,714 | 7,347 |
| Correct cycles | 11,714 (100%) | 7,347 (100%) |
| Vector degradation | 0 | 0 |
| Average requests/tool calls per second | 313.660 | 195.920 |
| p50/p95/p99 | 140/240/270 ms | 235/360/422 ms |
| Expected permission denials (full cycles) | 1,951 | 1,228 |

REST average throughput includes 23,508 raw requests within the statistical boundary, of which 23,428 requests were full cycles. MCP counts only the 14,694 tool calls that fall entirely within the measurement window, covering protocol, authentication, and backend processing, but excluding Agent reasoning and answer generation. MCP client initialization completed before warmup; warmup was 15 seconds in both cases, and since the two data scopes differ, no precise protocol overhead is derived from these figures. Permission denials appear as 404 in REST and as is_error tool results in MCP, and are not conflated as HTTP 404.

## Verifiable Evidence

- REST: [frozen profile](load-50-frozen-profile.json), [full summary](load-50-probe.json), [quality counts](load-50-quality.json), [Locust data](load-50-stats.csv). run `load_35831b4a774146fdabe70bbca521b1f9`.
- MCP: [frozen profile](load-mcp-50-frozen-profile.json), [full summary](load-mcp-50-probe.json). run `load_b8b57e36bca54647a03c1caf6908b097`.
- [Pre-fix data](load-50-before-wait.json): 12,382 cycles, 93.216% correct, 28.323% degraded. Retained as historical evidence, not treated as current behavior.
- Windows development machine: i5-14400F/16 logical processors/about 31.8 GiB RAM; PostgreSQL 16.15 + pgvector 0.8.6, a single Uvicorn process, pool cap 8, local MiniLM. Model fingerprints, corpus counts, resource peaks, and teardown and concurrent-import observations are in the respective summaries.

Database pool waiters and vector queue depth were not exported; the report does not fabricate these two metrics. PostgreSQL wait events include idle ClientRead, which is not counted as backlog. The server does not generate answers, so there are no server-side answer tokens or generation latency.

During MCP-driven development, the Client construction for SDK 2.2 was corrected first; the first full measurement mistakenly counted the SDK-prefixed expected denials as failures. After fixing the test parser based on the SDK source code and newly added regression tests, the results in the table above were rerun with entirely fresh files/identities, without relaxing backend permissions. Records of the earlier synthetic-criteria issue are kept in [initial probe](load-50-initial-probe.json).

## Reproducing the Development Short Test

```powershell
uv sync --locked --group load
uv run python scripts/run_load_probe.py --users 50 --spawn-rate 5 --warmup 15 --duration 75
uv run python scripts/run_load_probe.py --transport mcp --users 50 --spawn-rate 5 --warmup 15 --duration 75
```

The runner connects only to the local agico_test; credentials are passed in via a restricted temporary file/subprocess environment; load clients do not inherit database credentials. On completion it cleans up its own schema, files, and identities, keeping credential-free results. Database cleanup has a bounded timeout; on failure, credentials are deleted independently and cleanup-required.json is written; if its own activity is still present, the run directory/schema is kept to avoid cleanup races.

The archived CSV only normalized the duplicate line endings from the Windows export, with no values changed; it is the last snapshot that Locust writes out periodically, and end-of-run statistics are governed by the summary JSON.

## Formal Threshold Judgment

Prepare the real corpus, authorized test identities, business tasks, and owner confirmation, then fill in and freeze `tests/load/acceptance-profile.json`. Latency thresholds are in milliseconds; success/correct/degradation rates use the 0–1 range. The loading phase checks configuration, credential uniqueness, and test-case references; formal REST judgment checks the statistical duration, actual spawn and the minimum number of live users during measurement, test-case coverage, latency, and the correct/degradation rates. Permission-denial and forbidden-source cases must independently all pass and cannot be diluted by the overall correct rate. The HTTP success-rate scope includes business-expected rejection responses and does not count them as transport failures. Missing thresholds/business confirmation yields blocked; failing to meet the thresholds yields failed with a non-zero exit code.
The real Locust 2 identity-feature probe validation ran with a user count of started=2/minimum_live=2 and 633 requests with zero failures; the development configuration is still explicitly blocked. It only validates the new acceptance count path and does not serve as capacity data.

```powershell
$env:AGICO_KB_LOAD_PROFILE = 'D:\ApprovedTest\frozen-profile.json'
$env:AGICO_KB_LOAD_CREDENTIALS = 'D:\ProtectedTest\credentials.json'
$env:AGICO_KB_LOAD_PROFILE_SHA256 = (Get-FileHash -LiteralPath $env:AGICO_KB_LOAD_PROFILE -Algorithm SHA256).Hash.ToLowerInvariant()
$env:AGICO_KB_LOAD_METRICS_PATH = 'D:\ApprovedTest\quality.json'
uv run --group load locust -f tests/load/locustfile.py --headless --users 50 --spawn-rate 5 --run-time 32m --host $env:AGICO_KB_TEST_URL --csv .local/formal-50
```

The formal configuration's address, user count, ramp-up, 2-minute warm-up, and 30-minute measurement period must match the command exactly. Passing the REST sub-gate still does not constitute real Agent / employee portal / overall production acceptance. Company profile, business SLOs, actual Accio / web / DingTalk, and the target server have not yet been provided; formal acceptance remains incomplete.
