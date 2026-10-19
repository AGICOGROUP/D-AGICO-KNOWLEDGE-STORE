# Batch 2: Verification and Review of Steps 3, 4, 5

Date: 2026-09-15. The user's "continue 4–6" instruction covered the 4th through 6th development steps in order, corresponding to zero-based plan numbers 3, 4, and 5. Branch `dev/steps-3-5`; step 6 not yet started.

## Delivered

1. **Parsing and indexing**: format adaptation, local OCR, model fingerprint, job leases, processing generations, subprocess timeouts, bounded retries, atomic index switching, documented limitations, attachment association.
2. **Retrieval and reading**: Chinese keywords + local vectors, exact model-number and metadata filtering, permission and official-version filtering, rank fusion, pagination, source locating, on-demand original-text reading, background queries and fallback.
3. **MCP and transport**: real Streamable HTTP on official SDK 2.2, independent identity, dynamic management of tool listing, per-call authorization, regular and admin tools, original-file transfer script with hash/provenance verification.

## Simplified Technical Choices

Uses python-docx 1.2.0, openpyxl 3.1.5, python-pptx 1.0.2, PyMuPDF 1.28.2, and RapidOCR 3.9.2, reusing mature format libraries and OCR. For this batch of samples, this route does not require pulling in Docling's full model dependencies; actual parsing limitations are explicitly documented, and complex samples will be optimized later in a targeted way.

Vectors use FastEmbed 0.8.0/ONNX with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions, as a lightweight starting point; BGE-M3, which has not yet been tested, was not adopted. First-time download, loading, and the two embedding passes on this machine took about 95 seconds in total, **including download, not query latency**. Actual file fingerprint `5c77a9d2efad18b678486b45e7858af72b58986d1d1f018523e729f69608d29f`; the index identity also includes the name, dimension, FastEmbed version, and `native-v1`.

For legacy `.doc/.xls/.ppt`, no conversion service is installed; at this stage they are only saved with an explicit prompt to convert to newer formats. CAD/model packages are stored as originals and can be linked to explanatory attachments, without pretending that drawings/geometry have been understood. The multilingual model name does not mean all languages or real industry materials have been accepted; business-sample validation remains a next step.

References: [PyMuPDF page and table APIs](https://pymupdf.readthedocs.io/en/latest/page.html), [RapidOCR official implementation](https://github.com/RapidAI/RapidOCR), [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). The implementation uses the pinned-version APIs on this machine and does not mix in SDK 1.x examples.

## Test Evidence

Final full regression: **59 passed**, about 20 seconds. Uses real PostgreSQL/pgvector, freshly synthesized files each round, isolated schemas, and actual HTTP MCP sessions. Fault injection is used only for the explicit timeout, model-failure, and transaction-failure scenarios.

Coverage:

- DOCX body/tables, Excel formulas and missing-cache explanations, PPT source locating, native PDFs, scanned-PDF OCR, preservation of long conditional paragraphs, attachment-type fallback.
- Vector failures retain text, expired leases reject stale processes, withdrawn drafts are not restored by old jobs, timeout termination, attempt caps, and index transaction failures preserve the old generation.
- Model numbers are not cross-matched, sources are consistent, private/cross-department permissions, old-revision/withdrawn filtering, and historical records from different business periods remain searchable.
- Keyword fallback, no mixing of model spaces, stale locator rejection, pagination over complete units, empty backgrounds returned honestly, and paging through a fixed candidate pool with no duplicates or omissions.
- Actual HTTP MCP initialization, tool listing, search/read, upload/submit, concurrent duplicate preparation, admin permissions and publish retries, rejection of forged fields.
- Real DOCX round-trip consistency, no tokens sent to other origins, no redirect following, and corrupted downloads do not produce final files.

Ruff lint/format checks and the wheel build pass. Upstream Starlette/AnyIO deprecation notices and FastEmbed's historical mean-pooling notice remain visible; assertions were not loosened and the notices were not suppressed. The FastEmbed version is currently pinned and the index identity is tagged, with no old mean-pooling index mixed in.

## Real Codex Verification

`scripts/probe_business_mcp.py` creates a brand-new isolated test schema, submits, processes, and publishes synthetic materials, and gives Codex a read-only member role. Only a temporary one-off MCP configuration was made; the global client configuration was not modified and no real company data was used.

Codex actually called `kb_search` → `kb_read` and answered **AX-210 has a maximum of 80°C, applies only to the standard configuration, and is not suitable for explosion-proof environments**, citing paragraph 2 of "Synthetic Product Parameters". Both calls succeeded.

The two tool result texts total **1,291 characters**, not a token count. The Codex CLI reported cumulative input of 74,380 tokens, of which 67,328 were cached input; this includes pre-existing client instructions, tool definitions, and repeated context, so it cannot all be counted as knowledge-base content consumption, nor can this run be used to claim a savings ratio. This batch's implementation avoids loading the whole library; real business token/quality benchmarks are deferred to later verification.

This result proves the business MCP lookup chain is usable; it does not replace the step 6 acceptance criterion of "proactively identifying knowledge needs and applying them in normal business tasks". Real configurations for Accio and web/DingTalk were not provided and remain unintegrated.
## Review and Fixes

Scope of the independent read-only review: `e96bd3c..fdc799c`, followed by re-review of the fixes.

| Major Issue | Fix |
|---|---|
| DOCX text boxes could be missed yet still marked as complete | Detect uncovered structures such as text boxes and return explicit limitations with a partial status; added regression tests for text boxes and header tables |
| PDF text blocks spanning table boundaries were filtered out entirely, causing conditions to be lost | Filter only text blocks fully contained within a table, preserving cross-boundary conditions; regression verified that NOT EXPLOSION PROOF is retained |
| The candidate pool grew with offset, changing fusion ranking | The content candidate pool is now fixed; after reproducing on the actual database, duplicates/missing items were fixed, and only short snippets are extracted at the SQL layer |

The reviewer's re-review confirmed that all three issues were resolved, with no regressions introduced by the fixes. The primary executor completed the final full test suite. **This batch's review is complete; there are no unresolved major review issues.**

## Follow-up Boundaries

Formal deployment, independent backup restoration, the employee delegation chain, and capacity acceptance have not been implemented, so no claim is made that the 50/200 Agent targets have been met. Formal distribution of models and dependencies, service accounts, network trust, and operational recovery will be implemented during the deployment phase.

As agreed with the user, work stopped at the end of this batch; steps 6, 7, and 8 (integration testing of real tasks and entry points, deployment with backup and restoration, and first-version acceptance) will begin only after confirmation.
