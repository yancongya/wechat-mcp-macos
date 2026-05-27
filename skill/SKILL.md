---
name: wechat-mcp-setup
description: "微信本地数据读写。解密 macOS 微信数据库，读取聊天记录、搜索、语音转文字、生成群聊总结。触发词：微信、wechat、群聊、聊天记录、总结、搜索微信"
---

# WeChat MCP

macOS 微信本地数据读取能力。

## 能力

| 能力 | 入口 | 说明 |
|------|------|------|
| 读取聊天记录 | `wechat_read` MCP tool | 按群/联系人，支持筛选发送者、时间、分组 |
| 关键词搜索 | `wechat_search` MCP tool | 跨群搜索，支持按发送者/时间分组 |
| 群聊列表 | `wechat_groups` MCP tool | 列出所有群聊 |
| 每日总结 | `pipeline.py` | 纯规则：活跃度/话题/时间线/待办 |
| 语音转文字 | `voice_to_text.py` | SILK 解码 + faster-whisper |
| 输出文件 | `pipeline.py --output` | 保存到本地文件 |

## 安装

```bash
bash /Users/tanyancong/.hanako/skills/wechat-mcp-setup/scripts/check_env.sh
```

按输出的缺失步骤执行，所有脚本幂等。

## 使用

### 读取消息
```python
# MCP 工具
wechat_read(chat="群名", hours=24, sender="张三", format="grouped")

# 或 pipeline
python3 pipeline.py --dry-run --hours 24 --chat 58299288465
```

### 搜索
```python
wechat_search(keyword="关键词", group_by="sender", hours=24)
```

### 生成总结
```bash
# 预览
python3 pipeline.py --dry-run --hours 24 --chat 58299288465

# 保存到文件
python3 pipeline.py --output /tmp/summary.txt --hours 24 --chat 58299288465

# 带语音转文字
python3 pipeline.py --dry-run --hours 24 --voice-engine whisper

# JSON 格式（供 AI 读取）
python3 pipeline.py --dry-run --hours 24 --json
```

### 清理缓存
```bash
python3 cleanup.py --check   # 查看大小
python3 cleanup.py           # 清理
```

## 决策规范

1. **用户要数据** → 纯脚本返回结果
2. **用户要分析** → 先脚本取数据，再 AI 理解
3. **用户要发消息** → 生成文件，用户手动粘贴

零 token 优先。

## 项目路径

```
~/Desktop/OH-WorkSpace/wechat-decrypt-macos/
├── pipeline.py          # 总结
├── cleanup.py           # 缓存清理
├── voice_to_text.py     # 语音转文字
├── crypto/              # 解密模块
├── key.txt / wechat_keys.json / contacts.json
└── .venv/
```

## 密钥过期

微信更新后重跑 `sign_wechat.sh` + `extract_keys.sh`。
