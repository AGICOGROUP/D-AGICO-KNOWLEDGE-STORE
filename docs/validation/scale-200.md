# 200-User Scaling Path

200 users is a mid-to-late-stage goal; this task did not run a 200-user test, nor does it claim capacity qualification on that basis. Scaling continues to use the same `locustfile.py`, configuration structure, independent credentials, synthetic preparation, and correctness counting; the runner may generate up to 200 independent identities, but it must first copy and freeze a 200-user configuration, and warmup time must cover at least the full ramp-up.

## Measurement Order

1. After the real 50-user, 30-minute run and the business quality thresholds pass, freeze the 200-user target configuration, the 200 independent credentials, and the same business question set.
2. Keep each user to at most one request at a time, read after search, with no think time; report complete cycles and true throughput, and do not treat connection count as throughput.
3. Record p50/p95/p99, HTTP status, document/version/chunk/source correctness, full hybrid and keyword degradation, CPU/RSS/disk, database active/wait events, connection pool waiters, the vector queue, and the ingestion backlog.
4. Repeat the permission, takedown, old-revision, and original-document checks before and after the load; material produced by concurrent imports must not be written back into the already-frozen initial corpus snapshot.

Development probe command example (not an acceptance command):

```powershell
uv run python scripts/run_load_probe.py --users 200 --spawn-rate 5 --warmup 45 --duration 75
```

## Decide Scaling Step by Step Based on Bottlenecks

| Evidence | Address First | Re-test Requirement |
|---|---|---|
| High semantic degradation or vector queueing | Measure and adjust query vector slots, model-service concurrency, or a separate model service | Semantic correctness must not drop because of fallback |
| High database active, pool waits, or slow queries | First check query plans, indexes, connection pooling, and pagination candidate size | Permission filtering and source correctness stay consistent |
| Vector scans become the bottleneck after corpus growth | Evaluate pgvector HNSW with real scale and query plans | Recall quality and the rebuild/restore process verified simultaneously |
| Continuously growing ingestion backlog | Adjust parsing resources or add workers | Search service latency, job fencing, and takedown correctness do not degrade |
| Single-service CPU saturation with the above items ruled out | Then evaluate additional API workers/retrieval nodes | Clarify the shared model, connection count, and consistency costs |

The original local vector-busy degradation has been resolved through bounded waiting on the same model; the latest 50-identity REST and real MCP short tests were both correct with no degradation. A real larger corpus, 30-minute stability, queue/connection-pool waits, and the target machine still need to be measured, and 200-user capacity cannot be extrapolated on this basis. Without measured evidence, do not preemptively add HNSW, model services, workers, or retrieval nodes.
