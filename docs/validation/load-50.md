# 50 身份 REST／MCP 本地验证

## 当前结论（2026-09-16）

向量忙时立即降级的问题已改为有界 FIFO 等待：保留一个模型和执行线程，最多容纳 64 个活动／等待请求，等待与推理共用 5 秒预算；超时的本机推理结束前不释放执行槽。没有增加缓存、模型服务或部署组件。

两轮均重新生成、上传、解析及发布 104 份合成资料，使用 50 个独立身份，两个事业部按权检索。每个调用者同一时刻只有一个请求，搜索后读取对应来源。并发新增资料不回写到已冻结快照。以下是短测结果，**不是正式 30 分钟容量或真实业务验收**。

| 指标 | REST 后端 | 官方 MCP SDK 客户端 |
|---|---:|---:|
| 独立身份／客户端 | 50 | 50，全部初始化 |
| 测量时间 | 74.947 秒 | 75 秒 |
| 完整搜索／读取周期 | 11,714 | 7,347 |
| 正确周期 | 11,714（100%） | 7,347（100%） |
| 向量降级 | 0 | 0 |
| 平均请求／工具调用每秒 | 313.660 | 195.920 |
| p50／p95／p99 | 140／240／270 ms | 235／360／422 ms |
| 预期权限拒绝（完整周期） | 1,951 | 1,228 |

REST 平均吞吐包含统计边界内的 23,508 个原始请求，完整周期为 23,428 个请求。MCP 只统计完全落入测量窗口的 14,694 次工具调用，含协议、鉴权和后端处理，不包含 Agent 推理或回答生成。MCP 客户端初始化在预热前完成；预热均为 15 秒，两类数据口径不同，不据此计算精确协议损耗。权限拒绝在 REST 是 404，在 MCP 是 is_error 的工具结果，不混称 HTTP 404。

## 可核查证据

- REST：[冻结配置](load-50-frozen-profile.json)、[完整汇总](load-50-probe.json)、[质量计数](load-50-quality.json)、[Locust 数据](load-50-stats.csv)。run `load_35831b4a774146fdabe70bbca521b1f9`。
- MCP：[冻结配置](load-mcp-50-frozen-profile.json)、[完整汇总](load-mcp-50-probe.json)。run `load_b8b57e36bca54647a03c1caf6908b097`。
- [修复前数据](load-50-before-wait.json)：12,382 周期、93.216% 正确、28.323% 降级。保留作为历史证据，不当作当前行为。
- Windows 开发机 i5-14400F／16 逻辑处理器／约 31.8 GiB 内存；PostgreSQL 16.15＋pgvector 0.8.6、单 Uvicorn 进程、池上限 8、本地 MiniLM。模型指纹、语料数量、资源峰值、撤下及并发导入观察见各自汇总。

未导出数据库池等待者或向量队列深度，报告不虚构这两个指标。PostgreSQL wait event 包括空闲 ClientRead，不当成积压。服务器不生成回答，因此没有服务端回答 token 或生成延迟。

MCP 驱动开发时先修正了 SDK 2.2 的 Client 构造方式；首次完整测量误把 SDK 加前缀的预期拒绝当作失败。按 SDK 源码和新增回归修正测试解析器后，以全新文件／身份重新跑出上表结果，没有放宽后台权限。早期合成判据问题记录保留在 [初次探测](load-50-initial-probe.json)。

## 重现开发短测

```powershell
uv sync --locked --group load
uv run python scripts/run_load_probe.py --users 50 --spawn-rate 5 --warmup 15 --duration 75
uv run python scripts/run_load_probe.py --transport mcp --users 50 --spawn-rate 5 --warmup 15 --duration 75
```

运行器仅连接本地 agico_test，凭据通过受限临时文件／子进程环境传入；负载客户端不继承数据库凭据。结束清理自己的 schema、文件和身份，保留无密钥结果。数据库清理有限超时；失败时独立删除凭据并写 cleanup-required.json，仍有自有活动时保留运行目录／schema，避免清理竞态。

归档 CSV 仅统一了 Windows 导出的重复换行，未更改数值；它是 Locust 周期性写出的末次快照，结束统计以汇总 JSON 为准。

## 正式阈值判定

准备实际语料、授权测试身份、业务任务和负责人确认，填写并冻结 `tests/load/acceptance-profile.json`。延迟阈值用毫秒，成功／正确／降级率用 0–1。加载阶段检查配置、凭据唯一性和用例引用；正式 REST 判定检查统计时长、实际启动和测量期间最小存活人数、用例覆盖、延迟及正确率／降级率。权限拒绝和禁止来源用例独立要求全部通过，不能被总体正确率稀释。HTTP 成功率口径包括业务预期的拒绝响应，不把它们计作传输故障。未填阈值／业务确认即 blocked；不达标即 failed 并返回非零退出码。

真实 Locust 2 身份功能探针验证人数计数为 started=2/minimum_live=2，633 请求零失败；开发配置仍明确 blocked。它只验证新验收计数路径，不作为容量数据。

```powershell
$env:AGICO_KB_LOAD_PROFILE = 'D:\ApprovedTest\frozen-profile.json'
$env:AGICO_KB_LOAD_CREDENTIALS = 'D:\ProtectedTest\credentials.json'
$env:AGICO_KB_LOAD_PROFILE_SHA256 = (Get-FileHash -LiteralPath $env:AGICO_KB_LOAD_PROFILE -Algorithm SHA256).Hash.ToLowerInvariant()
$env:AGICO_KB_LOAD_METRICS_PATH = 'D:\ApprovedTest\quality.json'
uv run --group load locust -f tests/load/locustfile.py --headless --users 50 --spawn-rate 5 --run-time 32m --host $env:AGICO_KB_TEST_URL --csv .local/formal-50
```

正式配置的地址、人数、爬升、2 分钟预热和 30 分钟测量必须与命令一致。REST 子门通过也不等于真实 Agent／员工入口／生产总体验收。公司资料、业务 SLO、实际 Accio／网页／钉钉和目标服务器尚未提供，正式验收保持未完成。
