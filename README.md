# AGICO 企业知识扩展库

为现有 Codex、Accio 等 Agent 提供企业知识与文件能力，复用现有网页／钉钉入口。已实现文件后台、身份与版本管理、解析索引、混合检索、业务 MCP、发布分类审核、原文件对账，以及备份恢复和 Windows 运行配置；Codex 已通过普通任务的合成资料验证。

**当前测试版：** 已分离代码与运行数据，完成初始 5 份及新增 9 份真实文件基本流程验证，见[目录与操作说明](docs/validation/local-preview.md)及[小文件测试结果](docs/validation/small-files.md)。新增 Windows 单目录安装入口已完成本机隔离服务验证，见[安装验证](docs/validation/server-setup.md)。企业资料整体质量、Accio／员工入口、实际服务器环境和容量仍待验收，尚未正式上线。

## 新 Windows 服务器安装

在管理员 PowerShell 中拉取代码，然后指定一个独立的知识库数据目录：

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\企业知识库"
```

脚本安装依赖、数据库与本地模型，生成受保护凭证，并注册自动启动服务。首次需要 Windows x64、管理员权限、Git 和联网下载；代码路径使用英文，数据目录使用独立空目录。代码与真实文件、数据库、模型、凭证分离，GitHub 只保存项目代码。详见[服务器安装与管理](docs/server-setup.md)。其他电脑接入所需的 HTTPS／域名和员工权限仍需按实际环境设置。

## 开发与当前本机测试环境

Python 3.12，依赖见 `uv.lock`。本机 PostgreSQL 程序位于 `.local`，数据已迁移至 `D:\企业知识库\postgres`，通过 `.local/test-layout.json` 指定的英文路径映射启动，仅监听 127.0.0.1:15432。真实样本位于独立的 `agico_preview` 数据库，开发测试仍使用 `agico_test`。无系统服务或正式员工账号。

本机测试 API／worker：`./scripts/test-runtime.ps1 start`；状态与停止分别用 `status`、`stop`。服务地址 `http://127.0.0.1:8765`，MCP 路径 `/mcp`。本机路径和凭证不随仓库分发，新机器按运维说明配置。

```powershell
uv sync --locked --group load
./scripts/dev-db.ps1 start
uv run python scripts/prepare_dev_db.py
uv run pytest -q
```

完整开发测试需安装上面的 `load` 依赖组（含负载工具及资源采样库）；正式服务不需要该组。测试使用专用 `agico_test` 数据库，每个用例创建随机 schema 和新合成文件；结束只清理该 schema 和测试临时目录。当前测试夹具读取本机 `.local/pg-password.txt`，不能指向生产数据库。新机器需先按[组件记录](docs/validation/component-check.md)安装 PostgreSQL、初始化集群和该凭证文件；`.local` 不随代码分发。

启动 API 与 worker 前，由运维在受保护环境中设置 `AGICO_KB_DATABASE_URL`（正式运行业务使用非超级用户）和 `AGICO_KB_STORAGE_ROOT`（两进程共用的可写 NTFS 绝对目录）；凭证不放命令参数、仓库或日志。`AGICO_KB_MODEL_CACHE` 默认 `.local/models`，部署时指定共用绝对路径。`AGICO_KB_ALLOWED_HOSTS` 指定 MCP 允许域名，默认仅本机。迁移需要已安装 pgvector。

```powershell
uv run python -m agico_kb.admin migrate
uv run python -m agico_kb.admin set-identity employee-id 员工姓名 baiste member
uv run python -m agico_kb.admin issue-token employee-id
uv run uvicorn agico_kb.main:app_factory --factory --host 127.0.0.1 --port 8000
```

`issue-token` 在运维终端仅输出一次密钥，请交给可信客户端配置，不记录输出。`revoke-tokens employee-id` 撤销该身份所有令牌。负责人角色为 `publisher`，每个事业部独立配置。身份运维 CLI 需要数据库管理权限，不向普通员工开放。

另一个终端运行 `uv run python -m agico_kb.worker`。开发模式首次获取模型需要访问公共模型源；文档解析、OCR 与向量推理在本机执行。正式部署先单独准备模型，设置离线模式和预期模型身份，具体命令见[运维说明](docs/validation/operations.md)。模型文件指纹、维度、FastEmbed 与解析版本共同标记索引，防止不同模型空间混用。

## Agent 与文件适配

### 员工网页入口

浏览器打开服务根地址（本机 `http://127.0.0.1:8765/`），输入成员访问码即可提交和查找文件。上传前必须显式选择所属事业部，不自动代选；提交后进入待审核区。页面支持处理状态、已发布资料搜索、事业部／类型筛选和原件下载。详见[网页使用与验证](docs/validation/employee-portal.md)。

业务 MCP 地址为 API 的 `/mcp`，使用独立 Bearer token。`kb_context` 按需查背景；`kb_search` 默认返回 5 项相关资料，`kb_read` 读取必要原文；上传、提交、状态与文件工具共用同一后台。负责人另有发布、撤下、恢复与历史管理工具。

将[简短使用约定](client/agent-usage.md)放入客户端现有指令机制，让 Agent 在需要公司知识时主动查阅并应用，复用任务内已有依据，避免全库读取。真实 Codex 合成任务已验证背景、事实、规范和经验会影响产出，见[结果与用量](docs/validation/codex.md)。Accio、网页和钉钉仍需实机联调。

在实际持有文件的环境配置 `AGICO_KB_URL` 和 `AGICO_KB_TOKEN`，运行仓库附带的 `client/file_transfer.py`：

```powershell
uv run python client/file_transfer.py upload ./资料.docx --organization baiste --title 产品资料
uv run python client/file_transfer.py download VERSION_ID ./下载资料.docx
```

脚本校验大小与 SHA-256，凭证只发送到配置服务，不跟随重定向；下载验证完整后才生成最终文件，不覆盖已有文件。原文件不通过模型上下文传输。该脚本随仓库交付，未打包成独立员工应用。

结束开发数据库：`./scripts/dev-db.ps1 stop`。API 路由说明位于启动后的 `/docs`。

## 当前 API 流程

1. `POST /v1/uploads`，携带 Bearer 与 `Idempotency-Key`，提交文件名、大小、可选 SHA-256。
2. `PUT /v1/uploads/{upload_id}/content` 流式传原文件；大小与哈希校验完成后才允许提交。
3. `POST /v1/submissions`，绑定事业部、用途、标题、可选产品／型号／项目／业务日期；缺省用途为“其他资料”。原文件不依赖解析进程，草稿作者可下载。
4. worker 处理解析与索引；负责人在 `/v1/submissions` 查看草稿，通过 `/v1/versions/{version_id}/publish` 发布。可选 `category_id` 在发布事务中修正用途，无需重新上传；MCP `kb_publish` 使用同一参数。正文不完整时只有显式 `accept_incomplete=true` 才能发布，处理状态与警告仍如实显示。
5. `/v1/documents` 只列有权访问的当前正式版本；`/v1/versions/{version_id}` 和其 `/content` 返回状态和原文件。历史版本必须显式 `history=true` 且具有所属事业部负责人权限。

替换稿携带 `document_id`、`base_revision` 并沿用文件当前元数据。管理操作携带 `expected_revision`；发布还校验草稿实际 `base_revision`，修改请求里的期望号不能绕过旧稿冲突。冲突稿保留，需核对后作为新提交。

撤下：`POST /v1/documents/{id}/withdraw`；恢复曾发布版本：`POST /v1/versions/{id}/restore`；撤回自己的草稿：`POST /v1/submissions/{id}/withdraw`；负责人共享：`PUT /v1/documents/{id}/grants`；审计：`GET /v1/documents/{id}/audit`。

负责人通过 `GET /v1/documents?managed=true` 查找职责范围内全部文件（包括撤下资料），再用 `GET /v1/documents/{id}/versions` 选择历史版本恢复。普通员工不能通过这些管理接口看到撤下资料。

所有新请求重新认证。共享只授予读取，不复制文件；私有文档默认只有作者和所属负责人可读。新下载请求受当前授权与正式状态约束，已交付的副本不能追回。

## 一致性与阶段限制

- 保存临时文件、校验、同盘改名后才登记完整状态；断传清理临时文件。进程在改名与数据库提交之间退出可能留下孤立文件；`python -m agico_kb.operations reconcile` 只读报告缺失、损坏、无引用原件和陈旧临时文件，不自动删除。
- 文件提交、作业登记、发布切换、修订号及审计使用数据库事务；并发发布锁住同一文件记录，旧稿不能覆盖新稿。
- 解析子进程有超时，后台失败有限重试；索引完成后原子切换代次，旧进程不能覆盖新结果。向量失败时仍可检索已解析文字。
- 支持 DOCX／XLSX／PPTX、原生 PDF、扫描 PDF OCR 和文本资料；未覆盖的文本框、图形、公式缓存等如实提示。旧二进制 Office／CAD／模型包仅保存原文件，可关联说明附件。具体边界见本批次报告。
- 内容候选先按权限过滤，融合关键词和精确向量排名；默认只返回短片段，再按需读取完整单元。长单元的向量可能受模型长度限制，原文与关键词仍保留并提示。
- 查询向量共用一个模型，忙时按先后顺序短暂等待；总数有上限，等待和推理共用超时预算，防止无界积压。
- 测试为真实 PostgreSQL 和 NTFS 文件操作，不证明 50／200 Agent 容量。
- 备份通过短暂暂停写入保存数据库和已完成原件，读取继续；恢复拒绝已有数据库或目录，先校验哈希，再恢复权限、正式版本与待处理任务。只在隔离的新目标演练恢复。
- Windows 安装脚本默认仅生成配置；正式安装需明确目标和专用账号。TLS、ACL、无人登录启动及独立灾备尚待目标服务器验证，当前不要把开发服务直接开放给企业多人使用。

开发计划：[计划](docs/superpowers/plans/2026-09-15-enterprise-knowledge-store.md)。当前验证：[整体报告](docs/validation/final-review.md)、[REST／MCP 50 身份短测](docs/validation/load-50.md)、[进程重启与 32 MiB 文件](docs/validation/process-restarts.md)、[Codex](docs/validation/codex.md)、[运维与恢复](docs/validation/operations.md)、[接入契约](docs/validation/integration-contract.md)、[进度](docs/validation/progress.md)。
