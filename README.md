# WeChat MCP macOS

macOS 微信本地数据读取能力。解密本地数据库，读取聊天记录、搜索、语音转文字、生成群聊总结。

## 功能

| 功能 | 说明 |
|------|------|
| 读取聊天记录 | 按群/联系人读取，支持筛选发送者、时间 |
| 关键词搜索 | 跨群搜索，支持按发送者/时间分组 |
| 群聊列表 | 列出所有群聊 |
| 每日总结 | 纯规则生成：活跃度/话题/热词 |
| 语音转文字 | SILK 解码 + faster-whisper |
| MCP Server | 供 AI Agent 调用的标准化接口 |

## 原理

```
微信本地加密数据库 (SQLCipher 4)
    ↓ 解密
读取消息 / 语音 / 联系人
    ↓ 纯脚本分析
生成结构化总结
```

- 零 API 调用，纯本地运行
- 不修改原始数据库
- 支持文本/图片/语音/链接/合并转发

## 快速开始

### 1. 安装依赖

```bash
# Python 依赖
pip install zstandard pycryptodome pilk faster-whisper

# 系统依赖（macOS）
brew install sqlcipher llvm
```

### 2. 提取密钥

```bash
# 确保微信已登录
sudo python3 init-keys.py
```

### 3. 生成总结

```bash
# 今天自然日
python3 pipeline.py --dry-run

# 指定群
python3 pipeline.py --dry-run --chat 群名关键词

# 过去 48 小时
python3 pipeline.py --dry-run --hours 48

# 保存到文件
python3 pipeline.py --output summary.txt

# 带语音转文字
python3 pipeline.py --dry-run --voice-engine whisper
```

## 项目结构

```
wechat-mcp-macos/
├── pipeline.py          # 主入口：总结生成
├── cleanup.py           # 缓存清理
├── voice_to_text.py     # 语音转文字
├── extract-messages.py  # 消息提取
├── init-keys.py         # 密钥提取
├── server.py            # MCP Server
├── crypto/              # 解密模块
├── skill/               # Hana Skill
│   ├── SKILL.md
│   └── scripts/         # 安装脚本
├── plugin/              # Hana Plugin
│   ├── manifest.json
│   ├── tools/           # MCP 工具
│   └── routes/          # 状态页面
├── contacts.example.json
└── .gitignore
```

## Hana 集成

### Skill 安装

```bash
# 复制 skill 目录到 Hana 技能池
cp -r skill ~/.hanako/skills/wechat-mcp-setup
```

### Plugin 安装

```bash
# 复制 plugin 目录到 Hana 插件目录
cp -r plugin ~/.hanako/plugins/wechat-mcp
```

### 使用

在 Hana Agent 中：
- "帮我总结今天琅泽群的消息" → 调用 pipeline.py
- "搜一下群里提到的项目" → 调用 wechat_search
- "看看张三最近说了什么" → 调用 wechat_read

## 决策规范

1. **用户要数据** → 纯脚本返回结果
2. **用户要分析** → 先脚本取数据，再 AI 理解
3. **零 token 优先**：能用脚本解决的不调 AI

## 依赖

- Python >= 3.10
- macOS (Apple Silicon / Intel)
- Homebrew
- 微信 for Mac（已登录过）

## 致谢

- 解密模块来自 [wechat-digest](https://github.com/cliffyan28/wechat-digest) (MIT)
- 密钥提取来自 [wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos)
- MCP Server 来自 [wechat-mcp-macos](https://pypi.org/project/wechat-mcp-macos/)

## License

MIT
