import { execFileSync } from "node:child_process";
import path from "node:path";

const PROJECT_DIR = path.join(process.env.HOME, "Desktop/OH-WorkSpace/wechat-mcp-macos");
const PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");
const BRIDGE = path.join(PROJECT_DIR, "scripts/plugin_bridge.py");

function runBridge(input) {
  try {
    const output = execFileSync(PYTHON, [BRIDGE, "read"], {
      cwd: PROJECT_DIR,
      input: JSON.stringify(input),
      timeout: 60000,
      encoding: "utf-8",
      maxBuffer: 20 * 1024 * 1024,
    });
    return JSON.parse(output);
  } catch (error) {
    return { ok: false, error: error.stderr || error.message };
  }
}

export const name = "wechat_read";
export const description = "读取微信群聊或私聊。支持今天、昨天、任意日期区间、发送者过滤及复合游标分页；能识别‘我’和真实头像 wxid。";

export const sessionPermission = { readOnly: true };

export const parameters = {
  type: "object",
  properties: {
    chat: { type: "string", description: "群聊、联系人名称或 wxid" },
    date: { type: "string", description: "指定单日 YYYY-MM-DD" },
    start: { type: "string", description: "开始日期或时间 YYYY-MM-DD[ HH:MM]" },
    end: { type: "string", description: "结束日期或时间；仅日期时包含整天" },
    today: { type: "boolean", description: "读取今天，默认日界线 04:00" },
    yesterday: { type: "boolean", description: "读取昨天，默认日界线 04:00" },
    last: { type: "string", description: "快捷范围，例如 7d、12h" },
    hours: { type: "number", description: "兼容参数：最近 N 小时，0 表示今天" },
    day_start: { type: "string", description: "每日边界 HH:MM，默认 04:00" },
    page_size: { type: "number", description: "每页 1-500 条，默认 100" },
    cursor: { type: "string", description: "上一页返回的 next_cursor" },
    sender: { type: "string", description: "只显示指定发送者，例如‘我’" },
    format: { type: "string", enum: ["plain", "grouped"], description: "输出格式" },
  },
  required: ["chat"],
};

export async function execute(input) {
  const result = runBridge(input);
  if (!result.ok) {
    return { content: [{ type: "text", text: `读取失败: ${result.error}` }] };
  }
  return {
    content: [{ type: "text", text: result.text || "无消息" }],
    structuredContent: result,
  };
}
