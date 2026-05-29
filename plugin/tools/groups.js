const { execSync } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.join(
  process.env.HOME,
  "Desktop/OH-WorkSpace/wechat-mcp-macos"
);
const VENV_PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");

export const name = "wechat_groups";
export const description = "列出所有微信群聊";

export async function execute() {
  const code = `
import sys, os, json
sys.path.insert(0, '.')
from wechat_mcp_macos.config import load_config, KEYS_FILE
from wechat_mcp_macos.db import WeChatDB

cfg = load_config()
with open(str(KEYS_FILE)) as f:
    keys = json.load(f)
db = WeChatDB(cfg['db_dir'], keys)

groups = db.get_groups()
print(f"共 {len(groups)} 个群聊:")
for g in groups:
    print(f"  {g['name']} ({g['username']})")
`;

  try {
    const result = execSync(`${VENV_PYTHON} -c "${code.replace(/"/g, '\\"')}"`, {
      cwd: PROJECT_DIR,
      timeout: 30000,
      encoding: "utf-8",
    });
    return {
      content: [{ type: "text", text: result.trim() }],
    };
  } catch (e) {
    return {
      content: [{ type: "text", text: `获取群列表失败: ${e.message}` }],
    };
  }
}
