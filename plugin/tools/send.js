const { execSync } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.join(
  process.env.HOME,
  "Desktop/OH-WorkSpace/wechat-decrypt-macos"
);
const VENV_PYTHON = path.join(PROJECT_DIR, ".venv/bin/python");

export const name = "wechat_send";
export const description = "发送微信消息。通过 UI 自动化操作微信桌面客户端。";

export const parameters = {
  type: "object",
  properties: {
    text: { type: "string", description: "消息内容" },
    chat: { type: "string", description: "群聊/联系人名称（不填则发到当前窗口）" },
  },
  required: ["text"],
};

export async function execute(input) {
  const text = input.text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const chat = input.chat ? input.chat.replace(/"/g, '\\"') : "";

  let code;
  if (chat) {
    code = `
import sys, os, time
sys.path.insert(0, '.')
from wechat_mcp_macos.sender import activate_wechat, search_and_select_chat, send_message

ok, msg = activate_wechat()
if not ok:
    print(f"激活微信失败: {msg}")
    sys.exit(1)
time.sleep(0.5)

ok, msg = search_and_select_chat("${chat}")
if not ok:
    print(f"搜索群聊失败: {msg}")
    sys.exit(1)
time.sleep(1)

ok, msg = send_message("${text}")
if ok:
    print(f"已发送到 ${chat}")
else:
    print(f"发送失败: {msg}")
`;
  } else {
    code = `
import sys
sys.path.insert(0, '.')
from wechat_mcp_macos.sender import send_message

ok, msg = send_message("${text}")
if ok:
    print("已发送")
else:
    print(f"发送失败: {msg}")
`;
  }

  try {
    const result = execSync(`${VENV_PYTHON} -c "${code.replace(/"/g, '\\"')}"`, {
      cwd: PROJECT_DIR,
      timeout: 15000,
      encoding: "utf-8",
    });
    return {
      content: [{ type: "text", text: result.trim() }],
    };
  } catch (e) {
    return {
      content: [{ type: "text", text: `发送失败: ${e.message}` }],
    };
  }
}
