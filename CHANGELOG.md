# 更新日志

## 2026-11-19
- **chore** `backend/_pkg/wechat_mcp_macos/` 首次纳入版本管理（`server.py` / `db.py` / `decryptor.py` / `key_extractor.py` / `sender.py` / `c_src`）。`db.py` 以线上部署副本为准同步，含 `_find_msg_tables` 分片修复（按 `create_time` 取最新分片，而不是按行数取最大分片）。
- **docs** 新增 `docs/remote-access-and-lifecycle.md`：为什么远程调用只能走 LAN sshd（TCC 只给 sshd 会话 Full Disk Access，Tailscale SSH 读微信容器报 `Operation not permitted`）、`wechat-mcp-remote` 的 LAN 地址解析顺序与缓存、stdio MCP「一个连接一个进程」模型、孤儿判定特征、守卫参数与排障速查表、端到端自检脚本。
- **feat** `scripts/wechat-mcp-remote` 纳入版本管理（原为 NAS 上的裸脚本）。README 架构图、项目结构同步更新，并新增「远程 MCP 接入与进程生命周期」章节。
- **fix** 新增 `liveness.py`（server 启动时 `liveness.install(mcp)`），解决 stdio MCP 进程堆积：通过 Tailscale SSH（`tailscaled be-child ssh-monitor` → `login -f`）启动的会话给进程 `stdin=/dev/null`，MCP 循环既收不到请求也收不到 EOF，进程会一直活到重启（2026-11-19 发现 7 个残留，最久 2.5 天）。三道守卫：①启动时采样 fd 0 是否为 `/dev/null`（必须在 `mcp.run()` 之前采样，FastMCP 4 启动后会把 `/dev/null` 挂到 fd 0，事后检测无法区分真假客户端）②父进程消失/启动时已是 `ppid==1` ③`WECHAT_MCP_IDLE_TIMEOUT`（默认 6h）无消息。全部可在 `WECHAT_MCP_GUARDS=0` 关闭。
- **fix** `wechat-mcp-remote` 重写：解析到的 Mac LAN IP 写入 `~/.cache/wechat-mcp-remote/`，之后用裸 TCP 探测（`/dev/tcp/<ip>/22`，不建 ssh 会话、不走 Tailscale）复用，仅在缓存失效时才回落到 shell hop；新增第 4 个参数作为 LAN IP 提示。原因：macOS 只有 LAN sshd 带 Full Disk Access，Tailscale SSH 读微信容器会 `Operation not permitted`；而旧版每次启动都做一次 Tailscale shell hop，既慢又会留下 tailscaled 会话。

## 2026-08-13
- **fix** 修复 `skill/scripts/check_env.sh` 自检时通过 `echo "$0" | bash` 把整个脚本再执行一遍来统计对勾的 bug，消除每次环境检查耗时翻倍（原 1~2 分钟）的问题。
- **perf** 新增 `scripts/summarize.sh` 一键包装，自动跳过 `check_env` 与运行前清理；`prepare` 步骤由 1~2 分钟降至约 4 秒。
- **docs** 明确总结流程保持纯 agent 执行：`prepare` → agent 读取 `prompt.txt` 写出 `summary.json` → `render`，不外包外部 LLM API。
