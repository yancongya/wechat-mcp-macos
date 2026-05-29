---
name: wechat-mcp-setup
description: "微信本地数据读写。解密 macOS 微信数据库，读取聊天记录、搜索、语音转文字、生成群聊总结。触发词：微信、wechat、群聊、聊天记录、总结、搜索微信、日报图、群聊图片"
---

# WeChat MCP

macOS 微信本地数据读取能力。

## 能力

| 能力 | 入口 | 说明 |
|------|------|------|
| 读取聊天记录 | `wechat_read` MCP tool | 按群/联系人，支持筛选发送者、时间、分组 |
| 关键词搜索 | `wechat_search` MCP tool | 跨群搜索，支持按发送者/时间分组 |
| 群聊列表 | `wechat_groups` MCP tool | 列出所有群聊 |
| 纯规则总结 | `pipeline.py` | 零 LLM：活跃度/话题/时间线 |
| 语音转文字 | `voice_to_text.py` | SILK 解码 + faster-whisper |
| 结构化管理 Prompt | `prompts/render.py` | registry 匹配 → 取数据 → 填充 LLM 模板 |
| 长图生成 | `summary_img.py` | 总结渲染为精美长图 |

## 安装

```bash
bash ~/Desktop/OH-WorkSpace/wechat-mcp-macos/skill/scripts/check_env.sh
```

按输出的缺失步骤执行，所有脚本幂等。

字体依赖（长图生成）：
```bash
pip3 install Pillow --break-system-packages
```

## 使用

### 读取消息
```python
# MCP 工具
wechat_read(chat="群名", hours=24, sender="张三", format="grouped")

# 或 pipeline 直接读
python3 pipeline.py --dry-run --hours 24 --chat 58299288465
```

### 搜索
```python
wechat_search(keyword="关键词", group_by="sender", hours=24)
```

### 纯规则总结（零 token）
```bash
# 预览
cd ~/Desktop/OH-WorkSpace/wechat-mcp-macos
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --chat 58299288465

# 保存到文件
backend/.venv/bin/python pipeline.py --output /tmp/summary.txt --hours 24 --chat 58299288465

# JSON 格式（供链式调用）
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --json

# 带语音转文字
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --voice-engine whisper
```

### Prompt 渲染（registry 模式）
按 chat 自动匹配 prompt 模板，精确匹配优先 → 类型回退 → catchall。

```bash
# 列出所有已注册 prompt
backend/.venv/bin/python prompts/render.py --list

# 匹配渲染
backend/.venv/bin/python prompts/render.py "琅泽群"

# 指定时间 + 生成图片
backend/.venv/bin/python prompts/render.py "琅泽群" --hours 48 --image

# JSON 输出（供 AI 链式调用）
backend/.venv/bin/python prompts/render.py "琅泽群" --json
```

添加新 trigger：编辑 `prompts/registry.json`，新增 prompts 列表条目即可，无需改代码。

### 长图工作流（AI 总结 → 长图）
```bash
# 1. 取原始消息
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --chat "58299288465@chatroom" --json

# 2. AI 总结为结构化 JSON（格式见下方）
# 3. 渲染为长图
backend/.venv/bin/python summary_img.py --input /tmp/summary.json --output /tmp/summary.png
```

AI 总结输出的 JSON schema：
```json
{
  "header": {
    "title": "群名称",
    "date": "2026-05-30",
    "stats": "N 人参与 · N 条消息",
    "hot_word": "热词"
  },
  "topics": [
    {
      "title": "话题标题",
      "time": "10:00 - 11:00",
      "summary": "一句话摘要",
      "detail": "详细分析段落",
      "quotes": [["发言人", "引用内容"]]
    }
  ]
}
```

### 群 wxid 速查
```
58299288465@chatroom  — 【琅泽-老K】几何节点全能班0群
48672694909@chatroom  — 【琅泽-老K】BL几何节点入门
19215266204@chatroom  — 朔朔的程序化反馈群
```

### 清理缓存
```bash
backend/.venv/bin/python cleanup.py --check  # 查看大小
backend/.venv/bin/python cleanup.py          # 清理
```

## 工作流（强制）

无论用户措辞如何（总结、分析、看看、读一下、讲一下、群里说了啥、最近在聊什么等），必须按以下顺序执行：

1. **调用数据工具** → 先通过 render.py / wechat_read / pipeline.py 获取原始数据
2. **分析数据** → 基于返回的真实数据做分析
3. **输出结果** → 基于数据分析给出回答
4. **不得跳过步骤 1** → 不允许直接用自身知识替代表述或猜测内容

## 决策规范

1. **用户要数据** → 纯脚本返回结果
2. **用户要分析** → 先脚本取数据，再 AI 理解
3. **用户要图片** → pipeline 取数据 → AI 总结为 JSON → summary_img.py 渲染
4. **用户要发消息** → 生成文件，用户手动粘贴

零 token 优先。

## 注意事项

- **字体**：macOS 上 PIL 需用 STHeiti Medium.ttc（`/System/Library/Fonts/STHeiti Medium.ttc`）
- **群成员名称**：大部分显示为 wxid，唯有 contacts.json 映射过的联系人会显示昵称
- **图片消息**：数据库中图片为二进制 blob，当前无法直接解密显示

## 项目路径

```
~/Desktop/OH-WorkSpace/wechat-mcp-macos/
├── backend/.venv/          # Python 运行时（wechat_mcp_macos 包）
├── plugin/                 # Hana 插件（manifest + tools）
├── skill/                  # Agent skill 源码
├── prompts/
│   ├── registry.json       # 所有 trigger 定义
│   ├── render.py           # 匹配 + 填充引擎
│   ├── templates/          # LLM prompt 模板
│   └── summaries/          # 生成的长图
├── pipeline.py             # 纯规则总结
├── summary_img.py          # 长图渲染
├── voice_to_text.py        # 语音转文字
├── crypto/                 # 解密模块
├── key.txt / wechat_keys.json / contacts.json
└── .gitignore
```

## 密钥过期

微信更新后重跑 `sign_wechat.sh` + `extract_keys.sh`。
