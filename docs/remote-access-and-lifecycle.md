# 远程 MCP 接入与进程生命周期

这份文档回答三件事：**能不能从别的机器调**、**为什么必须走 LAN**、**为什么机器上会堆进程**。

## TL;DR

| 事项 | 结论 |
|---|---|
| 远程调用通道 | 只能走 **LAN sshd**（Tailscale SSH 拿不到 Full Disk Access） |
| 桥接脚本 | `scripts/wechat-mcp-remote`：缓存 LAN IP + 裸 TCP 探测，只有缓存失效才开 shell hop |
| 进程模型 | **一个客户端连接 = 一个 `python -m wechat_mcp_macos`**，没有共享守护进程 |
| 退出机制 | `backend/_pkg/wechat_mcp_macos/liveness.py` 的三道守卫，见第 5 节 |

---

## 1. 为什么必须走 LAN sshd

macOS 的 TCC 把微信容器（`~/Library/Containers/com.tencent.xinWeChat/.../db_storage`）保护起来了。要通过程序读它，进程得持有 **完全磁盘访问权限（Full Disk Access）**。

- **普通 sshd 会话有**：这台机器上给 `sshd`/`sshd-keygen-wrapper` 授过 FDA，所以 `ssh user@<LAN-IP>` 拉起的进程能读。
- **Tailscale SSH 没有**：它是 `tailscaled`（root）自己 fork 出 `be-child ssh-monitor`，再 `setuid` 到目标用户 exec，**完全不经过 sshd 的权限上下文**，所以读到的是 `EPERM`。

症状（能连上，但一读库就炸）：

```
PermissionError: [Errno 1] Operation not permitted:
/Users/<user>/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<id>/db_storage/session/session.db
```

快速判定（在远程机器上执行，不要在那台 Mac 本地跑）：

```bash
# LAN -> 有权限，能列目录
ssh <user>@192.168.3.38 'ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage/session/ | head'

# 同一个命令走 Tailscale IP -> Operation not permitted
ssh <user>@100.x.y.z   'ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage/session/ | head'
```

> 结论：**"能 ssh 上" ≠ "能读数据"。** MCP 桥接必须落在 LAN sshd 上。

---

## 2. 桥接脚本 `wechat-mcp-remote`

```bash
usage: wechat-mcp-remote <ssh-shell-host> <remote-user> <remote-binary> [lan-ip-hint ...]
```

| 参数 | 说明 |
|---|---|
| `ssh-shell-host` | 用来查询 Mac 当前 LAN 地址的 ssh 目标（缓存 miss 时才用） |
| `remote-user` | 远端用户名 |
| `remote-binary` | 远端 MCP 可执行文件绝对路径 |
| `lan-ip-hint ...` | 已知的 LAN 地址，按顺序尝试（避免每次靠 DHCP 猜） |

LAN 地址解析顺序：

1. 环境变量 `WECHAT_LAN_IP`（人工钉死，最高优先级）
2. 缓存文件 `~/.cache/wechat-mcp-remote/<user>@<host>`
3. 命令行 `lan-ip-hint`
4. 兜底：`ssh <shell-host> "ipconfig getifaddr en0..en5"` —— **只在全部失效时发生**

每一步都先用**裸 TCP 探测**（`exec 3<>/dev/tcp/<ip>/22`）验证可达：不开 ssh 握手、不建会话、不消耗 Tailscale。

其它行为：

- 成功后把地址写回缓存；DHCP 换 IP 时探测失败会自动回落。
- 正式 exec 时**不加 `ssh -n`**：stdin 必须留给 MCP 客户端（加 `-n` 就是把它变成 `/dev/null` 的经典 bug）。
- 带 `ServerAliveInterval=30`，客户端死了连接会被主动探测掉。

安装（以 NAS 为例）：

```bash
cp scripts/wechat-mcp-remote ~/.local/bin/wechat-mcp-remote
chmod 755 ~/.local/bin/wechat-mcp-remote
```

---

## 3. MCP 配置示例

Hermes（`~/.hermes/config.yaml`）：

```yaml
  wechat-mengyu:
    command: /home/<you>/.local/bin/wechat-mcp-remote
    args:
      - mengyus-mac-mini-cli          # ssh 别名，指向 LAN
      - mengyulu
      - /Users/mengyulu/wechat-mcp-macos/backend/.venv/bin/wechat-mcp-macos
      - 192.168.3.38                  # LAN hint
    timeout: 180
    enabled: true
```

Claude Code / 其它 stdio MCP 客户端同理：`command` 指到桥接脚本，`args` 按上面的顺序给。

对应的 ssh 别名（`~/.ssh/config`）建议直接指 LAN，并保留一个 `-ts` 备用：

```
Host mengyus-mac-mini-cli
    HostName 192.168.3.38
    User mengyulu
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    HostKeyAlias mengyus-mac-mini
    StrictHostKeyChecking accept-new
```

---

## 4. 进程模型：一个连接一个进程

MCP stdio 传输是**点对点管道**，没有共享守护进程，也不能复用已有实例：

```
客户端连接  ->  ssh（LAN）  ->  python -m wechat_mcp_macos   （本次会话独占）
客户端断开  ->  进程应该退出
```

所以机器上出现多个同名进程，**不是"多副本"**，而是**过去多次连接各留了一个**。判断孤儿（已经没有任何客户端）：

| 特征 | 值 |
|---|---|
| fd 0 | `/dev/null`（stdin 被 `-n` 或 `login -f` 置空） |
| RSS / CPU | ~4 MB、累计 CPU 0.4s（一次请求都没处理过） |
| 命令行 | 完全一致，只有 `etime` 不同 |
| 父进程 | `sshd-session` 或 `tailscaled be-child ssh-monitor` 的残留 |

历史事故：2026-11-19 在 Mac mini 上发现 **7 个残留**，最久的跑了 **2.5 天**，加起来还挂着 6 个已死的 Tailscale 会话。根因就是"不退出"。

清理旧孤儿（只杀得到自己 uid 的）：

```bash
pgrep -fl "python -m wechat_mcp_macos"
pkill -TERM -f "python -m wechat_mcp_macos"
```

外层 `tailscaled be-child ssh-monitor` 属于 root，杀不掉也不需要杀 —— tailscaled 发现会话失效后会自行回收。

---

## 5. liveness 守卫

`backend/_pkg/wechat_mcp_macos/liveness.py`，在 `main()` 里 `liveness.install(mcp)` 后 `mcp.run()`。

| # | 守卫 | 触发条件 | 对应事故 |
|---|---|---|---|
| 1 | stdin 空挂 | 启动时 fd 0 就是 `/dev/null` | 客户端根本连不上，进程却活着 |
| 2 | 父进程消失 | 运行中被 reparent 到 launchd，**或**启动时已经 `ppid == 1` | 会话先死、进程才起来 |
| 3 | 空闲超时 | `WECHAT_MCP_IDLE_TIMEOUT`（默认 6h）内无任何 MCP 消息 | 客户端假死/半开连接 |

**关键坑（踩过）**：FastMCP 4 的 stdio transport 在 `mcp.run()` 之后会把 `/dev/null` 挂到 fd 0（真实客户端流放在复制出的描述符上）。所以守卫 1 **必须在 `mcp.run()` 之前采样**，否则会把所有正常会话误杀。

环境变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `WECHAT_MCP_GUARDS` | `1` | `0` 关闭全部守卫 |
| `WECHAT_MCP_PARENT_GUARD` | `1` | `0` 只关父进程守卫（例如将来改成 LaunchAgent 常驻） |
| `WECHAT_MCP_GUARD_GRACE` | `8` | 启动后多少秒开始生效 |
| `WECHAT_MCP_GUARD_INTERVAL` | `15` | 巡检间隔 |
| `WECHAT_MCP_IDLE_TIMEOUT` | `21600` | 空闲多少秒退出，`0` = 永不 |

自检（应该在 25 秒内结束并返回 0）：

```bash
cd ~/wechat-mcp-macos
WECHAT_MCP_GUARD_GRACE=1 WECHAT_MCP_GUARD_INTERVAL=1 \
  backend/.venv/bin/wechat-mcp-macos < /dev/null ; echo "rc=$?"
```

---

## 6. 排障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| `PermissionError: Operation not permitted` | 走了 Tailscale SSH | 换 LAN 地址 / 用桥接脚本 |
| `initialize` 超时、无响应 | LAN IP 变了或 Mac 睡眠 | 手动连一次确认；清 `~/.cache/wechat-mcp-remote/` |
| 同名 Python 进程越堆越多 | 客户端重连留下的孤儿（旧版） | 新版会自动退出；存量用第 4 节的 `pkill` |
| 进程秒退（rc=0） | stdin 是 `/dev/null` 或 `ppid==1` —— 说明连接没真正建立 | 先查 ssh 能不能通；调试时 `WECHAT_MCP_GUARDS=0` |
| 残留 root 的 `be-child ssh-monitor` | tailscaled 尚未回收死会话 | 等它回收；或 `sudo pkill -f "be-child ssh-monitor"` |
| 中文/emoji 乱码 | 终端编码 | 与 MCP 无关，查客户端 locale |

---

## 7. 端到端自检

从远程机器起一个真实 MCP 会话，确认"能连 + 能读 + 断开即退"：

```python
import json, subprocess, time
W, BIN = "/path/to/wechat-mcp-remote", "/Users/<user>/wechat-mcp-macos/backend/.venv/bin/wechat-mcp-macos"
p = subprocess.Popen([W, "<shell-host>", "<user>", BIN, "192.168.3.38"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
send = lambda o: (p.stdin.write((json.dumps(o) + "\n").encode()), p.stdin.flush())
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                 "clientInfo": {"name": "e2e", "version": "1"}}})
print(json.loads(p.stdout.readline())["result"]["serverInfo"])   # 期望 ~0.5-1s
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {"name": "list_chats", "arguments": {"limit": 3}}})
print(json.loads(p.stdout.readline())["result"]["content"][0]["text"][:120])  # 期望真实会话
p.stdin.close()
print("rc:", p.wait(timeout=40))   # 期望 0，且远端 pgrep 无残留
```

参照值（2026-11-19 实测，NAS -> Mac mini）：`initialize` 0.5–0.9s，8 个工具，`list_chats` 0.1s，断开后 0.2s 退出。
