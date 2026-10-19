# Business Quality Acceptance Matrix

## Boundary of Conclusions

The automated results from 2026-09-16 come from entirely fresh synthetic materials and only demonstrate that retrieval, source reading, and permission rules can be machine-verified. They do not represent the quality of the company's real materials, nor can they replace the business unit owner's signed confirmation of the applicability conditions for background, facts, specifications, templates, and cases.

Formal business acceptance must first fill in and freeze, in `tests/load/acceptance-profile.json`, the material quantity/capacity/chunks/model identity, representative questions, target origin, server and client descriptions, all SLOs, and the business owner's name and sign-off status; missing any of these required fields blocks a formal run.

## Coverage Matrix

| Capability | Synthetic automated check | 2026-09-16 result | Still required for formal acceptance |
|---|---|---|---|
| Background understanding | Business-unit background documents and organization-scoped reading | Background control sources are searchable and readable | Business owner annotates the enterprise background to be adopted and the risks of missing background |
| Fact lookup | Emergency shutdown authorization codes, verifying document/version/chunk and original text | All exact-code payload samples passed; natural-language fact observation target ranked 1st, no degradation, 61.348 ms | Re-verify with real product facts and acceptable wording |
| Specification application | Current inspection intervals and isolation of old revisions | All current-revision samples for both business units passed | Owner confirms specifications, templates, and exception conditions |
| Experience reference | Field experience materials and applicable business units | Imported and readable by organization | Owner judges whether a case applies to a new question; the model cannot self-assess |
| Document lookup and full-text reading | Same closed loop: search first, then read the source | All document samples passed; complete cycles strictly 1:1 | Re-verify file names, sources, and full text at the integrated entry point |
| Same name across departments | Both business units have a "Joint Delivery Checklist" | Each identity only hits its own business unit's version | Use real same-named documents to verify organizational attribution |
| Similar models | `AX-620/AX-620X` and `PX-410/PX-410X` filed as separate records | Synthetic materials retain distinct pressure parameters | Re-verify with real similar-model tasks that parameters must not be mixed up |
| Historical business and old revisions | 60-day-old version superseded by the 30-day current version | All current-revision payload samples passed; the old version is not used as a formal source | Business owner confirms whether historical materials may still be cited |
| Unknown questions | Nonexistent "quantum entanglement repeater" document | All no-answer document samples passed | Add annotations permitting "no answer" to the real question set |
| Permission denial | Private quotation document authorized to only one person; checks by other identities forbid document_id, and reads return 404 | Search did not leak restricted documents; all 1,951 expected 404s in REST were judged correct business results | Re-verify with the target identity directory and the real authorization policy |
| Takedown | Searchable and readable before takedown; that version is forbidden after takedown | The taken-down version no longer appears; full-text reads return 404 | Integrated clients verify caching and takedown propagation |
| CAD/3D originals | STEP stores only the original; full-text capability is not fabricated | Originals published as file-only and included in the material snapshot | Real CAD/3D files require verification of download, the associated description, and how clients open them |
| Original file immutability | Downloaded with historical permissions before and after takedown and compared via SHA-256 | The 32 MiB new binary original has a consistent SHA-256 before and after real API/worker restarts; see the process restart record | Measure representative large files separately; do not mix in small full-text latency |

## Semantic Quality Observations

After the bounded-wait fix, the latest 11,714 REST and 7,347 MCP complete query/read cycles were all correct, with no degradation; sources still require the correct document/version/chunk and original text, and HTTP success is not accepted as a substitute. The first REST semantic query's source ranked 2nd, with no degradation. See [current load evidence](load-50.md); the original 840 failed semantic cycles belong to pre-fix records.

[Real Codex general tasks](codex.md) separately demonstrate the application of background, facts, specifications, and experience to results; the load matrix above has no answer-generation phase of its own. Character counts are not tokens; business quality is still confirmed by the actual materials and the owner.

[Process restart and 32 MiB file evidence](process-restarts.md) supplements local file verification; it does not cover opening in the company's actual CAD/3D software or assembly dependencies.
