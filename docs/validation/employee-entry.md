# Web / DingTalk Employee Entry Points

2026-09-15: Both entry points are **not yet integration-tested**. No web code, DingTalk application, conversation mapping, or test employees have been provided. Standalone identity testing of the backend MCP and HTTP does not substitute for entry point acceptance.

Entry points must map the logged-in employee to an independent, revocable credential via a trusted service; Agent parameters must not determine identity. Credentials must not be handed to browser scripts or written into chat content. The trusted file handoff layer uploads and downloads under the current employee's identity, and returns the conversation of the original task and the original recipient.

## Pending Live Verification

| Scenario | Evidence that must be saved | Current status |
|---|---|---|
| Web employees A / B query the same restricted material | Entry-point employee, backend identity, each one's results; B has no permission | Not integration-tested |
| DingTalk employees A / B query the same restricted material | Same as above, plus group / private chat conversation binding | Not integration-tested |
| Upload, submission, and status query via both entry points | New file hash, upload_id, submitter, and status consistency | Not integration-tested |
| Download and delivery via both entry points | Current permission, file hash, original recipient, and original conversation consistency | Not integration-tested |
| Concurrent conversations, account switching, credential revocation | No identity crossover, no conversation crossover; requests rejected after revocation | Not integration-tested |
| Ordinary knowledge tasks | Background / facts / standards / experience genuinely influence the output | Not integration-tested |

Real materials must first be authorized and may only be published after confirmation by the business owner. These materials and personnel are still missing, so the quality acceptance of real business knowledge has likewise not been completed.
