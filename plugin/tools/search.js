const { execSync } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.join(
  process.env.HOME,
  "Desktop/OH-WorkSpace/wechat-decrypt-macos"
);
const VENV_PYTHON = path.join(PROJECT_DIR, ".venv/bin/python");

function runPython(code) {
  try {
    const result = execSync(`${VENV_PYTHON} -c "${code.replace(/"/g, '\\"')}"`, {
      cwd: PROJECT_DIR,
      timeout: 60000,
      encoding: "utf-8",
    });
    return { ok: true, output: result.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

export const name = "wechat_search";
export const description = "搜索微信聊天记录关键词。支持按发送者/时间分组。";

export const parameters = {
  type: "object",
  properties: {
    keyword: { type: "string", description: "搜索关键词" },
    chat: { type: "string", description: "限定搜索范围（群聊名称）" },
    hours: { type: "number", description: "搜索最近 N 小时，默认 24" },
    group_by: { type: "string", description: "分组方式: sender/time/none，默认 none" },
    limit: { type: "number", description: "最大返回条数，默认 50" },
  },
  required: ["keyword"],
};

export async function execute(input) {
  const keyword = input.keyword.replace(/"/g, '\\"');
  const chat = input.chat ? input.chat.replace(/"/g, '\\"') : "";
  const hours = input.hours || 24;
  const groupBy = input.group_by || "none";
  const limit = input.limit || 50;

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

${chat ? `usernames = [db.resolve_username("${chat}")]; usernames = [u for u in usernames if u]` : `usernames = [g["username"] for g in db.get_groups()]`}

results = db.search_messages(["${keyword}"], usernames, time.time() - ${hours} * 3600, time.time())
if not results:
    print("未找到匹配消息")
else:
    total = sum(len(msgs) for msgs in results.values())
    group_by = "${groupBy}"

    if group_by == "sender":
        by_sender = defaultdict(list)
        for uname, msgs in results.items():
            for m in msgs:
                by_sender[m["sender"]].append(m)
        print(f"找到 {total} 条匹配消息 (按发送者分组):")
        for sender, msgs in sorted(by_sender.items(), key=lambda x: -len(x[1])):
            print(f"\\n── {sender} ({len(msgs)} 条) ──")
            for m in msgs[:${limit // 10 + 1}]:
                group = m.get("group_name", "")
                print(f"  [{m['time_str']}] ({group}) {m['text'][:80]}")
    elif group_by == "time":
        by_hour = defaultdict(list)
        for uname, msgs in results.items():
            for m in msgs:
                hour = m["time_str"][:2] if m.get("time_str") else "??"
                by_hour[hour].append(m)
        print(f"找到 {total} 条匹配消息 (按时段分组):")
        for hour in sorted(by_hour.keys()):
            msgs = by_hour[hour]
            print(f"\\n── {hour}:00 ({len(msgs)} 条) ──")
            for m in msgs[:5]:
                print(f"  [{m['time_str']}] {m['sender']}: {m['text'][:80]}")
    else:
        print(f"找到 {total} 条匹配消息:")
        for uname, msgs in results.items():
            name = msgs[0]["group_name"] if msgs else uname
            print(f"\\n── {name} ({len(msgs)} 条) ──")
            for m in msgs[:20]:
                print(f"  [{m['time_str']}] {m['sender']}: {m['text'][:100]}")
`;

  const result = runPython(code);
  if (result.ok) {
    return {
      content: [{ type: "text", text: result.output || "未找到匹配消息" }],
    };
  }
  return {
    content: [{ type: "text", text: `搜索失败: ${result.error}` }],
  };
}
