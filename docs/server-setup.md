# Windows 服务器安装

代码目录与知识库数据目录分开。首次安装只需指定 `DataRoot`，脚本自动安装固定版本的 Python、PostgreSQL/pgvector、应用依赖和本地模型，初始化数据库和访问凭证，并注册三个自动启动的 Windows 服务。

## 首次安装

要求：Windows x64、管理员 PowerShell 5.1、Git、可访问 GitHub/PyPI/conda-forge 和模型下载站点的网络，以及足够磁盘空间。服务包装器使用 Windows 的 .NET Framework 运行环境。代码放在英文路径；数据目录支持中文和空格，使用本机 NTFS 磁盘上的空目录，不能位于代码目录内。已有测试库不能直接当作全新安装目录。

在**管理员 PowerShell**执行：

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

安装结束后，管理员从 `config/initial-access.json` 读取初始化管理员的 MCP 地址、令牌和到期时间。令牌有效期为 30 天，仅用于初始化验证；后续按员工或 Agent 身份分配权限与令牌，不能把它当成全公司共用账号。不要将配置内容粘贴到日志、聊天或 Git。

浏览器访问 `/health/ready` 用于检查就绪状态；直接打开 `/mcp` 出现 `UNAUTHENTICATED` 表示没有携带凭证，并非安装失败。MCP 客户端需要配置 Bearer token，并使用 MCP 协议调用。

API 默认只允许本机访问。让其他电脑、现有网页或钉钉适配服务连接时，需要另行配置可信域名、HTTPS 反向代理与防火墙，以及调用者身份。仅选择一个文件夹无法推断这些网络和身份信息；知识库本身不重复建设员工聊天入口。

## 服务管理

默认服务名为 `AgicoKbDatabase`、`AgicoKbApi`、`AgicoKbWorker`，分别使用独立的 `NT SERVICE\服务名` 虚拟账号，设为自动启动。数据库、API 和后台解析进程各自运行，应用连接数据库使用非超级用户角色。

```powershell
# 在代码目录、管理员 PowerShell 中运行；将 Status 换成 Start 或 Stop
.\scripts\configure-server-services.ps1 -AppRoot $PWD.Path -DataRoot "D:\企业知识库" -Action Status
```

安装错误详见数据目录 `logs/setup.log`、`logs/setup-errors.log`，服务日志在 `logs/database`、`logs/api`、`logs/worker`。日志也应视为内部运维资料。`-Action Uninstall` 停止并删除本安装的服务注册，保留数据与凭证；重新注册用 `-Action Install`。

## 更新与备份

数据目录不会进入 Git，`git pull` 不会迁移或删除知识库。更新前先备份并阅读版本说明，停止 API 和 worker，更新代码，再按该版本要求同步依赖及迁移数据库，最后启动服务并检查就绪状态。当前安装脚本不是自动升级工具，不要在服务运行时重新同步 Python 环境。

备份必须同时覆盖数据库和原文件，并妥善保管配置及模型身份。不能把运行中的 `postgres` 目录随意复制当作可靠备份。使用现有[运维与恢复流程](validation/operations.md)；迁往新服务器应按恢复流程操作，而非改写安装归属文件。

依赖安装方式依据 [uv 官方文档](https://docs.astral.sh/uv/getting-started/installation/)、[micromamba 官方文档](https://mamba.readthedocs.io/en/stable/installation/micromamba-installation.html)；服务包装器固定为 [WinSW 2.12](https://github.com/winsw/winsw/tree/v2.12.0)。实际版本和下载校验值保存在 `deploy/windows/bootstrap-lock.json` 与 `deploy/windows/postgres-explicit.txt`。
