# WeChat MCP macOS

macOS 微信本地数据读取。解密本地数据库，读取聊天记录、搜索、语音转文字、生成群聊总结。

**零 API 调用，纯本地运行，不修改原始数据库。**

## 架构

```
plugin/ + skill/                  ← Hana Agent 集成层
    ↓ 对话触发
prompts/render.py                 ← 匹配 chat → 取数 → 压缩上下文 → 生成给 LLM 的 JSON prompt
    ├── registry.json             ← 按群/联系人配置不同 prompt
    ├── templates/                ← LLM 模板文件
    └── summaries/                ← 生成的长图与中间产物
    ↓
scripts/chat-summary-workflow.sh  ← prepare / render 一键流水线
    ├── enrich_summary_json.py    ← 注入统计、热度、活跃群友、关键词 tag
    └── validate_summary_json.py  ← 渲染前自检
    ↓
backend/_pkg/wechat_mcp_macos/    ← FastMCP stdio 服务源码（部署副本在 backend/.venv/site-packages/）
    ├── server.py                 ← 8 个 MCP 工具（读消息 / 搜索 / 发送 / 状态）
    ├── db.py                     ← 解密库查询；按 create_time 选 message 分片，不按行数
    ├── liveness.py               ← 孤儿守卫（见「远程 MCP 接入与进程生命周期」）
    └── key_extractor.py          ← 数据库密钥提取
backend/.venv/                    ← Python 运行时（wechat_mcp_macos 包）
    ├── pipeline.py               ← 纯规则总结 + 联系人解析
    ├── summary_img.py            ← 长图渲染
    └── voice_to_text.py          ← 语音转文字
scripts/wechat-mcp-remote         ← 远程 MCP 桥接（强制 LAN sshd，见 docs/）
cleanup.py                       ← 中间数据清理（时间+容量双阈值）
cleanup-policy.json              ← 清理策略配置

contacts.json                    ← 从 contact.db 解密的通讯录（6000+ 条）
group_nicknames.json             ← 群成员补充昵称映射（fallback）
```

## 功能

| 能力 | 入口 | 原理 |
|------|------|------|
| 读取聊天记录 | `wechat_read` | 解密 SQLCipher 4 本地数据库直接查询 |
| 关键词搜索 | `wechat_search` | 跨群全文搜索，按发送者/时间分组 |
| 群聊列表 | `wechat_groups` | 列出所有微信群名称 |
| 纯规则总结 | `pipeline.py` | 零 LLM：活跃度/话题/时间线/热词 |
| 语音转文字 | `voice_to_text.py` | SILK 解码 + faster-whisper |
| 结构化管理 Prompt | `prompts/render.py` | registry 匹配 chat → 压缩上下文 → 生成给 LLM 的 JSON prompt |
| 日期区间查询 | `scripts/chat_query.py` | 群聊/私聊、任意日期区间、双方身份和头像 wxid |
| 总结流水线 | `scripts/chat-summary-workflow.sh` | prepare / render 一键串联取数、enrich、自检、出图 |
| 长图渲染 | `summary_img.py` | AI 总结 JSON + 统计增强数据 → Pillow 渲染为图片 |

## 快速开始

### 1. 克隆并创建环境

```bash
git clone https://github.com/yancongya/wechat-mcp-macos.git
cd wechat-mcp-macos

# 创建 Python 运行时环境
python3 -m venv backend/.venv

# 安装核心 Python 包
backend/.venv/bin/pip install wechat-mcp-macos

# 安装项目依赖
backend/.venv/bin/pip install -r requirements.txt

# 长图生成需要 Pillow
pip3 install Pillow --break-system-packages
```

### 2. 提取密钥

```bash
# 确保微信已登录，需要 sudo 权限
sudo backend/.venv/bin/python init-keys.py
```

### 3. 生成总结

默认日报口径，日界线 04:00：
- `--today`：本逻辑日 04:00 至现在
- `--yesterday`：上一个逻辑日 04:00 至本逻辑日 04:00
- `--start/--end`：任意日期区间，结束日期包含整天
- LLM 读取**压缩上下文**，不再直接吞全量原始聊天

```bash
cd wechat-mcp-macos

# 日期区间查询
backend/.venv/bin/python scripts/chat_query.py "钟子鹏" \
  --start 2026-07-25 --end 2026-07-31

# 一键准备：取数 + 压缩上下文 + LLM JSON Prompt
bash scripts/chat-summary-workflow.sh prepare \
  --chat "琅泽" --yesterday

# AI 生成 summary.json 后，一键出图
bash scripts/chat-summary-workflow.sh render /path/to/summary.json

# 查看清理状态
backend/.venv/bin/python cleanup.py --check
```

#### summary.json 字段规范

```json
{
  "header": { "title", "date", "stats", "hot_word" },
  "summary": ["省流结论"],
  "topics": [{ "title", "time", "summary", "detail", "quotes": [{ "name", "avatar_name", "avatar_username", "content" }] }],
  "activity": [{ "label": "HH:00", "count": N }],  // 必须 24 个点
  "top_speakers": [{ "name", "count" }],  // 字段名是 count，不是 messages
  "keyword_tags": ["热词"],
  "report_meta": { "is_group", "activity_granularity", "raw_chars", "compressed_chars", "chars_saved", "estimated_tokens_saved", "compression_ratio" }
}
```

⚠️ 禁止手写 JSON，必须由 AI 分析 context.json 后生成。完整流程见 `skill/SKILL.md`。

## 联系人解析

`resolve_name()` 三层 fallback，确保群聊总结中的发送者始终显示可读昵称：

1. **contacts.json**（通讯录，6000+ 条）— 从 contact.db 解密获得
2. **group_nicknames.json**（群成员补充映射）— 手动维护，用于通讯录中没有的群友
3. **截断 wxid**（兜底）— 如 `wxid_psli7doelfml22` → `psli7do`

### 刷新通讯录

```bash
# 用现有密钥解密 contact.db 并更新 contacts.json
cd wechat-mcp-macos && python3 -c "
import json, os, sqlite3, sys
sys.path.insert(0, 'backend/.venv/lib/python3.14/site-packages')
from wechat_mcp_macos.decryptor import decrypt_database, decrypt_wal

with open('wechat_keys.json') as f:
    keys = json.load(f)
contact_db = os.path.expanduser('~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_hx1vuhtjkb3v22_1dd5/db_storage/contact/contact.db')
enc_key = keys.get('contact/contact.db')
cache = os.path.expanduser('~/.wechat-mcp/decrypted/contact/contact.db')
os.makedirs(os.path.dirname(cache), exist_ok=True)

decrypt_database(contact_db, cache, enc_key)
if os.path.exists(contact_db + '-wal'):
    decrypt_wal(contact_db + '-wal', cache, enc_key)

conn = sqlite3.connect(cache)
contacts = {}
for row in conn.execute('SELECT username, nick_name, remark FROM contact WHERE username != \"\"'):
    uname, nick, remark = row
    contacts[uname] = {'nickname': nick or '', 'remark': remark or ''}
conn.close()

with open('contacts.json', 'w') as f:
    json.dump(contacts, f, ensure_ascii=False, indent=2)
print(f'contacts.json updated: {len(contacts)} entries')
"
```

若密钥过期，需重新运行 `sign_wechat.sh` + `extract_keys.sh`。

## 项目结构

```
wechat-mcp-macos/
├── backend/
│   ├── _pkg/wechat_mcp_macos/    # FastMCP 服务源码（server / db / liveness / …）
│   └── .venv/              # Python 运行时（wechat_mcp_macos 包，实际部署位置）
├── plugin/                 # Hana 插件（manifest.json + 4 tools）
├── skill/                  # Hana Agent skill（含完整流程规范）
├── prompts/                # Prompt 管理系统
│   ├── registry.json       # trigger 定义
│   ├── render.py           # 匹配引擎
│   ├── templates/          # LLM prompt 模板文件
│   └── summaries/          # 生成的长图与中间产物（.gitignore）
├── scripts/                # 工具脚本
│   ├── chat-summary-workflow.sh  # 总结流水线（prepare/render）
│   ├── chat_query.py       # 日期区间查询
│   ├── enrich_summary_json.py    # 注入统计、热度、活跃群友
│   ├── validate_summary_json.py  # JSON 格式校验
│   └── wechat-mcp-remote   # 远程 MCP 桥接（LAN IP 缓存 + 裸 TCP 探测）
├── pipeline.py             # 纯规则总结 + 联系人解析
├── summary_img.py          # 长图渲染
├── voice_to_text.py        # 语音转文字
├── cleanup.py              # 中间数据清理（时间+容量双阈值）
├── cleanup-policy.json     # 清理策略配置
├── contacts.json           # 通讯录映射（从 contact.db 解密）
├── group_nicknames.json    # 群成员补充昵称（fallback）
├── crypto/                 # 解密模块
├── docs/                   # 远程接入 / 进程生命周期说明
├── tests/                  # 单元测试
└── .gitignore
```

## Hana 集成

### 安装 Skill

```bash
cp -r skill ~/.hanako/skills/wechat-mcp-setup
```

### 安装 Plugin

```bash
cp -r plugin ~/.hanako/plugins/wechat-mcp
```

### 触发方式

在 Hana Agent 中可直接说：
- "检查微信状态"
- "列出所有微信群"
- "读一下 xxx 的消息"
- "搜一下关于 xxx 的内容"
- "总结琅泽群"

插件通过 `execFileSync + JSON stdin → scripts/plugin_bridge.py` 调用仓库查询核心，避免字符串拼接与输入转义风险。

读取与搜索均支持：
- `date/start/end/today/yesterday/last/hours`
- 默认 04:00 日界线
- “我”与联系人/群成员身份识别
- `avatar_username` 真实头像键
- `page_size + cursor` 稳定分页，同一秒多条消息不会漏读

## 日报长图默认内容

当前 enrich + render 默认会在长图里补充：
- 标题下统计说明：原始文本字数、压缩后字数、约减少的 tokens 百分比
- 自适应热度曲线：单日/不超过 48 小时按小时，多日/周报按 04:00 逻辑日统计每天消息量
- 活跃群友头像 + 名字 + 消息数
- 关键词 tag
- 省流版（自动过滤技术指标行，避免与顶部统计说明重复）

## 渲染防裁切机制

`summary_img.py` 内置了内容安全保护：
- 预计算高度时预留 2x 安全余量，防止文字写出画布
- 渲染完成后根据实际内容定位自动裁切多余空白
- 若内容超出预计算范围，会自动扩展画布并补全纸张纹理

## 中间数据清理

`cleanup-policy.json` 同时控制时间与容量阈值。默认在每次 `chat-summary-workflow.sh prepare` 前执行：

```bash
# 只检查，不删除
backend/.venv/bin/python cleanup.py --check

# 正式清理
backend/.venv/bin/python cleanup.py
```

默认策略：总结中间文件保留 7 天/上限 50 MB，最终图片保留 30 天/上限 100 MB，解密缓存保留 7 天/上限 500 MB，日志保留 30 天/上限 50 MB；每类始终保护最新文件。配置不触碰密钥、MCP 配置或微信原始数据库。

## Prompt Registry

按群/联系人自定义 prompt 模板，无需改代码。

```json
{
  "prompts": [
    {
      "id": "langze-daily",
      "name": "琅泽群每日总结",
      "trigger": { "type": "group", "chats": ["58299288465@chatroom"] },
      "process": { "mode": "llm", "template_file": "group-summary-image-json.txt" },
      "output": { "text": true, "image": true }
    }
  ]
}
```

匹配优先级：**精确匹配 → 类型回退（群/私聊）→ catchall**。

## 决策规范

1. **用户要数据** → 纯脚本返回结果
2. **用户要分析** → 先脚本取数据，再 AI 理解
3. **用户要总结/分析** → 区间取数 → AI 输出带 `avatar_username` 的 JSON → 默认生成图片
4. **用户明确只要文字** → 才跳过图片
5. **本人身份** → 展示名为“我”，真实 self wxid 用于消息统计和头像匹配
6. **用户要发消息** → 生成文件，用户手动粘贴

零 token 优先。

## 远程 MCP 接入与进程生命周期

两个已经踩过的坑，完整说明见 [`docs/remote-access-and-lifecycle.md`](docs/remote-access-and-lifecycle.md)。

**① 远程调用只能走 LAN sshd。** macOS 的 TCC 只给 sshd 会话 Full Disk Access；Tailscale SSH 是 `tailscaled` 自己 `setuid` exec 出来的，读微信容器直接 `Operation not permitted`——**能连上 ≠ 能读数据**。桥接脚本 `scripts/wechat-mcp-remote` 负责：缓存 LAN IP → 裸 TCP 探测复验 → 只有缓存失效才做一次 shell hop 重新解析。正式 exec 时**绝不给 ssh 加 `-n`**，否则 stdin 变 `/dev/null`，服务起来也没有客户端。

```yaml
# Hermes ~/.hermes/config.yaml
  wechat-mengyu:
    command: /path/to/.local/bin/wechat-mcp-remote
    args: [mengyus-mac-mini-cli, mengyulu,
           /Users/mengyulu/wechat-mcp-macos/backend/.venv/bin/wechat-mcp-macos,
           192.168.3.38]        # LAN hint，避免每次靠 DHCP 猜
    timeout: 180
```

**② 一个客户端连接一个进程，进程必须自己会退。** stdio MCP 没有共享守护进程，也不能复用实例；客户端断开后不退出就会堆孤儿（2026-11-19 实测 7 个残留，最久跑了 2.5 天，每个还挂着一个已死的 Tailscale 会话）。`liveness.py` 三个守卫：启动时 stdin 就是 `/dev/null`、父进程消失（含启动即 `ppid == 1`）、空闲超时（默认 6h）。

> 守卫 1 必须在 `mcp.run()` **之前**采样：FastMCP 4 启动后会把 `/dev/null` 挂到 fd 0，事后再检测会把所有正常会话误杀。

```bash
# 找孤儿：命令行一样、只有 etime 不同、fd0 -> /dev/null、RSS ~4MB
pgrep -fl "python -m wechat_mcp_macos"
pkill -TERM -f "python -m wechat_mcp_macos"     # 只杀得掉本 uid 的

# 自检守卫：应在 ~1s 内退出，rc=0
WECHAT_MCP_GUARD_GRACE=1 backend/.venv/bin/wechat-mcp-macos < /dev/null; echo "rc=$?"

# 调试时关闭守卫
WECHAT_MCP_GUARDS=0 backend/.venv/bin/wechat-mcp-macos
```

| 变量 | 默认 | 作用 |
|---|---|---|
| `WECHAT_MCP_GUARDS` | `1` | `0` 关闭全部守卫 |
| `WECHAT_MCP_PARENT_GUARD` | `1` | `0` 只关父进程守卫（改成 LaunchAgent 常驻时用） |
| `WECHAT_MCP_GUARD_GRACE` | `8` | 启动后多少秒开始生效 |
| `WECHAT_MCP_GUARD_INTERVAL` | `15` | 巡检间隔（秒） |
| `WECHAT_MCP_IDLE_TIMEOUT` | `21600` | 空闲多少秒退出，`0` = 永不 |

## 依赖

- macOS（Apple Silicon / Intel）
- Python >= 3.10
- Homebrew + sqlcipher + llvm
- 微信 for Mac（已登录过）

## 致谢

- 解密模块来自 [wechat-digest](https://github.com/cliffyan28/wechat-digest) (MIT)
- 密钥提取来自 [wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos)
- MCP Server 来自 [wechat-mcp-macos](https://pypi.org/project/wechat-mcp-macos/)

## License

MIT
