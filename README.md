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
scripts/group-summary-workflow.sh ← prepare / render 一键流水线
    ├── enrich_summary_json.py    ← 注入统计、热度、活跃群友、关键词 tag
    └── validate_summary_json.py  ← 渲染前自检
    ↓
backend/.venv/                    ← Python 运行时（wechat_mcp_macos 包）
    ├── pipeline.py               ← 纯规则总结
    ├── summary_img.py            ← 长图渲染
    └── voice_to_text.py          ← 语音转文字
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
| 日报流水线 | `scripts/group-summary-workflow.sh` | prepare / render 一键串联取数、enrich、自检、出图 |
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

默认日报口径：
- `hours=0` 表示**今天自然日 0:00 到现在**
- 不再默认使用 rolling 24h，避免跨天和 token 膨胀
- LLM 默认读取**压缩上下文**，不再直接吞全量原始聊天

```bash
# 统一切记用 backend/.venv/bin/python 执行
cd wechat-mcp-macos

# 纯规则总结（零 token）
backend/.venv/bin/python pipeline.py --dry-run --hours 24

# 指定群
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --chat "琅泽"

# JSON 输出（供链式调用）
backend/.venv/bin/python pipeline.py --dry-run --hours 24 --json

# Prompt 渲染（匹配 registry → 填充 LLM 模板）
backend/.venv/bin/python prompts/render.py "琅泽群" --hours 48

# 一键准备：取数 + 规则摘要 + 给 LLM 的 JSON prompt
bash scripts/group-summary-workflow.sh prepare "【琅泽-老K】几何节点全能班0群" 0

# 一键出图：把 LLM 返回的 summary.json 渲染成长图
# render 会自动 enrich + validate，再出图
bash scripts/group-summary-workflow.sh render /path/to/summary.json

# 单独校验增强版 JSON
backend/.venv/bin/python scripts/validate_summary_json.py /path/to/summary.enriched.json
```

## 项目结构

```
wechat-mcp-macos/
├── backend/
│   └── .venv/              # Python 运行时（wechat_mcp_macos 包）
├── plugin/                 # Hana 插件（manifest.json + 4 tools）
│   ├── manifest.json
│   ├── index.js
│   ├── tools/              # status / read / search / groups
│   └── routes/             # 状态页面
├── skill/                  # Hana Agent skill
│   ├── SKILL.md
│   └── scripts/            # 幂等安装脚本
├── prompts/                # Prompt 管理系统
│   ├── registry.json       # trigger 定义（群/联系人 → prompt 映射）
│   ├── render.py           # 匹配引擎：取数据 → 填充模板
│   ├── templates/          # LLM prompt 模板文件
│   └── summaries/          # 生成的长图（.gitignore）
├── pipeline.py             # 纯规则总结
├── summary_img.py          # 长图渲染（含自动裁切与高度适配）
├── voice_to_text.py        # 语音转文字
├── extract-messages.py     # 消息提取
├── init-keys.py            # 密钥提取
├── server.py               # 独立 MCP Server（FastMCP）
├── cleanup.py              # 缓存清理
├── crypto/                 # 解密模块
├── scripts/                # 流水线脚本
│   ├── group-summary-workflow.sh
│   ├── enrich_summary_json.py
│   └── validate_summary_json.py
├── contacts.example.json
├── requirements.txt
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

底层全部走 `execSync → backend/.venv/bin/python` 直接执行，零 token 开销。

## 日报长图默认内容

当前 enrich + render 默认会在长图里补充：
- 标题下统计说明：原始文本字数、压缩后字数、约减少的 tokens 百分比
- 时间段热度曲线
- 活跃群友头像 + 名字 + 消息数
- 关键词 tag p- 省流版（自动过滤技术指标行，避免与顶部统计说明重复）

## 渲染防裁切机制

`summary_img.py` 内置了内容安全保护：
- 预计算高度时预留 2x 安全余量，防止文字写出画布
- 渲染完成后根据实际内容定位自动裁切多余空白
- 若内容超出预计算范围，会自动扩展画布并补全纸张纹理

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
3. **用户要图片** → pipeline / render 取数据 → AI 输出带 `avatar_name` 的 JSON → summary_img.py 渲染
4. **用户要发消息** → 生成文件，用户手动粘贴

零 token 优先。

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
