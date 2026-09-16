# 企业知识扩展库与文件库开发计划

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task in the current task. Steps use checkbox tracking. 默认按任务依赖顺序推进，先跑通最小可用流程，再补齐首版能力；每项完成后验证，不要求多 Agent 并行开发。

**Goal:** 在 Windows 服务器建设 Codex、Accio 等 Agent 的额外企业知识扩展库，让它们通过 MCP 按需了解公司的业务、产品、规范和经验，将这些知识用于各种回答、判断和工作任务，产出更贴合公司需求的结果；同时提供员工经现有入口上传、查找和获取文件的能力。

**Architecture:** Agent 回答问题或执行任务 → 按需调用 MCP → 理解相关公司背景、事实、规范和经验 → 将知识用于回答与任务执行。一套 Python 知识后台提供 MCP 和文件传输接口，独立后台进程负责解析、OCR 和索引；PostgreSQL 保存管理数据和索引，Windows 数据目录保存原文件。现有网页／钉钉复用同一套知识与文件能力。

**Tech Stack:** 已验证 Python 3.12、FastAPI、官方 MCP Python SDK 2.2、PostgreSQL＋pgvector、jieba；原生格式使用 python-docx／openpyxl／python-pptx／PyMuPDF，扫描件用 RapidOCR；本地向量采用 FastEmbed 的多语言 MiniLM 384 维轻量模型。依赖锁定于 uv.lock，模型身份含文件指纹。Windows 服务托管仍按后续 WinSW 方案验证。

**Spec:** [项目设计基线](../../项目设计基线.md)、[知识库分类方案](../../知识库分类方案.md)。本文件和这两份文档一起构成当前开发依据。

## 全局约束

- 简单、高效、合理设计，不过分设计。
- 核心交付为 Agent 可按需调用的企业知识扩展包：统一资料库＋MCP 能力＋简短使用约定，让公司知识真正影响原 Agent 的回答、判断和执行结果。
- 企业相关工作应按需获取公司上下文与依据，不依赖用户每次指示搜索；不重复加载全部背景或扫描全库，不把任何举例固化为专门业务系统。
- 原文件先保存，解析、向量化在后台执行；不配置专用入库 Agent。
- 公司资料本地保存；云端模型的数据边界通过现有 Agent 接入契约明确。
- 复用已有网页、钉钉及执行 Agent；不开发独立问答应用或完整员工网站。
- 保留原文件、可靠来源、权限、正式版本、冲突检查、撤下恢复和独立备份。
- 自由提交、授权发布；公司公共知识不是无条件公开资料。
- 默认按需返回相关内容，不设置严格固定的任务 token 配额。
- 首期按 50 个 Agent 同时读取验证，中后期扩展并验收到 200；未测试前不得宣称达标。
- 测试资料每轮均按新文件重新上传、解析和索引；显式故障重试及幂等场景除外，不能用历史成果代替新输入测试。
- Windows 为目标系统；Codex 和 Accio 为主要客户端。当前机器不自动视为公司目标服务器。
- 对本计划或设计的变更直接整合覆盖原内容；正式系统资料历史版本与备份照常保留。

---

## 1. 计划交付与当前状态

计划编号 0–8，共 9 步。三批本地实现及阶段 review 已完成；2026-09-16 用户授权补齐全部本地遗漏，再做整体 review。已补查询向量有界等待、发布分类审核、只读原件对账、MCP 并发测试和阈值判定、真实进程重启与 32 MiB 文件验证。真实企业资料、Accio／员工入口、正式服务器和正式容量验收仍需实际输入，本地验证不替代这些验收。

核心验收目标是“Agent 获得并应用企业知识，让回答和各种任务结果更贴合公司”。从背景理解、事实查询、规范应用、经验参考中选择少量实际问题和任务验证，不以单一例子定义产品。历史报价只是一种说明性案例。员工上传、查资料、问内容和取文件共用这套资料能力，继续保留在首版范围。

本计划的大框架与两类需求已经确定。下文的组件组合、表名、工具名、模块文件及测试参数是可执行的起始建议，在对应功能实施时确定即可；能够用成熟组件更简单地实现同一目标时，直接覆盖调整相关内容，不为遵守草案形式增加工作。身份权限、原文件保存、版本追溯、发布与恢复等基础要求仍需落实。

首个可用流程只选少量代表性资料和一个可用客户端，跑通“保存资料→处理→按权检索和读取→Agent 应用到问题或任务→获取原文件”。用合成或获准试点资料、小范围身份验证，随后补齐各格式、Codex／Accio、员工入口、运行保障与首期容量验收。初次跑通不等于首版全部交付。

已核实的文档依据：PostgreSQL 与 Docling 提供 Windows 支持；pgvector 官方提供 Windows 构建说明；Codex 本机 CLI 的 `mcp add --help` 显示支持 Streamable HTTP URL 和环境变量 Bearer token。支持说明不能代替实际服务器和客户端联调。[PostgreSQL Windows](https://www.postgresql.org/download/windows/)、[Docling 安装](https://docling-project.github.io/docling/getting_started/installation/)、[pgvector](https://github.com/pgvector/pgvector)、[Codex MCP](https://developers.openai.com/codex/mcp/)

Accio 按用户提供的名称列为必测客户端；Task 0 了解其实际版本与接入条件，具体 MCP、认证和附件传输能力在可用环境中验证，最迟于 Task 5／6 完成。不得仅依据支持 MCP 的宣传就宣称整个接入已兼容。

## 2. 技术方案与复用边界

### 2.1 推荐组合

| 部分 | 首版建议 | 开发边界 |
|---|---|---|
| 后台与 MCP | Python＋FastAPI＋官方 MCP SDK | 写企业业务与工具封装，复用协议实现 |
| 数据与检索 | PostgreSQL＋pgvector＋内置全文检索 | 写数据关联、授权过滤和检索组合，复用数据库能力 |
| 中文关键词 | jieba 分词＋少量业务词典＋编号精确字段 | 确保中文及产品编码有效，不另上一个搜索集群 |
| 文档处理 | 原生 Office 库＋PyMuPDF＋RapidOCR | 写格式适配与真实来源映射；未解析区域明确降级，不开发 OCR 引擎 |
| 向量化 | 本地 multilingual MiniLM，384 维，FastEmbed／ONNX | 批处理和模型文件指纹；不训练模型；复杂业务样本再决定是否升级模型 |
| 原文件 | NTFS 数据目录，文件标识生成存储路径 | 写流式上传、权限校验和版本引用 |
| 后台作业 | PostgreSQL 作业表＋一个工作进程 | 支持领取、重试、崩溃恢复；不先引入消息中间件 |
| Windows 运行 | PostgreSQL 原生服务；WinSW 托管 API 和 worker | 写安装、健康检查、备份及恢复脚本 |

pgvector 支持与 PostgreSQL 全文检索组合；中文分词使用 jieba 的搜索模式，型号原串另存，避免分词破坏编号。[pgvector 混合检索](https://github.com/pgvector/pgvector#hybrid-search)、[jieba](https://github.com/fxsjy/jieba)、[PostgreSQL 全文检索](https://www.postgresql.org/docs/current/textsearch-controls.html)

第二批选择轻量多语言 MiniLM 作为本地向量起点，已在 Windows 用合成资料验证；BGE-M3 保留为确有质量需求时的候选，当前未引入。API 查询向量化单并发、有超时降级，worker 独立运行；正式模型分发和容量测试在对应部署／验收阶段进行。

### 2.2 为什么采用组件组合

这样可把权限、发布、版本和文件记录保存在同一数据库，缩短一致性处理链路。但这仍然需要开发企业业务层，不能把这些工作称为全部开箱即用。

完整 RAG 产品也可复用。以 RAGFlow 为对照候选，其公开功能包含解析、检索、问答与 Agent，部署涉及更多配套组件。是否能以配置直接满足本项目的版本、授权和文件交付，需实际验证，不能直接假设满足或不满足。[RAGFlow 官方仓库](https://github.com/infiniflow/ragflow)

Task 0 先检查公司是否已有可复用平台；若其能通过同一组基础验收且减少总体开发和运维工作，就采用它并覆盖调整后续计划。若没有，按本组合推进。只选一条路线实施，不同时维护两套知识库。

### 2.3 Windows 具体安排

- 优先 Windows 原生安装；记录实际 Windows 版本后，从官方支持范围选择 PostgreSQL 主版本，再锁定对应 pgvector 和 Python 依赖。
- pgvector 若需编译，在构建机使用官方说明构建匹配 PostgreSQL 主版本及架构的扩展，保存构建记录和文件校验值；不从不明来源下载 DLL。
- 本机已验证原生格式库、RapidOCR 和本地向量模型；目标服务器仍需部署验证。旧 .doc/.xls/.ppt 当前仅保存原件并提示提交新版 Office 格式，不先引入未验证的转换服务；有实际存量需求时再接入受限转换进程。
- 优先复用公司现有 HTTPS 入口；若没有，先使用 API 服务自身的 TLS 能力及公司认可的证书，部署验证必须检查 Codex 和 Accio 的信任链。
- API 与 worker 注册为后台服务，使用专用服务账号；PostgreSQL 与文件数据目录只向需要的进程授权。复用服务包装组件，不自行实现 Windows 服务框架。[WinSW](https://github.com/winsw/winsw)
- 不默认引入 Docker Desktop、WSL、Linux 虚拟机或新的反向代理。原生组件确有阻塞时，在 Task 0 比较受支持的替代方式，再更新计划。

## 3. 数据和接口约定

### 3.1 最小数据结构

| 表／记录 | 核心字段和作用 |
|---|---|
| `organizations`, `categories` | 稳定标识、名称、启用状态；初始化 8 个组织分区与 6 个用途分类 |
| `principals`, `memberships`, `access_tokens` | 用户／服务身份、组织关系、令牌摘要与撤销状态；角色由可信服务端管理 |
| `documents`, `document_grants` | 文件身份、归属、分类、产品／型号／项目、可选业务日期、读权限、`active_version_id`、`revision` |
| `versions` | 原文件位置、哈希、上传者、源版本标签、`base_revision`、发布与处理状态 |
| `file_links` | 版本之间的预览／模型包／参数表等关联；共享不复制原始文件 |
| `uploads`, `jobs` | 上传完成及提交状态；作业尝试数、领取租约、处理代次、下次重试时间 |
| `chunks` | `version_id`、处理代次、片段原文、真实来源位置、分词文本、向量及模型标识 |
| `audit_events` | 谁对哪个记录进行了提交、发布、撤下、恢复和授权修改 |

企业知识扩展包含公司与部门背景、业务及产品事实、工作规范、模板、项目资料和历史经验。沿用同一数据结构，背景和简短经验可使用 Markdown 原文件。业务日期来自原文或经确认的记录，上传时间不替代业务日期；具体场景使用原文及通用关联字段，不因例子增加专用业务数据库。

### 3.2 状态与一致性

处理状态：`queued / processing / ready / partial / failed / stored_only`。`partial` 同时返回明确的能力与失败说明，例如文字可读但向量尚不可用；`stored_only` 表示附件登记，不等于解析成功。

发布状态：`draft / published / superseded / withdrawn`。负责人可以显式接受“仅文件管理”或部分解析版本，但发布界面必须说明其内容检索限制；不静默把失败当作完整可检索。

`documents.active_version_id` 是普通查询使用的正式版本指针；默认搜索和读取必须同时检查该指针、发布状态和权限。历史版本仅通过明确历史请求且具备相应授权时读取。

上面的“历史版本”只指同一文件被替换的修订版。不同项目、时间和业务活动的历史记录可各自保持正式可检索，无需打开系统修订历史，也不能被“只查最新日期”规则全部排除。返回原文的适用范围、时间、前提和限制，缺项如实说明；根据已有用途分类与原文区分当前规范、事实和历史参考，不把历史案例自动作为执行要求。

- 新文件从 `revision=0` 起；提交记录保存当时的 `base_revision`。
- 发布、撤下、恢复及影响可见性的管理修改，在同一数据库事务中检查并递增 `revision`。
- 提交与发布都检查版本；发布时同时核对提交保存的 `base_revision` 与当前 `revision`，不能仅让客户端改填 `expected_revision` 就绕过旧稿冲突。发生冲突保留修改稿，返回 `VERSION_CONFLICT` 及当前版本信息给有权限的操作者，重新核对修改基础后才能再次提交。
- 新解析结果先写入独立处理代次，完成后切换该版本可用的处理代次；查询不读半批结果。
- worker 重试必须验证租约与处理代次，旧进程不能覆盖新结果、重新发布或恢复撤下内容。
- 上传先落临时文件，完整校验后在同一磁盘原子改名，再登记可提交状态；数据库登记失败留下的孤立文件由对账作业处理。不能先声称上传成功再异步保存原文件。
- 首版无回答缓存；撤下和权限变更可直接由查询与下载授权检查生效，不额外建设缓存失效系统。

### 3.3 普通 MCP 能力

以下是建议的能力划分与初始接口名称，在 MCP 实施阶段结合成熟 SDK 和实际客户端确定；可合并重复工具，但保持能力清楚、输出精简。认证身份来自请求上下文，不提供可自由填写的 `employee_name`、`role` 或 `allowed_departments` 参数。

| 工具 | 输入 | 返回 |
|---|---|---|
| `kb_context` | 可选 `organization_id`、任务关键词 | 任务需要背景时才调用；返回相关正式背景与来源，不是任务启动必经步骤 |
| `kb_search` | `query`、`mode=files/content`、可选组织／型号／分类／文件／业务日期范围筛选、`limit`、`offset` | 相关背景、事实、规范或经验资料的文件卡片或片段、来源与继续读取标识 |
| `kb_read` | `version_id`、可选 `chunk_id`、`offset`、`limit`、`max_chars` | 原文段落／章节／表格、位置、完整性说明 |
| `kb_prepare_upload` | 文件名、大小、可选内容哈希 | `upload_id`、受控上传地址、有效期和传输方式 |
| `kb_submit` | `upload_id`、归属、分类、可选 `document_id`、`base_revision`、产品等字段 | `document_id`、`version_id`、处理和发布状态 |
| `kb_status` | `version_id` | 处理、发布、失败说明及允许的下一步操作 |
| `kb_get_file` | `version_id` | 文件名、大小、哈希和受控获取引用；不返回文件二进制 |
| `kb_withdraw_submission` | 自己的 `version_id` | 撤回未发布提交的结果，不授予撤下正式资料的权限 |

`kb_search` 首版默认返回 5 项，配置上限与分页；5 是可调整的起始值，不是任务预算。后续读取按完整语义单元返回，不切掉单位和脚注。响应中明确 `has_more`、`next_offset`、`warnings`。

管理能力另行授权：`kb_review_submissions`、`kb_publish`、`kb_withdraw`、`kb_restore`。它们分别列出职责范围内待处理提交、发布、撤下和恢复；管理写入都携带 `expected_revision`。首版优先让负责人通过现有 Agent 使用同一后台管理能力，不同时开发重复的业务管理 CLI；CLI 仅保留身份初始化、令牌撤销等必要运维操作。无管理权限的客户端不展示管理工具，后台仍独立检查授权。

错误码统一使用 `NOT_FOUND`（不存在或不可见）、`VERSION_CONFLICT`、`UPLOAD_INCOMPLETE`、`PROCESSING_INCOMPLETE`、`INVALID_ARGUMENT`、`RETRY_LATER`；工具同时返回可理解的中文说明。后台详细原因进入有权限的日志。

### 3.4 文件传输与身份

```text
准备上传 → 现有入口或 Agent 所在运行环境向上传地址传文件 → 校验完成 → 使用 upload_id 提交
获取文件引用 → 已认证的交付程序下载 → 通过原网页／钉钉入口交付
```

文件传输采用 HTTP 流式接口：`PUT /v1/uploads/{upload_id}/content` 与 `GET /v1/versions/{version_id}/content`。上传记录绑定身份、大小及完成状态，提交时再次验证归属。

下载地址默认要求身份认证；普通浏览器若不能直接附带认证，由原入口的可信交付程序取回后交付，不默认发永久公网裸链接。新下载请求重新检查权限和撤下状态；已经交付给员工或第三方平台的文件副本无法靠知识库撤下追回。

Codex／Accio 若无法直接完成二进制传输，提供同一个小型客户端适配脚本，运行在实际持有文件的环境中。脚本只传输文件及输出引用，不建立本地知识副本或另一套后台。MCP 工具不接受服务端任意本地路径，也不默认抓取任意 URL。

独立员工客户端优先使用独立、可撤销的访问令牌或已有授权体系。入口 Agent 代表多名员工时，由可信入口／执行调度层绑定实际用户的凭证或委托身份，不能在模型参数中选择身份。首版不另建 OAuth 身份平台；需要 OAuth 时复用现有身份服务并按 SDK 接入。真实委托链未通过前，不上线多人混用身份的员工入口。[MCP 认证规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

## 4. 计划文件结构

以下为规划路径；步骤 0–5 已实现。迁移 SQL 和分类 JSON 实际放在 src/agico_kb 内随 Python 包分发，其他尚未实现路径随对应任务创建。

```text
pyproject.toml / uv.lock                 依赖及锁定版本
src/agico_kb/main.py                     API、MCP 挂载与健康检查
src/agico_kb/config.py                   配置和依赖检查
src/agico_kb/contracts.py                工具及业务请求／返回类型
src/agico_kb/db.py                       数据库连接与事务
src/agico_kb/auth.py                     可信身份解析与统一授权
src/agico_kb/catalog.py                  分类、元数据与授权目录
src/agico_kb/files.py                    上传、提交、受控下载及文件对账
src/agico_kb/lifecycle.py                发布、冲突、撤下、恢复与审计
src/agico_kb/ingestion.py                解析、来源位置和片段构建
src/agico_kb/embeddings.py               本地模型、模型版本及批处理
src/agico_kb/worker.py                   作业领取、租约及重试
src/agico_kb/search.py                   精确筛选、中文检索、向量融合、读取
src/agico_kb/mcp_server.py               普通及管理工具封装
src/agico_kb/admin.py                    负责人和运维的受控 CLI
migrations/001_initial.sql              数据结构、约束及索引
config/taxonomy.json                    8 个组织与 6 个分类
config/search-dictionary.txt            样本验证需要的术语，不填虚构业务词
client/file_transfer.py                 现有运行环境的上传／下载适配
client/agent-usage.md                   Codex／Accio 共用的简短知识使用约定
scripts/probe.ps1                       Windows 依赖和权限检查
scripts/install-services.ps1            注册本项目 Windows 服务
scripts/backup.ps1 / restore.ps1        数据与文件备份及独立恢复
deploy/windows/                        API／worker 的 WinSW 配置模板
tests/conftest.py                       隔离数据库、临时存储及测试身份
tests/contract/                        业务接口、授权和状态转换测试
tests/integration/                     文件处理、检索和 MCP 集成测试
tests/fixtures/                        合成测试源文件与原文位置答案
tests/load/                            50／200 并发请求负载定义
docs/validation/                       组件、业务、客户端、负载与恢复报告
```

保持按功能组织的少量模块；不为每个业务字段建立单独包，不提前拆成多个部署项目。文件表用于说明职责，不要求照表预建所有空模块或为了文件数量而拆分代码。

## 5. 执行任务

### Task 0：Windows 与客户端兼容验证，锁定一条路线

**交付：** 可复现的依赖验证记录，以及服务器、Codex、Accio 和文件交接的接入契约。

**文件：** 新建 `scripts/probe.ps1`、`docs/validation/component-check.md`、`docs/validation/integration-contract.md`、`tests/fixtures/manifest.json`；必要时覆盖修改本计划的技术表。

**输入：** 已有 Windows 环境、可用客户端及合成或获准样本。**输出：** 首条可用技术路线、已验证的组件版本、身份和文件交接约定，以及剩余实际接入项；全格式与双客户端详细验证在对应任务中完成。

- [x] 记录开发 Windows 版本、架构、CPU、内存，并提供 GPU／磁盘配置检查脚本；已明确当前机器不是经确认的目标服务器。目标系统与服务账号条件留到部署阶段核实，脚本不输出密钥。
- [x] 用官方安装／构建方式验证数据库、向量扩展、Python 和一条文档处理路线；先用简单原生文字文件验证解析与向量化。记录当前通过的版本，后续加入 OCR 和其他格式时再锁定对应依赖，不使用浮动 `latest` 交付。
- [x] 用少量新建合成样本验证中文内容、型号、单位与来源定位。跨页复杂表格、各 Office 格式和扫描件安排在 Task 3，资料适用性安排在 Task 4／6／8；真实样本到位后重新完整导入，不把所有样本问题都变为 Task 0 的前置阻塞。
- [x] 核实是否已有满足要求的平台；依据同一组验证结果选择复用平台或本计划组件组合。未证明能减少总工作量，不额外引入完整 RAG 产品。
- [x] 从现有可用的 Codex 或 Accio 先验证列工具、测试调用、认证与网络可达，并摸清文件传输路径；另一客户端记录已有信息及需要实测的部分，在 Task 5／6 完成。Codex 的 `mcp add` 为有依据的接入方式；Accio 使用其实际版本提供的配置入口。
- [x] 写清可信入口绑定员工凭证、原会话返回的接入契约；真实网页／钉钉委托配置尚未提供，记录到 Task 6 验证，当前独立身份试验不代替多人委托联调。

实施时使用的检查命令：

```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_ComputerSystem | Select-Object NumberOfLogicalProcessors, TotalPhysicalMemory
Get-Volume | Select-Object DriveLetter, FileSystem, SizeRemaining, Size
codex mcp add --help
```

**通过条件：** 基础组件、一种代表性原生文字格式和一个可用客户端有可复现的运行证据，可据此推进最小流程。其他格式、另一客户端和真实员工委托保留为后续对应任务，不宣称已通过。缺真实环境时允许使用合成资料验证后台，但部署兼容和客户端联调必须留有清楚的未完成记录。

### Task 1：建立后台、身份、分类及原文件保存能力

**文件：** 新建 `pyproject.toml`、`uv.lock`、`main.py`、`config.py`、`contracts.py`、`db.py`、`auth.py`、`catalog.py`、`files.py`、`migrations/001_initial.sql`、`config/taxonomy.json`、`tests/conftest.py`、`tests/contract/test_files.py`（源代码路径均位于第 4 节对应目录）。

**接口：** 输入可信 `Principal` 与文件流；输出上传标识和不可变原文件引用。`Principal` 至少包含服务端确认的 `subject_id`、组织关系和操作权限，不从工具 JSON 生成角色。

- [x] 初始化 Git（仅在尚无仓库且开始实施时）、Python 包、数据库迁移、健康检查和配置加载；测试夹具为每轮建立隔离数据库／schema、临时目录和合成身份，禁止清理生产数据。
- [x] 初始化 8 个组织分区和 6 个用途分类；稳定标识与显示名称分开，可调整配置。
- [x] 先写流式上传、大小／哈希校验、中文文件名、失败断传、越权读取和重复提交测试，运行确认目标行为尚未实现。
- [x] 实现受控上传与提交：完成文件写入后才返回可提交状态；原文件按随机标识存储，名称仅作元数据；重复幂等键只返回原结果，相同键不同请求返回冲突。
- [x] 实现归属和文件读权限；跨部门共享只增加授权，不复制内容。上传可先暂存；提交未指定用途时归为“其他资料”，负责人审核用途后发布。
- [x] 实现状态与文件获取，确保解析服务未运行时仍能保存文件，并由有权限者获取自己的提交。
- [x] 运行测试；通过后提交本任务涉及的代码和验证记录。

```powershell
uv run pytest tests/contract/test_files.py -q
```

**通过条件：** 上传断传不生成完整提交；未授权身份无法读文件记录或下载；两次同幂等请求不生成双版本；原文件下载哈希等于上传哈希；分类与截图名称一致。

### Task 2：发布、版本冲突、撤下及恢复

**文件：** 新建 `src/agico_kb/lifecycle.py`、`src/agico_kb/admin.py`、`tests/contract/test_lifecycle.py`；修改 `files.py`、`auth.py`、`contracts.py` 和迁移。

**接口：** `publish(version_id, expected_revision)`、`withdraw(document_id, expected_revision)`、`restore(version_id, expected_revision)` 从可信上下文取操作人；成功返回最新 `revision`，冲突返回 `VERSION_CONFLICT`。另提供自己未发布提交的撤回，以及职责范围内的待发布列表。

- [x] 先写员工不能发布、负责人限本职责发布、旧稿不能覆盖新稿、撤下后普通访问停止、历史授权读取和恢复测试。
- [x] 在同一事务中检查 `expected_revision`、版本准备情况和权限，切换正式指针、更新状态并写审计。
- [x] 对新文件、替换稿、部分解析和附件型文件分别返回明确发布结果。仅文件发布不会冒充正文已解析。
- [x] 将待处理列表、发布、撤下和恢复实现为同一后台业务能力，供 Task 5 的管理工具调用。CLI 仅提供必要身份初始化和令牌撤销；凭证由受保护的配置或环境提供，不作为命令参数写入日志。
- [x] 运行并发发布场景：A、B 基于相同 revision 提交；A 发布后，B 发布返回冲突且提交仍存在。
- [x] 验证通过后提交代码和状态转换说明。

```powershell
uv run pytest tests/contract/test_lifecycle.py -q
```

关键断言按以下完整场景实现，不仅检查内部字段：

```text
员工提交 v1 → 负责人发布成功 → 两人基于同 revision 提交 v2a/v2b
发布 v2a 成功 → 发布 v2b 返回 VERSION_CONFLICT → 默认读取仍为 v2a
撤下成功 → 普通搜索／读取／新下载请求均不可见
具备权限的负责人选择历史版本恢复 → 默认正式版本切换且留下审计
```

### Task 3：后台解析、来源定位和可恢复索引任务

**文件：** 新建 `ingestion.py`、`embeddings.py`、`worker.py`、`tests/integration/test_ingestion.py`、`tests/integration/test_worker_recovery.py`；修改 `files.py`、`contracts.py` 和迁移。

**接口：** 作业输入不可变 `version_id` 与原文件位置；产出 `chunks`、来源位置、能力说明及处理状态。片段定位格式为 `{kind, page?, section?, sheet?, range?, slide?, table?}`，只填可验证字段。

- [x] 为每种代表性格式准备带已知答案的新测试文件；断言原文、型号、数字、单位、表头及来源位置，不只断言“解析成功”。
- [x] 使用成熟原生格式库组织片段，绑定章节标题和表格上下文；DOCX 使用章节／段落，XLSX 使用工作表／区域，PPTX 使用幻灯片位置，PDF 使用可验证的原页码。组件未提供足够位置时在适配层补齐；仍无法确定时明确降级，禁止编造。
- [x] 对 Excel 公式保留公式及已有缓存结果的区别，不宣称已重新计算；复杂图形未解析时报告能力限制。扫描件按需 OCR，不对原生文字页一律全量 OCR。
- [x] 完成中文分词、向量批处理及模型标识记录；模型标识、revision、向量维度与分块版本作为一组，变更时重建受影响索引，查询不混用不同模型空间。
- [x] worker 领取使用数据库事务与行锁，随后在事务外处理文件；租约允许服务重启后重新领取，完成写入校验处理代次。首版一个进程，解析子进程设置超时，失败有限重试后保留错误与手动重试入口。
- [x] 向量化失败但文字有效时保留关键词读取能力，状态标为 `partial`；整份文件失败仍保存原文件。CAD／3D 包登记为 `stored_only`，保留依赖包和辅助资料关联。
- [x] 新输入重跑所有解析场景，模拟处理过程中退出、超时和索引写入失败；验证无重复可见片段、无半批结果、无丢文件。

```powershell
uv run pytest tests/integration/test_ingestion.py tests/integration/test_worker_recovery.py -q
```

**通过条件：** 常规样本内容及来源符合答案清单；故障可恢复；未解析内容被如实标注；后台故障不阻止已发布资料读取。

### Task 4：精确文件查找、混合检索和按需读取

**文件：** 新建 `search.py`、`config/search-dictionary.txt`、`tests/integration/test_search.py`、`tests/integration/test_source_read.py`；修改 `catalog.py`、`auth.py`、迁移和契约。

**接口：** `SearchRequest` 使用第 3.3 节字段；结果统一包含 `document_id`、`version_id`、`title`、`organization_id`、`source_version`、`locator`、`excerpt`、`capabilities`。读原文使用同一 `version_id/chunk_id`。

- [x] 先写精确型号、相似型号、中文同义表达、文件内检索、权限隔离、被替换修订版和撤下测试；单独验证历史业务记录仍被检索，上传日期不被误认为业务日期，现行规范与历史案例可以区分。
- [x] 文件模式优先编号／型号精确匹配和元数据过滤，再查文件名及说明；内容模式融合关键词与向量结果。默认中文分词在入库和查询使用同一规则，业务编号另外精确比较。查询向量化超时或不可用时可降级为关键词检索，并在响应中说明检索范围受限，不拖垮文件查找。
- [x] 采用简单的排名融合与片段去重；暂不加入独立重排模型。排序前候选必须受权限制约，最终序列化及读取再次校验可见性。
- [x] 首先用精确向量查询作为正确性基准；数据量需要时增加 HNSW 索引。特别测试部门／文件权限过滤后候选不足的问题，必要时采用迭代扫描或受限集合精确检索。[pgvector 过滤说明](https://github.com/pgvector/pgvector#filtering)
- [x] 实现分页及相关段落／完整表格读取，返回 `has_more` 与继续定位信息；读取量有保护性上限，但不切掉关键单位和脚注后默认为完整。
- [x] 背景工具复用已发布的背景类文件，仅在任务需要时调用；空背景返回缺少资料，不生成公司事实。明确范围不命中时返回无结果或少量待选择项。请求类似产品时允许返回相似候选并保留规格差异；请求指定型号时不以相似型号冒充精确匹配。
- [x] 完成合成代表样本检索基准，真实企业样本质量留到 Task 6，确认典型问题能找到正式依据；通过后提交代码与检索报告。

```powershell
uv run pytest tests/integration/test_search.py tests/integration/test_source_read.py -q
```

SQL 层最小检索契约（参数由程序绑定，禁止拼接用户文本）：

```sql
-- document/version/permission filters must be applied to both candidate queries.
-- segmented_text and segmented_query use the same application tokenizer.
to_tsvector('simple', segmented_text)
plainto_tsquery('simple', segmented_query)
-- Exact model/document identifiers are compared as normalized stored fields.
```

**通过条件：** 型号不串、版本不串、权限不串；搜索返回引用可以回读对应原文；正文不可用时能找到文件并说明限制。正文返回量不随全库目录长度直接增长。

### Task 5：MCP 与文件传输适配

**文件：** 新建 `mcp_server.py`、`client/file_transfer.py`、`tests/integration/test_mcp.py`、`tests/integration/test_file_transfer.py`；修改 `main.py`、`contracts.py` 和身份适配。

**接口：** 按第 3.3 节工具注册；MCP 与 HTTP 文件接口调用相同业务服务和授权检查，不各自维护状态。

- [x] 使用 Task 0 锁定的官方 SDK 主版本挂载 Streamable HTTP；校验生命周期、认证、并发请求和客户端关闭后资源释放。避免混用 SDK 1.x 与 2.x 示例。
- [x] 先写真实 HTTP MCP 会话测试：初始化、列工具、搜索、读取、提交、查询状态、重复调用重试；管理工具只向对应权限展示且逐次授权。
- [x] 实现第 3.3 节全部普通工具及单独授权的管理工具，工具说明短而明确，错误返回下一步可执行信息。
- [x] 用真实二进制文件测试准备上传→上传→提交→取回。客户端传输脚本校验服务器来源、凭证作用域和下载哈希，不向未知地址发送后台凭证。
- [x] 验证传入其他身份的 `upload_id`、已撤下版本、新的下载请求、伪造角色字段都不能绕过权限；测试重复工具调用不会重复发布或提交。
- [x] 检查结果中不返回 Base64 文件、长期凭证、服务器本地路径或全库清单；通过后提交适配代码。

```powershell
uv run pytest tests/integration/test_mcp.py tests/integration/test_file_transfer.py -q
```

官方 SDK 当前文档为 2.x，提供 Streamable HTTP；实施使用已锁定 SDK 的 API，不从旧示例直接复制。[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

### Task 6：验证 Agent 在实际任务中使用企业知识，再联调员工入口

**文件：** 新建 `client/agent-usage.md`、`docs/validation/codex.md`、`docs/validation/accio.md`、`docs/validation/employee-entry.md`；更新 `integration-contract.md`，必要时调整 `file_transfer.py`。

**输入：** Task 5 的服务与真实客户端。**输出：** 两客户端各自可复现的配置和实测结果，以及网页／钉钉链路的完成证据。

- [ ] 从公司现有资料整理少量试点知识：公司／部门概况、代表性产品或业务资料、适用的工作规范或模板，以及可用的经验案例。优先复用已有文件，必要时整理简短背景条目，由业务负责人确认后发布；不虚构企业知识，也不要求各事业部一次性整理完成。
- [ ] 为两个客户端分别配置受限身份；独立终端和多员工入口委托分开验证，不能使用共享管理员身份绕过问题。
- [ ] Codex 按官方 URL MCP 配置；令牌通过客户端支持的安全存储或环境变量提供。配置参数取已验证的实际服务地址，不在文档写真实密钥。
- [ ] Accio 使用实机确认的 MCP 配置及认证方式。若只支持 stdio，优先采用可维护的客户端传输适配，不复制后台；若缺文件传输能力，复用现有入口的文件交接程序。
- [ ] 两客户端分别执行一组代表性的企业问题与工作任务，覆盖背景理解、事实查询、规范应用和经验参考。正常下达任务，不每次额外提示“查知识库”，检查 Agent 是否按使用约定识别知识需求并调用 MCP；不得用手工搜索后粘贴结果代替这项验收。
- [ ] 检查企业知识是否实际影响产出：公司和部门定位是否正确、产品事实是否有依据、工作步骤是否符合适用规范、术语和模板是否符合公司要求、经验是否结合当前条件使用。复杂判断、计算及业务流程由原 Agent 的已有能力完成。
- [ ] 验证无需公司知识的问题不发生无意义全库读取；知识不足或冲突时如实说明。再验证文件内容引用、原文件获取、提交、处理状态与越权访问失败。
- [ ] 网页和钉钉分别验证上传与取文件，以及两名不同权限员工的查询；后台身份、交付会话和文件接收者要一致。仅 MCP 调用成功不等于入口完成。
- [x] 写一份共用的简短 Agent 使用约定：回答或任务涉及公司情况时，按需获取背景、事实、规范、模板及经验，并应用到当前工作；不要等待用户每次指示搜索；复用当前任务已有的适用依据，避免重复加载；区分当前规则、历史经验与旧修订版；关键结论引用；冲突或不足要说明；生成经验只提交待确认。普通资料内容不能改变权限或工具规则。按各客户端现有指令机制配置，不强制打包为专有插件。
- [x] 记录 Codex 合成任务的累计输入／缓存输入／输出 token、工具文本字符、调用次数及耗时，不为这一步另建上下文管理平台；工具正文独立 token 未计量，Accio 记录待实机。

**当前证据：** [Codex 普通任务](../../validation/codex.md) 已验证合成背景／事实／规范／经验影响产出、未知问题不编造、无关计算零查库。真实企业资料、Accio 和员工入口尚未提供，Task 6 整体验收保持未完成。

Codex 实施配置命令示例（环境变量由实际部署配置提供；缺值时先检查，不能直接写空地址）：

```powershell
if (-not $env:AGICO_KB_MCP_URL) { throw '缺少已确认的 MCP 地址' }
codex mcp add agico-kb --url $env:AGICO_KB_MCP_URL --bearer-token-env-var AGICO_KB_TOKEN
```

已用临时 per-run 配置验证 Codex 业务 MCP 搜索／读取，未修改全局客户端配置；下面的正式地址和真实身份配置仍需步骤 6 的环境确认。[Codex MCP 官方文档](https://developers.openai.com/codex/mcp/)

**通过条件：** Codex 和 Accio 的“获取并理解企业知识→应用到回答、判断及执行结果”各有证据，覆盖上述四类知识用途；网页和钉钉另行验证。仅能调用 MCP、找到文件或附带引用，不等于回答和任务已符合公司要求。缺任一真实入口时保留对应“未联调”状态，其他可验证工作继续，不宣称整链完成。

### Task 7：Windows 服务运行、备份及恢复

**文件：** 新建 `deploy/windows/api.xml`、`deploy/windows/worker.xml`、`scripts/install-services.ps1`、`scripts/backup.ps1`、`scripts/restore.ps1`、`docs/validation/operations.md`、`tests/integration/test_restore.py`。

**接口：** 配置提供安装根目录、独立数据根目录、数据库连接和受保护的凭证来源；备份输出清单、数据库快照、文件及校验值。

- [ ] 以专用服务账号注册 API 与 worker，验证开机启动、故障重启、日志轮转、健康状态及受限防火墙范围；后台启动不弹出交互窗口。
- [x] API 在线查询与解析分进程，CPU 密集解析不阻塞 HTTP 事件循环；本地查询向量化使用有界 FIFO 等待，等待与推理共用超时预算，超时或超限才降级（负载比例见 Task 8）。数据库连接池每进程最多 8 个，不按 200 Agent 直接创建 200 条数据库连接。
- [ ] 建立独立备份目标，覆盖数据库、原文件、分类配置与所需恢复信息。备份凭证和身份配置采用受控保存，不能只备向量索引或把同磁盘副本当作独立备份。
- [x] 首版备份使用明确的短暂写入暂停：暂停新提交／发布／后台完成写入、等待进行中的写事务结束、备份数据库和对应文件清单、恢复写入；读取维持服务。记录暂停时长，按实际需求再优化在线备份。
- [x] 恢复先在新的隔离数据库与新目录执行；已验证文件哈希、正式版本、授权、检索与待处理作业。拒绝已有数据库或目录，不覆盖现用环境。
- [x] 在隔离开发环境实际终止／重启 API、worker，并停启数据库；验证原件、权限、版本和作业回收。提供部署／升级／回退手册，固定依赖和模型；不等同于正式 Windows 无人登录启动验收，具体故障注入范围见进程重启记录。
- [ ] 运行恢复测试与人工 Windows 重启演练，保存证据后提交脚本。

```powershell
uv run pytest tests/integration/test_restore.py -q
```

**通过条件：** 无人登录时服务正常运行；备份可恢复到隔离环境；原文件与正式查询结果一致；运维记录清楚单机恢复能力及实测恢复时间，不宣称高可用。

**当前证据：** [运维记录](../../validation/operations.md) 包含备份恢复、离线模型配置、Windows 服务生成脚本和实际开发数据库停启验证；首次查询旧连接问题已修复。正式服务安装、Windows 无人登录重启、TLS、防火墙和独立备份位置待目标机验收，Task 7 整体通过条件尚未全部满足。

### Task 8：业务试用与 50 并发验收，记录 200 扩展路径

**文件：** 新建 `tests/load/locustfile.py`、`tests/load/acceptance-profile.json`、`docs/validation/business-quality.md`、`docs/validation/load-50.md`、`docs/validation/scale-200.md`。

**输入：** 一个事业部的获准真实资料和试用人员、已联调客户端、记录完整的服务器条件。**输出：** 业务验收与可复现容量报告。

- [ ] 先选一个事业部完整导入代表性资料；每次重跑均采用新上传／新处理，真实样本报告与合成测试报告分开。
- [ ] 固定一组来自公司实际工作的代表性问题和任务，覆盖背景理解、事实查询、规范应用和经验参考；例如业务介绍、产品判断、按公司要求制作交付物、参考历史案例处理新问题，例子按实际资料替换。另覆盖文件查找与内容读取、同名不同部门、类似产品、历史记录与旧修订版、无答案、权限拒绝及 CAD／3D 文件获取。
- [ ] 由业务负责人标注相关企业背景、事实依据、应采用的规范与模板、案例的适用条件，以及可接受的回答／交付物；同时评价检索覆盖和公司要求的落实，不只看是否找到资料或由模型自评。
- [ ] 在压测前填写并冻结 `acceptance-profile.json`：服务器、资料数／总量／片段数、客户端版本、查询混合比例、响应时间和成功率目标、测试持续时间、最大返回量及解析并发。阈值来源于试用要求，不在本计划虚构硬件承诺。
- [ ] 默认准备 50 个闭环虚拟 Agent，每个同一时刻一个请求，搜索／读取各半、无思考间隔，预热后持续 30 分钟；这些是起始测试配置，可在测试前按真实调用行为调整。报告实际吞吐，不将客户端连接数当作负载。
- [ ] 使用不同查询及不易缓存的数据，分别测正常读取、首次查询、批量导入／更新并行、撤下和故障恢复；大文件下载单独测试，模型生成耗时单独记录。
- [ ] 记录 p50／p95／p99、成功率、向量化排队、数据库与 worker 积压、内存／CPU／磁盘，以及成功任务累计 token。性能测试前后都检查检索质量与权限，不能通过减少正确结果数伪造加速。
- [ ] 50 负载达到冻结标准且业务质量通过后，完成首期验收；200 作为中后期目标复用同一测试框架。首期可做 200 探测，但未达到同样质量和稳定性标准不宣称通过。
- [ ] 依据测量瓶颈决定扩容：先调整索引／连接池／查询和解析资源；再考虑独立模型服务、额外 worker 或检索节点。没有瓶颈证据不预先增加部署组件。

正式后端负载命令（先填写并冻结配置、准备每个虚拟用户的独立凭证；设置 `AGICO_KB_LOAD_PROFILE`、`AGICO_KB_LOAD_CREDENTIALS`、`AGICO_KB_LOAD_PROFILE_SHA256`，具体步骤见容量报告）。默认总时长为 2 分钟预热加 30 分钟统计，CLI 参数必须与冻结配置一致：

```powershell
uv run --group load locust -f tests/load/locustfile.py --headless --users 50 --spawn-rate 5 --run-time 32m --host $env:AGICO_KB_TEST_URL --csv .local/load-50
```

该命令测试 REST 后端，不等于 50 个真实 MCP 会话或完整 Agent 任务；正式接入容量需同时补充实际客户端调用链证据。

**通过条件：** 每项有结果和环境记录；未达标项继续定位并复测；首期不能只凭功能测试通过就交付为 50 并发合格。全量 200 验收单独安排，不拖入首版无限扩展。

**当前证据：** [本机 50 身份 REST／MCP 短测](../../validation/load-50.md) 已完成；修复向量忙时立即降级后，两轮完整周期均正确、无降级。MCP 已验证实际 50 个 SDK 客户端，尚不包含真实 Agent 推理／生成。[业务质量矩阵](../../validation/business-quality.md) 和 [200 扩展路径](../../validation/scale-200.md) 已交付；公司实际资料、目标服务器、完整员工／Agent 工作流和正式 30 分钟验收仍未完成。以上正式验收复选框保留未勾选。

## 6. 里程碑和执行依赖

| 里程碑 | 任务 | 可以检查的结果 |
|---|---|---|
| M0：基础路线可运行 | 0 | Windows 基础组件、一种代表性格式和一个可用客户端有实测依据 |
| M1：文件管理可用 | 1–2 | 先保存文件；归类、权限、发布、版本、撤下恢复完整 |
| M2：内容检索可用 | 3–4 | 自动处理，关键词＋向量检索，读取真实来源 |
| M3：企业知识扩展可用 | 5–6 | Codex／Accio 在各种回答和任务中应用公司背景、事实、规范及经验；员工入口另行联调 |
| M4：首期交付 | 7–8 | Windows 长期运行、独立恢复、业务质量与 50 并发验收 |
| 中后期：扩容 | 8 的 200 配置 | 根据资料量和负载扩展并完成 200 验收 |

按依赖推进，优先完成最小流程：Task 1／2 的必要保存、权限和发布能力 → Task 3／4 的代表性格式处理与检索 → Task 5／6 的一个客户端取用并应用企业知识。随后补齐各任务其余能力和全部验收；文件获取也随最小流程验证。某个真实客户端环境暂不可用时，可继续独立工作，不虚报联调完成。工期随实际样本、入口改动量与人员安排估算，不要求先把所有细节调查完才开发。

## 7. 测试与交付规则

- 对权限、版本、原文件保存、处理恢复和检索质量先写有意义的失败场景，再实现并跑对应测试。配置文档的小改动只做必要校验，不编写镜像式测试。
- 每轮测试使用隔离环境和新输入；显式标注幂等／恢复场景的故障注入点。真实文件不上传到未经确认的外部模型或测试服务。
- 每任务在本地验证通过后形成一个可审查提交；没有 Git 时在开始实施任务初始化，不为本次写计划而创建代码仓库。
- 完成交付包含源码和锁定依赖、Windows 部署脚本、分类配置、客户端接入说明、备份恢复说明及测试证据。
- 记录解析支持范围与缺陷，不声称所有 PDF 表格、扫描件或 CAD／3D 已理解；原文件管理始终独立有效。
- 如选型、接口或权限实现变更，同步覆盖更新设计基线、分类方案中的受影响内容和本计划；不同客户端不形成不同企业知识规则。

## 8. 需求覆盖自查

| 已确认需求 | 对应任务 |
|---|---|
| Agent 企业知识扩展：理解公司知识并用于回答和任务 | 4、6、8 |
| 历史业务与案例可查，区分业务历史和旧修订版 | 0、2、4、6、8 |
| 员工上传、找资料、问内容、取文件 | 1、3–6 |
| Windows 服务器；Codex／Accio | 0、5–7 |
| 7 个截图组织＋公司公共知识；用途分类 | 1、4 |
| 文件先保存、后台解析和向量化 | 1、3 |
| 低浪费、按需读取、不严格卡 token | 4–6、8 |
| 自由提交、授权发布、版本冲突 | 1–2 |
| 可信员工身份与跨部门权限 | 0–2、4–6 |
| CAD／3D 保存完整包、辅助资料版本关联 | 1、3–4 |
| 撤下恢复、失败重试、独立备份 | 2–3、7 |
| 首期 50；中后期 200 | 8 |
| 复用成熟组件，避免重建入口与过分设计 | 0 及全局约束 |

总体检查结论：两类需求共用一套知识与文件后台的框架保持不变；9 步的本地开发和收尾已实施，真实客户端、正式服务器和业务验收缺项保留未完成状态。复选框区分本地证据与正式验收，不以合成测试替代实机联调。
