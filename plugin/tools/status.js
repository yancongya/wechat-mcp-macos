import { execFileSync } from "node:child_process";
import path from "node:path";

const PROJECT_DIR = path.join(process.env.HOME, "Desktop/OH-WorkSpace/wechat-mcp-macos");
const PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");
const BRIDGE = path.join(PROJECT_DIR, "scripts/plugin_bridge.py");

export const name = "wechat_status";
export const description = "只读检查微信数据库、密钥、进程与签名状态。";
export const parameters = { type: "object", properties: {} };
export const sessionPermission = { readOnly: true };

export async function execute() {
  try {
    const output = execFileSync(PYTHON, [BRIDGE, "status"], {
      cwd: PROJECT_DIR,
      input: "{}",
      timeout: 60000,
      encoding: "utf-8",
    });
    const result = JSON.parse(output);
    if (!result.ok) throw new Error(result.error);
    return {
      content: [{ type: "text", text: result.text }],
      structuredContent: result,
    };
  } catch (error) {
    return { content: [{ type: "text", text: `检查失败: ${error.stderr || error.message}` }] };
  }
}
