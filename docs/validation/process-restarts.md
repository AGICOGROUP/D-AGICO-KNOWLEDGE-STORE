# 本地进程重启与 32 MiB 文件完整性验证

## 运行

在开发数据库 `127.0.0.1:15432/agico_test` 可用、`.local/pg-password.txt` 已配置且 Python 环境包含 `psutil` 时，从仓库根目录执行：

```powershell
$env:AGICO_KB_RUN_PROCESS_RESTARTS = '1'
try {
    .venv\Scripts\python.exe -m pytest tests/integration/test_process_restarts.py -q -s --tb=short
    $restartTestExit = $LASTEXITCODE
} finally {
    Remove-Item Env:AGICO_KB_RUN_PROCESS_RESTARTS -ErrorAction SilentlyContinue
}
if ($restartTestExit -ne 0) { throw '进程重启验证未通过' }
```

默认完整测试会跳过本项；显式启用后每次创建新的隔离 schema、随机身份和令牌，并生成全新随机二进制文件。数据库密码只进入子进程环境，不出现在命令行或结果中。

## 验证范围

- 使用真实 HTTP 和现有 `FileTransfer`，以 1 MiB 块生成、上传、下载 32 MiB `.bin` 文件；校验大小和 SHA-256，不使用 base64，不将文件内容送入模型。
- 发布为 `file_only`，实际终止 uvicorn 后在相同端口重启；检查正式版本仍可见、重复发布被拒绝。
- worker 子进程通过真实 `Worker.claim()` 取得工作和租约后，在解析子进程创建前由测试暂停；通过原子标记确认实际 worker PID 没有解析器后代，实际终止整个自有进程树。Windows venv 启动器本身可有 Python 和 conhost 后代。
- 只将本次隔离 schema 中该任务的租约缩短到未来 1 秒，等待自然过期，再启动未修改的 `python -m agico_kb.worker`。不重置任务状态、generation 或 attempts，不更改生产逻辑。
- 检查替代 worker 以不同 generation 和第二次 attempts 完成 `stored_only` 处理；正式版本、文档 revision 和发布审计均不重复；跨事业部无权限身份在重启前后均无法下载；文件哈希保持一致。

生产租约由 `max(60, parse_timeout_seconds + 300)` 决定，默认 420 秒。本测试显式缩短租约，因此验证的是进程故障后的正常过期回收路径，不代表实际生产恢复只需 1 秒，也不覆盖解析器执行到一半的崩溃或模型初始化恢复。

所有 Windows 子进程设置 `CREATE_NO_WINDOW`。清理只操作记录的 PID/创建时间及其后代，逐层暂停每个进程再枚举子进程，随后按倒序终止进程树，每次等待最多 10 秒；只删除经名称和数据库验证的本次 schema。文件由 pytest 临时目录管理，不递归删除工作区目录。

## 执行记录

2026-09-16，在同机 MCP 负载和其他数据库测试结束后单独执行，父任务最终同时启用进程重启和数据库重启两项演练，结果：**2 passed in 11.78s**；本文件场景内部耗时 **4.532s**。

| 项目 | 实测结果 |
| --- | --- |
| 新文件大小 | 33,554,432 字节（32 MiB） |
| 上传及两次下载 SHA-256 | `d8e2e4926817a92291548dbd1b79f3bc1354656c4caa12773a9a50ebba6fcb99` |
| uvicorn 启动器 PID | 25332 → 2568 |
| worker 启动器 PID | 7792 → 10520 |
| 四个自有进程退出码 | 15，15，15，15（故意强制终止） |
| 首次 claim 原租约剩余 | 419.918706 秒 |
| 测试故障注入租约 | 改为未来 1 秒，然后等待自然过期 |
| 恢复后任务 | complete，attempts=2，generation 已变更 |
| 正式版本 | 单一版本，revision=1，单次发布审计，file_only |
| 权限 | 跨事业部身份两次下载均返回 404 |
| 最终处理能力 | stored_only；file=true，text=false，vector=false |

首轮暴露的是测试对 Windows venv 启动器后代的错误假设，修正为识别实际 worker PID 后，用新 schema、新身份和新随机文件重新完整执行通过。没有修改生产代码。pytest 的现有 Starlette/AnyIO 弃用提示仍存在。
