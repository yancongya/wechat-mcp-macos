const { execSync } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.join(
  process.env.HOME,
  "Desktop/OH-WorkSpace/wechat-mcp-macos"
);
const VENV_PYTHON = path.join(PROJECT_DIR, "backend/.venv/bin/python");

function runPython(code) {
  try {
    const result = execSync(`${VENV_PYTHON} -c '${code.replace(/'/g, "'\\''")}'`, {
      cwd: PROJECT_DIR,
      timeout: 30000,
      encoding: "utf-8",
    });
    return { ok: true, output: result.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

export const name = "wechat_status";
export const description = "检查微信 MCP Server 状态：配置、密钥、微信进程等";

export async function execute() {
  const code = `
import sys, os, json
sys.path.insert(0, '.')
from wechat_mcp_macos.config import load_config, KEYS_FILE
from wechat_mcp_macos.key_extractor import is_wechat_running, is_wechat_signed, get_wechat_pid

cfg = load_config()
lines = []

# DB
if cfg['db_dir']:
    lines.append(f"数据库目录: ✅")
else:
    lines.append(f"数据库目录: ❌ 未配置")

# Keys
if os.path.exists(str(KEYS_FILE)):
    with open(KEYS_FILE) as f:
        keys = json.load(f)
    lines.append(f"加密密钥: ✅ {len(keys)} 个数据库")
else:
    lines.append(f"加密密钥: ❌ 未提取")

# WeChat
pid = get_wechat_pid()
if pid:
    lines.append(f"微信进程: ✅ 运行中 (PID {pid})")
else:
    lines.append(f"微信进程: ❌ 未运行")

# Signing
if is_wechat_signed():
    lines.append(f"微信签名: ✅ ad-hoc")
else:
    lines.append(f"微信签名: ❌ hardened runtime")

print("\\n".join(lines))
`;

  const result = runPython(code);
  if (result.ok) {
    return {
      content: [{ type: "text", text: `=== 微信 MCP 状态 ===\n${result.output}` }],
    };
  }
  return {
    content: [{ type: "text", text: `检查失败: ${result.error}` }],
  };
}
