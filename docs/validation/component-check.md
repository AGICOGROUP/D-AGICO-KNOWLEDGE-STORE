# Step 0: Development Environment Component Verification

Date: 2026-09-15. This machine is a development environment and has not been designated a company production server; no company business files were imported.

| Item | Verified Result |
|---|---|
| Windows | Windows 10 Pro 10.0.19045, 64-bit, 16 logical processors, approximately 32 GB RAM |
| Python | uv creates a project-specific Python 3.12.14 environment; dependencies pinned in uv.lock |
| Database | PostgreSQL 16.15 running natively on Windows at 127.0.0.1:15432, development directory .local, not a system service |
| Vector Extension | pgvector 0.8.6; actual CREATE EXTENSION and vector cosine distance SQL passed |
| Native Text Path | A synthetic DOCX was created each time; 3 paragraphs extracted via python-docx, with Chinese text, model numbers, and units consistent paragraph by paragraph |
| Local Vector Path | FastEmbed/ONNX, BAAI/bge-small-zh-v1.5, 512 dimensions; a Chinese temperature question correctly hit the paragraph containing AX-210 and 80 degrees Celsius, cosine similarity approximately 0.756 |
| Codex | Temporary Streamable HTTP MCP + environment-variable Bearer; a real client discovered and invoked the read-only test tool, which returned AGICO-PROBE-OK and synthetic=true |
| Accio | Name confirmed by the user; actual version and integration environment not yet provided; to be verified in practice in Steps 5/6 |

The first MCP probe lacked read-only annotations, and the client refused to invoke it. After adding accurate annotations such as readOnlyHint for the actual read-only tool, the retry succeeded, without changing the approval policy, global MCP configuration, or permissions.

## Route and Boundaries

No existing complete knowledge platform suitable for direct integration has been found so far; the planned Python + PostgreSQL component combination is adopted. It has not been ruled out that a platform may already exist in other company environments, and no additional separate RAG platform has been built.

The lightweight DOCX / Chinese vector probes were used to confirm that the minimal native Windows route is feasible; they do not mean that Docling, OCR, complex tables, and BGE-M3 have passed. Full parsing and model selection will be decided in Step 3 using representative samples. No 50/200 concurrency capacity acceptance testing was performed.

The database runtime packages—PostgreSQL 16 / pgvector Windows packages—were obtained from conda-forge via official micromamba; native, isolated installation without Docker or WSL. The production server's operating system, disks, service accounts, TLS, and backup locations still need to be verified at deployment time.

Reproducible entry points: `scripts/probe_components.py`, `scripts/probe_mcp.py`, `scripts/prepare_dev_db.py`. Local results are saved in the Git-ignored `.local/probe/`, and credentials are not committed to the repository.

Sources: [pgvector Windows/Conda support](https://github.com/pgvector/pgvector), [micromamba installation](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html).
