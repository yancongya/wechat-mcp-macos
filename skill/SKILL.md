---
name: wechat-local
description: "macOS 微信本地数据工作流。支持群聊/私聊、联系人搜索、任意日期区间、04:00 日界线、双方身份与头像、文字总结和日报图。用户提到微信、群聊、私聊、聊天记录、某人与我的对话、搜索微信、总结聊天、日报图，或要求按今天/昨天/指定日期/日期区间回顾时使用。总结意图默认完整生成图片；明确只要文字时才不出图。"
compatibility: "macOS；需要本项目 backend/.venv、已提取微信数据库密钥，以及可执行本机命令的 Agent。"
---

# WeChat Local

通过项目 CLI 读取 macOS 微信本地数据库。核心能力不依赖 Hana 插件；插件作为可选适配层，现已与同一查询核心保持一致。

项目根目录默认为：

```bash
~/Desktop/OH-WorkSpace/wechat-mcp-macos
```

所有 Python 命令使用项目虚拟环境：

```bash
backend/.venv/bin/python
```

## 意图决策

| 用户意图 | 行为 |
|---|---|
| 读取、看看刚才说了什么 | 查询真实消息，文字返回 |
| 搜索关键词 | 查询真实消息，文字返回 |
| 统计消息量 | 运行规则统计，文字或 JSON 返回 |
| 总结、分析、回顾群聊或私聊 | 取数 → AI 总结 JSON → enrich → validate → 默认生成并交付 PNG |
| 明确说“只要文字” | 完成总结，但跳过图片 |

不得凭自身知识猜测聊天内容。任何总结必须先调用本地数据脚本。

## 日期规则

默认日界线为 **04:00**，与用户的一天口径一致。

- `--today`：本逻辑日 04:00 至现在
- `--yesterday`：上一个逻辑日 04:00 至本逻辑日 04:00
- `--date 2026-07-30`：该日 04:00 至次日 04:00
- `--start 2026-07-25 --end 2026-07-31`：包含 7 月 25 日至 31 日，结束于 8 月 1 日 04:00
- 精确时间采用左闭右开 `[start, end)`
- 用户明确要求自然日时传 `--day-start 00:00`

不要自行把“某日到某日”解释成只到结束日期 00:00。

## 读取区间消息

```bash
cd ~/Desktop/OH-WorkSpace/wechat-mcp-macos

# 今天
backend/.venv/bin/python scripts/chat_query.py "钟子鹏" --today

# 昨天
backend/.venv/bin/python scripts/chat_query.py "琅泽群" --yesterday

# 单日
backend/.venv/bin/python scripts/chat_query.py "钟子鹏" --date 2026-07-30

# 日期区间，结束日期包含整天
backend/.venv/bin/python scripts/chat_query.py "琅泽群" \
  --start 2026-07-25 --end 2026-07-31

# 精确时间
backend/.venv/bin/python scripts/chat_query.py "钟子鹏" \
  --start "2026-07-30 18:00" --end "2026-07-31 04:00"

# 快捷范围
backend/.venv/bin/python scripts/chat_query.py "琅泽群" --last 7d
```

输出包括：

- `range`：起止时间及时间戳
- `messages`：带发送者身份的真实消息
- `stats`：消息数、文本数、活动曲线、双方/群成员消息数
- `truncated`：是否触发消息上限

默认最多读取 10000 条。若 `truncated=true`，必须在回答和图片中说明数据被截断；可按需要提高 `--max-messages`。

## 身份与头像铁律

每条消息必须保留：

```json
{
  "sender": "我",
  "sender_type": "self",
  "sender_username": "wxid_xxx"
}
```

或者：

```json
{
  "sender": "联系人昵称",
  "sender_type": "contact",
  "sender_username": "wxid_xxx"
}
```

- 页面展示使用 `sender`。
- 头像匹配使用真实 `sender_username`。
- 引用 JSON 中同时填写 `avatar_name` 和 `avatar_username`。
- 用户本人展示名写“我”，`avatar_username` 必须使用 self wxid。
- 群聊和私聊都不得省略用户本人的消息、消息数和头像。
- 数据不能可靠识别发送者时，说明限制，不猜测归属。

## 总结图片标准流程

⚠️ **铁律：必须执行完整流水线，禁止跳过任何步骤，禁止手写 summary.json。**

### 前置条件

执行前必须满足：
1. 存在 `backend/.venv` 虚拟环境
2. 微信数据库密钥已提取
3. 当前目录是项目根目录

### 步骤 1：运行 prepare（必须）

```bash
cd ~/Desktop/OH-WorkSpace/wechat-mcp-macos
bash scripts/chat-summary-workflow.sh prepare \
  --chat "钟子鹏" \
  --start 2026-07-25 \
  --end 2026-07-31
```

**此步骤不可跳过**，它会生成：
- `context.json`：压缩后的聊天上下文（含原始文本字数统计）
- `pipeline.json`：流水线元数据
- `prompt.txt`：AI 生成用的 prompt

验证：检查输出目录是否包含这三个文件。

### 步骤 2：AI 生成 summary.json（必须）

读取 `context.json` 和 `prompt.txt`，**严格基于真实压缩上下文**生成 `summary.json`。

⚠️ **禁止手写 JSON**，必须由 AI 分析 context.json 后生成。

#### 必填字段

```json
{
  "header": {
    "title": "群名/私聊名 报告类型",
    "date": "YYYY-MM-DD",
    "stats": "约N条消息 · M人参与",
    "hot_word": "关键词1 / 关键词2"
  },
  "summary": ["一句话摘要"],
  "topics": [
    {
      "title": "话题标题",
      "time": "HH:00-HH:00",
      "summary": "话题摘要",
      "detail": "详细说明",
      "quotes": [
        {"name": "发言者", "avatar_name": "显示名", "avatar_username": "wxid", "content": "引用内容"}
      ]
    }
  ],
  "activity": [
    {"label": "00:00", "count": 0},
    ...  // 必须 24 个点（00:00-23:00）
  ],
  "top_speakers": [
    {"name": "显示名", "avatar_name": "显示名", "avatar_username": "wxid", "count": 68}
  ],
  "keyword_tags": ["热词1", "热词2"],
  "report_meta": {
    "is_group": true,
    "activity_granularity": "hour",
    "raw_chars": 18500,
    "compressed_chars": 3200,
    "chars_saved": 15300,
    "estimated_tokens_saved": 5100,
    "compression_ratio": 82.7
  }
}
```

**字段规范：**
- `top_speakers[].count`：消息数字段名是 `count`，**不是** `messages`
- `activity`：必须是数组，24 个点，标签格式 `"HH:00"`
- `report_meta`：必须包含 `raw_chars`、`compressed_chars` 等统计数据，否则图片显示 0

#### 内容要求

- 私聊：提取双方诉求、约定、待办、未决事项、情绪变化。
- 群聊：提取核心议题、共识、分歧、技巧、资源和待办。
- 多日范围：合并重复话题，描述话题演变，不逐条堆砌流水账。
- 热度粒度：单日或不超过 48 小时按小时展示，多日/周报按逻辑日展示每天消息量。
- 对验证码、密码、令牌和疑似敏感数字做脱敏。
- 引用必须包含准确的 `avatar_username`。

### 步骤 3：运行 render（必须）

```bash
bash scripts/chat-summary-workflow.sh render /path/to/summary.json
```

该步骤依次执行：

```text
enrich_summary_json.py  // 补充统计指标
→ validate_summary_json.py  // 校验格式
→ summary_img.py  // 渲染图片
```

验证：检查输出是否包含 `summary.png`，且图片中原始文本字数不为 0。

成功后交付 `summary.png`。不要只在文字里给出文件路径。

### 常见错误

| 错误 | 原因 | 修复 |
|------|------|------|
| 活跃群友消息数显示 0 | `count` 写成了 `messages` | 改为 `count` |
| 原始文本 0 字 | `report_meta` 缺少 `raw_chars` | 从 context.json 补充 |
| 省流版显示压缩指标 | 指标行放在了 summary 数组里 | 移到 report_meta |
| validate 报错 activity 非数组 | activity 写成了对象 `{hourly:[...]}` | 改为直接数组 |

## 中间数据清理

清理策略位于 `cleanup-policy.json`，同时支持时间与容量阈值：

- `summary_intermediates`：context/pipeline/prompt/summary 等中间文件
- `summary_images`：最终 PNG/JPG，默认保留更久
- `decrypted`：`~/.wechat-mcp/decrypted` 解密缓存
- `logs`：`~/.wechat-mcp/logs` 日志

每次 `chat-summary-workflow.sh prepare` 前默认执行一次清理；临时跳过可传 `--skip-cleanup`。

```bash
# 只查看占用和预计清理量，不删除
backend/.venv/bin/python cleanup.py --check

# 按 cleanup-policy.json 正式清理
backend/.venv/bin/python cleanup.py

# 只清理中间总结数据
backend/.venv/bin/python cleanup.py --only summary_intermediates
```

清理顺序：先删除超过 `max_age_days` 的文件，再从最旧文件开始压到 `max_size_mb`；始终保护最新 `keep_newest` 个。不得清理密钥、配置或微信原始数据库。

## Prompt Registry

`prompts/registry.json` 决定专用模板与通用 fallback。

- 专用群可使用定制模板。
- 未匹配群聊使用 `any-group-summary`。
- 未匹配私聊使用 `any-contact-summary`。
- 两种通用总结都配置 `image: true`。

“总结”意图必须走完整图片工作流，不能因为 fallback 命中规则统计而停在文字阶段。

## Hana 插件工具

插件 0.3.0 自动发现 `tools/*.js`，提供四个只读工具：

```text
wechat-mcp_wechat_read
wechat-mcp_wechat_search
wechat-mcp_wechat_groups
wechat-mcp_wechat_status
```

`wechat_read` 和 `wechat_search` 与 Skill 共用 `scripts/plugin_bridge.py`、`chat_query.py` 和 `date_range.py`，日期、身份和分页语义一致。读取分页使用 `(create_time, local_id)` 复合游标，搜索分页额外加入会话 wxid；传回 `next_cursor` 即可继续。

插件工具只是快捷入口。总结图片仍按本 Skill 的 prepare → AI JSON → render 流程执行。

## 环境检查

```bash
bash skill/scripts/check_env.sh
```

密钥失效时按顺序执行：

```bash
bash skill/scripts/sign_wechat.sh
bash skill/scripts/extract_keys.sh
```

头像依赖 `head_image/head_image.db`。若头像无法显示，先检查密钥和头像库记录，不要伪造头像。

## 兼容入口

旧命令仍可用：

```bash
bash scripts/group-summary-workflow.sh prepare "群名或 wxid" 24
```

它会转发到新的 `chat-summary-workflow.sh`。新工作流优先使用参数式调用。
