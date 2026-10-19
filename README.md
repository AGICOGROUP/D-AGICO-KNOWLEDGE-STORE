# AGICO 企业知识扩展库

**项目方：** [AGICOGROUP（Gavin）](https://github.com/AGICOGROUP)

**联合开发者：** [Dennis-pan-x](https://github.com/Dennis-pan-x)

为现有 Codex、Accio 等 Agent 提供企业知识与文件能力，复用现有网页／钉钉入口。已实现文件后台、身份与版本管理、解析索引、混合检索、业务 MCP、发布分类审核、原文件对账，以及备份恢复和 Windows 运行配置；Codex 已通过普通任务的合成资料验证。

**当前测试版：** 已分离代码与运行数据，完成初始 5 份及新增 9 份真实文件基本流程验证，见[目录与操作说明](docs/validation/local-preview.md)及[小文件测试结果](docs/validation/small-files.md)。新增 Windows 单目录安装入口已完成本机隔离服务验证，见[安装验证](docs/validation/server-setup.md)。企业资料整体质量、Accio／员工入口、实际服务器环境和容量仍待验收，尚未正式上线。

**账号系统正式数据：** 自 2026-10-14 起，当前及后续登记账号作为正式运行数据保留。服务器部署必须迁移最新数据库与原件，保持原密码、身份、角色和事业部权限，不重新初始化账号；详见[正式账号迁移](docs/server-setup.md)和[数据保护要求](agent.md)。账号数据保存在受保护运行目录，不提交 Git。

## 新 Windows 服务器安装

在管理员 PowerShell 中拉取代码，然后指定一个独立的知识库数据目录：

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\企业知识库"
```

脚本安装依赖、数据库与本地模型，生成受保护凭证，并注册自动启动服务。首次需要 Windows x64、管理员权限、Git 和联网下载；代码路径使用英文，数据目录使用独立空目录。代码与真实文件、数据库、模型、凭证分离，GitHub 只保存项目代码。详见[服务器安装与管理](docs/server-setup.md)。其他电脑接入所需的 HTTPS／域名和员工权限仍需按实际环境设置。

## 开发与当前本机测试环境

Python 3.12，依赖见 `uv.lock`。本机 PostgreSQL 程序位于 `.local`，数据已迁移至 `D:\企业知识库\postgres`，通过 `.local/test-layout.json` 指定的英文路径映射启动，仅监听 127.0.0.1:15432。真实样本位于独立的 `agico_preview` 数据库，开发测试仍使用 `agico_test`。本机由现有 watchdog 维护 API 与 worker；账号采用三级角色和邀请注册，正式服务器环境仍需部署验收。

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
uv run python -m agico_kb.accounts bootstrap --email administrator@example.com
uv run uvicorn agico_kb.main:app_factory --factory --host 127.0.0.1 --port 8000
```

初始化工具只创建一次首个开发人员，交互输入初始密码，首次登录必须修改。后续账号全部通过网页“账号管理”登记邮箱、角色和事业部，再复制一次性邀请链接；开发人员可邀请三种角色，管理员可邀请自己有管理权限的事业部内的管理员和普通用户，不能邀请开发人员。普通用户无邀请权限。密码加盐哈希保存，网页登录使用 HttpOnly 会话，改密或停用会撤销已有会话与 AI 授权。详见[账号与邀请设计](docs/design/account-login-and-invitations.md)。

另一个终端运行 `uv run python -m agico_kb.worker`。开发模式首次获取模型需要访问公共模型源；文档解析、OCR 与向量推理在本机执行。正式部署先单独准备模型，设置离线模式和预期模型身份，具体命令见[运维说明](docs/validation/operations.md)。模型文件指纹、维度、FastEmbed 与解析版本共同标记索引，防止不同模型空间混用。

### 入库图片识别（可选）

默认关闭。启用时，在服务器的受保护目录保存视觉 API 配置文件：第一行 `api=<密钥>`，第二行指定模型。主线路为官方 GLM：`AGICO_KB_VISION_API_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions`、`AGICO_KB_VISION_PDF_MODEL=glm-5.3-flash`。备用线路已改为端脑云：独立的 `AGICO_KB_VISION_FALLBACK_CONFIG_FILE` 保存其密钥及通过当前评测的模型，`AGICO_KB_VISION_FALLBACK_API_URL=https://cephalon.cloud/user-center/v1/model/chat/completions`。多个通过评测的备用模型可写为 `model=glm-5.3-flashx/glm-5.3-flash`，`AGICO_KB_VISION_FALLBACK_MODEL` 与第一项一致。没有通过图片题的模型不得加入视觉备用池。

命名备用池独立配置 `AGICO_KB_NAMING_FALLBACK_MODELS=glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash`，依次尝试，仅发送文件名、文字片段及可见项目线索。DeepSeek 作为命名可选备用，不向其发送图片；视觉接口拒绝 DeepSeek 系列。HY4 已弃用，不能加入任何任务的模型配置。此设置不改变官方 GLM 主线路。

主线路连接失败、超时、限流或服务端错误，重试一次后当前视觉请求临时走备用；备用模型仍遇到可重试故障时仅尝试配置内下一模型，下一独立请求仍优先官方 GLM。供应商由线路选择确定，不能因两家模型同名而直接跳过主线路；备用缓存区分供应商，Word/Excel 不把备用内容再存入主缓存。端脑云的视觉和命名请求通过共享存储中的文件锁，在 API/worker 进程之间共用一个并发槽，官方 GLM 保留原并发。输出截断继续扩展或分区处理，不跨供应商切换；余额不足明确报错。旧 B.AI 地址不再作为默认线路。视觉只在 **worker 处理新提交或显式重试** 时调用，不会因网页搜索、MCP 查询、下载或 Agent 对话调用；AI 命名也会调用配置的主/备用线路。支持 Word、Excel、PPT、PDF 和独立 PNG/JPG/TIFF/BMP；旧 Office 格式转换后按对应新格式处理。选中的图片会发送给外部服务，部署前按公司资料外发规则决定是否启用。

在受保护的 worker 运行配置中指定 `AGICO_KB_VISION_CONFIG_FILE` 为配置文件绝对路径。`AGICO_KB_VISION_MAX_IMAGES_PER_FILE` 默认 64，`AGICO_KB_VISION_MAX_CALLS_PER_DAY` 默认 0（不限量）；每天的调用计数和图片摘要缓存在知识库数据目录的 `originals/.vision`，相同图像不再次调用模型。短暂超时、网络错误、限流和模型输出未完成会在同一次解析中自动重试一次，每次请求记录调用次数，默认不限制每日调用；失败或仍有未处理图时保留“图片未解释”提示，正文解析照常进行。摘要供检索定位，工程数值、接线和尺寸仍以原图为准。无需此功能时移除该环境变量并重启 worker。

解析状态和提醒不等于内容准确率。用 `python scripts/score_ingestion.py <独立标注的评分清单.json> --vision-config <本地配置文件>` 对源文件全新解析、逐条核对页码／幻灯片／工作表上的事实。非图纸资料要求超过95%，并须完成独立参考核对；CAD 和图纸 PDF 改验收原图定位、图名图号、说明和明细，不做全图尺寸转写准确率验收。混合 PDF 按页分别检查。清单格式和边界见 [解析质量验收](docs/validation/ingestion-quality.md)、[图纸检索策略](docs/design/drawing-lookup-policy.md)。此命令不会读取已发布的解析结果，也不会修改知识库记录。

Word 读取正文、文本框、内容控件以及脚注／尾注，注释编号与正文引用相连；处理行内和浮动内容图时跳过小 Logo。Excel 原生文本框文字保留工作表、锚定单元格和段落；浮动图片和单元格内图片先用本地 OCR 收录可辨认文字，再由视觉模型转述图片。已识别的 AGICO、NORM 等品牌 Logo 跳过，不调用外部 API。空白绘图形状不视为缺失内容；真正的图表和未能提取的图片仍保留提醒。查询时不会调用视觉模型。

PPT 按有实际视觉内容的幻灯片渲染后转述，并保留页码；非图纸 PDF 的扫描正文优先采用通过本地布局与置信度筛选的 OCR，完成状态不依赖视觉 API 配置；复杂视觉内容再调用 API。PDF 字体映射乱码先回退到像素 OCR，不把乱码当作可靠正文；工艺流程图与大型轮廓字体图纸结合图名、图框字段、视图和版面分类，公差正文不会进入全图尺寸路线。图纸中的 OCR 图名、图号、说明和明细只对关键区域做像素复核；未核定字段仍保留具体提醒。数字核对容许20.00与20等明确等值格式，仍区分330/350、正负号、直径／半径和多段编号。独立图片保留本地 OCR 文字并按内容路线补充视觉转述。纯装饰图形不触发 PPT 缺失提醒。

解析提醒统一分三类：实际内容缺失、数字冲突、转换或接口故障保留为问题并推动通用能力修复；已完成 OCR／转述、空字符清理、公式缓存保留和按页存储等正常过程信息不展示；图纸需结合原图、修订正文、仅保存原件等保留一条简短资料说明。未知提醒默认保留。新旧记录在列表、审批详情、查询和读取接口使用同一展示规则；不会修改历史正文或诊断，提示消失不代表历史缺失已补齐。详见[提醒处理策略](docs/design/parse-notice-policy.md)。

## Agent 与文件适配

### 员工网页入口

浏览器打开服务根地址（本机 `http://127.0.0.1:8765/`），使用已激活的邮箱和密码登录后即可提交和查找文件。新成员只能通过管理员邀请链接加入，邮箱自动填入且不可修改，初始密码填写两次。可选择文件或整个文件夹，也可拖入文件夹及所有层级的子文件夹；显式选择本批所属事业部，不自动代选。资料类型与型号可不填，提交后进入待审核区。页面支持处理状态、已发布资料搜索、事业部／类型筛选和原件下载。

待审核和已发布的 PDF、Word、Excel、PPT、图片，点击缩略图或文件名即可进入只读文件预览，支持全文滚动、上一页／下一页、页码跳转、旋转与缩放。Office 使用本机 LibreOffice（可配置 `AGICO_KB_OFFICE_CONVERTER`）生成完整 PDF 预览副本，Excel 清除预览副本的打印区域限制并分页展示工作表；图片使用独立大图，多页 TIFF 可翻页。预览页按需生成、缓存与加载，关闭后取消请求并释放浏览器图片；原件与检索解析内容不变，不发送至第三方 Office 预览服务。CAD、模型、视频等维持图像预览。每次读取预览及缓存页都检查原件权限；撤回后不可继续读取旧缓存。详见[全文预览验证](docs/validation/document-preview-20261013.md)及[网页使用与验证](docs/validation/employee-portal.md)。

业务 MCP 地址为 API 的 `/mcp`，通过 OAuth 浏览器账号授权连接；配置仅含服务地址，员工密码不提供给 Agent。客户端完成授权码与 PKCE 流程后管理短期凭证。旧共享访问码在账号模式启用后不再认证。`kb_context` 按需查背景；`kb_search` 默认返回 5 项相关资料，`kb_read` 读取必要原文；上传、提交、状态与文件工具共用同一后台。负责人另有发布、撤下、恢复与历史管理工具。

将[简短使用约定](client/agent-usage.md)放入客户端现有指令机制，让 Agent 在需要公司知识时主动查阅并应用，复用任务内已有依据，避免全库读取。真实 Codex 合成任务已验证背景、事实、规范和经验会影响产出，见[结果与用量](docs/validation/codex.md)。Accio、网页和钉钉仍需实机联调。

受控传输脚本可由已经完成 OAuth 的可信客户端调用。在实际持有文件的环境设置 `AGICO_KB_URL` 和 `AGICO_KB_TOKEN`（当前 OAuth 短期访问凭证，不是员工密码或旧共享码），运行仓库附带的 `client/file_transfer.py`：

```powershell
uv run python client/file_transfer.py upload ./资料.docx --organization baiste --title 产品资料
uv run python client/file_transfer.py download VERSION_ID ./下载资料.docx
```

脚本校验大小与 SHA-256，凭证只发送到配置服务，不跟随重定向；下载验证完整后才生成最终文件，不覆盖已有文件。原文件不通过模型上下文传输。该脚本随仓库交付，未打包成独立员工应用。

结束开发数据库：`./scripts/dev-db.ps1 stop`。API 路由说明位于启动后的 `/docs`。

## 当前 API 流程

1. `POST /v1/uploads`，携带 OAuth Bearer（客户端）或登录会话＋CSRF（网页），以及 `Idempotency-Key`，提交文件名、大小、可选 SHA-256。
2. `PUT /v1/uploads/{upload_id}/content` 流式传原文件；大小与哈希校验完成后才允许提交。
3. `POST /v1/submissions`，绑定事业部、用途、原文件名等元数据；网页提交时设置 `naming_pending=true`。缺省用途为“其他资料”。原文件不依赖解析进程，草稿作者可下载。
4. 提交成功后，后台 worker 读取 `src/agico_kb/ai_naming_rules.md`，按原文件名、同批名称和同事业部可访问的项目记录用 AI 判断归属和规范标题。已成交项目沿用正式编号；未成交项目由数据库分配稳定的 `OPP-年份-流水号`，后续文件复用该编号。项目记录保存在数据库 `projects` 表，AI 不自行编造编号；命名任务进度保存在 `versions.capabilities.naming_review`。证据不足或 AI 暂不可用时显示“命名待核对”，由负责人通过 `PATCH /v1/versions/{version_id}/name` 确认后再解析；该接口可填写 `official_project_code`。原文件及下载文件名不变。`POST /v1/naming/suggest` 仅预览建议，不分配项目编号。
5. worker 按提交先后领取已完成命名的解析任务，默认最多同时处理 5 份文件；完成一份后补入下一份。`AGICO_KB_WORKER_CONCURRENCY` 可设为 1–5，数据库限制跨执行器的运行总数。页面区分“AI 命名中”“命名待核对”“等待解析”和“解析中---”。负责人在 `/v1/submissions` 查看草稿，通过 `/v1/versions/{version_id}/publish` 发布；待核对文件未确认名称时不能发布。可选 `category_id` 在发布事务中修正用途，无需重新上传；MCP `kb_publish` 使用同一参数。正文不完整时只有显式 `accept_incomplete=true` 才能发布，处理状态与警告仍如实显示。
6. `/v1/documents` 只列有权访问的当前正式版本；`/v1/versions/{version_id}` 和其 `/content` 返回状态和原文件。历史版本必须显式 `history=true` 且具有所属事业部负责人权限。

替换稿携带 `document_id`、`base_revision` 并沿用文件当前元数据。管理操作携带 `expected_revision`；发布还校验草稿实际 `base_revision`，修改请求里的期望号不能绕过旧稿冲突。冲突稿保留，需核对后作为新提交。

撤下：`POST /v1/documents/{id}/withdraw`；恢复曾发布版本：`POST /v1/versions/{id}/restore`；撤回自己的草稿：`POST /v1/submissions/{id}/withdraw`；负责人共享：`PUT /v1/documents/{id}/grants`；审计：`GET /v1/documents/{id}/audit`。

负责人通过 `GET /v1/documents?managed=true` 查找职责范围内全部文件（包括撤下资料），再用 `GET /v1/documents/{id}/versions` 选择历史版本恢复。普通员工不能通过这些管理接口看到撤下资料。

所有新请求重新认证。共享只授予读取，不复制文件；私有文档默认只有作者和所属负责人可读。新下载请求受当前授权与正式状态约束，已交付的副本不能追回。

## 一致性与阶段限制

- 保存临时文件、校验、同盘改名后才登记完整状态；断传清理临时文件。进程在改名与数据库提交之间退出可能留下孤立文件；`python -m agico_kb.operations reconcile` 只读报告缺失、损坏、无引用原件和陈旧临时文件，不自动删除。
- 文件提交、作业登记、发布切换、修订号及审计使用数据库事务；并发发布锁住同一文件记录，旧稿不能覆盖新稿。
- 解析子进程有超时，后台失败有限重试；索引完成后原子切换代次，旧进程不能覆盖新结果。向量失败时仍可检索已解析文字。
- 支持 DOCX／XLSX／PPTX、原生 PDF、扫描 PDF OCR 和文本资料；未覆盖的文本框、图形、公式缓存等如实提示。DWG／DXF 从原生文字和块属性建立带布局、图层和实体出处的图纸检索索引，保留图名图号、说明、明细的文字线索，不推算几何尺寸。PDF 在完整语义解析前逐页预检；扫描页复用本地 OCR 进行分类，明确的图纸页只建立原图关联索引，不调用全页尺寸视觉转写；正文、普通表格及分类不确定页继续完整解析。索引处理完成前不可审核通过。没有可提取原生文字的 CAD 会标为“仅保存原件”，审核后可作为文件库资料发布。图纸结果读取必须结合原图。DXF 由 `ezdxf` 读取；DWG 需在受保护运行配置中设置 `AGICO_KB_CAD_CONVERTER` 为本机 ODA File Converter 可执行文件绝对路径。转换只处理临时副本，失败时原文件仍可下载。旧二进制 Office／模型包仅保存原文件，可关联说明附件。
- 文件库支持 `.blend`、`.glb`、`.step` 和 `.stp` 的静态 3D 缩略图：Blender 在独立后台进程中渲染，STEP 先用 Open Cascade 网格化；不改变原件或解析结果。Windows 会自动查找 Program Files 下的 Blender，也可设置 `AGICO_KB_BLENDER` 指向可执行文件。超过 64 MiB、渲染器缺失、模型无可见网格或超时的文件显示原文件卡片，仍可下载原件。
- 内容候选先按权限过滤，融合关键词和精确向量排名；默认只返回短片段，再按需读取完整单元。长单元的向量可能受模型长度限制，原文与关键词仍保留并提示。
- 查询向量共用一个模型，忙时按先后顺序短暂等待；总数有上限，等待和推理共用超时预算，防止无界积压。
- 测试为真实 PostgreSQL 和 NTFS 文件操作，不证明 50／200 Agent 容量。
- 备份通过短暂暂停写入保存数据库和已完成原件，读取继续；恢复拒绝已有数据库或目录，先校验哈希，再恢复权限、正式版本与待处理任务。只在隔离的新目标演练恢复。
- Windows 安装脚本默认仅生成配置；正式安装需明确目标和专用账号。TLS、ACL、无人登录启动及独立灾备尚待目标服务器验证，当前不要把开发服务直接开放给企业多人使用。

开发计划：[计划](docs/superpowers/plans/2026-09-15-enterprise-knowledge-store.md)。当前验证：[整体报告](docs/validation/final-review.md)、[REST／MCP 50 身份短测](docs/validation/load-50.md)、[进程重启与 32 MiB 文件](docs/validation/process-restarts.md)、[Codex](docs/validation/codex.md)、[运维与恢复](docs/validation/operations.md)、[接入契约](docs/validation/integration-contract.md)、[进度](docs/validation/progress.md)。
