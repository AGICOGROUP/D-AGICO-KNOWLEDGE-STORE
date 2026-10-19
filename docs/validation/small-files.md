# Office/PDF/CAD Small-File Ingestion Test

2026-09-16. Per the user's request, 3 files each of Office, PDF, and CAD were selected, 9 files in total, with the original files totaling 799,134 bytes (780.4 KiB). Office covers one file each of Word, Excel, and PPT, selecting the smallest non-temporary file for each format; for PDF and CAD, the smallest 3 files of each were selected, excluding lock files, backups, and auto-save copies.

## Files and Results

| Type | Anonymized sample (original file list kept in local test records) | KiB | Processing result |
|---|---|---:|---|
| Office | Sample 01.docx | 13.6 | Body text retrievable and readable |
| Office | Sample 02.xlsx | 10.3 | Body text retrievable and readable |
| Office | Sample 03.pptx | 278.8 | Body text retrievable and readable, incompleteness warning retained |
| PDF | Sample 04.pdf | 57.3 | Body text retrievable and readable, incompleteness warning retained |
| PDF | Sample 05.pdf | 91.6 | Body text retrievable and readable, incompleteness warning retained |
| PDF | Sample 06.pdf | 112.2 | Body text retrievable and readable, incompleteness warning retained |
| CAD | Sample 07.dwg | 39.9 | Original file only |
| CAD | Sample 08.dwg | 86.3 | Original file only |
| CAD | Sample 09.dwg | 90.3 | Original file only |

## Verification Conclusions

- 9/9 passed brand-new upload and submission, private test release, filename search, and MCP file lookup.
- 9/9 downloaded SHA-256 values matched the source files; source files were unchanged; unauthorized test readers cannot see the material or download it.
- 6/6 Office/PDF samples passed body-text fragment retrieval, reading with source attribution, and MCP/HTTP body-text consistency checks. Fragment queries were derived from extraction results and served to verify the indexing and read-back pipeline; they were not an independent business Q&A accuracy test.
- 3/3 PDFs were scanned drawings processed via OCR, with an explicit notice that recognized text, table, and graphic semantics may be incomplete; no manual acceptance of dimensions or OCR accuracy was performed this round. PPT gave a notice that images/graphics are not interpreted; Excel retained formulas and existing cached values, without recalculation.
- 3/3 CAD explicitly returned the original-file-only capability, and MCP reads produced no fabricated body text.
- Original-file reconciliation covered 33 references in total (including samples withdrawn in previous rounds); missing, corrupted, and orphaned files and stale temporary files were all 0.
## Issues Fixed During Testing

The first round uncovered a retrieval miss for complete CAD filenames containing full-width parentheses: the query was Unicode-normalized first, while database titles/filenames were only lowercased. The same SQL NFKC normalization and case handling is now applied to filenames, titles, and queries; the permission, model-filtering, and body-retrieval paths are unchanged. Two new real-database regression tests, for title and filename respectively, cover full-width/half-width variants, German characters, and cross-department rejection; they failed first and then passed.

After the fix, the test API/worker were restarted, all 9 documents were re-uploaded and fully retested, with no reuse of first-round processing results. The final batch `4cad93b9804e44559ec2fe12df53a95b` took 18.609 seconds (from that batch's upload to completion of MCP validation, excluding troubleshooting and fix time). The 9 versions from the first-round batch have been taken down via the admin API; the audit records and originals are retained. The 5 previously valid test samples remain in place, for a total of 14 valid versions.

The full test suite this time came out at **136 passed / 2 skipped**; the two skipped items are fault-restart drills that require explicit enabling and were not repeated this round. Ruff checks passed, and a targeted read-only review passed, with no unresolved Important/Critical findings.

## Records and Scope of Use

The complete results (including version IDs, hashes, processing warnings, validation results, and partial extracted text) are kept in a protected directory: `D:\EnterpriseKnowledgeBase\validation\4cad93b9804e44559ec2fe12df53a95b\results.json`; the original files, body text, and tokens were not committed to Git.

The samples are saved as private test material under the "Company Public Knowledge" category; no real business division was assigned, and they have not been opened to regular employees. The current services continue to run locally.
