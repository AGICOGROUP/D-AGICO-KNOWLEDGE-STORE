# Enterprise Knowledge Base Classification Scheme

Date: 2026-09-15
Status: Determined based on the organization screenshot and authorization provided by the user, and implemented as backend classification configuration; so far only isolated synthetic test materials have been imported, and no official company documents have been imported.
Scope: The organizational partitions, material classifications, and retrieval rules of the unified knowledge base. Subsequent adjustments are consolidated and superseded within this document, avoiding appended contradictory conclusions.

## 1. Design Conclusions

Adopt a two-level browsing structure of "organizational partition → material purpose," combined with filters such as product, model, project, and file format. By default, no additional fixed directory levels are added.

Two types of needs share this organizational scheme: Agents such as Codex and Accio use MCP to learn the company's background, facts, standards, and experience on demand, so that various answers and task results better fit the company's needs; employees delegate Agents, through the existing web / DingTalk entry points, to upload, search for, and retrieve files. The classification serves general-purpose enterprise knowledge expansion; specific business examples do not determine the system's main workflow.

These are logical partitions within the same backend. It does not require each department to deploy separate systems, databases, or vector services, nor does it require employees to maintain index directories. Original files, database records, and content indexes use consistent file identifiers, organizational attribution, and version associations.

## 2. Level-1 Organizational Partitions

The following 7 names are retained as shown in the user's screenshot. The screenshot does not provide the reporting relationships between the organizations or their business content, so they are treated for now as parallel material-attribution partitions, without inferring each department's business direction.

| Order | Partition Name | Basis for Setting |
|---|---|---|
| 1 | General International Group | User screenshot |
| 2 | Best Business Unit | User screenshot |
| 3 | Xingyuan Business Unit | User screenshot |
| 4 | Baichuang Business Unit | User screenshot |
| 5 | Aijike Business Unit | User screenshot |
| 6 | Jimuke Business Unit | User screenshot |
| 7 | Market Intelligence Management Center | User screenshot |
| 8 | Company Common Knowledge | Added by this scheme; used for confirmed general company background, common policies, unified terminology, and general templates |

"General International Group" is retained as a separate partition per the screenshot and is not directly equivalent to "Company Common Knowledge." If it is later confirmed that the two have completely overlapping material responsibilities, they may be merged; there is no need to maintain two copies of content simultaneously.

"Company Common Knowledge" is the name of a material purpose and attribution; it does not mean public on the internet, nor does it imply that all materials within it are automatically visible to all employees. Actual visibility is enforced according to authorization.

## 3. Level-2 Material Classifications

All organizations use the same simple set of categories, applied as needed; empty categories need not be shown in everyday browsing. Category descriptions are used for ingestion and lookup, and do not presuppose that the business units share the same business.

| Category | Applicable Content |
|---|---|
| Organization and Business Background | Departmental responsibilities, business scope, product line overviews, business terminology, and necessary company introductions |
| Product and Technical Materials | Product manuals, parameter tables, technical standards, drawings, CAD, 3D model packages, BOMs, installation and maintenance materials |
| Project and Customer Materials | Project requirements, project proposals, customer correspondence, historical quotations, contracts, and project deliverable documents; managed according to actual permissions |
| Process Standards and Templates | Business processes, work requirements, operating procedures, report templates, quotation templates, and other reusable work templates |
| Experience Cases and FAQs | Project retrospectives, fault handling, lessons learned, common Q&A, and typical cases; retain sources and applicable conditions |
| Other Materials | Files that do not yet fit the above categories; collect them first and adjust later according to actual needs |
Just select the primary use as the single main category. Specific product manuals go under "Product & Technical Documentation"; quotation templates intended for repeated filling go under "Process Standards & Templates"; quotations actually produced for a given project go under "Projects & Customer Records". A file is not duplicated for multiple uses; it can be found by different tasks through its associated information.

"Pending Confirmation", "In Processing", "Published", and the like are statuses, not second-level document categories, and no business directories with those names should be created.

## 4. Formats, Products, and Projects Are Expressed as Filter Attributes

Do not fixedly create format folders such as PDF, Word, Excel, PPT, or CAD under each department. A product's manuals, specification sheets, and drawings should be findable together; formats are automatically recognized by the system and offered as filters.

The first version retains the following basic information:

- System records: file identifier, file name, format, size, uploader, time, storage location, processing status, version, and publication status.
- Organization and purpose: primary owning organization, one main category, and the actual access scope.
- Filled in as needed: product/model, project/customer identifier, document/drawing/part number, business date, and related files. The business date is the document's own quotation/project date, not substituted with the upload time.

Documents without a product or project are not forced to fill in these fields. Employees are not required to complete a long list of tags when uploading; information can be brought in from the selected partition and the current task, supplemented by suggestions from the Agent, with the responsible person checking the required information at publication.

Fields such as product, model, and project carry cross-format association and precise filtering; vector retrieval supplements semantic search and is not used to determine permissions, official versions, or organizational ownership.

## 5. Upload and Classification

1. First save the original file and the submission record; the partition specified by the upload entry serves as the ownership candidate, and the backend validates submission permissions.
2. If the user does not specify one, the department corresponding to the trusted login identity may be adopted as the initial ownership candidate, without automatically publishing the file or expanding access permissions on that basis.
3. The backend automatically identifies the file format and performs supported parsing, keyword indexing, and vector indexing; the Agent may propose document category and product/project association suggestions based on existing capabilities, without adding a dedicated classification Agent.
4. When ownership is unclear, keep the submission pending confirmation and retain the controlled submission visibility scope; it must not be placed directly into company public knowledge or shared with other departments merely for the convenience of classification. If the main category is unclear, "Other Documents" may be used temporarily.
5. Before publication, an authorized responsible person confirms ownership, the necessary associated information, version, and visibility scope. Completing processing does not mean automatic publication.

File classification does not block saving the original file. Unparseable CAD files, model packages, or abnormal files can still be registered, searched, and retrieved by basic information; failed processing can be retried, and the current content-reading capability is clearly labeled.

## 6. Cross-Department Documents and Permissions

- A document retains one primary owner and one set of versions, maintained by the corresponding responsible person.
- When used by multiple departments, set an appropriate sharing scope or associated references on the original record instead of copying multiple copies per department.
- Documents confirmed as company-wide general material may be placed under "Company Public Knowledge"; official product documents of a specific department, even when shared across departments, may continue to be managed by the original department.
- Department partitions are used for organization and retrieval; actual access permissions are enforced by the backend and cannot be controlled solely by folder names or the Agent's self-declared identity.
- Not selecting a department does not mean access to all documents; search results, summaries, full texts, and attachments return only content the caller is authorized to obtain.
- A partition may contain documents with narrower restrictions; an employee's membership in the department does not automatically grant access to all files. Specific scopes use the unified backend permission capability, and the first version does not build a separate complex approval system.

## 7. Agent Search and Reading
Prioritize files, organizations, products, models, or project conditions that are explicitly specified in the task. The scope explicitly specified by the user is a hard condition; the employee's department can only serve as a clue for query relevance, and must not be used without justification to exclude cross-departmental materials the employee is authorized to view.

- Invocation in Q&A and tasks: look up background, verify facts, apply standards and templates, and reference past experience as needed, handing the results back to the original Agent to understand and use for the current work; do not rely on the employee explicitly requesting a search every time.
- Looking up company or department background: read only the small number of relevant official background entries when the task requires it; do not load the full library catalog or require reading background before every task.
- Finding files: within the scope of permissions, locate them by ownership, name, model, material type, and applicable official version, and return file cards together with fetch references.
- Asking about content: use hybrid keyword and vector retrieval, return relevant original text fragments, file versions, and locations, and expand sections or tables when needed.
- File already specified: search directly within the target version of that file, avoiding another search of the full library.
- Multiple departments have similar products and the task is unclear: return a small number of distinguishable candidates, and if necessary have the Agent ask the employee to choose, avoiding guesswork.
- Source already clear and direct reading supported: allow direct retrieval by file and location, reducing repeated searches.

Business materials from different projects or periods are independent records, categorized by primary purpose and linked through product/project and other information. Confirmed historical records continue to participate in ordinary authorized retrieval; official version filtering is used only for revisions within the same record. A document's earlier date does not mean it is an old version that has been replaced. For example, historical quotations belong under "Projects and Customer Materials" — this is an application of the general rule, and no dedicated quotation classification system needs to be added.

Related public knowledge may be retrieved together when access is permitted and it is relevant to the task. By default, return an appropriate amount of results along with entry points for further reading, and do not hand all categories, file listings, or complete files to the model; strictly fixed per-task token quotas are not set for now.

These are query methods of the same backend and MCP; no separate retrieval-decision Agent or multi-layer memory service is added.

## 8. Example

The following merely illustrates the classification approach; it does not imply that the actual products or existing files of the Best Business Division are known.

```text
Best Business Division
└─ Products and Technical Materials
   ├─ Product A Manual.pdf
   ├─ Product A Parameter Table.xlsx
   └─ Product A Drawing.dwg
```

The three files are linked through identical product/model information, and each retains its own version and publication status. An employee can query "all official materials for this product" or filter for "drawings"; the Agent can search the manual for installation requirements. The source drawing and its preview PDF should be linked to specific versions; they must not be presumed to be the same version based on the file name alone.

External dependencies of CAD/3D files are stored as complete packages; model dependency files are not split apart for the sake of classification.

## 9. First-Release Implementation Boundaries

- Adopt 8 initial organizational partitions and 6 general material categories; treat them as adjustable configuration, not hard-coded into interfaces.
- Two-level browsing by default, with products, projects, and formats available as filters; display groupings will be added later once there is actual demand.
- Do not create an empty business file directory tree in this phase, do not import or move existing company materials, and do not configure real user permissions.
- During development, connect filtering capabilities such as organizational affiliation, category, and product into the unified backend and MCP; the web/DingTalk clients continue to use the existing Agent pipeline.
- This plan does not change the overall rules of "free submission, authorized publication", version conflict checking, recoverable takedown, and independent backups.
- In the initial phase, only verify that categorization, search by department, authorized cross-department access, precise locating, and on-demand content reading work effectively, then optimize based on real usage.
## 10. Basis

- User authorization for this round: determine the organizational partitions according to the provided screenshot, and determine the internal department classifications on the user's behalf; adhere to a simple, efficient, and reasonable design.
- Source of organization names: the screenshot provided by the user, `codex-clipboard-cfc90245-09f8-4507-9dce-587512ba93c4.png`.
- Original project handover materials: kept offline by the project lead; this repository follows the current design and development plan.
