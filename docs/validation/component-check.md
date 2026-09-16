# 步骤 0：开发环境组件验证

日期：2026-09-15。本机是开发环境，未认定为公司正式服务器；未导入公司业务文件。

| 项目 | 实测结果 |
|---|---|
| Windows | Windows 10 Pro 10.0.19045，64 位，16 逻辑处理器，约 32 GB 内存 |
| Python | uv 创建项目专属 Python 3.12.14 环境；依赖固定在 uv.lock |
| 数据库 | PostgreSQL 16.15 原生 Windows 运行，127.0.0.1:15432，开发目录 .local，非系统服务 |
| 向量扩展 | pgvector 0.8.6；实际 CREATE EXTENSION、向量余弦距离 SQL 通过 |
| 原生文字路线 | 每次新建合成 DOCX，通过 python-docx 提取 3 段，中文、型号、单位逐段一致 |
| 本地向量路线 | FastEmbed/ONNX，BAAI/bge-small-zh-v1.5，512 维；中文温度问题正确命中含 AX-210、80 摄氏度的段落，余弦相似度约 0.756 |
| Codex | 临时 Streamable HTTP MCP＋环境变量 Bearer；真实客户端发现并调用只读测试工具，返回 AGICO-PROBE-OK、synthetic=true |
| Accio | 名称由用户确认，实际版本和接入环境尚未提供；步骤 5／6 实测 |

MCP 首次探针缺少只读注解，客户端拒绝调用。为实际只读工具补充准确的 readOnlyHint 等注解后重试成功，未改变审批策略、全局 MCP 配置或权限。

## 路线与边界

当前未发现可直接接入的现有完整知识平台，采用计划中的 Python＋PostgreSQL 组件组合。未排除公司其他环境可能已有平台，也没有额外建设另一套 RAG 平台。

轻量 DOCX／中文向量探针用于确认原生 Windows 最小路线可行，不代表 Docling、OCR、复杂表格和 BGE-M3 已通过。完整解析与模型取舍在步骤 3 用代表性样本决定。没有进行 50／200 并发容量验收。

数据库运行包由官方 micromamba 从 conda-forge 获取 PostgreSQL 16／pgvector Windows 包；原生隔离安装，不使用 Docker 或 WSL。正式服务器的系统、磁盘、服务账号、TLS、备份位置仍需部署时核实。

可复现入口：`scripts/probe_components.py`、`scripts/probe_mcp.py`、`scripts/prepare_dev_db.py`。本地结果保存在被 Git 忽略的 `.local/probe/`，凭证不纳入仓库。

来源：[pgvector Windows／Conda 支持](https://github.com/pgvector/pgvector)、[micromamba 安装](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)。
