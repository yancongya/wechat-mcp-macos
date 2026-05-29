const { execSync } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.join(
  process.env.HOME,
  "Desktop/OH-WorkSpace/wechat-mcp-macos"
);
const VENV_PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");

function runPython(code) {
  try {
    const result = execSync(`${VENV_PYTHON} -c "${code.replace(/"/g, '\\"')}"`, {
      cwd: PROJECT_DIR,
      timeout: 30000,
      encoding: "utf-8",
    });
    return { ok: true, output: result.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

export const name = "wechat_read";
export const description = "读取微信聊天记录。支持群聊和私聊。可按时间/发送者筛选。";

export const parameters = {
  type: "object",
  properties: {
    chat: { type: "string", description: "群聊/联系人名称或 wxid" },
    limit: { type: "number", description: "返回消息数量，默认 20" },
    hours: { type: "number", description: "读取最近 N 小时，默认 24" },
    sender: { type: "string", description: "只看指定发送者的消息" },
    format: { type: "string", description: "输出格式: plain/grouped，默认 plain" },
  },
  required: ["chat"],
};

export async function execute(input) {
  const chat = input.chat.replace(/"/g, '\\"');
  const limit = input.limit || 20;
  const hours = input.hours || 24;
  const sender = input.sender ? input.sender.replace(/"/g, '\\"') : "";
  const format = input.format || "plain";

  const senderFilter = sender ? `and m["sender"] == "${sender}"` : "";

  const code = `
import sys, os, json, time
from collections import defaultdict
sys.path.insert(0, '.')
from wechat_mcp_macos.config import load_config, KEYS_FILE
from wechat_mcp_macos.db import WeChatDB

cfg = load_config()
with open(str(KEYS_FILE)) as f:
    keys = json.load(f)
db = WeChatDB(cfg['db_dir'], keys)

username = db.resolve_username("${chat}")
if not username:
    print(f"未找到: ${chat}")
    sys.exit(0)

since_ts = time.time() - ${hours} * 3600
msgs = db.get_messages(username, since_ts=since_ts, limit=${limit})

if "${sender}":
    msgs = [m for m in msgs if m["sender"] == "${sender}"]

if not msgs:
    print("无消息")
else:
    fmt = "${format}"
    if fmt == "grouped":
        by_sender = defaultdict(list)
        for m in msgs:
            by_sender[m["sender"]].append(m)
        print(f"共 {len(msgs)} 条消息 ({len(by_sender)} 人发言):")
        for sender, sm in sorted(by_sender.items(), key=lambda x: -len(x[1])):
            print(f"\\n── {sender} ({len(sm)} 条) ──")
            for m in sm[:10]:
                print(f"  [{m['time_str']}] {m['text'][:100]}")
            if len(sm) > 10:
                print(f"  ... 还有 {len(sm) - 10} 条")
    else:
        for m in msgs:
            print(f"[{m['time_str']}] {m['sender']}: {m['text'][:200]}")
`;

  const result = runPython(code);
  if (result.ok) {
    return {
      content: [{ type: "text", text: result.output || "无消息" }],
    };
  }
  return {
    content: [{ type: "text", text: `读取失败: ${result.error}` }],
  };
}
