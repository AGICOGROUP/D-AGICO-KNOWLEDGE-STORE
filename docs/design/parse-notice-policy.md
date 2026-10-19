# Parsing Alert Handling Strategy

## Principles

Fix parsing capability first, then reduce problem alerts. Alerts must not be made to disappear by hiding actual gaps, lowering standards for ordinary documents, modifying source files, or manually backfilling production results. Unknown alerts are retained by default.

## Three Categories of Alerts

1. Actual problems: content not extracted, number conflicts, unreadable fields, conversion failures, unavailable interfaces. Fixed by the generic process and clearly displayed while unresolved.
2. Normal process: OCR or transcription completed, null characters cleaned up, formula caches retained, saved page by page, queued for continued processing. Not shown as content problems in the staff interface; kept in internal diagnostics.
3. Source notes: drawings must be read against the original image; body text revised; only the original is saved. Each document is merged into a single short note.

## Project Capabilities

- Word captures footnotes, endnotes, and their tables, preserving body-text references; Excel captures native text boxes plus worksheet and cell anchors. Genuinely missing charts and formula results are still flagged.
- The OCR route for PDF body text is determined jointly by layout, confidence, and unexplained ink; tolerance, tables, illustrations, or file names alone must not lower the body-text acceptance standard. Pure-OCR completion does not depend on vision API configuration.
- PDFs are examined page by page to identify drawings. An explicit drawing title, together with independent evidence such as scattered layout, title blocks, and views, is used to distinguish drawings; large outline-font drawings can be identified by combining multiple engineering title-block fields with dense vector evidence. Coverage includes Spanish section/view labels, Croatian title blocks, and scanned test figures. A test figure must simultaneously have a figure-number field, multiple views, explicit drawing units, and a scattered layout; it must not be classified on "illustration" or "appendix" wording alone. When classification is uncertain, full-text processing continues.
- Substitute characters, control characters, and abnormal glyph clusters in the font mapping trigger pixel OCR. Once mapping corruption is confirmed, abnormal short fragments on the same page are also not stored as native reliable text; their visible content is recovered by OCR. Body-text completeness cannot be judged from the number of garbled characters alone.
- Drawings capture the drawing title, drawing number, project, description, itemized details, and original-image location; isolated dimensions and geometric relations are not transcribed. Key OCR fields are rechecked by the vision API per original-image region, with requests merged for adjacent regions; the description/detail region includes the body text below the title. Text outside the native-reliable fields and recheck regions is retained.
- Rechecks may only correct OCR against original-image pixels. When a field is illegible, other fields already recovered are still captured and the specific problem is retained; fields that are absent or left blank must not be passed off as failures. API interruptions use normal checkpoint-resume processing, caching the source region; there is no manual daily quota.
- Ordinary tolerance body text, short-column specification sheets, and Chinese line-wrapped body text retain full-text semantic processing; a table of contents mentioning "drawings" does not trigger a full-drawing dimension check.
- Number checks treat only the explicit decimal-point formats 20.00/20 as equivalent. 330/350, omitted symbols, and diameter/radius confusion remain problems. The meaning of multi-segment numbering and commas is not normalized by guesswork. OCR evidence is not the ground truth; checks must not be made to pass by stuffing numbers into the output.
- Repeated headers and footers may be filtered only with evidence spanning multiple pages and margin positions; body-text numbers are not filtered merely for being identical. Segment status is aggregated according to actual completion, and internal diagnostics are not truncated so that later problems are not missed.
- When segmented parsing encounters a single transient subprocess failure, the project automatically retries that segment once; persistent failures still record the specific unfinished page numbers instead of clogging the queue with unbounded retries. Database connection establishment has a time limit so the background does not linger on unresponsive connections.

## History Records

The display strategy applies to history refreshes. Real content gaps must be re-parsed by the project background, with the index and diagnostics updated after a normal Worker run completes; source files and publication relationships are preserved, and an existing valid index keeps serving until the new generation completes. Production segments must not be edited directly, nor real alerts manually cleared. The parser and the number-evidence cache have been updated to new versions; results from the old route must not be passed off as new parses.
## Acceptance Boundaries

Non-drawing materials are still required to achieve a content accuracy rate above 95%, with key numbers, units, and correspondences verified separately. Drawing checks verify correct positioning against the original image, drawing titles and numbers, notes, and itemized details. OCR extraction volume, confidence, process completion, number of tests passed, and number of reminders do not equal content accuracy.
