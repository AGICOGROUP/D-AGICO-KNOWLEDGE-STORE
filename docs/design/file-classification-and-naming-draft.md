# Company Knowledge Base File Classification and Naming Rules (Discussion Draft)

Date: 2026-10-12. The naming skeleton follows the draft reviewed by leadership; the temporary numbering for projects whose deals are not closed and the method for recording projects will be determined in follow-up discussions.

## I. Design Basis and Scope

The existing knowledge base materials reviewed include *AI_Suggestions_Issues_and_Core_Requirements_Summary-1.xlsx* and *AGICO_Human_AI_Cement_Calcium_Magnesium_Industry_Chain_Institute_Planning_V2.1_CN.pdf*, as well as the client basic design input list, the filter bag brochure, the scope of supply, the process equipment list, and drawings. Some items in the requirements summary are noted as "summarized from requirements," and the industry chain design institute document is an "internal planning recommendation draft"; they should not be treated as approved organizational policies.

These materials show that business documents span international sales, customer research, mechanical equipment and engineering technology, procurement and supply chain, goods inspection and delivery, marketing assets, HR and finance, and internal management. The complete product boundaries of each business division, existing project codes, order codes, and the formal drawing numbering system have not yet been fully verified.

The approach adopted is to classify by business purpose and name by applicable object. Business division, business domain, document purpose, document format, and permissions are recorded separately and cannot substitute for one another. PDF, Excel, CAD, and images are formats and are not used as primary business classifications.

## II. Suggested 10 Primary Business Categories

| Primary Category | Common Specific File Types | Applicable Objects and Search Clues |
|---|---|---|
| 1 Company and Product Promotion | Company profile, product catalogs, product brochures, promotional images, promotional videos, factory profile, external project casebooks | Company, product, model, application scenario, language |
| 2 Customer and Market Materials | Inquiries, customer requirements, customer background checks, communication minutes, market research, competitor materials, trade fair intelligence | Customer, country, project, communication date |
| 3 Sales and Commercial Documents | Quotations to customers, sales contracts, technical and commercial agreements, scope of supply, commercial terms, negotiation records | Customer/project, quotation number, contract number, date |
| 4 Technical and Engineering Design | Technical proposals, process descriptions, parameter tables, calculation sheets, equipment lists, design inputs, process/mechanical/civil/electrical drawings | Product, model, project, drawing number, discipline, design stage |
| 5 Procurement and Supplier Materials | Supplier profiles, supplier quotations, price comparison sheets, purchase contracts, procurement technical agreements, order follow-up sheets | Supplier, purchase order/project, equipment, date |
| 6 Production and Quality Materials | Manufacturing records, inspection reports, goods inspection records, test reports, certificates of conformity, acceptance records, quality issue analyses | Project/order, equipment, batch/serial number, inspection date |
| 7 Delivery and Logistics Documents | Packing lists, shipping marks, commercial invoices, packing plans, loading supervision records, bills of lading, customs declaration and transport documents | Project/order, shipment batch, case number, date |
| 8 After-Sales and Operation & Maintenance | Installation instructions, operation manuals, maintenance manuals, spare parts lists, failure cases, after-sales reports | Product/model, customer project, equipment serial number, failure topic |
| 9 Standards, Processes, and Templates | Industry standards, technical specifications, work SOPs, customer survey templates, quotation templates, inspection templates, business training materials | Standard number, applicable business, applicable product, publication/implementation version |
| 10 Business Operations and Internal Management | Strategic planning, management policies, operating reports, budgets, financial materials, recruitment and HR materials, internal meeting minutes | Responsible department, management topic, reporting period |
"Other/Uncategorized" serves only as a temporary holding place at upload time; specific types will be added later as actual new needs arise, and it is not treated as a long-term primary category.

### Easily Confused Boundary Cases

- Quotations given to customers belong to Sales; quotations from suppliers to the company belong to Procurement. The specific types "Customer Quotation" and "Supplier Quotation" must be used respectively.
- Blank general quotation templates belong to Processes and Templates; quotations filled in with customer information belong to Sales.
- Product catalogs belong to Marketing; equipment technical parameter sheets belong to Technical. The same product may be linked to two documents with different purposes.
- External-facing project casebooks belong to Marketing; customer project execution materials are classified by their specific purpose—Technical, Commercial, Quality, etc.—and share the same project object.
- Design drawings belong to Technical; quality inspection records belong to Quality. An inspection record must not be filed as a drawing merely because it contains a schematic diagram.
- Contract attachments are classified by their content and linked to the parent contract and project; they must not be uniformly placed under "Other".
- For multi-purpose files, choose one primary category and add purpose tags; do not duplicate originals into multiple copies just for classification.

## III. A Universal Naming Skeleton

```text
ApplicableObject_FileType_ContentTopic_[BusinessDate]_Language_Version.Extension
```

The square brackets only indicate that the date is a conditional field; actual names do not include the brackets. You are not required to force every attribute into the file name.

| Field | Rule |
|---|---|
| Applicable Object | Dedicated files should preferentially use a registered project number/order number/customer abbreviation; general files use product/model/business scope, e.g., "Lime Production Line", "AGICO", "Procurement Business" |
| File Type | Chosen from the unified list, e.g., Customer Quotation, Process Flow Diagram, Equipment List, Packing List, Customer Survey Template |
| Content Topic | Briefly answers "what exactly does this document cover"; may be omitted when the type is already sufficiently clear, to avoid repeating the topic |
| Business Date | Used for materials where time distinction adds value, such as quotations, communication records, inspections, logistics, photos, and reports; format YYYYMMDD. Reports use YYYYMM or YYYYQn for the reporting period |
| Language | The system displays Chinese names; file names use ZH, EN, ES, RU, FR, PT, etc.; Chinese-English ZH-EN, Chinese-Spanish ZH-ES. Multiple language versions are linked to the same document record and are not treated as unrelated files |
| Version | For materials compiled by the company itself with no formal revision system, use V01, V02; materials that already have an official version/revision number keep it as-is, and must not be renumbered in a way that masks the original version |

Won projects use the company's official project number; for projects that are not yet won and have no official number, the system registers the project name and supporting evidence and generates a unique temporary number of the form `OPP-Year-SerialNumber`. Matching country, product, and date serve only as candidate clues; evidence such as customer, location, and capacity must also be considered; when attribution is unclear, the case is referred for manual confirmation. After a deal is closed, the association between the official number and the temporary number is kept, the official number serves as the canonical title prefix, and the old number retains search capability.

The business date is determined by the document's actual nature: quotation date, signing date, inspection date, shipment date, meeting date, etc.; the upload time must never be passed off as the business date. When no reliable date is available, leave it blank and pending confirmation. Contract effective date, standard implementation date, original publication date, and the like are stored separately as attributes and must not be conflated into one vague "date".

"Upload time, submitter, business division, permissions, and review status" are recorded by the system and are generally not included in the name. If externally exported files need organization/brand identification, it can be added by the export rules; employees are not required to enter it repeatedly in every name.

## IV. Three Common Variants

### 1 General Product/Knowledge Materials

```text
ProductOrApplicableScope_FileType_Topic_Language_Version.Extension
```
Examples:

- AGICO_Brochure_FilterBag_EN_V01.pdf
- BeamKiln_TechnicalDescription_WorkingPrinciple_ZH_V01.docx
- LimeProductionLine_CustomerSurveyTemplate_BasicDesignInput_ZH_V01.xlsx
- Procurement_SOP_SupplierPriceComparison_ZH_V01.docx

### 2 Customer/Project/Order Execution Materials

```text
ProjectOrOrder_DocumentType_Topic_[BusinessDate]_Language_Version.Extension
```

Examples:

- Argentina400TPDLimeProject_CustomerQuotation_EquipmentSupply_20261008_ES_V02.xlsx
- Argentina400TPDLimeProject_EquipmentList_CombustionSystem_ZH-ES_V01.xlsx
- SomeOrder_PackingList_SecondBatchShipment_20261008_EN_V01.xlsx
- SomeProject_InspectionReport_ReducerFactoryTest_20261008_ZH_V01.pdf

"SomeOrder/SomeProject", the example dates, and the versions are only for demonstrating the rules; they are not formal codes and are not directly assigned to existing files.

### 3 Engineering Drawings: Retain Engineering Identifiers

```text
ProjectOrProduct_[OfficialDrawingNumber]_DrawingTitle_Language_[OfficialRevisionNumber].Extension
```

Example with a drawing number: `ProjectAbbreviation_OriginalDrawingNumber_RotaryKilnGeneralAssemblyDrawing_ZH-EN_OriginalRevisionNumber.dwg`. Example without a drawing number: `10TPHCalciumHydroxide_ProcessFlowDiagram_ZH.dwg`. The square brackets indicate that the field is filled in or omitted depending on the existing documents; actual file names do not include the square brackets.

- Official drawing numbers and revision numbers follow the drawing title block and the existing registration system; they must not be fabricated by AI.
- The DWG and exported PDF of the same revision use the same base name, differing only in extension, and are linked in the system.
- When the original drawing has no drawing number or revision number, the corresponding field is simply omitted; obtaining a drawing number is not made a condition for uploading or publishing, and AI does not generate official drawing numbers.
- Chinese, translated, bilingual, and annotated versions record language and derivation relationships separately; an update to a translated version does not automatically mean the engineering design was revised.
- For drawing materials with external references, accompanying directories, or signature packages, keep the internal file names and paths; the knowledge base only needs the standardized display name, to avoid renaming breaking the associations.

Standard documents also retain the standard number and the edition of the standard's publisher, e.g. `TB-T2629-2023_RailwayBallastedTrackConcreteElasticSleeper_ZH.pdf`; they are not arbitrarily converted into a company V01. Drawings contained within a standard do not change that standard document's business classification.

## V. The Few Rules That Must Be Unified

1. Only the field order, type words, object abbreviations, and the date, language, and version expressions are unified; topics in both Chinese and foreign languages may be retained.
2. Underscores separate fields; avoid using underscores within a field as much as possible, to reduce ambiguity.
3. Do not use base names with no business meaning, such as "New Document", "Final Version", "Final Final Version", "Revised", "Latest Version", or "Zhang's Spreadsheet".
4. The formality of a file is distinguished by business status and approval status. "Published" is not equivalent to a signed contract or an approved design, and it is not written into the file name.
5. A difference in language does not require changing the content version; translation revisions, design revisions, and knowledge base upload revisions are recorded separately.
6. Fields that cannot be confirmed before upload are not guessed by AI. The system proposes the name, and the person in charge confirms the type, object, and engineering identifiers.
7. Source files keep their original names and the originals; the standardized name serves as the knowledge base display title/alias. Digital signatures, drawing references, and source traceability are not affected by the naming convention.

## VI. Knowledge Base Fields and the Current Implementation Process

In the current web upload flow, the employee selects the business division, uploads the original file first, and submits it. After submission, the backend hands the runtime rules file `src/agico_kb/ai_naming_rules.md` and the project records the AI is authorized to access over to the AI to determine attribution; materials of the same project under different names share the official or temporary number, and then enter the parsing queue. When evidence is insufficient or the AI is temporarily unavailable, the person in charge confirms the name before parsing. Temporary numbers are generated by the database; official project numbers, drawing numbers, dates, or versions must not be guessed by AI. Project numbers and aliases are persistently stored in the `projects` table, and naming and parsing progress is kept in the existing task status, without relying on the AI to maintain its own Markdown memory.
The system retains: original filename, canonical title, primary business category, specific type, business division, customer/project/order, product model, drawing number/discipline/revision, file date or period, language, source party, original/translation/export relationship, permissions, and approval status.

Recommended process:

```text
Select original file and business division → Upload original file → Submit
→ AI proposes a canonical name based on runtime rules, the original filename, and visible project records; the database assigns or reuses a project number
→ Name unambiguous: parse; name ambiguous: the responsible person verifies, then parse
→ Responsible person reviews → Canonical name officially displayed
```

Naming confirmation must not be treated as successful parsing, and AI guesses made before a drawing number has been extracted must not become official engineering numbers. Search simultaneously supports canonical titles, original filenames, project/customer abbreviations, product models, and parsed content; filtering by attributes is more reliable than relying on long filenames alone. The business division identifier in a filename must not determine access permissions.

## 7. Implementation Order and Items to Verify

1. First, settle these 10 primary categories and the commonly used specific types, seeking input from the people actually responsible for the documents.
2. Trial-apply the rules to typical files: pick a few samples each of product marketing materials, customer quotations, procurement contracts, equipment lists, engineering drawings, inspection reports, packing lists, SOPs, standards, and business reports, to identify the fields employees find hardest to fill in.
3. When the same project already has an official number, prefer to reuse it; if no deal has closed and there is no official number, the system assigns a stable temporary number. When a CAD drawing has an official drawing number and revision number, keep them as-is; if not, omit them. Gradually organize existing names for customers, products, and projects as search aliases; there is no requirement to establish a company-wide unified abbreviation table in advance.
4. Newly uploaded files get canonical titles first; legacy files keep their originals and are gradually organized through reviewed titles/aliases, with no physical renaming across the entire repository.
5. Automatic naming and manual verification in the upload interface are already implemented; the ten primary business categories, alias registration, and additional attribute filtering will continue to be advanced based on actual usage, and none of these should block existing files from being ingested.

There is currently no need to draw up completely different rules for each department upfront. A shared skeleton plus a small number of necessary exceptions can be expanded step by step as the company's actual business grows.
