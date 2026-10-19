# Local Process Restart and 32 MiB File Integrity Verification

## Running

With the development database `127.0.0.1:15432/agico_test` available, `.local/pg-password.txt` configured, and the Python environment containing `psutil`, run from the repository root:

```powershell
$env:AGICO_KB_RUN_PROCESS_RESTARTS = '1'
try {
    .venv\Scripts\python.exe -m pytest tests/integration/test_process_restarts.py -q -s --tb=short
    $restartTestExit = $LASTEXITCODE
} finally {
    Remove-Item Env:AGICO_KB_RUN_PROCESS_RESTARTS -ErrorAction SilentlyContinue
}
if ($restartTestExit -ne 0) { throw 'Process restart verification failed' }
```

The default full test suite skips this item; once explicitly enabled, each run creates a new isolated schema, random identity and token, and generates a brand-new random binary file. The database password only enters the subprocess environment and never appears on the command line or in results.

## Verification Scope

- Uses real HTTP and the existing `FileTransfer` to generate, upload, and download a 32 MiB `.bin` file in 1 MiB chunks; verifies size and SHA-256, without using base64 and without sending file content to the model.
- Publishes as `file_only`, actually terminates uvicorn and then restarts on the same port; checks that the official release is still visible and that duplicate publishing is rejected.
- After the worker subprocess obtains work and a lease through a real `Worker.claim()`, the test suspends it before the parser subprocess is created; via atomic flags it confirms that the actual worker PID has no parser descendants, and actually terminates the entire owned process tree. The Windows venv launcher itself may have Python and conhost descendants.
- Only the lease for that task in this isolated schema is shortened to 1 second in the future; after waiting for it to expire naturally, the unmodified `python -m agico_kb.worker` is started. Task state, generation, and attempts are not reset, and production logic is not changed.
- Checks that the replacement worker completes the `stored_only` processing with a different generation and a second attempts value; the official release, document revision, and publish audit are not duplicated; the cross-business-unit identity without permission cannot download either before or after the restart; and the file hash remains consistent.

The production lease is determined by `max(60, parse_timeout_seconds + 300)`, defaulting to 420 seconds. This test explicitly shortens the lease, so what it verifies is the normal expiry-and-reclaim path after a process failure; it does not mean that actual production recovery takes only 1 second, nor does it cover a crash partway through parser execution or recovery from model initialization.

All Windows subprocesses set `CREATE_NO_WINDOW`. Cleanup only operates on the recorded PID/creation time and its descendants, suspending each process level by level before enumerating its children, then terminating the process tree in reverse order, waiting up to 10 seconds each time; only this schema, verified by name and against the database, is deleted. Files are managed by pytest's temporary directory; the workspace directory is not recursively deleted.

## Execution Record

On 2026-09-16, this was executed separately after the same-machine MCP load and other database tests finished; the parent task ultimately enabled both the process-restart and database-restart drills, with the result: **2 passed in 11.78s**; the scenario in this file itself took **4.532s**.

| Item | Measured Result |
| --- | --- |
| New file size | 33,554,432 bytes (32 MiB) |
| SHA-256 of upload and two downloads | `d8e2e4926817a92291548dbd1b79f3bc1354656c4caa12773a9a50ebba6fcb99` |
| uvicorn launcher PID | 25332 → 2568 |
| worker launcher PID | 7792 → 10520 |
| Exit codes of the four owned processes | 15, 15, 15, 15 (deliberately force-terminated) |
| Remaining original lease at first claim | 419.918706 seconds |
| Fault-injected lease in test | Changed to 1 second in the future, then waited for natural expiry |
| Task after recovery | complete, attempts=2, generation changed |
| Official release | single release, revision=1, a single publish audit, file_only |
| Permissions | the cross-business-unit identity received 404 on both downloads |
| Final processing capability | stored_only; file=true, text=false, vector=false |

The first run exposed an incorrect assumption in the test about Windows venv launcher descendants; after fixing it to identify the actual worker PID, a complete re-run with a new schema, a new identity, and a new random file passed. No production code was modified. pytest's existing Starlette/AnyIO deprecation warnings remain present.
