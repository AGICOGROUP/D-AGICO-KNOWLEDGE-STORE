# Windows 服务器安装

代码目录与知识库数据目录分开。首次安装只需指定 `DataRoot`，脚本自动安装固定版本的 Python、PostgreSQL/pgvector、应用依赖和本地模型，初始化数据库和受保护运行配置，并注册三个自动启动的 Windows 服务。

## 首次安装

要求：Windows x64、管理员 PowerShell 5.1、Git、可访问 GitHub/PyPI/conda-forge 和模型下载站点的网络，以及足够磁盘空间。服务包装器使用 Windows 的 .NET Framework 运行环境。代码放在英文路径；数据目录支持中文和空格，使用本机 NTFS 磁盘上的空目录，不能位于代码目录内。已有测试库不能直接当作全新安装目录。

Server 2012 R2 默认 PowerShell 4.0，需要先安装微软 WMF 5.1；安装器在写入数据前检查 PowerShell 版本。micromamba 还需要匹配的 C++ 运行库，安装器在安装 Python 和数据库前检查所用 DLL 的版本。旧运行库可使用受控补充包提供应用本地的 `msvcp140.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`，放在 `AppRoot\.runtime` 与 micromamba.exe 同目录；不得替换公司服务器的 System32 DLL。补充包须校验来源、SHA-256 和微软数字签名。上述步骤不代替目标服务器的实际运行验证。

若提供了这三份本地运行库，安装器在 Python 环境同步后还会自动复制到本安装的虚拟环境和底层 Python 解释器目录，校验副本并先导入 ONNX Runtime、FastEmbed 和 RapidOCR。仅为 micromamba 提供运行库不足以覆盖 Python。自动处理拒绝修改本安装 `.runtime` 之外的 Python，不改变全局运行库、系统 Python 或数据库与原件；安装中断后使用同一命令继续，已有数据库和凭证会复用。

在**管理员 PowerShell**执行：

API 密钥由开发人员在网页“API 配置”中手动录入。源码、Git 和新部署包只带接口、模型等非密钥配置，不携带 API 或钉钉密钥。每条线路仅保留一个当前密钥，使用 Fernet 认证加密保存；完整值只能输入，界面仅显示前三位和后四位，点击“更换”输入新值覆盖旧值，不保留历史版本。主线路标注 bigmodel.cn，备用线路标注 cephalon.cloud；支持模型与低中高三档强度统一保存，备用线路四个候选均允许图片与文字任务；只有主线路全部模型不可用后才临时切换备用线路 30 分钟，期满重试主线路首选。留空不修改。升级已有实例时先迁移当前密钥到服务器外置配置目录，保留原账号和资料。

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

官方 GLM 主线路和端脑云备用线路分别保存到 DataRoot\config\api-keys 的 primary.txt、fallback.txt。该目录位于项目和原件存储之外，使用 Fernet 认证加密文件，不做机器或系统账号绑定；服务自动生成的解密材料独立保存在 config\.api-key-encryption.key，并限制为 API、worker 读取和维护人员管理。无需另设密码。迁移已有密文时必须同时迁移该材料；缺失或损坏时拒绝解密，也不会静默生成替代材料。API 服务可替换密钥，worker 只读；网页只有开发人员可操作，接口仅返回配置状态、密钥脱敏标识、支持模型、首选与临时模型、推理强度及恢复时间，不返回完整密钥。模型故障状态与线路切换期跨 API 与 worker 进程保存；主线路按首选顺序尝试本线路模型，全部不可用后才启用备用线路。从切换时刻起统一保持备用线路 1800 秒，期间不因单个主线路模型提前结束冷却而回切；期满从主线路首选开始重试，仍全部失败则进入下一轮半小时备用期。备用模型自身失败不延长本轮线路切换期。全部暂不可用时保留进度并等待恢复，不做反复请求。新密钥保存后后续任务自动读取，无需重启。已有进行中的请求可使用提交前读取的值。连接测试验证当前已保存的线路与模型；更换密钥后需先保存再测试。公网部署使用 HTTPS，本机入口允许 HTTP。迁移服务器时既有账号和资料照常备份恢复，API 密钥可在新服务器网页重新输入，或随外置配置目录和解密材料一并迁移。单独密钥文件泄露时认证加密提供保护；不能据此假设泄露整个配置目录或服务器管理员权限仍安全。历史私有部署包可能仍含旧凭证，必须受控保管；新打包程序不再写入它们。

## 钉钉身份模式（可选，先在独立实例验收）

完整接入流程、PC/手机入口区别、免登协议和其他应用复用方法见 [钉钉 H5 接入复用指南](integrations/dingtalk-h5-integration.md)，详细设计见 [钉钉身份接入](design/dingtalk-identity.md)。不设置 `AGICO_KB_DINGTALK_CONFIG_FILE` 时保留现有邮箱模式；私有包包含钉钉凭证也不会自动切换正式实例。

1. 在开发机由受保护的 CLI 凭证导入生成 `.deployment-private/dingtalk.json`，不手抄密钥到命令行。使用 `scripts/dingtalk-setup.py validate --config PATH` 校验配置，`probe` 只读验证真实通讯录，不代表免登完成。凭证文件须仅管理员和 SYSTEM 可读。
2. 明确填写真实部门 ID 到知识库事业部 ID 的 `department_map`；未确认的映射留空。`maintenance_principals` 只填写现有、有效的开发人员 principal_id，不能填邮箱或钉钉 userId，更不能用新账号替换正式账号。运维账号在钉钉模式中保留本地登录；员工密码及邀请入口停用。
3. 先在独立测试数据库与目录验证。正式切换前取最新完整数据库和原件备份，停止 API/worker，加载维护身份执行迁移 007；它只增加钉钉绑定、挑战和会话认证来源，不改现有密码、身份或资料。使用现有运行角色时，为新增表授予与其余业务表相同的读写权限。
4. 全新安装可自动复制私有包中的钉钉配置至 `DataRoot/config/dingtalk.json`；已有安装不会覆盖目标映射。升级时由受控运维复制并校验该文件，在主 `runtime.json` 设置 `AGICO_KB_DINGTALK_CONFIG_FILE` 为这个目标绝对路径。配置必须与 runtime.json 在同一受保护配置目录，再执行 Stop → Install 同步服务配置和凭证 ACL。不要重新 bootstrap 已有实例。
5. 为企业内部网页应用配置客户端可达的测试 HTTPS 主页和 PC 主页、相应域名，并确认试用人员可见范围后发布。仅授权成员/部门读取还不等于员工可以使用应用；项目额外使用 `qyapi_get_microapp_list` 查询员工可用应用，核对 AgentId。企业应用密钥只在服务端；不申请通讯录写权限或微应用管理权限。
6. 在钉钉 PC/手机内分别确认真实免登、部门读取/审批边界、解绑/停用后的网页和 MCP 拒绝，以及原账号双重验证绑定。普通浏览器提示从钉钉进入；端外扫码登录不属于当前实现。健康接口或模拟测试不能代替真实客户端验收。
7. 搬往正式服务器时迁移切换前最新数据库和受保护配置，保持 CorpId/userId 与原账号绑定。调整 PUBLIC_URL、ALLOWED_HOSTS、钉钉主页/PC 主页和实际使用的域名配置，重建会话并让 AI 重新授权。不是仅修改一处网址；不可恢复测试库来替代正式账号库。

当前真实客户端验收状态见 [验证记录](validation/dingtalk-identity-20261019.md)。

## 服务管理

默认服务名为 `AgicoKbDatabase`、`AgicoKbApi`、`AgicoKbWorker`，分别使用独立的 `NT SERVICE\服务名` 虚拟账号，设为自动启动。数据库、API 和后台解析进程各自运行，应用连接数据库使用非超级用户角色。

```powershell
# 在代码目录、管理员 PowerShell 中运行；将 Status 换成 Start 或 Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "D:\企业知识库" -Action Status
```

安装错误详见数据目录 `logs/setup.log`、`logs/setup-errors.log`，服务日志在 `logs/database`、`logs/api`、`logs/worker`。日志也应视为内部运维资料。`-Action Uninstall` 停止并删除本安装的服务注册，保留数据与凭证；重新注册用 `-Action Install`。

修改 runtime.json 使用 **Stop → 修改 → Install**。Install 拒绝在 API/worker 运行时刷新配置，完成后两个服务配置与主配置一致。维护配置仅管理员读取，不写入服务配置。已安装实例重复运行 setup 只检查并启动，不替代配置同步步骤。

开发人员在同一页配置主线路和备用线路。主线路支持 `glm-5.3-flashx`、`glm-5.3-flash`；备用线路支持这两个 GLM 及 `deepseek-v4.1-flash-1017`、`deepseek-v4.1-flash`，四个候选均允许图片与文字任务；仅主线路全部模型不可用时使用备用线路，切换期统一为半小时。旧版 `deepseek-v4-flash`、HY4 不再作为可选视觉模型。模型是否能稳定识别需以当前平台实际图片响应验证，目录存在或 HTTP 200 不能代替内容验收。

## 更新与备份

### 独立 HTTPS 入口

`scripts/prepare-https-certificate.py` 校验证书包的 SAN 域名、有效期与私钥匹配关系，向已安装实例的 `DataRoot/config/tls` 导入规范化文件；该目录已存在时拒绝覆盖。私钥仅管理员和 SYSTEM 可读，HTTPS 服务安装后额外授予该服务读取权限。证书包和私钥不能进入源码或 Git。

离线 HTTPS 安装包提供固定版本的官方 Nginx Windows 程序，来源及 SHA-256 记录在 `deploy/windows/https-lock.json`。在管理员 PowerShell 中执行 `scripts/configure-server-https.ps1 -AppRoot D:\AGICO-KNOWLEDGE-STORE -DataRoot D:\AGICO-KB-DATA -Hostname zsk.agicogroup.com -Port 8888`。脚本核对实例归属、程序哈希、端口占用及证书，注册独立虚拟账号服务并添加专用 TCP 防火墙规则。程序在应用目录，证书、配置和日志均在数据目录；不会启动 IIS 或修改其他站点。

首次安装固定使用维护模式：`/_agico/https-ready` 返回 HTTPS 就绪，其他地址返回部署中（HTTP 503），不反代知识库 API。必须完成服务器钉钉身份配置、数据库与资料迁移和权限验收后，另行切换业务入口。HTTPS 就绪不代表知识库正式上线。安装验证保持域名和证书检查，不使用跳过证书校验的浏览器设置。服务停止/状态检查可使用同一脚本的 `-Action Stop` / `-Action Status`。已归属实例重复 Install 保留配置；证书更新及业务入口切换需专门流程，不能删除证书目录后重装。

Nginx 官方将 Windows 构建标为 beta，采用单个实际工作进程；当前封装只解决这台旧服务器的 HTTPS 入口。已在本机实测真实 TLS、虚拟账号服务启动/重启、私钥 ACL 和维护路由隔离；Windows Server 2012 R2 的实际兼容性和外网证书链仍以目标服务器验收为准。参考 [Nginx Windows 官方说明](https://nginx.org/en/docs/windows.html)。

### 从既有钉钉实例导入公司服务器

`scripts/import-company-data.py` 只接受已经验证的逻辑备份和精确文件清单。`scripts/import-server-migration.ps1` 是部署模板，交付前必须用实际私有迁移包 SHA-256 替换校验占位值；不得对未知来源 SQL 备份运行。私有包含真实资料、钉钉凭证、加密的 API 密钥及其解密材料，只保存在受保护的运行数据或私有交付目录，不能进入源码、Git 或普通报告。

导入前核对已安装实例归属、维护模式、模型指纹和目标空库；目标已有账号、资料、API 密钥或钉钉配置时拒绝覆盖。恢复使用新的数据库和暂存原件目录，逐表核对身份、部门成员关系、钉钉绑定、资料和上传记录指纹，并在服务器上只读核验现有员工仍可使用该应用。指纹使用 UTC 规范化时间，避免服务器时区不同造成误报。

校验通过后保留原空数据库和原配置副本，切换原件目录与运行配置，保留目标服务器自己的数据库连接凭证，迁入原有部门映射、维护身份、模型顺序和推理强度。复用现有服务注册流程同步配置及 ACL，再确认本机认证模式为 `dingtalk`；公网入口继续保持维护模式，真实客户端验收及应用发布另行完成。导入不创建新管理员，不执行账号 bootstrap，不将历史邮箱登录方式重新启用。

隔离演练已覆盖真实实例的逻辑恢复、七张业务表指纹、原件校验、API 密钥解密、主备模型/强度保留、匿名资料读取拒绝及重复导入保护。该结果不代替公司服务器实际导入验收。失败时保留新库、暂存目录和迁移日志供核查，不自动删除数据库或覆盖现网；若存在部分文件切换，禁止删除目录后盲目重试。

首次安装若无法访问模型下载站，可将经过校验的离线包解压至数据目录，使 `models/agico-offline-models.json` 与模型文件一同存在。初始化器逐文件核验 SHA-256 和模型名称，成功后使用本地模式加载文字向量与 OCR 模型；文件缺失或校验失败会停止，不会静默改走网络。离线模型包不包含账号、业务原件或 API 密钥。

数据目录不会进入 Git，`git pull` 不会迁移或删除知识库。更新前先备份并阅读版本说明，停止 API 和 worker，更新代码，再按该版本要求同步依赖及迁移数据库，最后启动服务并检查就绪状态。当前安装脚本不是自动升级工具，不要在服务运行时重新同步 Python 环境。

备份必须同时覆盖数据库和原文件，并妥善保管配置及模型身份。不能把运行中的 `postgres` 目录随意复制当作可靠备份。使用现有[运维与恢复流程](validation/operations.md)；迁往新服务器应按恢复流程操作，而非改写安装归属文件。

依赖安装方式依据 [uv 官方文档](https://docs.astral.sh/uv/getting-started/installation/)、[micromamba 官方文档](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html)；服务包装器固定为 [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0)。实际版本和下载校验值保存在 `deploy/windows/bootstrap-lock.json` 与 `deploy/windows/postgres-explicit.txt`。
