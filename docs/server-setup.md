# Windows 服务器安装

代码目录与知识库数据目录分开。首次安装只需指定 `DataRoot`，脚本自动安装固定版本的 Python、PostgreSQL/pgvector、应用依赖和本地模型，初始化数据库和受保护运行配置，并注册三个自动启动的 Windows 服务。

## 首次安装

要求：Windows x64、管理员 PowerShell 5.1、Git、可访问 GitHub/PyPI/conda-forge 和模型下载站点的网络，以及足够磁盘空间。服务包装器使用 Windows 的 .NET Framework 运行环境。代码放在英文路径；数据目录支持中文和空格，使用本机 NTFS 磁盘上的空目录，不能位于代码目录内。已有测试库不能直接当作全新安装目录。

在**管理员 PowerShell**执行：

当前项目的 API 配置已保存在 `.deployment-private/providers.json`，该私有目录不提交 Git，也不属于任务输出。使用本机准备的**私有部署包**迁移，解压后该目录随源码保留；首次 setup 自动将同一密钥、接口、视觉模型池和命名模型池导入服务器受保护配置，并自动换成服务器路径，无需重新填写 API。仅从 Git clone 或公开源码包安装不会包含密钥。安装会保护私有目录；私有部署包仅用于可信服务器间传输，不能作为公开下载资料。

```powershell
git clone https://github.com/AGICOGROUP/D-AGICO-KNOWLEDGE-STORE.git D:\AGICO-KNOWLEDGE-STORE
cd D:\AGICO-KNOWLEDGE-STORE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -DataRoot "D:\企业知识库"
```

首次下载需要时间。失败后修复网络等原因，使用同一命令重试；脚本校验安装归属，复用已保存凭证和数据库。已完成安装再次执行会检查并启动服务，不会重建知识库，也不会自动升级依赖。不要移动已绑定的代码或数据目录。

默认 API 为 `http://127.0.0.1:8765`，MCP 为 `http://127.0.0.1:8765/mcp`，数据库仅监听本机 `15432`。端口被其他程序占用时，首次安装可显式指定 `-ApiPort`、`-DatabasePort`；同机多实例还需不同 `-ServicePrefix` 和独立代码、数据目录。

## 数据与访问

| 位置 | 内容 |
| --- | --- |
| 代码目录 `.runtime` | 自动下载的程序、Python 环境、安装缓存；不提交 Git |
| 数据目录 `originals` | 原始文件 |
| 数据目录 `postgres` | 元数据、权限、版本、文本和向量索引 |
| 数据目录 `models` | 本地向量与 OCR 模型 |
| 数据目录 `config` | 安装归属、受保护凭证和服务配置 |
| 数据目录 `logs`、`temp`、`services` | 运行日志、临时文件和服务包装器 |

安装结束后，`config/accounts-setup.json` 提供首次账号初始化说明，不生成员工共享访问码。先加载受保护运行配置，再创建首个开发人员：

```powershell
. .\deploy\windows\config-loader.ps1
Import-ProtectedKbConfig -ConfigFile "D:\企业知识库\config\runtime.json" -Account ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
# setup 安装环境使用 .runtime；本机开发环境使用 .venv。
$PythonExe = Join-Path $PWD.Path ".runtime\venv\Scripts\python.exe"
& $PythonExe -m agico_kb.accounts bootstrap --email administrator@example.com
```

工具交互输入初始密码，密码不进入命令参数。已有任何账号时拒绝重复初始化。首个账号为开发人员，第一次登录必须改密；新密码由用户自己设置。后续用户只能由有权账号登记邮箱、角色、事业部并分享一次性邀请链接加入。管理员可邀请自己有管理权限的事业部内的管理员和普通用户，不能邀请开发人员；普通用户不能邀请。详见[账号权限与邀请](design/account-login-and-invitations.md)。

浏览器访问 `/health/ready` 检查就绪状态。直接打开 `/mcp` 返回 401 并提供 OAuth 发现地址是正常的未授权状态。MCP 客户端仅配置服务 URL，再启动账号授权；由用户本人在知识库网页登录并确认，不能将密码发送给 AI。Codex 可使用 `codex mcp login agico-kb`，其他客户端必须支持 OAuth。

API 默认只监听本机。其他电脑使用时配置可信域名、HTTPS 反向代理和防火墙；`AGICO_KB_PUBLIC_URL` 为邀请链接默认地址，`AGICO_KB_ALLOWED_HOSTS` 必须包含 MCP 实际入口域名。临时隧道网址可能改变，不能当正式长期邀请入口。

## 已有安装升级账号系统（运维一次性操作）

先备份数据库与原件，停止 API 和 worker，更新程序及依赖，在当前安装环境加载受保护配置后执行 `python -m agico_kb.admin migrate`。迁移 005 只增加账号、邀请、会话和 OAuth 表，不修改原文件、解析内容、上传者和历史审计；在首个账号创建前保留旧认证模式，创建成功后启用账号模式并拒绝旧共享码。

使用上面的账号初始化命令创建一个明确指定的开发人员，再启动服务并验证登录、首登改密、邀请和 MCP 发现。旧历史身份不会自动转换为可登录账号。旧共享配置中的静态 Authorization 请求头应移除，客户端重新通过账号授权。管理员停用、改密或权限调整撤销对应账号既有会话和 AI 授权。

## 当前正式账号迁往服务器（2026-10-14 起）

当前本机实例已经启用正式账号系统，已登记账号是正式运行数据。部署代码本身不会携带数据库；必须迁移最新数据库和原件备份，不能将服务器的空库当成现有项目，也不能重新创建首个管理员代替迁移。

1. 切换前对当前实际实例运行 `agico_kb.operations backup`，生成含完整逻辑数据库与已提交原件的备份。数据库包含 account_settings、account_users、principals、memberships、account_invitations、account_audit 及 OAuth 记录。备份保存于受保护数据目录，不进入 Git；本次基线备份仅是当前时点快照，正式切换前必须再取最新备份。
2. 准备服务器运行环境，**不要创建任何新账号**。使用服务脚本 `-Action Stop` 停止目标三个服务，再只启动目标数据库服务。恢复须创建新的数据库；服务管理要求原件仍位于 `DataRoot\originals`。确认全新目标 originals 是普通目录且完全为空后，将这个空目录移动到同一 DataRoot 下未存在的临时名称，使 originals 不存在。若目录不为空或含重解析点，停止操作，不能清空、覆盖或挪动已有业务资料。按下方命令恢复，来源本机数据保持不变。
3. 保留服务器 setup 生成的 `agico_app` 用户和密码，仅将受保护 runtime.json 的数据库名改为恢复的新库、schema 改为备份 schema；原件根目录保持 `DataRoot\originals`。设置正式 HTTPS 入口与允许域名，执行服务脚本 `-Action Install` 同步 API/worker 配置、重新应用 ACL 并启动服务。`-Action Start` 不同步修改后的主配置。不要运行 accounts bootstrap；已有账号的初始化工具也会拒绝重建。
4. 切流前核对账号登记数量及身份关联，验证已激活的管理员和员工以原密码登录、权限范围一致、停用账号仍被拒绝、未激活账号和邀请记录存在；同时核对原件和版本关系。未通过则保持本机实例作为原有效入口，不用新空库替代。
5. 地址改变后用户重新登录，AI 客户端在新 MCP 地址重新授权；账号密码无需因搬迁而更改。旧邀请地址需换成正式入口或重新生成链接，原有效期、单次使用和停用规则继续生效。

仅全新且无账号的独立知识库适用上述“首次初始化”步骤。项目优化、测试资料清理、代码更新及 Git 同步不得清除或覆盖正式账号。账号系统的日期与版本状态不代表整套知识库已完成正式服务器部署验收。

恢复示例（管理员 PowerShell，已停止目标应用、只启动了目标数据库；Archive 换成受控备份路径）：

```powershell
. .\deploy\windows\config-loader.ps1
$Operator = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Maintenance = Read-ProtectedKbJson -ConfigFile "D:\企业知识库\config\maintenance.json" -Account $Operator
$env:AGICO_KB_MAINTENANCE_URL = $Maintenance.database_url
try {
    .\scripts\restore.ps1 -PythonExe "$PWD\.runtime\venv\Scripts\python.exe" -PgBin "$PWD\.runtime\postgres\Library\bin" -Archive "E:\知识库备份\cutover" -NewDatabase agico_restored -NewStorage "D:\企业知识库\originals" -RuntimeRole agico_app
} finally {
    Remove-Item Env:\AGICO_KB_MAINTENANCE_URL -ErrorAction SilentlyContinue
    $Maintenance = $null
}
```

`-RuntimeRole` 必须是目标已有的非超级用户登录角色，工具授予新库业务表和序列的运行权限。后续 schema 升级由维护身份执行迁移，新增表也需授予运行角色相应权限。正式账号及业务授权是表内数据，与此数据库运行角色分开。

## 可选解析与预览依赖

基础安装不自动安装 LibreOffice、ODA File Converter、FFmpeg 或 Blender。Office 全文预览与旧格式转换依赖 LibreOffice，DWG 文字和缩略图依赖 ODA，视频缩略图依赖 FFmpeg，部分三维缩略图依赖 Blender。填写 `AGICO_KB_OFFICE_CONVERTER`、`AGICO_KB_CAD_CONVERTER`、`AGICO_KB_BLENDER` 的绝对可执行文件路径，FFmpeg 加入服务可见 PATH；授予 API/worker 服务身份必要的读取/执行权限。

官方 GLM 与端脑云密钥由项目私有配置自动导入目标 `DataRoot\config` 的独立运行文件，主配置自动生成 `AGICO_KB_VISION_CONFIG_FILE`、`AGICO_KB_VISION_FALLBACK_CONFIG_FILE` 的目标路径，无需填写开发机路径。端脑云备用地址为 `https://cephalon.cloud/user-center/v1/model/chat/completions`；配置只包含当前评测通过的视觉模型，以 `/` 分隔，第一项对应 `AGICO_KB_VISION_FALLBACK_MODEL`。服务安装保护密钥文件，API/worker 仅能读取。公开源码包、Git 和命令行不包含密钥；私有部署包带同一 API 配置，必须受控保管。部署后验证主/备用连接。API/worker 指向同一 AGICO_KB_STORAGE_ROOT，命名与视觉请求用 .vision 文件锁跨进程串行；其他实例或外部应用共用密钥仍可能触发供应商限流。

## 服务管理

默认服务名为 `AgicoKbDatabase`、`AgicoKbApi`、`AgicoKbWorker`，分别使用独立的 `NT SERVICE\服务名` 虚拟账号，设为自动启动。数据库、API 和后台解析进程各自运行，应用连接数据库使用非超级用户角色。

```powershell
# 在代码目录、管理员 PowerShell 中运行；将 Status 换成 Start 或 Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "D:\企业知识库" -Action Status
```

安装错误详见数据目录 `logs/setup.log`、`logs/setup-errors.log`，服务日志在 `logs/database`、`logs/api`、`logs/worker`。日志也应视为内部运维资料。`-Action Uninstall` 停止并删除本安装的服务注册，保留数据与凭证；重新注册用 `-Action Install`。

修改 runtime.json 使用 **Stop → 修改 → Install**。Install 拒绝在 API/worker 运行时刷新配置，完成后两个服务配置与主配置一致。维护配置仅管理员读取，不写入服务配置。已安装实例重复运行 setup 只检查并启动，不替代配置同步步骤。

命名模型与视觉模型分开：运行配置中的 `AGICO_KB_NAMING_FALLBACK_MODELS` 可设为 `glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash`；DeepSeek 只用于文字命名备用，禁止图片请求。视觉凭证文件的模型列表只写两个 GLM，HY4 不再允许配置。

## 更新与备份

数据目录不会进入 Git，`git pull` 不会迁移或删除知识库。更新前先备份并阅读版本说明，停止 API 和 worker，更新代码，再按该版本要求同步依赖及迁移数据库，最后启动服务并检查就绪状态。当前安装脚本不是自动升级工具，不要在服务运行时重新同步 Python 环境。

备份必须同时覆盖数据库和原文件，并妥善保管配置及模型身份。不能把运行中的 `postgres` 目录随意复制当作可靠备份。使用现有[运维与恢复流程](validation/operations.md)；迁往新服务器应按恢复流程操作，而非改写安装归属文件。

依赖安装方式依据 [uv 官方文档](https://docs.astral.sh/uv/getting-started/installation/)、[micromamba 官方文档](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html)；服务包装器固定为 [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0)。实际版本和下载校验值保存在 `deploy/windows/bootstrap-lock.json` 与 `deploy/windows/postgres-explicit.txt`。
