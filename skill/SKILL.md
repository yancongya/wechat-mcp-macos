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

### 1. 准备数据和 Prompt

```bash
bash scripts/chat-summary-workflow.sh prepare \
  --chat "钟子鹏" \
  --start 2026-07-25 \
  --end 2026-07-31
```

脚本输出目录包含：

```text
context.json
pipeline.json
prompt.txt
```

### 2. 生成结构化总结

读取 `context.json` 和 `prompt.txt`，严格基于真实压缩上下文生成 `summary.json`。

- 私聊：提取双方诉求、约定、待办、未决事项、情绪变化。
- 群聊：提取核心议题、共识、分歧、技巧、资源和待办。
- 多日范围：合并重复话题，描述话题演变，不逐条堆砌流水账。
- 热度粒度必须匹配报告周期：单日或不超过 48 小时按小时展示，多日/周报按逻辑日展示每天消息量；不得把多天数据叠加成 24 小时曲线冒充周热度。
- 对验证码、密码、令牌和疑似敏感数字做脱敏。
- 引用必须包含准确的 `avatar_username`。

### 3. 校验并出图

```bash
bash scripts/chat-summary-workflow.sh render /path/to/summary.json
```

该步骤依次执行：

```text
enrich_summary_json.py
→ validate_summary_json.py
→ summary_img.py
```

成功后交付 `summary.png`。不要只在文字里给出文件路径。

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
