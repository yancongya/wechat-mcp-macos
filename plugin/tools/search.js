import { execFileSync } from "node:child_process";
import path from "node:path";

const PROJECT_DIR = path.join(process.env.HOME, "Desktop/OH-WorkSpace/wechat-mcp-macos");
const PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");
const BRIDGE = path.join(PROJECT_DIR, "scripts/plugin_bridge.py");

function runBridge(input) {
  try {
    const output = execFileSync(PYTHON, [BRIDGE, "search"], {
      cwd: PROJECT_DIR,
      input: JSON.stringify(input),
      timeout: 120000,
      encoding: "utf-8",
      maxBuffer: 30 * 1024 * 1024,
    });
    return JSON.parse(output);
  } catch (error) {
    return { ok: false, error: error.stderr || error.message };
  }
}

export const name = "wechat_search";
export const description = "搜索微信群聊或私聊。支持任意日期区间、本人身份识别、按发送者或时段分组。";

export const sessionPermission = { readOnly: true };

export const parameters = {
  type: "object",
  properties: {
    keyword: { type: "string", description: "搜索关键词" },
    chat: { type: "string", description: "可选：限定群聊、联系人或 wxid" },
    date: { type: "string", description: "指定单日 YYYY-MM-DD" },
    start: { type: "string", description: "开始日期或时间 YYYY-MM-DD[ HH:MM]" },
    end: { type: "string", description: "结束日期或时间；仅日期时包含整天" },
    today: { type: "boolean", description: "搜索今天，默认日界线 04:00" },
    yesterday: { type: "boolean", description: "搜索昨天，默认日界线 04:00" },
    last: { type: "string", description: "快捷范围，例如 7d、12h" },
    hours: { type: "number", description: "兼容参数：最近 N 小时，0 表示今天" },
    day_start: { type: "string", description: "每日边界 HH:MM，默认 04:00" },
    group_by: { type: "string", enum: ["sender", "time", "none"], description: "分组方式" },
    page_size: { type: "number", description: "每页 1-500 条，默认 50" },
    cursor: { type: "string", description: "上一页返回的 next_cursor" },
    limit: { type: "number", description: "兼容参数：最大返回条数" },
    max_scan_per_chat: { type: "number", description: "每个会话最多扫描条数，默认 5000" },
  },
  required: ["keyword"],
};

export async function execute(input) {
  const result = runBridge(input);
  if (!result.ok) {
    return { content: [{ type: "text", text: `搜索失败: ${result.error}` }] };
  }
  return {
    content: [{ type: "text", text: result.text || "未找到匹配消息" }],
    structuredContent: result,
  };
}
