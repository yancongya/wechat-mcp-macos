import { execFileSync } from "node:child_process";
import path from "node:path";

const PROJECT_DIR = path.join(process.env.HOME, "Desktop/OH-WorkSpace/wechat-mcp-macos");
const PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");
const BRIDGE = path.join(PROJECT_DIR, "scripts/plugin_bridge.py");

export const name = "wechat_groups";
export const description = "只读列出所有微信群聊及其 wxid。";
export const parameters = { type: "object", properties: {} };
export const sessionPermission = { readOnly: true };

export async function execute() {
  try {
    const output = execFileSync(PYTHON, [BRIDGE, "groups"], {
      cwd: PROJECT_DIR,
      input: "{}",
      timeout: 60000,
      encoding: "utf-8",
      maxBuffer: 20 * 1024 * 1024,
    });
    const result = JSON.parse(output);
    if (!result.ok) throw new Error(result.error);
    return {
      content: [{ type: "text", text: result.text }],
      structuredContent: result,
    };
  } catch (error) {
    return { content: [{ type: "text", text: `获取群列表失败: ${error.stderr || error.message}` }] };
  }
}
