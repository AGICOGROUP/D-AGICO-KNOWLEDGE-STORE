# Windows 运维与隔离恢复

## 当前证据与边界

本轮在本机 PostgreSQL 16.15 / pgvector 0.8.6 上实测逻辑备份、新数据库及新目录恢复；随机业务 schema 和生产默认 `public` 均覆盖。每次测试新建合成原件及隔离对象。测试覆盖原件字节、私有权限、正式活动版本、关键词/向量检索、排队/运行作业及锁故障。测试目录位于同磁盘，只是恢复验证，不是独立灾备。

本地真实数据库停止/启动演练通过：数据库停止时 live=200、ready=503，启动后 ready=200，首个普通鉴权读取、搜索、原件和发布/作业数量均正确。演练发现并修复闲置连接池中的旧连接：每次借出连接执行 psycopg_pool 的健康检查，自动丢弃已断开的连接。测试控制子进程在 Windows 使用 DEVNULL，避免新数据库进程继承捕获管道而使测试等待不结束。这个测试需显式设置 `AGICO_TEST_RESTART_DB=1`，只允许在该本地开发集群无人使用时运行。

服务脚本已生成 XML 并通过 PowerShell 语法检查。尚未在指定服务器安装服务，未修改防火墙、创建账号或执行 Windows 重启。无人登录启动、真实服务故障重启、日志达到阈值后的轮转、TLS 代理及跨磁盘/跨机灾备仍须目标服务器验收。不要据此宣称已完成生产长期运行验收。

## 安装目录、账号与依赖

1. 将本仓库及 `uv.lock` 放在固定绝对目录（示例 `D:\Agico\App`），执行 `uv sync --frozen --no-dev`。Python 固定为 3.12；部署保留源码、迁移和锁文件。升级新建一个应用版本目录，保留上一版；不要在服务运行中更新其虚拟环境。
2. 由管理员准备专用普通本地或域账号（如 `SERVER\agico_kb`），授予“作为服务登录”，不加入管理员组。可选已有 gMSA，系统不要求 Active Directory。API 和 worker 使用同一专用账号；PostgreSQL 使用独立数据库账号。运行账号只能读应用及模型，写原件和日志，不能修改程序。维护账号另存，拥有新建恢复数据库/安装 vector 的权限；不放入服务配置。
3. PostgreSQL 16 / pgvector 使用经过验证的软件包并单独按其官方服务流程部署。本脚本只管理 API/worker；实际 PostgreSQL Windows 服务、磁盘、内存、备份网络目标由运维确认。数据库连接池每个进程最多 8 个，解析子进程与 API 分离，查询向量同时仅一个任务。
4. 从 WinSW 官方 **v2.12.0** 发布取得 exe，并独立记录真实 SHA-256。脚本不下载包装器；安装前核验 exe 路径、SHA-256、FileVersion。不要把示例哈希当真实签名。
5. 从 `deploy/windows/runtime.example.json` 制作受保护的本地 JSON；填写真实 DSN、绝对原件/模型目录和模型身份。不要提交这个文件、在终端打印配置或把 DSN 放到命令行。配置 DACL 关闭继承，只允许专用账号（读）、SYSTEM 和 Administrators；父目录仅管理员可改，避免替换配置。`config-loader.ps1` 每次加载检查 DACL，不满足则拒绝启动。

ACL 示例由管理员替换真实路径及账号后执行：

```powershell
icacls D:\Agico\Secrets\runtime.json /inheritance:r
icacls D:\Agico\Secrets\runtime.json /grant:r 'SERVER\agico_kb:R' '*S-1-5-18:F' '*S-1-5-32-544:F'
```

检查并删除已有的其他显式授权；`/grant:r` 不会自动删除其他账号的 ACE。应用/脚本/服务 XML 目录亦应禁止业务账号写入。原件、备份、凭证分别授权；备份含访问令牌哈希及企业原文，需受控加密存放，密钥另行保管。

## 模型准备与离线运行

首次联网准备在管理员维护窗口单独执行：

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.embeddings --cache D:\AgicoModels
```

把输出的完整模型 identity 写入受保护配置 `AGICO_KB_EXPECTED_MODEL_IDENTITY`，设置 `AGICO_KB_MODEL_OFFLINE` 为字符串 `true`。模型身份包含模型名、本地模型文件哈希、维度、fastembed 版本和实现标识。服务启动要求离线及身份 pin；指纹不匹配时语义检索/向量入库失败，保留关键词检索和原文。代码默认开发模式兼容联网；生产使用启动脚本强制离线。模型缓存需要单独受控归档，恢复到明确目录；本逻辑备份不内嵌模型大文件。上游下载不保证未来能取得同一模型，所以保留模型归档及锁文件，恢复后验证 identity 再开放服务。

## 生成与安装服务

默认只生成新目录下的 `AgicoKbApi.xml` / `AgicoKbWorker.xml`，不修改服务或账号：

```powershell
$install = @{
  PythonExe = 'D:\Agico\App\.venv\Scripts\python.exe'
  AppRoot = 'D:\Agico\App'
  ConfigFile = 'D:\Agico\Secrets\runtime.json'
  OutputDirectory = 'D:\Agico\Services-v1'
  ServiceAccount = 'SERVER\agico_kb'
}
D:\Agico\App\scripts\install-services.ps1 @install
```

审核后真正安装时，使用另一个尚不存在的输出目录，并额外传入：`-Install -TargetComputer SERVER -AccountProvisioned -WinSWExe <已校验绝对路径> -WinSWSha256 <真实64位哈希> -Credential (Get-Credential 'SERVER\agico_kb')`。普通账号凭证通过 PowerShell `PSCredential` 交给 SCM 的 `New-Service`，不写 XML/argv；gMSA 无需 Credential。禁止将密码做成脚本字符串。安装脚本要求显式提升的管理员会话，拒绝已有服务，不自动启动服务，失败不自动删除现场。普通账号的显式安装分支还通过 .NET `System.Diagnostics.EventLog` 为服务 ID 注册 Application 事件源（若不存在），与 [WinSW 2.12.0 原生安装行为](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/src/WinSW/Program.cs)一致；无需 PowerShell 7 缺少的 `New-EventLog` cmdlet。默认生成模式不访问或创建事件源，实际事件日志写入仍须目标服务器验收。

生成配置指定绝对路径、Automatic 延迟启动、10 秒后重启（绝不重启 Windows）、30 秒停止等待、每个 stdout/stderr 日志 10 MB 阈值及 8 个轮转文件。包装器自身日志还应纳入磁盘监控。使用官方 [WinSW 2.12.0 XML 配置](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/xmlConfigFile.md)和[日志配置](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/loggingAndErrorReporting.md)；实际 SCM 安装和轮转尚待目标机实测。

配置验证、模型准备和数据库迁移完成后，由管理员 `Start-Service AgicoKbApi,AgicoKbWorker`。启动脚本不旁路组织执行策略；按组织要求签名/解除可信文件阻止。服务控制台不会弹出交互桌面窗口。

## 健康、网络与日常检查

- API 默认绑定 `127.0.0.1:8765`，本机 `/health/live` 只报告进程；`/health/ready` 用独立连接做 2 秒连接/查询限时检查，失败返回 503，不占用应用池等待槽。ready 不代表模型已加载、队列已清空或完整业务权限验收。
- 企业访问使用明确主机名和受信 TLS 反向代理，允许列表中配置实际主机名，代理保留授权及流式 MCP 行为。只开放企业来源到代理 HTTPS，PostgreSQL 与应用端口限制本机/必要来源。具体证书、代理、地址和防火墙尚未配置，无虚构公网入口。
- 检查失败/积压作业、磁盘剩余量、原件/备份容量、日志增长、最近一次成功备份及恢复时间。模型不可用不会破坏原件；处理反复失败达上限后需要有权限人员重试。
- API 重启后重新鉴权；worker 使用 PostgreSQL lease 与 generation fencing，过期 worker 不得提交旧结果。服务中断期间可能等待租约过期，正常恢复不强行重置现场 lease。

## 原文件只读对账

加载上述受保护配置后，可对当前数据库与原文件目录做只读核对：

```powershell
D:\Agico\App\.venv\Scripts\python.exe -m agico_kb.operations reconcile --stale-part-age-seconds 86400 --timeout 30
```

命令返回 JSON，列出缺失原件、大小或 SHA-256 不符的原件、无 complete/submitted 上传引用的 `.blob`，以及最后修改时间超过指定秒数的 `.part` 候选；默认年龄为 24 小时。只扫描当前平面原件目录中应用命名的文件，解析临时目录不在范围内。路径仅返回相对文件名，不返回数据库凭证；命令不会删除、改名或修复任何文件。

扫描复用备份写入暂停锁，等待正在完成的上传事务结束后，再取得稳定的数据库引用；新上传可以继续传入临时文件，但最终改名与登记需等待本次对账结束后重试。读取保持可用。陈旧 `.part` 仍可能属于暂停中的上传，只是人工核对候选，不能据此直接删除；不经过应用的数据库／磁盘修改不受该锁保护。结果对应本次扫描时段，释放锁后现场可能改变。

默认等待写事务最多 30 秒，获得锁后的扫描预算也是 30 秒；每个文件及每 1 MiB 哈希块检查时间，底层单次磁盘读操作无法强制中断，因此这不是硬性总耗时保证。超时、文件不可读或锁连接中断时返回失败、不输出完整报告，并释放写入暂停。大资料库可在维护窗口显式调整 `--timeout`，根据输出的 `pause_seconds` 安排频率。发现异常后由运维核实原件及备份，不自动清理现场。

## 备份

事先配置**独立磁盘、受控网络位置或另一主机**的新目标目录。任务不自动选择磁盘或宣称同盘副本是灾备。用受保护配置加载 DSN 到当前进程环境；避免 PowerShell Transcript/调试回显配置。备用维护配置可通过同一个 `Import-ProtectedKbConfig` 加载，服务配置不包含维护凭证。

```powershell
. D:\Agico\App\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile D:\Agico\Secrets\runtime.json -Account 'SERVER\agico_kb'
D:\Agico\App\scripts\backup.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915
```

备份依次排队取得 schema 专用独占 advisory lock、等待已开始的写事务、复制数据库引用的 complete/submitted 原件并校验、执行 custom 格式 schema 范围 `pg_dump`、记录迁移版本/分类/模型身份/依赖锁及哈希，然后释放锁。新写入含上传最终 rename、提交、发布/撤下/授权、文件关联、身份令牌和 worker 写入，均快速返回可重试 `MAINTENANCE` 503；读取、鉴权继续，临时上传流可以继续但最终提交需重试。写事务最长等待 30 秒，pg 工具默认 300 秒；超时恢复写入并保留不完整现场。后台解析若遇暂停，将由保留的租约和重试恢复。

清单 `manifest.json` 只有成功释放原始锁连接后才成为完成标记；检查清单中的 `pause_seconds`。被终止的锁连接不能产生已完成备份。原件缺失/哈希错误则失败。`.part`、解析目录及未引用孤儿文件不作为完成原件备份；不自动删除孤儿。未通过应用写门的直接数据库/文件修改不受保护，备份期间禁止这类维护写入。大量原件时暂停包含完整复制时间；监控实际时长，必要时再设计存储快照。

## 新目标恢复与切换

只接受受控来源的可信备份；SHA-256 能检出意外损坏，不能代替签名鉴真，PostgreSQL archive 包含可执行 SQL。恢复账号仅注入 `AGICO_KB_MAINTENANCE_URL` 环境变量（从独立受保护配置加载）；命令行没有 DSN/密码。

```powershell
D:\Agico\App\scripts\restore.ps1 -PythonExe D:\Agico\App\.venv\Scripts\python.exe -PgBin 'C:\Program Files\PostgreSQL\16\bin' -Archive E:\AgicoBackup\run-20260915 -NewDatabase agico_recovery_20260915 -NewStorage D:\AgicoRecovery\run-20260915
```

恢复先验证清单、全部哈希和路径，再用 template0 创建新数据库；已有数据库或目录一律拒绝。预装 public.vector、使用 `pg_restore --no-owner --no-acl`，默认 public 的 CREATE SCHEMA 项被精确跳过，避免与 template0 自带 public 冲突。恢复检查数据库原件引用与文件。业务权限/身份/活动版本随表数据保留；数据库角色与文件系统 ACL 由运维单独建立，不恢复来源库 owner/ACL。

仅新隔离库内 running 作业重新排队、清空 generation/lease 并重置尝试数；已发布版本和现有活动 generation 保留。queued 作业维持队列，失败/取消状态保留。恢复失败留下 `RESTORE_INCOMPLETE` 和新库/目录供检查，启动脚本拒绝使用此目录；绝不自动 DROP 或覆盖现网。

隔离启动新配置后，验证 `/health/ready`、指定原件字节、普通/跨事业部/私有权限、正式版本和搜索引用；启动 worker 并确认遗留作业处理正常。校验模型归档及 identity，重新签发/撤销必要令牌。人工审核后停止旧写入并切流；未验证前旧服务继续使用原库。切换后产生新写入时，回退会涉及新增数据，不能简单恢复旧快照。纯代码升级可以保留数据原址并回到上一应用目录，但只在迁移向后兼容且验证通过时这样做。

## 目标服务器最终验收清单

记录实际服务器、服务身份、软件哈希、独立备份位置和执行时间；执行无人登录 Windows 重启、API/worker 进程退出重启、数据库临时不可用后 ready 恢复、日志轮转、TLS/防火墙和隔离恢复。没有这些目标机证据，不勾选 Task 7 的完整生产运行通过条件。
